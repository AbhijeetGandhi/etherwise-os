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
from datetime import datetime, timezone
from typing import Optional

from core import config

# status precedence, worst first. 'down' (never ran) is worse than 'stale'
# (ran, went cold) is worse than 'degraded' (fresh but thin) is worse than 'ok'.
STATUS_RANK = {"down": 3, "stale": 2, "degraded": 1, "ok": 0}

# Which run statuses PROVE the rail is alive (2026-08-13).
# A rail whose 'nothing new' outcome is a clean skip (runner.TaskSkip ->
# status 'skipped_empty') still proved liveness: it ran, reached its source and
# correctly found nothing. Counting only 'completed' marked such a rail DEAD at
# its own healthy steady state — loom_fetch is weekly and USUALLY has 0 new
# recordings, so it would have read 'down' forever even after being fixed.
# Opt-in per source (`empty_ok`) because the opposite case exists and matters:
# upwork_scan's TaskSkip means 'no tiles extracted on ANY feed', which is a
# BROKEN scanner and must never be allowed to read as fresh.
SUCCESS_STATUSES = ("completed",)
SUCCESS_STATUSES_EMPTY_OK = ("completed", "skipped_empty")

# escalation thresholds, in hours PAST a source's own deadline. The brief runs
# twice daily, so these land at roughly 'still dead after ~2 briefs' and 'after
# ~6 briefs' — the point of escalating wording is that a persistent dead source
# must not read like a single missed run.
ESCALATE_PERSISTENT_HOURS = 24
ESCALATE_DEAD_HOURS = 72


def _parse(ts: Optional[str]) -> Optional[datetime]:
    """Return an AWARE datetime, or None if unparseable/absent.

    Two conventions live in this schema and mixing them skews every age:
      runs.completed_at  — IST isoformat WITH an offset (…+05:30), already aware
      datetime('now')    — SQLite default on most tables: UTC, naive, space-sep
    So a naive timestamp is UTC by construction. Assuming local time instead
    would have made every manually-fed source read 5.5h younger than it is.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)          # accepts ' ' or 'T' separators
    except ValueError:
        return None                              # never crash a health read
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _escalation(hours_overdue: float) -> int:
    """How loudly to say it. The brief runs twice a day, so a source that is
    still dead 24h/72h past its deadline has already slid past ~2 and ~6 briefs
    — it must not read the same as 'missed one run' or it blends into the daily
    noise. Derived from elapsed time, so there is no counter to drift."""
    if hours_overdue > ESCALATE_DEAD_HOURS:
        return 3        # days dead — say it like an incident
    if hours_overdue > ESCALATE_PERSISTENT_HOURS:
        return 2        # still dead a day later
    return 1            # just went stale


def source_status(conn, source: dict, now: datetime) -> dict:
    """Health of ONE source. Freshness comes from its last successful run, or —
    for manually-fed sources with no runs row (the finance importers) — from a
    declared `freshness_sql` returning the newest datum it produced. Degradation
    comes from an unresolved degrade-anomaly on its LATEST run."""
    task = source["task_name"]
    fsql = source.get("freshness_sql")
    if fsql:
        # data-backed: 'how old is the newest row this source produced?' A
        # manual importer has no run ledger, but its data still has an age.
        row = conn.execute(fsql).fetchone()
        last_success = row[0] if row and row[0] else None
    else:
        ok_statuses = (SUCCESS_STATUSES_EMPTY_OK if source.get("empty_ok")
                       else SUCCESS_STATUSES)
        marks = ",".join("?" * len(ok_statuses))
        last_row = conn.execute(
            f"SELECT completed_at FROM runs WHERE task_name=? AND"
            f" status IN ({marks}) ORDER BY completed_at DESC LIMIT 1",
            (task, *ok_statuses)).fetchone()
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

    hours_overdue = 0.0 if (age_hours is None or not stale) \
        else max(0.0, age_hours - max_age)
    return {
        "key": source["key"], "label": source["label"],
        "critical": bool(source.get("critical")),
        "backs": source.get("backs"), "why": source.get("why"),
        "task_name": task, "max_age_hours": max_age,
        "manual": bool(fsql),          # manually fed (no scheduled run to blame)
        "last_success": last_success,
        "age_hours": None if age_hours is None else round(age_hours, 1),
        "stale": bool(stale), "degraded": bool(degraded),
        "degrade_detail": degrade_detail,
        "hours_overdue": round(hours_overdue, 1),
        "days_overdue": int(hours_overdue // 24),
        # 'down' (never produced anything) is maximally broken by definition —
        # there is no age to escalate from, so it starts at the top.
        "escalation": 3 if status == "down"
                      else (_escalation(hours_overdue) if status == "stale" else 0),
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


def _age_phrase(hours: Optional[float]) -> str:
    if hours is None:
        return "never"
    if hours < 1:
        return "under an hour ago"
    if hours < 48:
        return f"{hours:.0f}h ago"
    return f"{hours / 24:.0f} days ago"


def headline(health: dict) -> str:
    """The compact line that is ALWAYS printed, healthy or not. It is always
    present on purpose: a health line that only appears when something is wrong
    is indistinguishable from a health check that silently stopped running."""
    items = health.get("sources", [])
    bad = [s for s in items if not s["ok"]]
    if not bad:
        return f"all {len(items)} sources fresh"
    # the lead word must match what is actually wrong: a DEGRADED source has no
    # escalation (it ran, it was just thin), so keying off escalation alone
    # mislabelled it as STALE
    if any(s["escalation"] >= 3 for s in bad):
        lead = "DEAD"
    elif any(s["status"] in ("stale", "down") for s in bad):
        lead = "STALE"
    else:
        lead = "DEGRADED"
    return f"{lead} — {len(bad)} of {len(items)} sources need attention"


def _cadence(hours: float) -> str:
    """'every 3h' / 'every 8 days' — a deadline a human reads without arithmetic."""
    return f"every {hours:.0f}h" if hours < 48 else f"every {hours / 24:.0f} days"


def source_line(s: dict) -> str:
    """One unhealthy source, worded by how long it has been broken. The brief
    runs twice a day, so 'missed one run' and 'dead since Tuesday' must not read
    the same or the second one disappears into the daily noise."""
    label = s["label"]
    why = (s.get("why") or "").strip()
    tail = f" It feeds {why[0].lower() + why[1:]}." if why else ""
    if s["status"] == "degraded":
        return f"DEGRADED: {label} ran but produced thin output.{tail}"
    if s["status"] == "down":
        return f"DEAD: {label} has NEVER produced data.{tail}"
    last = (s.get("last_success") or "")[:10]
    days = s.get("days_overdue") or 0
    if s["escalation"] >= 3:
        fix = (" It is fed manually — an import is overdue."
               if s.get("manual") else
               " Nothing is feeding it; this needs a look today.")
        return (f"DEAD {days}d: {label} has produced nothing since {last}"
                f" (expected {_cadence(s['max_age_hours'])}).{tail}{fix}")
    if s["escalation"] == 2:
        return (f"STILL STALE ({days}d overdue): {label} last produced"
                f" {_age_phrase(s.get('age_hours'))} — nothing since {last}.")
    return (f"STALE: {label} last produced {_age_phrase(s.get('age_hours'))}"
            f" (expected {_cadence(s['max_age_hours'])}).")


def report(health: dict) -> dict:
    """Everything a renderer needs: the always-on headline, the loud lines, and
    whether anything is wrong. One wording source so the brief's text and HTML
    bodies can never drift apart."""
    bad = [s for s in health.get("sources", []) if not s["ok"]]
    bad.sort(key=lambda s: (-STATUS_RANK[s["status"]], -s["escalation"]))
    return {"headline": headline(health), "ok": not bad,
            "lines": [source_line(s) for s in bad],
            "worst_escalation": max((s["escalation"] for s in bad), default=0),
            "count": len(health.get("sources", [])), "unhealthy": len(bad)}


def worst_for(health: dict, backs: str) -> Optional[dict]:
    """The WORST-off source feeding a figure. Money is fed by several (the
    Upwork sync, the weekly finance pull, the manual statement imports), and a
    tab is only as trustworthy as its sickest input — so a KPI stamp must take
    the worst, not the first."""
    hits = [it for it in health.get("sources", []) if it.get("backs") == backs]
    if not hits:
        return None
    return max(hits, key=lambda it: (STATUS_RANK[it["status"]],
                                     it.get("hours_overdue") or 0))
