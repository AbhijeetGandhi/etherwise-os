"""Unit tests for modules/upwork/scoring.py — hard rules are pure Python
(claw lesson: guardrails in code, never only prompts); the gateway is mocked.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from core import claude_gateway as gw
from modules.upwork import scoring


def job(**kw):
    base = {
        "id": "2064908810394200829",
        "title": "Make.com Automation Build",
        "description": "Build automations across Airtable and Make.",
        "contract_type": "Hourly",
        "hourly_min": 30.0, "hourly_max": 45.0, "fixed_budget": None,
        "skills_json": json.dumps(["Make.com", "Airtable"]),
        "client_total_spent": 12000.0,
    }
    base.update(kw)
    return base


class TestHardRules(unittest.TestCase):
    def test_clean_job_passes(self):
        self.assertIsNone(scoring.hard_rule_skip(job()))

    def test_hourly_floor(self):
        self.assertEqual(scoring.hard_rule_skip(job(hourly_max=25.0)),
                         "hourly_below_30")

    def test_fixed_floor(self):
        j = job(contract_type="Fixed", hourly_max=None, fixed_budget=300.0)
        self.assertEqual(scoring.hard_rule_skip(j), "fixed_below_500")

    def test_no_placeholder_escape_even_for_whales(self):
        j = job(contract_type="Fixed", hourly_max=None, fixed_budget=50.0,
                client_total_spent=200_000.0)
        self.assertEqual(scoring.hard_rule_skip(j), "fixed_below_500")

    # n8n rule REFINED 2026-06-12 (Abhijeet): primary-subject-only.
    def test_n8n_title_start_skips(self):
        self.assertEqual(
            scoring.hard_rule_skip(job(title="n8n expert needed",
                                       description="automation work")),
            "n8n_exclusion")
        self.assertEqual(
            scoring.hard_rule_skip(job(title="N8N Developer for Agency",
                                       description="ops automations")),
            "n8n_exclusion")

    def test_n8n_sole_tool_skips(self):
        j = job(title="Automation Developer",
                description="migrate our N8N flows and maintain them")
        self.assertEqual(scoring.hard_rule_skip(j), "n8n_exclusion")

    def test_n8n_in_multi_tool_pipeline_scores_normally(self):
        j = job(title="Automation Developer",
                description="pipeline across Make.com, n8n and Airtable")
        self.assertIsNone(scoring.hard_rule_skip(j))
        # title-START still skips even when other tools appear later
        # (brief: title-start OR sole-tool — deterministic, no judgment)
        j2 = job(title="n8n to Make.com migration")
        self.assertEqual(scoring.hard_rule_skip(j2), "n8n_exclusion")

    def test_n8n_in_tags_only_does_not_skip(self):
        j = job(skills_json=json.dumps(["n8n", "Make.com"]))
        self.assertIsNone(scoring.hard_rule_skip(j))  # 2026-06-10 decision

    def test_n8n_word_boundary(self):
        self.assertIsNone(scoring.hard_rule_skip(
            job(title="Automation build",
                description="working with n8nx-like tools")))

    def test_no_asia_skips(self):
        self.assertEqual(
            scoring.hard_rule_skip(job(description="No Asia timezones pls")),
            "no_asia_timezone")

    def test_onsite_skips(self):
        self.assertEqual(
            scoring.hard_rule_skip(job(description="onsite in NYC required")),
            "onsite_required")


class TestPromptAssembly(unittest.TestCase):
    def test_system_contains_matrix_gate_and_exemplars(self):
        system = scoring.system_prompt()
        self.assertIn("Skill Fit", system)
        self.assertIn("Category Priority Gate", system)
        self.assertIn("$50,000", system)        # whale gate thresholds
        self.assertIn("capped", system.lower())
        # anchored exemplars from fixtures present
        self.assertIn("ANCHORED EXEMPLARS", system)
        ex = json.loads(scoring.EXEMPLARS_PATH.read_text())
        sample = ex["budget"][0]["title"][:30]
        self.assertIn(sample, system)

    def test_schema_matches_eval_shape(self):
        for field in ("skill_fit", "budget", "client_quality", "competition",
                      "description_quality", "base_total", "bonuses",
                      "raw_total", "gated", "effective_total"):
            self.assertIn(field, scoring.SCHEMA["properties"], field)


class TestScoreJob(unittest.TestCase):
    def verdict(self, effective=18, gated=False):
        return {"skill_fit": 4, "budget": 4, "client_quality": 2,
                "competition": 3, "description_quality": 4, "base_total": 21,
                "bonuses": {"core_stack": 3, "long_term": 0, "invite": 0,
                            "claude": 0, "whale_client": 0, "fresh": 0},
                "raw_total": effective if not gated else effective + 6,
                "gated": gated, "effective_total": effective,
                "recommendation": "bid"}

    def test_returns_effective_score_and_breakdown(self):
        with mock.patch.object(scoring.runner, "claude_call") as cc:
            cc.return_value = gw.GatewayResult(
                text="{}", parsed=self.verdict(), model="m", route="payg",
                usage={}, cost_usd=0.001, stop_reason="end_turn",
                duration_ms=5)
            out = scoring.score_job(job(), task_name="t", db_path=None)
        self.assertEqual(out["score"], 18)
        self.assertFalse(out["gated"])
        self.assertEqual(out["breakdown"]["skill_fit"], 4)
        self.assertEqual(out["loom_flag"], 0)

    def test_loom_flag_at_22_effective(self):
        with mock.patch.object(scoring.runner, "claude_call") as cc:
            cc.return_value = gw.GatewayResult(
                text="{}", parsed=self.verdict(effective=24), model="m",
                route="payg", usage={}, cost_usd=0.001,
                stop_reason="end_turn", duration_ms=5)
            out = scoring.score_job(job(), task_name="t", db_path=None)
        self.assertEqual(out["loom_flag"], 1)

    def test_gated_verdict_never_exceeds_15(self):
        bad = self.verdict(effective=19, gated=True)  # model failed the cap
        with mock.patch.object(scoring.runner, "claude_call") as cc:
            cc.return_value = gw.GatewayResult(
                text="{}", parsed=bad, model="m", route="payg", usage={},
                cost_usd=0.001, stop_reason="end_turn", duration_ms=5)
            out = scoring.score_job(job(), task_name="t", db_path=None)
        self.assertEqual(out["score"], 15)   # cap enforced in CODE
        self.assertIn("capped", out["breakdown"]["gate"])


if __name__ == "__main__":
    unittest.main()
