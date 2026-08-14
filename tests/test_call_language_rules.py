"""Abhijeet's Hindi/Yogita call rules — offline.

Fixtures mirror the real corpus shapes, including the two that mattered:
Fathom stores `speaker` sometimes as a string and sometimes as
{display_name: ...}, and Yogita is named in calls she was never on.
"""
from __future__ import annotations

import unittest

from modules.jarvis import call_language_rules as R


def turns(*pairs):
    return {"transcript": [{"speaker": s, "text": t} for s, t in pairs]}


def turns_nested(*pairs):
    return {"transcript": [{"speaker": {"display_name": s}, "text": t}
                           for s, t in pairs]}


class SpeakerTests(unittest.TestCase):
    def test_yogita_speaking_counts(self):
        self.assertTrue(R.has_yogita(turns(
            ("Abhijeet Gandhi", "hi"), ("Yogita Soma", "haan bolo"))))

    def test_yogita_speaking_counts_in_the_nested_shape(self):
        # both shapes appear in the live corpus; missing one loses ~half the calls
        self.assertTrue(R.has_yogita(turns_nested(
            ("Yogita Soma", "ok"), ("Abhijeet Gandhi", "right"))))

    def test_yogita_merely_MENTIONED_does_not_count(self):
        # the false positive that would have moved 18 other clients' calls to
        # Hermon: "I will have Yogita work on it" in someone else's meeting
        p = turns(("Abhijeet Gandhi", "I will have Yogita work on it."),
                  ("Alex Apfel", "Great."))
        self.assertFalse(R.has_yogita(p))
        self.assertIsNone(R.classify(p)["rule"])

    def test_structured_invitee_still_counts(self):
        self.assertTrue(R.has_yogita(
            {"transcript": [], "participants": [{"name": "Yogita Soma"}]}))


class HindiTests(unittest.TestCase):
    def test_ratio_is_measured_not_guessed(self):
        self.assertAlmostEqual(R.hindi_ratio("अब ok"), 2 / 4, places=3)
        self.assertEqual(R.hindi_ratio("all english here"), 0.0)
        self.assertEqual(R.hindi_ratio(""), 0.0)

    def test_hindi_call_is_internal_and_loses_its_client(self):
        # only our own people spoke -> the STRONG form (see InternalTeamTests);
        # either way the outcome Abhijeet asked for holds: internal, no client
        v = R.classify(turns(("Abhijeet Gandhi", "मुझे लगता है यह ठीक है"),
                             ("Jay Pokharna", "हाँ सही है")))
        self.assertEqual(v["rule"], "internal-team")
        self.assertEqual(v["kind"], "internal")
        self.assertIsNone(v["client_id"])

    def test_hindi_with_an_outsider_stays_the_WEAK_rule(self):
        """The weak rule is the one the speaker guard polices, so a call with
        anyone from outside must land on it — never on the strong verdict,
        which no guard is applied to because it cannot fire here."""
        v = R.classify(turns(("Abhijeet Gandhi", "हाँ ठीक है"),
                             ("Zeeshan Rasheed", "جی بالکل, हाँ भाई")))
        self.assertEqual(v["rule"], "hindi")
        self.assertEqual(v["kind"], "internal")
        self.assertEqual(v["external_speakers"], ["zeeshan rasheed"])

    def test_english_call_is_left_alone(self):
        v = R.classify(turns(("Abhijeet Gandhi", "Let us ship on Friday"),
                             ("Alex Apfel", "Agreed")))
        self.assertIsNone(v["rule"])
        self.assertIsNone(v["kind"])

    def test_cutoff_is_honoured(self):
        p = turns(("A", "one two three four five six सात"))   # ~1 of 8 words
        self.assertEqual(R.classify(p, cutoff=0.01)["rule"], "hindi")
        self.assertIsNone(R.classify(p, cutoff=0.90)["rule"])


class PrecedenceTests(unittest.TestCase):
    def test_yogita_BEATS_hindi_and_goes_to_hermon(self):
        # the 65-call collision: Abhijeet's ruling is that these are Hermon work,
        # tagged so they are never quoted as client-facing
        v = R.classify(turns(("Yogita Soma", "हाँ मैंने वो ticket देख लिया"),
                             ("Abhijeet Gandhi", "ठीक है")))
        self.assertEqual(v["rule"], "yogita")
        self.assertEqual(v["client_id"], "hermon")
        self.assertEqual(v["kind"], "client-work")
        self.assertTrue(v["internal_language"])
        self.assertIn("internal team call", v["evidence"])

    def test_yogita_in_english_is_hermon_but_not_flagged_internal_language(self):
        v = R.classify(turns(("Yogita Soma", "I pushed the fix"),
                             ("Abhijeet Gandhi", "thanks")))
        self.assertEqual(v["client_id"], "hermon")
        self.assertFalse(v["internal_language"])

    def test_every_verdict_carries_its_evidence(self):
        for p in (turns(("Yogita Soma", "ok")),
                  turns(("A", "यह हिंदी है"))):
            self.assertTrue(R.classify(p)["evidence"])


class InternalTeamTests(unittest.TestCase):
    """Rule 2 — *"all devnagri call with jay are internal. tanmay is new
    emplyee so its internal as well."* (Abhijeet, 2026-08-14).

    Encoded as a rule rather than applied to the rows that happened to be
    queued that day, so the next Hindi standup with a teammate is not a fresh
    unknown. The safety property is that it is NARROWER than the plain Hindi
    rule, not wider: an outsider in the room stops it firing at all."""

    def test_devanagari_with_only_our_people_is_the_strong_verdict(self):
        v = R.classify(turns(("Jay Pokharna", "हाँ वो हो गया"),
                             ("Tanmay", "ठीक है भाई")))
        self.assertEqual(v["rule"], "internal-team")
        self.assertEqual(v["kind"], "internal")
        self.assertIsNone(v["client_id"])
        self.assertEqual(v["external_speakers"], [])
        self.assertIn("Jay Pokharna", v["evidence"])
        self.assertIn("no external speaker", v["evidence"])

    def test_it_holds_for_a_NEW_HIRE_without_a_code_change(self):
        """Tanmay is E008, hired 2026-08-10. The roster is derived from
        Payroll/employees.json (config.INTERNAL_PEOPLE), so a hire stops
        looking like a client the moment payroll knows about them."""
        from core import config
        self.assertTrue(config.is_internal_person("Tanmay Abhay Bora"))
        self.assertTrue(config.is_internal_person("Tanmay"))
        self.assertIn("tanmay abhay bora", config.INTERNAL_PEOPLE)

    def test_an_EXTERNAL_speaker_is_never_swallowed_by_it(self):
        """THE regression this rule must never cause. `fathom/117473152` is
        Zeeshan Rasheed's own client call, held in Urdu, which sat on
        `fathom-kind:internal-language` asserting the opposite of the truth.
        A stronger verdict that skipped the speaker guard would make that
        permanent, so the strong verdict declines to fire when anyone outside
        the roster spoke — even alongside two of our own."""
        v = R.classify(turns(("Jay Pokharna", "हाँ ठीक है"),
                             ("Abhijeet Gandhi", "चलो"),
                             ("Zeeshan Rasheed", "हाँ भाई بالکل")))
        self.assertEqual(v["rule"], "hindi")          # the GUARDED path
        self.assertNotEqual(v["rule"], "internal-team")
        self.assertEqual(v["external_speakers"], ["zeeshan rasheed"])

    def test_an_unidentifiable_label_counts_as_external(self):
        """"iPhone", an initial, a half-typed name — we cannot say it was one
        of ours, so we do not say it. Withholding the strong verdict is free:
        the weak rule reaches the same `internal` kind by the guarded path."""
        v = R.classify(turns(("Jay Pokharna", "हाँ ठीक है"),
                             ("iPhone", "हाँ")))
        self.assertEqual(v["rule"], "hindi")
        self.assertEqual(v["external_speakers"], ["iphone"])

    def test_english_team_call_is_still_left_alone(self):
        """The rule is about Devanagari calls. An English internal call is not
        this rule's business — the normal classifier decides."""
        v = R.classify(turns(("Jay Pokharna", "shipped it"),
                             ("Tanmay", "great")))
        self.assertIsNone(v["rule"])
        self.assertEqual(v["internal_speakers"], ["jay pokharna", "tanmay"])

    def test_yogita_still_outranks_it(self):
        v = R.classify(turns(("Yogita Soma", "हाँ मैंने देख लिया"),
                             ("Jay Pokharna", "ठीक है")))
        self.assertEqual(v["rule"], "yogita")
        self.assertEqual(v["client_id"], "hermon")


if __name__ == "__main__":
    unittest.main()
