"""Tests for modules/upwork/scan_pipeline.py — Chrome and the gateway are
mocked; claiming, scoring flow, shadow intents, and dedup run real code
against a temp v3 DB.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db
from modules.upwork import scan_pipeline as sp
from modules.upwork import scoring


def tile(jid, title="Make.com Automation Build", posted="Posted 2 hours ago",
         budget_line="Hourly: $35.00 - $50.00 - Expert - Est. Time: More"
                     " than 6 months, 30+ hrs/week"):
    return {
        "href": f"https://www.upwork.com/jobs/Make_~02{jid}/",
        "title": title,
        "text": f"{posted}\n•\nProposals: 5 to 10\n{title}\n{budget_line}\n"
                f"Automate things with Make and Airtable.\nSkills\nMake.com\n"
                f"Airtable\nVerified\n\nPayment verified\n\n"
                f"Rating is 4.9 out of 5.\n $12K+ spent\n  USA",
    }


def payload(*tiles_):
    return json.dumps({"url": "https://www.upwork.com/nx/find-work/my-feed",
                       "tile_count": len(tiles_), "tiles": list(tiles_)})


def verdict(score=18):
    return {"score": score, "gated": False,
            "loom_flag": 1 if score >= 22 else 0,
            "breakdown": {"skill_fit": 4, "budget": 4, "client_quality": 2,
                          "competition": 4, "description_quality": 4,
                          "base_total": 22, "bonuses": {"core_stack": 3},
                          "raw_total": score, "recommendation": "bid",
                          "gate": None, "total": score}}


class ScanPipelineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scan-test-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.tmp / "logs"),
            mock.patch.object(config, "LOCK_DIR", self.tmp / "locks"),
            mock.patch.object(sp, "ensure_scan_tab",
                              return_value=(111, 3)),
            mock.patch.object(sp, "navigate_tab"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self, sql, args=()):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, args)]

    def run_scan(self, chrome_payloads, score_map=None):
        """chrome_payloads: one payload per feed call (3 feeds)."""
        score_map = score_map or {}

        def fake_score(row, task_name, db_path=None):
            return verdict(score_map.get(row["id"], 14))

        from core import runner
        with mock.patch.object(sp.feed_bridge, "chrome_eval",
                               side_effect=chrome_payloads), \
             mock.patch.object(scoring, "score_job",
                               side_effect=fake_score), \
             mock.patch.object(scoring, "draft_proposal",
                               return_value="Drafted proposal text."):
            return runner.run_task(
                sp.TASK_NAME, lambda ctx: sp.scan(ctx, _sleep=lambda s: None),
                module="upwork", db_path=self.db_path)


JID_A = "2064908810394200829"
JID_B = "2065069560236358341"


class TestScan(ScanPipelineCase):
    def test_full_run_claims_scores_and_records_intents(self):
        result = self.run_scan(
            [payload(tile(JID_A)), payload(), payload(tile(JID_B))],
            score_map={JID_A: 24, JID_B: 14})
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metrics["claimed"], 2)
        self.assertEqual(result.metrics["scored"], 2)
        self.assertEqual(result.metrics["drafts"], 1)       # only the 24
        self.assertEqual(result.metrics["clickup_intents"], 2)
        self.assertEqual(result.metrics["hot"], 1)

        rows = {r["id"]: r for r in self.rows("SELECT * FROM scored_jobs")}
        self.assertEqual(rows[JID_A]["status"], "Drafted")
        self.assertEqual(rows[JID_A]["score"], 24)
        self.assertEqual(rows[JID_A]["loom_flag"], 1)
        self.assertEqual(rows[JID_B]["status"], "Scored")
        self.assertIsNone(rows[JID_B]["draft_proposal"])

        ledger = self.rows("SELECT * FROM shadow_ledger ORDER BY id")
        clickup = [r for r in ledger if r["target"] == "clickup"]
        email = [r for r in ledger if r["target"] == "email"]
        self.assertEqual(len(clickup), 2)
        self.assertEqual(clickup[0]["diff_status"], "pending")
        hot_payload = json.loads(clickup[0]["payload_json"])
        self.assertEqual(hot_payload["list"], "hot")
        self.assertEqual(len(email), 1)
        self.assertIn(JID_A, json.loads(email[0]["payload_json"])["hot_ids"])

        run = self.rows("SELECT * FROM runs")[0]
        self.assertEqual(run["shadow"], 1)   # upwork is shadowed

    def test_second_run_dedups_and_skips_empty(self):
        self.run_scan([payload(tile(JID_A)), payload(), payload()],
                      score_map={JID_A: 14})
        result = self.run_scan([payload(tile(JID_A)), payload(), payload()])
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metrics["claimed"], 0)
        self.assertEqual(result.metrics["scored"], 0)       # nothing New
        self.assertEqual(len(self.rows(
            "SELECT * FROM scored_jobs")), 1)               # no dup rows
        self.assertEqual(len(self.rows(
            "SELECT * FROM shadow_ledger WHERE target='clickup'")), 1)

    def test_hard_rule_job_skipped_without_model_call(self):
        t = tile(JID_A, title="n8n migration expert")
        calls = []
        with mock.patch.object(scoring, "score_job",
                               side_effect=lambda *a, **k:
                               calls.append(1) or verdict()):
            from core import runner
            with mock.patch.object(sp.feed_bridge, "chrome_eval",
                                   side_effect=[payload(t), payload(),
                                                payload()]):
                result = runner.run_task(
                    sp.TASK_NAME,
                    lambda ctx: sp.scan(ctx, _sleep=lambda s: None),
                    module="upwork", db_path=self.db_path)
        self.assertEqual(calls, [])          # model never invoked
        row = self.rows("SELECT * FROM scored_jobs")[0]
        self.assertEqual(row["status"], "Skipped")
        self.assertEqual(row["hard_rule_skip"], "n8n_exclusion")
        self.assertEqual(result.metrics["skipped"], 1)
        self.assertEqual(result.metrics["clickup_intents"], 0)

    def test_crashed_run_leftover_new_rows_get_scored(self):
        # seed a stray New row (prior crash), then run with empty feeds
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO scored_jobs (id, title, job_url, feed_source,"
                " status, fetched_at, contract_type, hourly_max)"
                " VALUES (?,?,?,?,?,datetime('now'),'Hourly',40)",
                (JID_B, "Leftover", f"https://www.upwork.com/jobs/X_~02{JID_B}/",
                 "my_feed", "New"))
        result = self.run_scan([payload(), payload(), payload()],
                               score_map={JID_B: 13})
        self.assertEqual(result.metrics["scored"], 1)
        self.assertEqual(self.rows("SELECT status FROM scored_jobs")[0]
                         ["status"], "Scored")

    def test_all_empty_feeds_skip_empty(self):
        result = self.run_scan([payload(), payload(), payload()])
        self.assertEqual(result.status, "skipped_empty")


class TestTabScripts(unittest.TestCase):
    def test_find_script_matches_marker(self):
        self.assertIn(sp.MARKER, sp._FIND_TAB)
        self.assertIn("return \"\"", sp._FIND_TAB)

    def test_create_script_restores_focus(self):
        self.assertIn("set prior to active tab index", sp._CREATE_TAB)
        self.assertIn("set active tab index of front window to prior",
                      sp._CREATE_TAB)
        self.assertIn(sp.MARKER, sp._CREATE_TAB)

    def test_navigate_does_not_use_active_tab(self):
        with mock.patch.object(sp, "_osascript") as osa:
            sp.navigate_tab((42, 7), "https://x.example/")
        script = osa.call_args[0][0]
        self.assertIn("tab 7 of window id 42", script)
        self.assertNotIn("active tab", script)


if __name__ == "__main__":
    unittest.main()
