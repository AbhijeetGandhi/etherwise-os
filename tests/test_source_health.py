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
from datetime import datetime, timedelta
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
        with db.connect(self.db_path) as conn:
            for s in config.SOURCES:
                _run(conn, s["task_name"], hours_ago=0.5)
        checks = dc.check_source_freshness(db_path=self.db_path, now=_now())
        self.assertTrue(all(c.status == "PASS" for c in checks),
                        [(c.name, c.status, c.detail) for c in checks])


if __name__ == "__main__":
    unittest.main()
