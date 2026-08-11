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
from modules.chief_of_staff import nudges

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
    """A pending draft + the thread sat idle >= COS_FOLLOWUP_IDLE_DAYS -> nudge
    'follow up (draft ready)'. Mirrors the cockpit Today follow-up source.

    ONE CARD PER PERSON, not per room (see nudges.followup_key). The card is
    labelled with the registry client when the thread is attributed, so an
    active client never reads as a cold Upwork lead — the Brandi Jordan / Cradle
    Company symptom Abhijeet caught on 2026-08-12."""
    cutoff = (_now(now) - timedelta(days=config.COS_FOLLOWUP_IDLE_DAYS))
    groups: dict = {}
    with db.connect(db_path) as conn:
        for r in conn.execute(
            "SELECT d.thread_id, t.topic, t.room_name, t.latest_message_dt,"
            " t.client_id,"
            " CAST(julianday(?) - julianday(substr(t.latest_message_dt,1,10))"
            "   AS INT) AS idle_days"
            " FROM drafts d JOIN threads t ON t.id = d.thread_id"
            " WHERE d.sent_status='pending'"
            " AND substr(t.latest_message_dt,1,10) <= ?"
            " ORDER BY t.latest_message_dt DESC",
                (_iso(_now(now)), cutoff.strftime("%Y-%m-%d"))):
            key = nudges.followup_key(r["client_id"], r["room_name"],
                                      r["thread_id"])
            g = groups.setdefault(key, {"rows": [], "client_id": r["client_id"]})
            g["rows"].append(dict(r))

    reg = _registry()
    out = []
    for key, g in groups.items():
        rows = g["rows"]                       # newest thread first (ORDER BY)
        head = rows[0]
        # the label: registry name for an attributed thread, else the room's
        # counterparty name. Never the raw room id.
        label = None
        if g["client_id"] and reg:
            label = reg.canonical(g["client_id"])
        label = label or (head["room_name"] or "").split(",")[0].strip() \
            or head["thread_id"]
        topic = head["topic"] or head["room_name"] or head["thread_id"]
        title = f"Follow up: {topic}" if len(rows) == 1 \
            else f"Follow up: {label} — {len(rows)} open threads"
        out.append({
            "source": "sales", "dept": "sales", "kind": "owed_reply",
            "title": title, "client": label,
            "action": "Draft is ready — review + send in Upwork.",
            "due": None, "dedup_key": key,
            # thread_id(s) in the payload so the Today assembler can dedup this
            # nudge against its follow-up card (the "follow-up x2" root cause).
            # evidence_ts = the LATEST message across every thread in the group.
            # write_nudge uses it to decide whether a card the founder already
            # marked done has any REASON to come back: if nothing has been said
            # since they actioned it, it stays gone. Without this the card
            # refired every sweep and 'Done' was meaningless (Rocky Choy
            # accumulated 10 rows, Mustafa 11, before this was caught
            # 2026-08-12). Grouping matters here too: acting on the newer of a
            # person's two rooms now settles both.
            # the OLDEST obligation sets the age; ranking a person by their
            # freshest thread hides real neglect
            "payload": {"idle_days": max(r["idle_days"] for r in rows),
                        "thread_id": head["thread_id"],
                        "thread_ids": [r["thread_id"] for r in rows],
                        "client_id": g["client_id"],
                        "evidence_ts": max(r["latest_message_dt"]
                                           for r in rows)}})
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
    return out


# ── System ────────────────────────────────────────────────────────────────────

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


ALL_RULES = (rule_owed_replies, rule_hotlead_no_loom, rule_won_no_outcome,
             rule_lost_pattern, rule_stale_dossier, rule_commitments_due,
             rule_system)


def gather(db_path, now=None) -> List[dict]:
    """Run every rule; return the flat candidate list (deterministic order)."""
    out: List[dict] = []
    for rule in ALL_RULES:
        out.extend(rule(db_path, now))
    return out
