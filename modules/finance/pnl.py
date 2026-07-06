"""P&L category map + computation (Phase 3, §3). The SINGLE authority for what a
ledger row counts as. Every row maps to exactly one bucket; nothing is silently
dropped or silently lumped into 'Other' — an unmapped row is `uncategorized` and
surfaced (flagged), never hidden (Abhijeet's rule).

Buckets (Abhijeet 2026-06-26):
  OPERATING P&L      revenue | refund | platform_fee | tax | payroll | opex
  BELOW THE LINE     owner_draw            (equity, shown separately, NOT in op P&L)
  EXCLUDED           transfer | sweep | cc_settlement   (balance-sheet / non-economic)
  FLAGGED            uncategorized

Transfers/sweeps/cc_settlement never re-count as revenue/expense. Money is the
pinned USD presentation (amount_base_minor); pure integer minor units, no float.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from modules.finance import money, transfers

OPERATING = ("revenue", "refund", "platform_fee", "tax", "payroll", "opex")
# sources whose rows are reconciled-to-statement (v3); the rest are legacy v2 (USD,
# unreconciled) — kept for continuous history, marked so they're distinguishable.
RECONCILED_SOURCES = {"IDFC", "Wise", "Upwork", "Upwork-CSV"}

# ── Bank-spend reclassification (Abhijeet, 2026-07-08 cockpit review) ─────────
# Operating expenses = the CREDIT-CARD statement. Bank-statement spends are the
# owner's personal outflows (-> owner_draw, below the line) EXCEPT:
#   - POS debits to the business-SaaS vendors below       -> opex
#   - POS debits to Upwork (+ the PayPal Upwork-escrow)    -> platform_fee
#   - office rent (Rakhi Bohra)                            -> opex
#   - past employees/contractors (Harshit, Atharva)        -> payroll
#   - Jay/Shruti transfers that are salary-shaped          -> payroll
#     (>= _SALARY_MIN_BASE_MINOR base-USD; smaller = personal transfer)
#   - credit-card bill payments on the bank                -> cc_settlement
# Bank sources = IDFC (v3 import) + 'Bank' (legacy v2 import of the same account).
# 'Card' rows are the credit-card statement itself and stay opex.
BANK_SOURCES = {"IDFC", "Bank"}
BUSINESS_POS_VENDORS = (
    "GOOGLE WORKSPACE", "GOOGLE CLOUD", "CLAUDE.AI", "GODADDY", "AMAZON WEB",
    "AMAZONAWS", "OPENAI", "CHATGPT", "LOOM SUBSCRIPTION", "MAKE.COM", "GITHUB",
    "LEMLIST", "FRAMER", "VERCEL", "LINKEDIN", "WHOP.COM", "CALENDLY", "VAPI",
    "PERPLEXITY", "RENDER.COM", "TERABOX", "PAYPROGLOBA", "CLEVERBRIDG",
)
_RENT_PAYEES = ("RAKHI MUKESH BOHRA",)                 # office rent
_CONTRACTOR_PAYEES = ("HARSHIT SINGHAL", "ATHARVA AJAY BARAWKA")
_EMPLOYEE_PAYEES = ("JAY SUSHIL POKHARNA", "SHRUTI DEEPAK DHUMNE")
_SALARY_MIN_BASE_MINOR = 100_000                       # $1,000 — salary-shaped floor


def _bank_spend(row: dict) -> str:
    """Classify a bank-statement spend (a row that would previously have been
    lumped into opex). See the rule table above; default is owner_draw —
    personal spending from the business account is an equity draw, not a cost."""
    d = (row.get("description") or "").upper()
    if "CREDITCARDBILL" in d.replace(" ", ""):
        return "cc_settlement"                          # bill payment, not a spend
    if d.startswith("POS") or "POS-VISA/" in d:
        if "UPWORK" in d or "UPWRKESCROW" in d:
            return "platform_fee"                       # Upwork charges off the bank
        if any(v in d for v in BUSINESS_POS_VENDORS):
            return "opex"                               # business SaaS on debit card
        return "owner_draw"                             # personal card-on-bank spend
    if "UPWRKESCROW" in d:
        return "platform_fee"
    if any(p in d for p in _RENT_PAYEES):
        return "opex"                                   # office rent
    if any(p in d for p in _CONTRACTOR_PAYEES):
        return "payroll"                                # past employees/contractors
    if any(p in d for p in _EMPLOYEE_PAYEES):
        base = abs(row.get("amount_base_minor") or 0)
        return "payroll" if base >= _SALARY_MIN_BASE_MINOR else "owner_draw"
    return "owner_draw"                                 # UPI / EMI / transfers / rest


def bucket(row: dict) -> str:
    """The P&L bucket for one transactions row. Order matters: transfers/non-economic
    are removed first, then v3 `category`, then Upwork/legacy `type`/`accounting_subtype`."""
    if transfers.is_transfer_leg(row):
        return "transfer"
    cat = (row.get("category") or "").strip()
    if cat in ("internal_sweep",):
        return "sweep"
    if cat == "cc_settlement":
        return "cc_settlement"
    if cat == "owner_draw":
        return "owner_draw"
    if cat == "income":
        return "revenue"
    if cat == "expense":
        # bank-statement expenses are NOT business opex by default (2026-07-08):
        # route through the bank-spend rules; non-bank sources keep opex.
        return _bank_spend(row) if (row.get("source") or "") in BANK_SOURCES \
            else "opex"
    if cat == "salary":
        return "payroll"
    if cat == "fee":                       # Wise fee
        return "platform_fee"

    t = (row.get("type") or "").strip()
    # the Upwork subtype label can live in accounting_subtype OR upwork_type
    # (API vs CSV import); the API's structural upwork_type values (APAdjustment/
    # ARInvoice/...) don't collide with the fee/tax/earning labels matched below.
    sub = (row.get("accounting_subtype") or "").strip() or (row.get("upwork_type") or "").strip()
    if t in ("Hourly Earning", "Fixed Earning", "Bonus"):
        return "revenue"
    if t == "Refund":
        return "refund"
    if t == "Connect Purchase":
        return "platform_fee"
    if sub == "Service Fee":
        return "platform_fee"
    if sub in ("WHT", "Withholding Tax", "GST"):
        return "tax"
    if sub in ("Milestone", "Payment"):    # earnings mis-typed 'Other' by Upwork
        return "revenue"
    # legacy v2 Bank/Card types. Card = the credit-card statement = opex by
    # definition; legacy Bank rows route through the same bank-spend rules.
    if t == "Salary":
        return "payroll"
    if t in ("Other Expense", "Tools / SaaS", "Office", "Software", "Marketing"):
        return _bank_spend(row) if (row.get("source") or "") in BANK_SOURCES \
            else "opex"
    if t in ("Personal", "Owner Draw"):
        return "owner_draw"

    # LAST RESORT (Abhijeet-approved 2026-06-26): some Upwork rows carry the type
    # only in the free-text description, no structured field. Derive from it; this
    # is intentionally last so a structured field always wins. Order: tax/fee
    # before earnings (a fee line never says 'earnings').
    return _by_description(row.get("description") or "")


def _by_description(desc: str) -> str:
    d = desc.lower()
    if "withholding tax" in d or "gst (" in d:
        return "tax"
    if "service fee" in d or "membership" in d or "connects" in d:
        return "platform_fee"
    if "earnings" in d or "milestone" in d or "bonus" in d:
        return "revenue"
    return "uncategorized"


def compute(rows) -> dict:
    """Aggregate rows into the P&L. Returns minor-unit totals per bucket, the
    operating profit, owner draws (below the line), excluded totals, and the
    flagged uncategorized rows. `rows` are dicts with amount_base_minor + the
    classification fields."""
    by_bucket = defaultdict(int)
    counts = defaultdict(int)
    uncategorized = []
    legacy_rows = 0
    for r in rows:
        # prefer the PERSISTED bucket (finance-bucketize) so the P&L is reproducible
        # from stored data; fall back to computing it for un-bucketized rows.
        b = r.get("pnl_bucket") or bucket(r)
        usd = r.get("amount_base_minor")
        if usd is None:
            uncategorized.append({"record_id": r.get("record_id"),
                                  "reason": "no amount_base_minor"})
            continue
        by_bucket[b] += usd
        counts[b] += 1
        if b == "uncategorized":
            uncategorized.append({"record_id": r.get("record_id"),
                                  "type": r.get("type"), "category": r.get("category"),
                                  "subtype": r.get("accounting_subtype"),
                                  "usd": str(money.from_minor(usd))})
        if (r.get("source") or "") not in RECONCILED_SOURCES:
            legacy_rows += 1

    operating = sum(by_bucket[b] for b in OPERATING)
    revenue = by_bucket["revenue"] + by_bucket["refund"]
    return {
        "by_bucket_minor": dict(by_bucket),
        "counts": dict(counts),
        "net_revenue_minor": revenue,
        "operating_costs_minor": sum(by_bucket[b] for b in
                                     ("platform_fee", "tax", "payroll", "opex")),
        "operating_profit_minor": operating,
        "owner_draw_minor": by_bucket["owner_draw"],
        "excluded_minor": {k: by_bucket[k] for k in ("transfer", "sweep", "cc_settlement")},
        # flagged = unmapped rows (no bucket) + rows with no pinned amount; both surfaced
        "uncategorized_count": len(uncategorized),
        "legacy_v2_rows": legacy_rows,
        "_uncategorized": uncategorized[:50],
    }
