"""bin/finance-stripe-sync — the 06:20 Stripe rail.

It had no tests, and it had a bug that only a RED finance-doctor could reach:
`dr.reds` (the bound method) where `dr.reds()` was meant. On a RED morning the
job crashed with a TypeError instead of reporting which invariant had failed —
found on 2026-10-06, the first time a RED at 06:20 was actually on the cards
(September's bank statement in the ledger ahead of the Stripe export, #11 RED).
No network, no subprocess: the importer call and the doctor are both stubbed.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

from modules.finance import doctor


def _load():
    path = Path(__file__).resolve().parents[1] / "bin" / "finance-stripe-sync"
    loader = importlib.machinery.SourceFileLoader("fin_stripe_sync", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _Ctx:
    def __init__(self):
        self.anomalies, self.lines = [], []

    def anomaly(self, kind, severity, detail):
        # exactly what core.runner does with it
        self.anomalies.append((kind, severity, json.loads(json.dumps(detail, default=str))))

    def log(self, msg):
        self.lines.append(msg)


def _result(*checks):
    return doctor.DoctorResult([doctor.Check(*c) for c in checks])


class TestStripeSync(unittest.TestCase):
    def setUp(self):
        self.mod = _load()
        self.ctx = _Ctx()

    def _sync(self, imp, dr):
        calls = []

        def fake_bin(name, *args):
            calls.append((name, args))
            return imp if name == "finance-import-stripe" else {"changed": 0}
        with mock.patch.object(self.mod, "_bin", side_effect=fake_bin), \
                mock.patch.object(self.mod.fdoc, "run", return_value=dr):
            return self.mod.sync(self.ctx), calls

    def test_a_red_doctor_is_reported_by_name_not_crashed_on(self):
        """The morning this job has something to say."""
        dr = _result(("#1 statement integrity", "GREEN", "ok"),
                     ("#11 Stripe arrival", "RED", "1 of 7 tie to no settlement"),
                     ("#15 bank splits", "RED", "a leg is missing"))
        out, _ = self._sync({"inserted": 0}, dr)
        self.assertFalse(out["doctor_green"])
        self.assertEqual(self.ctx.anomalies, [
            ("finance_stripe_doctor_red", "critical",
             {"reds": ["#11 Stripe arrival", "#15 bank splits"]})])
        self.assertEqual(len(self.ctx.lines), 1)
        self.assertIn("doctor RED #11 Stripe arrival, #15 bank splits", self.ctx.lines[0])
        self.assertNotIn("bound method", self.ctx.lines[0])

    def test_a_green_doctor_files_nothing(self):
        dr = _result(("#11 Stripe arrival", "GREEN", "7 tie"),
                     ("#7 transfer-integrity", "N/A", "pending"))
        out, calls = self._sync({"inserted": 0}, dr)
        self.assertTrue(out["doctor_green"])
        self.assertEqual(out["doctor_waiting"], 0)
        self.assertEqual(self.ctx.anomalies, [])
        self.assertTrue(self.ctx.lines[0].endswith("doctor GREEN"))
        self.assertEqual([c[0] for c in calls], ["finance-import-stripe"])   # nothing new: no bucketize

    def test_a_green_that_is_waiting_on_an_export_says_so(self):
        """#11 may let a landing wait 14 days for the Stripe export that proves
        it (his answer, 2026-10-07). That is GREEN — no anomaly — but this is
        the job that imports the export, so its log line says one is owed."""
        check = doctor.Check("#11 Stripe arrival", "GREEN", "6 tie; 1 waiting",
                             fix="Upload a Stripe balance export.",
                             pending=[{"record_id": "bank:x", "due": "2026-10-20"}])
        out, _ = self._sync({"inserted": 0}, doctor.DoctorResult([check]))
        self.assertTrue(out["doctor_green"])
        self.assertEqual(out["doctor_waiting"], 1)
        self.assertEqual(self.ctx.anomalies, [])
        self.assertIn("doctor GREEN (1 waiting on an upload, first due 2026-10-20)",
                      self.ctx.lines[0])

    def test_new_rows_are_bucketized_before_the_doctor_is_asked(self):
        """A payout that arrives in a fresh export is what turns #11 back to
        GREEN; its rows need their buckets before the verdict is read."""
        dr = _result(("#11 Stripe arrival", "GREEN", "7 tie"))
        out, calls = self._sync({"inserted": 2, "payout_idfc_reclassified": 1,
                                 "reconcile": {"ok": True}}, dr)
        self.assertEqual([c[0] for c in calls], ["finance-import-stripe", "finance-bucketize"])
        self.assertEqual(calls[1][1], ("--apply",))
        self.assertEqual((out["inserted"], out["reconcile_ok"], out["payout_idfc_reclassified"]),
                         (2, True, 1))

    def test_an_aborted_import_is_an_anomaly_and_a_failed_run(self):
        with self.assertRaises(RuntimeError):
            self._sync({"abort": "identity does not hold", "reconcile": {"ok": False}},
                       _result(("#11 Stripe arrival", "GREEN", "x")))
        self.assertEqual(self.ctx.anomalies[0][0], "finance_stripe_reconcile")


if __name__ == "__main__":
    unittest.main()
