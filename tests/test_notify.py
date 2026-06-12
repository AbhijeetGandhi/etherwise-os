"""Unit tests for core/notify.py — the critical-anomaly interrupt drainer.
SMTP is injected; everything else runs real against a temp DB."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from core import db, notify


class NotifyTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="notify-test-"))
        self.db_path = self.tmp / "t.db"
        db.migrate(self.db_path)
        self.sent = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sender(self, subject, html):
        self.sent.append((subject, html))

    def seed_anomaly(self, kind="task_failure", severity="critical",
                     detail='{"run_id": 7, "error": "boom"}'):
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO anomalies (task_name, kind, detail_json,"
                " severity) VALUES ('t', ?, ?, ?)", (kind, detail, severity))

    def test_drains_criticals_into_one_email(self):
        self.seed_anomaly(kind="task_failure")
        self.seed_anomaly(kind="budget_hard_cap")
        out = notify.drain_critical_anomalies(db_path=self.db_path,
                                              send=self.sender)
        self.assertEqual(out["drained"], 2)
        self.assertEqual(len(self.sent), 1)          # ONE batched interrupt
        subject, html = self.sent[0]
        self.assertIn("INTERRUPT", subject)
        self.assertIn("task_failure", html)
        self.assertIn("budget_hard_cap", html)

    def test_idempotent_via_cursor(self):
        self.seed_anomaly()
        notify.drain_critical_anomalies(db_path=self.db_path,
                                        send=self.sender)
        out = notify.drain_critical_anomalies(db_path=self.db_path,
                                              send=self.sender)
        self.assertEqual(out["drained"], 0)
        self.assertEqual(len(self.sent), 1)

    def test_new_anomaly_after_drain_gets_drained(self):
        self.seed_anomaly()
        notify.drain_critical_anomalies(db_path=self.db_path,
                                        send=self.sender)
        self.seed_anomaly(kind="parity_drift")
        out = notify.drain_critical_anomalies(db_path=self.db_path,
                                              send=self.sender)
        self.assertEqual(out["drained"], 1)
        self.assertIn("parity_drift", self.sent[1][1])

    def test_warn_severity_ignored(self):
        self.seed_anomaly(severity="warn")
        out = notify.drain_critical_anomalies(db_path=self.db_path,
                                              send=self.sender)
        self.assertEqual(out["drained"], 0)
        self.assertEqual(self.sent, [])

    def test_send_failure_does_not_advance_cursor(self):
        self.seed_anomaly()

        def broken(subject, html):
            raise OSError("smtp down")

        with self.assertRaises(OSError):
            notify.drain_critical_anomalies(db_path=self.db_path,
                                            send=broken)
        out = notify.drain_critical_anomalies(db_path=self.db_path,
                                              send=self.sender)
        self.assertEqual(out["drained"], 1)          # retried successfully


if __name__ == "__main__":
    unittest.main()
