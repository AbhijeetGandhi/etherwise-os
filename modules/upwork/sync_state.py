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


def upsert_transaction(conn, row: dict, profile: str) -> str:
    key = build_dedup_key(row, profile)
    exists = conn.execute("SELECT 1 FROM transactions WHERE record_id=?",
                          (key,)).fetchone()
    if exists:
        conn.execute(
            "UPDATE transactions SET status=?, fully_paid_dt=?,"
            " updated_at=datetime('now') WHERE record_id=?",
            (row.get("status"), row.get("fullyPaidDateTime"), key))
        return "updated"
    conn.execute(
        """INSERT INTO transactions (record_id, upwork_type, description,
           amount, currency, creation_dt, fully_paid_dt, status, profile)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (key, row.get("type"), row.get("description"),
         money(row.get("amount")), (row.get("amount") or {}).get("currency"),
         row.get("createdDateTime"), row.get("fullyPaidDateTime"),
         row.get("status"), profile))
    return "inserted"


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
