"""Cockpit write actions — v1 scope: nudge state (done/snooze/dismiss) into
the LOCAL nudge_state table (decision B1b; M4 owns the real Airtable schema).

Drafts-only is sacred: there is NO action here that sends a message on any
channel. Follow-up "done" only records the founder's decision; the actual
send stays a human action in Upwork (copy + open thread, client-side).
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from core import config, db

VALID_ACTIONS = {"done", "snooze", "dismiss"}
STATE_FOR = {"done": "done", "snooze": "snoozed", "dismiss": "dismissed"}
DEFAULT_SNOOZE_DAYS = 3


def apply_nudge(db_path: Optional[Path], item_key: str, action: str,
                snooze_days: Optional[int] = None, note: Optional[str] = None,
                today: Optional[str] = None) -> dict:
    if action not in VALID_ACTIONS:
        raise ValueError(f"invalid nudge action: {action!r}"
                         f" (allowed: {sorted(VALID_ACTIONS)})")
    if not item_key:
        raise ValueError("item_key required")
    state = STATE_FOR[action]
    snooze_until = None
    if action == "snooze":
        base = date.fromisoformat(today) if today else \
            date.fromisoformat(__import__("datetime").datetime.now(
                config.TZ).strftime("%Y-%m-%d"))
        snooze_until = (base + timedelta(
            days=snooze_days or DEFAULT_SNOOZE_DAYS)).isoformat()
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO nudge_state (item_key, state, snooze_until, note,"
            " source, updated_at) VALUES (?,?,?,?, 'cockpit', datetime('now'))"
            " ON CONFLICT(item_key) DO UPDATE SET state=excluded.state,"
            " snooze_until=excluded.snooze_until, note=excluded.note,"
            " updated_at=datetime('now')",
            (item_key, state, snooze_until, note))
    return {"ok": True, "item_key": item_key, "state": state,
            "snooze_until": snooze_until}


def nudge(db_path: Optional[Path], body: dict,
          today: Optional[str] = None) -> dict:
    """POST /api/nudge handler. body: {item_key, action, snooze_days?, note?}"""
    return apply_nudge(db_path, body.get("item_key"), body.get("action"),
                       snooze_days=body.get("snooze_days"),
                       note=body.get("note"), today=today)
