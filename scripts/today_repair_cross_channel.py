#!/usr/bin/env python3
"""Cross-channel Today repair — DRY RUN unless --apply.

Two jobs, both consequences of the 2026-08-12 cross-platform fixes:

  1. REPORT (always). Per follow-up card: what the board says today under the
     new rules, what it said under the old Upwork-only rule, and every piece of
     evidence behind the difference — per row, with reasoning, so the decision
     is inspectable before anything is written.

  2. REPAIR (--apply, OPTIONAL AND NOT REQUIRED). Back-fill `nudge_state` from
     the `nudges` table for follow-up keys where the founder's Done landed in
     the engine only (/api/cos-nudge wrote the engine, /api/nudge wrote the
     overlay). data.today() now MERGES both stores at read time, and nothing
     else in the repo reads nudge_state, so the divergence is already handled in
     code — this exists only if you want the two tables to physically agree.
     It never invents a decision: it copies an existing done/dismissed/snoozed
     row and never downgrades a newer overlay decision.

Nothing here touches Airtable / ClickUp / Upwork. Local SQLite only.
"""
from __future__ import annotations

import sys
from pathlib import Path

APPLY = "--apply" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import config, db                                    # noqa: E402
from modules.chief_of_staff import evidence, nudges            # noqa: E402
from modules.cockpit import data                               # noqa: E402


def _fmt(ts) -> str:
    return str(ts)[:19] if ts else "—"


def report(conn, day: str) -> None:
    print("=" * 78)
    print(f"FOLLOW-UP CARDS — cross-channel truth as of {day}")
    print("=" * 78)
    rows = list(conn.execute(
        "SELECT d.thread_id, t.room_name, t.topic, t.client_id, t.bucket,"
        " t.contract_id, t.latest_message_dt"
        " FROM drafts d JOIN threads t ON t.id=d.thread_id"
        " WHERE d.sent_status='pending' ORDER BY t.latest_message_dt DESC"))
    groups: dict = {}
    for r in rows:
        k = nudges.followup_key(r["client_id"], r["room_name"], r["thread_id"])
        g = groups.setdefault(k, {"threads": [], "client_id": r["client_id"],
                                  "rows": []})
        g["threads"].append(r["thread_id"])
        g["client_id"] = g["client_id"] or r["client_id"]
        g["rows"].append(dict(r))

    for key, g in groups.items():
        head = g["rows"][0]
        ev = evidence.counterparty(conn, thread_ids=g["threads"],
                                   client_id=g["client_id"])
        eng = evidence.engagement(conn, thread_ids=g["threads"],
                                  client_id=g["client_id"])
        # OLD = exactly what the shipped code did: per-thread age off
        # threads.latest_message_dt, oldest room wins.
        old_ages = [evidence.age_days(r["latest_message_dt"], day)
                    for r in g["rows"] if r["latest_message_dt"]]
        old_age = max(old_ages) if old_ages else None
        old_ts = min((r["latest_message_dt"] for r in g["rows"]
                      if r["latest_message_dt"]), default=None)
        new_age = evidence.age_days(ev["waiting_since"], day)
        print(f"\n── {key}")
        print(f"   who        {head['room_name']!r}"
              f"  client_id={g['client_id']!r}  rooms={len(g['threads'])}")
        print(f"   OLD  age from threads.latest_message_dt (any row, any"
              f" direction), oldest room = {_fmt(old_ts)} -> '{old_age}d"
              f" since client'")
        print(f"   NEW  age from the oldest COUNTERPARTY message, cross-channel"
              f" = {_fmt(ev['waiting_since'])} -> '{new_age}d since client'")
        print(f"        they last spoke ANYWHERE {_fmt(ev['ts'])}"
              f" on {ev['channel']}   (this is the Done authority)")
        print(f"   we last spoke {_fmt(ev['ours_ts'])} on {ev['ours_channel']}"
              f"   answered_since={ev['answered_since']}")
        print(f"   channels read: {', '.join(ev['channels']) or 'none'}")
        print(f"   engagement: {eng['state']} contract={eng['contract_id']}"
              f" status={eng['status']} since={eng['since']}")
        if ev["unproven"]:
            print(f"   UNPROVEN: {ev['unproven']}")
        # the founder's decisions, from BOTH stores
        for k2 in (key, f"nudge:{key}"):
            s = conn.execute("SELECT state, updated_at FROM nudge_state WHERE"
                             " item_key=?", (k2,)).fetchone()
            if s:
                print(f"   nudge_state[{k2}] = {s['state']} @"
                      f" {_fmt(s['updated_at'])} (UTC)")
        n = conn.execute("SELECT id, state, updated_at, created_at FROM nudges"
                         " WHERE dedup_key=? ORDER BY id DESC LIMIT 1",
                         (key,)).fetchone()
        if n:
            print(f"   nudges#{n['id']} = {n['state']} @ {_fmt(n['updated_at'])}"
                  f" IST (= {nudges.ts_key(n['updated_at'], naive='ist')} UTC)"
                  f"  created {_fmt(n['created_at'])} IST")
        if eng.get("active"):
            print("   VERDICT: relabelled CLIENT COMMS — an unsent Upwork draft"
                  " on a live contract, not a prospect to chase. Kept on the"
                  " board (relabel, never suppress).")


def rendered(day: str) -> None:
    print("\n" + "=" * 78)
    print("WHAT TODAY WOULD RENDER RIGHT NOW (read-only call to data.today)")
    print("=" * 78)
    out = data.today(today=day)
    for e in out["queue"]:
        print(f"  [{e['kind']}/{e.get('nudge_kind') or '-'}]"
              f" urg={e['urgency']:>8}  {e.get('label') or ''}")
        print(f"      {e.get('title')!r}  client={e.get('client')!r}"
              f"  age={e.get('age_days')}")
        if e.get("possibly_resolved"):
            print(f"      CHIP: possibly resolved in"
                  f" {e['possibly_resolved']['source']}")
        if e.get("detail", {}).get("source"):
            print(f"      {e['detail']['source']}")
    print(f"\n  clusters: {out.get('clusters')}")
    print(f"  hidden (no provenance): {out.get('queue_hidden_no_provenance')}")


def repair(conn) -> int:
    """nudges(done/dismissed/snoozed) -> nudge_state, per row, never inventing.

    Scoped to `followup:` keys. Those are the only ones where the founder can
    have acted on BOTH card shapes for the same counterparty, which is what
    made the two stores disagree. Everything else (commitments, clusters,
    system) only ever renders as a nudge card, so its engine row is the whole
    truth and copying hundreds of historical rows into the overlay would be
    churn with no reader."""
    print("\n" + "=" * 78)
    print(f"OPTIONAL REPAIR: follow-up decisions in the engine but not the"
          f" overlay ({'APPLY' if APPLY else 'DRY RUN'})")
    print("  NOT REQUIRED — data.today() now merges both stores at read time,")
    print("  and nothing else reads nudge_state. This only makes them agree.")
    print("=" * 78)
    n = 0
    for r in conn.execute(
        "SELECT dedup_key, state, snooze_until, MAX(updated_at) updated_at"
        " FROM nudges WHERE state IN ('done','dismissed','snoozed')"
        " AND dedup_key LIKE 'followup:%'"
            " GROUP BY dedup_key, state"):
        key = r["dedup_key"] or ""
        eng_utc = nudges.ts_key(r["updated_at"], naive="ist")
        cur = conn.execute("SELECT state, updated_at FROM nudge_state WHERE"
                           " item_key=?", (key,)).fetchone()
        if cur and nudges.ts_key(cur["updated_at"]) >= eng_utc:
            continue                       # the overlay already knows, or newer
        why = ("no overlay row" if not cur else
               f"overlay is older ({_fmt(cur['updated_at'])} <"
               f" {eng_utc})")
        print(f"  {key:44} {r['state']:9} @ {eng_utc} UTC   [{why}]")
        n += 1
        if APPLY:
            conn.execute(
                "INSERT INTO nudge_state (item_key, state, snooze_until,"
                " source, updated_at) VALUES (?,?,?, 'engine-sync', ?)"
                " ON CONFLICT(item_key) DO UPDATE SET state=excluded.state,"
                " snooze_until=excluded.snooze_until,"
                " updated_at=excluded.updated_at",
                (key, r["state"], r["snooze_until"], eng_utc))
    print(f"\n  {n} row(s) {'written' if APPLY else 'would be written'}")
    return n


def main() -> None:
    day = config.now_ist().strftime("%Y-%m-%d") if hasattr(config, "now_ist") \
        else __import__("datetime").datetime.now(config.TZ).strftime("%Y-%m-%d")
    with db.connect(config.DB_PATH) as conn:
        report(conn, day)
    rendered(day)
    with db.connect(config.DB_PATH) as conn:
        repair(conn)
    if not APPLY:
        print("\n(DRY RUN — nothing was written. Re-run with --apply.)")


if __name__ == "__main__":
    main()
