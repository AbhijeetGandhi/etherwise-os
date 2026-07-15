"""Source-health: does each number-backing source still deserve to be believed?

Two ways a source silently lies:
  STALE     — it stopped producing. The dead connects rail rendered 'applied 0'
              as if measured; the truth was 'not measured for 3 days'.
  DEGRADED  — it ran but produced thin/bad output. The scanner extracting 2
              tiles/feed looked healthy (run completed) while under-scanning 90%
              of lead-gen for days.

Both are read here from ledger truth — freshness from `runs`, degradation from
`anomalies` bound to the latest run — and turned into one verdict per source.
bin/doctor and the cockpit both consume it, so 'the source is stale' shows up as
doctor-amber AND as 'not current' on the exact KPI that source feeds.

Pure over its inputs: pass a connection, `now`, and a `sources` list and it is
fully deterministic — no clock, no config reads at call time — which is what
makes the freshness/degradation logic unit-testable without waiting real hours.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from core import config

# status precedence, worst first. 'down' (never ran) is worse than 'stale'
# (ran, went cold) is worse than 'degraded' (fresh but thin) is worse than 'ok'.
STATUS_RANK = {"down": 3, "stale": 2, "degraded": 1, "ok": 0}


def _parse(ts: Optional[str]) -> Optional[datetime]:
    """runs timestamps are IST isoformat with an offset (…+05:30). Return an
    aware datetime, or None if unparseable/absent."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        # tolerate a space-separated 'YYYY-MM-DD HH:MM:SS' with no zone by
        # assuming the system TZ — never crash a health read on a format quirk
        try:
            return datetime.fromisoformat(ts.replace(" ", "T")).replace(
                tzinfo=config.TZ)
        except ValueError:
            return None


def source_status(conn, source: dict, now: datetime) -> dict:
    """Health of ONE source. Reads its last successful run (freshness) and any
    unresolved degrade-anomaly on its LATEST run (degradation)."""
    task = source["task_name"]
    last_row = conn.execute(
        "SELECT completed_at FROM runs WHERE task_name=? AND status='completed'"
        " ORDER BY completed_at DESC LIMIT 1", (task,)).fetchone()
    last_success = last_row[0] if last_row else None
    dt = _parse(last_success)
    age_hours = None if dt is None else max(
        0.0, (now - dt).total_seconds() / 3600.0)

    max_age = source["max_age_hours"]
    never = dt is None
    stale = never or (age_hours is not None and age_hours > max_age)

    # degradation: an unresolved degrade-kind anomaly bound to the task's LATEST
    # run. Tying to run_id (not time) makes it self-healing — a healthy run
    # raises no anomaly, so the previous one no longer matches 'the latest run'.
    degraded, degrade_detail = False, None
    kinds = tuple(source.get("degrade_kinds") or ())
    if kinds:
        latest = conn.execute(
            "SELECT MAX(id) FROM runs WHERE task_name=?", (task,)).fetchone()[0]
        if latest is not None:
            marks = ",".join("?" * len(kinds))
            arow = conn.execute(
                f"SELECT detail_json FROM anomalies WHERE run_id=? AND"
                f" resolved_at IS NULL AND kind IN ({marks})"
                " ORDER BY id DESC LIMIT 1", (latest, *kinds)).fetchone()
            if arow:
                degraded = True
                try:
                    degrade_detail = json.loads(arow[0]) if arow[0] else None
                except (TypeError, ValueError):
                    degrade_detail = None

    if never:
        status = "down"
    elif stale:
        status = "stale"
    elif degraded:
        status = "degraded"
    else:
        status = "ok"

    return {
        "key": source["key"], "label": source["label"],
        "critical": bool(source.get("critical")),
        "backs": source.get("backs"), "why": source.get("why"),
        "task_name": task, "max_age_hours": max_age,
        "last_success": last_success,
        "age_hours": None if age_hours is None else round(age_hours, 1),
        "stale": bool(stale), "degraded": bool(degraded),
        "degrade_detail": degrade_detail,
        "status": status, "ok": status == "ok",
    }


def source_health(conn, now: Optional[datetime] = None,
                  sources: Optional[tuple] = None) -> dict:
    """All declared sources, plus a rolled-up verdict for the health chip."""
    now = now or datetime.now(config.TZ)
    sources = config.SOURCES if sources is None else sources
    items = [source_status(conn, s, now) for s in sources]
    worst = "ok"
    for it in items:
        if STATUS_RANK[it["status"]] > STATUS_RANK[worst]:
            worst = it["status"]
    # only a CRITICAL source in trouble should be allowed to turn the whole
    # system amber; a non-critical laggard is informational
    crit_bad = [it for it in items
                if it["critical"] and not it["ok"]]
    return {"sources": items, "worst": worst, "ok": not crit_bad,
            "critical_unhealthy": [it["key"] for it in crit_bad]}


def by_backs(health: dict, backs: str) -> Optional[dict]:
    """The health record for whichever source feeds a given cockpit figure, so a
    KPI can ask 'is my source still trustworthy?' without knowing the registry."""
    for it in health.get("sources", []):
        if it.get("backs") == backs:
            return it
    return None
