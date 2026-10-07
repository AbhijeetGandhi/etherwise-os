"""Upwork financials through the MCP — the transactions rail that replaces the
CSV import, and the connects usage read (use cases A and B in
docs/upwork/MCP_USE_CASES.md).

STATUS 2026-08-26 evening. Built overnight to the tool reference on Abhijeet's
"if we can access transactions data then the import feature … can completely
be replaced with mcp"; PINNED at 16:20 IST by four captures he approved
("the three for transactions + connects"): tests/fixtures/mcp/accounts.json,
financials_transactions_{personal,agency}.json, connects_balance.json. The
agency answers through the MCP (org 1910033523997712724, role FL_AGENCY) where
the GraphQL accounting entity had refused every hour since June. Scheduled
daily at 06:05 IST (launchd/io.etherwise.v3.upwork-financials.plist, SOURCES
key upwork_financials) on his "Finish use case A — retire the CSV import".

THE ROW, as the MCP hands it (GraphQL `transactionHistoryRow`):
  recordId · type APInvoice|APAdjustment|ARInvoice|ARPayment|APPayment ·
  accountingSubtype Hourly|Service Fee|WHT|Membership Fee|Payment|Withdrawal|
  Withdrawal Fee · description · transactionAmount{rawValue signed} ·
  transactionCreationDate = the INVOICE date · paymentStatus (payments only) ·
  assignmentCompanyName.
Two facts the ledger has to live with:
  * DATES. The CSV export carries the PAYMENT date (an 08-16 hourly invoice is
    an 08-26 CSV row); the MCP carries the invoice date and nothing else. A row
    the MCP inserts is dated by invoice — the accrual month he chose for the
    P&L on 2026-08-03 (pnl_month) — and a row the CSV inserted first keeps its
    payment date. Never a fabricated date. Recorded as a decision for him in
    UPWORK_BRIEF.
  * SIGNS. The MCP signs its rows from the Upwork balance's point of view for
    the AP side (fees negative, earnings positive) but AR rows and withdrawals
    come positive; the ledger's rule (sync_state._signed_amount_minor: debit
    types/subtypes negative) reproduces the CSV's signs for every kind seen,
    and an explicitly negative MCP row stays negative whatever its label.
    ON THE AP SIDE A POSITIVE ROW STAYS POSITIVE TOO (2026-10-07): a Service
    Fee or WHT adjustment signed as money IN is that money coming BACK — a
    reversed week returns its fee and its withholding. The label rule alone
    charged both a second time on the first refund this rail read (6 Oct,
    $430.00 taken back: booked as -473.43 where Upwork took 386.57); the
    CSV-era ledger holds the same two rows as credits on all five refunds it
    has. See signed_minor.

WHAT run() DOES, network first, one write transaction after (the mcp_search
rule — never an MCP call inside an open transaction):
  1. list_accounts → the personal (TALENT) and agency (FL_AGENCY) orgs;
  2. `transactions` per org over the last MCP_FINANCIALS_LOOKBACK_DAYS (limit
     200; a `total_count` above it splits the window once, then reports);
  3. every row through sync_state.upsert_transaction under the SAME canonical
     key the CSV and GraphQL rows use (`upwork:<recordId>`) — an MCP row never
     twins a CSV row, it updates it; a NEW row is tagged source 'Upwork-MCP',
     typed like the CSV importer types it (upwork_csv.CSV_TYPE_MAP) and
     bucketed (pnl.bucket) on insert so the Money tab reads it the same hour.
     Withdrawal legs go in as `transfer` rows (signed negative like the 65
     older withdrawal rows; excluded from the P&L) on his 26 Aug 16:50 word —
     the CSV importer had HELD them since Phase 1b and the ledger's last
     withdrawal was 27 May; counted as transfer_legs;
  4. every row read lands in `upwork_mcp_rows` (migration 030) — the MCP side
     of the CSV tie-out bin/doctor runs ("upwork mcp twins");
  5. `connects_balance` (personal) → the newest usage rows mapped onto
     connects_history's vocabulary. SHADOW while config.MCP_CONNECTS_SINCE is
     None: compared against the Chrome rail's rows (a twin = same UTC day,
     delta, job), never inserted. Set the day and rows from that day on are
     written — the Chrome rail must stop the same day (law 2: never both).

  6. (17:55, his OK on the capture) Upwork's own `summary` for every
     completed Mon-Sun week not yet stored, per org → upwork_mcp_summaries
     (migration 031); bin/doctor "upwork weekly tie-out" adds up what this
     rail read for the same days and compares, exact in minor units — the
     continuous replacement for the CSV's running-balance check.

Read-only on Upwork by the transport's allowlist.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

from core import config, db, runner
from modules.finance import pnl
from modules.finance.importers import upwork_csv
from modules.upwork import sync_state
from modules.upwork.feeds import mcp_inflight, mcp_search

TOOL = "get_freelancer_financials"
SOURCE = "Upwork-MCP"
TASK_NAME = "upwork_financials"
FLOOR_CURSOR = "upwork_financials_floor"      # sync_cursors: the earliest window floor read

# role string on list_accounts -> the org slot. The agency's role is
# FL_AGENCY (fixture accounts.json); CLIENT is the hiring side, not money in.
ROLE_PROFILE = {"TALENT": "personal", "FL_AGENCY": "agency", "AGENCY": "agency"}
# the ledger's spelling of the profile (1,731 'Personal' / 47 'Agency' rows;
# 17 stray lowercase ones from an earlier sync are the exception, not the rule)
PROFILE_LABEL = {"personal": "Personal", "agency": "Agency"}

# the withdrawal class (principal + its fee). bin/finance-import-upwork HELD
# these "for Phase 2" since 2026-06-25 and nothing released them — the
# ledger's last withdrawal row was 27 May. Abhijeet, 26 Aug 16:50: "insert them
# as transfers" — signed negative (sync_state's rule), pnl_bucket 'transfer'
# (transfers.is_transfer_leg), excluded from the P&L; counted as transfer_legs.
TRANSFER_SUBTYPES = frozenset({"Withdrawal", "Withdrawal Fee"})

# connects usage: the labels the Chrome rail stored for the same events, so
# connects_history._kind and every reader see one vocabulary
LABELS = {"applied": "Applied to job", "boost": "Connects bid for boosted proposal.",
          "boost_refund": "Boost connects returned (outbid)", "refund": "Connects returned",
          "badge": "Availability Badge", "purchase": "Connects purchased"}
JOB_PREFIX = "~02"
# a proposal costs single- to low double-digit connects; a boost is the bigger
# bid. Used ONLY when the refunded job has no earlier debit on record.
_PROPOSAL_MAX_CONNECTS = 30


# ── accounts ─────────────────────────────────────────────────────────────────

def orgs(session) -> dict:
    """{"personal": org_uid, "agency": org_uid|None} from list_accounts."""
    accounts = (session.call("list_accounts", {}) or {}).get("accounts") or []
    out = {"personal": None, "agency": None}
    for a in accounts:
        profile = ROLE_PROFILE.get(str(a.get("role") or "").upper())
        if profile and not out[profile] and a.get("org_uid"):
            out[profile] = str(a["org_uid"])
    if not out["personal"]:
        raise RuntimeError("no TALENT account on this token")
    return out


# ── fetches (network only) ───────────────────────────────────────────────────

def _window(days: int, today: Optional[date] = None) -> tuple:
    hi = today or datetime.now(timezone.utc).date()
    lo = hi - timedelta(days=days)
    return lo.isoformat(), hi.isoformat()


def _transactions_call(session, org: str, frm: str, to: str, log, limit: int) -> dict:
    res = mcp_search.paced_call(session, TOOL, {
        "action": "transactions", "org_uid": org,
        "params": {"from_date": frm, "to_date": to, "limit": limit}}, log) or {}
    rows = _rows_of(res)
    if not rows and set(res) == {"text"}:
        raise RuntimeError(f"{TOOL} transactions {org}: not a transactions answer:"
                           f" {str(res['text'])[:160]!r}")
    total = res.get("total_count")
    if total is None:
        total = len(rows)
    return {"rows": rows, "total": int(total)}


def fetch_transactions(session, org: str, frm: str, to: str, log: Callable[[str], None],
                       limit: int = 200) -> dict:
    """The transactions of one org in [frm, to] (max 53 weeks): {rows, total,
    truncated, calls}. A `total_count` above `limit` splits the window once
    (two halves, merged by recordId); still above it, `truncated` is True and
    the caller says so — never a silent drop."""
    got = _transactions_call(session, org, frm, to, log, limit)
    calls = 1
    if got["total"] > len(got["rows"]):
        log(f"{TOOL} transactions {org} {frm}..{to}: {got['total']} exist,"
            f" {len(got['rows'])} returned — splitting the window")
        lo, hi = date.fromisoformat(frm), date.fromisoformat(to)
        mid = lo + (hi - lo) // 2
        merged, truncated = {}, False
        for a, b in ((lo.isoformat(), mid.isoformat()),
                     ((mid + timedelta(days=1)).isoformat(), hi.isoformat())):
            part = _transactions_call(session, org, a, b, log, limit)
            calls += 1
            truncated = truncated or part["total"] > len(part["rows"])
            for r in part["rows"]:
                merged.setdefault(str(_pick(r, "recordId", "record_id", "id")), r)
        for r in got["rows"]:
            merged.setdefault(str(_pick(r, "recordId", "record_id", "id")), r)
        rows = list(merged.values())
        return {"rows": rows, "total": max(got["total"], len(rows)),
                "truncated": truncated, "calls": calls}
    return {"rows": got["rows"], "total": got["total"], "truncated": False, "calls": calls}


def fetch_summary(session, org: str, frm: str, to: str, log: Callable[[str], None]) -> dict:
    """Upwork's own financial summary for the window: per-client billed / fee
    / tax / earned and the totals, by INVOICE date (fixture
    financials_summary_personal.json, his OK 26 Aug 17:55). run() stores one
    per completed Mon-Sun week per org (upwork_mcp_summaries, migration 031)
    for bin/doctor's "upwork weekly tie-out"."""
    return mcp_search.paced_call(session, TOOL, {
        "action": "summary", "org_uid": org,
        "params": {"from_date": frm, "to_date": to}}, log) or {}


def fetch_connects(session, org: str, log: Callable[[str], None], limit: int = 10) -> dict:
    """The connects balance (free / paid / rollover) and the newest usage rows
    (the tool's ceiling is 10)."""
    return mcp_search.paced_call(session, TOOL, {
        "action": "connects_balance", "org_uid": org,
        "params": {"limit": max(1, min(10, limit))}}, log) or {}


def _rows_of(res) -> list:
    """The transaction rows wherever the envelope keeps them. Live shape:
    data.transactionHistory.transactionDetail.transactionHistoryRow."""
    if not isinstance(res, dict):
        return []
    th = ((res.get("transactionHistory") or {}).get("transactionDetail") or {})
    if isinstance(th, dict) and isinstance(th.get("transactionHistoryRow"), list):
        return th["transactionHistoryRow"]
    for key in ("transactions", "rows", "items", "data"):
        v = res.get(key)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            inner = _rows_of(v)
            if inner:
                return inner
    return []


# ── the row mapping (pinned by the fixtures) ─────────────────────────────────

def _pick(raw: dict, *names):
    for n in names:
        if n in raw and raw[n] not in (None, ""):
            return raw[n]
    return None


def transaction_row(raw: dict) -> Optional[dict]:
    """One MCP transaction → the shape sync_state.upsert_transaction reads
    (recordId, type, accountingSubtype, description, amount{rawValue,
    currency}, createdDateTime, fullyPaidDateTime, status) plus `company`.
    None when no id can be found — a row without an identity cannot be
    deduped and is skipped with a metric, never inserted."""
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
        "company": _pick(raw, "assignmentCompanyName", "client", "client_name"),
    }


# The AP side — invoices and adjustments on what Upwork owes him — arrives
# signed from the balance's point of view: all seven APAdjustment rows in the
# 26 Aug captures (Service Fee, WHT, Withdrawal Fee) are explicitly negative,
# every APInvoice positive. On these two types the payload's own sign is the
# ledger's. What arrives UNSIGNED is the other side — ARInvoice and APPayment
# are positive in the payload and debits in the ledger — and the label rule
# (sync_state._signed_amount_minor) still signs those.
SELF_SIGNED_TYPES = frozenset({"APInvoice", "APAdjustment"})


def signed_minor(row: dict) -> Optional[int]:
    """The ledger's signed integer minor units for an MCP row: the CSV
    convention (sync_state._signed_amount_minor — Service Fee / WHT /
    Membership Fee / withdrawals negative, earnings and card payments
    positive), and an explicitly negative MCP row stays negative whatever its
    label. None when the MCP gave no amount (the upsert quarantines it).

    A POSITIVE row on the AP side stays positive whatever its label, for the
    same reason: a fee or a withholding signed as money in is that money
    coming back. A reversed week returns its 10% fee and its 0.1% withholding
    ("Service Fee return", "Withholding Tax … return"), and the label rule
    alone made each a second charge. The CSV-era ledger holds both as credits
    on every refund it has (five, 2025-04-01 to 2025-09-16)."""
    raw = (row.get("amount") or {}).get("rawValue")
    if raw in (None, ""):
        return None
    minor = sync_state._signed_amount_minor(row)
    if minor is None:
        return None
    try:
        value = Decimal(str(raw).replace(",", "").strip())
    except InvalidOperation:
        return minor
    if value < 0:
        return -abs(minor)
    if value > 0 and (row.get("type") or "").strip() in SELF_SIGNED_TYPES:
        return abs(minor)
    return minor


def ledger_type(row: dict) -> str:
    """The ledger's `type` for an MCP row — the CSV importer's map on the
    accounting subtype (Hourly → Hourly Earning, Membership Fee → Connect
    Purchase, as the 83 API-era membership rows already read), 'Other' for
    the rest, exactly as a CSV row of the same kind is typed."""
    return upwork_csv.CSV_TYPE_MAP.get((row.get("accountingSubtype") or "").strip(), "Other")


def is_transfer_leg(row: dict) -> bool:
    return ((row.get("accountingSubtype") or "").strip() in TRANSFER_SUBTYPES
            or (row.get("type") or "").strip() == "APPayment")


def as_stored(row: dict) -> dict:
    """The transactions-row view pnl.bucket reads, for the row as this rail
    stores it (type from ledger_type, upwork_type = the API type)."""
    return {"type": ledger_type(row), "upwork_type": row.get("type"),
            "accounting_subtype": row.get("accountingSubtype"),
            "description": row.get("description"), "source": SOURCE, "category": None}


def bucket_for(row: dict) -> str:
    return pnl.bucket(as_stored(row))


# ── connects usage (the Chrome rail's vocabulary) ────────────────────────────

def _usage_rows(res) -> list:
    if not isinstance(res, dict):
        return []
    uh = res.get("usage_history")
    if isinstance(uh, dict) and isinstance(uh.get("transactions"), list):
        return uh["transactions"]
    for key in ("history", "usage", "transactions", "recent", "rows", "items"):
        v = res.get(key)
        if isinstance(v, list):
            return v
    return []


def _balance_of(res) -> Optional[dict]:
    if not isinstance(res, dict):
        return None
    bal = res.get("balance")
    if isinstance(bal, dict):
        out = {"total": _pick(bal, "connectsBalance", "total"),
               "free": _pick(bal, "connectsBalanceFree", "free"),
               "paid": _pick(bal, "connectsBalancePaid", "paid"),
               "rollover": _pick(bal, "rolloverBalance", "rollover")}
        return {k: v for k, v in out.items() if v is not None} or None
    if isinstance(bal, (int, float)):
        return {"total": bal}
    return None


def _job_id(raw: dict) -> Optional[str]:
    jp = raw.get("jobPost") if isinstance(raw.get("jobPost"), dict) else {}
    jid = _pick(jp, "jobId", "id") or _pick(raw, "job_id", "jobId")
    if not jid:
        return None
    jid = str(jid).strip()
    return jid[len(JOB_PREFIX):] if jid.startswith(JOB_PREFIX) else jid


def _job_title(raw: dict) -> Optional[str]:
    jp = raw.get("jobPost") if isinstance(raw.get("jobPost"), dict) else {}
    t = _pick(jp, "jobTitle", "title") or _pick(raw, "job_title", "jobTitle")
    return str(t)[:160] if t else None


def _delta(raw: dict) -> Optional[int]:
    amt = raw.get("amount")
    v = _pick(amt, "rawValue", "displayValue", "value") if isinstance(amt, dict) else amt
    try:
        return int(Decimal(str(v).replace("+", "").replace(",", "").strip()))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _prior_kinds(conn, job_id: str, day: Optional[str] = None, delta: Optional[int] = None) -> set:
    """The kinds already on record for a job — OTHER events than the one being
    classified (same day + delta = its own twin from the Chrome rail, or its
    own row from a previous read; the first live run read the -18 proposal
    of 23 Aug as a boost because its Chrome twin said 'applied')."""
    if conn is None or not job_id:
        return set()
    return {r["kind"] for r in conn.execute(
        "SELECT DISTINCT kind FROM connects_history WHERE job_id=?"
        " AND NOT (event_date = ? AND connects_delta = ?)",
        (job_id, day or "", delta if delta is not None else 0))}


def connects_events(res, conn=None) -> list:
    """The usage rows of a connects_balance answer → connects_history-shaped
    events: id, event_date (the UTC day — what the Chrome rail stored for the
    same events, checked on the 23-24 Aug rows), kind, label, job_id (the
    digits after '~02', as the rail's link parser keeps them), job_title,
    connects_delta, at (the instant).

    The MCP row carries no label, so the kind is read from the shape:
      DEBIT, no job, -1                → badge
      DEBIT with a job                 → applied; when two debits share the
                                          job and the instant the smaller is
                                          the proposal, the larger its boost;
                                          a lone debit on a job that already
                                          has an applied row is a boost
      REFUND_CREDIT with a job         → boost_refund when the job has a boost
                                          on record (or the amount is bigger
                                          than a proposal costs), else refund
      a credit without a job           → purchase
      anything else                    → other (kept raw, label = the type)"""
    rows = []
    for raw in _usage_rows(res):
        delta = _delta(raw)
        when = sync_state.iso_dt(_pick(raw, "createdDateTime", "created_at", "date")) or ""
        if delta is None or len(when) < 10:
            continue
        rows.append({"at": when, "day": when[:10], "delta": delta,
                     "tt": str(_pick(raw, "transactionType", "type") or "").upper(),
                     "job_id": _job_id(raw), "job_title": _job_title(raw)})
    rows.sort(key=lambda r: (r["at"], r["delta"]))
    applied_seen = set()
    out = []
    for r in rows:
        job, delta, tt = r["job_id"], r["delta"], r["tt"]
        if delta < 0 and job:
            pair = [x for x in rows if x is not r and x["job_id"] == job
                    and x["at"] == r["at"] and x["delta"] < 0]
            if pair and any(abs(x["delta"]) < abs(delta) for x in pair):
                kind = "boost"
            elif pair:
                kind = "applied"
            elif job in applied_seen or "applied" in _prior_kinds(conn, job, r["day"], delta):
                kind = "boost"
            else:
                kind = "applied"
            if kind == "applied":
                applied_seen.add(job)
        elif delta < 0 and delta == -1:
            kind = "badge"
        elif delta > 0 and job:
            prior = _prior_kinds(conn, job, r["day"], delta)
            kind = ("boost_refund" if "boost" in prior
                    else "refund" if "applied" in prior and abs(delta) <= _PROPOSAL_MAX_CONNECTS
                    else "boost_refund" if abs(delta) > _PROPOSAL_MAX_CONNECTS
                    else "refund")
        elif delta > 0 and tt not in ("REFUND_CREDIT",):
            kind = "purchase"
        else:
            kind = "other"
        label = LABELS.get(kind) or (tt or "other")
        cid = hashlib.md5(f"mcp|{r['at']}|{delta}|{job or ''}".encode("utf-8")).hexdigest()[:16]
        out.append({"id": cid, "event_date": r["day"], "kind": kind, "label": label,
                    "job_id": job, "job_title": r["job_title"], "connects_delta": delta,
                    "at": r["at"]})
    return out


def connects_twin(conn, e: dict) -> bool:
    """Does the Chrome rail already hold this event? Same UTC day, same
    delta, same job (or both without one) — the id cannot match (the rail
    hashes its label), the shape can."""
    if e.get("job_id"):
        row = conn.execute(
            "SELECT 1 FROM connects_history WHERE event_date=? AND connects_delta=?"
            " AND job_id=? LIMIT 1", (e["event_date"], e["connects_delta"], e["job_id"])).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM connects_history WHERE event_date=? AND connects_delta=?"
            " AND job_id IS NULL LIMIT 1", (e["event_date"], e["connects_delta"])).fetchone()
    return row is not None


# ── the audit twin (migration 030) ───────────────────────────────────────────

def record_seen(conn, row: dict, profile: str, outcome: str, amount_minor) -> None:
    conn.execute(
        "INSERT INTO upwork_mcp_rows (record_id, profile, upwork_type, accounting_subtype,"
        " amount_minor, currency, invoice_dt, status, company, outcome)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(record_id) DO UPDATE SET last_seen_at=datetime('now'),"
        " seen_runs=seen_runs+1, status=COALESCE(excluded.status, status),"
        " amount_minor=COALESCE(amount_minor, excluded.amount_minor),"
        " outcome=CASE WHEN outcome='quarantined' THEN excluded.outcome ELSE outcome END",
        (row["recordId"], profile, row.get("type"), row.get("accountingSubtype"),
         amount_minor, (row.get("amount") or {}).get("currency"), row.get("createdDateTime"),
         row.get("status"), row.get("company"), outcome))


# ── the weekly summary (Upwork's own totals, the tie-out's other side) ───────

def completed_weeks(today: date, n: Optional[int] = None) -> list:
    """The last n completed Mon-Sun weeks before `today`, oldest first, as
    (start, end) ISO dates."""
    n = config.MCP_SUMMARY_WEEKS if n is None else n
    this_monday = today - timedelta(days=today.weekday())
    out = []
    for k in range(n, 0, -1):
        start = this_monday - timedelta(days=7 * k)
        out.append((start.isoformat(), (start + timedelta(days=6)).isoformat()))
    return out


def summaries_due(conn, today: date, profiles) -> list:
    """(profile, start, end) for every completed week not yet stored — a
    missed Monday is read on the next run, never lost."""
    have = {(r["profile"], r["period_start"]) for r in conn.execute(
        "SELECT profile, period_start FROM upwork_mcp_summaries")}
    return [(p, a, b) for p in profiles for a, b in completed_weeks(today)
            if (p, a) not in have]


def _minor(v) -> Optional[int]:
    if v in (None, ""):
        return None
    try:
        return int((Decimal(str(v).replace(",", "").strip()) * 100).to_integral_value())
    except InvalidOperation:
        return None


def summary_row(res: dict) -> Optional[dict]:
    """One summary answer → the upwork_mcp_summaries columns; None when the
    answer is not a summary at all (a text error)."""
    if not isinstance(res, dict) or ("totals" not in res and "earnings_available" not in res):
        return None
    totals = res.get("totals") or {}
    return {"billed_minor": _minor(totals.get("billed")), "fee_minor": _minor(totals.get("fee")),
            "tax_minor": _minor(totals.get("tax")), "earned_minor": _minor(totals.get("earned")),
            "currency": totals.get("currency") or "USD",
            "earnings_available": 1 if res.get("earnings_available", True) else 0,
            "payload_json": json.dumps(res, ensure_ascii=False)}


def store_summary(conn, profile: str, start: str, end: str, row: dict) -> None:
    conn.execute(
        "INSERT INTO upwork_mcp_summaries (profile, period_start, period_end, billed_minor,"
        " fee_minor, tax_minor, earned_minor, currency, earnings_available, payload_json)"
        " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(profile, period_start) DO UPDATE SET"
        " period_end=excluded.period_end, billed_minor=excluded.billed_minor,"
        " fee_minor=excluded.fee_minor, tax_minor=excluded.tax_minor,"
        " earned_minor=excluded.earned_minor, currency=excluded.currency,"
        " earnings_available=excluded.earnings_available, payload_json=excluded.payload_json,"
        " fetched_at=datetime('now')",
        (profile, start, end, row["billed_minor"], row["fee_minor"], row["tax_minor"],
         row["earned_minor"], row["currency"], row["earnings_available"], row["payload_json"]))


# what the summary adds up: the billings (the earnings rows). Fee and tax are
# derived from them (10 % / 0.1 %); memberships, card payments and
# withdrawals are not in a summary.
BILLED_SUBTYPES = frozenset({"Hourly", "Fixed Price", "Fixed-price", "Bonus", "Milestone"})
FEE_SUBTYPES = frozenset({"Service Fee"})
TAX_SUBTYPES = frozenset({"WHT", "Withholding Tax"})
# WHAT THE FIRST STORED WEEKS TAUGHT (26 Aug 19:00): the summary's period is
# the week the invoice was actually CREATED, and that instant is not what
# `transactionCreationDate` shows — that field is the week-END label (a
# Sunday), while the invoice is created the Monday after, or later for the
# agency (its 2 Aug invoices sat in the 10-16 Aug summary; its 9 and 16 Aug
# invoices in 17-23 Aug). So a week's summary is NOT "the rows dated inside
# it"; it is a set of the rail's invoice rows dated up to MCP_TIEOUT_LAG_DAYS
# before it. The tie-out therefore matches per client: the summary's billed
# amount must be the sum of a subset of that client's invoice rows in the
# window, each row used once across the weeks (oldest week first).
MCP_TIEOUT_LAG_DAYS = 28


def _subset_sum(amounts: list, target: int):
    """Indices of a subset of `amounts` (ints, small n) summing to target, or
    None. Prefers fewer, older rows (the list arrives date-ordered)."""
    n = len(amounts)
    if target == 0:
        return []
    if n > 12:                                   # never in practice; keep it bounded
        amounts = amounts[:12]
        n = 12
    best = None
    for mask in range(1, 1 << n):
        if sum(amounts[i] for i in range(n) if mask >> i & 1) == target:
            chosen = [i for i in range(n) if mask >> i & 1]
            if best is None or len(chosen) < len(best):
                best = chosen
    return best


def match_week(conn, profile: str, start: str, end: str, by_client: list, matched: set) -> dict:
    """Upwork's per-client billed amounts for one summary week against the
    rail's invoice rows (upwork_mcp_rows, BILLED_SUBTYPES, dated within
    MCP_TIEOUT_LAG_DAYS before the week's end, not yet matched). Returns
    {ok: [client…], gaps: [{client, billed, candidates}], used: [record_id…]}
    and adds the used ids to `matched`."""
    lo = (date.fromisoformat(start) - timedelta(days=MCP_TIEOUT_LAG_DAYS)).isoformat()
    rows = [dict(r) for r in conn.execute(
        "SELECT record_id, amount_minor, company, substr(invoice_dt,1,10) AS d FROM upwork_mcp_rows"
        " WHERE profile=? AND accounting_subtype IN ({}) AND amount_minor IS NOT NULL"
        " AND substr(invoice_dt,1,10) BETWEEN ? AND ? ORDER BY invoice_dt, record_id"
        .format(",".join("?" * len(BILLED_SUBTYPES))),
        (profile, *sorted(BILLED_SUBTYPES), lo, end))]
    out = {"ok": [], "gaps": [], "used": []}
    for c in by_client or []:
        client = c.get("client") or "?"
        target = _minor(c.get("billed")) or 0
        cand = [r for r in rows if (r["company"] or "?") == client and r["record_id"] not in matched]
        pick = _subset_sum([r["amount_minor"] for r in cand], target)
        if pick is None:
            out["gaps"].append({"client": client, "billed": target,
                                "candidates": [(r["d"], r["amount_minor"]) for r in cand]})
            continue
        for i in pick:
            matched.add(cand[i]["record_id"])
            out["used"].append(cand[i]["record_id"])
        out["ok"].append(client)
    return out


# ── the pass (fetch everything, then write once) ─────────────────────────────

_UNSET = object()


def run(ctx, session_factory=None, *, lookback_days: Optional[int] = None,
        today: Optional[date] = None, connects_since=_UNSET) -> dict:
    """Transactions for every org over the look-back window → the ledger via
    the audited upsert + upwork_mcp_rows; the connects usage rows → shadow
    compare (or connects_history from config.MCP_CONNECTS_SINCE on).
    Metrics per org: fetched / inserted / updated / transfer_legs /
    quarantined / no_id / total / truncated / differs (rows whose stored
    amount is not the one today's read derives — named, never rewritten)."""
    from modules.upwork.feeds import mcp_client
    lookback = lookback_days if lookback_days is not None else config.MCP_FINANCIALS_LOOKBACK_DAYS
    since = config.MCP_CONNECTS_SINCE if connects_since is _UNSET else connects_since
    frm, to = _window(lookback, today)
    session = (session_factory or mcp_client.MCPSession)()
    try:
        if hasattr(session, "initialize"):
            session.initialize()
        who = orgs(session)
    except Exception as exc:                                     # noqa: BLE001
        kind = mcp_client.start_failure_kind(exc)
        raise RuntimeError(
            f"Upwork MCP session could not start ({kind}): {exc!r} —"
            f" {mcp_client.START_FAILURE_ADVICE[kind]}. The ledger read waits"
            " for the next run.") from exc
    fetched = {}
    for profile, org in who.items():
        if org:
            fetched[profile] = fetch_transactions(session, org, frm, to, ctx.log)
    if not who.get("agency"):
        ctx.anomaly("financials_no_agency", "warn",
                    {"note": "list_accounts returned no FL_AGENCY org — the agency"
                             " ledger was not read this run", "orgs": who})
    connects = fetch_connects(session, who["personal"], ctx.log)
    # the completed weeks whose summary is not stored yet: a short read, the
    # network calls, then the one write below (never a call inside a write)
    dbp = ctx.db_path or config.DB_PATH
    day = today or datetime.now(timezone.utc).date()
    with db.connect(dbp) as conn:
        due = summaries_due(conn, day, [PROFILE_LABEL[k] for k, v in who.items() if v])
    summaries = []
    for profile, a, b in due:
        org = who["personal" if profile == "Personal" else "agency"]
        res = fetch_summary(session, org, a, b, ctx.log)
        row = summary_row(res)
        if row is None:
            ctx.log(f"summary {profile} {a}..{b}: not a summary answer: {str(res)[:120]!r}")
            continue
        summaries.append((profile, a, b, row))

    metrics = {"window": [frm, to], "transactions": {}, "connects": {}, "summaries": {}}
    with db.connect(dbp) as conn:
        for profile, a, b, row in summaries:
            store_summary(conn, profile, a, b, row)
        metrics["summaries"] = {"due": len(due), "stored": len(summaries),
                                "weeks": [f"{p} {a}" for p, a, _b, _r in summaries]}
        # the earliest window floor the rail has ever read from: every invoice
        # dated on or after it has been in view (bin/doctor's twins check
        # draws its coverage line there — a CSV row older than that predates
        # the rail and is never a missing twin)
        conn.execute(
            "INSERT INTO sync_cursors (name, value, updated_at) VALUES (?,?,datetime('now'))"
            " ON CONFLICT(name) DO UPDATE SET value=MIN(value, excluded.value),"
            " updated_at=datetime('now')", (FLOOR_CURSOR, frm))
        for slot, got in fetched.items():
            profile = PROFILE_LABEL.get(slot, slot)
            m = {"fetched": len(got["rows"]), "inserted": 0, "updated": 0, "transfer_legs": 0,
                 "quarantined": 0, "no_id": 0, "truncated": got["truncated"],
                 "total": got["total"], "calls": got["calls"], "differs": []}
            for raw in got["rows"]:
                row = transaction_row(raw)
                if row is None:
                    m["no_id"] += 1
                    continue
                amt = signed_minor(row)
                if is_transfer_leg(row):
                    m["transfer_legs"] += 1
                r = sync_state.upsert_transaction(conn, row, profile, amount_minor=amt)
                outcome = r if r in ("inserted", "updated") else "quarantined"
                m[outcome] += 1
                # WHAT THE LEDGER HOLDS AGAINST WHAT UPWORK SAYS TODAY. The
                # upsert never rewrites an amount it already has (a corrected
                # value must survive a re-read), so a row stored under an
                # older sign rule stays as stored — and stayed invisible: the
                # two returned-fee rows of 6 Oct sat wrong with nothing to
                # say so. Named here, never changed here.
                if r == "updated" and amt is not None:
                    held = conn.execute(
                        "SELECT amount_minor, source FROM transactions"
                        " WHERE canonical_key=? OR record_id=?",
                        (f"upwork:{row['recordId']}", row["recordId"])).fetchone()
                    if held and held["amount_minor"] is not None and held["amount_minor"] != amt:
                        m["differs"].append({
                            "record_id": row["recordId"], "subtype": row.get("accountingSubtype"),
                            "ledger": held["amount_minor"], "upwork": amt, "source": held["source"]})
                if r == "inserted":
                    conn.execute(
                        "UPDATE transactions SET source=?, type=?, pnl_bucket=?, fx_rate='1',"
                        " assignment_company=COALESCE(assignment_company, ?)"
                        " WHERE record_id=?",
                        (SOURCE, ledger_type(row), bucket_for(row), row.get("company"),
                         row["recordId"]))
                # WHEN THE CLIENT WAS BILLED, on every row we see — the CSV
                # carries only the payout date, so without this the ledger can
                # answer "what came in" and not "what did we bill" (migration
                # 037; his 2026-08-27 "I wanna see both").
                if row.get("createdDateTime"):     # Upwork's INVOICE instant
                    conn.execute(
                        "UPDATE transactions SET billed_dt=? WHERE record_id=?"
                        " AND (billed_dt IS NULL OR billed_dt != ?)",
                        (row["createdDateTime"], row["recordId"], row["createdDateTime"]))
                record_seen(conn, row, profile, outcome, amt)
            if got["truncated"]:
                ctx.anomaly("financials_truncated", "warn",
                            {"profile": profile, "total": got["total"], "returned": m["fetched"],
                             "window": [frm, to],
                             "note": "more rows exist than one read returns — narrow"
                                     " MCP_FINANCIALS_LOOKBACK_DAYS or page"})
            metrics["transactions"][slot] = m

        events = connects_events(connects, conn)
        kinds, twins, inserted, would = {}, 0, 0, 0
        orphans = []
        for e in events:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
            if since and e["event_date"] >= since:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO connects_history (id, event_date, kind, label,"
                    " job_id, job_title, connects_delta) VALUES (?,?,?,?,?,?,?)",
                    (e["id"], e["event_date"], e["kind"], e["label"], e["job_id"],
                     e["job_title"], e["connects_delta"]))
                inserted += cur.rowcount
            else:
                would += 1
                if connects_twin(conn, e):
                    twins += 1
                else:
                    # NAMED, not just counted: the retirement decision turns on
                    # what the two rails disagree about, and "9 of 10" is not
                    # something anyone can act on.
                    orphans.append({"date": e["event_date"], "kind": e["kind"],
                                    "delta": e["connects_delta"],
                                    "job_id": e.get("job_id"),
                                    "label": (e.get("label") or "")[:60]})
        metrics["connects"] = {
            "rows": len(events), "kinds": kinds, "balance": _balance_of(connects),
            "mode": "live" if since else "shadow", "since": since,
            "inserted": inserted, "shadow_rows": would, "twins": twins,
            "untwinned": would - twins, "untwinned_events": orphans[:10],
            "oldest": min((e["event_date"] for e in events), default=None),
            "all_new": bool(events) and (inserted if since else (would - twins)) == len(events)}
        ctx.log(f"connects: {len(events)} usage rows {kinds}, {metrics['connects']['mode']},"
                f" twins {twins}/{would}, inserted {inserted}, balance {_balance_of(connects)}")
    # money in flight: the hours and milestones no invoice covers yet (his
    # 26 Aug 22:30 "money in flight"). Its own network reads, after the ledger
    # write closed — never a call inside a transaction.
    try:
        metrics["in_flight"] = mcp_inflight.run(ctx, session, who["personal"], today=today)
    except Exception as exc:                                     # noqa: BLE001
        ctx.log(f"in-flight pass FAILED (non-fatal): {exc!r}")
        ctx.anomaly("inflight_failed", "warn", {"error": repr(exc)[:200],
                    "note": "hours and milestones were not refreshed this run"})
        metrics["in_flight"] = {"status": "error", "error": repr(exc)[:200]}
    for profile, m in metrics["transactions"].items():
        ctx.log(f"{profile}: {m['fetched']} rows ({m['total']} exist) — inserted {m['inserted']},"
                f" updated {m['updated']}, transfer legs {m['transfer_legs']},"
                f" quarantined {m['quarantined']}, no id {m['no_id']}")
        if m["differs"]:
            ctx.log(f"{profile}: {len(m['differs'])} stored amounts are not what this read derives"
                    " (ledger vs Upwork, minor units; nothing rewritten): "
                    + ", ".join(f"{x['record_id']} {x['ledger']} vs {x['upwork']}"
                                for x in m["differs"][:10]))
    return metrics


def task(ctx) -> dict:
    return run(ctx)


def main(argv=None) -> int:
    """`python3 -m modules.upwork.feeds.mcp_financials [--lookback DAYS]` —
    the daily read, or a ONE-OFF wider read (--lookback 90: the withdrawal
    legs the CSV importer held since 27 May, with their amounts, resolving
    the missing-amount quarantine entries the GraphQL pass left). A wider
    read is the same two calls with a longer window; the split-once paging
    still applies, `total_count` above two reads is a warn anomaly."""
    import argparse
    ap = argparse.ArgumentParser(description="Upwork ledger through the MCP")
    ap.add_argument("--lookback", type=int, default=None,
                    help=f"days to read (default config: {config.MCP_FINANCIALS_LOOKBACK_DAYS})")
    args = ap.parse_args(argv)

    def _task(ctx):
        return run(ctx, lookback_days=args.lookback)
    result = runner.run_task(TASK_NAME, _task, module="upwork")
    print(json.dumps({"run_id": result.run_id, "status": result.status,
                      "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
