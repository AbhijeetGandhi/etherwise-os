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
            " pnl_bucket TEXT, amount_minor INTEGER, amount_base_minor INTEGER,"
            " currency TEXT, fx_rate TEXT, description TEXT, creation_dt TEXT,"
            " pnl_month TEXT, first_seen_at TEXT, updated_at TEXT)")

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


# Real arrival email (amounts and reference kept verbatim).
ARRIVAL = ("CMF Publishing just paid you",
           "This notification is for the business account of Etherwise .\n"
           "You received 1,626.40 USD from CMF Publishing.\n"
           "The money will be automatically converted to INR and deposited into\n"
           "your verified INR bank account ending with 0234 by the next business day.\n"
           "Payment details\nFrom:\nCMF Publishing\nAmount received:\n"
           "1,626.40 USD\nReference:\nINV-2627-037\nTransfer Number:\nThanks,")


class ArrivalTests(unittest.TestCase):
    def test_parses_payer_amount_and_reference(self):
        a = wise_mail.parse_arrival(*ARRIVAL)
        self.assertEqual(a["payer"], "CMF Publishing")
        self.assertEqual(a["amount_minor"], 162640)
        self.assertEqual(a["currency"], "USD")
        self.assertEqual(a["reference"], "INV-2627-037")

    def test_reference_key_still_includes_date_and_amount(self):
        a = wise_mail.parse_arrival(*ARRIVAL)
        self.assertEqual(wise_mail.income_key(a, "2026-07-31"),
                         "wise:MAILREF-inv-2627-037-2026-07-31-162640")

    def test_a_REPEATED_reference_does_not_collide(self):
        # The live ledger proves Wise's reference is free text that repeats:
        # 23 distinct payments carry "Malave", 4 carry "SENDER". Keying on the
        # reference alone made payments 2..N look like duplicates of payment 1,
        # which DROPS revenue. Two real Malave payments must key differently.
        a1 = dict(payer="X", amount_minor=31500, currency="USD",
                  reference="Malave", incomplete=False)
        a2 = dict(payer="X", amount_minor=91830, currency="USD",
                  reference="Malave", incomplete=False)
        self.assertNotEqual(wise_mail.income_key(a1, "2026-01-16"),
                            wise_mail.income_key(a2, "2025-12-22"))

    def test_key_falls_back_when_wise_prints_no_reference(self):
        # 63 historical rows carried no reference; they still need a stable key
        a = dict(payer="Horizons ABA MIdCo LLC", amount_minor=240680,
                 currency="USD", reference=None, incomplete=False)
        self.assertEqual(wise_mail.income_key(a, "2026-07-03"),
                         "wise:MAILPAY-horizons-aba-midco-llc-2026-07-03-240680")

    def test_settlement_email_is_not_an_arrival(self):
        self.assertIsNone(wise_mail.parse_arrival(*REAL_1))

    def test_description_matches_the_csv_phrasing(self):
        # downstream attribution reads descriptions; mail rows must be
        # indistinguishable from CSV rows or client mapping silently changes
        r = wise_mail.income_row(wise_mail.parse_arrival(*ARRIVAL),
                                 paid_date="2026-07-31")
        self.assertEqual(r["description"],
                         "Received money from CMF Publishing with reference INV-2627-037")
        self.assertEqual(r["pnl_bucket"], "revenue")
        self.assertEqual(r["amount_minor"], 162640)     # income is POSITIVE


# The SECOND shape Wise sends for the same payment (live, 2026-08-10).
ARRIVAL_ALT = ("Money received from Consulting Systems B.V.",
               "You received 3,046.39 USD from Consulting Systems B.V.\n"
               "Reference:\n11477447-2800599882\n")
ARRIVAL_MAIN = ("Consulting Systems BV just paid you",
                "You received 3,046.39 USD from Consulting Systems BV.\n"
                "Reference:\n11477447-2800599882\n")


class DuplicateNotificationTests(unittest.TestCase):
    def test_the_second_shape_parses_at_all(self):
        a = wise_mail.parse_arrival(*ARRIVAL_ALT)
        self.assertIsNotNone(a)
        self.assertEqual(a["amount_minor"], 304639)

    def test_payer_is_not_truncated_at_an_abbreviation_dot(self):
        # "Consulting Systems B.V." was being cut to "Consulting Systems B"
        self.assertEqual(wise_mail.parse_arrival(*ARRIVAL_ALT)["payer"],
                         "Consulting Systems B.V.")

    def test_trailing_sentence_period_is_still_trimmed(self):
        self.assertEqual(wise_mail.parse_arrival(*ARRIVAL_MAIN)["payer"],
                         "Consulting Systems BV")

    def test_the_two_shapes_collapse_to_ONE_payment(self):
        pair = [(wise_mail.parse_arrival(*ARRIVAL_MAIN), "2026-08-10"),
                (wise_mail.parse_arrival(*ARRIVAL_ALT), "2026-08-10")]
        self.assertEqual(len(wise_mail.dedupe_arrivals(pair)), 1)

    def test_collapse_does_not_depend_on_the_reference_matching(self):
        # the real risk: if the second shape omits the reference the keys
        # diverge and the payment counts twice. Identity is date+amount.
        alt = dict(wise_mail.parse_arrival(*ARRIVAL_ALT), reference=None)
        pair = [(wise_mail.parse_arrival(*ARRIVAL_MAIN), "2026-08-10"),
                (alt, "2026-08-10")]
        kept = wise_mail.dedupe_arrivals(pair)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0][0]["reference"], "11477447-2800599882",
                         "the copy WITH a reference must win")

    def test_two_real_payments_the_same_day_are_NOT_collapsed(self):
        # CMF genuinely sent 1,615.00 and 1,626.40 on 2026-07-31
        a1 = dict(payer="CMF", amount_minor=161500, currency="USD",
                  reference="INV-042", incomplete=False)
        a2 = dict(payer="CMF", amount_minor=162640, currency="USD",
                  reference="INV-037", incomplete=False)
        self.assertEqual(
            len(wise_mail.dedupe_arrivals([(a1, "2026-07-31"), (a2, "2026-07-31")])), 2)


class IncomeCommitTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE transactions (canonical_key TEXT PRIMARY KEY,"
            " dedup_key TEXT, wise_id TEXT, source TEXT, category TEXT,"
            " pnl_bucket TEXT, amount_minor INTEGER, amount_base_minor INTEGER,"
            " currency TEXT, fx_rate TEXT, description TEXT, creation_dt TEXT,"
            " pnl_month TEXT, first_seen_at TEXT, updated_at TEXT)")
        self.a = wise_mail.parse_arrival(*ARRIVAL)

    def _csv_row(self, desc, minor=162640, dt="2026-07-31"):
        self.conn.execute(
            "INSERT INTO transactions (canonical_key, source, category,"
            " amount_minor, currency, description, creation_dt)"
            " VALUES ('wise:TRANSFER-2282113339','Wise','income',?,'USD',?,?)",
            (minor, desc, dt))

    def test_writes_income_when_the_ledger_has_none(self):
        out = wise_mail.commit_income(self.conn, [(self.a, "2026-07-31")])
        self.assertEqual(out["inserted"], 1)
        row = self.conn.execute(
            "SELECT source, category, amount_minor, pnl_month FROM transactions"
        ).fetchone()
        self.assertEqual(row, ("Wise-mail", "income", 162640, "2026-07"))

    def test_usd_rows_carry_amount_base_minor(self):
        # payment_mail_rail._ledger_twin requires amount_base_minor IS NOT NULL.
        # Without it the payment reconciles against nothing and the AR view keeps
        # claiming the client never paid — which is exactly what happened.
        wise_mail.commit_income(self.conn, [(self.a, "2026-07-31")])
        self.assertEqual(self.conn.execute(
            "SELECT amount_base_minor FROM transactions").fetchone()[0], 162640)

    def test_non_usd_leaves_base_null_rather_than_inventing_a_rate(self):
        a = dict(self.a, currency="AUD")
        wise_mail.commit_income(self.conn, [(a, "2026-07-31")])
        self.assertIsNone(self.conn.execute(
            "SELECT amount_base_minor FROM transactions").fetchone()[0])

    def test_refuses_when_the_csv_already_has_it_BY_REFERENCE(self):
        # the CSV keys income by a transfer id we cannot see, so reference
        # matching is the only thing standing between us and double revenue
        self._csv_row("Received money from CMF Publishing LLC with reference "
                      "INV-2627-037")
        out = wise_mail.commit_income(self.conn, [(self.a, "2026-07-31")])
        self.assertEqual(out["inserted"], 0)
        self.assertEqual(out["already_present"], 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM transactions").fetchone()[0], 1)

    def test_refuses_when_the_csv_has_it_with_NO_reference(self):
        self._csv_row("Received money from CMF Publishing")
        a = dict(self.a, reference=None)
        out = wise_mail.commit_income(self.conn, [(a, "2026-07-31")])
        self.assertEqual(out["inserted"], 0)

    def test_a_different_payment_same_day_is_NOT_a_duplicate(self):
        # CMF really did send two payments on 2026-07-31 (1,615.00 and 1,626.40)
        self._csv_row("Received money from CMF Publishing LLC with reference "
                      "INV-2627-042", minor=161500)
        out = wise_mail.commit_income(self.conn, [(self.a, "2026-07-31")])
        self.assertEqual(out["inserted"], 1)          # the 1,626.40 is genuinely new
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM transactions").fetchone()[0], 2)

    def test_a_repeated_reference_on_a_NEW_payment_is_still_written(self):
        # regression for the under-count: an older payment sharing the free-text
        # reference must NOT make this one look already-present
        self._csv_row("Received money from Someone with reference Malave",
                      minor=31500, dt="2026-01-16")
        a = dict(payer="Someone", amount_minor=91830, currency="USD",
                 reference="Malave", incomplete=False)
        out = wise_mail.commit_income(self.conn, [(a, "2026-06-22")])
        self.assertEqual(out["inserted"], 1)

    def test_reference_match_tolerates_a_few_days_of_date_drift(self):
        self._csv_row("Received money from CMF Publishing LLC with reference "
                      "INV-2627-037", dt="2026-08-02")
        out = wise_mail.commit_income(self.conn, [(self.a, "2026-07-31")])
        self.assertEqual(out["inserted"], 0)     # same payment, dated 2 days on

    def test_commit_income_is_idempotent(self):
        first = wise_mail.commit_income(self.conn, [(self.a, "2026-07-31")])
        second = wise_mail.commit_income(self.conn, [(self.a, "2026-07-31")])
        self.assertEqual((first["inserted"], second["inserted"]), (1, 0))

    def test_incomplete_arrival_is_skipped_not_written(self):
        out = wise_mail.commit_income(
            self.conn, [({"payer": "X", "incomplete": True}, "2026-07-31")])
        self.assertEqual(out["inserted"], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM transactions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
