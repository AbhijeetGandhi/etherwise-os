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

from core import config, doctor_checks, source_health
from core.airtable_client import AirtableClient

_STATUS_ORDER = {"Active": 0, "Paused": 1, "Lead": 2, "Ended": 3, "Past": 4}
_clients_cache = {"at": 0.0, "data": None}   # 60s TTL — Airtable is external

# Light doctor for the System panel: fast, local, no network, no full-tree
# secret scan (that's bin/doctor's job). Reflects the REAL machine.
_DOCTOR_LIGHT = ("check_python", "check_db", "check_pricing_coverage",
                 "check_guardrails_selftest", "check_calendar",
                 "check_source_freshness")
_SEVERITY = {"PASS": 0, "SKIP": 0, "WARN": 1, "FAIL": 2}


def _ro(db_path: Optional[Path]) -> sqlite3.Connection:
    """Read-only connection that is WAL-robust. mode=ro URI opens FAIL when the
    -wal/-shm files are absent (i.e. whenever the last writer checkpoint-truncated
    and no job is mid-run) — which made every cockpit reader 500 between writer
    runs (2026-07-07 incident). A normal open + PRAGMA query_only enforces the
    same no-writes contract at the connection level while letting SQLite manage
    the WAL side files."""
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA query_only=ON")
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
        sources = source_health.source_health(conn)
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
        "sources": sources,
        "doctor": _doctor_light(),
    }


ROOM_URL = "https://www.upwork.com/messages/rooms/{tid}/"
_TERMINAL_PROP = ("Won", "Lost", "Expired", "Withdrawn", "Skipped")
_DECIDED_PROP = ("Won", "Lost", "Expired", "Withdrawn")   # win-rate denominator
_ACTIVE_CONTRACT = ("ACTIVE", "PENDING")
_HOT = config.HOT_LEAD_THRESHOLD
_RECENT_HOT_DAYS = 7    # Today shows actionable-now hot leads; backlog -> Pipeline


import re as _re

_FTS_TERM = _re.compile(r"[A-Za-z0-9]+")


def _fathom_status(conn) -> dict:
    row = conn.execute("SELECT value FROM sync_cursors WHERE name="
                       "'fathom_created_after'").fetchone()
    last = conn.execute(
        "SELECT completed_at FROM runs WHERE task_name='fathom_poll' AND"
        " status='completed' ORDER BY id DESC LIMIT 1").fetchone()
    return {"cursor": row["value"] if row else None,
            "last_poll": last["completed_at"] if last else None}


def nudges(db_path: Optional[Path] = None) -> dict:
    """The Chief-of-Staff Nudge Queue — real M4 nudges, ranked + grouped
    (replaces the proto follow-up/hot-lead suppression). Read-only; the queue's
    done/snooze/dismiss go through actions.cos_nudge -> nudges.set_state. Also
    surfaces the latest draft brief for the cockpit replay."""
    from modules.chief_of_staff import nudges as eng, brief as cos_brief
    q = eng.grouped_queue(db_path or config.DB_PATH)
    latest = cos_brief.latest_brief()
    return {"entries": q["entries"], "total": q["total"],
            "latest_brief": latest}


def knowledge(db_path: Optional[Path] = None, *, client: Optional[str] = None,
              q: Optional[str] = None) -> dict:
    """Knowledge panel — live kb (M3). Three modes (all READ-ONLY, no model):
    - default: dossier index + totals + scope split + proof-points + Fathom tile
    - ?client=<id>: that client's dossier markdown + facts grouped by category
    - ?q=<text>: FTS search over cited facts (optionally client-scoped).
    The 'ask' box is a POST (actions.ask) that runs the librarian out-of-process.
    """
    conn = _ro(db_path)
    try:
        if q:
            return {"mode": "search", "q": q,
                    "results": _kb_search(conn, q, client)}
        if client:
            return _kb_dossier(conn, client)
        return _kb_overview(conn)
    finally:
        conn.close()


def _kb_overview(conn) -> dict:
    dossiers = [dict(r) for r in conn.execute(
        "SELECT client_id, client_name, fact_count, source_count,"
        " last_compiled_at FROM dossier_index ORDER BY fact_count DESC")]
    totals = conn.execute(
        "SELECT (SELECT COUNT(*) FROM facts) facts,"
        " (SELECT COUNT(*) FROM sources) sources,"
        " (SELECT COUNT(*) FROM facts WHERE scope='client') client_facts,"
        " (SELECT COUNT(*) FROM facts WHERE scope='agency') agency_facts,"
        " (SELECT COUNT(*) FROM facts WHERE scope='cross-client') cross_facts"
    ).fetchone()
    return {
        "mode": "overview",
        "dossiers": dossiers,
        "totals": dict(totals),
        "proof_points": {"agency": totals["agency_facts"],
                         "cross": totals["cross_facts"]},
        "fathom": _fathom_status(conn),
    }


def _kb_dossier(conn, client: str) -> dict:
    row = conn.execute(
        "SELECT client_id, client_name, dossier_path, fact_count, source_count,"
        " last_compiled_at FROM dossier_index WHERE client_id=?",
        (client,)).fetchone()
    facts = [dict(r) for r in conn.execute(
        "SELECT category, fact_text, citation, confidence, scope FROM facts"
        " WHERE client_id=? AND scope='client' ORDER BY category, id",
        (client,))]
    sources = [dict(r) for r in conn.execute(
        "SELECT source_type, source_ref, title, occurred_dt FROM sources"
        " WHERE client_id=? ORDER BY occurred_dt", (client,))]
    return {"mode": "dossier", "client": client,
            "meta": dict(row) if row else None,
            "facts": facts, "sources": sources}


def _kb_search(conn, q: str, client: Optional[str]) -> list:
    terms = " OR ".join(dict.fromkeys(_FTS_TERM.findall(q.lower())))
    if not terms:
        return []
    sql = ("SELECT f.client_id, f.category, f.fact_text, f.citation,"
           " f.confidence, f.scope FROM facts_fts JOIN facts f"
           " ON f.id = facts_fts.rowid WHERE facts_fts MATCH ?")
    args: list = [terms]
    if client:
        sql += " AND f.client_id=?"
        args.append(client)
    sql += " ORDER BY rank LIMIT 40"
    return [dict(r) for r in conn.execute(sql, args)]


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


BOARD_WINDOW_DAYS = 7      # a hot lead older than this is dead on Upwork

# every skip reason that means "failed the HOURLY rate floor". The name drifted
# across v2/v3 (low_hourly_rate -> low_hourly -> hourly_below_30) and all three
# still sit in the table, so any rule that reasons about the hourly floor must
# consider all of them or it silently under-counts history. ONE definition,
# shared by pipeline_facts (the chart) and pipeline_rows (the drill) — that
# shared definition is what makes a bar equal to the rows behind it.
_HOURLY_SKIPS = ("hourly_below_30", "low_hourly_rate", "low_hourly")
_HOURLY_SKIP_Q = ",".join("?" * len(_HOURLY_SKIPS))


def pipeline(db_path: Optional[Path] = None,
             today: Optional[str] = None) -> dict:
    """Pipeline panel v2 (2026-07-07 accuracy audit) — TRUTHFUL sources only:
    - funnel per week from scored_jobs (scanned / scored>=8 / carded) — live and
      accurate; 'replied' from the room-sourced proposals (sparse but real).
    - 'applied' is returned as UNAVAILABLE (never a fake 0): proposals are
      room-sourced only and the vendorProposals scope was declined (too costly);
      the connects-history CSV rail is the planned source of applied-truth.
    - board is WINDOWED to actionable recency (BOARD_WINDOW_DAYS); the uncarded
      shadow-era backlog is counted separately, never mixed into 'to triage'.
    - win rate keeps its threads-only upper-bound caveat.
    """
    from collections import Counter
    day = today or _ist_today()
    d0 = date.fromisoformat(day)
    this_mon = d0 - timedelta(days=d0.weekday())
    fresh_cut = (d0 - timedelta(days=BOARD_WINDOW_DAYS)).isoformat()
    conn = _ro(db_path)
    try:
        def week_counts(sql, args=()):
            c = Counter()
            for r in conn.execute(sql, args):
                try:
                    d = date.fromisoformat((r[0] or "")[:10])
                    c[(d - timedelta(days=d.weekday())).isoformat()] += 1
                except ValueError:
                    pass
            return c

        scanned = week_counts("SELECT fetched_at FROM scored_jobs"
                              " WHERE fetched_at LIKE '20%'")
        scored = week_counts("SELECT first_scored_at FROM scored_jobs"
                             " WHERE score >= 8 AND first_scored_at LIKE '20%'")
        carded = week_counts("SELECT first_scored_at FROM scored_jobs"
                             " WHERE clickup_task_id IS NOT NULL"
                             " AND first_scored_at LIKE '20%'")
        replied = week_counts("SELECT created_dt FROM proposals"
                              " WHERE created_dt LIKE '20%'")
        # HIRED — the funnel's real end state (contracts.start_dt is the truth;
        # proposals.status='Won' is thread-sourced and undercounts)
        hired = week_counts("SELECT start_dt FROM contracts"
                            " WHERE start_dt LIKE '20%'")
        funnel_weeks = []
        for i in range(9, -1, -1):
            wk = (this_mon - timedelta(weeks=i)).isoformat()
            funnel_weeks.append({"week": wk, "scanned": scanned.get(wk, 0),
                                 "scored": scored.get(wk, 0),
                                 "carded": carded.get(wk, 0),
                                 "replied": replied.get(wk, 0),
                                 "hired": hired.get(wk, 0),
                                 "current": i == 0})

        def carded_between(lo, hi):
            return conn.execute(
                "SELECT COUNT(*) FROM scored_jobs WHERE clickup_task_id IS NOT"
                " NULL AND substr(first_scored_at,1,10) BETWEEN ? AND ?",
                (lo, hi)).fetchone()[0]
        carded_now = {
            "today": carded_between(day, day),
            "week": carded_between((d0 - timedelta(days=6)).isoformat(), day),
            "last_week": carded_between(
                (d0 - timedelta(days=13)).isoformat(),
                (d0 - timedelta(days=7)).isoformat())}

        # applied-truth: the connects-history rail (daily Chrome scrape — the
        # page has no export and its API is scope-blocked). When the rail has
        # data, counts are TRUE (0 = genuinely no applications); before the
        # first scrape, say loudly that it's unavailable — never fake zeros.
        applied_weeks = Counter()
        try:
            ch = conn.execute("SELECT COUNT(*) n, MIN(event_date) since"
                              " FROM connects_history").fetchone()
        except Exception:                        # pre-013 DB
            ch = {"n": 0, "since": None}
        if ch["n"]:
            def applied_between(lo, hi):
                return conn.execute(
                    "SELECT COUNT(*) FROM connects_history WHERE kind='applied'"
                    " AND event_date BETWEEN ? AND ?", (lo, hi)).fetchone()[0]
            applied = {
                "available": True, "since": ch["since"],
                "today": applied_between(day, day),
                "week": applied_between((d0 - timedelta(days=6)).isoformat(), day),
                "last_week": applied_between(
                    (d0 - timedelta(days=13)).isoformat(),
                    (d0 - timedelta(days=7)).isoformat()),
                "note": f"connects-history rail (daily scrape); history begins"
                        f" {ch['since']} — a 0 here is a true zero"}
            applied_weeks = week_counts(
                "SELECT event_date FROM connects_history WHERE kind='applied'")
        else:
            applied = {"available": False,
                       "reason": "proposals are room-sourced only (client"
                                 " replies); submissions are not captured —"
                                 " vendorProposals scope declined",
                       "rail": "connects-history scrape rail (built — awaiting"
                               " its first run)"}
        # 'applied' is only KNOWN for weeks the connects rail actually covers.
        # A week that ended before the rail's first scrape has NO data — it must
        # render as unknown (em-dash), never as a fabricated 0 (2026-07-13: the
        # funnel showed 0 applied for every historical week, which read as "he
        # never applied" instead of "we weren't measuring yet").
        # A week is MEASURED only if the rail was already running when it BEGAN.
        # Judging on the week's END was wrong: the week of Jun-29 ended Jul-05,
        # after the rail's first row (Jun-30), so it was called 'covered' and
        # reported 0 applied for Jun 29 — six days nobody was counting. Coverage
        # is a property of the whole period, not of its last day (2026-07-14).
        since = ch["since"] if ch["n"] else None
        for w in funnel_weeks:
            covered = bool(since) and w["week"] >= since
            w["applied"] = applied_weeks.get(w["week"], 0) if covered else None
        applied["covered_since"] = since

        replied_total = conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
        replied_month = conn.execute(
            "SELECT COUNT(*) FROM proposals WHERE substr(created_dt,1,7)=?",
            (day[:7],)).fetchone()[0]
        last_reply = conn.execute(
            "SELECT MAX(substr(created_dt,1,10)) FROM proposals").fetchone()[0]
        client_replied = {"total": replied_total, "this_month": replied_month,
                          "last": last_reply}

        # by-status: blank status rows fall back to the raw upwork_status so
        # nothing shows as 'Unknown' when the API gave us a real state.
        by_status = {}
        for r in conn.execute(
                "SELECT COALESCE(NULLIF(status,''), upwork_status, 'Unknown') s,"
                " COUNT(*) n FROM proposals GROUP BY s"):
            by_status[r["s"]] = r["n"]
        dmarks = ",".join("?" * len(_DECIDED_PROP))
        won = conn.execute("SELECT COUNT(*) FROM proposals WHERE status='Won'"
                           ).fetchone()[0]
        decided = conn.execute(
            f"SELECT COUNT(*) FROM proposals WHERE status IN ({dmarks})",
            _DECIDED_PROP).fetchone()[0]
        win_rate = {
            "won": won, "decided": decided,
            "pct": round(100 * won / decided, 1) if decided else None,
            "caveat": "threads-only — submitted-no-reply losses are invisible"
                      " (no Lost rows exist), so this is a steep upper bound;"
                      " true rate needs the connects-history applied count"}

        # the OPEN board, windowed to actionable freshness; the uncarded
        # shadow-era backlog (never pushed while ClickUp was shadowed) is
        # reported separately — it must not read as 'to triage'.
        bands_row = conn.execute(
            "SELECT SUM(score>=16) hot, SUM(score>=12 AND score<16) standard,"
            " SUM(score>=8 AND score<12) low FROM scored_jobs"
            " WHERE status IN ('Scored','Drafted') AND clickup_task_id IS NULL"
            " AND substr(first_scored_at,1,10) >= ?", (fresh_cut,)).fetchone()
        backlog = conn.execute(
            "SELECT COUNT(*) FROM scored_jobs WHERE status IN"
            " ('Scored','Drafted') AND clickup_task_id IS NULL AND score>=8"
            " AND (first_scored_at IS NULL OR substr(first_scored_at,1,10) < ?)",
            (fresh_cut,)).fetchone()[0]
        bands = {"hot": bands_row["hot"] or 0,
                 "standard": bands_row["standard"] or 0,
                 "low": bands_row["low"] or 0,
                 "window_days": BOARD_WINDOW_DAYS,
                 "backlog_uncarded": backlog,
                 "note": f"open board, last {BOARD_WINDOW_DAYS} days —"
                         f" {backlog} older uncarded rows (mostly the shadow-era"
                         " backlog; listings likely expired) excluded"}
        to_triage = bands["hot"]

        # ICP / Prime — the tier that actually discriminates now that ~80% of
        # scored jobs clear the 'hot' bar. This is the KPI that replaced
        # 'to triage': since the live ClickUp cutover every hot lead is carded
        # within the same run, so 'hot AND uncarded' is structurally always 0 —
        # a metric that can no longer be non-zero is a dead metric.
        cut30 = (d0 - timedelta(days=29)).isoformat()
        icp_row = conn.execute(
            "SELECT SUM(icp_tier='icp') icp, SUM(icp_tier='icp_near') near"
            " FROM scored_jobs WHERE substr(fetched_at,1,10) >= ?",
            (cut30,)).fetchone()
        icp_last = conn.execute(
            "SELECT MAX(substr(fetched_at,1,10)) FROM scored_jobs"
            " WHERE icp_tier='icp'").fetchone()[0]
        icp_all = conn.execute(
            "SELECT COUNT(*) FROM scored_jobs WHERE icp_tier='icp'").fetchone()[0]
        icp_kpi = {"last_30": icp_row["icp"] or 0,
                   "near_30": icp_row["near"] or 0,
                   "total": icp_all, "last": icp_last}

        # every zero in the KPI strip must be able to say WHY it is zero — a
        # bare 0 with no context is the dead-end metric this cockpit exists to
        # abolish (2026-07-14: four zeros at once, all for different reasons)
        def days_since(iso):
            if not iso:
                return None
            try:
                return (d0 - date.fromisoformat(iso[:10])).days
            except ValueError:
                return None
        client_replied["days_since"] = days_since(client_replied["last"])
        icp_kpi["days_since"] = days_since(icp_last)

        # source health, stamped onto the figures each source feeds. A stale
        # rail or a degraded scanner must not render as a live-looking count —
        # the KPI carries its source's verdict so the UI can say 'not current'.
        health = source_health.source_health(conn)
        applied["source"] = source_health.by_backs(health, "applied")
        scanner_health = source_health.by_backs(health, "pipeline")
    finally:
        conn.close()
    return {"applied": applied, "funnel_weeks": funnel_weeks,
            "carded": carded_now, "client_replied": client_replied,
            "by_status": by_status, "win_rate": win_rate, "bands": bands,
            "to_triage": to_triage, "icp": icp_kpi,
            "scanner_health": scanner_health}


def pipeline_facts(db_path: Optional[Path] = None,
                   today: Optional[str] = None) -> dict:
    """One compact fact-row per job (plus the dated outcome rails) — the SOURCE
    the Pipeline charts aggregate client-side, exactly as Money aggregates the
    ledger. Charts and drills therefore share one predicate set: a bar can never
    disagree with the rows behind it.

    Job facts (short keys; ~4.8k rows):
      i  id            m  month entered the pipeline (fetched_at)
      d  date fetched  s  score (null = never scored)
      k  hard-rule skip reason (null = passed the floors)
      f  feed source   c  1 if a ClickUp card exists
      t  icp tier ('icp' | 'icp_near' | null)
      nr 1 if the ONLY reason it failed the hourly floor is that the client
         never stated a rate (Upwork sends those as $0, so the floor reads a
         missing rate as a cheap one — an unknown, not a reject). Carried in
         the fact row so the chart never has to ask the server for a count.

    The month key is ALWAYS fetched_at: a job is scored and carded within minutes
    of being fetched, and using one clock for every stage is what lets the funnel
    subtract stage-to-stage without a month-boundary artefact.
    """
    day = today or _ist_today()
    conn = _ro(db_path)
    try:
        jobs = [{"i": r["id"], "d": r["d"], "m": (r["d"] or "")[:7],
                 "s": r["score"], "k": r["hard_rule_skip"],
                 "f": r["feed_source"], "c": 1 if r["carded"] else 0,
                 "t": r["icp_tier"], "nr": 1 if r["no_rate"] else 0}
                for r in conn.execute(
                    "SELECT id, substr(fetched_at,1,10) AS d, score,"
                    " hard_rule_skip, feed_source, icp_tier,"
                    " clickup_task_id IS NOT NULL AS carded,"
                    " (hard_rule_skip IN ('hourly_below_30','low_hourly_rate',"
                    "  'low_hourly') AND hourly_max IS NULL) AS no_rate"
                    " FROM scored_jobs WHERE fetched_at LIKE '20%'")]
        applies = [{"d": r["d"], "m": r["d"][:7]} for r in conn.execute(
            "SELECT event_date AS d FROM connects_history WHERE kind='applied'"
            " AND event_date LIKE '20%'")]
        replies = [{"d": r["d"], "m": r["d"][:7]} for r in conn.execute(
            "SELECT substr(created_dt,1,10) AS d FROM proposals"
            " WHERE created_dt LIKE '20%'")]
        hires = [{"d": r["d"], "m": r["d"][:7]} for r in conn.execute(
            "SELECT substr(start_dt,1,10) AS d FROM contracts"
            " WHERE start_dt LIKE '20%'")]
        try:
            ch = conn.execute("SELECT COUNT(*) n, MIN(event_date) since"
                              " FROM connects_history").fetchone()
            rail_since = ch["since"] if ch["n"] else None
        except Exception:                              # pre-013 DB
            rail_since = None
    finally:
        conn.close()
    return {"jobs": jobs, "applies": applies, "replies": replies,
            "hires": hires, "rail_since": rail_since, "as_of": day,
            "hot_threshold": config.HOT_LEAD_THRESHOLD,
            "score_floor": 8}


def pipeline_rows(db_path: Optional[Path] = None, *,
                  stage: Optional[str] = None,
                  week: Optional[str] = None,
                  status: Optional[str] = None,
                  frm: Optional[str] = None,
                  to: Optional[str] = None,
                  skip: Optional[str] = None,
                  feed: Optional[str] = None,
                  icp: Optional[str] = None,
                  score_min: Optional[str] = None,
                  score_max: Optional[str] = None,
                  carded: Optional[str] = None,
                  today: Optional[str] = None) -> dict:
    """Drill-down: the rows behind any Pipeline figure (traceability contract,
    same as Money).

    stage: jobs|scanned|scored|carded|applied|replied|hired|triage|threads.

    'jobs' is the generic, filterable stage every CHART band drills through —
    its filters mirror the fact-row keys exactly (frm/to month window, skip,
    feed, icp, score_min/score_max, carded), so the rows it returns are by
    construction the same rows the bar counted. The week-based stages remain for
    the funnel-by-week table. Read-only; capped at 300 rows."""
    if stage not in ("jobs", "scanned", "scored", "carded", "applied",
                     "replied", "hired", "triage", "threads"):
        return {"error": f"unknown stage {stage!r}", "rows": [], "count": 0}
    lo = hi = None
    if week:
        d = date.fromisoformat(week)
        lo, hi = week, (d + timedelta(days=6)).isoformat()

    def wk(col):
        """Date filter for a dated stage: an exact funnel WEEK when the
        funnel-by-week table asks, otherwise the Pipeline's month RANGE. The
        outcome cards (applied/replied/hired) are range-scoped, so their drills
        must be too — a card and its rows that disagree is the dead-end metric
        this whole contract exists to prevent."""
        if lo:
            return f" AND substr({col},1,10) BETWEEN ? AND ?", [lo, hi]
        cond, args = "", []
        if frm:
            cond += f" AND substr({col},1,7) >= ?"
            args.append(frm)
        if to:
            cond += f" AND substr({col},1,7) <= ?"
            args.append(to)
        return cond, args

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    conn = _ro(db_path)
    try:
        rows: list = []
        total: Optional[int] = None
        if stage == "jobs":
            # the CHART drill — filters mirror the fact-row keys 1:1
            cond, args = " AND fetched_at LIKE '20%'", []
            if frm:
                cond += " AND substr(fetched_at,1,7) >= ?"
                args.append(frm)
            if to:
                cond += " AND substr(fetched_at,1,7) <= ?"
                args.append(to)
            if skip == "__none__":
                cond += " AND hard_rule_skip IS NULL"
            elif skip == "__any__":
                cond += " AND hard_rule_skip IS NOT NULL"
            elif skip == "no_rate":
                # the split (must match pipeline_facts' `nr` exactly): failed an
                # hourly floor with NO rate on the post — an unknown, not a cheap job
                cond += (f" AND hard_rule_skip IN ({_HOURLY_SKIP_Q})"
                         " AND hourly_max IS NULL")
                args.extend(_HOURLY_SKIPS)
            elif skip in _HOURLY_SKIPS:      # ...vs a rate we actually saw
                cond += " AND hard_rule_skip=? AND hourly_max IS NOT NULL"
                args.append(skip)
            elif skip:
                cond += " AND hard_rule_skip=?"
                args.append(skip)
            if feed:
                cond += " AND feed_source=?"
                args.append(feed)
            if icp:
                cond += " AND icp_tier=?"
                args.append(icp)
            smin, smax = _int(score_min), _int(score_max)
            if smin is not None:
                cond += " AND score >= ?"
                args.append(smin)
            if smax is not None:
                cond += " AND score < ?"
                args.append(smax)
            if carded == "1":
                cond += " AND clickup_task_id IS NOT NULL"
            elif carded == "0":
                cond += " AND clickup_task_id IS NULL"
            rows = [dict(r) for r in conn.execute(
                "SELECT id, title, score, status, job_url, clickup_task_url,"
                " hard_rule_skip, feed_source, icp_tier, contract_type,"
                " hourly_max, fixed_budget, client_total_spent,"
                " substr(fetched_at,1,10) AS d FROM scored_jobs"
                f" WHERE 1=1{cond} ORDER BY fetched_at DESC LIMIT 300", args)]
            # the UNCAPPED count — the drill shows 300 rows but must never imply
            # that 300 is the answer when the figure it came from says otherwise
            total = conn.execute(
                f"SELECT COUNT(*) FROM scored_jobs WHERE 1=1{cond}",
                args).fetchone()[0]
        elif stage in ("scanned", "scored", "carded"):
            col = "fetched_at" if stage == "scanned" else "first_scored_at"
            cond, args = wk(col)
            if stage == "scored":
                cond += " AND score >= 8"
            if stage == "carded":
                cond += " AND clickup_task_id IS NOT NULL"
            q = (f"SELECT id, title, score, status, job_url, clickup_task_url,"
                 f" substr({col},1,10) AS d FROM scored_jobs"
                 f" WHERE {col} LIKE '20%'{cond}"
                 f" ORDER BY {col} DESC LIMIT 300")
            rows = [dict(r) for r in conn.execute(q, args)]
            total = conn.execute(
                f"SELECT COUNT(*) FROM scored_jobs WHERE {col} LIKE '20%'{cond}",
                args).fetchone()[0]
        elif stage == "applied":
            cond, args = wk("event_date")
            rows = [dict(r) for r in conn.execute(
                "SELECT event_date AS d, job_title AS title, job_id,"
                " connects_delta, label FROM connects_history"
                f" WHERE kind='applied'{cond}"
                " ORDER BY event_date DESC LIMIT 300", args)]
            total = conn.execute(
                "SELECT COUNT(*) FROM connects_history"
                f" WHERE kind='applied'{cond}", args).fetchone()[0]
        elif stage == "replied":
            cond, args = wk("created_dt")
            rows = [dict(r) for r in conn.execute(
                "SELECT id, job_title AS title, status, upwork_status,"
                " client_company, substr(created_dt,1,10) AS d FROM proposals"
                f" WHERE created_dt LIKE '20%'{cond}"
                " ORDER BY created_dt DESC LIMIT 300", args)]
            total = conn.execute(
                "SELECT COUNT(*) FROM proposals"
                f" WHERE created_dt LIKE '20%'{cond}", args).fetchone()[0]
        elif stage == "hired":
            cond, args = wk("start_dt")
            rows = [dict(r) for r in conn.execute(
                "SELECT id, COALESCE(title, job_title) AS title, client_company,"
                " upwork_status AS status, hourly_rate, contract_type,"
                " substr(start_dt,1,10) AS d FROM contracts"
                f" WHERE start_dt LIKE '20%'{cond}"
                " ORDER BY start_dt DESC LIMIT 300", args)]
            total = conn.execute(
                "SELECT COUNT(*) FROM contracts"
                f" WHERE start_dt LIKE '20%'{cond}", args).fetchone()[0]
        elif stage == "triage":
            fresh_cut = (date.fromisoformat(today or _ist_today())
                         - timedelta(days=BOARD_WINDOW_DAYS)).isoformat()
            rows = [dict(r) for r in conn.execute(
                "SELECT id, title, score, status, job_url,"
                " substr(first_scored_at,1,10) AS d,"
                " draft_proposal IS NOT NULL AS has_draft FROM scored_jobs"
                " WHERE status IN ('Scored','Drafted') AND clickup_task_id IS"
                " NULL AND score>=16 AND substr(first_scored_at,1,10) >= ?"
                " ORDER BY score DESC, first_scored_at DESC LIMIT 300",
                (fresh_cut,))]
        elif stage == "threads":
            cond, args = ("", [])
            if status:
                cond = (" WHERE COALESCE(NULLIF(status,''), upwork_status,"
                        " 'Unknown') = ?")
                args = [status]
            rows = [dict(r) for r in conn.execute(
                "SELECT id, job_title AS title,"
                " COALESCE(NULLIF(status,''), upwork_status, 'Unknown') AS status,"
                " client_company, substr(created_dt,1,10) AS d,"
                " substr(modified_dt,1,10) AS modified FROM proposals"
                f"{cond} ORDER BY COALESCE(modified_dt, created_dt) DESC"
                " LIMIT 300", args)]
    finally:
        conn.close()
    return {"stage": stage, "week": week, "status": status,
            "rows": rows, "count": len(rows),
            "total": len(rows) if total is None else total}


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

    # money() omits 'revenue' when finance-doctor is RED (gated) — tolerate that
    rev = money(db_path, today=day).get("revenue", {})
    # proto-nudges (v1): a revenue-pacing signal; M4 replaces with real feed
    proto = []
    if rev.get("pct_to_target") is not None and rev["pct_to_target"] < 100:
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
            "revenue": {"mtd_usd": rev.get("month_usd"),
                        "target_usd": rev.get("target_usd"),
                        "pct": rev.get("pct_to_target")},
        },
    }


def money(db_path: Optional[Path] = None,
          today: Optional[str] = None) -> dict:
    """Money panel from the AUDITED M5 finance picture. GATED on finance-doctor:
    nothing is shown unless GREEN (the prime directive — no finance number in the
    cockpit on RED). Cash = account_balances (IDFC savings + IDFC FD + Wise, all
    base-USD). Revenue / P&L / burn / runway from pnl_bucket + amount_base_minor
    (reflects every audit fix + non-Upwork revenue). Reuses modules.finance.pnl so
    the numbers MATCH bin/finance-pnl exactly. Money is integer minor units; the
    USD shown is from_minor (presentation only)."""
    import datetime as _dt
    from collections import defaultdict
    from decimal import Decimal
    from modules.finance import pnl, doctor, money as fmoney

    day = today or _ist_today()
    month, dom, last_month = day[:7], day[8:10], _prev_month(day[:7])
    dbp = db_path or config.DB_PATH

    dr = doctor.run(dbp)
    if not dr.green:                          # hard gate — show nothing on RED
        return {"doctor": "RED", "blocked": True,
                "reds": [c.invariant for c in dr.reds()],
                "note": "Finance blocked — finance-doctor is RED; no figures "
                        "are shown until every invariant is GREEN."}

    conn = _ro(db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT source, type, upwork_type, category, accounting_subtype,"
            " description, pnl_bucket, amount_base_minor, creation_dt FROM"
            " transactions WHERE canonical_key IS NOT NULL")]
        # LATEST closing per account: with overlapping/re-imported statements an
        # account has several balance rows — summing them double-counted cash
        # (2026-07-07: two idfc statements would have shown ~2x savings).
        # Deterministic: newest period_end/statement_date, id as tiebreak.
        bals = [dict(r) for r in conn.execute(
            "SELECT account, currency, closing_minor FROM account_balances b"
            " WHERE b.id = (SELECT b2.id FROM account_balances b2"
            "  WHERE b2.account = b.account"
            "  ORDER BY COALESCE(b2.period_end, b2.statement_date) DESC, b2.id DESC"
            "  LIMIT 1)")]
        fxr = conn.execute(
            "SELECT fx_rate FROM transactions WHERE source='IDFC' AND fx_rate"
            " IS NOT NULL AND fx_rate!='1' ORDER BY fx_date DESC LIMIT 1").fetchone()
        feed = [dict(r) for r in conn.execute(
            "SELECT creation_dt, pnl_bucket, amount_base_minor, currency, source"
            " FROM transactions WHERE canonical_key IS NOT NULL"
            " ORDER BY creation_dt DESC LIMIT 15")]
    finally:
        conn.close()

    inr_usd = Decimal(fxr["fx_rate"]) if fxr else Decimal("0")

    def usd(minor):                           # int minor -> 2dp float for the UI
        return round(float(fmoney.from_minor(int(minor or 0))), 2)

    def mo(r):
        return (r.get("creation_dt") or "")[:7]

    # revenue = the pnl 'revenue' bucket (base-USD), MTD-vs-same-day-last-month
    def rev_minor(pred):
        return sum(r["amount_base_minor"] or 0 for r in rows
                   if r.get("pnl_bucket") == "revenue" and pred(r))
    month_minor = rev_minor(lambda r: mo(r) == month)
    last_minor = rev_minor(lambda r: mo(r) == last_month)
    last_mtd_minor = rev_minor(lambda r: mo(r) == last_month
                               and (r.get("creation_dt") or "")[8:10] <= dom)
    bm = defaultdict(int)
    for r in rows:
        if r.get("pnl_bucket") == "revenue" and mo(r):
            bm[mo(r)] += r["amount_base_minor"] or 0
    by_month = [{"month": k, "usd": usd(v), "current": k == month}
                for k, v in sorted(bm.items())][-13:]     # 13 bars (design brief)
    tgt = config.MONTHLY_REVENUE_TARGET_USD

    # per-bucket totals (the P&L waterfall + drill targets; NULL -> uncategorized)
    bucket_minor = defaultdict(int)
    months_all = set()
    for r in rows:
        bucket_minor[r.get("pnl_bucket") or "uncategorized"] += \
            r["amount_base_minor"] or 0
        if mo(r):
            months_all.add(mo(r))

    # P&L / burn / runway — reuse the audited compute (matches finance-pnl)
    allp = pnl.compute(rows)
    hi = max(((r.get("creation_dt") or "")[:10] for r in rows
              if r.get("creation_dt")), default=day)
    lo = (_dt.date.fromisoformat(hi) - _dt.timedelta(days=365)).isoformat()
    trail = pnl.compute([r for r in rows if (r.get("creation_dt") or "")[:10] >= lo])
    monthly_op = trail["operating_profit_minor"] / 12

    # cash position from account_balances (IDFC INR -> USD at the pinned rate)
    cash_minor, cash_lines = 0, []
    for b in bals:
        u = b["closing_minor"] if b["currency"] == "USD" \
            else fmoney.apply_rate(b["closing_minor"], inr_usd)
        cash_minor += u
        cash_lines.append({"account": b["account"], "currency": b["currency"],
                           "closing": str(fmoney.from_minor(b["closing_minor"])),
                           "usd": usd(u)})

    connects_month = sum(r["amount_base_minor"] or 0 for r in rows
                         if r.get("type") == "Connect Purchase" and mo(r) == month)
    connects_life = sum(r["amount_base_minor"] or 0 for r in rows
                        if r.get("type") == "Connect Purchase")
    runway = round(cash_minor / abs(monthly_op), 1) if monthly_op < 0 else None

    return {
        "doctor": "GREEN", "blocked": False,
        "as_of": hi, "months": sorted(months_all),
        "buckets": {b: usd(v) for b, v in sorted(bucket_minor.items())},
        "revenue": {
            "month": month, "month_usd": usd(month_minor), "target_usd": tgt,
            "pct_to_target": round(100 * usd(month_minor) / tgt, 1) if tgt else None,
            "last_month": last_month, "last_month_usd": usd(last_minor),
            "last_month_mtd_usd": usd(last_mtd_minor), "by_month": by_month,
        },
        "pnl": {
            "net_revenue_usd": usd(allp["net_revenue_minor"]),
            "operating_costs_usd": usd(allp["operating_costs_minor"]),
            "operating_profit_usd": usd(allp["operating_profit_minor"]),
            "owner_draw_usd": usd(allp["owner_draw_minor"]),
            "monthly_operating_profit_usd": usd(int(monthly_op)),
            "runway_months": runway, "uncategorized_count": allp["uncategorized_count"],
        },
        "cash": {"value_usd": usd(cash_minor), "by_account": cash_lines,
                 "as_of": hi, "inr_usd_rate": str(inr_usd),
                 "note": "savings + fixed deposits + Wise, base-USD"},
        "connects": {"this_month_usd": usd(connects_month),
                     "lifetime_usd": usd(connects_life)},
        # corrected feed: pnl_bucket + base-USD (kept keys type/profile/amount for the UI)
        "transactions": [{"creation_dt": r["creation_dt"], "type": r["pnl_bucket"],
                          "amount": usd(r["amount_base_minor"]), "currency": r["currency"],
                          "profile": r["source"]} for r in feed],
    }


def money_transactions(db_path: Optional[Path] = None, *,
                       bucket: Optional[str] = None,
                       month: Optional[str] = None,
                       source: Optional[str] = None,
                       q: Optional[str] = None) -> dict:
    """Drill-down: the EXACT transactions behind a Money figure (traceability
    contract — every number is explainable). Same query set as money():
    canonical_key IS NOT NULL, pnl_bucket, amount_base_minor — so the returned
    rows SUM to the displayed metric to the cent (sum_usd is computed on integer
    minor units, never floats). Doctor-GATED like money(): no rows on RED.

    Filters (all optional, AND-ed): bucket (comma-list — 'revenue,refund' is the
    Net-revenue KPI), month (YYYY-MM), source, q (case-insensitive substring over
    description+type). No filters = the full reconciled ledger (the explorer)."""
    from modules.finance import doctor, money as fmoney

    dbp = db_path or config.DB_PATH
    dr = doctor.run(dbp)
    if not dr.green:                          # hard gate — no rows on RED
        return {"doctor": "RED", "blocked": True,
                "reds": [c.invariant for c in dr.reds()],
                "rows": [], "count": 0, "sum_usd": 0.0}

    conn = _ro(db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT source, type, description, pnl_bucket, amount_base_minor,"
            " creation_dt FROM transactions WHERE canonical_key IS NOT NULL")]
    finally:
        conn.close()

    def usd(minor):
        return round(float(fmoney.from_minor(int(minor or 0))), 2)

    want_buckets = ({b.strip() for b in bucket.split(",") if b.strip()}
                    if bucket else None)
    needle = (q or "").strip().lower()
    out, total_minor = [], 0
    for r in rows:
        b = r.get("pnl_bucket") or "uncategorized"
        m = (r.get("creation_dt") or "")[:7]
        if want_buckets is not None and b not in want_buckets:
            continue
        if month and m != month:
            continue
        if source and (r.get("source") or "") != source:
            continue
        if needle and needle not in ((r.get("description") or "") + " "
                                     + (r.get("type") or "")).lower():
            continue
        minor = r.get("amount_base_minor") or 0
        total_minor += minor
        out.append({"dt": (r.get("creation_dt") or "")[:10], "m": m, "b": b,
                    "u": usd(minor), "s": r.get("source") or "",
                    "t": r.get("type") or "", "d": r.get("description") or ""})
    out.sort(key=lambda x: x["dt"], reverse=True)
    return {"doctor": "GREEN", "blocked": False, "rows": out,
            "count": len(out), "sum_usd": usd(total_minor)}
