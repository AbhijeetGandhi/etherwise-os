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
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from core import config, db, runner

TASK_NAME = "upwork_funnel_refresh"
STEPS = ("messages", "funnel")
TIMEOUT_S = 3 * 3600


def _pull(ctx) -> tuple:
    """(exit code, {step: result}) from the read-only pull, as a subprocess so
    its fetchers keep their own pacing, quiet minutes and logs."""
    out = config.VAR_DIR / "prop" / "funnel_refresh_last.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.unlink()
    except OSError:
        pass
    cmd = [sys.executable, str(config.V3_ROOT / "scripts" / "upwork_pull_all.py"), *STEPS,
           "--json-out", str(out)]
    if ctx.db_path:
        cmd += ["--db", str(ctx.db_path)]
    env = dict(os.environ, PYTHONPATH=str(config.V3_ROOT))
    r = subprocess.run(cmd, cwd=str(config.V3_ROOT), env=env, timeout=TIMEOUT_S,
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


def main() -> int:
    result = runner.run_task(TASK_NAME, run, module="upwork")
    print(json.dumps({"run_id": result.run_id, "status": result.status, "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
