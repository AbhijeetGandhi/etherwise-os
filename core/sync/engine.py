"""Generic Airtable sync engine core (architecture §4, Day 3 queue).

Pure planning + injected execution so every rule is unit-testable:
- field-ownership maps: system-wins fields push DB->Airtable; human-wins
  fields pull Airtable->DB unconditionally (AT always wins on them — v2
  manual rule) with an audit_log row per change
- recordId-first dedup: match by stored airtable_record_id, fall back to the
  canonical key field; only changed fields are sent
- typecast on every payload; <=10 records per request
- status whitelist for the jobs flow: Skipped/Scored/Drafted/ClickUp Created
  — NEVER New (unscored) or Phantom (the 2026-06-10/12 incident classes);
  fabricated-pattern ids are excluded outright regardless of status
- shadow mode: execute_push(shadow=True) records intents via the provided
  recorder (runner ctx.record_shadow_write) and never touches Airtable

The M1 session wires this to live tables + schedules; the kernel ships the
engine and its contract.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from core import config, db

sys.path.insert(0, str(config.V3_ROOT / "scripts"))
import job_id_guard  # noqa: E402  (same guard as bridge/push/restoration)

# Hard whitelist — what may mirror to Airtable from the jobs flow.
SYNC_STATUS_WHITELIST = ("Skipped", "Scored", "Drafted", "ClickUp Created")


@dataclass
class FieldSpec:
    at_field: str                # Airtable field name
    owner: str                   # "system" (push) | "human" (pull, AT wins)


@dataclass
class Operation:
    kind: str                    # "create" | "update"
    record_id: Optional[str]
    key: str                     # canonical key (job id etc.)
    fields: dict


def eligible_jobs(db_path=None) -> list:
    """scored_jobs rows allowed to mirror: whitelisted status, sane id."""
    with db.connect(db_path) as conn:
        marks = ",".join("?" * len(SYNC_STATUS_WHITELIST))
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM scored_jobs WHERE status IN ({marks})",
            SYNC_STATUS_WHITELIST)]
    return [r for r in rows if not job_id_guard.looks_fabricated(r["id"])]


def _system_fields(row: dict, ownership: dict) -> dict:
    out = {}
    for col, spec in ownership.items():
        if spec.owner == "system" and row.get(col) is not None:
            out[spec.at_field] = row[col]
    return out


def plan_push(local_rows, at_records_by_id: dict, ownership: dict,
              at_key_field: str, key_col: str = "id") -> list:
    """Diff local system-owned fields against Airtable; emit create/update
    ops. Match precedence: stored airtable_record_id, then the key field."""
    by_key = {}
    for rec in at_records_by_id.values():
        key = rec.get("fields", {}).get(at_key_field)
        if key is not None:
            by_key[str(key)] = rec

    ops = []
    for row in local_rows:
        desired = _system_fields(row, ownership)
        rec = None
        if row.get("airtable_record_id"):
            rec = at_records_by_id.get(row["airtable_record_id"])
        if rec is None:
            rec = by_key.get(str(row[key_col]))

        if rec is None:
            fields = dict(desired)
            fields[at_key_field] = row[key_col]
            ops.append(Operation("create", None, str(row[key_col]), fields))
            continue

        existing = rec.get("fields", {})
        diff = {f: v for f, v in desired.items() if existing.get(f) != v}
        if diff:
            ops.append(Operation("update", rec["id"], str(row[key_col]),
                                 diff))
    return ops


def plan_pull(local_rows, at_records_by_id: dict, ownership: dict,
              key_col: str = "id"):
    """Human-owned fields: Airtable always wins. Returns (updates, audits) —
    updates are {key_col, <col>: value} dicts; audits are audit_log row kits."""
    updates, audits = [], []
    human = {col: spec for col, spec in ownership.items()
             if spec.owner == "human"}
    for row in local_rows:
        rec = at_records_by_id.get(row.get("airtable_record_id") or "")
        if rec is None:
            continue
        at_fields = rec.get("fields", {})
        change = {}
        for col, spec in human.items():
            at_val = at_fields.get(spec.at_field)
            if at_val is not None and at_val != row.get(col):
                change[col] = at_val
                audits.append({
                    "entity": "scored_jobs", "entity_id": str(row[key_col]),
                    "field": col, "old_value": row.get(col),
                    "new_value": at_val, "source": "airtable",
                    "actor": "sync_engine",
                })
        if change:
            change[key_col] = row[key_col]
            updates.append(change)
    return updates, audits


def batch(ops, size: int = 10):
    return [ops[i:i + size] for i in range(0, len(ops), size)]


def create_payload(creates) -> dict:
    return {"records": [{"fields": op.fields} for op in creates],
            "typecast": True}


def update_payload(updates) -> dict:
    return {"records": [{"id": op.record_id, "fields": op.fields}
                        for op in updates],
            "typecast": True}


def execute_push(ops, *, shadow: bool,
                 record_shadow: Optional[Callable] = None,
                 airtable: Optional[Callable] = None,
                 entity: str) -> dict:
    """shadow=True: every intended write goes to the shadow recorder
    (§9 zero external writes). shadow=False: batched Airtable calls,
    typecast always."""
    creates = [op for op in ops if op.kind == "create"]
    updates = [op for op in ops if op.kind == "update"]

    if shadow:
        for op in ops:
            record_shadow(target="airtable", operation=op.kind,
                          entity=entity, entity_key=op.key,
                          payload=op.fields)
        return {"creates": len(creates), "updates": len(updates),
                "shadow": True}

    done_creates = done_updates = 0
    for chunk in batch(creates):
        out = airtable("POST", "", create_payload(chunk))
        done_creates += len(out.get("records", []))
    for chunk in batch(updates):
        out = airtable("PATCH", "", update_payload(chunk))
        done_updates += len(out.get("records", []))
    return {"creates": done_creates, "updates": done_updates,
            "shadow": False}
