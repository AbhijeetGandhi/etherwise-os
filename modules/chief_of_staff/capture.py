"""Quick Capture — zero-friction note inbox. /note, cockpit quick-add, and
(once Gmail is connected) email-to-self drop raw text into `notes`; the next
sweep classifies each (Sonnet) into a nudge / commitment / KB-note / idea so
nothing is ever lost. Actionable buckets create the target row; kb_note/idea
stay in the notes table as the record (searchable). Shadow-safe: local only.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from core import config, db, runner
from modules.chief_of_staff import nudges

MODEL_KEY = "drafting"   # Sonnet (design: Sonnet for capture classification)
BUCKETS = ("nudge", "commitment", "kb_note", "idea")

SCHEMA = {
    "type": "object",
    "properties": {
        "bucket": {"type": "string", "enum": list(BUCKETS)},
        "title": {"type": "string"},
        "action": {"type": ["string", "null"]},
        "due": {"type": ["string", "null"]},          # ISO date or null
        "who": {"type": "string", "enum": ["us", "client"]},
    },
    "required": ["bucket", "title", "action", "due", "who"],
    "additionalProperties": False,
}

SYSTEM = (
    "Classify a captured note from the founder of an automation agency into ONE "
    "bucket:\n"
    "- nudge: an actionable to-do ('follow up with X', 'fix the Y automation').\n"
    "- commitment: a promise with an owner (who=us|client), often a due date "
    "('tell client the register ships Friday').\n"
    "- kb_note: a reference fact to remember about a client or the business "
    "(no action needed).\n"
    "- idea: a someday/maybe idea or thought.\n"
    "title: a short imperative title. action: the next step (nudge only, else "
    "null). due: ISO date if a timeframe is stated, else null. who: us unless "
    "clearly the client's promise."
)


def add_note(db_path, text: str, source: str = "terminal") -> dict:
    """Drop a raw note into the inbox (state='new'); the sweep classifies it."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty note")
    with db.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO notes (text, source, state) VALUES (?,?, 'new')",
            (text, source))
    return {"id": cur.lastrowid, "text": text, "source": source}


def _apply(db_path, note: dict, cls: dict,
           now: Optional[datetime] = None) -> dict:
    bucket = cls.get("bucket")
    ref = None
    if bucket == "nudge":
        out = nudges.write_nudge(
            db_path, now=now, source="capture", dept="sales", kind="capture",
            title=cls.get("title") or note["text"][:80],
            action=cls.get("action"), due=cls.get("due"),
            dedup_key=f"note:{note['id']}")
        ref = str(out["id"])
    elif bucket == "commitment":
        key = f"commitment:note:{note['id']}"
        with db.connect(db_path) as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO commitments (who, text, due, source_cite,"
                    " dedup_key, state) VALUES (?,?,?,?,?, 'open')",
                    (cls.get("who") or "us", cls.get("title") or note["text"],
                     cls.get("due"), f"note/{note['id']}", key))
                ref = str(cur.lastrowid)
            except Exception:
                ref = None
    # kb_note / idea: the note row itself is the record (no extra row)
    with db.connect(db_path) as conn:
        conn.execute("UPDATE notes SET state='classified', classified=?,"
                     " classified_ref=? WHERE id=?",
                     (bucket, ref, note["id"]))
    return {"note_id": note["id"], "bucket": bucket, "ref": ref}


def classify_new(db_path, limit: int = 20, now: Optional[datetime] = None,
                 _classify=None) -> dict:
    """Classify all new notes (Sonnet) and route them. Returns per-bucket counts."""
    with db.connect(db_path) as conn:
        notes = [dict(r) for r in conn.execute(
            "SELECT id, text, source FROM notes WHERE state='new'"
            " ORDER BY id LIMIT ?", (limit,))]
    counts = {b: 0 for b in BUCKETS}
    for note in notes:
        cls = (_classify(db_path, note) if _classify
               else _classify_one(db_path, note))
        if not cls:
            continue
        _apply(db_path, note, cls, now=now)
        counts[cls.get("bucket", "idea")] = counts.get(cls.get("bucket"), 0) + 1
    return {"classified": len(notes), **counts}


def _classify_one(db_path, note: dict) -> Optional[dict]:
    result = runner.claude_call(
        task_name="cos_capture", model_key=MODEL_KEY, system=SYSTEM,
        schema=SCHEMA, user_content=note["text"],
        purpose="capture classify", max_output_tokens=300, db_path=db_path)
    return result.parsed


def ideas(db_path) -> List[dict]:
    with db.connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, text, created_at FROM notes WHERE classified='idea'"
            " ORDER BY id DESC")]
