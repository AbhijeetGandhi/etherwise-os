"""Score bands are ONE config source (Upwork chat decision 1, 2026-08-25).

Before this, the four cuts (8 / 12 / 16 / 22) were literals in five readers,
so "recalibrate 16" would have moved the drafts and the digest but not the
ClickUp routing, the cockpit funnel or the cutover scorecard. These tests pin:
the tuple and its order, the band arithmetic, that every reader follows a
PATCHED config value (i.e. reads config at call time, not a copied literal),
and — by grepping the readers' source — that no band literal crept back in.
The VALUES are pinned too: a recalibration is a deliberate, reviewed edit of
config.py and of test_values_unchanged_until_the_scorecard_says_otherwise,
never a side effect of another change.
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db
from core.evals import scoring_eval
from modules.cockpit import data

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import clickup_push  # noqa: E402

READERS = (
    "scripts/clickup_push.py",
    "modules/upwork/scan_pipeline.py",
    "modules/cockpit/data.py",
    "scripts/mcp_shadow_report.py",
    "core/evals/scoring_eval.py",
)
# a score/total compared against one of the four cut values as a literal
_LITERAL_BAND = re.compile(
    r"(?:\b(?:score|total)\b\s*(?:>=|<=|<|>)\s*(?:8|12|16|22)\b)"
    r"|(?:\b(?:8|12|16|22)\b\s*(?:>=|<=|<|>)\s*\b(?:score|total)\b)"
    r"|(?:BETWEEN\s+(?:8|12|16|22)\s+AND\s+(?:11|15|21)\b)"
    r"|(?:>=\s*(?:16|22)\b)")


class TestConfigBands(unittest.TestCase):
    def test_one_tuple_named_and_strictly_increasing(self):
        self.assertEqual(config.SCORE_BANDS,
                         (config.SCORE_FLOOR, config.SCORE_STANDARD,
                          config.HOT_LEAD_THRESHOLD, config.LOOM_FLAG_THRESHOLD))
        self.assertEqual(list(config.SCORE_BANDS), sorted(set(config.SCORE_BANDS)))

    def test_values_unchanged_until_the_scorecard_says_otherwise(self):
        """Decision 1: single-source FIRST, values only after the MCP scorecard
        measures the drift. Changing these numbers is the recalibration commit
        and must edit this test on purpose."""
        self.assertEqual(config.SCORE_BANDS, (8, 12, 16, 22))

    def test_band_arithmetic_and_labels(self):
        expect = {None: "unscored", 0: "below", 7: "below", 8: "low", 11: "low",
                  12: "standard", 15: "standard", 16: "hot", 21: "hot",
                  22: "loom", 28: "loom"}
        for score, band in expect.items():
            self.assertEqual(config.score_band(score), band, score)
        self.assertEqual(config.score_band_label("below"), "<8")
        self.assertEqual(config.score_band_label("low"), "8-11")
        self.assertEqual(config.score_band_label("standard"), "12-15")
        self.assertEqual(config.score_band_label("hot"), "16-21")
        self.assertEqual(config.score_band_label("loom"), "22+")

    def test_band_helpers_follow_a_patched_cut(self):
        with mock.patch.object(config, "HOT_LEAD_THRESHOLD", 18):
            self.assertEqual(config.score_band(17), "standard")
            self.assertEqual(config.score_band(18), "hot")
            self.assertEqual(config.score_band_label("hot"), "18-21")
            self.assertEqual(config.score_band_label("standard"), "12-17")


class TestReadersFollowConfig(unittest.TestCase):
    def test_clickup_routing_reads_config_at_call_time(self):
        with mock.patch.object(config, "HOT_LEAD_THRESHOLD", 18):
            self.assertEqual(clickup_push.list_for_score(17, invite=False),
                             clickup_push.LIST_STANDARD)
            self.assertEqual(clickup_push.list_for_score(18, invite=False),
                             clickup_push.LIST_HOT)
        with mock.patch.object(config, "SCORE_FLOOR", 10):
            self.assertIsNone(clickup_push.list_for_score(9, invite=False))
            self.assertEqual(clickup_push.list_for_score(10, invite=False),
                             clickup_push.LIST_LOW)
            self.assertIn("<10", clickup_push._guard(
                {"id": "2064484440376044477", "status": "Scored", "score": 9}))
        # unchanged live behaviour at today's values
        self.assertEqual(clickup_push.list_for_score(16, invite=False),
                         clickup_push.LIST_HOT)
        self.assertEqual(clickup_push.list_for_score(12, invite=False),
                         clickup_push.LIST_STANDARD)
        self.assertEqual(clickup_push.list_for_score(8, invite=False),
                         clickup_push.LIST_LOW)
        self.assertIsNone(clickup_push.list_for_score(7, invite=False))

    def test_clickup_priority_reads_config_at_call_time(self):
        row = {"score": 22, "score_breakdown_json": "{}"}
        self.assertEqual(clickup_push.priority_for(row), 1)
        self.assertEqual(clickup_push.priority_for({"score": 16}), 2)
        self.assertEqual(clickup_push.priority_for({"score": 15}), 3)
        with mock.patch.object(config, "LOOM_FLAG_THRESHOLD", 24):
            self.assertEqual(clickup_push.priority_for(row), 2)
        invite = {"score": 9,
                  "score_breakdown_json": '{"bonuses": {"invite": 4}}'}
        self.assertEqual(clickup_push.priority_for(invite), 1)

    def test_eval_band_labels_derive_from_config(self):
        self.assertEqual(scoring_eval.band(16), "16-21")
        self.assertEqual(scoring_eval.band(None), "?")
        with mock.patch.object(config, "LOOM_FLAG_THRESHOLD", 24):
            self.assertEqual(scoring_eval.band(23), "16-23")
            self.assertEqual(scoring_eval.band(24), "24+")


class TestCockpitBands(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bands-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.today = "2026-08-25"
        with db.connect(self.db_path) as conn:
            for jid, score in (("2064484440376044471", 7), ("2064484440376044472", 8),
                               ("2064484440376044473", 12), ("2064484440376044474", 16),
                               ("2064484440376044475", 22)):
                conn.execute(
                    "INSERT INTO scored_jobs (id, title, score, status,"
                    " fetched_at, first_scored_at, job_url) VALUES"
                    " (?,?,?,'Scored','2026-08-24T10:00:00+00:00',"
                    " '2026-08-24 10:05:00', ?)",
                    (jid, f"job {score}", score,
                     f"https://www.upwork.com/jobs/x_~02{jid}/"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_facts_and_bands_carry_the_config_cuts(self):
        facts = data.pipeline_facts(self.db_path, self.today)
        self.assertEqual(facts["score_floor"], config.SCORE_FLOOR)
        self.assertEqual(facts["hot_threshold"], config.HOT_LEAD_THRESHOLD)
        bands = data.pipeline(self.db_path, self.today)["bands"]
        self.assertEqual((bands["hot"], bands["standard"], bands["low"]),
                         (2, 1, 1))
        with mock.patch.object(config, "HOT_LEAD_THRESHOLD", 20), \
                mock.patch.object(config, "SCORE_FLOOR", 10):
            bands = data.pipeline(self.db_path, self.today)["bands"]
            self.assertEqual((bands["hot"], bands["standard"], bands["low"]),
                             (1, 2, 0))
            self.assertEqual(
                data.pipeline_facts(self.db_path, self.today)["score_floor"], 10)

    def test_scored_and_triage_drills_use_the_config_cuts(self):
        wk = "2026-08-24"                                     # a Monday
        scored = data.pipeline_rows(self.db_path, stage="scored", week=wk,
                                    _today=self.today)
        self.assertEqual(scored["total"], 4)                  # >= 8
        triage = data.pipeline_rows(self.db_path, stage="triage",
                                    _today=self.today)
        self.assertEqual({r["score"] for r in triage["rows"]}, {16, 22})
        with mock.patch.object(config, "SCORE_FLOOR", 12), \
                mock.patch.object(config, "HOT_LEAD_THRESHOLD", 22):
            scored = data.pipeline_rows(self.db_path, stage="scored", week=wk,
                                        _today=self.today)
            self.assertEqual(scored["total"], 3)
            triage = data.pipeline_rows(self.db_path, stage="triage",
                                        _today=self.today)
            self.assertEqual({r["score"] for r in triage["rows"]}, {22})


class TestNoLiteralBands(unittest.TestCase):
    def test_readers_carry_no_band_literals(self):
        """The grep that keeps decision 1 true: none of the five readers may
        compare a score against 8/12/16/22 written as a number."""
        offenders = []
        for rel in READERS:
            for n, line in enumerate((ROOT / rel).read_text().splitlines(), 1):
                if _LITERAL_BAND.search(line):
                    offenders.append(f"{rel}:{n}: {line.strip()}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_grep_still_bites(self):
        self.assertTrue(_LITERAL_BAND.search("if total >= 16:"))
        self.assertTrue(_LITERAL_BAND.search("SUM(score>=12 AND score<16)"))
        self.assertTrue(_LITERAL_BAND.search("score BETWEEN 8 AND 11"))
        self.assertFalse(_LITERAL_BAND.search(
            "if total >= config.HOT_LEAD_THRESHOLD:"))


if __name__ == "__main__":
    unittest.main()
