"""Upwork message threads → cited facts. A thread's messages are concatenated
into one source (source_type='upwork'); each line is anchored by send time so
extracted facts cite 'upwork/<thread_id> @ <timestamp>'. Classification is by
explicit client_id when known (thread→client linkage is unreliable for some
clients whose Upwork org name differs from their company name).
"""
from __future__ import annotations

from typing import List, Optional

from core import db
from modules.knowledge import kb, extract, index


def build_thread_text(db_path, thread_id: str) -> str:
    """Render a thread's messages chronologically with timestamp anchors."""
    with db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT created_dt, sender_user_name, direction, message FROM"
            " messages WHERE thread_id=? ORDER BY created_dt", (thread_id,))
        lines = []
        for r in rows:
            ts = (r["created_dt"] or "")[:19]
            who = r["sender_user_name"] or r["direction"] or "?"
            lines.append(f"[{ts}] {who}: {r['message'] or ''}".strip())
    return "\n".join(lines)


def ingest_thread(db_path, thread_id: str, client_id: Optional[str],
                  client_name: Optional[str], extract_facts: bool = True) -> dict:
    """Ingest one Upwork thread as a source; optionally extract facts.
    Idempotent on content hash. Returns a summary dict."""
    text = build_thread_text(db_path, thread_id)
    if not text.strip():
        return {"status": "empty", "thread_id": thread_id}
    ref = f"upwork/{thread_id}"
    chash = kb.content_hash(text)
    with db.connect(db_path) as conn:
        dup = conn.execute("SELECT id FROM sources WHERE content_hash=?",
                           (chash,)).fetchone()
        if dup:
            return {"status": "duplicate", "source_id": dup["id"]}
        cur = conn.execute(
            "INSERT INTO sources (source_type, source_ref, content_hash,"
            " client_id, client_name, content_type, density, title, status)"
            " VALUES ('upwork', ?, ?, ?, ?, 'client-msgs', ?, ?, 'ingested')",
            (ref, chash, client_id, client_name,
             round(min(1.0, len(text) / 8000.0), 3),
             f"Upwork thread {thread_id}"))
        sid = cur.lastrowid
        conn.execute("INSERT INTO source_fts (text, source_id) VALUES (?,?)",
                     (text, sid))
    res = {"status": "ingested", "source_id": sid, "client_id": client_id,
           "source_ref": ref, "text": text}
    if extract_facts:
        try:
            facts = extract.extract_facts(res, text, db_path=db_path)
            res["facts"] = index.index_facts(db_path, sid, client_id, facts)
        except Exception as e:               # one bad thread must not abort
            res["facts"] = {"inserted": 0, "rejected": 0, "error": str(e)[:200]}
    return res


def ingest_client_threads(db_path, client_id: str, client_name: str) -> dict:
    """Ingest every thread linked to a client (threads.client_id, set by
    clients.link_threads). Returns counts. Idempotent via content hash."""
    with db.connect(db_path) as conn:
        tids = [r["id"] for r in conn.execute(
            "SELECT id FROM threads WHERE client_id=?", (client_id,))]
    threads = facts = 0
    for tid in tids:
        r = ingest_thread(db_path, tid, client_id, client_name)
        if r.get("status") == "ingested":
            threads += 1
            facts += (r.get("facts") or {}).get("inserted", 0)
    return {"threads_ingested": threads, "facts": facts, "linked": len(tids)}
