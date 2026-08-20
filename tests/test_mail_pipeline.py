"""Shared mail pipeline (transport-agnostic) — the ONE classify + assemble path
that the Gmail API rail and the IMAP rail both call, so their noise/self-noise
filtering and event shape can never diverge (the bug class J1 spent a night on).

Fully offline: no network, no Gmail/IMAP shape — just header strings in, a
normalized event dict out, plus a lazy body thunk that must fire ONLY on pass.
"""
from __future__ import annotations

import unittest

from modules.jarvis import mail_pipeline as mp


class ClassifyTests(unittest.TestCase):
    def _classify(self, frm, subj="Hi"):
        return mp.classify("email", "n1", frm, subj, "2026-08-09T10:00:00+00:00",
                           "https://link/n1", "email/n1")

    def test_clean_client_passes(self):
        ev = self._classify("Chris Cave <chris@adelye.com>", "Cert update")
        self.assertTrue(ev["_pass"])
        self.assertIsNone(ev["_reason"])
        self.assertEqual(ev["id"], "evt:email:n1")
        self.assertEqual(ev["source"], "email")
        self.assertEqual(ev["native_id"], "n1")
        self.assertEqual(ev["author"], "chris@adelye.com")
        self.assertEqual(ev["permalink"], "https://link/n1")
        self.assertEqual(ev["raw_ref"], "email/n1")

    def test_noise_sender_excluded(self):
        ev = self._classify("Google <no-reply@accounts.google.com>", "Security alert")
        self.assertFalse(ev["_pass"])
        self.assertTrue(ev["_reason"].startswith("noise:"))

    def test_self_noise_wins_over_client_shape(self):
        # our own automation domain — excluded, and tagged self-noise (checked first)
        ev = self._classify("Etherwise Bot <bot@etherwise.io>", "Hot Upwork Leads")
        self.assertFalse(ev["_pass"])
        self.assertTrue(ev["_reason"].startswith("self-noise"))


class AssembleTests(unittest.TestCase):
    def test_excluded_never_fetches_body(self):
        called = []

        def body():
            called.append(1)
            return {"text": "SHOULD NOT BE READ"}

        # Sender changed 2026-08-20: this used to be 'no-reply@stripe.com',
        # which is now an OPERATIONAL platform and deliberately protected — a
        # Stripe receipt is a vendor cost, and 'Your receipt' was one of the
        # subjects the old gate was wrongly eating. The claim under test is
        # metadata-first (an excluded row never costs a body fetch), so it needs
        # a sender that is still noise, not a sender that used to be.
        ev = mp.assemble("email", "n2", "noreply@sendgrid.net", "Your receipt",
                         "2026-08-09T10:00:00+00:00", "l", "r", body)
        self.assertEqual(ev["state"], "excluded")
        self.assertIsNone(ev["text"])
        self.assertTrue(ev["exclude_reason"].startswith(("noise:", "self-noise")))
        self.assertEqual(called, [])                # metadata-first: no body fetch

    def test_passing_fetches_body_and_holds(self):
        ev = mp.assemble("email", "n3", "jane@realco.com", "Question",
                         "2026-08-09T10:00:00+00:00", "l", "r",
                         lambda: {"text": "can you help with Make?"})
        self.assertEqual(ev["state"], "held")
        self.assertEqual(ev["text"], "can you help with Make?")

    def test_ts_falls_back_to_body_when_header_missing(self):
        ev = mp.assemble("email", "n4", "jane@realco.com", "Q", None, "l", "r",
                         lambda: {"text": "body", "ts": "2026-08-09T09:00:00+00:00"})
        self.assertEqual(ev["ts"], "2026-08-09T09:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
