"""Payroll roster source (Phase 6) — AUTOMATES the payroll data flow.

The renderer needs date_of_birth (-> PDF password), PAN and Department on every
slip. The canonical source is Airtable (Payroll base, Employees table); the local
Payroll/employees.json is the cache/fallback for gross/TDS/PT + offline runs.

load() returns {company, salary_split, employees[]} with the Airtable-sourced
fields (DOB / PAN / Department / Bank / IFSC) merged OVER the local rows by
Employee ID, so passwords/PANs/Department flow automatically. GRACEFUL FALLBACK:
if Airtable is unreachable (no token, network), it returns the local roster
untouched and records why in `_roster_source` — never fabricates a value. The
caller still surfaces any employee left without a DOB as unlocked_no_dob.

Airtable is the canonical roster; only the slip-display fields are merged here.
gross_salary / monthly_tds / monthly_pt stay from the local cache (Abhijeet edits
those deliberately; they drive money and must not change silently).
"""
from __future__ import annotations

import json
from pathlib import Path

from core import config
from core.airtable_client import AirtableClient

EMPLOYEES_TABLE = "tblnVWoTI5Hd1rCdf"          # Payroll base / Employees
_MERGE_FIELDS = ("date_of_birth", "pan", "department", "bank_account", "ifsc")
_AIRTABLE_FIELD = {"date_of_birth": "Date of Birth", "pan": "PAN",
                   "department": "Department", "bank_account": "Bank Account",
                   "ifsc": "IFSC"}


def _airtable_active(client) -> dict:
    """{employee_id|email: {merge fields}} for Active employees from Airtable."""
    recs = client.list_records(config.AT_BASE_PAYROLL, EMPLOYEES_TABLE,
                               formula="{Status}='Active'")
    out = {}
    for r in recs:
        f = r.get("fields", {})
        rec = {k: f.get(_AIRTABLE_FIELD[k]) for k in _MERGE_FIELDS}
        eid = f.get("Employee ID") or f.get("employee_id")
        if eid:
            out[str(eid)] = rec
        email = f.get("Email") or f.get("email")
        if email:
            out[str(email).lower()] = rec
    return out


def load(roster_path, use_airtable: bool = True, client_factory=AirtableClient) -> dict:
    """Local roster enriched from Airtable (DOB/PAN/Department/Bank/IFSC). Airtable
    wins per Employee ID for those fields only; everything else (gross/TDS/PT) stays
    local. Falls back to local on any Airtable error."""
    cfg = json.loads(Path(roster_path).read_text())
    if not use_airtable:
        cfg["_roster_source"] = "local (airtable disabled)"
        return cfg
    try:
        air = _airtable_active(client_factory())
    except Exception as ex:                    # no token / network / API — graceful
        cfg["_roster_source"] = f"local (airtable unavailable: {type(ex).__name__})"
        return cfg
    merged = 0
    for e in cfg["employees"]:
        a = air.get(str(e.get("employee_id"))) or air.get(str(e.get("email") or "").lower())
        if not a:
            continue
        for k in _MERGE_FIELDS:
            if a.get(k) not in (None, ""):      # never overwrite with a blank
                e[k] = a[k]
        merged += 1
    cfg["_roster_source"] = f"airtable+local (merged {merged})"
    return cfg
