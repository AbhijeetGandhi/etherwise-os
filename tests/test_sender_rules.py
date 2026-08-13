"""Sender triage — offline.

Abhijeet's instruction (2026-08-10) was that obvious promotional mail should
never reach him, and that anything he DOES answer must never be asked again.
Both are behaviours, so both are tested.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from modules.jarvis import sender_rules as SR

RULES = SR.load_rules()


def cls(sender, **kw):
    kw.setdefault("rules", RULES)
    kw.setdefault("learned", {})
    return SR.classify(sender, **kw)


class StructuralTests(unittest.TestCase):
    def test_bank_tld_is_excluded_without_being_listed(self):
        # the point of a structural rule: a bank nobody listed still gets caught
        v = cls("news@mailers.somebanknobodylisted.bank.in")
        self.assertEqual(v["verdict"], SR.EXCLUDE)

    def test_bulk_subdomains_generalise(self):
        for s in ("x@mailers.hdfcbank.bank.in", "x@marketing.goindigo.in",
                  "x@updates.easemytrip.com", "x@em1.cloudflare.com",
                  "x@info.clickup.com", "x@e.atlassian.com"):
            self.assertEqual(cls(s)["verdict"], SR.EXCLUDE, s)

    def test_noreply_local_part(self):
        self.assertEqual(cls("noreply@somewhere-new.com")["verdict"], SR.EXCLUDE)

    def test_named_categories(self):
        # the three Abhijeet called out by name
        for s in ("newsletter@chessbase.com", "x@hdfcfund.com", "x@axismf.com"):
            self.assertEqual(cls(s)["verdict"], SR.EXCLUDE, s)

    def test_plus_tag_is_the_sender_saying_what_the_mail_is(self):
        """`nick+news@leftclick.ai` — the sender chose the tag. It generalises to
        every marketer using the convention, which no domain list does."""
        for s in ("nick+news@leftclick.ai", "a+newsletter@brand-new-vendor.io",
                  "x+promo@somewhere.com"):
            v = cls(s)
            self.assertEqual(v["verdict"], SR.EXCLUDE, s)
            self.assertTrue(v["rule"].startswith("plus-tag:"), v)

    def test_an_ordinary_plus_address_is_untouched(self):
        # a client writing from first+last@ must not be swept up
        self.assertEqual(cls("jane+acme@some-unknown-agency.com")["verdict"],
                         SR.ASK)

    def test_the_held_backlog_senders_are_all_decided(self):
        """Every distinct sender that was sitting in `held` on 2026-08-14 — 58
        rows the Review badge was asking a human to look at, not one of them a
        client. If a rule change ever un-decides one of these, this fails."""
        for s in ("information@mailers.hdfcbank.bank.in", "support@mail.vapi.ai",
                  "team@mail.clickup.com", "store+92972187970@t.shopifyemail.com",
                  "guest@choose.etihadguest.com", "green_bss@bigshareonline.com",
                  "gogreen@cdslindia.co.in", "announcements@stablemoney.in",
                  "info@getdigit.in", "mailers@bseindia.in", "grant@tella.tv",
                  "mail@depositphotos.com", "nick+news@leftclick.ai",
                  "ian@mail.chatarv.ai", "upwork@t.upwork.com",
                  "shop@fashioncollections.tatacliq.com",
                  "Coursera@m.learn.coursera.org", "info@members.netflix.com",
                  "announce@m.parallels.com", "events@send.zapier.com"):
            self.assertEqual(cls(s)["verdict"], SR.EXCLUDE, s)

    def test_a_real_business_domain_is_still_a_question(self):
        v = cls("jane@some-unknown-agency.com")
        self.assertEqual(v["verdict"], SR.ASK)
        self.assertEqual(v["rule"], "unknown")

    def test_freemail_is_asked_not_excluded(self):
        # a client writing from a personal address is common; excluding would
        # silently drop real client mail
        self.assertEqual(cls("someone@gmail.com")["verdict"], SR.ASK)


class RegistryTests(unittest.TestCase):
    class FakeReg:
        by_id = {"acme": {"emails": ["acme.com"], "aliases": ["Acme"]},
                 "personal-client": {"emails": ["someone@gmail.com"]}}

        def matchable_ids(self):
            return list(self.by_id)

        def match_email(self, a):
            return {"x@acme.com": "acme",
                    "someone@gmail.com": "personal-client"}.get(a)

        def match_name(self, n):
            return "cmfp" if (n or "").lower() == "michael gandia" else None

    def test_client_domain_beats_every_heuristic(self):
        v = cls("x@acme.com", reg=self.FakeReg())
        self.assertEqual(v["verdict"], "acme")

    def test_a_full_address_does_NOT_claim_its_whole_domain(self):
        # the bug this caught: 'tenni.theurer@gmail.com' in the registry made
        # _registered_domains claim gmail.com, so every Gmail sender became a
        # question. A full address claims one person, not the domain.
        v = cls("unrelated-stranger@gmail.com", reg=self.FakeReg())
        self.assertEqual(v["rule"], "freemail")
        self.assertNotEqual(v["rule"], "registry")

    def test_display_name_resolves_a_personal_address(self):
        # mikegandia@gmail.com was unanswerable by domain but the display name
        # is a registered alias — asking about it would have been asking
        # something the system already knew
        v = cls("mikegandia@gmail.com", reg=self.FakeReg(),
                display_name="Michael Gandia")
        self.assertEqual(v["verdict"], "cmfp")
        self.assertEqual(v["rule"], "registry-alias")


class LearnedTests(unittest.TestCase):
    def test_learned_beats_everything_including_a_client_domain(self):
        learned = {"acme.com": {"verdict": SR.EXCLUDE, "reason": "noise:test"}}
        v = SR.classify("x@acme.com", reg=RegistryTests.FakeReg(),
                        rules=RULES, learned=learned)
        self.assertEqual(v["verdict"], SR.EXCLUDE)
        self.assertEqual(v["rule"], "learned")

    def test_learn_persists_so_the_question_never_returns(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "learned.json"
            p.write_text(json.dumps({"mappings": {}}))
            before = SR.classify("x@mystery-corp.com", rules=RULES, learned={})
            self.assertEqual(before["verdict"], SR.ASK)

            SR.learn("x@mystery-corp.com", "acme", "Abhijeet said so", path=p)

            after = SR.classify("anyone@mystery-corp.com", rules=RULES,
                                learned=json.loads(p.read_text())["mappings"])
            self.assertEqual(after["verdict"], "acme")
            self.assertEqual(after["rule"], "learned")

    def test_learn_records_provenance_and_date(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "learned.json"
            rec = SR.learn("x@foo.com", SR.EXCLUDE, "noise:test", path=p)
            self.assertEqual(rec["learned_from"], "abhijeet")
            self.assertTrue(rec["learned_on"])


if __name__ == "__main__":
    unittest.main()
