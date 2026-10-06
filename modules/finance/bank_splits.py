"""One bank payment that was two things.

Abhijeet, 2026-10-06, on Rs 1,15,000 paid to his CA on 19 Sep 2026 with the
narration "Professional fees": *"55000 were fees. rest we got cash"*.

So a single statement line carries a Rs 55,000 cost of running the business AND
a Rs 60,000 draw, and a ledger row can sit in only one bucket. Leaving all of it
where an unlisted bank spend lands (an owner draw) hides a real professional fee
from the P&L. Calling all of it a cost books Rs 60,000 of expense the business
never bore, because that cash came back -- the one thing this ledger exists to
prevent, and the same reasoning as `pnl._PERSONAL_COUNTERPARTIES`. What his
RETURN says about the payment is between him and his CA; this records where the
money went.

HOW IT LANDS -- the mechanism `payroll_tax` already uses for a remittance that
covers two months. The bank row is never edited: it stays exactly what the bank
sent, in the bucket `pnl` gives it, and it still ties the statement out. Two
derived rows on the hand-entered `Direct` source re-file the stated share:

    contra   +share   in the bank row's own bucket    takes it OUT of there
    cost     -share   in the bucket he named          puts it where it belongs

Both carry the bank row's date and the bank row's own pinned FX rate, so the pair
nets to zero in paise AND in the USD presentation, and the month's total across
all buckets is still exactly what left the account.

A LIST OF FACTS, NOT A RULE. "Professional fees" to the CA is not always part fee
and part cash; the next one is whatever he says it is. Each entry is ONE payment,
keyed on its canonical key, with what he said about it beside it.

AND A CHECK THAT HOLDS THE LIST TO THE LEDGER (doctor #15). A split is only right
while the bank row is still in the bucket it is being carried out of: if a later
rule moved the CA's payment to opex wholesale, these rows would count the fee
twice. So #15 goes RED on that, on a share that is not strictly inside the
payment, on a leg that is missing or no longer equals the share, and on a leg
whose entry has been deleted from the list.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from modules.finance import money

SOURCE = "Direct"
SOURCE_FILE = "derived:bank-split"
# NOT 'bank:...'. `bin/finance-import-idfc` treats every non-IDFC row whose key
# starts 'bank:' inside a statement window as a legacy v2 row and quarantines it;
# a derived row that looked like one would be deleted by the next import.
KEY_PREFIX = "split"

# canonical key of the bank row -> the part of it that is NOT what the row's own
# bucket says.
#   share_minor   that part, in paise; strictly less than the payment
#   from_bucket   where `pnl.bucket` files the whole bank row (asserted by #15)
#   to_bucket     what the share really is
#   label         shown beside both legs on the Money page, so they read as a pair
#   what          the share in words, for the row descriptions
#   on            the day he said it
SPLITS = {
    # 19 Sep 2026 - Rs 1,15,000.00
    # IFT/10171724864/PRITI BORA & CO /900638497/Professional fees
    # "55000 were fees. rest we got cash"
    "bank:9c2696cf7d475e96": {
        "share_minor": 5500000,
        "from_bucket": "owner_draw",
        "to_bucket": "opex",
        "label": "CA fee",
        "what": "CA professional fee, Priti Bora & Co",
        "on": "2026-10-06",
    },
}

# bucket -> the `category` that makes `pnl.bucket` return it for a Direct row, so
# the stored pnl_bucket stays reproducible (doctor #10). Only the buckets a split
# has needed; another is added here together with its test, never assumed.
_CATEGORY = {"opex": "expense", "owner_draw": "owner_draw", "payroll": "salary"}

_PARENT_COLS = ("canonical_key, source, type, upwork_type, category,"
                " accounting_subtype, description, amount_minor, amount_base_minor,"
                " currency, fx_rate, fx_date, creation_dt, pnl_month")


def _legs() -> list:
    """Every row the list above calls for, written or not."""
    rows = []
    for parent, spec in SPLITS.items():
        share = spec["share_minor"]
        rows.append({"parent": parent, "role": "contra", "bucket": spec["from_bucket"],
                     "amount_minor": share,
                     "canonical_key": f"{KEY_PREFIX}|{parent}|contra"})
        rows.append({"parent": parent, "role": "cost", "bucket": spec["to_bucket"],
                     "amount_minor": -share,
                     "canonical_key": f"{KEY_PREFIX}|{parent}|cost"})
    return rows


def _parent(conn: sqlite3.Connection, key: str) -> Optional[dict]:
    r = conn.execute(f"SELECT {_PARENT_COLS} FROM transactions WHERE canonical_key=?",
                     (key,)).fetchone()
    return dict(r) if r is not None else None


def _fault(parent: dict, spec: dict) -> Optional[str]:
    """Why this payment cannot carry this split, or None when it can. One place,
    because the writer must refuse exactly what the doctor reports."""
    from modules.finance import pnl
    paid = -(parent.get("amount_minor") or 0)
    share = spec["share_minor"]
    if paid <= 0:
        return "the bank row is not a payment (it is a credit, or has no amount)"
    if not 0 < share < paid:
        return (f"the share ({share} paise) is not strictly inside the payment"
                f" ({paid} paise) -- a split of all or none of it is not a split")
    if spec["from_bucket"] not in _CATEGORY or spec["to_bucket"] not in _CATEGORY:
        return "a bucket this module has no row shape for"
    now = pnl.bucket(parent)
    if now != spec["from_bucket"]:
        return (f"the bank row is filed under {now}, not {spec['from_bucket']} --"
                " the split would move money out of a bucket it is not in")
    if not parent.get("fx_rate"):
        return "the bank row has no pinned FX rate, so the legs cannot be priced"
    return None


def in_ledger(conn: sqlite3.Connection) -> int:
    """How many of the listed payments are actually here. Zero is the honest
    state of a ledger that has not imported that statement, and is not an error."""
    return sum(1 for key in SPLITS if _parent(conn, key) is not None)


def _rs(minor: int) -> str:
    """'Rs 1,15,000.00' -- lakh grouping, the way he writes and reads rupees.
    `money.fmt` groups in thousands ('115,000.00'), which is right for the USD
    the rest of the ledger presents and reads as a different number here."""
    whole, _, paise = str(money.from_minor(abs(int(minor)))).partition(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ("-" if minor < 0 else "") + "Rs " + ",".join(groups + [tail]) + "." + paise


def _describe(leg: dict, spec: dict, parent: dict) -> str:
    """Each leg says what it is doing and to which payment, because in six months
    a positive row in owner draws with no explanation reads like a mistake."""
    paid = -parent["amount_minor"]
    share = _rs(spec["share_minor"])
    total = _rs(paid)
    rest = _rs(paid - spec["share_minor"])
    day = (parent.get("creation_dt") or "")[:10]
    if leg["role"] == "cost":
        return (f"{spec['what']}: {share} of the {total} bank payment of {day};"
                f" the other {rest} stays under {spec['from_bucket']}"
                f" (his split, {spec['on']})")
    return (f"Split: {share} of the {total} bank payment of {day} was"
            f" {spec['what']} -- carried out of {spec['from_bucket']} here and"
            f" filed under {spec['to_bucket']} by the matching row"
            f" (his split, {spec['on']})")


def rows_to_write(conn: sqlite3.Connection) -> list:
    """The legs that are missing, priced at the BANK ROW'S OWN pinned FX rate.

    Nothing is written for a payment that is not in the ledger (an adjustment to
    a payment that does not exist is a number with nothing behind it) or that
    fails `_fault` -- those are #15's to report, not this function's to paper
    over."""
    out = []
    for leg in _legs():
        if conn.execute("SELECT 1 FROM transactions WHERE canonical_key=?",
                        (leg["canonical_key"],)).fetchone() is not None:
            continue
        spec = SPLITS[leg["parent"]]
        parent = _parent(conn, leg["parent"])
        if parent is None or _fault(parent, spec):
            continue
        row = dict(leg)
        row["currency"] = parent["currency"] or "INR"
        row["fx_rate"] = parent["fx_rate"]
        row["fx_date"] = parent["fx_date"]
        # magnitude first, sign after: |contra| == |cost| in USD as well as in
        # paise, so the pair cannot leave a rounding cent behind in the P&L.
        base = money.apply_rate(abs(leg["amount_minor"]), parent["fx_rate"])
        row["amount_base_minor"] = base if leg["amount_minor"] > 0 else -base
        row["creation_dt"] = parent["creation_dt"]
        row["pnl_month"] = parent["pnl_month"]      # the pair follows its payment
        row["type"] = spec["label"]
        row["category"] = _CATEGORY[leg["bucket"]]
        row["description"] = _describe(leg, spec, parent)
        out.append(row)
    return out


def apply(conn: sqlite3.Connection) -> dict:
    """Insert the legs that are missing. Idempotent by canonical key, and it
    never edits a bank row -- the statement stays exactly what the bank sent."""
    todo = rows_to_write(conn)
    for r in todo:
        # NO `amount` (the legacy REAL column): money here is integer minor
        # units end to end, exactly as in payroll_tax.apply.
        conn.execute(
            "INSERT INTO transactions (record_id, canonical_key, dedup_key, source,"
            " source_file, type, category, pnl_bucket, pnl_month, amount_minor,"
            " amount_base_minor, currency, fx_rate, fx_date, status, description,"
            " creation_dt, fully_paid_dt)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'Cleared',?,?,?)",
            (r["canonical_key"], r["canonical_key"], r["canonical_key"], SOURCE,
             SOURCE_FILE, r["type"], r["category"], r["bucket"], r["pnl_month"],
             r["amount_minor"], r["amount_base_minor"], r["currency"], r["fx_rate"],
             r["fx_date"], r["description"], r["creation_dt"], r["creation_dt"]))
    return {"written": len(todo), "keys": [r["canonical_key"] for r in todo]}


def problems(conn: sqlite3.Connection) -> list:
    """Everything that makes a split wrong rather than merely unwritten. Doctor
    #15 is this list; an empty list plus nothing left to write is GREEN."""
    out = []
    declared = {leg["canonical_key"]: leg for leg in _legs()}
    written = {r["canonical_key"]: dict(r) for r in conn.execute(
        "SELECT canonical_key, amount_minor, amount_base_minor, pnl_bucket"
        " FROM transactions WHERE source_file=?", (SOURCE_FILE,))}
    for key in sorted(set(written) - set(declared)):
        out.append({"key": key, "error": "a split row whose payment is no longer"
                    " in bank_splits.SPLITS -- it is still moving money"})
    for parent_key, spec in SPLITS.items():
        legs = [leg for leg in declared.values() if leg["parent"] == parent_key]
        have = [written[leg["canonical_key"]] for leg in legs
                if leg["canonical_key"] in written]
        parent = _parent(conn, parent_key)
        if parent is None:
            if have:
                out.append({"key": parent_key, "error": "split rows exist but the"
                            " bank payment they split is not in the ledger"})
            continue
        fault = _fault(parent, spec)
        if fault:
            out.append({"key": parent_key, "error": fault})
            continue
        for leg in legs:
            row = written.get(leg["canonical_key"])
            if row is None:
                continue                         # unwritten: rows_to_write's job
            if row["amount_minor"] != leg["amount_minor"]:
                out.append({"key": leg["canonical_key"],
                            "declared_minor": leg["amount_minor"],
                            "written_minor": row["amount_minor"],
                            "error": "the written leg no longer equals the share"})
            if row["pnl_bucket"] != leg["bucket"]:
                out.append({"key": leg["canonical_key"], "stored": row["pnl_bucket"],
                            "declared": leg["bucket"],
                            "error": "the written leg is in the wrong bucket"})
        if len(have) == 2 and (sum(r["amount_minor"] for r in have)
                               or sum(r["amount_base_minor"] or 0 for r in have)):
            out.append({"key": parent_key,
                        "error": "the two legs do not net to zero"})
    return out
