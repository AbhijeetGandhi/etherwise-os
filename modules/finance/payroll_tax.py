"""The TDS withheld from salaries — payroll cost the bank never shows.

Abhijeet, 2026-09-07: *"Also even if I am sending less salary you need to include
tds that we share as well?"* Yes. Every payroll figure this ledger held was the
NET transfer. The tax withheld from those wages and remitted separately to the CA
sat in owner draws, so a month of employing people looked cheaper than it was by
the whole of the TDS.

HIS FIGURES ARE THE SOURCE, THE BANK IS THE CHECK — his design, verbatim: "Ask
me tds amount each month I will give you. then you can match it against the
deduct from bank account." One remittance can cover two months (Rs 60,985 paid
7 July is May AND June), so the bank alone cannot say what any single month owed;
only he can. What the bank CAN do is prove the total, and doctor #14 does exactly
that — in both directions, so a remittance he has not given me a month for makes
the ledger say so instead of guessing.

  2026-09-07: "we started giving tds from apr that is this year" — so January,
  February and March carry none, and the Rs 1,16,000 of 9 March ("Advance tax
  and pt payments") is not payroll. August is withheld but not yet remitted:
  "august you will see in the next statement".

  2026-10-06: it did -- Rs 43,000 on 7 Sep, "TDS august", Rs 12,445 more than
  July's Rs 30,555, so the figure was put to him rather than read off the line.
  Asked whether all of it was TDS on August's salaries: "Yes, all of it". #14
  had the loop ready: the moment that row landed without a month behind it the
  check would have gone RED and named it.

PROFESSIONAL TAX IS DELIBERATELY ABSENT. The roster says Rs 200 a head a month
and no such payment leaves the bank monthly. His call: "Not monthly — leave it
out for now". Each month is therefore understated by roughly Rs 1,200-1,600, and
that is the honest state — a computed accrual with no payment behind it would be
a number the reconcile could never check.

HOW IT LANDS. The remittance row keeps its place in the statement, untouched, and
`pnl` files it as payroll. `accrual` puts it in the month its line names. Where
one row names TWO months the line cannot be read, so `accrual_pins()` puts the
whole payment in the FIRST of them and the ADJUSTMENT rows below carry the rest
across — net zero, on the existing `Direct` source (the same hand-entered
mechanism the two Wise fee backfills use), at the remittance's own pinned FX
rate so the USD presentation nets to zero too.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from modules.finance import money

SOURCE = "Direct"
SOURCE_FILE = "derived:tds-split"
KEY_PREFIX = "tds-split"

# Nothing before this is TDS on wages: "we started giving tds from apr that is
# this year" (2026-09-07). The March advance-tax payment and every 2025 row are
# out of scope by that sentence, not by an amount test.
SINCE = "2026-04-01"

# His answers, month by month, in paise. TDS on salaries only — not advance tax,
# not the CA's own fees. A month absent from here is a month with no TDS, which
# is a fact about Jan-Mar 2026 and not a gap.
MONTHLY_TDS = {
    "2026-04": 2808000,        # remitted 6 May, Rs 28,080, "TDS for april"
    "2026-05": 2808000,        # his split of the 7 Jul payment
    "2026-06": 3290500,        # his split of the 7 Jul payment
    "2026-07": 3055500,        # remitted 6 Aug, Rs 30,555, "TDS July"
    "2026-08": 4300000,        # remitted 7 Sep, Rs 43,000, "TDS august"
}

# The remittances that carry those months, so #14 can prove the table against the
# bank: canonical key -> the months that payment covers, EARLIEST FIRST (the
# first is where the bank row itself is attributed).
REMITTANCES = {
    "bank:553f3afc6c497e3e": ("2026-04",),              # 6 May,  Rs 28,080
    "bank:04607cb1178e7003": ("2026-05", "2026-06"),    # 7 Jul,  Rs 60,985
    "bank:1b70a0304700d50b": ("2026-07",),              # 6 Aug,  Rs 30,555
    "bank:6435ebd60cc8b361": ("2026-08",),              # 7 Sep,  Rs 43,000
}


def declared_minor(month: str) -> int:
    return MONTHLY_TDS.get(month, 0)


def accrual_pins() -> dict:
    """`accrual.EXPLICIT` entries for the remittances whose line names more than
    one month, so the bank row lands in the first of them.

    Merged into EXPLICIT rather than typed there a second time: the pin and the
    split are the same decision, and two copies of one decision drift. A
    single-month remittance gets no pin — its own narration says the month, and
    an override that agrees with the reading later reads as a decision that was
    never made."""
    return {key: months[0] for key, months in REMITTANCES.items() if len(months) > 1}


# ── the reconcile (doctor #14 reads all three) ───────────────────────────────

def remitted(conn: sqlite3.Connection) -> dict:
    """canonical key -> what actually left the bank, for the rows in REMITTANCES.
    Missing means the row is not in the ledger, which #14 reports rather than
    silently treating as zero."""
    out = {}
    for key in REMITTANCES:
        r = conn.execute("SELECT amount_minor FROM transactions WHERE canonical_key=?",
                         (key,)).fetchone()
        if r is not None and r["amount_minor"] is not None:
            out[key] = -r["amount_minor"]
    return out


def mismatches(conn: sqlite3.Connection) -> list:
    """Where his figures and the bank disagree. The whole point of writing the
    months down separately: if May + June is not Rs 60,985 then something else is
    in that payment, and it must say so rather than absorb the difference."""
    bank = remitted(conn)
    out = []
    for key, months in REMITTANCES.items():
        if key not in bank:
            out.append({"key": key, "months": list(months),
                        "error": "remittance row is not in the ledger"})
            continue
        want = sum(declared_minor(m) for m in months)
        if want != bank[key]:
            out.append({"key": key, "months": list(months), "declared_minor": want,
                        "remitted_minor": bank[key], "delta_minor": bank[key] - want,
                        "error": "his monthly figures do not add up to the payment"})
    return out


def uncovered(conn: sqlite3.Connection, since: Optional[str] = SINCE) -> list:
    """Bank debits that `pnl` books as a TDS remittance and REMITTANCES does not
    cover. THE DIRECTION THAT MATTERS MOST.

    The booking rule is deliberately narrow (the CA's name AND the word TDS) so
    it can never mis-book; this is the wide end, and it is what turns his
    arrangement into a loop that runs itself. When September's statement lands
    with August's remittance in it, this is what says "you have not told me what
    month this is for" — instead of the payment quietly sitting in the month it
    cleared, which is what happened to every other accrual in this ledger until
    somebody noticed months later."""
    from modules.finance import pnl
    sql = ("SELECT record_id, canonical_key, substr(creation_dt,1,10) d, amount_minor,"
           " description, source, category, type, upwork_type, accounting_subtype,"
           " amount_base_minor FROM transactions"
           " WHERE source IN ('IDFC','Bank') AND amount_minor < 0"
           "   AND COALESCE(category,'') != 'internal_sweep'")
    args: list = []
    if since:
        sql += " AND substr(creation_dt,1,10) >= ?"
        args.append(since)
    out = []
    for r in conn.execute(sql + " ORDER BY creation_dt", args):
        row = dict(r)
        if not pnl.is_tds_remittance(row.get("description")):
            continue
        if row.get("canonical_key") in REMITTANCES:
            continue
        out.append(row)
    return out


# ── the split rows ───────────────────────────────────────────────────────────

def _splits() -> list:
    """A remittance covering several months lands whole in the FIRST of them
    (pinned by `accrual_pins`). These carry the rest across, net zero.

    Signs read the way the ledger reads: a cost is negative, so the month holding
    too much gets a POSITIVE contra and the month holding none gets the negative
    cost. Ugly to read once, correct in every total that matters."""
    rows = []
    for key, months in REMITTANCES.items():
        if len(months) < 2:
            continue
        holder = months[0]
        for m in months[1:]:
            amount = declared_minor(m)
            if not amount:
                continue
            rows.append({"remit": key, "month": holder, "pair": m, "role": "contra",
                         "amount_minor": amount,
                         "canonical_key": f"{KEY_PREFIX}|{key}|{holder}|contra"})
            rows.append({"remit": key, "month": m, "pair": holder, "role": "cost",
                         "amount_minor": -amount,
                         "canonical_key": f"{KEY_PREFIX}|{key}|{m}|cost"})
    return rows


def _describe(r: dict) -> str:
    """Each leg says what it is doing and to which payment, because in six months
    a bare 'TDS split' on a positive Salary row reads like a mistake."""
    amount = money.fmt(abs(r["amount_minor"]), "Rs ")
    if r["role"] == "contra":
        return (f"TDS split: {amount} of the remittance filed under {r['month']}"
                f" belongs to {r['pair']} and is carried out of {r['month']} here"
                " (his monthly figures, 2026-09-07)")
    return (f"TDS split: {amount}, the {r['month']} share of the remittance the"
            f" bank line files under {r['pair']} (his monthly figures, 2026-09-07)")


def rows_to_write(conn: sqlite3.Connection) -> list:
    """The split rows that are missing, priced at the REMITTANCE'S OWN pinned FX
    rate.

    Two refusals, both deliberate. A split whose remittance is not in the ledger
    is not written at all — an adjustment to a payment that does not exist is a
    number with nothing behind it. And a remittance with no `fx_rate` is skipped
    rather than priced at some other day's rate: the USD presentation of a split
    must net to zero against the row it splits, which is only true when both use
    the same rate."""
    out = []
    for r in _splits():
        if conn.execute("SELECT 1 FROM transactions WHERE canonical_key=?",
                        (r["canonical_key"],)).fetchone() is not None:
            continue
        src = conn.execute(
            "SELECT amount_minor, currency, fx_rate, fx_date FROM transactions"
            " WHERE canonical_key=?", (r["remit"],)).fetchone()
        if src is None or not src["fx_rate"]:
            continue
        row = dict(r)
        row["currency"] = src["currency"] or "INR"
        row["fx_rate"] = src["fx_rate"]
        row["fx_date"] = src["fx_date"]
        # magnitude first, sign after: |contra| == |cost| in USD as well as in
        # paise, so the pair cannot leave a rounding cent behind in the P&L.
        base = money.apply_rate(abs(r["amount_minor"]), src["fx_rate"])
        row["amount_base_minor"] = base if r["amount_minor"] > 0 else -base
        out.append(row)
    return out


def apply(conn: sqlite3.Connection) -> dict:
    """Insert the split rows that are missing. Idempotent by canonical key, and
    it never edits a bank row — the statement stays exactly what the bank sent."""
    todo = rows_to_write(conn)
    for r in todo:
        # NO `amount` (the legacy REAL column). Money in this module is integer
        # minor units end to end; putting a float in a money column is the exact
        # thing the §3.1 guard exists to stop, and nothing in v3 reads it.
        conn.execute(
            "INSERT INTO transactions (record_id, canonical_key, dedup_key, source,"
            " source_file, type, category, pnl_bucket, pnl_month, amount_minor,"
            " amount_base_minor, currency, fx_rate, fx_date, status, description,"
            " creation_dt, fully_paid_dt)"
            " VALUES (?,?,?,?,?, 'Salary','salary','payroll',?,?,?,?,?,?,'Cleared',?,?,?)",
            (r["canonical_key"], r["canonical_key"], r["canonical_key"], SOURCE,
             SOURCE_FILE, r["month"], r["amount_minor"],
             r["amount_base_minor"], r["currency"], r["fx_rate"], r["fx_date"],
             _describe(r), r["month"] + "-01", r["month"] + "-01"))
    return {"written": len(todo),
            "keys": [r["canonical_key"] for r in todo]}
