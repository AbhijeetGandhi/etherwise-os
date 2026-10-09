"""Source-health: the mechanism that stops a silently-degraded source from
rendering stale/thin numbers as if they were current.

Two failure modes, one verdict per source:
  STALE     — stopped producing (freshness from `runs`)
  DEGRADED  — ran but thin (a degrade anomaly on the LATEST run, self-healing)
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import config, db, source_health as sh

IST = config.TZ

# two synthetic sources so the test never depends on the live registry
SRC = (
    {"key": "rail", "label": "Rail", "task_name": "t_rail",
     "max_age_hours": 26, "degrade_kinds": (), "critical": True,
     "backs": "applied", "why": "applied-truth"},
    {"key": "scan", "label": "Scan", "task_name": "t_scan",
     "max_age_hours": 3, "degrade_kinds": ("scan_feed_degraded",),
     "critical": True, "backs": "pipeline", "why": "funnel + board"},
)


def _now():
    return datetime(2026, 7, 16, 12, 0, tzinfo=IST)


def _run(conn, task, *, hours_ago, status="completed"):
    ts = (_now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO runs (task_name, started_at, completed_at, status, shadow)"
        " VALUES (?,?,?,?,0)",
        (task, ts, ts if status == "completed" else None, status))
    return cur.lastrowid


class SourceHealthCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="src-health-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def health(self):
        with db.connect(self.db_path) as conn:
            return sh.source_health(conn, now=_now(), sources=SRC)

    # ── freshness ────────────────────────────────────────────────────────────
    def test_fresh_source_is_ok(self):
        with db.connect(self.db_path) as conn:
            _run(conn, "t_rail", hours_ago=1)
            _run(conn, "t_scan", hours_ago=0.5)
        h = self.health()
        self.assertTrue(h["ok"])
        self.assertEqual(h["worst"], "ok")
        self.assertTrue(all(s["ok"] for s in h["sources"]))

    def test_source_past_its_max_age_is_stale(self):
        with db.connect(self.db_path) as conn:
            _run(conn, "t_rail", hours_ago=30)   # > 26h
            _run(conn, "t_scan", hours_ago=1)
        h = self.health()
        rail = {s["key"]: s for s in h["sources"]}["rail"]
        self.assertEqual(rail["status"], "stale")
        self.assertFalse(h["ok"])
        self.assertEqual(h["worst"], "stale")
        self.assertIn("rail", h["critical_unhealthy"])

    def test_never_run_source_is_down(self):
        h = self.health()                        # no runs at all
        self.assertTrue(all(s["status"] == "down" for s in h["sources"]))
        self.assertEqual(h["worst"], "down")

    def test_a_failed_latest_run_does_not_refresh(self):
        # the last SUCCESS is what counts; a fresh failure leaves it stale
        with db.connect(self.db_path) as conn:
            _run(conn, "t_rail", hours_ago=40, status="completed")
            _run(conn, "t_rail", hours_ago=0.2, status="failed")
            _run(conn, "t_scan", hours_ago=1)
        rail = {s["key"]: s for s in self.health()["sources"]}["rail"]
        self.assertEqual(rail["status"], "stale")
        self.assertGreater(rail["age_hours"], 26)

    # ── degradation (self-healing via run_id) ────────────────────────────────
    def test_degrade_anomaly_on_latest_run_marks_degraded(self):
        with db.connect(self.db_path) as conn:
            _run(conn, "t_rail", hours_ago=1)
            rid = _run(conn, "t_scan", hours_ago=0.5)
            conn.execute(
                "INSERT INTO anomalies (task_name, kind, severity, run_id,"
                " detail_json) VALUES ('t_scan','scan_feed_degraded','critical',"
                " ?, '{\"feeds\":[\"my_feed=2\"]}')", (rid,))
        scan = {s["key"]: s for s in self.health()["sources"]}["scan"]
        self.assertEqual(scan["status"], "degraded")
        self.assertTrue(scan["degraded"])
        self.assertEqual(scan["degrade_detail"], {"feeds": ["my_feed=2"]})

    def test_degradation_self_heals_when_a_clean_run_follows(self):
        # the anomaly is bound to the OLD run; a newer clean run is 'latest', so
        # the source is healthy again WITHOUT anyone resolving the anomaly
        with db.connect(self.db_path) as conn:
            _run(conn, "t_rail", hours_ago=1)
            old = _run(conn, "t_scan", hours_ago=2)
            conn.execute(
                "INSERT INTO anomalies (task_name, kind, severity, run_id)"
                " VALUES ('t_scan','scan_feed_degraded','critical', ?)", (old,))
            _run(conn, "t_scan", hours_ago=0.5)   # clean, newer -> now 'latest'
        scan = {s["key"]: s for s in self.health()["sources"]}["scan"]
        self.assertEqual(scan["status"], "ok")
        self.assertFalse(scan["degraded"])

    def test_stale_outranks_degraded(self):
        # a source both cold AND with an old degrade anomaly reads STALE — not
        # getting data at all is worse than getting thin data
        with db.connect(self.db_path) as conn:
            _run(conn, "t_rail", hours_ago=1)
            old = _run(conn, "t_scan", hours_ago=10)   # > 3h max
            conn.execute(
                "INSERT INTO anomalies (task_name, kind, severity, run_id)"
                " VALUES ('t_scan','scan_feed_degraded','critical', ?)", (old,))
        scan = {s["key"]: s for s in self.health()["sources"]}["scan"]
        self.assertEqual(scan["status"], "stale")

    def test_unrelated_anomaly_kind_is_ignored(self):
        with db.connect(self.db_path) as conn:
            _run(conn, "t_rail", hours_ago=1)
            rid = _run(conn, "t_scan", hours_ago=0.5)
            conn.execute(
                "INSERT INTO anomalies (task_name, kind, severity, run_id)"
                " VALUES ('t_scan','budget_soft_cap','warn', ?)", (rid,))
        scan = {s["key"]: s for s in self.health()["sources"]}["scan"]
        self.assertEqual(scan["status"], "ok")   # not a degrade kind

    def test_by_backs_finds_the_right_source(self):
        with db.connect(self.db_path) as conn:
            _run(conn, "t_rail", hours_ago=1)
            _run(conn, "t_scan", hours_ago=0.5)
        h = self.health()
        self.assertEqual(sh.by_backs(h, "applied")["key"], "rail")
        self.assertEqual(sh.by_backs(h, "pipeline")["key"], "scan")
        self.assertIsNone(sh.by_backs(h, "nothing"))


class EscalationCase(unittest.TestCase):
    """A source dead for days must not read like one that missed a single run —
    the brief fires twice daily, so persistent failures have to get louder or
    they blend into the noise."""

    def _stale(self, hours, max_age=26, manual=False):
        return {"status": "stale", "label": "Rail", "why": "the applied-truth source",
                "last_success": "2026-07-10", "age_hours": hours,
                "max_age_hours": max_age, "hours_overdue": hours - max_age,
                "days_overdue": int((hours - max_age) // 24),
                "escalation": sh._escalation(hours - max_age), "manual": manual}

    def test_escalation_levels_by_time_past_deadline(self):
        self.assertEqual(sh._escalation(1), 1)      # just missed
        self.assertEqual(sh._escalation(23), 1)
        self.assertEqual(sh._escalation(25), 2)     # ~2 briefs later
        self.assertEqual(sh._escalation(71), 2)
        self.assertEqual(sh._escalation(100), 3)    # days dead

    def test_wording_gets_louder_and_differs_at_each_level(self):
        l1 = sh.source_line(self._stale(27))
        l2 = sh.source_line(self._stale(55))
        l3 = sh.source_line(self._stale(150))
        self.assertTrue(l1.startswith("STALE:"))
        self.assertTrue(l2.startswith("STILL STALE"))
        self.assertTrue(l3.startswith("DEAD"))
        self.assertEqual(len({l1, l2, l3}), 3)      # three distinct voices

    def test_manual_source_is_told_to_import_not_debugged(self):
        line = sh.source_line(self._stale(1200, max_age=1080, manual=True))
        self.assertIn("fed manually", line)
        self.assertNotIn("Nothing is feeding it", line)

    def test_headline_is_always_present_and_says_all_clear(self):
        healthy = {"sources": [{"ok": True, "escalation": 0} for _ in range(6)]}
        r = sh.report(healthy)
        self.assertTrue(r["ok"])
        self.assertEqual(r["headline"], "all 6 sources fresh")
        self.assertEqual(r["lines"], [])

    def test_headline_lead_word_matches_what_is_actually_wrong(self):
        # a DEGRADED source has no escalation (it ran, just thin) — keying the
        # lead word off escalation alone mislabelled it 'STALE'
        degraded = {"sources": [
            {"ok": True, "escalation": 0, "status": "ok"},
            {"ok": False, "escalation": 0, "status": "degraded"}]}
        self.assertTrue(sh.headline(degraded).startswith("DEGRADED —"))
        stale = {"sources": [
            {"ok": False, "escalation": 1, "status": "stale"},
            {"ok": False, "escalation": 0, "status": "degraded"}]}
        self.assertTrue(sh.headline(stale).startswith("STALE —"))

    def test_headline_leads_with_dead_when_anything_is_dead(self):
        h = {"sources": [{"ok": True, "escalation": 0},
                         dict(self._stale(200), ok=False, key="k")]}
        r = sh.report(h)
        self.assertFalse(r["ok"])
        self.assertTrue(r["headline"].startswith("DEAD —"))
        self.assertEqual(r["worst_escalation"], 3)


class DataBackedSourceCase(unittest.TestCase):
    """The finance importers are fed by hand — no runs row to age. Their
    freshness comes from the age of the DATA they produced."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="src-data-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _src(self, max_age=1080):
        return ({"key": "ledger", "label": "Statement imports",
                 "task_name": "finance_import", "max_age_hours": max_age,
                 "degrade_kinds": (), "critical": True, "backs": "money",
                 "freshness_sql": "SELECT MAX(first_seen_at) FROM transactions",
                 "why": "the manual imports behind cash"},)

    def _insert(self, first_seen):
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type,"
                " amount_minor, currency, first_seen_at)"
                " VALUES ('r1','k1','Other',100,'USD',?)", (first_seen,))

    def test_recent_import_is_fresh(self):
        # naive timestamps in this schema are UTC (SQLite datetime('now'))
        utc_recent = (_now().astimezone(timezone.utc)
                      - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        self._insert(utc_recent)
        with db.connect(self.db_path) as conn:
            h = sh.source_health(conn, now=_now(), sources=self._src())
        s = h["sources"][0]
        self.assertEqual(s["status"], "ok")
        self.assertTrue(s["manual"])

    def test_naive_timestamp_is_read_as_utc_not_local(self):
        # a naive stamp read as IST would look 5.5h younger than it is
        utc = (_now().astimezone(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")
        self._insert(utc)
        with db.connect(self.db_path) as conn:
            h = sh.source_health(conn, now=_now(), sources=self._src())
        self.assertLess(h["sources"][0]["age_hours"], 0.5)   # ~now, not -5.5h

    def test_unfed_ledger_goes_stale(self):
        old = (_now().astimezone(timezone.utc)
               - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        self._insert(old)
        with db.connect(self.db_path) as conn:
            h = sh.source_health(conn, now=_now(), sources=self._src())
        s = h["sources"][0]
        self.assertEqual(s["status"], "stale")
        self.assertEqual(s["escalation"], 3)          # 15d past a 45d deadline
        self.assertIn("fed manually", sh.source_line(s))

    def test_empty_table_is_down_not_crash(self):
        with db.connect(self.db_path) as conn:
            h = sh.source_health(conn, now=_now(), sources=self._src())
        self.assertEqual(h["sources"][0]["status"], "down")


class WorstForCase(unittest.TestCase):
    """Money is fed by several sources; a tab is only as trustworthy as its
    sickest input, so the stamp must take the worst — not the first."""

    def test_worst_for_picks_the_sickest_of_several(self):
        health = {"sources": [
            {"key": "a", "backs": "money", "status": "ok", "hours_overdue": 0},
            {"key": "b", "backs": "money", "status": "stale", "hours_overdue": 10},
            {"key": "c", "backs": "money", "status": "stale", "hours_overdue": 400},
            {"key": "d", "backs": "pipeline", "status": "down", "hours_overdue": 0},
        ]}
        self.assertEqual(sh.worst_for(health, "money")["key"], "c")
        self.assertEqual(sh.worst_for(health, "pipeline")["key"], "d")
        self.assertIsNone(sh.worst_for(health, "nothing"))


class RegistryCase(unittest.TestCase):
    def test_every_declared_source_is_well_formed(self):
        seen = set()
        for s in config.SOURCES:
            for key in ("key", "label", "task_name", "max_age_hours", "backs"):
                self.assertIn(key, s, s.get("key"))
            self.assertNotIn(s["key"], seen, "duplicate source key")
            seen.add(s["key"])
            self.assertGreater(s["max_age_hours"], 0)

    def test_money_backing_sources_are_registered(self):
        by_key = {s["key"]: s for s in config.SOURCES}
        for k in ("upwork_sync", "crm_mirror", "finance_upwork_weekly",
                  "finance_ledger"):
            self.assertIn(k, by_key)
        # the money tab must have more than one feeder (that is why worst_for exists)
        money = [s for s in config.SOURCES if s["backs"] == "money"]
        self.assertGreater(len(money), 1)


class DoctorSourceCheckCase(unittest.TestCase):
    """The doctor check that turns a stale/degraded critical source amber."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="src-doc-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stale_real_source_is_a_warn_not_a_fail(self):
        from core import doctor_checks as dc
        # the real registry's connects_rail has never run in this fresh DB ->
        # down -> WARN (amber). It must be WARN, not FAIL: suspect, not corrupt.
        checks = dc.check_source_freshness(db_path=self.db_path, now=_now())
        statuses = {c.status for c in checks}
        self.assertIn("WARN", statuses)
        self.assertNotIn("FAIL", statuses)

    def test_fresh_sources_pass(self):
        from core import doctor_checks as dc
        recent_utc = (_now().astimezone(timezone.utc)
                      - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with db.connect(self.db_path) as conn:
            for s in config.SOURCES:
                if s.get("freshness_sql"):
                    # data-backed: a runs row would not feed it, its DATA does
                    conn.execute(
                        "INSERT INTO transactions (record_id, canonical_key,"
                        " type, amount_minor, currency, source, first_seen_at)"
                        " VALUES ('r1','k1','Other',100,'USD','IDFC',?)",
                        (recent_utc,))
                else:
                    _run(conn, s["task_name"], hours_ago=0.5)
        checks = dc.check_source_freshness(db_path=self.db_path, now=_now())
        self.assertTrue(all(c.status == "PASS" for c in checks),
                        [(c.name, c.status, c.detail) for c in checks])


class EmptyOkCase(unittest.TestCase):
    """`empty_ok`: does a CLEAN EMPTY SKIP count as the rail being alive?

    Why this exists (2026-08-13): loom_fetch is weekly and its normal healthy
    outcome is '0 new recordings', which run_fetch records via runner.TaskSkip
    -> status 'skipped_empty'. Freshness counted only status='completed', so the
    rail would have read DOWN forever even once working — the wrong-health-
    definition bug that let it sit unnoticed having never run.

    It is opt-in, and the opposite case is why: upwork_scan's TaskSkip means 'no
    tiles extracted on ANY feed', which is a BROKEN scanner. That one must still
    read stale, or the registry would bless exactly the silent zero it exists to
    catch."""

    OPT_IN = ({"key": "loomish", "label": "Weekly fetch", "task_name": "t_loom",
               "max_age_hours": 192, "degrade_kinds": (), "critical": False,
               "backs": "today", "empty_ok": True, "why": "weekly pull"},)
    OPT_OUT = ({"key": "scanish", "label": "Scanner", "task_name": "t_scan2",
                "max_age_hours": 3, "degrade_kinds": (), "critical": True,
                "backs": "pipeline", "why": "the funnel"},)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="src-empty-ok-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _skip_run(self, conn, task, hours_ago):
        """A clean empty-skip exactly as core.runner._finish_run writes it —
        status 'skipped_empty' WITH completed_at set (the run did finish)."""
        ts = (_now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO runs (task_name, started_at, completed_at, status,"
            " shadow) VALUES (?,?,?, 'skipped_empty', 0)", (task, ts, ts))

    def _status(self, sources):
        with db.connect(self.db_path) as conn:
            return sh.source_health(conn, now=_now(),
                                    sources=sources)["sources"][0]

    def test_empty_ok_source_is_fresh_after_a_clean_skip(self):
        with db.connect(self.db_path) as conn:
            self._skip_run(conn, "t_loom", 2)
        s = self._status(self.OPT_IN)
        self.assertEqual(s["status"], "ok", s)
        self.assertIsNotNone(s["last_success"])

    def test_empty_ok_source_still_goes_stale_when_it_stops_skipping(self):
        """empty_ok must not make a source immortal — it advances freshness, it
        does not suspend the deadline."""
        with db.connect(self.db_path) as conn:
            self._skip_run(conn, "t_loom", 400)      # > 192h deadline
        self.assertEqual(self._status(self.OPT_IN)["status"], "stale")

    def test_empty_ok_source_with_only_skips_is_never_down(self):
        """'down' means NEVER produced. A rail that has skipped cleanly has run."""
        with db.connect(self.db_path) as conn:
            for h in (2, 200, 400):
                self._skip_run(conn, "t_loom", h)
        self.assertNotEqual(self._status(self.OPT_IN)["status"], "down")

    def test_source_without_empty_ok_is_NOT_freshened_by_a_skip(self):
        """The guard that keeps this honest: a scanner extracting nothing is
        broken, and its skip must never read as a live run."""
        with db.connect(self.db_path) as conn:
            self._skip_run(conn, "t_scan2", 1)       # well inside its 3h deadline
        s = self._status(self.OPT_OUT)
        self.assertEqual(s["status"], "down", s)     # never a *completed* run
        self.assertIsNone(s["last_success"])

    def test_registry_opt_ins_are_exactly_the_clean_skip_rails(self):
        """Whoever adds empty_ok next must justify it: the ONLY rails whose
        TaskSkip means 'healthy, nothing new' are the sweep (quiet inbox), the
        weekly loom fetch (corpus current), and — added 2026-08-25 with the
        Upwork rails registry — the card sweep (cos_sweep skips 'empty' when no
        card changed, its steady state 2 runs in 3) and the Sunday outcome email
        (a week with no closed proposal to ask about is a clean skip). Added
        2026-10-09 with the sales engine's three agents: the conversation
        reader (no conversation moved since its last reading), the win-back
        drafter (nobody new to draft for this week) and the progress email (it
        already went today) — each skip is the healthy steady state."""
        opted = {s["key"] for s in config.SOURCES if s.get("empty_ok")}
        self.assertEqual(
            opted, {"jarvis_sweep", "jarvis_imap_01", "jarvis_imap_02",
                    "loom_fetch", "cos_sweep", "upwork_outcomes",
                    "upwork_progress", "upwork_conversation_codes",
                    "upwork_reactivation"}, opted)
        # upwork_scan's TaskSkip means a broken feed — it must stay opted out
        scan = next(s for s in config.SOURCES if s["key"] == "scanner")
        self.assertFalse(scan.get("empty_ok"))


if __name__ == "__main__":
    unittest.main()
