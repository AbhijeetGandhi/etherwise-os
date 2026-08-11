#!/usr/bin/env python3
"""One-off migration for the 2026-08-12 Today-tab repair. Prints a plan, and
only writes with --apply.

Three things move:

1. THREAD ATTRIBUTION — link_threads now reads the registry instead of the
   hardcoded 4-client pilot roster, so run it.

2. FOLLOW-UP KEYS — cards are keyed on the counterparty, not the room. Abhijeet's
   existing Done/Dismissed marks are keyed on rooms; if they were simply dropped
   every follow-up he has ever dealt with would reappear tomorrow, which is the
   exact failure this whole change exists to fix. For each new group key we carry
   over the LATEST action across its member rooms (the most recent decision is
   the one that stands) and keep the old rows untouched as an audit trail.

3. ESCALATION LADDER — the pre-`covers` escalations cannot be reconciled
   automatically because they never recorded what they were about. They are
   listed here individually with their overlap so the redundant ones can be
   closed on evidence rather than in bulk.
"""
from __future__ import annotations

import sys
from pathlib import Path

APPLY = "--apply" in sys.argv
sys.path.insert(0, str(Path.home() / "Desktop/Etherwise/etherwise-v3"))
from core import config, db, registry                       # noqa: E402
from modules.chief_of_staff import nudges                   # noqa: E402
from modules.knowledge import clients                       # noqa: E402

_RANK = {"done": 3, "dismissed": 2, "snoozed": 1}


def main() -> None:
    print(f"=== migrate-today ({'APPLY' if APPLY else 'DRY RUN'}) ===\n")

    # 1 ── thread attribution ------------------------------------------------
    cmap = clients.load_client_map()
    print(f"1. roster: {len(cmap)} clients from the registry"
          f" (was 4 hardcoded)")
    if APPLY:
        res = clients.link_threads(config.DB_PATH, cmap)
        print(f"   linked={res['linked']} unmatched={res['unmatched']}")
        for cid, n in sorted(res["by_client"].items(), key=lambda x: -x[1]):
            print(f"     {n:3}  {cid}")
    else:
        reg = registry.load()
        with db.connect(config.DB_PATH) as conn:
            would = 0
            for t in conn.execute("SELECT id, room_name, topic, client_id"
                                  " FROM threads WHERE client_id IS NULL"):
                p = conn.execute(
                    "SELECT GROUP_CONCAT(DISTINCT sender_org_name) o,"
                    " GROUP_CONCAT(DISTINCT sender_user_name) u FROM messages"
                    " WHERE thread_id=? AND sender_user_id!=?",
                    (t["id"], clients._OWNER_USER_ID)).fetchone()
                cid = reg.match_upwork_room(t["id"]) or clients.match_counterparty(
                    [t["room_name"], p["o"] if p else None,
                     p["u"] if p else None, t["topic"]], cmap)[0]
                if cid:
                    would += 1
                    print(f"     would link {t['room_name']!r} -> {cid}")
            print(f"   would link {would}")

    # 2 ── follow-up state keys ----------------------------------------------
    print("\n2. follow-up Done/Dismissed marks -> counterparty keys")
    with db.connect(config.DB_PATH) as conn:
        rooms = {r["id"]: r for r in conn.execute(
            "SELECT id, room_name, client_id FROM threads")}
        moves: dict = {}
        # BOTH key forms the cockpit can write: the follow-up card sends
        # 'followup:<room>' and the owed_reply nudge sends 'nudge:followup:<room>'.
        # Dropping either would lose real decisions.
        for s in conn.execute(
                "SELECT item_key, state, snooze_until, updated_at FROM"
                " nudge_state WHERE item_key LIKE 'followup:%'"
                " OR item_key LIKE 'nudge:followup:%'"):
            ik = s["item_key"]
            pre = "nudge:" if ik.startswith("nudge:") else ""
            tid = ik[len(pre):].split(":", 1)[1]
            if not tid.startswith("room_"):
                continue                       # already migrated
            t = rooms.get(tid)
            if not t:
                continue                       # thread gone; nothing to carry
            new = pre + nudges.followup_key(t["client_id"], t["room_name"], tid)
            if new == s["item_key"]:
                continue
            cur = moves.get(new)
            # the most recent decision wins; state rank only breaks a tie
            if (not cur or (str(s["updated_at"]), _RANK.get(s["state"], 0))
                    > (str(cur["updated_at"]), _RANK.get(cur["state"], 0))):
                moves[new] = dict(s)
        for new, s in sorted(moves.items()):
            print(f"   {s['state']:9} {s['item_key']} -> {new}"
                  f"  (@{str(s['updated_at'])[:16]})")
        if APPLY:
            for new, s in moves.items():
                conn.execute(
                    "INSERT INTO nudge_state (item_key, state, snooze_until,"
                    " updated_at) VALUES (?,?,?,?)"
                    " ON CONFLICT(item_key) DO UPDATE SET state=excluded.state,"
                    " snooze_until=excluded.snooze_until,"
                    " updated_at=excluded.updated_at",
                    (new, s["state"], s["snooze_until"], s["updated_at"]))
        print(f"   {len(moves)} carried over"
              f"{'' if APPLY else ' (dry run)'}")

    # 3 ── legacy escalations -------------------------------------------------
    print("\n3. escalations with no `covers` (pre-2026-08-12, unreconcilable)")
    with db.connect(config.DB_PATH) as conn:
        for r in conn.execute(
            "SELECT id, kind, dedup_key, created_at, title, payload_json"
            " FROM nudges WHERE state IN ('open','snoozed') AND kind LIKE"
            " 'escalate%' ORDER BY created_at"):
            print(f"   #{r['id']} {r['created_at'][:10]} {r['dedup_key']}")
            print(f"        {r['title'][:96]}")


if __name__ == "__main__":
    main()
