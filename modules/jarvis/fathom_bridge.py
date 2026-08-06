"""Fathom -> registry mapping bridge + dossier heal (J1 D3 §4.6).

M3 mapped Fathom recordings against a 4-client PILOT list; the registry knows all
67. This bridge re-maps UNMAPPED Fathom sources through the registry: a match now
heals the source (reattributes it + its facts + recompiles the dossier); a real
external participant with no match becomes a QUEUED event so Abhijeet assigns it
once and every future call from that participant auto-maps.

Bounded on purpose (there are hundreds of old unattributed calls): the runner
processes newest-first up to a cap, and never queues a call with no external
participant signal (those would flood the queue, not inform it).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from core import db, registry
from modules.jarvis import events
from modules.knowledge import dossier, ingest

_FREEMAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com",
             "yahoo.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
             "aol.com", "live.com", "msn.com"}
_INTERNAL = ("etherwise.io",)


def _rid(source_ref: str) -> str:
    return source_ref.split("/", 1)[1] if "/" in source_ref else source_ref


def _read_participants(raw_path: str) -> tuple[list[str], Optional[str]]:
    """(participants, url) from the raw Fathom payload; ([], None) if unreadable."""
    try:
        payload = json.loads(Path(raw_path).read_text())
    except (OSError, ValueError):
        return [], None
    parsed = ingest.parse_fathom(payload)
    return parsed.get("participants") or [], parsed.get("url")


def _match(reg, participants: list[str], title: str) -> Optional[str]:
    for p in participants:
        cid = reg.match_email(p) if "@" in p else reg.match_name(p)
        if cid:
            return cid
    # last resort: a fathom_name / alias token appears in title or a participant
    hay = (title or "").lower() + " " + " ".join(participants).lower()
    for cid in reg.matchable_ids():
        c = reg.by_id[cid]
        for tok in (c.get("fathom_names") or []):
            if tok and tok.lower() in hay:
                return cid
    return None


def _external_signal(participants: list[str]) -> Optional[str]:
    """The first external (non-internal) participant email — else None."""
    for p in participants:
        if "@" in p:
            dom = p.rsplit("@", 1)[-1].lower()
            if dom not in _INTERNAL:
                return p
    return None


def heal_source(db_path, source_ref: str, client_id: str, client_name: str,
                *, now_iso: Optional[str] = None,
                dossier_dir: Optional[Path] = None) -> dict:
    """Reattribute an unmapped Fathom source + its facts to a client, then
    recompile that client's dossier. Idempotent; only touches unmapped rows."""
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    with db.connect(db_path) as conn:
        rows = conn.execute("SELECT id FROM sources WHERE source_ref=?",
                            (source_ref,)).fetchall()
        sids = [r["id"] for r in rows]
        facts = 0
        if sids:
            qs = ",".join("?" * len(sids))
            conn.execute(
                f"UPDATE sources SET client_id=?, client_name=?,"
                f" content_type='client-call' WHERE id IN ({qs})",
                (client_id, client_name, *sids))
            cur = conn.execute(
                f"UPDATE facts SET client_id=?, scope='client'"
                f" WHERE source_id IN ({qs})", (client_id, *sids))
            facts = cur.rowcount
    comp = dossier.compile_dossier(db_path, client_id, client_name, now_iso,
                                   dossier_dir=dossier_dir)
    return {"sources": len(sids), "facts_reattributed": facts,
            "dossier": comp.get("status")}


def sync_unmapped(db_path, reg=None, *, limit: int = 50, queue_cap: int = 40,
                  run_id: Optional[int] = None,
                  read_participants: Callable = _read_participants,
                  now_iso: Optional[str] = None,
                  dossier_dir: Optional[Path] = None) -> dict:
    """Re-map unmapped Fathom sources through the registry. Auto-heals matches;
    queues external-but-unmatched calls; skips signal-less internal calls."""
    reg = reg or registry.load()
    with db.connect(db_path) as conn:
        unmapped = conn.execute(
            "SELECT source_ref, title, client_name, raw_path FROM sources"
            " WHERE source_type='fathom' AND client_id IS NULL"
            " ORDER BY occurred_dt DESC LIMIT ?", (limit,)).fetchall()

    healed = queued = skipped = 0
    for s in unmapped:
        parts, url = read_participants(s["raw_path"])
        cid = _match(reg, parts, s["title"])
        if cid:
            heal_source(db_path, s["source_ref"], cid, reg.by_id[cid]["name"],
                        now_iso=now_iso, dossier_dir=dossier_dir)
            healed += 1
            continue
        signal = _external_signal(parts)
        if not signal or queued >= queue_cap:
            skipped += 1
            continue
        rid = _rid(s["source_ref"])
        ev = {"id": events.canonical_id("fathom", rid), "source": "fathom",
              "native_id": rid, "ts": None, "author": signal,
              "subject": s["title"], "state": "queued", "text":
              "Participants: " + ", ".join(parts), "permalink": url,
              "raw_ref": s["source_ref"], "client_id": None}
        with db.connect(db_path) as conn:
            if events.store_event(conn, ev, run_id) == "inserted":
                queued += 1
            else:
                skipped += 1
    return {"scanned": len(unmapped), "auto_healed": healed,
            "queued": queued, "skipped": skipped}
