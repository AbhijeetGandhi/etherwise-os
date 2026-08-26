"""Upwork financials through the MCP — transactions, the weekly summary, the
connects ledger (use cases A and B in docs/upwork/MCP_USE_CASES.md).

STATUS 2026-08-26 (built overnight on Abhijeet's "if we can access transactions
data then the import feature … can completely be replaced with mcp"): the
REQUEST side is built to the tool reference (`get_freelancer_financials`:
actions transactions / summary / connects_balance, per `org_uid`, dated
windows of at most 53 weeks, `limit` up to 200); the RESPONSE side is one
mapping function per row kind (`transaction_row`, `connects_event`), written
tolerant of the two spellings the Upwork surfaces use (the GraphQL
`transactionHistoryRow` keys and their snake_case twins) and PINNED BY A
FIXTURE the moment he OKs the capture calls (plan §12). Until then this module
is importable and tested on synthetic payloads, and NOT scheduled: no plist,
no SOURCES entry, nothing calls `run()` on a clock.

NETWORK ONLY inside the fetch functions (the mcp_search rule: never an MCP
call inside an open write transaction); `run()` fetches everything first and
writes once through `sync_state.upsert_transaction` — the SAME canonical key
(`upwork:<recordId>`) the CSV import and the GraphQL sync use, so an MCP row
never twins a CSV row: it updates it. Source tag `Upwork-MCP` on the rows this
path inserts. Read-only on Upwork, by the transport's allowlist.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from core import config, db
from modules.upwork import sync_state
from modules.upwork.feeds import mcp_search

TOOL = "get_freelancer_financials"
SOURCE = "Upwork-MCP"
TASK_NAME = "upwork_financials"


# ── accounts ─────────────────────────────────────────────────────────────────

def orgs(session) -> dict:
    """{"personal": org_uid, "agency": org_uid|None} from list_accounts: the
    TALENT account is the person, an AGENCY account is the agency. The agency
    is a second ORG here — the GraphQL sync's failing accounting entity was the
    same organisation reached the wrong way."""
    accounts = (session.call("list_accounts", {}) or {}).get("accounts") or []
    out = {"personal": None, "agency": None}
    for a in accounts:
        role = str(a.get("role") or "").upper()
        if role == "TALENT" and not out["personal"]:
            out["personal"] = str(a.get("org_uid"))
        elif role == "AGENCY" and not out["agency"]:
            out["agency"] = str(a.get("org_uid"))
    if not out["personal"]:
        raise RuntimeError("no TALENT account on this token")
    return out


# ── fetches (network only) ───────────────────────────────────────────────────

def _window(days: int, today: Optional[date] = None) -> tuple:
    hi = today or datetime.now(timezone.utc).date()
    lo = hi - timedelta(days=days)
    return lo.isoformat(), hi.isoformat()


def fetch_transactions(session, org: str, frm: str, to: str, log: Callable[[str], None],
                       limit: int = 200) -> dict:
    """The transactions of one org in [frm, to] (max 53 weeks): {rows, total,
    truncated}. `total_count` above `limit` is reported, never silently
    dropped — the caller narrows the window."""
    res = mcp_search.paced_call(session, TOOL, {
        "action": "transactions", "org_uid": org,
        "params": {"from_date": frm, "to_date": to, "limit": limit}}, log) or {}
    rows = _rows_of(res)
    total = res.get("total_count")
    if total is None:
        total = len(rows)
    truncated = bool(total > len(rows))
    if truncated:
        log(f"{TOOL} transactions {org} {frm}..{to}: {total} exist, {len(rows)} returned")
    return {"rows": rows, "total": total, "truncated": truncated}


def fetch_summary(session, org: str, frm: str, to: str, log: Callable[[str], None]) -> dict:
    """The weekly financial summary for the window: per-client earned /
    billed / fee / tax and the overall totals. `earnings_available: false`
    comes back with a note — relayed, never read as zero."""
    return mcp_search.paced_call(session, TOOL, {
        "action": "summary", "org_uid": org,
        "params": {"from_date": frm, "to_date": to}}, log) or {}


def fetch_connects(session, org: str, log: Callable[[str], None], limit: int = 10) -> dict:
    """The connects balance (free / paid / rollover) and the newest usage
    rows with job titles — the Chrome rail's page, as data."""
    return mcp_search.paced_call(session, TOOL, {
        "action": "connects_balance", "org_uid": org,
        "params": {"limit": max(1, min(10, limit))}}, log) or {}


def _rows_of(res: dict) -> list:
    """The list of transaction rows wherever the envelope keeps it."""
    if not isinstance(res, dict):
        return []
    for key in ("transactions", "rows", "data", "items"):
        v = res.get(key)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            inner = _rows_of(v)
            if inner:
                return inner
    th = ((res.get("transactionHistory") or {}).get("transactionDetail") or {})
    if isinstance(th, dict) and isinstance(th.get("transactionHistoryRow"), list):
        return th["transactionHistoryRow"]
    return []


# ── the row mapping (the part the fixture pins) ──────────────────────────────

def _pick(raw: dict, *names):
    for n in names:
        if n in raw and raw[n] not in (None, ""):
            return raw[n]
    return None


def transaction_row(raw: dict) -> Optional[dict]:
    """One MCP transaction → the shape `sync_state.upsert_transaction` reads
    (the GraphQL `transactionHistoryRow` keys): recordId, type,
    accountingSubtype, description, amount{rawValue,currency},
    createdDateTime, fullyPaidDateTime, status. Tolerates the snake_case
    twins. Returns None when no id can be found — a row without an identity
    cannot be deduped and is skipped with a metric, never inserted."""
    rid = _pick(raw, "recordId", "record_id", "id", "transactionId", "transaction_id")
    if rid is None:
        return None
    amt = raw.get("transactionAmount") or raw.get("amount") or raw.get("transaction_amount")
    if isinstance(amt, (int, float, str)):
        amt = {"rawValue": amt, "currency": _pick(raw, "currency") or "USD"}
    elif not isinstance(amt, dict):
        amt = {}
    amount = {"rawValue": _pick(amt, "rawValue", "raw_value", "amount", "value"),
              "currency": _pick(amt, "currency") or _pick(raw, "currency") or "USD"}
    return {
        "recordId": str(rid),
        "type": _pick(raw, "type", "transactionType", "transaction_type"),
        "accountingSubtype": _pick(raw, "accountingSubtype", "accounting_subtype", "subtype"),
        "description": _pick(raw, "description", "descriptionUI", "description_ui"),
        "amount": amount,
        "createdDateTime": sync_state.iso_dt(_pick(raw, "transactionCreationDate",
                                                    "createdDateTime", "created_at", "date")),
        "fullyPaidDateTime": sync_state.iso_dt(_pick(raw, "fullyPaidDate", "fullyPaidDateTime",
                                                      "fully_paid_at")),
        "status": _pick(raw, "paymentStatus", "status", "payment_status"),
        "assignmentCompanyName": _pick(raw, "assignmentCompanyName", "client", "client_name"),
    }


def connects_event(raw: dict, today: Optional[date] = None) -> Optional[dict]:
    """One connects usage row → a `connects_history` event (the Chrome rail's
    shape): id, event_date, kind, label, job_id, job_title, connects_delta.
    The kind rule is the rail's own (`connects_history._kind`) so the two
    sources agree on what "applied" means; the canonical id hashes the same
    fields the rail hashes, so an event seen by both is one row."""
    from modules.upwork import connects_history as ch
    delta = _pick(raw, "connects", "amount", "delta", "connects_delta", "quantity")
    try:
        delta = int(str(delta).replace("+", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    label = str(_pick(raw, "action", "label", "description", "type") or "").strip()[:120]
    if not label:
        return None
    when = _pick(raw, "date", "event_date", "createdDateTime", "created_at")
    try:
        day = sync_state.iso_dt(when)[:10] if when else (today or datetime.now(timezone.utc).date()).isoformat()
        date.fromisoformat(day)
    except (TypeError, ValueError):
        return None
    job = raw.get("job") if isinstance(raw.get("job"), dict) else {}
    job_id = _pick(raw, "job_id", "jobId") or _pick(job, "id")
    job_title = _pick(raw, "job_title", "jobTitle") or _pick(job, "title")
    if isinstance(job_title, dict):
        job_title = job_title.get("title")
    cid = hashlib.md5(f"{day}|{delta}|{label.upper()}|{job_id or ''}|1".encode("utf-8")).hexdigest()[:16]
    return {"id": cid, "event_date": day, "kind": ch._kind(label, delta, job_id),
            "label": label, "job_id": str(job_id) if job_id else None,
            "job_title": str(job_title)[:160] if job_title else None,
            "connects_delta": delta}


# ── the pass (fetch everything, then write once) ─────────────────────────────

def run(ctx, session_factory=None, *, lookback_days: Optional[int] = None,
        today: Optional[date] = None) -> dict:
    """Transactions for every org over the look-back window → the
    transactions table via the audited upsert; the connects usage rows →
    connects_history. Metrics per org: fetched / inserted / updated /
    quarantined / no_id / truncated. NOT SCHEDULED until the fixtures pin the
    mapping (docs/upwork/MCP_USE_CASES.md §12)."""
    from modules.upwork.feeds.mcp_client import MCPSession
    lookback = lookback_days if lookback_days is not None else config.MCP_FINANCIALS_LOOKBACK_DAYS
    frm, to = _window(lookback, today)
    session = (session_factory or MCPSession)()
    if hasattr(session, "initialize"):
        session.initialize()
    who = orgs(session)
    fetched = {}
    for profile, org in who.items():
        if not org:
            continue
        fetched[profile] = fetch_transactions(session, org, frm, to, ctx.log)
    connects = fetch_connects(session, who["personal"], ctx.log)

    metrics = {"window": [frm, to], "transactions": {}, "connects": {}}
    dbp = ctx.db_path or config.DB_PATH
    with db.connect(dbp) as conn:
        for profile, got in fetched.items():
            m = {"fetched": len(got["rows"]), "inserted": 0, "updated": 0,
                 "quarantined": 0, "no_id": 0, "truncated": got["truncated"],
                 "total": got["total"]}
            for raw in got["rows"]:
                row = transaction_row(raw)
                if row is None:
                    m["no_id"] += 1
                    continue
                r = sync_state.upsert_transaction(conn, row, profile)
                m["inserted" if r == "inserted" else "updated" if r == "updated" else "quarantined"] += 1
                if r == "inserted":
                    conn.execute("UPDATE transactions SET source=? WHERE record_id=?",
                                 (SOURCE, row["recordId"]))
            metrics["transactions"][profile] = m
        events = [e for e in (connects_event(x, today) for x in _usage_rows(connects)) if e]
        new = 0
        for e in events:
            cur = conn.execute(
                "INSERT OR IGNORE INTO connects_history (id, event_date, kind, label,"
                " job_id, job_title, connects_delta) VALUES (?,?,?,?,?,?,?)",
                (e["id"], e["event_date"], e["kind"], e["label"], e["job_id"],
                 e["job_title"], e["connects_delta"]))
            new += cur.rowcount
        metrics["connects"] = {"rows": len(events), "new": new,
                               "balance": _balance_of(connects),
                               "all_new": bool(events) and new == len(events)}
    return metrics


def _usage_rows(res: dict) -> list:
    if not isinstance(res, dict):
        return []
    for key in ("history", "usage", "transactions", "recent", "rows", "items"):
        v = res.get(key)
        if isinstance(v, list):
            return v
    return []


def _balance_of(res: dict) -> Optional[dict]:
    if not isinstance(res, dict):
        return None
    bal = res.get("balance")
    if isinstance(bal, dict):
        return bal
    if isinstance(bal, (int, float)):
        return {"total": bal}
    out = {k: res[k] for k in ("total", "free", "paid", "rollover") if k in res}
    return out or None
