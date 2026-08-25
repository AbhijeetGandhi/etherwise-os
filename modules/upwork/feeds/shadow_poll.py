"""Shadow poll — what would the MCP feed have surfaced? Observe, never act.

Runs the standing-query set against mcp.upwork.com, normalizes every result
through mcp_feed (the seam proven in 92146a3), and records it in
mcp_shadow_jobs. It does not score with a model, does not push to ClickUp,
does not draft, and CANNOT write to Upwork (mcp_client refuses non-read tools
in code). After 24-48h, scripts/mcp_shadow_report.py turns this table into the
cutover decision.

THE STANDING QUERIES live in config (MCP_TERM_QUERIES, MCP_SMART_PAGES, ...)
since the 0.1 provider path (2026-08-25) shares them; the round itself is
feeds/mcp_search.py. n8n is deliberately ABSENT as a query — the n8n exclusion
belongs to hard_rule_skip, which sees n8n jobs arriving via other terms and
skips the sole-tool ones exactly as the scraper path does.

BOUNDED BY CONSTRUCTION, because the API & MCP Terms §4.1 draws the line at
"enumerate or continuously monitor Upwork's available content corpus":
first page only per query, one `get` per NEW job (never re-fetched), a hard
per-run ceiling on gets, and an hourly cadence — a bounded standing search for
one freelancer's own lead flow, not corpus enumeration. The plist carries the
same note.

FINDING 12 (Upwork chat, 2026-08-25): this poll used to open its write
transaction and THEN make up to 25 MCP calls inside it — 43 s holding the
lock against a 10 s busy timeout, killing a concurrent writer at :40. Now:
one short read, every network call with no connection open, one short write.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core import config, db, runner
from modules.upwork import scoring
from modules.upwork.feeds import mcp_feed, mcp_search

TASK_NAME = "mcp_shadow_poll"

# kept as names for the tests/reports that read them; the values are config's
TERM_QUERIES = config.MCP_TERM_QUERIES
SMART_PAGES = config.MCP_SMART_PAGES
PER_QUERY_LIMIT = config.MCP_PER_QUERY_LIMIT
MAX_GETS_PER_RUN = config.MCP_MAX_GETS_PER_RUN
GET_TIMEOUT_BUDGET = len(TERM_QUERIES) + SMART_PAGES + MAX_GETS_PER_RUN + 5


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def poll(ctx) -> dict:
    dbp = ctx.db_path or config.DB_PATH
    from modules.upwork.feeds import mcp_client
    session = mcp_client.MCPSession()
    session.initialize()
    org = mcp_search.org_uid(session)

    # ── 1. one short READ: what do we already hold?
    with db.connect(dbp) as conn:
        known = {r["id"] for r in conn.execute("SELECT id FROM mcp_shadow_jobs")}
        scraper_ids = {r["id"] for r in conn.execute("SELECT id FROM scored_jobs")}
        thin = [r["id"] for r in conn.execute(
            "SELECT id FROM mcp_shadow_jobs WHERE got_detail=0"
            " ORDER BY first_seen_at DESC LIMIT ?", (MAX_GETS_PER_RUN,))]
        thin_json = {r["id"]: r["normalized_json"] for r in conn.execute(
            "SELECT id, normalized_json FROM mcp_shadow_jobs WHERE id IN (%s)"
            % ",".join("?" * len(thin)), thin)} if thin else {}

    # ── 2. NETWORK, with no connection open
    rows = mcp_search.search_rows(session, org, ctx.log)
    now = _now()
    new_ids = [jid for jid in rows if jid not in known]
    seen_again = len(rows) - len(new_ids)
    gets = 0
    fresh: list = []                     # (jid, job, extra, srow) to INSERT
    for jid in new_ids:
        srow = rows[jid]
        get_row = None
        if gets < MAX_GETS_PER_RUN:
            get_row = mcp_search.get_row(session, org, jid, ctx.log)
            gets += 1
        job = mcp_feed.normalize(srow, get_row)
        extra = job.pop("mcp_extra", None)
        fresh.append((jid, job, extra, srow, bool(get_row)))
    # BACKFILL: spend whatever remains of the get budget enriching rows a
    # previous run had to cap (first run inserted 119 with only 25 gets).
    # Without this, got_detail=0 rows keep a rate-unstated shape forever
    # and the report would count phantom hard-rule skips.
    enriched: list = []                  # (jid, job, extra) to UPDATE
    for jid in thin:
        if gets >= MAX_GETS_PER_RUN:
            break
        get_row = mcp_search.get_row(session, org, jid, ctx.log)
        gets += 1
        if not get_row:
            continue
        try:
            srow = json.loads(thin_json.get(jid) or "{}")
        except (TypeError, ValueError):
            srow = {}
        # re-normalize with the search-shaped remnant we stored; the get
        # supplies the fields that were missing
        job = mcp_feed.normalize(
            {"id": jid, "title": srow.get("title"),
             "proposal_count": srow.get("total_applicants"),
             "created_date": srow.get("created_dt"),
             "duration": srow.get("engagement_duration"),
             "engagement": srow.get("engagement"),
             "experience_level": srow.get("experience_level"),
             "job_type": (srow.get("contract_type") or "").lower(),
             "skills": srow.get("skills") or [],
             "client": {}}, get_row)
        extra = job.pop("mcp_extra", None)
        enriched.append((jid, job, extra))

    # ── 3. one short WRITE
    with db.connect(dbp) as conn:
        for jid, srow in rows.items():
            if jid in known:
                conn.execute(
                    "UPDATE mcp_shadow_jobs SET last_seen_at=?,"
                    " also_found_by=? WHERE id=?",
                    (now, ",".join(sorted(set(
                        (srow.get("_also") or []) + [srow["_feed_source"]]))),
                     jid))
        for jid, job, extra, srow, got in fresh:
            conn.execute(
                "INSERT OR IGNORE INTO mcp_shadow_jobs"
                " (id, title, normalized_json, mcp_extra_json, found_by,"
                "  also_found_by, hard_rule_skip, in_scored_jobs, got_detail,"
                "  created_dt, first_seen_at, last_seen_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (jid, job.get("title"), json.dumps(job, default=str),
                 json.dumps(extra, default=str) if extra else None,
                 srow["_feed_source"],
                 ",".join(srow.get("_also") or []),
                 scoring.hard_rule_skip(job),
                 1 if jid in scraper_ids else 0,
                 1 if got else 0,
                 job.get("created_dt"), now, now))
        for jid, job, extra in enriched:
            conn.execute(
                "UPDATE mcp_shadow_jobs SET normalized_json=?,"
                " mcp_extra_json=?, hard_rule_skip=?, got_detail=1,"
                " last_seen_at=? WHERE id=?",
                (json.dumps(job, default=str),
                 json.dumps(extra, default=str) if extra else None,
                 scoring.hard_rule_skip(job), now, jid))

    metrics = {"queries": len(TERM_QUERIES) + SMART_PAGES,
               "surfaced": len(rows), "new": len(new_ids),
               "seen_again": seen_again, "gets": gets,
               "backfilled": len(enriched),
               "get_capped": max(0, len(new_ids) - min(gets, len(new_ids)))}
    ctx.log(f"shadow poll: {json.dumps(metrics)}")
    return metrics


def main() -> int:
    result = runner.run_task(TASK_NAME, poll, module="system")
    print(json.dumps({"status": result.status, "metrics": result.metrics},
                     default=str))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
