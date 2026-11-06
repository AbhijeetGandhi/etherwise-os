"""Globaltize Workforce money from email — the ledger source for the client that
pays through the platform we built for them (2026-11-06).

Why this exists
---------------
Abhijeet, 2 Nov, on the Rs 1,96,863.08 that landed by IMPS on the RDA rail:
"This is the globaltize actually. they started paying through other platform
that we built." And 6 Nov: "are we currently monitoring the contact@ email for
money? … we need to start globaltize now, I received one more payment today and
it's not in the cockpit." Nothing read those mails for money: the finance rails
(`finance_wise_mail`, `finance_payment_mail`) read inbox 01 only, and the Jarvis
sweep files a Globaltize payment notice as `platform_notice` noise.

What Globaltize sends (real mails, info@app.globaltize.com, to contact@):

    Subject: You've been paid $2,127.00 by Globaltize
    Body:    Globaltize paid you $2,127.00 for INV-2627-086 from Wise.
             Amount: $2,127.00   Paid On: November 4, 2026   Reference: PAY-33D1D00E

    Subject: Your withdrawal of $2,100.00 is being processed
    Body:    we're processing your withdrawal to Etherwise.
             Withdrawal Amount $2,100.00   Sent To: Etherwise
             Transfer Fee: No fee   Reference: cofr_6fuDIVKYxtNL3

The money model — the Wise one, exactly
---------------------------------------
A "paid" mail is INCOME in USD (the client paid; it sits in the Globaltize
wallet). A withdrawal is a TRANSFER OUT of that wallet (to the bank, or to Wise),
with its fee a platform_fee line. The money then LANDS somewhere we already read:

  * to Etherwise  -> IDFC, by IMPS on the RDA remittance rail, as a pooled
                     remitter ("TRANSFERTOMOBIL/YESB…/RDAremittance"), in INR;
  * to Wise       -> a Wise arrival "Received money from Whop Inc with reference
                     GLOBALTIZE", net of the fee (Whop is the processor they pay through).

Both landings were being read as INCOME (the bank rail's RDA rule, the Wise
rail's arrival rule) — right until the wallet had a ledger of its own. Now they
are `transfer_in`, paired to the withdrawal that sent them, so the money counts
once: the "paid" mail is the revenue; the landing is the settlement. finance
doctor #16 holds the line (see `double_counted`).

Keys: every mail carries a platform reference (PAY-… / INV-… / cofr_…), unique
per payment, so the canonical key is `globaltize:PAY-<ref>` / `globaltize:WD-<ref>`
/ `globaltize:FEE-WD-<ref>`; re-reading a month is free. Two payments of one
amount on one day (29 Oct: $1,117.50 twice, different references and memos) stay
two — this is NOT the Wise date+amount collapse, because the reference here is
the platform's own id, not free text the payer typed.

Reads only on Gmail. Writes `transactions` rows + the pairing on landing rows.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

SOURCE = "Globaltize-mail"
SENDER = "info@app.globaltize.com"
CLIENT_COMPANY = "Globaltize LLC"          # the registry's billing canonical (id `globatize`)
CURRENCY = "USD"

PAID_SUBJECT = re.compile(r"You'?ve been paid\s+\$(?P<amt>\d[\d,]*\.?\d*)\s+by Globaltize", re.I)
PAID_MEMO = re.compile(r"Globaltize paid you\s+\$(?P<amt>\d[\d,]*\.?\d*)\s+for\s+(?P<memo>.+?)\.(?:\s|$)",
                       re.I | re.S)
PAID_ON = re.compile(r"Paid On:\s*(?P<d>[A-Z][a-z]+ \d{1,2}, \d{4})")
REFERENCE = re.compile(r"Reference:\s*(?P<ref>[A-Za-z0-9][\w\-]{2,40})")

WITHDRAWAL_SUBJECT = re.compile(
    r"Your withdrawal of\s+\$(?P<amt>\d[\d,]*\.?\d*)\s+is being processed", re.I)
SENT_TO = re.compile(r"Sent To:\s*(?P<dest>[^\n]+?)\s*(?:\n|\s{2,}|(?=Transfer Fee)|(?=Reference)|$)", re.I)
SENT_TO_LOOSE = re.compile(r"withdrawal to\s+(?P<dest>[A-Za-z][A-Za-z ]{1,30}?)\.", re.I)
FEE = re.compile(r"Transfer Fee:\s*(?P<fee>No fee|\$\d[\d,]*\.?\d*)", re.I)

DEST_BANK = "Etherwise"                    # the IDFC account, in their words
DEST_WISE = "Wise"

# The landing of a withdrawal to the bank: IMPS on the RDA rail, 0-10 days later,
# at the processor's rate. Measured 29→30 Oct: $2,100 landed as Rs 1,96,863.08 =
# $2,073.36 at the ECB pin, 98.7% — a 1.3% spread. Tolerance: the landing may be
# up to 3% UNDER the withdrawal (spread) and 0.5% over (a better rate than ECB),
# never more. A wider band would pair unrelated amounts.
LANDING_DAYS = 10
LANDING_LOW, LANDING_HIGH = Decimal("0.97"), Decimal("1.005")
# The Wise arrival is the withdrawal net of Whop's fee, to the cent, 1-5 days on.
WISE_PAYER, WISE_REFERENCE = "Whop Inc", "GLOBALTIZE"
WISE_DAYS = 7


def _minor(d: Decimal) -> int:
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _dec(s: str) -> Decimal:
    return Decimal(str(s).replace(",", "").strip())


def _iso(words: str) -> Optional[str]:
    try:
        return datetime.strptime(words, "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_paid(subject: str, body: str) -> Optional[dict]:
    """The income facts from a "You've been paid" mail, or None if not one.
    `incomplete` when the subject matches but the body does not (a template
    change must alarm, not skip)."""
    ms = PAID_SUBJECT.search(subject or "")
    if not ms:
        return None
    mr = REFERENCE.search(body or "")
    md = PAID_ON.search(body or "")
    mm = PAID_MEMO.search(body or "")
    if not (mr and md and _iso(md.group("d"))):
        return {"kind": "paid", "incomplete": True,
                "have": {"reference": bool(mr), "paid_on": bool(md)}}
    return {
        "kind": "paid", "incomplete": False,
        "amount_minor": _minor(_dec(ms.group("amt"))), "currency": CURRENCY,
        "paid_date": _iso(md.group("d")), "reference": mr.group("ref"),
        "memo": re.sub(r"\s+", " ", mm.group("memo")).strip() if mm else None,
    }


def parse_withdrawal(subject: str, body: str) -> Optional[dict]:
    """The transfer facts from a withdrawal mail, or None if not one."""
    ms = WITHDRAWAL_SUBJECT.search(subject or "")
    if not ms:
        return None
    mr = REFERENCE.search(body or "")
    mt = SENT_TO.search(body or "") or SENT_TO_LOOSE.search(body or "")
    mf = FEE.search(body or "")
    if not (mr and mt):
        return {"kind": "withdrawal", "incomplete": True,
                "have": {"reference": bool(mr), "sent_to": bool(mt), "fee": bool(mf)}}
    fee = Decimal("0")
    if mf and not mf.group("fee").lower().startswith("no"):
        fee = _dec(mf.group("fee").lstrip("$"))
    return {
        "kind": "withdrawal", "incomplete": False,
        "amount_minor": _minor(_dec(ms.group("amt"))), "currency": CURRENCY,
        "fee_minor": _minor(fee), "destination": mt.group("dest").strip(),
        "reference": mr.group("ref"),
    }


def parse(subject: str, body: str) -> Optional[dict]:
    return parse_paid(subject, body) or parse_withdrawal(subject, body)


# ── the ledger rows ──────────────────────────────────────────────────────────

def income_row(p: dict) -> dict:
    memo = f" for {p['memo']}" if p.get("memo") else ""
    return {
        "canonical_key": f"globaltize:PAY-{p['reference']}", "category": "income",
        "pnl_bucket": "revenue", "amount_minor": p["amount_minor"],
        "currency": p["currency"], "creation_dt": p["paid_date"],
        "description": f"Globaltize paid{memo} with reference {p['reference']}",
        "assignment_company": CLIENT_COMPANY,
    }


def withdrawal_rows(w: dict, *, mail_date: str) -> list:
    """The transfer_out leg (and the fee leg when there is one). Dated the day
    the mail arrived: the platform does not print the request date."""
    ref = w["reference"]
    fee_words = (f"fee: {w['fee_minor'] / 100:.2f} {w['currency']}"
                 if w.get("fee_minor") else "no fee")
    rows = [{
        "canonical_key": f"globaltize:WD-{ref}", "category": "transfer_out",
        "pnl_bucket": "transfer", "amount_minor": -w["amount_minor"],
        "currency": w["currency"], "creation_dt": mail_date,
        "description": f"Withdrawal to {w['destination']} ({fee_words}) {ref}",
        "assignment_company": CLIENT_COMPANY,
    }]
    if w.get("fee_minor"):
        rows.append({
            "canonical_key": f"globaltize:FEE-WD-{ref}", "category": "fee",
            "pnl_bucket": "platform_fee", "amount_minor": -w["fee_minor"],
            "currency": w["currency"], "creation_dt": mail_date,
            # NOT "withdrawal fee": pnl.bucket reads those two words as the fee
            # leg of a transfer and files it outside the P&L; this is a cost of
            # getting paid, like Wise's charge, and belongs in platform_fee
            "description": f"Globaltize transfer charge (Whop) for {ref}",
            "assignment_company": CLIENT_COMPANY,
        })
    return rows


def commit(conn, rows: list) -> dict:
    """Insert rows whose canonical_key is new; never update an existing one."""
    ins = skipped = 0
    for r in rows:
        if conn.execute("SELECT 1 FROM transactions WHERE canonical_key=?",
                        (r["canonical_key"],)).fetchone():
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO transactions (canonical_key, dedup_key, source, category,"
            " pnl_bucket, amount_minor, amount_base_minor, currency, fx_rate,"
            " description, assignment_company, creation_dt, pnl_month,"
            " first_seen_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,'1',?,?,?,?,datetime('now'),datetime('now'))",
            (r["canonical_key"], r["canonical_key"], SOURCE, r["category"],
             r["pnl_bucket"], r["amount_minor"], r["amount_minor"], r["currency"],
             r["description"], r["assignment_company"], r["creation_dt"],
             (r["creation_dt"] or "")[:7]))
        ins += 1
    return {"inserted": ins, "already_present": skipped}


# ── the landings: a withdrawal's money arriving where we already read ───────

def _bank_candidates(conn, wd: dict) -> list:
    """IDFC credits on the RDA rail inside the window that fit the withdrawal
    at the processor's rate. `amount_base_minor` is the ECB pin in USD cents."""
    lo = (Decimal(wd["amount_minor"]) * LANDING_LOW).to_integral_value()
    hi = (Decimal(wd["amount_minor"]) * LANDING_HIGH).to_integral_value()
    d0 = wd["creation_dt"][:10]
    d1 = (datetime.fromisoformat(d0) + timedelta(days=LANDING_DAYS)).strftime("%Y-%m-%d")
    return [dict(r) for r in conn.execute(
        "SELECT record_id, canonical_key, category, amount_minor, amount_base_minor,"
        " substr(creation_dt,1,10) d, related_transaction_id, assignment_company"
        " FROM transactions WHERE source='IDFC' AND amount_minor > 0"
        " AND upper(description) LIKE '%RDA%' AND category IN ('income','transfer_in')"
        " AND substr(creation_dt,1,10) BETWEEN ? AND ?"
        " AND amount_base_minor BETWEEN ? AND ?"
        " AND (related_transaction_id IS NULL OR related_transaction_id = ?)",
        (d0, d1, int(lo), int(hi), wd["canonical_key"]))]


def _wise_candidates(conn, wd: dict) -> list:
    net = wd["amount_minor"] - wd.get("fee_minor", 0)      # what Whop forwards: amount less its fee
    d0 = wd["creation_dt"][:10]
    d1 = (datetime.fromisoformat(d0) + timedelta(days=WISE_DAYS)).strftime("%Y-%m-%d")
    return [dict(r) for r in conn.execute(
        "SELECT record_id, canonical_key, category, amount_minor, amount_base_minor,"
        " substr(creation_dt,1,10) d, related_transaction_id, assignment_company"
        " FROM transactions WHERE source LIKE 'Wise%' AND amount_minor = ?"
        " AND description LIKE ? AND description LIKE ?"
        " AND category IN ('income','transfer_in')"
        " AND substr(creation_dt,1,10) BETWEEN ? AND ?"
        " AND (related_transaction_id IS NULL OR related_transaction_id = ?)",
        (net, f"%{WISE_PAYER}%", f"%{WISE_REFERENCE}%", d0, d1, wd["canonical_key"]))]


def withdrawals(conn) -> list:
    out = []
    for r in conn.execute(
            "SELECT canonical_key, amount_minor, substr(creation_dt,1,10) creation_dt,"
            " description FROM transactions WHERE source=? AND category='transfer_out'"
            " ORDER BY creation_dt", (SOURCE,)):
        d = dict(r)
        d["amount_minor"] = -d["amount_minor"]
        fee = conn.execute("SELECT amount_minor FROM transactions WHERE canonical_key=?",
                           (d["canonical_key"].replace("globaltize:WD-", "globaltize:FEE-WD-"),)
                           ).fetchone()
        d["fee_minor"] = -(fee[0]) if fee else 0
        d["destination"] = DEST_WISE if f"to {DEST_WISE}" in d["description"] else DEST_BANK
        out.append(d)
    return out


def pair_landings(conn, *, apply: bool = True) -> dict:
    """Pair every withdrawal with the row where its money landed, and make that
    row a transfer_in (it was read as income until the wallet had a ledger).
    Exactly one candidate, or nothing — ambiguity is reported, never guessed.
    Idempotent: a landing already paired to this withdrawal is left as is."""
    rep = {"paired": [], "already": [], "in_flight": [], "ambiguous": []}
    for wd in withdrawals(conn):
        cands = (_wise_candidates(conn, wd) if wd["destination"] == DEST_WISE
                 else _bank_candidates(conn, wd))
        if len(cands) > 1:
            rep["ambiguous"].append({"withdrawal": wd["canonical_key"],
                                     "candidates": [c["canonical_key"] for c in cands]})
            continue
        if not cands:
            rep["in_flight"].append({"withdrawal": wd["canonical_key"], "date": wd["creation_dt"],
                                     "amount_minor": wd["amount_minor"], "to": wd["destination"]})
            continue
        c = cands[0]
        if c["related_transaction_id"] == wd["canonical_key"] and c["category"] == "transfer_in":
            rep["already"].append({"withdrawal": wd["canonical_key"], "landing": c["canonical_key"]})
            continue
        rep["paired"].append({"withdrawal": wd["canonical_key"], "landing": c["canonical_key"],
                              "landing_date": c["d"], "was": c["category"],
                              "landing_base_minor": c["amount_base_minor"]})
        if apply:
            conn.execute(
                "UPDATE transactions SET category='transfer_in', pnl_bucket='transfer',"
                " assignment_company=COALESCE(assignment_company, ?), related_transaction_id=?,"
                " updated_at=datetime('now') WHERE canonical_key=?",
                (CLIENT_COMPANY, wd["canonical_key"], c["canonical_key"]))
            conn.execute(
                "INSERT INTO audit_log (actor, entity, entity_id, field, old_value, new_value,"
                " source, note) VALUES ('agent:finance-globaltize-mail','transactions',?,"
                " 'category',?,'transfer_in','sqlite',?)",
                (c["record_id"] or c["canonical_key"], c["category"],
                 f"the landing of Globaltize withdrawal {wd['canonical_key']}"
                 f" ({wd['amount_minor'] / 100:.2f} USD to {wd['destination']}) — the"
                 f" 'You've been paid' mail is the revenue, this row is the settlement"))
    return rep


def double_counted(conn) -> list:
    """Rows that would count Globaltize money twice: a landing still booked as
    income while the withdrawal that sent it is in the ledger. The doctor's
    invariant (#16); `pair_landings` is the fix."""
    out = []
    for wd in withdrawals(conn):
        cands = (_wise_candidates(conn, wd) if wd["destination"] == DEST_WISE
                 else _bank_candidates(conn, wd))
        if len(cands) == 1 and cands[0]["category"] == "income":
            out.append({"withdrawal": wd["canonical_key"], "landing": cands[0]["canonical_key"],
                        "date": cands[0]["d"], "amount_base_minor": cands[0]["amount_base_minor"]})
    return out


def in_flight(conn, today: str) -> list:
    """Withdrawals whose landing the ledger has not seen yet — normal inside the
    month (the bank statement comes monthly), worth a word once it is old."""
    out = []
    for wd in withdrawals(conn):
        cands = (_wise_candidates(conn, wd) if wd["destination"] == DEST_WISE
                 else _bank_candidates(conn, wd))
        if not cands:
            age = (datetime.fromisoformat(today) - datetime.fromisoformat(wd["creation_dt"])).days
            out.append({"withdrawal": wd["canonical_key"], "date": wd["creation_dt"],
                        "amount_minor": wd["amount_minor"], "to": wd["destination"], "age_days": age})
    return out
