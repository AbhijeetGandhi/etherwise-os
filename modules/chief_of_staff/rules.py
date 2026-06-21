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

from core import config, db

# scored_jobs states that are still actionable (not parked/declined/applied)
_ACTIONABLE_JOB = ("Scored", "Drafted")


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(config.TZ)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# ── Sales (M1) ────────────────────────────────────────────────────────────────

def rule_owed_replies(db_path, now=None) -> List[dict]:
    """A pending draft + the thread sat idle >= COS_FOLLOWUP_IDLE_DAYS -> nudge
    'follow up (draft ready)'. Mirrors the cockpit Today follow-up source."""
    cutoff = (_now(now) - timedelta(days=config.COS_FOLLOWUP_IDLE_DAYS))
    out = []
    with db.connect(db_path) as conn:
        for r in conn.execute(
            "SELECT d.thread_id, t.topic, t.room_name, t.latest_message_dt,"
            " CAST(julianday(?) - julianday(substr(t.latest_message_dt,1,10))"
            "   AS INT) AS idle_days"
            " FROM drafts d JOIN threads t ON t.id = d.thread_id"
            " WHERE d.sent_status='pending'"
            " AND substr(t.latest_message_dt,1,10) <= ?",
                (_iso(_now(now)), cutoff.strftime("%Y-%m-%d"))):
            topic = r["topic"] or r["room_name"] or r["thread_id"]
            out.append({
                "source": "sales", "dept": "sales", "kind": "owed_reply",
                "title": f"Follow up: {topic}",
                "action": "Draft is ready — review + send in Upwork.",
                "due": None, "dedup_key": f"followup:{r['thread_id']}",
                "payload": {"idle_days": r["idle_days"]}})
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
                "source": "sales", "dept": "sales", "kind": "lost_pattern",
                "title": f"{r['n']} losses in {r['category']} — revisit scoring",
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
                "title": f"{r['n']} unprocessed sources for {r['client_id']}",
                "action": f"Run extraction so {r['client_id']}'s dossier is current.",
                "due": None, "dedup_key": f"stale_dossier:{r['client_id']}",
                "payload": {"ingested": r["n"]}})
    return out


def rule_commitments_due(db_path, now=None) -> List[dict]:
    """Our open commitments due within COS_COMMITMENT_DUE_DAYS (or overdue) ->
    escalate. Reads the commitments table (Phase 2 populates it; inert until)."""
    horizon = (_now(now) + timedelta(days=config.COS_COMMITMENT_DUE_DAYS))
    out = []
    with db.connect(db_path) as conn:
        for r in conn.execute(
            "SELECT id, text, due, source_cite FROM commitments"
            " WHERE state='open' AND who='us' AND due IS NOT NULL"
            " AND substr(due,1,10) <= ?", (horizon.strftime("%Y-%m-%d"),)):
            out.append({
                "source": "commitment", "dept": "knowledge",
                "kind": "commitment_due",
                "title": f"Commitment due: {r['text'][:80]}",
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
        for r in conn.execute(
            "SELECT id, task_name, started_at FROM runs WHERE status='failed'"
            " AND started_at >= ?", (_iso(fail_lo),)):
            out.append({
                "source": "system", "dept": "system", "kind": "system",
                "title": f"Job failed: {r['task_name']}",
                "action": "Check the run log; re-run or fix.",
                "due": None, "dedup_key": f"system:run:{r['id']}"})
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
