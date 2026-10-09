"""Tests — "the game" (modules/upwork/experiments.py; migration 039).

Abhijeet, 2026-10-09: "make this a game ... we try it for the next week ...
then we see what the results were". Approved rules: every other draft gets the
new version; called at ~40 per side + 7 days, at most 6 weeks; plain-word
verdicts. Pins: T1 seeds once; versions alternate and stick to a job; only
cold jobs and only with an explicit database; version B's instruction reaches
the draft on every rung and A's draft is untouched; the assignment is written
only when a draft exists; a sent letter is recognised; a rewritten letter is
flagged, not moved; the chance arithmetic is exact; the verdict says what the
numbers allow and no more; the ClickUp card tells him which version he holds.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import db
from modules.upwork import evidence, experiments as ex, scoring


def _clickup():
    p = Path(__file__).resolve().parents[1] / "scripts" / "clickup_push.py"
    spec = importlib.util.spec_from_file_location("clickup_push_t", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="exp-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        with db.connect(self.db_path) as conn:
            ex.seed_t1(conn)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestSeedAndAlternate(Base):
    def test_t1_seeds_once_with_two_versions(self):
        with db.connect(self.db_path) as conn:
            self.assertFalse(ex.seed_t1(conn))
            t = ex.running(conn, "cold")
        self.assertEqual(t["id"], ex.T1_ID)
        self.assertEqual(sorted(t["arms"]), ["A", "B"])
        self.assertIsNone(t["arms"]["A"]["draft_instruction"])
        self.assertIn("client's situation", t["arms"]["B"]["draft_instruction"])
        self.assertEqual((t["metric"], t["min_per_arm"], t["wait_days"], t["max_weeks"]),
                         ("replied", 40, 7, 6))

    def test_versions_alternate_and_stick_to_a_job(self):
        picks = []
        with db.connect(self.db_path) as conn:
            for j in ("j1", "j2", "j3", "j4"):
                t = ex.arm_for_draft(conn, j)
                ex.record_draft(conn, t["experiment_id"], j, t["arm"])
                picks.append(t["arm"])
            self.assertEqual(picks, ["A", "B", "A", "B"])
            self.assertEqual(ex.arm_for_draft(conn, "j2")["arm"], "B")     # a re-draft keeps its side
            self.assertIsNone(ex.arm_for_draft(conn, "j9", "invite"))      # invitations are outside T1


class TestDrafter(Base):
    JOB = {"id": "777", "title": "Airtable CRM cleanup", "description": "We re-type leads.",
           "contract_type": "Hourly", "hourly_min": 40, "hourly_max": 60, "skills_json": "[]"}
    EV = {"status": "none", "found": 0, "block": "", "facts": [], "stack": []}

    def draft(self, job, texts=("Your team re-types leads every morning. It costs deals.",), db_path="db"):
        with mock.patch.object(evidence, "for_job", return_value=self.EV), \
             mock.patch.object(evidence, "validate_draft", return_value=(True, [])), \
             mock.patch.object(scoring, "draft_proposal", side_effect=list(texts)) as dp:
            out = scoring.draft_proposal_grounded(
                job, "t", db_path=self.db_path if db_path == "db" else db_path)
        return out, dp

    def test_version_b_gets_the_instruction_and_a_does_not(self):
        (_, prov_a), dp_a = self.draft(dict(self.JOB, id="1"))
        (_, prov_b), dp_b = self.draft(dict(self.JOB, id="2"))
        self.assertEqual((prov_a["test"]["arm"], prov_b["test"]["arm"]), ("A", "B"))
        self.assertIsNone(dp_a.call_args.kwargs["evidence_block"])
        self.assertIn("TEST T1, VERSION B", dp_b.call_args.kwargs["evidence_block"])
        with db.connect(self.db_path) as conn:
            got = {r["job_id"]: r["arm"] for r in conn.execute(
                "SELECT job_id, arm FROM experiment_assignments WHERE via='draft'")}
        self.assertEqual(got, {"1": "A", "2": "B"})

    def test_no_draft_no_assignment(self):
        with mock.patch.object(evidence, "for_job", return_value=self.EV), \
             mock.patch.object(evidence, "validate_draft", return_value=(False, [{"kind": "unsourced_number", "value": "40"}])), \
             mock.patch.object(scoring, "draft_proposal", side_effect=["a", "b"]):
            text, prov = scoring.draft_proposal_grounded(self.JOB, "t", db_path=self.db_path)
        self.assertIsNone(text)
        with db.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM experiment_assignments").fetchone()[0], 0)

    def test_a_bare_call_never_touches_a_database(self):
        (_, prov), dp = self.draft(self.JOB, db_path=None)
        self.assertNotIn("test", prov)
        self.assertIsNone(dp.call_args.kwargs["evidence_block"])

    def test_the_card_says_which_version(self):
        cp = _clickup()
        row = {"id": "2", "score": 24, "draft_proposal": "Your team re-types leads.",
               "draft_evidence_json": json.dumps({"test": {"id": ex.T1_ID, "arm": "B",
                                                           "label": "The client's situation first"}})}
        body = cp.task_body(row)
        self.assertIn("Test T1 · version B", body)
        self.assertIn("no I / we", body)
        self.assertNotIn("Test T1", cp.task_body(dict(row, draft_evidence_json="{}")))


class TestSentAndVerdict(Base):
    def test_a_sent_letter_is_recognised(self):
        self.assertEqual(ex.detect_arm(ex.T1_ID, "Hi Sam, I have built 40 Airtable CRMs."), "A")
        self.assertEqual(ex.detect_arm(ex.T1_ID, "Hi Sam,\n\nYour reps re-type every lead by hand."
                                                 " Each copy loses a day. I would start with..."), "B")
        self.assertIsNone(ex.detect_arm(ex.T1_ID, ""))

    def test_link_sent_flags_a_rewrite_and_detects_the_rest(self):
        with db.connect(self.db_path) as conn:
            ex.record_draft(conn, ex.T1_ID, "j1", "B")
            out = ex.link_sent(conn, [
                {"id": "p1", "job_id": "j1", "sent": "2026-10-10T08:00:00Z", "kind": "cold",
                 "letter": "Hi, I am an Airtable expert."},                      # B draft, sent as A
                {"id": "p2", "job_id": "j2", "sent": "2026-10-10T09:00:00Z", "kind": "cold",
                 "letter": "Hi, your CRM loses leads."},                         # no draft: detected
                {"id": "p3", "job_id": "j3", "sent": "2026-10-01T09:00:00Z", "kind": "cold",
                 "letter": "Hi, your CRM loses leads."},                         # before the test
            ])
            rows = {r["job_id"]: dict(r) for r in conn.execute("SELECT * FROM experiment_assignments")}
        self.assertEqual(out, {"linked": 1, "detected": 1, "rewritten": 1})
        self.assertEqual((rows["j1"]["arm"], rows["j1"]["sent_arm"], rows["j1"]["proposal_id"]), ("B", "A", "p1"))
        self.assertEqual((rows["j2"]["via"], rows["j2"]["arm"]), ("detected", "B"))
        self.assertNotIn("j3", rows)

    def test_chance_is_exact(self):
        self.assertAlmostEqual(ex.chance_better(12, 40, 18, 40), 0.914, places=3)
        self.assertAlmostEqual(ex.chance_better(5, 40, 10, 40), 0.919, places=3)
        self.assertAlmostEqual(ex.chance_better(10, 40, 10, 40), 0.5, places=6)
        self.assertIsNone(ex.chance_better(0, 0, 1, 2))

    def board(self, a_hits, b_hits, n=40, sent="2026-10-20", today="2026-11-02"):
        def rows(h):
            return [{"sent": sent, "rep": 1 if i < h else 0, "op": 1 if i < h + 5 else 0, "hired": 0}
                    for i in range(n)]
        exp = dict(ex.T1)
        return ex.scoreboard(exp, {"A": rows(a_hits), "B": rows(b_hits)}, today)

    def test_the_verdict_says_only_what_the_numbers_allow(self):
        self.assertTrue(self.board(4, 4, n=20)["verdict"].startswith("too early: 20 of 40"))
        self.assertTrue(self.board(4, 4, today="2026-10-24")["verdict"].startswith("too early"))  # 4 days' wait
        self.assertEqual(self.board(3, 12)["verdict"], "B is clearly better")
        self.assertEqual(self.board(6, 10)["verdict"], "a lean towards B")
        self.assertEqual(self.board(8, 8)["verdict"], "no difference")
        late = self.board(2, 3, n=10, today="2026-11-25")                    # six weeks: call it
        self.assertTrue(late["timed_out"])
        self.assertNotIn("too early", late["verdict"])

    def test_a_broken_version_is_called_out(self):
        exp = dict(ex.T1)
        b = ex.scoreboard(exp, {"A": [{"sent": "2026-10-12", "op": 1, "rep": 0}] * 15,
                                "B": [{"sent": "2026-10-12", "op": 0, "rep": 0}] * 15}, "2026-10-14")
        self.assertEqual((b["verdict"], b["tone"]), ("version B looks broken: no opens in its first 15", "bad"))


if __name__ == "__main__":
    unittest.main()
