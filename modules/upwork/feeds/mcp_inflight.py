"""Money in flight — the hours logged and the milestones agreed that no
invoice exists for yet (Abhijeet, 26 Aug 22:30: "money in flight + offers").

WHY. `transactions` is invoiced money, so Money answered "what came in" and
could not answer "what am I owed". Two blind spots, both found by the first
read (26 Aug 22:42):
  * $565.83 of Monday-to-Wednesday hours across Ioannis and Ryan Stomberg,
    logged, at a known rate, and invisible until Upwork invoices on Sunday;
  * Mustafa's contract 43986743 — $4,500 fixed price, milestone 1 paid,
    milestones 2 and 3 ($1,500 each) NOT FUNDED and past due since 15 and
    22 July. Six weeks of a signed contract stalled, and nothing could say so.

WHAT IT READS (inside the daily upwork_financials rail):
  * `get_freelancer_financials timesheets` over the last
    MCP_TIMESHEET_DAYS, paged 10 at a time -> upwork_timesheets, keyed on the
    wire's own cursor (a contract can log twice on one day). Every hour so far
    is manual (`totalOnlineHoursWorked` 0), and `memo` is his own work note.
  * `list_contracts get` for ACTIVE contracts, MCP_MILESTONE_MAX_GETS a run,
    oldest-checked first -> upwork_milestones + the fields the contracts
    SEARCH does not carry (type, rate, job id). A contract is re-read every
    MCP_CONTRACT_DETAIL_DAYS. `state` is the authority on a milestone, NOT
    fundedAmount: the wire reports fundedAmount == the milestone amount even
    on a NotFunded one.

Nothing here is revenue and nothing here touches the ledger: it is what is
coming, kept beside it. A card for a stalled milestone comes from
chief_of_staff.rules, not from here.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

from core import config, db
from modules.upwork import sync_state
from modules.upwork.feeds import mcp_search

FIN_TOOL = "get_freelancer_financials"
CONTRACT_TOOL = "list_contracts"
PAGE = 10


def _minor(v) -> Optional[int]:
    if v in (None, ""):
        return None
    try:
        return int((Decimal(str(v).replace(",", "").strip()) * 100).to_integral_value())
    except InvalidOperation:
        return None


# ── timesheets ───────────────────────────────────────────────────────────────

def _edges_of(res) -> tuple:
    """(edges, page_info, total) from a timesheets answer."""
    if not isinstance(res, dict):
        return [], {}, None
    rep = ((res.get("data") or {}).get("contractTimeReport")
           or res.get("contractTimeReport") or {})
    if not isinstance(rep, dict):
        return [], {}, None
    edges = [e for e in (rep.get("edges") or []) if isinstance(e, dict)]
    return edges, rep.get("pageInfo") or {}, rep.get("totalCount")


def fetch_timesheets(session, org: str, frm: str, to: str, log: Callable[[str], None],
                     max_pages: int = 10) -> dict:
    """Every time entry in [frm, to]. The tool takes no cursor param, so the
    window itself is the paging unit: a page short of `totalCount` is reported,
    never silently dropped."""
    res = mcp_search.paced_call(session, FIN_TOOL, {
        "action": "timesheets", "org_uid": org,
        "params": {"from_date": frm, "to_date": to, "limit": PAGE}}, log) or {}
    edges, page_info, total = _edges_of(res)
    if not edges and total is None and (set(res) == {"text"} or res.get("status") not in (None, "ok")):
        raise RuntimeError(f"{FIN_TOOL} timesheets {org}: not a timesheet answer: {str(res)[:160]!r}")
    truncated = bool(page_info.get("hasNextPage")) or (total is not None and total > len(edges))
    if truncated:
        log(f"timesheets {frm}..{to}: {total} entries, {len(edges)} read — narrowing")
    return {"edges": edges, "total": total if total is not None else len(edges),
            "truncated": truncated}


def timesheet_row(edge: dict) -> Optional[dict]:
    node = edge.get("node") if isinstance(edge.get("node"), dict) else edge
    cursor = edge.get("cursor")
    contract = (node.get("contract") or {}) if isinstance(node, dict) else {}
    cid, day = contract.get("id"), node.get("dateWorkedOn")
    if not cid or not day:
        return None
    hours = node.get("totalHoursWorked") or 0
    return {"cursor_id": str(cursor or f"{cid}|{day}|{hours}"),
            "contract_id": str(cid), "term_id": str(node.get("termId") or "") or None,
            "contract_title": contract.get("title"),
            "date_worked": str(day)[:10], "week_worked": (node.get("weekWorkedOn") or "")[:10] or None,
            "hours": float(hours), "charges_minor": _minor(node.get("totalCharges")) or 0,
            "online_hours": float(node.get("totalOnlineHoursWorked") or 0),
            "memo": (node.get("memo") or "").strip() or None}


def upsert_timesheet(conn: sqlite3.Connection, row: dict) -> str:
    exists = conn.execute("SELECT 1 FROM upwork_timesheets WHERE cursor_id=?",
                          (row["cursor_id"],)).fetchone()
    fields = (row["contract_id"], row["term_id"], row["contract_title"], row["date_worked"],
              row["week_worked"], row["hours"], row["charges_minor"], row["online_hours"],
              row["memo"])
    if exists:
        conn.execute(
            "UPDATE upwork_timesheets SET contract_id=?, term_id=?, contract_title=?,"
            " date_worked=?, week_worked=?, hours=?, charges_minor=?, online_hours=?,"
            " memo=?, updated_at=datetime('now') WHERE cursor_id=?",
            (*fields, row["cursor_id"]))
        return "updated"
    conn.execute(
        "INSERT INTO upwork_timesheets (contract_id, term_id, contract_title, date_worked,"
        " week_worked, hours, charges_minor, online_hours, memo, cursor_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)", (*fields, row["cursor_id"]))
    return "inserted"


# ── contract detail + milestones ─────────────────────────────────────────────

def fetch_contract(session, cid: str, org: str, log: Callable[[str], None]) -> dict:
    res = mcp_search.paced_call(session, CONTRACT_TOOL, {
        "action": "get", "org_uid": org, "params": {"contract_id": str(cid)}}, log) or {}
    return ((res.get("data") or {}).get("contractDetails")
            or res.get("contractDetails") or {})


def milestone_rows(details: dict) -> list:
    offer = (details.get("offer") or {}) if isinstance(details, dict) else {}
    out = []
    for m in (offer.get("milestones") or []):
        if not isinstance(m, dict) or not m.get("id"):
            continue
        out.append({
            "id": str(m["id"]), "sequence_id": m.get("sequenceId"),
            "description": (m.get("description") or "").strip()[:500] or None,
            "due_dt": sync_state.iso_dt(m.get("dueDateTime")),
            "amount_minor": _minor((m.get("depositAmount") or {}).get("rawValue")),
            "funded_minor": _minor((m.get("fundedAmount") or {}).get("rawValue")),
            "paid_minor": _minor((m.get("paid") or {}).get("rawValue")),
            "state": m.get("state"), "state_label": m.get("state_label")})
    return out


def contract_detail(details: dict) -> dict:
    """The fields the contracts SEARCH does not carry."""
    offer = (details.get("offer") or {}) if isinstance(details, dict) else {}
    terms = (details.get("terms") or {}) if isinstance(details, dict) else {}
    fixed = (terms.get("fixedPriceTerms") or [{}])[0] if terms.get("fixedPriceTerms") else {}
    hourly = (terms.get("hourlyTerms") or [{}])[0] if terms.get("hourlyTerms") else {}
    otype = (offer.get("type") or "").upper()
    return {"job_id": (details.get("job") or {}).get("id"),
            "contract_type": ("FIXED" if "FIXED" in otype else "HOURLY" if "HOURLY" in otype else None),
            "fixed_amount_minor": _minor((fixed.get("fixedAmount") or {}).get("rawValue")),
            "hourly_rate": (hourly.get("hourlyRate") or {}).get("rawValue"),
            "client_company": (details.get("clientOrganization") or {}).get("name")}


def upsert_milestone(conn: sqlite3.Connection, cid: str, m: dict) -> str:
    exists = conn.execute("SELECT state FROM upwork_milestones WHERE id=?", (m["id"],)).fetchone()
    fields = (str(cid), m["sequence_id"], m["description"], m["due_dt"], m["amount_minor"],
              m["funded_minor"], m["paid_minor"], m["state"], m["state_label"])
    if exists:
        conn.execute(
            "UPDATE upwork_milestones SET contract_id=?, sequence_id=?, description=?,"
            " due_dt=?, amount_minor=?, funded_minor=?, paid_minor=?, state=?, state_label=?,"
            " updated_at=datetime('now') WHERE id=?", (*fields, m["id"]))
        return "paid" if exists["state"] != m["state"] and m["state"] == "Paid" else "updated"
    conn.execute(
        "INSERT INTO upwork_milestones (contract_id, sequence_id, description, due_dt,"
        " amount_minor, funded_minor, paid_minor, state, state_label, id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)", (*fields, m["id"]))
    return "inserted"


def contracts_to_read(conn: sqlite3.Connection, limit: int, now: Optional[datetime] = None) -> list:
    """ACTIVE contracts whose detail has never been read, or was read more
    than MCP_CONTRACT_DETAIL_DAYS ago — never-read first, then oldest."""
    floor = ((now or datetime.now(timezone.utc))
             - timedelta(days=config.MCP_CONTRACT_DETAIL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    return [r["id"] for r in conn.execute(
        "SELECT c.id, (SELECT MAX(updated_at) FROM upwork_milestones m"
        "               WHERE m.contract_id = c.id) AS seen"
        " FROM contracts c WHERE c.upwork_status='ACTIVE'"
        " ORDER BY (seen IS NOT NULL), COALESCE(seen, ''), c.start_dt DESC LIMIT ?",
        (limit,)) if r["seen"] is None or r["seen"] < floor]


# ── the pass (called by the daily financials rail) ───────────────────────────

def run(ctx, session, org: str, today: Optional[date] = None,
        lookback_days: Optional[int] = None, max_gets: Optional[int] = None) -> dict:
    day = today or datetime.now(timezone.utc).date()
    days = lookback_days if lookback_days is not None else config.MCP_TIMESHEET_DAYS
    gets = max_gets if max_gets is not None else config.MCP_MILESTONE_MAX_GETS
    frm = (day - timedelta(days=days)).isoformat()
    got = fetch_timesheets(session, org, frm, day.isoformat(), ctx.log)

    dbp = ctx.db_path or config.DB_PATH
    with db.connect(dbp) as conn:
        ids = contracts_to_read(conn, gets)
    details = {cid: fetch_contract(session, cid, org, ctx.log) for cid in ids}

    metrics = {"timesheets": {"seen": len(got["edges"]), "inserted": 0, "updated": 0,
                              "skipped": 0, "total": got["total"], "truncated": got["truncated"]},
               "milestones": {"contracts_read": len(details), "inserted": 0, "updated": 0,
                              "paid": 0}}
    with db.connect(dbp) as conn:
        for edge in got["edges"]:
            row = timesheet_row(edge)
            if row is None:
                metrics["timesheets"]["skipped"] += 1
                continue
            metrics["timesheets"][upsert_timesheet(conn, row)] += 1
        for cid, det in details.items():
            for m in milestone_rows(det):
                r = upsert_milestone(conn, cid, m)
                metrics["milestones"][r] = metrics["milestones"].get(r, 0) + 1
            d = contract_detail(det)
            conn.execute(
                "UPDATE contracts SET contract_type=COALESCE(contract_type, ?),"
                " job_id=COALESCE(job_id, ?), client_company=COALESCE(client_company, ?),"
                " updated_at=datetime('now') WHERE id=?",
                (d["contract_type"], d["job_id"], d["client_company"], cid))
        metrics["unbilled"] = unbilled(conn, day)
        metrics["stalled_milestones"] = len(stalled(conn, day))
    ctx.log(f"in flight: {metrics['timesheets']['seen']} time entries"
            f" ({metrics['timesheets']['inserted']} new),"
            f" {metrics['milestones']['contracts_read']} contract details,"
            f" unbilled {metrics['unbilled']['charges_minor']} minor over"
            f" {metrics['unbilled']['hours']} h, {metrics['stalled_milestones']} stalled milestone(s)")
    return metrics


# ── what the screens ask ─────────────────────────────────────────────────────

def unbilled(conn: sqlite3.Connection, today: Optional[date] = None) -> dict:
    """Hours logged that no invoice covers yet. Upwork invoices a week on the
    Sunday it ends, so 'not yet invoiced' = every entry in a week whose
    invoice date has not passed, plus anything explicitly unmarked.

    It is an ESTIMATE of money coming, and the screens must say so: each entry
    is stored rounded to the cent the way Upwork prints it (29.166667 ->
    $29.17), so a week's total can sit a cent or two off the invoice Upwork
    writes on Sunday."""
    day = today or datetime.now(timezone.utc).date()
    monday = (day - timedelta(days=day.weekday())).isoformat()
    rows = [dict(r) for r in conn.execute(
        "SELECT contract_id, contract_title, SUM(hours) h, SUM(charges_minor) c,"
        " MIN(date_worked) lo, MAX(date_worked) hi FROM upwork_timesheets"
        " WHERE invoiced_at IS NULL AND date_worked >= ? GROUP BY contract_id, contract_title"
        " ORDER BY c DESC", (monday,))]
    return {"since": monday,
            "hours": round(sum(r["h"] or 0 for r in rows), 2),
            "charges_minor": sum(r["c"] or 0 for r in rows),
            "by_contract": [{"contract_id": r["contract_id"], "title": r["contract_title"],
                             "hours": round(r["h"] or 0, 2), "charges_minor": r["c"],
                             "from": r["lo"], "to": r["hi"]} for r in rows]}


def stalled(conn: sqlite3.Connection, today: Optional[date] = None) -> list:
    """Milestones the client never funded whose due date has passed — money
    agreed and not moving. Mustafa's two are the reason this exists."""
    day = (today or datetime.now(timezone.utc).date()).isoformat()
    return [dict(r) for r in conn.execute(
        "SELECT m.id, m.contract_id, m.sequence_id, m.amount_minor, m.due_dt, m.state,"
        " c.title AS contract_title, c.client_company FROM upwork_milestones m"
        " LEFT JOIN contracts c ON c.id = m.contract_id"
        " WHERE m.state IN ('NotFunded', 'Not funded') AND substr(m.due_dt,1,10) < ?"
        " ORDER BY m.due_dt", (day,))]
