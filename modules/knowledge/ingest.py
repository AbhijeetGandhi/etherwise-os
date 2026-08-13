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

def _seg_speaker(seg: dict):
    """Speaker can be a plain string (export zip) OR an object (live API:
    {display_name, matched_calendar_invitee_email, ...}). Return (name, email)."""
    sp = seg.get("speaker")
    if isinstance(sp, dict):
        name = (sp.get("display_name") or sp.get("name") or "").strip()
        email = (sp.get("matched_calendar_invitee_email")
                 or sp.get("email") or "").strip()
        return name, email
    return (str(sp).strip() if sp else ""), ""


def parse_fathom(payload: dict) -> dict:
    """Fathom meeting JSON -> normalized source dict. Handles BOTH the live API
    poller shape AND the historical export (asset zip). Transcript segments may
    carry a `timestamp` (citation anchors); `speaker` may be a string (export)
    or an object {display_name, matched_calendar_invitee_email} (API).

    `participants` is the UNION of the explicit invitee list and the transcript
    speakers — see the comment at the union below; it is the single most
    expensive line in this module's history."""
    rid = str(payload.get("recording_id") or payload.get("id") or "")
    tx = payload.get("transcript") or []
    lines, spoken = [], set()
    if isinstance(tx, list):
        for seg in tx:
            if not isinstance(seg, dict):
                continue
            sp, email = _seg_speaker(seg)
            ts_raw = seg.get("timestamp")
            ts = (str(ts_raw).strip() if ts_raw else "")
            if sp:
                spoken.add(sp)
            if email:
                spoken.add(email)          # email domain drives classification
            prefix = f"[{ts}] " if ts else ""
            txt = seg.get("text", "")
            lines.append(f"{prefix}{sp}: {txt if isinstance(txt, str) else ''}".strip())
        text = "\n".join(lines)
    else:
        text = str(tx)
    # participants: the explicit list UNION the transcript speakers. NEVER `or`.
    #
    # THIS WAS `or` UNTIL 2026-08-14 AND IT COST 114 CLIENT CALLS. Read it again:
    # `{x for x in parts if x} or spoken` is not a fallback for an EMPTY explicit
    # list, it is a fallback for a FALSY one — and a Fathom EXPORT payload always
    # carries `participants: [{"name": "Abhijeet Gandhi"}]`, the person who owns
    # the recording. One truthy element, so `spoken` was never even evaluated and
    # every OTHER human who actually spoke on the call was discarded.
    #
    # Measured on the live corpus before the fix: 357 of 617 Fathom payloads are
    # that export shape, and 298 of 617 sources had at least one speaker missing
    # from `participants` — i.e. the classifier never saw the name it needed. The
    # damage is not "some calls stayed unmapped", it is that the residue then went
    # to an LLM which was shown a participant list of ONE INTERNAL PERSON and a
    # title of "Impromptu Google Meet Meeting", and reasonably concluded
    # internal-product. 114 real client calls were EXCLUDED on that basis.
    #
    # Union, not "explicit wins": the two sources are complementary, not ranked.
    # calendar invitees carry emails (domain -> client, the strongest signal) and
    # include people who never spoke; transcript speakers carry the names of
    # people who joined via someone else's device and appear in no invite. Taking
    # either one alone loses real attribution signal. `spoken` holds names AND
    # matched invitee emails (see the loop above), so the union is a set of both.
    parts = []
    for p in (payload.get("participants") or []):
        if isinstance(p, dict):
            parts.append((p.get("name") or p.get("display_name") or "").strip())
            email = p.get("email") or p.get("matched_calendar_invitee_email")
            if email:
                parts.append(str(email).strip())
        elif p:
            parts.append(str(p).strip())
    participants = sorted({x for x in parts if x} | spoken)
    return {
        "source_type": "fathom",
        "source_ref": f"fathom/{rid}",
        "title": payload.get("title"),
        "occurred_dt": payload.get("created_at") or payload.get("date")
        or payload.get("recording_start") or payload.get("recording_start_time"),
        "participants": participants,
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

def parse_email(payload: dict) -> dict:
    """Gmail message -> normalized source dict (delegates to email_poll so the
    parsing lives next to the puller; imported lazily to avoid a cycle)."""
    from modules.knowledge import email_poll
    return email_poll.parse_email(payload)


def parse_loom(payload: dict) -> dict:
    """Loom export item -> normalized source dict. Loom recordings are single-
    narrator with a plain-text transcript (no speaker/timestamp segments), so
    extraction anchors on verbatim quotes rather than timestamps. No participant
    list — classification is by title (most Looms are outbound, so they land as
    agency/proof-points via scope, not a client dossier)."""
    vid = str(payload.get("id") or "")
    return {
        "source_type": "loom",
        "source_ref": f"loom/{vid}",
        "title": payload.get("name"),
        "occurred_dt": payload.get("date") or payload.get("createdAt"),
        "participants": [],
        "text": payload.get("transcript") or "",
        "url": f"https://www.loom.com/share/{vid}" if vid else None,
    }


_PARSERS = {"fathom": parse_fathom, "email": parse_email, "loom": parse_loom}


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
