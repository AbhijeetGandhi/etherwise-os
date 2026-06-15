"""Cockpit data layer — v3 SQLite readers, opened READ-ONLY (reads can't
corrupt; design §5/§8). One function per section. Airtable REST fallback
arrives with Clients (Phase 4). Columns are read from the live schema —
nothing invented.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from core import config, doctor_checks
from core.airtable_client import AirtableClient

_STATUS_ORDER = {"Active": 0, "Paused": 1, "Lead": 2, "Ended": 3, "Past": 4}
_clients_cache = {"at": 0.0, "data": None}   # 60s TTL — Airtable is external

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
        shadow_by_task = {r["task_name"]: r["n"] for r in conn.execute(
            "SELECT task_name, COUNT(*) AS n FROM shadow_ledger"
            " GROUP BY task_name ORDER BY n DESC")}
    finally:
        conn.close()
    return {
        "jobs": jobs,
        "spend": {"today_usd": round(spend_today, 4),
                  "mtd_usd": round(spend_mtd, 4),
                  "soft_limit_usd": config.DAILY_SOFT_LIMIT_USD,
                  "hard_limit_usd": config.DAILY_HARD_LIMIT_USD},
        "shadow": {"pending": shadow.get("pending", 0), "by_status": shadow,
                   "by_task": shadow_by_task},
        "doctor": _doctor_light(),
    }


ROOM_URL = "https://www.upwork.com/messages/rooms/{tid}/"
_TERMINAL_PROP = ("Won", "Lost", "Expired", "Withdrawn", "Skipped")
_DECIDED_PROP = ("Won", "Lost", "Expired", "Withdrawn")   # win-rate denominator
_ACTIVE_CONTRACT = ("ACTIVE", "PENDING")
_HOT = config.HOT_LEAD_THRESHOLD
_RECENT_HOT_DAYS = 7    # Today shows actionable-now hot leads; backlog -> Pipeline


def knowledge(db_path: Optional[Path] = None) -> dict:
    """Knowledge panel — honest stub until M3 (ingestion). Surfaces the
    Fathom poller's high-water-mark + last run so the IA slot isn't empty."""
    conn = _ro(db_path)
    try:
        row = conn.execute("SELECT value FROM sync_cursors WHERE name="
                           "'fathom_created_after'").fetchone()
        last = conn.execute(
            "SELECT completed_at, status FROM runs WHERE task_name="
            "'fathom_poll' AND status='completed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return {
        "status": "stub",
        "message": "Knowledge ingestion arrives with M3 (Fathom/Loom "
                   "transcripts → classify → extract → cite). The Fathom "
                   "poller is staged; nothing indexed yet.",
        "fathom": {"cursor": row["value"] if row else None,
                   "last_poll": last["completed_at"] if last else None},
    }


def clients(db_path: Optional[Path] = None, *, client=None,
            use_cache: bool = True) -> dict:
    """Clients panel — live from the Airtable Clients table via the stdlib
    REST client (the cloud-data seam; independent of MCP). 60s cache so tab
    switches don't hammer Airtable. Per-client deep health (owed replies,
    hours vs cap) arrives when the comms mirror lands; v1 shows status,
    contracts, activity, tags."""
    now = time.monotonic()
    if use_cache and _clients_cache["data"] is not None \
            and now - _clients_cache["at"] < 60:
        return _clients_cache["data"]

    cl = client or AirtableClient()
    recs = cl.list_records(
        config.AIRTABLE_BASE, config.AT["clients"],
        fields=["Name", "Status", "Folder Name", "First Contract Date",
                "Tags", "Contracts", "Transactions", "Notes"])
    rows = []
    for r in recs:
        f = r.get("fields", {})
        rows.append({
            "name": f.get("Name") or f.get("Folder Name") or "(unnamed)",
            "status": f.get("Status") or "Unknown",
            "first_contract": f.get("First Contract Date"),
            "tags": f.get("Tags") or [],
            "contracts": len(f.get("Contracts") or []),
            "transactions": len(f.get("Transactions") or []),
            "note": (f.get("Notes") or "")[:140],
        })
    rows.sort(key=lambda x: (_STATUS_ORDER.get(x["status"], 9),
                             x["name"].lower()))
    summary = {}
    for x in rows:
        summary[x["status"]] = summary.get(x["status"], 0) + 1
    out = {"clients": rows, "count": len(rows), "by_status": summary,
           "source": "airtable:Clients"}
    _clients_cache["at"], _clients_cache["data"] = now, out
    return out


def pipeline(db_path: Optional[Path] = None,
             today: Optional[str] = None) -> dict:
    """Pipeline panel: applied trend, proposals by status, win rate, score
    bands (from scored_jobs.score — no ClickUp dependency), and the all-time
    untasked-hot 'to triage' backlog."""
    day = today or _ist_today()
    d0 = date.fromisoformat(day)
    conn = _ro(db_path)
    try:
        def applied_between(lo, hi):
            return conn.execute(
                "SELECT COUNT(*) FROM proposals WHERE"
                " substr(created_dt,1,10) BETWEEN ? AND ?",
                (lo, hi)).fetchone()[0]
        applied = {
            "today": applied_between(day, day),
            "week": applied_between((d0 - timedelta(days=6)).isoformat(), day),
            "last_week": applied_between(
                (d0 - timedelta(days=13)).isoformat(),
                (d0 - timedelta(days=7)).isoformat())}
        # Applied-per-week trend: bucket by week-start (Monday) in Python so
        # it's consistent (no SQLite/Python %W mismatch) and ZERO-FILLED out
        # to the CURRENT week — so a recent gap reads as a gap, not a chart
        # that mysteriously ends early (redline 5).
        from collections import Counter
        rows = conn.execute("SELECT created_dt FROM proposals"
                            " WHERE created_dt LIKE '20%'").fetchall()
        counts = Counter()
        for r in rows:
            try:
                d = date.fromisoformat(r["created_dt"][:10])
                counts[(d - timedelta(days=d.weekday())).isoformat()] += 1
            except ValueError:
                pass
        this_mon = d0 - timedelta(days=d0.weekday())
        trend = [{"week": (this_mon - timedelta(weeks=i)).isoformat(),
                  "count": counts.get(
                      (this_mon - timedelta(weeks=i)).isoformat(), 0),
                  "current": i == 0}
                 for i in range(9, -1, -1)]

        by_status = {r["status"] or "Unknown": r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM proposals GROUP BY status")}
        dmarks = ",".join("?" * len(_DECIDED_PROP))
        won = conn.execute("SELECT COUNT(*) FROM proposals WHERE status='Won'"
                           ).fetchone()[0]
        decided = conn.execute(
            f"SELECT COUNT(*) FROM proposals WHERE status IN ({dmarks})",
            _DECIDED_PROP).fetchone()[0]
        win_rate = {
            "won": won, "decided": decided,
            "pct": round(100 * won / decided, 1) if decided else None,
            # Mirror holds only proposals WITH a message room (roomList
            # sourcing) — submitted-but-no-reply losses are absent, so this
            # OVERSTATES win rate until the M1 sourcing fix. Honest caveat.
            "caveat": "threads-only — submitted-no-reply proposals not yet "
                      "captured (M1 sourcing fix pending); win rate is an "
                      "upper bound"}

        # Score bands = the OPEN board (untasked Scored/Drafted) — the actual
        # "what's on the board" snapshot. Counting all-time + tasked rows
        # inverted the ratio with degraded/pre-canonical scoring noise
        # (redline 1). Hot here == to_triage.
        bands_row = conn.execute(
            "SELECT SUM(score>=16) hot, SUM(score>=12 AND score<16) standard,"
            " SUM(score>=8 AND score<12) low FROM scored_jobs"
            " WHERE status IN ('Scored','Drafted') AND clickup_task_id IS NULL"
        ).fetchone()
        bands = {"hot": bands_row["hot"] or 0,
                 "standard": bands_row["standard"] or 0,
                 "low": bands_row["low"] or 0,
                 "note": "open board (untasked). Scores include the "
                         "pre-canonical-matrix backlog — hot is an upper "
                         "bound until a backlog re-score (M1 cleanup)."}
        to_triage = bands["hot"]
    finally:
        conn.close()
    return {"applied": applied, "applied_trend": trend,
            "by_status": by_status, "win_rate": win_rate, "bands": bands,
            "to_triage": to_triage}


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
            # real thread age (days) so the queue reads honestly — the old UI
            # showed word_count as "Nw" which was misread as weeks (redline 2)
            "age_days": r["age_days"],
            "thread_url": ROOM_URL.format(tid=r["thread_id"]),
        } for r in conn.execute(
            "SELECT d.thread_id, d.body, d.word_count, d.tier,"
            " t.bucket, t.topic, t.room_name,"
            " CAST(julianday(?) - julianday(substr(t.latest_message_dt,1,10))"
            "   AS INT) AS age_days"
            " FROM drafts d JOIN threads t ON t.id = d.thread_id"
            " WHERE d.sent_status = 'pending'"
            " ORDER BY CASE t.bucket WHEN 'owed' THEN 0 WHEN 'unmatched-owed'"
            " THEN 1 ELSE 2 END, d.generated_at DESC", (day,))
            if f"followup:{r['thread_id']}" not in suppressed]

        # Today shows RECENT actionable hot leads only; the all-time untasked
        # backlog is surfaced in Pipeline as "to triage" (Abhijeet, Phase 3).
        recent_lo = (date.fromisoformat(day)
                     - timedelta(days=_RECENT_HOT_DAYS)).isoformat()
        hot_leads = [dict(r) for r in conn.execute(
            "SELECT id, title, score, job_url,"
            " (draft_proposal IS NOT NULL) AS has_draft FROM scored_jobs"
            " WHERE score >= ? AND status IN ('Scored','Drafted')"
            " AND clickup_task_id IS NULL"
            " AND substr(first_scored_at,1,10) >= ?"
            " ORDER BY first_scored_at DESC, score DESC LIMIT 10",
            (_HOT, recent_lo))
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
            " ('Scored','Drafted') AND clickup_task_id IS NULL"
            " AND substr(first_scored_at,1,10) >= ?",
            (_HOT, recent_lo)).fetchone()[0]
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
    day = today or _ist_today()
    month = day[:7]
    dom = day[8:10]                       # day-of-month, for MTD-vs-MTD
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

        def rev_through_day(ym: str, dd: str) -> float:
            # earnings in month ym up to day-of-month dd (apples-to-apples MTD)
            return conn.execute(
                f"SELECT COALESCE(SUM(amount),0) FROM transactions WHERE"
                f" type IN ({earn_marks}) AND amount > 0"
                f" AND substr(creation_dt,1,7) = ?"
                f" AND substr(creation_dt,9,2) <= ?",
                (*EARNING_TYPES, ym, dd)).fetchone()[0]

        month_usd = rev_for(month)
        last_usd = rev_for(last_month)
        last_mtd_usd = rev_through_day(last_month, dom)  # same-day-of-month
        by_month = [{"month": r["ym"], "usd": round(r["usd"], 2),
                     "current": r["ym"] == month}
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
            # apples-to-apples: last month through the SAME day-of-month, so
            # MTD-vs-MTD doesn't read like a crash (redline 3)
            "last_month_mtd_usd": round(last_mtd_usd, 2),
            "by_month": by_month,
        },
        "connects": {"this_month_usd": round(connects_month, 2),
                     "lifetime_usd": round(connects_life, 2)},
        "transactions": feed,
        "cash": {"value_usd": None,
                 "note": "Arrives with M5 — the finance module will mirror "
                         "statement running-balances for a real cash "
                         "position. (Revenue + transactions below are live.)"},
    }
