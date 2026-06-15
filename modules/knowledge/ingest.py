"""Ingest: parse a dropped source → dedup (content hash) → classify (client +
type) → density-grade → record a `sources` row. Source-agnostic drop-zone
(mirrors finance-inbox); Fathom is the first parser. Extraction is a separate
step (extract.py) so ingest stays model-free and cheap.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

from core import config, db
from modules.knowledge import kb

_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


# ── parsers (per source) ──────────────────────────────────────────────────────

def parse_fathom(payload: dict) -> dict:
    """Fathom meeting JSON -> normalized source dict."""
    rid = str(payload.get("recording_id") or payload.get("id") or "")
    tx = payload.get("transcript") or []
    if isinstance(tx, list):
        lines = []
        speakers = set()
        for seg in tx:
            sp = (seg.get("speaker") or "").strip()
            if sp:
                speakers.add(sp)
            lines.append(f"{sp}: {seg.get('text', '')}".strip(": ").strip())
        text = "\n".join(lines)
    else:
        text, speakers = str(tx), set()
    return {
        "source_type": "fathom",
        "source_ref": f"fathom/{rid}",
        "title": payload.get("title"),
        "occurred_dt": payload.get("created_at")
        or payload.get("recording_start_time"),
        "participants": sorted(speakers),
        "text": text,
        "url": payload.get("url"),
    }


# ── classify + grade ──────────────────────────────────────────────────────────

def classify(participants: List[str], title: str, client_map: List[dict]):
    """Rule-based (SCHEMA Rule 5 allows Haiku/rules here): match participants
    /title against each client's identifiers (name fragments, email domains).
    Returns (client_id, client_name, content_type)."""
    hay = " ".join(participants or []).lower() + " " + (title or "").lower()
    for c in client_map:
        for ident in c.get("identifiers", []):
            if ident.lower() in hay:
                return c["client_id"], c["name"], "client-call"
    low = (title or "").lower()
    if any(w in low for w in ("demo", "capability", "walkthrough")):
        return None, None, "capability"
    return None, None, "unknown"


def grade_density(text: str) -> float:
    """0..1 information-density heuristic (deterministic). Blends length with
    lexical variety; deep-extract decisions key off this (SCHEMA Rule 5)."""
    words = re.findall(r"\w+", (text or "").lower())
    if not words:
        return 0.0
    length_score = min(1.0, len(words) / 1200.0)
    variety = len(set(words)) / len(words)            # 0..1
    return round(0.6 * length_score + 0.4 * variety, 3)


# ── ingest one file ───────────────────────────────────────────────────────────

_PARSERS = {"fathom": parse_fathom}


def ingest_file(path: Path, db_path=None, client_map=None,
                source_type: str = "fathom") -> dict:
    """Parse + dedup + classify + grade + record a sources row. Returns a
    summary dict; status 'duplicate' if the content hash already exists."""
    payload = json.loads(Path(path).read_text())
    parsed = _PARSERS[source_type](payload)
    chash = kb.content_hash(parsed["text"])
    cid, cname, ctype = classify(parsed["participants"], parsed["title"],
                                 client_map or [])
    density = grade_density(parsed["text"])

    with db.connect(db_path) as conn:
        dup = conn.execute("SELECT id FROM sources WHERE content_hash=?",
                           (chash,)).fetchone()
        if dup:
            return {"status": "duplicate", "source_id": dup["id"],
                    "client_id": cid}
        cur = conn.execute(
            "INSERT INTO sources (source_type, source_ref, content_hash,"
            " client_id, client_name, content_type, density, title,"
            " occurred_dt, raw_path, status) VALUES"
            " (?,?,?,?,?,?,?,?,?,?, 'ingested')",
            (parsed["source_type"], parsed["source_ref"], chash, cid, cname,
             ctype, density, parsed["title"], parsed["occurred_dt"],
             str(path)))
        sid = cur.lastrowid
        # FTS the raw text so search hits the source material too
        conn.execute("INSERT INTO source_fts (text, source_id) VALUES (?,?)",
                     (parsed["text"], sid))
    return {"status": "ingested", "source_id": sid, "client_id": cid,
            "client_name": cname, "content_type": ctype, "density": density,
            "source_ref": parsed["source_ref"], "text": parsed["text"]}
