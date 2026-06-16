"""CRM numbers → cited facts. Deterministic (no model): pull a client's
contracts + transactions from v3 SQLite by its identifiers and emit CONFIRMED
facts citing the CRM as source. These feed the dossier's "The numbers" section.
"""
from __future__ import annotations

from typing import List

from core import db
from modules.knowledge import kb, index

_EARNINGS = ("Hourly Earning", "Fixed Earning", "Bonus")


def crm_facts(db_path, client_id: str, client_name: str,
              identifiers: List[str]) -> List[dict]:
    """Emit cited CRM facts for a client, matched by its identifiers against
    transaction description/company + contract fields (CRM-link is fuzzy until
    the clients mirror lands)."""
    ids = [i for i in (identifiers or []) if i]
    if not ids:
        return []
    facts: List[dict] = []
    with db.connect(db_path) as conn:
        # contracts: any identifier in company/title/job fields
        cclause = " OR ".join(
            ["client_company LIKE ? OR job_title LIKE ? OR title LIKE ?"] * len(ids))
        cargs = [a for i in ids for a in (f"%{i}%", f"%{i}%", f"%{i}%")]
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
        tclause = " OR ".join(
            ["description LIKE ? OR assignment_company LIKE ?"] * len(ids))
        targs = [a for i in ids for a in (f"%{i}%", f"%{i}%")]
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
               identifiers: List[str]) -> dict:
    """Idempotently materialize a client's CRM facts under a synthetic
    source_type='crm' source row (one per client). Re-derivable: existing CRM
    facts for the client are cleared and recomputed. Returns index counts."""
    facts = crm_facts(db_path, client_id, client_name, identifiers)
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
