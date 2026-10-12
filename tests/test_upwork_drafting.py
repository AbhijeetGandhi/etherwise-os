"""The drafting prompt — the whole playbook reaches the model, or this fails.

Until 2026-08-16 `draft_proposal` did `SKILL.md.read_text()[:6000]` against a
24,271-char file. Every rule in this test lived past character 6,000 and had
never once reached the model in 2,279 live drafts. These assertions exist so a
future `[:n]`, or a quiet swap back to the retired skill file, breaks the suite
instead of the pipeline.
"""
from __future__ import annotations

import unittest
from unittest import mock

from core import claude_gateway as gw
from modules.upwork import scoring


class TestReferenceIsWhole(unittest.TestCase):
    def test_source_is_the_master_reference_not_the_retired_skill(self):
        self.assertTrue(str(scoring.PROPOSAL_REFERENCE).endswith(
            "upwork/proposal-writing-master-reference.md"))
        self.assertTrue(scoring.PROPOSAL_REFERENCE.exists(),
                        f"missing: {scoring.PROPOSAL_REFERENCE}")

    def test_nothing_is_truncated(self):
        raw = scoring.PROPOSAL_REFERENCE.read_text()
        ref = scoring.proposal_reference()
        # only the one named exclusion may be missing
        self.assertGreater(len(ref), len(raw) - 1200)
        self.assertGreater(len(ref), 20_000)
        # the LAST content of the file survives — a prefix slice would not
        self.assertIn("End of reference.", ref)

    def test_the_rules_that_used_to_fall_off_the_end(self):
        """Each of these lived past char 6,000 in the retired 24k skill file."""
        ref = scoring.proposal_reference()
        for rule in (
            "NO em dashes",              # ~char 11,700 in the old file
            "NO contractions",           # never reached the model
            "Pre-Send Checklist",
            "Anti-Patterns from Past Corrections",
            "250-450 words",
            "$50/hr",
            "$51.50/hr",
            "Best, Abhijeet Gandhi",
        ):
            self.assertIn(rule, ref, f"missing from drafting prompt: {rule!r}")

    def test_the_retired_rate_is_gone(self):
        system = scoring.draft_system()
        self.assertNotIn("32.50", system)
        # ...and from the scoring matrix too (CLAUDE.md: do NOT quote $32.50)
        self.assertNotIn("32.50", scoring.system_prompt())
        self.assertIn("$51.50/hr", scoring.system_prompt())

    def test_the_contradictions_of_the_retired_skill_are_gone(self):
        system = scoring.draft_system()
        self.assertNotIn("REPEALED", system)
        self.assertNotIn("contractions are ENCOURAGED", system)
        # the skill ORDERED a lookup a tool-less single-shot call cannot do
        self.assertNotIn("you MUST read", system)
        self.assertNotIn("knowledge-base/wiki", system)

    def test_the_model_is_told_it_cannot_open_files(self):
        system = scoring.draft_system()
        self.assertIn("cannot open files", system)
        self.assertIn("EVIDENCE block", system)


class TestSectionExclusion(unittest.TestCase):
    def test_file_locations_section_is_dropped(self):
        system = scoring.draft_system()
        self.assertNotIn("Section 16: File Locations", system)
        self.assertNotIn("/Users/abhijeet", system)   # no local paths to the API

    def test_neighbouring_sections_survive(self):
        ref = scoring.proposal_reference()
        self.assertIn("## Section 15: What NOT to Include", ref)
        self.assertIn("## Section 17: Craft", ref)

    def test_drop_section_stops_at_the_next_heading(self):
        text = ("# T\n\n## Section 16: File Locations\n\n- a path\n\n"
                "## Section 17: Craft\n\nkeep me\n")
        out = scoring._drop_section(text, "## Section 16: File Locations")
        self.assertNotIn("a path", out)
        self.assertIn("keep me", out)
        self.assertIn("## Section 17: Craft", out)

    def test_drop_section_stops_at_a_horizontal_rule(self):
        text = "## Section 16: File Locations\n\n- a path\n\n---\n\nEnd.\n"
        out = scoring._drop_section(text, "## Section 16: File Locations")
        self.assertNotIn("a path", out)
        self.assertIn("End.", out)

    def test_drop_section_is_a_noop_when_absent(self):
        text = "## Section 1: Hard Rules\n\nno em dashes\n"
        self.assertEqual(scoring._drop_section(text, "## Section 16: X"), text)


class TestDraftCall(unittest.TestCase):
    def _call(self, **kw):
        with mock.patch.object(scoring.runner, "claude_call") as cc:
            cc.return_value = gw.GatewayResult(
                text="  a draft  ", parsed=None, model="m", route="payg",
                usage={}, cost_usd=0.01, stop_reason="end_turn", duration_ms=5)
            out = scoring.draft_proposal({"id": "1", "title": "t"},
                                         task_name="upwork_scan", **kw)
        return out, cc.call_args.kwargs

    def test_evidence_goes_in_the_user_message_never_the_system_block(self):
        """The system block is prompt-cached for an hour. Per-job evidence in
        it would bust the cache on every single job and multiply the bill."""
        _, kw = self._call(evidence_block="EVIDENCE\n[E1] a cited fact")
        self.assertIn("[E1] a cited fact", kw["user_content"])
        self.assertNotIn("[E1]", kw["system"])

    def test_system_block_is_identical_with_and_without_evidence(self):
        _, plain = self._call()
        _, with_ev = self._call(evidence_block="EVIDENCE\n[E1] x")
        self.assertEqual(plain["system"], with_ev["system"])

    def test_returns_stripped_text(self):
        out, _ = self._call()
        self.assertEqual(out, "a draft")




class TestJobBrief(unittest.TestCase):
    """What the drafter is told about the job (2026-10-12). The brief used to
    cut the post at 3,000 characters; the questions a post asks usually come
    last, so the drafter answered none of Freedom Property's six."""

    def job(self, **kw):
        base = {"id": "j1", "title": "Make.com engineer", "contract_type": "HOURLY",
                "hourly_min": 20, "hourly_max": 45, "skills_json": "[]",
                "description": ("About us. " * 600)
                + "How to Apply: 1. Describe a project without an SOP."}
        base.update(kw)
        return base

    def test_the_whole_post_reaches_the_drafter(self):
        job = self.job()
        self.assertGreater(len(job["description"]), 6000)
        brief = scoring.job_brief(job)
        self.assertIn("How to Apply: 1. Describe a project without an SOP.", brief)
        self.assertIn(job["description"], brief)

    def test_screening_questions_are_listed_and_answered_separately(self):
        brief = scoring.job_brief(self.job(
            screening_questions_json='["Why Airtable?", "Describe a scope change."]'))
        self.assertIn("Upwork screening questions:\n1. Why Airtable?\n2. Describe a scope change.", brief)
        self.assertIn("SCREENING ANSWERS", brief)
        self.assertIn("own answer box", brief)

    def test_no_questions_no_screening_block(self):
        for raw in (None, "", "[]", "not json", "[\"  \"]"):
            brief = scoring.job_brief(self.job(screening_questions_json=raw))
            self.assertNotIn("SCREENING ANSWERS", brief, raw)

    def test_the_drafting_call_carries_the_whole_post(self):
        job = self.job(screening_questions_json='["Why Airtable?"]')
        seen = {}

        def fake_call(**kw):
            seen.update(kw)
            return mock.Mock(text="Hi,\n\nDraft.")

        with mock.patch.object(scoring.runner, "claude_call", side_effect=fake_call):
            scoring.draft_proposal(job, "test_task")
        self.assertIn("Describe a project without an SOP.", seen["user_content"])
        self.assertIn("1. Why Airtable?", seen["user_content"])


class TestReferenceRules2026_10_12(unittest.TestCase):
    """His 12 Oct rules reach the model: no rate in the letter, the post's
    questions answered in their own section, full sentences."""

    def test_no_rate_in_the_letter(self):
        ref = scoring.proposal_reference()
        self.assertIn("No rate in the letter", ref)
        self.assertNotIn("Rate stated at end", ref)
        self.assertNotIn("Rate: $50/hr. Available", ref)
        self.assertNotIn("Follow with rate + start date", ref)

    def test_questions_get_their_own_section(self):
        ref = scoring.proposal_reference()
        self.assertIn("Answers to your questions", ref)
        self.assertIn("SCREENING ANSWERS", ref)

    def test_full_sentences_not_fragments(self):
        ref = scoring.proposal_reference()
        self.assertIn("full, connected sentences", ref)
        self.assertNotIn("Paragraphs of three sentences at most, often one", ref)



class TestLetterLength(unittest.TestCase):
    """Upwork's cover letter box cuts off at 5,000 characters; screening
    answers have their own boxes and do not count (2026-10-12)."""

    JOB = {"id": "9", "title": "Make.com engineer", "description": "Post.",
           "contract_type": "HOURLY", "hourly_min": 20, "hourly_max": 45, "skills_json": "[]"}
    EV = {"status": "none", "found": 0, "block": "", "facts": [], "stack": []}

    def test_letter_chars_stops_at_the_screening_block(self):
        text = "Hi,\n\n" + "a" * 100 + "\n\nSCREENING ANSWERS\n\nQ1. Why\n\n" + "b" * 9000
        self.assertEqual(scoring.letter_chars(text), len("Hi,\n\n" + "a" * 100))
        self.assertEqual(scoring.letter_chars("x" * 10), 10)

    def test_the_brief_states_the_limit(self):
        self.assertIn("4,700 characters", scoring.job_brief(self.JOB))

    def grounded(self, texts):
        from modules.upwork import evidence
        with mock.patch.object(evidence, "for_job", return_value=self.EV), \
             mock.patch.object(evidence, "validate_draft", return_value=(True, [])), \
             mock.patch.object(scoring, "draft_proposal", side_effect=list(texts)) as dp:
            out = scoring.draft_proposal_grounded(self.JOB, "t", db_path=None)
        return out, dp

    def test_a_long_letter_is_written_again_once_and_the_shorter_kept(self):
        (text, prov), dp = self.grounded(["x" * 6000, "y" * 4500])
        self.assertEqual(text, "y" * 4500)
        self.assertEqual(dp.call_count, 2)
        self.assertIn("SHORTEN", dp.call_args.kwargs["evidence_block"])
        self.assertEqual((prov["repaired"], prov["letter_chars"]), (["too_long"], 4500))
        self.assertNotIn("too_long", prov)
        self.assertNotIn("letter_issues", prov)

    def test_still_long_is_flagged_never_dropped(self):
        (text, prov), dp = self.grounded(["x" * 6000, "y" * 5500])
        self.assertEqual(text, "y" * 5500)
        self.assertTrue(prov["too_long"])
        self.assertEqual(prov["letter_issues"], ["too_long"])

    def test_a_b_letter_that_opens_with_i_is_written_again(self):
        from modules.upwork import experiments as ex
        test = {"experiment_id": ex.T1_ID, "arm": "B", "label": "B", "instruction": "B-RULE"}
        bad = "Hi,\n\nI have built this before. It works.\n\nMore."
        good = "Hi,\n\nYour inbox is full. Leads wait.\n\nI have built this before."
        with mock.patch.object(scoring, "_test_for_draft", return_value=test):
            (text, prov), dp = self.grounded([bad, good])
        self.assertEqual(text, good)
        self.assertIn("OPENING", dp.call_args.kwargs["evidence_block"])
        self.assertIn("B-RULE", dp.call_args.kwargs["evidence_block"])
        self.assertEqual(prov["repaired"], ["opening"])
        self.assertNotIn("letter_issues", prov)

    def test_a_repair_that_is_no_better_is_not_kept(self):
        from modules.upwork import experiments as ex
        test = {"experiment_id": ex.T1_ID, "arm": "B", "label": "B", "instruction": "B-RULE"}
        bad = "Hi,\n\nI have built this before. It works."
        with mock.patch.object(scoring, "_test_for_draft", return_value=test):
            (text, prov), dp = self.grounded([bad, bad + " Still me."])
        self.assertEqual(text, bad)
        self.assertEqual(prov["letter_issues"], ["opening"])

    def test_a_short_letter_costs_no_extra_call(self):
        (text, prov), dp = self.grounded(["z" * 3000])
        self.assertEqual(dp.call_count, 1)
        self.assertEqual(prov["letter_chars"], 3000)

    def test_the_system_prompt_forbids_invented_stories(self):
        system = scoring.draft_system()
        self.assertIn("Stories are claims too", system)
        self.assertIn("NOTE FOR ABHIJEET", system)



class TestTidyLetter(unittest.TestCase):
    def test_the_sign_off_moves_after_the_answers(self):
        raw = ("Hi,\n\nLetter.\n\nBest, Abhijeet Gandhi\n\n---\n\nAnswers to your questions"
               "\n\nQ1. One\n\nAnswer one.")
        self.assertEqual(scoring.tidy_letter(raw),
                         "Hi,\n\nLetter.\n\nAnswers to your questions\n\nQ1. One\n\n"
                         "Answer one.\n\nBest, Abhijeet Gandhi")

    def test_screening_answers_stay_after_the_sign_off(self):
        raw = ("Hi,\n\nLetter.\n\nBest,\nAbhijeet Gandhi\n\nSCREENING ANSWERS\n\nQ1. Why\n\nBecause.")
        self.assertEqual(scoring.tidy_letter(raw), raw)

    def test_a_plain_letter_is_untouched(self):
        raw = "Hi,\n\nLetter one.\n\nLetter two.\n\nBest,\nAbhijeet Gandhi"
        self.assertEqual(scoring.tidy_letter(raw), raw)


if __name__ == "__main__":
    unittest.main()
