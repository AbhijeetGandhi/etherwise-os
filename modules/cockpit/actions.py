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

# Re-run a task from the Activity run log (UI brief §3b). SAME mechanism as
# wake/rail — one _kick, one allowlist — keyed by the `runs.task_name` the run
# log actually displays, so the UI never has to know a launchd label.
#
# Membership mirrors the source registry's `catch_up` verdict ("safe to re-run
# unattended after a missed window", core/config.py SOURCES) and extends it only
# to jobs that are idempotent reads/mirrors. Deliberately ABSENT, though their
# plists exist in launchd/:
#   cos-brief-am / cos-brief-pm   outward brief with a send side-effect
#   finance-payment-mail          catch_up=False, staged and not loaded
#   catch-up                      kicks other jobs — an indirect exec surface
#   cockpit                       would restart the server serving the request
#   june15-credit-validation      a one-off, not a rail
#   upwork-outcomes               removed 2026-08-25 (Upwork chat): it is an
#                                 outward email once its send path exists, so
#                                 its SOURCES entry is catch_up False and the
#                                 registry test forbids it here; the deliberate
#                                 button is RAIL_JOBS "outcome-capture" above
# Every label here must exist as a plist in launchd/ (tests assert it).
RERUN_JOBS = {
    "upwork_scan": "io.etherwise.v3.upwork-scan",
    "upwork_sync": "io.etherwise.v3.upwork-sync",
    "upwork_inbox": "io.etherwise.v3.upwork-inbox",
    "upwork_connects": "io.etherwise.v3.upwork-connects",
    # (mcp_shadow_poll sat here 08-25 → 08-26; the poller retired at the 0.1
    # flip — the MCP round now runs inside upwork_scan.)
    "crm_mirror": "io.etherwise.v3.crm-mirror",
    "kb_daily": "io.etherwise.v3.kb-daily",
    "loom_fetch": "io.etherwise.v3.kb-loom-fetch",
    "cos_sweep": "io.etherwise.v3.cos-sweep",
    "jarvis_sweep": "io.etherwise.v3.jarvis-sweep",
    "jarvis_slack": "io.etherwise.v3.jarvis-slack",
    "finance_stripe": "io.etherwise.v3.finance-stripe",
    "finance_wise_mail": "io.etherwise.v3.finance-wise-mail",
    "finance_upwork_weekly": "io.etherwise.v3.finance-upwork-weekly",
    # the MCP ledger read (2026-08-26, use case A): read-only on Upwork, the
    # upsert dedups on the canonical key — safe to kick any time.
    "upwork_financials": "io.etherwise.v3.upwork-financials",
    # added 2026-08-13 with the SOURCES catch_up False->True flip. Safe to kick:
    # read-only IMAP, no sends, and upsert_provisional dedupes on canonical_key.
    "finance_payment_mail": "io.etherwise.v3.finance-payment-mail",
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


def rerun(db_path: Optional[Path], body: dict) -> dict:
    """POST /api/rerun — re-run an allowlisted task from the Activity run log.
    body: {task}, where task is the `runs.task_name` shown in the log. Refuses
    anything not in RERUN_JOBS (the cockpit must not become a launchd-exec
    surface); the kick itself is the same launchctl kickstart as wake/rail."""
    task = (body or {}).get("task")
    if task not in RERUN_JOBS:
        raise ValueError("unknown/forbidden rerun task: {!r}"
                         " (allowed: {})".format(task, sorted(RERUN_JOBS)))
    return {**_kick(RERUN_JOBS[task]), "task": task}


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
    # WRITE THROUGH TO THE OVERLAY — the mirror of apply_nudge's write-through
    # to the engine. Today renders the same counterparty as a NUDGE card or as a
    # FOLLOW-UP card depending on whether a draft exists, and the two used to
    # record their decisions in different tables. A Done pressed on one form was
    # then invisible to the other, so it came back the next time the shape
    # flipped (the Cradle Company, 2026-08-12). Same decision, both stores.
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO nudge_state (item_key, state, snooze_until, note,"
            " source, updated_at) VALUES (?,?,?,?, 'cockpit', datetime('now'))"
            " ON CONFLICT(item_key) DO UPDATE SET state=excluded.state,"
            " snooze_until=excluded.snooze_until, note=excluded.note,"
            " updated_at=datetime('now')",
            (dedup_key, _COS_STATE[action], snooze_until,
             (body or {}).get("note")))
        _capture_subject(conn, dedup_key)
    return {"ok": out["updated"] > 0, "dedup_key": dedup_key,
            "state": _COS_STATE[action]}


_NOTE_CLASSIFY_CLI = _REPO_ROOT / "bin" / "note-classify"


def _spawn_classifier(db_path: Optional[Path], note_id: int) -> dict:
    """Kick the capture classifier as a DETACHED child, and never let its
    failure cost the note.

    THE THREE CONSTRAINTS THIS RESOLVES, in the order they bind:

    1. No model calls in the cockpit process (UI_BUILD_BRIEF §0.7). So it is a
       child, the same seam `ask` uses with `bin/kb-ask`.
    2. It must not block the HTTP response for seconds. So it is `Popen`, not
       `run` — `ask` can afford to wait 45s because he is watching a spinner
       for an answer; here he is typing and expects the box to clear at once.
    3. It must not be able to lose the note. So the row is ALREADY COMMITTED
       before this is called, the exception is swallowed, and both remaining
       backstops still hold: the 2-hourly sweep still classifies unclassified
       notes, and Today renders them in the meantime with "waiting to be
       classified" rather than nothing at all.

    `start_new_session=True` is load-bearing: without it the child dies with
    the cockpit, and `bin/cockpit reload` is a thing that happens after every
    Python change on this server. The child must outlive its parent.
    """
    try:
        subprocess.Popen(                       # noqa: S603 — fixed argv
            [sys.executable, str(_NOTE_CLASSIFY_CLI), str(note_id)],
            cwd=str(_REPO_ROOT), start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        return {"spawned": True, "error": None}
    except Exception as exc:                    # noqa: BLE001 — see docstring
        return {"spawned": False, "error": f"{type(exc).__name__}: {exc}"}


def note(db_path: Optional[Path], body: dict) -> dict:
    """POST /api/note — Quick Capture from the cockpit.

    Commits the raw text to the CoS notes inbox, then spawns the classifier as
    a detached child so the card exists within seconds rather than at the next
    2-hourly sweep. Returns immediately; the box polls GET /api/note?note_id=N
    for the verdict. Local only — no external write.

    A rejected note (empty, or over the cap) raises, the server answers 500 with
    the message, and the UI leaves the text in the box. That is deliberate: the
    one thing this route may never do is report success over text it did not
    store."""
    from modules.chief_of_staff import capture
    text = ((body or {}).get("text") or "").strip()
    if not text:
        raise ValueError("text required")
    out = capture.add_note(db_path, text, source="cockpit")
    # db_path is None in production (the server passes its own), and the child
    # reads config.DB_PATH — so a TEST db must not spawn a child against the
    # live one. Tests pass a db_path; production does not.
    kick = ({"spawned": False, "error": "test db — classifier not spawned"}
            if db_path is not None and Path(db_path) != config.DB_PATH
            else _spawn_classifier(db_path, out["id"]))
    return {**out, "classifying": kick["spawned"],
            "classify_error": kick["error"],
            # what the toast may truthfully say
            "note": ("Classifying now — the card appears here in a moment."
                     if kick["spawned"] else
                     "Saved. The next sweep will classify it (within 2 hours)"
                     " — it is listed under Captured below until then.")}


def note_resolve(db_path: Optional[Path], body: dict) -> dict:
    """POST /api/note/resolve — his answer to a note that produced no card.

    body: {note_id, action: 'card'|'filed'}. 'card' overrides the classifier
    and mints the capture card; 'filed' agrees with it and stops Today showing
    the note. Both are HIS decision, and neither deletes anything: the note row
    survives either way, which is the whole contract of this feature."""
    from modules.chief_of_staff import capture
    body = body or {}
    note_id = body.get("note_id")
    if note_id in (None, ""):
        raise ValueError("note_id required")
    return capture.resolve(db_path, int(note_id), body.get("action") or "")


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


def _engine_key(item_key: str) -> str:
    """The nudges.dedup_key behind a cockpit item_key. Follow-up cards and their
    owed_reply nudges already share a key; the nudge surface just prefixes it."""
    return item_key[6:] if item_key.startswith("nudge:") else item_key


def _capture_subject(conn, item_key: str) -> None:
    """Write down WHICH CONVERSATIONS this decision was about.

    The key the decision is filed under is derived from attribution and changes
    the moment attribution improves — three times now (room -> person 08-12,
    person -> client 08-13). The threads it covers do not change. Recording them
    here is what lets the decision be found again under tomorrow's key; see
    modules.chief_of_staff.decisions.

    Never allowed to cost the decision itself: a failure here means the card may
    reappear after a future re-keying, which is bad; losing the Done outright is
    worse. Swallowed on purpose, and the founder's row is already written."""
    try:
        from modules.chief_of_staff import decisions
        decisions.capture(conn, item_key)
    except Exception:                       # noqa: BLE001 — see docstring
        pass


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
        _capture_subject(conn, item_key)
    # WRITE THROUGH TO THE ENGINE. nudge_state is a cockpit-local overlay; the
    # nudges table is what the morning brief, top_n and the sweep read. Until
    # 2026-08-12 'Done' wrote only the overlay, so a card the founder had closed
    # stayed open everywhere except the one list that happened to consult it —
    # and the sweep, seeing it open, kept refreshing it. Same decision, one
    # place. Silent no-op when the key is a follow-up with no nudge row yet.
    engine, engine_rows, engine_error = _engine_key(item_key), 0, None
    try:
        from modules.chief_of_staff import nudges as _n
        engine_rows = _n.set_state(db_path, state=state, dedup_key=engine,
                                   snooze_until=snooze_until,
                                   note=note)["updated"]
    except Exception as exc:       # the founder's action is recorded regardless
        engine_error = f"{type(exc).__name__}: {exc}"
    # engine_rows == 0 is NORMAL and not an error: Today lists a follow-up as
    # soon as its draft exists, while the sweep only mints the nudge after
    # COS_FOLLOWUP_IDLE_DAYS, so a card can be actioned before the engine has a
    # row for it. _is_actioned covers that window off nudge_state, and the
    # sweep's own evidence check covers it once the row appears. It is reported
    # rather than swallowed because a silent 0 here is indistinguishable from
    # the write-through being broken, which is the bug this exists to fix.
    return {"ok": True, "item_key": item_key, "state": state,
            "snooze_until": snooze_until, "engine_rows": engine_rows,
            "engine_error": engine_error}


def nudge(db_path: Optional[Path], body: dict,
          today: Optional[str] = None) -> dict:
    """POST /api/nudge handler. body: {item_key, action, snooze_days?, note?}"""
    return apply_nudge(db_path, body.get("item_key"), body.get("action"),
                       snooze_days=body.get("snooze_days"),
                       note=body.get("note"), today=today)


# ── Jarvis mapping-queue resolution (J1 D3) ──────────────────────────────────
# assign/create_alias write the registry via an AUDITED git commit (the only
# registry write path); ignore excludes the event. All go through the approval
# module so the cockpit process never touches clients.yaml directly.
_QUEUE_ACTIONS = {"assign", "create_alias", "ignore", "internal"}


def queue_resolve(db_path, body: dict) -> dict:
    from modules.jarvis import approve
    body = body or {}
    action, event_id = body.get("action"), body.get("event_id")
    if action not in _QUEUE_ACTIONS:
        return {"error": f"action must be one of {sorted(_QUEUE_ACTIONS)}"}
    if not event_id:
        return {"error": "event_id required"}
    return approve.resolve(db_path, event_id, action,
                           client_id=body.get("client_id"),
                           reason=body.get("reason"), by="cockpit-approval")


# ── batch resolution (Review) ────────────────────────────────────────────────
# 58 held emails from banks and airlines, and 29 Loom recordings the classifier
# was 72% sure about, are not 87 decisions — they are two. But a batch must not
# become a different KIND of write from the single action it repeats, so this
# is a loop over approve.resolve and nothing else: same authority, same git
# commits, same heal, same refusals. Each item succeeds or fails on its own and
# the failures are returned, never summarised away.
QUEUE_BATCH_MAX = 60


def queue_batch(db_path, body: dict) -> dict:
    """POST /api/queue/batch — resolve many queued/held events in one request.

    body: {event_ids: [...], action, client_id?, reason?}
      or  {items: [{event_id, action, client_id?, reason?}, ...]}

    The cap is real and enforced server-side: every assign can recompile a
    dossier (approve.resolve -> fathom_bridge.heal_source), so an unbounded
    batch is an unbounded request. The UI chunks; the server refuses."""
    from modules.jarvis import approve
    body = body or {}
    items = body.get("items")
    if items is None:
        ids = body.get("event_ids") or []
        if not isinstance(ids, list):
            return {"error": "event_ids must be a list"}
        items = [{"event_id": e, "action": body.get("action"),
                  "client_id": body.get("client_id"),
                  "reason": body.get("reason")} for e in ids]
    if not isinstance(items, list) or not items:
        return {"error": "nothing to do: pass event_ids[] or items[]"}
    if len(items) > QUEUE_BATCH_MAX:
        return {"error": "batch too large: {} items, cap is {}".format(
            len(items), QUEUE_BATCH_MAX)}

    results, applied, failed, commits = [], 0, 0, []
    for it in items:
        it = it or {}
        eid, action = it.get("event_id"), it.get("action")
        if action not in _QUEUE_ACTIONS:
            res = {"error": "action must be one of {}".format(sorted(_QUEUE_ACTIONS))}
        elif not eid:
            res = {"error": "event_id required"}
        else:
            try:
                res = approve.resolve(db_path, eid, action,
                                      client_id=it.get("client_id"),
                                      reason=it.get("reason"),
                                      by="cockpit-approval")
            except Exception as exc:            # noqa: BLE001 — one bad row
                res = {"error": repr(exc)[:300]}   # must not lose the other 59
        ok = bool(res.get("ok"))
        applied += 1 if ok else 0
        failed += 0 if ok else 1
        sha = (res.get("registry") or {}).get("commit")
        if sha and sha not in commits:
            commits.append(sha)
        results.append({"event_id": eid, "action": action, "ok": ok,
                        "error": res.get("error"),
                        "registry": res.get("registry"),
                        "healed": bool(res.get("heal"))})
    return {"ok": failed == 0, "total": len(items), "applied": applied,
            "failed": failed, "commits": commits, "results": results}


# ── Upwork thread review (the third backlog) ─────────────────────────────────
# THE DISMISSAL STORE. `nudge_state` under 'review:thread:<id>' — the cockpit's
# own decision overlay, the same table Today's Done writes to and the same table
# data._is_actioned reads. See the long note above data.review_threads for why
# it is not a column on `threads` and not a fake `events` row.
#
# The verdict here is about IDENTITY, not about a task, so unlike a Done it does
# NOT decay: nothing in the reader lets a new message resurrect it. Restore
# DELETES the row — a reversal should leave no trace of itself, otherwise the
# next reader has to know which of two states means "never decided".
REVIEW_THREAD_PREFIX = "review:thread:"
REVIEW_THREAD_ACTIONS = {"dismiss", "restore", "assign", "create_client"}
REVIEW_BATCH_MAX = 100
DEFAULT_DISMISS_REASON = "prospect — never converted"


def _thread_key(thread_id: str) -> str:
    return REVIEW_THREAD_PREFIX + thread_id


def _thread_evidence(db_path, thread_id: str) -> str:
    """The one-line audit note a registry commit carries for a thread write.
    Read from the DB, never from the request body: the evidence in a permanent
    commit must be what the system can see, not what a form said."""
    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT room_name, topic, proposal_id, latest_message_dt,"
            " (SELECT COUNT(*) FROM messages m WHERE m.thread_id=t.id) n"
            " FROM threads t WHERE id=?", (thread_id,)).fetchone()
    if not row:
        return "upwork room {}".format(thread_id)
    bits = ["upwork room {}".format(thread_id),
            "'{}'".format(row["room_name"] or "(unnamed)"),
            "{} messages".format(row["n"])]
    if row["latest_message_dt"]:
        bits.append("last {}".format(str(row["latest_message_dt"])[:10]))
    if row["proposal_id"]:
        bits.append("proposal {}".format(row["proposal_id"]))
    return "; ".join(bits)


def review_thread(db_path, body: dict) -> dict:
    """POST /api/review/thread — the ONLY write path for the thread backlog.

    body: {thread_ids: [...], action, client_id?, reason?, new_client?}
      dismiss        record 'not a client' permanently (local, no git)
      restore        undo a dismissal (deletes the row)
      assign         claim the room for an EXISTING registry client (git commit)
      create_client  make a NEW registry entry from ONE thread, then assign it

    create_client is capped at one thread on purpose. Every other action here is
    a repetition of the same small decision; that one is a new identity in the
    spine, and a form that could mint several at once is a form that will."""
    from modules.jarvis import approve
    body = body or {}
    action = body.get("action")
    if action not in REVIEW_THREAD_ACTIONS:
        return {"error": "action must be one of {}".format(
            sorted(REVIEW_THREAD_ACTIONS))}
    ids = body.get("thread_ids")
    if ids is None and body.get("thread_id"):
        ids = [body.get("thread_id")]
    if not isinstance(ids, list) or not ids:
        return {"error": "thread_ids required"}
    ids = [str(i) for i in ids if i]
    if len(ids) > REVIEW_BATCH_MAX:
        return {"error": "batch too large: {} threads, cap is {}".format(
            len(ids), REVIEW_BATCH_MAX)}

    if action in ("dismiss", "restore"):
        reason = (body.get("reason") or DEFAULT_DISMISS_REASON).strip()
        with db.connect(db_path) as conn:
            known = {r["id"] for r in conn.execute(
                "SELECT id FROM threads WHERE id IN ({})".format(
                    ",".join("?" * len(ids))), ids)}
            unknown = [i for i in ids if i not in known]
            for tid in sorted(known):
                if action == "dismiss":
                    conn.execute(
                        "INSERT INTO nudge_state (item_key, state, note, source,"
                        " updated_at) VALUES (?, 'dismissed', ?, 'cockpit',"
                        " datetime('now')) ON CONFLICT(item_key) DO UPDATE SET"
                        " state='dismissed', note=excluded.note,"
                        " snooze_until=NULL, updated_at=datetime('now')",
                        (_thread_key(tid), reason))
                else:
                    conn.execute("DELETE FROM nudge_state WHERE item_key=?",
                                 (_thread_key(tid),))
        return {"ok": not unknown, "action": action, "total": len(ids),
                "applied": len(known), "failed": len(unknown),
                "unknown": unknown,
                "reason": reason if action == "dismiss" else None,
                "note": ("Recorded in nudge_state as 'dismissed'. It will not"
                         " come back on its own — press Restore, or let the"
                         " thread gain a client_id."
                         if action == "dismiss" else
                         "Dismissal deleted — the thread is back in the queue.")}

    if action == "create_client":
        nc = body.get("new_client") or {}
        if len(ids) != 1:
            return {"error": "create_client takes exactly one thread"
                             " (got {})".format(len(ids))}
        tid = ids[0]
        ev = _thread_evidence(db_path, tid)
        created = approve.create_client(
            nc.get("id") or "", nc.get("name") or "",
            kind=nc.get("kind") or "prospect", upwork_room=tid, evidence=ev,
            by="cockpit-thread-review")
        if created.get("error"):
            return {"ok": False, "action": action, "total": 1, "applied": 0,
                    "failed": 1, "results": [{"thread_id": tid,
                                              "ok": False,
                                              "error": created["error"]}]}
        linked = approve.assign_thread(db_path, tid, created["client_id"],
                                       evidence=ev, by="cockpit-thread-review")
        ok = bool(linked.get("ok"))
        return {"ok": ok, "action": action, "total": 1,
                "applied": 1 if ok else 0, "failed": 0 if ok else 1,
                "created": created,
                "commits": [c for c in [created.get("commit")] if c],
                "results": [{"thread_id": tid, "ok": ok,
                             "error": linked.get("error"),
                             "client_id": created["client_id"],
                             "registry": created.get("registry")}]}

    # assign
    client_id = body.get("client_id")
    if not client_id:
        return {"error": "assign requires client_id"}
    results, applied, failed, commits = [], 0, 0, []
    for tid in ids:
        try:
            res = approve.assign_thread(
                db_path, tid, client_id, evidence=_thread_evidence(db_path, tid),
                by="cockpit-thread-review")
        except Exception as exc:                # noqa: BLE001
            res = {"error": repr(exc)[:300]}
        ok = bool(res.get("ok"))
        applied += 1 if ok else 0
        failed += 0 if ok else 1
        sha = (res.get("registry") or {}).get("commit")
        if sha and sha not in commits:
            commits.append(sha)
        results.append({"thread_id": tid, "ok": ok, "error": res.get("error"),
                        "client_id": res.get("client_id"),
                        "client_name": res.get("client_name"),
                        "registry": res.get("registry")})
    return {"ok": failed == 0, "action": action, "total": len(ids),
            "applied": applied, "failed": failed, "commits": commits,
            "results": results}


# ── Card closure confirm/reject (J1.5) ───────────────────────────────────────
# A possibly-resolved card shows a "confirm?" chip. Confirm closes it (hidden);
# reject keeps it open and stops re-chipping. Resolves the card by its item_key.
def card_closure(db_path, body: dict) -> dict:
    from modules.chief_of_staff import nudges
    body = body or {}
    item_key, action = body.get("item_key") or "", body.get("action")
    if action not in ("confirm", "reject"):
        return {"error": "action must be confirm|reject"}
    dedup_key = item_key[6:] if item_key.startswith("nudge:") else item_key
    if not dedup_key:
        return {"error": "item_key required"}
    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM nudges WHERE dedup_key=? AND state IN ('open','snoozed')"
            " ORDER BY id DESC LIMIT 1", (dedup_key,)).fetchone()
        if not row:
            # A closure chip can now be raised at RENDER time from deterministic
            # cross-channel evidence (data.today: "you already replied on Slack
            # after their last message"), so a card can carry the chip while the
            # engine row is done/decayed or has never been minted at all. The
            # decision must still land somewhere — returning "no open card" made
            # the founder press a button that visibly did nothing, which is the
            # exact failure this whole area exists to stop.
            row = conn.execute(
                "SELECT id FROM nudges WHERE dedup_key=? ORDER BY id DESC"
                " LIMIT 1", (dedup_key,)).fetchone()
    state = "confirmed" if action == "confirm" else "rejected"
    if row:
        return nudges.set_closure(db_path, row["id"], closure_state=state)
    # no engine row at all -> record it where a follow-up card's actions live.
    # 'confirm' means handled (same as Done); 'reject' means keep it open, which
    # for a card with no stored state is already true, so it is a no-op we SAY.
    if action == "confirm":
        res = apply_nudge(db_path, item_key, "done")
        return {"updated": 1, "closure_state": state, "via": "nudge_state",
                "engine_rows": res.get("engine_rows", 0)}
    return {"updated": 0, "closure_state": state, "via": "none",
            "note": "no stored card for that key — it was already open"}
