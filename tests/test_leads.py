"""Leads — the bucket the Review queue never had (2026-11-01).

Measured on 30 Oct: 73 queued events, of which 15 were a platform's payment and
membership notices (info@/payments@app.globaltize.com, echoing a client at
0.95), 21 were vendors selling to us, and 11 were PROSPECTS with nowhere to go
— including "New inquiry from Hannah Melotto (Melotto Group)", a website-form
lead three days in a mapping queue. Pinned here:

  * a platform money/membership notice is filed (never asked), a human writing
    from a role address about work is not;
  * the website form's mail is a LEAD by shape — the sweep, the resweep and the
    Review action all land it in `leads` and file the event under the lead id,
    exactly once;
  * the proposer's `lead` intent with no client goes to the leads table too;
  * a named vendor category may retire a row the proposer tied to a client; a
    structural rule still may not;
  * Today and /api/leads read the same table; the one write is the status.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db
from modules.cockpit import leads as cockpit_leads
from modules.jarvis import approve, events, leads, mapping, resweep
from modules.jarvis import sender_rules as SR


class Shapes(unittest.TestCase):
    def test_platform_money_and_membership_notices_are_filed(self):
        for author, subj in (
                ("info@app.globaltize.com", "Your withdrawal of $135.00 is being processed"),
                ("info@app.globaltize.com", "You've been paid $1,117.50 by Globaltize"),
                ("payments@app.globaltize.com", "Receipt: $10.80 to abhijeet"),
                ("payments@app.globaltize.com", "Abhijeet Test paid you $2.00"),
                ("Globaltize <info@app.globaltize.com>", "Globaltize invited you to Globaltize Workforce"),
                ("invitations@app.globaltize.com", "Invitation to join Globaltize Workforce")):
            self.assertEqual(mapping.structural_shape(author, subj), "platform_notice", subj)
            self.assertEqual(mapping.route_structural(author, subj),
                             ("file", "filed:platform-notice"), subj)

    def test_upworks_own_account_mail_is_filed(self):
        """2026-11-02, from the held backlog he found on Review: the platform
        reporting a fact about HIS account, off donotreply@upwork.com."""
        for subj in ("Your payments are available early.",
                     "Payment Received - Thank You!",
                     "Visa was updated on Abhijeet Gandhi",
                     "We received a request to reset your password",
                     "Your new password is set"):
            self.assertEqual(mapping.structural_shape("donotreply@upwork.com", subj),
                             "platform_notice", subj)
        # a meeting or a message off the same address still takes the normal path
        for subj in ("You rescheduled the meeting with Randy B.", "Meeting with Randy",
                     "Mustafa A. sent you a message"):
            self.assertNotEqual(mapping.structural_shape("donotreply@upwork.com", subj),
                                "platform_notice", subj)

    def test_a_person_on_a_role_address_still_reaches_review(self):
        self.assertIsNone(mapping.structural_shape("info@acmeclinic.com",
                                                   "Re: the intake automation"))
        self.assertIsNone(mapping.structural_shape("nick@globaltize.com",
                                                   "You've been paid — not really, a question"))
        # the money verb alone is not enough either
        self.assertIsNone(mapping.structural_shape("nick@globaltize.com",
                                                   "Receipt: $10.80 to abhijeet"))

    def test_the_website_form_is_a_lead_by_shape(self):
        self.assertEqual(mapping.structural_shape(
            "onboarding@resend.dev", "New inquiry from Hannah Melotto (Melotto Group)"),
            "website_lead")
        self.assertEqual(mapping.route_structural(
            "onboarding@resend.dev", "New inquiry from Hannah Melotto (Melotto Group)"),
            ("lead", "website_lead"))
        self.assertEqual(mapping.structural_shape(
            "onboarding@resend.dev", "New case study lead: jay@etherwise.io (Work)"),
            "website_lead")
        # Resend relays other things too; only the form's subjects are leads
        self.assertIsNone(mapping.structural_shape("onboarding@resend.dev",
                                                   "Your domain is verified"))

    def test_the_lead_intent_routes_to_the_leads_table(self):
        self.assertEqual(mapping.route("lead"), "lead")
        self.assertEqual(mapping.route("lead", protected=True), "review")
        self.assertEqual(mapping.route("client_message"), "review")

    def test_describe_reads_the_person_off_the_form_subject(self):
        d = leads.describe({"source": "email", "author": "onboarding@resend.dev",
                            "subject": "New inquiry from Hannah Melotto (Melotto Group)",
                            "text": "Name: Hannah Melotto Email: hannah@melottogroup.com ..."})
        self.assertEqual((d["name"], d["company"], d["email"], d["source"]),
                         ("Hannah Melotto", "Melotto Group", "hannah@melottogroup.com",
                          "website_form"))
        d = leads.describe({"source": "email", "author": "Cole <cole@boostjet.co>",
                            "subject": "Production agent rollouts", "text": "hi"})
        self.assertEqual((d["name"], d["company"], d["domain"], d["source"]),
                         ("Cole", "Boostjet", "boostjet.co", "email"))


class Table(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="leads-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        with db.connect(self.db_path) as conn:
            events.store_event(conn, {
                "id": "evt:email:form1", "source": "email", "native_id": "form1",
                "ts": "2026-10-27T10:00:00", "author": "onboarding@resend.dev",
                "subject": "New inquiry from Hannah Melotto (Melotto Group)",
                "state": "queued", "text": "Email: hannah@melottogroup.com — we need help",
                "permalink": None, "raw_ref": None, "client_id": None}, None)
            events.store_event(conn, {
                "id": "evt:email:cold1", "source": "email", "native_id": "cold1",
                "ts": "2026-10-28T10:00:00", "author": "cole@boostjet.co",
                "subject": "Production agent rollouts", "state": "queued",
                "text": "Would you build this for us?", "permalink": None,
                "raw_ref": None, "client_id": None}, None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ev(self, eid):
        with db.connect(self.db_path) as conn:
            return dict(conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone())

    def test_from_event_files_the_event_under_the_lead_once(self):
        with db.connect(self.db_path) as conn:
            a = leads.from_event(conn, self._ev("evt:email:form1"), note="website form")
            b = leads.from_event(conn, self._ev("evt:email:form1"))
        self.assertEqual(a, b)
        ev = self._ev("evt:email:form1")
        self.assertEqual((ev["state"], ev["exclude_reason"]), ("excluded", f"filed:lead:{a}"))
        with db.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0], 1)
            row = dict(conn.execute("SELECT * FROM leads").fetchone())
        self.assertEqual((row["name"], row["company"], row["status"], row["source"]),
                         ("Hannah Melotto", "Melotto Group", "new", "website_form"))

    def test_the_review_action_lead_goes_through_approve(self):
        out = approve.resolve(self.db_path, "evt:email:cold1", "lead",
                              reason="asked for a build", do_git=False)
        self.assertTrue(out["ok"])
        self.assertEqual(self._ev("evt:email:cold1")["state"], "excluded")
        d = cockpit_leads.leads(self.db_path)
        self.assertEqual(d["open"], 1)
        self.assertEqual(d["rows"][0]["company"], "Boostjet")

    def test_the_resweep_lands_the_form_lead_retroactively(self):
        with db.connect(self.db_path) as conn:
            out = resweep.run(conn, reg=None)
        self.assertEqual(out["leads"], 1)
        self.assertEqual(self._ev("evt:email:form1")["state"], "excluded")
        self.assertEqual(self._ev("evt:email:cold1")["state"], "queued")   # a sender rule's call

    def test_today_and_the_status_write(self):
        with db.connect(self.db_path) as conn:
            lid = leads.from_event(conn, self._ev("evt:email:form1"))
        with mock.patch.object(config, "DB_PATH", self.db_path):
            n = cockpit_leads.new_leads(self.db_path, fresh_days=10_000)
        self.assertEqual(n["total"], 1)
        self.assertEqual(n["rows"][0]["name"], "Hannah Melotto")
        self.assertEqual(cockpit_leads.set_status(self.db_path, {"id": lid, "status": "nope"})
                         .get("error", "")[:6], "status")
        self.assertTrue(cockpit_leads.set_status(self.db_path,
                                                 {"id": lid, "status": "contacted"})["ok"])
        n = cockpit_leads.new_leads(self.db_path, fresh_days=10_000)
        self.assertEqual(n["total"], 0)                      # it left Today
        self.assertEqual(cockpit_leads.leads(self.db_path)["open"], 1)   # still open


class VendorCategory(unittest.TestCase):
    def test_a_named_vendor_may_retire_a_machine_proposal(self):
        rules = SR.load_rules()
        v = SR.classify("josh@marketlikeahero.com", rules=rules, learned={})
        self.assertEqual((v["verdict"], v["rule"]), (SR.EXCLUDE, "category:vendor_outreach"))
        d = resweep.decide({"id": "x", "author": "josh@marketlikeahero.com",
                            "subject": "Should we close this out?", "text": "",
                            "proposed_client_id": "hero-marketing-agency",
                            "resolved_by": None}, reg=None, rules=rules, learned={})
        self.assertEqual(d["action"], "exclude")
        # …but a STRUCTURAL rule (a bulk subdomain) still may not
        d = resweep.decide({"id": "y", "author": "x@mailers.somevendor.com",
                            "subject": "RECO x Etherwise", "text": "",
                            "proposed_client_id": "reco-health", "resolved_by": None},
                           reg=None, rules=rules, learned={})
        self.assertEqual(d["action"], "skip")


if __name__ == "__main__":
    unittest.main()
