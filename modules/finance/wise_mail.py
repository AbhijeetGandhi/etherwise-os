"""Wise SETTLEMENT leg from email — the exact amount and fee, without the CSV.

Why this exists
---------------
`payment_mail_rail` reads the ARRIVAL notification ("Adelye Ltd just paid you"),
which carries a gross figure and nothing else. The money that actually reaches
the bank is smaller, and by how much is only stated in a SECOND email that Wise
sends once the payout completes:

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

Scope note (deliberate): this module handles the settlement legs only —
`transfer_out` and `fee`. The INCOME leg cannot be keyed from mail, because Wise
assigns the incoming payment a *different* transfer id that appears only in the
CSV. Income therefore stays in `provisional_payments` (see `income_gap()`), and
anyone who stops exporting the CSV must be told that revenue, not fees, is the
half that goes stale.
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


def _minor(d: Decimal) -> int:
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _dec(s: str) -> Decimal:
    return Decimal(str(s).replace(",", "").strip())


def parse_settlement(subject: str, body: str) -> Optional[dict]:
    """The settlement facts, or None if this is not a completed-payout email.

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
            " category, pnl_bucket, amount_minor, currency, fx_rate, description,"
            " creation_dt, pnl_month, first_seen_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
            (r["canonical_key"], r["canonical_key"], r["wise_id"], source,
             r["category"], r["pnl_bucket"], r["amount_minor"], r["currency"],
             r["fx_rate"], r["description"], r["creation_dt"],
             (r["creation_dt"] or "")[:7]))
        ins += 1
    return {"inserted": ins, "already_present": skipped}


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
