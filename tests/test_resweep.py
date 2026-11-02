"""Rules are retroactive — offline.

The defect: `assets/sender-rules.json` was written FROM the 58 rows sitting in
`held` on 2026-08-14 and then never applied to them, because the only consumer
was a manual script nobody re-ran. Abhijeet, 2026-08-19: *"there are ton of
emails that are like marketing ones and should immediately be marked as ignore
— why are those in my review tab?"*

So the behaviours under test are (a) a new rule reaches rows that already exist,
(b) it reaches them without anyone remembering to do anything, and (c) the three
things it is never allowed to do: overrule him, sweep a pending proposal, or
turn a rule into a client attribution.
"""
from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from core import db, registry
from modules.jarvis import events, resweep
from modules.jarvis import sender_rules as SR

REG_YAML = """\
clients:
  -
    id: globatize
    name: Globaltize LLC
    aliases:
      - Globaltize
      - Nick Esquivel
    emails:
      - globaltize.com
    billing:
      canonical: Globaltize LLC
"""

# Trimmed from the live queue on 2026-08-19. Every one of these was on the
# Review board when Abhijeet asked the question.
ROWS = [
    # (id, state, author, subject, text, proposed, resolved_by)
    ("evt:email:promo1", "held", "information@mailers.hdfcbank.bank.in",
     "Your Personal Loan is available", "From: HDFC <information@mailers.hdfcbank.bank.in>\nbank promo",
     None, None),
    ("evt:email:promo2", "queued", "team@mail.clickup.com",
     "You almost missed this (massive discount)", "From: ClickUp <team@mail.clickup.com>\nsale",
     None, None),
    ("evt:email:ops1", "held", "onboarding@resend.dev",
     "[Etherwise Ops] RateLimitError — Orders → NetSuite sync",
     "From: onboarding@resend.dev\nClient : Test Client A\n"
     "http://localhost:3000/incidents/abc", None, None),
    ("evt:email:panda1", "queued", "docs@email.pandadoc.net",
     "Nick Esquivel sent you International Independent Contractor Agreement",
     "From: Nick Esquivel via PandaDoc <docs@email.pandadoc.net>\n"
     "Nick Esquivel sent you International Independent Contractor Agreement.",
     None, None),
    ("evt:email:checkr1", "queued", "support@eu.checkr.com",
     "Background check paused: more information needed",
     "From: Checkr Background Service <support@eu.checkr.com>\n"
     "We need more information to complete your background check.", None, None),
    ("evt:email:checkr2", "queued", "support@eu.checkr.com",
     "Your background check is complete!",
     "From: Checkr Background Service <support@eu.checkr.com>\ndone",
     "globatize", None),
    ("evt:email:lead1", "queued", "onboarding@resend.dev",
     "New inquiry from Nick Esquivel (Globaltize)",
     "From: Etherwise Contact Form <onboarding@resend.dev>\n"
     "Name: Nick Esquivel\nEmail: nick@globaltize.com", None, None),
    ("evt:email:cold1", "queued", "sanal.k@sentinelfox.com",
     "Re: Potential AWS Credits for Etherwise",
     "From: Sanal K <sanal.k@sentinelfox.com>\nfollowing up", None, None),
]

# A row Abhijeet himself parked in `held` — a rule may never re-open it.
HIS_ROW = ("evt:email:his1", "held", "information@mailers.hdfcbank.bank.in",
           "Bank promo he chose to keep", "From: HDFC\nkeep", None,
           "cockpit-approval")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = Path(self.tmp.name) / "t.db"
        db.migrate(self.dbp)
        rp = Path(self.tmp.name) / "clients.yaml"
        rp.write_text(REG_YAML)
        self.reg = registry.load(rp, force=True)
        self.rules, self.learned = SR.load_rules(), SR.load_learned()

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self, rows=ROWS):
        with db.connect(self.dbp) as conn:
            for (eid, state, author, subject, text, proposed, by) in rows:
                conn.execute(
                    "INSERT INTO events (id, source, native_id, ts, author,"
                    " subject, state, text, proposed_client_id, resolved_by)"
                    " VALUES (?,'email',?, '2026-08-15T00:00:00Z', ?,?,?,?,?,?)",
                    (eid, eid.split(":")[-1], author, subject, state, text,
                     proposed, by))

    def state_of(self, eid):
        with db.connect(self.dbp) as conn:
            r = conn.execute("SELECT state, client_id, proposed_client_id,"
                             " exclude_reason, resolved_by, proposal_evidence"
                             " FROM events WHERE id=?", (eid,)).fetchone()
        return dict(r)

    def run_resweep(self, **kw):
        with db.connect(self.dbp) as conn:
            out = resweep.run(conn, reg=self.reg, rules=self.rules,
                              learned=self.learned, **kw)
        return out


class RetroactivityTests(Base):
    def test_a_written_rule_reaches_the_rows_it_was_written_from(self):
        """The whole defect in one assertion. 'mailers.hdfcbank.bank.in' and
        'mail.clickup.com' are both NAMED in the rules file's own note, and both
        were still sitting on the Review board."""
        self.seed()
        for eid in ("evt:email:promo1", "evt:email:promo2"):
            self.assertIn(self.state_of(eid)["state"], ("held", "queued"))
        self.run_resweep()
        for eid in ("evt:email:promo1", "evt:email:promo2"):
            self.assertEqual(self.state_of(eid)["state"], "excluded", eid)

    def test_every_exclusion_records_the_phrase_that_decided_it(self):
        self.seed()
        self.run_resweep()
        row = self.state_of("evt:email:promo2")
        self.assertTrue(row["exclude_reason"])
        self.assertIn("mail.clickup.com", row["proposal_evidence"])
        self.assertTrue(row["resolved_by"].startswith("sender_rules:"))

    def test_the_resolver_is_not_mistaken_for_a_human(self):
        self.seed()
        self.run_resweep()
        self.assertFalse(events.is_human_decision(
            self.state_of("evt:email:promo1")["resolved_by"]))

    def test_it_is_idempotent(self):
        self.seed()
        first = self.run_resweep()
        second = self.run_resweep()
        self.assertGreater(first["excluded"], 0)
        self.assertEqual(second["excluded"], 0)


class NeverOverruleHimTests(Base):
    def test_a_row_he_resolved_himself_is_left_alone(self):
        self.seed(ROWS + [HIS_ROW])
        self.run_resweep()
        row = self.state_of("evt:email:his1")
        self.assertEqual(row["state"], "held")
        self.assertEqual(row["resolved_by"], "cockpit-approval")

    def test_a_pending_proposal_is_never_swept(self):
        # checkr2 carries proposed_client_id=globatize. A proposal is a decision
        # awaiting confirmation, not an undecided row.
        self.seed()
        self.run_resweep()
        row = self.state_of("evt:email:checkr2")
        self.assertEqual(row["state"], "queued")
        self.assertEqual(row["proposed_client_id"], "globatize")

    def test_it_never_writes_a_map(self):
        """A rule may PROPOSE a client, never attribute one. Attribution feeds
        extract, facts and dossiers; an exclusion feeds nothing."""
        self.seed()
        self.run_resweep()
        with db.connect(self.dbp) as conn:
            n = conn.execute("SELECT COUNT(*) n FROM events WHERE"
                             " state='mapped'").fetchone()["n"]
        self.assertEqual(n, 0)


class TheTenThatMustSurviveTests(Base):
    """Group C from the live queue: transactional mail on the critical path of
    the newest client, plus a real inbound lead. A blunt 'automated sender =
    ignore' rule buries all of it."""

    SURVIVORS = ("evt:email:panda1", "evt:email:checkr1", "evt:email:checkr2",
                 "evt:email:cold1")

    def test_none_of_them_is_excluded(self):
        self.seed()
        self.run_resweep()
        for eid in self.SURVIVORS:
            self.assertNotEqual(self.state_of(eid)["state"], "excluded", eid)
        # the contact-form inquiry survives too — never excluded by a rule; the
        # proposer's intent gate decides lead vs pitch (2026-11-02)
        row = self.state_of("evt:email:lead1")
        self.assertNotEqual(row["state"], "excluded", row)

    def test_the_signed_agreement_is_proposed_to_its_client(self):
        self.seed()
        self.run_resweep()
        row = self.state_of("evt:email:panda1")
        self.assertEqual(row["proposed_client_id"], "globatize")
        self.assertEqual(row["state"], "queued")
        self.assertIn("globatize", row["proposal_evidence"])

    def test_an_unnamed_transactional_row_stays_a_question(self):
        self.seed()
        self.run_resweep()
        row = self.state_of("evt:email:checkr1")
        self.assertEqual(row["state"], "queued")
        self.assertIsNone(row["proposed_client_id"])

    def test_the_contact_form_inquiry_is_kept_for_the_proposer(self):
        # same sender as 43 swept ops alerts — the rule cannot key on the
        # address alone. 1 Nov made the form's SUBJECT shape a lead outright;
        # 2 Nov (11 of 12 were pitches or downloads) the shape is recognised
        # and the row is KEPT for the proposer, whose intent gate reads the
        # message. Never excluded as noise.
        self.seed()
        self.run_resweep()
        row = self.state_of("evt:email:lead1")
        self.assertNotEqual(row["state"], "excluded", row)
        with db.connect(self.dbp) as conn:
            n = conn.execute("SELECT COUNT(*) FROM leads WHERE event_id='evt:email:lead1'").fetchone()[0]
        self.assertEqual(n, 0)

class OpsAlertTests(Base):
    def test_his_own_ops_alerts_are_self_noise_not_client_mail(self):
        self.seed()
        self.run_resweep()
        row = self.state_of("evt:email:ops1")
        self.assertEqual(row["state"], "excluded")
        self.assertIn("etherwise ops", row["exclude_reason"])
        self.assertIn("self-noise", row["exclude_reason"])

    def test_the_same_sender_still_delivers_real_mail(self):
        """onboarding@resend.dev sends BOTH. If the rule ever keys on the
        address instead of the subject, this fails and the lead is lost."""
        self.seed()
        self.run_resweep()
        ops = self.state_of("evt:email:ops1")
        self.assertEqual(ops["state"], "excluded")
        self.assertIn("self-noise", ops["exclude_reason"])
        lead = self.state_of("evt:email:lead1")
        # 2026-11-02: not excluded, not a lead by shape — kept for the proposer
        self.assertNotEqual(lead["state"], "excluded", lead)


class FingerprintTests(Base):
    def test_nothing_runs_while_the_rules_are_unchanged(self):
        self.seed()
        with db.connect(self.dbp) as conn:
            first = resweep.run_if_rules_changed(conn, reg=self.reg)
            second = resweep.run_if_rules_changed(conn, reg=self.reg)
        self.assertTrue(first["ran"])
        self.assertFalse(second["ran"])

    def test_the_fingerprint_watches_the_code_side_rules_too(self):
        """`[etherwise ops]` went into config.SELF_SUBJECT_PATTERNS, not the
        JSON. A fingerprint over only the JSON would have let exactly this bug
        recur through the other half of the gate."""
        from core import config
        before = resweep.rules_fingerprint()
        old = config.SELF_SUBJECT_PATTERNS
        try:
            config.SELF_SUBJECT_PATTERNS = old + ("a brand new system mail:",)
            self.assertNotEqual(resweep.rules_fingerprint(), before)
        finally:
            config.SELF_SUBJECT_PATTERNS = old

    def test_a_registry_change_also_re_reads_the_backlog(self):
        a = resweep.rules_fingerprint(reg=self.reg)
        b = resweep.rules_fingerprint(reg=types.SimpleNamespace(sha="deadbeef"))
        self.assertNotEqual(a, b)

    def test_a_rule_edit_re_runs_without_anyone_remembering(self):
        """No --apply, no re-run, no remembering. The rules change and the
        backlog is re-read, because the fingerprint moved."""
        self.seed()
        with db.connect(self.dbp) as conn:
            resweep.run_if_rules_changed(conn, reg=self.reg)
        self.assertEqual(self.state_of("evt:email:cold1")["state"], "queued")

        # Abhijeet answers one question -> learned-mappings.json gains a line.
        real_load = SR.load_learned
        SR.load_learned = lambda: {
            **real_load(),
            "sentinelfox.com": {"verdict": SR.EXCLUDE,
                                "reason": "noise:sender:cold-outreach"}}
        try:
            with db.connect(self.dbp) as conn:
                out = resweep.run_if_rules_changed(conn, reg=self.reg)
        finally:
            SR.load_learned = real_load
        self.assertTrue(out["ran"])
        self.assertEqual(self.state_of("evt:email:cold1")["state"], "excluded")


class WiredIntoTheSweepTests(Base):
    """The part that makes this unforgettable rather than merely correct.

    `bin/jarvis-triage-queue` was never broken — it reads `bulk_subdomains`
    perfectly well. It was last run on the night of 2026-08-13/14 IST, in the
    same session that wrote those rules, and then never again, because nothing
    ran it. Five days of mail piled up behind rules that already described it.
    So the guarantee under test is not "the rules work"; it is "the rules are
    applied without a human deciding to apply them".
    """
    class Ctx:
        def __init__(self, dbp):
            self.db_path = dbp
            self.run_id = 1
            self.anomalies = []
            self.logs = []

        def log(self, m):
            self.logs.append(m)

        def anomaly(self, kind, severity, detail):
            self.anomalies.append((kind, severity, detail))

    def _sweep(self):
        from modules.jarvis import sweep
        ctx = self.Ctx(self.dbp)
        return sweep.run_sweep(
            ctx, token_fn=lambda: "tok",
            list_fn=lambda *a, **k: {"messages": []},
            meta_fn=lambda *a, **k: {}, full_fn=lambda *a, **k: {},
            reg=self.reg), ctx

    def test_a_sweep_with_no_new_mail_still_applies_the_rules(self):
        """The exact shape of the original failure: nothing arrived, so nothing
        ran, so the backlog stayed. TaskSkip here would throw the metrics away
        AND leave the rows."""
        self.seed()
        metrics, _ctx = self._sweep()
        self.assertTrue(metrics["resweep_ran"])
        self.assertGreater(metrics["resweep_excluded"], 0)
        self.assertTrue(metrics["resweep_only"])
        self.assertEqual(self.state_of("evt:email:promo1")["state"], "excluded")

    def test_a_second_empty_sweep_goes_back_to_skipping(self):
        from core import runner
        self.seed()
        self._sweep()
        with self.assertRaises(runner.TaskSkip):
            self._sweep()

    def test_a_bulk_exclusion_is_never_silent(self):
        from core import config
        self.seed()
        old = config.JARVIS_RESWEEP_ANOMALY_THRESHOLD
        try:
            config.JARVIS_RESWEEP_ANOMALY_THRESHOLD = 2
            _metrics, ctx = self._sweep()
        finally:
            config.JARVIS_RESWEEP_ANOMALY_THRESHOLD = old
        kinds = [k for k, _s, _d in ctx.anomalies]
        self.assertIn("jarvis_resweep_bulk_exclude", kinds)

    def test_a_small_re_sweep_does_not_cry_wolf(self):
        self.seed()
        _metrics, ctx = self._sweep()
        self.assertEqual(
            [k for k, _s, _d in ctx.anomalies if "resweep" in k], [])

    def test_the_metrics_carry_the_rules_that_fired(self):
        self.seed()
        metrics, _ctx = self._sweep()
        self.assertIn("self-noise:subject:[etherwise ops]",
                      metrics["resweep_rules"])


class PlanShapeTests(Base):
    def test_plan_writes_nothing(self):
        self.seed()
        with db.connect(self.dbp) as conn:
            resweep.plan(conn, reg=self.reg, rules=self.rules,
                         learned=self.learned)
        self.assertEqual(self.state_of("evt:email:promo1")["state"], "held")

    def test_actions_are_a_closed_set(self):
        self.seed(ROWS + [HIS_ROW])
        with db.connect(self.dbp) as conn:
            plan = resweep.plan(conn, reg=self.reg, rules=self.rules,
                                learned=self.learned)
        self.assertTrue(plan)
        for _r, d in plan:
            self.assertIn(d["action"], ("skip", "exclude", "propose", "keep", "lead"))
            self.assertTrue(d["rule"] and d["reason"])

    def test_a_row_with_no_sender_is_left_to_the_content_classifiers(self):
        self.seed([("evt:fathom:1", "held", None, "A call", "transcript",
                    None, None)])
        out = self.run_resweep()
        self.assertEqual(out.get("skip"), 1)
        self.assertEqual(self.state_of("evt:fathom:1")["state"], "held")


if __name__ == "__main__":
    unittest.main()
