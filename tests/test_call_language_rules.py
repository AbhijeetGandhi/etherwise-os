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
        v = R.classify(turns(("Abhijeet Gandhi", "मुझे लगता है यह ठीक है"),
                             ("Jay Pokharna", "हाँ सही है")))
        self.assertEqual(v["rule"], "hindi")
        self.assertEqual(v["kind"], "internal")
        self.assertIsNone(v["client_id"])

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


if __name__ == "__main__":
    unittest.main()
