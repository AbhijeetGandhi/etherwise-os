#!/usr/bin/env python3
"""Re-score jobs that were graded under a prompt that has since been corrected.

Abhijeet, 2026-10-05, on "HubSpot Account Audit and Re-Architecting": "why was
this job not considered as hot lead". The scorer had never been told HubSpot
was his work and graded 179 of 199 HubSpot-titled jobs 1-2 on skill fit.
Fixing the prompt fixes the NEXT job; this goes back for the ones already
graded wrong that are still young enough to bid on.

Unlike `score_unblocked` (rows a hard rule stopped, which carry no score at
all), these rows HAVE a score — a wrong one. So the selection is: title matches
--pattern, the STORED skill-fit grade is at or under --max-fit, first scored
inside --days, no card yet, no proposal against it. The model runs again and
the row's score, breakdown and loom flag are replaced.

It scores; it does not draft or card. The scan's own push-retry path cards any
scored-and-uncarded row inside CLICKUP_PUSH_RETRY_DAYS on its next run, which
is also why --days defaults to that window: re-scoring an older row would
spend a model call on a job that can no longer become a card.

Usage: PYTHONPATH=. python3 scripts/rescore_titles.py --pattern 'hubspot|monday\\.com'
                        [--live] [--days 7] [--max-fit 2] [--id JOB_ID]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config, db  # noqa: E402
from modules.upwork import scoring  # noqa: E402

TASK = "rescore_titles"


def _fit(row: dict):
    try:
        return scoring._raw_fit(json.loads(row.get("score_breakdown_json") or "{}")
                                .get("skill_fit"))
    except ValueError:
        return None


def candidates(pattern: str, days: int, max_fit: float, only_id=None) -> list:
    rx = re.compile(pattern, re.I)
    with db.connect(config.DB_PATH) as conn:
        bid_on = {r[0] for r in conn.execute(
            "SELECT DISTINCT marketplace_job_id FROM proposals"
            " WHERE marketplace_job_id IS NOT NULL")}
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM scored_jobs WHERE score IS NOT NULL"
            " AND clickup_task_id IS NULL AND status = 'Scored'"
            " AND first_scored_at >= datetime('now', ?)"
            " ORDER BY first_scored_at DESC", (f"-{days} days",))]
    out = []
    for r in rows:
        if only_id and r["id"] != only_id:
            continue
        if r["id"] in bid_on or not rx.search(r.get("title") or ""):
            continue
        fit = _fit(r)
        if fit is None or fit > max_fit:
            continue
        out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", required=True, help="regex on the job title")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--days", type=int, default=config.CLICKUP_PUSH_RETRY_DAYS)
    ap.add_argument("--max-fit", type=float, default=2.0)
    ap.add_argument("--id", default=None)
    a = ap.parse_args()
    rows = candidates(a.pattern, a.days, a.max_fit, a.id)
    done, failed = [], []
    if a.live:
        for r in rows:
            try:
                v = scoring.score_job(r, task_name=TASK, db_path=config.DB_PATH)
            except Exception as exc:                       # noqa: BLE001
                failed.append({"id": r["id"], "error": str(exc)[:140]})
                continue
            with db.connect(config.DB_PATH) as conn:
                conn.execute(
                    "UPDATE scored_jobs SET score=?, score_breakdown_json=?,"
                    " loom_flag=?, updated_at=datetime('now')"
                    " WHERE id=? AND clickup_task_id IS NULL AND status='Scored'",
                    (v["score"], json.dumps(v["breakdown"]), v["loom_flag"], r["id"]))
            done.append({"id": r["id"], "was": r["score"], "now": v["score"],
                         "fit_was": _fit(r), "fit_now": v["breakdown"].get("skill_fit"),
                         "title": (r["title"] or "")[:72],
                         "say": (v["breakdown"].get("recommendation") or "")[:260]})
    hot = sum(1 for d in done if d["now"] >= config.HOT_LEAD_THRESHOLD)
    print(json.dumps({
        "mode": "LIVE" if a.live else "DRY-RUN", "candidates": len(rows),
        "rescored": len(done), "now_hot": hot, "hot_bar": config.HOT_LEAD_THRESHOLD,
        "failed": failed,
        "rows": sorted(done, key=lambda d: -d["now"]) or
                [{"was": r["score"], "title": (r["title"] or "")[:72]} for r in rows],
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
