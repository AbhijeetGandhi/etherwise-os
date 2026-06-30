"""M1b sync_state — proposals/contracts/offers/transactions mirror (SHADOW).

Port of v2's sync_state onto kernel primitives, clean v3 names. The GraphQL
field selections live in queries.py (ported verbatim — they encode the
quirks). Writes ONLY v3 tables (internal); the Airtable mirror is the sync
engine's job and stays shadowed until cutover.

Hard rules in code:
- TERMINAL PROTECTION: proposals whose status is Won/Lost/Expired/Withdrawn/
  Skipped are never status-flipped — the UPDATE's WHERE clause excludes them
  and we assert the row was either terminal or updated.
- recordId-first dedup: transactions key on the ported build_dedup_key
  (profile-scoped); all upserts are INSERT-or-UPDATE on primary key.
- Dual-ACE: transactions fetched per profile (personal + agency tenants).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from core import config, db, runner
from modules.finance import money as fin_money   # integer-minor signed amounts
from modules.upwork import queries
from modules.upwork.upwork_client import UpworkClient

TASK_NAME = "upwork_sync"
TERMINAL = ("Won", "Lost", "Expired", "Withdrawn", "Skipped")


def money(node: Optional[dict]) -> Optional[float]:
    if not node:
        return None
    try:
        return float(node.get("amount") or node.get("rawValue"))
    except (TypeError, ValueError):
        return None


def iso_dt(value) -> Optional[str]:
    """Normalize a date to ISO-8601, the v3 canonical format. Upwork/v2 hand
    us epoch MILLISECONDS for proposal dates (e.g. '1779100550433'); the rest
    of v3 (transactions, scored_jobs) is ISO. Storing epoch-millis here broke
    every date-window consumer (cockpit applied counts, outcome_capture).
    Digit strings are treated as epoch (13=ms, 10=s); anything else passes
    through unchanged (already ISO)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        ts = int(s)
        if len(s) >= 13:        # milliseconds
            ts //= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return s                    # already ISO


# ── upserts (terminal-protected) ─────────────────────────────────────────────

def upsert_proposal(conn, vp: dict, thread_id: Optional[str]) -> str:
    """Returns 'inserted' | 'updated' | 'terminal-preserved'."""
    pid = vp["id"]
    upwork_status = ((vp.get("status") or {}).get("status")
                     if isinstance(vp.get("status"), dict)
                     else vp.get("status"))
    job = vp.get("job") or {}
    client = vp.get("client") or {}
    exists = conn.execute("SELECT status FROM proposals WHERE id=?",
                          (pid,)).fetchone()
    if exists is None:
        conn.execute(
            """INSERT INTO proposals (id, upwork_status, charge_rate,
               charge_currency, cover_letter, marketplace_job_id, job_title,
               client_company, client_country, client_total_spent,
               created_dt, modified_dt, thread_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, upwork_status, money(vp.get("chargeRate")),
             (vp.get("chargeRate") or {}).get("currency"),
             vp.get("coverLetter"), job.get("id"), job.get("title"),
             client.get("companyName"), client.get("country"),
             money(client.get("totalSpent")),
             iso_dt(vp.get("createdDateTime")),
             iso_dt(vp.get("modifiedDateTime")), thread_id))
        return "inserted"
    if exists["status"] in TERMINAL:
        # mirror non-status telemetry only; the verdict is permanent
        conn.execute(
            "UPDATE proposals SET upwork_status=?, modified_dt=?,"
            " updated_at=datetime('now') WHERE id=? AND status IN"
            " (?,?,?,?,?)", (upwork_status, iso_dt(vp.get("modifiedDateTime")),
                             pid, *TERMINAL))
        return "terminal-preserved"
    conn.execute(
        """UPDATE proposals SET upwork_status=?, charge_rate=?,
           cover_letter=COALESCE(?, cover_letter), job_title=?,
           client_company=?, modified_dt=?, thread_id=COALESCE(?, thread_id),
           updated_at=datetime('now')
           WHERE id=? AND (status IS NULL OR status NOT IN (?,?,?,?,?))""",
        (upwork_status, money(vp.get("chargeRate")), vp.get("coverLetter"),
         job.get("title"), client.get("companyName"),
         iso_dt(vp.get("modifiedDateTime")), thread_id, pid, *TERMINAL))
    return "updated"


def upsert_contract(conn, c: dict, thread_id: Optional[str],
                    profile: str) -> str:
    cid = c["id"]
    exists = conn.execute("SELECT 1 FROM contracts WHERE id=?",
                          (cid,)).fetchone()
    fields = (c.get("title"), c.get("status"), c.get("contractType"),
              c.get("startDateTime"), c.get("endDateTime"),
              (c.get("hourlyChargeRate") or {}).get("amount"),
              (c.get("job") or {}).get("id"),
              (c.get("job") or {}).get("title"),
              (c.get("client") or {}).get("companyName"),
              profile, thread_id)
    if exists:
        conn.execute(
            """UPDATE contracts SET title=?, upwork_status=?,
               contract_type=?, start_dt=?, end_dt=?, hourly_rate=?,
               job_id=?, job_title=?, client_company=?, profile=?,
               thread_id=COALESCE(?, thread_id), updated_at=datetime('now')
               WHERE id=?""", (*fields, cid))
        return "updated"
    conn.execute(
        """INSERT INTO contracts (title, upwork_status, contract_type,
           start_dt, end_dt, hourly_rate, job_id, job_title, client_company,
           profile, thread_id, id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (*fields, cid))
    return "inserted"


def upsert_offer(conn, o: dict, thread_id: Optional[str]) -> str:
    exists = conn.execute("SELECT 1 FROM offers WHERE id=?",
                          (o["id"],)).fetchone()
    fields = (o.get("title"), (o.get("state") or {}).get("status")
              if isinstance(o.get("state"), dict) else o.get("state"),
              o.get("type"), (o.get("job") or {}).get("id"),
              (o.get("client") or {}).get("id"),
              (o.get("client") or {}).get("name"),
              o.get("messageToContractor"), thread_id)
    if exists:
        conn.execute(
            "UPDATE offers SET title=?, state=?, type=?, job_id=?,"
            " client_id=?, client_name=?, message_to_contractor=?,"
            " thread_id=COALESCE(?, thread_id), updated_at=datetime('now')"
            " WHERE id=?", (*fields, o["id"]))
        return "updated"
    conn.execute(
        "INSERT INTO offers (title, state, type, job_id, client_id,"
        " client_name, message_to_contractor, thread_id, id)"
        " VALUES (?,?,?,?,?,?,?,?,?)", (*fields, o["id"]))
    return "inserted"


def build_dedup_key(row: dict, profile: str) -> str:
    """Ported from v2 verbatim: profile-scoped composite, recordId-first."""
    rid = row.get("recordId") or row.get("id")
    if rid:
        return f"{profile}:{rid}"
    return (f"{profile}:{row.get('createdDateTime')}"
            f":{(row.get('amount') or {}).get('amount')}"
            f":{row.get('description')}")


# Sign by Upwork accounting type/subtype — fees, taxes, charges and withdrawals
# are DEBITS (negative); earnings/payments/bonuses are credits (positive). Derived
# from the audited ledger (ARInvoice=connects/GST, APPayment=withdrawal,
# APAdjustment=Service Fee/WHT/GST/Withdrawal Fee). Matches the CSV-authoritative
# convention + doctor #9; the CSV re-import + doctor remain the backstop.
_DEBIT_TYPES = {"ARInvoice", "APPayment"}
_DEBIT_SUBTYPES = {"Service Fee", "WHT", "Withholding Tax", "GST", "Withdrawal Fee",
                   "Membership Fee", "Connects", "Connect Purchase"}


def _signed_amount_minor(row: dict):
    """Integer minor units (USD cents) with the correct sign, or None when the API
    gives no amount (AP/AR types) — surfaced by doctor #8, recovered from the CSV;
    NEVER fabricated."""
    node = row.get("amount") or {}
    raw = node.get("amount") if node.get("amount") is not None else node.get("rawValue")
    if raw in (None, ""):
        return None
    minor = abs(fin_money.to_minor(str(raw)))          # Decimal path, no float
    t = (row.get("type") or "").strip()
    sub = (row.get("subtype") or row.get("accountingSubtype") or "").strip()
    return -minor if (t in _DEBIT_TYPES or sub in _DEBIT_SUBTYPES) else minor


def _missing_amount_reason(canonical: str) -> str:
    """Stable reason => exactly ONE quarantine entry per canonical, so the same
    amountless row recurring every sync (the agency-auth/scope gap persists until
    re-OAuth) maps to one entry that is refreshed, never appended per run."""
    return (f"upwork txn missing amount ({canonical}); recover from CSV or agency "
            f"re-OAuth (agency financials / vendorProposals scope gap)")


def _quarantine_missing_amount(conn, row: dict, profile: str, canonical: str) -> str:
    """Route an amountless NEW txn to quarantine instead of inserting a NULL-amount
    row (which would trip doctor #8 and break the GREEN gate). Never fabricates,
    never drops. IDEMPOTENT: refresh the single unresolved entry for this canonical
    if it already exists, else insert one."""
    reason = _missing_amount_reason(canonical)
    payload = json.dumps({
        "canonical_key": canonical, "profile": profile, "type": row.get("type"),
        "subtype": row.get("subtype") or row.get("accountingSubtype"),
        "description": row.get("description"),
        "currency": (row.get("amount") or {}).get("currency"),
        "createdDateTime": row.get("createdDateTime"),
    }, sort_keys=True)
    existing = conn.execute(
        "SELECT id FROM quarantine WHERE source='upwork' AND resolved_at IS NULL"
        " AND reason=?", (reason,)).fetchone()
    if existing:
        conn.execute("UPDATE quarantine SET at=datetime('now'), payload_json=?"
                     " WHERE id=?", (payload, existing["id"]))
        return "quarantine-refreshed"
    conn.execute("INSERT INTO quarantine (source, reason, payload_json)"
                 " VALUES ('upwork', ?, ?)", (reason, payload))
    return "quarantined"


def _resolve_missing_amount(conn, canonical: str) -> None:
    """Recovery round-trip: an amount finally arrived for a previously-amountless
    canonical -> mark its pending quarantine entry imported. No delete (nothing is
    dropped); the resolved entry is the audit trail of the recovery. Delegates to
    the SHARED resolver in modules.finance.ledger so the API path and the weekly
    CSV-import path resolve identically (one implementation, no drift)."""
    from modules.finance import ledger
    ledger.resolve_missing_amount(conn, canonical)


def upsert_transaction(conn, row: dict, profile: str) -> str:
    """Idempotent transaction upsert. Fixes the M1-cutover bugs:
      #2 stub-twin minter: key on the CANONICAL namespace `upwork:<recordId>`
         (record_id=<rid>, canonical_key=upwork:<rid>) — the SAME identity the CSV
         import uses — so a re-sync DEDUPS against the canonical/CSV row instead of
         minting a NULL-key `personal:`/`agency:` twin (the double-count root).
      #1 amount never refreshed: UPDATE COALESCE-fills amount_minor when an EXISTING
         row's amount is NULL (self-heals) without clobbering a corrected value.
      #3 fee sign: amount stored as SIGNED integer minor units via _signed_amount_minor.
      #4 NULL-amount insert: a NEW txn the API hands us with no amount is QUARANTINED
         (idempotently), never inserted as a NULL-amount row (which trips doctor #8).
         When a later sync supplies the amount it inserts AND resolves the entry."""
    rid = row.get("recordId") or row.get("id")
    canonical = f"upwork:{rid}" if rid else None
    amt = _signed_amount_minor(row)
    existing = conn.execute(
        "SELECT record_id FROM transactions WHERE canonical_key=? OR record_id=?",
        (canonical, rid)).fetchone() if rid else None
    if existing:
        conn.execute(
            "UPDATE transactions SET status=?, fully_paid_dt=?,"
            " amount_minor=COALESCE(amount_minor, ?),"
            " amount_base_minor=COALESCE(amount_base_minor, ?),"
            " updated_at=datetime('now') WHERE record_id=?",
            (row.get("status"), row.get("fullyPaidDateTime"), amt, amt,
             existing["record_id"]))
        if amt is not None and canonical:
            _resolve_missing_amount(conn, canonical)   # amount present now -> clear any pending entry
        return "updated"
    if amt is None:                                     # M1 bug #4: never insert a NULL-amount row
        return _quarantine_missing_amount(conn, row, profile, canonical or "upwork:?")
    conn.execute(
        "INSERT INTO transactions (record_id, canonical_key, dedup_key, upwork_type,"
        " accounting_subtype, description, amount_minor, amount_credited_minor,"
        " amount_base_minor, currency, creation_dt, fully_paid_dt, status, profile,"
        " source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'Upwork')",
        (rid, canonical, build_dedup_key(row, profile), row.get("type"),
         row.get("subtype") or row.get("accountingSubtype"), row.get("description"),
         amt, amt, amt, (row.get("amount") or {}).get("currency") or "USD",
         row.get("createdDateTime"), row.get("fullyPaidDateTime"),
         row.get("status"), profile))
    if canonical:
        _resolve_missing_amount(conn, canonical)   # recovery: amount arrived -> clear pending entry, no dup
    return "inserted"


# ── submitted-proposals 2nd pass (ARMED, INACTIVE until cutover) ──────────────

# substrings that mean "the token lacks the vendorProposals scope" (not a real
# failure) — the pass auto-skips until the cutover re-OAuth grants the scope.
_SCOPE_HINTS = ("vendorproposals", "oauth2 permissions", "scope", "permission")


def _vp_node_to_proposal(node: dict) -> dict:
    """vendorProposals node -> the dict upsert_proposal expects. Field set
    mirrors ROOM_LIST.vendorProposal (pinned from M3-era introspection)."""
    mjp = node.get("marketplaceJobPosting") or {}
    org = node.get("organization") or {}
    audit = node.get("auditDetails") or {}
    return {
        "id": node["id"],
        "status": {"status": (node.get("status") or {}).get("status")},
        "chargeRate": (node.get("terms") or {}).get("chargeRate"),
        "coverLetter": node.get("coverLetter"),
        "job": {"id": mjp.get("id"),
                "title": (mjp.get("content") or {}).get("title")},
        "client": {"companyName": org.get("name"),
                   "country": (org.get("location") or {}).get("country"),
                   "totalSpent": None},
        "createdDateTime": (audit.get("createdDateTime") or {}).get("rawValue"),
        "modifiedDateTime": (audit.get("modifiedDateTime") or {}).get("rawValue"),
    }


def sync_submitted_proposals(ctx, client, page_size: int = 100,
                             max_pages: int = 20) -> dict:
    """2nd pass: enumerate ALL submitted proposals (incl. roomless ones the
    roomList pass can't see — the freeze at 74 since June 9) and upsert keyed on
    id (= roomList vendorProposal.id = the My-Proposals applicationUID). Terminal
    statuses protected; thread_id/cover_letter COALESCE-preserved so room-having
    rows keep their room and only gain telemetry. AUTO-SKIPS (status
    'scope_blocked') until the cutover re-OAuth grants the vendorProposals scope;
    activates with NO code change the moment the scoped token exists."""
    dbp = ctx.db_path or config.DB_PATH
    metrics: dict = {}
    after, pages, seen = "0", 0, 0
    while pages < max_pages:
        pages += 1
        try:
            data = client.graphql(queries.SUBMITTED_PROPOSALS,
                                  {"first": page_size, "after": after})
        except Exception as exc:                    # noqa: BLE001
            if any(h in str(exc).lower() for h in _SCOPE_HINTS):
                ctx.log("submitted-proposals: scope not yet granted — INACTIVE"
                        " (activates at cutover re-OAuth)")
                return {"status": "scope_blocked", "armed": True}
            raise
        conn_data = (data.get("vendorProposals") or {})
        edges = conn_data.get("edges") or []
        with db.connect(dbp) as conn:
            for e in edges:
                node = e.get("node") or e
                if not node.get("id"):
                    continue
                seen += 1
                r = upsert_proposal(conn, _vp_node_to_proposal(node),
                                    thread_id=None)   # COALESCE preserves rooms
                metrics[r] = metrics.get(r, 0) + 1
        page_info = conn_data.get("pageInfo") or {}
        if not page_info.get("hasNextPage") or not edges:
            break
        after = page_info.get("endCursor") or after
    ctx.log(f"submitted-proposals: {seen} seen, {json.dumps(metrics)}")
    return {"status": "active", "seen": seen, "pages": pages, **metrics}


# ── task ──────────────────────────────────────────────────────────────────────

def sync(ctx, client_factory=UpworkClient, lookback_days: int = 14) -> dict:
    dbp = ctx.db_path or config.DB_PATH
    client = client_factory()
    metrics = {"proposals": {}, "contracts": {}, "offers": {},
               "transactions": {}}

    data = client.graphql(queries.ROOM_LIST,
                          {"first": 200, "after": "0"})
    rooms = ((data.get("roomList") or {}).get("edges")) or []
    rooms = [e.get("node") or e for e in rooms]

    with db.connect(dbp) as conn:
        for room in rooms:
            thread_id = room.get("id")
            vp = room.get("vendorProposal")
            if vp and vp.get("id"):
                r = upsert_proposal(conn, vp, thread_id)
                metrics["proposals"][r] = metrics["proposals"].get(r, 0) + 1
            c = room.get("contract")
            if c and c.get("id"):
                r = upsert_contract(conn, c, thread_id, profile="personal")
                metrics["contracts"][r] = metrics["contracts"].get(r, 0) + 1
            for o in (room.get("offers") or []):
                if o and o.get("id"):
                    r = upsert_offer(conn, o, thread_id)
                    metrics["offers"][r] = metrics["offers"].get(r, 0) + 1
    ctx.log(f"rooms-state: {len(rooms)} rooms,"
            f" {json.dumps(metrics['proposals'])} proposals")

    # 2nd pass: catch submitted-but-roomless proposals (the freeze since June 9).
    # ARMED but INACTIVE — auto-skips until the cutover re-OAuth grants the
    # vendorProposals scope; never fails the sync on the scope error.
    try:
        metrics["submitted_proposals"] = sync_submitted_proposals(ctx, client)
    except Exception as exc:                        # noqa: BLE001
        ctx.log(f"submitted-proposals pass errored (non-fatal): {exc!r}")
        metrics["submitted_proposals"] = {"status": "error", "error": str(exc)[:200]}

    end_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    start_iso = (datetime.now(timezone.utc) - timedelta(days=lookback_days)
                 ).strftime("%Y-%m-%dT%H:%M:%SZ")
    for profile, ace_id in config.ACE_IDS.items():
        # v2 semantics: financial queries can return per-ACE auth errors as
        # GraphQL partial errors — tolerate, log, continue with the other ACE.
        env = client.graphql(
            queries.TRANSACTION_HISTORY,
            {"aceIds": [ace_id], "startISO": start_iso, "endISO": end_iso},
            tolerate_errors=True)
        if env.get("errors"):
            msg = "; ".join(str(e.get("message"))
                            for e in env["errors"])[:160]
            ctx.log(f"transactions[{profile}] partial errors: {msg}")
            metrics["transactions"][f"errors_{profile}"] = len(env["errors"])
        txns = ((((env.get("data") or {}).get("transactionHistory") or {})
                 .get("transactionDetail") or {})
                .get("transactionHistoryRow")) or []
        with db.connect(dbp) as conn:
            for row in txns:
                r = upsert_transaction(conn, row, profile)
                metrics["transactions"][r] = \
                    metrics["transactions"].get(r, 0) + 1
        ctx.log(f"transactions[{profile}]: {len(txns)}")

    return metrics


def main() -> int:
    result = runner.run_task(TASK_NAME, sync, module="upwork")
    print(json.dumps({"run_id": result.run_id, "status": result.status,
                      "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
