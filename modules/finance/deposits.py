"""The fixed-deposit principal — the biggest number in "in the bank", and the
one nothing was updating.

Abhijeet, 2026-09-05: *"cash in the bank doesn't seems updated"*. It was not.
IDFC's auto-sweep pins the savings closing at almost exactly Rs 5,00,000, so
nearly everything he holds sits in the LINKED FIXED DEPOSITS — and that figure
had been typed in by hand on 26 June and had not moved in 71 days, while the
screen presented it beside a savings balance that was current to the hour.

`bin/finance-import-idfc` was right to refuse to overwrite it. Its derived
estimate is `-(net internal_sweep)` OVER ONE WINDOW, which assumes the deposits
were empty when the window opened; applied to an account with standing FDs it
would not have been stale, it would have been catastrophically wrong. So the
authoritative anchor is KEPT, and rolled forward instead.

Every rupee that moves between savings and the deposits leaves a statement line:

    'Sweepout FD 10298600286 booked'                      debit  -> INTO the FD
    'Auto sweep in from 10298600286/Principal:259/ ...'   credit -> OUT of it
    'FD 10241213363 ETHERWISE'                            debit  -> INTO the FD

THE PRINCIPAL IS READ OUT OF THE TEXT, not taken from the amount. A sweep-in
credits principal PLUS the net interest it earned ("Principal:21917/Gross
Interest:14/TDS:1" arrives as Rs 21,930), and subtracting the credited amount
would shrink the deposit by its own interest every single quarter.

An estimate, and labelled as one in `source_file`: a deposit that matures and
renews INSIDE the FD system never touches savings, so this cannot see it — but
that case leaves the principal unchanged anyway, which is the direction that
costs nothing. What it CAN see, it gets exactly. His own app figure always
outranks it: `--fd-balance-inr` sets a new anchor and the roll starts from there.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Optional

from modules.finance import money

ACCOUNT = "idfc-fd"
BANK_SOURCES = ("IDFC", "Bank")

# "Auto sweep in from 10298600286/Principal:259/ UPI/DR/..." — IDFC's own
# wording. Anchored on the label so a stray number elsewhere cannot match.
_PRINCIPAL = re.compile(r"Principal:(\d+(?:\.\d+)?)")


def anchor(conn: sqlite3.Connection) -> tuple:
    """The newest HAND-ENTERED deposit total: (minor, 'YYYY-MM-DD').

    Only an `authoritative:` row may anchor. A derived row must never anchor the
    next derivation or the estimate would compound on itself and drift with no
    way back to a fact he supplied."""
    r = conn.execute(
        "SELECT closing_minor, statement_date FROM account_balances"
        " WHERE account=? AND source_file LIKE 'authoritative:%'"
        " ORDER BY statement_date DESC, id DESC LIMIT 1", (ACCOUNT,)).fetchone()
    if r is None:
        return 0, None
    return r["closing_minor"], (r["statement_date"] or "")[:10]


def net_sweeps(conn: sqlite3.Connection, since: Optional[str]) -> dict:
    """Money into the deposits minus principal out of them, STRICTLY AFTER
    `since` (the anchor day's own legs are already inside his app figure).

    `since` None means no anchor exists, and then there is nothing to roll: a
    from-zero sum would silently invent the standing deposits as new inflow."""
    out = {"into_minor": 0, "out_minor": 0, "interest_minor": 0,
           "net_minor": 0, "legs": 0, "unparsed": 0}
    if not since:
        return out
    marks = ",".join("?" * len(BANK_SOURCES))
    for r in conn.execute(
            f"SELECT amount_minor a, COALESCE(description,'') d FROM transactions"
            f" WHERE source IN ({marks}) AND category='internal_sweep'"
            f"   AND substr(creation_dt,1,10) > ?"
            f" ORDER BY creation_dt", (*BANK_SOURCES, since)):
        amount = r["a"] or 0
        out["legs"] += 1
        if amount < 0:                          # left savings -> into the deposit
            out["into_minor"] += -amount
            continue
        hit = _PRINCIPAL.search(r["d"])         # arrived in savings -> out of it
        if hit:
            principal = money.to_minor(hit.group(1))
            out["out_minor"] += principal
            out["interest_minor"] += amount - principal
        else:
            out["out_minor"] += amount          # no principal stated: take it whole
            out["unparsed"] += 1
    out["net_minor"] = out["into_minor"] - out["out_minor"]
    return out


def rolled(conn: sqlite3.Connection, as_of: Optional[str] = None) -> Optional[dict]:
    """The deposit principal today, or None when no anchor exists to roll.

    None is not zero and the caller must not treat it as one — with no figure
    from him there is nothing to say, and saying nothing beats presenting a
    guessed cash position as fact."""
    base, since = anchor(conn)
    if since is None:
        return None
    moved = net_sweeps(conn, since)
    return {"balance_minor": base + moved["net_minor"],
            "anchor_minor": base, "anchor_date": since,
            "as_of": as_of or since, **moved}


def source_label(roll: dict) -> str:
    """What `account_balances.source_file` says, so the provenance survives the
    row: which fact it started from, and that the rest is derived."""
    return f"derived:fd-rolled-from-authoritative:{roll['anchor_date']}"
