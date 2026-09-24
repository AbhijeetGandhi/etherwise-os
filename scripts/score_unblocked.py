#!/usr/bin/env python3
"""Score the jobs a hard rule used to kill and no longer does.

Abhijeet, 2026-09-09, on "Airtable + Make.com Expert - Ecommerce Operations,
Production Dashboard, and Marketing Hub": "I don't know why this is not even
showing up". It had been hard-skipped as `off_domain_title` — the word
`marketing` matched inside the name of a thing they wanted built — so it was
never scored, never carded, and nothing downstream could recover it. Loosening
the rule fixes the NEXT one; this fixes the ones already lost.

A hard rule runs before the model, so its victims carry no score at all. That
makes them invisible to every repair that works on scores (rescore_v3,
board_reopen_misscored). The only way back is to run the model on them.

SCOPED TIGHT, because model calls cost money and old listings are dead anyway:
only rows whose STORED skip reason the current rules no longer produce, only
within --days, and only reasons named in --reasons (default: the one that
changed). It scores; it does not draft. The scan's own push-retry path cards
anything scored and uncarded within CLICKUP_PUSH_RETRY_DAYS, so the card
follows on its own.

--limit/--sample, 2026-09-23: the rollback of the two 08-09 rules plus the
core-stack exemption on the rate floors unblocks ~1,400 rows at once, and
scoring all of them to find out what fraction are any good is the expensive way
to learn a number a random 60 will tell you. `--sample N` draws a uniform
sample (seeded, so the same draw is reproducible); `--limit N` takes the newest
N. Sample to MEASURE, then run the whole thing once the number is known.

Usage: PYTHONPATH=. python3 scripts/score_unblocked.py [--live] [--days 14]
                        [--reasons off_domain_title,no_platform_named]
                        [--sample 60 | --limit 200]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config, db  # noqa: E402
from modules.upwork import scoring  # noqa: E402

TASK = "score_unblocked"


def candidates(days: int, reasons) -> list:
    with db.connect(config.DB_PATH) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM scored_jobs WHERE hard_rule_skip IS NOT NULL"
            " AND score IS NULL AND fetched_at >= datetime('now', ?)",
            (f"-{days} days",))]
    out = []
    for r in rows:
        if reasons and r.get("hard_rule_skip") not in reasons:
            continue
        if scoring.hard_rule_skip(r) is None:      # the rule no longer bites
            out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--reasons", default="off_domain_title")
    ap.add_argument("--sample", type=int, default=0,
                    help="uniform random sample of this many (seeded)")
    ap.add_argument("--limit", type=int, default=0, help="newest N only")
    ap.add_argument("--seed", type=int, default=20260923)
    a = ap.parse_args()
    reasons = {x.strip() for x in a.reasons.split(",") if x.strip()}
    rows = candidates(a.days, reasons)
    population = len(rows)
    if a.sample and a.sample < len(rows):
        random.Random(a.seed).shuffle(rows)
        rows = rows[:a.sample]
    elif a.limit:
        rows = sorted(rows, key=lambda r: r.get("fetched_at") or "",
                      reverse=True)[:a.limit]
    done, hot, failed = [], 0, []
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
                    " loom_flag=?, status='Scored', hard_rule_skip=NULL,"
                    " first_scored_at=datetime('now'), updated_at=datetime('now')"
                    " WHERE id=? AND score IS NULL",
                    (v["score"], json.dumps(v["breakdown"]), v["loom_flag"],
                     r["id"]))
            hot += 1 if v["score"] >= config.HOT_LEAD_THRESHOLD else 0
            done.append({"id": r["id"], "score": v["score"],
                         "was": r["hard_rule_skip"], "title": (r["title"] or "")[:70]})
    bands = Counter(config.score_band(d["score"]) for d in done)
    carded = sum(1 for d in done if d["score"] >= config.SCORE_FLOOR)
    print(json.dumps({
        "mode": "LIVE" if a.live else "DRY-RUN",
        "population": population, "candidates": len(rows), "scored": len(done),
        "hot": hot, "hot_bar": config.HOT_LEAD_THRESHOLD, "failed": failed,
        "bands": dict(bands),
        "card_rate": round(carded / len(done), 3) if done else None,
        "projected_cards_from_population":
            round(population * carded / len(done)) if done else None,
        "rows": sorted(done, key=lambda x: -x["score"]) or
                [{"title": (r["title"] or "")[:70], "was": r["hard_rule_skip"]}
                 for r in rows],
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
