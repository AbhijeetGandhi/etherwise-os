"""Who was on the call — the identity half of Fathom attribution (2026-08-14).

Every test here is a specific thing that went wrong on the live corpus while a
queue of 238 items was being drained, and the comment says which:

  * a queue row whose entire question was "who is 'j'?" (junk speaker labels)
  * `rockychoy` — an EXISTING client with a contract, unreachable because the
    registry holds 'rocky-choy' and norm() keeps the separator
  * a call auto-mapped to `dane-worthington` at 0.92 whose only external speaker
    was Dhanya, because `dhanya-sangolli` had a stub dossier, therefore no
    fingerprint, therefore was not an answer the model was allowed to give
  * five hermon calls excluded as 'internal-product' with Jaap Meijvogel and
    Abel Jansen — both registered hermon identifiers — audibly on them

Fully offline: no model, no network.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core import registry
from modules.jarvis import fathom_bridge, loom_classifier

REG_YAML = """\
clients:
  -
    id: rocky-choy
    name: Rocky Choy
    billing:
      canonical: Rocky Choy
  -
    id: dhanya-sangolli
    name: Dhanya Sangolli
    aliases:
      - "Dhanya S"
    billing:
      canonical: Dhanya Sangolli
  -
    id: dane-worthington
    name: Dane Worthington
    billing:
      canonical: Dane Worthington
  -
    id: hermon
    name: Hermon
    aliases:
      - "Jaap Meijvogel"
    emails:
      - hermon.io
    billing:
      canonical: Hermon
  -
    id: horizons-aba
    name: Horizons ABA
    emails:
      - horizonsaba.com
    billing:
      canonical: Horizons ABA
"""


SHARED_TOKEN_YAML = """\
clients:
  -
    id: dhanya-sangolli
    name: Dhanya Sangolli
    billing:
      canonical: Dhanya Sangolli
  -
    id: horizons-aba
    name: Horizons ABA
    aliases:
      - "Dhanya Patel"
    billing:
      canonical: Horizons ABA
"""


def _reg():
    tmp = tempfile.TemporaryDirectory()
    p = Path(tmp.name) / "clients.yaml"
    p.write_text(REG_YAML)
    reg = registry.load(p, force=True)
    reg._tmp = tmp                      # keep the dir alive for the test's life
    return reg


class JunkLabelTests(unittest.TestCase):
    """A video tool writes whatever the participant typed into the join box."""

    def test_devices_and_desks_are_not_people(self):
        for s in ("iPhone", "iPad", "Abhijeet's iPhone", "Guest", "Admin CS",
                  "Meeting Room 2", "SM-G991B", "unknown speaker", "Speaker 1",
                  "Conference Room", "Notetaker"):
            self.assertTrue(fathom_bridge.is_junk_label(s), s)

    def test_initials_and_single_letters_are_not_people(self):
        # measured live: 'j' authored two queue rows, 'AP' one. A row whose whole
        # question is "who is 'j'?" cannot be answered by the human either.
        for s in ("j", "AP", "JK", "MRB", "x", "  "):
            self.assertTrue(fathom_bridge.is_junk_label(s), s)

    def test_real_names_survive(self):
        for s in ("Jackson Ly", "Matthijs van der Plas", "rockychoy", "Dhanya",
                  "Dr Ahmed", "Rod", "Amar", "oleksandraponomarenko"):
            self.assertFalse(fathom_bridge.is_junk_label(s), s)

    def test_external_signal_ranks_a_name_above_a_device(self):
        """Junk is ranked last, not dropped: somebody external WAS on the call,
        so it is not internal — but the label must not become the row's author,
        because the assign path offers the author to clients.yaml as an alias."""
        self.assertEqual(
            fathom_bridge._external_signal(["Abhijeet Gandhi", "iPhone",
                                            "Jackson Ly"]), "Jackson Ly")

    def test_external_signal_keeps_junk_when_it_is_all_there_is(self):
        self.assertEqual(
            fathom_bridge._external_signal(["Abhijeet Gandhi", "iPhone"]),
            "iPhone")

    def test_external_signal_none_when_everyone_is_us(self):
        self.assertIsNone(
            fathom_bridge._external_signal(["Abhijeet Gandhi", "Jay Pokharna"]))

    def test_an_address_still_wins_outright(self):
        self.assertEqual(
            fathom_bridge._external_signal(["Jackson Ly", "a@beglaubigt.de"]),
            "a@beglaubigt.de")


class SignalBlockTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "recording_id": "1", "title": "Impromptu Zoom Meeting",
            "participants": [{"name": "Abhijeet Gandhi"}],
            "transcript": [
                {"speaker": {"display_name": "Abhijeet Gandhi"}, "text": "hi"},
                {"speaker": {"display_name": "Jaap Meijvogel",
                             "matched_calendar_invitee_email": "jaap@hermon.io"},
                 "text": "the setter form"},
                {"speaker": "iPhone", "text": "hello"},
            ]}

    def test_speakers_domains_and_junk_are_separated(self):
        sig = loom_classifier.fathom_signals(self.payload)
        self.assertEqual(sig["external_speakers"], ["Jaap Meijvogel"])
        self.assertEqual(sig["invitee_email_domains"], ["hermon.io"])
        self.assertEqual(sig["non_person_labels"], ["iPhone"])

    def test_our_own_people_are_never_external(self):
        sig = loom_classifier.fathom_signals(self.payload)
        self.assertNotIn("Abhijeet Gandhi", sig["external_speakers"])

    def test_unreadable_payload_is_empty_not_an_error(self):
        self.assertEqual(loom_classifier.fathom_signals({}),
                         {"external_speakers": [], "invitee_email_domains": [],
                          "non_person_labels": []})


class FoldedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.reg = _reg()

    def test_rockychoy_reaches_rocky_choy(self):
        """registry.norm() collapses internal whitespace but keeps it, so
        'rocky choy' never equals 'rockychoy' and the strict matcher misses a
        client with a contract."""
        cid, why = loom_classifier.folded_identity_match(self.reg, ["rockychoy"])
        self.assertEqual(cid, "rocky-choy")
        self.assertIn("rockychoy", why)

    def test_two_clients_in_one_room_is_not_an_identifier(self):
        self.assertIsNone(loom_classifier.folded_identity_match(
            self.reg, ["rockychoy", "Dane Worthington"]))

    def test_short_keys_are_refused(self):
        # below FOLD_MIN_LEN an alnum key is a coincidence, and a wrong
        # client_id is permanent.
        self.assertIsNone(loom_classifier.folded_identity_match(self.reg, ["j"]))


class SpeakerHintTests(unittest.TestCase):
    def setUp(self):
        self.reg = _reg()

    def test_bare_given_name_claimed_by_exactly_one_client(self):
        self.assertEqual(loom_classifier.speaker_client_hints(self.reg,
                                                              ["Dhanya"]),
                         {"dhanya-sangolli"})

    def test_full_name_via_the_strict_matcher(self):
        self.assertEqual(loom_classifier.speaker_client_hints(
            self.reg, ["Jaap Meijvogel"]), {"hermon"})

    def test_a_token_two_clients_share_is_dropped(self):
        """'A name appearing across multiple clients is not an identifier.'"""
        idx = loom_classifier._name_token_index(self.reg)
        self.assertEqual(idx.get("dhanya"), "dhanya-sangolli")
        self.assertNotIn("aba", idx)               # under the 4-char floor

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "clients.yaml"
            p.write_text(SHARED_TOKEN_YAML)
            shared = registry.load(p, force=True)
            self.assertIsNone(
                loom_classifier._name_token_index(shared).get("dhanya"))
            self.assertEqual(
                loom_classifier.speaker_client_hints(shared, ["Dhanya"]), set())

    def test_junk_and_unknown_names_hint_nothing(self):
        self.assertEqual(loom_classifier.speaker_client_hints(
            self.reg, ["iPhone", "William Shaw", "Rod"]), set())


class SpeakerGuardTests(unittest.TestCase):
    """'Stated vs observed', enforced in code rather than hoped for in a prompt."""

    def test_conflict_demotes_an_auto_map_to_a_queued_proposal(self):
        # THE live error: 0.92 -> dane-worthington on a call whose only external
        # speaker was Dhanya, who is a client in her own right.
        action, detail, ev = loom_classifier.speaker_guard(
            "map", "dane-worthington", {"dhanya-sangolli"}, "VAPI + Twilio")
        self.assertEqual((action, detail), ("queue", "dhanya-sangolli"))
        self.assertIn("CONFLICT", ev)
        self.assertIn("VAPI + Twilio", ev)         # both readings stay visible

    def test_agreement_is_corroboration_and_changes_nothing(self):
        self.assertEqual(loom_classifier.speaker_guard(
            "map", "hermon", {"hermon"}, "setter, EOD form")[:2],
            ("map", "hermon"))

    def test_a_client_in_the_room_falsifies_internal(self):
        action, detail, _ = loom_classifier.speaker_guard(
            "exclude", "fathom-kind:internal-product", {"hermon"}, "")
        self.assertEqual((action, detail), ("queue", "hermon"))

    def test_two_hints_are_not_an_identifier(self):
        self.assertEqual(loom_classifier.speaker_guard(
            "map", "hermon", {"hermon", "rocky-choy"}, "")[:2], ("map", "hermon"))

    def test_no_hint_leaves_the_verdict_alone(self):
        self.assertEqual(loom_classifier.speaker_guard(
            "exclude", "fathom-kind:personal-noise", set(), "")[:2],
            ("exclude", "fathom-kind:personal-noise"))


class CandidateFloorTests(unittest.TestCase):
    def setUp(self):
        self.reg = _reg()
        self.valid = set(self.reg.matchable_ids())

    def test_every_matchable_client_is_an_answer_the_model_may_give(self):
        """A candidate list that omits a client makes every call about that
        client a guaranteed error — measured, not theorised."""
        fps = [{"client_id": "hermon", "name": "Hermon", "domain": "UGC coaching",
                "stack": ["Make"], "terms": ["setter"], "people": [],
                "systems": []}]
        cand = loom_classifier.fingerprint_candidates(fps, self.valid, self.reg)
        self.assertEqual({c["id"] for c in cand}, self.valid)

    def test_a_client_without_vocabulary_says_so(self):
        cand = loom_classifier.fingerprint_candidates([], self.valid, self.reg)
        by = {c["id"]: c for c in cand}
        self.assertIn("no_vocabulary", by["dhanya-sangolli"])
        self.assertIn("Dhanya Sangolli", by["dhanya-sangolli"]["people"])
        self.assertEqual(by["dhanya-sangolli"]["terms"], [])

    def test_without_a_registry_the_old_behaviour_is_unchanged(self):
        self.assertEqual(loom_classifier.fingerprint_candidates([], self.valid),
                         [])


if __name__ == "__main__":
    unittest.main()
