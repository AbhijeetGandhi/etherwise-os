"""Staging DAL for the EXTRACT stage (Jarvis J2 §4.3 stage 3).

Everything the READER produces lands HERE first — never in live facts /
commitments / cards. `bin/jarvis-extract-review` reads these; `--promote` moves
facts + commitments into the live tables after Abhijeet's review. Promotion is
idempotent: a staging row stamps `promoted_at` the first time, so a re-run finds
nothing pending and inserts nothing (no duplicates).
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from modules.knowledge import kb


# ── bundling: mapped events not yet extracted, grouped per client ────────────
def unextracted_bundles(conn: sqlite3.Connection, *, max_clients: int,
                        max_events_per_client: int) -> list[dict]:
    """Mapped events with extracted_at IS NULL, grouped by client_id. Returns a
    list of {client_id, events:[row,...]} — at most max_clients bundles, each
    capped at max_events_per_client (oldest first, so nothing starves). A client
    with more events than the cap keeps the overflow unextracted for next run."""
    rows = conn.execute(
        "SELECT id, client_id, author, subject, ts, text, permalink"
        " FROM events WHERE state='mapped' AND client_id IS NOT NULL"
        " AND text IS NOT NULL AND extracted_at IS NULL"
        " ORDER BY client_id, ts").fetchall()
    by_client: dict[str, list] = {}
    for r in rows:
        by_client.setdefault(r["client_id"], []).append(dict(r))
    bundles = []
    for cid in list(by_client)[:max_clients]:
        bundles.append({"client_id": cid,
                        "events": by_client[cid][:max_events_per_client]})
    return bundles


def mark_extracted(conn: sqlite3.Connection, event_ids: list[str]) -> None:
    for eid in event_ids:
        conn.execute("UPDATE events SET extracted_at=datetime('now'),"
                     " updated_at=datetime('now') WHERE id=?", (eid,))


# ── staging writes ───────────────────────────────────────────────────────────
def insert_fact(conn, run_id, f: dict) -> None:
    conn.execute(
        "INSERT INTO extract_staging_facts (run_id, client_id, event_id,"
        " category, fact_text, citation, confidence, access_scope, origin)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, f["client_id"], f.get("event_id"), f.get("category"),
         f["fact_text"], f["citation"], f["confidence"], f["access_scope"],
         f.get("origin", "fact")))


def insert_commitment(conn, run_id, c: dict) -> None:
    conn.execute(
        "INSERT INTO extract_staging_commitments (run_id, client_id, event_id,"
        " who, text, due, ask, source_cite, dedup_key)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, c["client_id"], c.get("event_id"), c["who"], c["text"],
         c.get("due"), int(bool(c.get("ask"))), c["source_cite"],
         c["dedup_key"]))


def insert_closure(conn, run_id, cl: dict) -> None:
    conn.execute(
        "INSERT INTO extract_staging_closures (run_id, client_id, event_id,"
        " intent, evidence, confidence) VALUES (?,?,?,?,?,?)",
        (run_id, cl["client_id"], cl.get("event_id"), cl["intent"],
         cl["evidence"], cl["confidence"]))


# ── review reads (pending = not yet promoted) ────────────────────────────────
def pending_facts(conn, run_id: Optional[int] = None) -> list[dict]:
    return _pending(conn, "extract_staging_facts", run_id)


def pending_commitments(conn, run_id: Optional[int] = None) -> list[dict]:
    return _pending(conn, "extract_staging_commitments", run_id)


def pending_closures(conn, run_id: Optional[int] = None) -> list[dict]:
    # closures have no promotion path yet (deferred) — "pending" = all of them
    q = "SELECT * FROM extract_staging_closures WHERE applied_at IS NULL"
    args: tuple = ()
    if run_id is not None:
        q += " AND run_id=?"
        args = (run_id,)
    return [dict(r) for r in conn.execute(q + " ORDER BY client_id, id", args)]


def _pending(conn, table: str, run_id: Optional[int]) -> list[dict]:
    q = f"SELECT * FROM {table} WHERE promoted_at IS NULL"
    args: tuple = ()
    if run_id is not None:
        q += " AND run_id=?"
        args = (run_id,)
    return [dict(r) for r in conn.execute(q + " ORDER BY client_id, id", args)]


def run_cost(conn, run_id: int) -> float:
    """Measured $ this extract run spent (from the gateway's usage ledger)."""
    # purpose is "extract <client> run:<id>" — run:<id> is the SUFFIX, so anchor
    # the match at the end (no trailing %) or run:1 would also catch run:10/11.
    row = conn.execute(
        "SELECT COALESCE(SUM(total_cost_usd),0) s FROM claude_usage"
        " WHERE task_name='jarvis_extract' AND purpose LIKE ?",
        (f"%run:{run_id}",)).fetchone()
    return float(row["s"])


# ── promotion (facts + commitments -> live tables; idempotent) ───────────────
def _ensure_source(conn, event_id: str, client_id: str) -> Optional[int]:
    """Create-or-get the sources row for an email event (facts.source_id is NOT
    NULL). Idempotent on content_hash. Returns source_id or None if the event
    is gone."""
    ev = conn.execute("SELECT id, subject, ts, text, permalink FROM events"
                      " WHERE id=?", (event_id,)).fetchone()
    if not ev:
        return None
    ch = kb.content_hash(f"{event_id}|{ev['text'] or ''}")
    row = conn.execute("SELECT id FROM sources WHERE content_hash=?",
                       (ch,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO sources (source_type, source_ref, content_hash, client_id,"
        " content_type, title, occurred_dt, status)"
        " VALUES ('email',?,?,?,'email',?,?,'extracted')",
        (ev["permalink"] or event_id, ch, client_id, ev["subject"], ev["ts"]))
    return cur.lastrowid


def promote(db_path, *, run_id: Optional[int] = None) -> dict:
    """Move pending staged facts + commitments into the live tables. Idempotent
    (promoted_at guard + commitments dedup_key UNIQUE). Closures are NOT promoted
    (wiring into the live closure pass is a deferred, gated step)."""
    from core import db
    facts_added = comm_added = comm_skipped = 0
    with db.connect(db_path) as conn:
        for f in _pending(conn, "extract_staging_facts", run_id):
            sid = _ensure_source(conn, f["event_id"], f["client_id"])
            if sid is None:
                continue                      # source event vanished; leave pending
            cur = conn.execute(
                "INSERT INTO facts (source_id, client_id, category, fact_text,"
                " citation, confidence, scope, access_scope)"
                " VALUES (?,?,?,?,?,?, 'client', ?)",
                (sid, f["client_id"], f["category"] or "other", f["fact_text"],
                 f["citation"], f["confidence"], f["access_scope"]))
            conn.execute("UPDATE extract_staging_facts SET"
                         " promoted_at=datetime('now'), promoted_ref=?"
                         " WHERE id=?", (cur.lastrowid, f["id"]))
            facts_added += 1
        for c in _pending(conn, "extract_staging_commitments", run_id):
            try:
                cur = conn.execute(
                    "INSERT INTO commitments (who, client_id, text, due,"
                    " source_cite, dedup_key, state) VALUES (?,?,?,?,?,?, 'open')",
                    (c["who"], c["client_id"], c["text"], c["due"],
                     c["source_cite"], c["dedup_key"]))
                ref = cur.lastrowid
                comm_added += 1
            except sqlite3.IntegrityError:     # dedup_key already live -> skip
                ref = None
                comm_skipped += 1
            conn.execute("UPDATE extract_staging_commitments SET"
                         " promoted_at=datetime('now'), promoted_ref=?"
                         " WHERE id=?", (ref, c["id"]))
    return {"facts_promoted": facts_added, "commitments_promoted": comm_added,
            "commitments_skipped_existing": comm_skipped}
