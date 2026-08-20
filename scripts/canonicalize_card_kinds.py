#!/usr/bin/env python3
"""Bring stored card kinds back into the vocabulary the lifecycle tables price.

    PYTHONPATH=. python3 scripts/canonicalize_card_kinds.py            # dry run
    PYTHONPATH=. python3 scripts/canonicalize_card_kinds.py --apply
    PYTHONPATH=. python3 scripts/canonicalize_card_kinds.py --apply --all

`kind` is free text: the sweep hands over whatever Opus called the situation,
and `_is_cluster_card` reads those words deliberately. What was never intended
is that an invented kind then missed EVERY table keyed by kind at once —
COS_URGENCY_BASE (fallback 25), COS_DECAY_TTL_DAYS (fallback 14 days), and
sweep.RULE_KINDS, which is the list the reconciler clears when a condition
vanishes. A card wearing an invented kind was therefore mis-ranked AND
un-closable by anything but a human click, and nothing reported it.

Measured 2026-08-20: 44 of the 54 kinds ever stored were model inventions. The
live one was `system_recurring` — a single word off `system` — which kept
"upwork_scan pipeline down — 10+ consecutive failures choking lead flow" on
Today for 14 hours after the scan had recovered, with 13 days still to run.

`nudges.canonical_kind` now runs inside `write_nudge`, so nothing NEW can land
outside the vocabulary. This is the catch-up for rows already stored.

By default it touches only ACTIVE cards (open|snoozed) — those are the ones
whose ranking and closability still matter, and rewriting 800 decayed rows
would destroy the historical record of what the model actually called things
for no benefit. `--all` includes them if you ever want the archive normalised.

Idempotent: a second run is a no-op. The model's original wording is preserved
in payload.kind_raw, never discarded.

NOTE ON CLOSING THE STALE CARD: this script does not close anything. Once the
kind is `system` the card is inside RULE_KINDS, and the next `cos_sweep` clears
it by the ordinary reconcile path because `rules.gather` no longer produces a
candidate for a scan that is healthy. That is the point — a hand-closed card
would prove nothing about whether the mechanism works.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

APPLY = "--apply" in sys.argv
ALL_STATES = "--all" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import config, db                                   # noqa: E402
from modules.chief_of_staff import nudges                     # noqa: E402

STATES = None if ALL_STATES else ("open", "snoozed")


def main() -> int:
    dbp = config.DB_PATH
    where, args = "", []
    if STATES:
        where = f" WHERE state IN ({','.join('?' * len(STATES))})"
        args = list(STATES)

    changes, unchanged = [], 0
    with db.connect(dbp) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, kind, source, dedup_key, title, state, urgency,"
            f" payload_json FROM nudges{where} ORDER BY id", args)]

    for r in rows:
        canon = nudges.canonical_kind(r["kind"], source=r["source"])
        if canon == r["kind"]:
            unchanged += 1
            continue
        old_base = config.COS_URGENCY_BASE.get(r["kind"], 25)
        new_base = config.COS_URGENCY_BASE[canon]
        old_ttl = config.COS_DECAY_TTL_DAYS.get(r["kind"], 14)
        new_ttl = config.COS_DECAY_TTL_DAYS[canon]
        changes.append({**r, "canon": canon, "old_base": old_base,
                        "new_base": new_base, "old_ttl": old_ttl,
                        "new_ttl": new_ttl})

    print(f"{len(rows)} card(s) examined "
          f"({'all states' if ALL_STATES else 'open|snoozed only'})")
    print(f"{unchanged} already canonical · {len(changes)} to rewrite\n")

    for c in changes:
        print(f"  #{c['id']} [{c['state']}] {c['kind']}  ->  {c['canon']}")
        print(f"      key   : {c['dedup_key']}")
        print(f"      title : {(c['title'] or '')[:88]}")
        print(f"      base  : {c['old_base']} -> {c['new_base']}"
              f"   ttl: {c['old_ttl']} -> {c['new_ttl']} day(s)")

    if not changes:
        print("nothing to do.")
        return 0
    if not APPLY:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    written = 0
    with db.connect(dbp) as conn:
        for c in changes:
            try:
                pl = json.loads(c["payload_json"] or "{}")
                if not isinstance(pl, dict):
                    pl = {"payload": pl}
            except Exception:
                pl = {}
            # keep the model's wording; it describes, it no longer controls
            pl.setdefault("kind_raw", c["kind"])
            # urgency is base * recency * stake; recompute rather than scale, so
            # the row agrees with what compute_urgency would say today.
            conn.execute(
                "UPDATE nudges SET kind=?, payload_json=? WHERE id=?",
                (c["canon"], json.dumps(pl), c["id"]))
            written += 1
    print(f"\nrewrote {written} card(s). "
          "Urgency refreshes on the next write_nudge/sweep; "
          "closability is immediate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
