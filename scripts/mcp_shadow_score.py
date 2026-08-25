#!/usr/bin/env python3
"""The budgeted scoring pass over shadow rows (Upwork chat, 2026-08-25).

    PYTHONPATH=. python3 scripts/mcp_shadow_score.py --dry-run
    PYTHONPATH=. python3 scripts/mcp_shadow_score.py [--limit N] [--drift N]

Migration 027 left mcp_shadow_jobs.score NULL "until someone runs a deliberate,
budgeted scoring pass". This is it. Same scorer the live scan uses
(modules.upwork.scoring.score_job: the v2.3 matrix + the code-enforced gate),
over two sets:

  A. MCP-ONLY survivors — shadow rows that pass the hard rules, are NOT in
     scored_jobs, and have no shadow score yet. Answers: how many of the jobs
     only the MCP surfaced would have been hot?
  B. DRIFT sample — shadow rows the scraper ALSO scored. The MCP-described job
     is scored and stored beside scored_jobs.score. Answers: how far does an
     MCP description move the same job's score (the number the band
     recalibration waits on)?

Writes ONLY mcp_shadow_scores (+ the mcp_shadow_jobs.score convenience column).
Never scored_jobs, never a ClickUp card, never a draft, never an email — the
shadow principle of 027 holds; tests/test_mcp_shadow_score.py greps for it.

Budget: chunked into runner runs of <= CHUNK calls (the per-run USD ceiling),
task names upwork_shadow_score_<n> (the upwork_ prefix gets the priority daily
budget), module "system" like the shadow poller. Idempotent: a re-run skips
every id already in mcp_shadow_scores. --dry-run prints the two sets and the
projected cost from the scan's measured per-call cost, and makes no model call.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import claude_gateway as gw                            # noqa: E402
from core import config, db, runner                              # noqa: E402
from modules.upwork import scoring                               # noqa: E402

TASK_PREFIX = "upwork_shadow_score"
CHUNK = 25                 # <= the eval's chunk; ~$0.20 at the scan's per-call cost
DEFAULT_LIMIT = 200        # MCP-only rows per invocation
DEFAULT_DRIFT = 100        # shared rows per invocation

# ── selection (pure SQL, both sets skip anything already scored) ─────────────

_MCP_ONLY_SQL = """
    SELECT s.id, s.normalized_json, NULL AS scraper_score
      FROM mcp_shadow_jobs s
     WHERE s.hard_rule_skip IS NULL
       AND NOT EXISTS (SELECT 1 FROM mcp_shadow_scores x WHERE x.id = s.id)
       AND NOT EXISTS (SELECT 1 FROM scored_jobs j WHERE j.id = s.id)
     ORDER BY s.first_seen_at DESC
     LIMIT ?"""

_DRIFT_SQL = """
    SELECT s.id, s.normalized_json, j.score AS scraper_score
      FROM mcp_shadow_jobs s
      JOIN scored_jobs j ON j.id = s.id
     WHERE s.hard_rule_skip IS NULL
       AND j.score IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM mcp_shadow_scores x WHERE x.id = s.id)
     ORDER BY s.first_seen_at DESC
     LIMIT ?"""


def select_sets(conn, limit: int, drift: int) -> tuple[list, list]:
    mcp_only = [dict(r) for r in conn.execute(_MCP_ONLY_SQL, (limit,))]
    shared = [dict(r) for r in conn.execute(_DRIFT_SQL, (drift,))]
    return mcp_only, shared


def per_call_cost(conn) -> float:
    """The scan's measured scoring cost over the last 30 days; a fixed
    fallback when there is no history (fresh DB)."""
    row = conn.execute(
        "SELECT AVG(total_cost_usd) FROM claude_usage WHERE task_name='upwork_scan'"
        " AND purpose LIKE 'score %' AND ist_date >= date('now', '-30 days')"
    ).fetchone()
    return float(row[0]) if row and row[0] else 0.01


# ── the pass ─────────────────────────────────────────────────────────────────

def _write(dbp, row: dict, verdict: dict, run_id, model: str) -> None:
    with db.connect(dbp) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO mcp_shadow_scores (id, score, gated, loom_flag,"
            " breakdown_json, scraper_score, model, run_id)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (row["id"], int(verdict["score"]), int(bool(verdict["gated"])),
             int(verdict["loom_flag"]), json.dumps(verdict["breakdown"]),
             row["scraper_score"], model, run_id))
        conn.execute("UPDATE mcp_shadow_jobs SET score=? WHERE id=?",
                     (int(verdict["score"]), row["id"]))


def chunk_task(rows: list, dbp, _score=scoring.score_job):
    """One runner task: score `rows`, write each verdict as it lands (a crash
    mid-chunk keeps what was paid for; the re-run skips it)."""
    def fn(ctx):
        scored = failed = 0
        model = config.MODELS["scoring"]
        for row in rows:
            job = json.loads(row["normalized_json"])
            try:
                # the model call stays OUTSIDE any DB with-block (house rule)
                verdict = _score(job, task_name=ctx.task_name, db_path=dbp)
            except gw.ClaudeBudgetExceeded:
                raise                       # stop the whole pass, loudly
            except gw.GatewayError as exc:
                ctx.log(f"{row['id']}: {exc!r}")
                failed += 1
                continue
            _write(dbp, row, verdict, ctx.run_id, model)
            scored += 1
        if failed:
            ctx.anomaly("shadow_score_failed", "warn",
                        {"failed": failed, "scored": scored})
        return {"scored": scored, "failed": failed, "rows": len(rows)}
    return fn


def run_pass(dbp=None, limit: int = DEFAULT_LIMIT, drift: int = DEFAULT_DRIFT,
             dry_run: bool = False, _score=scoring.score_job,
             _run_task=runner.run_task, out=print) -> dict:
    dbp = dbp or config.DB_PATH
    with db.connect(dbp) as conn:
        mcp_only, shared = select_sets(conn, limit, drift)
        cost = per_call_cost(conn)
    rows = mcp_only + shared
    out(f"A. MCP-only survivors to score: {len(mcp_only)}")
    out(f"B. drift sample (both feeds scored): {len(shared)}")
    out(f"projected: {len(rows)} calls x ${cost:.4f} = ${len(rows) * cost:.2f}"
        f" in {-(-len(rows) // CHUNK)} runs of <= {CHUNK}")
    if dry_run or not rows:
        return {"mcp_only": len(mcp_only), "shared": len(shared),
                "calls": len(rows), "projected_usd": round(len(rows) * cost, 2),
                "dry_run": True, "runs": []}

    results = []
    for n, i in enumerate(range(0, len(rows), CHUNK), start=1):
        try:
            r = _run_task(f"{TASK_PREFIX}_{n}",
                          chunk_task(rows[i:i + CHUNK], dbp, _score),
                          module="system", db_path=dbp)
        except Exception as exc:                      # noqa: BLE001
            # the runner has already written the failed run + its critical
            # anomaly and re-raised; the pass stops here, rows already scored
            # in this chunk are kept (written one by one), the rest wait
            results.append({"run_id": None, "status": "failed",
                            "metrics": None, "error": repr(exc)})
            out(f"run {n}: failed {exc!r}")
            out("stopping: a chunk failed (budget cap or a non-transient error);"
                " re-run to continue where it stopped")
            break
        results.append({"run_id": r.run_id, "status": r.status,
                        "metrics": r.metrics, "error": r.error})
        out(f"run {n}: {r.status} {r.metrics or r.error}")
    out("")
    out(summary(dbp))
    return {"mcp_only": len(mcp_only), "shared": len(shared),
            "calls": len(rows), "dry_run": False, "runs": results}


# ── the two answers, from the table (re-runnable on its own) ─────────────────

def summary(dbp=None) -> str:
    dbp = dbp or config.DB_PATH
    hot, loom = config.HOT_LEAD_THRESHOLD, config.LOOM_FLAG_THRESHOLD
    with db.connect(dbp) as conn:
        only = [r["score"] for r in conn.execute(
            "SELECT score FROM mcp_shadow_scores WHERE scraper_score IS NULL")]
        pairs = [(r["score"], r["scraper_score"]) for r in conn.execute(
            "SELECT score, scraper_score FROM mcp_shadow_scores"
            " WHERE scraper_score IS NOT NULL")]
    lines = ["SHADOW SCORES (mcp_shadow_scores)"]
    if only:
        bands: dict = {}
        for s in only:
            b = config.score_band(s)
            bands[b] = bands.get(b, 0) + 1
        band_txt = " · ".join(f"{b} {config.score_band_label(b)}: {n}"
                              for b, n in sorted(bands.items(),
                                                 key=lambda kv: -kv[1]))
        lines.append(f"A. MCP-only scored: {len(only)} · hot (>={hot}):"
                     f" {sum(s >= hot for s in only)} · loom (>={loom}):"
                     f" {sum(s >= loom for s in only)} · {band_txt}")
    else:
        lines.append("A. MCP-only scored: 0")
    if pairs:
        diffs = [m - s for m, s in pairs]
        both_hot = sum(m >= hot and s >= hot for m, s in pairs)
        scr_hot = sum(s >= hot for _, s in pairs)
        mcp_hot = sum(m >= hot for m, _ in pairs)
        within2 = sum(abs(d) <= 2 for d in diffs)
        lines.append(f"B. drift on {len(pairs)} shared jobs: median (mcp - scraper)"
                     f" {statistics.median(diffs):+.1f} · mean {statistics.mean(diffs):+.2f}"
                     f" · within +-2: {within2}/{len(pairs)} · hot by scraper {scr_hot},"
                     f" by mcp {mcp_hot}, by both {both_hot}")
    else:
        lines.append("B. drift: no shared jobs scored yet")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="max MCP-only rows to score this run")
    ap.add_argument("--drift", type=int, default=DEFAULT_DRIFT,
                    help="max shared rows to score for the drift measure")
    ap.add_argument("--dry-run", action="store_true",
                    help="list the sets and the projected cost; no model call")
    ap.add_argument("--summary", action="store_true",
                    help="print the two answers from the table and exit")
    a = ap.parse_args(argv)
    if a.summary:
        print(summary())
        return 0
    run_pass(limit=a.limit, drift=a.drift, dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
