"""A platform Abhijeet operates on is not a bulk sender.

2026-08-20. The noise rules match the sender's LOCAL PART and ignore the
domain. `donotreply@upwork.com` matched noreply-local 'donotreply' and
`noreply@{eu1,eu2,us2}.make.com` matched 'noreply'. But those are not bulk
senders that happen to mention a client — they are the ONLY addresses those
platforms send from, so the rule was not filtering Upwork's marketing, it was
deleting Upwork.

Measured over 910 structural exclusions: 28 of 30 non-job-alert Upwork rows
were wrong, including "Ryan Stomberg hired you for" (the Globaltize win),
"Your contract has started", "Contract rate increased", "Julie H. sent you a
message" and four weekly summaries; plus 139 Make.com client-automation
failures naming DoulaMatch, All Pandadoc - Sign Watcher, [AUTOFILL] BT RBT
Self and Hipp Follow-up.

The fix has two halves and both are load-bearing. Protecting the DOMAIN without
filtering the MESSAGE would have re-admitted 126 'New job alert' rows the
scanner already reads. Both halves are asserted here.
"""
from __future__ import annotations

import unittest

from modules.jarvis import sender_rules as SR
from modules.jarvis import mail_pipeline


class SubjectNormalisation(unittest.TestCase):
    """Platform subjects arrive RFC2047-encoded. A rule matched against the raw
    string silently matches nothing — which here would mean protecting
    upwork.com and re-admitting every job alert. The decode is the fix's
    load-bearing detail, not a nicety."""

    def test_decodes_q_encoding(self):
        got = SR._subject_norm("=?UTF-8?q?New_job_alert:_Zapier_Expert_Needed?=")
        self.assertEqual(got, "new job alert: zapier expert needed")

    def test_decodes_base64(self):
        got = SR._subject_norm("=?utf-8?B?SGVsbG8gV29ybGQ=?=")
        self.assertEqual(got, "hello world")

    def test_malformed_encoding_does_not_raise(self):
        self.assertIsInstance(SR._subject_norm("=?UTF-8?q?broken"), str)

    def test_none_and_empty(self):
        self.assertEqual(SR._subject_norm(None), "")
        self.assertEqual(SR._subject_norm(""), "")


class OperationalProtection(unittest.TestCase):

    def _v(self, sender, subject=None):
        return SR.operational_verdict(sender, subject)

    # ── the mail that was being deleted ──────────────────────────────────────
    def test_upwork_hire_is_protected(self):
        v = self._v("donotreply@upwork.com", "Ryan Stomberg hired you for")
        self.assertEqual(v["verdict"], SR.ASK)
        self.assertTrue(v["protected"])

    def test_upwork_contract_and_message_mail_is_protected(self):
        for subj in ("Your contract has started",
                     "Contract updated: Ai Automation Engineer",
                     "Contract rate increased: AI & Automation Engineer",
                     "Julie H. sent you a message",
                     "Carly R. scheduled a meeting with you",
                     "Your weekly summary is ready for review",
                     "Tanushant S. of Tanushant Sharma has restarted your contract"):
            self.assertEqual(self._v("donotreply@upwork.com", subj)["verdict"],
                             SR.ASK, subj)

    def test_make_scenario_failures_are_protected(self):
        for host in ("eu1", "eu2", "us2"):
            v = self._v(f"noreply@{host}.make.com",
                        "🛑 Encountered error in All Pandadoc - Sign Watcher scenario")
            self.assertEqual(v["verdict"], SR.ASK, host)

    def test_a_region_nobody_has_seen_is_covered(self):
        """Keying Make on the LOCAL part rather than listing subdomains is what
        makes this hold — us2/eu1/eu2 are not an exhaustive list of regions."""
        v = self._v("noreply@us9.make.com", "🛑 Encountered error in X scenario")
        self.assertEqual(v["verdict"], SR.ASK)

    def test_vapi_fraud_alert_is_protected(self):
        """This CORRECTS the transactional tier's note, which assumed
        support@mail.vapi.ai was a product blast. All 8 of its rows are
        'Fraud Alert on Your Account'."""
        v = self._v("support@mail.vapi.ai", "Fraud Alert on Your Account")
        self.assertEqual(v["verdict"], SR.ASK)

    # ── …while the platforms' own campaigns still go ─────────────────────────
    def test_upwork_job_alerts_still_excluded(self):
        for subj in ("New job alert: Shopify Developer",
                     "=?UTF-8?q?New_job_alert:_Data_Engineer?=",
                     "Use your additional Connects to submit proposals now",
                     "Find your next project with these handpicked jobs",
                     "Nice job upgrading your membership!"):
            v = self._v("donotreply@upwork.com", subj)
            self.assertEqual(v["verdict"], SR.EXCLUDE, subj)

    def test_make_campaign_senders_still_excluded(self):
        for s in ("make-events@make.com", "info@make.com"):
            self.assertEqual(self._v(s, "A first look at Waves '26")["verdict"],
                             SR.EXCLUDE, s)

    def test_vapi_campaign_sender_still_excluded(self):
        self.assertEqual(
            self._v("team@mail.vapi.ai", "Vapicon 2026")["verdict"], SR.EXCLUDE)

    def test_campaign_only_subdomains_are_not_protected(self):
        """`never` — the parent is protected without dragging its blast channel
        in. mail.clickup.com is 8 promos to 1 real billing error."""
        self.assertIsNone(self._v("team@mail.clickup.com", "35% off Everything AI"))
        self.assertIsNone(self._v("team@mail.airtable.com", "Last chance"))
        self.assertIsNone(self._v("updates@e.stripe.com", "Stripe Tour Singapore"))

    def test_the_parents_of_those_are_protected(self):
        self.assertEqual(
            self._v("support@clickup.com", "Your ClickUp Plan Renewal")["verdict"],
            SR.ASK)
        self.assertEqual(
            self._v("noreply@airtable.com", "Your Airtable statement")["verdict"],
            SR.ASK)
        self.assertEqual(
            self._v("invoice+statements+acct_1x@stripe.com",
                    "Your receipt from Render")["verdict"], SR.ASK)

    def test_unrelated_senders_are_untouched(self):
        for s in ("information@mailers.hdfcbank.bank.in", "account@chess.com",
                  "hello@apify.com", "team@mail.vapi.ai.evil.com"):
            v = self._v(s, "anything")
            self.assertTrue(v is None or v["verdict"] == SR.EXCLUDE, s)

    def test_a_bad_regex_cannot_protect_fail(self):
        """A malformed pattern must skip, never crash the gate and never
        accidentally exclude."""
        bad = {"operational": {"domains": ["x.com"],
                               "marketing_subjects": {"x.com": ["(unclosed"]}}}
        v = SR.operational_verdict("noreply@x.com", "hello", bad)
        self.assertEqual(v["verdict"], SR.ASK)


class IngestGateAgreesWithTheRetroactivePath(unittest.TestCase):
    """One rule set, three callers. If the door and the resweep disagree, a
    message gets one answer on arrival and another a week later — which is the
    defect resweep.py was built to close, reappearing one layer down."""

    def _door(self, sender, subject):
        return mail_pipeline.classify(
            "email", "n1", sender, subject, None, "", "raw")

    def test_the_hire_survives_the_door(self):
        got = self._door("donotreply@upwork.com", "Ryan Stomberg hired you for")
        self.assertTrue(got["_pass"], got.get("_reason"))

    def test_a_make_failure_is_filed_as_an_incident_not_as_noise(self):
        """Updated when Abhijeet chose the incident route, 2026-08-20. It used
        to assert `_pass` — that a Make failure reached the mapping queue —
        which was right against the bug it was written for (the alert being
        DELETED as bulk mail) and wrong about where it belongs. Both outcomes
        set `_pass` False, so the distinction that matters is the REASON: an
        ops-alert is filed and readable on Activity; a noise: exclusion is
        gone. Asserting the reason is what keeps the two from being confused
        again."""
        got = self._door("noreply@us2.make.com",
                         "🛑 The scenario 1 DoulaMatch | Bot Watcher has been stopped.")
        self.assertFalse(got["_pass"])
        self.assertTrue(got["_reason"].startswith("ops-alert:make.com:"),
                        got["_reason"])
        self.assertNotIn("noise:", got["_reason"])

    def test_job_alerts_are_still_stopped_at_the_door(self):
        got = self._door("donotreply@upwork.com",
                         "=?UTF-8?q?New_job_alert:_Shopify_Developer?=")
        self.assertFalse(got["_pass"])
        self.assertIn("upwork.com", got["_reason"])

    def test_ordinary_noise_is_still_stopped(self):
        """…by sender_rules, which is the layer that owns it. The door is only
        the metadata gate: 'information@mailers.hdfcbank.bank.in' matches
        neither _NOISE_SENDER_SUBSTR nor _NOISE_SENDER_DOMAINS and passes it,
        exactly as it did before this change. The bulk-subdomain 'mailers' and
        the '.bank.in' suffix are sender_rules' business. Asserted here so the
        division stays explicit — protection was added at the door AND in
        resweep, and the thing to prove is that neither leaks."""
        self.assertTrue(self._door("information@mailers.hdfcbank.bank.in",
                                   "Your Personal Loan offer")["_pass"])
        v = SR.classify("information@mailers.hdfcbank.bank.in",
                        subject="Your Personal Loan offer")
        self.assertEqual(v["verdict"], SR.EXCLUDE)
        self.assertNotIn("operational", v["rule"])

    def test_self_noise_still_wins_over_protection(self):
        """Precedence: our own automation is ours whatever address it wears."""
        got = self._door("contact@etherwise.io", "Upwork scan: 3 scored, 3 hot")
        self.assertFalse(got["_pass"])
        self.assertIn("self-noise", got["_reason"])


class OpsAlertsLeaveTheMappingQueue(unittest.TestCase):
    """A third thing a protected sender's mail can be. Abhijeet, 2026-08-20,
    after the restore put 134 Make failures on Review: the mapping queue asks
    "which client is this?" and an automation error cannot answer it. Kept —
    deleting them was the original bug — but filed as incidents, grouped by the
    scenario named in the subject, so 131 rows become the ~8 automations that
    are actually failing."""

    def _a(self, subject, sender="noreply@us2.make.com"):
        return SR.ops_alert(sender, subject)

    def test_error_alert_is_detected_and_named(self):
        a = self._a("🛑 Encountered error in 1  DoulaMatch | Bot Watcher scenario")
        self.assertEqual(a["domain"], "make.com")
        self.assertEqual(a["scenario"], "1 DoulaMatch | Bot Watcher")

    def test_stopped_scenario_groups_with_its_errors(self):
        """The same automation writes both shapes; they must land in ONE group
        or the count of 'what is failing' is inflated."""
        err = self._a("🛑 Encountered error in All Pandadoc - Sign Watcher  scenario")
        stop = self._a("🛑 The scenario All Pandadoc - Sign Watcher  has been stopped.")
        self.assertEqual(err["scenario"], stop["scenario"])

    def test_warning_shapes(self):
        for subj, want in (
            ("⚠️ Encountered warnings in scenario Run Deduplication after Deal is moved",
             "Run Deduplication after Deal is moved"),
            ("⚠️ Encountered 2 warnings in scenario 9 | Auto-Membership Change",
             "9 | Auto-Membership Change"),
        ):
            self.assertEqual(self._a(subj)["scenario"], want, subj)

    def test_the_reason_carries_platform_and_scenario(self):
        a = self._a("🛑 Encountered error in [AUTOFILL] BT RBT Self  scenario")
        self.assertTrue(a["reason"].startswith("ops-alert:make.com:"))
        self.assertIn("[AUTOFILL] BT RBT Self", a["reason"])

    def test_the_displayed_name_keeps_its_capitalisation(self):
        """Rules match case-folded; the name shown to a human does not. His
        automation is called '1 DoulaMatch | Bot Watcher'."""
        a = self._a("🛑 ENCOUNTERED ERROR IN 1  DoulaMatch | Bot Watcher SCENARIO")
        self.assertEqual(a["scenario"], "1 DoulaMatch | Bot Watcher")

    def test_non_alert_mail_from_the_same_sender_is_not_one(self):
        self.assertIsNone(self._a("Your Make plan is renewing"))

    def test_other_platforms_are_not_ops_alerts(self):
        self.assertIsNone(self._a("Your contract has started",
                                  "donotreply@upwork.com"))

    def test_it_never_returns_silence_for_an_unrecognised_shape(self):
        """`match` fired but no `subject_of` pattern fits: the whole subject
        becomes the group. One group per subject is a degradation; None would
        be a disappearance."""
        a = self._a("🛑 Encountered error, details inside")
        self.assertIsNotNone(a)
        self.assertTrue(a["scenario"])

    def test_the_door_files_it_as_an_incident_not_a_question(self):
        got = mail_pipeline.classify(
            "email", "n9", "noreply@eu1.make.com",
            "🛑 Encountered error in Hipp Follow-up Unverified Encounters scenario",
            None, "", "raw")
        self.assertFalse(got["_pass"])
        self.assertTrue(got["_reason"].startswith("ops-alert:make.com:"))

    def test_a_client_facing_platform_message_is_still_a_question(self):
        got = mail_pipeline.classify(
            "email", "n10", "donotreply@upwork.com",
            "Ryan Stomberg hired you for: Ai Automation Engineer",
            None, "", "raw")
        self.assertTrue(got["_pass"], got.get("_reason"))


class SharedPlatformDomainsCannotBeClaimed(unittest.TestCase):
    """A bare-domain `emails:` entry is a WHOLESALE claim — 'anyone writing from
    here is this client'. On a platform notification host that claim is false by
    construction, because every client's mail arrives from it.

    2026-08-20: `tanushant-sharma` listed `email.upwork.com`, which is the host
    behind every Upwork room notification. match_email falls back to the domain,
    so from that line onward every 'X sent you a message' resolved to Tanushant
    — found on 'Julie H. sent you a message' and 'John B. sent you a message'
    (Socialtyze), both filed under him. Same shape as claiming gmail.com.

    The claim is DROPPED and recorded, not raised: one bad line must not take
    the roster down, and must not vanish either."""

    def _reg(self, emails):
        from core import registry
        return registry.Registry(
            [{"id": "c1", "name": "C One", "kind": "client", "emails": emails}],
            sha="test")

    def test_a_platform_domain_claim_is_refused(self):
        r = self._reg(["email.upwork.com"])
        self.assertIsNone(r.match_email("room_abc@email.upwork.com"))
        self.assertTrue(any("email.upwork.com" in w for w in r.warnings))

    def test_freemail_cannot_be_claimed_either(self):
        r = self._reg(["gmail.com"])
        self.assertIsNone(r.match_email("someone@gmail.com"))

    def test_a_full_address_on_such_a_domain_still_works(self):
        """It names one room / one person, which is a claim that can be true."""
        r = self._reg(["room_abc@email.upwork.com", "jane@gmail.com"])
        self.assertEqual(r.match_email("room_abc@email.upwork.com"), "c1")
        self.assertEqual(r.match_email("jane@gmail.com"), "c1")
        self.assertEqual(r.warnings, [])

    def test_an_ordinary_company_domain_is_untouched(self):
        r = self._reg(["realclient.com"])
        self.assertEqual(r.match_email("anyone@realclient.com"), "c1")
        self.assertEqual(r.warnings, [])

    def test_the_live_registry_is_clean_of_fatal_claims(self):
        """Any warning here is a line in clients.yaml for Abhijeet to fix by
        hand — the registry is his, no agent writes to it. Failing is wrong;
        this asserts the guard HOLDS, not that the file is perfect."""
        from core import registry
        reg = registry.load()
        for w in reg.warnings:
            self.assertIn("ignoring email", w)
        self.assertIsNone(reg.match_email("room_zzz@email.upwork.com"))


if __name__ == "__main__":
    unittest.main()
