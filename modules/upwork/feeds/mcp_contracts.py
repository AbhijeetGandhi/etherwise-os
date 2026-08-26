"""Contracts through the MCP — use case D, Phase 0.3 (Abhijeet, 26 Aug 19:18:
"0.3 — contracts through the MCP").

WHY. The hourly sync only knows a contract through its room (ROOM_LIST: one
contract id per room), so the `contracts` table held 59 rows, every one
with a room, and the question "is there a second TCC contract?" could not
be answered from it. `list_contracts search` on the PERSONAL org lists
every contract the freelancer holds — the agency's too (Ioannis P, Brandi
Jordan are agency contracts in the ledger), while the agency org itself
refuses the search ("you don't have access to this data"). Four pages, 34
active contracts on 26 Aug (fixture tests/fixtures/mcp/
contracts_search_active.json, his OK). Answer: ONE TCC contract, 44099615,
15 Jul 2026.

HOW (run by sync_state.sync() once per IST day — the first :45 sync after
midnight; sync_cursors `upwork_contracts_day`):
  * `search` per status in config.MCP_CONTRACT_STATUSES (ACTIVE, PAUSED,
    and CLOSED narrowed to contracts ended in the last
    MCP_CONTRACTS_CLOSED_DAYS), 10 a page by offset until hasMore is false
    (page cap MCP_CONTRACTS_MAX_PAGES) — ~6 reads a day;
  * every row → `contracts`: a NEW contract is inserted with what the
    search carries (id, title, status, start, client name, offer id ride in
    the row); an EXISTING one gains the status (ACTIVE / PAUSED / CLOSED),
    an end date when the node carries one, and keeps everything the room
    pass wrote (title, type, rate, job, thread: fill-or-keep). The search
    has no contract type, rate or job id — `get` does (one read per
    contract; not read here, ◆ for him);
  * the org a contract belongs to is read from the ledger: a client the
    agency invoices (upwork_mcp_rows.profile = 'Agency' for that company)
    is an agency contract → contracts.profile 'agency' (the room pass
    writes 'personal' for all; its update is fill-or-keep since today);
  * a status change ACTIVE → CLOSED / PAUSED is counted (`ended`, `paused`)
    — the 0.3 signal the win/loss loop reads.

Read-only on Upwork by the transport's allowlist. A pass failure is a
`contracts_pass_failed` warn anomaly on the sync, never a failed sync.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from core import config, db
from modules.upwork import sync_state
from modules.upwork.feeds import mcp_search

TOOL = "list_contracts"
DAY_CURSOR = "upwork_contracts_day"
PAGE = 10


def _rows_of(res) -> tuple:
    """(rows, has_more, next_offset) from a search answer; ([], False, None)
    when the envelope carries no list."""
    if not isinstance(res, dict):
        return [], False, None
    vc = (res.get("data") or {}).get("vendorContracts") or res.get("vendorContracts") or {}
    rows = vc.get("contracts") if isinstance(vc, dict) else None
    if rows is None and isinstance(res.get("contracts"), list):
        rows = res["contracts"]
    if rows is None:
        return [], False, None
    return [r for r in rows if isinstance(r, dict)], bool(res.get("hasMore")), res.get("next_offset")


def search(session, org: str, statuses: list, log: Callable[[str], None],
           end_date_after: Optional[str] = None, max_pages: Optional[int] = None) -> dict:
    """Every contract in `statuses` (one search, paged by offset). {rows,
    pages, more} — `more` says the page cap stopped the read."""
    max_pages = max_pages if max_pages is not None else config.MCP_CONTRACTS_MAX_PAGES
    rows, offset, pages, more = [], 0, 0, False
    while pages < max_pages:
        params = {"contract_statuses": list(statuses), "limit": PAGE, "offset": offset}
        if end_date_after:
            params["end_date_after"] = end_date_after
        res = mcp_search.paced_call(session, TOOL, {"action": "search", "org_uid": org,
                                                    "params": params}, log) or {}
        page_rows, has_more, next_offset = _rows_of(res)
        if not page_rows and next_offset is None and (set(res) == {"text"}
                                                      or res.get("status") not in (None, "ok")):
            raise RuntimeError(f"{TOOL} search {statuses}: not a contracts answer: {str(res)[:160]!r}")
        pages += 1
        rows.extend(page_rows)
        if not has_more or not page_rows:
            break
        offset = int(next_offset) if next_offset is not None else offset + PAGE
    else:
        more = True
    return {"rows": rows, "pages": pages, "more": more}


def contract_row(node: dict) -> Optional[dict]:
    cid = node.get("id")
    if not cid:
        return None
    org = node.get("clientOrganization") or node.get("clientTeam") or {}
    return {"id": str(cid), "title": node.get("title"),
            "status": (node.get("status") or "").upper() or None,
            "start_dt": sync_state.iso_dt(node.get("startDate") or node.get("createDate")),
            "end_dt": sync_state.iso_dt(node.get("endDate") or node.get("endDateTime")),
            "client": org.get("name"), "client_org_id": org.get("id"),
            "offer_id": node.get("offerId")}


def profile_for(conn: sqlite3.Connection, client: Optional[str]) -> str:
    """'agency' when the agency invoices this client (the ledger's own
    evidence), else 'personal' — the contracts table's spelling."""
    if not client:
        return "personal"
    row = conn.execute("SELECT profile FROM upwork_mcp_rows WHERE company=? AND profile='Agency'"
                       " LIMIT 1", (client,)).fetchone()
    return "agency" if row else "personal"


def upsert(conn: sqlite3.Connection, row: dict) -> str:
    """'inserted' | 'updated' | 'ended' | 'paused' | 'unchanged'. Fill-or-keep
    on everything the room pass knows better."""
    existing = conn.execute("SELECT upwork_status, profile FROM contracts WHERE id=?",
                            (row["id"],)).fetchone()
    profile = profile_for(conn, row["client"])
    if existing is None:
        conn.execute(
            "INSERT INTO contracts (id, title, upwork_status, start_dt, end_dt, client_company,"
            " profile) VALUES (?,?,?,?,?,?,?)",
            (row["id"], row["title"], row["status"], row["start_dt"], row["end_dt"],
             row["client"], profile))
        return "inserted"
    before = existing["upwork_status"]
    conn.execute(
        "UPDATE contracts SET upwork_status=COALESCE(?, upwork_status), title=COALESCE(title, ?),"
        " start_dt=COALESCE(start_dt, ?), end_dt=COALESCE(?, end_dt),"
        " client_company=COALESCE(client_company, ?),"
        " profile=CASE WHEN ?='agency' THEN 'agency' ELSE profile END,"
        " updated_at=datetime('now') WHERE id=?",
        (row["status"], row["title"], row["start_dt"], row["end_dt"], row["client"],
         profile, row["id"]))
    if before != row["status"] and row["status"] == "CLOSED":
        return "ended"
    if before != row["status"] and row["status"] == "PAUSED":
        return "paused"
    return "updated" if before != row["status"] else "unchanged"


def _ist_day(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(config.TZ).strftime("%Y-%m-%d")


def due_today(conn: sqlite3.Connection, now: Optional[datetime] = None) -> bool:
    row = conn.execute("SELECT value FROM sync_cursors WHERE name=?", (DAY_CURSOR,)).fetchone()
    return not row or row["value"] < _ist_day(now)


def run(ctx, session, org: str, now: Optional[datetime] = None,
        max_pages: Optional[int] = None) -> dict:
    """One daily round: the searches (network), then one write."""
    today = _ist_day(now)
    since = (date.fromisoformat(today) - timedelta(days=config.MCP_CONTRACTS_CLOSED_DAYS)).isoformat()
    fetched = {}
    for status in config.MCP_CONTRACT_STATUSES:
        fetched[status] = search(session, org, [status], ctx.log,
                                 end_date_after=since if status == "CLOSED" else None,
                                 max_pages=max_pages)
    metrics = {"statuses": {}, "inserted": 0, "ended": 0, "paused": 0, "agency": 0, "reads": 0}
    dbp = ctx.db_path or config.DB_PATH
    with db.connect(dbp) as conn:
        for status, got in fetched.items():
            m = {"rows": len(got["rows"]), "pages": got["pages"], "more": got["more"],
                 "inserted": 0, "ended": 0, "paused": 0, "updated": 0, "unchanged": 0}
            for node in got["rows"]:
                row = contract_row(node)
                if row is None:
                    continue
                r = upsert(conn, row)
                m[r] = m.get(r, 0) + 1
                if r in ("inserted", "ended", "paused"):
                    metrics[r] += 1
            metrics["statuses"][status] = m
            metrics["reads"] += got["pages"]
            if got["more"]:
                ctx.log(f"contracts {status}: page cap {got['pages']} reached — older rows wait")
        metrics["agency"] = conn.execute(
            "SELECT COUNT(*) FROM contracts WHERE profile='agency'").fetchone()[0]
        conn.execute(
            "INSERT INTO sync_cursors (name, value, updated_at) VALUES (?,?,datetime('now'))"
            " ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
            (DAY_CURSOR, today))
    ctx.log(f"contracts: {metrics['reads']} reads, {metrics['inserted']} new,"
            f" {metrics['ended']} ended, {metrics['paused']} paused, {metrics['agency']} agency")
    return metrics
