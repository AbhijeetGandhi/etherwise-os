"""IDFC First Bank XLSX statement importer (INR account 'Etherwise', 60606200234).

Ported from v2 etherwise_os/webapp/bank_import.py as the spec, with the v3
correctness regime applied and ONE structural change the regime forces
(approved 2026-06-25):

  v2 DROPPED rows at import (internal sweeps, WISE/PAYMENT-ESCROW credits,
  RazorpaySo CC-bill settlements [detection strings, not payment calls], owner
  ATM/draws) to keep its flat P&L from double-counting. v3 CANNOT drop them:
    * statement-integrity (#1): the `Balance` column is a running balance AFTER
      every row, so a dropped row breaks `balance[i] +/- amount == balance[i+1]`;
    * transfer-integrity (Phase 2): the WISE credit IS the INR leg of a Wise
      `transfer_out`, and PAYMENT ESCROW IS the INR leg of an Upwork withdrawal.
  So v3 imports EVERY row and CLASSIFIES it (`category`); the double-count is
  prevented at the P&L-bucketing + transfer-integrity layer instead of by
  deletion. Nothing is dropped; a malformed row quarantines, never vanishes.

Amounts are signed INTEGER paise (debit negative, credit positive) via
money.to_minor (Decimal, no float). currency='INR'; the USD presentation amount
(amount_base_minor) is pinned separately by fx.pin_base on the value date and is
NOT computed here (the parser is pure / network-free / deterministic).

Canonical key `bank:<hash16>` reproduces v2's md5(date|signed-amount-2dp|DESC)
EXACTLY (computed from Decimal, never float) so re-imports dedup against the
already-migrated legacy bank rows.

The IDFC xlsx (learned from real statements): 13 metadata rows, a header row
(`Transaction Date`), transaction rows, then a `Total` row. Columns:
Transaction Date | Value Date | Particulars | Cheque No | Debit | Credit | Balance.
"""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, List, Optional

from modules.finance import money
from modules.finance.importers import statement

NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

_MONTH = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}


# ── stdlib xlsx reader (no openpyxl) ────────────────────────────────────────

def _col_letter_to_index(letters: str) -> int:
    """A->0, B->1, ... Z->25, AA->26 ..."""
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _read_xlsx_rows(path: Path | str | bytes) -> List[List[str]]:
    """Every cell of sheet1 as list-of-rows-of-strings (stdlib zipfile+ElementTree).

    Honours each cell's `r` ref (e.g. 'F12') so a row that omits a column has that
    position filled with '' instead of shifting later cells left — otherwise Credit
    lands in the Balance slot on debit-only rows.
    """
    src: Any = path
    if isinstance(path, bytes):
        import io
        src = io.BytesIO(path)
    rows: List[List[str]] = []
    with zipfile.ZipFile(src) as z:
        strings: List[str] = []
        try:
            with z.open("xl/sharedStrings.xml") as f:
                for si in ET.parse(f).getroot().findall("main:si", NS):
                    t = si.find(".//main:t", NS)
                    strings.append(t.text or "" if t is not None else "")
        except KeyError:
            pass
        with z.open("xl/worksheets/sheet1.xml") as f:
            for row in ET.parse(f).getroot().findall(".//main:row", NS):
                positioned: dict[int, str] = {}
                max_col = -1
                for i, c in enumerate(row.findall("main:c", NS)):
                    t = c.get("t", "")
                    v = c.find("main:v", NS)
                    if v is None:
                        val = ""
                    elif t == "s":
                        val = strings[int(v.text)] if v.text else ""
                    else:
                        val = v.text or ""
                    ref = c.get("r", "")
                    m = re.match(r"([A-Z]+)\d+", ref)
                    col_idx = _col_letter_to_index(m.group(1)) if m else i
                    positioned[col_idx] = val
                    if col_idx > max_col:
                        max_col = col_idx
                rows.append([positioned.get(i, "") for i in range(max_col + 1)])
    return rows


def _iso_date(d: str) -> str:
    """'03-May-2026' -> '2026-05-03'. '' on bad input."""
    m = re.match(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", (d or "").strip())
    if not m:
        return ""
    day, mon, year = m.groups()
    return f"{year}-{_MONTH.get(mon, 0):02d}-{int(day):02d}"


# ── classification (v2 predicates kept as the spec; map to a v3 category) ───
# v3 imports these rows (does not drop them); category drives P&L bucketing.

def _is_internal_sweep(desc: str) -> bool:
    """Bank-side moves between savings and a fixed deposit — NOT economic events,
    they relocate our own cash into/out of FD principal (it stays our money). The
    auto-sweep pair ('Auto sweep in from' / 'Sweepout FD ... booked') plus MANUAL
    FD creation/closure ('FD <acct-no> ETHERWISE'). Missing the manual-FD pattern
    mis-booked 2 rows (-Rs 5L) as opex AND understated the FD/cash balance."""
    s = desc or ""
    return (s.startswith("Auto sweep in from") or "Sweepout FD" in s
            or bool(re.match(r"FD \d{6,}", s)))


def _is_already_tracked_credit(desc: str) -> bool:
    """Is this inbound credit the INR leg of a TRANSFER (already booked elsewhere),
    vs new client INCOME? The distinction is RAIL-based, NOT processor-name-based
    (Abhijeet 2026-06-26) — both Visa and Wise appear on the inward-remittance rail:
      - NEFT / ESCROW rail  = transfer_in: Upwork's direct remittance ('Upwork
        Escrow Inc' / 'PAYMENT ESCROW') and Etherwise's own Wise USD->INR transfer
        ('WISE PAYMENTS IPL ... ESCROW'). The earning/transfer is already on the
        Upwork/Wise ledger -> exclude from P&L.
      - IMPS / RDA-Vostro rail = INCOME: foreign client payments delivered via a
        Rupee Drawing Arrangement ('.../VISAPAYMENTSLIM/...', '.../WISEPAYMENTSLIM/
        ...RDAVostro'). These have NO matching self-transfer out-leg -> NOT a
        transfer. (Name-based matching wrongly split Wise-RDA from Visa-RDA.)
      - GLOBAL REMITTANCE rail = where a STRIPE SETTLEMENT LANDS (2026-09-05).
        Citi writes 'GLOBAL REMITTANCE-OTHERS/CITI0100000/388 GREENWICH ST.NEW
        YORK' and carries NO remitter name, so nothing in the text says Stripe.
        The money says it: five for five, to the paisa, 1-3 days after the
        Stripe rail's own transfer-out leg -- Rs 23,111.76 (8 Jul), 17,329.69
        (22 Jul), 14,574.08 (3 Aug), 56,103.57 (17 Aug), 50,786.06 (1 Sep).
        Stripe has ALREADY booked that money as revenue, so reading the landing
        as income counts it twice; the two July rows were corrected by hand at
        import and this is that correction made permanent (Rs 1,21,463.71 of
        August/September Stripe revenue would have doubled otherwise).
        THE DANGEROUS DIRECTION IS THE OTHER ONE -- a genuine client wire that
        Citi labels the same way would vanish from the top line instead of
        merely being counted twice -- so finance.doctor #11 re-proves the Stripe
        pairing for every one of these rows, and says so when it cannot."""
    s = (desc or "").upper()
    if "RDA" in s or "PAYMENTSLIM" in s:        # inward-remittance rail -> income
        return False
    return any(k in s for k in ("ESCROW", "WISE PAYMENTS", "TRANSFERWISE", "TW INDIA",
                                "GLOBAL REMITTANCE"))


def _is_credit_card_bill_payment(desc: str) -> bool:
    """Bank-side CC-bill settlement (Razorpay rail; detected here, never initiated);
    the card txns are booked via the Card source, so this row is the settlement,
    not new spend."""
    s = (desc or "").upper()
    return "RAZORPAYSO" in s or "RAZORPAY SO" in s   # inbound desc match, not a payment call


def _is_owner_cash_or_draw(desc: str) -> bool:
    """Owner equity moves (not business expense): ATM/cash + NEFT/IMPS to the
    owner's own account. Matches the FULL owner name so other Gandhi payees
    (e.g. payroll to family) are not swept up."""
    s = (desc or "").upper()
    if "CASH WITHDRAWAL" in s or "/ATM" in s or s.startswith("ATM"):
        return True
    return "ABHIJEET ASHOK GANDHI" in s or "ABHIJEET GANDHI" in s


def classify_category(desc: str, kind: str) -> str:
    """The v3 bucket for one IDFC row. Order mirrors v2's skip precedence.

      internal_sweep   -> excluded from P&L (bank balance mgmt; kept for reconcile)
      transfer_in      -> excluded; ties to a Wise transfer_out / Upwork withdrawal
      cc_settlement    -> excluded; ties to the Card statement total
      owner_draw       -> excluded (equity, not expense)
      salary           -> P&L (payroll spend)
      expense / income -> P&L
    """
    if _is_internal_sweep(desc):
        return "internal_sweep"
    if kind == "credit" and _is_already_tracked_credit(desc):
        return "transfer_in"
    if kind == "debit" and _is_credit_card_bill_payment(desc):
        return "cc_settlement"
    if _is_owner_cash_or_draw(desc):
        return "owner_draw"
    if kind == "debit" and re.search(r"\bSalary\b", desc, re.IGNORECASE):
        return "salary"
    return "expense" if kind == "debit" else "income"


# ── canonical key (matches legacy bank:<hash16>, computed without float) ────

def bank_hash(date: str, amount_minor: int, description: str) -> str:
    """16-hex md5(date|signed-amount-2dp|DESC.upper()) — byte-for-byte the v2
    format, but the amount string comes from Decimal (money.from_minor), never
    float, so legacy `bank:` rows dedup cleanly."""
    amt = money.from_minor(amount_minor)            # exact Decimal, 2dp
    norm = f"{date}|{amt:.2f}|{(description or '').strip().upper()}"
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]


def _minor(s: Optional[str]) -> Optional[int]:
    raw = str(s or "").replace(",", "").strip()
    if not raw:
        return None
    try:
        return money.to_minor(raw)                  # Decimal path, no float
    except Exception:
        return None


# ── parse ───────────────────────────────────────────────────────────────────

def parse(path_or_bytes: Path | str | bytes) -> dict:
    """Parse one IDFC xlsx -> {meta, rows, quarantine}.

    rows (file native order — authoritative for the running-balance reconcile):
      date, value_date, description, amount_minor (signed INR paise),
      balance_minor, currency='INR', kind ('debit'|'credit'), category,
      canonical_key 'bank:<hash16>'. amount_base_minor/fx pinned later by fx.pin_base.
    A row whose Debit/Credit/Balance won't parse is QUARANTINED (amount_minor=None
    so the reconcile also flags it), never silently dropped.
    """
    raw = _read_xlsx_rows(path_or_bytes)
    meta: dict[str, Any] = {}
    for r in raw[:13]:
        if not r:
            continue
        for i in range(0, len(r) - 1, 2):
            key = (r[i] or "").strip()
            val = (r[i + 1] or "").strip()
            if key == "ACCOUNT NUMBER":
                meta["account_number"] = val
            elif key == "CUSTOMER NAME":
                meta["customer_name"] = val
            elif key == "STATEMENT PERIOD":
                m = re.match(r"(\d{1,2}-[A-Za-z]{3}-\d{4})\s*TO\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", val)
                if m:
                    meta["period_start"] = _iso_date(m.group(1))
                    meta["period_end"] = _iso_date(m.group(2))

    header_idx = next((i for i, r in enumerate(raw)
                       if r and r[0] == "Transaction Date"), None)
    if header_idx is None:
        return {"meta": meta, "rows": [], "quarantine": []}

    end_idx = len(raw)
    for j in range(header_idx + 1, len(raw)):
        if raw[j] and raw[j][0] == "Total":
            end_idx = j
            if len(raw[j]) >= 3:
                meta["total_debit_minor"] = _minor(raw[j][1])
                meta["total_credit_minor"] = _minor(raw[j][2])
            break

    rows: List[dict] = []
    quarantine: List[dict] = []
    seen: dict = {}                  # base hash -> count, for collision suffixing
    for i, r in enumerate(raw[header_idx + 1:end_idx]):
        src_row = header_idx + 1 + i
        if not r or len(r) < 7:
            continue
        date, value_date, desc, _cheque, debit, credit, balance = r[:7]
        date_iso = _iso_date(date)
        if not date_iso:
            continue
        desc = (desc or "").strip()
        debit_minor = _minor(debit)
        credit_minor = _minor(credit)
        balance_minor = _minor(balance)
        # Malformed: a value present in Debit/Credit/Balance but unparseable.
        bad = ((debit and debit_minor is None)
               or (credit and credit_minor is None)
               or (balance and balance_minor is None))
        if bad:
            quarantine.append({"date": date_iso, "description": desc,
                               "reason": "unparseable amount/balance",
                               "raw": {"debit": debit, "credit": credit,
                                       "balance": balance}})
            rows.append({"date": date_iso, "value_date": _iso_date(value_date) or date_iso,
                         "description": desc, "amount_minor": None,
                         "balance_minor": balance_minor, "currency": "INR",
                         "kind": "unknown", "category": "quarantine",
                         "canonical_key": None})
            continue
        kind = "debit" if (debit_minor or 0) > 0 else "credit"
        amount_minor = (credit_minor or 0) - (debit_minor or 0)   # signed paise
        # Base hash is stable cross-statement (date|amount|desc). Same-day
        # IDENTICAL lines (e.g. 5x identical auto-sweeps) collide -> suffix the
        # repeats by occurrence (-1, -2, ...) so each distinct statement line is
        # its own row and none is silently dropped; the first occurrence keeps the
        # bare hash. Occurrence order is the file's native order -> stable + idempotent.
        base = bank_hash(date_iso, amount_minor, desc)
        n = seen.get(base, 0)
        seen[base] = n + 1
        ck = f"bank:{base}" if n == 0 else f"bank:{base}-{n}"
        rows.append({
            "date": date_iso,
            "value_date": _iso_date(value_date) or date_iso,
            "description": desc,
            "amount_minor": amount_minor,
            "balance_minor": balance_minor,
            "currency": "INR",
            "kind": kind,
            "category": classify_category(desc, kind),
            "canonical_key": ck,
            "source_row": src_row,
            "key": ck,                       # reconcile label
        })

    # Opening/closing for the account_balances tie-out (#1). Native order may be
    # oldest- or newest-first; derive both ends from the chain.
    dated = [r for r in rows if r.get("balance_minor") is not None
             and r.get("amount_minor") is not None]
    if dated:
        newest_first = (len(dated) < 2) or (dated[0]["date"] >= dated[-1]["date"])
        first, last = (dated[0], dated[-1])
        if newest_first:
            meta["closing_balance_minor"] = first["balance_minor"]
            meta["opening_balance_minor"] = last["balance_minor"] - last["amount_minor"]
        else:
            meta["opening_balance_minor"] = first["balance_minor"] - first["amount_minor"]
            meta["closing_balance_minor"] = last["balance_minor"]
    return {"meta": meta, "rows": rows, "quarantine": quarantine}


def reconcile_running_balance(rows: List[dict]) -> dict:
    """IDFC statement integrity (#1) — shared native-order reconcile."""
    return statement.reconcile_running_balance(rows)
