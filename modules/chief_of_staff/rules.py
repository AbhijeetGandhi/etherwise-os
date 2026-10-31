"""Deterministic sweep rules — the cheap pre-filter that surfaces CANDIDATE
nudges from Sales (M1) + Knowledge (M3) + System. Rules are PURE producers:
each returns a list of candidate dicts (write_nudge kwargs); the sweep writes
them, clears conditions that vanished, hashes the set for empty-skip, and only
then spends Opus. No model calls here; reuses the cockpit's live queries so
there's one source of truth for "owed reply" / "hot lead".

Design §5 + Abhijeet's 2026-06-22 redlines (config thresholds).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from core import config, db, registry
from modules.chief_of_staff import evidence, nudges

# scored_jobs states that are still actionable (not parked/declined/applied)
_ACTIONABLE_JOB = ("Scored", "Drafted")


def _registry():
    """The registry is advisory here: a malformed clients.yaml must degrade the
    card's LABEL, never stop the sweep from surfacing the work."""
    try:
        return registry.load()
    except Exception:
        return None


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(config.TZ)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# ── Sales (M1) ────────────────────────────────────────────────────────────────

def rule_owed_replies(db_path, now=None) -> List[dict]:
    """A pending draft + the counterparty silent >= COS_FOLLOWUP_IDLE_DAYS ->
    nudge 'follow up (draft ready)'. Mirrors the cockpit Today follow-up source.

    ONE CARD PER PERSON, not per room (see nudges.followup_key). The card is
    labelled with the registry client when the thread is attributed, so an
    active client never reads as a cold Upwork lead — the Brandi Jordan / Cradle
    Company symptom Abhijeet caught on 2026-08-12.

    IDLENESS AND EVIDENCE ARE CROSS-CHANNEL (2026-08-12). Both used to come from
    `threads.latest_message_dt`: the newest row of ANY kind on the UPWORK thread.
    That signal is not the counterparty (90 of 139 threads had a newer non-
    inbound row; 21 had an Upwork *system* row as their latest) and it is not
    the conversation (The Cradle Company's runs on Slack). The SQL below stays
    as a cheap Upwork-side prefilter — it can only over-select — and
    `evidence.counterparty` then answers, per person, when they actually last
    spoke on any channel. A counterparty who spoke inside the idle window is
    dropped: the rule's own semantics, applied to honest data. Dropping is safe
    here — the sweep decays the nudge rather than deleting it, and it re-mints
    the moment the condition holds again."""
    cutoff = (_now(now) - timedelta(days=config.COS_FOLLOWUP_IDLE_DAYS))
    groups: dict = {}
    with db.connect(db_path) as conn:
        for r in conn.execute(
            "SELECT d.thread_id, t.topic, t.room_name, t.latest_message_dt,"
            " t.client_id"
            " FROM drafts d JOIN threads t ON t.id = d.thread_id"
            " WHERE d.sent_status='pending'"
            # a win-back draft (reactivation.py, Mondays) is not an owed reply —
            # same exclusion as the Today follow-up builder (data._act_now_queue)
            " AND COALESCE(d.draft_kind,'') != 'reactivation'"
            " AND substr(t.latest_message_dt,1,10) <= ?"
            " ORDER BY t.latest_message_dt DESC",
                (cutoff.strftime("%Y-%m-%d"),)):
            key = nudges.followup_key(r["client_id"], r["room_name"],
                                      r["thread_id"])
            g = groups.setdefault(key, {"rows": [], "client_id": r["client_id"]})
            g["rows"].append(dict(r))

        # one evidence read per counterparty, inside the same connection.
        # EVERY room that person holds, not the ones that happen to carry a
        # pending draft (Abhijeet, 2026-08-27: Upwork opens a new room per job,
        # the talking moves there, and this rule kept nagging about the room it
        # left). counterparty.expand is the whole difference between "have we
        # answered them" and "have we answered them in THIS room".
        from modules.upwork import counterparty as _cp
        for key, g in groups.items():
            tids = _cp.expand(conn, [r["thread_id"] for r in g["rows"]])
            g["thread_ids"] = tids
            g["ev"] = evidence.counterparty(
                conn, thread_ids=tids, client_id=g["client_id"],
                thread_latest=max(r["latest_message_dt"] for r in g["rows"]))
            g["engagement"] = evidence.engagement(
                conn, thread_ids=tids, client_id=g["client_id"])

    reg = _registry()
    nowdt = _now(now)
    out = []
    for key, g in groups.items():
        rows = g["rows"]                       # newest thread first (ORDER BY)
        head = rows[0]
        ev, eng = g["ev"], g["engagement"]
        # idle = the OLDEST obligation this person holds, cross-channel aware
        # (evidence.counterparty explains why that is not the same as `ts`)
        idle = evidence.age_days(ev["waiting_since"], nowdt)
        if idle is not None and idle < config.COS_FOLLOWUP_IDLE_DAYS:
            continue      # they spoke recently, just not on Upwork — not idle
        # WE SPOKE AFTER THEY DID, anywhere: no reply is owed, whatever an
        # unsent draft on one room says. Until 2026-08-27 `answered_since` was
        # computed and shown but never allowed to suppress, and the group only
        # covered the drafted rooms — so "Client comms: Dhanya Sangolli" sat on
        # Today because of a draft from 30 July while he had answered her on
        # 26 August and started a $300 contract with her that day.
        if ev.get("answered_since"):
            continue
        # the label: registry name for an attributed thread, else the room's
        # counterparty name. Never the raw room id.
        label = None
        if g["client_id"] and reg:
            label = reg.canonical(g["client_id"])
        label = label or (head["room_name"] or "").split(",")[0].strip() \
            or head["thread_id"]
        topic = head["topic"] or head["room_name"] or head["thread_id"]
        # A CONTRACTED CLIENT IS NOT A SALES FOLLOW-UP. The kind stays
        # 'owed_reply' on purpose — card identity, decay TTL, done-semantics and
        # the Today dedup all key off it, and renaming the kind to fix a LABEL
        # would break four things to fix one. What changes is the claim the card
        # makes about the relationship, and where it points the founder.
        client_card = bool(eng.get("active"))
        if client_card:
            title = f"Client comms: {topic}" if len(rows) == 1 \
                else f"Client comms: {label} — {len(rows)} open threads"
            action = (f"Active client (contract {eng['contract_id']}) with an"
                      f" UNSENT Upwork draft. " + evidence.describe(ev, eng)
                      + ". Send it or bin it — do not chase them as a lead.")
        else:
            title = f"Follow up: {topic}" if len(rows) == 1 \
                else f"Follow up: {label} — {len(rows)} open threads"
            action = "Draft is ready — review + send in Upwork."
        out.append({
            "source": "sales", "dept": "sales", "kind": "owed_reply",
            "title": title, "client": label,
            "action": action,
            "due": None, "dedup_key": key,
            # thread_id(s) in the payload so the Today assembler can dedup this
            # nudge against its follow-up card (the "follow-up x2" root cause).
            # evidence_ts = when the COUNTERPARTY last spoke, ACROSS CHANNELS.
            # write_nudge uses it to decide whether a card the founder already
            # marked done has any REASON to come back: if nothing has been said
            # since they actioned it, it stays gone. Without this the card
            # refired every sweep and 'Done' was meaningless (Rocky Choy
            # accumulated 10 rows, Mustafa 11, before this was caught
            # 2026-08-12). Grouping matters here too: acting on the newer of a
            # person's two rooms now settles both.
            "payload": {"idle_days": idle,
                        "thread_id": head["thread_id"],
                        # EVERY room this person holds, so the conversation
                        # view (data._conversation renders whatever it is given)
                        # shows the chat the talking actually moved to, not the
                        # one that happens to hold an unsent draft.
                        "thread_ids": g["thread_ids"],
                        "client_id": g["client_id"],
                        "engagement": eng, "evidence": ev,
                        "evidence_ts": ev["ts"]}})
    return out


def rule_hotlead_no_loom(db_path, now=None) -> List[dict]:
    """Hot lead flagged for a custom Loom (loom_flag) and still actionable, no
    Loom recorded -> 'record custom Loom'."""
    out = []
    qs = ",".join("?" * len(_ACTIONABLE_JOB))
    recent_lo = (_now(now) - timedelta(days=config.COS_HOTLEAD_RECENT_DAYS)
                 ).strftime("%Y-%m-%d")
    with db.connect(db_path) as conn:
        for r in conn.execute(
            "SELECT id, title, score, job_url, fixed_budget, hourly_max"
            " FROM scored_jobs WHERE loom_flag=1 AND clickup_task_id IS NULL"
            f" AND status IN ({qs}) AND substr(first_scored_at,1,10) >= ?",
                (*_ACTIONABLE_JOB, recent_lo)):
            stake = r["fixed_budget"] or (
                (r["hourly_max"] or 0) * 160 or None)   # ~monthly proxy
            out.append({
                "source": "sales", "dept": "sales", "kind": "hotlead_no_loom",
                "title": f"Record a Loom: {r['title']}",
                "action": "Score is Loom-worthy and no Loom yet — record a custom one.",
                "due": None, "dedup_key": f"hotlead_no_loom:{r['id']}",
                "money_at_stake": stake, "score": r["score"],
                "payload": {"job_url": r["job_url"]}})
    return out


def rule_won_no_outcome(db_path, now=None) -> List[dict]:
    """Won proposal with no outcome reason captured -> 'capture outcome'."""
    out = []
    recent_lo = (_now(now) - timedelta(days=config.COS_WON_OUTCOME_RECENT_DAYS)
                 ).strftime("%Y-%m-%d")
    with db.connect(db_path) as conn:
        for r in conn.execute(
            "SELECT id, job_title, client_company FROM proposals"
            " WHERE status='Won' AND (status_reason IS NULL OR status_reason='')"
            " AND substr(COALESCE(modified_dt, created_dt),1,10) >= ?",
                (recent_lo,)):
            out.append({
                "source": "sales", "dept": "sales", "kind": "won_no_outcome",
                "title": f"Capture why we won: {r['job_title'] or r['id']}",
                "client": r["client_company"],
                "action": "Record the outcome reason (feeds scoring + proof-points).",
                "due": None, "dedup_key": f"won_outcome:{r['id']}"})
    return out


def rule_lost_pattern(db_path, now=None) -> List[dict]:
    """COS_LOST_PATTERN_MIN+ losses in one job category -> revisit scoring/
    positioning. Category comes from the scored_jobs join."""
    placeholders = ",".join("?" * len(config.COS_LOST_STATUSES))
    out = []
    with db.connect(db_path) as conn:
        for r in conn.execute(
            f"SELECT sj.category AS category, COUNT(*) AS n FROM proposals p"
            f" JOIN scored_jobs sj ON sj.id = p.marketplace_job_id"
            f" WHERE p.status IN ({placeholders}) AND sj.category IS NOT NULL"
            f" GROUP BY sj.category HAVING n >= ?",
                (*config.COS_LOST_STATUSES, config.COS_LOST_PATTERN_MIN)):
            out.append({
                # count lives in the payload (rendered live), never the stored
                # title — cluster/count is a render-time grouping, not a card
                "source": "sales", "dept": "sales", "kind": "lost_pattern",
                "title": f"Revisit scoring: {r['category']}",
                "action": "Review positioning/rate/scoring for this category.",
                "due": None, "dedup_key": f"lost_pattern:{r['category']}",
                "payload": {"losses": r["n"]}})
    return out


# ── Knowledge (M3) ────────────────────────────────────────────────────────────

def rule_stale_dossier(db_path, now=None) -> List[dict]:
    """A client has ingested sources not yet extracted into facts -> the brain
    has unprocessed material; nudge to run extraction."""
    out = []
    with db.connect(db_path) as conn:
        for r in conn.execute(
            "SELECT client_id, COUNT(*) AS n FROM sources"
            " WHERE status='ingested' AND client_id IS NOT NULL"
            " GROUP BY client_id"):
            out.append({
                "source": "knowledge", "dept": "knowledge",
                "kind": "stale_dossier",
                "title": f"Unprocessed sources: {r['client_id']}",
                "client": r["client_id"],
                "action": f"Run extraction so {r['client_id']}'s dossier is current.",
                "due": None, "dedup_key": f"stale_dossier:{r['client_id']}",
                "payload": {"ingested": r["n"]}})
    return out


def rule_commitments_due(db_path, now=None) -> List[dict]:
    """Our open commitments due within COS_COMMITMENT_DUE_DAYS (or recently
    overdue) -> escalate. Bounded on BOTH sides: nothing overdue by more than
    COS_COMMITMENT_STALE_DAYS, whose relative due date ('demo tomorrow' from an
    old call) is no longer meaningful and only floods Today. Reads the
    commitments table."""
    horizon = (_now(now) + timedelta(days=config.COS_COMMITMENT_DUE_DAYS))
    stale_floor = (_now(now) - timedelta(days=config.COS_COMMITMENT_STALE_DAYS))
    out = []
    with db.connect(db_path) as conn:
        for r in conn.execute(
            "SELECT id, text, due, source_cite, client_id FROM commitments"
            " WHERE state='open' AND who='us' AND due IS NOT NULL"
            " AND COALESCE(review_state,'') != 'stale'"     # stale -> not a card
            " AND source_cite NOT LIKE 'upwork/%'"           # Upwork promises: the branch below
            " AND substr(due,1,10) <= ? AND substr(due,1,10) >= ?",
            (horizon.strftime("%Y-%m-%d"), stale_floor.strftime("%Y-%m-%d"))):
            out.append({
                # write_nudge strips relative-date strings from a dated title; the
                # due DATE is stored and rendered as "due tomorrow / overdue 1d"
                "source": "commitment", "dept": "knowledge",
                "kind": "commitment_due",
                "title": f"Commitment due: {r['text'][:80]}",
                "client": r["client_id"],
                "action": "Deliver or update the client.",
                "due": r["due"], "dedup_key": f"commitment:{r['id']}",
                "payload": {"cite": r["source_cite"]}})
        # a promise typed on Upwork is a task the moment it is made (Abhijeet,
        # 2026-08-26): dated or not (undated = due UPWORK_PROMISE_DAYS after
        # the promise), the card says whom and where, and opens the room
        carded = {c["dedup_key"] for c in out}
        out.extend(c for c in _upwork_promise_cards(conn, now)
                   if c["dedup_key"] not in carded)
    return out


def _upwork_promise_cards(conn, now=None) -> List[dict]:
    """Cards for open promises made on Upwork (modules/upwork/promises.py):
    "You promised <room>: <text>", due as stated or UPWORK_PROMISE_DAYS after
    the message; the room and the message id ride in the payload so Today can
    open the thread. Bounded like the dated rule (nothing older than
    COS_COMMITMENT_STALE_DAYS)."""
    from modules.upwork import promises as _promises
    nowdt = _now(now)
    stale_floor = (nowdt - timedelta(days=config.COS_COMMITMENT_STALE_DAYS)).strftime("%Y-%m-%d")
    out = []
    for r in conn.execute(
            "SELECT c.id, c.text, c.due, c.source_cite, c.client_id, c.created_at"
            " FROM commitments c WHERE c.state='open' AND c.who='us'"
            " AND COALESCE(c.review_state,'') != 'stale' AND c.source_cite LIKE ?"
            " AND substr(c.created_at,1,10) >= ?",
            (_promises.SOURCE_PREFIX + "%", stale_floor)):
        parsed = _promises.parse_cite(r["source_cite"])
        room_id, message_id = parsed if parsed else (None, None)
        room = conn.execute("SELECT room_name, client_id FROM threads WHERE id=?",
                            (room_id,)).fetchone() if room_id else None
        promised = conn.execute("SELECT created_dt FROM messages WHERE id=?",
                                (message_id,)).fetchone() if message_id else None
        promised_on = _ist_day((promised["created_dt"] if promised else None) or r["created_at"])
        # the person we answered: whoever wrote to us last before the promise
        to = conn.execute(
            "SELECT sender_user_name FROM messages WHERE thread_id=? AND created_dt < ?"
            " AND lower(direction)='inbound' AND sender_user_name IS NOT NULL"
            " ORDER BY created_dt DESC LIMIT 1",
            (room_id, promised["created_dt"] if promised else "")).fetchone() if promised else None
        due = r["due"]
        if not due:
            try:
                due = (datetime.strptime(promised_on, "%Y-%m-%d")
                       + timedelta(days=config.UPWORK_PROMISE_DAYS)).strftime("%Y-%m-%d")
            except ValueError:
                due = (nowdt + timedelta(days=config.UPWORK_PROMISE_DAYS)).strftime("%Y-%m-%d")
        room_name = (room["room_name"] if room else None) or "the client"
        who = (to["sender_user_name"] if to else None) or room_name.split(",")[0].strip()
        client = r["client_id"] or (room["client_id"] if room else None) or room_name
        # reply-type promises clear themselves on the next message in the
        # room; a deliverable waits for Done or its date (his 26 Aug rule)
        reply = _promises.is_reply_type(r["text"])
        out.append({
            "source": "commitment", "dept": "sales", "kind": "commitment_due",
            "title": f"You promised {who}: {r['text'][:80]}",
            "client": client,
            "action": ("Reply on Upwork — your next message in the room clears this by itself."
                       if reply else
                       "Mark Done here when it is delivered — a reply alone does not clear it."),
            "due": due, "dedup_key": f"commitment:{r['id']}",
            "payload": {"cite": r["source_cite"], "room_id": room_id,
                        "room_name": room_name, "to": who, "promised_on": promised_on,
                        "undated": r["due"] is None, "auto_clears": reply}})
    return out


def _ist_day(ts: Optional[str]) -> str:
    """An Upwork ISO instant (UTC) or a SQLite 'now' → the IST calendar day;
    a promise typed at 05:07 IST is a 26 Aug promise, not a 25 Aug one."""
    if not ts:
        return ""
    import re as _re
    t = _re.sub(r"\.\d+", "", str(ts).strip()).replace("Z", "+00:00").replace(" ", "T", 1)
    if not _re.search(r"[+-]\d\d:\d\d$", t):
        t += "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return str(ts)[:10]
    return dt.astimezone(config.TZ).strftime("%Y-%m-%d")


# ── System ────────────────────────────────────────────────────────────────────

def rule_upwork_waiting(db_path, now=None) -> List[dict]:
    """An OFFER or an INVITATION is waiting on Upwork (the quarter-hour
    heartbeat, modules/upwork/feeds/mcp_pulse.py — his 26 Aug 22:30 answer).
    Both are money that arrived without him asking, and until tonight neither
    had anywhere to appear: the offers table had not been written since 11
    June and invitations had no table at all. Due TODAY — an unanswered offer
    expires."""
    out = []
    today = _now(now).strftime("%Y-%m-%d")
    with db.connect(db_path) as conn:
        try:
            offers = list(conn.execute(
                "SELECT id, title, client_name, type FROM offers"
                " WHERE state IN ('awaiting_your_acceptance', 'AWAITING_YOUR_ACCEPTANCE')"))
        except Exception:                       # noqa: BLE001 — pre-migration DB
            offers = []
        for r in offers:
            who = r["client_name"] or "a client"
            out.append({
                "source": "sales", "dept": "sales", "kind": "upwork_offer",
                "title": f"Offer waiting: {(r['title'] or 'an Upwork offer')[:70]}",
                "client": r["client_name"],
                "action": "Open it on Upwork and accept or decline — an offer expires on its own.",
                "due": today, "dedup_key": f"upwork_offer:{r['id']}",
                "payload": {"offer_id": r["id"], "who": who, "type": r["type"],
                            "url": "https://www.upwork.com/nx/offers/"}})
        try:
            invites = list(conn.execute(
                "SELECT id, job_title, client_name, created_dt FROM upwork_invitations"
                " WHERE seen_at IS NULL AND COALESCE(state,'pending') NOT IN"
                " ('declined', 'expired', 'withdrawn')"))
        except Exception:                       # noqa: BLE001 — pre-033 DB
            invites = []
        for r in invites:
            out.append({
                "source": "sales", "dept": "sales", "kind": "upwork_invitation",
                "title": f"Invited to a job: {(r['job_title'] or 'an Upwork job')[:70]}",
                "client": r["client_name"],
                "action": "A client invited you — read it and answer; invitations expire quietly.",
                "due": today, "dedup_key": f"upwork_invite:{r['id']}",
                "payload": {"invitation_id": r["id"], "invited_on": r["created_dt"]}})
    return out


def rule_stalled_milestone(db_path, now=None) -> List[dict]:
    """A fixed-price milestone the client never funded, past its due date —
    money agreed and not moving (modules/upwork/feeds/mcp_inflight.py). Found
    the day it was built: Mustafa's contract 43986743, $3,000 across two
    milestones due 15 and 22 July, still Not funded on 26 August."""
    from modules.finance import money as _money
    out = []
    try:
        with db.connect(db_path) as conn:
            from modules.upwork.feeds import mcp_inflight
            rows = mcp_inflight.stalled(conn, _now(now).date())
    except Exception:                           # noqa: BLE001 — pre-033 DB
        return out
    for r in rows:
        amount = _money.from_minor(r["amount_minor"]) if r["amount_minor"] is not None else None
        who = r["client_company"] or r["contract_title"] or r["contract_id"]
        out.append({
            "source": "sales", "dept": "sales", "kind": "stalled_milestone",
            # the client leads the title: two milestones on one contract read as
            # duplicates otherwise (Mustafa's are $1,500 each, 15 and 22 Jul).
            "title": (f"Unfunded milestone {r['sequence_id'] or ''}".rstrip() +
                      (f": ${amount:,.2f}" if amount is not None else "") +
                      f" — {str(who)[:40]}"),
            "client": r["client_company"],
            "money_at_stake": float(amount) if amount is not None else None,
            "action": "The client never funded this milestone and its date has passed —"
                      " ask them to fund it, or agree to drop it.",
            "due": (r["due_dt"] or "")[:10] or None,
            "dedup_key": f"milestone:{r['id']}",
            "payload": {"contract_id": r["contract_id"], "milestone_id": r["id"],
                        "sequence": r["sequence_id"], "who": who,
                        "amount_minor": r["amount_minor"], "due": r["due_dt"]}})
    return out


def rule_system(db_path, now=None) -> List[dict]:
    """Failed jobs (last N hours) + spend approaching the daily cap."""
    nowdt = _now(now)
    out = []
    with db.connect(db_path) as conn:
        fail_lo = (nowdt - timedelta(hours=config.COS_SYSTEM_FAIL_WINDOW_H))
        # A failure a later run of the same task has already recovered from is
        # HISTORY, not an action. upwork_scan runs hourly; three transient
        # osascript timeouts put three "Job failed" cards on Today (plus two
        # Opus escalations about them) while the scan had been green for hours.
        # A card you cannot act on teaches you to ignore the board.
        for r in conn.execute(
            "SELECT f.id, f.task_name, f.started_at FROM runs f"
            " WHERE f.status='failed' AND f.started_at >= ?"
            " AND NOT EXISTS (SELECT 1 FROM runs ok WHERE ok.task_name=f.task_name"
            "   AND ok.status IN ('completed','skipped_empty')"
            "   AND ok.started_at > f.started_at)",
                (_iso(fail_lo),)):
            out.append({
                "source": "system", "dept": "system", "kind": "system",
                "title": f"Job failed: {r['task_name']}",
                "action": "Check the run log; re-run or fix.",
                "due": None, "dedup_key": f"system:run:{r['id']}",
                "payload": {"task": r["task_name"],
                            "failed_at": r["started_at"]}})
        ist_date = nowdt.strftime("%Y-%m-%d")
        spent = conn.execute(
            "SELECT COALESCE(SUM(total_cost_usd),0) s FROM claude_usage"
            " WHERE ist_date=?", (ist_date,)).fetchone()["s"]
    cap = config.DAILY_HARD_LIMIT_USD
    if cap and spent >= config.COS_SPEND_WARN_FRACTION * cap:
        out.append({
            "source": "system", "dept": "system", "kind": "system",
            "title": f"Spend ${spent:.2f}/{cap:.0f} today — near cap",
            "action": "Throttle Opus-heavy jobs or wait for the daily reset.",
            "due": None, "dedup_key": f"system:spend:{ist_date}",
            "payload": {"spent": round(spent, 2), "cap": cap}})
    return out


def rule_idle_contracts(db_path, now=None) -> List[dict]:
    """Contracts Upwork still calls ACTIVE that nobody has touched in months.

    ONE card, not one per contract: on 2026-08-27 seventeen of his thirty-five
    open contracts had no message, no money and no logged hour for 90 days,
    the oldest quiet since 2023. Seventeen cards would be a flood and each one
    on its own is not worth a decision; the pile is. Ending them is a click on
    Upwork that v3 will never make for him — this rail is read-only — so the
    card exists to hand him the list, and it clears itself when the count
    falls below `config.IDLE_CONTRACTS_CARD_MIN`."""
    try:
        with db.connect(db_path) as conn:
            from modules.upwork.feeds import mcp_contracts
            rows = mcp_contracts.idle(conn, now=_now(now))
    except Exception:                           # noqa: BLE001 — pre-033 DB
        return []
    if len(rows) < config.IDLE_CONTRACTS_CARD_MIN:
        return []
    oldest = rows[0]
    named = ", ".join(f"{r['client_company'] or r['title'] or r['id']}"
                      f" (quiet since {r['quiet_since'] or 'ever'})"
                      for r in rows[:3])
    return [{
        "source": "system", "dept": "ops", "kind": "system",
        "title": f"{len(rows)} Upwork contracts are open with nothing on them",
        "client": None,
        "action": (f"No message, no money and no logged hour on any of them for"
                   f" {config.MCP_CONTRACT_IDLE_DAYS} days. Oldest: {named}."
                   f" Upwork never closes a contract on its own, so these keep"
                   f" counting as committed work — end them on Upwork when you"
                   f" agree they are done."),
        "due": None,
        "dedup_key": "idle_contracts",
        "payload": {"count": len(rows), "days": config.MCP_CONTRACT_IDLE_DAYS,
                    "oldest_quiet_since": oldest["quiet_since"],
                    "contracts": [{"id": r["id"], "who": r["client_company"],
                                   "title": r["title"],
                                   "quiet_since": r["quiet_since"]} for r in rows]}}]


def rule_applicants_waiting(db_path, now=None) -> List[dict]:
    """People who applied to HIS posting and are waiting on him.

    The other direction from every other Upwork card in here. On 2026-08-27 the
    LinkedIn Ghostwriter post had 9 applicants — 4 he had never opened, 6
    shortlisted, 0 offered — and the only place that number lived was a browser
    tab. A shortlist that never becomes an offer is a hire that quietly does
    not happen.

    One card per open posting, because each posting is its own decision."""
    out = []
    try:
        with db.connect(db_path) as conn:
            from modules.upwork.feeds import mcp_hiring
            postings = mcp_hiring.open_postings(conn)
            ranges = {p["id"]: mcp_hiring.bid_range(conn, p["id"]) for p in postings}
    except Exception:                           # noqa: BLE001 — pre-034 DB
        return out
    from modules.finance import money as _money
    for p in postings:
        total = p.get("applicants") or 0
        if total < config.HIRING_CARD_MIN_APPLICANTS:
            continue
        unopened, short = p.get("new_applicants") or 0, p.get("shortlisted") or 0
        bits = [f"{total} applied"]
        if unopened:
            bits.append(f"{unopened} you have not opened")
        if short:
            bits.append(f"{short} shortlisted")
        if p.get("offered"):
            bits.append(f"{p['offered']} offered")
        rng, market = ranges.get(p["id"]), None
        if p.get("rate_low") is not None and p.get("rate_high") is not None:
            market = (f" The market for {p.get('rate_occupation') or 'this work'} runs"
                      f" ${p['rate_low']:g}-{p['rate_high']:g}/hr")
            if rng:
                market += (f", and the bids run ${_money.from_minor(rng['low_minor'])}"
                           f"-{_money.from_minor(rng['high_minor'])}")
            market += "."
        out.append({
            "source": "system", "dept": "ops", "kind": "system",
            "title": f"{total} people applied to \"{(p.get('title') or '')[:44]}\"",
            "client": None,
            "action": (", ".join(bits) + "."
                       + (market or "")
                       + " Nobody has been hired yet — open the shortlist on Upwork."),
            "due": None,
            "dedup_key": f"applicants:{p['id']}",
            "payload": {"posting_id": p["id"], "title": p.get("title"),
                        "applicants": total, "new": unopened, "shortlisted": short,
                        "offered": p.get("offered"), "hired": p.get("hired"),
                        "rate_low": p.get("rate_low"), "rate_high": p.get("rate_high"),
                        "bid_range_minor": rng}})
    return out


# Two rules left the board on 2026-11-01 (the cockpit audit; his call):
#   rule_stale_dossier  — "Unprocessed sources: <client>, run extraction" was a
#                         job the system can do itself. kb_daily now extracts
#                         attributed Looms too (modules/knowledge/daily.py), so
#                         the condition clears on its own instead of asking him.
#   rule_idle_contracts — the pile of quiet Upwork contracts already lives on
#                         Clients › Needs a look; it was the same fact on three
#                         screens. One home.
# Both functions stay importable (the Clients view and the tests use them); they
# just no longer mint cards. Their kinds stay in sweep.RULE_KINDS so the cards
# already on the board are reconciled away, not orphaned.
ALL_RULES = (rule_owed_replies, rule_hotlead_no_loom, rule_won_no_outcome,
             rule_lost_pattern, rule_commitments_due,
             rule_upwork_waiting, rule_stalled_milestone,
             rule_applicants_waiting, rule_system)


def gather(db_path, now=None) -> List[dict]:
    """Run every rule; return the flat candidate list (deterministic order)."""
    out: List[dict] = []
    for rule in ALL_RULES:
        out.extend(rule(db_path, now))
    return out
