"""Proposals through the MCP — use case C, Phase 0.2 (Abhijeet, 26 Aug 17:55:
"use case C next").

WHY. `proposals.status` had no writer in v3 (finding 3): Won / Lost / Expired
were the May import, the win rate on the cockpit a May snapshot, and the
Sunday outcome email skipped every week on "no closed proposals". The hourly
sync only ever learned about a proposal once the client opened a room
(ROOM_LIST), so the 30 proposals currently open on Upwork were absent from
the table; the GraphQL vendorProposals pass that would have listed them has
been scope-blocked since 9 June. `list_freelancer_proposals` answers through
the MCP with the SAME `vendorProposals` node the adapter already parses
(fixture tests/fixtures/mcp/proposals_list.json, captured 25 Aug on his OK).

HOW (run by sync_state.sync() every hour at :45, in place of the blocked
GraphQL pass; one MCPSession per sync):
  * the tool filters ONE status per call and pages 10 at a time, newest
    modified first. A round = one `list` per status in
    config.MCP_PROPOSAL_STATUSES (Accepted = SUBMITTED in the tool's words,
    Offered, Hired, Activated = an invitation accepted, Declined, Withdrawn,
    Archived; Pending is transient and skipped), stopping at the last
    modified instant the previous round stored for that status
    (sync_cursors `upwork_proposals_modified:<Status>`), so a quiet hour is
    seven one-page calls. The first round has no cursors and pages back up
    to MCP_PROPOSALS_MAX_PAGES per status — the backlog since the freeze.
  * every node goes through sync_state.upsert_proposal (the flat shape via
    _vp_node_to_proposal): telemetry fill-or-keep, thread_id preserved, the
    Upwork status recorded in `upwork_status`. Then the map: the proposal's
    own status first (config.MCP_PROPOSAL_STATUS_MAP, decision 4: Hired →
    Won, Declined → Lost, Withdrawn → Withdrawn, Activated → Interview) and,
    when that writes nothing, the (list, own status) pair
    (config.MCP_PROPOSAL_LIST_MAP, his 26 Aug 19:05 answer: submitted + job
    closed/gone → Expired, invitation accepted + job closed/gone → Lost,
    "Archived (job closed)" → Expired, or Lost on an Interview already
    held). A terminal status is NEVER flipped — the
    UPDATE's WHERE clause excludes them, and a row whose stored verdict
    disagrees with Upwork's is counted (`terminal_disagreements`) for a
    human, never rewritten.
  * an answer that is not a list (a text error) fails the pass loudly; a
    pass failure is a `proposals_pass_failed` warn anomaly on the sync, not a
    failed sync (rooms and contracts still landed).

WHAT THE FIRST LIVE ROUND TAUGHT (26 Aug 18:30-18:38, run 7241: 818 seen,
331 new rows, 21 Won / 6 Lost / 20 Withdrawn / 37 Interview written): the
list's `status` is NOT the proposal's own status. The Hired list (231) held
42 nodes that say Hired, 29 that say Activated and ~129 that say Accepted —
the JOB was filled, by us or by someone else; the Declined list (186) held 6
Declined and ~175 Accepted; 154 'Accepted' rows date back to 2023 with no
room and no contract. So the list is the job-level outcome bucket and the
node status our proposal's own last state; decision 4's map reads the node
status and is right as far as it goes. Both dimensions are now stored on the
row (migration 032: mcp_list_status, upwork_status_label) so the rest of the
map — an Accepted proposal in the Hired list is a job someone else won — can
be read and decided by him, never guessed.

Read-only on Upwork by the transport's allowlist. Nothing here sends.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Optional

from core import config, db
from modules.upwork import sync_state
from modules.upwork.feeds import mcp_search

TOOL = "list_freelancer_proposals"
CURSOR_PREFIX = "upwork_proposals_modified:"
PAGE = 10


def _nodes_of(res) -> tuple:
    """(nodes, page_info, total) from a list answer; ([], {}, None) when the
    envelope carries no list at all."""
    if not isinstance(res, dict):
        return [], {}, None
    vp = ((res.get("data") or {}).get("vendorProposals")
          or res.get("vendorProposals") or {})
    edges = vp.get("edges") if isinstance(vp, dict) else None
    if edges is None and isinstance(res.get("proposals"), list):
        edges = res["proposals"]
    if edges is None:
        return [], {}, None
    nodes = [(e.get("node") or e) for e in edges if isinstance(e, dict)]
    page_info = (vp.get("pageInfo") if isinstance(vp, dict) else None) or {}
    if not page_info and res.get("next_cursor"):
        page_info = {"hasNextPage": bool(res.get("hasMore")), "endCursor": res["next_cursor"]}
    total = vp.get("totalCount") if isinstance(vp, dict) else res.get("totalCount")
    return nodes, page_info, total


def modified_of(node: dict) -> Optional[str]:
    """The node's modified instant as ISO (the wire carries epoch-ms rawValue
    + an ISO displayValue; either is accepted)."""
    audit = node.get("auditDetails") or {}
    m = audit.get("modifiedDateTime") or node.get("modifiedDateTime")
    if isinstance(m, dict):
        m = m.get("displayValue") or m.get("rawValue")
    return sync_state.iso_dt(m)


def status_of(node: dict) -> Optional[str]:
    st = node.get("status")
    return st.get("status") if isinstance(st, dict) else st


def status_label_of(node: dict) -> Optional[str]:
    st = node.get("status")
    return st.get("status_label") if isinstance(st, dict) else None


def record_listing(conn: sqlite3.Connection, pid: str, list_status: str, node: dict) -> None:
    """Which list the proposal was seen in + Upwork's label (migration 032):
    the list is the job-level outcome bucket, the node status our own last
    state — both are needed to read a row (first live round, 26 Aug)."""
    conn.execute(
        "UPDATE proposals SET mcp_list_status=?, upwork_status_label=?,"
        " mcp_listed_at=datetime('now') WHERE id=?",
        (list_status, status_label_of(node), pid))


def fetch_status(session, org: str, status: str, since: Optional[str],
                 log: Callable[[str], None], max_pages: Optional[int] = None) -> dict:
    """Every proposal in `status` modified after `since` (all of them, up to
    max_pages, when there is no cursor yet), newest first. {nodes, pages,
    total, more} — `more` says the page cap stopped the read."""
    max_pages = max_pages if max_pages is not None else config.MCP_PROPOSALS_MAX_PAGES
    nodes, cursor, pages, total, more = [], None, 0, None, False
    while pages < max_pages:
        params = {"status": status, "sort_field": "MODIFIEDDATETIME",
                  "sort_order": "DESC", "limit": PAGE}
        if cursor:
            params["cursor"] = cursor
        res = mcp_search.paced_call(session, TOOL, {"action": "list", "org_uid": org,
                                                    "params": params}, log) or {}
        page_nodes, page_info, page_total = _nodes_of(res)
        if page_total is None and not page_nodes:
            if set(res) == {"text"} or res.get("status") not in (None, "ok"):
                raise RuntimeError(f"{TOOL} list {status}: not a proposals answer:"
                                   f" {str(res)[:160]!r}")
        pages += 1
        if total is None:                 # the same count rides on every page
            total = page_total
        stop = False
        for n in page_nodes:
            m = modified_of(n)
            if since and m and m < since:
                stop = True
                break
            nodes.append(n)
        if stop or not page_nodes or not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break
    else:
        more = True
    return {"nodes": nodes, "pages": pages, "total": total, "more": more}


def _cursor(conn: sqlite3.Connection, status: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM sync_cursors WHERE name=?",
                       (CURSOR_PREFIX + status,)).fetchone()
    return row["value"] if row else None


def _set_cursor(conn: sqlite3.Connection, status: str, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_cursors (name, value, updated_at) VALUES (?,?,datetime('now'))"
        " ON CONFLICT(name) DO UPDATE SET value=MAX(value, excluded.value),"
        " updated_at=datetime('now')", (CURSOR_PREFIX + status, value))


def verdict_for(upwork_status: Optional[str], list_status: Optional[str],
                current: Optional[str]) -> Optional[str]:
    """The status to write, or None: the proposal's own status first
    (decision 4), then the (list, own status) pair (his 26 Aug 19:05 answer);
    an Expired becomes Lost when our row already holds an Interview — we were
    in the room, the job went elsewhere."""
    own = config.MCP_PROPOSAL_STATUS_MAP.get(upwork_status or "")
    listed = (config.MCP_PROPOSAL_LIST_MAP.get((list_status, upwork_status or ""))
              if list_status else None)
    if own in sync_state.TERMINAL:
        target = own                       # Upwork's own verdict wins
    elif listed:
        target = listed                    # a closed job ends a live Interview
    else:
        target = own
    if target == "Expired" and current == "Interview":
        target = "Lost"
    return target


def apply_outcome(conn: sqlite3.Connection, pid: str, upwork_status: str,
                  list_status: Optional[str] = None) -> str:
    """The map on one row: 'won' | 'lost' | 'expired' | 'withdrawn' |
    'interview' when written, 'kept' when the row already says so,
    'terminal' when the stored verdict is terminal (never flipped; a
    disagreement is counted by the caller), 'none' when the map says leave
    it. A non-terminal Interview may become Lost / Expired / Won."""
    row = conn.execute("SELECT status FROM proposals WHERE id=?", (pid,)).fetchone()
    if row is None:
        return "none"
    current = row["status"]
    target = verdict_for(upwork_status, list_status, current)
    if not target:
        return "none"
    if current == target:
        return "kept"
    if current in sync_state.TERMINAL:
        return "terminal"
    marks = ",".join("?" * len(sync_state.TERMINAL))
    conn.execute(
        f"UPDATE proposals SET status=?, updated_at=datetime('now')"
        f" WHERE id=? AND (status IS NULL OR status NOT IN ({marks}))",
        (target, pid, *sync_state.TERMINAL))
    return target.lower()


def backfill_verdicts(conn: sqlite3.Connection) -> dict:
    """The map over every row already listed (mcp_list_status set) whose
    verdict is missing or non-terminal — run once when the second half of
    the map landed (26 Aug), harmless to repeat."""
    marks = ",".join("?" * len(sync_state.TERMINAL))
    out = {}
    for r in conn.execute(
            f"SELECT id, upwork_status, mcp_list_status FROM proposals"
            f" WHERE mcp_list_status IS NOT NULL AND (status IS NULL OR status NOT IN ({marks}))",
            sync_state.TERMINAL).fetchall():
        o = apply_outcome(conn, r["id"], r["upwork_status"], r["mcp_list_status"])
        out[o] = out.get(o, 0) + 1
    return out


def run(ctx, session, org: str, statuses=None, max_pages: Optional[int] = None) -> dict:
    """One round over the statuses: network first (every list read), then one
    write transaction. Metrics per status: seen / inserted / updated /
    terminal-preserved / outcomes {won, lost, withdrawn, interview} /
    terminal_disagreements / pages / total / more."""
    statuses = tuple(statuses or config.MCP_PROPOSAL_STATUSES)
    dbp = ctx.db_path or config.DB_PATH
    with db.connect(dbp) as conn:
        cursors = {s: _cursor(conn, s) for s in statuses}
    fetched = {}
    for s in statuses:
        fetched[s] = fetch_status(session, org, s, cursors[s], ctx.log, max_pages)
    metrics = {"statuses": {}, "seen": 0, "inserted": 0, "outcomes": {},
               "terminal_disagreements": 0}
    with db.connect(dbp) as conn:
        for s, got in fetched.items():
            m = {"seen": len(got["nodes"]), "inserted": 0, "updated": 0, "terminal-preserved": 0,
                 "outcomes": {}, "node_statuses": {}, "terminal_disagreements": 0,
                 "pages": got["pages"], "total": got["total"], "more": got["more"],
                 "since": cursors[s]}
            newest = None
            for node in got["nodes"]:
                pid = node.get("id")
                if not pid:
                    continue
                r = sync_state.upsert_proposal(conn, sync_state._vp_node_to_proposal(node),
                                               thread_id=None)
                m[r] = m.get(r, 0) + 1
                record_listing(conn, pid, s, node)
                node_status = status_of(node) or s
                m["node_statuses"][node_status] = m["node_statuses"].get(node_status, 0) + 1
                outcome = apply_outcome(conn, pid, node_status, s)
                if outcome in ("won", "lost", "expired", "withdrawn", "interview"):
                    m["outcomes"][outcome] = m["outcomes"].get(outcome, 0) + 1
                    metrics["outcomes"][outcome] = metrics["outcomes"].get(outcome, 0) + 1
                elif outcome == "terminal":
                    m["terminal_disagreements"] += 1
                    metrics["terminal_disagreements"] += 1
                mod = modified_of(node)
                if mod and (newest is None or mod > newest):
                    newest = mod
            if newest:
                _set_cursor(conn, s, newest)
            metrics["statuses"][s] = m
            metrics["seen"] += m["seen"]
            metrics["inserted"] += m["inserted"]
            if got["more"]:
                ctx.log(f"proposals {s}: page cap {got['pages']} reached — older rows wait")
    ctx.log(f"proposals: {metrics['seen']} seen, {metrics['inserted']} new,"
            f" outcomes {metrics['outcomes']}, disagreements {metrics['terminal_disagreements']}")
    if metrics["terminal_disagreements"]:
        ctx.anomaly("proposal_terminal_disagreement", "warn",
                    {"count": metrics["terminal_disagreements"],
                     "note": "Upwork's status maps to a different verdict than the stored"
                             " terminal one — never flipped; read the rows"})
    return metrics
