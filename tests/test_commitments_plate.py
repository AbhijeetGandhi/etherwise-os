"""Commitments — whose they are, and which ones reach him (2026-11-01).

The "128 commitments due" email. The AM/PM brief listed every open who=us
commitment past its due date, unbounded: 158 lines on 30 Oct, growing 1.4 a
day, the oldest from April. Today's card rule applied two filters and showed
4. Same table, two answers, and the long one was the one he stopped reading.
Pinned here:
  * brief and Today share ONE filter (commitments.on_your_plate): not
    stale-flagged, at most COS_COMMITMENT_STALE_DAYS overdue, grouped by client;
  * a "<teammate> will …" line is the team's, a "<client-side person> will …"
    line is the client's, and "Send Shruti the brief" is still his;
  * the brief shows COS_BRIEF_COMMITMENT_LINES and names the rest as a count;
  * the PM brief is a delta — no commitments list, no top priorities, and a
    quiet day says so in one line.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from core import config, db
from modules.chief_of_staff import brief, commitments as C


def dt(d=22, hh=12):
    return datetime(2026, 6, d, hh, 0, tzinfo=config.TZ)


class WhoseIsIt(unittest.TestCase):
    def test_team_and_client_side_subjects(self):
        self.assertEqual(C.classify_who("us", "Ankita to create the SOP and send access"), "team")
        self.assertEqual(C.classify_who("us", "Shruti will join the 12 o'clock meeting"), "team")
        self.assertEqual(C.classify_who("us", "Abel to assign the Discord task to Abhijeet"), "client")
        self.assertEqual(C.classify_who("us", "Jaap: share the onboarding form"), "client")
        self.assertEqual(C.classify_who("us", "Abel and Jaap will review the backlog"), "client")

    def test_a_promise_about_someone_is_still_his(self):
        self.assertEqual(C.classify_who("us", "Send Shruti the brief by Friday"), "us")
        self.assertEqual(C.classify_who("us", "Abhijeet will get all info from Shruti"), "us")
        self.assertEqual(C.classify_who("us", "Build a dashboard by tomorrow EOD"), "us")
        self.assertEqual(C.classify_who("us", "WhatsApp Ankita to get her started"), "us")
        self.assertEqual(C.classify_who("us", "Invite Jay to the Etherwise Pro Slack"), "us")

    def test_client_stays_client_and_unknown_names_stay_his(self):
        self.assertEqual(C.classify_who("client", "Ankita will send the data"), "client")
        self.assertEqual(C.classify_who("us", "Zorblax will fix it"), "us")


class Plate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="plate-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        rows = [
            # (who, client, text, due, review_state)
            ("us", "hermon", "Testing by the 20th", "2026-06-20", None),     # overdue 2d
            ("us", "hermon", "Dashboard by the 21st", "2026-06-21", None),   # overdue 1d
            ("us", "globatize", "SSN flow live", "2026-06-23", None),        # due soon
            ("us", None, "Old April thing", "2026-04-10", None),             # > 30d overdue
            ("us", "cmf", "Flagged stale", "2026-06-19", "stale"),           # stale-flagged
            ("team", "hermon", "Shruti to join the call", "2026-06-20", None),
            ("client", "hermon", "Grant access", None, None),
            ("us", "rocky-choy", "Scenario sorted", "2026-06-18", None),     # overdue 4d
        ]
        with db.connect(self.db_path) as conn:
            for i, (who, cid, text, due, rs) in enumerate(rows):
                conn.execute(
                    "INSERT INTO commitments (who, client_id, text, due, source_cite,"
                    " dedup_key, state, review_state) VALUES (?,?,?,?,?,?,'open',?)",
                    (who, cid, text, due, f"fathom/{i}", f"k{i}", rs))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_one_filter_grouped_by_client(self):
        p = C.on_your_plate(self.db_path, now=dt(22))
        self.assertEqual(p["count"], 4)                      # 2 hermon + globatize + rocky
        self.assertEqual([c["text"] for c in p["overdue"]],
                         ["Dashboard by the 21st", "Testing by the 20th", "Scenario sorted"])
        self.assertEqual([c["text"] for c in p["due_soon"]], ["SSN flow live"])
        self.assertEqual([cid for cid, _ in p["by_client"]][0], "hermon")   # most on the line first
        self.assertEqual(p["hidden_old"], 2)                 # April + the stale one

    def test_the_brief_and_today_agree_and_the_brief_is_capped(self):
        b = brief.build_brief(self.db_path, "am", now=dt(22))
        self.assertEqual(b["commitments"]["on_plate"], 4)
        self.assertEqual(len(b["commitments"]["overdue"]), 3)
        html_body = brief.render_html(b, {"headline": "h", "narrative": "n"})
        self.assertNotIn("Old April thing", html_body)
        self.assertNotIn("Flagged stale", html_body)
        self.assertNotIn("Shruti to join", html_body)
        self.assertIn("4 on your plate", html_body)
        self.assertNotIn("open commitments", html_body)      # the 709 footer is gone
        with mock.patch.object(config, "COS_BRIEF_COMMITMENT_LINES", 2):
            html_body = brief.render_html(b, {"headline": "h", "narrative": "n"})
            self.assertIn("and 2 more on the Today tab", html_body)
            txt = brief.render_text(b, {"headline": "h", "narrative": "n"})
            self.assertIn("and 2 more on the Today tab", txt)

    def test_reassign_open_moves_only_subject_lines(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO commitments (who, client_id, text, due, source_cite,"
                         " dedup_key, state) VALUES ('us','hermon','Yogita to test the"
                         " form',NULL,'fathom/9','k9','open')")
        dry = C.reassign_open(self.db_path)
        self.assertEqual((dry["team"], dry["client"]), (1, 0))
        with db.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT who FROM commitments WHERE dedup_key='k9'")
                             .fetchone()[0], "us")              # dry-run wrote nothing
        C.reassign_open(self.db_path, commit=True)
        with db.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT who FROM commitments WHERE dedup_key='k9'")
                             .fetchone()[0], "team")


class Delta(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="delta-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_quiet_day_is_one_line_and_never_a_commitments_list(self):
        with mock.patch.object(brief.source_health, "report",
                               return_value={"ok": True, "headline": "all fresh",
                                             "lines": [], "worst_escalation": 0}):
            d = brief.build_delta(self.db_path, now=dt(22, 20))
        self.assertTrue(d["quiet"])
        self.assertEqual(d["headline"], "Since this morning: nothing changed")
        html_body = brief.render_delta_html(d)
        self.assertIn("Nothing came in", html_body)
        self.assertNotIn("OVERDUE", html_body)
        self.assertNotIn("Top priorities", html_body)

    def test_what_changed_since_eleven_is_listed_and_nothing_else(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, room_name) VALUES ('t1', 'Acme room')")
            conn.execute("INSERT INTO messages (id, thread_id, message, created_dt,"
                         " sender_user_name, direction) VALUES"
                         " ('m1','t1','Yes, send it','2026-06-22T14:05:00','Pat','inbound'),"
                         " ('m0','t1','Old one','2026-06-22T09:00:00','Pat','inbound')")
            conn.execute("INSERT INTO commitments (who, client_id, text, due, source_cite,"
                         " dedup_key, state, created_at) VALUES ('us','acme','Send the"
                         " register',NULL,'c','k1','open','2026-06-22T15:00:00')")
            conn.execute("INSERT INTO runs (task_name, started_at, status, error) VALUES"
                         " ('upwork_hiring','2026-06-22T13:06:00','failed','URLError')")
        with mock.patch.object(brief.source_health, "report",
                               return_value={"ok": True, "headline": "all fresh",
                                             "lines": [], "worst_escalation": 0}):
            d = brief.build_delta(self.db_path, now=dt(22, 20))
        self.assertFalse(d["quiet"])
        self.assertEqual([m["text"] for m in d["replies_in"]], ["Yes, send it"])
        self.assertEqual([c["text"] for c in d["promises_made"]], ["Send the register"])
        self.assertEqual(d["runs_failed"][0]["task_name"], "upwork_hiring")
        self.assertIn("1 replies in", d["headline"])
        html_body = brief.render_delta_html(d)
        self.assertIn("Yes, send it", html_body)
        self.assertNotIn("Old one", html_body)

    def test_write_brief_pm_writes_the_delta_not_the_digest(self):
        sent = []
        with mock.patch.object(config, "COS_BRIEFS_DIR", self.tmp / "briefs"), \
             mock.patch.object(brief, "email_is_live", return_value=True), \
             mock.patch.object(brief.source_health, "report",
                               return_value={"ok": True, "headline": "all fresh",
                                             "lines": [], "worst_escalation": 0}):
            out = brief.write_brief(self.db_path, "pm", now=dt(22, 20),
                                    _narrate=lambda *a: self.fail("no narrator for a delta"),
                                    _send=lambda s, b: sent.append((s, b)))
        self.assertTrue(out["path"].endswith("2026-06-22-pm.html"))
        self.assertEqual(len(sent), 1)
        self.assertIn("Since this morning", out["headline"])


if __name__ == "__main__":
    unittest.main()
