"""Wise settlement-from-email — fully offline.

The bodies below are REAL Wise emails (amounts kept, nothing invented), so the
parser is pinned against what Wise actually sends rather than what I assumed it
sends. The arithmetic assertions are the point: these two transfers already exist
in the live ledger from the CSV import as -158224 / -3276 and -159343 / -3297,
so if the parser reproduces those numbers it is provably CSV-equivalent.
"""
from __future__ import annotations

import sqlite3
import unittest

from modules.finance import wise_mail


REAL_1 = ("Your money's been sent (#2282075707)",
          "This notification is for the business account of Etherwise .\n"
          "Hello Abhijeet Ashok,\n"
          "151,240.31 INR is on its way to you.\n"
          "The rate was 1 USD = 95.5862 INR, and our fee was 32.76 USD.\n"
          "Need a receipt? You can download it from your Wise account.")

REAL_2 = ("Your money's been sent (#2282113479)",
          "152,278.85 INR is on its way to you.\n"
          "The rate was 1 USD = 95.5667 INR, and our fee was 32.97 USD.")


class ParseTests(unittest.TestCase):
    def test_reproduces_the_csv_row_to_the_minor_unit(self):
        s = wise_mail.parse_settlement(*REAL_1)
        self.assertFalse(s["incomplete"])
        self.assertEqual(s["transfer_id"], "2282075707")
        # the live CSV row for this transfer is -158224 / -3276
        self.assertEqual(s["sent_minor"], 158224)
        self.assertEqual(s["fee_minor"], 3276)
        self.assertEqual(s["gross_minor"], 161500)      # == the 1,615.00 payment
        self.assertEqual(s["currency"], "USD")
        self.assertEqual(s["net_minor"], 15124031)
        self.assertEqual(s["net_currency"], "INR")
        self.assertEqual(s["fx_rate"], "95.5862")       # string, not float

    def test_second_real_email(self):
        s = wise_mail.parse_settlement(*REAL_2)
        self.assertEqual(s["sent_minor"], 159343)
        self.assertEqual(s["fee_minor"], 3297)
        self.assertEqual(s["gross_minor"], 162640)

    def test_currency_is_read_not_assumed(self):
        # two historical transfers were AUD; a USD default would corrupt them
        s = wise_mail.parse_settlement(
            "Your money's been sent (#2200000001)",
            "1,000.00 INR is on its way to you.\n"
            "The rate was 1 AUD = 50.00 INR, and our fee was 1.50 AUD.")
        self.assertEqual(s["currency"], "AUD")
        self.assertEqual(s["fee_currency"], "AUD")
        self.assertEqual(s["sent_minor"], 2000)

    def test_non_settlement_email_is_not_a_settlement(self):
        self.assertIsNone(wise_mail.parse_settlement(
            "Adelye Ltd just paid you", "You received 682.50 USD"))
        self.assertIsNone(wise_mail.parse_settlement(
            "Your e-FIRC is available (#2282075707)", "your certificate"))

    def test_unparseable_settlement_is_flagged_loudly_not_dropped(self):
        # a Wise template change must surface as an incomplete row, never as a
        # silent skip that quietly stops the ledger updating
        s = wise_mail.parse_settlement(
            "Your money's been sent (#2299999999)", "Your transfer is complete.")
        self.assertTrue(s["incomplete"])
        self.assertEqual(s["transfer_id"], "2299999999")
        self.assertEqual(wise_mail.ledger_rows(s, paid_date="2026-08-10"), [])

    def test_zero_rate_cannot_divide(self):
        s = wise_mail.parse_settlement(
            "Your money's been sent (#2299999998)",
            "0.00 INR is on its way to you.\n"
            "The rate was 1 USD = 0 INR, and our fee was 1.00 USD.")
        self.assertTrue(s["incomplete"])


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE transactions (canonical_key TEXT PRIMARY KEY,"
            " dedup_key TEXT, wise_id TEXT, source TEXT, category TEXT,"
            " pnl_bucket TEXT, amount_minor INTEGER, currency TEXT, fx_rate TEXT,"
            " description TEXT, creation_dt TEXT, pnl_month TEXT,"
            " first_seen_at TEXT, updated_at TEXT)")

    def test_keys_match_the_csv_namespace_exactly(self):
        rows = wise_mail.ledger_rows(
            wise_mail.parse_settlement(*REAL_1), paid_date="2026-07-31")
        keys = sorted(r["canonical_key"] for r in rows)
        # these are the keys the CSV importer produced for this same transfer
        self.assertEqual(keys, ["wise:FEE-TRANSFER-2282075707",
                                "wise:TRANSFER-2282075707"])

    def test_both_legs_are_negative_and_bucketed(self):
        rows = wise_mail.ledger_rows(
            wise_mail.parse_settlement(*REAL_1), paid_date="2026-07-31")
        by = {r["category"]: r for r in rows}
        self.assertEqual(by["transfer_out"]["amount_minor"], -158224)
        self.assertEqual(by["transfer_out"]["pnl_bucket"], "transfer")
        self.assertEqual(by["fee"]["amount_minor"], -3276)
        self.assertEqual(by["fee"]["pnl_bucket"], "platform_fee")

    def test_an_existing_csv_row_always_wins(self):
        # the whole double-count defence: same key -> insert is refused
        self.conn.execute(
            "INSERT INTO transactions (canonical_key, source, amount_minor)"
            " VALUES ('wise:TRANSFER-2282075707','Wise',-158224)")
        rows = wise_mail.ledger_rows(
            wise_mail.parse_settlement(*REAL_1), paid_date="2026-07-31")
        out = wise_mail.commit(self.conn, rows)
        self.assertEqual(out, {"inserted": 1, "already_present": 1})
        kept = self.conn.execute(
            "SELECT source, amount_minor FROM transactions"
            " WHERE canonical_key='wise:TRANSFER-2282075707'").fetchone()
        self.assertEqual(kept, ("Wise", -158224))      # CSV row untouched

    def test_commit_is_idempotent(self):
        rows = wise_mail.ledger_rows(
            wise_mail.parse_settlement(*REAL_2), paid_date="2026-07-31")
        first = wise_mail.commit(self.conn, rows)
        second = wise_mail.commit(self.conn, rows)
        self.assertEqual(first["inserted"], 2)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM transactions").fetchone()[0], 2)

    def test_pnl_month_is_set_so_the_row_reaches_the_pnl(self):
        rows = wise_mail.ledger_rows(
            wise_mail.parse_settlement(*REAL_1), paid_date="2026-07-31")
        wise_mail.commit(self.conn, rows)
        months = {r[0] for r in self.conn.execute(
            "SELECT pnl_month FROM transactions")}
        self.assertEqual(months, {"2026-07"})


if __name__ == "__main__":
    unittest.main()
