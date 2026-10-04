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

    # NOTE on `title=`: since 2026-09-23 a platform in the TITLE exempts a job
    # from the rate floors, and the shared fixture's title is "Make.com
    # Automation Build". Every rate test therefore states a neutral title —
    # otherwise it would be asserting the exemption, not the floor.
    def test_hourly_floor(self):
        self.assertEqual(
            scoring.hard_rule_skip(job(title="Backend Developer", hourly_max=25.0)),
            "hourly_below_30")

    def test_fixed_floor(self):
        j = job(title="Backend Developer", contract_type="Fixed", hourly_max=None, fixed_budget=300.0)
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
        j = job(title="Backend Developer", hourly_min=None, hourly_max=None,
                client_total_spent=200.0,
                client_payment_verified=False, client_hires=0)
        self.assertEqual(scoring.hard_rule_skip(j), "rate_unstated_low_client")

    def test_rate_unstated_payment_verified_with_hire_is_scored(self):
        # under the $5k spend bar, but payment-verified with a hire history
        j = job(hourly_min=None, hourly_max=None, client_total_spent=1500.0,
                client_payment_verified=True, client_hires=3)
        self.assertIsNone(scoring.hard_rule_skip(j))

    def test_rate_unstated_verified_but_never_hired_is_skipped(self):
        j = job(title="Backend Developer", hourly_min=None, hourly_max=None,
                client_total_spent=0.0,
                client_payment_verified=True, client_hires=0)
        self.assertEqual(scoring.hard_rule_skip(j), "rate_unstated_low_client")

    def test_stated_low_rate_still_skips_as_below_30(self):
        # a rate WAS given and it's genuinely low — unchanged, and NOT the
        # rate-unstated path even for a proven spender
        j = job(title="Backend Developer", hourly_min=15.0, hourly_max=20.0,
                client_total_spent=200_000.0)
        self.assertEqual(scoring.hard_rule_skip(j), "hourly_below_30")

    def test_no_placeholder_escape_even_for_whales(self):
        j = job(title="Backend Developer", contract_type="Fixed", hourly_max=None,
                fixed_budget=50.0, client_total_spent=200_000.0)
        self.assertEqual(scoring.hard_rule_skip(j), "fixed_below_500")

    def test_the_same_placeholder_budget_passes_when_he_sells_the_tool(self):
        """The other half of the rule above, and the reason it moved: a $50
        "Airtable Expert" post is a client who has not thought about price yet,
        not a client offering $50 for the work. He has bid on four of these."""
        j = job(title="Airtable Expert", contract_type="Fixed", hourly_max=None,
                fixed_budget=50.0, client_total_spent=0.0,
                client_payment_verified=False, client_hires=0)
        self.assertIsNone(scoring.hard_rule_skip(j))

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
        self.assertEqual(out["breakdown"]["scorer_version"], scoring.SCORER_VERSION)
        self.assertEqual(scoring.SCORER_VERSION, "3.1")     # 3.1: HubSpot + Monday.com
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

    def test_the_two_rules_of_08_sep_are_gone(self):
        """Abhijeet, 2026-09-23: "can you roll back the recent changes ... with
        current filter seems like lot of important jobs are also not getting
        scored."

        He was right to want them gone and wrong about why, and the measurement
        is worth keeping: across the 90 proposals he has sent since 1 June,
        `off_domain_title` and `no_platform_named` hid EXACTLY ZERO jobs he went
        on to bid on, while withholding 47 a day from the model. A filter that
        costs reach and has never once saved him from a job he wanted is not
        paying for itself. The jobs they used to kill now reach the scorer,
        which grades them 1-2 on skill fit and writes down why."""
        body = ("We need a senior engineer to build agentic systems in Python "
                "with Claude and GPT-4, deploying to AWS. Strong backend and "
                "API design skills required. " * 4)
        for title in ("Senior AI Engineer - Build Autonomous AI Agents",
                      "LLM Application Developer",
                      "AI Voice Agent for Home Services",
                      "Marketing Automation Specialist",
                      "Funnel Builder and Manager",
                      "Content Production System",
                      "SEO / AEO Implementation Specialist"):
            self.assertIsNone(scoring.hard_rule_skip(self._job(title, body)), title)

    def test_the_rules_a_number_can_settle_are_still_rules(self):
        """What came out was judgment the model does better. What stays is
        arithmetic: rate, budget, timezone, onsite, n8n. Removing a judgment
        rule is not an argument for removing these."""
        cheap = dict(self._job("Backend Developer", "Build an API. " * 40),
                     hourly_min=10.0, hourly_max=15.0)
        self.assertEqual(scoring.hard_rule_skip(cheap), "hourly_below_30")
        blank = dict(self._job("Backend Developer", "Build an API. " * 40),
                     hourly_min=None, hourly_max=None)
        self.assertEqual(scoring.hard_rule_skip(blank), "rate_unstated_low_client")
        small = dict(self._job("Backend Developer", "Build an API. " * 40),
                     contract_type="Fixed", hourly_min=None, hourly_max=None,
                     fixed_budget=150.0)
        self.assertEqual(scoring.hard_rule_skip(small), "fixed_below_500")

    def test_his_own_tools_in_the_title_outrank_the_rate_floors(self):
        """THE RULES THAT WERE ACTUALLY HIDING HIS WORK. Ten of the 90 jobs he
        bid on since 1 June were hard-gated, and not one of them by the 08 Sep
        rules — six by `hourly_below_30`, three by `rate_unstated_low_client`,
        one by n8n. Four had "Airtable" in the title: "Airtable Expert",
        "Airtable Developer & Interface Expert", "Airtable Setup Specialist".
        He bid on them anyway and negotiated up, so the posted rate is the
        client's opening position, not a fact about the work."""
        body = "Set up our base and the interfaces on top of it. " * 10
        for title in ("Airtable Setup Specialist",
                      "GoHighLevel Pipeline Setup for Cleaning Business",
                      "HubSpot Marketing Operations Specialist"):
            cheap = dict(self._job(title, body), hourly_min=10.0, hourly_max=15.0)
            self.assertIsNone(scoring.hard_rule_skip(cheap), title)
            blank = dict(self._job(title, body), hourly_min=None, hourly_max=None)
            self.assertIsNone(scoring.hard_rule_skip(blank), title)
            small = dict(self._job(title, body), contract_type="Fixed",
                         hourly_min=None, hourly_max=None, fixed_budget=150.0)
            self.assertIsNone(scoring.hard_rule_skip(small), title)

    def test_the_exemption_reads_the_title_only(self):
        """A platform mentioned in passing in the body is not a client buying
        configuration of it. The exemption is a door, not a hole — the same
        shape as every other title rule here."""
        j = dict(self._job("Backend Developer",
                           "You will occasionally touch our Airtable base. " * 10),
                 hourly_min=10.0, hourly_max=15.0)
        self.assertEqual(scoring.hard_rule_skip(j), "hourly_below_30")

    def test_n8n_still_wins_over_the_exemption(self):
        """His exclusion, 2026-06-10, and it is not a rate rule. A title that
        starts with n8n is out whatever else it names."""
        j = self._job("n8n Automation Expert for Airtable Sync",
                      "Build it in n8n. " * 30)
        self.assertEqual(scoring.hard_rule_skip(j), "n8n_exclusion")

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

    def test_the_job_that_started_all_of_this_still_reaches_the_model(self):
        """Abhijeet, 2026-09-09, on "Airtable + Make.com Expert - Ecommerce
        Operations, Production Dashboard, and Marketing Hub": "I don't know why
        this is not even showing up". It was hard-skipped because `marketing`
        matched inside the NAME OF A THING THEY WANT BUILT.

        That rule is gone now (2026-09-23) and this job would pass on the
        platform alone, so the assertion is deliberately kept: it is the one
        case that has cost him a real job, and a future gate that re-breaks it
        should fail here first. It scored 28."""
        body = "Ecommerce ops build. " * 30
        ok = self._job("Airtable + Make.com Expert - Ecommerce Operations, "
                       "Production Dashboard, and Marketing Hub", body)
        self.assertIsNone(scoring.hard_rule_skip(ok))

    def test_the_exemplars_anchor_every_grade_and_say_why(self):
        """The old file had three skill-fit examples, nothing below grade 3,
        every `why` empty — and it graded a VOICE AI job 4 of 5, a trade he has
        bid on zero times out of 90 cards. A scale anchored that loosely is
        most of why skill fit drifted."""
        ex = json.loads(scoring.EXEMPLARS_PATH.read_text())["skill_fit"]
        # every grade anchored at least once (it was "exactly once" until
        # 2026-10-05, when one example per grade turned out to teach the model
        # that Airtable and Make were the whole of his trade)
        self.assertEqual({e["grade"] for e in ex}, {1, 2, 3, 4, 5})
        for e in ex:
            self.assertTrue((e.get("why") or "").strip(), e["title"])
        self.assertTrue(any(e["grade"] == 5 and "airtable" in e["title"].lower()
                            for e in ex))

    def test_hubspot_is_his_and_the_line_runs_through_the_work_not_the_name(self):
        """Abhijeet, 2026-10-05, on "HubSpot Account Audit and Re-Architecting"
        ($150-250/hr, client with 46 hires): "why was this job not considered
        as hot lead". It scored 10 against a bar of 19. The scorer had graded
        skill fit 1 of 5 and written "HubSpot architecture and administration
        is not Etherwise's trade ... a specialist vertical he does not occupy"
        — because the prompt's description of him never said HubSpot, and all
        five grading examples were Airtable and Make jobs. 179 of 199
        HubSpot-titled jobs had been graded 1-2 since 9 Sep, while the scanner
        searched Upwork for "hubspot" every hour and his own knowledge base
        held 535 HubSpot facts across 17 clients.

        What is pinned: the prompt NAMES the platforms he sells; the same
        platform is anchored on BOTH sides of the line (architecture high,
        website/marketing low), because naming HubSpot alone would have swung
        every HubSpot marketing-VA post to a 5; and the exact job he asked
        about is the grade-5 example."""
        prompt = scoring.system_prompt()
        for platform in ("HubSpot", "Monday.com", "Airtable", "Make.com"):
            self.assertIn(platform, prompt, platform)
        self.assertIn("KIND OF", prompt)                  # the work, not the name
        ex = json.loads(scoring.EXEMPLARS_PATH.read_text())["skill_fit"]
        hub = {e["grade"] for e in ex if "hubspot" in e["title"].lower()}
        self.assertTrue(any(g >= 4 for g in hub), hub)    # CRM architecture
        self.assertTrue(any(g <= 2 for g in hub), hub)    # website / marketing
        self.assertTrue(any(e["grade"] == 5
                            and e["title"] == "HubSpot Account Audit and Re-Architecting"
                            for e in ex))
        self.assertTrue(any(e["grade"] >= 4 and "monday.com" in e["title"].lower()
                            for e in ex))

    def test_the_platforms_he_turned_down_are_named_as_not_his(self):
        """His answers the same day: no to Salesforce, Zoho, Pipedrive, ClickUp
        and Notion; GoHighLevel and voice stay low. Written into the prompt so
        the next reader does not 'helpfully' generalise HubSpot to every CRM."""
        prompt = scoring.system_prompt()
        i = prompt.index("NOT on his list")
        for name in ("Salesforce", "Zoho", "Pipedrive", "ClickUp", "Notion"):
            self.assertIn(name, prompt[i:i + 400], name)

    def test_an_example_without_money_fields_renders_without_empty_brackets(self):
        self.assertNotIn(" ()", scoring.system_prompt())

    def test_claude_and_fresh_are_gone_from_the_schema(self):
        """Deleted, not zero-weighted: a field the model is asked to fill is a
        field it weighs."""
        props = scoring.SCHEMA["properties"]["bonuses"]["properties"]
        self.assertNotIn("claude", props)
        self.assertNotIn("fresh", props)
        self.assertEqual(sorted(props),
                         ["core_stack", "invite", "long_term", "whale_client"])
