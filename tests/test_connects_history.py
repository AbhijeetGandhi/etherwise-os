"""Connects-history scrape rail — parser, kind classifier, dedup, session
guard, idempotent upserts. Chrome is mocked; DB logic runs real."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from core import config, db
from modules.upwork import connects_history as ch

TODAY = date(2026, 7, 7)


def dom_row(d, action, connects, href=None):
    return {"date": d, "action": action, "connects": connects, "href": href}


class TestParseRows(unittest.TestCase):
    def test_kinds_dates_and_job_links(self):
        rows = ch.parse_rows([
            dom_row("Today", "Availability Badge", "-1\nSpent 1 Connect"),
            dom_row("Today", "Monthly Connects renewal", "+100\nAdded 100 Connects"),
            dom_row("July 2, 2026",
                    "You've been outbid to boost your proposal\nHealthcare Workflow",
                    "+235\nAdded 235 Connects", "/jobs/~022070238449191845747/"),
            dom_row("June 30, 2026", "Job cancelled\nAirtable expert",
                    "+9\nAdded 9 Connects", "/jobs/~022067035114629810313/"),
            dom_row("July 1, 2026", "Submitted a proposal\nAI Automation build",
                    "-16\nSpent 16 Connects", "/jobs/~02999/"),
        ], TODAY)
        by = {r["kind"]: r for r in rows}
        self.assertEqual(by["badge"]["event_date"], "2026-07-07")   # Today anchor
        self.assertEqual(by["badge"]["connects_delta"], -1)
        self.assertEqual(by["renewal"]["connects_delta"], 100)
        self.assertEqual(by["boost_refund"]["connects_delta"], 235)
        self.assertEqual(by["boost_refund"]["job_id"], "2070238449191845747")
        self.assertEqual(by["refund"]["job_id"], "2067035114629810313")
        # the one that matters: spend WITH a job link = an application
        self.assertEqual(by["applied"]["connects_delta"], -16)
        self.assertEqual(by["applied"]["job_id"], "999")
        self.assertEqual(by["applied"]["job_title"], "AI Automation build")

    def test_identical_same_day_rows_dedupe_by_occurrence(self):
        rows = ch.parse_rows([
            dom_row("July 3, 2026", "Availability Badge", "-1"),
            dom_row("July 3, 2026", "Availability Badge", "-1"),
        ], TODAY)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["id"], rows[1]["id"])
        # stable across re-parses (daily overlapping scrapes dedupe cleanly)
        again = ch.parse_rows([
            dom_row("July 3, 2026", "Availability Badge", "-1"),
            dom_row("July 3, 2026", "Availability Badge", "-1"),
        ], TODAY)
        self.assertEqual([r["id"] for r in rows], [r["id"] for r in again])

    def test_garbage_rows_skipped(self):
        rows = ch.parse_rows([
            dom_row(None, "x", "-1"), dom_row("Today", "", "-1"),
            dom_row("Someday", "x", "-1"), dom_row("Today", "x", "no-number"),
        ], TODAY)
        self.assertEqual(rows, [])


class TestScrapeTask(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="connects-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.tmp / "logs"),
            mock.patch.object(config, "LOCK_DIR", self.tmp / "locks"),
            mock.patch.dict(config.SHADOW_MODE, {"upwork": True}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, payload):
        from core import runner
        from modules.upwork import scan_pipeline as sp
        with mock.patch.object(sp, "ensure_scan_tab", return_value=(1, 1)), \
                mock.patch.object(sp, "navigate_tab"), \
                mock.patch.object(ch.feed_bridge, "chrome_eval",
                                  return_value=json.dumps(payload)):
            return runner.run_task(
                ch.TASK_NAME, lambda c: ch.scrape(c, _sleep=lambda s: None),
                module="upwork", db_path=self.db_path)

    def test_upsert_idempotent(self):
        payload = {"url": "https://www.upwork.com/nx/plans/connects/history/#x",
                   "rows": [dom_row("July 2, 2026", "Submitted a proposal\nJob X",
                                    "-12", "/jobs/~02123/"),
                            dom_row("July 2, 2026", "Availability Badge", "-1")]}
        r1 = self._run(payload)
        self.assertEqual(r1.metrics["new"], 2)
        r2 = self._run(payload)                    # overlapping re-scrape
        self.assertEqual(r2.metrics["new"], 0)
        with db.connect(self.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) c FROM connects_history").fetchone()["c"]
            k = conn.execute("SELECT kind FROM connects_history WHERE job_id='123'"
                             ).fetchone()["kind"]
        self.assertEqual(n, 2)
        self.assertEqual(k, "applied")

    def test_off_page_fails_loud(self):
        payload = {"url": "https://www.upwork.com/ab/account-security/login",
                   "rows": []}
        with self.assertRaises(RuntimeError):
            self._run(payload)
        with db.connect(self.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) c FROM connects_history").fetchone()["c"]
        self.assertEqual(n, 0)                     # nothing ingested


if __name__ == "__main__":
    unittest.main()
