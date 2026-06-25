"""Upwork transaction-history CSV importer (Reports → Transaction History).

Ported from v2 etherwise_os/webapp/upwork_import.py as the spec, with one change
the correctness regime demands: amounts parse to integer cents via money.to_minor
(Decimal) — NEVER v2's float `_num`. Each row's `Ref ID` is the canonical
recordId → canonical_key `upwork:<Ref ID>` (same namespace the API sync uses, so
CSV imports and API rows dedup against each other). The `Current balance $`
column is the running balance → the statement-integrity tie-out (§3.3 #1/#3):
balance[i] == balance[i-1] + amount[i], exact to the cent. A row whose `Amount $`
is blank is NOT fabricated — it quarantines and breaks the tie-out until resolved.

`Amount $` is always USD (Upwork's base); USD is our base, so amount_base_minor
== amount_minor and fx_rate '1'.
"""
from __future__ import annotations

import csv
import datetime
import io
from typing import List, Optional

from modules.finance import money

# Upwork "Transaction type" -> our Type whitelist (ported verbatim from v2).
CSV_TYPE_MAP = {
    "Hourly": "Hourly Earning", "Fixed-price": "Fixed Earning",
    "Fixed Price": "Fixed Earning", "Bonus": "Bonus", "Service Fee": "Other",
    "WHT": "Other", "Withholding Tax": "Other", "GST": "Other",
    "Membership Fee": "Connect Purchase", "Connects": "Connect Purchase",
    "Refund": "Refund", "Withdrawal": "Withdrawal", "Withdrawal Fee": "Other",
    "Subscription": "Other",
}


def iso_date(s: Optional[str]) -> Optional[str]:
    """Upwork dates 'May 27, 2026' -> 'YYYY-MM-DD'."""
    if not s:
        return None
    s = s.strip().strip('"')
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


def amount_minor(s: Optional[str]) -> Optional[int]:
    """'$-45.00' / '1,234.50' -> int cents via Decimal. Blank -> None (NOT 0 —
    never fabricate; the row will quarantine)."""
    raw = str(s or "").replace(",", "").replace("$", "").strip()
    if not raw:
        return None
    try:
        return money.to_minor(raw)          # Decimal path, exact, no float
    except Exception:
        return None


def parse(text: str) -> List[dict]:
    """One normalized dict per CSV row (rows with no Ref ID are skipped)."""
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        ref = (r.get("Ref ID") or "").strip()
        if not ref:
            continue
        type_raw = (r.get("Transaction type") or "").strip()
        summary = (r.get("Transaction summary") or "").strip()
        desc1 = (r.get("Description 1") or "").strip()
        out.append({
            "ref_id": ref,
            "canonical_key": f"upwork:{ref}",
            "date": iso_date(r.get("Date")),
            "type_raw": type_raw,
            "type": CSV_TYPE_MAP.get(type_raw, "Other"),
            "amount_minor": amount_minor(r.get("Amount $")),
            "currency": "USD",                       # Amount $ is always USD
            "balance_minor": amount_minor(r.get("Current balance $")),
            "description": " · ".join(p for p in (summary, desc1) if p),
            "account_name": (r.get("Account name") or "").strip(),
        })
    return out


def reconcile_running_balance(rows: List[dict]) -> dict:
    """Upwork statement integrity (§3.3 #1/#3) — delegates to the shared
    native-order reconcile (modules.finance.importers.statement). Upwork rows
    carry ref_id, used as the break/quarantine label key."""
    from modules.finance.importers import statement
    return statement.reconcile_running_balance(rows)
