"""Scoring eval: Haiku vs Sonnet on historical jobs (decision #4).

PYTHONPATH=. python3 -m core.evals.scoring_eval [--limit-per-model N]

Sample: every outcome-linked job (scored_jobs joined to proposals with
Won/Interview/Expired) + a deterministic stratified fill across score bands.
Both models score the identical v2.3 matrix prompt through the gateway
(strict JSON schema). Plus a run-1 calibration leg: re-score the June-12
run's legit jobs (from v2, read-only) with Sonnet and compare to the run's
stored scores. REPORT ONLY — config.MODELS['scoring'] changes only after
Abhijeet reviews the numbers.

Budget: chunked into runner runs of <=25 calls so the per-run USD ceiling
holds; every call lands in claude_usage under task eval_scoring_*.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from core import claude_gateway as gw
from core import config, db, runner

HAIKU = "claude-haiku-4-5-20251001"
SONNET = config.MODELS["scoring"]            # claude-sonnet-4-6
V2_DB = config.V2_DB_PATH
REPORTS = config.WORKSPACE_ROOT / "reports"
TODAY = datetime.now(config.TZ).strftime("%Y-%m-%d")

SCHEMA = {
    "type": "object",
    "properties": {
        "skill_fit": {"type": "integer"},
        "budget": {"type": "integer"},
        "client_quality": {"type": "integer"},
        "competition": {"type": "integer"},
        "description_quality": {"type": "integer"},
        "base_total": {"type": "integer"},
        "bonuses": {
            "type": "object",
            "properties": {"core_stack": {"type": "integer"},
                           "long_term": {"type": "integer"},
                           "invite": {"type": "integer"},
                           "claude": {"type": "integer"},
                           "whale_client": {"type": "integer"},
                           "fresh": {"type": "integer"}},
            "required": ["core_stack", "long_term", "invite", "claude",
                         "whale_client", "fresh"],
            "additionalProperties": False,
        },
        "raw_total": {"type": "integer"},
        "gated": {"type": "boolean"},
        "effective_total": {"type": "integer"},
        "recommendation": {"type": "string"},
    },
    "required": ["skill_fit", "budget", "client_quality", "competition",
                 "description_quality", "base_total", "bonuses", "raw_total",
                 "gated", "effective_total", "recommendation"],
    "additionalProperties": False,
}

SYSTEM = """You score Upwork jobs for Etherwise (Abhijeet Gandhi — AI & \
Automation agency; core stack Make.com, n8n, Vapi, Airtable, GoHighLevel, \
Python, AI/LLM; Top Rated Plus; $32.50/hr). Apply the v2.3 scoring matrix \
EXACTLY and output ONLY the JSON object.

BASE DIMENSIONS (base_total = skill_fit*2 + budget + client_quality + \
competition + description_quality, max 28):
- skill_fit 1-5 (x2 in base): 5 = dead-center core stack
- budget 1-5
- client_quality 1-3 CAPPED: new client with $0 spent = 2 (neutral); 1 only \
for active red flags (rating <3.5, payment issues)
- competition 1-5: 20-50 proposals is NORMAL (not low); 150+ without a \
whale client is the only real disqualifier
- description_quality 1-5

BONUSES (each 0 if absent): core_stack +3 (category in Make.com/n8n/Vapi/\
Airtable/GoHighLevel); long_term +3 (20+ hr/week or multi-month); invite +4; \
claude +2 (mentions Claude/Claude Code); whale_client +2 (client spent \
$100K+); fresh +1 ONLY if posted under 1 hour before it was fetched \
(compare created_dt to fetched_at; if not computable, 0).
raw_total = base_total + sum(bonuses).

CATEGORY PRIORITY GATE (v2.3): a job is GATED if its primary category/\
skills/description centers on GoHighLevel/GHL/HighLevel, Vapi, or Retell \
(voice-AI / GHL work). Make.com, Airtable, n8n, Python, generic AI/LLM jobs \
are NEVER gated. Direct invites are exempt. GHL/Vapi as one endpoint in a \
Make/Airtable-primary build is NOT gated. A gated job CLEARS the whale gate \
if client_total_spent >= $50,000 OR hourly_max >= $45 OR fixed_budget >= \
$3,000. gated=true means: gated category AND fails the whale gate -> \
effective_total = min(raw_total, 15). Otherwise effective_total = raw_total.

Score what the data shows; missing fields score neutrally. recommendation: \
one short sentence."""


def sample_jobs(db_path=None) -> list:
    with db.connect(db_path) as conn:
        linked = [dict(r) for r in conn.execute(
            "SELECT sj.*, p.status AS outcome FROM scored_jobs sj"
            " JOIN proposals p ON p.marketplace_job_id = sj.id"
            " WHERE p.status IN ('Won','Interview','Expired')"
            " AND sj.score IS NOT NULL ORDER BY sj.id")]
        have = {r["id"] for r in linked}
        fill = []
        for band_sql, n in (("score >= 22", 12), ("score BETWEEN 16 AND 21", 12),
                            ("score BETWEEN 12 AND 15", 12),
                            ("score BETWEEN 8 AND 11", 6)):
            rows = [dict(r) for r in conn.execute(
                f"SELECT *, NULL AS outcome FROM scored_jobs WHERE {band_sql}"
                " AND score_breakdown_json IS NOT NULL"
                " AND length(COALESCE(description,'')) > 100"
                " ORDER BY id LIMIT ?", (n + 5,))]
            fill += [r for r in rows if r["id"] not in have][:n]
    jobs = linked + fill
    assert len(jobs) >= 45, f"sample too small: {len(jobs)}"
    return jobs[:50]


def job_input(row) -> str:
    fields = {k: row.get(k) for k in (
        "title", "contract_type", "hourly_min", "hourly_max", "fixed_budget",
        "weekly_budget", "total_applicants", "experience_level", "engagement",
        "engagement_duration", "skills_json", "client_country",
        "client_total_spent", "client_hires", "client_rating",
        "client_payment_verified", "created_dt", "fetched_at")}
    fields["description"] = (row.get("description") or "")[:1800]
    return ("Score this job. Output only the JSON object.\n"
            + json.dumps(fields, default=str))


def score_with(model_override, jobs, task_name, db_path=None):
    """Chunked runner runs; returns {job_id: verdict|None}."""
    out = {}

    def chunk_task(chunk):
        def fn(ctx):
            ok = fail = 0
            for row in chunk:
                verdict = None
                for attempt in (1, 2):
                    try:
                        r = gw.call(task_name=task_name, model_key="scoring",
                                    model_override=model_override,
                                    system=SYSTEM,
                                    user_content=job_input(row),
                                    schema=SCHEMA, purpose="scoring-eval",
                                    max_output_tokens=700, db_path=db_path)
                        verdict = r.parsed
                        break
                    except gw.TransientAPIError:
                        time.sleep(5 * attempt)
                    except gw.GatewayError as exc:
                        print(f"  {row['id']}: {exc}", file=sys.stderr)
                        break
                out[row["id"]] = verdict
                ok += verdict is not None
                fail += verdict is None
            return {"scored": ok, "failed": fail}
        return fn

    for i in range(0, len(jobs), 25):
        runner.run_task(f"{task_name}_{i // 25 + 1}",
                        chunk_task(jobs[i:i + 25]), module="kernel",
                        db_path=db_path)
    return out


def band(score):
    if score is None:
        return "?"
    if score >= 22:
        return "22+"
    if score >= 16:
        return "16-21"
    if score >= 12:
        return "12-15"
    if score >= 8:
        return "8-11"
    return "<8"


def model_metrics(jobs, verdicts) -> dict:
    pairs = [(r, verdicts[r["id"]]) for r in jobs
             if verdicts.get(r["id"]) is not None]
    diffs = [v["effective_total"] - r["score"] for r, v in pairs]
    hot_hist = {r["id"] for r, _ in pairs if r["score"] >= 16}
    hot_new = {r["id"] for r, v in pairs if v["effective_total"] >= 16}
    won = [v["effective_total"] for r, v in pairs if r.get("outcome") == "Won"]
    exp = [v["effective_total"] for r, v in pairs
           if r.get("outcome") == "Expired"]
    return {
        "scored": len(pairs), "failed": len(jobs) - len(pairs),
        "mae": round(statistics.mean(abs(d) for d in diffs), 2),
        "mean_bias": round(statistics.mean(diffs), 2),
        "band_agreement_pct": round(100 * statistics.mean(
            1 if band(r["score"]) == band(v["effective_total"]) else 0
            for r, v in pairs)),
        "hot_lost": len(hot_hist - hot_new),
        "hot_gained": len(hot_new - hot_hist),
        "won_mean_score": round(statistics.mean(won), 1) if won else None,
        "expired_mean_score": round(statistics.mean(exp), 1) if exp else None,
        "gate_applied_count": sum(1 for _, v in pairs if v["gated"]),
    }


def run1_calibration(db_path=None) -> dict:
    """Re-score the June-12 run-1 legit jobs (v2, read-only) with Sonnet."""
    import sqlite3
    conn = sqlite3.connect(f"file:{V2_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM jobs WHERE fetched_at >= '2026-06-12'"
            " AND status IN ('Scored','Drafted','ClickUp Created')"
            " AND score IS NOT NULL ORDER BY fetched_at LIMIT 15")]
    finally:
        conn.close()
    if not rows:
        return {"jobs": 0}
    verdicts = score_with(None, rows, "eval_run1_calibration",
                          db_path=db_path)
    pairs = [(r, verdicts[r["id"]]) for r in rows
             if verdicts.get(r["id"]) is not None]
    return {
        "jobs": len(pairs),
        "run1_mean": round(statistics.mean(r["score"] for r, _ in pairs), 1),
        "resc_mean": round(statistics.mean(
            v["effective_total"] for _, v in pairs), 1),
        "per_job": [{"id": r["id"], "run1": r["score"],
                     "rescored": v["effective_total"],
                     "gated": v["gated"]} for r, v in pairs],
    }


def cost_for(task_prefix, db_path=None) -> float:
    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(total_cost_usd),0) FROM claude_usage"
            " WHERE task_name LIKE ?", (task_prefix + "%",)).fetchone()
        return round(row[0], 4)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None,
                   help="cap sample size (smoke runs)")
    args = p.parse_args()

    jobs = sample_jobs()
    if args.limit:
        jobs = jobs[:args.limit]
    print(f"sample: {len(jobs)} jobs"
          f" ({sum(1 for j in jobs if j.get('outcome'))} outcome-linked)",
          file=sys.stderr)

    results = {}
    for label, override in (("haiku", HAIKU), ("sonnet", None)):
        verdicts = score_with(override, jobs, f"eval_scoring_{label}")
        results[label] = model_metrics(jobs, verdicts)
        results[label]["cost_usd"] = cost_for(f"eval_scoring_{label}")

    calib = run1_calibration()
    calib["cost_usd"] = cost_for("eval_run1_calibration")

    REPORTS.mkdir(exist_ok=True)
    report = REPORTS / f"scoring-eval-{TODAY}.md"
    lines = [
        f"# Scoring eval — Haiku vs Sonnet ({TODAY})",
        "",
        f"Sample: {len(jobs)} historical jobs"
        f" ({sum(1 for j in jobs if j.get('outcome'))} outcome-linked),"
        " v2.3 matrix + Category Priority Gate, identical strict-schema"
        " prompt per model. Baseline = stored historical score"
        " (Sonnet-era production).",
        "", "## Results", "",
        "| metric | haiku | sonnet |", "|---|---|---|",
    ]
    for key in ("scored", "failed", "mae", "mean_bias",
                "band_agreement_pct", "hot_lost", "hot_gained",
                "won_mean_score", "expired_mean_score",
                "gate_applied_count", "cost_usd"):
        lines.append(f"| {key} | {results['haiku'].get(key)}"
                     f" | {results['sonnet'].get(key)} |")
    lines += [
        "", "## Run-1 calibration (Sonnet re-score of the June-12 legit"
        " jobs)", "",
        f"run-1 stored mean: **{calib.get('run1_mean')}** · re-scored mean:"
        f" **{calib.get('resc_mean')}** · jobs: {calib.get('jobs')} · cost:"
        f" ${calib.get('cost_usd')}",
        "",
        "```json", json.dumps(calib.get("per_job", []), indent=1), "```",
        "",
        "## Decision input (#4) — NO config change until Abhijeet reviews",
        "- MODELS['scoring'] stays claude-sonnet-4-6 pending review.",
    ]
    report.write_text("\n".join(lines))
    print(json.dumps({"haiku": results["haiku"], "sonnet": results["sonnet"],
                      "calibration": {k: v for k, v in calib.items()
                                      if k != "per_job"},
                      "report": str(report)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
