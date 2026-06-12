"""Tests for modules/knowledge/fathom_poll.py — HTTP mocked, real cursor +
inbox-file behavior on temp dirs."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db, runner
from modules.knowledge import fathom_poll as fp


def meeting(rid, created="2026-06-13T05:00:00Z"):
    return {"recording_id": rid, "created_at": created,
            "title": f"Call {rid}", "transcript": [{"speaker": "A",
                                                    "text": "hello"}]}


class FathomCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fathom-test-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.inbox = self.tmp / "inbox"
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.tmp / "logs"),
            mock.patch.object(config, "LOCK_DIR", self.tmp / "locks"),
            mock.patch.object(config, "KNOWLEDGE_INBOX", self.inbox),
            mock.patch.object(fp, "api_key", return_value="k"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_poll(self, pages):
        feed = list(pages)

        def fake_fetch(key, created_after, cursor=None):
            return feed.pop(0)

        return runner.run_task(
            fp.TASK_NAME, lambda ctx: fp.poll(ctx, _fetch=fake_fetch),
            module="knowledge", db_path=self.db_path)

    def cursor(self):
        with db.connect(self.db_path) as conn:
            row = conn.execute("SELECT value FROM sync_cursors WHERE name=?",
                               (fp.CURSOR,)).fetchone()
        return row["value"] if row else None

    def test_saves_files_and_advances_cursor(self):
        result = self.run_poll([{"items": [meeting("r1"),
                                           meeting("r2",
                                                   "2026-06-13T06:00:00Z")]}])
        self.assertEqual(result.metrics["saved"], 2)
        files = sorted((self.inbox / "fathom").glob("*.json"))
        self.assertEqual(len(files), 2)
        self.assertIn("2026-06-13__r1.json", files[0].name)
        self.assertEqual(self.cursor(), "2026-06-13T06:00:00Z")
        self.assertEqual(json.loads(files[0].read_text())["title"], "Call r1")

    def test_dedupes_existing_files(self):
        self.run_poll([{"items": [meeting("r1")]}])
        result = self.run_poll([{"items": [meeting("r1"),
                                           meeting("r3",
                                                   "2026-06-13T07:00:00Z")]}])
        self.assertEqual(result.metrics["saved"], 1)
        self.assertEqual(result.metrics["deduped"], 1)

    def test_pagination_follows_next_cursor(self):
        result = self.run_poll([
            {"items": [meeting("a")], "next_cursor": "c2"},
            {"items": [meeting("b", "2026-06-13T08:00:00Z")]},
        ])
        self.assertEqual(result.metrics["saved"], 2)
        self.assertEqual(result.metrics["pages"], 2)

    def test_empty_response_skips(self):
        result = self.run_poll([{"items": []}])
        self.assertEqual(result.status, "skipped_empty")

    def test_runs_row_not_shadowed_relevant(self):
        self.run_poll([{"items": [meeting("r1")]}])
        with db.connect(self.db_path) as conn:
            run = dict(conn.execute("SELECT * FROM runs").fetchone())
        self.assertEqual(run["task_name"], fp.TASK_NAME)
        self.assertEqual(run["shadow"], 1)   # knowledge module flag (writes
        #                                      are local-only so it's moot)


if __name__ == "__main__":
    unittest.main()
