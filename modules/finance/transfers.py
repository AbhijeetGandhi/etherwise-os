"""Transfer classification + integrity (Phase 2, §3.10).

A TRANSFER moves money between our OWN accounts — it is NOT income or expense.
Examples: an Upwork withdrawal (Upwork USD balance -> bank), a Wise USD->INR
send. Both legs of a transfer must reconcile, and the whole bucket is EXCLUDED
from P&L so a transfer never re-counts as revenue/expense (Abhijeet, 2026-06-25).

Transfer legs, by source marker (a DB transactions row is a dict):
  - Upwork withdrawal principal: accounting_subtype 'Withdrawal' (upwork_type
    'APPayment') -> the -USD leg that pairs with the +INR bank credit.
  - Upwork withdrawal fee:       accounting_subtype 'Withdrawal Fee', or a legacy
    row whose description says 'withdrawal fee' -> its OWN line in the bucket.
  - Wise transfer out:           kind/category 'transfer_out' (Sent money to
    Etherwise) -> the USD leg of the Wise USD->INR transfer.
  - IDFC bank transfer in:       category 'transfer_in' (WISE / PAYMENT ESCROW
    credits) -> the +INR leg.

The classifier reads whatever marker a row carries, so it works across API-,
CSV-, Wise- and IDFC-sourced rows without a per-source branch at the call site.
"""
from __future__ import annotations

_SUBTYPES = {"Withdrawal", "Withdrawal Fee"}
_KINDS = {"transfer_out", "transfer_in"}


def is_transfer_leg(row: dict) -> bool:
    # the subtype label can live in accounting_subtype OR upwork_type, depending on
    # whether the row came via the API or a CSV import.
    if ((row.get("accounting_subtype") or "") in _SUBTYPES
            or (row.get("upwork_type") or "") in _SUBTYPES):
        return True
    if (row.get("type") or "") == "Withdrawal":      # a withdrawal IS a transfer leg
        return True
    if (row.get("upwork_type") or "") == "APPayment":
        return True
    if (row.get("kind") or row.get("category") or "") in _KINDS:
        return True
    if "withdrawal fee" in (row.get("description") or "").lower():
        return True
    return False


def leg_role(row: dict) -> str:
    """Coarse role for pairing/reporting: 'out_principal' (money leaving an
    account), 'fee' (the transfer's own fee line), 'in_principal' (money
    arriving). Used by the transfer-integrity invariant + P&L exclusion."""
    sub = row.get("accounting_subtype") or ""
    if sub == "Withdrawal Fee" or "withdrawal fee" in (row.get("description") or "").lower():
        return "fee"
    if sub == "Withdrawal" or (row.get("upwork_type") or "") == "APPayment" \
            or (row.get("kind") or row.get("category")) == "transfer_out":
        return "out_principal"
    if (row.get("category") or "") == "transfer_in":
        return "in_principal"
    return "transfer"


def exclude_from_pnl(rows):
    """Iterator of the P&L-eligible rows (every non-transfer row). Phase-3 P&L
    layers MUST funnel through this so transfers never re-count as revenue."""
    return (r for r in rows if not is_transfer_leg(r))
