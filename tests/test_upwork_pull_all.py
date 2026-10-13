"""scripts/upwork_pull_all.py — the read-only pull of every Upwork record
(2026-10-09, "I want you to have each and every data about every client").

Pins what can be held without the network: which conversations it would read
(proposal rooms with no stored message first, newest first, then client
rooms), that a dry run touches nothing, that files it re-reads are MOVED aside
(one previous copy each, so nothing grows), that a refused sign-in stops the run (exit 2, never
retried), and that the funnel backfill keeps a stored row's label.
"""
from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import db

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("upwork_pull_all", ROOT / "scripts" / "upwork_pull_all.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pull = _load()


class PullCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pull-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        with db.connect(self.db_path) as conn:
            for pid, room, sent in (("p1", "room_old", "2026-08-01"), ("p2", "room_new", "2026-09-20"),
                                    ("p3", "room_have", "2026-09-25")):
                conn.execute("INSERT INTO proposal_outcomes (proposal_id, sent_dt, kind, room_id, source)"
                             " VALUES (?, ?, 'cold', ?, 'test')", (pid, sent, room))
            conn.execute("INSERT INTO threads (id) VALUES ('room_have')")
            conn.execute("INSERT INTO messages (id, thread_id, message, created_dt, direction)"
                         " VALUES ('m1', 'room_have', 'hi', '2026-09-26', 'inbound')")
            conn.execute("INSERT INTO threads (id, client_id, latest_message_dt) VALUES"
                         " ('room_client', 'acme', '2026-09-30')")
        self.log = mock.patch.object(pull, "LOG", self.tmp / "pull.log")
        self.log.start()

    def tearDown(self):
        self.log.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestPullAll(PullCase):
    def test_conversations_without_messages_proposals_first_newest_first(self):
        with db.connect(self.db_path) as conn:
            self.assertEqual(pull.rooms_missing_messages(conn), ["room_new", "room_old", "room_client"])

    def test_a_dry_run_reads_and_writes_nothing(self):
        with mock.patch.object(pull, "_session", side_effect=AssertionError("no network in a dry run")):
            rc = pull.main(["--dry-run", "--db", str(self.db_path)])
        self.assertEqual(rc, 0)
        self.assertIn("would_read_rooms", (self.tmp / "pull.log").read_text())

    def test_files_read_again_are_moved_aside_never_deleted(self):
        d = self.tmp / "raw" / "get"
        d.mkdir(parents=True)
        (d / "p1.json").write_text("{}")
        n = pull._move_aside([d / "p1.json", d / "nope.json"], "reads")
        self.assertEqual(n, 1)
        self.assertFalse((d / "p1.json").exists())
        self.assertEqual((self.tmp / "raw" / "get_previous" / "p1.json").read_text(), "{}")
        # the next night replaces the previous copy: the folder never grows
        (d / "p1.json").write_text('{"night": 2}')
        pull._move_aside([d / "p1.json"], "reads")
        self.assertEqual((self.tmp / "raw" / "get_previous" / "p1.json").read_text(), '{"night": 2}')
        self.assertEqual(len(list((self.tmp / "raw").iterdir())), 2)          # get/ and get_previous/

    def test_a_refused_sign_in_stops_the_run_and_is_not_retried(self):
        from modules.upwork.feeds import mcp_client
        calls = []

        def dead():
            calls.append(1)
            raise mcp_client.MCPAuthError("Upwork MCP grant has been dead since ... invalid_grant")
        with mock.patch.object(pull, "_session", side_effect=dead):
            rc = pull.main(["messages", "contracts", "--db", str(self.db_path)])
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 1)
        self.assertIn("STOPPED at messages", (self.tmp / "pull.log").read_text())

    def test_unknown_steps_are_refused(self):
        with self.assertRaises(SystemExit):
            pull.main(["everything", "--db", str(self.db_path)])

    def test_the_new_step_is_named_only_never_part_of_all(self):
        self.assertNotIn("new", pull.STEPS)
        self.assertIn("new", pull.EXTRA_STEPS)

    def test_the_new_step_reads_nothing_when_nothing_is_new(self):
        with mock.patch.object(pull, "_run", side_effect=AssertionError("nothing to read")):
            rc = pull.main(["new", "--db", str(self.db_path)])
        self.assertEqual(rc, 0)
        self.assertIn('new: {"new": 0}', (self.tmp / "pull.log").read_text())

    def test_the_new_step_runs_the_chain_without_moving_anything_aside(self):
        from datetime import date
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO proposals (id, created_dt, job_title) VALUES ('n1', ?, 't')",
                         (date.today().isoformat() + "T08:10:06+00:00",))
        ran = []
        with mock.patch.object(pull, "_run", side_effect=lambda *c: ran.append(c)), \
             mock.patch.object(pull, "_move_aside", side_effect=AssertionError("nothing is moved")), \
             mock.patch.object(pull, "_tail_says_stopped", return_value=False):
            rc = pull.main(["new", "--db", str(self.db_path)])
        self.assertEqual(rc, 0)
        self.assertEqual([c[0] for c in ran], [
            "scripts/funnel/fetch.py", "scripts/funnel/fetch_jobs.py", "scripts/funnel/build_dataset.py",
            "scripts/funnel/features.py", "scripts/funnel/export_rows.py",
            "scripts/proposal_funnel_backfill.py"])
        self.assertEqual(ran[0], ("scripts/funnel/fetch.py", "--new-only"))
        self.assertTrue(ran[-1][-1].startswith("top-up "))


class TestBackfillKeepsTheLabel(unittest.TestCase):
    def test_a_stored_row_keeps_its_source_on_refresh(self):
        src = (ROOT / "scripts" / "proposal_funnel_backfill.py").read_text()
        self.assertIn('if c not in ("proposal_id", "source")', src)
        self.assertIn('"--source"', src)


if __name__ == "__main__":
    unittest.main()
