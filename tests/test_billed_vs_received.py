"""Billed against received — the two halves of the month.

Abhijeet, 2026-08-27: *"I wanna see both. One is how much money we billed to
the clients in a month, and then actual receivables to the account in that
month as well. For example, this month we billed for like twenty five thousand
dollars, but in account we received like twenty two thousand, which could be
because three thousand is gonna come in next month."*

Until migration 037 the ledger had ONE date — `creation_dt`, which for Upwork
CSV rows is the PAYOUT date — so "revenue this month" silently meant cash and
the billed view did not exist. Re-dating those rows would have destroyed the
cash view to build the billed one (measured: July would have moved by
$1,264.41), so both dates live side by side.

What these guard: billed uses the invoice date and falls back to the payment
date when it is unknown, so an unknown never invents a gap; `pnl_month` still
overrides both; and the bridge between the two figures adds up.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from unittest import mock

from core import config, db
from modules.cockpit import data
from modules.finance import doctor as _doctor


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="billed-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO account_balances (account, currency,"
                         " opening_minor, closing_minor, statement_date, source_file)"
                         " VALUES ('idfc','USD',0,0,'2026-08-01','test')")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rev(self, rid, amount, paid_dt, billed_dt=None, pnl_month=None):
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO transactions (record_id, dedup_key, canonical_key,"
                " amount_minor, amount_base_minor, pnl_bucket, creation_dt,"
                " billed_dt, pnl_month, source, currency) VALUES"
                " (?,?,?,?,?,'revenue',?,?,?,'Upwork-CSV','USD')",
                (rid, rid, "upwork:" + rid, amount, amount, paid_dt, billed_dt, pnl_month))

    def money(self, today="2026-08-27"):
        # money() is hard-gated on finance-doctor GREEN (no finance number in
        # the cockpit on RED). A synthetic ledger is not the thing under test.
        # A REAL (empty, therefore GREEN) result, not a Mock: money() now also
        # asks the doctor what it is letting through on a deadline
        # (`pending()`, 2026-10-07), and a Mock answers every question with
        # another Mock. A stand-in that cannot drift from the class is the class.
        green = _doctor.DoctorResult([])
        with mock.patch.object(_doctor, "run", return_value=green):
            return data.money(self.db_path, today=today)["revenue"]


class TestBilledVsReceived(Case):
    def test_the_gap_between_billed_and_received_is_explained(self):
        self.rev("a", 2200000, "2026-08-05", billed_dt="2026-08-01")   # billed + paid in Aug
        self.rev("b", 300000, "2026-09-02", billed_dt="2026-08-24")    # billed Aug, pays Sep
        self.rev("c", 100000, "2026-08-03", billed_dt="2026-07-28")    # billed Jul, arrived Aug
        r = self.money()
        self.assertEqual(r["month_usd"], 25000.0)          # billed: a + b
        self.assertEqual(r["received_usd"], 23000.0)       # cash: a + c
        self.assertEqual(r["billed_lands_later_usd"], 3000.0)
        self.assertEqual(r["received_billed_earlier_usd"], 1000.0)
        # the bridge closes: billed - lands later + arrived for earlier = received
        self.assertEqual(round(r["month_usd"] - r["billed_lands_later_usd"]
                               + r["received_billed_earlier_usd"], 2), r["received_usd"])

    def test_an_unknown_invoice_date_never_invents_a_gap(self):
        self.rev("a", 500000, "2026-08-10")                # no billed_dt at all
        r = self.money()
        self.assertEqual(r["month_usd"], r["received_usd"])
        self.assertEqual(r["billed_lands_later_usd"], 0)
        self.assertEqual(r["received_billed_earlier_usd"], 0)

    def test_the_manual_accrual_override_still_wins_over_both_dates(self):
        self.rev("a", 400000, "2026-09-05", billed_dt="2026-09-01", pnl_month="2026-08")
        r = self.money()
        self.assertEqual(r["month_usd"], 4000.0)           # pnl_month put it in August
        self.assertEqual(r["received_usd"], 0)             # the cash is still September's

    def test_cash_is_the_payment_date_even_when_billing_moved_it(self):
        self.rev("a", 900000, "2026-08-20", billed_dt="2026-07-01")
        r = self.money()
        self.assertEqual(r["month_usd"], 0)                # billed in July
        self.assertEqual(r["received_usd"], 9000.0)        # arrived in August
        self.assertEqual(r["received_billed_earlier_usd"], 9000.0)

    def test_the_migration_backfills_from_the_mcp_invoice_date(self):
        with db.connect(self.db_path) as conn:
            cols = [c["name"] for c in conn.execute("PRAGMA table_info(transactions)")]
        self.assertIn("billed_dt", cols)
        root = Path(__file__).resolve().parents[1]
        sql = (root / "core" / "migrations" / "037_billed_dt.sql").read_text()
        self.assertIn("upwork_mcp_rows", sql)              # filled from the MCP's invoice_dt
        self.assertIn("billed_dt IS NULL", sql)            # never overwrites


if __name__ == "__main__":
    unittest.main()
