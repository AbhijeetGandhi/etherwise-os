"""room_link — tie an email to the Upwork room whose counterparty sent it.

WHY (Abhijeet, 2026-08-26 04:58): David Séjourné emailed the 01 inbox on 24 Aug
with the brief for the first workflow; on 25 Aug his Upwork message said "I've
sent you an email". The IMAP rail had the mail, the sweep excluded it
(imap-low-base-rate:no-client-signal — a prospect is not a registry client),
and the draft-writer, which reads the Upwork room alone, answered "I don't see
the email". Both halves were in the DB; nothing joined them.

THE RULE, deterministic and cheap (no model call): Upwork renders a client as
"First L." (messages.sender_user_name); a mail carries a display name ("David
Séjourné") or at least an address local part ("david"). A mail matches a room
when the first names agree (accent- and case-insensitive) and the surname
initial agrees when both sides have one, and the room had an inbound message
within config.UPWORK_ROOM_LINK_DAYS. Two rooms with two different people who
both match → ambiguous → no link (a wrong link would put one prospect's brief
into another's draft, which is worse than no link). Two rooms with the SAME
person (two job posts) → the most recently active one.

WHAT A LINK DOES: the sweep queues the mail like any other real mail (Review
still sees it, with the room named in the evidence) instead of excluding it;
the Upwork Inbox row shows it; the draft-writer is given its text. Nothing
here sends anything or writes outside room_links/events.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

from core import config

_FROM_RE = re.compile(r"^From:\s*(.+?)\s*$", re.I | re.M)
_ADDR_RE = re.compile(r"<([^>]+)>")
_ENC_RE = re.compile(r"=\?([^?]+)\?([BbQq])\?([^?]*)\?=")


def _decode_words(s: str) -> str:
    """RFC 2047 encoded-words ("=?UTF-8?B?…?=") → text; anything odd stays."""
    def one(m):
        charset, enc, payload = m.group(1), m.group(2).upper(), m.group(3)
        try:
            if enc == "B":
                import base64
                return base64.b64decode(payload).decode(charset, "replace")
            import quopri
            return quopri.decodestring(payload.replace("_", " ")).decode(charset, "replace")
        except Exception:                       # noqa: BLE001 — a bad header is not fatal
            return payload
    return _ENC_RE.sub(one, s or "")


def _fold(s: str) -> str:
    """lower-case, accents stripped, punctuation → spaces."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def sender_name(author: Optional[str], text: Optional[str]) -> tuple:
    """(first, surname_initial|None, display) for a mail: the From: display
    name when the event text carries one, else the address's local part."""
    display = None
    m = _FROM_RE.search(text or "")
    if m:
        raw = _decode_words(m.group(1))
        raw = _ADDR_RE.sub("", raw).strip().strip('"').strip()
        if raw and "@" not in raw:
            display = raw
    if not display:
        local = (author or "").split("@")[0]
        display = re.sub(r"[._-]+", " ", local)
    toks = [t for t in _fold(display).split() if t]
    if not toks:
        return None, None, display
    first = toks[0]
    initial = toks[-1][0] if len(toks) > 1 else None
    return first, initial, display


def upwork_name(sender_user_name: Optional[str]) -> tuple:
    """"David S." → ("david", "s"); "Nabil Tayeb Cherif" → ("nabil", "c")."""
    toks = [t for t in _fold(sender_user_name or "").split() if t]
    if not toks:
        return None, None
    return toks[0], (toks[-1][0] if len(toks) > 1 else None)


def candidates(conn: sqlite3.Connection, days: Optional[int] = None) -> list:
    """Counterparties of the rooms active inside the window: one row per
    (room, inbound sender name) with the room's newest inbound instant."""
    days = config.UPWORK_ROOM_LINK_DAYS if days is None else days
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    rows = conn.execute(
        "SELECT m.thread_id, m.sender_user_name AS name, MAX(m.created_dt) AS latest,"
        " t.room_name, t.client_id"
        " FROM messages m JOIN threads t ON t.id = m.thread_id"
        " WHERE m.direction='inbound' AND m.sender_user_name IS NOT NULL"
        " AND m.created_dt >= ?"
        " GROUP BY m.thread_id, m.sender_user_name ORDER BY latest DESC", (since,)).fetchall()
    return [dict(r) for r in rows]


def match_event(conn: sqlite3.Connection, ev: dict, days: Optional[int] = None) -> Optional[dict]:
    """The room a mail belongs to, or None. `ev` needs author + text (the
    events row shape). Returns {thread_id, matched_name, client_id, how}."""
    first, initial, _display = sender_name(ev.get("author"), ev.get("text"))
    if not first:
        return None
    hits = []
    for c in candidates(conn, days):
        cf, ci = upwork_name(c["name"])
        if cf != first:
            continue
        if initial and ci and initial != ci:
            continue
        hits.append(c)
    if not hits:
        return None
    # the same person in two rooms (two job posts, one client name) is one
    # person; two rooms with different counterparties who both match is
    # ambiguity, and ambiguity links nothing. Upwork shows "First L." only, so
    # the room's own name (the client / company line) is the tie-breaker.
    people = {(upwork_name(h["name"]), _fold(h["room_name"] or "")) for h in hits}
    if len(people) > 1:
        return None
    best = hits[0]                               # newest inbound first
    return {"thread_id": best["thread_id"], "matched_name": best["name"],
            "client_id": best["client_id"],
            "how": "name-match" + ("" if initial else "-first-only")}


def record(conn: sqlite3.Connection, event_id: str, link: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO room_links (event_id, thread_id, how, matched_name)"
        " VALUES (?,?,?,?)",
        (event_id, link["thread_id"], link["how"], link.get("matched_name")))


def evidence(link: dict, room_name: Optional[str] = None) -> str:
    return (f"matches Upwork room {link['thread_id']}"
            f" ({room_name or link.get('matched_name')}) by {link['how']}")


def linked_mail(conn: sqlite3.Connection, thread_ids, limit: int = 5) -> list:
    """The emails linked to these rooms, newest first: id, ts, author,
    display name, subject, a bounded body, the Gmail permalink."""
    ids = [t for t in (thread_ids or []) if t]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    out = []
    for r in conn.execute(
            f"SELECT e.id, e.ts, e.author, e.subject, e.text, e.permalink, e.state,"
            f" l.thread_id, l.matched_name FROM room_links l JOIN events e ON e.id = l.event_id"
            f" WHERE l.thread_id IN ({marks}) ORDER BY e.ts DESC LIMIT ?", (*ids, limit)):
        _f, _i, display = sender_name(r["author"], r["text"])
        out.append({"id": r["id"], "at": r["ts"], "author": r["author"],
                    "sender": display or r["author"], "subject": r["subject"],
                    "body": body_text(r["text"]), "link": r["permalink"],
                    "state": r["state"], "thread_id": r["thread_id"]})
    return out


def body_text(text: Optional[str], limit: Optional[int] = None) -> str:
    """The mail body without the From:/Subject: header lines the rail
    prepends, whitespace folded, bounded."""
    limit = config.UPWORK_ROOM_LINK_MAIL_CHARS if limit is None else limit
    lines = []
    for ln in (text or "").splitlines():
        if re.match(r"^(From|Subject|To|Cc|Date):", ln, re.I):
            continue
        lines.append(ln)
    t = re.sub(r"[ \t]+", " ", "\n".join(lines)).strip()
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t[:limit] + ("…" if len(t) > limit else "")


def backfill(conn: sqlite3.Connection, days: Optional[int] = None) -> dict:
    """Re-examine the mail the gate excluded for lack of a client signal
    inside the window; link and queue what matches a room now. Idempotent."""
    from modules.jarvis import events as _events
    days = config.UPWORK_ROOM_LINK_DAYS if days is None else days
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    rows = [dict(r) for r in conn.execute(
        "SELECT id, author, subject, text FROM events WHERE source='email'"
        " AND state='excluded' AND exclude_reason='imap-low-base-rate:no-client-signal'"
        " AND ts >= ? AND id NOT IN (SELECT event_id FROM room_links)", (since,))]
    linked = []
    for ev in rows:
        link = match_event(conn, ev, days)
        if not link:
            continue
        record(conn, ev["id"], link)
        room = conn.execute("SELECT room_name FROM threads WHERE id=?",
                            (link["thread_id"],)).fetchone()
        _events.set_queued(conn, ev["id"], link["client_id"], 0.9,
                           evidence(link, room["room_name"] if room else None))
        linked.append({"event_id": ev["id"], "thread_id": link["thread_id"],
                       "subject": ev.get("subject")})
    return {"examined": len(rows), "linked": linked}
