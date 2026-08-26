"""One person, several rooms.

Abhijeet, 2026-08-27: *"if I'm talking with Tanya, and we have an old chat, but
now they send out a new contract, and it starts the new chat … the context
moves on to the new chat. But then in the Today tab, we are still seeing if my
reply was pending on the old chat."*

He is right, and it was live on three people that morning:

  Dhanya Sangolli   a pending draft on a room last touched 30 Jul — while he
                    spoke to her on 26 Aug and started a $300 contract that day
  Rocky Choy        a pending draft on a room he never replied in at all —
                    while he spoke to Rocky on 25 Aug in another room
  Royan Stomberg    draft on the 16 Aug room; he answered on the 17th, next door

THE CARD was never the problem: `nudges.followup_key` has grouped by person
since 12 August. What was wrong is narrower and worse — the EVIDENCE read was
handed only the rooms that carried a pending draft, so a reply sent in the
sibling room was invisible, `answered_since` could never become true, and the
card nagged about a conversation that had moved on four weeks earlier.

So the identity moves out of the card and onto the thread (migration 035):

    c:<client_id>   attributed to a registry client — the strongest key
    p:<name-slug>   otherwise the counterparty's name off the room
    t:<thread_id>   no usable name: a group of one, i.e. the old behaviour

and everything reads the same one — the owed-reply decision, the conversation
view (which already renders every thread it is given), and the draft-writer's
context. `nudges.followup_key` is now built FROM this key so there is a single
implementation of the slug; its output is byte-for-byte what it was, because
re-keying a card would strand the founder's Done under the old key.

Never merges on a guess: two people whose names slug the same would merge, so
the slug is the WHOLE first field of the room name ("Dhanya Sangolli" out of
"Dhanya Sangolli, Dhanya S"), not a first name, and an empty one falls back to
the thread id rather than pooling every unnamed room together.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Sequence

_SLUG = re.compile(r"[^a-z0-9]+")


def slug_for(room_name: Optional[str]) -> str:
    """"Dhanya Sangolli, Dhanya S" -> "dhanya-sangolli". The room name's first
    comma-field is the PERSON; what follows is their company or handle and
    varies between rooms for the same human."""
    base = (room_name or "").split(",")[0].strip()
    return _SLUG.sub("-", base.lower()).strip("-")


def key_for(client_id: Optional[str], room_name: Optional[str],
            thread_id: str) -> str:
    if client_id:
        return f"c:{client_id}"
    slug = slug_for(room_name)
    return f"p:{slug}" if slug else f"t:{thread_id}"


def key_forms(client_id: Optional[str], room_name: Optional[str],
              thread_id: str) -> List[str]:
    """Every key this thread could be filed under, most specific first — the
    inverse of `key_for`, and it must stay in lockstep with it."""
    forms = []
    if client_id:
        forms.append(f"c:{client_id}")
    slug = slug_for(room_name)
    if slug:
        forms.append(f"p:{slug}")
    forms.append(f"t:{thread_id}")
    return forms


# ── keeping it current ───────────────────────────────────────────────────────

def refresh(conn: sqlite3.Connection, thread_ids: Optional[Sequence[str]] = None) -> int:
    """Recompute the key for every thread (or the ones named) and write it
    where it changed. Cheap — a few hundred rows — and idempotent, which is why
    it can run on every message pass instead of being trusted as stable:
    attributing a thread to a client CHANGES its key."""
    sql = "SELECT id, client_id, room_name, counterparty_key FROM threads"
    args: tuple = ()
    if thread_ids:
        ids = [t for t in thread_ids if t]
        if not ids:
            return 0
        sql += f" WHERE id IN ({','.join('?' * len(ids))})"
        args = tuple(ids)
    changed = 0
    for r in list(conn.execute(sql, args)):
        want = key_for(r["client_id"], r["room_name"], r["id"])
        if r["counterparty_key"] != want:
            conn.execute("UPDATE threads SET counterparty_key=? WHERE id=?",
                         (want, r["id"]))
            changed += 1
    return changed


# ── reading it ───────────────────────────────────────────────────────────────

def key_of(conn: sqlite3.Connection, thread_id: str) -> Optional[str]:
    r = conn.execute("SELECT client_id, room_name, counterparty_key FROM threads"
                     " WHERE id=?", (thread_id,)).fetchone()
    if r is None:
        return None
    return r["counterparty_key"] or key_for(r["client_id"], r["room_name"], thread_id)


def threads_for(conn: sqlite3.Connection, key: str) -> List[str]:
    return [r["id"] for r in conn.execute(
        "SELECT id FROM threads WHERE counterparty_key=?"
        " ORDER BY latest_message_dt DESC", (key,))]


def expand(conn: sqlite3.Connection, thread_ids: Iterable[str]) -> List[str]:
    """Every room belonging to the same people as `thread_ids`, newest-active
    first. THE POINT OF THE MODULE: a decision about a person must see all of
    that person's rooms, not the subset that happened to reach the caller."""
    seen, out = set(), []
    for tid in thread_ids:
        key = key_of(conn, tid)
        if key is None:
            if tid not in seen:
                seen.add(tid)
                out.append(tid)
            continue
        for sib in threads_for(conn, key) or [tid]:
            if sib not in seen:
                seen.add(sib)
                out.append(sib)
    return out


def _utc(value) -> Optional[datetime]:
    """Upwork's ISO instant or SQLite's 'YYYY-MM-DD HH:MM:SS' -> aware UTC."""
    if not value:
        return None
    text = str(value).strip()
    try:
        if "T" not in text:
            return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        text = re.sub(r"(\.\d{3})\d+", r"\1", text)      # 3.9: 3 fraction digits
        got = datetime.fromisoformat(text)
        return got if got.tzinfo else got.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def last_ours(conn: sqlite3.Connection, key: str) -> Optional[str]:
    """When we last spoke to this person in ANY of their rooms. System stories
    are not our voice (mcp_messages demotes Upwork's own notifications to
    direction 'system' precisely so they cannot answer for him)."""
    r = conn.execute(
        "SELECT MAX(m.created_dt) ts FROM messages m JOIN threads t ON t.id=m.thread_id"
        " WHERE t.counterparty_key=? AND lower(m.direction)='outbound'",
        (key,)).fetchone()
    return r["ts"] if r and r["ts"] else None


# ── the stale draft ──────────────────────────────────────────────────────────

def supersede_stale(conn: sqlite3.Connection, dry_run: bool = False) -> List[dict]:
    """A pending draft on a room the conversation has LEFT.

    Superseded when we have spoken to that person, anywhere, since the draft
    was written. Not deleted — `sent_status` becomes 'superseded', the same
    word poll_inbox uses when it redrafts, so the row stays readable and the
    change reverses with one UPDATE.

    Nothing is superseded on a guess: an unparseable timestamp on either side
    leaves the draft alone."""
    out = []
    rows = list(conn.execute(
        "SELECT d.id, d.thread_id, d.generated_at, t.counterparty_key, t.room_name"
        " FROM drafts d JOIN threads t ON t.id = d.thread_id"
        " WHERE d.sent_status='pending'"))
    latest: dict = {}
    for r in rows:
        key = r["counterparty_key"]
        if not key:
            continue
        if key not in latest:
            latest[key] = last_ours(conn, key)
        ours, written = _utc(latest[key]), _utc(r["generated_at"])
        if ours is None or written is None or ours <= written:
            continue
        out.append({"draft_id": r["id"], "thread_id": r["thread_id"],
                    "who": r["room_name"], "written": r["generated_at"],
                    "spoke_at": latest[key]})
        if not dry_run:
            conn.execute("UPDATE drafts SET sent_status='superseded' WHERE id=?",
                         (r["id"],))
    return out
