"""Score bands are ONE config source (Upwork chat decision 1, 2026-08-25).

Before this, the four cuts (8 / 12 / 16 / 22 at the time) were literals in five readers,
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
# a score/total compared against a cut value written as a literal — the
# historical cuts (16) as well as the live ones, so a reader can neither keep
# an old number nor freeze a new one
_LITERAL_BAND = re.compile(
    r"(?:\b(?:score|total)\b\s*(?:>=|<=|<|>)\s*(?:8|12|16|20|22)\b)"
    r"|(?:\b(?:8|12|16|20|22)\b\s*(?:>=|<=|<|>)\s*\b(?:score|total)\b)"
    r"|(?:BETWEEN\s+(?:8|12|16|20|22)\s+AND\s+(?:11|15|19|21)\b)"
    r"|(?:>=\s*(?:16|20|22)\b)")


class TestConfigBands(unittest.TestCase):
    def test_one_tuple_named_and_strictly_increasing(self):
        self.assertEqual(config.SCORE_BANDS,
                         (config.SCORE_FLOOR, config.SCORE_STANDARD,
                          config.HOT_LEAD_THRESHOLD, config.LOOM_FLAG_THRESHOLD))
        self.assertEqual(list(config.SCORE_BANDS), sorted(set(config.SCORE_BANDS)))

    def test_values_are_the_decided_ones(self):
        """Decision 1 (2026-08-25): single-source FIRST, values only after the
        evidence. Re-cut 2026-09-08 for scorer v3.0 — the score is on a new
        scale (skill_fit x4 + budget + client_quality + two bonuses), so these
        four numbers moved together with the formula, re-derived by re-scoring
        4,343 rows of history arithmetically. RE-CUT AGAIN 2026-09-09 after the
        skill-fit halving bug: every score rose 4-8 points, so 16 stopped
        meaning what it was chosen to mean. 23 is the same operating point on
        the corrected scale (~87 hot cards a week, 89% of his proposals), not a
        new decision. Changing them again is a decision, and it must edit this
        test on purpose."""
        self.assertEqual(config.SCORE_BANDS, (19, 21, 23, 28))

    def test_band_arithmetic_and_labels(self):
        expect = {None: "unscored", 0: "below", 18: "below", 19: "low",
                  20: "low", 21: "standard", 22: "standard", 23: "hot",
                  27: "hot", 28: "loom", 35: "loom"}
        for score, band in expect.items():
            self.assertEqual(config.score_band(score), band, score)
        self.assertEqual(config.score_band_label("below"), "<19")
        self.assertEqual(config.score_band_label("low"), "19-20")
        self.assertEqual(config.score_band_label("standard"), "21-22")
        self.assertEqual(config.score_band_label("hot"), "23-27")
        self.assertEqual(config.score_band_label("loom"), "28+")

    def test_band_helpers_follow_a_patched_cut(self):
        with mock.patch.object(config, "HOT_LEAD_THRESHOLD", 25):
            self.assertEqual(config.score_band(24), "standard")
            self.assertEqual(config.score_band(25), "hot")
            self.assertEqual(config.score_band_label("hot"), "25-27")
            self.assertEqual(config.score_band_label("standard"), "21-24")


class TestReadersFollowConfig(unittest.TestCase):
    def test_clickup_routing_reads_config_at_call_time(self):
        with mock.patch.object(config, "HOT_LEAD_THRESHOLD", 25):
            self.assertEqual(clickup_push.list_for_score(24, invite=False),
                             clickup_push.LIST_STANDARD)
            self.assertEqual(clickup_push.list_for_score(25, invite=False),
                             clickup_push.LIST_HOT)
        with mock.patch.object(config, "SCORE_FLOOR", 17):
            self.assertIsNone(clickup_push.list_for_score(16, invite=False))
            self.assertEqual(clickup_push.list_for_score(17, invite=False),
                             clickup_push.LIST_LOW)
            self.assertIn("<17", clickup_push._guard(
                {"id": "2064484440376044477", "status": "Scored", "score": 16}))
        # live behaviour at today's values (v3.0.1 scale): 23 is the hot bar
        self.assertEqual(clickup_push.list_for_score(23, invite=False),
                         clickup_push.LIST_HOT)
        self.assertEqual(clickup_push.list_for_score(30, invite=False),
                         clickup_push.LIST_HOT)
        self.assertEqual(clickup_push.list_for_score(21, invite=False),
                         clickup_push.LIST_STANDARD)
        self.assertEqual(clickup_push.list_for_score(19, invite=False),
                         clickup_push.LIST_LOW)
        self.assertIsNone(clickup_push.list_for_score(18, invite=False))

    def test_clickup_priority_reads_config_at_call_time(self):
        row = {"score": 28, "score_breakdown_json": "{}"}
        self.assertEqual(clickup_push.priority_for(row), 1)
        self.assertEqual(clickup_push.priority_for({"score": 24}), 2)
        self.assertEqual(clickup_push.priority_for({"score": 20}), 3)
        with mock.patch.object(config, "LOOM_FLAG_THRESHOLD", 30):
            self.assertEqual(clickup_push.priority_for(row), 2)
        invite = {"score": 9,
                  "score_breakdown_json": '{"bonuses": {"invite": 4}}'}
        self.assertEqual(clickup_push.priority_for(invite), 1)

    def test_eval_band_labels_derive_from_config(self):
        self.assertEqual(scoring_eval.band(25), "23-27")
        self.assertEqual(scoring_eval.band(22), "21-22")
        self.assertEqual(scoring_eval.band(None), "?")
        with mock.patch.object(config, "LOOM_FLAG_THRESHOLD", 32):
            self.assertEqual(scoring_eval.band(31), "23-31")
            self.assertEqual(scoring_eval.band(32), "32+")


class TestCockpitBands(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bands-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.today = "2026-08-25"
        with db.connect(self.db_path) as conn:
            # v3.0.1 cuts (19/21/23/28): below · low · standard · hot · loom
            for jid, score in (("2064484440376044471", 18), ("2064484440376044472", 19),
                               ("2064484440376044473", 21), ("2064484440376044474", 23),
                               ("2064484440376044475", 28)):
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
        with mock.patch.object(config, "HOT_LEAD_THRESHOLD", 27), \
                mock.patch.object(config, "SCORE_FLOOR", 17):
            bands = data.pipeline(self.db_path, self.today)["bands"]
            self.assertEqual((bands["hot"], bands["standard"], bands["low"]),
                             (1, 2, 2))
            self.assertEqual(
                data.pipeline_facts(self.db_path, self.today)["score_floor"], 17)

    def test_scored_and_triage_drills_use_the_config_cuts(self):
        wk = "2026-08-24"                                     # a Monday
        scored = data.pipeline_rows(self.db_path, stage="scored", week=wk,
                                    _today=self.today)
        self.assertEqual(scored["total"], 4)                  # >= SCORE_FLOOR 19
        triage = data.pipeline_rows(self.db_path, stage="triage",
                                    _today=self.today)
        self.assertEqual({r["score"] for r in triage["rows"]}, {23, 28})
        with mock.patch.object(config, "SCORE_FLOOR", 21), \
                mock.patch.object(config, "HOT_LEAD_THRESHOLD", 28):
            scored = data.pipeline_rows(self.db_path, stage="scored", week=wk,
                                        _today=self.today)
            self.assertEqual(scored["total"], 3)
            triage = data.pipeline_rows(self.db_path, stage="triage",
                                        _today=self.today)
            self.assertEqual({r["score"] for r in triage["rows"]}, {28})


class TestClickUpListIds(unittest.TestCase):
    """The five ClickUp list ids are config-owned (2026-08-25); the script
    derives its LIST_* names and the reverse map from config.CLICKUP_LISTS."""

    def test_script_lists_come_from_config(self):
        self.assertEqual(clickup_push.LIST_ICP, config.CLICKUP_LISTS["icp"])
        self.assertEqual(clickup_push.LIST_HOT, config.CLICKUP_LISTS["hot"])
        self.assertEqual(clickup_push.LIST_STANDARD, config.CLICKUP_LISTS["standard"])
        self.assertEqual(clickup_push.LIST_LOW, config.CLICKUP_LISTS["low"])
        self.assertEqual(clickup_push.LIST_INVITES, config.CLICKUP_LISTS["invites"])
        self.assertEqual(set(clickup_push.LIST_NAMES.values()),
                         set(config.CLICKUP_LISTS))
        self.assertEqual(len(set(config.CLICKUP_LISTS.values())), 5)   # distinct ids

    def test_script_carries_no_list_id_literal(self):
        src = (ROOT / "scripts/clickup_push.py").read_text()
        for list_id in config.CLICKUP_LISTS.values():
            self.assertNotIn(list_id, src, list_id)

    def test_routing_vocabulary_is_complete(self):
        # every list the router can return has a name for the intent ledger
        for total, invite, icp in ((25, False, None), (21, False, None),
                                   (19, False, None), (9, True, None),
                                   (25, False, "icp")):
            list_id = clickup_push.list_for_score(total, invite=invite, icp_tier=icp)
            self.assertIn(list_id, clickup_push.LIST_NAMES)


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
        self.assertTrue(_LITERAL_BAND.search("if total >= 20:"))
        self.assertTrue(_LITERAL_BAND.search("SUM(score>=12 AND score<16)"))
        self.assertTrue(_LITERAL_BAND.search("score BETWEEN 8 AND 11"))
        self.assertFalse(_LITERAL_BAND.search(
            "if total >= config.HOT_LEAD_THRESHOLD:"))


if __name__ == "__main__":
    unittest.main()
