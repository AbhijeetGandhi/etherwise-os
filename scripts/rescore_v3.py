#!/usr/bin/env python3
"""Re-score the job history onto the v3.0 scale — arithmetic only, no model call.

Abhijeet, 2026-09-08: "there is lot of noise we definitely need to improve our
socring." The re-weighting that came out of that (skill_fit x4, budget,
client_quality, core_stack/invite; competition and description_quality graded
but not counted; the four backwards bonuses dropped) changes what every stored
score MEANS. Leaving history on the old scale would put two incompatible
numbers in the same column and make every band, funnel and drill in the cockpit
lie about one of them.

IT COSTS NOTHING TO FIX, because the score is now computed in code from the
stored breakdown: `scoring.compute_total` reads the same graded dimensions the
model returned at the time and re-does the arithmetic. 4,343 rows carry a
usable breakdown and are re-scored exactly; the ~969 older rows (pre-v2.3
shape) have no components to re-derive from and are LEFT ALONE, tagged so the
cockpit can tell them apart rather than silently mixing scales.

WHAT IT DOES NOT DO: it does not re-run the hard rules over history and it does
not touch `status`, `hard_rule_skip` or `clickup_task_id`. Those jobs really
were carded, and rewriting that would erase the evidence this whole change was
argued from. The new gate applies to what arrives next.

Usage: PYTHONPATH=. python3 scripts/rescore_v3.py [--apply] [--since YYYY-MM-DD]
"""
import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config  # noqa: E402
from modules.upwork import scoring  # noqa: E402

_GRADES = ("skill_fit", "budget", "client_quality", "competition",
           "description_quality")


def recomputable(bd) -> bool:
    """A breakdown we can re-derive from. Anything else keeps its old score —
    a guessed component would be a fabricated number in a money-adjacent
    ranking, which is the one thing this codebase does not do."""
    return (isinstance(bd, dict) and isinstance(bd.get("bonuses"), dict)
            and all(isinstance(bd.get(k), (int, float)) for k in _GRADES))


def was_gated(bd) -> bool:
    """The stored gate note is the record of it. 'lifted by code' means the
    exemption already won and the row is not capped."""
    note = bd.get("gate") or ""
    return note.startswith("gated:")


def plan(conn, since=None) -> tuple:
    sql = ("SELECT id, score, loom_flag, score_breakdown_json FROM scored_jobs"
           " WHERE score IS NOT NULL")
    args = []
    if since:
        sql += " AND fetched_at >= ?"
        args.append(since)
    changes, skipped = [], 0
    for r in conn.execute(sql, args):
        try:
            bd = json.loads(r["score_breakdown_json"] or "{}")
        except json.JSONDecodeError:
            bd = None
        if not recomputable(bd):
            skipped += 1
            continue
        base, raw = scoring.compute_total(bd)
        new = min(raw, scoring._GATE_CAP) if was_gated(bd) else raw
        bd["base_total"], bd["raw_total"] = base, raw
        bd["total"] = new
        bd["scorer_version"] = scoring.SCORER_VERSION
        changes.append({"id": r["id"], "old": r["score"], "new": new,
                        "loom": 1 if new >= config.LOOM_FLAG_THRESHOLD else 0,
                        "json": json.dumps(bd)})
    return changes, skipped


def band_counts(scores) -> dict:
    return Counter(config.score_band(s) for s in scores)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--since", default=None)
    a = ap.parse_args()
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        changes, skipped = plan(conn, a.since)
        before = band_counts([c["old"] for c in changes])
        after = band_counts([c["new"] for c in changes])
        moved = sum(1 for c in changes if c["old"] != c["new"])
        report = {"mode": "APPLY" if a.apply else "DRY-RUN",
                  "scorer_version": scoring.SCORER_VERSION,
                  "recomputable": len(changes), "left_on_old_scale": skipped,
                  "scores_changed": moved,
                  "bands_before": dict(before), "bands_after": dict(after)}
        if a.apply:
            for c in changes:
                conn.execute(
                    "UPDATE scored_jobs SET score=?, loom_flag=?,"
                    " score_breakdown_json=?, updated_at=datetime('now')"
                    " WHERE id=?", (c["new"], c["loom"], c["json"], c["id"]))
            conn.commit()
        print(json.dumps(report, indent=1))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
