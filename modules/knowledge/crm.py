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
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from core import db, registry
from modules.knowledge import kb, index

_EARNINGS = ("Hourly Earning", "Fixed Earning", "Bonus")
# columns a free-text identifier may match on
_CONTRACT_COLS = ("client_company", "job_title", "title")
_TXN_COLS = ("description", "assignment_company")


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
        # transactions: earnings, any identifier in description/company
        # (substring), or a ledger label as the WHOLE company field (exact —
        # 'S' as a substring of a description would match half the ledger).
        tpreds: List[str] = []
        targs: List[str] = []
        for n in ids:
            tpreds.append(" OR ".join(f"kbnorm({c}) LIKE ?" for c in _TXN_COLS))
            targs.extend([f"%{n}%"] * len(_TXN_COLS))
        for lbl in labels:
            tpreds.append("kbnorm(assignment_company) = ?")
            targs.append(lbl)
        tclause = " OR ".join(f"({p})" for p in tpreds)
        types = ",".join("?" * len(_EARNINGS))
        rev = conn.execute(
            f"SELECT COALESCE(SUM(amount),0) s, COUNT(*) n,"
            f" MIN(creation_dt) first, MAX(creation_dt) last FROM transactions"
            f" WHERE type IN ({types}) AND amount > 0 AND ({tclause})",
            list(_EARNINGS) + targs).fetchone()
        if rev and rev["n"]:
            span = ""
            if rev["first"] and rev["last"]:
                span = f" ({rev['first'][:10]} → {rev['last'][:10]})"
            facts.append({
                "category": "numbers",
                "fact_text": f"Revenue to date: ${rev['s']:,.0f} across"
                             f" {rev['n']} earning transactions{span}.",
                "citation": "CRM:transactions",
                "confidence": "CONFIRMED"})
    return facts


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
