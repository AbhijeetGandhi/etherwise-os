"""Wise statement CSV importer (USD account). Ported from v2 as the spec, with
the correctness regime applied: amounts are integer cents (money.to_minor, no
float); canonical_key `wise:<TransferWise ID>` (the v2 namespace, so re-imports
dedup). The `Running Balance` drives statement integrity (shared reconcile).

v2 hardcoded every Wise row as 'Fixed Earning' — WRONG (it swept fees + transfers
into revenue). This classifies each row so Phase-3 P&L buckets correctly:
  - CREDIT 'Received money from <client>'  -> income  (Direct revenue)
  - DEBIT  'Sent money to Etherwise' (+ Exchange Rate) -> transfer_out (Wise USD
       -> IDFC INR; the Exchange Rate is the REALIZED §3.10 rate, pinned here;
       the INR leg lands in the IDFC import; Phase-2 transfer-integrity ties them)
  - DEBIT  'Wise Charges'                  -> fee      (expense)
Base = USD, so amount_base_minor == amount_minor; the realized FX rate is stored
for the transfer's INR-leg tie-out, never used to re-value the USD leg.
"""
from __future__ import annotations

import csv
import datetime
import io
from typing import List, Optional

from modules.finance import money
from modules.finance.importers import statement


def iso_date(s: Optional[str]) -> Optional[str]:
    """Wise dates are 'DD-MM-YYYY' -> 'YYYY-MM-DD'."""
    if not s:
        return None
    s = s.strip().strip('"')
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


def _minor(s: Optional[str]) -> Optional[int]:
    raw = str(s or "").replace(",", "").replace("$", "").strip()
    if not raw:
        return None
    try:
        return money.to_minor(raw)
    except Exception:
        return None


def _classify(desc: str, wise_id: str, txn_type: str, fx: str) -> str:
    d = (desc or "").lower()
    if wise_id.startswith("FEE-") or "wise charges" in d or "fee" == txn_type.lower():
        return "fee"
    if "sent money to etherwise" in d or (fx and txn_type.upper() == "DEBIT"):
        return "transfer_out"          # Wise USD -> IDFC INR (realized fx)
    if txn_type.upper() == "CREDIT" or "received money from" in d:
        return "income"
    return "other"


def parse(text: str) -> List[dict]:
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        wid = (r.get("TransferWise ID") or "").strip()
        if not wid:
            continue
        desc = (r.get("Description") or "").strip()
        ttype = (r.get("Transaction Type") or "").strip()
        fx = (r.get("Exchange Rate") or "").strip()      # realized, Decimal str
        out.append({
            "wise_id": wid,
            "key": wid,                                   # reconcile label
            "canonical_key": f"wise:{wid}",
            "date": iso_date(r.get("Date")),
            "amount_minor": _minor(r.get("Amount")),
            "currency": (r.get("Currency") or "USD").strip() or "USD",
            "balance_minor": _minor(r.get("Running Balance")),
            "fx_rate": fx or None,                        # USD->INR realized rate
            "txn_type": ttype,
            "kind": _classify(desc, wid, ttype, fx),
            "description": desc,
            "fees_minor": _minor(r.get("Total fees")),
        })
    return out


def reconcile_running_balance(rows: List[dict]) -> dict:
    return statement.reconcile_running_balance(rows)
