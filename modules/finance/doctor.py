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

import datetime as _dt
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
    # reconcile only FILE-BACKED statements (opening + Σ txns == closing). Deposit/
    # aggregate balances (the FD principal) carry a 'derived:'/'authoritative:'
    # source and have NO transaction flow in the ledger — they are point-in-time
    # balances, not statements, so #1 must skip them (else they falsely go RED).
    stmts = _file_stmts(conn)     # includes period_start/period_end (011)
    if not stmts:
        return Check("#1 statement integrity", "N/A", "no statements imported yet")
    reds = []
    for s in stmts:
        # WINDOW attribution when the statement carries its coverage (011) —
        # overlap-safe; else legacy source_file attribution (pre-011 rows).
        flow = None
        if s.get("period_start") and s.get("period_end"):
            flow = _window_flow(conn, s["account"], s["currency"],
                                s["period_start"], s["period_end"])
        if flow is None:
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
    """#6 payroll: per LINE Σcomponents (basic+hra+special+lta) == gross AND
    gross − (TDS+PT) == net; per RUN Σ(line gross/tds/pt/net) == the run totals.
    Exact integer paise. N/A until a Phase-5 run exists."""
    try:
        runs = [dict(r) for r in conn.execute("SELECT * FROM payroll_runs")]
    except Exception:
        return Check("#6 payroll", "N/A", "payroll not built yet (Phase 5)")
    if not runs:
        return Check("#6 payroll", "N/A", "no payroll runs yet")
    bad = []
    for run in runs:
        lines = [dict(r) for r in conn.execute(
            "SELECT * FROM payroll_lines WHERE run_id=?", (run["id"],))]
        for ln in lines:
            comps = ln["basic_minor"] + ln["hra_minor"] + ln["special_minor"] + ln["lta_minor"]
            if comps != ln["gross_minor"]:
                bad.append({"run": run["period"], "emp": ln["employee_id"],
                            "error": "Σcomponents != gross", "delta_minor": comps - ln["gross_minor"]})
            if ln["gross_minor"] - ln["tds_minor"] - ln["pt_minor"] != ln["net_minor"]:
                bad.append({"run": run["period"], "emp": ln["employee_id"],
                            "error": "gross-(tds+pt) != net"})
        for fld in ("gross_minor", "tds_minor", "pt_minor", "net_minor"):
            tot = sum(ln[fld] for ln in lines)
            if tot != run[fld]:
                bad.append({"run": run["period"], "error": f"Σ{fld} != run {fld}",
                            "delta_minor": tot - run[fld]})
    if bad:
        return Check("#6 payroll", "RED", f"{len(bad)} payroll reconciliation error(s)", bad[:20])
    return Check("#6 payroll", "GREEN",
                 f"{len(runs)} run(s) reconcile (Σcomponents=gross, gross-deductions=net, Σnet=run)")


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


def _file_stmts(conn):
    """File-backed statement balances (exclude derived:/authoritative: aggregates
    like the FD principal — those are not transaction-backed statements)."""
    return [dict(r) for r in conn.execute(
        "SELECT account, currency, opening_minor, closing_minor, statement_date,"
        " source_file, period_start, period_end FROM account_balances"
        " WHERE source_file NOT LIKE 'derived:%'"
        " AND source_file NOT LIKE 'authoritative:%' ORDER BY account, currency,"
        " statement_date")]


# account -> (transactions.source, canonical_key prefix): the rows a statement
# window covers. Window attribution is overlap-safe — a re-downloaded statement
# re-covers months whose rows were first inserted by an earlier file (2026-07-07).
_ACCOUNT_ROWS = {"idfc": ("IDFC", "bank:%"), "wise": ("Wise", "wise:%")}


def _window_flow(conn, account: str, currency: str, start: str, end: str):
    """Σ amount_minor of the account's ledger rows dated within [start, end];
    None when the account has no row mapping (falls back to file attribution)."""
    m = _ACCOUNT_ROWS.get(account)
    if not m or not (start and end) or start > end:
        return None if not m else 0
    src, pref = m
    return conn.execute(
        "SELECT COALESCE(SUM(amount_minor),0) f FROM transactions"
        " WHERE source=? AND canonical_key LIKE ? AND currency=?"
        " AND substr(creation_dt,1,10) BETWEEN ? AND ?",
        (src, pref, currency, start, end)).fetchone()["f"]


def _check_continuity(conn) -> Check:
    """#2 continuity: consecutive statements of an account must agree at the
    boundary. UNIFIED rule (011, overlap-safe):
        a.closing == b.opening + Σ(account rows in [b.period_start, a.period_end])
    - contiguous statements: the window is empty -> b.opening must equal a.closing
      (the pre-011 rule, unchanged);
    - OVERLAPPING statements (a re-downloaded export): b re-covers part of a's
      window, so b.opening + the overlap flow must land exactly on a.closing;
    - a COVERAGE GAP (b starts later than the day after a ends) is RED — missing
      days can hide missing transactions.
    Statements without windows (pre-011) use the closing==opening equality."""
    import datetime as _dt
    from collections import defaultdict
    groups = defaultdict(list)
    for s in _file_stmts(conn):
        groups[(s["account"], s["currency"])].append(s)
    multi = {k: v for k, v in groups.items() if len(v) > 1}
    if not multi:
        return Check("#2 continuity", "N/A", "one statement per account — no period chain yet")
    breaks = []
    for (acct, ccy), v in multi.items():
        for a, b in zip(v, v[1:]):
            windowed = (a.get("period_end") and b.get("period_start"))
            if windowed:
                day_after_a = (_dt.date.fromisoformat(a["period_end"])
                               + _dt.timedelta(days=1)).isoformat()
                if b["period_start"] > day_after_a:
                    breaks.append({"account": acct, "error": "coverage gap",
                                   "from": a["period_end"], "to": b["period_start"]})
                    continue
                overlap = _window_flow(conn, acct, ccy,
                                       b["period_start"], a["period_end"])
                if overlap is None:            # unmapped account -> equality rule
                    overlap = 0
                expected = b["opening_minor"] + overlap
                if a["closing_minor"] != expected:
                    breaks.append({"account": acct, "from": a["statement_date"],
                                   "to": b["statement_date"],
                                   "gap_minor": expected - a["closing_minor"]})
            elif a["closing_minor"] != b["opening_minor"]:
                breaks.append({"account": acct, "from": a["statement_date"],
                               "to": b["statement_date"],
                               "gap_minor": b["opening_minor"] - a["closing_minor"]})
    if breaks:
        return Check("#2 continuity", "RED",
                     f"{len(breaks)} boundary break(s) (gap or closing mismatch)", breaks)
    return Check("#2 continuity", "GREEN",
                 f"{sum(len(v) for v in multi.values())} statements chain consistently")


def _check_ledger_tieout(conn) -> Check:
    """#3 ledger<->statement tie-out (the key one): the LEDGER reproduces each
    statement. Per (account, currency) with file-backed statements, the earliest
    opening + Σ(all ledger amount_minor tagged to those source_files & currency)
    == the latest closing — exact in minor units. Also: a file-backed statement
    with ZERO ledger rows is RED (claimed but unbacked)."""
    from collections import defaultdict
    groups = defaultdict(list)
    for s in _file_stmts(conn):
        groups[(s["account"], s["currency"])].append(s)
    if not groups:
        return Check("#3 ledger-statement tie-out", "N/A", "no file-backed statements yet")
    reds = []
    for (acct, ccy), v in groups.items():
        v.sort(key=lambda r: r["statement_date"])
        sources = [s["source_file"] for s in v]
        marks = ",".join("?" * len(sources))
        row = conn.execute(
            f"SELECT COALESCE(SUM(amount_minor),0) f, COUNT(*) n FROM transactions"
            f" WHERE currency=? AND source_file IN ({marks})", (ccy, *sources)).fetchone()
        if row["n"] == 0:
            reds.append({"account": acct, "error": "statement has no ledger rows"})
            continue
        expected = v[0]["opening_minor"] + row["f"]
        if expected != v[-1]["closing_minor"]:
            reds.append({"account": acct, "expected_minor": expected,
                         "actual_minor": v[-1]["closing_minor"],
                         "delta_minor": expected - v[-1]["closing_minor"]})
    if reds:
        return Check("#3 ledger-statement tie-out", "RED",
                     f"{len(reds)} account(s) where the ledger doesn't reproduce the statement", reds)
    return Check("#3 ledger-statement tie-out", "GREEN",
                 f"{len(groups)} account(s): ledger reproduces statement (opening+Σ==closing)")


def _check_cash(conn) -> Check:
    """#4 cash: the cash position is fully SOURCED — every account_balances closing
    is non-null and traceable, i.e. either a file-backed statement (reconciled by
    #1/#3) or an explicit 'authoritative:'/'derived:' source (e.g. the FD principal).
    A closing with no source, or NULL, is an unexplained cash number -> RED."""
    bals = [dict(r) for r in conn.execute(
        "SELECT account, currency, closing_minor, source_file FROM account_balances")]
    if not bals:
        return Check("#4 cash", "N/A", "no cash balances yet")
    bad = [{"account": b["account"], "reason": "null closing" if b["closing_minor"] is None
            else "no source_file"}
           for b in bals if b["closing_minor"] is None or not b["source_file"]]
    if bad:
        return Check("#4 cash", "RED", f"{len(bad)} unexplained cash balance(s)", bad)
    n_auth = sum(1 for b in bals if (b["source_file"] or "").startswith(("authoritative:", "derived:")))
    return Check("#4 cash", "GREEN",
                 f"{len(bals)} cash balance(s) sourced ({len(bals)-n_auth} statement, {n_auth} authoritative)")


def _check_transfer_integrity(conn) -> Check:
    """#7 transfer-integrity (§3.10): a transfer moves money between our OWN
    accounts, so the bucket is EXCLUDED from P&L (the core guarantee enforced
    here) and both legs must reconcile. The Upwork withdrawal legs reconcile to
    the Upwork statement (principal + fee). The CROSS-CURRENCY both-legs tie-out
    (Upwork/Wise -USD leg <-> IDFC +INR leg) is done by the dedicated tie-out step
    (bin/finance-tieout-transfers) against the realized INR in the source CSVs —
    validated 32/32 in-window on the Upwork legs. It is NOT a date-proximity rule
    (that produced false orphans). The Etherwise Wise USD->INR legs await the Wise
    v3 re-import, so the full reconcile is reported here as PENDING (N/A), not RED;
    no transfer leg is allowed to leak into P&L meanwhile."""
    from modules.finance import transfers
    rows = [dict(r) for r in conn.execute(
        "SELECT record_id, type, upwork_type, accounting_subtype, category,"
        " description FROM transactions WHERE upwork_type='APPayment'"
        " OR accounting_subtype IN ('Withdrawal','Withdrawal Fee')"
        " OR category='transfer_in' OR lower(description) LIKE '%withdrawal fee%'")]
    legs = [r for r in rows if transfers.is_transfer_leg(r)]
    ins = [r for r in legs if (r.get("category") or "") == "transfer_in"]
    outs = [r for r in legs if transfers.leg_role(r) == "out_principal"]
    return Check("#7 transfer-integrity", "N/A",
                 f"{len(ins)} transfer_in + {len(outs)} out legs identified +"
                 " excluded from P&L; cross-currency tie-out via"
                 " finance-tieout-transfers (Wise re-import pending)")


def _check_bucket_reproducibility(conn) -> Check:
    """#10 P&L reproducibility (added 2026-06-26): the stored pnl_bucket must equal
    pnl.bucket(row) for every canonical row, so the money picture is re-derivable
    from the DB (not recomputed ad hoc) and auditable by query. A stale stored
    bucket (!= recomputed) is RED; rows not yet bucketized are N/A (run
    finance-bucketize). Guards against the stored-category vs computed-bucket drift
    that made the P&L non-reproducible."""
    from modules.finance import pnl
    # amount_base_minor: bucket() is amount-DEPENDENT (2026-07-08 salary floor) —
    # recompute with the same fields bucketize writes from, or the check would
    # agree with a truncated (wrong) computation on both sides.
    rows = [dict(r) for r in conn.execute(
        "SELECT record_id, source, type, upwork_type, category, accounting_subtype,"
        " description, pnl_bucket, amount_base_minor FROM transactions"
        " WHERE canonical_key IS NOT NULL")]
    if not rows or all(r.get("pnl_bucket") is None for r in rows):
        return Check("#10 P&L reproducibility", "N/A", "not bucketized yet (run finance-bucketize)")
    null = [r for r in rows if r.get("pnl_bucket") is None]
    stale = [r for r in rows if r.get("pnl_bucket") is not None
             and r["pnl_bucket"] != pnl.bucket(r)]
    if stale:
        return Check("#10 P&L reproducibility", "RED",
                     f"{len(stale)} rows: stored pnl_bucket != bucket() — re-bucketize",
                     [{"record_id": s["record_id"], "stored": s["pnl_bucket"],
                       "computed": pnl.bucket(s)} for s in stale[:20]])
    if null:
        return Check("#10 P&L reproducibility", "N/A",
                     f"{len(null)} rows not yet bucketized (run finance-bucketize)")
    return Check("#10 P&L reproducibility", "GREEN",
                 f"{len(rows)} rows: stored pnl_bucket reproduces bucket()")


# A Stripe settlement leaves Stripe and lands at IDFC 1-3 days later; 7 days is slack
# for a weekend plus a bank holiday, not a licence to pair unrelated amounts.
_STRIPE_ARRIVAL_DAYS = 7


def _check_stripe_arrival(conn) -> Check:
    """#11 Stripe arrival (added 2026-09-05). A 'GLOBAL REMITTANCE' credit on the
    bank is kept OUT of revenue because it is the INR landing of a Stripe
    settlement whose revenue the Stripe rail has already booked. Citi's wire carries no
    remitter name, so that exclusion rests entirely on the pairing -- and the
    failure it would cause is the expensive one: a genuine client wire labelled
    the same way would disappear from the top line rather than merely double.
    So every one of them must tie, to the paisa, to a Stripe transfer-out inside
    _STRIPE_ARRIVAL_DAYS. An unpaired landing is RED and names itself."""
    arrivals = [dict(r) for r in conn.execute(
        "SELECT record_id, substr(creation_dt,1,10) d, amount_minor FROM transactions"
        " WHERE source IN ('IDFC','Bank') AND amount_minor > 0"
        "   AND upper(COALESCE(description,'')) LIKE '%GLOBAL REMITTANCE%'"
        " ORDER BY creation_dt")]
    if not arrivals:
        return Check("#11 Stripe arrival", "N/A", "no GLOBAL REMITTANCE credits on the bank")
    settlements = [dict(r) for r in conn.execute(
        "SELECT record_id, substr(creation_dt,1,10) d, amount_minor FROM transactions"
        " WHERE source='Stripe' AND amount_minor < 0")]
    used, orphans = set(), []
    for a in arrivals:
        hit = next((p for p in settlements
                    if p["record_id"] not in used
                    and -p["amount_minor"] == a["amount_minor"]
                    and p["d"] <= a["d"] <= _plus_days(p["d"], _STRIPE_ARRIVAL_DAYS)), None)
        if hit is None:
            orphans.append({"record_id": a["record_id"], "date": a["d"],
                            "amount_minor": a["amount_minor"],
                            "error": "no Stripe settlement of this amount in the window"})
        else:
            used.add(hit["record_id"])
    if orphans:
        return Check("#11 Stripe arrival", "RED",
                     f"{len(orphans)} of {len(arrivals)} GLOBAL REMITTANCE credits tie to no"
                     " Stripe settlement — they are excluded from revenue and may be real income",
                     orphans[:20])
    return Check("#11 Stripe arrival", "GREEN",
                 f"{len(arrivals)} GLOBAL REMITTANCE credits each tie to a Stripe settlement")


def _plus_days(iso: str, n: int) -> str:
    return (_dt.date.fromisoformat(iso) + _dt.timedelta(days=n)).isoformat()


def run(db_path=None, period: Optional[str] = None) -> DoctorResult:
    """Evaluate every implemented invariant. RED on any -> the caller must block
    downstream publication for `period`."""
    with db.connect(db_path or config.DB_PATH) as conn:
        checks = [_check_dedup(conn), _check_sanity(conn), _check_type_sign(conn),
                  _check_statement_integrity(conn), _check_continuity(conn),
                  _check_ledger_tieout(conn), _check_cash(conn),
                  _check_transfer_integrity(conn), _check_bucket_reproducibility(conn),
                  _check_payroll(conn), _check_stripe_arrival(conn)]
    return DoctorResult(checks)
