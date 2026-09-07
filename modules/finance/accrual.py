"""The month a bank row BELONGS to, when that is not the month it was paid.

Abhijeet, 2026-09-07: *"I know I sent some salries in september but that really
is of august. I know we discussed this in the past I don't know why this mistake
was made."*

We did discuss it. Migration 016 was written on 2026-08-03 and quotes him:
"A salary paid on Jul 2 that was FOR June must land in JUNE's revenue/expense/
profit split, not July's." The `pnl_month` column exists for exactly this. It was
set on TWO rows that day — Ranjana's Rs 20,000 and Jay's Rs 2,16,000, both paid
early July for June — and nothing was ever built to keep doing it. Every month
since has drifted the same way, unnoticed, because the decision lived in a
migration comment and a pair of hand-written values instead of in a rule.

WHAT DECIDES IT: the bank line says so. "Salary august", "Salary july", "Rent
april" — IDFC carries his own narration through, so the month is read, not
guessed. `pnl_month` is set ONLY when the named month differs from the payment
month, so the column keeps meaning "this row is not where its date puts it" and
a normal on-time payment stays NULL.

DELIBERATELY BANK-ONLY. The Upwork, Wise and Stripe rails carry their own dates
(`billed_dt`, the invoice date) and their own unsettled question about re-dating
history; an Upwork line reading "Earnings for Aug 10-Aug 16" must not be quietly
re-dated by a rule aimed at payroll. Sweeps, transfers and card settlements are
out too: they are excluded from the P&L, so a month on them would mean nothing.

WHAT IT CANNOT READ goes in EXPLICIT, keyed on the canonical key, with his answer
attached — never inferred from an amount or a name.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Optional

BANK_SOURCES = ("IDFC", "Bank")

# How far back to correct. Abhijeet, 2026-09-07, offered 2026 / August-onward /
# everything: "Fix all of 2026". Seven rows in 2025 drift the same way and are
# deliberately LEFT ALONE — those months have been reported and closed. Every
# future row is past this floor, so it never blocks the live case.
ACCRUAL_SINCE = "2026-01-01"

# Rows whose month is a P&L question. transfer/sweep/cc_settlement are excluded
# from the operating P&L, so attributing them to a month says nothing.
ACCRUING_BUCKETS = ("revenue", "refund", "platform_fee", "tax", "payroll",
                    "opex", "owner_draw")

_NUM = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
        "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_MONTH = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october"
    r"|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\b",
    re.IGNORECASE)

# The bank line names no month and no rule can supply one. His answer, verbatim,
# against the row it belongs to.
#   2026-09-07, on Rs 20,000 to Ranjana paid 19 Aug reading only "Salary raise":
#   "Ranjana's is a July top-up".
# Jay's Rs 2,18,350 of 31 Aug ("Salary plus old tdf diff") is August — the month
# it was paid — so it needs no entry: an override that agrees with the date is
# noise that later reads as a decision.
EXPLICIT = {
    "bank:1df7ad18f0be4511": "2026-07",       # Ranjana, Rs 20,000, paid 19 Aug
}


def month_named(description: Optional[str], paid: Optional[str]) -> Optional[str]:
    """'Salary august' paid 2026-09-03 -> '2026-08'. None when no month is named.

    The YEAR comes from the payment date, wrapped by distance: a December named
    in January is last year's, a January named in December is next year's (an
    advance). Six months is the fold — beyond that the near reading is absurd.

    TWO DIFFERENT MONTHS IN ONE LINE IS AN ANSWER OF None. "TDS may and june",
    Rs 60,985 paid 7 July, covers both; putting the whole of it in May because
    May is written first would be a guess dressed as a reading. It stays on its
    payment date and stays visible."""
    if not description or not paid or len(paid) < 7:
        return None
    found = {_NUM.get(m.lower()[:3]) for m in _MONTH.findall(description)}
    found.discard(None)
    if len(found) != 1:
        return None
    num = found.pop()
    year, paid_month = int(paid[:4]), int(paid[5:7])
    gap = num - paid_month
    if gap > 6:
        year -= 1
    elif gap < -6:
        year += 1
    return f"{year}-{num:02d}"


def effective_month(row: dict) -> Optional[str]:
    """The month this row should be attributed to, or None to leave it on its
    payment date. None is the answer for almost every row and must stay cheap.

    Same month as the payment is None, not the month: `pnl_month` means "this row
    is NOT where its date puts it", and filling it in on every on-time payment
    would bury the exceptions in noise."""
    if (row.get("source") or "") not in BANK_SOURCES:
        return None
    if (row.get("pnl_bucket") or "") not in ACCRUING_BUCKETS:
        return None
    paid = (row.get("creation_dt") or "")[:10]
    fixed = EXPLICIT.get(row.get("canonical_key") or "")
    want = fixed or month_named(row.get("description"), paid)
    if not want or want == paid[:7]:
        return None
    return want


def rows_to_set(conn: sqlite3.Connection, since: Optional[str] = ACCRUAL_SINCE) -> list:
    """Every bank row whose stored `pnl_month` is not what the line says it
    should be — the preview, and the same list `--apply` writes.

    IT NEVER CLEARS ONE. A row the rule has no opinion about ("NEFT/x/JAY/y",
    pnl_month '2026-06' set by a human) is left exactly as it is: silence from a
    reader is not evidence against a fact somebody entered. The first cut of
    this did clear them, and the cockpit's own August test — written for the
    reader side of this same feature — caught it."""
    sql = ("SELECT record_id, canonical_key, source, pnl_bucket, pnl_month,"
           " creation_dt, description, amount_minor FROM transactions"
           " WHERE source IN (?, ?) AND canonical_key IS NOT NULL")
    args: list = list(BANK_SOURCES)
    if since:
        sql += " AND substr(creation_dt,1,10) >= ?"
        args.append(since)
    out = []
    for r in conn.execute(sql + " ORDER BY creation_dt", args):
        row = dict(r)
        want = effective_month(row)
        if want and want != row.get("pnl_month"):
            row["want"] = want
            out.append(row)
    return out


def apply(conn: sqlite3.Connection, since: Optional[str] = ACCRUAL_SINCE) -> dict:
    """Write it. Idempotent — a second run finds nothing, because the wanted
    value is recomputed from the line, never accumulated."""
    changed = rows_to_set(conn, since)
    for r in changed:
        conn.execute("UPDATE transactions SET pnl_month=?, updated_at=datetime('now')"
                     " WHERE record_id=?", (r["want"], r["record_id"]))
    return {"changed": len(changed),
            "months": sorted({r["want"] for r in changed})}
