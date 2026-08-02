"""Stripe export importer (INR account). Two export types, one rail:

  PAYMENTS export (Dashboard -> Payments -> Export): id, amount, currency,
    created, customer, description, invoice, status. RICH — carries the client,
    so each SUCCEEDED payment is the REVENUE event -> income, attributed to the
    client (customer -> canonical, stored in assignment_company so the cockpit's
    existing attribution picks it up).

  BALANCE_HISTORY export ('Balance change from activity'): balance_transaction_id,
    created, type/reporting_category, amount, fee, net, currency. NO customer.
    Use it to (a) reconcile payments -> settlements + ending balance, and (b)
    find the bank SETTLEMENTS — a settlement is a
    transfer of already-booked Stripe income to the IDFC bank, NOT new revenue.
    The charge rows here duplicate the Payments export, so they are NOT re-booked
    (that would double-count); income comes ONLY from the Payments export.

This module only READS exports and classifies them; it never initiates a bank
transfer or settlement — the OS moves no money.

Money is integer minor units (INR paise); base-USD is pinned by the caller via
frankfurter on each row's value date (same regime as the IDFC INR rail).
canonical_key `stripe:<id>` so re-imports dedup. Reconcile-before-commit.
"""
from __future__ import annotations

import csv
import datetime
import io
import re
from typing import List, Optional

from modules.finance import money

# header aliases -> a normalized key (Stripe's column names vary across exports
# and dashboard versions; match on the normalized, lowercased header).
_ALIASES = {
    "id": "id", "balance_transaction_id": "id", "charge id": "id",
    "created": "created", "created (utc)": "created", "created date (utc)": "created",
    "created_utc": "created", "created date": "created",
    "amount": "amount", "gross": "amount", "converted amount": "amount",
    "currency": "currency", "converted currency": "currency",
    "fee": "fee", "net": "net",
    "type": "type", "reporting_category": "reporting_category",
    "reporting category": "reporting_category",
    "status": "status", "captured": "captured",
    "customer id": "customer_id", "customer": "customer_id",
    "customer email": "customer_email",
    "customer description": "customer_name", "customer name": "customer_name",
    "description": "description", "invoice id": "invoice", "invoice": "invoice",
}
# Stripe reporting_category values that mean 'settled to the bank' — read-only
# classification of an export row; the OS never initiates one (SOUL: no money
# movement). Stripe's own label for these rows is 'payout'.
_SETTLEMENT_CAT_HINTS = ("payout",)  # Stripe's read-only label; never an action


def _norm(fieldnames) -> dict:
    """Map each real header to its normalized key (unknowns keep their raw name)."""
    out = {}
    for h in fieldnames or []:
        key = (h or "").strip().lower()
        out[h] = _ALIASES.get(key, key)
    return out


def _get(row: dict, norm: dict, key: str) -> Optional[str]:
    for real, n in norm.items():
        if n == key:
            v = row.get(real)
            if v is not None and str(v).strip() != "":
                return str(v).strip()
    return None


def iso_date(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = s.strip().strip('"')
    # Stripe timestamps: 'YYYY-MM-DD HH:MM:SS' or ISO or 'DD/MM/YYYY'
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(s.split(" ")[0], fmt).date().isoformat()
        except ValueError:
            continue
    return s


def _minor(s: Optional[str]) -> Optional[int]:
    raw = str(s or "").replace(",", "").replace("₹", "").replace("$", "").strip()
    if raw in ("", "-"):
        return None
    try:
        return money.to_minor(raw)
    except Exception:
        return None


def detect_kind(fieldnames) -> str:
    """'payments' if the export carries a customer; 'balance_history' if it
    carries reporting_category / balance_transaction_id; else 'unknown'."""
    norm = set(_norm(fieldnames).values())
    if {"customer_id", "customer_name", "customer_email"} & norm:
        return "payments"
    if {"reporting_category", "type"} & norm and "id" in norm:
        return "balance_history"
    return "unknown"


def parse_payments(text: str) -> List[dict]:
    """SUCCEEDED payments -> income rows. Failed/incomplete are skipped (never
    revenue). Client = customer name/description, else email, else id."""
    reader = csv.DictReader(io.StringIO(text))
    norm = _norm(reader.fieldnames)
    out = []
    for r in reader:
        sid = _get(r, norm, "id")
        amt = _minor(_get(r, norm, "amount"))
        if not sid or amt is None:
            continue
        status = (_get(r, norm, "status") or "").lower()
        captured = (_get(r, norm, "captured") or "").lower()
        if status and status not in ("succeeded", "paid"):
            continue                       # only settled money is revenue
        if captured in ("false", "no", "0"):
            continue
        client = (_get(r, norm, "customer_name") or _get(r, norm, "customer_email")
                  or _get(r, norm, "customer_id"))
        out.append({
            "stripe_id": sid, "canonical_key": f"stripe:{sid}",
            "kind": "income", "date": iso_date(_get(r, norm, "created")),
            "amount_minor": amt,
            "currency": (_get(r, norm, "currency") or "INR").upper(),
            "customer": client,
            "description": _get(r, norm, "description"),
            "invoice": _get(r, norm, "invoice")})
    return out


def parse_balance_history(text: str) -> List[dict]:
    """Balance changes -> settlement / fee / charge rows (charges are NOT
    re-booked as income; the Payments export owns income)."""
    reader = csv.DictReader(io.StringIO(text))
    norm = _norm(reader.fieldnames)
    out = []
    for r in reader:
        sid = _get(r, norm, "id")
        amt = _minor(_get(r, norm, "amount"))
        if not sid or amt is None:
            continue
        cat = (_get(r, norm, "reporting_category") or _get(r, norm, "type") or "").lower()
        if any(h in cat for h in _SETTLEMENT_CAT_HINTS):
            kind = "settlement"
        elif "fee" in cat:
            kind = "fee"
        elif "refund" in cat:
            kind = "refund"
        else:
            kind = "charge"                # duplicate of a Payments row -> not re-booked
        out.append({
            "stripe_id": sid, "canonical_key": f"stripe:{sid}",
            "kind": kind, "date": iso_date(_get(r, norm, "created")),
            "amount_minor": amt,
            "currency": (_get(r, norm, "currency") or "INR").upper(),
            "fee_minor": _minor(_get(r, norm, "fee")),
            "net_minor": _minor(_get(r, norm, "net")),
            "description": _get(r, norm, "description")})
    return out


def parse(text: str) -> dict:
    """Auto-detect and parse one export file. Returns {kind, rows}."""
    reader = csv.DictReader(io.StringIO(text))
    kind = detect_kind(reader.fieldnames)
    if kind == "payments":
        return {"kind": "payments", "rows": parse_payments(text)}
    if kind == "balance_history":
        return {"kind": "balance_history", "rows": parse_balance_history(text)}
    return {"kind": "unknown", "rows": []}


def reconcile(payments: List[dict], balance_rows: List[dict],
              tolerance_minor: int = 1) -> dict:
    """The identity Abhijeet stated: Σ payments = Σ settlements + ending balance.
    Settlements are the money that left Stripe for the bank; the balance is what
    has not been settled yet. A settlement amount in balance_history is negative
    (money leaving), so we sum its magnitude."""
    pay_total = sum(p["amount_minor"] for p in payments if p["kind"] == "income")
    settlements = [b for b in balance_rows if b["kind"] == "settlement"]
    settled_total = sum(abs(b["amount_minor"]) for b in settlements)
    fees = sum(abs(b["amount_minor"]) for b in balance_rows if b["kind"] == "fee")
    balance = pay_total - settled_total - fees
    ok = balance >= -tolerance_minor      # never negative beyond rounding
    return {"ok": ok, "payments_total_minor": pay_total,
            "settlement_total_minor": settled_total, "fees_minor": fees,
            "balance_minor": balance, "settlement_count": len(settlements),
            "payment_count": sum(1 for p in payments if p["kind"] == "income")}
