"""The conversation reader — why a conversation did or did not become a hire.

Abhijeet, 2026-10-09: "... why only five people hired us and what are the
reasons that 25 people did not move ahead ... was it because of our messaging
... the calls ...". His answers the same night: an AI reading of the
conversations, up to $25 for the first pass; Globaltize is never read (their
contract, section 6.4).

Once per conversation, and again only when new messages arrive, it reads the
stored messages of a proposal's conversation (or a client's room) and codes
them into `conversation_codes`: how far it got, what happened, why it was lost
in the client's words, who went quiet, objections, whether price came up, the
next step, the tone, and — for a past client — an angle for a check-in. One
model call per conversation ('extract', Sonnet), a JSON schema, nothing else.

Order: proposal conversations that did not become hires first (the question he
asked), then the hires, then client rooms with no proposal. A run stops at the
gateway's per-run cap; the whole first pass stops at BUDGET_USD until he raises
it. Before anything leaves the machine, credentials, keys, emails and phone
numbers are cut out of the text (one room holds a secret a client shared).

Never read: a conversation of an EXCLUDED client — by the conversation's
client, the room's registry match, the proposal's client, or the room name.
Never written: anything but conversation_codes. No send path exists here.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from typing import Optional

from core import config, db, registry, runner

TASK_NAME = "upwork_conversation_codes"
BUDGET_USD = 25.0                 # his cap for the first pass (2026-10-09, "Up to $25")
EXCLUDED = ("globatize",)         # registry ids never read (Globaltize, contract 6.4)
MODEL_KEY = "extract"
MAX_MESSAGES = 80                 # the newest 80 messages of a conversation
MAX_CHARS = 24000
MAX_PER_RUN = 25                  # conversations per run; the per-run $ cap usually binds first

STAGES = ("no_reply", "replied", "talking", "call", "offer", "hired", "working", "ended")
OUTCOMES = ("hired", "lost", "went_quiet", "still_open", "not_a_fit", "client_ongoing",
            "client_ended")
LOST = ("price", "chose_someone_else", "timing", "scope_or_fit", "we_did_not_answer",
        "they_went_quiet", "project_cancelled", "trust", "skills", "communication",
        "other", "not_lost")
QUIET = ("them", "us", "neither")
PRICE = ("none", "they_said_too_high", "negotiated", "accepted", "we_lowered")
TONE = ("warm", "neutral", "cold", "frustrated")

SCHEMA = {
    "type": "object",
    "properties": {
        "stage_reached": {"type": "string", "enum": list(STAGES)},
        "outcome": {"type": "string", "enum": list(OUTCOMES)},
        "lost_reason": {"type": "string", "enum": list(LOST)},
        "lost_detail": {"type": "string"},
        "went_quiet": {"type": "string", "enum": list(QUIET)},
        "objections": {"type": "array", "items": {"type": "string"}},
        "price_talk": {"type": "string", "enum": list(PRICE)},
        "next_step": {"type": "string"},
        "tone": {"type": "string", "enum": list(TONE)},
        "reactivation_angle": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["stage_reached", "outcome", "lost_reason", "lost_detail", "went_quiet",
                 "objections", "price_talk", "next_step", "tone", "reactivation_angle",
                 "summary"],
    "additionalProperties": False,
}

SYSTEM = (
    "You read one Upwork conversation between an automation agency (\"Us\": "
    "Abhijeet and his team at Etherwise) and a prospective or past client, and "
    "code what happened. The agency wants to learn why conversations do or do "
    "not become hires, and which past clients could be welcomed back.\n"
    "Rules:\n"
    "- Code only what the messages show. If something is not in the messages, "
    "say so plainly (\"not in the messages\"); never guess a reason.\n"
    "- stage_reached: the furthest point reached (no_reply, replied, talking, "
    "call = a call was held or firmly booked, offer = an offer was made, hired, "
    "working = work under way, ended = the work finished).\n"
    "- outcome: hired, lost, went_quiet, still_open, not_a_fit, client_ongoing "
    "or client_ended. lost_reason is not_lost unless the outcome is lost, "
    "went_quiet or not_a_fit.\n"
    "- lost_detail: one sentence, in the client's own words where they gave "
    "them. went_quiet: who sent the last unanswered message's counterpart — "
    "'them' if the client stopped answering us, 'us' if the client's message "
    "was left unanswered, 'neither' otherwise.\n"
    "- objections: short phrases (price, timeline, experience with a tool, ...).\n"
    "- next_step: the single most useful thing the agency could do now, or "
    "\"none\". reactivation_angle: for a past client only, a specific, honest "
    "reason to check in (something unfinished, a next phase they mentioned); "
    "otherwise \"none\".\n"
    "- summary: two or three plain sentences.\n"
    "- Never copy credentials, passwords, keys, tokens, links, email addresses, "
    "phone numbers or any secret into your answer, even if the text contains "
    "them. Write about the conversation, not its private data.")

# what is cut out before the text leaves the machine
_REDACT = (
    (re.compile(r"(?i)\b(password|passwd|pwd|pass ?code|api[ _-]?key|secret|token|"
                r"access[ _-]?key|client[ _-]?secret)\b\s*[:=]?\s*\S+"), r"\1 [cut]"),
    (re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|github_pat|xox[abpr]|AKIA|AIza|ya29)"
                r"[A-Za-z0-9_\-\.]{8,}"), "[key cut]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+"), "[token cut]"),
    (re.compile(r"https?://\S*(?:token|key|secret|sig|auth|password)=\S+", re.I), "[link cut]"),
    (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"), "[long code cut]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
)
_PHONE = re.compile(r"(?<!\w)\+?\d[\d ().-]{8,}\d(?!\w)")
_DATEISH = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _phone(m) -> str:
    """A run of digits is a phone number only with 10+ digits and not a date."""
    s = m.group(0)
    if _DATEISH.match(s) or sum(ch.isdigit() for ch in s) < 10:
        return s
    return "[phone]"


def redact(text: Optional[str]) -> str:
    out = text or ""
    for rx, rep in _REDACT:
        out = rx.sub(rep, out)
    return _PHONE.sub(_phone, out)


def _norm(s: Optional[str]) -> str:
    return registry.norm(s or "") if s else ""


def is_excluded(reg, room_id: Optional[str], room_name: Optional[str],
                client_id: Optional[str], company: Optional[str] = None) -> bool:
    """True when the conversation may belong to an EXCLUDED client. Deliberately
    loose — a substring of any of their names in the room name or the
    proposal's client counts — because reading one excluded conversation is
    the failure that matters, and skipping a stranger's costs nothing."""
    if client_id in EXCLUDED:
        return True
    if reg is not None:
        try:
            if room_id and reg.match_upwork_room(room_id) in EXCLUDED:
                return True
        except Exception:                                  # noqa: BLE001
            pass
    names = []
    for cid in EXCLUDED:
        names.append(cid)
        if reg is not None:
            c = getattr(reg, "by_id", {}).get(cid) or {}
            names += [c.get("name") or "", (c.get("billing") or {}).get("canonical") or ""]
            names += list(c.get("aliases") or [])
    hay = " | ".join(_norm(x) for x in (room_name, company) if x)
    return any(_norm(n) and _norm(n) in hay for n in names)


def queue(conn, reg=None) -> list:
    """Every conversation to (re)read, in reading order: proposal conversations
    that did not become hires, then hires, then client rooms with no proposal.
    A conversation is due when it has messages newer than its last coding."""
    props: dict = {}
    for r in conn.execute(
            "SELECT proposal_id, room_id, hired, client_company, job_title, sent_dt"
            " FROM proposal_outcomes WHERE room_id IS NOT NULL ORDER BY sent_dt"):
        props.setdefault(r["room_id"], dict(r))
    coded = {r["thread_id"]: r["coded_through"] for r in conn.execute(
        "SELECT thread_id, MAX(coded_through) AS coded_through FROM conversation_codes"
        " GROUP BY thread_id")}
    out = []
    for r in conn.execute(
            "SELECT m.thread_id AS room, MAX(m.created_dt) AS last,"
            " SUM(lower(m.direction)='inbound') AS theirs, COUNT(*) AS n,"
            " t.room_name, t.client_id"
            " FROM messages m LEFT JOIN threads t ON t.id = m.thread_id"
            " GROUP BY m.thread_id"):
        p = props.get(r["room"])
        if is_excluded(reg, r["room"], r["room_name"], r["client_id"],
                       (p or {}).get("client_company")):
            continue
        if not r["theirs"]:
            continue                                        # nothing from them to read
        if coded.get(r["room"]) and coded[r["room"]] >= r["last"]:
            continue
        prio = (0 if p and not p["hired"] else 1) if p else 2
        out.append({"room": r["room"], "last": r["last"], "n": r["n"], "prio": prio,
                    "proposal": p, "client_id": r["client_id"], "room_name": r["room_name"]})
    # within a priority, newest conversations first (the freshest lessons):
    # sort by the newest message, then stably by priority
    out.sort(key=lambda x: x["last"] or "", reverse=True)
    out.sort(key=lambda x: x["prio"])
    return out


_WHO = {"inbound": "Client", "outbound": "Us", "system": "Upwork"}


def transcript(conn, item: dict) -> str:
    """The text the model reads: what the job and our proposal were, what
    Upwork recorded, then the newest MAX_MESSAGES messages, redacted."""
    rows = [dict(r) for r in conn.execute(
        "SELECT created_dt, lower(direction) AS dir, sender_user_name, message FROM messages"
        " WHERE thread_id=? ORDER BY created_dt DESC LIMIT ?", (item["room"], MAX_MESSAGES))]
    rows.reverse()
    head = []
    p = item.get("proposal")
    if p:
        o = conn.execute("SELECT kind, job_title, bid_amount, upwork_status, status_reason, hired,"
                         " client_did, letter FROM proposal_outcomes WHERE proposal_id=?",
                         (p["proposal_id"],)).fetchone()
        if o:
            head.append(f"Job: {o['job_title']}")
            head.append(f"Our proposal ({o['kind']}, bid {o['bid_amount']}), sent {str(p['sent_dt'])[:10]}:")
            head.append(redact((o["letter"] or "")[:3000]))
            head.append(f"Upwork status now: {o['upwork_status'] or '-'}"
                        f"{' (' + o['status_reason'] + ')' if o['status_reason'] else ''};"
                        f" hired us: {'yes' if o['hired'] else 'no'}"
                        f"{'; what the client did: ' + o['client_did'] if o['client_did'] else ''}")
    else:
        head.append("A client conversation (no proposal of ours on record for it).")
    lines = []
    for m in rows:
        who = _WHO.get(m["dir"], "Upwork")
        lines.append(f"[{str(m['created_dt'])[:16].replace('T', ' ')}] {who}: "
                     f"{redact((m['message'] or '').strip())[:2000]}")
    text = "\n".join(head) + "\n\nMessages (oldest first):\n" + "\n".join(lines)
    return text[-MAX_CHARS:] if len(text) > MAX_CHARS else text


def spent(conn) -> float:
    r = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM conversation_codes").fetchone()
    return float(r[0] or 0)


def _clean(parsed: dict) -> dict:
    """Cut anything secret-looking out of the answer too, and keep the enums honest."""
    out = dict(parsed or {})
    for k in ("lost_detail", "next_step", "reactivation_angle", "summary"):
        out[k] = redact(out.get(k) or "")[:600]
    out["objections"] = [redact(str(x))[:120] for x in (out.get("objections") or [])][:8]
    for k, allowed, dflt in (("stage_reached", STAGES, "replied"), ("outcome", OUTCOMES, "still_open"),
                             ("lost_reason", LOST, "other"), ("went_quiet", QUIET, "neither"),
                             ("price_talk", PRICE, "none"), ("tone", TONE, "neutral")):
        if out.get(k) not in allowed:
            out[k] = dflt
    return out


def code_one(conn, item: dict, db_path=None, _call=None) -> dict:
    """Read one conversation and store its coding. Returns the stored row."""
    call = _call or runner.claude_call
    text = transcript(conn, item)
    res = call(task_name=TASK_NAME, model_key=MODEL_KEY, system=SYSTEM, schema=SCHEMA,
               user_content=text, purpose=f"code {item['room']}",
               max_output_tokens=1200, db_path=db_path)
    c = _clean(res.parsed or {})
    p = item.get("proposal") or {}
    row = {
        "thread_id": item["room"], "coded_through": item["last"],
        "client_id": item.get("client_id"), "proposal_id": p.get("proposal_id"),
        "stage_reached": c["stage_reached"], "outcome": c["outcome"],
        "lost_reason": c["lost_reason"], "lost_detail": c["lost_detail"],
        "went_quiet": c["went_quiet"], "objections_json": json.dumps(c["objections"]),
        "price_talk": c["price_talk"], "next_step": c["next_step"], "tone": c["tone"],
        "reactivation_angle": c["reactivation_angle"], "summary": c["summary"],
        "model": getattr(res, "model", None), "cost_usd": float(getattr(res, "cost_usd", 0) or 0),
        "coder": "model",
        "coded_at": datetime.now(config.TZ).isoformat(timespec="seconds"),
    }
    cols = ", ".join(row)
    conn.execute(f"INSERT OR REPLACE INTO conversation_codes ({cols}) VALUES"
                 f" ({', '.join('?' * len(row))})", tuple(row.values()))
    return row


def code_batch(conn, *, db_path=None, budget: float = BUDGET_USD, limit: int = MAX_PER_RUN,
               reg=None, _call=None) -> dict:
    """Read the queue until the batch limit, the first-pass budget or the
    gateway's per-run cap stops it. Returns the run's metrics."""
    from core import claude_gateway as gw
    if reg is None:
        try:
            reg = registry.load()
        except Exception:                                  # noqa: BLE001
            reg = None
    q = queue(conn, reg)
    m = {"due": len(q), "coded": 0, "cost_usd": 0.0, "stopped": None,
         "spent_before": round(spent(conn), 4), "budget_usd": budget}
    for item in q[:limit]:
        if spent(conn) >= budget:
            m["stopped"] = "budget"
            break
        try:
            row = code_one(conn, item, db_path=db_path, _call=_call)
        except gw.ClaudeBudgetExceeded as exc:
            m["stopped"] = getattr(exc, "scope", None) or "cap"
            break
        conn.commit()
        m["coded"] += 1
        m["cost_usd"] = round(m["cost_usd"] + row["cost_usd"], 4)
    m["left"] = max(0, m["due"] - m["coded"])
    return m


def _run(ctx) -> dict:
    with db.connect(ctx.db_path) as conn:
        m = code_batch(conn, db_path=ctx.db_path)
    if not m["due"]:
        raise runner.TaskSkip("nothing new to read")
    return m


def main() -> int:
    runner.run_task(TASK_NAME, _run, module="upwork")
    return 0


if __name__ == "__main__":
    sys.exit(main())
