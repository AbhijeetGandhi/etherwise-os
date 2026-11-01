"""A salary the narration forgot to call one.

Abhijeet, 2026-09-07: *"There's something wrong yogita must have got paid on
july"*. She was — twice on 31 July, in the same run as everyone else, Rs 48,800
and Rs 1,000, together exactly the Rs 49,800 she is paid every other month. Both
lines stop at the IFSC:

    NEFT/IDFB6212M5765601/YOGITA SUDHIR SOMA/BKID0000098
    NEFT/IDFB6212M5772894/JAY SUSHIL POKHARNA/BARB0GOREAS/Salary july

`bank_xlsx.classify_category` needs the word "Salary" to file a row as salary, so
hers became a generic bank expense; `pnl._bank_spend` then routes bank expenses
by payee, and its `_EMPLOYEE_PAYEES` held exactly two names — Jay and Shruti. She
fell to the default and her July pay became an OWNER DRAW. Alphons Jaimon
(Rs 80,000, Nov 2025) and Dnyaneshwari Anarase (Rs 25,000, Feb 2026) went the
same way. The payroll bucket depended on him remembering to type a word.

THE LEDGER ALREADY KNOWS WHO HIS PEOPLE ARE. His choice, 2026-09-07: "anyone ever
paid a narrated salary". A payee with even one line reading "Salary" is a payroll
payee for ever after, so a blank narration cannot quietly remove them — and it
covers people who have since left, which the live roster cannot.

WHAT STOPS IT SWALLOWING EVERYTHING, also his call ("read the line when it says
something"): a line that names a purpose OTHER than salary is left alone. His
Rs 1,25,000 to Ranjana reading "Cash withdraw" is a draw, and says so.

This writes `category`, not the bucket. `pnl.bucket` already turns
category='salary' into payroll, so the fix needs no new branch in the money
rules and stays reproducible by doctor #10. It runs inside
`bin/finance-bucketize`, ahead of the bucket pass, which the cockpit runs after
every import.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Optional

BANK_SOURCES = ("IDFC", "Bank")

# Same floor as the effective-month correction: "Fix all of 2026" (2026-09-07).
SINCE = "2026-01-01"

# The narration says what it is, and it is not salary. Deliberately short and
# grounded in lines that actually exist — "Cash withdraw" is the one in his
# ledger. It grows when a real line demands it, never on speculation, because
# every word here is a way for a real salary to be missed again.
NOT_SALARY = re.compile(r"cash\s*withdraw|withdrawal|\bloan\b|reimburse|\bgift\b"
                        r"|\brent\b|\brefund\b", re.IGNORECASE)

_TITLE = re.compile(r"^(mr|mrs|ms|dr)\.?\s+", re.IGNORECASE)


def payee(description: Optional[str]) -> str:
    """'NEFT/IDFB6212M5765601/YOGITA SUDHIR SOMA/BKID0000098' -> 'YOGITA SUDHIR
    SOMA'. IDFC puts the counterparty in the third slash-field on every NEFT,
    IMPS and IFT line. '' when the shape is anything else, which is a refusal,
    not a match on everything."""
    parts = (description or "").split("/")
    if len(parts) < 3:
        return ""
    name = _TITLE.sub("", parts[2].strip())
    return " ".join(name.split()).upper()


def payroll_payees(conn: sqlite3.Connection) -> set:
    """Everyone who has ever been paid a line that SAID salary. Not scoped by
    date: a person who left in 2025 still explains a 2026 row, and the whole
    point is that one narrated line is enough, for ever."""
    out = set()
    marks = ",".join("?" * len(BANK_SOURCES))
    # debits only: a RETURNED salary is a positive `salary` row whose third
    # slash-field is the bank's reason ("INCORRECT ACCOUNT NUMBER (AC01)"), not
    # a person (2026-10-30, Jay's bounced Rs 1,08,000)
    for r in conn.execute(
            f"SELECT DISTINCT description d FROM transactions"
            f" WHERE source IN ({marks}) AND category='salary'"
            f" AND amount_minor < 0", BANK_SOURCES):
        name = payee(r["d"])
        if name:
            out.add(name)
    return out


def rows_to_fix(conn: sqlite3.Connection, since: Optional[str] = SINCE) -> list:
    """Bank debits to a known payroll payee that are not filed as salary and
    whose line does not say why. The preview, the writer's list, and doctor #13
    are all this one query — three readings of the same fact cannot disagree."""
    known = payroll_payees(conn)
    if not known:
        return []
    sql = ("SELECT record_id, canonical_key, description, amount_minor, category,"
           " pnl_bucket, creation_dt FROM transactions"
           " WHERE source IN (?, ?) AND amount_minor < 0"
           "   AND COALESCE(category,'') != 'salary' AND canonical_key IS NOT NULL")
    args: list = list(BANK_SOURCES)
    if since:
        sql += " AND substr(creation_dt,1,10) >= ?"
        args.append(since)
    out = []
    for r in conn.execute(sql + " ORDER BY creation_dt", args):
        row = dict(r)
        desc = row.get("description") or ""
        if payee(desc) not in known:
            continue
        if NOT_SALARY.search(desc):              # the line says what it is
            continue
        out.append(row)
    return out


def apply(conn: sqlite3.Connection, since: Optional[str] = SINCE) -> dict:
    """Set `category='salary'` so `pnl.bucket` files it as payroll. Idempotent:
    a fixed row no longer matches (its category IS salary), and it then joins
    `payroll_payees` itself, which is harmless — it was already in it."""
    fix = rows_to_fix(conn, since)
    for r in fix:
        conn.execute("UPDATE transactions SET category='salary',"
                     " updated_at=datetime('now') WHERE record_id=?", (r["record_id"],))
    return {"changed": len(fix),
            "payees": sorted({payee(r["description"]) for r in fix})}
