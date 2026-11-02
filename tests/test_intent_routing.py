"""The proposer answers TWO questions, and the second one decides the board.

Abhijeet, 2026-08-20: *"I don't know why I am having to tell you about
donotreply upwork emails and everything. Those are obviously not client 99% of
the time. Are you not using some sort of AI to categorize these?"*

It was — on one question. `mapping._SYSTEM` asked which client id an email
concerned and nothing else, so for a 1Password invoice the model answered `null`
correctly, and `null` was read as "a human must decide". Ninety rows sat on the
Review board; seventeen needed him.

"I cannot attribute this" and "this needs a person" are different claims. These
tests hold them apart.
"""
from __future__ import annotations

import unittest

from modules.jarvis import mapping


class TheRoutingTableIsComplete(unittest.TestCase):
    def test_every_intent_has_a_destination(self):
        """A new intent cannot be added without deciding what happens to it."""
        for i in mapping.INTENTS:
            self.assertIn(i, mapping.INTENT_ROUTE, i)

    def test_destinations_are_only_the_four(self):
        # 'lead' joined on 2026-11-01: a prospect's row goes to the leads table
        # (migration 046) and the event is filed under it — not review, not noise
        self.assertEqual(set(mapping.INTENT_ROUTE.values()),
                         {"review", "file", "drop", "lead"})

    def test_every_filed_intent_has_a_reason_prefix(self):
        """Filed is not deleted — it must land under a reason you can query."""
        for intent, dest in mapping.INTENT_ROUTE.items():
            if dest == "file":
                self.assertIn(intent, mapping.FILE_REASON, intent)

    def test_the_schema_and_the_vocabulary_agree(self):
        self.assertEqual(
            mapping.PROPOSAL_SCHEMA["properties"]["intent"]["enum"],
            list(mapping.INTENTS))
        self.assertIn("intent", mapping.PROPOSAL_SCHEMA["required"])


class WhereThingsGo(unittest.TestCase):
    def test_a_person_reaches_him(self):
        self.assertEqual(mapping.route("client_message"), "review")
        # a lead reaches him too — on Today's "New leads" line and Pipeline ›
        # Leads, through its own table, instead of sitting in a mapping queue
        self.assertEqual(mapping.route("lead"), "lead")
        self.assertEqual(mapping.route("lead", protected=True), "review")

    def test_notifications_are_filed_not_asked_about(self):
        """'Ryan Stomberg hired you for' reports a fact. 'Your ClickUp Plan
        Renewal' is our own money. Neither is a question."""
        self.assertEqual(mapping.route("relationship_event"), "file")
        self.assertEqual(mapping.route("account_notice"), "file")
        self.assertEqual(mapping.route("ops_alert"), "file")

    def test_marketing_is_dropped(self):
        self.assertEqual(mapping.route("marketing"), "drop")

    def test_an_unknown_intent_goes_to_him(self):
        """Over-filing loses a client; over-asking costs a minute. The safe
        default is the expensive one."""
        self.assertEqual(mapping.route("something_new"), "review")
        self.assertEqual(mapping.route(""), "review")
        self.assertEqual(mapping.route(None), "review")


class TransactionalAlwaysWins(unittest.TestCase):
    """'Background check paused: more information needed' reads exactly like an
    account_notice, and is the thing blocking the Globaltize start date. That
    judgement is too expensive to hand to a classifier."""

    def test_protection_overrides_every_intent(self):
        for i in mapping.INTENTS:
            self.assertEqual(mapping.route(i, protected=True), "review", i)


class AReplyIsNeverDropped(unittest.TestCase):
    """Measured on the first live run: 'Re: Potential AWS Credits for
    Etherwise', from a named human at sentinelfox.com, was classified
    `marketing` — credits sound promotional — and would have been binned. It is
    a live conversation about money for Etherwise.

    Marketing does not arrive as a reply to a thread you are already in. Cold
    outreach fakes 'Re:' occasionally and that is the right trade: over-asking
    costs a minute, dropping a reply costs the conversation."""

    def test_the_live_false_negative(self):
        self.assertEqual(
            mapping.route("marketing",
                          subject="Re: Potential AWS Credits for Etherwise"),
            "review")

    def test_reply_prefixes(self):
        for s in ("Re: x", "RE: x", "re:x", "Fwd: x", "FW: x", "Aw: x",
                  "  Re : x".replace(" :", ":")):
            self.assertEqual(mapping.route("marketing", subject=s), "review", s)

    def test_a_fresh_campaign_is_still_dropped(self):
        for s in ("HACKATHONS just for you, abhijeet",
                  "Power House Is Open — Come Join Me.",
                  "Here are some new things we shipped",
                  "Rethinking your pipeline: a webinar"):
            self.assertEqual(mapping.route("marketing", subject=s), "drop", s)

    def test_a_word_starting_with_re_is_not_a_reply(self):
        self.assertEqual(
            mapping.route("marketing", subject="Rethink your CRM"), "drop")
        self.assertEqual(
            mapping.route("marketing", subject="Reminder: our webinar"), "drop")

    def test_filing_is_not_overridden_by_a_reply(self):
        """Only the DROP is reversed. A filed row is still findable, so there is
        nothing to rescue it from."""
        self.assertEqual(
            mapping.route("account_notice", subject="Re: Your invoice"), "file")


class ProposerOutputIsSanitised(unittest.TestCase):
    class _Ctx:
        def __init__(self, parsed):
            self.parsed = parsed

        def claude(self, **kw):
            return self

        def log(self, *a):
            pass

    def _propose(self, parsed):
        ev = {"id": "e1", "author": "a@b.com", "subject": "s", "text": "t"}
        return mapping.propose(self._Ctx(parsed), ev, [], {"c1"})

    def test_a_hallucinated_intent_falls_back_to_him(self):
        got = self._propose({"proposed_client_id": None, "confidence": 0.9,
                             "evidence": "e", "intent": "totally_made_up"})
        self.assertEqual(got["intent"], "client_message")
        self.assertEqual(mapping.route(got["intent"]), "review")

    def test_a_missing_intent_falls_back_to_him(self):
        got = self._propose({"proposed_client_id": None, "confidence": 0.1,
                             "evidence": "e"})
        self.assertEqual(got["intent"], "client_message")

    def test_a_valid_intent_survives(self):
        got = self._propose({"proposed_client_id": None, "confidence": 0.5,
                             "evidence": "e", "intent": "account_notice"})
        self.assertEqual(got["intent"], "account_notice")

    def test_a_hallucinated_client_id_is_still_dropped(self):
        got = self._propose({"proposed_client_id": "not-a-client",
                             "confidence": 0.9, "evidence": "e",
                             "intent": "client_message"})
        self.assertIsNone(got["proposed_client_id"])

    def test_a_pitch_concerns_no_client_whatever_name_it_drops(self):
        """Terry Benjamin, 2026-10-27: a freelancer offering overflow capacity,
        citing our Horizons ABA case study -> proposed to horizons-aba at 0.85.
        Abhijeet: "this is one clearly asking for work ... how is this for
        client review? or assigned to horizons aba". The id is nulled so the
        filed reason cannot carry a client, and the note says what was dropped."""
        for intent in mapping.PITCH_INTENTS:
            got = self._propose({"proposed_client_id": "c1", "confidence": 0.85,
                                 "evidence": "Horizons ABA case mentioned as reference",
                                 "intent": intent})
            self.assertIsNone(got["proposed_client_id"], intent)
            self.assertEqual(got["proposal_note"], "pitch-concerns-no-client:c1")
            self.assertEqual(got["intent"], intent)
        # a client_message naming a client keeps it — the guard is pitch-only
        got = self._propose({"proposed_client_id": "c1", "confidence": 0.9,
                             "evidence": "e", "intent": "client_message"})
        self.assertEqual(got["proposed_client_id"], "c1")


class PitchesAreFiledNotReviewed(unittest.TestCase):
    """Someone who wants something FROM us — work, a sale, a backlink — is not a
    question for Abhijeet and is not a client's thread. Filed, so the day he
    hires, 'everyone who asked to work with us' is one query."""

    def test_both_pitch_intents_file_under_their_own_reason(self):
        self.assertEqual(mapping.route("talent_pitch"), "file")
        self.assertEqual(mapping.route("vendor_pitch"), "file")
        self.assertEqual(mapping.FILE_REASON["talent_pitch"], "filed:talent-pitch")
        self.assertEqual(mapping.FILE_REASON["vendor_pitch"], "filed:vendor-pitch")

    def test_a_pitch_s_own_follow_up_is_still_filed(self):
        # "Re: Paid automation implementation capacity" is Terry replying to
        # Terry. The reply override protects against DROPPING a thread; filing
        # keeps it findable, so the override does not apply.
        self.assertEqual(mapping.route("talent_pitch",
                                       subject="Re: Paid automation implementation capacity"),
                         "file")

    def test_the_prompt_says_a_cited_client_is_not_the_client(self):
        self.assertIn("merely NAMES", mapping._SYSTEM)
        self.assertIn("talent_pitch", mapping._SYSTEM)
        self.assertIn("vendor_pitch", mapping._SYSTEM)


if __name__ == "__main__":
    unittest.main()
