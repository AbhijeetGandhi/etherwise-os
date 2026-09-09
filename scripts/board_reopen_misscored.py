#!/usr/bin/env python3
"""Put back the cards the 2026-09-08 sweep closed on a score that was wrong.

Abhijeet, 2026-09-09, on a job the scorer itself called "Hot — bid this one":
"This one is 17 wtf". It was 27. `scoring.compute_total` was halving the
skill-fit grade, so every score since the v3.0 rewrite came out 4-8 points low
— and `board_rescore_sweep` had already closed 1,662 cards partly on those
numbers. 869 of them went for score alone.

This is the other half of that repair. It re-decides every card the sweep
closed, with today's rules and today's scores, and reopens the ones that now
qualify. It is NOT a general un-closer: the only cards it will touch are ones
this sweep closed (read from its own CSVs) that ClickUp still shows as
`skipped`. A card he closed himself, or one board_cleanup expired, is none of
its business.

The comment it leaves says the score was wrong rather than that the rules
changed, because that is what happened.

Usage: PYTHONPATH=. python3 scripts/board_reopen_misscored.py [--live]
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config  # noqa: E402
from modules.upwork import scoring  # noqa: E402
from scripts import board_rescore_sweep as sweep  # noqa: E402

REOPEN_STATUS = "new"
COMMENT = (
    "Reopened — the score that closed this card was wrong.\n\n"
    "The 2026-09-08 rebuild halved the skill-fit grade in code (it was written "
    "for an older prompt that asked the model to pre-double it, and the model "
    "did not). Every score came out 4-8 points low. Corrected, this one scores "
    "{score} against a hot bar of {bar}, so it should never have been closed. "
    "Sorry for the noise.")


def closed_by_the_sweep() -> dict:
    """task_id -> the reason the sweep gave, from its own CSVs. Deduped, and
    only rows it actually decided to CLOSE."""
    out = {}
    for path in sorted(glob.glob(str(sweep.REPORTS / "board-rescore-sweep-*.csv"))):
        with open(path) as fh:
            for row in csv.DictReader(fh):
                if row.get("action") == "CLOSE":
                    out[row["task_id"]] = row.get("why", "")
    return out


def plan(tok: str) -> tuple:
    candidates = closed_by_the_sweep()
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        jobs = sweep._rows(conn)
    finally:
        conn.close()
    reopen, leave = [], Counter()
    for tid, why in candidates.items():
        job = jobs.get(tid)
        if job is None:
            leave["v3 has no record of it"] += 1
            continue
        skip = scoring.hard_rule_skip(job)
        if skip:
            leave[f"still fails: {skip}"] += 1
            continue
        score = job.get("score")
        if score is None or score < config.HOT_LEAD_THRESHOLD:
            leave["still under the hot bar"] += 1
            continue
        reopen.append({"id": tid, "score": score, "title": job.get("title") or "",
                       "closed_because": why})
    return reopen, leave


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    tok = sweep.token()
    reopen, leave = plan(tok)
    reopen.sort(key=lambda r: -r["score"])

    done, failed, already = 0, [], 0
    if a.live:
        print(f"reopening {len(reopen)} cards", flush=True)
        for n, r in enumerate(reopen, 1):
            try:
                # only ever undo THIS sweep's own closure: a card he moved
                # himself since then is his, not ours
                cur = sweep.req("GET", f"/task/{r['id']}", tok)
                if (cur.get("status") or {}).get("status", "").lower() \
                        != sweep.CLOSED_STATUS:
                    already += 1
                    time.sleep(sweep.RATE_SLEEP_S)
                    continue
                sweep.req("PUT", f"/task/{r['id']}", tok, {"status": REOPEN_STATUS})
                time.sleep(sweep.RATE_SLEEP_S)
                sweep.req("POST", f"/task/{r['id']}/comment", tok,
                          {"comment_text": COMMENT.format(
                              score=r["score"], bar=config.HOT_LEAD_THRESHOLD),
                           "notify_all": False})
                done += 1
            except Exception as exc:                       # noqa: BLE001
                failed.append({"id": r["id"], "error": str(exc)[:120]})
            if n % 50 == 0:
                print(f"  {n}/{len(reopen)} reopened, {len(failed)} failed",
                      flush=True)
            time.sleep(sweep.RATE_SLEEP_S)

    print(json.dumps({
        "mode": "LIVE" if a.live else "DRY-RUN",
        "closed_by_the_sweep": sum(leave.values()) + len(reopen),
        "to_reopen": len(reopen), "reopened": done,
        "left_closed": dict(leave), "moved_on_since": already,
        "failed": failed[:10],
        "top": [f"{r['score']} {r['title'][:60]}" for r in reopen[:15]],
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
