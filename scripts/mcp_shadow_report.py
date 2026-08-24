#!/usr/bin/env python3
"""The cutover scorecard: shadow poller vs Chrome scanner, same window.

    PYTHONPATH=. python3 scripts/mcp_shadow_report.py [--hours 48]

Reads mcp_shadow_jobs and scored_jobs, compares only the window where BOTH ran
(from the first shadow row onward), and answers the three questions the
cutover hangs on:

  1. COVERAGE — of the jobs the scraper scored >= HOT (16), how many did the
     shadow ALSO surface? Per feed_source, so the my_feed loss he accepted is
     visible as its own number rather than blended in.
  2. LATENCY — for jobs both saw, who saw them first, by how long?
  3. UNIQUES — what did the MCP surface that the scraper missed entirely, and
     did any of it pass hard rules? (The scraper misses things too; a cutover
     that only counts losses is rigged.)

No recommendation is printed. The numbers are the recommendation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HOURS = int(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else 48
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import config, db                                     # noqa: E402


def main() -> int:
    with db.connect(config.DB_PATH) as conn:
        first = conn.execute(
            "SELECT MIN(first_seen_at) FROM mcp_shadow_jobs").fetchone()[0]
        if not first:
            print("no shadow rows yet — has the poller run?")
            return 1
        # julianday ON BOTH SIDES, never string compare: shadow rows carry
        # 'T...Z' timestamps and scored_jobs carries space-separated ones, and
        # 'T' > ' ' lexicographically — the exact trap nudges.ts_key exists
        # for, walked into again in this script's first version. The symptom
        # was COVERAGE 0/0: every scraper row silently failed the clip and 55
        # genuinely-shared jobs reported as "MCP-only".
        shadow = {r["id"]: dict(r) for r in conn.execute(
            "SELECT * FROM mcp_shadow_jobs WHERE julianday(first_seen_at) >="
            " julianday('now', ?)", (f"-{HOURS} hours",))}
        scraper = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, score, feed_source, first_scored_at, hard_rule_skip,"
            " title FROM scored_jobs WHERE julianday(first_scored_at) >= MAX("
            " julianday('now', ?), julianday(?))", (f"-{HOURS} hours", first))}

    print(f"window: last {HOURS}h, clipped to first shadow row {first}")
    print(f"shadow rows: {len(shadow)} · scraper rows in window: {len(scraper)}")

    hot = {i: r for i, r in scraper.items() if (r["score"] or 0) >= 16}
    both = [i for i in hot if i in shadow]
    missed = [i for i in hot if i not in shadow]
    print(f"\n1. COVERAGE of scraper hot (>=16): {len(both)}/{len(hot)}")
    by_feed: dict = {}
    for i in missed:
        by_feed.setdefault(hot[i]["feed_source"], []).append(i)
    for feed, ids in sorted(by_feed.items()):
        print(f"   missed via {feed}: {len(ids)}")
        for i in ids[:6]:
            print(f"     {i}  [{hot[i]['score']}] {hot[i]['title'][:56]}")

    if both:
        import datetime as dt

        def _p(s):
            s = str(s).replace("Z", "+00:00")
            try:
                d = dt.datetime.fromisoformat(s)
                return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
            except ValueError:
                return None
        deltas = []
        for i in both:
            a, b = _p(scraper[i]["first_scored_at"]), _p(shadow[i]["first_seen_at"])
            if a and b:
                deltas.append((b - a).total_seconds() / 60.0)
        if deltas:
            deltas.sort()
            med = deltas[len(deltas) // 2]
            print(f"\n2. LATENCY on {len(deltas)} shared jobs: median shadow-minus-scraper"
                  f" {med:+.0f} min (negative = shadow saw it first)")

    # belt and braces: in_scored_jobs was stamped at INSERT time against the
    # full scored_jobs table, so it catches overlap even outside the window.
    uniques = [i for i in shadow
               if i not in scraper and not shadow[i].get("in_scored_jobs")]
    clean = [i for i in uniques if not shadow[i]["hard_rule_skip"]]
    print(f"\n3. MCP-ONLY jobs: {len(uniques)} ({len(clean)} pass hard rules)")
    for i in clean[:8]:
        print(f"   {i}  {shadow[i]['title'][:60]}  [{shadow[i]['found_by']}]")

    yield_by = {}
    for r in shadow.values():
        yield_by[r["found_by"]] = yield_by.get(r["found_by"], 0) + 1
    print("\nquery yield (first-finder):")
    for q, n in sorted(yield_by.items(), key=lambda kv: -kv[1]):
        print(f"   {n:>4}  {q}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
