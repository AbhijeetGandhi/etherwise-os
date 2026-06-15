"""Cockpit data layer — v3 SQLite readers, opened READ-ONLY (reads can't
corrupt; design §5/§8). One function per section. Airtable REST fallback
arrives with Clients (Phase 4). Columns are read from the live schema —
nothing invented.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from core import config, doctor_checks

# Light doctor for the System panel: fast, local, no network, no full-tree
# secret scan (that's bin/doctor's job). Reflects the REAL machine.
_DOCTOR_LIGHT = ("check_python", "check_db", "check_pricing_coverage",
                 "check_guardrails_selftest", "check_calendar")
_SEVERITY = {"PASS": 0, "SKIP": 0, "WARN": 1, "FAIL": 2}


def _ro(db_path: Optional[Path]) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _ist_today() -> str:
    return datetime.now(config.TZ).strftime("%Y-%m-%d")


# Canonical earnings definition — mirrors v2 reconcile_airtable.py (the
# reconciled revenue parity set). amount>0 guards refunds/reversals.
EARNING_TYPES = ("Hourly Earning", "Fixed Earning", "Bonus")


def _prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _doctor_light() -> dict:
    checks = []
    for name in _DOCTOR_LIGHT:
        fn = getattr(doctor_checks, name)
        try:
            checks.extend(fn())
        except Exception as exc:  # noqa: BLE001 — a panel must never 500
            checks.append(doctor_checks.Check("WARN", name, f"check error: {exc!r}"))
    worst = "PASS"
    for c in checks:
        if _SEVERITY.get(c.status, 0) > _SEVERITY.get(worst, 0):
            worst = c.status
    return {"worst": worst,
            "checks": [{"status": c.status, "name": c.name,
                        "detail": c.detail} for c in checks]}


def system(db_path: Optional[Path] = None) -> dict:
    """System health panel: last run per job, spend vs ceilings, shadow
    volume, light doctor."""
    conn = _ro(db_path)
    try:
        # latest run per task BY TIME (started_at) — not by id, which a
        # backfill / out-of-order insert can break.
        jobs = [dict(r) for r in conn.execute(
            "SELECT task_name, status, started_at, completed_at, duration_ms"
            " FROM runs r WHERE started_at = ("
            "  SELECT MAX(started_at) FROM runs WHERE task_name = r.task_name)"
            " GROUP BY task_name ORDER BY started_at DESC")]
        today = _ist_today()
        month = today[:7]
        spend_today = conn.execute(
            "SELECT COALESCE(SUM(total_cost_usd),0) FROM claude_usage"
            " WHERE ist_date = ?", (today,)).fetchone()[0]
        spend_mtd = conn.execute(
            "SELECT COALESCE(SUM(total_cost_usd),0) FROM claude_usage"
            " WHERE substr(ist_date,1,7) = ?", (month,)).fetchone()[0]
        shadow = {r["diff_status"] or "pending": r["n"] for r in conn.execute(
            "SELECT diff_status, COUNT(*) AS n FROM shadow_ledger"
            " GROUP BY diff_status")}
    finally:
        conn.close()
    return {
        "jobs": jobs,
        "spend": {"today_usd": round(spend_today, 4),
                  "mtd_usd": round(spend_mtd, 4),
                  "soft_limit_usd": config.DAILY_SOFT_LIMIT_USD,
                  "hard_limit_usd": config.DAILY_HARD_LIMIT_USD},
        "shadow": {"pending": shadow.get("pending", 0), "by_status": shadow},
        "doctor": _doctor_light(),
    }


ROOM_URL = "https://www.upwork.com/messages/rooms/{tid}/"
_TERMINAL_PROP = ("Won", "Lost", "Expired", "Withdrawn", "Skipped")
_ACTIVE_CONTRACT = ("ACTIVE", "PENDING")
_HOT = config.HOT_LEAD_THRESHOLD


def today(db_path: Optional[Path] = None, today: Optional[str] = None) -> dict:
    """Today hero: actionable queue (follow-ups w/ draft, hot leads,
    proto-nudges) + the metrics strip. Read-only; real data only."""
    day = today or _ist_today()
    conn = _ro(db_path)
    try:
        # items the founder has acted on: done/dismissed always hidden;
        # snoozed hidden until snooze_until <= day (B1b local nudge_state).
        suppressed = {r["item_key"] for r in conn.execute(
            "SELECT item_key FROM nudge_state WHERE state IN"
            " ('done','dismissed') OR (state='snoozed' AND"
            " COALESCE(snooze_until,'') > ?)", (day,))}

        # follow-ups = pending drafts + thread context; owed before followup
        follow_ups = [{
            "thread_id": r["thread_id"],
            "topic": r["topic"] or r["room_name"],
            "tier": r["tier"], "bucket": r["bucket"],
            "draft": r["body"], "word_count": r["word_count"],
            "thread_url": ROOM_URL.format(tid=r["thread_id"]),
        } for r in conn.execute(
            "SELECT d.thread_id, d.body, d.word_count, d.tier,"
            " t.bucket, t.topic, t.room_name FROM drafts d"
            " JOIN threads t ON t.id = d.thread_id"
            " WHERE d.sent_status = 'pending'"
            " ORDER BY CASE t.bucket WHEN 'owed' THEN 0 WHEN 'unmatched-owed'"
            " THEN 1 ELSE 2 END, d.generated_at DESC")
            if f"followup:{r['thread_id']}" not in suppressed]

        hot_leads = [dict(r) for r in conn.execute(
            "SELECT id, title, score, job_url,"
            " (draft_proposal IS NOT NULL) AS has_draft FROM scored_jobs"
            " WHERE score >= ? AND status IN ('Scored','Drafted')"
            " AND clickup_task_id IS NULL"
            " ORDER BY first_scored_at DESC, score DESC LIMIT 10", (_HOT,))
            if f"hotlead:{r['id']}" not in suppressed]

        # string-compare the YYYY-MM-DD prefix — created_dt carries a
        # "+0000" offset SQLite's date() can't parse (would return NULL).
        d0 = date.fromisoformat(day)
        week_lo = (d0 - timedelta(days=6)).isoformat()
        lw_lo = (d0 - timedelta(days=13)).isoformat()
        lw_hi = (d0 - timedelta(days=7)).isoformat()

        def applied_between(lo, hi):
            return conn.execute(
                "SELECT COUNT(*) FROM proposals WHERE"
                " substr(created_dt,1,10) BETWEEN ? AND ?",
                (lo, hi)).fetchone()[0]

        applied_today = applied_between(day, day)
        applied_week = applied_between(week_lo, day)
        applied_last = applied_between(lw_lo, lw_hi)

        prop_marks = ",".join("?" * len(_TERMINAL_PROP))
        active_props = conn.execute(
            f"SELECT COUNT(*) FROM proposals WHERE status NOT IN"
            f" ({prop_marks}) OR status IS NULL", _TERMINAL_PROP).fetchone()[0]
        interviews = conn.execute(
            "SELECT COUNT(*) FROM proposals WHERE status = 'Interview'"
        ).fetchone()[0]
        cmarks = ",".join("?" * len(_ACTIVE_CONTRACT))
        active_contracts = conn.execute(
            f"SELECT COUNT(*) FROM contracts WHERE upwork_status IN"
            f" ({cmarks})", _ACTIVE_CONTRACT).fetchone()[0]
        follow_ups_due = len(follow_ups)
        hot_count = conn.execute(
            "SELECT COUNT(*) FROM scored_jobs WHERE score >= ? AND status IN"
            " ('Scored','Drafted') AND clickup_task_id IS NULL",
            (_HOT,)).fetchone()[0]
    finally:
        conn.close()

    rev = money(db_path, today=day)["revenue"]
    # proto-nudges (v1): a revenue-pacing signal; M4 replaces with real feed
    proto = []
    if rev["pct_to_target"] is not None and rev["pct_to_target"] < 100:
        proto.append({"kind": "pacing",
                      "text": f"Revenue {rev['pct_to_target']}% to "
                              f"${int(rev['target_usd']):,} target ({rev['month']})",
                      "ref": "money"})
    return {
        "follow_ups": follow_ups,
        "hot_leads": hot_leads,
        "proto_nudges": proto,
        "metrics": {
            "applied": {"today": applied_today, "week": applied_week,
                        "last_week": applied_last},
            "active": {"proposals": active_props, "interviews": interviews,
                       "contracts": active_contracts},
            "follow_ups_due": follow_ups_due, "hot_leads": hot_count,
            "revenue": {"mtd_usd": rev["month_usd"],
                        "target_usd": rev["target_usd"],
                        "pct": rev["pct_to_target"]},
        },
    }


def money(db_path: Optional[Path] = None,
          today: Optional[str] = None) -> dict:
    """Money panel from v3 SQLite (Upwork transactions). Revenue uses the
    canonical earnings definition. Cash position is intentionally NOT
    fabricated — no bank/Wise balance source exists in v3 SQLite yet."""
    month = (today or _ist_today())[:7]
    last_month = _prev_month(month)
    earn_marks = ",".join("?" * len(EARNING_TYPES))
    conn = _ro(db_path)
    try:
        def rev_for(ym: str) -> float:
            return conn.execute(
                f"SELECT COALESCE(SUM(amount),0) FROM transactions WHERE"
                f" type IN ({earn_marks}) AND amount > 0"
                f" AND substr(creation_dt,1,7) = ?",
                (*EARNING_TYPES, ym)).fetchone()[0]

        month_usd = rev_for(month)
        last_usd = rev_for(last_month)
        by_month = [{"month": r["ym"], "usd": round(r["usd"], 2)}
                    for r in conn.execute(
                        f"SELECT substr(creation_dt,1,7) AS ym,"
                        f" SUM(amount) AS usd FROM transactions WHERE"
                        f" type IN ({earn_marks}) AND amount > 0"
                        f" GROUP BY ym ORDER BY ym DESC LIMIT 12",
                        EARNING_TYPES)][::-1]
        connects_month = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE"
            " type = 'Connect Purchase' AND substr(creation_dt,1,7) = ?",
            (month,)).fetchone()[0]
        connects_life = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE"
            " type = 'Connect Purchase'").fetchone()[0]
        feed = [dict(r) for r in conn.execute(
            "SELECT creation_dt, type, amount, currency, profile, description"
            " FROM transactions ORDER BY creation_dt DESC LIMIT 15")]
    finally:
        conn.close()
    return {
        "revenue": {
            "month": month, "month_usd": round(month_usd, 2),
            "target_usd": config.MONTHLY_REVENUE_TARGET_USD,
            "pct_to_target": round(100 * month_usd
                                   / config.MONTHLY_REVENUE_TARGET_USD, 1)
            if config.MONTHLY_REVENUE_TARGET_USD else None,
            "last_month": last_month, "last_month_usd": round(last_usd, 2),
            "by_month": by_month,
        },
        "connects": {"this_month_usd": round(connects_month, 2),
                     "lifetime_usd": round(connects_life, 2)},
        "transactions": feed,
        "cash": {"value_usd": None,
                 "note": "No bank/Wise balance source in v3 SQLite yet "
                         "(Upwork ledger only). Define the source — lands "
                         "with the M5 finance mirror."},
    }
