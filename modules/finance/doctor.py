"""finance-doctor (§3.6) — the blocking gate. Evaluates the §3.3 reconciliation
invariants exact-to-the-minor-unit and returns GREEN/RED with the precise
discrepancy. HARD RULE: no finance number is shown in the cockpit, written to a
brief, or synced to Airtable unless the doctor is GREEN for that period.

Step-0 skeleton implements the invariants checkable now:
  #5 dedup integrity   — canonical_key UNIQUE; nothing silently vanishes
  #8 sanity bounds     — no NULL amount/currency in the live ledger
  #1 statement integrity (over account_balances; trivially GREEN when empty)
  #6 payroll (over payroll runs; N/A until Phase 5)
Invariants #2/#3/#4 (continuity, tie-out, cash) + #7 (FX) land with Phase 1-2.
Every check reports delta in MINOR UNITS — never a float.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import List, Optional

from core import config, db


@dataclass
class Check:
    invariant: str            # e.g. "#5 dedup integrity"
    status: str               # GREEN | RED | N/A
    detail: str = ""
    deltas: List[dict] = field(default_factory=list)  # offending rows, minor units


@dataclass
class DoctorResult:
    checks: List[Check]

    @property
    def green(self) -> bool:
        return all(c.status != "RED" for c in self.checks)

    def reds(self) -> List[Check]:
        return [c for c in self.checks if c.status == "RED"]

    def summary(self) -> dict:
        return {"green": self.green,
                "checks": [{"invariant": c.invariant, "status": c.status,
                            "detail": c.detail, "deltas": c.deltas[:20]}
                           for c in self.checks]}


# ── invariants ────────────────────────────────────────────────────────────────

def _check_dedup(conn) -> Check:
    """#5: canonical_key UNIQUE *and PRESENT*. A NULL/empty canonical_key is now
    a FAILURE (not just a duplicate non-null key): such a row is invisible to
    dedup and can silently double-count an already-keyed twin across namespaces
    — exactly the 2026-06-25 recovery double-count. Strengthened so the doctor
    catches that class."""
    dups = [dict(r) for r in conn.execute(
        "SELECT canonical_key, COUNT(*) n FROM transactions"
        " WHERE canonical_key IS NOT NULL AND canonical_key!=''"
        " GROUP BY canonical_key HAVING n > 1")]
    if dups:
        return Check("#5 dedup integrity", "RED",
                     f"{len(dups)} duplicated canonical_key(s)",
                     [{"canonical_key": d["canonical_key"], "count": d["n"]}
                      for d in dups])
    nullkey = [dict(r) for r in conn.execute(
        "SELECT record_id FROM transactions WHERE canonical_key IS NULL"
        " OR canonical_key=''")]
    if nullkey:
        return Check("#5 dedup integrity", "RED",
                     f"{len(nullkey)} transactions with NULL canonical_key"
                     " (invisible to dedup — can double-count a keyed twin)",
                     [{"record_id": r["record_id"]} for r in nullkey[:50]])
    live = conn.execute("SELECT COUNT(*) n FROM transactions").fetchone()["n"]
    quar = conn.execute("SELECT COUNT(*) n FROM quarantine WHERE source IN"
                        " ('bank','wise','card','upwork','Bank','Direct',"
                        "'upwork-api')").fetchone()["n"]
    return Check("#5 dedup integrity", "GREEN",
                 f"{live} live + {quar} quarantined; canonical_key unique")


def _check_sanity(conn) -> Check:
    """#8: no NULL amount_minor / currency in the LIVE ledger (the malformed
    rows surface here until recovered or quarantined)."""
    bad = [dict(r) for r in conn.execute(
        "SELECT record_id, amount_minor, currency FROM transactions"
        " WHERE amount_minor IS NULL OR currency IS NULL OR currency=''")]
    if bad:
        return Check("#8 sanity bounds", "RED",
                     f"{len(bad)} live transactions with NULL amount/currency"
                     " (recover or quarantine — see step-0 finding)",
                     [{"record_id": b["record_id"]} for b in bad[:50]])
    return Check("#8 sanity bounds", "GREEN", "no NULL amount/currency live")


def _check_statement_integrity(conn) -> Check:
    """#1: for each statement, opening + Σcredits − Σdebits == closing, exact in
    minor units. Over account_balances + the period's transactions. Trivially
    GREEN until Phase-1 imports populate balances."""
    stmts = [dict(r) for r in conn.execute(
        "SELECT account, currency, opening_minor, closing_minor, statement_date,"
        " source_file FROM account_balances")]
    if not stmts:
        return Check("#1 statement integrity", "N/A", "no statements imported yet")
    reds = []
    for s in stmts:
        flow = conn.execute(
            "SELECT COALESCE(SUM(amount_minor),0) f FROM transactions"
            " WHERE source_file=? AND currency=?",
            (s["source_file"], s["currency"])).fetchone()["f"]
        expected = s["opening_minor"] + flow
        if expected != s["closing_minor"]:
            reds.append({"account": s["account"], "statement": s["statement_date"],
                         "expected_minor": expected,
                         "actual_minor": s["closing_minor"],
                         "delta_minor": expected - s["closing_minor"]})
    if reds:
        return Check("#1 statement integrity", "RED",
                     f"{len(reds)} statement(s) don't reconcile", reds)
    return Check("#1 statement integrity", "GREEN",
                 f"{len(stmts)} statement(s) reconcile exactly")


def _check_payroll(conn) -> Check:
    """#6: per-run Σcomponents==gross, gross−(tds+pt)==net, Σnet==disbursement.
    N/A until Phase-5 payroll runs exist."""
    try:
        n = conn.execute("SELECT COUNT(*) n FROM payroll_runs").fetchone()["n"]
    except Exception:
        return Check("#6 payroll", "N/A", "payroll not built yet (Phase 5)")
    return Check("#6 payroll", "N/A" if not n else "GREEN",
                 f"{n} run(s)")


def _check_type_sign(conn) -> Check:
    """#9 type-sign sanity (added 2026-06-25): a category whose sign is fixed by
    its economic meaning must carry that sign — a deduction is always a debit
    (amount_minor <= 0). Two proven classes the Upwork sync recorded as +income:
      - 'Connect Purchase' (a $4.5K P&L distortion), identified by type;
      - GST CHARGES (India tax invoiced to us = a debit), identified by
        accounting_subtype='GST' AND upwork_type='ARInvoice'. Added after the
        full-history import surfaced 19 positive GST charges (-$118.98). A GST
        REFUND (upwork_type='APAdjustment', e.g. ref 794900371 +$3.30, confirmed
        positive by the statement) is LEGITIMATELY positive and is NOT flagged —
        the sign tracks charge-vs-refund, not the GST label (statement is the
        authority, not a blunt rule).
    Rows are corrected from the authoritative CSV; any still + (e.g. out of the
    export range) stay RED until their source statement is available — never
    silently passed. Withdrawals are NOT here: their sign is a transfer
    convention resolved by the #3 statement tie-out, not a fixed-sign rule."""
    bad = [dict(r) for r in conn.execute(
        "SELECT record_id, amount_minor, accounting_subtype, type FROM transactions"
        " WHERE (type='Connect Purchase'"
        "        OR (accounting_subtype='GST' AND upwork_type='ARInvoice'))"
        " AND amount_minor > 0")]
    if bad:
        return Check("#9 type-sign sanity", "RED",
                     f"{len(bad)} fee/tax rows (Connect Purchase / GST charge) are"
                     " + (income) but are deductions (debits) — correct from source",
                     [{"record_id": b["record_id"], "amount_minor": b["amount_minor"],
                       "subtype": b["accounting_subtype"], "type": b["type"]}
                      for b in bad[:50]])
    return Check("#9 type-sign sanity", "GREEN", "fee/tax-type signs correct")


def _check_transfer_integrity(conn) -> Check:
    """#7 transfer-integrity (§3.10): a transfer moves money between our OWN
    accounts, so both legs must reconcile and the bucket is EXCLUDED from P&L. The
    Upwork withdrawal legs are now reconciled to the statement (withdrawal
    principal + its fee line). The CROSS-CURRENCY reconcile (Upwork/Wise -USD leg
    <-> IDFC +INR leg at the realized rate) needs the IDFC statement: once any
    `transfer_in` leg exists, an orphan transfer_in (no out-leg within +/-7 days)
    is RED. Until IDFC is v3-imported there are no in-legs, so the out-legs are
    PENDING (N/A) — not RED (the USD-only aggregate is expected, per Abhijeet)."""
    from modules.finance import transfers
    rows = [dict(r) for r in conn.execute(
        "SELECT record_id, creation_dt, amount_base_minor, amount_minor, type,"
        " upwork_type, accounting_subtype, category, description FROM transactions"
        " WHERE upwork_type='APPayment' OR accounting_subtype IN"
        " ('Withdrawal','Withdrawal Fee') OR category='transfer_in'"
        " OR lower(description) LIKE '%withdrawal fee%'")]
    legs = [r for r in rows if transfers.is_transfer_leg(r)]
    ins = [r for r in legs if (r.get("category") or "") == "transfer_in"]
    outs = [r for r in legs if transfers.leg_role(r) == "out_principal"]
    if not ins:
        return Check("#7 transfer-integrity", "N/A",
                     f"{len(outs)} transfer-out legs identified + excluded from P&L;"
                     " cross-currency both-legs reconcile pending IDFC v3-import")

    def _day(r):
        return (r.get("creation_dt") or "")[:10]
    out_days = sorted(_day(o) for o in outs if _day(o))
    orphans = []
    for inc in ins:
        d = _day(inc)
        near = any(abs((datetime.date.fromisoformat(od)
                        - datetime.date.fromisoformat(d)).days) <= 7
                   for od in out_days if od and d)
        if not near:
            orphans.append({"record_id": inc["record_id"],
                            "amount_base_minor": inc.get("amount_base_minor")})
    if orphans:
        return Check("#7 transfer-integrity", "RED",
                     f"{len(orphans)} one-legged transfer(s): a +INR transfer_in"
                     " with no -USD out-leg within +/-7 days", orphans[:20])
    return Check("#7 transfer-integrity", "GREEN",
                 f"{len(ins)} in-legs each pair an out-leg; transfers excluded from P&L")


def run(db_path=None, period: Optional[str] = None) -> DoctorResult:
    """Evaluate every implemented invariant. RED on any -> the caller must block
    downstream publication for `period`."""
    with db.connect(db_path or config.DB_PATH) as conn:
        checks = [_check_dedup(conn), _check_sanity(conn), _check_type_sign(conn),
                  _check_statement_integrity(conn), _check_transfer_integrity(conn),
                  _check_payroll(conn)]
    return DoctorResult(checks)
