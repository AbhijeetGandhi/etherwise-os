"""Wise SETTLEMENT leg from email — the exact amount and fee, without the CSV.

Why this exists
---------------
`payment_mail_rail` reads the ARRIVAL notification ("Adelye Ltd just paid you"),
which carries a gross figure and nothing else. The money that actually reaches
the bank is smaller, and by how much is only stated in a SECOND email that Wise
sends once the money has been converted and sent on:

    Subject: Your money's been sent (#2282075707)
    Body:    151,240.31 INR is on its way to you.
             The rate was 1 USD = 95.5862 INR, and our fee was 32.76 USD.

That is the whole settlement: destination amount, realized FX rate, and fee. The
source amount is not stated but is exactly recoverable — net / rate — and the
gross is that plus the fee. Verified against real ledger rows before this module
was written: 151,240.31 / 95.5862 = 1,582.24 and the fee 32.76, which reproduce
the CSV's `-158224` and `-3276` to the minor unit.

The key insight that makes this safe
------------------------------------
The Wise CSV importer keys every row `wise:<TransferWise ID>`, and the subject's
`#2282075707` IS that id. So a mail-derived row can carry the IDENTICAL canonical
key as the CSV row for the same transfer — which makes double-counting
structurally impossible rather than merely unlikely. We never overwrite an
existing row: a CSV row already present always wins, mail only fills gaps.

The income leg
--------------
Wise leaves "Transfer Number:" empty in the arrival email, so income cannot
borrow the settlement's id. It does, however, print the invoice reference, and
the CSV writes that same reference into its description — so the reference is
the natural key the two sources share. `commit_income` refuses to write when a
matching CSV row already exists (by reference, else by payer+amount+day), which
is what makes mail-sourced revenue safe to add rather than a way to count the
same money twice.

Together these two legs cover every row shape the Wise CSV has ever produced
(income / transfer_out / fee — verified against the full history), which is what
makes retiring the manual export possible.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

# Subject carries the transfer id; that id is the CSV's TransferWise ID.
SETTLEMENT_SUBJECT = re.compile(r"money'?s been sent\s*\(#(?P<tid>\d{6,})\)", re.I)

# "151,240.31 INR is on its way to you."  — destination amount + currency.
NET_PATTERN = re.compile(
    r"(?P<amt>\d[\d,]*\.?\d*)\s*(?P<cur>[A-Z]{3})\s+is on its way", re.I)

# "The rate was 1 USD = 95.5862 INR, and our fee was 32.76 USD."
# Currency is captured, never assumed: two historical transfers were AUD.
RATE_PATTERN = re.compile(
    r"rate was\s*1\s*(?P<src>[A-Z]{3})\s*=\s*(?P<rate>\d+\.?\d*)\s*(?P<dst>[A-Z]{3})", re.I)
FEE_PATTERN = re.compile(
    r"fee was\s*(?P<amt>\d[\d,]*\.?\d*)\s*(?P<cur>[A-Z]{3})", re.I)


# ── arrival ("X just paid you") — the INCOME leg ─────────────────────────────
# Wise leaves "Transfer Number:" EMPTY in this email (verified across the live
# corpus), which is why income cannot borrow the settlement's id. But it DOES
# print the invoice reference, and the CSV writes that same reference into its
# description ("Received money from CMF Publishing LLC with reference
# INV-2627-037"). So the reference is the natural key shared by both sources,
# and matching on it is what stops a mail row and a CSV row both counting.
ARRIVAL_SUBJECT = re.compile(
    # Wise sends TWO different emails for the SAME payment (observed live
    # 2026-08-10 on the Consulting Systems B.V. pair): "X just paid you" and
    # "Money received from X". Both must parse — and the duplicate must then be
    # collapsed by dedupe_arrivals, or the payment counts twice.
    r"^(?:(?P<payer>.+?)\s+just paid you\b|Money received from\s+(?P<payer2>.+?)\s*$)",
    re.I)
ARRIVAL_AMOUNT = re.compile(
    # payer runs to END OF LINE, not to the first period: "Consulting Systems
    # B.V." was being truncated to "Consulting Systems B", which changed both
    # the stored description and the fallback key.
    r"You received\s+(?P<amt>\d[\d,]*\.?\d*)\s*(?P<cur>[A-Z]{3})\s+from\s+"
    r"(?P<payer>[^\n]{2,80})", re.I)
ARRIVAL_REFERENCE = re.compile(r"Reference:\s*\n?\s*(?P<ref>[A-Za-z0-9][\w\-/]{2,40})")


def _clean_payer(raw: str) -> str:
    """Trim a payer name without eating an abbreviation's dots.

    "Consulting Systems B.V." must survive intact; only a sentence-ending period
    (one preceded by a letter pair, i.e. not part of "B.V.") is dropped.
    """
    p = (raw or "").strip()
    p = re.sub(r"\s+", " ", p)
    if p.endswith(".") and not re.search(r"\b[A-Z]\.[A-Z]\.$", p):
        p = p[:-1]
    return p.strip()


def parse_arrival(subject: str, body: str) -> Optional[dict]:
    """The income facts from an arrival notification, or None if not one."""
    ms = ARRIVAL_SUBJECT.search(subject or "")
    ma = ARRIVAL_AMOUNT.search(body or "")
    if not (ms or ma):
        return None
    if ma:
        payer = _clean_payer(ma.group("payer"))
    else:
        payer = _clean_payer(ms.group("payer") or ms.group("payer2") or "")
    if not ma:
        return {"payer": payer, "incomplete": True}
    mr = ARRIVAL_REFERENCE.search(body or "")
    return {
        "payer": payer,
        "incomplete": False,
        "amount_minor": _minor(_dec(ma.group("amt"))),
        "currency": ma.group("cur").upper(),
        "reference": mr.group("ref") if mr else None,
    }


def dedupe_arrivals(arrivals: list) -> list:
    """Collapse the two notifications Wise sends for one payment.

    `arrivals` is [(arrival, paid_date)]. Identity is (date, amount, currency)
    — deliberately NOT the reference or the payer string, because those are
    exactly what differ between the two shapes: one said "Consulting Systems BV"
    and the other "Consulting Systems B.V.". Relying on the reference matching
    was luck; if the second shape ever omits it the keys diverge and the payment
    is counted twice.

    Two genuinely distinct payments from one payer on one day are only merged if
    they are also for the identical amount — the same assumption income_key
    already rests on. CMF's two payments on 2026-07-31 differ in amount and so
    survive as two.

    The copy carrying a reference wins, since it produces the better key.
    """
    best = {}
    for a, paid in arrivals:
        if a.get("incomplete"):
            continue
        k = (paid, a["amount_minor"], a["currency"])
        cur = best.get(k)
        if cur is None or (not cur[0].get("reference") and a.get("reference")):
            best[k] = (a, paid)
    return list(best.values())


def _minor(d: Decimal) -> int:
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _dec(s: str) -> Decimal:
    return Decimal(str(s).replace(",", "").strip())


def parse_settlement(subject: str, body: str) -> Optional[dict]:
    """The settlement facts, or None if this is not a settlement email.

    Returns source/destination amounts in MINOR units, the realized rate as the
    string Wise printed (never a float — it is money, and the CSV stores it as a
    string too), and the fee. `sent_minor` is derived, so it is marked as such:
    if Wise ever prints the source amount directly, prefer that.
    """
    ms = SETTLEMENT_SUBJECT.search(subject or "")
    if not ms:
        return None
    mn, mr, mf = (NET_PATTERN.search(body or ""), RATE_PATTERN.search(body or ""),
                  FEE_PATTERN.search(body or ""))
    if not (mn and mr and mf):
        # A settlement subject we cannot fully parse is a LOUD gap, not a silent
        # skip — the caller counts these so a Wise template change surfaces.
        return {"transfer_id": ms.group("tid"), "incomplete": True,
                "have": {"net": bool(mn), "rate": bool(mr), "fee": bool(mf)}}

    rate = _dec(mr.group("rate"))
    if rate <= 0:
        return {"transfer_id": ms.group("tid"), "incomplete": True,
                "have": {"net": True, "rate": False, "fee": True}}

    net = _dec(mn.group("amt"))
    fee = _dec(mf.group("amt"))
    src_cur = mr.group("src").upper()
    sent = net / rate                       # source-currency amount converted
    return {
        "transfer_id": ms.group("tid"),
        "incomplete": False,
        "sent_minor": _minor(sent),          # DERIVED: net / rate
        "sent_derived": True,
        "currency": src_cur,
        "fee_minor": _minor(fee),
        "fee_currency": mf.group("cur").upper(),
        "gross_minor": _minor(sent + fee),
        "net_minor": _minor(net),
        "net_currency": mn.group("cur").upper(),
        "fx_rate": mr.group("rate"),         # string, as the CSV stores it
        "fx_to": mr.group("dst").upper(),
    }


def ledger_rows(s: dict, *, paid_date: str) -> list[dict]:
    """The two `transactions` rows this settlement implies, keyed EXACTLY as the
    CSV importer would key them (`wise:TRANSFER-<id>` / `wise:FEE-TRANSFER-<id>`),
    so a later CSV import collides instead of duplicating."""
    if s.get("incomplete"):
        return []
    tid = f"TRANSFER-{s['transfer_id']}"
    return [
        {"canonical_key": f"wise:{tid}", "wise_id": tid, "category": "transfer_out",
         "pnl_bucket": "transfer", "amount_minor": -s["sent_minor"],
         "currency": s["currency"], "fx_rate": s["fx_rate"],
         "description": f"Sent money to Etherwise (fee: "
                        f"{s['fee_minor'] / 100:.2f} {s['fee_currency']})",
         "creation_dt": paid_date},
        {"canonical_key": f"wise:FEE-{tid}", "wise_id": f"FEE-{tid}", "category": "fee",
         "pnl_bucket": "platform_fee", "amount_minor": -s["fee_minor"],
         "currency": s["fee_currency"], "fx_rate": None,
         "description": f"Wise Charges for: {tid}", "creation_dt": paid_date},
    ]


def commit(conn, rows: list[dict], *, source: str = "Wise-mail") -> dict:
    """Insert rows that are not already in the ledger. NEVER updates an existing
    canonical_key: a CSV-sourced row is the stronger record and always wins.
    Returns counts so the caller can report inserted vs already-present."""
    ins = skipped = 0
    for r in rows:
        if conn.execute("SELECT 1 FROM transactions WHERE canonical_key=?",
                        (r["canonical_key"],)).fetchone():
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO transactions (canonical_key, dedup_key, wise_id, source,"
            " category, pnl_bucket, amount_minor, amount_base_minor, currency,"
            " fx_rate, description, creation_dt, pnl_month, first_seen_at,"
            " updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
            (r["canonical_key"], r["canonical_key"], r["wise_id"], source,
             r["category"], r["pnl_bucket"], r["amount_minor"],
             _base_minor(r["amount_minor"], r["currency"]), r["currency"],
             r["fx_rate"], r["description"], r["creation_dt"],
             (r["creation_dt"] or "")[:7]))
        ins += 1
    return {"inserted": ins, "already_present": skipped}


# The CSV can date an income row a day or two off the arrival email (the wire
# posts, then the statement dates it). Matching on an exact day was too strict.
INCOME_MATCH_WINDOW_DAYS = 3

# The ledger's reporting currency. For a USD row `amount_base_minor` is simply
# the amount (verified: all 314 USD Wise rows have base == minor); a non-USD row
# needs a real FX conversion and is left NULL, exactly as the CSV importer leaves
# the 6 AUD rows, so a later FX-aware pass fills it rather than us inventing a
# rate. This column is NOT decorative: payment_mail_rail._ledger_twin requires
# `amount_base_minor IS NOT NULL`, so a row without it can never reconcile
# against its provisional twin and the payment looks permanently unpaid.
BASE_CURRENCY = "USD"


def _base_minor(amount_minor: int, currency: str):
    return amount_minor if (currency or "").upper() == BASE_CURRENCY else None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40]


def income_key(a: dict, paid_date: str) -> str:
    """Canonical key for a mail-derived income row.

    The reference is NOT unique on its own. Wise's reference field is free text
    the payer types, and the live ledger proves it repeats: 23 separate payments
    carry the reference "Malave", 4 carry "SENDER", 2 carry "MercuryACH". Keying
    on the reference alone would make the 2nd..Nth payment look like a duplicate
    of the 1st and silently DROP it — an under-count of revenue, which is the
    worse direction of the two errors.

    So the reference is a disambiguator, never the whole key: date and amount are
    always included. Two payments identical in reference, date AND amount are the
    same payment described twice.
    """
    ref = _slug(a["reference"]) if a.get("reference") else _slug(a["payer"])
    tag = "MAILREF" if a.get("reference") else "MAILPAY"
    return f"wise:{tag}-{ref}-{paid_date}-{a['amount_minor']}"


def existing_income(conn, a: dict, paid_date: str):
    """An income row already in the ledger for this payment, or None.

    Checked BEFORE writing, because the CSV keys income by a transfer id we
    cannot see. The AMOUNT is required in every branch: the reference alone is
    not identifying (see income_key — "Malave" appears on 23 distinct payments),
    and matching on it alone would treat a new payment as already-present and
    drop it.

      1. reference + same amount, within a few days — the settlement can post a
         day or two after the arrival, so an exact date match is too strict;
      2. failing that, same payer + same amount + same day.
    """
    if a.get("reference"):
        row = conn.execute(
            "SELECT canonical_key, source FROM transactions"
            " WHERE category='income' AND description LIKE ?"
            "   AND amount_minor=? AND currency=?"
            "   AND ABS(julianday(creation_dt) - julianday(?)) <= ?",
            (f"%{a['reference']}%", a["amount_minor"], a["currency"],
             paid_date, INCOME_MATCH_WINDOW_DAYS)).fetchone()
        if row:
            return row
    like = f"%{a['payer'][:18]}%"
    return conn.execute(
        "SELECT canonical_key, source FROM transactions"
        " WHERE category='income' AND amount_minor=? AND currency=?"
        "   AND date(creation_dt)=date(?) AND description LIKE ?",
        (a["amount_minor"], a["currency"], paid_date, like)).fetchone()


def income_row(a: dict, *, paid_date: str) -> dict:
    """The `transactions` income row this arrival implies. The description is
    written in the CSV's exact phrasing so anything downstream that reads
    descriptions (client attribution, the P&L) cannot tell the two apart."""
    ref = f" with reference {a['reference']}" if a.get("reference") else ""
    return {
        "canonical_key": income_key(a, paid_date), "wise_id": None,
        "category": "income", "pnl_bucket": "revenue",
        "amount_minor": a["amount_minor"], "currency": a["currency"],
        "fx_rate": None,
        "description": f"Received money from {a['payer']}{ref}",
        "creation_dt": paid_date,
    }


def commit_income(conn, arrivals: list[tuple], *, source: str = "Wise-mail") -> dict:
    """Insert income rows for arrivals the ledger does not already have.

    `arrivals` is [(arrival_dict, paid_date)]. Returns counts plus the rows it
    refused, so a run can show WHY something was skipped rather than just that
    a number was smaller than expected."""
    ins, dup = 0, []
    for a, paid in arrivals:
        if a.get("incomplete"):
            continue
        hit = existing_income(conn, a, paid)
        if hit:
            dup.append((a.get("reference") or a["payer"], hit[0], hit[1]))
            continue
        r = income_row(a, paid_date=paid)
        if conn.execute("SELECT 1 FROM transactions WHERE canonical_key=?",
                        (r["canonical_key"],)).fetchone():
            dup.append((a.get("reference") or a["payer"], r["canonical_key"],
                        "already-mail"))
            continue
        conn.execute(
            "INSERT INTO transactions (canonical_key, dedup_key, source, category,"
            " pnl_bucket, amount_minor, amount_base_minor, currency, description,"
            " creation_dt, pnl_month, first_seen_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
            (r["canonical_key"], r["canonical_key"], source, r["category"],
             r["pnl_bucket"], r["amount_minor"],
             _base_minor(r["amount_minor"], r["currency"]), r["currency"],
             r["description"], r["creation_dt"], (r["creation_dt"] or "")[:7]))
        ins += 1
    return {"inserted": ins, "already_present": len(dup), "duplicates": dup}


def income_gap(conn) -> dict:
    """Revenue the ledger is still missing, i.e. provisional payments with no
    ledger twin. This is the half email CANNOT key, and the number that grows if
    the CSV export stops. Surfaced so 'the fees are current' is never mistaken
    for 'the books are current'."""
    rows = conn.execute(
        "SELECT payer, client_id, amount_minor, currency, paid_date"
        "  FROM provisional_payments"
        " WHERE state != 'confirmed' AND provider = 'wise'"
        " ORDER BY paid_date").fetchall()
    total = sum(r[2] or 0 for r in rows)
    return {"count": len(rows), "total_minor": total,
            "rows": [tuple(r) for r in rows]}
