"""Anomalies heal on the next clean run (2026-11-01, the cockpit audit).

3,139 anomalies were open on 30 Oct, 2,280 'critical'. Nothing ever resolved
one, the critical-interrupt drain had never run, and `catch_up_gave_up` alone
was 1,488 rows because its one-time-loud guard searched for a key shape the
writer never wrote. Pinned here:
  * a later clean run (completed OR skipped_empty) of the same task resolves
    every anomaly its earlier runs raised; a different task's rows, catch_up's
    rows and unbound rows are left alone;
  * catch-up files ONE give-up per source key, and the guard then finds it;
  * catch-up re-arms a give-up the moment the target runs cleanly.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from core import config, db, runner


def _now():
    return datetime.now(config.TZ)


class SelfHeal(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="heal-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, task, status="failed"):
        with db.connect(self.db_path) as conn:
            cur = conn.execute("INSERT INTO runs (task_name, started_at, status, shadow)"
                               " VALUES (?,?,?,0)", (task, _now().isoformat(), status))
            return cur.lastrowid

    def _anom(self, task, run_id, kind="task_failure", severity="critical"):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO anomalies (task_name, kind, detail_json, severity,"
                         " run_id) VALUES (?,?,?,?,?)",
                         (task, kind, "{}", severity, run_id))

    def _open(self):
        with db.connect(self.db_path) as conn:
            return [tuple(r) for r in conn.execute(
                "SELECT task_name, kind FROM anomalies WHERE resolved_at IS NULL"
                " ORDER BY id")]

    def test_a_clean_run_resolves_its_own_tasks_earlier_anomalies_only(self):
        r1 = self._run("upwork_hiring")
        self._anom("upwork_hiring", r1)
        self._anom("upwork_hiring", r1, kind="financials_truncated", severity="warn")
        r2 = self._run("upwork_scan")
        self._anom("upwork_scan", r2)
        self._anom("catch_up", r2, kind="catch_up_gave_up")
        self._anom("cockpit", None, kind="stale_intent", severity="warn")
        r3 = self._run("upwork_hiring", status="completed")
        healed = runner._self_heal(self.db_path, "upwork_hiring", r3)
        self.assertEqual(healed, 2)
        self.assertEqual(self._open(), [("upwork_scan", "task_failure"),
                                        ("catch_up", "catch_up_gave_up"),
                                        ("cockpit", "stale_intent")])
        # catch_up never heals its own (they are about other sources)
        r4 = self._run("catch_up", status="completed")
        self.assertEqual(runner._self_heal(self.db_path, "catch_up", r4), 0)

    def test_run_task_heals_on_completed_and_on_a_clean_skip(self):
        r1 = self._run("t_demo")
        self._anom("t_demo", r1)
        with mock.patch.dict(config.SHADOW_MODE, {"demo": True}):
            runner.run_task("t_demo", lambda ctx: {"ok": 1}, module="demo",
                            db_path=self.db_path)
        self.assertEqual(self._open(), [])
        r2 = self._run("t_demo")
        self._anom("t_demo", r2)

        def skip(ctx):
            raise runner.TaskSkip("nothing new")
        with mock.patch.dict(config.SHADOW_MODE, {"demo": True}):
            runner.run_task("t_demo", skip, module="demo", db_path=self.db_path)
        self.assertEqual(self._open(), [])

    def test_a_failed_run_does_not_heal(self):
        r1 = self._run("t_demo")
        self._anom("t_demo", r1)

        def boom(ctx):
            raise ValueError("no")
        with mock.patch.dict(config.SHADOW_MODE, {"demo": True}):
            with self.assertRaises(ValueError):
                runner.run_task("t_demo", boom, module="demo", db_path=self.db_path)
        kinds = [k for _, k in self._open()]
        self.assertEqual(kinds, ["task_failure", "task_failure"])   # the old one + the new one


class CatchUpGiveUp(unittest.TestCase):
    """Imports the bin the way tests/test_catch_up.py does."""

    def setUp(self):
        import importlib.util
        import importlib.machinery
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_loader(
            "catch_up_bin_heal",
            importlib.machinery.SourceFileLoader("catch_up_bin_heal", str(root / "bin" / "catch-up")))
        self.cu = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cu)
        self.tmp = Path(tempfile.mkdtemp(prefix="cu-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.src = ({"key": "rail", "label": "Rail", "task_name": "t_rail",
                     "max_age_hours": 26, "degrade_kinds": (), "critical": True,
                     "backs": "x", "launchd_label": "io.test.rail", "catch_up": True,
                     "why": "w"},)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_give_up_is_found_by_the_guard_and_re_armed_on_recovery(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO anomalies (task_name, kind, detail_json, severity)"
                         " VALUES ('catch_up','catch_up_gave_up',?, 'critical')",
                         (json.dumps({"key": "rail", "why": "failing"}),))
            self.assertTrue(self.cu._already_open(conn, "catch_up_gave_up", "rail"))
            # nothing has recovered: stays open
            self.assertEqual(self.cu.clear_recovered(conn, sources=self.src), [])
            ts = (_now() + timedelta(minutes=5)).isoformat(timespec="seconds")
            conn.execute("INSERT INTO runs (task_name, started_at, completed_at, status,"
                         " shadow) VALUES ('t_rail', ?, ?, 'completed', 0)", (ts, ts))
            self.assertEqual(self.cu.clear_recovered(conn, sources=self.src), ["rail"])
            self.assertFalse(self.cu._already_open(conn, "catch_up_gave_up", "rail"))

    def test_the_old_sources_list_shape_is_still_understood(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO anomalies (task_name, kind, detail_json, severity)"
                         " VALUES ('catch_up','catch_up_gave_up',?, 'critical')",
                         (json.dumps({"sources": ["rail"], "detail": "x"}),))
            ts = (_now() + timedelta(minutes=5)).isoformat(timespec="seconds")
            conn.execute("INSERT INTO runs (task_name, started_at, completed_at, status,"
                         " shadow) VALUES ('t_rail', ?, ?, 'completed', 0)", (ts, ts))
            self.assertEqual(self.cu.clear_recovered(conn, sources=self.src), ["rail"])


if __name__ == "__main__":
    unittest.main()
