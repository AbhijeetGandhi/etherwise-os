"""modules/upwork/evidence.py — every gate that stands between the knowledge
base and a client's inbox.

The failure this replaces: the drafter was TOLD to look facts up in files it
could not open, so it made them up. Live draft 2058152699613066610 claimed
"Horizons ABA's healthcare onboarding pipeline, 49+ active boards, 7,000+
automation runs/month" — 49+ is Hermon's number on the wrong client, and
7,000+/month exists nowhere. TestTheLiveFabrication is that exact string.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from core import db
from modules.upwork import evidence, scoring


def fact(text, tool="make.com", grade=3, age=30, citation="fathom/1 @ 00:01",
         **kw):
    f = {"id": abs(hash(text)) % 10**9, "fact_text": text, "citation": citation,
         "confidence": "CONFIRMED", "scope": "agency", "category": "numbers",
         "client_id": None, "tool": tool, "grade": grade, "age_days": age,
         "recency": 1.0, "stack_weight": 3, "names_client": False,
         "measured": True}
    f.update(kw)
    return f


class TestStackDetection(unittest.TestCase):
    def test_title_beats_skills_beats_description(self):
        stack = evidence.job_stack({
            "title": "Airtable specialist",
            "skills_json": json.dumps(["Make.com"]),
            "description": "we also use slack"})
        self.assertEqual(stack["airtable"], 3)
        self.assertEqual(stack["make.com"], 2)
        self.assertEqual(stack["slack"], 1)

    def test_unknown_tools_are_not_guessed(self):
        stack = evidence.job_stack({"title": "Kommo CRM implementation",
                                    "description": "kommo and amocrm"})
        self.assertEqual(stack, {})

    def test_bad_skills_json_does_not_raise(self):
        self.assertEqual(evidence.job_stack({"skills_json": "not json"}), {})


class TestFtsSafety(unittest.TestCase):
    def test_monday_dot_com_is_quoted(self):
        """Unquoted, FTS5 raises `syntax error near "."` and takes the scan."""
        self.assertEqual(evidence.fts_phrase("monday.com"), '"monday com"')

    def test_every_tool_in_the_vocabulary_survives_a_real_match(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
        conn.execute("INSERT INTO t VALUES ('monday.com and make.com')")
        for tool in evidence.TOOLS:
            phrase = evidence.fts_phrase(tool)
            conn.execute("SELECT * FROM t WHERE t MATCH ?", (phrase,)).fetchall()

    def test_punctuation_only_term_yields_none(self):
        self.assertIsNone(evidence.fts_phrase("..."))


class TestQualityGate(unittest.TestCase):
    def test_tool_inventory_is_graded_zero(self):
        """BM25 ranks these top because they are dense in tool names. They are
        not delivery evidence and must never be emitted."""
        for text in (
            "Primary tools used: Airtable, Make.com, Zapier, and n8n.",
            "The agency has experience with automation platforms.",
            "Speaker's primary automation platform is Make.com.",
        ):
            self.assertEqual(evidence.grade({"fact_text": text}), 0, text)

    def test_a_constraint_with_a_number_grades_three(self):
        g = evidence.grade({"fact_text":
            "Airtable's 500,000 records per table limit means transactional"
            " data cannot live in the base.", "category": "architecture"})
        self.assertEqual(g, 3)

    def test_a_bare_statement_grades_below_the_floor(self):
        self.assertLess(evidence.grade({"fact_text": "The team uses Slack."}),
                        evidence.MIN_GRADE)


class TestRedaction(unittest.TestCase):
    """Agency scope is NOT an anonymity or a safety boundary. Every string here
    is real, sitting in scope='agency', CONFIRMED."""

    LEAKS = {
        "hourly_rate": [
            "Abhijeet Gandhi's Upwork rate is listed at approximately"
            " $30 per hour.",
            "Etherwise charges $25 per hour for retainer/maintenance work",
            "Etherwise charges $32.50 per hour for development work, though"
            " rates vary based on project size",
        ],
        "inr_amount": [
            "Some agency employees who started at ₹30,000/month have grown"
            " to earning over ₹1 lakh/month.",
            "The agency discussed offering the college student an internship at"
            " approximately ₹25,000/month as a starting point.",
        ],
        "compensation": [
            "Abhi built a commission/salary calculation script in Airtable that"
            " computes closer bookings and applies commission percentages.",
        ],
    }

    MUST_SURVIVE = [
        "One client needed 125,000 minutes of AI calling per month; setting up"
        " those automations in Make.com would have cost over $5,000, but n8n"
        " brought it down to approximately $500.",
        "Apify pricing for LinkedIn scraping is approximately $30 per month.",
        "Airtable's native automations cannot use third-party JavaScript"
        " libraries.",
        "Universal PandaDoc watcher scenario runs 25+ times per day",
        "Biweekly multi-state payroll runs integrate Fingercheck and Sundial.",
    ]

    def test_every_known_leak_is_caught(self):
        for expected, texts in self.LEAKS.items():
            for t in texts:
                self.assertEqual(evidence.redact_reason(t), expected, t[:60])

    def test_legitimate_cost_and_scale_facts_survive(self):
        for t in self.MUST_SURVIVE:
            self.assertIsNone(evidence.redact_reason(t), t[:60])

    def test_the_citation_is_redacted_too(self):
        """Several rate statements live in the verbatim transcript quote, not
        in the summarised fact."""
        rows = [{"id": 1, "fact_text": "The agency prices projects carefully.",
                 "citation": "fathom/9 @ 'our rate is $25 per hour for that'",
                 "category": "other"}]
        kept = [f for f in rows
                if evidence._classify(dict(f, tool="x", stack_weight=1,
                                           occurred_dt="2026-08-01"),
                                      evidence.client_vocabulary(), None)]
        self.assertEqual(kept, [])


class TestNameNormalisation(unittest.TestCase):
    """Fathom mis-transcribes "Horizons ABA" nine ways. Emitting one verbatim
    puts a client that does not exist into a client-facing proposal."""

    def test_the_garbles_are_canonicalised(self):
        for garble in ("Verizon CBA", "HorizonCBA", "Horizons EBA",
                       "Horizons EPA", "Horizon CBA", "Horizons APA",
                       "Items ABA", "Horizon CEBA", "Horizon ABA"):
            out = evidence.normalise_names("%s has 300+ automations" % garble)
            self.assertTrue(out.startswith("Horizons ABA"), out)

    def test_the_canonical_name_is_left_alone(self):
        self.assertEqual(evidence.normalise_names("Horizons ABA built 300+"),
                         "Horizons ABA built 300+")


class TestRecency(unittest.TestCase):
    def test_decay_buckets(self):
        self.assertEqual(evidence.recency_weight(10), 1.0)
        self.assertEqual(evidence.recency_weight(300), 0.8)
        self.assertEqual(evidence.recency_weight(600), 0.5)
        self.assertEqual(evidence.recency_weight(2000), 0.25)

    def test_an_undated_source_never_outranks_a_dated_recent_one(self):
        self.assertLess(evidence.recency_weight(None),
                        evidence.recency_weight(300))

    def test_age_days(self):
        self.assertEqual(
            evidence.age_days("2026-08-01T10:00:00Z", today=date(2026, 8, 16)),
            15)
        self.assertIsNone(evidence.age_days(None))
        self.assertIsNone(evidence.age_days("not a date"))

    def test_a_stale_measured_claim_is_dropped_entirely(self):
        """"80+ automations" (2024-11) and "3,000+" (2026-04) are the same
        claim. Citing the old one is a 37x understatement in his own voice."""
        old = {"id": 1, "fact_text": "Built over 80 automations using Make.com",
               "citation": "loom/x", "category": "numbers",
               "occurred_dt": "2024-11-07", "tool": "make.com",
               "stack_weight": 3}
        self.assertIsNone(evidence._classify(
            old, evidence.client_vocabulary(), date(2026, 8, 16)))

    def test_an_old_constraint_survives_where_an_old_count_does_not(self):
        c = {"id": 2, "category": "architecture", "citation": "loom/y",
             "fact_text": "Make.com does not support custom code blocks,"
                          " which is why n8n gets used for heavier work.",
             "occurred_dt": "2025-03-01", "tool": "n8n", "stack_weight": 2}
        self.assertIsNotNone(evidence._classify(
            c, evidence.client_vocabulary(), date(2026, 8, 16)))

    def test_an_undated_measured_claim_is_dropped(self):
        f = {"id": 3, "fact_text": "Built 800+ automations", "citation": "l",
             "category": "numbers", "occurred_dt": None, "tool": "make.com",
             "stack_weight": 3}
        self.assertIsNone(evidence._classify(
            f, evidence.client_vocabulary(), date(2026, 8, 16)))


class TestStaleClaimResolution(unittest.TestCase):
    def test_only_the_freshest_vintage_of_a_claim_survives(self):
        old = fact("Built over 80 automations using Make.com", age=600)
        new = fact("Speaker has built 3,000+ automations using Make.com", age=34)
        out = evidence._resolve_stale_claims([old, new])
        self.assertEqual([f["fact_text"] for f in out], [new["fact_text"]])

    def test_a_per_client_count_does_not_suppress_a_portfolio_count(self):
        portfolio = fact("Built 3,000+ automations using Make.com", age=34)
        per_client = fact("Horizons ABA: 300+ automations built in Make.com",
                          age=104, names_client=True)
        out = evidence._resolve_stale_claims([portfolio, per_client])
        self.assertEqual(len(out), 2)

    def test_a_fact_with_no_measured_unit_passes_through(self):
        c = fact("Airtable automations cannot use third-party JS libraries",
                 measured=False)
        self.assertEqual(evidence._resolve_stale_claims([c]), [c])


class TestClientVocabulary(unittest.TestCase):
    def test_the_reference_alias_resolves_to_the_registry_canonical(self):
        """The reference writes "Horizons"; the registry canonical is
        "Horizons ABA". A name-only vocabulary attributed Section 4's whole
        Horizons block to nobody and rejected every legitimate claim."""
        self.assertEqual(
            evidence._clients_named("### Horizons (Healthcare ABA provider)"),
            ["Horizons ABA"])
        self.assertEqual(
            evidence._clients_named("I built it for Horizons ABA"),
            ["Horizons ABA"])

    def test_a_common_noun_phrase_is_not_a_client(self):
        """The registry really does hold a client called "AI Agency"."""
        self.assertEqual(
            evidence._clients_named("I run a small AI agency (Etherwise)"), [])

    def test_a_surface_form_containing_a_function_word_is_unusable(self):
        """"Optimize IS" would otherwise fire on "Optimize is Airtable's #1
        services partner", which is a sentence in the reference."""
        self.assertFalse(evidence._usable_form("Optimize IS"))
        self.assertTrue(evidence._usable_form("Horizons ABA"))
        self.assertFalse(evidence._usable_form("AI Agency"))


class TestTheLiveFabrication(unittest.TestCase):
    """The whole point. This is the text a live draft actually shipped."""

    LIVE = ("Hey, the intake volume is the hard part here.\n\n"
            "I have built almost this exact stack for Horizons ABA's healthcare"
            " onboarding pipeline, 49+ active boards, 7,000+ automation"
            " runs/month.\n\nBest, Abhijeet Gandhi")

    def setUp(self):
        self.ref = scoring.proposal_reference()
        self.job = {"title": "Airtable onboarding build", "description": ""}

    def test_it_is_rejected(self):
        ok, problems = evidence.validate_draft(self.LIVE, self.ref, [], self.job)
        self.assertFalse(ok)
        kinds = {p["kind"] for p in problems}
        self.assertIn("unsourced_number", kinds)         # 7,000 exists nowhere
        self.assertIn("client_number_mismatch", kinds)   # 49+ is Hermon's

    def test_the_transplant_is_named_precisely(self):
        _, problems = evidence.validate_draft(self.LIVE, self.ref, [], self.job)
        transplant = [p for p in problems
                      if p["kind"] == "client_number_mismatch"
                      and p["value"] == 49.0]
        self.assertEqual(len(transplant), 1)
        self.assertEqual(transplant[0]["client"], "Horizons ABA")

    def test_the_same_number_on_the_right_client_passes(self):
        ok, problems = evidence.validate_draft(
            "I built 49+ Airtable bases for Hermon, replicated across brands.",
            self.ref, [], self.job)
        self.assertTrue(ok, problems)

    def test_a_real_horizons_claim_passes(self):
        ok, problems = evidence.validate_draft(
            "For Horizons ABA I built 300+ Make.com scenarios, 197+ of them"
            " still active after 19 months.", self.ref, [], self.job)
        self.assertTrue(ok, problems)


class TestValidatorBoundaries(unittest.TestCase):
    def setUp(self):
        self.ref = scoring.proposal_reference()

    def test_numbers_from_the_job_post_are_allowed(self):
        job = {"title": "Sync 4,200 records nightly",
               "description": "we have 37 brands"}
        ok, p = evidence.validate_draft(
            "Syncing 4,200 records across 37 brands is the shape of this.",
            self.ref, [], job)
        self.assertTrue(ok, p)

    def test_enumeration_is_not_a_claim(self):
        ok, p = evidence.validate_draft(
            "I would break this into 3 phases over 2 weeks.", self.ref, [],
            {"title": "t", "description": ""})
        self.assertTrue(ok, p)

    def test_a_supplied_fact_licenses_its_own_numbers(self):
        f = fact("A client needed 125,000 minutes of AI calling per month")
        ok, p = evidence.validate_draft(
            "One build handled 125,000 minutes of calling a month.",
            self.ref, [f], {"title": "t", "description": ""})
        self.assertTrue(ok, p)

    def test_invented_third_party_pricing_is_caught(self):
        """Measured on the existing corpus: the model invents SaaS pricing
        ("Clay: ~$149-$349/mo") that no source states and a client can check."""
        ok, problems = evidence.validate_draft(
            "Tooling: Clay at roughly $149 to $349 a month depending on volume.",
            self.ref, [], {"title": "t", "description": ""})
        self.assertFalse(ok)
        self.assertEqual({p["value"] for p in problems}, {149.0, 349.0})

    def test_a_digit_inside_a_product_name_is_not_a_claim(self):
        """Measured 2026-08-16: a draft was blocked twice over "360" read out
        of "360dialog", the WhatsApp BSP. Same shape as n8n and GPT-4o."""
        self.assertEqual(
            evidence.numbers_in("360dialog, n8n and GPT-4o"), set())
        # a bare version decimal survives extraction but is below the free
        # ceiling, so it is enumeration rather than a claim
        self.assertEqual(evidence.numbers_in("Claude 4.5"), {4.5})
        ok, p = evidence.validate_draft(
            "WhatsApp goes through 360dialog; the AI step is GPT-4o on"
            " Claude 4.5.", self.ref, [], {"title": "t", "description": ""})
        self.assertTrue(ok, p)

    def test_a_real_figure_next_to_punctuation_still_counts(self):
        self.assertEqual(
            evidence.numbers_in("3,000+ automations at $50/hr, 197+ active"),
            {3000.0, 50.0, 197.0})

    def test_a_clean_draft_passes(self):
        ok, p = evidence.validate_draft(
            "Hey, the hard part is the retry logic, not the integration.\n\n"
            "Quick context on me: I run Etherwise and I have built 3,000+"
            " Make.com automations over three years.\n\nRate: $50/hr.\n\n"
            "Best, Abhijeet Gandhi", self.ref, [],
            {"title": "t", "description": ""})
        self.assertTrue(ok, p)


class TestRetryNote(unittest.TestCase):
    def test_it_names_what_failed(self):
        note = evidence.retry_note([
            {"kind": "unsourced_number", "value": 7000.0},
            {"kind": "client_number_mismatch", "value": 49.0,
             "client": "Horizons ABA"}])
        self.assertIn("7000", note)
        self.assertIn("Horizons ABA", note)
        self.assertIn("REWRITE", note)

    def test_empty_when_there_is_nothing_to_say(self):
        self.assertEqual(evidence.retry_note([]), "")


class TestBlockRendering(unittest.TestCase):
    def test_numbering_and_citations(self):
        block = evidence.render_block([fact("A"), fact("B")])
        self.assertIn("[E1] A", block)
        self.assertIn("[E2] B", block)
        self.assertIn("source:", block)

    def test_no_facts_no_block(self):
        self.assertEqual(evidence.render_block([]), "")


class TestHonestDegradation(unittest.TestCase):
    """Below MIN_FACTS we do not pad and we do not stretch. 374 facts exist for
    make.com and 283 for airtable, but 3 for Shopify and none for Retell."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "t.db"
        db.migrate(self.db)
        with db.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO sources (id, source_type, source_ref,"
                " content_hash, occurred_dt, status) VALUES"
                " (1,'fathom','1','h','2026-08-01','done')")
            for i, text in enumerate((
                "Airtable's native automations cannot use third-party"
                " JavaScript libraries, a limitation that forces a script step.",
                "Airtable bases hit a 500,000 records per table limit, so"
                " transactional rows must be offloaded.",
                "Airtable interfaces cannot filter on a lookup field, which"
                " requires a rollup helper column.",), 1):
                conn.execute(
                    "INSERT INTO facts (id, source_id, category, fact_text,"
                    " citation, confidence, scope) VALUES"
                    " (?,1,'architecture',?,?,'CONFIRMED','agency')",
                    (i, text, "fathom/1 @ 00:0%d" % i))
                conn.execute("INSERT INTO facts_fts (rowid, fact_text)"
                             " VALUES (?,?)", (i, text))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_covered_stack_gets_a_block(self):
        out = evidence.for_job({"title": "Airtable developer",
                                "description": ""}, db_path=self.db)
        self.assertEqual(out["status"], "supplied")
        self.assertGreaterEqual(out["found"], evidence.MIN_FACTS)
        self.assertIn("[E1]", out["block"])

    def test_an_uncovered_stack_degrades_and_says_so(self):
        out = evidence.for_job({"title": "Retell voice agent build",
                                "description": "retell and kommo"},
                               db_path=self.db)
        self.assertEqual(out["status"], "none")
        self.assertEqual(out["reason"], "insufficient_graded_facts")
        self.assertEqual(out["block"], "")


class TestTheEmitLadder(unittest.TestCase):
    """1. evidence draft. 2. rejected -> plain draft, told what failed.
    3. rejected again -> NO DRAFT. A claim that traces to nothing does not get
    to sit on a ClickUp card one copy-paste from a client."""

    JOB = {"id": "1", "title": "Airtable build", "description": ""}

    def _run(self, texts, ev_status="none", facts=None):
        ev = {"status": ev_status, "found": len(facts or []),
              "block": "EVIDENCE\n[E1] x" if ev_status == "supplied" else "",
              "facts": facts or [], "stack": ["airtable"]}
        with mock.patch.object(evidence, "for_job", return_value=ev), \
             mock.patch.object(scoring, "draft_proposal",
                               side_effect=list(texts)) as dp:
            return scoring.draft_proposal_grounded(self.JOB, "t"), dp

    def test_a_clean_first_draft_is_emitted(self):
        (text, prov), dp = self._run(["Hey, three phases over two weeks."])
        self.assertTrue(text)
        self.assertTrue(prov["validated"])
        self.assertEqual(dp.call_count, 1)

    def test_a_fabricating_draft_falls_back_and_is_told_why(self):
        (text, prov), dp = self._run(
            ["Clay costs $149 to $349 a month.", "Tooling cost depends on volume."],
            ev_status="supplied", facts=[fact("A")])
        self.assertEqual(text, "Tooling cost depends on volume.")
        self.assertEqual(dp.call_count, 2)
        self.assertEqual(prov["attempt"], "plain")
        self.assertEqual(prov["status"], "none")
        self.assertEqual(prov["reason"], "evidence_draft_failed_validation")
        retry = dp.call_args_list[1].kwargs["evidence_block"]
        self.assertIn("REWRITE", retry)
        self.assertIn("149", retry)

    def test_a_thin_evidence_job_still_gets_a_second_rung(self):
        """Measured 2026-08-16: with evidence absent the ladder had ONE rung,
        so a job with a thin stack got a single shot and was blocked outright.
        That is a coin flip, not a fallback."""
        (text, prov), dp = self._run(
            ["Clay costs $149 a month.", "Tooling cost depends on volume."],
            ev_status="none")
        self.assertEqual(text, "Tooling cost depends on volume.")
        self.assertEqual(dp.call_count, 2)
        self.assertTrue(prov["retried"])
        self.assertIn("REWRITE", dp.call_args_list[1].kwargs["evidence_block"])

    def test_fabricating_twice_emits_nothing_at_all(self):
        (text, prov), dp = self._run(
            ["Clay costs $149 a month.", "Clay costs $349 a month."],
            ev_status="supplied", facts=[fact("A")])
        self.assertIsNone(text)
        self.assertFalse(prov["validated"])
        self.assertEqual(prov["reason"], "validation_failed_on_every_attempt")
        self.assertTrue(prov["problems"])

    def test_provenance_records_the_supplied_facts(self):
        (text, prov), _ = self._run(["Three phases."], ev_status="supplied",
                                    facts=[fact("A cited thing", tool="airtable")])
        self.assertTrue(text)
        self.assertEqual(prov["status"], "supplied")
        self.assertEqual(prov["facts"][0]["tool"], "airtable")
        self.assertIn("citation", prov["facts"][0])



class TestClaimsWithoutNumbers(unittest.TestCase):
    """2026-10-12: the first draft written with a job's screening questions said
    "I hold the Airtable Builder Certification" (the job's own gate) and "over
    five years". Neither carries a number the number checks could see."""

    def problems(self, text):
        return evidence.claim_problems(text)

    def test_a_claimed_certification_is_a_problem(self):
        for text in ("I hold the Airtable Builder Certification and am a partner.",
                     "I am Airtable certified."):
            self.assertEqual([p["kind"] for p in self.problems(text)],
                             ["credential_claim"], text)

    def test_saying_he_does_not_hold_one_is_fine(self):
        for text in ("I do not hold the Airtable Builder Certification yet.",
                     "I have not taken the certification, but I have built 49+ bases."):
            self.assertEqual(self.problems(text), [], text)

    def test_more_years_than_he_has_is_a_problem(self):
        for text in ("I have been doing this kind of work for over five years.",
                     "I have 7 years of experience.", "I have Five years in Make."):
            self.assertEqual([p["kind"] for p in self.problems(text)],
                             ["years_claim"], text)

    def test_his_real_spans_and_the_clients_years_are_fine(self):
        for text in ("Over the past three years I have built 3,000+ Make.com automations.",
                     "I have been their architect for close to two years.",
                     "Your team has run this process for ten years, and I would start there."):
            self.assertEqual(self.problems(text), [], text)

    def test_the_validator_rejects_them_and_says_why(self):
        ok, problems = evidence.validate_draft(
            "Hi,\n\nI hold the Airtable Builder Certification.", "", [], {})
        self.assertFalse(ok)
        note = evidence.retry_note(problems)
        self.assertIn("no certification is on record", note)


if __name__ == "__main__":
    unittest.main()
