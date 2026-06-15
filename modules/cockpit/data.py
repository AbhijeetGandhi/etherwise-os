"""Cockpit data layer — v3 SQLite readers, opened READ-ONLY (reads can't
corrupt; design §5/§8). One function per section. Airtable REST fallback
arrives with Clients (Phase 4). Columns are read from the live schema —
nothing invented.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
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
