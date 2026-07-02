"""CRM Airtable mirror — v3 SQLite -> Airtable, replacing v2 sync_airtable.

ONE-WAY COPY-OUT ONLY. This module NEVER reads Airtable back into the v3 ledger
and NEVER triggers a money action. It only creates/updates Airtable rows from v3
DB rows. That boundary is what lets the Transactions CRM copy go live under its
OWN gate (SHADOW_MODE['crm_transactions']) while the finance ledger / doctor /
reports stay authoritative AND shadowed (SHADOW_MODE['finance'] unchanged): the
copy-out can't feed a wrong number back into the ledger and can't move money.

Gates (read here, NOT runner modules) — DECOUPLED from the scanner's 'upwork'
flag so the mirror flips WITHOUT un-shadowing the scan's ClickUp path:
  - Proposals + Contracts -> SHADOW_MODE['crm']
  - Transactions          -> SHADOW_MODE['crm_transactions']  (also decoupled from finance)

Field ownership (architecture §4): ONLY v3-owned (system) fields are ever in a
payload. Human-maintained Airtable fields (Outcome Reason/Notes, Lead Score,
Notes, Boosted, ...) are never written — they aren't in any ownership map, so the
mirror structurally cannot touch them (see HANDS_OFF + tests).

Dedup: engine.plan_push matches a v3 row to its Airtable row by stored
airtable_record_id, else by the canonical key field (Upwork Proposal / Contract /
Transaction ID) — the SAME ids v2 wrote — so v3 UPDATES v2's rows, never
duplicates. Only changed fields are sent; None values are skipped (never null out).

Money boundary (Transactions): outbound "Amount USD" is derived from the
authoritative integer amount_minor via money.from_minor, floated ONLY at the
Airtable JSON boundary for the display copy. v3 never trusts the Airtable number
back. Non-Upwork ledger rows (bank / wise / card) are NOT mirrored — this is the
Upwork CRM view (canonical_key LIKE 'upwork:%').
"""
from __future__ import annotations

import re

from core import config, db
from core.sync import engine
from core.sync.engine import FieldSpec
from modules.finance import money

BASE = config.AIRTABLE_BASE

# Upwork job id embedded in a Job URL as "~02<jobid>" — v3 proposals.marketplace_job_id
# is that <jobid> (the "02"-stripped tail). Mirrors v2's FIND("~02"+job_id, {Job URL}).
_JOBURL_JOBID = re.compile(r"~02(\d+)")


def _iso_date(v):
    return (v or "")[:10] or None


def _proposal_joburl_fallback(at_recs):
    """Adopt v2's legacy scored-job Proposals row (keyed by Job URL, no Upwork
    Proposal ID) when a v3 proposal shares its job id — so we UPDATE+stamp the id
    onto it instead of creating a duplicate. O(1) via a prebuilt job-id index."""
    idx = {}
    for rec in at_recs:
        m = _JOBURL_JOBID.search(rec.get("fields", {}).get("Job URL") or "")
        if m:
            idx.setdefault(m.group(1), rec)      # first row wins
    def match(row, _at_by_id):
        jid = row.get("marketplace_job_id")
        return idx.get(str(jid)) if jid else None
    return match


def _proposal_rows(conn):
    rows = [dict(r) for r in conn.execute("SELECT * FROM proposals")]
    for r in rows:
        r["_date_submitted"] = _iso_date(r.get("created_dt"))
    return rows


def _contract_rows(conn):
    rows = [dict(r) for r in conn.execute("SELECT * FROM contracts")]
    for r in rows:
        r["_start"] = _iso_date(r.get("start_dt"))
        r["_end"] = _iso_date(r.get("end_dt"))
    return rows


def _transaction_rows(conn):
    # Upwork CRM view only — bank/wise/card ledger rows are NOT mirrored.
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM transactions WHERE canonical_key LIKE 'upwork:%'")]
    for r in rows:
        m = r.get("amount_minor")
        # authoritative integer minor -> dollars; float ONLY at the Airtable
        # boundary (display copy). Never read back into the ledger.
        r["_amount_usd"] = float(money.from_minor(m)) if m is not None else None
        r["_date"] = _iso_date(r.get("fully_paid_dt") or r.get("creation_dt"))
        # Match on the NUMERIC Upwork id (from canonical_key), NOT record_id: 17
        # legacy rows carry record_id 'personal:/agency:<id>' while canonical is
        # 'upwork:<id>'. Airtable's "Upwork Transaction Record ID" holds the
        # numeric id, so keying on record_id would mis-match -> duplicate.
        r["_upwork_id"] = (r.get("canonical_key") or "").split(":", 1)[-1]
    return rows


SPECS = {
    "proposals": {
        "at_table": config.AT["proposals"], "at_key_field": "Upwork Proposal ID",
        "key_col": "id", "gate": "crm", "entity": "proposal",
        "rows": _proposal_rows, "build_fallback": _proposal_joburl_fallback,
        "ownership": {
            "status":             FieldSpec("Status", "system"),
            "job_title":          FieldSpec("Job Title", "system"),
            "client_company":     FieldSpec("Client Name", "system"),
            "client_country":     FieldSpec("Client Country", "system"),
            "client_total_spent": FieldSpec("Client Total Spent", "system"),
            "_date_submitted":    FieldSpec("Date Submitted", "system"),
        },
    },
    "contracts": {
        "at_table": config.AT["contracts"], "at_key_field": "Upwork Contract ID",
        "key_col": "id", "gate": "crm", "entity": "contract",
        "rows": _contract_rows,
        "ownership": {
            "title":           FieldSpec("Contract Title", "system"),
            "airtable_status": FieldSpec("Status", "system"),
            "profile":         FieldSpec("Profile", "system"),
            "_start":          FieldSpec("Start Date", "system"),
            "_end":            FieldSpec("End Date", "system"),
        },
    },
    "transactions": {
        "at_table": config.AT["transactions"],
        "at_key_field": "Upwork Transaction Record ID",
        "key_col": "_upwork_id", "gate": "crm_transactions", "entity": "transaction",
        "rows": _transaction_rows,
        "ownership": {
            "type":        FieldSpec("Type", "system"),
            "_amount_usd": FieldSpec("Amount USD", "system"),
            "status":      FieldSpec("Status", "system"),
            "profile":     FieldSpec("Profile", "system"),
            "source":      FieldSpec("Source", "system"),
            "description": FieldSpec("Description", "system"),
            "dedup_key":   FieldSpec("Dedup Key", "system"),
            "_date":       FieldSpec("Date", "system"),
        },
    },
}

# Human-maintained fields the mirror must NEVER write (test-asserted; none of
# these appear in any ownership map above, by construction).
HANDS_OFF = {
    "proposals": ("Outcome Reason", "Outcome Notes", "Lead Score", "Notes",
                  "Boosted", "Connects Spent", "Loom Link"),
    "contracts": ("Notes", "Client", "Workroom URL", "Total Earned",
                  "Hours This Week"),
    "transactions": ("Client", "Contract Title", "Wise Transaction ID"),
}


def mirror_table(ctx, client, name: str, spec: dict) -> dict:
    """Plan + (shadow|live) push one table. Airtable IO + shadow recording happen
    OUTSIDE the tight DB read (kernel convention)."""
    shadow = config.SHADOW_MODE.get(spec["gate"], True)     # default shadow ON
    at_recs = client.list_records(BASE, spec["at_table"])   # HTTP read
    at_by_id = {r["id"]: r for r in at_recs}
    with db.connect(ctx.db_path) as conn:
        local = spec["rows"](conn)
    build_fb = spec.get("build_fallback")
    fallback = build_fb(at_recs) if build_fb else None
    ops = engine.plan_push(local, at_by_id, spec["ownership"],
                           spec["at_key_field"], spec["key_col"],
                           fallback_match=fallback)
    res = engine.execute_push(
        ops, shadow=shadow, record_shadow=ctx.record_shadow_write,
        airtable=client.engine_writer(BASE, spec["at_table"]),
        entity=spec["entity"])
    # orphans: Airtable rows with no v3 match — REPORTED, never deleted.
    local_keys = {str(r[spec["key_col"]]) for r in local}
    at_keys = {str(rec.get("fields", {}).get(spec["at_key_field"]))
               for rec in at_recs
               if rec.get("fields", {}).get(spec["at_key_field"]) is not None}
    orphans = sorted(at_keys - local_keys)
    res.update({"table": name, "shadow": shadow, "gate": spec["gate"],
                "local_rows": len(local), "at_rows": len(at_recs),
                "orphans": len(orphans), "orphan_sample": orphans[:10]})
    return res


def run(ctx) -> dict:
    from core.airtable_client import AirtableClient
    client = AirtableClient()
    return {name: mirror_table(ctx, client, name, spec)
            for name, spec in SPECS.items()}
