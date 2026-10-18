"""What is allowed to reach the Review board.

2026-10-18, Abhijeet: "look at review tab, there are ton of in review that
shouldn't be." 290 items; roughly 10-20 were his. Three independent faults,
one test class each:

  * 97 of them were messages-table FOREIGN-KEY PLACEHOLDERS, not threads —
    `upwork_pull_all.step_messages` writes `INSERT OR IGNORE INTO threads (id)`
    so a room's messages have somewhere to hang. Every other column is NULL,
    so the grouper called them "Direct message, no job attached".
  * 84 were 'held' — the proposer had not run on them. Machine backlog counted
    as human decisions.
  * ~50 were notifications that walked past `mapping.route`, which had been
    wired into the LLM path only while the two deterministic paths called
    `set_queued` directly.

The rule these pin: the Review total counts DECISIONS. Anything else is
reported beside it, never inside it.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from core import db
from modules.cockpit import data
from modules.jarvis import mapping


class StructuralGate(unittest.TestCase):
    """The zero-LLM gate for the deterministic queue paths."""

    def test_upwork_message_notification_is_filed(self):
        self.assertEqual(
            mapping.structural_shape(
                "room_1f72c2d8db8fcd4484255a952a6fb5b8@mail.upwork.com",
                "Mustafa A. sent you a message"),
            "upwork_notification")
        dest, reason = mapping.route_structural(
            "room_1f72c2d8db8fcd4484255a952a6fb5b8@mail.upwork.com",
            "Mustafa A. sent you a message")
        self.assertEqual((dest, reason), ("file", "filed:upwork-notification"))

    def test_calendar_rsvp_is_filed(self):
        for subj in ("Accepted: TCC Training @ Fri 9 Oct 2026 8:30pm",
                     "Declined: TCC <> Etherwise @ Sat 26 Sept 2026",
                     "Invitation: Vinton Automation @ Fri Sep 4, 2026",
                     "Updated invitation: Weekly sync @ Mon"):
            self.assertEqual(mapping.structural_shape("x@client.com", subj),
                             "calendar_rsvp", subj)

    def test_real_mail_from_an_upwork_room_still_reaches_review(self):
        """The room sender is not itself the signal — the NOTIFICATION WORDING
        is. A client writing through Upwork's relay must not be filed."""
        self.assertIsNone(mapping.structural_shape(
            "room_1f72c2d8db8fcd4484255a952a6fb5b8@mail.upwork.com",
            "Re: Thursday Zapier run — can we move it?"))

    def test_ordinary_client_mail_is_untouched(self):
        for subj in ("URGENT Payroll Issue/Reversal or Fix Needed",
                     "Horizons Leadtrap leads missing GCLID's",
                     "Accepting the new scope",          # 'Accepting', not 'Accepted:'
                     "Invitation to tender — please review"):  # no colon verb
            self.assertIsNone(
                mapping.structural_shape("roshan@fingercheck.com", subj), subj)

    def test_a_transactional_sender_overrides_the_gate(self):
        """Same override as route(): an e-signature or background-check notice
        reaches the board even when it is shaped like a notification, because
        that judgement is too expensive to leave to a pattern."""
        dest, reason = mapping.route_structural(
            "room_1f72c2d8db8fcd4484255a952a6fb5b8@mail.upwork.com",
            "Mustafa A. sent you a message", protected=True)
        self.assertEqual(dest, "review")
        self.assertIsNone(reason)


class ThreadStubsAreNotThreads(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="review-stub-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        with db.connect(self.db_path) as conn:
            # a real unattributed room, and three FK placeholders exactly as
            # upwork_pull_all.step_messages writes them
            conn.execute(
                "INSERT INTO threads (id, room_name, topic, room_type,"
                " first_seen_at) VALUES ('room_real','Rick K','Front end',"
                " 'INTERVIEW','2026-10-01 00:00:00')")
            for i in range(3):
                conn.execute("INSERT OR IGNORE INTO threads (id) VALUES (?)",
                             ("room_stub%d" % i,))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stubs_are_excluded_from_the_board_and_counted_aside(self):
        t = data.review_threads(self.db_path)
        self.assertEqual(t["open_total"], 1)
        self.assertEqual(t["stub_total"], 3)
        shown = [i for g in t["groups"] for i in g["items"]]
        self.assertEqual([i["thread_id"] for i in shown], ["room_real"])

    def test_the_badge_counts_decisions_not_placeholders(self):
        c = data.review_counts(self.db_path)
        self.assertEqual(c["threads_open"], 1)
        self.assertEqual(c["threads_stub"], 3)
        self.assertEqual(c["total"], c["events_queued"] + 1)


class HeldIsBacklogNotWork(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="review-held-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        with db.connect(self.db_path) as conn:
            for i, state in enumerate(["queued"] * 2 + ["held"] * 5):
                conn.execute(
                    "INSERT INTO events (id, native_id, source, author,"
                    " subject, ts, state) VALUES (?,?,?,?,?,?,?)",
                    ("evt:%d" % i, "n%d" % i, "email", "a@b.com", "s",
                     "2026-10-10", state))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_held_is_reported_but_not_counted(self):
        """The proposer not having run is an ops problem, not his to-do."""
        c = data.review_counts(self.db_path)
        self.assertEqual(c["events_queued"], 2)
        self.assertEqual(c["events_held"], 5)      # still visible
        self.assertEqual(c["total"], 2)            # but not his queue


if __name__ == "__main__":
    unittest.main()
