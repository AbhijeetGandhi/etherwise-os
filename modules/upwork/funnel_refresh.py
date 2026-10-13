"""The nightly funnel refresh: keeps the Pipeline tab and the tests current.

Abhijeet, 2026-10-11: "yes start number 1 now" (the next step after the first
pull of 9 Oct: keep the proposal data current every night). Without it a
proposal he sends never reaches the funnel, the Pipeline goes stale the day
after a pull, and the running test can count nothing (experiments.link_sent
had no caller until this task).

Each night, read-only on Upwork (scripts/upwork_pull_all.py does the reading):
  1. messages   any proposal or client conversation with none stored (the
                15-minute heartbeat keeps the conversations that move current)
  2. funnel     every proposal list, a fresh read of the last 60 days, the
                research chain (scripts/funnel), then the backfill
  3. tests      every proposal sent inside a test's window is tied to the
                version it was drafted under, and to the version its letter
                actually reads as (a rewritten letter is flagged, not moved)
A refused sign-in fails the run once and is never retried: the grant guard has
already emailed him the command that fixes it.

THE HOURLY TOP-UP (2026-10-13, his "Can we build this" on the Proposals view's
"4 newer proposals are not in the funnel yet"): `--topup`, task
upwork_funnel_topup, at :55 every hour. Only the proposals the hourly sync has
stored and the funnel has not (new_proposal_ids): one `get` each and their job,
then the same local chain and backfill. No list is re-read and nothing is moved
aside, so a top-up costs a few reads; the nightly run above still re-reads
everything recent (opens, replies, rooms). It skips cleanly when nothing is new
and while the nightly refresh holds its lock.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from core import config, db, runner

TASK_NAME = "upwork_funnel_refresh"
STEPS = ("messages", "funnel")
TIMEOUT_S = 3 * 3600

TOPUP_TASK = "upwork_funnel_topup"
TOPUP_STEPS = ("new",)
TOPUP_TIMEOUT_S = 30 * 60
NEW_WITHIN_DAYS = 3


def new_proposal_ids(conn, today: Optional[date] = None) -> list:
    """Proposals the hourly sync has stored (`proposals`) that the funnel
    (`proposal_outcomes`) has not, sent in the last NEW_WITHIN_DAYS days,
    newest first. Bounded on purpose: anything older is the nightly run's to
    read again, so a proposal Upwork will not answer for is not asked about
    every hour forever."""
    cut = ((today or date.today()) - timedelta(days=NEW_WITHIN_DAYS)).isoformat()
    return [str(r[0]) for r in conn.execute(
        "SELECT p.id FROM proposals p WHERE substr(p.created_dt, 1, 10) >= ?"
        " AND NOT EXISTS (SELECT 1 FROM proposal_outcomes o WHERE o.proposal_id = p.id)"
        " ORDER BY p.created_dt DESC", (cut,))]


def _pull(ctx, steps=STEPS, timeout=TIMEOUT_S) -> tuple:
    """(exit code, {step: result}) from the read-only pull, as a subprocess so
    its fetchers keep their own pacing, quiet minutes and logs."""
    out = config.VAR_DIR / "prop" / "funnel_refresh_last.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.unlink()
    except OSError:
        pass
    cmd = [sys.executable, str(config.V3_ROOT / "scripts" / "upwork_pull_all.py"), *steps,
           "--json-out", str(out)]
    if ctx.db_path:
        cmd += ["--db", str(ctx.db_path)]
    env = dict(os.environ, PYTHONPATH=str(config.V3_ROOT))
    r = subprocess.run(cmd, cwd=str(config.V3_ROOT), env=env, timeout=timeout,
                       capture_output=True, text=True)
    try:
        res = json.loads(out.read_text())
    except (OSError, ValueError):
        res = {}
    return r.returncode, res.get("steps") or {}


def link_tests(conn) -> dict:
    """Tie every proposal sent since the first test started to its test
    version (experiments.link_sent: by job id; a proposal in a test's scope
    with no draft assignment is recorded as 'detected', never compared)."""
    from modules.upwork import experiments as ex
    if not ex._has_tables(conn):
        return {"linked": 0, "detected": 0, "rewritten": 0}
    first = conn.execute("SELECT MIN(started_at) FROM experiments WHERE started_at IS NOT NULL").fetchone()[0]
    if not first:
        return {"linked": 0, "detected": 0, "rewritten": 0}
    rows = [{"id": str(r[0]), "job_id": r[1], "sent": r[2], "kind": r[3], "letter": r[4]}
            for r in conn.execute(
                "SELECT proposal_id, job_id, sent_dt, kind, letter FROM proposal_outcomes"
                " WHERE substr(sent_dt,1,10) >= ?", (first[:10],))]
    return ex.link_sent(conn, rows)


def run(ctx, _pull_fn=None) -> dict:
    code, steps = (_pull_fn or _pull)(ctx)
    if code == 2:
        raise RuntimeError("the Upwork sign-in was refused during the nightly refresh (not retried);"
                           " renew it with bin/upwork-mcp-auth — the Pipeline tab's Agents view says which")
    if code != 0:
        raise RuntimeError(f"the nightly refresh failed (exit {code}): {json.dumps(steps)[:300]}"
                           " — see var/prop/pull_all.log")
    with db.connect(ctx.db_path) as conn:
        tests = link_tests(conn)
    return {"steps": steps, "tests": tests}


def topup(ctx, _pull_fn=None) -> dict:
    """The hourly top-up: add the proposals sent since the last refresh."""
    held = runner._acquire_lock(TASK_NAME)
    if held is None:
        # the nightly refresh is moving and rewriting the same var/prop files
        raise runner.TaskSkip("the nightly funnel refresh is running; it adds them itself")
    runner._release_lock(held)
    with db.connect(ctx.db_path) as conn:
        ids = new_proposal_ids(conn)
    if not ids:
        raise runner.TaskSkip("every proposal the sync has stored is already in the funnel")
    code, steps = (_pull_fn or (lambda c: _pull(c, TOPUP_STEPS, TOPUP_TIMEOUT_S)))(ctx)
    if code == 2:
        raise RuntimeError("the Upwork sign-in was refused during the hourly funnel top-up (not"
                           " retried); renew it with bin/upwork-mcp-auth")
    if code != 0:
        raise RuntimeError(f"the funnel top-up failed (exit {code}): {json.dumps(steps)[:300]}"
                           " — see var/prop/pull_all.log")
    with db.connect(ctx.db_path) as conn:
        tests = link_tests(conn)
        left = set(new_proposal_ids(conn))
    return {"new": len(ids), "added": sum(1 for i in ids if i not in left),
            "steps": steps, "tests": tests}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--topup" in argv:
        result = runner.run_task(TOPUP_TASK, topup, module="upwork")
        print(json.dumps({"run_id": result.run_id, "status": result.status, "metrics": result.metrics}))
        return 0 if result.status in ("completed", "skipped_empty") else 1
    result = runner.run_task(TASK_NAME, run, module="upwork")
    print(json.dumps({"run_id": result.run_id, "status": result.status, "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
