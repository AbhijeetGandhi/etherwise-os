#!/usr/bin/env python3
"""Pull every Upwork record we can read into the knowledge base — read-only.

Abhijeet, 2026-10-09: "I want you to have each and every data about every
client ... use API, use MCP of Upwork, use browser". What the 8 Oct audit
found missing, and what this fills, step by step (each step resumable; run
them all, or name some):

  contracts  every contract ever held — ACTIVE, PAUSED and CLOSED with no date
             cap (the daily sync keeps only 45 days of closed ones), through
             mcp_contracts' own search and upsert (fill-or-keep)
  messages   the stored messages of every proposal and client conversation
             that has none yet (97 of 183 proposal rooms on 9 Oct), paged back
             far, through mcp_messages' own fetch and upsert
  funnel     the proposals since the research (8 Oct) and a fresh read of the
             last FRESH_DAYS days' (their status, room and job move): the
             research fetchers (scripts/funnel: list + get, rooms, jobs), the
             dataset, features and rows, then the funnel backfill. Old lists and the recent gets
             and jobs are MOVED aside (never deleted) so they are read again.
             New rows are labelled 'refresh <day>'; a stored row keeps its label.

Not here: the open date of each proposal (it is read off the proposal page in
Chrome, with him awake — var/prop/fetch_viewed.py), and anything that writes
to Upwork (nothing does: every tool call goes through the system's read-only
MCP client).

    PYTHONPATH=. python3 scripts/upwork_pull_all.py                 # all steps
    PYTHONPATH=. python3 scripts/upwork_pull_all.py messages        # one step
    PYTHONPATH=. python3 scripts/upwork_pull_all.py --dry-run       # what it would do

PACE: the MCP client's own paced calls (mcp_search.paced_call), one at a time;
a dead sign-in stops the run at once and is never retried. Log:
var/prop/pull_all.log.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import config, db                                       # noqa: E402

ROOT = config.V3_ROOT
PROP = ROOT / "var" / "prop"
LOG = PROP / "pull_all.log"
FRESH_DAYS = 60                     # proposals this young get their status, room and job read again
MESSAGE_PAGES = 30                  # pages of 20 per room: a long conversation is read whole
STEPS = ("contracts", "messages", "funnel")


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


class _Ctx:
    """The little the feed helpers need from a run context."""
    def __init__(self, db_path):
        self.db_path = db_path

    @staticmethod
    def log(msg):
        log("  " + str(msg))


def _session():
    from modules.upwork.feeds import mcp_client
    s = mcp_client.MCPSession()
    s.initialize()
    return s


def _is_auth(exc: BaseException) -> bool:
    from modules.upwork.feeds import mcp_client
    t = repr(exc).lower()
    return isinstance(exc, mcp_client.MCPAuthError) or any(
        k in t for k in ("invalid_grant", "mcp 401", "mcp 403", "unauthor", "forbidden"))


# ── contracts: every one, no date cap ────────────────────────────────────────

def step_contracts(db_path, dry: bool) -> dict:
    from modules.upwork.feeds import mcp_contracts
    org = config.UPWORK_ORGS["personal"]
    if dry:
        with db.connect(db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
        return {"would": "search ACTIVE, PAUSED and every CLOSED contract", "stored_now": n}
    s = _session()
    out = {"inserted": 0, "ended": 0, "paused": 0, "updated": 0, "unchanged": 0, "reads": 0}
    got = {}
    for status in ("ACTIVE", "PAUSED", "CLOSED"):
        got[status] = mcp_contracts.search(s, org, [status], _Ctx.log, end_date_after=None, max_pages=80)
        out["reads"] += got[status]["pages"]
        if got[status]["more"]:
            log(f"  contracts {status}: the page cap stopped the read; run again to continue")
    with db.connect(db_path) as conn:
        for status, g in got.items():
            for node in g["rows"]:
                row = mcp_contracts.contract_row(node)
                if row:
                    r = mcp_contracts.upsert(conn, row)
                    out[r] = out.get(r, 0) + 1
    return out


# ── messages: every conversation that has none stored ────────────────────────

def rooms_missing_messages(conn) -> list:
    """Proposal rooms first (the funnel's question), then client rooms — every
    room we know of with no stored message, newest first."""
    rows = conn.execute(
        "SELECT po.room_id AS room, MAX(po.sent_dt) AS d, 0 AS prio FROM proposal_outcomes po"
        " WHERE po.room_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.thread_id = po.room_id)"
        " GROUP BY po.room_id"
        " UNION ALL"
        " SELECT t.id, t.latest_message_dt, 1 FROM threads t"
        " WHERE (t.client_id IS NOT NULL OR t.contract_id IS NOT NULL)"
        " AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.thread_id = t.id)"
        " AND t.id NOT IN (SELECT room_id FROM proposal_outcomes WHERE room_id IS NOT NULL)").fetchall()
    prio, when = {}, {}
    for r in rows:
        if r["room"] not in prio:
            prio[r["room"]], when[r["room"]] = r["prio"], r["d"] or ""
    out = sorted(prio, key=lambda x: when[x], reverse=True)    # newest first ...
    out.sort(key=lambda x: prio[x])                            # ... within each kind
    return out


def step_messages(db_path, dry: bool, limit: int = 0) -> dict:
    from modules.upwork.feeds import mcp_messages
    with db.connect(db_path) as conn:
        todo = rooms_missing_messages(conn)
    if limit:
        todo = todo[:limit]
    if dry:
        return {"would_read_rooms": len(todo)}
    org = config.UPWORK_ORGS["personal"]
    s = _session()
    out = {"rooms": 0, "inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
    for i, room in enumerate(todo, 1):
        try:
            nodes = mcp_messages.fetch_messages(s, org, room, _Ctx.log, max_pages=MESSAGE_PAGES)
        except Exception as exc:                           # noqa: BLE001
            if _is_auth(exc):
                raise
            out["failed"] += 1
            log(f"  {room}: {str(exc)[:160]}")
            continue
        with db.connect(db_path) as conn:
            conn.execute("INSERT OR IGNORE INTO threads (id) VALUES (?)", (room,))
            for node in nodes:
                row = mcp_messages.message_row(node, room)
                if row is None:
                    out["skipped"] += 1
                    continue
                r = mcp_messages.upsert_message(conn, row)
                out[r] = out.get(r, 0) + 1
        out["rooms"] += 1
        if i % 10 == 0:
            log(f"  messages: {i}/{len(todo)} rooms read, {out['inserted']} messages stored")
    return out


# ── funnel: new proposals, and a fresh read of the recent ones ───────────────

def _move_aside(paths, label: str) -> int:
    """Move files to an archive folder beside them — never delete."""
    n = 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        dest = p.parent.parent / f"{p.parent.name}_before_{stamp}"
        dest.mkdir(exist_ok=True)
        shutil.move(str(p), str(dest / p.name))
        n += 1
    log(f"  moved aside {n} {label}")
    return n


def _run(*cmd) -> None:
    env = dict(__import__("os").environ, PYTHONPATH=".")
    log("  $ " + " ".join(cmd))
    r = subprocess.run([sys.executable, *cmd], cwd=str(ROOT), env=env)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} exited {r.returncode}")


def _tail_says_stopped(logfile: Path) -> bool:
    try:
        last = [l for l in logfile.read_text().splitlines() if l.strip()][-1]
    except (OSError, IndexError):
        return False
    return "STOPPED" in last


def step_funnel(db_path, dry: bool) -> dict:
    cut = (date.today() - timedelta(days=FRESH_DAYS)).isoformat()
    with db.connect(db_path) as conn:
        recent = conn.execute("SELECT proposal_id, job_id FROM proposal_outcomes WHERE sent_dt >= ?",
                              (cut,)).fetchall()
        newest = conn.execute("SELECT MAX(sent_dt) FROM proposal_outcomes").fetchone()[0]
    lists = sorted((PROP / "raw" / "list").glob("*.json"))
    gets = [PROP / "raw" / "get" / f"{r['proposal_id']}.json" for r in recent]
    jobs = [PROP / "raw" / "job" / f"{r['job_id']}.json" for r in recent if r["job_id"]]
    rooms = [PROP / "raw" / "room" / f"{r['proposal_id']}.json" for r in recent]
    plan = {"newest_stored": newest, "fresh_since": cut, "lists_to_reread": len(lists),
            "recent_proposals": len(recent)}
    if dry:
        return plan
    _move_aside(lists, "proposal list pages (so new proposals are listed)")
    _move_aside(gets, f"proposal reads from the last {FRESH_DAYS} days")
    _move_aside(rooms, f"room reads from the last {FRESH_DAYS} days")
    _move_aside(jobs, f"job reads from the last {FRESH_DAYS} days")
    _run("scripts/funnel/fetch.py")
    if _tail_says_stopped(PROP / "fetch.log"):
        raise RuntimeError("the proposals fetch stopped (see var/prop/fetch.log)")
    _run("scripts/funnel/fetch_rooms.py")
    _run("scripts/funnel/fetch_jobs.py")
    if _tail_says_stopped(PROP / "fetch_jobs.log"):
        raise RuntimeError("the jobs fetch stopped (see var/prop/fetch_jobs.log)")
    # the research's own chain, in its order: one record per proposal (with the
    # ledger), the letter and context features, then the flat rows the backfill reads
    _run("scripts/funnel/build_dataset.py")
    _run("scripts/funnel/features.py")
    _run("scripts/funnel/export_rows.py")
    _run("scripts/proposal_funnel_backfill.py", "--apply", "--source",
         f"refresh {date.today().isoformat()}")
    with db.connect(db_path) as conn:
        plan["stored_now"] = conn.execute("SELECT COUNT(*) FROM proposal_outcomes").fetchone()[0]
        plan["newest_now"] = conn.execute("SELECT MAX(sent_dt) FROM proposal_outcomes").fetchone()[0]
    return plan


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("steps", nargs="*", help="any of: " + ", ".join(STEPS) + " (default: all)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rooms", type=int, default=0, help="messages: read at most this many rooms")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--json-out", type=Path, default=None,
                    help="write each step's result here (the nightly refresh reads it)")
    a = ap.parse_args(argv)
    steps = a.steps or list(STEPS)
    bad = [x for x in steps if x not in STEPS]
    if bad:
        ap.error(f"unknown step(s): {', '.join(bad)} (choose from {', '.join(STEPS)})")
    dbp = a.db or config.DB_PATH
    log(f"START {', '.join(steps)}{' (dry run)' if a.dry_run else ''}")
    results: dict = {}

    def done(code: int) -> int:
        if a.json_out:
            try:
                Path(a.json_out).write_text(json.dumps({"exit": code, "steps": results}))
            except OSError:
                pass
        return code

    for st in steps:
        try:
            if st == "contracts":
                m = step_contracts(dbp, a.dry_run)
            elif st == "messages":
                m = step_messages(dbp, a.dry_run, a.rooms)
            else:
                m = step_funnel(dbp, a.dry_run)
            results[st] = m
            log(f"{st}: {json.dumps(m)}")
        except Exception as exc:                           # noqa: BLE001
            if _is_auth(exc):
                results[st] = {"stopped": "sign-in refused"}
                log(f"STOPPED at {st}: the Upwork sign-in was refused — not retried. {str(exc)[:200]}")
                return done(2)
            results[st] = {"failed": repr(exc)[:300]}
            log(f"{st} FAILED: {exc!r}")
            return done(1)
    log("DONE")
    return done(0)


if __name__ == "__main__":
    sys.exit(main())
