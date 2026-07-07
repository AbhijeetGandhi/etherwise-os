"""IDFC FIRST credit-card statement importer (PDF -> rows). The card statement
IS the definition of operating expenses (Abhijeet 2026-07-08), so this closes
the M5 carry-forward ("card importer pending").

Two layers, payslip-style so the parser is fully testable without the PDF dep:
  - extract_text(path): thin pypdf wrapper (LAZY import — pypdf is a scoped dep
    like WeasyPrint; everything else is stdlib).
  - parse_text(text):   pure parsing -> statement meta + transaction rows.

Statement facts (Mayura/IDFC FIRST layout):
  - rows: 'dd/mm/yyyy DESC [Convert] [USD x.xx]  1,23,456.78 DR|CR' — the
    description can WRAP across text lines, so rows are matched date..DR/CR
    with DOTALL, never line-by-line.
  - SELF-VALIDATING reconcile: the summary block prints rupee amounts in a
    layout-dependent order, so we never trust positions — we search page-0
    amounts for the unique (opening, total) pair satisfying
        opening + ΣDR − ΣCR == total     (exact paise)
    Exactly one pair must exist or the import ABORTS (surfaced, never guessed).
  - money: Indian-format amounts -> integer paise via money.to_minor (no float).

Classification (bucket() applies the rest):
  - DR spends            -> category 'expense'  (source 'Card' -> opex)
  - CR 'Payment Received'-> category 'cc_settlement' (mirrors the bank-side
    bill payment leg — excluded from P&L on BOTH sides, never income)
  - other CR (reversals) -> category 'expense' with POSITIVE paise (opex offset)
"""
from __future__ import annotations

import hashlib
import re
from typing import List, Optional

from modules.finance import money

_TX = re.compile(
    r"(\d{2}/\d{2}/\d{4})\s+(.*?)\s+([\d,]+\.\d{2})\s*(DR|CR)", re.DOTALL)
_AMT = re.compile(r"([\d,]+\.\d{2})")
_PERIOD = re.compile(
    r"Statement Period:\s*(\d{2}/\w{3}/\d{4})\s*-\s*(\d{2}/\w{3}/\d{4})")
_CARD = re.compile(r"X{2,4}\s?(\d{4})")
_MONTHS = {m: i for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
_PAYMENT_HINTS = ("PAYMENT RECEIVED",)


def extract_text(path) -> List[str]:
    """Per-page text via pypdf (lazy scoped dep). Raises ImportError with a
    clear install hint when the dep is missing — surfaced, not swallowed."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:                   # pragma: no cover - env
        raise ImportError(
            "pypdf missing — /usr/bin/python3 -m pip install --user pypdf"
        ) from exc
    r = PdfReader(str(path))
    return [(p.extract_text() or "") for p in r.pages]


def _date_iso(ddmmyyyy: str) -> str:
    d, m, y = ddmmyyyy.split("/")
    return f"{y}-{m}-{d}"


def _named_date_iso(s: str) -> Optional[str]:
    d, mon, y = s.split("/")
    m = _MONTHS.get(mon[:3].title())
    return f"{y}-{m:02d}-{d}" if m else None


def _clean_desc(raw: str) -> str:
    d = re.sub(r"\s+", " ", raw).strip()
    d = re.sub(r"\s*\bConvert\b\s*", " ", d)     # EMI-eligibility column noise
    return re.sub(r"\s+", " ", d).strip(" ,")


def card_hash(card4: str, date: str, amount_minor: int, desc: str, occ: int) -> str:
    norm = f"{card4}|{date}|{money.from_minor(amount_minor)}|{desc.upper()}|{occ}"
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]


def parse_text(pages: List[str]) -> dict:
    """pages[] -> {card4, period_start, period_end, rows, summary_amounts,
    quarantine}. rows: date/desc/amount_minor (DR negative)/kind/canonical_key.
    Duplicate same-day same-amount same-desc rows get an occurrence index so
    the canonical key never collapses two real charges."""
    text = "\n".join(pages)
    card4 = None
    m = _CARD.search(text)
    if m:
        card4 = m.group(1)
    period_start = period_end = None
    m = _PERIOD.search(text)
    if m:
        period_start, period_end = _named_date_iso(m.group(1)), _named_date_iso(m.group(2))

    rows, quarantine, seen = [], [], {}
    for dm, raw_desc, amt, drcr in _TX.findall(text):
        desc = _clean_desc(raw_desc)
        if not desc:                              # never a real charge
            quarantine.append({"date": _date_iso(dm), "reason": "empty description",
                               "amount": amt, "drcr": drcr})
            continue
        paise = money.to_minor(amt.replace(",", ""))
        signed = -paise if drcr == "DR" else paise
        is_payment = any(h in desc.upper() for h in _PAYMENT_HINTS) and drcr == "CR"
        key = (dm, signed, desc.upper())
        occ = seen[key] = seen.get(key, 0) + 1
        rows.append({
            "date": _date_iso(dm), "description": desc,
            "amount_minor": signed, "drcr": drcr,
            "category": "cc_settlement" if is_payment else "expense",
            "canonical_key": "card:" + card_hash(card4 or "????", _date_iso(dm),
                                                 signed, desc, occ),
        })

    # page-0 amounts (the summary block) for the self-validating reconcile.
    # dr_tagged: amounts printed with a DR suffix — the balance-style figures
    # (Total Amount Due, Minimum Due). The true total MUST be one of these,
    # which breaks the payments/purchases degeneracy (they can satisfy the
    # same equation when a reversal makes both columns differ equally).
    page0 = pages[0] if pages else ""
    summary_amounts = [money.to_minor(a.replace(",", ""))
                       for a in _AMT.findall(page0)]
    dr_tagged = [money.to_minor(a.replace(",", "")) for a in
                 re.findall(r"([\d,]+\.\d{2})\s*DR", page0)]
    return {"card4": card4, "period_start": period_start,
            "period_end": period_end, "rows": rows,
            "summary_amounts": summary_amounts, "dr_tagged": dr_tagged,
            "quarantine": quarantine}


def reconcile(parsed: dict) -> dict:
    """Find the UNIQUE (opening, total_due) pair among the page-0 amounts with
        opening + ΣDR − ΣCR == total_due    (all integer paise; DR<0 in rows,
    so arithmetically: opening − Σsigned == total ... both statement sides are
    liabilities-positive). No pair or an ambiguous pair -> not ok (abort)."""
    flow = sum(r["amount_minor"] for r in parsed["rows"])   # DR neg, CR pos
    cands = sorted(set(parsed["summary_amounts"]))
    pairs = [(o, o - flow) for o in cands if (o - flow) in cands]
    # the total must be a DR-tagged (balance-style) figure when any are present
    dr = set(parsed.get("dr_tagged") or [])
    if dr:
        pairs = [(o, t) for o, t in pairs if t in dr]
    if len(pairs) == 1:
        o, t = pairs[0]
        return {"ok": True, "opening_minor": o, "total_due_minor": t,
                "flow_minor": flow}
    return {"ok": False, "pairs_found": len(pairs), "flow_minor": flow}
