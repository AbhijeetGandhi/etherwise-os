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

import re as _re
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
# MYHQ (2026-10-06): the coworking desk, Rs 26,622.64 by UPI on 22 Sep. Asked
# which of September's unlisted bank spends were the company's, he ticked it. A
# vendor rule and not a single payment, unlike the list further down: a
# coworking membership is an office by definition and it recurs, so asking him
# again every month would only be a way to get it wrong one month.
_RENT_PAYEES = ("RAKHI MUKESH BOHRA",                  # office rent
                "MYHQ")                                # coworking
# JIO (2026-10-07): "jio is my internet bill". He had ticked September's
# Rs 4,718.82 as the company's the day before; asked whether the identical
# payment of 20 Aug was the same bill, that sentence was the whole answer -- a
# statement about the VENDOR, not about one payment. So this is a vendor rule,
# and September's reference has left _CONFIRMED_BUSINESS_REFS: one decision,
# one place.
#
# Matched on the UPI PAYEE FIELD being exactly one of these -- the two
# spellings the same bill has arrived under (20 Aug "JIO", 17 Sep "MYJIO").
# Not a substring: "JIO Post" is a postpaid mobile line from Oct 2025, a
# different thing in a closed year, and "JIOMART" is a shop.
_INTERNET_PAYEES = ("JIO", "MYJIO")
_CONTRACTOR_PAYEES = ("HARSHIT SINGHAL", "ATHARVA AJAY BARAWKA")
_EMPLOYEE_PAYEES = ("JAY SUSHIL POKHARNA", "SHRUTI DEEPAK DHUMNE")
# personal (non-business) counterparties: bank money EITHER direction is
# personal, never revenue/opex. Dynatech was a private transfer Abhijeet sent
# (-$11,140, correctly owner_draw) that was returned (+$10,550) — but the return
# carried category='income' and got mis-booked as revenue (Abhijeet, 2026-07-30).
# PRASHANT JAWAHARLAL BHANDARI (2026-09-05): Rs 65,000 on 15 Aug and again on
# 2 Sep, both lines reading "Salary august", a payee with no history before this
# statement. Abhijeet: a family member who returns the money to him in cash. So
# no work was bought and no wage was borne -- the cash comes back -- and filing
# it under payroll would put Rs 1,30,000 of cost into the P&L that the business
# never paid, which is the one thing this ledger exists to prevent. It is a
# draw. What his RETURN says about it is between him and his CA; what this
# records is where the money went and that it came back.
# AVANTIKA DNYANESHWAR BHAMBURE (2026-09-05, "avantika is also a draw"): the
# same arrangement, Rs 14,800 on 2 Sep.
# HANUMAN SOMEENATH MORE (2026-10-06): Rs 1,50,000 in four lines on 22-23 Sep
# (Rs 100, Rs 100, Rs 99,800, Rs 50,000), a payee with no history and no
# narration. Abhijeet: "this is the employee we paid for but it for tax
# purposes. so we actualy received cash for that". The same arrangement as the
# two above -- the money came back -- so it is a draw. Unnarrated, it would
# have fallen to a draw anyway; he is NAMED so that a later line which does say
# "Salary" cannot turn Rs 1,50,000 of it into payroll.
_PERSONAL_COUNTERPARTIES = ("DYNATECH", "PRASHANT JAWAHARLAL BHANDARI",
                            "AVANTIKA DNYANESHWAR BHAMBURE",
                            "HANUMAN SOMEENATH MORE")
# bank CREDITS that are NOT client revenue (Abhijeet, 2026-07-30): quarterly FD
# interest (bank interest income) and refunds/reversals of expenses (cancelled
# subscriptions, POS refunds, UPI reversals). Excluded from revenue -> owner_draw
# (personal / non-operating), so they never inflate the top line.
# 2026-10-06, two more of the same kind, each by the bank's own wording:
#   "ONUS refund/EN413562/2716/11.09.2026" (+Rs 10,000, 12 Sep) is the bank
#   handing back what its ATM debited and never dispensed -- withdrawal 2716 of
#   11 Sep. It reverses a draw, so it is one; unmatched, it read as income and
#   would have put Rs 10,000 of his own cash into September's revenue.
#   "Mandate Refund Test" is Apple returning the Rs 399 it takes to test a UPI
#   mandate. The July one (3 Jul) had been sitting in revenue since.
_NONREVENUE_BANK_RE = _re.compile(
    r"FD INTEREST|/REV/|REF/POS|REF/VISAPOS|-CANCELLED|ONUS REFUND|MANDATE REFUND",
    _re.I)
_SALARY_MIN_BASE_MINOR = 100_000                       # $1,000 — salary-shaped floor
# an Upwork 'Payment' row whose text names the instrument is the card/PayPal
# settlement of a connects or membership invoice — the ARPayment leg as the
# CSV export spells it (2026-08-26)
_CARD_SETTLEMENT_RE = _re.compile(r"\b(visa|mastercard|master card|amex|paypal|card)\b", _re.I)
# Upwork bills TWO unrelated things down one bank line and the line cannot tell
# them apart: Connects/membership on his FREELANCER side (a platform cost) and
# the CLIENT side paying a freelancer he HIRED (an operating cost). Every one of
# them reads "POS-VISA/Upwork -<ref>REF/.../Dublin"; the merchant string is
# truncated differently by the day, so one October 2024 row happens to read
# "ConneDublin" and the rest say nothing at all.
# 26 of these exist, Feb 2024 to Sep 2026. Abhijeet, 2026-09-05: "16661 is a
# hire everything else is connects". So the DEFAULT stays platform_fee, which is
# right 25 times out of 26, and a hire is named here by its own Upwork
# reference. Not a date fence: he will buy Connects on this card again, and a
# fence would quietly file that as a hire. Both land in the operating P&L, so a
# wrong one costs presentation, not profit -- but a list of facts he confirmed
# beats a pattern he never stated. The real fix is reading the CLIENT org's own
# ledger through the MCP, which would say which it was without asking.
# 2026-10-06, the 27th of them, Rs 38,124 on 14 Sep: "A freelancer I hired".
_UPWORK_HIRE_REFS = ("948419050",                      # 2 Sep 2026, Rs 16,661.39
                     "951702482")                      # 14 Sep 2026, Rs 38,124.00

# Bank spends he has confirmed as BUSINESS, one payment at a time. The default
# for an unlisted bank spend is a draw and it is the right default, so each of
# these is here because he said so about THAT payment -- keyed on the bank's own
# reference for it, matched as a whole slash-field of the narration so a short
# number cannot match inside a longer one.
#
# NOT vendor rules, on purpose: MakeMyTrip is where a holiday gets booked too,
# and the CA's NEXT quarterly filing fee is his to call, not a pattern's. A
# payment leaves this list the day he says something about the VENDOR instead
# -- Jio did, on 2026-10-07 ("jio is my internet bill"), and is now
# _INTERNET_PAYEES above. (A split payment -- part cost, part cash back -- is
# not here at all: see `bank_splits`.)
#
#   2026-10-06, asked which of September's unlisted spends were the company's,
#   he ticked all four: "myHQ" (a vendor rule, above), "The trip", "Jio" (a
#   vendor rule since the next day), and "CA's Rs 4,000 filing fee" (the 4 Aug
#   one, a draw until that answer).
_CONFIRMED_BUSINESS_REFS = (
    "IDFB6259M0398252",    # 16 Sep 2026, Rs 12,000.00  Tamalisto, the trip's stay
    "6272125289",          # 29 Sep 2026, Rs  5,857.00  MakeMyTrip, the same trip
    "14039720",            #  4 Aug 2026, Rs  4,000.00  CA, "TDS return filing Q1"
)

# TDS remitted to the CA is PAYROLL (Abhijeet, 2026-09-07: "even if I am sending
# less salary you need to include tds that we share as well?"). It is wage that
# was earned and withheld — every payroll figure here was the net transfer, so a
# month of employing people looked cheaper than it was by the whole of the TDS,
# and the tax sat in owner draws where it meant nothing.
#
# NARROW ON PURPOSE: his CA's name AND the word TDS. Two other payments go to
# the same CA and are NOT payroll — the quarterly return-filing fee (Rs 4,000,
# 4 Aug: a professional fee; his word came on 2026-10-06 and it is business, so
# that one payment is opex by its own reference in _CONFIRMED_BUSINESS_REFS) and
# "Tax 2026" (Rs 4,72,710, 31 Aug: his own FY25-26 liability, which never says
# TDS). Both are kept out of PAYROLL by name, not by an amount or a date.
#
# The wide end of this lives in `payroll_tax.uncovered` + doctor #14, which
# looks at EVERY bank debit naming TDS and goes RED on one this rule books
# without his month behind it. A rule that cannot mis-book, a check that cannot
# miss — the amounts here are ~Rs 30,000 a month and the months are his to say.
_CA_PAYEE = "PRITI BORA & CO"
_TDS_WORD_RE = _re.compile(r"\bTDS\b", _re.I)
_NOT_TDS_REMITTANCE_RE = _re.compile(r"RETURN\s+FILING|FILING\s+FEE", _re.I)


def is_tds_remittance(description: Optional[str]) -> bool:
    """Does this bank line say 'TDS on wages, paid to the CA'? Public because
    doctor #14 must ask the SAME question the booking asks — a check that
    re-implements the rule it is checking is not a check."""
    d = (description or "").upper()
    return bool(_CA_PAYEE in d and _TDS_WORD_RE.search(d)
                and not _NOT_TDS_REMITTANCE_RE.search(d))


def is_confirmed_business(description: Optional[str]) -> bool:
    """Is this one of the payments he has confirmed as the company's, by its
    own bank reference? A whole slash-field must equal the reference: IDFC
    narrations are slash-separated, and '14039720' must not match inside some
    other payment's longer number."""
    fields = {f.strip() for f in (description or "").upper().split("/")}
    return any(ref in fields for ref in _CONFIRMED_BUSINESS_REFS)


def _upi_payee(d: str) -> str:
    """'UPI/DR/662601147108/MYJIO/HDFC/myjio4./Pay' -> 'MYJIO': the fourth
    slash-field of a UPI debit, as IDFC writes it. '' for any other shape, which
    is a refusal and not a match on everything."""
    parts = [p.strip() for p in d.split("/")]
    if len(parts) >= 4 and parts[0] == "UPI" and parts[1] == "DR":
        return parts[3]
    return ""


def _bank_spend(row: dict) -> str:
    """Classify a bank-statement spend (a row that would previously have been
    lumped into opex). See the rule table above; default is owner_draw —
    personal spending from the business account is an equity draw, not a cost."""
    d = (row.get("description") or "").upper()
    if "CREDITCARDBILL" in d.replace(" ", ""):
        return "cc_settlement"                          # bill payment, not a spend
    if is_confirmed_business(d):
        return "opex"                                   # he said so, of this payment
    if d.startswith("POS") or "POS-VISA/" in d:
        if "UPWORK" in d or "UPWRKESCROW" in d:
            if any(ref in d for ref in _UPWORK_HIRE_REFS):
                return "opex"                           # a freelancer he hired
            return "platform_fee"                       # Connects/plan off the bank
        if any(v in d for v in BUSINESS_POS_VENDORS):
            return "opex"                               # business SaaS on debit card
        return "owner_draw"                             # personal card-on-bank spend
    if "UPWRKESCROW" in d:
        return "platform_fee"
    if is_tds_remittance(d):
        return "payroll"                                # wage withheld, then remitted
    if _upi_payee(d) in _INTERNET_PAYEES:
        return "opex"                                   # his internet bill
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
    # ARPayment = the settlement leg of an Upwork membership/connects ARInvoice,
    # paid via the freelancer's OWN card/PayPal (Abhijeet, 2026-07-30). The cost
    # is the paired ARInvoice (already platform_fee); the payment leg is a
    # non-P&L settlement, never revenue — it was inflating the top line.
    # The CSV export spells the same leg 'Payment' with the card in the text
    # ("300 Connects · Visa 3368"); the MCP capture of 26 Aug showed the $45
    # Visa row booked as revenue that way (Abhijeet, 2026-08-26 17:55: fix it).
    if (row.get("upwork_type") or "").strip() == "ARPayment":
        return "transfer"
    if ((row.get("accounting_subtype") or row.get("upwork_type") or "").strip() == "Payment"
            and _CARD_SETTLEMENT_RE.search(row.get("description") or "")):
        return "transfer"
    # personal counterparties override any category (incl. a mis-tagged 'income'
    # on a returned private transfer) — bank money with them is never business
    if (row.get("source") or "") in BANK_SOURCES and any(
            p in (row.get("description") or "").upper()
            for p in _PERSONAL_COUNTERPARTIES):
        return "owner_draw"
    # FD interest + expense refunds/reversals: bank credits that are not client
    # revenue -> owner_draw (excluded from the operating P&L)
    if (row.get("source") or "") in BANK_SOURCES and \
            _NONREVENUE_BANK_RE.search(row.get("description") or ""):
        return "owner_draw"
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
    # the plan subscriptions (Freelancer Plus / Agency Plus): the CSV calls
    # them 'Subscription', the API/MCP 'Membership Fee' — a platform cost
    # either way. Two -$19.99 CSV rows sat uncategorized until 2026-08-26.
    if sub in ("Subscription", "Membership Fee"):
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
