"""The MCP feed must produce the dict the scorer already eats — exactly.

Fixtures are REAL payloads captured from mcp.upwork.com on 2026-08-24 against
Abhijeet's account, trimmed only in description length. Values are unedited:
job 2091139773174847123 is the score-32 Senior AI Automation Specialist role he
applied to (19 connects), 2091681309915385639 the Twilio/Workiz job.

The bar these tests defend (his, 2026-08-24): hard-rule verdicts must agree
100% because they are deterministic code; scores may differ by up to 2 because
the model is stochastic. So the RULES are asserted exactly here, and the
scoring comparison lives in scripts/mcp_feed_compare.py against live data.
"""
from __future__ import annotations

import unittest

from modules.upwork import scoring
from modules.upwork.feeds import mcp_feed

# ── real captured payloads ───────────────────────────────────────────────────

GET_HOURLY = {
    "can_apply": True,
    "connects_cost": 19,
    "connects_balance": 227,
    "client_record": {"contracts_active": 0, "contracts_total": 0,
                      "feedback_count": 0, "feedback_score": 0,
                      "hours_total": 0, "jobs_with_hires": 0},
    "preferred_qualifications": {"contractor_type": "All freelancers",
                                 "min_job_success_score": 0},
    "screening_questions": [
        "<untrusted_participant_content>\nDescribe 2–3 production AI automation"
        " systems you've built\n</untrusted_participant_content>"],
    "data": {"marketplaceJobPosting": {
        "id": "2091139773174847123",
        "activityStat": {
            "applicationsBidStats": {
                "avgRateBid": {"rawValue": "37.283750000000005"},
                "maxRateBid": {"rawValue": "120.0"},
                "minRateBid": {"rawValue": "15.0"}},
            "jobActivity": {"invitesSent": 0, "totalHired": 0,
                            "totalInvitedToInterview": 0}},
        "clientCompanyPublic": {
            "country": {"name": "United Arab Emirates", "region": ""},
            "timezone": "Asia/Tbilisi"},
        "content": {
            "title": "Senior AI Automation Specialist — LLMs + Make.com / n8n"
                     " (Long-term, IC or PM Route)",
            "description": "<untrusted_participant_content>\nWe are a small,"
                           " fully remote, async-first team building production"
                           " AI automation systems using LLMs, Make.com, and"
                           " n8n.\n</untrusted_participant_content>"},
        "contractTerms": {
            "contractType": "HOURLY", "experienceLevel": "EXPERT",
            "hourlyContractTerms": {"engagementType": "FULL_TIME",
                                    "hourlyBudgetMax": 50,
                                    "hourlyBudgetMin": 20,
                                    "hourlyBudgetType": "MANUAL"},
            "personsToHire": 1}}}}

SEARCH_HOURLY = {
    "id": "2091139773174847123",
    "title": "Senior AI Automation Specialist — LLMs + Make.com / n8n"
             " (Long-term, IC or PM Route)",
    "budget": "0.0",
    "job_type": "hourly",
    "proposal_count": 39,
    "created_date": "2026-08-22T12:25:48+0000",
    "published_date": "2026-08-22T12:27:41+0000",
    "duration": "More than 6 months",
    "engagement": "30+ hrs/week",
    "experience_level": "EXPERT",
    "hourly_budget_type": "client-specified range",
    "skills": ["Make.com"],
    "client": {"country": "United Arab Emirates", "total_posted_jobs": 2,
               "verification_status": "VERIFIED"},
}

GET_FIXED = {
    "can_apply": True,
    "connects_cost": 18,
    "client_record": {"contracts_active": 72, "contracts_total": 327,
                      "feedback_count": 181, "feedback_score": 4.62,
                      "hours_total": 11159.5, "jobs_with_hires": 215,
                      "spend_total": "375023.53"},
    "data": {"marketplaceJobPosting": {
        "id": "2091503480754476388",
        "clientCompanyPublic": {"city": "New York", "state": "NY",
                                "country": {"name": "United States"}},
        "content": {"title": "AI Expert architect/Claude Cowork & Code GPT",
                    "description": "Part-Time AI Builder. Strong with Claude,"
                                   " Claude Code, Make, GoHighLevel."},
        "contractTerms": {
            "contractType": "FIXED", "experienceLevel": "INTERMEDIATE",
            "fixedPriceContractTerms": {"amount": {"rawValue": "500.0"}},
            "personsToHire": 1}}}}

SEARCH_FIXED = {
    "id": "2091503480754476388", "job_type": "fixed", "budget": "500.0",
    "proposal_count": 20, "created_date": "2026-08-23T13:01:15+0000",
    "skills": ["Make.com", "GoHighLevel"],
    "client": {"country": "United States", "rating": 4.62,
               "total_hires": 215, "verification_status": "VERIFIED"},
}


class ShapeMatchesTheScraper(unittest.TestCase):
    """Every key scoring.job_input reads must exist, or the model silently
    scores a job with a missing dimension."""

    SCORED_KEYS = ("title", "description", "contract_type", "hourly_min",
                   "hourly_max", "fixed_budget", "weekly_budget",
                   "total_applicants", "experience_level", "engagement",
                   "engagement_duration", "client_country",
                   "client_total_spent", "client_hires", "client_rating",
                   "client_payment_verified", "created_dt", "feed_source")

    def test_every_scored_key_is_present(self):
        job = mcp_feed.normalize(SEARCH_HOURLY, GET_HOURLY)
        for k in self.SCORED_KEYS:
            self.assertIn(k, job, k)

    def test_identity_fields(self):
        job = mcp_feed.normalize(SEARCH_HOURLY, GET_HOURLY)
        self.assertEqual(job["id"], "2091139773174847123")
        self.assertIn("~022091139773174847123", job["href"])
        self.assertTrue(job["title"].startswith("Senior AI Automation"))


class ContractShape(unittest.TestCase):
    def test_hourly_takes_its_range_from_get(self):
        """search reports budget '0.0' on every hourly job. Reading that as a
        fixed price would put a $20-50/hr role under the $500 fixed floor."""
        job = mcp_feed.normalize(SEARCH_HOURLY, GET_HOURLY)
        self.assertEqual(job["contract_type"], "Hourly")
        self.assertEqual((job["hourly_min"], job["hourly_max"]), (20.0, 50.0))
        self.assertIsNone(job["fixed_budget"])

    def test_fixed_takes_its_amount_from_get(self):
        job = mcp_feed.normalize(SEARCH_FIXED, GET_FIXED)
        self.assertEqual(job["contract_type"], "Fixed")
        self.assertEqual(job["fixed_budget"], 500.0)
        self.assertIsNone(job["hourly_min"])


class ClientBlock(unittest.TestCase):
    def test_spend_comes_from_get(self):
        job = mcp_feed.normalize(SEARCH_FIXED, GET_FIXED)
        self.assertEqual(job["client_total_spent"], 375023.53)

    def test_absent_spend_is_none_not_zero(self):
        """A brand-new client has no spend_total key. None means 'not
        measured'; 0.0 would be a fabricated fact, and it feeds the
        proven-spender escape on rate-unstated jobs."""
        job = mcp_feed.normalize(SEARCH_HOURLY, GET_HOURLY)
        self.assertIsNone(job["client_total_spent"])

    def test_country_falls_back_to_search(self):
        """`get` dropped country entirely for a US client in the live sample."""
        g = {"data": {"marketplaceJobPosting": {
            "id": "1", "clientCompanyPublic": {"city": "LA", "state": "CA"},
            "content": {"title": "t"}, "contractTerms": {}}}}
        job = mcp_feed.normalize({"id": "1", "client": {"country": "United States"}}, g)
        self.assertEqual(job["client_country"], "United States")

    def test_payment_verified_is_1_or_0(self):
        self.assertEqual(mcp_feed.normalize(SEARCH_FIXED, GET_FIXED)
                         ["client_payment_verified"], 1)
        self.assertEqual(mcp_feed.normalize({"client": {}}, {})
                         ["client_payment_verified"], 0)


class CompetitionIsExactNotATier(unittest.TestCase):
    """The scraper keeps the LOW BOUND of Upwork's tier ('20 to 50' -> 20); the
    MCP returns the real count. Asserted so the difference is a recorded
    decision, not a surprise in the comparison run."""

    def test_exact_count(self):
        self.assertEqual(mcp_feed.normalize(SEARCH_HOURLY, GET_HOURLY)
                         ["total_applicants"], 39)


class DescriptionHandling(unittest.TestCase):
    def test_untrusted_marker_is_stripped(self):
        job = mcp_feed.normalize(SEARCH_HOURLY, GET_HOURLY)
        self.assertNotIn("untrusted_participant_content", job["description"])
        self.assertIn("Make.com", job["description"])

    def test_full_description_beats_the_search_snippet(self):
        job = mcp_feed.normalize(
            {"description_snippet": "trunc..."}, GET_HOURLY)
        self.assertNotIn("trunc...", job["description"])


class HardRulesBehaveIdentically(unittest.TestCase):
    """The deterministic half of his bar: rules must agree 100%. Boundary cases
    are constructed rather than sampled, because a job at exactly $30/hr is
    rare in a day's feed and is precisely where a mapping bug would hide."""

    def _hourly(self, hmin, hmax, **kw):
        g = {"client_record": kw.get("client_record", {}),
             "data": {"marketplaceJobPosting": {
                 "id": "x", "content": {"title": kw.get("title", "Make.com work"),
                                        "description": kw.get("desc", "Make.com build")},
                 "contractTerms": {"contractType": "HOURLY",
                                   "hourlyContractTerms": {"hourlyBudgetMin": hmin,
                                                           "hourlyBudgetMax": hmax}}}}}
        return mcp_feed.normalize({"id": "x", "client": {}}, g)

    def test_hourly_floor(self):
        self.assertEqual(scoring.hard_rule_skip(self._hourly(10, 29)),
                         "hourly_below_30")
        self.assertIsNone(scoring.hard_rule_skip(self._hourly(10, 30)))
        self.assertIsNone(scoring.hard_rule_skip(self._hourly(20, 50)))

    def test_fixed_floor(self):
        def fx(amount):
            g = {"data": {"marketplaceJobPosting": {
                "id": "x", "content": {"title": "Make.com", "description": "Make.com"},
                "contractTerms": {"contractType": "FIXED",
                                  "fixedPriceContractTerms": {
                                      "amount": {"rawValue": str(amount)}}}}}}
            return mcp_feed.normalize({"id": "x", "client": {}}, g)
        self.assertEqual(scoring.hard_rule_skip(fx(499)), "fixed_below_500")
        self.assertIsNone(scoring.hard_rule_skip(fx(500)))

    def test_rate_unstated_needs_a_proven_spender(self):
        """The blank-rate escape: unproven client skips, proven one scores."""
        bare = self._hourly(None, None)
        self.assertEqual(scoring.hard_rule_skip(bare), "rate_unstated_low_client")
        rich = self._hourly(None, None,
                            client_record={"spend_total": "375023.53"})
        self.assertIsNone(scoring.hard_rule_skip(rich))

    def test_n8n_sole_tool_still_excluded(self):
        job = self._hourly(40, 60, title="n8n Developer Needed",
                           desc="Build n8n workflows.")
        self.assertEqual(scoring.hard_rule_skip(job), "n8n_exclusion")

    def test_n8n_alongside_make_is_not_excluded(self):
        job = self._hourly(40, 60, title="Automation Engineer",
                           desc="Make.com and n8n workflows for our CRM.")
        self.assertIsNone(scoring.hard_rule_skip(job))

    def test_the_real_score_32_job_passes_the_rules(self):
        """It mentions n8n in title AND description, is hourly $20-50, and the
        live scraper scored it 32 and he applied. If the mapping made it skip,
        the swap would silently drop his best lead of the week."""
        job = mcp_feed.normalize(SEARCH_HOURLY, GET_HOURLY)
        self.assertIsNone(scoring.hard_rule_skip(job))


class ExtrasAreCarriedButNotScored(unittest.TestCase):
    def test_extras_present(self):
        x = mcp_feed.normalize(SEARCH_HOURLY, GET_HOURLY)["mcp_extra"]
        self.assertEqual(x["connects_cost"], 19)
        self.assertAlmostEqual(x["avg_rate_bid"], 37.28375)
        self.assertEqual(x["invites_sent"], 0)
        self.assertEqual(len(x["screening_questions"]), 1)
        self.assertNotIn("untrusted", x["screening_questions"][0])

    def test_extras_cannot_reach_the_scorer(self):
        """scoring.job_input pins its own key list, so a new field here cannot
        change a score by accident. This is the guard on that promise."""
        import json
        job = mcp_feed.normalize(SEARCH_HOURLY, GET_HOURLY)
        self.assertNotIn("mcp_extra", json.loads(scoring.job_input(job)))


class DegradesRatherThanRaises(unittest.TestCase):
    def test_empty_inputs(self):
        job = mcp_feed.normalize({}, {})
        self.assertIsNone(job["id"])
        self.assertEqual(job["title"], "")

    def test_none_inputs(self):
        self.assertIsInstance(mcp_feed.normalize(None, None), dict)

    def test_search_only(self):
        job = mcp_feed.normalize(SEARCH_HOURLY, None)
        self.assertEqual(job["contract_type"], "Hourly")
        self.assertEqual(job["total_applicants"], 39)


if __name__ == "__main__":
    unittest.main()
