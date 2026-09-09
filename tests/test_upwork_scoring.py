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
    def verdict(self, fit=4, budget=4, client=2, core=3, invite=0,
                long_term=0, whale=0, gated=False, claims=99):
        """A model verdict. `claims` is what the model SAYS the total is — the
        code ignores it and computes from the components (v3.0, 2026-09-08),
        so every test here pins the arithmetic rather than the model's word.
        `fit` is the wire shape: skill_fit arrives already weighted x2."""
        return {"skill_fit": fit, "budget": budget, "client_quality": client,
                "competition": 3, "description_quality": 4, "base_total": claims,
                "bonuses": {"core_stack": core, "long_term": long_term,
                            "invite": invite, "whale_client": whale},
                "raw_total": claims, "gated": gated,
                "effective_total": claims, "recommendation": "bid"}

    def test_returns_effective_score_and_breakdown(self):
        with mock.patch.object(scoring.runner, "claude_call") as cc:
            cc.return_value = gw.GatewayResult(
                text="{}", parsed=self.verdict(), model="m", route="payg",
                usage={}, cost_usd=0.001, stop_reason="end_turn",
                duration_ms=5)
            out = scoring.score_job(job(), task_name="t", db_path=None)
        # skill_fit 4 IS the grade (v3.0.1, 2026-09-09 — it used to be halved
        # on the assumption the model pre-doubled it, and did not): 4*4 +
        # budget 4 + client 2 = 22, + core_stack 3 = 25. The model claimed 99.
        self.assertEqual(out["score"], 25)
        self.assertFalse(out["gated"])
        self.assertEqual(out["breakdown"]["skill_fit"], 4)
        self.assertEqual(out["breakdown"]["scorer_version"], "3.0")
        self.assertEqual(out["loom_flag"], 0)      # 25 < LOOM_FLAG_THRESHOLD 28

    def test_loom_flag_at_22_effective(self):
        with mock.patch.object(scoring.runner, "claude_call") as cc:
            cc.return_value = gw.GatewayResult(
                text="{}", parsed=self.verdict(fit=10, budget=5, client=3),
                model="m", route="payg", usage={}, cost_usd=0.001,
                stop_reason="end_turn", duration_ms=5)
            out = scoring.score_job(job(), task_name="t", db_path=None)
        self.assertEqual(out["score"], 31)       # 5*4 + 5 + 3 + core 3
        self.assertEqual(out["loom_flag"], 1)

    def _score(self, verdict, j):
        with mock.patch.object(scoring.runner, "claude_call") as cc:
            cc.return_value = gw.GatewayResult(
                text="{}", parsed=verdict, model="m", route="payg", usage={},
                cost_usd=0.001, stop_reason="end_turn", duration_ms=5)
            return scoring.score_job(j, task_name="t", db_path=None)

    def test_a_gated_verdict_never_reaches_the_hot_bar(self):
        bad = self.verdict(fit=10, budget=5, client=3, gated=True)  # raw 31
        # a job with NO exemption (sub-$45 hourly, tiny spend) stays capped
        out = self._score(bad, job(hourly_max=40.0, fixed_budget=None,
                                   client_total_spent=100.0))
        self.assertEqual(out["score"], scoring._GATE_CAP)   # cap enforced in CODE
        self.assertIn("capped", out["breakdown"]["gate"])

    # ── whale-gate EXEMPTION code-enforced (2026-06-22 lead-loss fix) ──
    def test_gate_lifted_by_hourly_exemption(self):
        # model wrongly gated a GHL/Vapi job that clears $45/hr -> code lifts it
        v = self.verdict(fit=10, budget=5, client=3, core=0, gated=True)
        out = self._score(v, job(hourly_max=47.0, fixed_budget=None,
                                 client_total_spent=2000.0))
        self.assertFalse(out["gated"])
        self.assertEqual(out["score"], 28)           # restored to raw
        self.assertEqual(out["loom_flag"], 1)        # 28 >= 22
        self.assertIn("lifted by code", out["breakdown"]["gate"])

    def test_gate_lifted_by_fixed_budget(self):
        v = self.verdict(fit=8, budget=5, client=3, core=0, gated=True)
        out = self._score(v, job(hourly_max=None, contract_type="Fixed",
                                 fixed_budget=3000.0, client_total_spent=10.0))
        self.assertFalse(out["gated"])
        self.assertEqual(out["score"], 24)       # 4*4 + 5 + 3

    def test_gate_lifted_by_whale_spend(self):
        v = self.verdict(fit=10, budget=3, client=3, core=0, gated=True)
        out = self._score(v, job(hourly_max=40.0, fixed_budget=None,
                                 client_total_spent=50000.0))
        self.assertFalse(out["gated"])
        self.assertEqual(out["score"], 26)       # 5*4 + 3 + 3

    def test_gate_held_when_no_exemption(self):
        v = self.verdict(fit=10, budget=5, client=3, core=0, gated=True)
        out = self._score(v, job(hourly_max=40.0, fixed_budget=None,
                                 client_total_spent=100.0))
        self.assertTrue(out["gated"])
        self.assertEqual(out["score"], scoring._GATE_CAP)   # legitimately capped

    def test_gate_exempt_helper(self):
        self.assertTrue(scoring._gate_exempt({"hourly_max": 45}))
        self.assertTrue(scoring._gate_exempt({"fixed_budget": 3000}))
        self.assertTrue(scoring._gate_exempt({"client_total_spent": 50000}))
        self.assertFalse(scoring._gate_exempt({"hourly_max": 44,
                                               "fixed_budget": 2999,
                                               "client_total_spent": 49999}))


if __name__ == "__main__":
    unittest.main()


class TestPlatformGateAndWeights(unittest.TestCase):
    """v3.0, 2026-09-08. Abhijeet: "there is lot of noise we definitely need to
    improve our socring. like past 150 jobs I looked at I probably just opened
    10 and skipped 140 or afteropening I kep only 2 for applying."

    Measured over 2,006 hot cards and the 50 he actually bid on, the split was
    not in the client fields — it was in what the job IS. These pin the two
    rules that came out of it and the arithmetic that ranks what survives."""

    def _job(self, title, desc):
        return {"title": title, "description": desc, "contract_type": "Hourly",
                "hourly_min": 40.0, "hourly_max": 80.0}

    def test_his_own_category_in_the_title_beats_the_gate(self):
        """Abhijeet, 2026-09-08: "make sure that if something says AI automation
        develoepr it is not skipped and then you make decision based on the
        actual descipriton for." The first cut proved him right — "AI Automation
        Specialist for Business Solutions" scored 30, he bid on it, and it named
        no platform anywhere. The title buys it a reading, not a pass."""
        body = ("Build autonomous agents in Python with Claude and GPT-4 on "
                "AWS. Strong backend and API design required. " * 4)
        for title in ("AI Automation Developer", "Systems Integration Lead",
                      "Workflow Engineer for Operations"):
            self.assertIsNone(scoring.hard_rule_skip(self._job(title, body)), title)
        # ...and the exemption is a door, not a hole: the trades next door are
        # still skipped even when they say automation.
        self.assertEqual(
            scoring.hard_rule_skip(self._job("Marketing Automation Specialist", body)),
            "off_domain_title")

    def test_a_job_naming_no_platform_he_works_in_never_reaches_the_model(self):
        """"claude" appeared in 182 hot cards and produced zero applications;
        so did "full stack", "api" and "voice". They are AI-engineering posts,
        and he is a no-code systems consultant."""
        j = self._job("Senior AI Engineer - Build Autonomous AI Agents",
                      "We need a senior engineer to build agentic systems in "
                      "Python with Claude and GPT-4, deploying to AWS. Strong "
                      "backend and API design skills required. " * 4)
        self.assertEqual(scoring.hard_rule_skip(j), "no_platform_named")

    def test_the_same_job_passes_the_moment_it_names_one(self):
        j = self._job("Senior AI Engineer - Build Autonomous AI Agents",
                      "We need a senior engineer to build agentic systems on "
                      "top of our Airtable base and Make.com scenarios. " * 4)
        self.assertIsNone(scoring.hard_rule_skip(j))

    def test_an_ai_vendor_name_is_not_a_platform(self):
        """Adding openai/gpt/claude to the vocabulary passes 218 more cards and
        keeps exactly the same 47 of his 50 applications. A model vendor says
        nothing about whether a client is buying configuration work."""
        j = self._job("LLM Application Developer",
                      "Looking for someone to wire OpenAI and Claude into our "
                      "own product. TypeScript, Next.js, Postgres. " * 4)
        self.assertEqual(scoring.hard_rule_skip(j), "no_platform_named")

    def test_the_trades_next_door_are_skipped_on_the_title_alone(self):
        """212 cards since June across voice / marketing / funnel / content,
        zero applications between them. Title only: the words appear harmlessly
        inside a body ("...for our marketing team") and would over-fire."""
        for title in ("AI Voice Agent for Home Services",
                      "Marketing Automation Specialist",
                      "Funnel Builder and Manager",
                      "Content Production System"):
            j = self._job(title, "Airtable and Make.com throughout. " * 20)
            self.assertEqual(scoring.hard_rule_skip(j), "off_domain_title", title)

    def test_a_body_too_thin_to_read_is_never_gated_on_absence(self):
        """Silence is not evidence. ~200 scored rows have almost no body; they
        go to the model rather than being dropped on a technicality."""
        j = self._job("Automation help needed", "Need help. Call me.")
        self.assertIsNone(scoring.hard_rule_skip(j))

    def test_marketing_in_the_body_does_not_skip_a_platform_job(self):
        j = self._job("Airtable Consultant for Business Operations",
                      "You will work alongside our marketing team on the "
                      "Airtable base that runs content ops. " * 6)
        self.assertIsNone(scoring.hard_rule_skip(j))

    # ── the arithmetic ───────────────────────────────────────────────────────

    def test_skill_fit_is_the_spine_and_the_rest_are_tie_breakers(self):
        """7.11 average on the jobs he took vs 4.62 on the ones he skipped,
        while budget / client quality / competition / description quality were
        flat. A perfect fit with a poor budget must outrank the reverse."""
        great_fit = {"skill_fit": 10, "budget": 1, "client_quality": 1,
                     "bonuses": {}}
        great_client = {"skill_fit": 2, "budget": 5, "client_quality": 3,
                        "bonuses": {}}
        self.assertGreater(scoring.compute_total(great_fit)[1],
                           scoring.compute_total(great_client)[1])

    def test_claude_and_fresh_are_weightless_even_if_a_row_carries_them(self):
        """All four of long_term / whale_client / fresh / claude measured
        backwards. His call, 2026-09-08: "just delete claude and fresh keep the
        long term and whale" — a client who spends and stays is the kind he
        wants more of, which is a business decision, not a correlation. Old
        rows still carry the two deleted keys and must be unaffected by them."""
        v = {"skill_fit": 6, "budget": 3, "client_quality": 2,
             "bonuses": {"core_stack": 0, "invite": 0}}
        stale = dict(v, bonuses={"core_stack": 0, "invite": 0,
                                 "fresh": 1, "claude": 2})
        self.assertEqual(scoring.compute_total(v), scoring.compute_total(stale))

    def test_long_term_and_whale_still_count(self):
        v = {"skill_fit": 6, "budget": 3, "client_quality": 2,
             "bonuses": {"core_stack": 0, "invite": 0}}
        rich = dict(v, bonuses={"core_stack": 0, "invite": 0,
                                "long_term": 3, "whale_client": 2})
        self.assertEqual(scoring.compute_total(v)[1] + 5,
                         scoring.compute_total(rich)[1])

    def test_the_model_does_not_get_to_do_the_arithmetic(self):
        """The score has to be re-derivable from the stored breakdown — which
        is also what let this whole re-weighting be back-tested over 4,343 rows
        of history without spending one model call."""
        v = {"skill_fit": 8, "budget": 4, "client_quality": 3,
             "base_total": 999, "raw_total": 999,
             "bonuses": {"core_stack": 3, "invite": 0}}
        self.assertEqual(scoring.compute_total(v), (23, 26))   # 4*4+4+3, +3

    def test_skill_fit_is_the_grade_and_is_not_halved(self):
        """The bug Abhijeet caught on two of his own jobs, 2026-09-09. The
        scorer graded an Airtable build 5 of 5 and wrote "Hot — bid this one";
        it came out at 17. It graded an automation audit 4 and called it
        "squarely Abhijeet's lane"; that came out at 18. Both had been halved
        by code written for the v2.3 prompt, which asked for a doubled grade
        the model returned plain about half the time anyway."""
        travel = {"skill_fit": 5, "budget": 2, "client_quality": 2,
                  "bonuses": {"core_stack": 3}}
        audit = {"skill_fit": 4, "budget": 2, "client_quality": 2,
                 "bonuses": {"core_stack": 3, "long_term": 3}}
        self.assertEqual(scoring.compute_total(travel)[1], 27)
        self.assertEqual(scoring.compute_total(audit)[1], 26)

    def test_a_legacy_doubled_grade_is_still_read_correctly(self):
        """Rows scored before 2026-09-09 may carry the doubled shape. A stored
        value above 5 can only be doubled, so both readings survive; a stored
        2 or 4 is ambiguous and is read as the grade, which errs toward showing
        him a job rather than hiding one."""
        self.assertEqual(scoring._raw_fit(10), 5.0)     # doubled 5
        self.assertEqual(scoring._raw_fit(8), 4.0)      # doubled 4
        self.assertEqual(scoring._raw_fit(5), 5)        # cannot be doubled
        self.assertEqual(scoring._raw_fit(4), 4)        # ambiguous -> the grade
        self.assertEqual(scoring._raw_fit(None), 0.0)

    def test_his_platform_in_the_title_outranks_the_off_domain_rule(self):
        """Abhijeet, 2026-09-09, on "Airtable + Make.com Expert - Ecommerce
        Operations, Production Dashboard, and Marketing Hub": "I don't know why
        this is not even showing up". The word `marketing` matched inside the
        NAME OF A THING THEY WANT BUILT. When his own tools are in the title,
        the model reads the body and decides."""
        body = "Ecommerce ops build. " * 30
        ok = self._job("Airtable + Make.com Expert - Ecommerce Operations, "
                       "Production Dashboard, and Marketing Hub", body)
        self.assertIsNone(scoring.hard_rule_skip(ok))
        # no platform in the title and it is still the trade next door
        for title in ("Marketing Automation Specialist",
                      "AI Voice Agent for Home Services",
                      "Funnel Builder and Manager"):
            self.assertEqual(scoring.hard_rule_skip(self._job(title, body)),
                             "off_domain_title", title)

    def test_the_exemplars_anchor_every_grade_and_say_why(self):
        """The old file had three skill-fit examples, nothing below grade 3,
        every `why` empty — and it graded a VOICE AI job 4 of 5, a trade he has
        bid on zero times out of 90 cards. A scale anchored that loosely is
        most of why skill fit drifted."""
        ex = json.loads(scoring.EXEMPLARS_PATH.read_text())["skill_fit"]
        self.assertEqual(sorted(e["grade"] for e in ex), [1, 2, 3, 4, 5])
        for e in ex:
            self.assertTrue((e.get("why") or "").strip(), e["title"])
        top = next(e for e in ex if e["grade"] == 5)
        self.assertIn("airtable", top["title"].lower())

    def test_claude_and_fresh_are_gone_from_the_schema(self):
        """Deleted, not zero-weighted: a field the model is asked to fill is a
        field it weighs."""
        props = scoring.SCHEMA["properties"]["bonuses"]["properties"]
        self.assertNotIn("claude", props)
        self.assertNotIn("fresh", props)
        self.assertEqual(sorted(props),
                         ["core_stack", "invite", "long_term", "whale_client"])
