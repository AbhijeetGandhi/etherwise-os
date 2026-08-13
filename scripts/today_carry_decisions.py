#!/usr/bin/env python3
"""Carry the founder's Done/Dismiss/Snooze across every follow-up RE-KEYING.

    PYTHONPATH=. python3 scripts/today_carry_decisions.py            # dry run
    PYTHONPATH=. python3 scripts/today_carry_decisions.py --apply

A follow-up card's key is derived from attribution (`followup:c:<client_id>`
once the thread is attributed, `followup:p:<slug>` before that), so every
improvement to attribution renames the card and orphans the decision filed under
the old name. It has happened three times:

    2026-08-12  room -> person    migrated by scripts/today_repair_migrate.py
    2026-08-13  person -> client  not migrated — Rocky Choy and Dhanya Sangolli
                                  were back on Today the next morning
    (next)      merge / rename / re-attribution

The structural fix is `modules/chief_of_staff/decisions.py`: a decision is found
through the THREADS it covered, so it survives any future re-keying without a
script. This script is the one-time catch-up for decisions taken before that
existed — and it is written to be general, so re-running it after ANY future
re-keying is a no-op rather than a fourth bespoke migration.

What it does, per decision whose key no longer names a live card:

  1. resolves the threads the decision covered (recorded subjects, else the key
     forms those threads carried — see nudges.followup_key_forms),
  2. maps them to the key that counterparty carries TODAY,
  3. carries the decision there, preserving the ORIGINAL timestamp — the
     evidence comparison in data._is_actioned asks "did they speak after he
     decided", so stamping it 'now' would silently outrank real client replies,
  4. records the subjects in `card_subjects`, so the next re-keying needs none
     of this.

It refuses to carry anything it cannot demonstrate is the same counterparty:
every row printed names the threads and the room names behind the claim.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

APPLY = "--apply" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import config, db                                     # noqa: E402
from modules.chief_of_staff import decisions, nudges            # noqa: E402

_RANK = {"done": 3, "dismissed": 2, "snoozed": 1}
_CARRYABLE = ("done", "dismissed", "snoozed")


def _utc_to_ist_naive(utc_ts: str) -> str:
    """'YYYY-MM-DD HH:MM:SS' UTC -> the IST-naive shape `nudges._iso()` writes.

    The two stores use DIFFERENT conventions for the same column name and they
    differ by 5h30m: nudge_state.updated_at is SQLite datetime('now') (UTC),
    nudges.updated_at is IST-naive. Carrying a timestamp between them without
    converting is the 5h30m window that discarded real client replies once
    already (nudges.ts_key docstring)."""
    d = datetime.strptime(utc_ts[:19], "%Y-%m-%d %H:%M:%S")
    from datetime import timezone
    return (d.replace(tzinfo=timezone.utc).astimezone(config.TZ)
            .strftime("%Y-%m-%dT%H:%M:%S"))


def _load(conn):
    """Every decision the founder has recorded on a follow-up card, in both
    stores, normalised to a real UTC instant so the two can be compared."""
    out = []
    for r in conn.execute("SELECT item_key, state, snooze_until, note,"
                          " updated_at FROM nudge_state"):
        if r["state"] in _CARRYABLE and decisions.is_followup_key(r["item_key"]):
            out.append({"store": "nudge_state", "id": None,
                        "key": decisions.base_key(r["item_key"]),
                        "state": r["state"], "snooze_until": r["snooze_until"],
                        "note": r["note"], "raw": str(r["updated_at"] or ""),
                        "utc": nudges.ts_key(r["updated_at"])})
    for r in conn.execute("SELECT id, dedup_key, state, snooze_until, note,"
                          " updated_at FROM nudges"):
        if r["state"] in _CARRYABLE and decisions.is_followup_key(r["dedup_key"]):
            out.append({"store": "nudges", "id": r["id"],
                        "key": decisions.base_key(r["dedup_key"]),
                        "state": r["state"], "snooze_until": r["snooze_until"],
                        "note": r["note"], "raw": str(r["updated_at"] or ""),
                        "utc": nudges.ts_key(r["updated_at"], naive="ist")})
    return out


def _evidence_ts(payload_json):
    """When the counterparty last spoke, as the card itself recorded it."""
    import json
    try:
        return (json.loads(payload_json or "{}") or {}).get("evidence_ts")
    except (TypeError, ValueError):
        return None


def _better(a, b):
    """The decision that stands: the most recent one; state rank breaks a tie."""
    if a is None:
        return b
    return b if (b["utc"], _RANK.get(b["state"], 0)) > \
                (a["utc"], _RANK.get(a["state"], 0)) else a


def main() -> int:
    print(f"=== carry follow-up decisions across re-keyings"
          f" ({'APPLY' if APPLY else 'DRY RUN'}) ===\n")
    with db.connect(config.DB_PATH) as conn:
        threads = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, room_name, client_id FROM threads")}
        live = set()
        for r in conn.execute(
                "SELECT d.thread_id, t.room_name, t.client_id FROM drafts d"
                " JOIN threads t ON t.id=d.thread_id"
                " WHERE d.sent_status='pending'"):
            live.add(nudges.followup_key(r["client_id"], r["room_name"],
                                         r["thread_id"]))
        for r in conn.execute("SELECT dedup_key FROM nudges WHERE state IN"
                              " ('open','snoozed')"):
            if decisions.is_followup_key(r["dedup_key"]):
                live.add(decisions.base_key(r["dedup_key"]))

        rows = _load(conn)
        fwd = decisions.forward_map(conn, sorted({r["key"] for r in rows}))

        # winner per (target key, store) — one carry each, most recent wins
        winners: dict = {}
        moves: list = []
        for r in rows:
            for target in fwd.get(r["key"], ()):
                cov = sorted(decisions.cover(conn, r["key"]))
                moves.append((r, target, cov))
                slot = (target, r["store"])
                winners[slot] = _better(winners.get(slot),
                                        dict(r, target=target, cover=cov))
        chosen = {(w["store"], w["id"], w["key"], w["target"])
                  for w in winners.values()}

        print(f"decisions on follow-up cards: {len(rows)}"
              f"   keys whose identity has moved: {len(fwd)}"
              f"   live card keys: {len(live)}\n")
        if not moves:
            print("nothing to carry — every decision is filed under a key the"
                  " producers still emit.")
        for r, target, cov in sorted(moves, key=lambda m: (m[1], m[0]["utc"])):
            mark = "*" if (r["store"], r["id"], r["key"], target) in chosen else " "
            onboard = "ON TODAY" if target in live else "not on the board"
            print(f" {mark} {r['state']:9} {r['key']}")
            print(f"      -> {target}   [{onboard}]")
            print(f"      {r['store']}"
                  + (f" #{r['id']}" if r["id"] else "")
                  + f"  decided {r['raw']}  (= {r['utc']} UTC)")
            for tid in cov:
                t = threads.get(tid) or {}
                print(f"      same counterparty: {tid}"
                      f"  room={(t.get('room_name') or '-')[:38]!r}"
                      f"  client_id={t.get('client_id') or '-'}")
        print("\n  * = the decision that will be carried (most recent per"
              " target key + store); the others are older history\n")

        # what would change
        planned = []
        for (target, store), w in sorted(winners.items()):
            if store == "nudge_state":
                cur = conn.execute("SELECT state, updated_at FROM nudge_state"
                                   " WHERE item_key=?", (target,)).fetchone()
                if cur and nudges.ts_key(cur["updated_at"]) >= w["utc"]:
                    print(f"skip  {target} [nudge_state] — already holds a"
                          f" decision at {cur['updated_at']} (>= {w['utc']})")
                    continue
                planned.append((target, store, w, cur))
            else:
                cur = conn.execute(
                    "SELECT id, state, updated_at, payload_json FROM nudges"
                    " WHERE dedup_key=? AND state IN ('open','snoozed')"
                    " ORDER BY id DESC LIMIT 1", (target,)).fetchone()
                if not cur:
                    print(f"skip  {target} [nudges] — no open row to close"
                          f" (render-side forwarding covers it)")
                    continue
                # A DONE DECAYS ON NEWER EVIDENCE, AND SO MUST A CARRIED ONE.
                # Closing an open row is the destructive half of this script:
                # get it wrong and real work disappears. So it obeys the same
                # rule data._is_actioned does — if the counterparty has spoken,
                # on any channel, since he decided, the card is genuinely live
                # and the row is left alone. Live on 2026-08-14: Tousif Ahmed's
                # Done was 08-07 and he wrote back on 08-12.
                ev = _evidence_ts(cur["payload_json"])
                if ev and nudges.ts_key(ev) > w["utc"]:
                    print(f"skip  {target} [nudges #{cur['id']}] — they have"
                          f" spoken since ({ev}) the decision ({w['utc']} UTC);"
                          f" the card is correctly open")
                    continue
                planned.append((target, store, w, cur))

        print()
        for target, store, w, cur in planned:
            if store == "nudge_state":
                print(f"WRITE nudge_state {target}  <- {w['state']}"
                      f" @{w['utc']} UTC (was:"
                      f" {cur['state'] + ' @' + str(cur['updated_at']) if cur else 'no row'})")
            else:
                print(f"WRITE nudges #{cur['id']} {target}  {cur['state']}"
                      f" -> {w['state']} @{_utc_to_ist_naive(w['utc'])} IST"
                      f"  (carried from {w['key']})")

        backfillable = sorted({r["key"] for r in rows} |
                              {t for ts in fwd.values() for t in ts})
        if not APPLY:
            print(f"\nDRY RUN — {len(planned)} writes, plus card_subjects for"
                  f" {len(backfillable)} keys. Re-run with --apply.")
            return 0

        wrote = decisions.backfill(conn, backfillable)
        for target, store, w, cur in planned:
            note = (f"carried from {w['key']} (card re-keyed by attribution;"
                    f" original decision {w['raw']})")
            if store == "nudge_state":
                conn.execute(
                    "INSERT INTO nudge_state (item_key, state, snooze_until,"
                    " note, source, updated_at) VALUES (?,?,?,?,'carry',?)"
                    " ON CONFLICT(item_key) DO UPDATE SET state=excluded.state,"
                    " snooze_until=excluded.snooze_until, note=excluded.note,"
                    " source='carry', updated_at=excluded.updated_at",
                    (target, w["state"], w["snooze_until"], note, w["utc"]))
            else:
                conn.execute(
                    "UPDATE nudges SET state=?, snooze_until=?,"
                    " note=COALESCE(note,'') || ? , updated_at=? WHERE id=?",
                    (w["state"], w["snooze_until"], " " + note,
                     _utc_to_ist_naive(w["utc"]), cur["id"]))
        print(f"\napplied: {len(planned)} decisions carried;"
              f" card_subjects written for {len(wrote)} keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
