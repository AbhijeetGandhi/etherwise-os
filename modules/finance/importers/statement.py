"""Shared statement-integrity (§3.3 #1/#3) — used by every importer + the
doctor. A statement's `Running/Current balance` is checked in the file's NATIVE
order (the source's authoritative sequence, so same-day transactions tie out;
re-sorting by date breaks settlement triples). Balance is AFTER each row. Most
exports are newest-first: consecutive rows (i newer, i+1 older) satisfy
balance[i] − amount[i] == balance[i+1]; oldest-first is detected + handled.

Rows are dicts with: amount_minor (int|None), balance_minor (int|None),
date (str|None), and a label key (key|ref_id|canonical_key). A blank amount
quarantines + breaks the chain — never silently passed. Pure integer math.
"""
from __future__ import annotations

from typing import List


def _label(r: dict) -> str:
    return str(r.get("key") or r.get("ref_id") or r.get("canonical_key") or "?")


def reconcile_running_balance(rows: List[dict]) -> dict:
    dated = [r for r in rows if r.get("date")]
    newest_first = (len(dated) < 2) or (dated[0]["date"] >= dated[-1]["date"])
    breaks, quarantine = [], []
    for i in range(len(rows) - 1):
        cur, nxt = rows[i], rows[i + 1]
        if cur["amount_minor"] is None:
            quarantine.append({"key": _label(cur), "reason": "blank amount"})
            continue
        if cur["balance_minor"] is None or nxt["balance_minor"] is None:
            continue
        if newest_first:
            expected, actual = cur["balance_minor"] - cur["amount_minor"], nxt["balance_minor"]
        else:
            if nxt["amount_minor"] is None:
                continue
            expected, actual = cur["balance_minor"] + nxt["amount_minor"], nxt["balance_minor"]
        if expected != actual:
            breaks.append({"key": _label(cur), "expected_minor": expected,
                           "actual_minor": actual, "delta_minor": expected - actual})
    if rows and rows[-1]["amount_minor"] is None:
        quarantine.append({"key": _label(rows[-1]), "reason": "blank amount"})
    return {"ok": not breaks and not quarantine, "breaks": breaks,
            "quarantine": quarantine, "rows": len(rows)}
