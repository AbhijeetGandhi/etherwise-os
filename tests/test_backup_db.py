"""The nightly database backup (2026-11-18).

Found on a health check: the only nightly backup copied the retired v2
database, and the live one had no copy newer than five weeks. Pinned here:

  * a backup is a gzipped, consistent copy that OPENS and holds the rows;
  * a copy that does not verify is deleted and the run raises — never kept;
  * retention keeps the newest days plus the first backup of recent months,
    touches only its own files, and a re-run on the same day replaces the day;
  * the rail is registered (critical), scheduled and rerunnable.
"""
from __future__ import annotations

import gzip
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from core import backup, config, db

ROOT = Path(__file__).resolve().parents[1]


class Backup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bk-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO transactions (canonical_key, source, amount_minor, currency)"
                         " VALUES ('k1','Test',100,'USD')")
            conn.execute("INSERT INTO runs (task_name, started_at, status, shadow)"
                         " VALUES ('t','2026-11-18T03:20:00','completed',0)")
        self.target = self.tmp / "nightly"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, day="2026-11-18"):
        return backup.run(self.db_path, now=datetime.fromisoformat(day + "T03:20:00"),
                          target=self.target)

    def test_the_backup_is_a_gzipped_copy_that_opens_and_holds_the_rows(self):
        m = self._run()
        f = Path(m["file"])
        self.assertEqual(f.name, "etherwise-2026-11-18.db.gz")
        self.assertEqual((m["quick_check"], m["rows"]), ("ok", {"transactions": 1, "runs": 1}))
        restored = self.tmp / "restored.db"
        with gzip.open(f, "rb") as fin, open(restored, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        conn = sqlite3.connect(str(restored))
        self.assertEqual(conn.execute("SELECT canonical_key FROM transactions").fetchall(), [("k1",)])
        self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
        conn.close()
        # nothing half-made is left beside it, and the same day replaces itself
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), [f.name])
        self._run()
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), [f.name])

    def test_a_copy_that_does_not_verify_is_never_kept(self):
        real = backup._counts
        calls = []

        def short_second_time(conn):
            calls.append(1)
            c = real(conn)
            return c if len(calls) == 1 else dict(c, transactions=0)     # the copy lost rows
        with mock.patch.object(backup, "_counts", side_effect=short_second_time):
            with self.assertRaises(RuntimeError) as cm:
                self._run()
        self.assertIn("did not verify", str(cm.exception))
        self.assertEqual(list(self.target.iterdir()), [])

    def test_retention_keeps_recent_days_and_month_firsts_and_only_its_own_files(self):
        names = [f"etherwise-2026-{m:02d}-{d:02d}.db.gz"
                 for m, days in ((9, (1, 2, 30)), (10, (1, 15)), (11, range(10, 19)))
                 for d in days]
        stay = backup.keep(names, daily=7, monthly=6)
        self.assertEqual(sorted(stay), [
            "etherwise-2026-09-01.db.gz", "etherwise-2026-10-01.db.gz",
            "etherwise-2026-11-10.db.gz",                         # November's first
            *[f"etherwise-2026-11-{d:02d}.db.gz" for d in range(12, 19)]])
        self.assertEqual(backup.keep(names, daily=1, monthly=0), {"etherwise-2026-11-18.db.gz"})
        self.target.mkdir()
        for n in names + ["etherwise.db.bak-039-202610090628", "notes.txt"]:
            (self.target / n).write_bytes(b"x")
        gone = backup.prune(self.target, 7, 6)
        self.assertEqual(gone, ["etherwise-2026-09-02.db.gz", "etherwise-2026-09-30.db.gz",
                                "etherwise-2026-10-15.db.gz", "etherwise-2026-11-11.db.gz"])
        left = {p.name for p in self.target.iterdir()}
        self.assertTrue({"etherwise.db.bak-039-202610090628", "notes.txt"} <= left)   # not its files


class Registry(unittest.TestCase):
    def test_the_backup_is_registered_scheduled_and_rerunnable(self):
        from modules.cockpit import actions
        src = [s for s in config.SOURCES if s["key"] == "db_backup"][0]
        self.assertEqual((src["task_name"], src["critical"], src["catch_up"]),
                         ("backup_db", True, True))
        self.assertIn("backup_failed", src["degrade_kinds"])
        plist = ROOT / "launchd" / f"{src['launchd_label']}.plist"
        self.assertIn("bin/backup-db", plist.read_text())
        self.assertEqual(actions.RERUN_JOBS["backup_db"], src["launchd_label"])
        self.assertGreaterEqual(config.BACKUP_KEEP_DAILY, 7)


if __name__ == "__main__":
    unittest.main()
