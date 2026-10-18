"""The standing-query round against mcp.upwork.com — shared by the shadow
poller and the job-feed provider path (Phase 0.1, Upwork chat 2026-08-25).

NETWORK ONLY, NO DATABASE. Both callers used to interleave MCP calls with an
open write transaction (finding 12: the poller held the lock for 43 s against
a 10 s busy timeout and killed a concurrent writer). Splitting the round into
"fetch everything" (here) and "write once" (the callers) is the fix, and it is
the same house rule as "model calls outside the with-block".

Bounded by construction (API & MCP Terms §4.1): first pages only per query,
one `get` per NEW job, a hard per-run ceiling on gets, a fixed cadence (hourly
until 2026-10-18, every quarter hour since, his decision:
config.UPWORK_SCAN_MINUTES). The bounds are config (MCP_SMART_PAGES,
MCP_PER_QUERY_LIMIT, MCP_MAX_GETS_PER_RUN, MCP_TERM_QUERIES), never literals
here.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

from core import config
from modules.upwork.feeds import mcp_feed

SMART_LABEL = "mcp:smart_search"
_sleep = time.sleep          # injectable for tests
_last_call = [0.0]           # monotonic stamp of the previous paced call


def paced_call(session, tool: str, args: dict, log: Callable[[str], None]):
    """session.call with the server's pace respected: wait out
    MCP_CALL_INTERVAL_S since the previous call, and on a rate-limit refusal
    wait MCP_RATE_LIMIT_WAIT_S and retry ONCE. Measured 2026-08-25 22:41: the
    tool allows ~12 find_jobs requests/min sustained; a refused query would
    otherwise be an hour of silent under-scanning for that term."""
    gap = config.MCP_CALL_INTERVAL_S - (time.monotonic() - _last_call[0])
    if gap > 0 and _last_call[0]:
        _sleep(gap)
    try:
        return session.call(tool, args)
    except Exception as exc:                                     # noqa: BLE001
        if "rate limit" not in repr(exc).lower():
            raise
        log(f"{tool} {args.get('action')}: rate-limited, retrying once after"
            f" {config.MCP_RATE_LIMIT_WAIT_S:.0f}s")
        _sleep(config.MCP_RATE_LIMIT_WAIT_S)
        return session.call(tool, args)
    finally:
        _last_call[0] = time.monotonic()


def in_scan_window(minute: int) -> bool:
    """True from one minute before each scan slot (config.UPWORK_SCAN_MINUTES)
    to five after: the minutes the job scan is reading find_jobs. Another reader
    of the same tool (the funnel's job fetch) waits these out, so the two never
    share the tool's per-minute allowance. The window is the one the hourly
    scan had (:14-:20), kept for every slot."""
    return any((minute - m + 1) % 60 <= 6 for m in config.UPWORK_SCAN_MINUTES)


def org_uid(session) -> str:
    accounts = (session.call("list_accounts", {}) or {}).get("accounts") or []
    for a in accounts:
        if a.get("role") == "TALENT":
            return str(a["org_uid"])
    raise RuntimeError("no TALENT account on this token")


def search_rows(session, org: str, log: Callable[[str], None]) -> dict:
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
    for page in range(config.MCP_SMART_PAGES):
        params = {"cursor": cursor} if cursor else {}
        try:
            res = paced_call(session, "find_jobs",
                             {"action": "smart_search", "org_uid": org,
                              "params": params}, log)
        except Exception as exc:                                 # noqa: BLE001
            log(f"smart_search p{page + 1} failed: {exc!r}")
            break
        absorb(res.get("jobs"), SMART_LABEL)
        pi = res.get("pageInfo") or {}
        cursor = pi.get("endCursor")
        if not pi.get("hasNextPage"):
            break

    for q in config.MCP_TERM_QUERIES:
        try:
            res = paced_call(session, "find_jobs", {
                "action": "search", "org_uid": org,
                "params": {"query": q, "sort": "recency",
                           "limit": config.MCP_PER_QUERY_LIMIT}}, log)
        except Exception as exc:                                 # noqa: BLE001
            log(f"search {q!r} failed: {exc!r}")
            continue
        absorb(res.get("jobs"), f"mcp:{q}")
    return rows


def get_row(session, org: str, jid: str, log: Callable[[str], None]):
    """One `get`; None (logged) when it fails — the search row alone still
    passes the hard rules, so a failed get degrades, never aborts."""
    try:
        return paced_call(session, "find_jobs",
                          {"action": "get", "org_uid": org, "params": {"id": jid}},
                          log)
    except Exception as exc:                                     # noqa: BLE001
        log(f"get {jid} failed: {exc!r}")
        return None


def parse_instant(value) -> Optional[datetime]:
    """An MCP created_date ('2026-08-22T12:25:48+0000') or a config instant
    ('2026-08-26T09:00:00+00:00') as an aware datetime; None if unparseable.
    3.9's fromisoformat rejects the bare '+0000' offset, hence the repair."""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    if len(s) >= 5 and s[-5] in "+-" and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch_new(session, org: str, log: Callable[[str], None], *,
              known: Iterable[str], max_gets: Optional[int] = None,
              claim_since: Optional[str] = None) -> dict:
    """The provider round: search, then normalize every job not in `known`
    (one bounded `get` each). Returns {"jobs": [normalized...],
    "metrics": {...}} — the caller writes. Nothing here opens a connection.

    claim_since: an ISO instant; posts created before it are reported
    (`skipped_before_cut`) and not returned. A post with NO parseable
    created_date is kept — dropping fresh work on a parsing quirk would be
    the silent loss this pipeline exists to prevent."""
    known = set(known)
    max_gets = config.MCP_MAX_GETS_PER_RUN if max_gets is None else max_gets
    cut = parse_instant(claim_since)
    rows = search_rows(session, org, log)
    jobs, gets, skipped_cut, get_capped = [], 0, 0, 0
    by_feed: dict = {}
    for jid, srow in rows.items():
        feed = srow["_feed_source"]
        m = by_feed.setdefault(feed, {"extracted": 0, "known": 0, "new": 0,
                                      "skipped_before_cut": 0, "get_capped": 0})
        m["extracted"] += 1
        if jid in known:
            m["known"] += 1
            continue
        if cut is not None:
            created = parse_instant(srow.get("created_date")
                                    or srow.get("published_date"))
            if created is not None and created < cut:
                m["skipped_before_cut"] += 1
                skipped_cut += 1
                continue
        detail = None
        if gets < max_gets:
            detail = get_row(session, org, jid, log)
            gets += 1
        else:
            m["get_capped"] += 1
            get_capped += 1
        job = mcp_feed.normalize(srow, detail)
        job["_got_detail"] = bool(detail)
        jobs.append(job)
        m["new"] += 1
    return {"jobs": jobs,
            "metrics": {"queries": len(config.MCP_TERM_QUERIES) + config.MCP_SMART_PAGES,
                        "surfaced": len(rows), "new": len(jobs), "gets": gets,
                        "get_capped": get_capped, "skipped_before_cut": skipped_cut,
                        "by_feed": by_feed}}
