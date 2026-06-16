"""Cockpit write actions — v1 scope: nudge state (done/snooze/dismiss) into
the LOCAL nudge_state table (decision B1b; M4 owns the real Airtable schema).

Drafts-only is sacred: there is NO action here that sends a message on any
channel. Follow-up "done" only records the founder's decision; the actual
send stays a human action in Upwork (copy + open thread, client-side).
"""
from __future__ import annotations

import os
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from core import config, db

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ASK_CLI = _REPO_ROOT / "bin" / "kb-ask"
_ASK_TIMEOUT_S = 45
_ASK_MAX_LEN = 500

# Wake/rail = kick a launchd job. ALLOWLIST only — never an arbitrary label
# (the cockpit must not become a launchd-exec surface). This map IS the
# wake/rail seam: launchctl kick now → queue/API later for a hosted deploy.
WAKE_JOBS = {
    "scan": "io.etherwise.v3.upwork-scan",
    "sync": "io.etherwise.v3.upwork-sync",
    "inbox": "io.etherwise.v3.upwork-inbox",
    # "brief": M4 — no v3 brief job yet (UI greys it out)
}
RAIL_JOBS = {
    "outcome-capture": "io.etherwise.v3.upwork-outcomes",
}


def _kick(label: str) -> dict:
    proc = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"],
        capture_output=True, text=True, timeout=10)
    return {"ok": proc.returncode == 0, "label": label,
            "error": proc.stderr.strip()[:200] or None}


def wake(db_path: Optional[Path], body: dict) -> dict:
    """POST /api/wake — kick an allowlisted launchd job. body: {job}."""
    job = (body or {}).get("job")
    if job not in WAKE_JOBS:
        raise ValueError(f"unknown/forbidden wake job: {job!r}"
                         f" (allowed: {sorted(WAKE_JOBS)})")
    return {**_kick(WAKE_JOBS[job]), "job": job}


def rail(db_path: Optional[Path], body: dict) -> dict:
    """POST /api/rail — trigger an allowlisted rail (launchd kick). body:
    {rail}. The rail's executor is deterministic; in shadow its intended
    external writes go to shadow_ledger (no send)."""
    name = (body or {}).get("rail")
    if name not in RAIL_JOBS:
        raise ValueError(f"unknown rail: {name!r} (allowed: {sorted(RAIL_JOBS)})")
    return {**_kick(RAIL_JOBS[name]), "rail": name}

def ask(db_path: Optional[Path], body: dict) -> dict:
    """POST /api/ask — proxy the kb-librarian. The model call runs in a CHILD
    process (bin/kb-ask), never in the cockpit server (view+action plane rule).
    body: {question, client?}. Returns {answer, grounded, citations, ...}."""
    question = ((body or {}).get("question") or "").strip()
    if not question:
        raise ValueError("question required")
    if len(question) > _ASK_MAX_LEN:
        question = question[:_ASK_MAX_LEN]
    client = (body or {}).get("client") or ""
    args = [sys.executable, str(_ASK_CLI), question]
    if client:
        args.append(str(client))
    proc = subprocess.run(args, capture_output=True, text=True,
                          cwd=str(_REPO_ROOT), timeout=_ASK_TIMEOUT_S)
    if proc.returncode != 0:
        return {"grounded": False, "answer": "",
                "error": (proc.stderr.strip()[:300] or "librarian failed")}
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"grounded": False, "answer": "",
                "error": "librarian returned no answer"}


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
