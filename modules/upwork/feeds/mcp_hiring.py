"""Phase 3 — the hiring side. Abhijeet, 2026-08-27 00:30: "In — the applicant
pull".

Everything else in `modules/upwork/` is him bidding on other people's work.
This is the other direction: his OWN postings on the CLIENT org
(config.UPWORK_ORGS["client"]) and the people bidding on him. The tables are
deliberately separate — one table holding both sides is how a win rate ends up
counting the wrong direction.

WHY IT MATTERS TODAY. The LinkedIn Ghostwriter post went up on 21 August. Six
days later it had **9 applicants, 4 of them never opened, 6 shortlisted, 0
offered** and nothing in v3 could say so; the only place that number lived was
a browser tab. A shortlist that never becomes an offer is a hire that quietly
does not happen.

WHAT IT READS (daily):
  * `get_job_posting list` — PAGE-based (has_more + next_page, not a cursor).
    It already carries the whole funnel: applicants, new_applicants,
    shortlisted, messaged, offered, hired, pending_invitations. "How many
    applied" needs no second call.
  * `list_client_proposals list` per OPEN posting that has applicants — the
    list card carries a `cover_letter_snippet` and a totalCount.
    NOT `get_client_dashboard`, which inlines every cover letter in full
    (thousands of characters a run) to answer the same question.
  * `get_rate_insights` per open posting, at most once every
    MCP_RATE_INSIGHT_DAYS — what the market pays for the work he is buying.
    This tool REFUSES the freelancer org; the client org is the one that
    answers.

TWO WIRE FACTS:
  * SHORTLISTED AND READ ARE ANNOTATIONS, not statuses. `annotations[]` holds
    Read / Shortlisted / Recommended / Sponsored, while `status.status` is the
    client-side vocabulary (Created = applied, Activated = invitation
    accepted, Archived). A shortlisted applicant is still 'Created'.
  * `bid` is a display string ("USD 15.0") and the wire's rawValue is EMPTY on
    this side, so the number is parsed out of the display or it is not known.
    Never invented.

Read-only. Nothing here posts a job, invites, shortlists, offers or hires.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, List, Optional

from core import config, db, runner
from modules.upwork.feeds import mcp_search

POSTING_TOOL = "get_job_posting"
PROPOSAL_TOOL = "list_client_proposals"
RATE_TOOL = "get_rate_insights"
TASK_NAME = "upwork_hiring"

# a posting still taking applications
OPEN_STATUSES = ("PUBLISHED",)
_WRAP_RE = re.compile(r"</?untrusted_participant_content>", re.I)
_MONEY_RE = re.compile(r"(-?[0-9][0-9,]*(?:\.[0-9]+)?)")


def unwrap(value) -> Optional[str]:
    if value is None:
        return None
    return _WRAP_RE.sub("", str(value)).strip() or None


def bid_minor(display) -> Optional[int]:
    """"USD 15.0" -> 1500. The wire's rawValue is empty on the client side, so
    this is the only number available; anything unparseable is None, never 0."""
    if not display:
        return None
    m = _MONEY_RE.search(str(display))
    if not m:
        return None
    try:
        return int((Decimal(m.group(1).replace(",", "")) * 100).to_integral_value())
    except InvalidOperation:
        return None


# ── postings ─────────────────────────────────────────────────────────────────

def fetch_postings(session, org: str, log: Callable[[str], None],
                   max_pages: Optional[int] = None) -> List[dict]:
    """His own postings. PAGE-based: `next_page`, not a cursor — and a page
    that does not advance ends the loop, same rule as everywhere else."""
    pages = max_pages if max_pages is not None else config.MCP_POSTINGS_MAX_PAGES
    out, page, seen, ids = [], 1, set(), set()
    for _ in range(max(1, pages)):
        res = mcp_search.paced_call(session, POSTING_TOOL, {
            "action": "list", "org_uid": org,
            "params": {"limit": config.MCP_POSTINGS_PAGE, "page": page}}, log) or {}
        rows = res.get("postings")
        if not isinstance(rows, list):
            if not out:
                raise RuntimeError(f"{POSTING_TOOL} list {org}: not a postings answer:"
                                   f" {str(res)[:160]!r}")
            break
        for r in rows:                        # a repeated page must not double-count
            if isinstance(r, dict) and r.get("id") and r["id"] not in ids:
                ids.add(r["id"])
                out.append(r)
        nxt = res.get("next_page")
        if not res.get("has_more") or not nxt or nxt in seen or not rows:
            break
        seen.add(nxt)
        page = nxt
    return out


def posting_row(node: dict) -> Optional[dict]:
    pid = node.get("id")
    if not pid:
        return None

    def n(key):
        v = node.get(key)
        return int(v) if isinstance(v, (int, float)) else None

    return {"id": str(pid), "title": node.get("title"),
            "status": node.get("status"), "posting_type": node.get("type"),
            "access": node.get("access"),
            "created_dt": node.get("created_time"),
            "updated_dt": node.get("updated_time"),
            "premium": 1 if node.get("premium") else 0,
            "applicants": n("applicants"), "new_applicants": n("new_applicants"),
            "shortlisted": n("shortlisted"), "messaged": n("messaged"),
            "offered": n("offered"), "hired": n("hired"),
            "pending_invitations": n("pending_invitations")}


def upsert_posting(conn, row: dict) -> str:
    if conn.execute("SELECT 1 FROM upwork_job_postings WHERE id=?", (row["id"],)).fetchone():
        conn.execute(
            "UPDATE upwork_job_postings SET title=COALESCE(?, title), status=?,"
            " posting_type=COALESCE(?, posting_type), access=COALESCE(?, access),"
            " created_dt=COALESCE(created_dt, ?), updated_dt=?, premium=?,"
            " applicants=?, new_applicants=?, shortlisted=?, messaged=?,"
            " offered=?, hired=?, pending_invitations=?,"
            " last_seen_at=datetime('now') WHERE id=?",
            (row["title"], row["status"], row["posting_type"], row["access"],
             row["created_dt"], row["updated_dt"], row["premium"],
             row["applicants"], row["new_applicants"], row["shortlisted"],
             row["messaged"], row["offered"], row["hired"],
             row["pending_invitations"], row["id"]))
        return "updated"
    conn.execute(
        "INSERT INTO upwork_job_postings (id, title, status, posting_type, access,"
        " created_dt, updated_dt, premium, applicants, new_applicants, shortlisted,"
        " messaged, offered, hired, pending_invitations)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (row["id"], row["title"], row["status"], row["posting_type"], row["access"],
         row["created_dt"], row["updated_dt"], row["premium"], row["applicants"],
         row["new_applicants"], row["shortlisted"], row["messaged"], row["offered"],
         row["hired"], row["pending_invitations"]))
    return "inserted"


# ── applicants ───────────────────────────────────────────────────────────────

def fetch_applicants(session, org: str, posting_id: str, log: Callable[[str], None],
                     max_pages: Optional[int] = None) -> List[dict]:
    pages = max_pages if max_pages is not None else config.MCP_APPLICANTS_MAX_PAGES
    out, cursor, seen, ids = [], None, set(), set()
    for _ in range(max(1, pages)):
        params = {"job_posting_id": posting_id, "limit": config.MCP_APPLICANTS_PAGE}
        if cursor:
            params["cursor"] = cursor
        res = mcp_search.paced_call(session, PROPOSAL_TOOL, {
            "action": "list", "org_uid": org, "params": params}, log) or {}
        data = res.get("data") if isinstance(res.get("data"), dict) else {}
        rows = data.get("proposals")
        if not isinstance(rows, list):
            if not out:
                raise RuntimeError(f"{PROPOSAL_TOOL} list {posting_id}: not a proposals"
                                   f" answer: {str(res)[:160]!r}")
            break
        for r in rows:
            if isinstance(r, dict) and r.get("id") and r["id"] not in ids:
                ids.add(r["id"])
                out.append(r)
        info = data.get("pageInfo") or {}
        nxt = info.get("endCursor") or res.get("next_cursor")
        if not info.get("hasNextPage") or not nxt or nxt in seen or not rows:
            break
        seen.add(nxt)
        cursor = nxt
    return out


def _status_of(node: dict) -> tuple:
    """(status, status_label) from either wire shape.

    THE SHAPE MOVED UNDER US. The 2026-08-27 capture sent
    `status: {status: "Created", status_label: "Invitation accepted"}`; by
    2026-09-25 every one of the 16 applicants on his open posting came back
    with `status: "Created"` — a bare string — and `status.get(...)` raised
    `'str' object has no attribute 'get'` on the first applicant. That took the
    whole hiring rail down from 3 Sep: 23 days of nobody seeing who applied to
    his own postings. Both shapes are accepted now; a label the wire no longer
    sends is None, not invented."""
    st = node.get("status")
    if isinstance(st, dict):
        return st.get("status"), st.get("status_label")
    if isinstance(st, str):
        return st, None
    return None, None


def _org_name_of(node: dict) -> Optional[str]:
    """The applicant's full name. Same drift: `organization: {name}` in the
    August capture, a bare `agency: "<name>"` string by late September. Upwork
    abbreviates the person ("Mark Y.") and the full name only ever lived here,
    so losing it silently would leave every applicant half-named."""
    org = node.get("organization")
    if isinstance(org, dict) and org.get("name"):
        return unwrap(org.get("name"))
    agency = node.get("agency")
    return unwrap(agency) if isinstance(agency, str) else None


def applicant_row(node: dict, posting_id: str) -> Optional[dict]:
    aid = node.get("id")
    if not aid:
        return None
    ann = [a for a in (node.get("annotations") or []) if isinstance(a, str)]
    status, status_label = _status_of(node)
    user = node.get("user") if isinstance(node.get("user"), dict) else {}
    return {"id": str(aid), "job_posting_id": posting_id,
            "freelancer_id": user.get("id") or None,
            "freelancer_name": unwrap(user.get("name")),
            "org_name": _org_name_of(node),
            "bid_display": node.get("bid"),
            "bid_minor": bid_minor(node.get("bid")),
            "status": status, "status_label": status_label,
            "annotations_json": json.dumps(ann),
            # annotations, NOT status: a shortlisted applicant is still 'Created'
            "shortlisted": 1 if "Shortlisted" in ann else 0,
            "seen_by_him": 1 if "Read" in ann else 0,
            "cover_snippet": unwrap(node.get("cover_letter_snippet"))}


def upsert_applicant(conn, row: dict) -> str:
    if conn.execute("SELECT 1 FROM upwork_applicants WHERE id=?", (row["id"],)).fetchone():
        conn.execute(
            "UPDATE upwork_applicants SET status=COALESCE(?, status),"
            " status_label=COALESCE(?, status_label), annotations_json=?,"
            " shortlisted=?, seen_by_him=?,"
            " bid_display=COALESCE(?, bid_display), bid_minor=COALESCE(?, bid_minor),"
            " cover_snippet=COALESCE(?, cover_snippet), last_seen_at=datetime('now')"
            " WHERE id=?",
            (row["status"], row["status_label"], row["annotations_json"],
             row["shortlisted"], row["seen_by_him"], row["bid_display"],
             row["bid_minor"], row["cover_snippet"], row["id"]))
        return "updated"
    conn.execute(
        "INSERT INTO upwork_applicants (id, job_posting_id, freelancer_id,"
        " freelancer_name, org_name, bid_display, bid_minor, status, status_label,"
        " annotations_json, shortlisted, seen_by_him, cover_snippet)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (row["id"], row["job_posting_id"], row["freelancer_id"], row["freelancer_name"],
         row["org_name"], row["bid_display"], row["bid_minor"], row["status"],
         row["status_label"], row["annotations_json"], row["shortlisted"],
         row["seen_by_him"], row["cover_snippet"]))
    return "inserted"


# ── what the market pays ─────────────────────────────────────────────────────

def rate_due(conn, posting_id: str, now: Optional[datetime] = None) -> bool:
    r = conn.execute("SELECT rate_fetched_at FROM upwork_job_postings WHERE id=?",
                     (posting_id,)).fetchone()
    if not r or not r["rate_fetched_at"]:
        return True
    cutoff = ((now or datetime.now(timezone.utc))
              - timedelta(days=config.MCP_RATE_INSIGHT_DAYS)).isoformat()
    return str(r["rate_fetched_at"]) < cutoff


def fetch_rate(session, org: str, text: str, log: Callable[[str], None],
               level: str = "expert") -> Optional[dict]:
    res = mcp_search.paced_call(session, RATE_TOOL, {
        "action": "get", "org_uid": org,
        "params": {"experience_level": level, "project_text": text}}, log) or {}
    if res.get("rate_low") is None and res.get("rate_high") is None:
        return None
    return {"low": res.get("rate_low"), "high": res.get("rate_high"),
            "occupation": res.get("occupation"), "sample": res.get("based_on_jobs")}


def store_rate(conn, posting_id: str, rate: dict) -> None:
    conn.execute(
        "UPDATE upwork_job_postings SET rate_low=?, rate_high=?, rate_occupation=?,"
        " rate_sample=?, rate_fetched_at=datetime('now') WHERE id=?",
        (rate.get("low"), rate.get("high"), rate.get("occupation"),
         rate.get("sample"), posting_id))


# ── the screen question ──────────────────────────────────────────────────────

def open_postings(conn) -> list:
    """His live postings with the funnel and, where known, the going rate."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM upwork_job_postings WHERE status IN"
        f" ({','.join('?' * len(OPEN_STATUSES))}) ORDER BY created_dt DESC",
        OPEN_STATUSES)]


def bid_range(conn, posting_id: str) -> Optional[dict]:
    r = conn.execute(
        "SELECT MIN(bid_minor) lo, MAX(bid_minor) hi, COUNT(bid_minor) n"
        " FROM upwork_applicants WHERE job_posting_id=? AND bid_minor IS NOT NULL",
        (posting_id,)).fetchone()
    if not r or not r["n"]:
        return None
    return {"low_minor": r["lo"], "high_minor": r["hi"], "count": r["n"]}


# ── the pass ─────────────────────────────────────────────────────────────────

def run(ctx, session_factory=None) -> dict:
    from modules.upwork.feeds import mcp_client
    org = config.UPWORK_ORGS.get("client")
    if not org:
        raise RuntimeError("no client org in config.UPWORK_ORGS")
    session = (session_factory or mcp_client.MCPSession)()
    if hasattr(session, "initialize"):
        session.initialize()

    postings = fetch_postings(session, org, ctx.log)
    metrics = {"postings": {"seen": len(postings), "inserted": 0, "updated": 0},
               "applicants": {"seen": 0, "inserted": 0, "updated": 0},
               "rates_read": 0, "open": 0}
    dbp = ctx.db_path or config.DB_PATH
    rows = [r for r in (posting_row(p) for p in postings) if r]
    with db.connect(dbp) as conn:
        for row in rows:
            metrics["postings"][upsert_posting(conn, row)] += 1
        targets = [r for r in rows
                   if r["status"] in OPEN_STATUSES and (r["applicants"] or 0) > 0]
        rate_targets = [r for r in rows if r["status"] in OPEN_STATUSES
                        and rate_due(conn, r["id"])]
    metrics["open"] = len([r for r in rows if r["status"] in OPEN_STATUSES])

    fetched, rates = {}, {}                    # network OUTSIDE the transaction
    for r in targets[:config.MCP_APPLICANT_MAX_POSTINGS]:
        fetched[r["id"]] = fetch_applicants(session, org, r["id"], ctx.log)
    for r in rate_targets[:config.MCP_RATE_MAX_READS]:
        got = fetch_rate(session, org, r["title"] or "", ctx.log)
        if got:
            rates[r["id"]] = got

    with db.connect(dbp) as conn:
        for pid, nodes in fetched.items():
            metrics["applicants"]["seen"] += len(nodes)
            for node in nodes:
                row = applicant_row(node, pid)
                if row is None:
                    continue
                metrics["applicants"][upsert_applicant(conn, row)] += 1
        for pid, rate in rates.items():
            store_rate(conn, pid, rate)
        metrics["rates_read"] = len(rates)
    ctx.log(f"hiring: {metrics['open']} open posting(s),"
            f" {metrics['applicants']['seen']} applicants read"
            f" ({metrics['applicants']['inserted']} new), {metrics['rates_read']} rate read(s)")
    return metrics


def task(ctx) -> dict:
    return run(ctx)


def main() -> int:
    result = runner.run_task(TASK_NAME, task, module="upwork")
    print(json.dumps({"run_id": result.run_id, "status": result.status,
                      "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
