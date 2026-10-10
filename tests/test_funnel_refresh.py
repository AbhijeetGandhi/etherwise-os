"""The nightly funnel refresh (2026-10-11, his "yes start number 1 now").

Pins: it runs the read-only pull's messages and funnel steps, then ties every
proposal sent inside a test's window to its version (experiments.link_sent had
no caller before this task, so T1 could never count a sent proposal); a
drafted job's proposal gets its proposal id and the version its letter reads
as; a proposal with no draft is 'detected', never compared; a refused sign-in
fails the run without touching the tests; the schedule exists and is
registered, and the research fetchers it runs are tracked in scripts/funnel.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db, runner
from modules.upwork import experiments as ex
from modules.upwork import funnel_refresh as fr

ROOT = Path(__file__).resolve().parents[1]
B_LETTER = ("Your inbox is the bottleneck, not your team. Every routine question waits for a human.\n\n"
            "I run Etherwise and build these systems.")
A_LETTER = "I have built 3,000+ automations. My agency Etherwise does this daily.\n\nYour inbox needs it."


class RefreshCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="refresh-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.p = [mock.patch.object(config, "LOG_DIR", self.tmp / "logs"),
                  mock.patch.object(config, "LOCK_DIR", self.tmp / "locks")]
        for x in self.p:
            x.start()
        with db.connect(self.db_path) as conn:
            ex.seed_t1(conn)
            conn.execute("INSERT INTO experiment_assignments (experiment_id, job_id, arm, via)"
                         " VALUES (?, 'J1', 'B', 'draft')", (ex.T1_ID,))
            for pid, job, sent, letter in (("P1", "J1", "2026-10-10T10:00:00Z", B_LETTER),
                                           ("P2", "J2", "2026-10-10T11:00:00Z", A_LETTER),
                                           ("P0", "J0", "2026-10-01T11:00:00Z", A_LETTER)):
                conn.execute("INSERT INTO proposal_outcomes (proposal_id, sent_dt, kind, job_id, letter, source)"
                             " VALUES (?, ?, 'cold', ?, ?, 'test')", (pid, sent, job, letter))

    def tearDown(self):
        for x in self.p:
            x.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def go(self, code, steps=None):
        return runner.run_task(fr.TASK_NAME, lambda ctx: fr.run(ctx, _pull_fn=lambda c: (code, steps or {})),
                               module="upwork", db_path=self.db_path)


class TestRefresh(RefreshCase):
    def test_a_sent_proposal_is_tied_to_its_test_version(self):
        r = self.go(0, {"messages": {"rooms": 2}, "funnel": {"stored_now": 3}})
        self.assertEqual(r.status, "completed")
        self.assertEqual(r.metrics["tests"], {"linked": 1, "detected": 1, "rewritten": 0})
        with db.connect(self.db_path) as conn:
            rows = {x["job_id"]: dict(x) for x in conn.execute("SELECT * FROM experiment_assignments")}
        self.assertEqual((rows["J1"]["proposal_id"], rows["J1"]["sent_arm"], rows["J1"]["via"]), ("P1", "B", "draft"))
        self.assertEqual((rows["J2"]["arm"], rows["J2"]["via"]), ("A", "detected"))   # no draft: counted beside
        self.assertNotIn("J0", rows)                                                     # sent before the test began
        # the next night changes nothing
        r = self.go(0)
        self.assertEqual(r.metrics["tests"], {"linked": 0, "detected": 0, "rewritten": 0})

    def test_a_refused_sign_in_fails_the_run_and_leaves_the_tests_alone(self):
        with self.assertRaises(RuntimeError):
            self.go(2, {"messages": {"stopped": "sign-in refused"}})
        with db.connect(self.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM experiment_assignments WHERE proposal_id IS NOT NULL").fetchone()[0]
        self.assertEqual(n, 0)

    def test_the_pull_is_asked_for_messages_and_funnel_only(self):
        self.assertEqual(fr.STEPS, ("messages", "funnel"))


class TestWiring(unittest.TestCase):
    def test_scheduled_and_registered(self):
        label = "io.etherwise.v3.upwork-funnel-refresh"
        self.assertTrue((ROOT / "launchd" / f"{label}.plist").is_file())
        src = {s["key"]: s for s in config.SOURCES}["upwork_funnel_refresh"]
        self.assertEqual((src["task_name"], src["launchd_label"], src["catch_up"]), (fr.TASK_NAME, label, True))

    def test_the_research_fetchers_are_tracked_and_run_from_scripts_funnel(self):
        for f in ("fetch", "fetch_rooms", "fetch_jobs", "build_dataset", "features", "model", "export_rows"):
            self.assertTrue((ROOT / "scripts" / "funnel" / f"{f}.py").is_file(), f)
        src = (ROOT / "scripts" / "upwork_pull_all.py").read_text()
        self.assertNotIn('_run("var/prop/', src)
        for f in ("fetch", "fetch_rooms", "fetch_jobs", "build_dataset", "features", "export_rows"):
            self.assertIn(f'_run("scripts/funnel/{f}.py")', src)
        for f in ("fetch_rooms", "export_rows"):
            self.assertIn('sys.path.insert(0, "scripts/funnel")', (ROOT / "scripts" / "funnel" / f"{f}.py").read_text())


if __name__ == "__main__":
    unittest.main()
