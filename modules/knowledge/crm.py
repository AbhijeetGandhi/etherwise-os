"""CRM numbers → cited facts. Deterministic (no model): pull a client's
contracts + transactions from v3 SQLite by its identifiers and emit CONFIRMED
facts citing the CRM as source. These feed the dossier's "The numbers" section.

MATCHING RUNS THROUGH registry.norm(), ON BOTH SIDES (2026-08-13)
----------------------------------------------------------------
This module used to compare raw SQL `LIKE '%<identifier>%'` against the raw
ledger columns. `norm()` never touched either side, so the whitespace fix that
landed in core/registry.py the same day did not reach here: Upwork writes
"Tanushant  Sharma" (TWO spaces) into `transactions.assignment_company` and
`contracts.client_company`, the registry holds one, and `%Tanushant Sharma%`
missed every row. His dossier compiled to 0 facts while the ledger held
$757.23 net across two ACTIVE contracts. Same bug hid Ippon Technology.

The fix registers `registry.norm` itself as the SQLite function `kbnorm()` and
normalises the needle with the SAME function. Why this shape and not another:

  * The predicate is `LIKE '%x%'` — a LEADING-wildcard scan. There is no index
    for it to lose, so wrapping the column in a function costs nothing in plan
    terms. A generated/indexed column would not be usable by this predicate
    either, and a write-time normalised column would need a migration plus an
    edit to every ledger writer (Upwork API sync, four CSV importers, Wise
    mail, Stripe) for a table this matcher reads once a day.
  * Calling `norm` itself — rather than hand-writing lower()/REPLACE()/TRIM()
    in SQL — means the matcher CANNOT drift from the registry. A SQL mirror of
    norm() would already have been stale once: norm() changed on 2026-08-13.
    It also brings NFKD accent-folding, which a pure-SQL mirror cannot do
    (the ledger spells 'Adélye', dossiers spell 'Adelye').
  * Measured on the live ledger (3,710 transactions × 77 clients × 198
    identifiers): the whole-roster compile spends ~0.7s in this SQL, against
    0.025s before. Once per kb_daily. Not a hot path.

BILLING LEDGER LABELS
---------------------
When Upwork masks a client it writes a placeholder into `assignment_company`
('S' for Saman Shafigh, 'no company' for Irene Buht). The registry records
these as `billing.ledger_label` and deliberately NOT as aliases, because the
alias index does free-text substring matching and a one-character token would
claim everything. So a ledger_label is matched here by EXACT equality on the
company column only — never `LIKE`, never against `description`. That reaches
$404.55 (Saman) and $440.50 (Irene) that were in the ledger and in no dossier.

GROSS **AND** NET — BOTH, FROM THE P&L'S OWN BUCKETS (2026-08-13)
-----------------------------------------------------------------
Abhijeet: *"I want dossier showing both gross and net so prepare it that way."*

This module used to emit ONE money line, and it was gross: it summed rows whose
`type` was an Upwork earning label. A dossier therefore claimed $859.49 for
Tanushant Sharma while $757.23 is what actually landed — the difference being
Upwork's service fee and Indian GST/WHT, which sit in the SAME ledger as their
OWN rows, carrying the SAME `assignment_company`.

Net is NOT computed by haircutting gross. It is the sum of the client's rows in
the P&L buckets `revenue + refund + platform_fee + tax`, taken from
`modules.finance.pnl.compute()` — the identical function `bin/finance-pnl` and
the cockpit Money tab call, on the identical `amount_base_minor` integers. So:

  * the classification cannot drift from the Money tab, because there is only
    one classifier. A local re-implementation ("fee rows look like X") would
    have been a second authority, and the P&L map changes (ARPayment became a
    transfer on 2026-07-30; a hand-rolled copy would still be counting it);
  * the fee is whatever Upwork actually charged, not 10%. Measured across the
    live roster it ranges 10.0% (most) to 30.0% (beglaubigt, an old contract on
    the pre-2024 tiered rate) — a fixed haircut would have been wrong for six
    clients and silently wrong forever;
  * GST is 18% OF THE FEE and WHT ~0.1% of earnings, both India-specific, both
    already their own rows. Nothing is modelled here at all.

Verified to the cent against the ledger: Tanushant $859.49 gross / $757.23 net,
Ippon $378.58 / $333.53, Saman $450.00 / $404.55, Irene $490.00 / $440.50.

GROSS IS NOW THE MONEY TAB'S OWN DEFINITION (`pnl_bucket='revenue'`)
--------------------------------------------------------------------
The old `type IN ('Hourly Earning','Fixed Earning','Bonus')` filter saw ONLY
Upwork. Every direct client — Wise/Stripe/bank — had its revenue reduced to
whatever it happened to bill through Upwork, so horizons-aba's dossier said
$22,694.76 while the Clients tab said $74,361.76 **on the same screen** (the
Knowledge panel cross-links `clients()['revenue_usd']` onto each dossier row).
Two numbers, one client, one page.

Both now read `pnl_bucket='revenue'`. Measured row-by-row over all 608 revenue
rows before shipping (var/agent-scratch/gn): 473 agree with the Money tab's own
`_client_key`, 72 differ only in the LABEL the Money tab prints (it renders
Hermon's Wise income under the payer "Consulting Systems" and Saman's masked
Upwork row under the job title; the registry unifies both), 3 are agency-CSV
rows the registry reaches and `_client_key` does not, 50 are rows the Money tab
attributes and the registry cannot (below). **Zero rows are claimed by two
clients.** The Upwork/direct split is printed inside the fact so the previous
figure stays visible and the change is auditable rather than silent.

WHAT STILL DOES NOT RECONCILE — stated, not smoothed over
---------------------------------------------------------
  * beglaubigt: the Money tab shows $5,885.85, this matcher reaches $921.67.
    The missing $4,964.18 is Stripe revenue whose ledger rows carry no string
    any beglaubigt identifier matches. UNDER-counted here, and left that way —
    inventing an identifier to close a number is how a wrong attribution
    becomes permanent.
  * platform_fee: $10,581.29 of the ledger's $20,210.60 is client-attributable.
    The rest is Upwork membership/Connects and bank-side Upwork POS charges —
    real costs, but costs of the AGENCY, not of any one client. They belong in
    the P&L (they are in it) and in no dossier.
  * tax: two Upwork-CSV rows totalling +$719.17 are bucketed `tax` by the P&L's
    last-resort description rule ("… · Withholding t…" on an earnings line).
    They carry no client we match, so they touch no dossier — but they are why
    the roster's attributed tax (-$875.94) exceeds the ledger's NET tax
    (-$240.45). A P&L-side bug, logged here, not worked around here.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from core import db, registry
from modules.finance import money, pnl
from modules.knowledge import kb, index

# columns a free-text identifier may match on
_CONTRACT_COLS = ("client_company", "job_title", "title")
_TXN_COLS = ("description", "assignment_company")
# the P&L buckets a CLIENT can be charged with. payroll/opex/owner_draw are
# agency-level and never attributable to one client; transfer/sweep/
# cc_settlement are non-economic and excluded from the P&L entirely.
_CLIENT_BUCKETS = ("revenue", "refund", "platform_fee", "tax")
# every column pnl.bucket()/transfers.is_transfer_leg() may read, so the rows
# handed to pnl.compute() are classified exactly as the Money tab classifies them
_PNL_COLS = ("record_id, type, upwork_type, accounting_subtype, category, source,"
             " description, pnl_bucket, amount_base_minor, creation_dt")


def _bind_norm(conn) -> None:
    """Expose registry.norm() to SQL as kbnorm(). Deterministic so SQLite may
    cache it within a statement. Non-TEXT cells are coerced, never crash the
    compile (SQLite columns are dynamically typed)."""
    conn.create_function(
        "kbnorm", 1,
        lambda v: registry.norm(v if isinstance(v, str)
                                else ("" if v is None else str(v))),
        deterministic=True)


def _needles(values: Optional[Sequence[str]]) -> List[str]:
    """Normalised, de-duplicated match keys. An identifier that normalises to
    the empty string is DROPPED: `LIKE '%%'` matches every row in the ledger,
    so a blank alias would silently hand one client the whole business."""
    out: List[str] = []
    seen = set()
    for v in values or []:
        k = registry.norm(v)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def crm_facts(db_path, client_id: str, client_name: str,
              identifiers: List[str],
              ledger_labels: Optional[Sequence[str]] = None) -> List[dict]:
    """Emit cited CRM facts for a client, matched by its identifiers against
    transaction description/company + contract fields (CRM-link is fuzzy until
    the clients mirror lands). `ledger_labels` are the registry's
    `billing.ledger_label` strings — matched EXACTLY on the company column."""
    ids = _needles(identifiers)
    labels = _needles(ledger_labels)
    if not ids and not labels:
        return []
    facts: List[dict] = []
    with db.connect(db_path) as conn:
        _bind_norm(conn)
        # contracts: any identifier in company/title/job fields (substring),
        # or a ledger label as the WHOLE company field (exact).
        cpreds: List[str] = []
        cargs: List[str] = []
        for n in ids:
            cpreds.append(" OR ".join(f"kbnorm({c}) LIKE ?"
                                      for c in _CONTRACT_COLS))
            cargs.extend([f"%{n}%"] * len(_CONTRACT_COLS))
        for lbl in labels:
            cpreds.append("kbnorm(client_company) = ?")
            cargs.append(lbl)
        cclause = " OR ".join(f"({p})" for p in cpreds)
        for c in conn.execute(
                f"SELECT id, title, job_title, upwork_status, hourly_rate"
                f" FROM contracts WHERE {cclause}", cargs):
            c = dict(c)
            rate = f" at ${c['hourly_rate']}/hr" if c["hourly_rate"] else ""
            facts.append({
                "category": "numbers",
                "fact_text": f"Contract: {c['title'] or c['job_title']}"
                             f" — status {c['upwork_status']}{rate}.",
                "citation": f"CRM:contracts/{c['id']}",
                "confidence": "CONFIRMED"})
        # transactions: any identifier in description/company (substring), or a
        # ledger label as the WHOLE company field (exact — 'S' as a substring of
        # a description would match half the ledger). No type/sign filter here:
        # the rows are classified by pnl.compute() below, which is what makes
        # this agree with the Money tab. `canonical_key IS NOT NULL` is the P&L's
        # own row filter (bin/finance-pnl) — same population, same numbers.
        tpreds: List[str] = []
        targs: List[str] = []
        for n in ids:
            tpreds.append(" OR ".join(f"kbnorm({c}) LIKE ?" for c in _TXN_COLS))
            targs.extend([f"%{n}%"] * len(_TXN_COLS))
        for lbl in labels:
            tpreds.append("kbnorm(assignment_company) = ?")
            targs.append(lbl)
        tclause = " OR ".join(f"({p})" for p in tpreds)
        rows = [dict(r) for r in conn.execute(
            f"SELECT {_PNL_COLS} FROM transactions"
            f" WHERE canonical_key IS NOT NULL AND ({tclause})", targs)]
    facts.extend(_money_facts(rows))
    return facts


def _deductions(refund: int, fee: int, tax: int) -> List[str]:
    """The human phrases for the three ways gross shrinks. Only NON-ZERO parts
    are named — an absent deduction is not rendered as '$0.00' (the cockpit's
    no-fabricated-zeros law: a zero must either be true and explained, or not
    shown). Amounts are printed positive; the word 'less' carries the sign."""
    out = []
    if refund:
        out.append(f"${money.fmt(abs(refund))} refunded")
    if fee:
        out.append(f"${money.fmt(abs(fee))} Upwork service fees")
    if tax:
        out.append(f"${money.fmt(abs(tax))} tax withheld (GST/WHT)")
    return out


def _money_facts(rows: List[dict]) -> List[dict]:
    """GROSS and NET as two separately-labelled cited facts, from one row set.

    Pure given `rows` (the client's matched ledger rows) so the arithmetic is
    unit-testable without a database. Emits NOTHING when the client has no
    revenue row: a fee-only match (hero-marketing-agency matches two Service Fee
    rows and no earning) must never render as "net -$75.00" — that is not what
    the client paid, it is an orphaned cost row, and it is left to the P&L.
    """
    if not rows:
        return []
    p = pnl.compute(rows)
    bb = p["by_bucket_minor"]
    gross = bb.get("revenue", 0)
    n_rev = p["counts"].get("revenue", 0)
    if not n_rev:
        return []
    refund, fee, tax = (bb.get("refund", 0), bb.get("platform_fee", 0),
                        bb.get("tax", 0))
    # net is the SUM of the client-attributable buckets, signs intact (fees and
    # taxes are already negative in the ledger). Never gross × a rate.
    net = sum(bb.get(b, 0) for b in _CLIENT_BUCKETS)

    rev_rows = [r for r in rows if (r.get("pnl_bucket") or pnl.bucket(r)) == "revenue"]
    dates = sorted(d[:10] for d in
                   (r.get("creation_dt") or "" for r in rev_rows) if d)
    span = f" ({dates[0]} → {dates[-1]})" if dates else ""
    # Upwork vs direct: keeps the pre-2026-08-13 figure (Upwork-only) visible
    # inside the new total, so nobody has to trust that the number grew fairly.
    upwork = sum(r.get("amount_base_minor") or 0 for r in rev_rows
                 if (r.get("source") or "").startswith("Upwork"))
    direct = gross - upwork
    split = (f" — Upwork ${money.fmt(upwork)}, direct ${money.fmt(direct)}"
             if upwork and direct else "")

    cut = _deductions(refund, fee, tax)
    if cut:
        tail = f"gross ${money.fmt(gross)} less " + ", ".join(cut) + "."
    else:
        tail = ("the same as gross; the ledger carries no fee, tax or refund"
                " rows for this client.")
    plural = "" if n_rev == 1 else "s"
    return [
        {"category": "numbers",
         "fact_text": f"Revenue to date (GROSS, billed): ${money.fmt(gross)}"
                      f" across {n_rev} revenue transaction{plural}"
                      f"{span}{split}.",
         "citation": "CRM:transactions[pnl_bucket=revenue]",
         "confidence": "CONFIRMED"},
        {"category": "numbers",
         "fact_text": f"Revenue to date (NET, received after Upwork fees and"
                      f" tax): ${money.fmt(net)} — {tail}",
         "citation": "CRM:transactions"
                     "[pnl_bucket=revenue+refund+platform_fee+tax]",
         "confidence": "CONFIRMED"},
    ]


def ingest_crm(db_path, client_id: str, client_name: str,
               identifiers: List[str],
               ledger_labels: Optional[Sequence[str]] = None) -> dict:
    """Idempotently materialize a client's CRM facts under a synthetic
    source_type='crm' source row (one per client). Re-derivable: existing CRM
    facts for the client are cleared and recomputed. Returns index counts."""
    facts = crm_facts(db_path, client_id, client_name, identifiers,
                      ledger_labels)
    ref = f"crm/{client_id}"
    chash = kb.content_hash(ref)
    with db.connect(db_path) as conn:
        row = conn.execute("SELECT id FROM sources WHERE content_hash=?",
                           (chash,)).fetchone()
        if row:
            sid = row["id"]
            conn.execute("DELETE FROM facts WHERE source_id=?", (sid,))
        else:
            cur = conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id, client_name, content_type, title, status)"
                " VALUES ('crm', ?, ?, ?, ?, 'crm', ?, 'ingested')",
                (ref, chash, client_id, client_name, f"CRM numbers — {client_name}"))
            sid = cur.lastrowid
    return index.index_facts(db_path, sid, client_id, facts)
