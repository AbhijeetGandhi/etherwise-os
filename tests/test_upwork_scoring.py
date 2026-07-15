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

    # ── rate-unstated hourly → option B (2026-07-16) ─────────────────────────
    # Upwork ships a blank rate as $0; feed_bridge normalises it to None. The
    # old $30 floor read that as 'cheap' and killed ~370 real jobs. Now the
    # client's proven spend stands in for the absent rate.
    def test_rate_unstated_proven_spender_is_scored(self):
        # no rate, but a $12k client (base fixture) -> falls through to scoring
        j = job(hourly_min=None, hourly_max=None)
        self.assertIsNone(scoring.hard_rule_skip(j))

    def test_rate_unstated_unproven_client_is_skipped(self):
        j = job(hourly_min=None, hourly_max=None, client_total_spent=200.0,
                client_payment_verified=False, client_hires=0)
        self.assertEqual(scoring.hard_rule_skip(j), "rate_unstated_low_client")

    def test_rate_unstated_payment_verified_with_hire_is_scored(self):
        # under the $5k spend bar, but payment-verified with a hire history
        j = job(hourly_min=None, hourly_max=None, client_total_spent=1500.0,
                client_payment_verified=True, client_hires=3)
        self.assertIsNone(scoring.hard_rule_skip(j))

    def test_rate_unstated_verified_but_never_hired_is_skipped(self):
        j = job(hourly_min=None, hourly_max=None, client_total_spent=0.0,
                client_payment_verified=True, client_hires=0)
        self.assertEqual(scoring.hard_rule_skip(j), "rate_unstated_low_client")

    def test_stated_low_rate_still_skips_as_below_30(self):
        # a rate WAS given and it's genuinely low — unchanged, and NOT the
        # rate-unstated path even for a proven spender
        j = job(hourly_min=15.0, hourly_max=20.0, client_total_spent=200_000.0)
        self.assertEqual(scoring.hard_rule_skip(j), "hourly_below_30")

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

    def _score(self, verdict, j):
        with mock.patch.object(scoring.runner, "claude_call") as cc:
            cc.return_value = gw.GatewayResult(
                text="{}", parsed=verdict, model="m", route="payg", usage={},
                cost_usd=0.001, stop_reason="end_turn", duration_ms=5)
            return scoring.score_job(j, task_name="t", db_path=None)

    def test_gated_verdict_never_exceeds_15(self):
        bad = self.verdict(effective=19, gated=True)  # raw_total = 25
        # a job with NO exemption (sub-$45 hourly, tiny spend) stays capped
        out = self._score(bad, job(hourly_max=40.0, fixed_budget=None,
                                   client_total_spent=100.0))
        self.assertEqual(out["score"], 15)   # cap enforced in CODE
        self.assertIn("capped", out["breakdown"]["gate"])

    # ── whale-gate EXEMPTION code-enforced (2026-06-22 lead-loss fix) ──
    def test_gate_lifted_by_hourly_exemption(self):
        # model wrongly gated a GHL/Vapi job that clears $45/hr -> code lifts it
        v = self.verdict(effective=15, gated=True)   # model capped to 15
        v["raw_total"] = 28
        out = self._score(v, job(hourly_max=47.0, fixed_budget=None,
                                 client_total_spent=2000.0))
        self.assertFalse(out["gated"])
        self.assertEqual(out["score"], 28)           # restored to raw
        self.assertEqual(out["loom_flag"], 1)        # 28 >= 22
        self.assertIn("lifted by code", out["breakdown"]["gate"])

    def test_gate_lifted_by_fixed_budget(self):
        v = self.verdict(effective=15, gated=True); v["raw_total"] = 24
        out = self._score(v, job(hourly_max=None, contract_type="Fixed",
                                 fixed_budget=3000.0, client_total_spent=10.0))
        self.assertFalse(out["gated"])
        self.assertEqual(out["score"], 24)

    def test_gate_lifted_by_whale_spend(self):
        v = self.verdict(effective=15, gated=True); v["raw_total"] = 26
        out = self._score(v, job(hourly_max=40.0, fixed_budget=None,
                                 client_total_spent=50000.0))
        self.assertFalse(out["gated"])
        self.assertEqual(out["score"], 26)

    def test_gate_held_when_no_exemption(self):
        v = self.verdict(effective=15, gated=True); v["raw_total"] = 28
        out = self._score(v, job(hourly_max=40.0, fixed_budget=None,
                                 client_total_spent=100.0))
        self.assertTrue(out["gated"])
        self.assertEqual(out["score"], 15)           # legitimately capped

    def test_gate_exempt_helper(self):
        self.assertTrue(scoring._gate_exempt({"hourly_max": 45}))
        self.assertTrue(scoring._gate_exempt({"fixed_budget": 3000}))
        self.assertTrue(scoring._gate_exempt({"client_total_spent": 50000}))
        self.assertFalse(scoring._gate_exempt({"hourly_max": 44,
                                               "fixed_budget": 2999,
                                               "client_total_spent": 49999}))


if __name__ == "__main__":
    unittest.main()
