"""The Upwork heartbeat — one call every quarter hour that says what is new
(use case G; Abhijeet, 26 Aug 22:30: "the dashboard heartbeat" + "money in
flight + offers").

It also carries the room sweep (use case F, 0.4): the dashboard's own answer
already lists the newest rooms with their last-message instants, so noticing
that a reply landed is FREE — only a room that actually moved costs a read.
Once an IST hour the full room list is paged, because the preview is only the
newest five.

WHY. Two things happen on Upwork that are worth money the hour they happen and
that v3 could not see at all: an OFFER (the `offers` table's last write was
11 June) and an INVITATION (never stored, no table). Polling both every hour
costs two reads and finds nothing 99 times out of 100. `get_freelancer_
dashboard check` is ONE read that returns `offers.count` and
`invitations.count` — plus unread messages, the connects balance, the active
contracts and the newest matching jobs with their proposal counts (the
competition number the scan's rows do not carry). So the rail is:

    one dashboard read  ->  counts
    a count above zero  ->  fetch that thing's detail, once, and card it

A quiet quarter hour is one read. The detail reads only happen when something
is actually there (fixture dashboard_check.json, captured 26 Aug 22:45: 0
offers, 0 invitations, 0 unread).

Cards come from chief_of_staff.rules (rule_upwork_offers), so an offer reaches
Today and the interrupt path the same way every other card does. Nothing here
accepts, declines or answers anything — reading only.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from typing import Callable, Optional

from core import config, db, runner
from modules.upwork import sync_state
from modules.upwork.feeds import mcp_search

TOOL = "get_freelancer_dashboard"
OFFERS_TOOL = "list_offers"
PROPOSALS_TOOL = "list_freelancer_proposals"
TASK_NAME = "upwork_pulse"


# ── the heartbeat ────────────────────────────────────────────────────────────

def check(session, org: str, log: Callable[[str], None]) -> dict:
    return mcp_search.paced_call(session, TOOL, {"action": "check", "org_uid": org}, log) or {}


def read(res) -> dict:
    """The dashboard answer -> the numbers worth acting on. Missing blocks are
    None, never 0: 'the read did not say' and 'there are none' are different
    facts and only one of them is a reason to relax."""
    data = (res or {}).get("data") or {}
    if not isinstance(data, dict):
        return {}

    def count(block):
        b = data.get(block)
        if not isinstance(b, dict):
            return None
        n = b.get("count")
        if n is None and isinstance(b.get("items"), list):
            n = len(b["items"])
        return int(n) if n is not None else None

    msgs = data.get("messages") if isinstance(data.get("messages"), dict) else {}
    conn_block = data.get("connects") if isinstance(data.get("connects"), dict) else {}
    balance = (conn_block.get("balance") or {}) if isinstance(conn_block, dict) else {}
    jobs = (data.get("matching_jobs") or {}).get("jobs") if isinstance(data.get("matching_jobs"), dict) else None
    return {
        "offers": count("offers"),
        "invitations": count("invitations"),
        "unread": msgs.get("unread_in_preview"),
        "rooms": len(msgs.get("rooms") or []) if msgs else None,
        "connects": balance.get("connectsBalance"),
        "matching_jobs": len(jobs) if isinstance(jobs, list) else None,
        "matching_competition": [j.get("proposal_count") for j in (jobs or [])
                                 if isinstance(j, dict) and j.get("proposal_count") is not None],
    }


# ── the detail reads, only when a count says so ──────────────────────────────

def _offer_rows(res) -> list:
    if not isinstance(res, dict):
        return []
    by = ((res.get("data") or {}).get("offersByAttribute")
          or res.get("offersByAttribute") or {})
    rows = by.get("offers") if isinstance(by, dict) else None
    return [r for r in (rows or []) if isinstance(r, dict)]


def fetch_offers(session, org: str, log: Callable[[str], None], states=None) -> list:
    """The pending offers. ONE page: the tool's `hasMore` is true even on an
    empty page (measured 26 Aug), so paging on it would never end; ten is
    every pending offer he has ever had at once, many times over."""
    params = {"limit": 10}
    if states:
        params["states"] = list(states)
    return _offer_rows(mcp_search.paced_call(
        session, OFFERS_TOOL, {"action": "list", "org_uid": org, "params": params}, log))


def offer_node(raw: dict) -> Optional[dict]:
    """An MCP offer -> the shape sync_state.upsert_offer reads. The offer
    carries no client (vendorProposal.user is HIM); the client name is filled
    from the contract or the room by the caller when it is known."""
    oid = raw.get("id")
    if not oid:
        return None
    job = raw.get("job") or {}
    return {"id": str(oid), "title": raw.get("title") or (job.get("content") or {}).get("title"),
            "state": raw.get("state"), "type": raw.get("type"),
            "job": {"id": job.get("id")},
            "client": {"id": None, "name": None},
            "messageToContractor": raw.get("messageToContractor"),
            "state_label": raw.get("state_label"),
            "expected_start": ((raw.get("offerTerms") or {}).get("expectedStartDate"))}


def fetch_invitations(session, org: str, log: Callable[[str], None]) -> list:
    res = mcp_search.paced_call(session, PROPOSALS_TOOL, {
        "action": "invitations", "org_uid": org,
        "params": {"status": "pending", "limit": 10}}, log) or {}
    for key in ("invitations", "items", "results"):
        v = res.get(key)
        if isinstance(v, list):
            return [r for r in v if isinstance(r, dict)]
    data = res.get("data") or {}
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, dict) and isinstance(v.get("edges"), list):
                return [(e.get("node") or e) for e in v["edges"] if isinstance(e, dict)]
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
    return []


def upsert_invitation(conn: sqlite3.Connection, raw: dict) -> str:
    iid = raw.get("id") or raw.get("invitation_uid")
    if not iid:
        return "no_id"
    job = raw.get("jobPosting") or raw.get("job") or {}
    client = raw.get("client") or raw.get("clientOrganization") or {}
    fields = (str((job.get("id") or job.get("jobId") or "")) or None,
              job.get("title") or (job.get("content") or {}).get("title"),
              client.get("name") or raw.get("client_name"),
              raw.get("status") or raw.get("state"),
              sync_state.iso_dt(raw.get("createdDateTime") or raw.get("created_at")
                                or raw.get("created")),
              json.dumps(raw, ensure_ascii=False)[:4000])
    exists = conn.execute("SELECT 1 FROM upwork_invitations WHERE id=?", (str(iid),)).fetchone()
    if exists:
        conn.execute(
            "UPDATE upwork_invitations SET job_id=COALESCE(?, job_id),"
            " job_title=COALESCE(?, job_title), client_name=COALESCE(?, client_name),"
            " state=?, created_dt=COALESCE(created_dt, ?), payload_json=?,"
            " updated_at=datetime('now') WHERE id=?", (*fields, str(iid)))
        return "updated"
    conn.execute(
        "INSERT INTO upwork_invitations (job_id, job_title, client_name, state, created_dt,"
        " payload_json, id) VALUES (?,?,?,?,?,?,?)", (*fields, str(iid)))
    return "inserted"


# ── the pass ─────────────────────────────────────────────────────────────────

ROOMS_SWEEP_CURSOR = "upwork_rooms_hour"


def _ist_hour(now=None) -> str:
    from datetime import datetime as _dt
    return (now or _dt.now(config.TZ)).strftime("%Y-%m-%dT%H")


def rooms_sweep_due(conn, now=None) -> bool:
    """The dashboard preview is the newest FIVE rooms. Once an IST hour the
    room list is paged in full, so a reply on a quiet older room is not missed
    for a day."""
    row = conn.execute("SELECT value FROM sync_cursors WHERE name=?",
                       (ROOMS_SWEEP_CURSOR,)).fetchone()
    return not row or row["value"] < _ist_hour(now)


def mark_rooms_sweep(conn, now=None) -> None:
    conn.execute(
        "INSERT INTO sync_cursors (name, value, updated_at) VALUES (?,?,datetime('now'))"
        " ON CONFLICT(name) DO UPDATE SET value=excluded.value,"
        " updated_at=excluded.updated_at", (ROOMS_SWEEP_CURSOR, _ist_hour(now)))


def run(ctx, session_factory=None) -> dict:
    """One heartbeat. Metrics: the counts, plus what the detail reads did.
    Never raises on a dead session — the pulse is a watcher, and a watcher
    that fails a run every 15 minutes is noise; it says so as an anomaly."""
    from modules.upwork.feeds import mcp_client
    org = config.UPWORK_ORGS["personal"]
    try:
        session = (session_factory or mcp_client.MCPSession)()
        if hasattr(session, "initialize"):
            session.initialize()
        raw_dashboard = check(session, org, ctx.log)
        pulse = read(raw_dashboard)
    except Exception as exc:                                     # noqa: BLE001
        kind = mcp_client.start_failure_kind(exc)
        ctx.log(f"pulse FAILED ({kind}): {exc!r} — {mcp_client.START_FAILURE_ADVICE[kind]}")
        ctx.anomaly("pulse_failed", "warn",
                    {"kind": kind, "error": repr(exc)[:200],
                     "note": mcp_client.START_FAILURE_ADVICE[kind]})
        return {"status": "error", "kind": kind}

    metrics = {"pulse": pulse, "offers": {}, "invitations": {}}
    dbp = ctx.db_path or config.DB_PATH
    offers, invitations = [], []
    if pulse.get("offers"):
        offers = fetch_offers(session, org, ctx.log,
                              states=list(config.MCP_OFFER_PENDING_STATES))
    if pulse.get("invitations"):
        invitations = fetch_invitations(session, org, ctx.log)

    with db.connect(dbp) as conn:
        m = {"seen": len(offers), "inserted": 0, "updated": 0}
        for raw in offers:
            node = offer_node(raw)
            if node is None:
                continue
            r = sync_state.upsert_offer(conn, node, thread_id=None)
            m[r] = m.get(r, 0) + 1
        metrics["offers"] = m
        mi = {"seen": len(invitations)}
        for raw in invitations:
            r = upsert_invitation(conn, raw)
            mi[r] = mi.get(r, 0) + 1
        metrics["invitations"] = mi
    # Use case F rides the same heartbeat and the same session: the room list is
    # ONE call and it is the only thing that can tell us a reply landed. Failing
    # it must not fail the pulse — offers are the pulse's own job.
    try:
        from modules.upwork.feeds import mcp_messages
        with db.connect(dbp) as conn:
            full = rooms_sweep_due(conn)
        metrics["messages"] = mcp_messages.run(ctx, session, org,
                                               dashboard=raw_dashboard, full=full)
        if full:
            with db.connect(dbp) as conn:
                mark_rooms_sweep(conn)
    except Exception as exc:                                     # noqa: BLE001
        ctx.log(f"messages pass FAILED: {exc!r}")
        ctx.anomaly("messages_pass_failed", "warn", {"error": repr(exc)[:200]})
        metrics["messages"] = {"status": "error"}

    if pulse.get("offers") or pulse.get("invitations"):
        ctx.log(f"pulse: {pulse.get('offers')} offer(s), {pulse.get('invitations')}"
                f" invitation(s) — detail read and stored")
    else:
        ctx.log(f"pulse: nothing waiting (unread {pulse.get('unread')},"
                f" connects {pulse.get('connects')})")
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
