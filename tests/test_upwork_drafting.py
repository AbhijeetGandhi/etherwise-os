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


if __name__ == "__main__":
    unittest.main()
