#!/usr/bin/env python3
"""Retire the pre-`covers` escalation ladder. Dry run unless --apply.

Eight active escalations, four real situations. They accumulated because Opus
minted a fresh dedup_key each sweep and escalations were never reconciled
against the cards they describe.

Every close below has its OWN evidential reason, quoted from the card itself —
not "looks similar". Two are kept and given a `covers` list retroactively so the
new reconcile pass can retire them on their own when the underlying commitments
close, instead of them living forever like these did.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

APPLY = "--apply" in sys.argv
sys.path.insert(0, str(Path.home() / "Desktop/Etherwise/etherwise-v3"))
from core import config, db                                 # noqa: E402
from modules.chief_of_staff import nudges                   # noqa: E402

CLOSE = {
    "escalate_commitment:554":
        "Subsumed by escalate_client:554-546, which cites this card by name "
        "('the double-flag escalation on 554') and covers 554 + 546 together.",
    "escalate_client:voice-coach-cluster":
        "Premise is now false on both counts: two of its three 'separate' rooms "
        "(room_1f5d0e3d, room_999337f3) are the SAME person, Dhanya Sangolli, "
        "and now collapse to one card; the third ('AI Automation Specialist') is "
        "Brandi Jordan at The Cradle Company, an active client, not a competing "
        "bid. There is no cluster to reconcile.",
    "escalate_workstream:aug10-crunch":
        "Same four commitments (522/554/568/587) as escalate_capacity:aug8-10, "
        "which is newer and names the gating dependency explicitly.",
    "escalate_workstream:aug8-10-capacity":
        "Same four commitments (522/554/568/587) as escalate_capacity:aug8-10; "
        "duplicate framing of one capacity squeeze.",
    "escalate_system:upwork_scan":
        "Explicitly superseded by escalate_system:upwork_scan_3x ('the existing "
        "escalate_system nudge only names runs 5008 and 5043').",
    "escalate_system:upwork_scan_3x":
        "Premise is false: the three failures were NOT consecutive. Run 5125 "
        "failed 23:15 on 08-11 and runs 5130/5136/5141/5147 all completed after "
        "it. upwork_scan is up; these were transient osascript timeouts.",
    # second pass, once covers were backfilled and the overlap was visible
    "escalate_client:554-546":
        "Resolves to the same constituent set as escalate_capacity:aug8-10 "
        "(commitment:554 is the only one of 546/554 with an active card), and "
        "the surviving card names #554 explicitly. Under the containment rule "
        "these are one situation; keeping both is the duplication Abhijeet "
        "reported as 'manychat is in there like 10 times'.",
    "recruiting_pipeline_link:522-568":
        "Strict subset of escalate_capacity:aug8-10, which states the same "
        "dependency ('overdue #522 gates Tanmay #568') plus the two client "
        "deadlines. Written by the sweep with a non-'escalate' kind, which is "
        "how it escaped both the cluster filter and reconciliation for 3 days.",
}

# the survivor gets the covers it should have recorded at birth, so the new
# reconcile pass can retire it on its own instead of it living forever
KEEP_COVERS = {
    "escalate_capacity:aug8-10": ["commitment:522", "commitment:554",
                                  "commitment:568", "commitment:587"],
}


def main() -> None:
    print(f"=== cleanup-escalations ({'APPLY' if APPLY else 'DRY RUN'}) ===")
    closed = kept = skipped = 0
    with db.connect(config.DB_PATH) as conn:
        active = {r["dedup_key"]: dict(r) for r in conn.execute(
            "SELECT id, dedup_key, state, title FROM nudges"
            " WHERE state IN ('open','snoozed') AND source='sweep'")}
    for key, why in CLOSE.items():
        row = active.get(key)
        if not row:
            print(f"\nSKIP  {key} — not active (already gone)")
            skipped += 1
            continue
        print(f"\nCLOSE #{row['id']} {key}\n      {why}")
        if APPLY:
            nudges.clear(config.DB_PATH, key)
        closed += 1
    with db.connect(config.DB_PATH) as conn:
        for key, covers in KEEP_COVERS.items():
            row = active.get(key)
            if not row:
                print(f"\nSKIP  {key} — not active")
                continue
            live = [c for c in covers if conn.execute(
                "SELECT 1 FROM nudges WHERE dedup_key=? AND state IN"
                " ('open','snoozed')", (c,)).fetchone()]
            note = live or "[] (none of its commitments are still open)"
            print(f"\nKEEP  #{row['id']} {key}")
            print(f"      covers -> {note}")
            kept += 1
            if APPLY and live:
                pl = json.loads(conn.execute(
                    "SELECT payload_json p FROM nudges WHERE id=?",
                    (row["id"],)).fetchone()["p"] or "{}")
                pl["covers"] = live
                conn.execute("UPDATE nudges SET payload_json=? WHERE id=?",
                             (json.dumps(pl), row["id"]))
    print(f"\n--- closed {closed}, kept {kept}, skipped {skipped}"
          f"{'' if APPLY else '  (DRY RUN — nothing written)'}")


if __name__ == "__main__":
    main()
