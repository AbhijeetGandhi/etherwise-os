"""Shadow poll — what would the MCP feed have surfaced? Observe, never act.

Runs the standing-query set against mcp.upwork.com, normalizes every result
through mcp_feed (the seam proven in 92146a3), and records it in
mcp_shadow_jobs. It does not score with a model, does not push to ClickUp,
does not draft, and CANNOT write to Upwork (mcp_client refuses non-read tools
in code). After 24-48h, scripts/mcp_shadow_report.py turns this table into the
cutover decision.

THE STANDING QUERIES. smart_search brings Upwork's profile-skill matching (the
best_matches analogue — 28 of 50 target jobs came from that feed). The term
list mirrors his curated GraphQL expression PLUS the four gaps Test B found:
'Data Workflow Engineer', the Pilates reporting job, Clay+Pipedrive and
ActiveCampaign matched no standing term, so pipedrive / activecampaign /
clay / "automated reports" are here from day one. n8n is deliberately ABSENT
as a query — the n8n exclusion belongs to hard_rule_skip, which sees n8n jobs
arriving via other terms and skips the sole-tool ones exactly as the scraper
path does.

BOUNDED BY CONSTRUCTION, because the API & MCP Terms §4.1 draws the line at
"enumerate or continuously monitor Upwork's available content corpus":
first page only per query, one `get` per NEW job (never re-fetched), a hard
per-run ceiling on gets, and an hourly cadence — a bounded standing search for
one freelancer's own lead flow, not corpus enumeration. The plist carries the
same note.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from core import config, db, runner
from modules.upwork import scoring
from modules.upwork.feeds import mcp_feed

TASK_NAME = "mcp_shadow_poll"

TERM_QUERIES = (
    "make.com", "airtable", "gohighlevel", "vapi", "claude",
    "ai automation", "workflow automation", "zapier", "twilio",
    "pipedrive", "activecampaign", "clay outreach", "automated reports",
)
SMART_PAGES = 2          # smart_search page 1 + 2 (profile-matched)
PER_QUERY_LIMIT = 10     # server max per page anyway
MAX_GETS_PER_RUN = 25    # detail calls are the expensive half; bound them
GET_TIMEOUT_BUDGET = len(TERM_QUERIES) + SMART_PAGES + MAX_GETS_PER_RUN + 5


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _org_uid(session) -> str:
    accounts = (session.call("list_accounts", {}) or {}).get("accounts") or []
    for a in accounts:
        if a.get("role") == "TALENT":
            return str(a["org_uid"])
    raise RuntimeError("no TALENT account on this token")


def _search_rows(session, org_uid: str, log) -> dict:
    """{job_id: search_row} across every standing query, first pages only.
    A row keeps the FIRST query that found it in `_feed_source`; later finders
    are appended to `_also` so the report can rank query yield."""
    rows: dict = {}

    def absorb(jobs, label):
        for j in jobs or []:
            jid = str(j.get("id") or "")
            if not jid:
                continue
            if jid in rows:
                rows[jid].setdefault("_also", []).append(label)
            else:
                rows[jid] = {**j, "_feed_source": label, "_also": []}

    cursor = None
    for page in range(SMART_PAGES):
        params = {"cursor": cursor} if cursor else {}
        try:
            res = session.call("find_jobs", {"action": "smart_search",
                                             "org_uid": org_uid,
                                             "params": params})
        except Exception as exc:                                 # noqa: BLE001
            log(f"smart_search p{page + 1} failed: {exc!r}")
            break
        absorb(res.get("jobs"), f"mcp:smart_search")
        pi = res.get("pageInfo") or {}
        cursor = pi.get("endCursor")
        if not pi.get("hasNextPage"):
            break

    for q in TERM_QUERIES:
        try:
            res = session.call("find_jobs", {
                "action": "search", "org_uid": org_uid,
                "params": {"query": q, "sort": "recency",
                           "limit": PER_QUERY_LIMIT}})
        except Exception as exc:                                 # noqa: BLE001
            log(f"search {q!r} failed: {exc!r}")
            continue
        absorb(res.get("jobs"), f"mcp:{q}")
    return rows


def poll(ctx) -> dict:
    dbp = ctx.db_path or config.DB_PATH
    from modules.upwork.feeds import mcp_client
    session = mcp_client.MCPSession()
    session.initialize()
    org = _org_uid(session)

    rows = _search_rows(session, org, ctx.log)
    now = _now()
    new_ids, seen_again = [], 0
    with db.connect(dbp) as conn:
        known = {r["id"] for r in conn.execute(
            "SELECT id FROM mcp_shadow_jobs")}
        scraper_ids = {r["id"] for r in conn.execute(
            "SELECT id FROM scored_jobs")}
        for jid, srow in rows.items():
            if jid in known:
                seen_again += 1
                conn.execute(
                    "UPDATE mcp_shadow_jobs SET last_seen_at=?,"
                    " also_found_by=? WHERE id=?",
                    (now, ",".join(sorted(set(
                        (srow.get("_also") or []) + [srow["_feed_source"]]))),
                     jid))
            else:
                new_ids.append(jid)

        gets = 0
        for jid in new_ids:
            srow = rows[jid]
            get_row = None
            if gets < MAX_GETS_PER_RUN:
                try:
                    get_row = session.call("find_jobs", {
                        "action": "get", "org_uid": org,
                        "params": {"id": jid}})
                    gets += 1
                except Exception as exc:                         # noqa: BLE001
                    ctx.log(f"get {jid} failed: {exc!r}")
            job = mcp_feed.normalize(srow, get_row)
            extra = job.pop("mcp_extra", None)
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
                 1 if get_row else 0,
                 job.get("created_dt"), now, now))

        # BACKFILL: spend whatever remains of the get budget enriching rows a
        # previous run had to cap (first run inserted 119 with only 25 gets).
        # Without this, got_detail=0 rows keep a rate-unstated shape forever
        # and the report would count phantom hard-rule skips.
        backfilled = 0
        if gets < MAX_GETS_PER_RUN:
            thin = [r["id"] for r in conn.execute(
                "SELECT id FROM mcp_shadow_jobs WHERE got_detail=0"
                " ORDER BY first_seen_at DESC LIMIT ?",
                (MAX_GETS_PER_RUN - gets,))]
            for jid in thin:
                try:
                    get_row = session.call("find_jobs", {
                        "action": "get", "org_uid": org,
                        "params": {"id": jid}})
                    gets += 1
                except Exception as exc:                         # noqa: BLE001
                    ctx.log(f"backfill get {jid} failed: {exc!r}")
                    continue
                old = conn.execute("SELECT normalized_json FROM mcp_shadow_jobs"
                                   " WHERE id=?", (jid,)).fetchone()
                try:
                    srow = json.loads(old["normalized_json"])
                except (TypeError, ValueError):
                    srow = {}
                # re-normalize with the search-shaped remnant we stored; the
                # get supplies the fields that were missing
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
                conn.execute(
                    "UPDATE mcp_shadow_jobs SET normalized_json=?,"
                    " mcp_extra_json=?, hard_rule_skip=?, got_detail=1,"
                    " last_seen_at=? WHERE id=?",
                    (json.dumps(job, default=str),
                     json.dumps(extra, default=str) if extra else None,
                     scoring.hard_rule_skip(job), now, jid))
                backfilled += 1

    metrics = {"queries": len(TERM_QUERIES) + SMART_PAGES,
               "surfaced": len(rows), "new": len(new_ids),
               "seen_again": seen_again, "gets": gets, "backfilled": backfilled,
               "get_capped": max(0, len(new_ids) - gets)}
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
