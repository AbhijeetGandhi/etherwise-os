"""M1b poll_inbox — threads/messages mirror + classification + follow-up
drafts (SHADOW).

Port of v2's poll_inbox onto kernel primitives (threads/messages = v2
rooms/stories). The deterministic bucket logic carries VERBATIM (predictability
spectrum: code where code can do it): tiers from contract/proposal state,
awaiting-reply from the latest human message, thresholds HOT 48h / WARM 72h /
COLD 168h / UNKNOWN 72h, <12h-too-soon, max 2 consecutive outbound, snooze
respected, stale-30d+ left alone. The gateway adds judgment only where code
cannot: Haiku classifies thread INTENT for owed/unmatched threads (digest
context), Sonnet drafts the follow-up/reply text.

External writes (the contact@ digest) are INTENTS into shadow_ledger until
M1b cutover. Drafts are internal rows (drafts table) — drafts-only is the
hard line regardless of shadow.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Optional

from core import config, db, runner
from modules.upwork import queries
from modules.upwork.upwork_client import UpworkClient

TASK_NAME = "upwork_inbox"

HOT_PROPOSAL_STATUSES = {"Activated", "Offered", "Hired", "Accepted"}
TERMINAL_PROPOSAL_STATUSES = {"Declined", "Withdrawn", "Archived"}
ACTIVE_CONTRACT_STATUSES = {"ACTIVE", "PENDING"}
CLOSED_CONTRACT_STATUSES = {"CLOSED", "PAUSED", "CANCELLED"}
FOLLOWUP_THRESHOLD_HOURS = {"HOT": 48, "WARM": 72, "COLD": 168,
                            "UNKNOWN": 72}
ACTIONABLE_BUCKETS = ("owed", "unmatched-owed", "followup",
                      "unmatched-followup")

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string",
                   "enum": ["new_lead", "active_client", "support",
                            "spam", "other"]},
        "summary": {"type": "string"},
    },
    "required": ["intent", "summary"],
    "additionalProperties": False,
}


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hours_since(value: Optional[str]) -> Optional[float]:
    dt = _parse_dt(value)
    if not dt:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


# ── mirror upserts ────────────────────────────────────────────────────────────

def upsert_thread(conn, room: dict) -> None:
    latest = room.get("latestStory") or {}
    fields = (room.get("roomName"), room.get("topic"), room.get("roomType"),
              (room.get("contract") or {}).get("id"),
              (room.get("vendorProposal") or {}).get("id"),
              room.get("numUnread"), latest.get("id"),
              latest.get("createdDateTime"))
    if conn.execute("SELECT 1 FROM threads WHERE id=?",
                    (room["id"],)).fetchone():
        conn.execute(
            "UPDATE threads SET room_name=?, topic=?, room_type=?,"
            " contract_id=?, proposal_id=?, num_unread=?,"
            " latest_message_id=?, latest_message_dt=?,"
            " updated_at=datetime('now') WHERE id=?",
            (*fields, room["id"]))
    else:
        conn.execute(
            "INSERT INTO threads (room_name, topic, room_type, contract_id,"
            " proposal_id, num_unread, latest_message_id, latest_message_dt,"
            " id) VALUES (?,?,?,?,?,?,?,?,?)", (*fields, room["id"]))


def upsert_message(conn, thread_id: str, story: dict) -> bool:
    """True if newly inserted."""
    if conn.execute("SELECT 1 FROM messages WHERE id=?",
                    (story["id"],)).fetchone():
        return False
    sender = story.get("user") or {}
    sender_id = sender.get("id")
    direction = ("outbound" if sender_id == config.UPWORK_USER_ID
                 else "inbound" if sender_id else "system")
    conn.execute(
        "INSERT INTO messages (id, thread_id, message, created_dt,"
        " sender_user_id, sender_user_name, direction) VALUES (?,?,?,?,?,?,?)",
        (story["id"], thread_id, story.get("message"),
         story.get("createdDateTime"), sender_id, sender.get("name"),
         direction))
    return True


# ── classification (VERBATIM port of v2 logic) ───────────────────────────────

def compute_tier(room: dict) -> str:
    contract = room.get("contract") or {}
    vp = room.get("vendorProposal") or {}
    status_obj = vp.get("status") or {}
    proposal_status = status_obj.get("status") \
        if isinstance(status_obj, dict) else status_obj
    if contract.get("status") in ACTIVE_CONTRACT_STATUSES:
        return "HOT"
    if proposal_status in HOT_PROPOSAL_STATUSES:
        return "HOT"
    if proposal_status == "Pending":
        return "WARM"
    if contract.get("status") in CLOSED_CONTRACT_STATUSES:
        return "COLD"
    if proposal_status in TERMINAL_PROPOSAL_STATUSES:
        return "COLD"
    return "UNKNOWN"


def compute_awaiting(conn, room: dict, stale_days: int = 30) -> Optional[str]:
    row = conn.execute(
        "SELECT created_dt, sender_user_id FROM messages WHERE thread_id=?"
        " AND sender_user_id IS NOT NULL AND TRIM(COALESCE(message,'')) != ''"
        " ORDER BY created_dt DESC LIMIT 1", (room["id"],)).fetchone()
    if row is None:
        latest = (room.get("latestStory") or {}).get("createdDateTime")
        dt = _parse_dt(latest)
        if not dt:
            return None
        if (datetime.now(timezone.utc) - dt).days >= stale_days:
            return "None"
        return None
    dt = _parse_dt(row["created_dt"])
    if not dt:
        return None
    if (datetime.now(timezone.utc) - dt).days >= stale_days:
        return "None"
    return "Client" if row["sender_user_id"] == config.UPWORK_USER_ID \
        else "Abhijeet"


def _snoozed(conn, thread_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM threads WHERE id=? AND snooze_until IS NOT NULL"
        " AND snooze_until > date('now')", (thread_id,)).fetchone() is not None


def _human_ever(conn, thread_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM messages WHERE thread_id=? AND sender_user_id IS NOT"
        " NULL LIMIT 1", (thread_id,)).fetchone() is not None


def _consecutive_outbound(conn, thread_id: str) -> int:
    n = 0
    for row in conn.execute(
            "SELECT direction FROM messages WHERE thread_id=? AND"
            " direction != 'system' AND TRIM(COALESCE(message,'')) != ''"
            " ORDER BY created_dt DESC", (thread_id,)):
        if row["direction"] == "outbound":
            n += 1
        else:
            break
    return n


def classify_thread(conn, room: dict, tier: str,
                    awaiting: Optional[str]):
    thread_id = room["id"]
    latest = room.get("latestStory") or {}
    age_hours = _hours_since(latest.get("createdDateTime"))
    matched = bool((room.get("contract") or {}).get("id")
                   or (room.get("vendorProposal") or {}).get("id"))
    if _snoozed(conn, thread_id):
        return "snoozed", "snooze_until > today"
    if age_hours is None:
        return "hard-skip", "no latest story"
    if not _human_ever(conn, thread_id):
        return "hard-skip", "bot-only thread"
    if awaiting == "None":
        return "hard-skip", "thread stale 30d+"
    contract = room.get("contract") or {}
    if contract.get("status") in CLOSED_CONTRACT_STATUSES \
            and age_hours > 14 * 24:
        return "hard-skip", "ended contract, 14d+ silence"
    if awaiting == "Abhijeet":
        return ("owed", "matched: client owed reply") if matched else \
            ("unmatched-owed", "unmatched: prospect inquiry owed reply")
    if awaiting == "Client":
        if _consecutive_outbound(conn, thread_id) >= 2:
            return "healthy", ">=2 prior followups already"
        if age_hours < 12:
            return "healthy", "<12h since reply"
        threshold = FOLLOWUP_THRESHOLD_HOURS.get(tier, 72)
        if age_hours > threshold:
            return (("followup", f"{tier} {age_hours:.0f}h over {threshold}h")
                    if matched else
                    ("unmatched-followup", f"unmatched {tier}"
                                           f" {age_hours:.0f}h"))
        return "healthy", f"{tier} below {threshold}h threshold"
    return "healthy", "default"


# ── judgment (gateway) ────────────────────────────────────────────────────────

def thread_intent(ctx, conn, thread_id: str) -> dict:
    msgs = [dict(r) for r in conn.execute(
        "SELECT direction, message FROM messages WHERE thread_id=?"
        " AND TRIM(COALESCE(message,'')) != '' ORDER BY created_dt DESC"
        " LIMIT 6", (thread_id,))]
    convo = "\n".join(f"[{m['direction']}] {m['message'][:300]}"
                      for m in reversed(msgs))
    result = ctx.claude(
        model_key="classify", schema=INTENT_SCHEMA,
        system="Classify this Upwork thread for an automation agency's"
               " inbox triage. intent: new_lead | active_client | support |"
               " spam | other. summary: one factual line.",
        user_content=convo or "(no message text)",
        purpose=f"intent {thread_id}", max_output_tokens=150)
    return result.parsed


def draft_followup(ctx, conn, room: dict, bucket: str, tier: str) -> str:
    msgs = [dict(r) for r in conn.execute(
        "SELECT direction, message, created_dt FROM messages WHERE"
        " thread_id=? AND TRIM(COALESCE(message,'')) != ''"
        " ORDER BY created_dt DESC LIMIT 10", (room["id"],))]
    convo = "\n".join(f"[{m['direction']} {m['created_dt'][:10]}]"
                      f" {m['message'][:400]}" for m in reversed(msgs))
    kind = "reply to the client's last message" \
        if bucket in ("owed", "unmatched-owed") else "gentle follow-up"
    result = ctx.claude(
        model_key="drafting",
        system=("You draft Upwork messages for Abhijeet Gandhi (Etherwise,"
                " automation agency). Direct, warm, concise (<120 words),"
                " no fluff, ends with a concrete next step. DRAFT ONLY —"
                " never claims to have sent anything."),
        user_content=f"Thread ({tier}, {bucket}):\n{convo}\n\nWrite a {kind}.",
        purpose=f"draft {bucket} {room['id']}", max_output_tokens=400)
    return result.text.strip()


# ── task ──────────────────────────────────────────────────────────────────────

def poll(ctx, client_factory=UpworkClient) -> dict:
    dbp = ctx.db_path or config.DB_PATH
    client = client_factory()
    metrics = {"threads": 0, "new_messages": 0, "buckets": {}, "tiers": {},
               "drafts": 0, "intents": 0, "stale_drafts_cleared": 0}

    data = client.graphql(queries.ROOM_LIST, {"first": 200, "after": "0"})
    rooms = [(e.get("node") or e)
             for e in ((data.get("roomList") or {}).get("edges")) or []]
    metrics["threads"] = len(rooms)

    with db.connect(dbp) as conn:
        for room in rooms:
            upsert_thread(conn, room)

    # deep-fetch stories for threads with activity
    for room in rooms:
        latest_dt = (room.get("latestStory") or {}).get("createdDateTime")
        with db.connect(dbp) as conn:
            have = conn.execute(
                "SELECT MAX(created_dt) AS m FROM messages WHERE thread_id=?",
                (room["id"],)).fetchone()["m"]
        if latest_dt and have and latest_dt <= have:
            continue
        # v2 quirk: WITH_SENDERS hits non-null bubbles on system stories —
        # tolerate partial data; if the room nulled entirely, retry plain.
        env = client.graphql(queries.ROOM_STORIES_WITH_SENDERS,
                             {"roomId": room["id"], "first": 50},
                             tolerate_errors=True)
        edges = ((((env.get("data") or {}).get("room") or {})
                  .get("stories") or {}).get("edges")) or []
        if not edges and env.get("errors"):
            env = client.graphql(queries.ROOM_STORIES,
                                 {"roomId": room["id"], "first": 50},
                                 tolerate_errors=True)
            edges = ((((env.get("data") or {}).get("room") or {})
                      .get("stories") or {}).get("edges")) or []
        stories = [(e.get("node") or e) for e in edges if e]
        with db.connect(dbp) as conn:
            for s in stories:
                if s.get("id") and upsert_message(conn, room["id"], s):
                    metrics["new_messages"] += 1

    # classify (verbatim) + judgment
    digest_items = []
    for room in rooms:
        with db.connect(dbp) as conn:
            tier = compute_tier(room)
            awaiting = compute_awaiting(conn, room)
            if awaiting is None:
                continue
            bucket, _reason = classify_thread(conn, room, tier, awaiting)
            conn.execute(
                "UPDATE threads SET tier=?, awaiting_reply_from=?, bucket=?,"
                " updated_at=datetime('now') WHERE id=?",
                (tier, awaiting, bucket, room["id"]))
        metrics["buckets"][bucket] = metrics["buckets"].get(bucket, 0) + 1
        metrics["tiers"][tier] = metrics["tiers"].get(tier, 0) + 1

        if bucket not in ACTIONABLE_BUCKETS:
            continue
        with db.connect(dbp) as conn:
            pending = conn.execute(
                "SELECT 1 FROM drafts WHERE thread_id=? AND"
                " sent_status='pending' AND draft_kind=?",
                (room["id"], bucket)).fetchone()
        if pending:
            continue          # same bucket, draft already waiting — no churn
        with db.connect(dbp) as conn_r:
            intent = None
            if bucket in ("owed", "unmatched-owed"):
                intent = thread_intent(ctx, conn_r, room["id"])
                metrics["intents"] += 1
        draft = draft_followup(ctx, conn_r, room, bucket, tier)
        with db.connect(dbp) as conn:
            conn.execute("UPDATE drafts SET sent_status='superseded' WHERE"
                         " thread_id=? AND sent_status='pending'",
                         (room["id"],))
            conn.execute(
                "INSERT INTO drafts (thread_id, draft_kind, tier, body,"
                " word_count, rationale) VALUES (?,?,?,?,?,?)",
                (room["id"], bucket, tier, draft, len(draft.split()),
                 json.dumps(intent) if intent else None))
        metrics["drafts"] += 1
        digest_items.append({"thread_id": room["id"],
                             "topic": room.get("topic") or
                             room.get("roomName"),
                             "tier": tier, "bucket": bucket,
                             "intent": intent, "draft": draft})

    # stale-draft clearing (verbatim)
    with db.connect(dbp) as conn:
        marks = ",".join("?" * len(ACTIONABLE_BUCKETS))
        cur = conn.execute(
            f"UPDATE drafts SET sent_status='stale' WHERE"
            f" sent_status='pending' AND thread_id NOT IN"
            f" (SELECT id FROM threads WHERE bucket IN ({marks}))",
            ACTIONABLE_BUCKETS)
        metrics["stale_drafts_cleared"] = cur.rowcount

    # digest intent (external write -> shadow until cutover)
    if digest_items:
        payload = {"to": config.NOTIFY_EMAIL,
                   "subject": f"Upwork inbox: {len(digest_items)} threads"
                              " need you",
                   "items": digest_items}
        if ctx.shadow:
            ctx.record_shadow_write(
                target="email", operation="send", entity="inbox_digest",
                entity_key=datetime.now(config.TZ).strftime("%Y-%m-%dT%H"),
                payload=payload)
        else:
            ctx.require_live("inbox digest email")  # raises until cutover

    if not rooms:
        raise runner.TaskSkip("no rooms returned")
    return metrics


def main() -> int:
    result = runner.run_task(TASK_NAME, poll, module="upwork")
    print(json.dumps({"run_id": result.run_id, "status": result.status,
                      "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
