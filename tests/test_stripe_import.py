"""Stripe rail: parse both export types, reconcile payments->payouts+balance,
classify (income/transfer/fee), never double-book charges, and reclassify the
IDFC payout leg to transfer. Fixtures mirror Abhijeet's July sample exactly:
3 payments ₹55,015.53, 2 payouts ₹40,441.45, ₹14,574.08 still in balance.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from core import db
from modules.finance import pnl, ledger
from modules.finance.importers import stripe_csv

PAYMENTS_CSV = """id,Created (UTC),Amount,Currency,Status,Customer Description,Description
py_1,2026-07-03 10:00:00,20000.00,inr,Succeeded,Acme Retail Pvt Ltd,Invoice INV-1
py_2,2026-07-10 09:00:00,20015.53,inr,Succeeded,Beglaubigt,Invoice INV-2
py_3,2026-07-20 12:00:00,15000.00,inr,Succeeded,Acme Retail Pvt Ltd,Invoice INV-3
py_4,2026-07-21 12:00:00,9999.00,inr,Failed,Someone,should be skipped
"""

BALANCE_CSV = """balance_transaction_id,Created (UTC),reporting_category,Amount,Currency,Fee,Net
txn_c1,2026-07-03 10:00:05,charge,20000.00,inr,0,20000.00
txn_c2,2026-07-10 09:00:05,charge,20015.53,inr,0,20015.53
txn_c3,2026-07-20 12:00:05,charge,15000.00,inr,0,15000.00
txn_po1,2026-07-12 00:00:00,payout,-20441.45,inr,0,-20441.45
txn_po2,2026-07-24 00:00:00,payout,-20000.00,inr,0,-20000.00
"""


class ParseCase(unittest.TestCase):
    def test_detects_export_types(self):
        self.assertEqual(stripe_csv.parse(PAYMENTS_CSV)["kind"], "payments")
        self.assertEqual(stripe_csv.parse(BALANCE_CSV)["kind"], "balance_history")

    def test_payments_only_succeeded_with_client(self):
        rows = stripe_csv.parse_payments(PAYMENTS_CSV)
        self.assertEqual(len(rows), 3)                       # the Failed one skipped
        self.assertEqual(rows[0]["customer"], "Acme Retail Pvt Ltd")
        self.assertEqual(rows[0]["amount_minor"], 2000000)   # ₹20,000 -> paise
        self.assertTrue(all(r["kind"] == "income" for r in rows))

    def test_balance_history_classifies_payouts(self):
        rows = stripe_csv.parse_balance_history(BALANCE_CSV)
        kinds = [r["kind"] for r in rows]
        self.assertEqual(kinds.count("settlement"), 2)
        self.assertEqual(kinds.count("charge"), 3)

    def test_reconcile_matches_the_july_sample(self):
        rec = stripe_csv.reconcile(stripe_csv.parse_payments(PAYMENTS_CSV),
                                   stripe_csv.parse_balance_history(BALANCE_CSV))
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["payments_total_minor"], 5501553)   # ₹55,015.53
        self.assertEqual(rec["settlement_total_minor"], 4044145)     # ₹40,441.45
        self.assertEqual(rec["balance_minor"], 1457408)          # ₹14,574.08

    def test_reconcile_fails_when_payouts_exceed_payments(self):
        bad = stripe_csv.parse_balance_history(
            BALANCE_CSV.replace("-20000.00", "-99000.00"))
        rec = stripe_csv.reconcile(stripe_csv.parse_payments(PAYMENTS_CSV), bad)
        self.assertFalse(rec["ok"])


class BucketCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="stripe-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _insert(self, row):
        with db.connect(self.db_path) as conn:
            ledger.insert_stripe_row(conn, row, source_file="s.csv")
            return dict(conn.execute("SELECT * FROM transactions WHERE record_id=?",
                                     (row["stripe_id"],)).fetchone())

    def test_income_is_revenue_and_carries_the_client(self):
        r = self._insert({"stripe_id": "py_1", "kind": "income", "date": "2026-07-03",
                          "amount_minor": 2000000, "amount_base_minor": 24000,
                          "currency": "INR", "customer": "Acme Retail Pvt Ltd",
                          "description": "Invoice"})
        self.assertEqual(pnl.bucket(r), "revenue")
        self.assertEqual(r["assignment_company"], "Acme Retail Pvt Ltd")

    def test_payout_is_transfer_not_revenue(self):
        r = self._insert({"stripe_id": "po1", "kind": "settlement", "date": "2026-07-12",
                          "amount_minor": -2044145, "amount_base_minor": -24500,
                          "currency": "INR", "description": "Payout"})
        self.assertEqual(pnl.bucket(r), "transfer")           # excluded from P&L

    def test_fee_is_platform_fee(self):
        r = self._insert({"stripe_id": "fee1", "kind": "fee", "date": "2026-07-03",
                          "amount_minor": -5000, "amount_base_minor": -60,
                          "currency": "INR", "description": "Stripe fee"})
        self.assertEqual(pnl.bucket(r), "platform_fee")


class PayoutReconcileCase(unittest.TestCase):
    """The IDFC payout leg must be reclassified to transfer, never left as
    revenue/unlinked (that would double-count the Stripe income)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="stripe-po-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_matches_idfc_credit_by_amount_and_window(self):
        import importlib.util, importlib.machinery
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_loader("stripe_bin",
            importlib.machinery.SourceFileLoader("stripe_bin",
                str(root / "bin" / "finance-import-stripe")))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        with db.connect(self.db_path) as conn:
            # an IDFC credit of the exact payout amount, 2 days after the payout
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type, source,"
                " currency, amount_minor, amount_base_minor, pnl_bucket, creation_dt)"
                " VALUES ('idfc_po','k1','Other','IDFC','INR',2044145,24500,"
                "'revenue','2026-07-14T00:00:00+0000')")
            payouts = [{"stripe_id": "po1", "amount_minor": -2044145, "date": "2026-07-12"}]
            matched, ambiguous, unmatched = mod._payout_to_idfc_matches(conn, payouts)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0][1], "idfc_po")
        self.assertEqual(unmatched, [])


if __name__ == "__main__":
    unittest.main()
