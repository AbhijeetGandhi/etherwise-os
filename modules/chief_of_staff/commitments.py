"""Commitment extraction (Sonnet). Pulls concrete promises from a source's text
(Fathom transcript / Upwork thread / captured note) -> commitments(who, text,
due, source_cite, ...). who='us' is nagged in every brief until done (via the
sweep's commitment_due rule); who='client' is surfaced as awaiting-context only.

Source-cited + idempotent (dedup_key = hash of text+source). Steady state:
extract from NEW sources via a cursor; a full backfill over all history is a
separate gated cost (old promises are mostly resolved). Shadow-safe: a Sonnet
reasoning call, local writes only.
"""
from __future__ import annotations

import hashlib
import json
from typing import List, Optional

from core import config, db, runner

MODEL_KEY = "drafting"             # Sonnet (design: Sonnet for commitment extraction)
CURSOR = "cos_commitment_source_id"

SCHEMA = {
    "type": "object",
    "properties": {
        "commitments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "who": {"type": "string", "enum": ["us", "client"]},
                    "text": {"type": "string"},
                    "due": {"type": ["string", "null"]},   # ISO date or null
                    "quote": {"type": "string"},            # verbatim anchor
                },
                "required": ["who", "text", "due", "quote"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["commitments"],
    "additionalProperties": False,
}

SYSTEM = (
    "You extract concrete COMMITMENTS (promises to do something) from a "
    "transcript/thread for an automation agency. Rules (enforced):\n"
    "- who='us' = a promise by Abhijeet/Etherwise ('I'll send the register "
    "Friday', 'we'll have the POC done this week'). who='client' = a promise by "
    "the client ('I'll grant access tomorrow', 'we'll send the data').\n"
    "- Only real, specific commitments with an owner and an action. NOT vague "
    "intentions, opinions, or small talk. If none, return an empty list.\n"
    "- due: an ISO date (YYYY-MM-DD) when the promise is due, resolved RELATIVE "
    "to the source date given to you ('Friday' -> that week's Friday). null if "
    "no timeframe was stated. Never invent a date.\n"
    "- quote: a short verbatim phrase from the text that anchors the commitment "
    "(REQUIRED — a commitment with no anchor is useless)."
)


def _dedup_key(text: str, source_ref: str) -> str:
    norm = " ".join((text or "").lower().split())
    return "commitment:" + hashlib.sha256(
        f"{source_ref}|{norm}".encode()).hexdigest()[:16]


def _source_text(conn, source_id: int) -> Optional[dict]:
    src = conn.execute(
        "SELECT id, source_ref, client_id, occurred_dt FROM sources WHERE id=?",
        (source_id,)).fetchone()
    if not src:
        return None
    fts = conn.execute("SELECT text FROM source_fts WHERE source_id=?",
                       (source_id,)).fetchone()
    return {"source": dict(src), "text": fts["text"] if fts else ""}


def extract_from_source(db_path, source_id: int,
                        max_tokens: int = 1200) -> dict:
    """Extract + persist commitments from ONE source. Idempotent on dedup_key.
    Returns {inserted, skipped, found}."""
    with db.connect(db_path) as conn:
        bundle = _source_text(conn, source_id)
    if not bundle or not (bundle["text"] or "").strip():
        return {"inserted": 0, "skipped": 0, "found": 0, "reason": "no text"}
    src = bundle["source"]
    occurred = (src["occurred_dt"] or "")[:10] or "unknown"
    result = runner.claude_call(
        task_name="cos_commitments", model_key=MODEL_KEY, system=SYSTEM,
        schema=SCHEMA,
        user_content=(f"Source: {src['source_ref']}\nSource date: {occurred}\n\n"
                      f"{bundle['text'][:18000]}"),
        purpose=f"commitments {src['source_ref']}",
        max_output_tokens=max_tokens, db_path=db_path)
    found = (result.parsed or {}).get("commitments", [])
    inserted = skipped = 0
    with db.connect(db_path) as conn:
        for c in found:
            text = (c.get("text") or "").strip()
            quote = (c.get("quote") or "").strip()
            if not text or not quote:
                skipped += 1
                continue
            cite = f"{src['source_ref']} @ {quote[:80]}"
            key = _dedup_key(text, src["source_ref"])
            try:
                conn.execute(
                    "INSERT INTO commitments (who, client_id, text, due,"
                    " source_cite, dedup_key, state) VALUES (?,?,?,?,?,?, 'open')",
                    (c.get("who") or "us", src["client_id"], text,
                     c.get("due"), cite, key))
                inserted += 1
            except Exception:          # dedup_key UNIQUE -> already have it
                skipped += 1
    return {"inserted": inserted, "skipped": skipped, "found": len(found),
            "source_ref": src["source_ref"]}


def extract_new(db_path, limit: int = 20, source_types=("fathom", "upwork"),
                _extract=None) -> dict:
    """Extract commitments from sources newer than the cursor (steady-state
    incremental path; the daily job calls this). Advances the cursor."""
    _extract = _extract or extract_from_source
    qs = ",".join("?" * len(source_types))
    with db.connect(db_path) as conn:
        row = conn.execute("SELECT value FROM sync_cursors WHERE name=?",
                           (CURSOR,)).fetchone()
        last = int(row["value"]) if row else 0
        ids = [r["id"] for r in conn.execute(
            f"SELECT id FROM sources WHERE id > ? AND source_type IN ({qs})"
            f" ORDER BY id LIMIT ?", (last, *source_types, limit))]
    inserted = 0
    maxid = last
    for sid in ids:
        inserted += _extract(db_path, sid).get("inserted", 0)
        maxid = max(maxid, sid)
    if maxid > last:
        with db.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO sync_cursors (name, value, updated_at) VALUES"
                " (?,?,datetime('now')) ON CONFLICT(name) DO UPDATE SET"
                " value=excluded.value, updated_at=datetime('now')",
                (CURSOR, str(maxid)))
    return {"sources": len(ids), "inserted": inserted, "cursor": maxid}


def open_commitments(db_path, who: Optional[str] = None) -> List[dict]:
    sql = "SELECT * FROM commitments WHERE state='open'"
    args: list = []
    if who:
        sql += " AND who=?"
        args.append(who)
    sql += " ORDER BY (due IS NULL), due"
    with db.connect(db_path) as conn:
        return [dict(r) for r in conn.execute(sql, args)]
