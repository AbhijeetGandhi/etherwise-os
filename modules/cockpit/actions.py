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


# ── Finance ingest (upload -> stage -> dry-run -> commit) ─────────────────────
# The cockpit NEVER writes the ledger itself: uploads only STAGE files into the
# existing finance-inbox drop folders, and imports run through the SAME audited
# importer bins Abhijeet runs by hand (reconcile-or-abort, idempotent, dry-run
# default). Commit stays an explicit second click.
FINANCE_INBOX = _REPO_ROOT / "finance-inbox"
UPLOAD_KINDS = {
    # kind: (subdir, allowed extensions, server-side naming)
    "idfc":           ("bank",   (".xlsx",),                None),
    "wise":           ("wise",   (".csv",),                 None),
    "card":           ("card",   (".pdf",),                 "{d}-card-statement.pdf"),
    "upwork-personal": ("upwork", (".csv",), "{d}-upwork-personal-fullhistory.csv"),
    "upwork-agency":   ("upwork", (".csv",), "{d}-upwork-agency-fullhistory.csv"),
    # Stripe export (balance_history and/or the Payments export) — the balance
    # file name is preserved so a re-upload of the same export dedups by name
    "stripe":         ("stripe", (".csv",),                 None),
}
UPLOAD_MAX_BYTES = 20 * 1024 * 1024
_IMPORTER_BIN = {"idfc": "finance-import-idfc", "wise": "finance-import-wise",
                 "card": "finance-import-card",
                 "upwork-personal": "finance-import-upwork",
                 "upwork-agency": "finance-import-upwork",
                 "stripe": "finance-import-stripe"}


def _safe_name(filename: str) -> str:
    """Keep a recognizable client name, neutralize traversal/separators.
    Truncation PRESERVES the extension (a Google-Drive-token filename once lost
    its .pdf to the 80-char cut — 2026-07-07)."""
    base = Path(filename or "upload").name
    clean = "".join(c if (c.isalnum() or c in "._- ") else "_" for c in base)
    stem, ext = Path(clean).stem, Path(clean).suffix[:10]
    return stem[:80 - len(ext)] + ext


def finance_upload(kind: str, filename: str, data: bytes) -> dict:
    """Stage an uploaded statement/export into its drop folder. Raw-body route
    (not JSON). Validates kind, extension, size; never trusts the client path;
    never overwrites an existing file (suffixes instead). Writes NOTHING to the
    ledger — that is the importer's job, behind the explicit import action."""
    spec = UPLOAD_KINDS.get(kind)
    if not spec:
        return {"ok": False, "error": f"unknown kind {kind!r}"}
    subdir, exts, forced = spec
    if not data:
        return {"ok": False, "error": "empty upload"}
    if len(data) > UPLOAD_MAX_BYTES:
        return {"ok": False, "error": "file too large (20MB cap)"}
    ext = Path(filename or "").suffix.lower()
    if ext not in exts:
        return {"ok": False,
                "error": f"{kind} expects {'/'.join(exts)} (got {ext or 'no ext'})"}
    name = (forced.format(d=date.today().isoformat()) if forced
            else _safe_name(filename))
    target_dir = FINANCE_INBOX / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    n = 2
    while target.exists():                      # never clobber a staged file
        target = target_dir / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    target.write_bytes(data)
    return {"ok": True, "kind": kind, "staged_as": target.name,
            "dir": f"finance-inbox/{subdir}", "bytes": len(data),
            "importable": True, "note": None}


def _load_importer(bin_name: str):
    import importlib.machinery
    import importlib.util
    path = _REPO_ROOT / "bin" / bin_name
    loader = importlib.machinery.SourceFileLoader(bin_name.replace("-", "_"), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def finance_import(db_path: Optional[Path], body: dict) -> dict:
    """POST /api/finance/import — run an importer over its inbox. body:
    {kind, commit: bool}. Dry-run unless commit=true (the same human gate as
    the CLI). Returns the importer's own report + the doctor verdict after a
    commit, so the UI shows the gate state alongside the result."""
    kind = (body or {}).get("kind") or ""
    commit = bool((body or {}).get("commit"))
    bin_name = _IMPORTER_BIN.get(kind)
    if not bin_name:
        return {"ok": False, "error": f"unknown kind {kind!r}"}
    mod = _load_importer(bin_name)
    try:
        report = mod.run(commit)   # all importers: run(apply/commit: bool)
    except Exception as exc:                    # noqa: BLE001
        return {"ok": False, "kind": kind, "error": repr(exc)[:300]}
    out = {"ok": True, "kind": kind, "commit": commit,
           "report": {k: v for k, v in report.items() if not k.startswith("_")}}
    if commit:
        # bucketize new rows immediately (the documented post-import step —
        # otherwise the P&L misses them until the next manual run), then show
        # the gate state post-write.
        try:
            out["bucketized"] = _load_importer("finance-bucketize").run(True)["changed"]
        except Exception as exc:                # noqa: BLE001
            out["bucketize_error"] = repr(exc)[:200]
        from modules.finance import doctor
        dr = doctor.run(db_path or config.DB_PATH)
        out["doctor"] = "GREEN" if dr.green else "RED"
        if not dr.green:
            out["reds"] = [c.invariant for c in dr.reds()]
    return out


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

_COS_STATE = {"done": "done", "snooze": "snoozed", "dismiss": "dismissed"}


def cos_nudge(db_path: Optional[Path], body: dict) -> dict:
    """POST /api/cos-nudge — act on a REAL M4 nudge (the action layer over the
    nudges table; replaces the proto nudge_state path). body: {dedup_key,
    action: done|snooze|dismiss, snooze_days?, note?}."""
    from modules.chief_of_staff import nudges as eng
    action = (body or {}).get("action")
    if action not in _COS_STATE:
        raise ValueError(f"invalid nudge action: {action!r}")
    dedup_key = (body or {}).get("dedup_key")
    if not dedup_key:
        raise ValueError("dedup_key required")
    snooze_until = None
    if action == "snooze":
        days = (body or {}).get("snooze_days") or DEFAULT_SNOOZE_DAYS
        base = date.fromisoformat(__import__("datetime").datetime.now(
            config.TZ).strftime("%Y-%m-%d"))
        snooze_until = (base + timedelta(days=days)).isoformat()
    out = eng.set_state(db_path, state=_COS_STATE[action], dedup_key=dedup_key,
                        snooze_until=snooze_until, note=(body or {}).get("note"))
    return {"ok": out["updated"] > 0, "dedup_key": dedup_key,
            "state": _COS_STATE[action]}


def note(db_path: Optional[Path], body: dict) -> dict:
    """POST /api/note — Quick Capture from the cockpit. Drops raw text into the
    CoS notes inbox; the next sweep classifies it (nudge/commitment/kb/idea).
    Local only — no external write."""
    from modules.chief_of_staff import capture
    text = ((body or {}).get("text") or "").strip()
    if not text:
        raise ValueError("text required")
    return capture.add_note(db_path, text, source="cockpit")


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
