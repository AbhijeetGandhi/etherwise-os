"""The Pipeline tab — Upwork as the sales engine, end to end (2026-10-09).

Abhijeet, 2026-10-09, before bed: "now Upwork is our sales engine ... the
scanner ... the people we apply to ... how many opened ... replied ... hired
... how much they are paying ... each of these metrics can then reflect back
to the scanner ... and the proposal writer ... why only five people hired us
and what are the reasons that 25 people did not move ahead ... reactivation of
the client ... a really good UI in the cockpit ... different agents doing
different things but at the end there is this cohesive knowledge base". His
answers the same night: a NEW top-level Pipeline tab that the Funnel and the
scanner charts fold into (nothing parallel); an AI reading of conversations up
to $25, Globaltize excluded; win-back drafts, at most five a week, he sends.

ONE reader, ONE route (/api/lifecycle), no params. The proposals and their
money stay where they live (/api/upwork/funnel — the page reads both), so a
proposal figure here and on Proposals is the same array. This adds:

  scanner   what the scanner read per day, every job it flagged hot (with the
            proposal we sent on it, if we did), and what it said about every
            job we did apply to — "did we apply where it pointed?"
  talks     per proposal conversation, COUNTED from the stored messages: how
            often each side wrote, who wrote last and when, whether a call
            was mentioned, whether an offer exists. No message text leaves.
  codes     the conversation reader's notes (latest per conversation), what
            it has spent of its budget, and how many conversations wait
  tests     every test with its versions and its scoreboard
  clients   where every paying client is now — paying or stopped, came back
            or not — and the win-back list. Money, so doctor-gated: on a RED
            finance doctor this block is withheld and says so.
  agents    the seven agents: what each does, how its runs went, what it made
            this week, and what blocks it, in plain words with the fix
  kb        the shared knowledge base: what each store holds and how fresh

No model calls, no writes (cockpit law). `today` is a positional test clock;
`_now` (keyword-only, underscored, so a URL can never reach it) is the clock
the 24-hour windows use in tests.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from core import config
from modules.cockpit import data as _d
from modules.cockpit import proposal_funnel as _pf

HOT = config.HOT_LEAD_THRESHOLD
TOP = config.LOOM_FLAG_THRESHOLD

# ── the agents ───────────────────────────────────────────────────────────────
# One line each, in his words where he gave them. `tasks` are the scheduled
# jobs that ARE the agent; `stage` is where it sits on the pipeline strip.
AGENTS = (
    {"id": "scout", "name": "Scout", "stage": "found", "tasks": ("upwork_scan",),
     "does": "Reads every new Upwork job, drops the ones that break your rules,"
             " and scores the rest against what has won before."},
    {"id": "writer", "name": "Writer", "stage": "applied", "tasks": ("upwork_scan",),
     "does": "Drafts a proposal for every hot job from your proof points. Every"
             " other draft carries the running test's version."},
    {"id": "tracker", "name": "Tracker", "stage": "opened",
     "tasks": ("upwork_sync", "upwork_pulse", "upwork_inbox"),
     "does": "Keeps every proposal, open, reply, offer and contract current from Upwork."},
    {"id": "reader", "name": "Conversation reader", "stage": "talking",
     "tasks": ("upwork_conversation_codes",),
     "does": "Reads each conversation once, and again when it moves, and notes how"
             " far it got and why it stopped. Never reads Globaltize."},
    {"id": "accounts", "name": "Account keeper", "stage": "paid",
     "tasks": ("upwork_financials", "finance_wise_mail", "finance_stripe", "finance_payment_mail"),
     "does": "Records every payment (Upwork, the agency, Wise, Stripe, the bank) and"
             " names the client it came from."},
    {"id": "winback", "name": "Win-back", "stage": "back", "tasks": ("upwork_reactivation",),
     "does": "Picks past clients worth a check-in and drafts up to five messages a"
             " week for you to send."},
    {"id": "analyst", "name": "Analyst", "stage": None, "tasks": ("upwork_progress", "upwork_outcomes"),
     "does": "Calls the tests, writes the reading, and sends the progress email"
             " (Monday, Wednesday, Friday, and Sunday's)."},
)

# what stops an agent, read off the error its last run died with
_BLOCKERS = (
    ("upwork_mcp", re.compile(r"Upwork MCP grant has been dead since ([0-9:\- ]+IST)"),
     "The Upwork sign-in the scanner and the ledger use has expired.",
     "In Terminal, in the etherwise-v3 folder: PYTHONPATH=. python3 bin/upwork-mcp-auth"),
    ("upwork_api", re.compile(r"Upwork API grant has been dead since ([0-9:\- ]+IST)"),
     "The Upwork sign-in the sync and the inbox use has expired.",
     "In Terminal, in the etherwise-v3 folder: PYTHONPATH=. python3 bin/upwork-api-auth"),
    ("ai_credit", re.compile(r"credit balance is too low"),
     "The AI credit (Anthropic API) is used up.",
     "console.anthropic.com, Billing: add credit."),
)


def _has(conn, *tables) -> bool:
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return all(t in have for t in tables)


# ── the scanner ──────────────────────────────────────────────────────────────

def _scanner(conn, job_pid: dict) -> dict:
    """days: [day, read, passed the rules, hot, top]; hot: [job id, day, score,
    title, proposal id | None]; seen: {proposal id: [score | None, day, skip]}
    for every proposal whose job the scanner read."""
    days: dict = {}
    hot, seen = [], {}
    for r in conn.execute(
            "SELECT id, substr(fetched_at,1,10) AS d, score, hard_rule_skip, title"
            " FROM scored_jobs WHERE fetched_at LIKE '20%' ORDER BY fetched_at"):
        x = days.get(r["d"])
        if x is None:
            x = days[r["d"]] = [r["d"], 0, 0, 0, 0]
        x[1] += 1
        if r["hard_rule_skip"] is None:
            x[2] += 1
        s, jid = r["score"], str(r["id"])
        pid = job_pid.get(jid)
        if pid:
            seen[pid] = [s, r["d"], r["hard_rule_skip"]]
        if s is not None and s >= HOT:
            x[3] += 1
            if s >= TOP:
                x[4] += 1
            hot.append([jid, r["d"], s, (r["title"] or "")[:110], pid])
    return {"since": min(days) if days else None, "hot_at": HOT, "top_at": TOP,
            "days": [days[k] for k in sorted(days)], "hot": hot, "seen": seen,
            "job_url": "https://www.upwork.com/jobs/~02"}


# ── the conversations, counted ───────────────────────────────────────────────

_CALL = re.compile(r"zoom\.us|meet\.google|calendly\.com|teams\.microsoft|cal\.com/"
                   r"|\b(?:a|quick|short|brief|discovery|intro|video) call\b"
                   r"|\bhop on\b|\bjump on\b", re.I)
_DIR = {"inbound": "them", "outbound": "us"}


def _talks(conn) -> dict:
    """{proposal id: {rm, th, us, f, l, lf, call, off}} for every proposal with
    a conversation: messages from them / from us, first and last message,
    who wrote last, a call mentioned, an offer on record. `th` and `us` are
    None when no message of the room is stored (the backfill fills those)."""
    rooms: dict = {}
    for pid, room in conn.execute(
            "SELECT proposal_id, room_id FROM proposal_outcomes WHERE room_id IS NOT NULL"):
        rooms.setdefault(room, []).append(str(pid))
    offers = {r[0] for r in conn.execute(
        "SELECT DISTINCT thread_id FROM offers WHERE thread_id IS NOT NULL")} \
        if _has(conn, "offers") else set()
    st: dict = {}
    for room, d, dt, msg in conn.execute(
            "SELECT thread_id, lower(direction), created_dt, message FROM messages"
            " WHERE thread_id IN (SELECT room_id FROM proposal_outcomes WHERE room_id IS NOT NULL)"
            " ORDER BY created_dt"):
        who = _DIR.get(d)
        if not who:
            continue
        s = st.setdefault(room, {"th": 0, "us": 0, "f": None, "l": None, "lf": None, "call": 0})
        s["th" if who == "them" else "us"] += 1
        t = str(dt or "")[:19]
        s["f"] = s["f"] or t
        s["l"], s["lf"] = t, who
        if msg and _CALL.search(msg):
            s["call"] = 1
    out = {}
    for room, pids in rooms.items():
        s = st.get(room) or {"th": None, "us": None, "f": None, "l": None, "lf": None, "call": None}
        for pid in pids:
            out[pid] = dict(s, rm=room, off=1 if room in offers else 0)
    return out


# ── the conversation reader's notes ──────────────────────────────────────────

def _codes(conn, reg) -> dict:
    from modules.upwork import conversation_codes as cc
    if not _has(conn, "conversation_codes"):
        return {"rows": [], "spent_usd": 0.0, "budget_usd": cc.BUDGET_USD, "due": None}
    rows = []
    for r in conn.execute(
            "SELECT c.* FROM conversation_codes c JOIN (SELECT thread_id, MAX(coded_through) m"
            " FROM conversation_codes GROUP BY thread_id) x"
            " ON x.thread_id = c.thread_id AND x.m = c.coded_through"):
        r = dict(r)
        try:
            obj = json.loads(r.pop("objections_json") or "[]")
        except ValueError:
            obj = []
        rows.append({"room": r["thread_id"], "pid": r["proposal_id"], "client": r["client_id"],
                     "stage": r["stage_reached"], "outcome": r["outcome"],
                     "lost": r["lost_reason"], "lost_detail": r["lost_detail"],
                     "quiet": r["went_quiet"], "objections": obj, "price": r["price_talk"],
                     "next": r["next_step"], "tone": r["tone"],
                     "angle": r["reactivation_angle"], "summary": r["summary"],
                     "through": r["coded_through"], "at": r["coded_at"]})
    try:
        due = len(cc.queue(conn, reg))
    except Exception:                                      # noqa: BLE001
        due = None
    return {"rows": rows, "spent_usd": round(cc.spent(conn), 2), "budget_usd": cc.BUDGET_USD,
            "due": due, "excluded": list(cc.EXCLUDED)}


# ── the tests ────────────────────────────────────────────────────────────────

def _tests(conn, by_id: dict, day: str) -> list:
    from modules.upwork import experiments as ex
    if not _has(conn, "experiments", "experiment_arms", "experiment_assignments"):
        return []
    asg = ex.assignments(conn)
    out = []
    for t in ex.tests(conn):
        arms = [a["arm"] for a in t["arms"]]
        chosen = {a: [] for a in arms}
        detected = {a: 0 for a in arms}
        drafts = {a: 0 for a in arms}
        for a in asg:
            if a["experiment_id"] != t["id"]:
                continue
            if a["via"] == "draft":
                drafts[a["arm"]] = drafts.get(a["arm"], 0) + 1
            f = by_id.get(str(a["proposal_id"])) if a.get("proposal_id") else None
            if not f:
                continue
            if a["via"] == "draft":
                chosen.setdefault(a["arm"], []).append(
                    dict(f, _rewritten=bool(a.get("sent_arm") and a["sent_arm"] != a["arm"])))
            else:
                detected[a["arm"]] = detected.get(a["arm"], 0) + 1
        sb = ex.scoreboard(t, chosen, day) if t.get("started_at") else None
        out.append({
            "id": t["id"], "title": t["title"], "hypothesis": t["hypothesis"],
            "change": t["change"], "scope": t["scope"], "metric": t["metric"],
            "min_per_arm": t["min_per_arm"], "wait_days": t["wait_days"],
            "max_weeks": t["max_weeks"], "status": t["status"], "started_at": t["started_at"],
            "ended_at": t["ended_at"], "decision": t["decision"], "lesson": t["lesson"],
            "next_idea": t["next_idea"], "approved_by": t["approved_by"],
            "arms": [{"arm": a["arm"], "label": a["label"], "description": a["description"],
                      "detect_rule": a["detect_rule"]} for a in t["arms"]],
            "drafts": drafts, "detected": detected,
            "sent": {k: [r["id"] for r in v] for k, v in chosen.items()},
            "scoreboard": sb,
        })
    return out


# ── where every client is now, and the win-back list ─────────────────────────

def _clients(db_path, facts: list, day: str) -> dict:
    from modules.upwork import reactivation as ra
    money = _pf._money(db_path, facts)
    if money.get("blocked"):
        return {"blocked": True, "reds": money.get("reds") or [],
                "reason": "the finance doctor is RED, so who is paying is held back"
                          " until the ledger reconciles (the same gate as Money)"}
    reg = _pf._registry()
    who = _pf._Who(reg)
    conn = _d._ro(db_path)
    try:
        last_from = {r[0]: _DIR.get(r[1]) for r in conn.execute(
            "SELECT m.thread_id, lower(m.direction) FROM messages m JOIN ("
            " SELECT thread_id, MAX(created_dt) mx FROM messages"
            " WHERE lower(direction) IN ('inbound','outbound') GROUP BY thread_id) x"
            " ON x.thread_id = m.thread_id AND x.mx = m.created_dt"
            " WHERE lower(m.direction) IN ('inbound','outbound')")}
        # a client's conversation: the one the linker tied to them, the room of
        # a contract of theirs, or the room of the proposal that won them —
        # whichever moved last
        meta = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, room_name, client_id, latest_message_dt FROM threads")}
        msg_last = {r[0]: r[1] for r in conn.execute(
            "SELECT thread_id, MAX(created_dt) FROM messages GROUP BY thread_id")}
        cand: dict = {}

        def offer(k, rid):
            if k and rid:
                cand.setdefault(k, set()).add(rid)

        for rid, t in meta.items():
            if t["client_id"]:
                offer(who.of_id(t["client_id"]), rid)
        open_c: dict = {}
        for r in conn.execute("SELECT client_company, thread_id, upwork_status FROM contracts"):
            k = who.of_id((meta.get(r["thread_id"]) or {}).get("client_id")) \
                or who(_d._contract_client(r["client_company"]))
            offer(k, r["thread_id"])
            if k and r["upwork_status"] in ("ACTIVE", "PAUSED", "PENDING"):
                open_c[k] = open_c.get(k, 0) + 1
        won_room = {str(r[0]): r[1] for r in conn.execute(
            "SELECT proposal_id, room_id FROM proposal_outcomes WHERE room_id IS NOT NULL")}
        for k, c in (money.get("clients") or {}).items():
            offer(k, won_room.get(str(c.get("pid") or "")))

        def last_of(rid):
            return (meta.get(rid) or {}).get("latest_message_dt") or msg_last.get(rid) or ""

        rooms: dict = {}
        for k, rids in cand.items():
            rid = max(rids, key=last_of)
            rooms[k] = {"room": rid, "name": (meta.get(rid) or {}).get("room_name"),
                        "last": last_of(rid) or None, "from": last_from.get(rid)}
        angles: dict = {}
        if _has(conn, "conversation_codes"):
            for r in conn.execute(
                    "SELECT client_id, reactivation_angle FROM conversation_codes"
                    " WHERE client_id IS NOT NULL AND reactivation_angle IS NOT NULL"
                    " AND lower(reactivation_angle) NOT IN ('', 'none') ORDER BY coded_through"):
                angles[r[0]] = r[1]
        drafted = {r[0]: r[1] for r in conn.execute(
            "SELECT client_id, MAX(week) FROM reactivation_log GROUP BY client_id")} \
            if _has(conn, "reactivation_log") else {}
        drafts: dict = {}
        for r in conn.execute(
                "SELECT id, thread_id, generated_at, body, sent_status FROM drafts"
                " WHERE draft_kind='reactivation' ORDER BY generated_at"):
            drafts[r["thread_id"]] = {"id": r["id"], "at": r["generated_at"], "body": r["body"],
                                      "status": r["sent_status"]}
    finally:
        conn.close()
    rows = ra.lifecycle(money, day, rooms=rooms, open_contracts=open_c, reg=reg, key_of=who)
    ranked = ra.rank(rows, day, drafted=drafted, angles=angles)
    for r in ranked["top"]:
        room = (r.get("room") or {}).get("room")
        r["draft"] = drafts.get(room) if room else None
    return {"blocked": False, "rows": rows, "winback": ranked,
            "rules": {"paying_days": ra.PAYING_DAYS, "quiet_days": ra.QUIET_DAYS,
                      "return_gap_days": ra.RETURN_GAP_DAYS, "min_paid_usd": ra.MIN_PAID_USD,
                      "weekly_drafts": ra.WEEKLY_DRAFTS, "rest_weeks": ra.REST_WEEKS}}


# ── the agents ───────────────────────────────────────────────────────────────

def _blocker_of(error: Optional[str]) -> Optional[tuple]:
    for kind, rx, words, fix in _BLOCKERS:
        m = rx.search(error or "")
        if m:
            return kind, (m.group(1).strip() if m.groups() else None), words, fix
    return None


def _ai_credit(conn) -> Optional[dict]:
    """Out of AI credit right now? The newest gateway call says; 'since' is the
    first credit failure after the last call that went through."""
    last = conn.execute("SELECT called_at, error FROM claude_usage ORDER BY id DESC LIMIT 1").fetchone()
    if not last or "credit balance is too low" not in (last["error"] or ""):
        return None
    ok = conn.execute("SELECT MAX(called_at) FROM claude_usage WHERE error IS NULL").fetchone()[0]
    since = conn.execute(
        "SELECT MIN(called_at) FROM claude_usage WHERE error LIKE '%credit balance is too low%'"
        " AND called_at > ?", (ok or "",)).fetchone()[0]
    n = conn.execute(
        "SELECT COUNT(*) FROM claude_usage WHERE error LIKE '%credit balance is too low%'"
        " AND called_at > ?", (ok or "",)).fetchone()[0]
    return {"since": since, "failed_calls": n, "last_ok": ok}


def _agents(conn, now: datetime) -> dict:
    cut = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    week = (now - timedelta(days=7)).isoformat(timespec="seconds")
    day = now.strftime("%Y-%m-%d")
    tasks: dict = {}
    names = sorted({t for a in AGENTS for t in a["tasks"]})
    for t in names:
        last = conn.execute("SELECT started_at, status, error FROM runs WHERE task_name=?"
                            " ORDER BY id DESC LIMIT 1", (t,)).fetchone()
        okn = conn.execute("SELECT COUNT(*) FROM runs WHERE task_name=? AND started_at>=?"
                           " AND status NOT IN ('failed')", (t, cut)).fetchone()[0]
        bad = conn.execute("SELECT COUNT(*) FROM runs WHERE task_name=? AND started_at>=?"
                           " AND status='failed'", (t, cut)).fetchone()[0]
        lastok = conn.execute("SELECT MAX(started_at) FROM runs WHERE task_name=?"
                              " AND status IN ('completed','skipped_empty','skipped')",
                              (t,)).fetchone()[0]
        spend = conn.execute("SELECT COALESCE(SUM(total_cost_usd),0) FROM claude_usage"
                             " WHERE task_name=? AND ist_date=?", (t, day)).fetchone()[0]
        b = _blocker_of(last["error"]) if last and last["status"] == "failed" else None
        tasks[t] = {"task": t, "ran": bool(last), "last_at": last["started_at"] if last else None,
                    "last_status": last["status"] if last else None,
                    "error": (str(last["error"] or "").splitlines() or [""])[0][:240]
                    if last and last["status"] == "failed" else None,
                    "last_ok": lastok, "ok_24h": okn, "failed_24h": bad,
                    "ai_usd_today": round(float(spend or 0), 2),
                    "blocker": b[0] if b else None}
    credit = _ai_credit(conn)
    blockers: dict = {}
    for t in tasks.values():
        if not t["blocker"]:
            continue
        kind = t["blocker"]
        b = next(x for x in _BLOCKERS if x[0] == kind)
        e = blockers.setdefault(kind, {"kind": kind, "words": b[2], "fix": b[3], "since": None,
                                       "tasks": []})
        m = b[1].search(conn.execute("SELECT error FROM runs WHERE task_name=? ORDER BY id DESC"
                                     " LIMIT 1", (t["task"],)).fetchone()[0] or "")
        if m and m.groups() and not e["since"]:
            e["since"] = m.group(1).strip()
        e["tasks"].append(t["task"])
    if credit:
        e = blockers.setdefault("ai_credit", {"kind": "ai_credit", "words": _BLOCKERS[2][2],
                                              "fix": _BLOCKERS[2][3], "since": None, "tasks": []})
        e["since"] = e["since"] or credit["since"]
        e["failed_calls"] = credit["failed_calls"]
    made = _made(conn, week, now)
    out = []
    for a in AGENTS:
        ts = [tasks[t] for t in a["tasks"]]
        bl = sorted({t["blocker"] for t in ts if t["blocker"]})
        if a["id"] in ("writer", "reader", "winback") and credit:
            bl = sorted(set(bl) | {"ai_credit"})
        if not any(t["ran"] for t in ts):
            state = "off"
        elif bl:
            state = "blocked"
        elif any(t["last_status"] == "failed" for t in ts):
            state = "failing"
        else:
            state = "working"
        out.append({"id": a["id"], "name": a["name"], "does": a["does"], "stage": a["stage"],
                    "state": state, "blockers": bl, "tasks": ts, "made": made.get(a["id"], [])})
    return {"agents": out, "blockers": list(blockers.values()), "as_of": now.isoformat(timespec="seconds")}


def _count(conn, sql: str, *args) -> Optional[int]:
    try:
        return conn.execute(sql, args).fetchone()[0]
    except Exception:                                      # noqa: BLE001
        return None


def _made(conn, week: str, now: datetime) -> dict:
    """What each agent made in the last 7 days, in plain words."""
    wk = week[:10]
    return {
        "scout": [
            ("jobs read", _count(conn, "SELECT COUNT(*) FROM scored_jobs WHERE fetched_at >= ?", wk)),
            ("flagged hot", _count(conn, "SELECT COUNT(*) FROM scored_jobs WHERE fetched_at >= ?"
                                         " AND score >= ?", wk, HOT)),
        ],
        "writer": [
            ("drafts written", _count(conn, "SELECT COUNT(*) FROM scored_jobs WHERE fetched_at >= ?"
                                            " AND draft_proposal IS NOT NULL", wk)),
            ("drafts in a test", _count(conn, "SELECT COUNT(*) FROM experiment_assignments"
                                              " WHERE via='draft' AND assigned_at >= ?", wk)),
        ],
        "tracker": [
            ("proposals sent", _count(conn, "SELECT COUNT(*) FROM proposal_outcomes WHERE sent_dt >= ?", wk)),
            ("messages stored", _count(conn, "SELECT COUNT(*) FROM messages WHERE created_dt >= ?", wk)),
        ],
        "reader": [
            ("conversations read", _count(conn, "SELECT COUNT(DISTINCT thread_id) FROM conversation_codes"
                                                " WHERE coded_at >= ?", wk)),
        ],
        "accounts": [
            ("payments and costs recorded", _count(conn, "SELECT COUNT(*) FROM transactions"
                                                         " WHERE creation_dt >= ?", wk)),
        ],
        "winback": [
            ("check-ins drafted", _count(conn, "SELECT COUNT(*) FROM drafts WHERE draft_kind='reactivation'"
                                               " AND generated_at >= ?", wk)),
        ],
        "analyst": [
            ("progress emails", _count(conn, "SELECT COUNT(*) FROM runs WHERE task_name='upwork_progress'"
                                             " AND status='completed' AND started_at >= ?", week)),
        ],
    }


# ── the knowledge base ───────────────────────────────────────────────────────

_KB = (
    ("scored_jobs", "Jobs the scanner read", "fetched_at"),
    ("proposal_outcomes", "Proposals sent, and what came of each", "sent_dt"),
    ("proposal_views", "Proposal pages read (the open, the job's counts)", "read_at"),
    ("threads", "Conversations", "latest_message_dt"),
    ("messages", "Messages", "created_dt"),
    ("offers", "Offers", "updated_at"),
    ("contracts", "Contracts", "start_dt"),
    ("transactions", "Payments and costs (the ledger)", "creation_dt"),
    ("conversation_codes", "Conversations the reader has read", "coded_at"),
    ("experiments", "Tests", "started_at"),
    ("experiment_assignments", "Drafts and proposals in a test", "assigned_at"),
    ("job_snapshots", "Job activity, as it was when we applied", "taken_at"),
    ("drafts", "Reply and check-in drafts", "generated_at"),
    ("facts", "What we know from calls, Looms and documents", "created_at"),
)


def _kb(conn) -> dict:
    stores = []
    for table, words, col in _KB:
        if not _has(conn, table):
            continue
        n = _count(conn, f"SELECT COUNT(*) FROM {table}")
        newest = None
        try:
            newest = conn.execute(f"SELECT MAX({col}) FROM {table}").fetchone()[0]
        except Exception:                                  # noqa: BLE001
            newest = None
        stores.append({"table": table, "words": words, "rows": n, "newest": newest})
    rooms = _count(conn, "SELECT COUNT(DISTINCT room_id) FROM proposal_outcomes WHERE room_id IS NOT NULL")
    stored = _count(conn, "SELECT COUNT(DISTINCT po.room_id) FROM proposal_outcomes po"
                          " JOIN messages m ON m.thread_id = po.room_id")
    return {"stores": stores,
            "gaps": {"proposal_rooms": rooms, "rooms_with_messages": stored}}


# ── the route ────────────────────────────────────────────────────────────────

def lifecycle(db_path: Optional[Path] = None, today: Optional[str] = None, *,
              _now: Optional[datetime] = None) -> dict:
    """GET /api/lifecycle — everything the Pipeline tab adds to the funnel."""
    day = today or _d._ist_today()
    now = _now or datetime.now(config.TZ)
    conn = _d._ro(db_path)
    try:
        if not _has(conn, "proposal_outcomes", "scored_jobs"):
            return {"as_of": day, "built": False,
                    "reason": "the proposal tables are not in this database yet (migration 038)"}
        facts = _pf._facts(conn)
        job_pid: dict = {}
        for f in sorted(facts, key=lambda f: f.get("sent") or ""):
            if f.get("job"):
                job_pid.setdefault(str(f["job"]), f["id"])
        reg = _pf._registry()
        scanner = _scanner(conn, job_pid)
        talks = _talks(conn)
        codes = _codes(conn, reg)
        tests = _tests(conn, {f["id"]: f for f in facts}, day)
        agents = _agents(conn, now)
        kb = _kb(conn)
    finally:
        conn.close()
    try:
        clients = _clients(db_path, facts, day)
    except Exception as exc:                               # noqa: BLE001
        clients = {"blocked": True, "reason": f"could not be read: {exc!r}"}
    return {"as_of": day, "built": True, "scanner": scanner, "talks": talks, "codes": codes,
            "tests": tests, "clients": clients, "agents": agents, "kb": kb}
