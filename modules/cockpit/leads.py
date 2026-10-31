"""Leads in the cockpit — read-only view + the one status write (2026-11-01).

The leads table (migration 046) is the Review queue's missing bucket: a
prospect who wrote in. This module serves the Pipeline › Leads view and the
"New leads" line on Today, and takes the ONE write a human makes here — the
status (new → contacted → call → proposal → client | no) and a note. Nothing
here sends; he replies on the channel the lead came in on.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from core import config
from modules.jarvis import leads as L

STATUS_WORDS = {"new": "new", "contacted": "contacted", "call": "call booked",
                "proposal": "proposal sent", "client": "became a client",
                "no": "not a fit"}


def _ro(db_path: Optional[Path]) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or config.DB_PATH), timeout=10)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn


def leads(db_path: Optional[Path] = None) -> dict:
    """GET /api/leads — every lead, open first, with counts by status."""
    conn = _ro(db_path)
    try:
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM leads ORDER BY CASE status WHEN 'new' THEN 0"
                " WHEN 'contacted' THEN 1 WHEN 'call' THEN 2 WHEN 'proposal' THEN 3"
                " ELSE 9 END, first_seen_at DESC")]
        except sqlite3.OperationalError:
            return {"built": False, "reason": "migration 046 (leads) not applied",
                    "rows": [], "by_status": {}, "open": 0, "new": 0}
    finally:
        conn.close()
    by: dict = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    return {"built": True, "rows": rows, "by_status": by,
            "open": sum(1 for r in rows if r["status"] not in ("client", "no")),
            "new": by.get("new", 0), "statuses": list(L.STATUSES),
            "words": STATUS_WORDS}


def new_leads(db_path: Optional[Path] = None, *, fresh_days: int = 14) -> dict:
    """Today's line: leads still 'new', newest first, with the older count."""
    d = leads(db_path)
    if not d.get("built"):
        return {"total": 0, "rows": [], "older": 0}
    from datetime import datetime, timedelta
    cutoff = (datetime.now(config.TZ) - timedelta(days=fresh_days)).strftime("%Y-%m-%d")
    new = [r for r in d["rows"] if r["status"] == "new"]
    fresh = [r for r in new if (r["first_seen_at"] or "")[:10] >= cutoff]
    return {"total": len(new), "rows": fresh[:8], "older": len(new) - len(fresh),
            "fresh_days": fresh_days, "open": d["open"]}


def set_status(db_path: Optional[Path], body: dict) -> dict:
    """POST /api/lead — {id, status, note?}. The only write on this surface."""
    from core import db
    body = body or {}
    try:
        lead_id = int(body.get("id"))
    except (TypeError, ValueError):
        return {"error": "id (integer) required"}
    status = body.get("status")
    if status not in L.STATUSES:
        return {"error": f"status must be one of {list(L.STATUSES)}"}
    with db.connect(db_path) as conn:
        row = conn.execute("SELECT id FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not row:
            return {"error": f"unknown lead {lead_id}"}
        L.set_status(conn, lead_id, status, note=body.get("note"))
    return {"ok": True, "id": lead_id, "status": status}
