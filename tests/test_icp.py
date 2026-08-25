"""ICP / Prime tier — Abhijeet's ideal-client profile, deterministic + code-side.
Every threshold here is an objective fact from the feed payload."""
from __future__ import annotations

import unittest

from core import config
from modules.upwork import icp


def job(**kw):
    j = {"title": "Airtable + Make.com Automation Specialist",
         "description": "Ongoing automation work for our ops team.",
         "skills": ["Airtable", "Make.com"],
         "contract_type": "Hourly", "hourly_min": 40, "hourly_max": 60,
         "fixed_budget": None, "engagement_duration": "More than 6 months",
         "client_total_spent": 168000.0, "client_hires": 20}
    j.update(kw)
    return j


class TestMustHaves(unittest.TestCase):
    def test_ideal_job_is_icp(self):
        v = icp.classify(job(), score=24)
        self.assertEqual(v["tier"], "icp")
        self.assertTrue(all(v["must"].values()))
        self.assertTrue(v["wealthy"] and v["long_term"])

    def test_tool_is_mandatory(self):
        v = icp.classify(job(title="Zapier + HubSpot build", skills=["Zapier"],
                             description="Ongoing ops work."), score=24)
        self.assertIsNone(v["tier"])              # no Airtable/Make -> never ICP
        self.assertIn("no Airtable/Make.com", v["misses"])

    def test_tool_found_in_description_not_just_skills(self):
        v = icp.classify(job(title="Automation engineer", skills=[],
                             description="Ongoing work on our Make.com stack."),
                         score=24)
        self.assertEqual(v["tier"], "icp")

    def test_rate_must_reach_51(self):
        v = icp.classify(job(hourly_max=50), score=24)
        self.assertIsNone(v["tier"])              # $50 top is below the bar
        v = icp.classify(job(hourly_max=51), score=24)
        self.assertEqual(v["tier"], "icp")        # exactly $51 qualifies

    def test_fixed_price_equivalent(self):
        fixed = job(contract_type="Fixed", hourly_min=None, hourly_max=None,
                    fixed_budget=5000)
        self.assertEqual(icp.classify(fixed, score=24)["tier"], "icp")
        low = dict(fixed, fixed_budget=4999)
        self.assertIsNone(icp.classify(low, score=24)["tier"])

    def test_agency_hostile_is_disqualifying(self):
        for text in ("No agencies please.", "Individuals only.",
                     "Freelancers only — no teams.", "no outsourcing"):
            v = icp.classify(job(description="Ongoing work. " + text), score=24)
            self.assertIsNone(v["tier"], text)
            self.assertIn("post excludes agencies", v["misses"])


class TestQualifiersAndNearMiss(unittest.TestCase):
    def test_low_spend_is_near_miss_not_icp(self):
        v = icp.classify(job(client_total_spent=20000.0), score=24)
        self.assertEqual(v["tier"], "icp_near")   # must-haves hold, wealth short
        self.assertTrue(any("< $50,000" in m for m in v["misses"]))

    def test_spend_per_hire_proxy_rejects_cheap_buyer(self):
        # $60k spent but across 200 hires = $300/hire -> a $5-gig buyer
        v = icp.classify(job(client_total_spent=60000.0, client_hires=200),
                         score=24)
        self.assertEqual(v["tier"], "icp_near")
        self.assertFalse(v["wealthy"])
        self.assertTrue(any("pays small" in m for m in v["misses"]))

    def test_quick_fix_is_near_miss(self):
        v = icp.classify(job(description="One-time quick fix to our base.",
                             engagement_duration="Less than 1 week"), score=24)
        self.assertEqual(v["tier"], "icp_near")
        self.assertFalse(v["long_term"])

    def test_long_term_from_model_bonus(self):
        v = icp.classify(job(engagement_duration="Less than 1 month",
                             description="Automation work."),
                         score=24, breakdown={"bonuses": {"long_term": 3}})
        self.assertEqual(v["tier"], "icp")
        self.assertTrue(v["long_term"])

    def test_both_qualifiers_missing_is_no_tier(self):
        v = icp.classify(job(client_total_spent=1000.0,
                             engagement_duration="Less than 1 week",
                             description="One-time quick fix."), score=24)
        self.assertIsNone(v["tier"])

    def test_icp_never_promotes_a_weak_lead(self):
        # ideal client, but the job scored below the hot bar -> no tier
        # the ICP overlay sits ON TOP of the hot bar, whatever its value
        self.assertIsNone(icp.classify(job(), score=config.HOT_LEAD_THRESHOLD - 1)["tier"])
        self.assertEqual(icp.classify(job(), score=config.HOT_LEAD_THRESHOLD)["tier"], "icp")


class TestRouting(unittest.TestCase):
    def test_icp_outranks_hot_but_not_invite(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import clickup_push as cp
        self.assertEqual(cp.list_for_score(24, invite=False, icp_tier="icp"),
                         cp.LIST_ICP)
        self.assertEqual(cp.list_for_score(24, invite=False, icp_tier=None),
                         cp.LIST_HOT)
        self.assertEqual(cp.list_for_score(24, invite=True, icp_tier="icp"),
                         cp.LIST_INVITES)          # invite still wins
        # a near-miss routes to Hot as usual — the tag is informational
        self.assertEqual(cp.list_for_score(24, invite=False, icp_tier="icp_near"),
                         cp.LIST_HOT)


if __name__ == "__main__":
    unittest.main()
