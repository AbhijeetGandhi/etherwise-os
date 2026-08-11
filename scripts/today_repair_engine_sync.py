#!/usr/bin/env python3
"""Backfill the write-through that never existed. Dry run unless --apply.

Every Done/Dismiss/Snooze the founder has ever clicked went into nudge_state
alone. The nudges table — what the morning brief, top_n and the sweep read —
never heard about any of it. Carry those decisions across now, with the same
evidence rule the live path uses: a decision does NOT apply if the counterparty
has said something since.
"""
from __future__ import annotations

import sys
from pathlib import Path

APPLY = "--apply" in sys.argv
sys.path.insert(0, str(Path.home() / "Desktop/Etherwise/etherwise-v3"))
from core import config, db                                 # noqa: E402
from modules.chief_of_staff import nudges                   # noqa: E402

def main() -> None:
    print(f"=== sync-engine-state ({'APPLY' if APPLY else 'DRY RUN'}) ===")
    applied = revived = 0
    with db.connect(config.DB_PATH) as conn:
        latest = {}
        for r in conn.execute(
            "SELECT t.client_id, t.room_name, t.id, t.latest_message_dt m"
            " FROM threads t"):
            k = nudges.followup_key(r["client_id"], r["room_name"], r["id"])
            if nudges.ts_key(r["m"]) > nudges.ts_key(latest.get(k, "")):
                latest[k] = r["m"]
        rows = [dict(r) for r in conn.execute(
            "SELECT item_key, state, snooze_until, updated_at FROM nudge_state"
            " WHERE state IN ('done','dismissed','snoozed')")]
    for s in rows:
        key = s["item_key"][6:] if s["item_key"].startswith("nudge:") \
            else s["item_key"]
        with db.connect(config.DB_PATH) as conn:
            n = conn.execute(
                "SELECT id, state FROM nudges WHERE dedup_key=? AND state IN"
                " ('open','snoozed')", (key,)).fetchone()
        if not n:
            continue
        ev = latest.get(key)
        if s["state"] != "snoozed" and ev and \
                nudges.ts_key(ev) > nudges.ts_key(s["updated_at"]):
            print(f"  REVIVE  {key}: replied {str(ev)[:10]} after"
                  f" {s['state']} {str(s['updated_at'])[:10]} — leaving open")
            revived += 1
            continue
        print(f"  {s['state']:9} -> engine  {key}")
        applied += 1
        if APPLY:
            nudges.set_state(config.DB_PATH, state=s["state"], dedup_key=key,
                             snooze_until=s["snooze_until"])
    print(f"\n--- {applied} carried across, {revived} left open on new evidence"
          f"{'' if APPLY else '  (DRY RUN)'}")

if __name__ == "__main__":
    main()
