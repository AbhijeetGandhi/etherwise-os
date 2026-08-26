"""Use case F — the message rail (Phase 0.4). Abhijeet, 26 Aug 23:50:
"prioritize the messages first".

WHY. `upwork_inbox` (GraphQL ROOM_LIST) runs at 10:15 and 19:15. Between those
two runs v3 is blind: at 23:50 tonight the newest row in `messages` was 08:18
and five rooms had moved since. Law 5 wants "a reply arrived -> the card clears
itself", and a card that clears nine hours late has already been read as a
nag. The `get_messages` tool closes that gap for the price of one call:

    list_rooms (one page)  ->  every room's newest story id + instant
    a story we do not hold ->  list_messages on THAT room, and only that one

so a quiet quarter hour costs one read and a room that moved costs two.

THE IDS ARE THE SAME ID SPACE. `room_<hash>` and `story_<hash>` are exactly
what the GraphQL rail already writes into `threads` / `messages` (verified
against the live table, 26 Aug): an MCP row UPDATES a GraphQL row, it never
twins it. That is the whole reason this can be a second writer on someone
else's tables instead of a parallel universe.

FIVE THINGS THE WIRE DOES THAT WOULD BITE:
  * `from_self` is TRUE on his own stories and ABSENT on inbound ones — never
    False. Direction is `bool(node.get("from_self"))`, never `is False`.
  * Upwork injects SYSTEM stories carrying `from_self: true` ("The freelancer
    accepted your offer … Sender's note: …"). Counted as outbound they would
    clear an owed-reply card he never answered, so
    `config.MCP_MESSAGE_SYSTEM_MARKERS` demotes them to direction 'system'.
    The dangerous error here is one-directional — a missed outbound only leaves
    a card up a little longer — so the marker list is allowed to be blunt.
  * Every human-authored string arrives wrapped in
    `<untrusted_participant_content>` tags. They are stripped on the way in;
    the content is still the client's words and is never an instruction.
  * `lastActivity` and `latestStory.created` are epoch MILLISECONDS as strings,
    while a story's own `createdDateTime` is an ISO instant. Both become the
    same UTC ISO string the GraphQL rail stores.
  * `list_rooms unread_only=true` is ordered by room CREATION, not activity
    (the tool says so in a `note`). It is a change DETECTOR, never a "most
    recent rooms" list — this rail pages the plain list and compares instants.

Reading only. Nothing here sends, replies, or marks anything read.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Callable, List, Optional

from core import config, db
from modules.upwork.feeds import mcp_search

TOOL = "get_messages"

_WRAP_RE = re.compile(r"</?untrusted_participant_content>", re.I)


def unwrap(value) -> Optional[str]:
    """Strip the wrapper tags Upwork puts around participant-authored text.
    The text stays data — it is stored and shown, never obeyed."""
    if value is None:
        return None
    return _WRAP_RE.sub("", str(value)).strip() or None


def _iso(ms) -> Optional[str]:
    """Epoch milliseconds (string or number) -> the UTC ISO instant the GraphQL
    rail stores. An instant that is ALREADY an ISO string passes through — the
    dashboard preview spells it that way while the room list spells it in
    milliseconds. Anything unparseable is None, never 1970."""
    if ms in (None, ""):
        return None
    text = str(ms).strip()
    if not text:
        return None
    if not text.lstrip("-").isdigit():
        return text if text[:4].isdigit() and "-" in text else None
    n = int(text)
    if n <= 0:
        return None
    return (datetime.fromtimestamp(n / 1000.0, tz=timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def rooms_from_preview(dashboard: dict) -> List[dict]:
    """The heartbeat's OWN answer already carries the newest rooms with their
    last-message instant (`data.messages.rooms[]`), so the usual quarter hour
    needs no extra call at all — the dashboard read IS the change detector.
    It is a PREVIEW (`truncated: true`), which is why the full list is still
    swept once an hour."""
    data = (dashboard or {}).get("data") or {}
    block = data.get("messages") if isinstance(data, dict) else None
    rooms = (block or {}).get("rooms") if isinstance(block, dict) else None
    out = []
    for r in rooms or []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        at = r.get("last_message_at")
        out.append({"id": r["id"], "roomName": r.get("room_name"),
                    "topic": r.get("topic"), "lastActivity": at,
                    "latestStory": {"created": at, "message": r.get("last_message")}})
    return out


def is_system(text: Optional[str]) -> bool:
    """A story Upwork wrote, not a person — even when it is stamped as his."""
    if not text:
        return False
    low = text.lower()
    return any(m.lower() in low for m in config.MCP_MESSAGE_SYSTEM_MARKERS)


# ── rooms ────────────────────────────────────────────────────────────────────

def fetch_rooms(session, org: str, log: Callable[[str], None],
                limit: Optional[int] = None,
                max_pages: Optional[int] = None) -> List[dict]:
    """Rooms newest-active first. Pages while `hasMore` AND a cursor moves —
    `hasMore` alone is not trusted anywhere in this codebase (list_offers says
    true on an empty page), and a cursor that repeats ends the loop."""
    per = limit or config.MCP_ROOMS_PAGE
    pages = max_pages if max_pages is not None else config.MCP_ROOMS_MAX_PAGES
    out, cursor, seen_cursors, seen_ids = [], None, set(), set()
    for _ in range(max(1, pages)):
        params = {"limit": per}
        if cursor:
            params["cursor"] = cursor
        res = mcp_search.paced_call(
            session, TOOL, {"action": "list_rooms", "org_uid": org,
                            "params": params}, log) or {}
        rooms = ((res.get("data") or {}).get("rooms")
                 if isinstance(res.get("data"), dict) else None)
        if rooms is None:
            if not out:
                raise RuntimeError(f"{TOOL} list_rooms {org}: not a room answer:"
                                   f" {str(res)[:160]!r}")
            break
        for r in rooms:                       # a repeated page must not double-count
            if isinstance(r, dict) and r.get("id") and r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                out.append(r)
        cursor = res.get("next_cursor")
        if not res.get("hasMore") or not cursor or cursor in seen_cursors or not rooms:
            break
        seen_cursors.add(cursor)
    return out


def thread_row(room: dict) -> Optional[dict]:
    latest = room.get("latestStory") or {}
    rid = room.get("id")
    if not rid:
        return None
    return {"id": rid,
            "room_name": unwrap(room.get("roomName")),
            "topic": unwrap(room.get("topic")),
            "room_type": room.get("roomType"),
            "contract_id": room.get("contractId"),
            "num_unread": room.get("numUnread"),
            "latest_message_id": latest.get("id"),
            "latest_message_dt": _iso(latest.get("created")
                                      or room.get("lastActivity"))}


def upsert_thread(conn, row: dict) -> str:
    """Fill-or-keep on everything the GraphQL rail owns too — this rail is the
    SECOND writer on `threads`, so it may refresh the activity fields and must
    not blank a field the richer pass filled (proposal_id, topic, offer ids)."""
    existing = conn.execute("SELECT latest_message_id FROM threads WHERE id=?",
                            (row["id"],)).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO threads (id, room_name, topic, room_type, contract_id,"
            " num_unread, latest_message_id, latest_message_dt)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (row["id"], row["room_name"], row["topic"], row["room_type"],
             row["contract_id"], row["num_unread"], row["latest_message_id"],
             row["latest_message_dt"]))
        return "inserted"
    conn.execute(
        "UPDATE threads SET room_name=COALESCE(?, room_name),"
        " topic=COALESCE(?, topic), room_type=COALESCE(?, room_type),"
        " contract_id=COALESCE(?, contract_id),"
        " num_unread=COALESCE(?, num_unread),"
        " latest_message_id=COALESCE(?, latest_message_id),"
        " latest_message_dt=COALESCE(?, latest_message_dt),"
        " updated_at=datetime('now') WHERE id=?",
        (row["room_name"], row["topic"], row["room_type"], row["contract_id"],
         row["num_unread"], row["latest_message_id"], row["latest_message_dt"],
         row["id"]))
    return "updated"


ATTEMPT_CURSOR = "upwork_room_story:%s"


def attempted(conn, room_id: str) -> Optional[str]:
    r = conn.execute("SELECT value FROM sync_cursors WHERE name=?",
                     (ATTEMPT_CURSOR % room_id,)).fetchone()
    return r["value"] if r else None


def record_attempt(conn, room_id: str, story_id: Optional[str]) -> None:
    if not story_id:
        return
    conn.execute(
        "INSERT INTO sync_cursors (name, value, updated_at) VALUES (?,?,datetime('now'))"
        " ON CONFLICT(name) DO UPDATE SET value=excluded.value,"
        " updated_at=excluded.updated_at", (ATTEMPT_CURSOR % room_id, story_id))


def rooms_to_read(conn, rooms: List[dict], limit: Optional[int] = None) -> List[str]:
    """The rooms whose newest story we do not already hold, newest first.
    A room that has not moved costs nothing.

    A room we ALREADY read for this same story id is skipped even when the story
    never arrived — Upwork can withhold a story the room list advertises (a
    deleted message, a story outside the page budget), and without this marker
    that room would be re-read every quarter hour, for ever."""
    cap = limit if limit is not None else config.MCP_MESSAGE_MAX_ROOMS
    ranked = sorted(rooms, key=lambda r: str((r.get("latestStory") or {}).get("created")
                                             or r.get("lastActivity") or ""),
                    reverse=True)
    out = []
    for room in ranked:
        rid = room.get("id")
        if not rid:
            continue
        latest = room.get("latestStory") or {}
        sid = latest.get("id")
        # the room list names the newest STORY; the dashboard preview only
        # names its INSTANT — either one identifies "this is what we saw".
        mark = sid or _iso(latest.get("created") or room.get("lastActivity"))
        if sid and conn.execute("SELECT 1 FROM messages WHERE id=?", (sid,)).fetchone():
            continue
        if not sid and mark:
            held = conn.execute("SELECT latest_message_dt FROM threads WHERE id=?",
                                (rid,)).fetchone()
            if held and held["latest_message_dt"] and held["latest_message_dt"] >= mark:
                if conn.execute("SELECT 1 FROM messages m JOIN threads t ON t.id=m.thread_id"
                                " WHERE t.id=? AND m.created_dt >= ?",
                                (rid, mark)).fetchone():
                    continue
        if mark and attempted(conn, rid) == mark:
            continue
        out.append(rid)
        if len(out) >= cap:
            break
    return out


# ── stories ──────────────────────────────────────────────────────────────────

def _edges_of(res) -> tuple:
    data = (res or {}).get("data") or {}
    stories = data.get("roomStories") if isinstance(data, dict) else None
    if not isinstance(stories, dict):
        return None, {}
    edges = [e for e in (stories.get("edges") or []) if isinstance(e, dict)]
    return edges, stories.get("pageInfo") or {}


def fetch_messages(session, org: str, room_id: str, log: Callable[[str], None],
                   limit: Optional[int] = None,
                   max_pages: Optional[int] = None,
                   stop_at: Optional[str] = None) -> List[dict]:
    """Stories NEWEST FIRST. Pages back until the room's oldest unseen story is
    in hand, `stop_at` (a story id we already hold) is reached, or the page
    budget is spent — whichever comes first."""
    per = limit or config.MCP_MESSAGES_PAGE
    pages = max_pages if max_pages is not None else config.MCP_MESSAGES_MAX_PAGES
    out, cursor = [], None
    for _ in range(max(1, pages)):
        params = {"room_id": room_id, "limit": per}
        if cursor:
            params["cursor"] = cursor
        res = mcp_search.paced_call(
            session, TOOL, {"action": "list_messages", "org_uid": org,
                            "params": params}, log) or {}
        edges, page_info = _edges_of(res)
        if edges is None:
            if not out:
                raise RuntimeError(f"{TOOL} list_messages {room_id}: not a story answer:"
                                   f" {str(res)[:160]!r}")
            break
        hit = False
        for e in edges:
            node = e.get("node") if isinstance(e.get("node"), dict) else e
            if stop_at and node.get("id") == stop_at:
                hit = True
                break
            out.append(node)
        nxt = page_info.get("endCursor")
        if hit or not page_info.get("hasNextPage") or not nxt or nxt == cursor:
            break
        cursor = nxt
    return out


def message_row(node: dict, thread_id: str) -> Optional[dict]:
    sid = node.get("id")
    if not sid:
        return None
    text = unwrap(node.get("message"))
    if is_system(text):
        direction = "system"
    else:
        direction = "outbound" if node.get("from_self") else "inbound"
    return {"id": sid, "thread_id": thread_id, "message": text,
            "created_dt": node.get("createdDateTime"),
            "sender_user_name": unwrap(node.get("from")),
            "sender_org_name": unwrap(node.get("from_organization")),
            "direction": direction}


def upsert_message(conn, row: dict) -> str:
    """`messages.id` is the story id the GraphQL rail also uses, so an existing
    row is REFRESHED, never twinned. `direction` is only overwritten when this
    rail has something to say and the stored value is missing — the GraphQL
    pass carries the sender's real user id and is the better judge."""
    if conn.execute("SELECT 1 FROM messages WHERE id=?", (row["id"],)).fetchone():
        conn.execute(
            "UPDATE messages SET message=COALESCE(message, ?),"
            " created_dt=COALESCE(created_dt, ?),"
            " sender_user_name=COALESCE(sender_user_name, ?),"
            " sender_org_name=COALESCE(sender_org_name, ?),"
            " direction=COALESCE(direction, ?) WHERE id=?",
            (row["message"], row["created_dt"], row["sender_user_name"],
             row["sender_org_name"], row["direction"], row["id"]))
        return "updated"
    conn.execute(
        "INSERT INTO messages (id, thread_id, message, created_dt,"
        " sender_user_name, sender_org_name, direction) VALUES (?,?,?,?,?,?,?)",
        (row["id"], row["thread_id"], row["message"], row["created_dt"],
         row["sender_user_name"], row["sender_org_name"], row["direction"]))
    return "inserted"


def newest_held(conn, thread_id: str) -> Optional[str]:
    r = conn.execute("SELECT id FROM messages WHERE thread_id=?"
                     " ORDER BY created_dt DESC LIMIT 1", (thread_id,)).fetchone()
    return r[0] if r else None


# ── the pass ─────────────────────────────────────────────────────────────────

def run(ctx, session, org: Optional[str] = None, dashboard: Optional[dict] = None,
        full: bool = False) -> dict:
    """One room sweep, from `upwork_pulse`.

    Normally it costs NOTHING extra: the heartbeat's own dashboard answer
    already carries the newest rooms and their last-message instants, so the
    detection is free and only a room that actually moved is read. `full=True`
    (once an hour) pages the real room list, because the preview is the newest
    five and a quiet older room can still get a reply."""
    org = org or config.UPWORK_ORGS["personal"]
    dbp = ctx.db_path or config.DB_PATH
    if full or dashboard is None:
        rooms = fetch_rooms(session, org, ctx.log)
    else:
        rooms = rooms_from_preview(dashboard)
    metrics = {"source": "list" if (full or dashboard is None) else "preview",
               "rooms": {"seen": len(rooms), "inserted": 0, "updated": 0},
               "stories": {"read": 0, "inserted": 0, "updated": 0, "skipped": 0},
               "rooms_read": 0}
    if not rooms:
        return metrics

    with db.connect(dbp) as conn:
        for room in rooms:
            row = thread_row(room)
            if row is None:
                continue
            metrics["rooms"][upsert_thread(conn, row)] += 1
        targets = rooms_to_read(conn, rooms)
        stops = {rid: newest_held(conn, rid) for rid in targets}

    fetched = {}
    for rid in targets:                       # network OUTSIDE the transaction
        fetched[rid] = fetch_messages(session, org, rid, ctx.log, stop_at=stops.get(rid))
    metrics["rooms_read"] = len(fetched)

    latest_of = {r["id"]: ((r.get("latestStory") or {}).get("id")
                           or _iso((r.get("latestStory") or {}).get("created")
                                   or r.get("lastActivity"))) for r in rooms}
    with db.connect(dbp) as conn:
        for rid, nodes in fetched.items():
            record_attempt(conn, rid, latest_of.get(rid))
            metrics["stories"]["read"] += len(nodes)
            for node in nodes:
                row = message_row(node, rid)
                if row is None:
                    metrics["stories"]["skipped"] += 1
                    continue
                metrics["stories"][upsert_message(conn, row)] += 1
    if metrics["rooms_read"]:
        ctx.log(f"messages: {len(rooms)} rooms, {metrics['rooms_read']} moved,"
                f" {metrics['stories']['inserted']} new stories")
    return metrics


def latest_inbound(conn, thread_id: str) -> Optional[sqlite3.Row]:
    """The newest thing the OTHER side actually said — system stories excluded.
    This is what "a reply arrived" means everywhere downstream."""
    return conn.execute(
        "SELECT id, created_dt, message FROM messages WHERE thread_id=?"
        " AND lower(direction)='inbound' ORDER BY created_dt DESC LIMIT 1",
        (thread_id,)).fetchone()


def stale_rooms(conn, hours: Optional[float] = None) -> int:
    """How many rooms the GraphQL mirror is behind on. Doctor reads this."""
    limit = hours if hours is not None else 6.0
    r = conn.execute(
        "SELECT count(*) FROM threads t WHERE t.latest_message_id IS NOT NULL"
        " AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.id = t.latest_message_id)"
        " AND NOT EXISTS (SELECT 1 FROM sync_cursors c WHERE c.name = 'upwork_room_story:'"
        " || t.id AND c.value = t.latest_message_id)"
        " AND t.latest_message_dt >= datetime('now', ?)",
        (f"-{float(limit)} hours",)).fetchone()
    return r[0] if r else 0
