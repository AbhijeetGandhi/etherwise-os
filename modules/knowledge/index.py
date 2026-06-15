"""Index cited facts into the kb domain + FTS, and full-text search.

index_facts is the ONLY persistence path for facts and it re-validates every
fact (kb.validate_fact) before insert — uncited/untagged facts are rejected
and counted, never written (the DB CHECK/NOT-NULL is the backstop).
"""
from __future__ import annotations

from typing import List, Optional

from core import db
from modules.knowledge import kb


def index_facts(db_path, source_id: int, client_id: Optional[str],
                facts: List[dict]) -> dict:
    """Persist validated facts for a source. Returns {inserted, rejected}.
    Marks the source 'extracted'."""
    inserted = rejected = 0
    with db.connect(db_path) as conn:
        for f in facts:
            try:
                kb.validate_fact(f)
            except kb.UncitedFact:
                rejected += 1
                continue
            try:
                conn.execute(
                    "INSERT INTO facts (source_id, client_id, category,"
                    " fact_text, citation, confidence) VALUES (?,?,?,?,?,?)",
                    (source_id, client_id, f.get("category") or "other",
                     f["fact_text"], f["citation"], f["confidence"]))
                inserted += 1
            except Exception:           # DB-level backstop (CHECK/NOT NULL)
                rejected += 1
        conn.execute("UPDATE sources SET status='extracted' WHERE id=?",
                     (source_id,))
    return {"inserted": inserted, "rejected": rejected}


def search(db_path, query: str, client_id: Optional[str] = None,
           limit: int = 25) -> List[dict]:
    """FTS over facts; optional client scope. Returns cited fact rows."""
    sql = ("SELECT f.id, f.client_id, f.category, f.fact_text, f.citation,"
           " f.confidence, s.source_type, s.title FROM facts_fts"
           " JOIN facts f ON f.id = facts_fts.rowid"
           " JOIN sources s ON s.id = f.source_id"
           " WHERE facts_fts MATCH ?")
    args: list = [query]
    if client_id:
        sql += " AND f.client_id = ?"
        args.append(client_id)
    sql += " ORDER BY rank LIMIT ?"
    args.append(limit)
    with db.connect(db_path) as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def search_sources(db_path, query: str, limit: int = 25) -> List[dict]:
    """FTS over raw transcript text (source material)."""
    with db.connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT s.id, s.source_ref, s.title, s.client_id FROM source_fts"
            " JOIN sources s ON s.id = source_fts.source_id"
            " WHERE source_fts MATCH ? LIMIT ?", (query, limit))]
