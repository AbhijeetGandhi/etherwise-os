"""promises — a promise typed in an Upwork message becomes a task on Today.

WHY (Abhijeet, 2026-08-26 05:20): "we told David that we will let him know
immediately — that should become part of the Today tab because it's a task".
He believed this existed. Commitments did (660 rows), but every one came from a
Fathom call: the Upwork path (a `sources` row of type 'upwork' fed to the
extractor) was never scheduled, so "thank you david, I will take a look and let
you know" (05:07 IST, typed on Upwork) was invisible to Today.

HOW (the inbox poll, after ingesting; also "Check Upwork now"):
  1. every NEW outbound message since the cursor (messages.ingested_at) that
     passes a cheap phrase gate — "I'll", "will send", "let you know", "get
     back to you", "by Friday" … (config.UPWORK_PROMISE_PHRASES; no model
     call for the 90 % that are "thanks!") — goes, one message at a time with
     the two messages before it as context, to the same Sonnet extractor the
     Fathom path uses (chief_of_staff.commitments SYSTEM + SCHEMA). Only
     who='us' rows are kept.
  2. each lands in `commitments` with source_cite 'upwork/<room> @ <message
     id>' and a dedup key on (room, message, text) — idempotent across polls.
     A promise with no stated date is due config.UPWORK_PROMISE_DAYS after it
     was made; the card says so (rules.rule_commitments_due).
     At most UPWORK_PROMISE_MAX_PER_RUN extractor calls per poll (the first
     run after deploy sees a week of outbound mail); the rest wait.
  3. AUTO-CLEAR (UI law 5 — every card carries an observed clear condition):
     a LATER outbound message in the same room marks the promise done at the
     next poll — following up IS the delivery of "I'll let you know". The
     sweep then clears the card. Done by hand on Today works as before.

Nothing here sends anything. A failure in this pass is a metric on the poll
(`promise_errors`), never a failed inbox run: the cursor does not move past a
message the extractor could not read, so it is retried next time.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from core import config, db
from modules.chief_of_staff import commitments as _commitments

CURSOR = "upwork_promise_ingested_at"
SOURCE_PREFIX = "upwork/"


def _phrase_re():
    return re.compile("|".join(f"(?:{p})" for p in config.UPWORK_PROMISE_PHRASES), re.I)


def looks_like_promise(text: Optional[str]) -> bool:
    """The gate: does this message contain a promise-shaped phrase? Cheap and
    deliberately generous — the extractor decides; the gate only spares the
    model the "thanks!" and "sounds good" that make up most outbound mail."""
    t = " ".join((text or "").split())
    if len(t) < 8:
        return False
    return bool(_phrase_re().search(t))


def _utc_now(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def new_outbound(conn: sqlite3.Connection, now: Optional[datetime] = None) -> list:
    """Outbound messages ingested after the cursor (or, on the first run,
    within UPWORK_PROMISE_LOOKBACK_DAYS — months of old promises are not a
    backlog worth minting), oldest first."""
    row = conn.execute("SELECT value FROM sync_cursors WHERE name=?", (CURSOR,)).fetchone()
    floor = (_utc_now(now) - timedelta(days=config.UPWORK_PROMISE_LOOKBACK_DAYS)
             ).strftime("%Y-%m-%d %H:%M:%S")
    since = row["value"] if row and row["value"] > floor else floor
    # both clocks: ingested after the cursor (new to us) AND said inside the
    # window (new at all). The first live run (10:15, 26 Aug) read two 2025
    # messages from a room whose history had just been re-pulled — a message
    # can be ingested this week and a year old.
    said_floor = floor.replace(" ", "T")
    return [dict(r) for r in conn.execute(
        "SELECT m.id, m.thread_id, m.message, m.created_dt, m.ingested_at,"
        " t.room_name, t.client_id FROM messages m JOIN threads t ON t.id = m.thread_id"
        " WHERE lower(m.direction) = 'outbound' AND m.ingested_at > ?"
        " AND substr(replace(m.created_dt, ' ', 'T'), 1, 19) >= ?"
        " AND TRIM(COALESCE(m.message,'')) != '' ORDER BY m.ingested_at, m.created_dt",
        (since, said_floor))]


def _set_cursor(conn: sqlite3.Connection, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_cursors (name, value, updated_at) VALUES (?,?,datetime('now'))"
        " ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
        (CURSOR, value))


def context_before(conn: sqlite3.Connection, msg: dict, n: int = 2) -> list:
    """The n messages before this one in the room — the question a promise
    answers ("can you look at the brief?" → "I'll take a look")."""
    return [dict(r) for r in conn.execute(
        "SELECT direction, sender_user_name, message, created_dt FROM messages"
        " WHERE thread_id=? AND created_dt < ? AND TRIM(COALESCE(message,'')) != ''"
        " ORDER BY created_dt DESC LIMIT ?", (msg["thread_id"], msg["created_dt"], n))][::-1]


def dedup_key(thread_id: str, message_id: str, text: str) -> str:
    norm = " ".join((text or "").lower().split())
    return "commitment:" + hashlib.sha256(
        f"{SOURCE_PREFIX}{thread_id}|{message_id}|{norm}".encode()).hexdigest()[:16]


def source_cite(thread_id: str, message_id: str) -> str:
    return f"{SOURCE_PREFIX}{thread_id} @ {message_id}"


def parse_cite(cite: Optional[str]) -> Optional[tuple]:
    """'upwork/<room> @ <message>' → (room, message) or None."""
    if not cite or not cite.startswith(SOURCE_PREFIX):
        return None
    body = cite[len(SOURCE_PREFIX):]
    room, _sep, mid = body.partition(" @ ")
    return (room.strip(), mid.strip() or None) if room.strip() else None


def _user_content(msg: dict, before: list) -> str:
    when = (msg.get("created_dt") or "")[:10] or "unknown"
    lines = [f"Source: Upwork room {msg['thread_id']} ({msg.get('room_name') or 'a client'})",
             f"Source date: {when}",
             "The LAST message below is by Abhijeet (us). Extract only the promises",
             "HE makes in that last message; the earlier lines are context.",
             # the first live run (26 Aug) minted "stay in the call waiting for
             # Carly" from a check-in and "assist Rocky" from "I can definitely
             # assist you" — a promise is a specific thing he will DO next
             "A commitment is a specific thing he says he WILL DO after this message",
             "(send, share, build, fix, check, call, get back, let them know).",
             "NOT a commitment: saying he can or would be able to do something, offering",
             "a time for a call, a check-in, thanks, or a description of work already done.",
             ""]
    for b in before:
        who = "us" if (b.get("direction") or "").lower() == "outbound" else (b.get("sender_user_name") or "client")
        lines.append(f"[{(b.get('created_dt') or '')[:10]}] {who}: {(b.get('message') or '')[:600]}")
    lines.append(f"[{when}] us: {(msg.get('message') or '')[:1500]}")
    return "\n".join(lines)


def extract(ctx, conn: sqlite3.Connection, msg: dict) -> list:
    """The model reads ONE outbound message (with context) → the promises we
    made in it. Same system prompt and schema as the Fathom path."""
    result = ctx.claude(
        model_key=_commitments.MODEL_KEY, schema=_commitments.SCHEMA,
        system=_commitments.SYSTEM, user_content=_user_content(msg, context_before(conn, msg)),
        purpose=f"promise {msg['thread_id']}", max_output_tokens=600)
    found = (result.parsed or {}).get("commitments", []) if result else []
    out = []
    for c in found:
        text = (c.get("text") or "").strip()
        quote = (c.get("quote") or "").strip()
        if (c.get("who") or "us") != "us" or not text or not quote:
            continue
        out.append({"text": text, "due": c.get("due"), "quote": quote})
    return out


def record(conn: sqlite3.Connection, msg: dict, c: dict) -> bool:
    """One commitments row per promise; True when new."""
    key = dedup_key(msg["thread_id"], msg["id"], c["text"])
    try:
        conn.execute(
            "INSERT INTO commitments (who, client_id, text, due, source_cite,"
            " dedup_key, state) VALUES ('us', ?, ?, ?, ?, ?, 'open')",
            (msg.get("client_id"), c["text"], c.get("due"),
             source_cite(msg["thread_id"], msg["id"]), key))
        return True
    except sqlite3.IntegrityError:          # dedup_key UNIQUE → already have it
        return False


def auto_close(conn: sqlite3.Connection) -> int:
    """A later outbound message in the same room = the promise was followed
    up. Marks such open Upwork promises done; returns how many."""
    closed = 0
    for r in conn.execute(
            "SELECT id, source_cite FROM commitments WHERE state='open'"
            " AND who='us' AND source_cite LIKE ?", (SOURCE_PREFIX + "%",)):
        parsed = parse_cite(r["source_cite"])
        if not parsed or not parsed[1]:
            continue
        room, mid = parsed
        later = conn.execute(
            "SELECT 1 FROM messages later JOIN messages promise ON promise.id = ?"
            " WHERE later.thread_id = ? AND lower(later.direction) = 'outbound'"
            " AND later.id != promise.id AND later.created_dt > promise.created_dt"
            " AND TRIM(COALESCE(later.message,'')) != '' LIMIT 1", (mid, room)).fetchone()
        if later:
            conn.execute("UPDATE commitments SET state='done', updated_at=datetime('now')"
                         " WHERE id=?", (r["id"],))
            closed += 1
    return closed


def run(ctx, db_path=None, now: Optional[datetime] = None) -> dict:
    """The pass the inbox poll calls after ingesting. Never raises."""
    dbp = db_path or ctx.db_path or config.DB_PATH
    m = {"promise_scanned": 0, "promise_candidates": 0, "promises_found": 0,
         "promises_kept": 0, "promise_deferred": 0, "promise_errors": 0}
    try:
        with db.connect(dbp) as conn:
            msgs = new_outbound(conn, now)
        m["promise_scanned"] = len(msgs)
        last_ok = None
        for msg in msgs:
            if looks_like_promise(msg.get("message")):
                if m["promise_candidates"] >= config.UPWORK_PROMISE_MAX_PER_RUN:
                    m["promise_deferred"] += 1      # the cursor stops before it
                    break
                m["promise_candidates"] += 1
                try:
                    with db.connect(dbp) as conn:
                        found = extract(ctx, conn, msg)
                except Exception:                     # noqa: BLE001 — retried next poll
                    m["promise_errors"] += 1
                    break                             # the cursor stops here
                with db.connect(dbp) as conn:
                    for c in found:
                        if record(conn, msg, c):
                            m["promises_found"] += 1
            last_ok = msg["ingested_at"]
        with db.connect(dbp) as conn:
            if last_ok:
                _set_cursor(conn, last_ok)
            m["promises_kept"] = auto_close(conn)
    except Exception:                                 # noqa: BLE001 — a metric, not a failed poll
        m["promise_errors"] += 1
    return m
