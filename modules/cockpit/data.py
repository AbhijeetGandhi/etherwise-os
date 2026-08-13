"""Cockpit data layer — v3 SQLite readers, opened READ-ONLY (reads can't
corrupt; design §5/§8). One function per section. Airtable REST fallback
arrives with Clients (Phase 4). Columns are read from the live schema —
nothing invented.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from core import config, doctor_checks, registry, source_health
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


# ── card identity + provenance law (J1 D4) ───────────────────────────────────
# A card's identity is (client, intent), NOT the surface that produced it. The
# follow-up CARD (from drafts+threads) and the owed_reply NUDGE (from the M4
# store) are the same intent for the same thread; they must collapse to ONE card
# rolled forward with an honest age — never render twice (the "follow-up ×2").
_CLIENT_FACING_KINDS = {"owed_reply", "commitment_due", "won_no_outcome",
                        "hotlead_no_loom"}


def _card_key(item_key: str, client=None) -> str:
    """Canonical render identity. Follow-up threads key on the thread alone so a
    follow-up card and its owed_reply nudge collapse; every other card keys on
    its item_key (already unique per intent), so the closure overlay matches
    regardless of how the client label was derived."""
    ik = item_key or ""
    if ik.startswith("followup:"):
        return f"card:thread:{ik.split(':', 1)[1]}"
    if ik.startswith("nudge:followup:"):
        return f"card:thread:{ik.split(':', 2)[2]}"
    return f"card:{ik}"


def _is_actioned(key: str, suppressed: set, actioned_at: dict,
                 evidence_ts=None, created_at=None) -> bool:
    """Has the founder dealt with this card, and has nothing happened since?

    The cockpit's Done button writes ONLY to nudge_state, and until 2026-08-12
    nudge_state was consulted for the follow-up list but NOT for the nudge
    queue. So marking a nudge done wrote a row nothing ever read: the card came
    straight back, which is exactly what Abhijeet reported ("I marked it done
    yesterday and the day before but it still shows up"). Every card the queue
    renders now passes through here, under both key forms the UI can send.

    A Done is not permanent — it means "handled" — and it goes stale two ways.
    The two ways are NOT interchangeable, and which one applies depends on
    whether the card's kind can supply evidence at all. Both halves exist
    because the two failure modes are exact mirrors of each other; deleting
    either one to "simplify" reintroduces a bug we have already shipped once.

      evidence_ts  THE AUTHORITY WHENEVER IT IS PRESENT. A kind that can say
                   "here is when the counterparty last spoke" (owed_reply, via
                   modules.chief_of_staff.evidence) has told us everything there
                   is to know: newer than the Done -> live again; not newer ->
                   nothing has happened, stay gone. `created_at` MUST NOT get a
                   vote here. The sweep re-mints a follow-up row on a schedule,
                   not on news, so a re-mint is not an event — and letting it
                   override the Done meant the founder's "done" survived for
                   exactly as long as the gap to the next sweep. Measured
                   2026-08-12: followup:p:julie-heffernan (done 08-11 08:41Z,
                   evidence 08-10 19:36Z, i.e. NOTHING new) came back purely
                   because the sweep minted a fresh row at 08-11 22:41Z.

      created_at   THE FALLBACK, and only for kinds that supply no evidence at
                   all. Their dedup_keys are deliberately stable per entity
                   ('stale_dossier:horizons-aba', 'escalate_client:<id>'), so
                   with nothing else to go on one Done in August would hide that
                   client's dossier backlog in January while the morning brief,
                   which reads the nudges table, nagged about it daily. For
                   those kinds a fresh row IS the only available news.

    Edge case, stated rather than hidden: if a kind that normally carries
    evidence hands us None (an old payload written before evidence_ts existed),
    it falls through to the created_at branch and the card comes BACK. That is
    the safe direction — showing work that may be handled beats hiding work that
    is not."""
    hit = next((k for k in (key, f"nudge:{key}") if k in suppressed), None)
    if hit is None:
        return False
    from modules.chief_of_staff import nudges as eng
    was = actioned_at.get(hit)
    if not was:
        return True                      # snoozed: an explicit "not until <date>"
    if evidence_ts:
        # evidence present -> it decides, alone. No created_at override.
        return not eng.ts_key(evidence_ts) > eng.ts_key(was)
    if created_at and eng.ts_key(created_at, naive="ist") > eng.ts_key(was):
        return False                     # re-minted after the Done -> it's back
    return True


def _client_label(client_id) -> Optional[str]:
    """Registry display name for an attributed thread. Read-only and forgiving:
    a registry that fails to load must cost a nicer label, never the card."""
    if not client_id:
        return None
    try:
        from core import registry
        return registry.load().canonical(client_id)
    except Exception:
        return None


def _card_provenance(entry: dict):
    d = entry.get("detail") or {}
    return (entry.get("thread_url") or d.get("thread_url") or d.get("source")
            or d.get("job_url") or d.get("cite") or d.get("permalink"))


def _card_client_facing(entry: dict) -> bool:
    return (bool(entry.get("client")) or entry.get("kind") == "followup"
            or entry.get("nudge_kind") in _CLIENT_FACING_KINDS)


def _flag_no_provenance(db_path, count: int, keys: list) -> None:
    """The provenance law is loud, not silent: a client-facing card with no
    evidence link is hidden AND raises a once-a-day warn anomaly."""
    try:
        from core import db as _db
        with _db.connect(db_path or config.DB_PATH) as conn:
            dup = conn.execute(
                "SELECT 1 FROM anomalies WHERE kind='card_no_provenance'"
                " AND resolved_at IS NULL AND substr(detected_at,1,10)=date('now')"
                " LIMIT 1").fetchone()
            if not dup:
                conn.execute(
                    "INSERT INTO anomalies (task_name, kind, detail_json, severity)"
                    " VALUES ('cockpit_today','card_no_provenance',?, 'warn')",
                    (json.dumps({"hidden": count, "keys": keys[:20]}),))
    except Exception:  # noqa: BLE001 — never let flagging break the page
        pass


def _flag_applied_anomaly(db_path, kind: str, detail: dict) -> None:
    """Applied-tile reconciliation is loud, not silent: a config/feed value the
    connects rail (the applied-truth source) contradicts raises a once-a-day WARN
    anomaly of `kind` so the stale source surfaces as an incident, not just
    quietly on a tile. Used for 'stale_intent' (paused config vs rail) and
    'applied_feed_lag' (proposals feed vs rail)."""
    try:
        from core import db as _db
        with _db.connect(db_path or config.DB_PATH) as conn:
            dup = conn.execute(
                "SELECT 1 FROM anomalies WHERE kind=? AND resolved_at IS NULL"
                " AND substr(detected_at,1,10)=date('now') LIMIT 1",
                (kind,)).fetchone()
            if not dup:
                conn.execute(
                    "INSERT INTO anomalies (task_name, kind, detail_json, severity)"
                    " VALUES ('cockpit_today', ?, ?, 'warn')",
                    (kind, json.dumps(detail)))
    except Exception:  # noqa: BLE001 — never let flagging break the page
        pass


def _rail_applied_between(conn, lo: str, hi: str) -> int:
    """Applied-truth from the connects-history rail over [lo, hi] inclusive.

    The SINGLE source of the rail's applied count. today() (the act-now applied
    tile) and pipeline() (the KPI strip + the client-replies context line) both
    read it, so a stated assumption can never override observed applications on
    one surface while another tells the truth — the same no-silent-override law
    behind the applied-tile reconciliation. 0 on a pre-013 DB (no rail table)."""
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM connects_history WHERE kind='applied'"
            " AND event_date BETWEEN ? AND ?", (lo, hi)).fetchone()[0]
    except Exception:  # noqa: BLE001 — pre-013 DB has no connects_history
        return 0


def _rail_applied_windows(conn, day: str) -> dict:
    """today / this-week / last-week applied counts from the connects rail —
    the ONE shared applied-truth source both the Today tile and the Pipeline tab
    render from. Two tabs, one helper, identical windows: they can never show
    different applied numbers for the same day again (that divergence — Today's
    proposals-sourced 0 vs Pipeline's rail-sourced 8 — was the whole bug family).
    Windows match pipeline()'s: week = last 7 days incl. today; last_week = the 7
    days before that."""
    d0 = date.fromisoformat(day)
    return {
        "today": _rail_applied_between(conn, day, day),
        "week": _rail_applied_between(
            conn, (d0 - timedelta(days=6)).isoformat(), day),
        "last_week": _rail_applied_between(
            conn, (d0 - timedelta(days=13)).isoformat(),
            (d0 - timedelta(days=7)).isoformat()),
    }


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
        # spend-by-task today, so the spend figure is explainable (drills to it)
        spend_by_task = [dict(r) for r in conn.execute(
            "SELECT task_name, ROUND(SUM(total_cost_usd),4) usd, COUNT(*) calls"
            " FROM claude_usage WHERE ist_date=? GROUP BY task_name"
            " ORDER BY usd DESC", (today,))]
        # the operational INCIDENT feed — the anomalies table has been invisible
        # in the cockpit until now. Recent first; a failing task collapses to one
        # counted row so a crashloop doesn't bury everything else.
        #
        # 'open' is computed HONESTLY, not read off resolved_at (nothing sets it):
        # an anomaly is RECOVERED once its task completed successfully AFTER the
        # anomaly's last occurrence — same self-heal principle as source-health.
        # Otherwise a long-healed crashloop would read as dozens of live fires.
        # detected_at is UTC; runs.completed_at is IST-offset — compare as epoch
        # via julianday so the two clocks reconcile.
        anomalies = []
        for r in conn.execute(
                "SELECT task_name, kind, severity, MAX(detected_at) last,"
                " COUNT(*) n,"
                " SUM(CASE WHEN resolved_at IS NULL THEN 1 ELSE 0 END) n_open"
                " FROM anomalies"
                " WHERE detected_at >= datetime('now','-14 days')"
                " GROUP BY task_name, kind, severity"
                " ORDER BY last DESC LIMIT 40"):
            a = dict(r)
            self_healed = conn.execute(
                "SELECT 1 FROM runs WHERE task_name=? AND status='completed'"
                " AND julianday(completed_at) > julianday(?) LIMIT 1",
                (a["task_name"], a["last"])).fetchone() is not None
            # closed two ways, both honest: the task SELF-HEALED (a completed run
            # postdates the last fire) OR every occurrence was EXPLICITLY resolved
            # (root cause fixed by hand). Either way it stops counting as open —
            # a resolved incident must never inflate the open count (2026-08-08:
            # 26 catch_up_failed rows resolved once jarvis_sweep was loaded).
            a["recovered"] = self_healed or (a["n_open"] or 0) == 0
            a["open"] = 0 if a["recovered"] else (a["n_open"] or 0)
            anomalies.append(a)
        # already newest-first from SQL; stable-sort unrecovered ahead of recovered
        anomalies.sort(key=lambda a: a["recovered"])
        anom_open = sum(a["open"] for a in anomalies)
        sources = source_health.source_health(conn)
    finally:
        conn.close()
    return {
        "jobs": jobs,
        "spend": {"today_usd": round(spend_today, 4),
                  "mtd_usd": round(spend_mtd, 4),
                  "soft_limit_usd": config.DAILY_SOFT_LIMIT_USD,
                  "hard_limit_usd": config.DAILY_HARD_LIMIT_USD,
                  "by_task": spend_by_task},
        "shadow": {"pending": shadow.get("pending", 0), "by_status": shadow,
                   "by_task": shadow_by_task},
        "anomalies": {"recent": anomalies, "open": anom_open, "window_days": 14},
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
    """Fathom ingest state. The poll runs INSIDE the daily kb_daily task (there
    is no separate 'fathom_poll' task — reading that name reported 'never' while
    it was in fact polling every morning). Surface the last kb_daily completion
    and how many transcripts that run saved."""
    row = conn.execute("SELECT value FROM sync_cursors WHERE name="
                       "'fathom_created_after'").fetchone()
    last = conn.execute(
        "SELECT completed_at, metrics_json FROM runs WHERE task_name='kb_daily'"
        " AND status='completed' ORDER BY completed_at DESC LIMIT 1").fetchone()
    saved = None
    if last and last["metrics_json"]:
        try:
            saved = (json.loads(last["metrics_json"]).get("fathom") or {}).get("saved")
        except (TypeError, ValueError):
            saved = None
    return {"cursor": row["value"] if row else None,
            "last_poll": last["completed_at"] if last else None,
            "last_saved": saved}


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
        return _kb_overview(conn, db_path=db_path)
    finally:
        conn.close()


_KB_STALE_DAYS = 30       # a dossier not recompiled in this long is stale


def _kb_overview(conn, db_path: Optional[Path] = None) -> dict:
    today = _ist_today()
    dossiers = [dict(r) for r in conn.execute(
        "SELECT client_id, client_name, fact_count, source_count,"
        " last_compiled_at FROM dossier_index ORDER BY fact_count DESC")]

    # revenue cross-link: the dossier names now reconcile with the Clients
    # economics view, so each dossier can carry what the client is worth. Best
    # effort — if finance-doctor is RED the revenue simply isn't shown (never a
    # wrong number). Keyed accent-insensitively, same as the Clients merge.
    rev_by_client = {}
    try:
        cl = clients(db_path)
        if not cl.get("blocked"):
            rev_by_client = {_deaccent(c["name"]): c["revenue_usd"]
                             for c in cl["clients"]}
    except Exception:  # noqa: BLE001 — a cross-link must never break the panel
        rev_by_client = {}

    def _stale_days(iso):
        try:
            return (date.fromisoformat(today)
                    - date.fromisoformat((iso or "")[:10])).days
        except ValueError:
            return None
    for d in dossiers:
        d["revenue_usd"] = rev_by_client.get(_deaccent(d["client_name"] or ""))
        d["stale_days"] = _stale_days(d["last_compiled_at"])
        d["stale"] = bool(d["stale_days"] is not None
                          and d["stale_days"] > _KB_STALE_DAYS)
        # a zero that explains itself: no facts is 'not compiled', not 'known empty'
        d["empty_reason"] = ("never compiled" if not d["last_compiled_at"]
                             else None) if not d["fact_count"] else None

    totals = conn.execute(
        "SELECT (SELECT COUNT(*) FROM facts) facts,"
        " (SELECT COUNT(*) FROM sources) sources,"
        " (SELECT COUNT(*) FROM facts WHERE scope='client') client_facts,"
        " (SELECT COUNT(*) FROM facts WHERE scope='agency') agency_facts,"
        " (SELECT COUNT(*) FROM facts WHERE scope='cross-client') cross_facts,"
        " (SELECT COUNT(*) FROM facts WHERE scope='client'"
        "   AND client_id IS NULL) client_facts_unattributed,"
        " (SELECT COUNT(*) FROM sources WHERE client_id IS NULL)"
        "   sources_unattributed"
    ).fetchone()
    compiled = [d["last_compiled_at"] for d in dossiers if d["last_compiled_at"]]
    oldest = min(compiled) if compiled else None
    freshness = {
        "dossiers": len(dossiers),
        "stale": sum(1 for d in dossiers if d["stale"]),
        "oldest_compile": oldest,
        "oldest_days": _stale_days(oldest) if oldest else None,
        "kb_daily_last": _last_success(conn, "kb_daily"),
        "stale_after_days": _KB_STALE_DAYS,
    }
    return {
        "mode": "overview",
        "dossiers": dossiers,
        "totals": dict(totals),
        "accounting": _kb_accounting(dossiers, totals),
        "freshness": freshness,
        "proof_points": {"agency": totals["agency_facts"],
                         "cross": totals["cross_facts"]},
        "fathom": _fathom_status(conn),
    }


def _kb_accounting(dossiers: list, totals) -> dict:
    """Split every fact and every source into named, drillable buckets that ADD
    UP to the Knowledge cards.

    The Facts and Sources cards used to state a vague "N belong to no dossier"
    and the drills admitted they could not sum. Two different things were piled
    into that one number and only one of them was a defect:

      in_dossiers   claimed by a compiled dossier (dossier_index).
      unattributed  scope='client', but the SOURCE was never attributed to a
                    client, so the fact has no client_id. There is no dossier
                    for it to be missing from — it is the mapping queue's
                    backlog, and it is the bulk of what looked like a gap.
      proof_points  agency + cross-client scope. Excluded from every dossier BY
                    DESIGN (proof_points.py compiles the wiki instead).

    `unaccounted` is the residual after those three. It should be 0. A non-zero
    value is a REAL defect and says so: either a client_id on facts that has no
    dossier_index row, or an index row whose fact_count has drifted from the
    facts behind it (which is what a frozen `skipped_empty` compile used to
    leave behind). It is surfaced, never absorbed into a bucket."""
    in_facts = sum(d["fact_count"] or 0 for d in dossiers)
    in_sources = sum(d["source_count"] or 0 for d in dossiers)
    pp = (totals["agency_facts"] or 0) + (totals["cross_facts"] or 0)
    f_un = totals["client_facts_unattributed"] or 0
    s_un = totals["sources_unattributed"] or 0
    return {
        "facts": {
            "total": totals["facts"], "in_dossiers": in_facts,
            "unattributed": f_un, "proof_points": pp,
            "unaccounted": (totals["facts"] or 0) - in_facts - f_un - pp},
        "sources": {
            "total": totals["sources"], "in_dossiers": in_sources,
            "unattributed": s_un,
            "unaccounted": (totals["sources"] or 0) - in_sources - s_un},
    }


def _last_success(conn, task_name: str) -> Optional[str]:
    row = conn.execute(
        "SELECT completed_at FROM runs WHERE task_name=? AND status='completed'"
        " ORDER BY completed_at DESC LIMIT 1", (task_name,)).fetchone()
    return row["completed_at"] if row else None


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


_CO_SUFFIX = _re.compile(
    r"[\s.,]+(ltd|llc|inc|b\.?v|pte\.?\s*ltd|pty\.?\s*ltd|gmbh|corp|co|"
    r"limited|midco|tbk|international)\.?$", _re.I)
_WISE_PAYER = _re.compile(r"received money from (.+?)(?:\s+with reference\b|$)",
                          _re.I)


def _from_minor(minor) -> float:
    """int minor units -> 2dp float for the UI (money stays integer in the DB)."""
    from modules.finance import money as _fm
    return round(float(_fm.from_minor(int(minor or 0))), 2)


def _deaccent(s: str) -> str:
    """Accent- AND whitespace-insensitive key: the ledger spells some names with
    accents (Upwork/Wise 'Adélye') while the dossier and the merge map do not
    ('Adelye'), and Upwork writes 'Tanushant  Sharma' / 'Ippon  Technology' with
    TWO spaces where everything else holds one. Comparing on the folded form
    makes them reconcile without listing every spelling.

    The internal-whitespace collapse was added 2026-08-13, the same day
    registry.norm() got it, for the same reason and after the same measurement:
    this is the cockpit's own identity key (the client-economics merge, the
    CLIENT_CANONICAL lookup, the room-name contract/owed linkage, and the
    Knowledge dossier<->revenue cross-link) and it was the LAST place a
    double-spaced Upwork name could still fail to meet its registry entry.
    Measured over the live ledger before shipping (var/agent-scratch/s4):
    the Clients view is byte-identical — same 81 rows, same $292,497.73 total,
    same unlinked, same 20/28 contract and 2/5 owed linkages — and exactly two
    answers change, both None -> a real number: Ippon Technology $378.58 and
    Tanushant Sharma $859.49 now show revenue on their dossier row. No
    CLIENT_CANONICAL key contains a whitespace run, so a collapsed key can only
    meet a ledger string that was previously unreachable."""
    import unicodedata
    return _re.sub(r"\s+", " ",
                   "".join(c for c in unicodedata.normalize("NFKD", s or "")
                           if not unicodedata.combining(c))).strip().lower()


def _canon_client(raw: Optional[str]) -> Optional[str]:
    """Canonical display name for a client. Strips a company suffix, looks up the
    confident-merge map (accent-insensitively), else title-cases the cleaned
    name. Returns None for the placeholder labels the ledger uses for 'no
    company'."""
    s = (raw or "").strip()
    # '(inner)' is NOT excluded here anymore — it maps to a real client
    # (inner.garden) via CLIENT_CANONICAL. 'no company'/'s' stay unmappable.
    if not s or s.lower() in ("no company", "s"):
        return None
    core = _CO_SUFFIX.sub("", s).strip()
    for key in (_deaccent(core), _deaccent(s)):
        if key in config.CLIENT_CANONICAL:
            return config.CLIENT_CANONICAL[key]
    return core or None


def _wise_payer(description: Optional[str]) -> Optional[str]:
    m = _WISE_PAYER.search(description or "")
    return m.group(1).strip() if m else None


_BANK_SOURCES = ("IDFC", "Bank")
_BANK_PAYER = _re.compile(r"^(?:IMPS|NEFT|RTGS|IMPS-OPM)/[^/]+/([^/]+)/", _re.I)


def _bank_payer(description: Optional[str]) -> Optional[str]:
    """The remitter name in an IMPS/NEFT/RTGS credit ('IMPS/<ref>/<PAYER>/...').
    Used ONLY to attribute a bank credit to a KNOWN client alias — raw payer
    strings ('VISAPAYMENTSLIM', 'APIBankingEB') are processor noise, so the
    caller must require a CLIENT_CANONICAL hit, never the raw fallback."""
    m = _BANK_PAYER.match((description or "").strip())
    return m.group(1).strip() if m else None


def _job_title(description: Optional[str]) -> Optional[str]:
    """The job title an Upwork earning names before its ' · Earnings/Contract'
    suffix. The agency weekly CSV puts ONLY this in the description — no
    assignment_company — so it is the bridge to the contract that names the
    client."""
    d = (description or "").strip()
    return d.split(" · ")[0].strip() if " · " in d else None


_INVOICE_FOR_RE = _re.compile(r"^Invoice for (.+?):", _re.I)


def _upwork_label(description: Optional[str]) -> Optional[str]:
    """A human label for an Upwork earning whose client Upwork masked ('no
    company'/'S'). Abhijeet's call (2026-07-30): mark these by the JOB TITLE
    itself so the money is at least named by what it was for, never 'unlinked'.
    Handles 'Invoice for <title>: Milestone', '<title> · Earnings', and
    '(id) <title> - hrs'."""
    d = (description or "").strip()
    m = _INVOICE_FOR_RE.match(d)
    if m:
        return m.group(1).strip()
    if " · " in d:
        return d.split(" · ")[0].strip()
    m = _re.match(r"^\(\d+\)\s*(.+?)\s*-\s", d)
    return m.group(1).strip() if m else None


def _client_key(row: dict, title_map: Optional[dict] = None,
                id_map: Optional[dict] = None) -> Optional[str]:
    """The canonical client a revenue row belongs to, or None if unattributable.
    Paths, in order of directness:
      1. Wise rows carry the payer in the description.
      2. Upwork API rows carry assignment_company (Upwork's own label).
      3. a job title in the description ('Title · Earnings') -> the contract.
      4. a contract id in the description ('(43495766) NETFLY - hrs') -> the
         contract. Upwork hourly lines name the client this way, not by company.
      5. a bank IMPS remitter that maps to a KNOWN alias.
    Without 3/4, real earnings (Ioannis CSV, NETFLY hourly) sit in unlinked."""
    if (row.get("source") or "") == "Wise":
        k = _canon_client(_wise_payer(row.get("description")))
        if k:
            return k
    else:
        k = _canon_client(row.get("assignment_company"))
        if k:
            return k
    desc = row.get("description") or ""
    if title_map:
        jt = _job_title(desc)
        if jt and jt in title_map:
            return _canon_client(title_map[jt])
    if id_map:
        m = _CONTRACT_ID_RE.match(desc.strip())
        if m and m.group(1) in id_map:
            return _canon_client(id_map[m.group(1)])
    # bank credit (IMPS/NEFT) whose remitter maps to a KNOWN client alias.
    # Requires an explicit CLIENT_CANONICAL hit — never the raw payer, so
    # processor names never become junk clients.
    if (row.get("source") or "") in _BANK_SOURCES:
        payer = _bank_payer(row.get("description"))
        if payer and _deaccent(payer) in config.CLIENT_CANONICAL:
            return config.CLIENT_CANONICAL[_deaccent(payer)]
    # LAST resort for Upwork earnings Upwork masked the client on: label by the
    # job title itself so it is never nameless (Abhijeet, 2026-07-30).
    if (row.get("source") or "").startswith("Upwork"):
        lbl = _upwork_label(desc)
        if lbl:
            return _canon_client(lbl) or lbl
    return None


_CONTRACT_ID_RE = _re.compile(r"^\((\d+)\)")


def _contract_maps(conn) -> tuple:
    """(title_map, id_map) -> client identity. Upwork earnings name the client
    two ways: by job title ('Title · Earnings for ...') and by contract id
    ('(43495766) NETFLY - 8:00 hrs @ ...'). Both bridge to a synced contract
    that carries the client_company."""
    title_map, id_map = {}, {}
    for r in conn.execute(
            "SELECT id, title, client_company FROM contracts"
            " WHERE COALESCE(client_company,'') != ''"):
        if r["title"]:
            title_map[r["title"]] = r["client_company"]
        id_map[str(r["id"])] = r["client_company"]
    return title_map, id_map


def _is_passthrough(name: str) -> bool:
    low = (name or "").lower()
    return any(p in low for p in config.CLIENT_PASSTHROUGH_PAYERS)


def clients(db_path: Optional[Path] = None, *, sort: Optional[str] = None,
            _today: Optional[str] = None) -> dict:
    """Client-economics view from the LOCAL ledger (finance + contracts + threads
    are both live now). Revenue-to-date per client is the spine — ground truth
    from Upwork's assignment_company and the Wise payer, MERGED where they refer
    to one client (Adélye pays via both). Doctor-gated like Money: no finance
    number shows on RED. Every client's revenue drills to the exact transactions;
    a parity line proves the client totals + unlinked == the P&L revenue, to the
    cent. sort: 'revenue' (default) | 'recent'.

    `_today` is a TEST clock slot, not a request param — underscored so the
    server cannot forward `?today=` into it. It feeds the `as_of` provenance
    stamp, and a caller-supplied one printed a date the server never derived."""
    from modules.finance import doctor as _fdoc
    dbp = db_path or config.DB_PATH
    day = _today or _ist_today()
    dr = _fdoc.run(dbp)
    if not dr.green:
        return {"doctor": "RED", "blocked": True,
                "reason": "finance-doctor RED — client economics hidden until"
                          " the ledger reconciles (same gate as Money)"}
    conn = _ro(dbp)
    try:
        rev_rows = [dict(r) for r in conn.execute(
            "SELECT source, description, assignment_company, amount_base_minor,"
            " substr(creation_dt,1,10) AS d FROM transactions"
            " WHERE pnl_bucket='revenue' AND canonical_key IS NOT NULL")]
        # active contracts + owed replies, linked by a DETERMINISTIC token match
        # to a revenue client (never a fuzzy guess) — unmatched surfaced, not faked
        contracts = [dict(r) for r in conn.execute(
            "SELECT c.upwork_status, t.room_name FROM contracts c"
            " LEFT JOIN threads t ON t.id=c.thread_id"
            " WHERE c.upwork_status IN ('ACTIVE','PENDING')")]
        owed = [dict(r) for r in conn.execute(
            "SELECT room_name FROM threads WHERE bucket IN ('owed','unmatched-owed')")]
        title_map, id_map = _contract_maps(conn)
    finally:
        conn.close()

    agg: dict = {}
    total_minor = 0
    unlinked_minor, unlinked_n = 0, 0
    for r in rev_rows:
        total_minor += r["amount_base_minor"] or 0
        k = _client_key(r, title_map, id_map)
        if not k:
            unlinked_minor += r["amount_base_minor"] or 0
            unlinked_n += 1
            continue
        a = agg.setdefault(k, {"name": k, "revenue_minor": 0, "payments": 0,
                               "first": None, "last": None, "channels": set(),
                               "passthrough": _is_passthrough(k)})
        a["revenue_minor"] += r["amount_base_minor"] or 0
        a["payments"] += 1
        a["channels"].add("Upwork" if (r["source"] or "").startswith("Upwork")
                          else r["source"] or "?")
        d = r["d"]
        if d:
            a["first"] = min(a["first"], d) if a["first"] else d
            a["last"] = max(a["last"], d) if a["last"] else d

    # deterministic linkage: a client's core token appearing as a comma-part of
    # the room name. Unmatched contracts/threads are counted, never misassigned.
    def _match(room_name: str):
        parts = [_deaccent(p) for p in (room_name or "").split(",")]
        for name in agg:
            if _deaccent(name) in parts:
                return name
        return None

    linked_contracts = 0
    for c in contracts:
        k = _match(c.get("room_name"))
        if k:
            agg[k]["active_contracts"] = agg[k].get("active_contracts", 0) + 1
            linked_contracts += 1
    linked_owed = 0
    for o in owed:
        k = _match(o.get("room_name"))
        if k:
            agg[k]["owed_replies"] = agg[k].get("owed_replies", 0) + 1
            linked_owed += 1

    rows = []
    for a in agg.values():
        rows.append({
            "name": a["name"], "revenue_usd": _from_minor(a["revenue_minor"]),
            "payments": a["payments"], "first": a["first"], "last": a["last"],
            "channels": sorted(a["channels"]),
            "active_contracts": a.get("active_contracts", 0),
            "owed_replies": a.get("owed_replies", 0),
            "passthrough": a["passthrough"]})
    if sort == "recent":
        rows.sort(key=lambda x: (x["last"] or "", x["revenue_usd"]), reverse=True)
    else:
        rows.sort(key=lambda x: x["revenue_usd"], reverse=True)

    return {
        "doctor": "GREEN", "blocked": False, "as_of": dr.as_of if hasattr(
            dr, "as_of") else day, "sort": sort or "revenue",
        "clients": rows, "count": len(rows),
        "revenue_total_usd": _from_minor(total_minor),
        "attributed_usd": _from_minor(total_minor - unlinked_minor),
        "unlinked": {"usd": _from_minor(unlinked_minor), "payments": unlinked_n,
                     "note": "bank/other revenue with no parseable payer —"
                             " drills to its transactions"},
        "linkage": {"contracts_linked": linked_contracts,
                    "contracts_total": len(contracts),
                    "owed_linked": linked_owed, "owed_total": len(owed),
                    "note": "contracts/owed-replies linked by exact room-name"
                            " token; unmatched are counted, never misassigned"},
        "source": "ledger:transactions+contracts+threads"}


def client_transactions(db_path: Optional[Path] = None, *,
                        client: Optional[str] = None) -> dict:
    """The exact revenue transactions behind a client's total (traceability —
    the rows sum to the figure, to the cent). client='__unlinked__' returns the
    bank/other revenue with no parseable payer. Doctor-gated like Money."""
    from modules.finance import doctor as _fdoc
    dbp = db_path or config.DB_PATH
    if not _fdoc.run(dbp).green:
        return {"blocked": True, "rows": [], "count": 0, "sum_usd": 0.0}
    conn = _ro(dbp)
    try:
        rev = [dict(r) for r in conn.execute(
            "SELECT source, description, assignment_company, amount_base_minor,"
            " substr(creation_dt,1,10) AS dt FROM transactions"
            " WHERE pnl_bucket='revenue' AND canonical_key IS NOT NULL"
            " ORDER BY creation_dt DESC")]
        title_map, id_map = _contract_maps(conn)
    finally:
        conn.close()
    want, rows, total = client, [], 0
    for r in rev:
        k = _client_key(r, title_map, id_map)
        hit = (k is None and want == "__unlinked__") or (k is not None and k == want)
        if not hit:
            continue
        total += r["amount_base_minor"] or 0
        rows.append({"dt": r["dt"], "source": r["source"],
                     "payer": _wise_payer(r["description"])
                              or r["assignment_company"] or "—",
                     "desc": (r["description"] or "")[:80],
                     "usd": _from_minor(r["amount_base_minor"])})
    return {"client": client, "rows": rows[:300], "count": len(rows),
            "total": len(rows), "sum_usd": _from_minor(total)}


def clients_airtable(db_path: Optional[Path] = None, *, _client=None,
                     _use_cache: bool = True) -> dict:
    """The prior Airtable-backed roster (status/tags/notes). Kept as a secondary
    read; the primary Clients view is now the local economics one above.

    `_client` / `_use_cache` are TEST INJECTION SLOTS, not request inputs, and
    the leading underscore is load-bearing: server.py forwards every
    KEYWORD-ONLY param straight off the query string, so while this read
    declared a bare `client=` a plain `GET /api/clients/airtable?client=Adelye`
    passed the string "Adelye" in where an AirtableClient was expected and the
    route answered 500. server.py now also refuses to forward any param whose
    name starts with "_", so the two rules hold each other up. Never give an
    injection slot a name a URL could plausibly carry."""
    now = time.monotonic()
    if _use_cache and _clients_cache["data"] is not None \
            and now - _clients_cache["at"] < 60:
        return _clients_cache["data"]

    cl = _client or AirtableClient()
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
            applied = {
                "available": True, "since": ch["since"],
                # shared helper — the exact windows the Today tile renders, so
                # the two tabs can never disagree on the applied count.
                **_rail_applied_windows(conn, day),
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
        # The client-replies context line derives from OBSERVED applications (the
        # connects rail), NEVER from a pause assumption. If applications DID go
        # out in the window, a zero-reply month is a LAG ("give it time"), never
        # "nothing applied, so nothing to reply to" — that stale-assumption copy
        # was flatly contradicted by the rail (2026-08-08: 8 applied in 7d while
        # the tile still said nothing applied). Same helper the applied tile
        # reads, so the two surfaces can never disagree.
        rail_week = _rail_applied_between(
            conn, (d0 - timedelta(days=6)).isoformat(), day)
        if replied_month:
            client_replied["note"] = None
        elif rail_week > 0:
            client_replied["note"] = (
                f"{rail_week} applied in the last 7 days — replies typically lag"
                f" {config.REPLY_LAG_DAYS}+ days")
        else:
            client_replied["note"] = "no applications recorded in the last 7 days"

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


# ── drill-down filter validation ─────────────────────────────────────────────
# A reader validates its OWN user input, in its own error envelope: a 200
# carrying {"error": ...}, which pipeline.js checks before it touches res.rows
# and raises as a red toast. Established by activity()'s `day` and by
# pipeline_rows()' `stage`; `week` was the one that got missed, and `?week=lol`
# reached date.fromisoformat() and came back as a 500 stack-trace repr.
#
# WHICH filters get a validator: the ones where a malformed value produces a
# WRONG answer rather than an honest one. Dates and integers do — `frm=lol`
# string-compares its way to zero rows, and `score_min=lol` was silently dropped
# and quietly widened the drill past the bar it was launched from. Both read as
# data (UI_BUILD_BRIEF §0: a rejected filter must never render as a zero or an
# empty list). The open-vocabulary equality filters (status/skip/feed/icp) do
# NOT get one: they are SQL-bound against vocabularies that grow with the data,
# "no rows where feed_source='lol'" is the true answer, and a whitelist there
# would go stale and start refusing real values.
_STAGES = ("jobs", "scanned", "scored", "carded", "applied", "replied",
           "hired", "triage", "threads")


def _bad_filter(param: str, value, expected: str) -> dict:
    """The drill's rejection envelope — visibly an error, never a bare empty
    list. `total` is None (= not measured), not 0: nothing was counted."""
    return {"error": "bad {} {!r} (expected {})".format(param, value, expected),
            "rows": [], "count": 0, "total": None}


def _is_iso_day(v) -> bool:
    """Strict YYYY-MM-DD. Length-checked as well as parsed because 3.11's
    date.fromisoformat also accepts compact forms ('20260608') that the
    substr(col,1,10) comparisons in here would never match."""
    try:
        return len(v) == 10 and bool(date.fromisoformat(v))
    except (TypeError, ValueError):
        return False


def _is_iso_month(v) -> bool:
    """Strict YYYY-MM — what the substr(col,1,7) comparisons are against."""
    try:
        return len(v) == 7 and bool(date.fromisoformat(v + "-01"))
    except (TypeError, ValueError):
        return False


def _opt_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


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
                  _today: Optional[str] = None) -> dict:
    """Drill-down: the rows behind any Pipeline figure (traceability contract,
    same as Money).

    stage: jobs|scanned|scored|carded|applied|replied|hired|triage|threads.

    'jobs' is the generic, filterable stage every CHART band drills through —
    its filters mirror the fact-row keys exactly (frm/to month window, skip,
    feed, icp, score_min/score_max, carded), so the rows it returns are by
    construction the same rows the bar counted. The week-based stages remain for
    the funnel-by-week table. Read-only; capped at 300 rows.

    Every filter above is real user input and is validated up front — including
    one it does not read for this stage, because "silently ignored" is how a
    typo becomes a wrong answer. `_today` is the exception: a TEST clock slot,
    underscored so the server cannot forward `?today=` into it."""
    if stage not in _STAGES:
        return _bad_filter("stage", stage, "one of " + "|".join(_STAGES))
    # ── validate the user-supplied filters (see the note above) ─────────────
    # `week` IS user input — the funnel-by-week table sends it — so it is
    # validated rather than hidden, unlike the `_today` clock slot.
    if week and not _is_iso_day(week):
        return _bad_filter("week", week,
                           "YYYY-MM-DD, the Monday of a funnel week")
    for _p, _v in (("frm", frm), ("to", to)):
        if _v and not _is_iso_month(_v):
            return _bad_filter(_p, _v, "YYYY-MM")
    for _p, _v in (("score_min", score_min), ("score_max", score_max)):
        if _v not in (None, "") and _opt_int(_v) is None:
            return _bad_filter(_p, _v, "an integer")
    if carded not in (None, "", "0", "1"):
        return _bad_filter("carded", carded, "1 or 0")

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
            smin, smax = _opt_int(score_min), _opt_int(score_max)
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
            fresh_cut = (date.fromisoformat(_today or _ist_today())
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


def _applications_paused(day: str):
    """(paused_bool, since_or_None). Founder INTENT from config — the system
    respects a deliberate pause instead of reading it as neglect."""
    since = getattr(config, "APPLICATIONS_PAUSED_SINCE", None)
    return (bool(since and since <= day), since)


def _pause_contradiction(conn, day: str, paused_since: Optional[str]) -> int:
    """Intent reconciliation: a STATED pause must never silently override the
    OBSERVED rail. If config says applications are paused since `paused_since`
    yet the connects rail recorded >= STALE_INTENT_MIN_EVENTS 'applied' events
    AFTER it (within STALE_INTENT_WINDOW_DAYS), the intent is stale — return that
    count so the caller surfaces a contradiction instead of asserting 'paused'.
    Same law as no-fabricated-zeros / render-time-only counts. 0 = no conflict."""
    if not paused_since:
        return 0
    cutoff = (date.fromisoformat(day)
              - timedelta(days=config.STALE_INTENT_WINDOW_DAYS)).isoformat()
    n = conn.execute(
        "SELECT COUNT(*) FROM connects_history WHERE kind='applied'"
        " AND event_date > ? AND event_date >= ?",
        (paused_since, cutoff)).fetchone()[0]
    return n if n >= config.STALE_INTENT_MIN_EVENTS else 0


def today(db_path: Optional[Path] = None, today: Optional[str] = None) -> dict:
    """The daily action screen: ONE ruthlessly ranked act-now queue, a paused-
    aware 'ready when you resume' lead backlog, the source-health line (so a
    quiet queue reads as genuinely quiet, not a dead feed), and a metrics strip
    where every number drills and every zero explains itself. Read-only.

    Design bar (2026-07-27): answer 'what do I act on right now?' in one view;
    respect intent (applications paused since ~Jun 30) rather than nagging with
    an accumulating guilt pile of hot leads."""
    from modules.chief_of_staff import evidence as _evidence
    from modules.chief_of_staff import nudges as _eng
    day = today or _ist_today()
    paused, paused_since = _applications_paused(day)
    pause_conflict_n = 0
    rail_applied_week = 0
    conn = _ro(db_path)
    try:
        # intent reconciliation (no-silent-override law): a config pause the
        # connects rail contradicts is NOT rendered as fact. Computed BEFORE the
        # paused-aware backlog so both the tile and the backlog reflect observed
        # reality rather than a possibly-stale founder-intent constant.
        pause_conflict_n = _pause_contradiction(conn, day, paused_since)
        if pause_conflict_n:
            paused = False
        # items the founder has acted on: done/dismissed always hidden;
        # snoozed hidden until snooze_until <= day (B1b local nudge_state).
        #
        # actioned_at keeps WHEN, because a Done is not permanent: it means "I
        # have dealt with this", and it is undone by the other side saying
        # something new. Snoozes carry no timestamp here — a snooze is an
        # explicit "not until <date>" and evidence must not cut it short.
        #
        # ONE DECISION, TWO STORES — READ BOTH. The Done button reaches the
        # engine and the overlay by different routes: a FOLLOW-UP card posts
        # /api/nudge (nudge_state + write-through to nudges), a NUDGE card posts
        # /api/cos-nudge (nudges only). This function used to read nudge_state
        # alone, so a Done pressed while a counterparty rendered as a nudge card
        # was invisible the moment the same counterparty rendered as a follow-up
        # card — and it came back carrying whatever ancient nudge_state row it
        # still had. Live on 2026-08-12: followup:c:the-cradle-company was done
        # in the engine at 08-11 23:20Z while nudge_state still said 07-29
        # 22:54Z, so the card returned and Abhijeet's Done looked ignored. The
        # LATER of the two timestamps is the founder's real decision; the
        # earlier one is just the store that route did not touch.
        suppressed, actioned_at = set(), {}

        def _record(key: str, state: str, when: str) -> None:
            suppressed.add(key)
            if state not in ("done", "dismissed"):
                return               # snoozed: explicit "not until <date>"
            prev = actioned_at.get(key)
            if not prev or _eng.ts_key(when) > _eng.ts_key(prev):
                actioned_at[key] = when

        for r in conn.execute(
            "SELECT item_key, state, updated_at FROM nudge_state WHERE state IN"
            " ('done','dismissed') OR (state='snoozed' AND"
            " COALESCE(snooze_until,'') > ?)", (day,)):
            # nudge_state.updated_at is SQLite datetime('now') -> UTC
            _record(r["item_key"], r["state"], str(r["updated_at"] or ""))
        for r in conn.execute(
            "SELECT dedup_key, state, updated_at, snooze_until FROM nudges"
            " WHERE state IN ('done','dismissed') OR (state='snoozed' AND"
            " COALESCE(snooze_until,'') > ?)", (day,)):
            # nudges.updated_at is IST-naive (nudges._iso) — normalised to the
            # same UTC shape before it is compared with anything (ts_key law)
            _record(r["dedup_key"] or "", r["state"],
                    _eng.ts_key(r["updated_at"], naive="ist"))

        # follow-ups = pending drafts + thread context; owed before followup.
        # ONE ROW PER COUNTERPARTY (nudges.followup_key): Upwork opens a room per
        # job, so Rocky Choy and Dhanya Sangolli each held two rows that had to be
        # dismissed separately — which is what "I marked it done and it's still
        # there" actually was. The extra threads ride along in thread_ids so the
        # card can name them; nothing is dropped, only the duplicate row is.
        #
        # EVIDENCE IS CROSS-PLATFORM (2026-08-12). This list used to be built
        # entirely from t.latest_message_dt — the newest row of ANY kind on the
        # UPWORK thread. Two consequences, both live on the board that morning:
        # The Cradle Company (signed contract, two weeks of Slack) read as a
        # cold lead ignoring us for 9 days because its newest Upwork message was
        # 08-03; and Julie Heffernan came back the day after a real call because
        # Upwork wrote a *system* row. So the pass below is two-phase: collect
        # the rows first, then ask modules.chief_of_staff.evidence — once per
        # counterparty — when THEY last spoke on any channel, and whether they
        # are a contracted client at all. See that module for what each channel
        # can and cannot prove.
        _fu_rows: list = []
        for r in conn.execute(
            "SELECT d.thread_id, d.body, d.word_count, d.tier,"
            " t.bucket, t.topic, t.room_name, t.client_id, t.latest_message_dt"
            " FROM drafts d JOIN threads t ON t.id = d.thread_id"
            " WHERE d.sent_status = 'pending'"
            " ORDER BY CASE t.bucket WHEN 'owed' THEN 0 WHEN 'unmatched-owed'"
            " THEN 1 ELSE 2 END, d.generated_at DESC"):
            row = dict(r)
            row["key"] = _eng.followup_key(r["client_id"], r["room_name"],
                                           r["thread_id"])
            _fu_rows.append(row)
        _fu_groups: dict = {}
        for row in _fu_rows:
            g = _fu_groups.setdefault(row["key"], {"threads": [],
                                                   "client_id": None})
            g["threads"].append(row["thread_id"])
            g["client_id"] = g["client_id"] or row["client_id"]
        _fu_evidence: dict = {}
        for k, g in _fu_groups.items():
            ev = _evidence.counterparty(conn, thread_ids=g["threads"],
                                        client_id=g["client_id"])
            eng_state = _evidence.engagement(conn, thread_ids=g["threads"],
                                             client_id=g["client_id"])
            # aged against `day`, not the wall clock: today(today=...) must stay
            # reproducible for fixtures and for a replayed date. `waiting_since`
            # (oldest obligation, cross-channel aware), never `ts` (newest) —
            # they answer different questions; see evidence.counterparty.
            _fu_evidence[k] = {
                "ev": ev, "engagement": eng_state,
                "age": _evidence.age_days(ev["waiting_since"], day)}

        follow_ups = []
        _fu_index: dict = {}
        for r in _fu_rows:
            key = r["key"]
            _meta = _fu_evidence[key]
            # the Done check now asks the CROSS-CHANNEL question: did they say
            # anything, anywhere, after the founder closed this?
            if _is_actioned(key, suppressed, actioned_at, _meta["ev"]["ts"]):
                continue
            r["age_days"] = _meta["age"]
            if key in _fu_index:                       # same person, second room
                prev = follow_ups[_fu_index[key]]
                prev["thread_ids"].append(r["thread_id"])
                # the OLDEST obligation sets the age. (Age is now derived per
                # COUNTERPARTY, not per thread, so the two are already equal —
                # the max stays as the invariant, None-safe because an
                # unmeasured age must render '—', never 0.)
                prev["age_days"] = max([a for a in (prev["age_days"],
                                                    r["age_days"])
                                        if a is not None], default=None)
                # every draft rides along. Collapsing the row must not make the
                # second room's pre-written reply unreachable — Dhanya's two
                # rooms are two different job posts with two different drafts.
                prev["drafts"].append({"thread_id": r["thread_id"],
                                       "topic": r["topic"] or r["room_name"],
                                       "body": r["body"], "tier": r["tier"],
                                       "thread_url": ROOM_URL.format(
                                           tid=r["thread_id"])})
                continue
            _fu_index[key] = len(follow_ups)
            follow_ups.append({
                "thread_id": r["thread_id"], "thread_ids": [r["thread_id"]],
                "item_key": key,
                "topic": r["topic"] or r["room_name"],
                # who the thread is with: the REGISTRY client when the thread is
                # attributed, else the room's counterparty. An active client
                # rendered under a bare Upwork name reads as a cold lead — the
                # Brandi Jordan / Cradle Company confusion.
                "client": _client_label(r["client_id"]) or r["room_name"],
                "client_id": r["client_id"],
                "tier": r["tier"], "bucket": r["bucket"],
                "draft": r["body"], "word_count": r["word_count"],
                # age = days since the COUNTERPARTY last spoke on ANY channel.
                # Not since the newest Upwork row: that counted our own replies
                # and Upwork's system rows, and told Abhijeet the Cradle Company
                # had been silent for 9 days when the answer was 4, on Slack.
                "age_days": r["age_days"],
                "evidence": _meta["ev"],
                "engagement": _meta["engagement"],
                "thread_url": ROOM_URL.format(tid=r["thread_id"]),
                "drafts": [{"thread_id": r["thread_id"],
                            "topic": r["topic"] or r["room_name"],
                            "body": r["body"], "tier": r["tier"],
                            "thread_url": ROOM_URL.format(tid=r["thread_id"])}]})

        # ── Ready-when-you-resume backlog (paused-aware) ──────────────────────
        # Applications are deliberately paused, so accumulated hot leads are a
        # backlog to work through on resume, NOT daily neglect. Preview the best
        # by ICP-then-score from a FRESH window (older leads have expired on
        # Upwork — showing 600 dead posts as 'ready' would be a lie), and report
        # the accumulated total since the pause as honest context.
        fresh_lo = (date.fromisoformat(day)
                    - timedelta(days=config.TODAY_FRESH_LEAD_DAYS)).isoformat()
        _icp_rank = ("CASE icp_tier WHEN 'icp' THEN 2 WHEN 'icp_near' THEN 1"
                     " ELSE 0 END")
        # 'awaiting a proposal' = hot AND not yet applied to. Since the live
        # cutover a hot lead is CARDED within the run (status 'ClickUp Created'),
        # so carded-ness is NOT the signal — the connects rail's applied job_ids
        # are. Excluding those keeps the backlog to leads genuinely still open.
        applied_ids = {r[0] for r in conn.execute(
            "SELECT DISTINCT job_id FROM connects_history WHERE kind='applied'"
            " AND job_id IS NOT NULL")}
        def _not_applied(rows):
            return [r for r in rows if r["id"] not in applied_ids]
        backlog_items = _not_applied([dict(r) for r in conn.execute(
            f"SELECT id, title, score, job_url, icp_tier, clickup_task_url,"
            f" substr(first_scored_at,1,10) AS d,"
            f" (draft_proposal IS NOT NULL) AS has_draft FROM scored_jobs"
            f" WHERE score >= ? AND substr(first_scored_at,1,10) >= ?"
            f" ORDER BY {_icp_rank} DESC, score DESC, first_scored_at DESC"
            f" LIMIT ?", (_HOT, fresh_lo, config.TODAY_BACKLOG_TOPN + len(applied_ids)))]
                                   )[:config.TODAY_BACKLOG_TOPN]
        # accumulated context since the pause began (or all-time if not paused)
        acc_lo = paused_since if paused else fresh_lo
        acc = conn.execute(
            "SELECT COUNT(*) hot, COALESCE(SUM(icp_tier='icp'),0) icp,"
            " COALESCE(SUM(icp_tier='icp_near'),0) near FROM scored_jobs"
            " WHERE score >= ? AND substr(first_scored_at,1,10) >= ?",
            (_HOT, acc_lo)).fetchone()
        fresh_count = conn.execute(
            "SELECT COUNT(*) FROM scored_jobs WHERE score >= ? AND"
            " substr(first_scored_at,1,10) >= ?", (_HOT, fresh_lo)).fetchone()[0]
        backlog = {
            "paused": paused, "paused_since": paused_since,
            "items": backlog_items, "fresh_count": fresh_count,
            "fresh_days": config.TODAY_FRESH_LEAD_DAYS,
            "accumulated": acc["hot"], "accumulated_since": acc_lo,
            "icp": acc["icp"], "icp_near": acc["near"]}

        # APPLIED = connects rail. The tile's today/week/last_week come from the
        # ONE shared helper the Pipeline tab also reads, so the two surfaces can
        # never show different applied numbers again (the proposals-sourced count
        # rendered 0 while Pipeline's rail showed 8 — two tabs, two numbers).
        d0 = date.fromisoformat(day)
        week_lo = (d0 - timedelta(days=6)).isoformat()
        rail = _rail_applied_windows(conn, day)
        applied_today = rail["today"]
        applied_week = rail["week"]
        applied_last = rail["last_week"]
        rail_applied_week = applied_week
        # proposals-sourced week count — NOT rendered; used only to detect and
        # explain the feed's blindness (it only sees client-answered submissions;
        # the vendorProposals scope was declined, so submitted-no-reply
        # applications are permanently invisible to it — a scope limit, not a
        # broken sync).
        proposals_week = conn.execute(
            "SELECT COUNT(*) FROM proposals WHERE"
            " substr(created_dt,1,10) BETWEEN ? AND ?",
            (week_lo, day)).fetchone()[0]

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
    finally:
        conn.close()

    # ── the ONE ruthlessly ranked act-now queue ───────────────────────────────
    # Merge the two concrete action streams under a single urgency scale so the
    # founder reads one list, not three. A follow-up with a ready draft is the
    # actionable form of an 'owed_reply', so it is ranked on that scale and the
    # matching M4 nudge is DEDUPED out (never list the same thread twice). Hot
    # leads are deliberately NOT here while paused — they are the backlog above.
    now_dt = datetime.now(config.TZ)
    # lookups so every card can name its CLIENT and show detail on click: a
    # thread's room name (owed_reply dedup is 'followup:<room_id>'), and a
    # commitment's source citation (commitment dedup is 'commitment:<id>').
    tconn = _ro(db_path)
    try:
        room_client = {r["id"]: r["room_name"] for r in tconn.execute(
            "SELECT id, room_name FROM threads WHERE room_name IS NOT NULL")}
        commit_info = {str(r["id"]): dict(r) for r in tconn.execute(
            "SELECT id, text, due, source_cite, state FROM commitments")}
    finally:
        tconn.close()

    queue = []
    fu_keys = {f["item_key"] for f in follow_ups}
    for f in follow_ups:
        ev = f.get("evidence") or {}
        eng_state = f.get("engagement") or {}
        # A PENDING UPWORK DRAFT ON AN ACTIVE CLIENT IS NOT A SALES FOLLOW-UP.
        # It is client comms with an unsent message attached. Chosen treatment:
        # RELABEL, not suppress. Suppression cannot be justified here — an
        # active client can still be owed an Upwork reply, and hiding it would
        # trade a noisy board for a silently missed obligation, which is the
        # strictly worse failure. What was actually wrong was the CLAIM: calling
        # a contracted client a prospect who has ignored us for 9 days. So the
        # card stays, says what the relationship is, ages off the real
        # cross-channel last-contact, and ranks accordingly.
        client_card = bool(eng_state.get("active"))
        owed = f["bucket"] in ("owed", "unmatched-owed")
        urg = _eng.compute_urgency(
            "owed_reply" if owed and not client_card else "capture",
            score=None, refreshed_at=None, now=now_dt,
            overdue=(f.get("age_days") or 0) >= 3)
        # age is the live signal: an owed reply sitting 8 days is hotter
        urg = round(urg * (1.0 + min((f.get("age_days") or 0), 14) / 14.0), 3)
        entry = {
            "kind": "followup", "urgency": urg,
            "title": (f"Client comms: {f['topic']}" if client_card
                      else f["topic"]),
            "client": f.get("client"),
            "tier": f["tier"], "bucket": f["bucket"],
            "age_days": f["age_days"], "draft": f["draft"],
            "thread_url": f["thread_url"], "item_key": f["item_key"],
            "threads": len(f["thread_ids"]),
            # server-supplied label so a surface never has to infer the
            # relationship from the card kind (today.js still hard-codes a
            # "reply owed" pill for every followup card — that is the one
            # remaining place this claim is made without checking).
            "label": "client comms" if client_card else "reply owed",
            "engagement": eng_state, "evidence": ev,
            "detail": {"kind": ("Client comms (draft ready)" if client_card
                                else "Follow-up (draft ready)"),
                       "client": f.get("client"),
                       "age_days": f["age_days"], "tier": f["tier"],
                       # renders as "Idle — N days since the client wrote", now
                       # measured across every channel rather than Upwork alone
                       "idle_days": f["age_days"],
                       # renders as "From" — where the conversation actually is
                       "source": _evidence.describe(ev, eng_state),
                       "thread_url": f["thread_url"], "draft": f["draft"],
                       # EVERY room and EVERY draft this one card stands for.
                       # Collapsing two of a person's threads must not make the
                       # second room's written reply unreachable.
                       "drafts": f["drafts"],
                       "thread_urls": [ROOM_URL.format(tid=t)
                                       for t in f["thread_ids"]]}}
        # PROVABLE cross-channel closure -> the existing "possibly resolved"
        # chip, never a hide. If we spoke to them after they last spoke to us,
        # on any channel, no reply is owed: Cradle's Upwork message of 08-03
        # 14:41Z was answered on Slack at 15:35Z, 54 minutes later. The chip
        # states the evidence and leaves the decision with the founder.
        if ev.get("answered_since") and ev.get("ours_channel") != "upwork":
            entry["possibly_resolved"] = {
                "source": f"{ev['ours_channel']} — you replied there"
                          f" {str(ev['ours_ts'])[:10]}, after their last message"
                          f" ({ev.get('channel')} {str(ev.get('ts'))[:10]})",
                "confidence": None,
                "evidence": _evidence.describe(ev, eng_state)}
        queue.append(entry)
    # actionable M4 nudges — everything except the paused hot-lead stream, and
    # minus any owed_reply whose thread is already a follow-up row above.
    # First build the CLOSURE overlay (J1.5): a card whose cross-source closure
    # was decided is keyed by canonical card key so it applies to the matching
    # queue entry whatever surface produced it.
    active_n = _eng.active_nudges(db_path or config.DB_PATH, now=now_dt)
    closures = {}
    for n in active_n:
        cs = n.get("closure_state")
        if cs and cs != "rejected":
            closures[_card_key(f"nudge:{n['dedup_key']}")] = {
                "state": cs, "source": n.get("closure_source"),
                "confidence": n.get("closure_confidence"),
                "evidence": n.get("closure_evidence")}
    # SUPERSEDED CARDS: an escalation is a claim about other cards, so the cards
    # it covers must not also be listed. Three "Job failed: upwork_scan" rows
    # sitting beside an escalation that says "upwork_scan failed 3x" is the same
    # fact four times, and it is what made the board feel like noise.
    #
    # A card may only be hidden by an escalation that is ITSELF VISIBLE. Built
    # from all active nudges instead, a done-then-re-minted escalation would be
    # hidden by _is_actioned while still suppressing its constituents — the
    # board goes blank while real work sits open in the engine. An escalation
    # can only stand in for work it is actually standing in front of.
    _payload: dict = {}
    for n in active_n:
        try:
            _payload[n["dedup_key"]] = json.loads(n.get("payload_json") or "{}")
        except (TypeError, ValueError):
            _payload[n["dedup_key"]] = {}
    _visible = [n for n in active_n
                if not _is_actioned(n["dedup_key"] or "", suppressed,
                                    actioned_at,
                                    _payload.get(n["dedup_key"], {}).get(
                                        "evidence_ts"),
                                    n.get("created_at"))]
    superseded: set = set()
    for n in _visible:
        superseded |= {k for k in (_payload[n["dedup_key"]].get("covers") or [])
                       if k != n["dedup_key"]}
    # ...and an escalation is never hidden BY the thing it covers: two cards
    # naming each other would otherwise take each other off the board.
    superseded -= {n["dedup_key"] for n in _visible
                   if _payload[n["dedup_key"]].get("covers")}
    for n in _visible:
        if n["kind"] == "hotlead_no_loom":
            continue                        # that IS the paused backlog
        dk = n["dedup_key"] or ""
        if dk in superseded:
            continue                        # rolled up into an escalation
        pl = _payload.get(dk, {})
        # dedup an owed_reply against its follow-up card — both are keyed on the
        # COUNTERPARTY now, so the keys compare directly (the ×2 root cause)
        if n["kind"] == "owed_reply" and dk in fu_keys:
            continue                        # already shown with its draft
        # derive the client + detail for the clickable card
        client, detail = None, {"kind": n["kind"].replace("_", " ")}
        if dk.startswith("followup:"):
            tid = (pl.get("thread_ids") or [pl.get("thread_id")])[0]
            client = (_client_label(pl.get("client_id"))
                      or room_client.get(tid or "") or None)
            detail.update({"client": client,
                           "thread_url": ROOM_URL.format(tid=tid) if tid else None,
                           "idle_days": pl.get("idle_days")})
        elif dk.startswith("commitment:"):
            ci = commit_info.get(dk.split(":", 1)[1]) or {}
            client = pl.get("client")
            detail.update({"full_text": ci.get("text"), "due": ci.get("due"),
                           "source": ci.get("source_cite"), "client": client})
        else:
            detail.update({"job_url": pl.get("job_url"), "cite": pl.get("cite")})
        queue.append({
            "kind": "nudge", "nudge_kind": n["kind"], "urgency": n["urgency_live"],
            "title": n["title"], "action": n.get("action"), "due": n.get("due"),
            "dept": n["dept"], "client": client,
            "item_key": f"nudge:{n['dedup_key']}", "detail": detail})
    # rank, then collapse to one card per (client, intent): a follow-up card is
    # preferred over a bare owed_reply nudge for the same thread (it has the draft)
    queue.sort(key=lambda e: (-e["urgency"], 0 if e["kind"] == "followup" else 1))
    _seen: dict = {}
    _deduped: list = []
    for e in queue:
        k = _card_key(e.get("item_key", ""), e.get("client"))
        if k not in _seen:
            _seen[k] = len(_deduped); _deduped.append(e)
        elif e["kind"] == "followup" and _deduped[_seen[k]]["kind"] != "followup":
            _deduped[_seen[k]] = e          # prefer the actionable draft card
    queue = _deduped
    # cross-source closure overlay (J1.5): a resolved card is never rendered as
    # still-open. auto_closed/confirmed -> hidden; possibly_resolved -> a chip.
    _auto_closed = 0
    _open_q = []
    for e in queue:
        cl = closures.get(_card_key(e.get("item_key", ""), e.get("client")))
        if cl and cl["state"] in ("auto_closed", "confirmed"):
            _auto_closed += 1
            continue
        if cl and cl["state"] == "possibly_resolved":
            e["possibly_resolved"] = {"source": cl.get("source"),
                                      "confidence": cl.get("confidence"),
                                      "evidence": cl.get("evidence")}
        _open_q.append(e)
    queue = _open_q
    # provenance law (§4.5): a client-facing card with no evidence link may not
    # render — hide it and raise a loud (not silent) anomaly.
    _hidden_keys: list = []
    _kept: list = []
    for e in queue:
        if _card_client_facing(e) and not _card_provenance(e):
            _hidden_keys.append(_card_key(e.get("item_key", ""), e.get("client")))
        else:
            _kept.append(e)
    queue = _kept
    if _hidden_keys:
        _flag_no_provenance(db_path, len(_hidden_keys), _hidden_keys)
    # render-time clusters: counts by type over the ATOMIC set ("5 commitments
    # due · 7 follow-ups owed"). These are groupings, never stored cards.
    _CLUSTER = {"followup": "follow-ups", "owed_reply": "follow-ups",
                "commitment_due": "commitments", "hotlead_no_loom": "hot leads",
                "won_no_outcome": "win outcomes", "escalate_client": "escalations",
                "lost_pattern": "scoring", "stale_dossier": "dossiers",
                "system": "system", "capture": "captures"}
    _cl_counts: dict = {}
    for e in queue:
        label = _CLUSTER.get(e.get("nudge_kind") or e.get("kind"), "other")
        # a contracted client's thread is not part of the "follow-ups" (i.e.
        # chase-the-prospect) count — the cluster line is the first thing read,
        # and "3 follow-ups" that silently includes a live client is the same
        # false claim the card itself used to make
        if (e.get("engagement") or {}).get("active") and label == "follow-ups":
            label = "client comms"
        _cl_counts[label] = _cl_counts.get(label, 0) + 1
    clusters = [{"label": k, "count": v}
                for k, v in sorted(_cl_counts.items(), key=lambda x: -x[1])]

    # ruthless: Today shows the sharpest few; the full ranked queue is the
    # Nudges tab. Overflow is stated, never silently dropped.
    queue_total = len(queue)
    queue_more = max(0, queue_total - config.TODAY_QUEUE_TOPN)
    queue = queue[:config.TODAY_QUEUE_TOPN]

    # source health: a quiet queue must be provably quiet, not a dead feed
    try:
        sconn = _ro(db_path)
        try:
            sources = source_health.report(source_health.source_health(sconn))
        finally:
            sconn.close()
    except Exception:  # noqa: BLE001 — never let a health read break Today
        sources = {"headline": "source health unavailable", "ok": False,
                   "lines": [], "count": 0}

    # money() omits 'revenue' when finance-doctor is RED (gated) — tolerate that
    rev = money(db_path, today=day).get("revenue", {})
    # Applied's numbers ARE the connects rail (applied-truth). The note only
    # explains them: paused by intent, a stale-intent contradiction, or why the
    # proposals feed reads a different (lower) number than the tile shows.
    applied_note = None
    applied_contradiction = False
    if pause_conflict_n:
        # Fix 2: a paused config the connects rail contradicts is not fact.
        applied_note = (
            f"config says applications paused since {paused_since}, but the rail"
            f" recorded {pause_conflict_n} applications since then — intent may"
            f" be stale")
        applied_contradiction = True
        _flag_applied_anomaly(db_path, "stale_intent",
                              {"constant": "APPLICATIONS_PAUSED_SINCE",
                               "paused_since": paused_since,
                               "applied_since": pause_conflict_n})
    elif paused:
        applied_note = f"paused by intent since {paused_since} — not idle"
    elif proposals_week == 0 and rail_applied_week > 0:
        # no-fabricated-zeros + one-source law: the tile SHOWS the rail count
        # (never the proposals 0), and the note explains why the proposals feed
        # disagrees. This is NOT a broken sync to go hunting for: the proposals
        # table only captures applications a client has replied to — the
        # vendorProposals scope was declined, so submitted-but-unanswered
        # applications are permanently invisible to it.
        applied_note = (
            f"showing the connects rail (authoritative): {rail_applied_week}"
            f" applications in the last 7 days. The proposals feed reads 0 here"
            f" because it only captures client-answered submissions — the"
            f" vendorProposals scope was declined, so applied-but-no-reply is"
            f" invisible to it. A permanent API limit, not a broken sync.")
        applied_contradiction = True
        _flag_applied_anomaly(db_path, "applied_feed_lag",
                              {"proposals_week": proposals_week,
                               "rail_week": rail_applied_week})
    elif rail_applied_week == 0:
        applied_note = "no applications in the last 7 days"
    return {
        "sources": sources,
        "queue": queue, "queue_total": queue_total, "queue_more": queue_more,
        "queue_hidden_no_provenance": len(_hidden_keys),
        "queue_auto_closed": _auto_closed, "clusters": clusters,
        "follow_ups": follow_ups,           # kept for back-compat / counts
        "backlog": backlog,
        "metrics": {
            "applied": {"today": applied_today, "week": applied_week,
                        "last_week": applied_last, "paused": paused,
                        "paused_since": paused_since, "note": applied_note,
                        "contradiction": applied_contradiction},
            "active": {"proposals": active_props, "interviews": interviews,
                       "contracts": active_contracts},
            "queue_len": len(queue), "follow_ups_due": follow_ups_due,
            "backlog_fresh": backlog["fresh_count"],
            "revenue": {"mtd_usd": rev.get("month_usd"),
                        "target_usd": rev.get("target_usd"),
                        "pct": rev.get("pct_to_target")},
        },
    }


def today_rows(db_path: Optional[Path] = None, *, which: Optional[str] = None,
               _today: Optional[str] = None) -> dict:
    """The rows behind a Today backlog figure (traceability contract). which:
    'fresh' (hot leads in the fresh window, awaiting a proposal) or
    'accumulated' (all hot leads since applications paused). Read-only, capped.

    `_today` is a TEST clock slot, not a request param — underscored so the
    server cannot forward `?today=` into it (it used to, and `?today=lol` came
    back as a 500 from date.fromisoformat)."""
    if which not in ("fresh", "accumulated"):
        return {"error": f"unknown scope {which!r}", "rows": [], "count": 0}
    day = _today or _ist_today()
    paused, paused_since = _applications_paused(day)
    fresh_lo = (date.fromisoformat(day)
                - timedelta(days=config.TODAY_FRESH_LEAD_DAYS)).isoformat()
    lo = fresh_lo if which == "fresh" else (paused_since if paused else fresh_lo)
    conn = _ro(db_path)
    try:
        _icp_rank = ("CASE icp_tier WHEN 'icp' THEN 2 WHEN 'icp_near' THEN 1"
                     " ELSE 0 END")
        rows = [dict(r) for r in conn.execute(
            f"SELECT id, title, score, icp_tier, job_url, clickup_task_url,"
            f" substr(first_scored_at,1,10) AS d FROM scored_jobs"
            f" WHERE score >= ? AND substr(first_scored_at,1,10) >= ?"
            f" ORDER BY {_icp_rank} DESC, score DESC, first_scored_at DESC"
            f" LIMIT 300", (_HOT, lo))]
        total = conn.execute(
            "SELECT COUNT(*) FROM scored_jobs WHERE score >= ? AND"
            " substr(first_scored_at,1,10) >= ?", (_HOT, lo)).fetchone()[0]
    finally:
        conn.close()
    return {"which": which, "since": lo, "rows": rows, "count": len(rows),
            "total": total}


def _money_source_health(db_path: Optional[Path]) -> Optional[dict]:
    """Worst-off source feeding the Money tab. Never raises — a health read must
    not be able to take down the panel it is describing."""
    try:
        conn = _ro(db_path)
        try:
            return source_health.worst_for(
                source_health.source_health(conn), "money")
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None


def _money_provisional(db_path: Optional[Path]) -> Optional[dict]:
    """The payment-mail rail's overlay: is the ledger BEHIND the email evidence,
    and which reported payments are still unattributed. This is the caveat that
    stops a GREEN-but-stale ledger from rendering a confidently-wrong monthly
    total (the Adelye-looks-delinquent bug). Never raises — an overlay must not
    be able to take down the panel it annotates; absent table -> None."""
    try:
        from modules.finance import payment_mail_rail as _rail
        conn = _ro(db_path)
        try:
            stale = _rail.staleness(conn)
            unmapped = _rail.mapping_queue(conn)
        finally:
            conn.close()
        return {"staleness": stale, "unmapped_count": len(unmapped)}
    except Exception:  # noqa: BLE001 — table may not exist yet / read-only guard
        return None


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
            " description, pnl_bucket, amount_base_minor, creation_dt, pnl_month"
            " FROM transactions WHERE canonical_key IS NOT NULL")]
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
        # pnl_month accrual override (late salaries etc.) wins over the payment
        # date for the monthly P&L split; cash + all-time totals are unaffected
        return r.get("pnl_month") or (r.get("creation_dt") or "")[:7]

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
        # Money is fed by several sources (the Upwork sync, the weekly finance
        # pull, the manual statement imports) and is only as trustworthy as its
        # sickest input — so this is the WORST of them, not the first. A GREEN
        # doctor means the ledger is internally consistent; it says nothing
        # about whether anything has been ADDED lately.
        "source_health": _money_source_health(db_path),
        # payment-mail overlay: 'ledger stale — N payments seen in email but not
        # yet imported (most recent: <client>, <date>)'. A GREEN doctor means the
        # ledger is internally consistent; this says whether it is also CURRENT.
        "provisional": _money_provisional(db_path),
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
            " creation_dt, pnl_month FROM transactions"
            " WHERE canonical_key IS NOT NULL")]
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
        # pnl_month accrual override wins for the month a row is attributed to
        m = r.get("pnl_month") or (r.get("creation_dt") or "")[:7]
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


# ── Jarvis mapping queue (J1 D3) ──────────────────────────────────────────────
def queue(db_path: Optional[Path] = None) -> dict:
    """Unmapped events grouped by proposed client, with evidence + provenance,
    plus the client list for assignment and the latest sweep's reconciliation.
    Counts reconcile: total = mapped + queued + held + excluded."""
    conn = _ro(db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, source, author, subject, ts, permalink, proposed_client_id,"
            " proposal_confidence, proposal_evidence, substr(text,1,180) preview"
            " FROM events WHERE state='queued'"
            " ORDER BY proposed_client_id IS NULL, proposed_client_id, ts DESC")]
        counts = {s: 0 for s in ("mapped", "queued", "held", "excluded")}
        for r in conn.execute("SELECT state, COUNT(*) n FROM events GROUP BY state"):
            if r["state"] in counts:
                counts[r["state"]] = r["n"]
        counts["total"] = sum(counts[s] for s in
                              ("mapped", "queued", "held", "excluded"))
        held_rows = [dict(r) for r in conn.execute(
            "SELECT id, source, author, subject, ts, permalink,"
            " substr(text,1,180) preview FROM events WHERE state='held'"
            " ORDER BY ts DESC")]
        last = conn.execute(
            "SELECT id, metrics_json, completed_at FROM runs WHERE"
            " task_name='jarvis_sweep' AND status IN ('completed','skipped_empty')"
            " ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()

    reg = registry.load()
    groups: dict = {}
    for r in rows:
        pid = r.get("proposed_client_id")
        key = pid or "__unassigned__"
        g = groups.setdefault(key, {
            "proposed_client_id": pid,
            "proposed_name": (reg.by_id.get(pid) or {}).get("name") if pid
            else "Unassigned (no confident proposal)",
            "count": 0, "items": []})
        g["count"] += 1
        g["items"].append({
            "event_id": r["id"], "source": r["source"], "author": r["author"],
            "subject": r["subject"], "ts": r["ts"], "permalink": r["permalink"],
            "confidence": r["proposal_confidence"],
            "evidence": r["proposal_evidence"], "preview": r["preview"]})
    ordered = sorted(groups.values(),
                     key=lambda g: (g["proposed_client_id"] is None, -g["count"]))

    last_sweep = None
    if last:
        try:
            m = json.loads(last["metrics_json"] or "{}")
        except (TypeError, ValueError):
            m = {}
        last_sweep = {"at": last["completed_at"], "run_id": last["id"],
                      "reconciled": m.get("reconciled"), "metrics": m}

    held_items = [{"event_id": r["id"], "source": r["source"],
                   "author": r["author"], "subject": r["subject"], "ts": r["ts"],
                   "permalink": r["permalink"], "preview": r["preview"]}
                  for r in held_rows]
    return {"groups": ordered, "queued_total": len(rows),
            "held_items": held_items, "held_total": len(held_items),
            "counts": counts,
            "clients": [{"id": cid, "name": reg.by_id[cid]["name"]}
                        for cid in sorted(reg.matchable_ids(),
                                          key=lambda i: reg.by_id[i]["name"].lower())],
            "registry_sha": reg.sha[:12], "last_sweep": last_sweep}


# ── Activity: what the system actually did on a day (UI brief §3) ────────────
# THE CLOCKS IN THIS SCHEMA DO NOT AGREE, and mixing them silently moves a whole
# day's numbers (migration 015 documents the same trap between runs+anomalies):
#
#   runs.started_at        IST with an explicit +05:30  -> substr(...,1,10) IS
#                          the IST date (every runner stamps it that way)
#   claude_usage.ist_date  already an IST calendar date -> compare directly
#   nudges.created_at etc. IST but NAIVE (nudges._iso)  -> substr(...,1,10)
#   sources / facts / threads / shadow_ledger / events
#                          UTC via datetime('now')      -> _ist_day(col)
#
# date(col,'+330 minutes') is the IST calendar date of a UTC-stamped column and
# stays correct if that column ever gains an offset or a Z, because SQLite
# normalizes to UTC before applying the modifier. Unparseable values yield NULL,
# so a junk timestamp drops out of the day instead of being mis-filed into it.
_ACTIVITY_RUN_CAP = 500          # run-log rows per day (true total also returned)
_ACTIVITY_TOP_CLIENTS = 8
_REGISTRY_GIT_TIMEOUT_S = 6      # the git log is the only slow read here
_REGISTRY_CACHE_TTL_S = 120
_registry_cache: dict = {}       # day -> (fetched_at, payload)
# approve._git_commit's subject line, which is the only place the registry says
# WHAT it learned: "registry: <client_id> <field> += <value>".
_REGISTRY_SUBJECT_RE = _re.compile(
    r"^registry:\s+(?P<client>[A-Za-z0-9][\w.-]*)\s+(?P<field>\w+)\s*\+=\s*"
    r"(?P<value>\S.*)$")


def _ist_day(col: str) -> str:
    """SQL fragment: the IST calendar date of a UTC-stamped column."""
    return "date({}, '+330 minutes')".format(col)


def _ts_utc(ts) -> str:
    """A timestamp reduced to a comparable UTC instant. Delegates to the CoS
    engine's ts_key — the one place that knows all four shapes this codebase
    stores. Imported lazily because `modules.chief_of_staff.nudges` collides
    with this module's own nudges() reader at module scope."""
    from modules.chief_of_staff import nudges as _eng
    return _eng.ts_key(ts)


def _metrics_summary(metrics, limit: int = 4) -> Optional[str]:
    """One plain line from an arbitrary metrics dict: the first `limit` non-zero
    numeric leaves, path-labelled, in the order the task wrote them. Generic on
    purpose — every task writes a different shape and a per-task formatter would
    rot the day a task changes its metrics."""
    if not isinstance(metrics, dict):
        return None
    pairs = []

    def walk(node, path):
        for k, v in node.items():
            p = k if not path else path + "." + k
            if isinstance(v, dict):
                walk(v, p)
            elif isinstance(v, bool):
                continue                     # flags are not counts
            elif isinstance(v, (int, float)) and v:
                pairs.append((p, round(v, 4) if isinstance(v, float) else v))
            if len(pairs) >= limit:
                return

    walk(metrics, "")
    return " · ".join("{} {}".format(k, v) for k, v in pairs[:limit]) or None


def _registry_changes(day: str) -> dict:
    """Changes to config/clients.yaml on `day` (IST), read from git.

    Every mapping-queue approval writes an AUDITED commit (modules/jarvis/
    approve._git_commit) — that commit log is the only record that "2 clients
    added, 1 alias" ever happened, and it has been invisible until now.

    Degrades to an empty list with a note whenever git is missing, slow or
    angry: a registry line is worth less than the page it would take down. The
    result (success or degradation) is cached briefly — the subprocess is the
    slowest thing in activity() by an order of magnitude."""
    now = time.time()
    hit = _registry_cache.get(day)
    if hit and now - hit[0] < _REGISTRY_CACHE_TTL_S:
        return hit[1]
    root = Path(config.V3_ROOT)
    reg_path = Path(config.CLIENTS_REGISTRY)
    try:
        rel = str(reg_path.relative_to(root))
    except ValueError:
        rel = str(reg_path)
    out = {"file": rel, "commits": [], "count": 0, "insertions": 0,
           "deletions": 0, "note": None}
    argv = ["git", "-C", str(root), "log",
            "--since=" + day + "T00:00:00+05:30",
            "--until=" + day + "T23:59:59+05:30",
            "--no-merges", "--format=%H\x1f%cI\x1f%s", "--numstat",
            "--", rel]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              cwd=str(root), timeout=_REGISTRY_GIT_TIMEOUT_S)
    except Exception as exc:               # noqa: BLE001 — never raise from here
        out["note"] = "registry history unavailable ({})".format(
            type(exc).__name__)
        _registry_cache[day] = (now, out)
        return out
    if proc.returncode != 0:
        out["note"] = "registry history unavailable (git exit {})".format(
            proc.returncode)
        _registry_cache[day] = (now, out)
        return out
    current = None
    for line in (proc.stdout or "").splitlines():
        if "\x1f" in line:
            sha, at, subject = (line.split("\x1f", 2) + ["", ""])[:3]
            current = {"sha": sha[:12], "at": at, "subject": subject,
                       "insertions": 0, "deletions": 0}
            # approve._git_commit writes the subject as
            # "registry: <client_id> <field> += <value>", so WHAT changed is
            # already in the line we have — no second git call to say it.
            # A hand-written commit ("registry: add RECO Health as a prospect")
            # simply does not match and stays unparsed rather than being
            # forced into a shape it never had.
            m = _REGISTRY_SUBJECT_RE.match(subject or "")
            current["change"] = ({"client_id": m.group("client"),
                                  "field": m.group("field"),
                                  "value": m.group("value").strip()}
                                 if m else None)
            out["commits"].append(current)
            continue
        parts = line.split("\t")
        if current is not None and len(parts) == 3:
            for key, raw in (("insertions", parts[0]), ("deletions", parts[1])):
                try:
                    current[key] += int(raw)       # '-' on a binary diff
                except ValueError:
                    pass
    out["count"] = len(out["commits"])
    out["insertions"] = sum(c["insertions"] for c in out["commits"])
    out["deletions"] = sum(c["deletions"] for c in out["commits"])
    _registry_cache[day] = (now, out)
    return out


def _run_costs(run_rows: list, usage_rows: list) -> tuple:
    """Cost per run, and the spend no run can claim.

    claude_usage.run_id exists (migration 002) but has NEVER been written — 0 of
    9310 rows on the live DB — so task + time window is the only attribution
    available. ts_key() reconciles the mixed clock shapes (legacy naive rows are
    UTC, current writers stamp +05:30). A call outside every window of its task
    is reported as unattributed rather than smeared onto the nearest run."""
    now_key = _ts_utc(datetime.now(config.TZ).isoformat())
    windows = []
    for r in run_rows:
        start = _ts_utc(r.get("started_at"))
        if not start:
            continue                        # no window => claims nothing
        windows.append((r["id"], r.get("task_name"), start,
                        _ts_utc(r.get("completed_at")) or now_key))
    by_run: dict = {}
    unattributed = 0.0
    for u in usage_rows:
        usd = float(u.get("total_cost_usd") or 0)
        if u.get("run_id"):                 # if it is ever written, trust it
            by_run[u["run_id"]] = by_run.get(u["run_id"], 0.0) + usd
            continue
        called = _ts_utc(u.get("called_at"))
        hit = next((rid for rid, task, s, e in windows
                    if task == u.get("task_name") and s <= called <= e), None)
        if hit is None:
            unattributed += usd
        else:
            by_run[hit] = by_run.get(hit, 0.0) + usd
    return by_run, round(unattributed, 4)


def activity(db_path: Optional[Path] = None, *, day: Optional[str] = None) -> dict:
    """The daily digest — "what did the system do on this day?" in figures the
    UI can render as plain sentences. `day` is an IST calendar date (defaults to
    today IST) and is KEYWORD-ONLY: the server only forwards keyword-only
    params, so a positional one would silently ignore ?day=.

    Every section carries the previous day where the comparison is honest (a
    volume, not a stock). `empty` is True when literally nothing happened, so
    the UI says "nothing ran" instead of rendering a shell of zeros."""
    day = (day or _ist_today())[:10]
    try:
        d0 = date.fromisoformat(day)
    except ValueError:
        return {"error": "bad day {!r} (expected YYYY-MM-DD)".format(day)}
    prev = (d0 - timedelta(days=1)).isoformat()

    conn = _ro(db_path)
    try:
        # ── runs ────────────────────────────────────────────────────────────
        by_task: dict = {}
        totals = {"total": 0, "completed": 0, "failed": 0, "skipped": 0,
                  "running": 0, "wall_seconds": 0.0}
        wall_ms = 0
        for r in conn.execute(
                "SELECT task_name, status, COUNT(*) n,"
                " COALESCE(SUM(duration_ms),0) ms FROM runs"
                " WHERE substr(started_at,1,10)=? GROUP BY task_name, status",
                (day,)):
            entry = by_task.setdefault(r["task_name"],
                                       {"task": r["task_name"], "n": 0,
                                        "status_counts": {}, "wall_seconds": 0.0})
            entry["n"] += r["n"]
            entry["status_counts"][r["status"]] = r["n"]
            entry["wall_seconds"] = round(
                entry["wall_seconds"] + (r["ms"] or 0) / 1000.0, 1)
            totals["total"] += r["n"]
            wall_ms += r["ms"] or 0
            status = r["status"] or ""
            if status == "completed":
                totals["completed"] += r["n"]
            elif status == "failed":
                totals["failed"] += r["n"]
            elif status.startswith("skipped"):
                totals["skipped"] += r["n"]
            elif status == "running":
                totals["running"] += r["n"]
        totals["wall_seconds"] = round(wall_ms / 1000.0, 1)
        totals["by_task"] = sorted(by_task.values(), key=lambda e: -e["n"])
        prev_runs = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE substr(started_at,1,10)=?",
            (prev,)).fetchone()[0]

        run_rows = [dict(r) for r in conn.execute(
            "SELECT id, task_name, status, started_at, completed_at,"
            " duration_ms, shadow, error, metrics_json FROM runs"
            " WHERE substr(started_at,1,10)=?"
            " ORDER BY started_at DESC, id DESC LIMIT ?",
            (day, _ACTIVITY_RUN_CAP))]

        # ── spend (ist_date is authoritative; no clock maths needed) ────────
        spend_row = conn.execute(
            "SELECT COALESCE(SUM(total_cost_usd),0) usd, COUNT(*) calls"
            " FROM claude_usage WHERE ist_date=?", (day,)).fetchone()
        spend_by_task = [dict(r) for r in conn.execute(
            "SELECT task_name, ROUND(SUM(total_cost_usd),4) usd, COUNT(*) calls"
            " FROM claude_usage WHERE ist_date=? GROUP BY task_name"
            " ORDER BY usd DESC", (day,))]
        prev_spend = conn.execute(
            "SELECT COALESCE(SUM(total_cost_usd),0) FROM claude_usage"
            " WHERE ist_date=?", (prev,)).fetchone()[0]
        usage_rows = [dict(r) for r in conn.execute(
            "SELECT run_id, task_name, called_at, total_cost_usd"
            " FROM claude_usage WHERE ist_date=?", (day,))]

        # ── ingest: how many Fathoms were transcribed, and what else came in ─
        ingest_by_type = {r["source_type"]: r["n"] for r in conn.execute(
            "SELECT source_type, COUNT(*) n FROM sources WHERE "
            + _ist_day("ingested_at") + "=? GROUP BY source_type", (day,))}
        extracted = conn.execute(
            "SELECT COUNT(*) FROM sources WHERE " + _ist_day("ingested_at")
            + "=? AND status='extracted'", (day,)).fetchone()[0]
        prev_ingest = conn.execute(
            "SELECT COUNT(*) FROM sources WHERE " + _ist_day("ingested_at")
            + "=?", (prev,)).fetchone()[0]

        # ── facts ───────────────────────────────────────────────────────────
        facts_total = conn.execute(
            "SELECT COUNT(*) FROM facts WHERE " + _ist_day("created_at") + "=?",
            (day,)).fetchone()[0]
        facts_by_client = [{"client": r["c"], "n": r["n"]} for r in conn.execute(
            "SELECT COALESCE(client_id,'(unattributed)') c, COUNT(*) n"
            " FROM facts WHERE " + _ist_day("created_at") + "=?"
            " GROUP BY c ORDER BY n DESC, c LIMIT ?",
            (day, _ACTIVITY_TOP_CLIENTS))]
        facts_clients = conn.execute(
            "SELECT COUNT(DISTINCT COALESCE(client_id,'(unattributed)'))"
            " FROM facts WHERE " + _ist_day("created_at") + "=?",
            (day,)).fetchone()[0]
        prev_facts = conn.execute(
            "SELECT COUNT(*) FROM facts WHERE " + _ist_day("created_at") + "=?",
            (prev,)).fetchone()[0]

        # ── threads ─────────────────────────────────────────────────────────
        th = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(client_id IS NOT NULL),0) att"
            " FROM threads WHERE " + _ist_day("first_seen_at") + "=?",
            (day,)).fetchone()
        th_all = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(client_id IS NOT NULL),0) att"
            " FROM threads").fetchone()
        prev_threads = conn.execute(
            "SELECT COUNT(*) FROM threads WHERE "
            + _ist_day("first_seen_at") + "=?", (prev,)).fetchone()[0]
        # events.resolved_at is the one TIMESTAMPED attribution decision in the
        # schema (mapping-queue approvals + the auto-resolvers).
        mapping_resolved = conn.execute(
            "SELECT COUNT(*) FROM events WHERE " + _ist_day("resolved_at")
            + "=? AND client_id IS NOT NULL", (day,)).fetchone()[0]

        # ── nudges (IST-naive stamps: substr, never date(+330)) ─────────────
        nudge_created = conn.execute(
            "SELECT COUNT(*) FROM nudges WHERE substr(created_at,1,10)=?",
            (day,)).fetchone()[0]
        nudge_created_by_kind = {r["kind"]: r["n"] for r in conn.execute(
            "SELECT kind, COUNT(*) n FROM nudges"
            " WHERE substr(created_at,1,10)=? GROUP BY kind ORDER BY n DESC",
            (day,))}
        closed = {r["state"]: r["n"] for r in conn.execute(
            "SELECT state, COUNT(*) n FROM nudges"
            " WHERE state IN ('done','dismissed')"
            " AND substr(updated_at,1,10)=? GROUP BY state", (day,))}
        prev_nudges = conn.execute(
            "SELECT COUNT(*) FROM nudges WHERE substr(created_at,1,10)=?",
            (prev,)).fetchone()[0]

        # ── shadow ledger (cutover evidence) ────────────────────────────────
        shadow_rows = [dict(r) for r in conn.execute(
            "SELECT target, operation, COUNT(*) n FROM shadow_ledger WHERE "
            + _ist_day("at") + "=? GROUP BY target, operation ORDER BY n DESC",
            (day,))]
    finally:
        conn.close()

    cost_by_run, unattributed_usd = _run_costs(run_rows, usage_rows)
    log = []
    for r in run_rows:
        try:
            metrics = json.loads(r["metrics_json"]) if r["metrics_json"] else None
        except (TypeError, ValueError):
            metrics = None
        log.append({
            "id": r["id"], "task": r["task_name"], "status": r["status"],
            "started_at": r["started_at"], "completed_at": r["completed_at"],
            "duration_ms": r["duration_ms"], "shadow": r["shadow"],
            "error": (r["error"] or None),
            "summary": _metrics_summary(metrics),
            "usd": round(cost_by_run.get(r["id"], 0.0), 4)
            if r["id"] in cost_by_run else None,
        })
    totals["rows"] = log
    totals["rows_capped"] = totals["total"] > len(log)
    totals["rows_shown"] = len(log)
    totals["prev_total"] = prev_runs
    totals["failures"] = [e for e in log if e["status"] == "failed"][:20]

    reg_changes = _registry_changes(day)
    ingest_total = sum(ingest_by_type.values())
    shadow_total = sum(r["n"] for r in shadow_rows)
    nudge_closed = sum(closed.values())

    empty = not any((totals["total"], ingest_total, facts_total, th["n"],
                     mapping_resolved, reg_changes["count"], spend_row["calls"],
                     nudge_created, nudge_closed, shadow_total))
    return {
        "day": day,
        "prev_day": prev,
        "empty": empty,
        "runs": totals,
        "ingest": {
            "total": ingest_total,
            "by_source_type": ingest_by_type,
            # named, not inferred: this is the "how many Fathoms were
            # transcribed today" figure Abhijeet asked for by name
            "fathom_transcripts": ingest_by_type.get("fathom", 0),
            "loom_recordings": ingest_by_type.get("loom", 0),
            "email_messages": ingest_by_type.get("email", 0),
            "slack_messages": ingest_by_type.get("slack", 0),
            "extracted": extracted,
            "prev_total": prev_ingest,
        },
        "facts": {"total": facts_total, "clients": facts_clients,
                  "by_client": facts_by_client,
                  "top_n": _ACTIVITY_TOP_CLIENTS, "prev_total": prev_facts},
        "threads": {
            "new": th["n"], "new_attributed": th["att"],
            "attributed_total": th_all["att"],
            "unattributed_total": th_all["n"] - th_all["att"],
            "mapping_resolved": mapping_resolved,
            "prev_new": prev_threads,
            # honesty over a fabricated figure: link_threads UPDATEs
            # threads.client_id in place and stamps nothing, so "threads
            # attributed on this day" is not recoverable from the threads
            # table. mapping_resolved is the timestamped decision that IS.
            "note": "threads.client_id carries no attribution timestamp;"
                    " mapping_resolved counts events resolved to a client"
                    " on this day",
        },
        "registry": reg_changes,
        "spend": {"usd": round(spend_row["usd"] or 0, 4),
                  "calls": spend_row["calls"],
                  "by_task": spend_by_task,
                  "unattributed_usd": unattributed_usd,
                  "prev_usd": round(prev_spend or 0, 4)},
        "nudges": {"created": nudge_created,
                   "created_by_kind": nudge_created_by_kind,
                   "closed": nudge_closed,
                   "done": closed.get("done", 0),
                   "dismissed": closed.get("dismissed", 0),
                   "prev_created": prev_nudges},
        "shadow": {"total": shadow_total, "rows": shadow_rows},
    }


def activity_run(db_path: Optional[Path] = None, *,
                 run_id: Optional[str] = None) -> dict:
    """Everything about ONE run: the runs row, its parsed metrics, the anomalies
    it raised, its Claude calls and cost, and — when it failed — the error.

    `run_id` is KEYWORD-ONLY (the server forwards nothing else) and an unknown
    or malformed id returns {"error": ...} rather than raising, so a stale link
    in the run log renders a message instead of a 500."""
    try:
        rid = int(str(run_id).strip())
    except (TypeError, ValueError):
        return {"error": "bad run_id {!r}".format(run_id)}

    conn = _ro(db_path)
    try:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        if row is None:
            return {"error": "no run {}".format(rid)}
        run = dict(row)
        anomalies = [dict(r) for r in conn.execute(
            "SELECT id, detected_at, task_name, kind, severity, detail_json,"
            " resolved_at FROM anomalies WHERE run_id=? ORDER BY id", (rid,))]
        usage = [dict(r) for r in conn.execute(
            "SELECT id, called_at, task_name, purpose, model, billing_route,"
            " input_tokens, output_tokens, cache_creation_tokens,"
            " cache_read_tokens, total_cost_usd, duration_ms, tool_calls,"
            " stop_reason, error, run_id FROM claude_usage WHERE run_id=?"
            " ORDER BY id", (rid,))]
        attribution = "run_id"
        if not usage:
            # claude_usage.run_id has never been written by any task, so the
            # window fallback carries this entirely. Bound the scan to the run's
            # IST date ±1 day, then match exactly with ts_key.
            anchor = (run.get("started_at") or "")[:10]
            lo, hi = anchor, anchor
            try:
                d = date.fromisoformat(anchor)
                lo = (d - timedelta(days=1)).isoformat()
                hi = (d + timedelta(days=1)).isoformat()
            except ValueError:
                pass
            candidates = [dict(r) for r in conn.execute(
                "SELECT id, called_at, task_name, purpose, model, billing_route,"
                " input_tokens, output_tokens, cache_creation_tokens,"
                " cache_read_tokens, total_cost_usd, duration_ms, tool_calls,"
                " stop_reason, error, run_id FROM claude_usage"
                " WHERE task_name=? AND run_id IS NULL AND ist_date BETWEEN ? AND ?"
                " ORDER BY id", (run.get("task_name"), lo, hi))]
            start = _ts_utc(run.get("started_at"))
            end = (_ts_utc(run.get("completed_at"))
                   or _ts_utc(datetime.now(config.TZ).isoformat()))
            usage = [u for u in candidates
                     if start and start <= _ts_utc(u.get("called_at")) <= end]
            attribution = "window" if usage else "none"
        shadow_rows = [dict(r) for r in conn.execute(
            "SELECT target, operation, COUNT(*) n FROM shadow_ledger"
            " WHERE run_id=? GROUP BY target, operation ORDER BY n DESC",
            (rid,))]
    finally:
        conn.close()

    metrics, metrics_error = None, None
    if run.get("metrics_json"):
        try:
            metrics = json.loads(run["metrics_json"])
        except (TypeError, ValueError) as exc:
            metrics_error = "unparseable metrics_json: {!r}".format(exc)
    inline_anomalies = None
    if run.get("anomalies_json"):
        try:
            inline_anomalies = json.loads(run["anomalies_json"])
        except (TypeError, ValueError):
            inline_anomalies = run["anomalies_json"]

    from modules.cockpit import actions
    return {
        "run": run,
        "metrics": metrics,
        "metrics_error": metrics_error,
        "summary": _metrics_summary(metrics),
        "failed": run.get("status") == "failed",
        "error": run.get("error") or None,
        "anomalies": anomalies,
        "anomalies_inline": inline_anomalies,
        "usage": {
            "rows": usage,
            "calls": len(usage),
            "usd": round(sum(float(u.get("total_cost_usd") or 0)
                             for u in usage), 4),
            "input_tokens": sum(int(u.get("input_tokens") or 0) for u in usage),
            "output_tokens": sum(int(u.get("output_tokens") or 0) for u in usage),
            # how the calls were tied to this run, so the UI never presents a
            # window guess as a hard link
            "attribution": attribution,
        },
        "shadow": {"total": sum(r["n"] for r in shadow_rows),
                   "rows": shadow_rows},
        "rerunnable": run.get("task_name") in actions.RERUN_JOBS,
    }


# ── Activity drill: the per-SOURCE story behind "5 Fathom transcripts" ───────
# Abhijeet, verbatim: "Activity shows that 5 fathom transcripts. Once I click on
# it I need to see WHICH 5 transcripts were processed and how they were added to
# the registry and everything."
#
# This reader's job is keeping PROVABLE apart from REPLAYED, because the most
# interesting half of that answer is not in the schema.
#
#   PROVABLE — a key, not a guess
#     facts.source_id       a real FK (004_kb.sql, NOT NULL). Every fact names
#                           the source it came from, so "what came out of this
#                           call" is exact, not a same-day coincidence.
#     events.raw_ref        = sources.source_ref for a fathom/loom queue item,
#                           and events.resolved_by / resolved_at is the ONLY
#                           timestamped attribution decision anywhere in the DB.
#     claude_usage.purpose  literally spells "extract <source_ref>" /
#                           "commitments <source_ref>" (extract.py:108). That is
#                           a string CONVENTION, not a foreign key, and every
#                           row that uses it says so.
#
#   NOT RECORDED — therefore replayed, and labelled as replayed
#     WHY a source carries the client_id it carries. ingest.classify() writes
#     sources.client_id at INSERT and keeps no note of which identifier matched;
#     fathom_bridge.heal_source() overwrites it later and stamps nothing. So the
#     "how" is re-derived by running the same two rules again against TODAY's
#     registry, and every such answer carries proven=False. When the replay does
#     not reproduce the stored client_id we say exactly that instead of naming a
#     plausible identifier — the decision was made against an older registry, or
#     by hand, and the row cannot tell which.
#
#   NOT PROVABLE AT ALL, and not faked
#     "did these facts reach the dossier", as a TIMING claim.
#     Two reasons, one of which is now gone. (a) knowledge/daily.run_daily used
#     to compute `now_iso` at the START of the run and hand that same string to
#     the compile at the END, so last_compiled_at was the run's START time and
#     routinely EARLIER than facts written by that very run — FIXED 2026-08-13,
#     the stamp is now read per client at compile time (daily._stamp). (b) The
#     units still do not match and that one is NOT fixed: facts.created_at is
#     UTC (`datetime('now')`), last_compiled_at is an IST wall-clock string with
#     no seconds and no offset, so any ordering between them is out by 5h30 and
#     compares a ' ' separator against a 'T' (see CLAUDE.md, nudges.ts_key). So
#     the dossier answer here stays a PRESENCE claim (the dossier exists / does
#     not, and compile_dossier re-reads all of that client's facts every time),
#     and the note says which claim is being made.
_ACTIVITY_SOURCE_CAP = 200        # rows per call; the true total is always sent
_ACTIVITY_FACT_CAP = 400          # ditto for the per-fact drill
_PAYLOAD_MAX_BYTES = 4_000_000    # a raw payload bigger than this is not opened
_PAYLOAD_READ_CAP = 60            # raw payloads opened per call
_SOURCE_TYPE_RE = _re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
# The only source type whose attribution rules are pure and cheap enough to
# replay inside a read. Loom goes through a MODEL classifier and email through
# the sender-domain gate; re-running neither is possible here, and guessing at
# them would be worse than an honest "not replayable".
_REPLAY_TYPES = ("fathom",)


def _like_escape(s) -> str:
    """Escape a literal for use inside a LIKE pattern. `_` matches ANY single
    character in SQL LIKE and source refs are full of them, so an unescaped ref
    quietly matches other sources' rows."""
    return (str(s or "").replace("\\", "\\\\")
            .replace("%", "\\%").replace("_", "\\_"))


def _instant(v):
    """One of this schema's timestamp shapes -> datetime, or None. Used only for
    arithmetic on two stamps from the SAME payload (recording start/end), so no
    zone is assumed for a naive value — the subtraction is still correct."""
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _payload_extras(source_type: str, raw_path) -> tuple:
    """(extras, note) from the raw payload the ingester kept on disk.

    The `sources` row holds no participants and no duration — both exist ONLY in
    the raw JSON — so this opens the file. Bounded three ways (a source-type
    allowlist, a byte cap, and a per-call file cap in the caller) because a read
    endpoint that opens arbitrary files is a read endpoint that can hang."""
    extras = {"participants": [], "url": None, "duration_seconds": None,
              "recorded_by": None}
    if source_type != "fathom":
        return extras, ("participants and duration live only in the raw Fathom"
                        " payload; a {} source records neither".format(source_type))
    if not raw_path:
        return extras, "no raw payload path was recorded for this source"
    p = Path(raw_path)
    try:
        size = p.stat().st_size
    except OSError:
        return extras, "the raw payload is no longer on disk ({})".format(p.name)
    if size > _PAYLOAD_MAX_BYTES:
        return extras, ("raw payload is {:,} bytes, over the read cap — not"
                        " opened".format(size))
    try:
        payload = json.loads(p.read_text())
        from modules.knowledge import ingest as _ingest
        parsed = _ingest.parse_fathom(payload)
    except Exception as exc:                 # noqa: BLE001 — never 500 a drill
        return extras, "raw payload could not be read ({})".format(type(exc).__name__)
    extras["participants"] = list(parsed.get("participants") or [])
    extras["url"] = parsed.get("url") or payload.get("share_url")
    rec = payload.get("recorded_by")
    if isinstance(rec, dict):
        extras["recorded_by"] = rec.get("name") or rec.get("email")
    a = _instant(payload.get("recording_start_time"))
    b = _instant(payload.get("recording_end_time"))
    if a and b and b >= a and bool(a.tzinfo) == bool(b.tzinfo):
        extras["duration_seconds"] = int((b - a).total_seconds())
    return extras, None


def _ident_kind(client: dict, value: str) -> str:
    """Which registry FIELD an identifier came from — "registry alias" and
    "Fathom name" are different answers to "how did you decide this"."""
    n = registry.norm(value)
    if n == registry.norm(client.get("name") or ""):
        return "registry name"
    for field, label in (("aliases", "registry alias"),
                         ("fathom_names", "Fathom name"),
                         ("emails", "registry email/domain")):
        if any(n == registry.norm(x) for x in (client.get(field) or [])):
            return label
    return "registry identifier"


def _replay_attribution(source: dict, participants: list, reg,
                        client_map: list) -> Optional[dict]:
    """Re-derive HOW this source came to carry its client_id, by running the two
    real rules again: ingest.classify() (what runs at INSERT) and then
    fathom_bridge._match() (what runs when the bridge heals an older row).

    Returns None when neither reproduces the stored client_id — the caller turns
    that into "the current registry no longer explains this", which is the true
    answer and not a guess dressed as one."""
    cid = source.get("client_id")
    if not cid:
        return None
    entry = reg.by_id.get(cid) or {}
    hay = " ".join(participants or []).lower() + " " + (source.get("title") or "").lower()

    # 1. the INSERT-time rule: substring of any identifier in participants+title
    try:
        from modules.knowledge import ingest as _ingest
        got, _name, ctype = _ingest.classify(participants, source.get("title"),
                                             client_map)
    except Exception:                        # noqa: BLE001
        got, ctype = None, None
    if got == cid:
        row = next((c for c in client_map if c["client_id"] == cid), {})
        hit = next((i for i in row.get("identifiers", [])
                    if i and i.lower() in hay), None)
        if hit:
            return {"rule": "ingest.classify",
                    "how": _ident_kind(entry, hit),
                    "matched_on": hit,
                    "content_type": ctype,
                    "detail": "the ingester matched {} {!r} against the call's"
                              " participants and title".format(
                                  _ident_kind(entry, hit), hit)}

    # 2. the HEAL rule: an exact email/name match on one participant
    for p in participants or []:
        got = reg.match_email(p) if "@" in p else reg.match_name(p)
        if got != cid:
            continue
        kind = ("registry email/domain" if "@" in p else _ident_kind(entry, p))
        return {"rule": "fathom_bridge._match",
                "how": kind,
                "matched_on": p,
                "content_type": None,
                "detail": "participant {!r} resolves to this client through the"
                          " registry's {} index".format(p, kind)}

    # 3. the bridge's last resort: a fathom_name token anywhere in the haystack
    for tok in (entry.get("fathom_names") or []):
        if tok and tok.lower() in hay:
            return {"rule": "fathom_bridge._match (fathom_names)",
                    "how": "Fathom name", "matched_on": tok,
                    "content_type": None,
                    "detail": "the Fathom name {!r} appears in the call's title"
                              " or participants".format(tok)}
    return None


def _registry_commits_matching(day: str, needles: list) -> dict:
    """Registry commits whose MESSAGE names one of `needles`.

    approve._git_commit writes the event id into the commit BODY ("...evidence:
    fathom evt:fathom:123 from x@y.com"), which is the only thread from an
    approved queue item back to the commit it caused. Windowed day-1..day+2
    because an approval at 23:59 commits on the next calendar day, and degraded
    (never raised) exactly like _registry_changes."""
    out = {"commits": [], "note": None}
    if not needles:
        return out
    key = (day, tuple(sorted(needles)))
    now = time.time()
    hit = _registry_cache.get(key)
    if hit and now - hit[0] < _REGISTRY_CACHE_TTL_S:
        return hit[1]
    root = Path(config.V3_ROOT)
    reg_path = Path(config.CLIENTS_REGISTRY)
    try:
        rel = str(reg_path.relative_to(root))
    except ValueError:
        rel = str(reg_path)
    try:
        d0 = date.fromisoformat(day)
        since, until = (d0 - timedelta(days=1)).isoformat(), \
            (d0 + timedelta(days=2)).isoformat()
    except ValueError:
        since = until = day
    argv = ["git", "-C", str(root), "log", "--no-merges", "--fixed-strings",
            "--since=" + since + "T00:00:00+05:30",
            "--until=" + until + "T23:59:59+05:30",
            "--format=%H\x1f%cI\x1f%s\x1f%b\x1e"]
    argv += ["--grep=" + n for n in needles]
    argv += ["--", rel]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              cwd=str(root), timeout=_REGISTRY_GIT_TIMEOUT_S)
    except Exception as exc:                 # noqa: BLE001
        out["note"] = "registry commit search unavailable ({})".format(
            type(exc).__name__)
        _registry_cache[key] = (now, out)
        return out
    if proc.returncode != 0:
        out["note"] = "registry commit search unavailable (git exit {})".format(
            proc.returncode)
        _registry_cache[key] = (now, out)
        return out
    for chunk in (proc.stdout or "").split("\x1e"):
        if "\x1f" not in chunk:
            continue
        sha, at, subject, body = (chunk.strip("\n").split("\x1f", 3) + ["", "", ""])[:4]
        out["commits"].append({
            "sha": sha.strip()[:12], "at": at, "subject": subject,
            "body": (body or "").strip(),
            "matched": [n for n in needles if n in subject or n in (body or "")],
        })
    _registry_cache[key] = (now, out)
    return out


def _extract_stops(conn, day: str) -> list:
    """The day's runs whose metrics carry an `extract` block. `stopped` is the
    ONLY recorded reason a source that landed today was never read for facts
    ("budget", "cap"), and it lives in the run, not on the source row — so it is
    reported as RUN-level context on the source, never as a per-row fact."""
    out = []
    for r in conn.execute(
            "SELECT id, task_name, metrics_json FROM runs"
            " WHERE substr(started_at,1,10)=? AND metrics_json LIKE '%extract%'"
            " ORDER BY id", (day,)):
        try:
            ex = (json.loads(r["metrics_json"]) or {}).get("extract")
        except (TypeError, ValueError):
            continue
        if not isinstance(ex, dict):
            continue
        out.append({"run_id": r["id"], "task": r["task_name"],
                    "extracted": ex.get("extracted"), "facts": ex.get("facts"),
                    "stopped": ex.get("stopped"), "pending": ex.get("pending")})
    return out


def activity_sources(db_path: Optional[Path] = None, *,
                     day: Optional[str] = None,
                     source_type: Optional[str] = None) -> dict:
    """The rows behind "N Fathom transcripts" (or any source type) on one day.

    One row per SOURCE ingested on that IST calendar day — the same window and
    the same filter the digest tile counts, so the drill reconciles with the
    figure exactly. Both params are KEYWORD-ONLY and both are registered in
    server.QUERY_PARAMS; a malformed one comes back as {"error": ...} with
    total=None, never as an empty list (UI_BUILD_BRIEF §0)."""
    day = (day or _ist_today())[:10]
    if not _is_iso_day(day):
        return _bad_filter("day", day, "YYYY-MM-DD")
    stype = (source_type or "").strip() or None
    if stype is not None and not _SOURCE_TYPE_RE.match(stype):
        return _bad_filter("source_type", source_type,
                           "a source type such as fathom|loom|email|slack|crm")

    conn = _ro(db_path)
    try:
        day_types = {r["source_type"]: r["n"] for r in conn.execute(
            "SELECT source_type, COUNT(*) n FROM sources WHERE "
            + _ist_day("ingested_at") + "=? GROUP BY source_type", (day,))}
        where = _ist_day("ingested_at") + "=?"
        args: list = [day]
        if stype:
            where += " AND source_type=?"
            args.append(stype)
        total = conn.execute(
            "SELECT COUNT(*) FROM sources WHERE " + where, args).fetchone()[0]
        rows = [dict(r) for r in conn.execute(
            "SELECT id, source_type, source_ref, client_id, client_name,"
            " content_type, density, title, occurred_dt, raw_path, status,"
            " ingested_at FROM sources WHERE " + where
            + " ORDER BY COALESCE(occurred_dt, ingested_at) DESC, id DESC"
            " LIMIT ?", args + [_ACTIVITY_SOURCE_CAP])]

        ids = [r["id"] for r in rows]
        refs = [r["source_ref"] for r in rows if r["source_ref"]]
        marks = ",".join("?" * len(ids)) or "NULL"
        ref_marks = ",".join("?" * len(refs)) or "NULL"

        # facts — the FK link, so this is exact
        facts_by_source: dict = {}
        if ids:
            for f in conn.execute(
                    "SELECT source_id, COALESCE(client_id,'') cid, scope,"
                    " category, confidence, COUNT(*) n, MIN(created_at) f0,"
                    " MAX(created_at) f1 FROM facts WHERE source_id IN ("
                    + marks + ") GROUP BY source_id, cid, scope, category,"
                    " confidence", ids):
                e = facts_by_source.setdefault(f["source_id"], {
                    "n": 0, "clients": {}, "scopes": {}, "categories": {},
                    "confidence": {}, "first_at": None, "last_at": None,
                    # where each fact actually GOES. It is a cross-tab of
                    # (client_id, scope), not either one alone: 16 client-scope
                    # facts with a NULL client_id and 2 agency ones is "16 went
                    # nowhere", and reading scope on its own called that
                    # "proof-points" — the 2 speaking for the 16.
                    "routes": {"dossier": 0, "proof_points": 0, "nowhere": 0},
                    "dossier_clients": {}})
                e["n"] += f["n"]
                for bucket, key in (("clients", f["cid"] or "(no client)"),
                                    ("scopes", f["scope"] or "(none)"),
                                    ("categories", f["category"] or "(none)"),
                                    ("confidence", f["confidence"] or "(none)")):
                    e[bucket][key] = e[bucket].get(key, 0) + f["n"]
                if f["scope"] and f["scope"] != "client":
                    e["routes"]["proof_points"] += f["n"]
                elif f["cid"]:
                    e["routes"]["dossier"] += f["n"]
                    e["dossier_clients"][f["cid"]] = \
                        e["dossier_clients"].get(f["cid"], 0) + f["n"]
                else:
                    e["routes"]["nowhere"] += f["n"]
                e["first_at"] = min(x for x in (e["first_at"], f["f0"]) if x)
                e["last_at"] = max(x for x in (e["last_at"], f["f1"]) if x)

        # mapping-queue events — matched on raw_ref (what fathom_bridge writes)
        # AND on the canonical id, because the two rails key them differently
        ev_by_ref: dict = {}
        if refs:
            canon = ["evt:{}:{}".format(r["source_type"],
                                        (r["source_ref"] or "").split("/", 1)[-1])
                     for r in rows]
            cmarks = ",".join("?" * len(canon))
            for e in conn.execute(
                    "SELECT id, raw_ref, source, native_id, state, client_id,"
                    " proposed_client_id, proposal_confidence,"
                    " proposal_evidence, exclude_reason, resolved_by,"
                    " resolved_at, author, first_seen_at, sweep_run_id"
                    " FROM events WHERE raw_ref IN (" + ref_marks + ")"
                    " OR id IN (" + cmarks + ")", refs + canon):
                e = dict(e)
                ev_by_ref[e["raw_ref"] or e["id"]] = e
                ev_by_ref.setdefault(e["id"], e)

        # cost — claude_usage.purpose names the source_ref as a STRING, never a
        # key. One query per source would be 5 round-trips for a 5-row day; one
        # OR-chain of escaped LIKEs is a single scan.
        cost_by_ref: dict = {}
        if refs:
            clause = " OR ".join(["purpose LIKE ? ESCAPE '\\'"] * len(refs))
            pats = ["%" + _like_escape(r) for r in refs]
            for u in conn.execute(
                    "SELECT purpose, task_name, called_at, ist_date, model,"
                    " total_cost_usd, input_tokens, output_tokens, error"
                    " FROM claude_usage WHERE " + clause + " ORDER BY id", pats):
                u = dict(u)
                ref = next((r for r in refs if (u["purpose"] or "").endswith(r)),
                           None)
                if ref is None:
                    continue
                c = cost_by_ref.setdefault(ref, {"usd": 0.0, "calls": 0, "rows": []})
                c["usd"] = round(c["usd"] + float(u["total_cost_usd"] or 0), 6)
                c["calls"] += 1
                c["rows"].append(u)

        dossiers = {r["client_id"]: dict(r) for r in conn.execute(
            "SELECT client_id, client_name, dossier_path, fact_count,"
            " source_count, last_compiled_at FROM dossier_index")}
        extract_runs = _extract_stops(conn, day)
    finally:
        conn.close()

    reg = None
    client_map: list = []
    reg_note = None
    if any(r["source_type"] in _REPLAY_TYPES for r in rows):
        try:
            from modules.knowledge import clients as _kbclients
            reg = registry.load()
            client_map = _kbclients.load_client_map()
        except Exception as exc:             # noqa: BLE001
            reg_note = "the registry could not be loaded ({}), so no attribution" \
                       " could be replayed".format(type(exc).__name__)

    opened = 0
    out_rows = []
    approvals: list = []
    for r in rows:
        ref = r["source_ref"]
        ev = ev_by_ref.get(ref)
        extras, payload_note = {"participants": [], "url": None,
                                "duration_seconds": None,
                                "recorded_by": None}, None
        if opened < _PAYLOAD_READ_CAP:
            extras, payload_note = _payload_extras(r["source_type"], r["raw_path"])
            opened += 1
        else:
            payload_note = ("this call's raw payload was not opened — the drill"
                            " reads at most {} of them".format(_PAYLOAD_READ_CAP))

        # ── how the client was decided ──────────────────────────────────────
        approved = bool(ev and ev.get("resolved_at") and ev.get("client_id"))
        if approved:
            attribution = {
                "state": "attributed", "proven": True,
                "how": "mapping-queue approval",
                "matched_on": ev.get("author"),
                "rule": "jarvis.approve.resolve",
                "detail": "{} resolved this to {} on {} — a recorded decision,"
                          " not an inference".format(
                              ev.get("resolved_by") or "someone",
                              ev.get("client_id"), ev.get("resolved_at")),
            }
        elif r["client_id"]:
            replay = (_replay_attribution(r, extras["participants"], reg, client_map)
                      if reg is not None else None)
            if replay:
                attribution = dict(replay, state="attributed", proven=False)
            else:
                attribution = {
                    "state": "attributed", "proven": False, "how": None,
                    "matched_on": None, "rule": None,
                    "detail": reg_note or (
                        "the row says {} but today's registry does not reproduce"
                        " that match. Nothing records WHY a source was"
                        " attributed — ingest.classify stamps no reason and"
                        " heal_source overwrites in place — so this was decided"
                        " against an earlier registry, or by hand.".format(
                            r["client_id"])),
                }
        else:
            # `proven` here means "this sentence is a RECORDED state, not
            # something re-derived". A queued/excluded event IS recorded. "No
            # identifier matches" is a claim I just computed, so it is not.
            proven = False
            if ev and ev.get("state") == "queued":
                why, proven = ("waiting in the mapping queue for you to pick a"
                               " client"), True
            elif ev and ev.get("state") == "excluded":
                why, proven = "excluded by the sweep ({})".format(
                    ev.get("exclude_reason") or "no reason recorded"), True
            elif r["source_type"] not in _REPLAY_TYPES:
                why = ("not attributed, and this source type's decision path is"
                       " not replayable here")
            elif reg is None:
                why = reg_note or "not attributed; the registry could not be read"
            elif not extras["participants"]:
                # do not claim a check that could not happen
                why = ("not attributed — and the raw payload is not readable"
                       " ({}), so not even a replay of what was checked is"
                       " possible".format(payload_note or "no reason recorded"))
            else:
                why = ("no registry identifier matches any of this call's"
                       " participants or its title (replayed against today's"
                       " registry)")
            attribution = {"state": "unattributed", "proven": proven, "how": None,
                           "matched_on": None, "rule": None, "detail": why}

        if approved:
            approvals.append(ev["id"])

        # ── what came out of it ─────────────────────────────────────────────
        fx = facts_by_source.get(r["id"])
        if not fx:
            dossier = {"state": "no-facts", "client_id": r["client_id"],
                       "path": None, "fact_count": None,
                       "last_compiled_at": None,
                       "note": "no fact was extracted from this source, so"
                               " there is nothing to file"}
        else:
            routes = fx["routes"]
            extra = []
            if routes["proof_points"]:
                extra.append("{} of them are agency / cross-client scope and"
                             " compile into proof-points.md instead".format(
                                 routes["proof_points"]))
            if routes["dossier"] and routes["nowhere"]:
                extra.append("{} carry no client at all".format(routes["nowhere"]))
            tail = (" (" + "; ".join(extra) + ")") if extra else ""
            if routes["dossier"]:
                cid = max(fx["dossier_clients"], key=lambda k: fx["dossier_clients"][k])
                doc = dossiers.get(cid)
                dossier = {
                    "state": "compiled" if doc else "no-dossier",
                    "client_id": cid,
                    "path": (doc or {}).get("dossier_path"),
                    "fact_count": (doc or {}).get("fact_count"),
                    "last_compiled_at": (doc or {}).get("last_compiled_at"),
                    "note": ("compile_dossier re-reads every fact this client"
                             " has, so these are in it as of the last compile."
                             " last_compiled_at is stamped at the START of the"
                             " compiling run, so it cannot be used to prove"
                             " ordering — this is a presence claim, not a"
                             " timing one." if doc else
                             "these facts carry client {} but no dossier has"
                             " ever been compiled for it — they are in the KB"
                             " and searchable, but no dossier holds them"
                             .format(cid)) + tail,
                }
            elif routes["nowhere"]:
                dossier = {"state": "no-client", "client_id": None, "path": None,
                           "fact_count": None, "last_compiled_at": None,
                           "note": "{} of these facts are client-scope with no"
                                   " client attached, so no dossier can hold"
                                   " them. Attribute the call in Review and"
                                   " heal_source moves them.".format(
                                       routes["nowhere"]) + tail}
            else:
                dossier = {"state": "proof-points", "client_id": None,
                           "path": None, "fact_count": None,
                           "last_compiled_at": None,
                           "note": "every fact here is agency / cross-client"
                                   " scope, so they compile into"
                                   " proof-points.md, not a client dossier"}

        # ── why it was not processed ────────────────────────────────────────
        not_processed = None
        if r["status"] != "extracted":
            stopped = [e for e in extract_runs if e.get("stopped")]
            not_processed = {
                "status": r["status"],
                "reason": {"ingested": "ingested but never read for facts",
                           "skipped": "the ingester skipped it",
                           "quarantined": "quarantined"}.get(
                               r["status"], "status is {!r}".format(r["status"])),
                "run_context": (
                    "{} run #{} stopped extracting on {!r} with {} sources still"
                    " pending — this source is one of that day's unread tail."
                    " That is RUN-level evidence: the source row itself records"
                    " no reason.".format(
                        stopped[0]["task"], stopped[0]["run_id"],
                        stopped[0]["stopped"], stopped[0].get("pending"))
                    if stopped else None),
            }

        cost = cost_by_ref.get(ref)
        out_rows.append({
            "source_id": r["id"],
            "source_type": r["source_type"],
            "source_ref": ref,
            "native_id": (ref or "").split("/", 1)[-1],
            "title": r["title"],
            "occurred_dt": r["occurred_dt"],
            "ingested_at": r["ingested_at"],
            "status": r["status"],
            "content_type": r["content_type"],
            "density": r["density"],
            "url": extras["url"],
            "recorded_by": extras["recorded_by"],
            "duration_seconds": extras["duration_seconds"],
            "participants": extras["participants"],
            "payload_note": payload_note,
            "client_id": r["client_id"],
            "client_name": r["client_name"],
            "client_kind": (reg.kind(r["client_id"])
                            if reg is not None and r["client_id"] else None),
            "attribution": attribution,
            "queue": ({"event_id": ev["id"], "state": ev["state"],
                       "author": ev["author"],
                       "proposed_client_id": ev["proposed_client_id"],
                       "proposal_confidence": ev["proposal_confidence"],
                       "proposal_evidence": ev["proposal_evidence"],
                       "exclude_reason": ev["exclude_reason"],
                       "resolved_by": ev["resolved_by"],
                       "resolved_at": ev["resolved_at"]} if ev else None),
            "queue_note": None if ev else
            "no mapping-queue event was ever created for this source",
            "facts": (dict(fx, link="facts.source_id (a foreign key)")
                      if fx else {"n": 0, "clients": {}, "scopes": {},
                                  "categories": {}, "confidence": {},
                                  "first_at": None, "last_at": None,
                                  "link": "facts.source_id (a foreign key)"}),
            "dossier": dossier,
            "cost": (dict(cost, link="claude_usage.purpose names the source_ref"
                                     " as text — a convention, not a key")
                     if cost else None),
            "cost_note": None if cost else
            "no model call named this source in claude_usage.purpose",
            "not_processed": not_processed,
        })

    # ── did any of this change the registry? ────────────────────────────────
    reg_hits = _registry_commits_matching(day, approvals) if approvals else \
        {"commits": [], "note": None}
    by_needle: dict = {}
    for c in reg_hits["commits"]:
        for n in c.get("matched") or []:
            by_needle.setdefault(n, []).append(
                {k: c[k] for k in ("sha", "at", "subject", "body")})
    for row in out_rows:
        q = row.get("queue") or {}
        eid = q.get("event_id")
        hits = by_needle.get(eid) if eid else None
        if hits:
            row["registry_change"] = {"state": "committed", "commits": hits}
        elif row["attribution"].get("how") == "mapping-queue approval":
            row["registry_change"] = {
                "state": "approved-no-commit", "commits": [],
                "note": reg_hits["note"] or
                "this was approved through the queue, but no commit on"
                " config/clients.yaml names the event — approve.resolve only"
                " writes the registry when the sender yields a REUSABLE pattern"
                " (approve.learned_pattern); otherwise it resolves the event and"
                " leaves the registry alone"}
        else:
            row["registry_change"] = {
                "state": "none", "commits": [],
                "note": "no mapping-queue approval touched this source, so it"
                        " caused no registry commit"}

    return {
        "day": day,
        "source_type": stype,
        "total": total,
        "count": len(out_rows),
        "capped": total > len(out_rows),
        "rows": out_rows,
        "day_by_source_type": day_types,
        "extract_runs": extract_runs,
        "registry_search_note": reg_hits["note"],
        "registry_note": reg_note,
        "note": "One row per source INGESTED on this IST day — the same window"
                " the digest tile counts. Fact counts come from facts.source_id"
                " (a foreign key, exact). Cost comes from claude_usage.purpose"
                " naming the source_ref in text (a convention, not a key)."
                " 'How' is REPLAYED against today's registry unless it says"
                " 'mapping-queue approval', which is the only attribution"
                " decision the schema timestamps.",
    }


def activity_facts(db_path: Optional[Path] = None, *,
                   day: Optional[str] = None,
                   client: Optional[str] = None) -> dict:
    """Every fact created on `day` (IST), each with the source it came from.

    The rows behind the digest's "N facts extracted": one row per FACT, joined
    to its source through facts.source_id, so the drill sums to the tile. Note
    the window is the fact's creation day, which is NOT always its source's
    ingest day — a backfill extracts today from a call pulled in June."""
    day = (day or _ist_today())[:10]
    if not _is_iso_day(day):
        return _bad_filter("day", day, "YYYY-MM-DD")
    who = (client or "").strip() or None

    conn = _ro(db_path)
    try:
        where = _ist_day("f.created_at") + "=?"
        args: list = [day]
        if who:
            where += " AND COALESCE(f.client_id,'(unattributed)')=?"
            args.append(who)
        total = conn.execute(
            "SELECT COUNT(*) FROM facts f WHERE " + where, args).fetchone()[0]
        rows = [dict(r) for r in conn.execute(
            "SELECT f.id, f.client_id, f.category, f.confidence, f.scope,"
            " f.access_scope, f.fact_text, f.citation, f.created_at,"
            " f.source_id, s.source_type, s.source_ref, s.title source_title,"
            " s.occurred_dt FROM facts f LEFT JOIN sources s"
            " ON s.id = f.source_id WHERE " + where
            + " ORDER BY f.id DESC LIMIT ?", args + [_ACTIVITY_FACT_CAP])]
        orphans = sum(1 for r in rows if r["source_ref"] is None)
    finally:
        conn.close()

    return {
        "day": day, "client": who, "total": total, "count": len(rows),
        "capped": total > len(rows), "rows": rows,
        "orphans": orphans,
        "note": "One row per FACT, joined to its source through facts.source_id"
                " (a foreign key). The window is the fact's CREATION day, not"
                " its source's ingest day — a backfill can extract today from a"
                " call pulled months ago."
                + (" {} of these rows point at a source row that no longer"
                   " exists.".format(orphans) if orphans else ""),
    }
