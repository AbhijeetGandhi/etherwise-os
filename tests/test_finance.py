"""M5 finance correctness tests (§3). The two GUARDS (float-ban, no-disbursement)
are the prime-directive enforcers; plus money primitives, the doctor invariants,
and money-type migration Σ-invariance. No network."""
from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from core import db
from modules.finance import money, doctor

FINANCE_DIR = Path(__file__).resolve().parents[1] / "modules" / "finance"


# ── §3.1 GUARD: float is banned anywhere near money ──────────────────────────
class TestFloatBan(unittest.TestCase):
    def test_no_float_constructor_in_finance_module(self):
        offenders = []
        for py in FINANCE_DIR.rglob("*.py"):
            for i, line in enumerate(py.read_text().splitlines(), 1):
                # the dangerous cast/literal; isinstance(x, float) guards are
                # 'float)' not 'float(' and are allowed
                if re.search(r"\bfloat\(", line):
                    offenders.append(f"{py.name}:{i}: {line.strip()}")
        self.assertEqual(offenders, [], "float() in a money path:\n"
                         + "\n".join(offenders))

    def test_no_real_money_column_added(self):
        # money columns must be INTEGER minor or TEXT(Decimal); never REAL.
        # scan only the DDL (drop comment lines, which may say "legacy real").
        sql = (Path(__file__).resolve().parents[1] / "core" / "migrations"
               / "007_finance.sql").read_text()
        ddl = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
        self.assertNotIn("real", " ".join(ddl).lower())


# ── §6 GUARD: the system never moves money ───────────────────────────────────
class TestNoDisbursement(unittest.TestCase):
    BANNED = ("disburse", "payout", "transfer_funds", "release_funds",
              "send_money", "razorpay", "make_payment", "initiate_payment")

    def test_no_disbursement_symbol_in_finance(self):
        hits = []
        for py in FINANCE_DIR.rglob("*.py"):
            src = py.read_text().lower()
            for word in self.BANNED:
                # allow it only inside an explicit 'never'/'no ' disclaimer line
                for i, line in enumerate(src.splitlines(), 1):
                    if word in line and not any(
                            neg in line for neg in ("never", "no ", "not ",
                                                    "manual", "#")):
                        hits.append(f"{py.name}:{i}:{word}")
        self.assertEqual(hits, [], f"disbursement-like symbol: {hits}")


# ── §3.1 money primitives ────────────────────────────────────────────────────
class TestMoney(unittest.TestCase):
    def test_to_minor_exact(self):
        self.assertEqual(money.to_minor("285.17"), 28517)
        self.assertEqual(money.to_minor("-5357.14"), -535714)
        self.assertEqual(money.to_minor(Decimal("100.00")), 10000)

    def test_round_half_up(self):
        # 0.005 -> 0.01 (HALF_UP), the documented boundary rule
        self.assertEqual(money.to_minor("0.005"), 1)
        self.assertEqual(money.to_minor("0.004"), 0)
        self.assertEqual(money.pct(10000, "0.05"), 5)   # 0.05% of 100.00 = 0.005 -> 1c?
    def test_float_rejected_everywhere(self):
        for fn in (money.to_minor,):
            with self.assertRaises(TypeError):
                fn(285.17)              # a float must never enter
        with self.assertRaises(TypeError):
            money.apply_rate(100, 1.5)
        with self.assertRaises(TypeError):
            money.pct(100, 50.0)

    def test_from_minor_roundtrip(self):
        self.assertEqual(money.from_minor(28517), Decimal("285.17"))
        self.assertEqual(money.fmt(-1190476, "$"), "$-11,904.76")

    def test_pct_residual_sums_to_gross(self):
        gross = 10000000  # ₹100,000.00
        basic = money.pct(gross, "50")
        hra = money.pct(gross, "25")
        lta = money.pct(gross, "10")
        special = gross - basic - hra - lta      # residual -> exact
        self.assertEqual(basic + hra + lta + special, gross)


# ── §3.3 doctor invariants ───────────────────────────────────────────────────
class DoctorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fin-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _txn(self, conn, rid, amount_minor=10000, ccy="USD", ckey=None):
        conn.execute(
            "INSERT INTO transactions (record_id, amount_minor, currency,"
            " canonical_key) VALUES (?,?,?,?)", (rid, amount_minor, ccy, ckey))

    def test_clean_ledger_green(self):
        with db.connect(self.db_path) as conn:
            self._txn(conn, "t1", 10000, "USD", "k1")
            self._txn(conn, "t2", -5000, "USD", "k2")
        res = doctor.run(self.db_path)
        self.assertTrue(res.green, res.summary())

    def test_null_amount_red_8(self):
        with db.connect(self.db_path) as conn:
            self._txn(conn, "t1", None, "USD", "k1")   # malformed
        res = doctor.run(self.db_path)
        self.assertFalse(res.green)
        self.assertIn("#8 sanity bounds", [c.invariant for c in res.reds()])

    def test_dup_canonical_key_red_5(self):
        with db.connect(self.db_path) as conn:
            self._txn(conn, "t1", 100, "USD", "dup")
        # the UNIQUE index prevents dups; #5 is GREEN when keys are unique+present
        res = doctor.run(self.db_path)
        self.assertNotIn("#5 dedup integrity",
                         [c.invariant for c in res.reds()])

    def test_null_canonical_key_red_5(self):
        # the 2026-06-25 class: a NULL canonical_key is invisible to dedup and can
        # double-count a keyed twin -> strengthened #5 must FAIL on it
        with db.connect(self.db_path) as conn:
            self._txn(conn, "t1", 100, "USD", None)      # NULL key
        res = doctor.run(self.db_path)
        self.assertFalse(res.green)
        c5 = [c for c in res.checks if c.invariant == "#5 dedup integrity"][0]
        self.assertEqual(c5.status, "RED")
        self.assertIn("NULL canonical_key", c5.detail)

    def test_connect_purchase_sign_red_9(self):
        # a Connect Purchase is a fee (debit); a + one is the sync bug -> RED
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO transactions (record_id, amount_minor,"
                         " currency, canonical_key, type) VALUES"
                         " ('c1', 4500, 'USD', 'upwork:c1', 'Connect Purchase')")
        res = doctor.run(self.db_path)
        self.assertFalse(res.green)
        c9 = [c for c in res.checks if c.invariant == "#9 type-sign sanity"][0]
        self.assertEqual(c9.status, "RED")

    def test_connect_purchase_negative_green_9(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO transactions (record_id, amount_minor,"
                         " currency, canonical_key, type) VALUES"
                         " ('c1', -4500, 'USD', 'upwork:c1', 'Connect Purchase')")
        res = doctor.run(self.db_path)
        c9 = [c for c in res.checks if c.invariant == "#9 type-sign sanity"][0]
        self.assertEqual(c9.status, "GREEN")

    def test_gst_charge_positive_red_9(self):
        # a GST CHARGE (ARInvoice) is a tax debit; a + one is the sync bug -> RED
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO transactions (record_id, amount_minor,"
                         " currency, canonical_key, type, upwork_type,"
                         " accounting_subtype) VALUES"
                         " ('g1', 360, 'USD', 'upwork:g1', 'Other', 'ARInvoice', 'GST')")
        res = doctor.run(self.db_path)
        c9 = [c for c in res.checks if c.invariant == "#9 type-sign sanity"][0]
        self.assertEqual(c9.status, "RED")

    def test_gst_refund_positive_green_9(self):
        # a GST REFUND (APAdjustment) is money back -> legitimately +; NOT flagged
        # (the sign tracks charge-vs-refund, not the GST label — statement authority)
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO transactions (record_id, amount_minor,"
                         " currency, canonical_key, type, upwork_type,"
                         " accounting_subtype) VALUES"
                         " ('g2', 330, 'USD', 'upwork:g2', 'Other', 'APAdjustment', 'GST')")
        res = doctor.run(self.db_path)
        c9 = [c for c in res.checks if c.invariant == "#9 type-sign sanity"][0]
        self.assertEqual(c9.status, "GREEN")

    def _transfer_row(self, conn, rid, amount_minor, *, category=None,
                      subtype=None, upwork_type=None, day="2026-05-10"):
        conn.execute(
            "INSERT INTO transactions (record_id, amount_minor, amount_base_minor,"
            " currency, canonical_key, type, upwork_type, accounting_subtype,"
            " category, creation_dt) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, amount_minor, amount_minor, "USD", "k-" + rid, "Other",
             upwork_type, subtype, category, day + "T00:00:00+0000"))

    def test_transfer_integrity_identifies_legs_na_pending(self):
        # #7 identifies the transfer bucket (in-legs + out-legs) and reports N/A:
        # the core guarantee is exclusion from P&L; the cross-currency both-legs
        # tie-out is done by finance-tieout-transfers (realized-INR), not a
        # date-proximity rule. Never RED on identification -> doctor stays green.
        with db.connect(self.db_path) as conn:
            self._transfer_row(conn, "w1", -158223, subtype="Withdrawal",
                               upwork_type="APPayment")
            self._transfer_row(conn, "in1", 13000000, category="transfer_in")
        res = doctor.run(self.db_path)
        c7 = [c for c in res.checks if c.invariant == "#7 transfer-integrity"][0]
        self.assertEqual(c7.status, "N/A")
        self.assertIn("excluded from P&L", c7.detail)
        self.assertTrue(res.green)

    def _gr(self, conn, rid, minor, day, source="IDFC"):
        conn.execute(
            "INSERT INTO transactions (record_id, amount_minor, amount_base_minor,"
            " currency, canonical_key, type, source, category, description, creation_dt)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, minor, minor, "INR", "bank-" + rid, "Other", source, "transfer_in",
             "NEFT/CITIN1/GLOBAL REMITTANCE-OTHERS/CITI0100000/388 GREENWICH ST",
             day + "T00:00:00+0000"))

    def _stripe_out(self, conn, rid, minor, day):
        conn.execute(
            "INSERT INTO transactions (record_id, amount_minor, amount_base_minor,"
            " currency, canonical_key, type, source, creation_dt)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (rid, minor, minor, "INR", "stripe-" + rid, "Other", "Stripe",
             day + "T00:00:00+0000"))

    def test_unnarrated_salary_13_catches_a_payroll_payment_filed_as_a_draw(self):
        """Yogita's 31 July lines stopped at the IFSC, so Rs 49,800 of payroll
        read as an owner draw and nothing anywhere could notice."""
        with db.connect(self.db_path) as conn:
            for rid, minor, day, desc, cat in (
                    ("j", -4980000, "2026-06-30",
                     "NEFT/a/YOGITA SUDHIR SOMA/BKID/Salary june", "salary"),
                    ("y", -4880000, "2026-07-31",
                     "NEFT/b/YOGITA SUDHIR SOMA/BKID0000098", "expense")):
                conn.execute(
                    "INSERT INTO transactions (record_id, canonical_key, type, source,"
                    " category, pnl_bucket, amount_minor, currency, description, creation_dt)"
                    " VALUES (?,?,'Other','IDFC',?,'owner_draw',?,'INR',?,?)",
                    (rid, "bank:" + rid, cat, minor, desc, day + "T00:00:00+0000"))
        res = doctor.run(self.db_path)
        c = [x for x in res.checks if x.invariant == "#13 unnarrated salary"][0]
        self.assertEqual(c.status, "RED")
        self.assertEqual(c.deltas[0]["who"], "YOGITA SUDHIR SOMA")
        self.assertFalse(res.green)
        with db.connect(self.db_path) as conn:
            from modules.finance import salary_lines
            salary_lines.apply(conn)
        c = [x for x in doctor.run(self.db_path).checks
             if x.invariant == "#13 unnarrated salary"][0]
        self.assertEqual(c.status, "GREEN")

    def test_effective_month_12_catches_a_salary_on_the_wrong_month(self):
        """The check that did not exist. Migration 016 decided this on 3 August,
        two rows were fixed by hand, and nothing watched — so it drifted for five
        months in silence. RED here is self-healing: bucketize writes the months,
        and the cockpit runs bucketize after every import."""
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type, source,"
                " category, pnl_bucket, amount_minor, currency, description, creation_dt)"
                " VALUES ('s1','bank:s1','Other','IDFC','salary','payroll',-14100000,"
                " 'INR','NEFT/x/SHRUTI DEEPAK DHUMNE/HDFC/Salary august','2026-09-01')")
        res = doctor.run(self.db_path)
        c = [x for x in res.checks if x.invariant == "#12 effective month"][0]
        self.assertEqual(c.status, "RED")
        self.assertEqual(c.deltas[0]["named"], "2026-08")
        self.assertFalse(res.green)
        with db.connect(self.db_path) as conn:          # the pass runs
            from modules.finance import accrual
            accrual.apply(conn)
        c = [x for x in doctor.run(self.db_path).checks
             if x.invariant == "#12 effective month"][0]
        self.assertEqual(c.status, "GREEN")

    def test_effective_month_12_ignores_an_on_time_payment(self):
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type, source,"
                " category, pnl_bucket, amount_minor, currency, description, creation_dt)"
                " VALUES ('s2','bank:s2','Other','IDFC','salary','payroll',-4980000,"
                " 'INR','NEFT/x/YOGITA SUDHIR SOMA/BKID/Salary august','2026-08-31')")
        c = [x for x in doctor.run(self.db_path).checks
             if x.invariant == "#12 effective month"][0]
        self.assertEqual(c.status, "GREEN")

    def _tds(self, conn, key, minor, day, desc, rate="0.0105"):
        conn.execute(
            "INSERT INTO transactions (record_id, canonical_key, type, source,"
            " category, pnl_bucket, amount_minor, amount_base_minor, currency,"
            " fx_rate, fx_date, description, creation_dt) VALUES"
            " (?,?,'Other','IDFC','expense','payroll',?,?,'INR',?,?,?,?)",
            (key, key, minor, money.apply_rate(minor, rate), rate, day, desc,
             day + "T00:00:00+0000"))

    def _all_tds(self, conn):
        from modules.finance import payroll_tax as pt
        self._tds(conn, "bank:553f3afc6c497e3e", -2808000, "2026-05-06",
                  "IFT/10171724864/PRITI BORA & CO /13337732/TDS for april")
        self._tds(conn, "bank:04607cb1178e7003", -6098500, "2026-07-07",
                  "IFT/10171724864/PRITI BORA & CO /26482502/TDS may and june")
        self._tds(conn, "bank:1b70a0304700d50b", -3055500, "2026-08-06",
                  "IFT/10171724864/PRITI BORA & CO /27883768/TDS July")
        self._tds(conn, "bank:6435ebd60cc8b361", -4300000, "2026-09-07",
                  "IFT/10171724864/PRITI BORA & CO /23388539/TDS august")
        pt.apply(conn)

    def test_payroll_tds_14_green_when_his_months_tie_to_the_bank(self):
        """His design, 2026-09-07: "Ask me tds amount each month I will give you.
        then you can match it against the deduct from bank account."""
        with db.connect(self.db_path) as conn:
            self._all_tds(conn)
        c = [x for x in doctor.run(self.db_path).checks
             if x.invariant == "#14 payroll TDS"][0]
        self.assertEqual(c.status, "GREEN")

    def test_payroll_tds_14_is_na_on_a_ledger_with_no_remittances(self):
        c = [x for x in doctor.run(self.db_path).checks
             if x.invariant == "#14 payroll TDS"][0]
        self.assertEqual(c.status, "N/A")
        self.assertTrue(doctor.run(self.db_path).green)

    def test_payroll_tds_14_asks_for_the_month_of_a_remittance_it_has_not_got(self):
        """"august you will see in the next statement". When it does, the ledger
        must ask him for the figure — not book a month's payroll on the day it
        cleared, which is exactly how the #12 drift survived five months.

        August's did land (7 Sep, Rs 43,000) and he gave the month on
        2026-10-06, so the row that has no month behind it is now September's,
        due in October's statement. Same loop, one month on."""
        with db.connect(self.db_path) as conn:
            self._all_tds(conn)
            self._tds(conn, "bank:sep-tds", -3100000, "2026-10-07",
                      "IFT/10171724864/PRITI BORA & CO /99999999/TDS September")
        res = doctor.run(self.db_path)
        c = [x for x in res.checks if x.invariant == "#14 payroll TDS"][0]
        self.assertEqual(c.status, "RED")
        self.assertEqual(c.deltas[0]["key"], "bank:sep-tds")
        self.assertFalse(res.green)

    def test_payroll_tds_14_red_when_the_months_do_not_add_up_to_the_payment(self):
        with db.connect(self.db_path) as conn:
            self._all_tds(conn)
            conn.execute("UPDATE transactions SET amount_minor=-6200000"
                         " WHERE canonical_key='bank:04607cb1178e7003'")
        c = [x for x in doctor.run(self.db_path).checks
             if x.invariant == "#14 payroll TDS"][0]
        self.assertEqual(c.status, "RED")
        self.assertEqual(c.deltas[0]["delta_minor"], 6200000 - 6098500)

    def test_payroll_tds_14_red_while_a_two_month_remittance_sits_whole(self):
        """Self-healing, like #12 and #13: run bin/finance-bucketize."""
        with db.connect(self.db_path) as conn:
            self._tds(conn, "bank:04607cb1178e7003", -6098500, "2026-07-07",
                      "IFT/10171724864/PRITI BORA & CO /26482502/TDS may and june")
            self._tds(conn, "bank:553f3afc6c497e3e", -2808000, "2026-05-06",
                      "IFT/10171724864/PRITI BORA & CO /13337732/TDS for april")
            self._tds(conn, "bank:1b70a0304700d50b", -3055500, "2026-08-06",
                      "IFT/10171724864/PRITI BORA & CO /27883768/TDS July")
            self._tds(conn, "bank:6435ebd60cc8b361", -4300000, "2026-09-07",
                      "IFT/10171724864/PRITI BORA & CO /23388539/TDS august")
        c = [x for x in doctor.run(self.db_path).checks
             if x.invariant == "#14 payroll TDS"][0]
        self.assertEqual(c.status, "RED")
        self.assertIn("bucketize", c.detail)
        with db.connect(self.db_path) as conn:
            from modules.finance import payroll_tax
            payroll_tax.apply(conn)
        c = [x for x in doctor.run(self.db_path).checks
             if x.invariant == "#14 payroll TDS"][0]
        self.assertEqual(c.status, "GREEN")

    def test_stripe_arrival_11_green_when_every_landing_ties(self):
        with db.connect(self.db_path) as conn:
            self._stripe_out(conn, "s1", -1457408, "2026-08-02")
            self._gr(conn, "g1", 1457408, "2026-08-03")
        c = [x for x in doctor.run(self.db_path).checks
             if x.invariant == "#11 Stripe arrival"][0]
        self.assertEqual(c.status, "GREEN")

    def test_stripe_arrival_11_red_when_a_landing_ties_to_nothing(self):
        """The exclusion is what makes this check necessary: an unpaired landing
        has been kept OUT of revenue, so a real client wire Citi labelled the
        same way would go missing rather than double. Missing money must shout."""
        with db.connect(self.db_path) as conn:
            self._gr(conn, "g1", 5610357, "2026-08-17")
        res = doctor.run(self.db_path)
        c = [x for x in res.checks if x.invariant == "#11 Stripe arrival"][0]
        self.assertEqual(c.status, "RED")
        self.assertEqual(c.deltas[0]["amount_minor"], 5610357)
        self.assertFalse(res.green)

    def test_stripe_arrival_11_will_not_pair_one_settlement_twice(self):
        # two identical landings, one settlement -> the second is an orphan
        with db.connect(self.db_path) as conn:
            self._stripe_out(conn, "s1", -400000, "2026-08-02")
            self._gr(conn, "g1", 400000, "2026-08-03")
            self._gr(conn, "g2", 400000, "2026-08-04")
        c = [x for x in doctor.run(self.db_path).checks
             if x.invariant == "#11 Stripe arrival"][0]
        self.assertEqual((c.status, len(c.deltas)), ("RED", 1))

    def test_stripe_arrival_11_window_is_not_open_ended(self):
        with db.connect(self.db_path) as conn:
            self._stripe_out(conn, "s1", -400000, "2026-08-02")
            self._gr(conn, "g1", 400000, "2026-08-20")        # 18 days later
        c = [x for x in doctor.run(self.db_path).checks
             if x.invariant == "#11 Stripe arrival"][0]
        self.assertEqual(c.status, "RED")

    def test_bucket_reproducibility_10(self):
        from modules.finance import pnl
        with db.connect(self.db_path) as conn:
            self._txn(conn, "b1", 10000, "USD", "k1")
            conn.execute("UPDATE transactions SET type='Hourly Earning' WHERE record_id='b1'")
            # not bucketized yet -> #10 N/A
            res = doctor.run(self.db_path)
            c10 = [c for c in res.checks if c.invariant == "#10 P&L reproducibility"][0]
            self.assertEqual(c10.status, "N/A")
            # stored == bucket() -> GREEN
            conn.execute("UPDATE transactions SET pnl_bucket='revenue' WHERE record_id='b1'")
        self.assertEqual([c for c in doctor.run(self.db_path).checks
                          if c.invariant == "#10 P&L reproducibility"][0].status, "GREEN")
        # stale stored bucket -> RED (P&L not reproducible)
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE transactions SET pnl_bucket='opex' WHERE record_id='b1'")
        res = doctor.run(self.db_path)
        c10 = [c for c in res.checks if c.invariant == "#10 P&L reproducibility"][0]
        self.assertEqual(c10.status, "RED")
        self.assertFalse(res.green)

    def test_statement_integrity_skips_derived_balances(self):
        # a 'derived:' balance (e.g. the FD principal) is an aggregate, not a
        # statement -> #1 must NOT try to reconcile it against transactions.
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO account_balances (account,currency,opening_minor,"
                         "closing_minor,statement_date,source_file) VALUES"
                         " ('idfc-fd','INR',0,5006858,'2026-06-25','derived:sweep-legs:x'),"
                         " ('idfc-fd','INR',0,5868784,'2026-06-26','authoritative:idfc-app-fd:x')")
        res = doctor.run(self.db_path)
        c1 = [c for c in res.checks if c.invariant == "#1 statement integrity"][0]
        self.assertNotEqual(c1.status, "RED")   # both derived + authoritative skipped

    def _bal(self, conn, account, opening, closing, date, src, ccy="USD"):
        conn.execute("INSERT INTO account_balances (account,currency,opening_minor,"
                     "closing_minor,statement_date,source_file) VALUES (?,?,?,?,?,?)",
                     (account, ccy, opening, closing, date, src))

    def _stmt_txn(self, conn, rid, amt, src, ccy="USD"):
        conn.execute("INSERT INTO transactions (record_id, canonical_key, amount_minor,"
                     " currency, source_file) VALUES (?,?,?,?,?)", (rid, "k-"+rid, amt, ccy, src))

    def test_continuity_gap_red_2(self):
        with db.connect(self.db_path) as conn:
            self._bal(conn, "idfc", 0, 10000, "2026-05-31", "may.xlsx")
            self._bal(conn, "idfc", 9000, 12000, "2026-06-30", "jun.xlsx")  # opening 9000 != prev close 10000
        res = doctor.run(self.db_path)
        c2 = [c for c in res.checks if c.invariant == "#2 continuity"][0]
        self.assertEqual(c2.status, "RED")

    def test_continuity_contiguous_green_2(self):
        with db.connect(self.db_path) as conn:
            self._bal(conn, "idfc", 0, 10000, "2026-05-31", "may.xlsx")
            self._stmt_txn(conn, "m1", 10000, "may.xlsx")
            self._bal(conn, "idfc", 10000, 12000, "2026-06-30", "jun.xlsx")  # opening==prev close
            self._stmt_txn(conn, "j1", 2000, "jun.xlsx")
        c2 = [c for c in doctor.run(self.db_path).checks if c.invariant == "#2 continuity"][0]
        self.assertEqual(c2.status, "GREEN")

    def _bal_w(self, conn, account, opening, closing, date, src, start, end,
               ccy="USD"):
        conn.execute("INSERT INTO account_balances (account,currency,opening_minor,"
                     "closing_minor,statement_date,source_file,period_start,period_end)"
                     " VALUES (?,?,?,?,?,?,?,?)",
                     (account, ccy, opening, closing, date, src, start, end))

    def _bank_row(self, conn, rid, amt, dt, src_file, ccy="USD"):
        conn.execute("INSERT INTO transactions (record_id, canonical_key,"
                     " amount_minor, currency, source, source_file, creation_dt)"
                     " VALUES (?,?,?,?,?,?,?)",
                     (rid, "bank:" + rid, amt, ccy, "IDFC", src_file, dt))

    def test_overlapping_statements_green_1_and_2(self):
        # 2026-07-07 cockpit-upload shape: an old full statement, then a fresh
        # export whose window OVERLAPS it and adds new days. WINDOW attribution
        # keeps #1 green for BOTH statements (the overlap rows belong to the old
        # file by first-import attribution) and #2 consistent at the boundary.
        with db.connect(self.db_path) as conn:
            self._bank_row(conn, "r1", 7300, "2026-06-20", "old.xlsx")   # overlap
            self._bank_row(conn, "r2", 2700, "2026-06-25", "old.xlsx")   # overlap
            self._bank_row(conn, "r3", -1400, "2026-06-28", "new.xlsx")  # new days
            self._bal_w(conn, "idfc", 0, 10000, "2026-06-25", "old.xlsx",
                        "2026-06-01", "2026-06-25")
            # new statement re-covers 06-15.. and runs to 06-30:
            # opening (bal at 06-14) = 0; closing = 7300+2700-1400 = 8600
            self._bal_w(conn, "idfc", 0, 8600, "2026-06-30", "new.xlsx",
                        "2026-06-15", "2026-06-30")
        res = doctor.run(self.db_path)
        c1 = [c for c in res.checks if c.invariant == "#1 statement integrity"][0]
        c2 = [c for c in res.checks if c.invariant == "#2 continuity"][0]
        self.assertEqual(c1.status, "GREEN", c1.deltas)
        self.assertEqual(c2.status, "GREEN", c2.deltas)

    def test_overlap_with_missing_row_still_red(self):
        # window attribution must not WEAKEN the invariant: if the overlap flow
        # doesn't land exactly on the old closing, #2 stays RED.
        with db.connect(self.db_path) as conn:
            self._bank_row(conn, "r1", 7300, "2026-06-20", "old.xlsx")
            self._bal_w(conn, "idfc", 0, 7300, "2026-06-25", "old.xlsx",
                        "2026-06-01", "2026-06-25")
            # new statement claims opening 100 at 06-15 => boundary math breaks
            self._bal_w(conn, "idfc", 100, 7400, "2026-06-30", "new.xlsx",
                        "2026-06-15", "2026-06-30")
        c2 = [c for c in doctor.run(self.db_path).checks
              if c.invariant == "#2 continuity"][0]
        self.assertEqual(c2.status, "RED")

    def test_coverage_gap_red_2(self):
        # a HOLE between statement windows can hide transactions -> RED
        with db.connect(self.db_path) as conn:
            self._bal_w(conn, "idfc", 0, 0, "2026-06-10", "a.xlsx",
                        "2026-06-01", "2026-06-10")
            self._bal_w(conn, "idfc", 0, 0, "2026-06-30", "b.xlsx",
                        "2026-06-20", "2026-06-30")   # starts 9 days late
        c2 = [c for c in doctor.run(self.db_path).checks
              if c.invariant == "#2 continuity"][0]
        self.assertEqual(c2.status, "RED")
        self.assertEqual(c2.deltas[0]["error"], "coverage gap")

    def test_ledger_tieout_mismatch_red_3(self):
        with db.connect(self.db_path) as conn:
            self._bal(conn, "idfc", 0, 10000, "2026-06-30", "jun.xlsx")
            self._stmt_txn(conn, "j1", 7000, "jun.xlsx")     # 0+7000 != 10000 -> RED
        res = doctor.run(self.db_path)
        c3 = [c for c in res.checks if c.invariant == "#3 ledger-statement tie-out"][0]
        self.assertEqual(c3.status, "RED")
        self.assertFalse(res.green)

    def test_ledger_tieout_reproduces_green_3(self):
        with db.connect(self.db_path) as conn:
            self._bal(conn, "idfc", 0, 10000, "2026-06-30", "jun.xlsx")
            self._stmt_txn(conn, "j1", 6000, "jun.xlsx")
            self._stmt_txn(conn, "j2", 4000, "jun.xlsx")     # 0+10000 == 10000
        c3 = [c for c in doctor.run(self.db_path).checks if c.invariant == "#3 ledger-statement tie-out"][0]
        self.assertEqual(c3.status, "GREEN")

    def test_cash_unsourced_red_4(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO account_balances (account,currency,opening_minor,"
                         "closing_minor,statement_date,source_file) VALUES"
                         " ('mystery','USD',0,5000,'2026-06-30','')")  # no source -> unexplained
        res = doctor.run(self.db_path)
        c4 = [c for c in res.checks if c.invariant == "#4 cash"][0]
        self.assertEqual(c4.status, "RED")

    def test_cash_authoritative_fd_green_4(self):
        with db.connect(self.db_path) as conn:
            self._bal(conn, "idfc-fd", 0, 5868784, "2026-06-30", "authoritative:idfc-app-fd", "INR")
        c4 = [c for c in doctor.run(self.db_path).checks if c.invariant == "#4 cash"][0]
        self.assertEqual(c4.status, "GREEN")     # FD principal is an authoritative source

    def test_statement_integrity(self):
        with db.connect(self.db_path) as conn:
            # opening 100.00 + flow (+50.00 −20.00) = closing 130.00
            self._txn(conn, "s1", 5000, "USD", "k1")
            conn.execute("UPDATE transactions SET source_file='wise-2026-06.csv'"
                         " WHERE record_id='s1'")
            self._txn(conn, "s2", -2000, "USD", "k2")
            conn.execute("UPDATE transactions SET source_file='wise-2026-06.csv'"
                         " WHERE record_id='s2'")
            conn.execute(
                "INSERT INTO account_balances (account, currency, opening_minor,"
                " closing_minor, statement_date, source_file) VALUES"
                " ('wise','USD',10000,13000,'2026-06-30','wise-2026-06.csv')")
        res = doctor.run(self.db_path)
        si = [c for c in res.checks if c.invariant == "#1 statement integrity"][0]
        self.assertEqual(si.status, "GREEN", si.detail)

    def test_statement_integrity_mismatch_red(self):
        with db.connect(self.db_path) as conn:
            self._txn(conn, "s1", 5000, "USD", "k1")
            conn.execute("UPDATE transactions SET source_file='wise.csv'"
                         " WHERE record_id='s1'")
            conn.execute(
                "INSERT INTO account_balances (account, currency, opening_minor,"
                " closing_minor, statement_date, source_file) VALUES"
                " ('wise','USD',10000,99999,'2026-06-30','wise.csv')")  # wrong
        res = doctor.run(self.db_path)
        self.assertFalse(res.green)
        si = [c for c in res.checks if c.invariant == "#1 statement integrity"][0]
        self.assertEqual(si.status, "RED")
        self.assertEqual(si.deltas[0]["delta_minor"], 10000 + 5000 - 99999)


# ── §3.8 migration Σ-invariance (the conversion rule, on a temp db) ──────────
class TestMigrationInvariance(DoctorCase):
    def test_real_to_cents_preserves_sum(self):
        vals = ["285.17", "-5357.14", "4703.60", "0.01", "-0.01", "1000000.00"]
        with db.connect(self.db_path) as conn:
            for i, v in enumerate(vals):
                conn.execute("INSERT INTO transactions (record_id, amount,"
                             " currency) VALUES (?,?, 'USD')", (f"r{i}", float(v)))
            # the migration's SQL conversion
            conn.execute("UPDATE transactions SET amount_minor ="
                         " CAST(ROUND(amount*100) AS INTEGER) WHERE amount IS NOT NULL")
            before = conn.execute("SELECT CAST(ROUND(SUM(amount)*100) AS INTEGER)"
                                  " c FROM transactions").fetchone()["c"]
            after = conn.execute("SELECT SUM(amount_minor) c FROM"
                                 " transactions").fetchone()["c"]
            # and the Decimal path agrees to the cent
            dec_sum = sum(money.to_minor(v) for v in vals)
        self.assertEqual(before, after)
        self.assertEqual(after, dec_sum)


# ── Phase 1: Upwork-CSV importer (v2 parser as spec, integer-minor) ──────────
class TestUpworkCsv(unittest.TestCase):
    HEADER = ("Date,Transaction ID,Transaction type,Transaction summary,"
              "Transaction summary details,Description 1,Description 2,"
              "Description 3,Agency team,Freelancer,Client team,Account name,PO,"
              "Ref ID,Amount $,Amount in local currency,Currency,"
              "Current balance $,Payment method")

    def _csv(self, rows):
        from modules.finance.importers import upwork_csv  # noqa
        return self.HEADER + "\n" + "\n".join(rows)

    def test_parse_integer_minor_and_keys(self):
        from modules.finance.importers import upwork_csv
        rows = upwork_csv.parse(self._csv([
            '"May 30, 2026",923117156,Connects,300 Connects,,300 Connects fee,,,'
            ',,,Abhijeet Gandhi,,923117156,-45.00,,,343.32,']))
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["amount_minor"], -4500)       # integer cents, signed
        self.assertEqual(r["balance_minor"], 34332)
        self.assertEqual(r["canonical_key"], "upwork:923117156")
        self.assertEqual(r["date"], "2026-05-30")
        self.assertEqual(r["currency"], "USD")

    def test_blank_amount_is_none_not_fabricated(self):
        from modules.finance.importers import upwork_csv
        rows = upwork_csv.parse(self._csv([
            '"Jun 1, 2026",1,Subscription,Subscription Renewal,,,,,,,,'
            'Abhijeet Gandhi,,930000001,,,,100.00,']))
        self.assertIsNone(rows[0]["amount_minor"])       # never 0/guessed

    def test_running_balance_reconciles(self):
        from modules.finance.importers import upwork_csv
        # chronological: +100.00 -> bal 100.00 ; -45.00 -> bal 55.00
        rows = upwork_csv.parse(self._csv([
            '"Jun 1, 2026",1,Hourly,wk,,,,,,,,A,,111,100.00,,,100.00,',
            '"Jun 2, 2026",2,Connects,300,,,,,,,,A,,222,-45.00,,,55.00,']))
        rec = upwork_csv.reconcile_running_balance(rows)
        self.assertTrue(rec["ok"], rec)

    def test_same_day_settlement_triple_reconciles(self):
        # newest-first (Upwork's native order): an earning + its fee + WHT on the
        # SAME date must reconcile — re-sorting by (date,ref) used to false-break
        from modules.finance.importers import upwork_csv
        rows = upwork_csv.parse(self._csv([
            # newest first: bal after each. start 0; +100 earning -> 100; -10 fee
            # -> 90; -5 wht -> 85 ... listed newest(top)->oldest
            '"Jun 2, 2026",3,WHT,wht,,,,,,,,A,,303,-5.00,,,85.00,',
            '"Jun 2, 2026",2,Service Fee,fee,,,,,,,,A,,302,-10.00,,,90.00,',
            '"Jun 2, 2026",1,Hourly,wk,,,,,,,,A,,301,100.00,,,100.00,',
            '"Jun 1, 2026",0,Hourly,prev,,,,,,,,A,,300,0.00,,,0.00,']))
        rec = upwork_csv.reconcile_running_balance(rows)
        self.assertTrue(rec["ok"], rec)

    def test_running_balance_break_caught(self):
        from modules.finance.importers import upwork_csv
        rows = upwork_csv.parse(self._csv([
            '"Jun 1, 2026",1,Hourly,wk,,,,,,,,A,,111,100.00,,,100.00,',
            '"Jun 2, 2026",2,Connects,300,,,,,,,,A,,222,-45.00,,,99.99,']))  # wrong
        rec = upwork_csv.reconcile_running_balance(rows)
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["breaks"][0]["delta_minor"], 5500 - 9999)

    def test_blank_amount_quarantines_and_breaks_chain(self):
        from modules.finance.importers import upwork_csv
        rows = upwork_csv.parse(self._csv([
            '"Jun 1, 2026",1,Subscription,x,,,,,,,,A,,111,,,,100.00,']))
        rec = upwork_csv.reconcile_running_balance(rows)
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["quarantine"][0]["key"], "111")


# ── Phase 1b: Wise importer (classify income/transfer/fee, integer-minor) ────
class TestWiseCsv(unittest.TestCase):
    HEADER = ('"TransferWise ID",Date,"Date Time",Amount,Currency,Description,'
              '"Payment Reference","Running Balance","Exchange From","Exchange To",'
              '"Exchange Rate","Payer Name","Payee Name","Payee Account Number",'
              'Merchant,"Card Last Four Digits","Card Holder Full Name",Attachment,'
              'Note,"Total fees","Exchange To Amount","Transaction Type",'
              '"Transaction Details Type"')

    def _csv(self, rows):
        return self.HEADER + "\n" + "\n".join(rows)

    def test_classify_income_transfer_fee(self):
        from modules.finance.importers import wise_csv
        rows = wise_csv.parse(self._csv([
            'TRANSFER-1,"30-05-2026",,1615.00,USD,"Received money from CMF Publishing",,1615.00,,,,,,,,,,,,0,,CREDIT,',
            'TRANSFER-2,"30-05-2026",,-1582.23,USD,"Sent money to Etherwise (fee: 32.77)",,0.00,,,94.96500,,,,,,,,,32.77,,DEBIT,',
            'FEE-TRANSFER-2,"30-05-2026",,-32.77,USD,"Wise Charges for: TRANSFER-2",,1582.23,,,,,,,,,,,,0,,DEBIT,']))
        kinds = {r["wise_id"]: r["kind"] for r in rows}
        self.assertEqual(kinds["TRANSFER-1"], "income")
        self.assertEqual(kinds["TRANSFER-2"], "transfer_out")  # has realized fx
        self.assertEqual(kinds["FEE-TRANSFER-2"], "fee")
        t2 = [r for r in rows if r["wise_id"] == "TRANSFER-2"][0]
        self.assertEqual(t2["amount_minor"], -158223)         # integer cents
        self.assertEqual(t2["fx_rate"], "94.96500")            # realized, pinned
        self.assertEqual(t2["canonical_key"], "wise:TRANSFER-2")
        self.assertEqual(t2["date"], "2026-05-30")

    def test_running_balance_reconciles(self):
        from modules.finance.importers import wise_csv
        # newest-first: +1615 -> 1615 ; -32.77 fee -> 1582.23 ; -1582.23 -> 0
        rows = wise_csv.parse(self._csv([
            'T3,"30-05-2026",,-1582.23,USD,"Sent money to Etherwise",,0.00,,,94.9,,,,,,,,,0,,DEBIT,',
            'T2,"30-05-2026",,-32.77,USD,"Wise Charges",,1582.23,,,,,,,,,,,,0,,DEBIT,',
            'T1,"30-05-2026",,1615.00,USD,"Received money from X",,1615.00,,,,,,,,,,,,0,,CREDIT,']))
        self.assertTrue(wise_csv.reconcile_running_balance(rows)["ok"])


# ── Phase 1b: IDFC bank xlsx importer (import-all + classify; nothing dropped) ─
def _make_idfc_xlsx(grid):
    """Build a minimal IDFC-shaped xlsx (only the two members the reader opens:
    sharedStrings.xml + worksheets/sheet1.xml). grid is a list of rows; each row
    a list of cell values. A numeric-looking str -> numeric cell; other str ->
    shared string; None -> omitted cell (tests the position-aware reader)."""
    import io
    import zipfile

    shared, shared_idx = [], {}

    def _col(n):
        s = ""
        n += 1
        while n:
            n, rem = divmod(n - 1, 26)
            s = chr(65 + rem) + s
        return s

    sheet_rows = []
    for ri, row in enumerate(grid, 1):
        cells = []
        for ci, val in enumerate(row):
            if val is None:
                continue
            ref = f"{_col(ci)}{ri}"
            if re.fullmatch(r"-?\d+(\.\d+)?", str(val)):
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            else:
                if val not in shared_idx:
                    shared_idx[val] = len(shared)
                    shared.append(val)
                cells.append(f'<c r="{ref}" t="s"><v>{shared_idx[val]}</v></c>')
        sheet_rows.append(f'<row r="{ri}">' + "".join(cells) + "</row>")

    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    sheet_xml = (f'<?xml version="1.0"?><worksheet {ns}><sheetData>'
                 + "".join(sheet_rows) + "</sheetData></worksheet>")
    ss_xml = (f'<?xml version="1.0"?><sst {ns} count="{len(shared)}">'
              + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", ss_xml)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buf.getvalue()


class TestBankXlsx(unittest.TestCase):
    HDR = ["Transaction Date", "Value Date", "Particulars", "Cheque No",
           "Debit", "Credit", "Balance"]

    # oldest-first chain; opening 100000 implied (first bal - first amount)
    TXNS = [
        ["02-May-2026", "02-May-2026", "NEFT received from CLIENT", "", "", "50000.00", "150000.00"],
        ["03-May-2026", "03-May-2026", "Auto sweep in from 60600000", "", "", "30000.00", "180000.00"],
        ["03-May-2026", "03-May-2026", "Sweepout FD 999 booked", "", "30000.00", "", "150000.00"],
        ["05-May-2026", "05-May-2026", "WISE PAYMENTS INDIA PVT", "", "", "80000.00", "230000.00"],
        ["06-May-2026", "06-May-2026", "NEFT PAYMENT ESCROW UPWORK", "", "", "40000.00", "270000.00"],
        ["10-May-2026", "10-May-2026", "Ecom/123/RazorpaySo/456/", "", "15000.00", "", "255000.00"],
        ["12-May-2026", "12-May-2026", "NEFT Salary may EMP1", "", "25000.00", "", "230000.00"],
        ["15-May-2026", "15-May-2026", "ATM CASH WITHDRAWAL", "", "5000.00", "", "225000.00"],
        ["18-May-2026", "18-May-2026", "IMPS to ABHIJEET ASHOK GANDHI", "", "12000.00", "", "213000.00"],
        ["20-May-2026", "20-May-2026", "Google Workspace subscription", "", "8000.00", "", "205000.00"],
    ]

    def _xlsx(self):
        grid = [["ACCOUNT NUMBER", "60606200234"],
                ["CUSTOMER NAME", "ETHERWISE"],
                ["STATEMENT PERIOD", "01-May-2026 TO 31-May-2026"]]
        grid += [[]] * 10
        grid.append(self.HDR)
        grid += self.TXNS
        grid.append(["Total", "95000.00", "200000.00"])
        return _make_idfc_xlsx(grid)

    def test_imports_all_rows_none_dropped(self):
        from modules.finance.importers import bank_xlsx
        out = bank_xlsx.parse(self._xlsx())
        self.assertEqual(len(out["rows"]), len(self.TXNS))   # nothing dropped
        self.assertEqual(out["meta"]["account_number"], "60606200234")
        self.assertEqual(out["meta"]["period_start"], "2026-05-01")

    def test_classification_buckets(self):
        from modules.finance.importers import bank_xlsx
        rows = bank_xlsx.parse(self._xlsx())["rows"]
        cat = {r["description"]: r["category"] for r in rows}
        self.assertEqual(cat["NEFT received from CLIENT"], "income")
        self.assertEqual(cat["Auto sweep in from 60600000"], "internal_sweep")
        self.assertEqual(cat["Sweepout FD 999 booked"], "internal_sweep")
        self.assertEqual(cat["WISE PAYMENTS INDIA PVT"], "transfer_in")
        self.assertEqual(cat["NEFT PAYMENT ESCROW UPWORK"], "transfer_in")
        self.assertEqual(cat["Ecom/123/RazorpaySo/456/"], "cc_settlement")
        self.assertEqual(cat["NEFT Salary may EMP1"], "salary")
        self.assertEqual(cat["ATM CASH WITHDRAWAL"], "owner_draw")
        self.assertEqual(cat["IMPS to ABHIJEET ASHOK GANDHI"], "owner_draw")
        self.assertEqual(cat["Google Workspace subscription"], "expense")

    def test_upwork_disbursement_credits_are_transfer_in(self):
        # Upwork pays out under three descriptions, all the INR leg of a withdrawal
        # -> transfer_in (excluded from P&L). Missing 'Upwork Escrow Inc' here had
        # mis-booked 25 of them as income (a revenue double-count).
        from modules.finance.importers import bank_xlsx
        for d in ("NEFT/INDBH05122618504/Upwork Escrow Inc/INDB00000",
                  "NEFT/CITIN26664234140/WISE PAYMENTS IPL PA CB EXP ESCROW/CITI",
                  "NEFT/INDBH27057031069/PAYMENT ESCROW INC/INDB0000006"):
            self.assertEqual(bank_xlsx.classify_category(d, "credit"), "transfer_in", d)
        # a genuine direct-client NEFT stays income, not a transfer
        self.assertEqual(bank_xlsx.classify_category(
            "NEFT/002651944142/DYNATECH ENGINEER", "credit"), "income")

    def test_rda_inward_remittance_is_income_rail_based(self):
        # RAIL-based, not name-based: inward RDA-Vostro remittances are CLIENT
        # INCOME even when the processor is Wise — Visa-RDA and Wise-RDA both income;
        # only the NEFT/ESCROW rail is a transfer. (Abhijeet 2026-06-26.)
        from modules.finance.importers import bank_xlsx
        for d in ("IMPS/522522564207/WISEPAYMENTSLIM/HDFC0000240/8831/RDAVostro",
                  "IMPS/610322543022/VISAPAYMENTSLIM/HDFC0000240/RDAVostro"):
            self.assertEqual(bank_xlsx.classify_category(d, "credit"), "income", d)
        # the ESCROW rail (own Wise transfer) stays transfer_in
        self.assertEqual(bank_xlsx.classify_category(
            "NEFT/CITIN26664234140/WISE PAYMENTS IPL PA CB EXP ESCROW/CITI", "credit"),
            "transfer_in")

    def test_a_stripe_settlement_landing_is_not_client_income(self):
        """2026-09-05. Citi labels the inward wire 'GLOBAL REMITTANCE-OTHERS'
        with no remitter name, so nothing in the text says Stripe and the row
        read as INCOME. It is the INR landing of money the Stripe rail has
        already booked as revenue: five for five, to the paisa, 1-3 days after
        the Stripe transfer-out leg. Rs 1,21,463.71 of Aug/Sep Stripe revenue
        would have been counted twice; the two July rows had been corrected by
        hand at import, which is how the gap stayed invisible."""
        from modules.finance.importers import bank_xlsx
        self.assertEqual(bank_xlsx.classify_category(
            "NEFT/CITIN26709851278/GLOBAL REMITTANCE-OTHERS/CITI0100000/"
            "388 GREENWICH ST.NEW YORK, NYUSA 10013", "credit"), "transfer_in")
        # a DEBIT is never a landing, whatever the label says
        self.assertEqual(bank_xlsx.classify_category(
            "NEFT/CITIN1/GLOBAL REMITTANCE-OTHERS/CITI0100000/x", "debit"), "expense")

    def test_manual_fd_creation_is_internal_sweep_not_expense(self):
        # 'FD <acct> ETHERWISE' moves cash into a fixed deposit -> internal_sweep
        # (NOT opex); missing this mis-booked -Rs 5L as expense + understated cash.
        from modules.finance.importers import bank_xlsx
        self.assertEqual(bank_xlsx.classify_category("FD 10241213363 ETHERWISE", "debit"),
                         "internal_sweep")
        # genuine expenses that merely contain 'FD'-ish text stay expense
        self.assertEqual(bank_xlsx.classify_category(
            "UPI/DR/609634230935/SHINDE A/FDRL/9763830/UPI", "debit"), "expense")

    def test_signed_integer_paise_and_balances(self):
        from modules.finance.importers import bank_xlsx
        out = bank_xlsx.parse(self._xlsx())
        rows = {r["description"]: r for r in out["rows"]}
        self.assertEqual(rows["NEFT received from CLIENT"]["amount_minor"], 5000000)
        self.assertEqual(rows["Google Workspace subscription"]["amount_minor"], -800000)
        self.assertEqual(rows["NEFT received from CLIENT"]["currency"], "INR")
        self.assertEqual(out["meta"]["opening_balance_minor"], 10000000)
        self.assertEqual(out["meta"]["closing_balance_minor"], 20500000)

    def test_running_balance_reconciles(self):
        from modules.finance.importers import bank_xlsx
        rows = bank_xlsx.parse(self._xlsx())["rows"]
        self.assertTrue(bank_xlsx.reconcile_running_balance(rows)["ok"])

    def test_tampered_balance_breaks(self):
        from modules.finance.importers import bank_xlsx
        txns = [r[:] for r in self.TXNS]
        txns[4][6] = "271000.00"        # corrupt one Balance
        grid = [["ACCOUNT NUMBER", "60606200234"]] + [[]] * 12 + [self.HDR] + txns
        rows = bank_xlsx.parse(_make_idfc_xlsx(grid))["rows"]
        self.assertFalse(bank_xlsx.reconcile_running_balance(rows)["ok"])

    def test_canonical_key_matches_legacy_hash(self):
        import hashlib
        from modules.finance.importers import bank_xlsx
        rows = {r["description"]: r for r in bank_xlsx.parse(self._xlsx())["rows"]}
        # legacy v2 format: md5(date|signed-amount-2dp|DESC.upper())[:16]
        norm = "2026-05-02|50000.00|NEFT RECEIVED FROM CLIENT"
        expect = "bank:" + hashlib.md5(norm.encode()).hexdigest()[:16]
        self.assertEqual(rows["NEFT received from CLIENT"]["canonical_key"], expect)

    def test_unparseable_amount_quarantines_not_drops(self):
        from modules.finance.importers import bank_xlsx
        grid = [["ACCOUNT NUMBER", "60606200234"]] + [[]] * 12 + [self.HDR]
        grid.append(["07-May-2026", "07-May-2026", "garbled row", "", "", "NaN-amt", "9999.00"])
        out = bank_xlsx.parse(_make_idfc_xlsx(grid))
        self.assertEqual(len(out["quarantine"]), 1)          # surfaced
        self.assertEqual(len(out["rows"]), 1)                # present, not dropped
        self.assertIsNone(out["rows"][0]["amount_minor"])    # never fabricated

    def test_position_aware_reader_no_column_shift(self):
        # a debit-only row that OMITS the Credit cell (col F) must not slide
        # Balance (col G) left into the Credit slot.
        from modules.finance.importers import bank_xlsx
        grid = [["ACCOUNT NUMBER", "60606200234"]] + [[]] * 12 + [self.HDR]
        grid.append(["09-May-2026", "09-May-2026", "Rent debit", "", "11000.00", None, "44000.00"])
        rows = bank_xlsx.parse(_make_idfc_xlsx(grid))["rows"]
        self.assertEqual(rows[0]["amount_minor"], -1100000)  # debit, not +balance
        self.assertEqual(rows[0]["balance_minor"], 4400000)


# ── Phase 1b: FX pinning (§3.10) — injectable fetch, Decimal only ────────────
class TestSalaryLines(unittest.TestCase):
    """A salary the narration forgot to call one.

    Abhijeet, 2026-09-07: "There's something wrong yogita must have got paid on
    july". She was — Rs 48,800 + Rs 1,000 on 31 July, both lines stopping at the
    IFSC. No "Salary" in the text, so Rs 49,800 of payroll became an owner
    draw."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / "t.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _row(self, conn, rid, minor, day, desc, category="expense"):
        conn.execute(
            "INSERT INTO transactions (record_id, canonical_key, type, source, category,"
            " pnl_bucket, amount_minor, currency, description, creation_dt)"
            " VALUES (?,?,'Other','IDFC',?,'owner_draw',?,'INR',?,?)",
            (rid, "bank:" + rid, category, minor, desc, day + "T00:00:00+0000"))

    def test_the_payee_is_the_third_field(self):
        from modules.finance import salary_lines as sl
        self.assertEqual(sl.payee("NEFT/IDFB6212M5765601/YOGITA SUDHIR SOMA/BKID0000098"),
                         "YOGITA SUDHIR SOMA")
        self.assertEqual(sl.payee("NEFT/x/Mr PRASHANT JAWAHARLAL BHANDARI/MAHB/Salary"),
                         "PRASHANT JAWAHARLAL BHANDARI")
        # a shape it does not understand is a refusal, not a match on everything
        self.assertEqual(sl.payee("POS-VISA/CHESS.COM"), "")
        self.assertEqual(sl.payee(None), "")

    def test_one_narrated_salary_makes_a_payee_payroll_for_ever(self):
        from modules.finance import salary_lines as sl
        with db.connect(self.db_path) as conn:
            self._row(conn, "jun", -4980000, "2026-06-30",
                      "NEFT/a/YOGITA SUDHIR SOMA/BKID0000098/Salary june", "salary")
            self._row(conn, "jul1", -4880000, "2026-07-31",
                      "NEFT/b/YOGITA SUDHIR SOMA/BKID0000098")
            self._row(conn, "jul2", -100000, "2026-07-31",
                      "NEFT/c/YOGITA SUDHIR SOMA/BKID0000098")
            self.assertEqual(sl.payroll_payees(conn), {"YOGITA SUDHIR SOMA"})
            self.assertEqual(sorted(r["record_id"] for r in sl.rows_to_fix(conn)),
                             ["jul1", "jul2"])
            self.assertEqual(sl.apply(conn)["changed"], 2)
            got = [r[0] for r in conn.execute(
                "SELECT category FROM transactions WHERE record_id IN ('jul1','jul2')")]
            self.assertEqual(got, ["salary", "salary"])
            self.assertEqual(sl.rows_to_fix(conn), [])          # idempotent

    def test_a_line_that_says_what_it_is_is_left_alone(self):
        """His call: "read the line when it says something". Rs 1,25,000 to
        Ranjana reading "Cash withdraw" is a draw, and says so."""
        from modules.finance import salary_lines as sl
        with db.connect(self.db_path) as conn:
            self._row(conn, "sal", -1980000, "2026-08-02",
                      "NEFT/a/RANJANA ASHOK GANDHI/BARB0KOTHRU/Salary july", "salary")
            self._row(conn, "cash", -12500000, "2026-08-19",
                      "NEFT/b/RANJANA ASHOK GANDHI/BARB0KOTHRU/Cash withdraw")
            self.assertEqual(sl.rows_to_fix(conn), [])

    def test_a_stranger_is_never_swept_in(self):
        from modules.finance import salary_lines as sl
        with db.connect(self.db_path) as conn:
            self._row(conn, "sal", -4980000, "2026-06-30",
                      "NEFT/a/YOGITA SUDHIR SOMA/BKID/Salary june", "salary")
            self._row(conn, "other", -29183900, "2026-02-28",
                      "NEFT/b/TRUPTI ASHOK GANDHI/SBIN0007484")
            self.assertEqual(sl.rows_to_fix(conn), [])

    def test_a_credit_from_an_employee_is_not_a_salary(self):
        from modules.finance import salary_lines as sl
        with db.connect(self.db_path) as conn:
            self._row(conn, "sal", -4980000, "2026-06-30",
                      "NEFT/a/YOGITA SUDHIR SOMA/BKID/Salary june", "salary")
            self._row(conn, "back", 4980000, "2026-07-05",
                      "NEFT/b/YOGITA SUDHIR SOMA/BKID")
            self.assertEqual(sl.rows_to_fix(conn), [])

    def test_the_2026_floor_holds(self):
        from modules.finance import salary_lines as sl
        with db.connect(self.db_path) as conn:
            self._row(conn, "sal", -8000000, "2025-10-31",
                      "NEFT/a/ALPHONS JAIMON/ICIC0000004/Salary october", "salary")
            self._row(conn, "nov", -8000000, "2025-11-30",
                      "NEFT/b/ALPHONS JAIMON/ICIC0000004")
            self.assertEqual(sl.rows_to_fix(conn), [])          # his scope: 2026 only
            self.assertEqual(len(sl.rows_to_fix(conn, since=None)), 1)

    def test_a_draw_he_named_outranks_the_repair(self):
        """Prashant and Avantika are personal counterparties by his word, and
        pnl.bucket checks that BEFORE the salary category — so marking their
        rows salary cannot drag them back into payroll."""
        from modules.finance import pnl
        row = {"source": "IDFC", "category": "salary", "amount_base_minor": -73000,
               "description": "NEFT/x/Mr PRASHANT JAWAHARLAL BHANDARI/MAHB/Salary august"}
        self.assertEqual(pnl.bucket(row), "owner_draw")


class TestAccrual(unittest.TestCase):
    """The month a bank row belongs to.

    Abhijeet, 2026-09-07: "I know I sent some salries in september but that
    really is of august. I know we discussed this in the past I don't know why
    this mistake was made." He had discussed it — migration 016, 2026-08-03,
    quotes him. Two rows were set by hand that day and no rule was ever built."""

    def test_the_line_names_the_month_and_the_year_follows_the_payment(self):
        from modules.finance import accrual as a
        self.assertEqual(a.month_named("Salary august", "2026-09-03"), "2026-08")
        self.assertEqual(a.month_named("Rent april", "2026-05-03"), "2026-04")
        # a December named in January is LAST year's; January named in December
        # is next year's — six months is the fold
        self.assertEqual(a.month_named("Salary december", "2026-01-01"), "2025-12")
        self.assertEqual(a.month_named("Salary january", "2026-12-28"), "2027-01")
        self.assertIsNone(a.month_named("Creditcardpaym", "2026-08-26"))

    def test_two_months_in_one_line_is_not_a_reading(self):
        """"TDS may and june", Rs 60,985 paid 7 July, covers both. Taking May
        because May is written first would be a guess dressed as a reading."""
        from modules.finance import accrual as a
        self.assertIsNone(a.month_named("TDS may and june", "2026-07-07"))
        # the same month twice is still one month
        self.assertEqual(a.month_named("Earnings for Aug 10-Aug 16", "2026-09-02"),
                         "2026-08")

    def test_an_on_time_payment_stays_null(self):
        """pnl_month means 'this row is NOT where its date puts it'. Filling it
        in on every on-time payment would bury the exceptions."""
        from modules.finance import accrual as a
        row = {"source": "IDFC", "pnl_bucket": "payroll", "creation_dt": "2026-08-31",
               "description": "NEFT/x/YOGITA SUDHIR SOMA/BKID/Salary august"}
        self.assertIsNone(a.effective_month(row))
        row["creation_dt"] = "2026-09-03"
        self.assertEqual(a.effective_month(row), "2026-08")

    def test_only_bank_rows_and_only_pnl_buckets(self):
        """The Upwork/Wise/Stripe rails carry their own dates and their own
        unsettled re-dating question; an Upwork line reading 'Earnings for
        Aug 10-Aug 16' must not be re-dated by a rule aimed at payroll. Sweeps
        and transfers are out of the P&L, so a month on them says nothing."""
        from modules.finance import accrual as a
        base = {"pnl_bucket": "revenue", "creation_dt": "2026-09-02",
                "description": "Earnings for Aug 10-Aug 16"}
        self.assertIsNone(a.effective_month(dict(base, source="Upwork-MCP")))
        self.assertEqual(a.effective_month(dict(base, source="IDFC")), "2026-08")
        self.assertIsNone(a.effective_month(
            {"source": "IDFC", "pnl_bucket": "sweep", "creation_dt": "2026-09-02",
             "description": "Auto sweep in from 1029/ Salary august"}))

    def test_a_hand_set_month_on_an_unreadable_line_survives(self):
        """Silence from the reader is not evidence against a fact somebody
        entered. The first cut of this cleared them, and the cockpit's own
        August test for the READER side of this feature caught it."""
        import tempfile as _t, shutil as _s
        from pathlib import Path as _P
        from modules.finance import accrual as a
        tmp = _t.mkdtemp()
        try:
            p = _P(tmp) / "t.db"
            db.migrate(p)
            with db.connect(p) as conn:
                conn.execute(
                    "INSERT INTO transactions (record_id, canonical_key, type, source,"
                    " category, pnl_bucket, amount_minor, currency, description,"
                    " creation_dt, pnl_month) VALUES ('late','bank:late','Salary','IDFC',"
                    " 'salary','payroll',-21600000,'INR','NEFT/x/JAY/y',"
                    " '2026-07-02T00:00:00+0000','2026-06')")
                self.assertEqual(a.rows_to_set(conn), [])
                a.apply(conn)
                kept = conn.execute("SELECT pnl_month FROM transactions"
                                    " WHERE record_id='late'").fetchone()[0]
            self.assertEqual(kept, "2026-06")
        finally:
            _s.rmtree(tmp, ignore_errors=True)

    def test_an_unreadable_line_takes_his_answer_not_a_guess(self):
        from modules.finance import accrual as a
        row = {"source": "IDFC", "pnl_bucket": "payroll", "creation_dt": "2026-08-19",
               "canonical_key": "bank:1df7ad18f0be4511",
               "description": "NEFT/x/RANJANA ASHOK GANDHI/BARB0KOTHRU/Salary raise"}
        self.assertEqual(a.effective_month(row), "2026-07")   # "a July top-up"
        row["canonical_key"] = "bank:someone-else"
        self.assertIsNone(a.effective_month(row))             # never inferred


class TestPayrollTax(unittest.TestCase):
    """The TDS withheld from salaries — payroll cost the bank never shows.

    Abhijeet, 2026-09-07: "Also even if I am sending less salary you need to
    include tds that we share as well?" Every payroll figure here was the NET
    transfer; the withheld tax sat in owner draws, so a month of employing
    people looked cheaper than it was by the whole of the TDS."""

    # the real lines, verbatim from the IDFC statements
    APRIL = "IFT/10171724864/PRITI BORA & CO /13337732/TDS for april"
    MAYJUN = "IFT/10171724864/PRITI BORA & CO /26482502/TDS may and june"
    JULY = "IFT/10171724864/PRITI BORA & CO /27883768/TDS July"
    AUGUST = "IFT/10171724864/PRITI BORA & CO /23388539/TDS august"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / "t.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _remit(self, conn, key, minor, day, desc, rate="0.01053"):
        conn.execute(
            "INSERT INTO transactions (record_id, canonical_key, type, source,"
            " category, pnl_bucket, amount_minor, amount_base_minor, currency,"
            " fx_rate, fx_date, description, creation_dt) VALUES"
            " (?,?,'Other','IDFC','expense','payroll',?,?,'INR',?,?,?,?)",
            (key, key, minor, money.apply_rate(minor, rate), rate, day, desc,
             day + "T00:00:00+0000"))

    def _remit_all(self, conn):
        self._remit(conn, "bank:553f3afc6c497e3e", -2808000, "2026-05-06",
                    self.APRIL, "0.01057")
        self._remit(conn, "bank:04607cb1178e7003", -6098500, "2026-07-07",
                    self.MAYJUN, "0.01053")
        self._remit(conn, "bank:1b70a0304700d50b", -3055500, "2026-08-06",
                    self.JULY, "0.0105")
        self._remit(conn, "bank:6435ebd60cc8b361", -4300000, "2026-09-07",
                    self.AUGUST, "0.01127")

    # ── the booking rule ─────────────────────────────────────────────────────

    def test_tds_to_the_ca_is_payroll_and_the_ca_s_other_bills_are_not(self):
        """Narrow on purpose: his CA's name AND the word TDS. Other payments go
        to the same CA and are not payroll — the quarterly return-filing fee,
        'Tax 2026' (his own FY25-26 liability) and advance tax."""
        from modules.finance import pnl
        for line in (self.APRIL, self.MAYJUN, self.JULY, self.AUGUST):
            self.assertEqual(pnl._bank_spend({"description": line}), "payroll", line)
        for line in ("IFT/10171724864/PRITI BORA & CO /15151515/TDS return filing Q2",
                     "IFT/10171724864/PRITI BORA & CO /35546417/Tax 2026",
                     "IFT/10171724864/PRITI BORA & CO /26822883/Advance tax",
                     "IFT/10171724864/PRITI BORA & CO /19225121/Advance tax and pt"
                     " payments"):
            self.assertFalse(pnl.is_tds_remittance(line), line)
            self.assertEqual(pnl._bank_spend({"description": line}), "owner_draw", line)
        # The Q1 filing fee itself: still never payroll, and a business cost
        # since he said so of that one payment (2026-10-06). The Q2 line above
        # is the proof it is the PAYMENT that was confirmed and not the wording.
        q1 = "IFT/10171724864/PRITI BORA & CO /14039720/TDS return filing Q1"
        self.assertFalse(pnl.is_tds_remittance(q1))
        self.assertEqual(pnl._bank_spend({"description": q1}), "opex")

    def test_a_sweep_line_that_mentions_tds_is_not_a_remittance(self):
        """Every FD sweep-in carries 'Gross Interest:14/TDS:1' in its text, and
        one reads 'Principal:60985/ TDS may and june'. They are the FD side of
        the same payment, already excluded from the P&L as sweeps — matching on
        the word alone would have booked the money twice."""
        from modules.finance import pnl
        self.assertFalse(pnl.is_tds_remittance(
            "Auto sweep in from 10291859328/Principal:60985/ TDS may and june"))
        self.assertEqual(pnl.bucket(
            {"source": "IDFC", "category": "internal_sweep", "amount_base_minor": 6098500,
             "description": "Auto sweep in from 10291859328/Principal:60985/"
                            " TDS may and june"}), "sweep")

    # ── his figures vs the bank ──────────────────────────────────────────────

    def test_his_months_add_up_to_what_left_the_account(self):
        """His design: "Ask me tds amount each month I will give you. then you
        can match it against the deduct from bank account." April Rs 28,080,
        May+June Rs 60,985, July Rs 30,555, August Rs 43,000."""
        from modules.finance import payroll_tax as pt
        with db.connect(self.db_path) as conn:
            self._remit_all(conn)
            self.assertEqual(pt.mismatches(conn), [])
            self.assertEqual(sum(pt.MONTHLY_TDS.values()),
                             sum(pt.remitted(conn).values()))

    def test_a_month_he_got_wrong_says_so_instead_of_absorbing_it(self):
        """If May + June is not Rs 60,985 then something else is inside that
        payment, and the payroll figure for both months is wrong."""
        from modules.finance import payroll_tax as pt
        with db.connect(self.db_path) as conn:
            self._remit_all(conn)
            conn.execute("UPDATE transactions SET amount_minor=-6200000"
                         " WHERE canonical_key='bank:04607cb1178e7003'")
            bad = pt.mismatches(conn)
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["delta_minor"], 6200000 - 6098500)

    def test_a_missing_remittance_row_is_reported_not_treated_as_zero(self):
        from modules.finance import payroll_tax as pt
        with db.connect(self.db_path) as conn:
            bad = pt.mismatches(conn)
        self.assertEqual(len(bad), len(pt.REMITTANCES))
        self.assertEqual(len(bad), 4)                # Apr, May+Jun, Jul, Aug
        self.assertTrue(all("not in the ledger" in b["error"] for b in bad))

    def test_a_remittance_he_has_not_given_a_month_for_is_named(self):
        """The half that makes it self-running. "august you will see in the next
        statement" — when that statement lands, the ledger must ask him for the
        figure, not book a month's payroll on the day it cleared. It landed on
        7 Sep and he gave the month; the next one it has to ask about is
        September's, in October's statement."""
        from modules.finance import payroll_tax as pt
        with db.connect(self.db_path) as conn:
            self._remit_all(conn)
            self.assertEqual(pt.uncovered(conn), [])
            self._remit(conn, "bank:october-statement-tds", -3100000, "2026-10-07",
                        "IFT/10171724864/PRITI BORA & CO /99999999/TDS September",
                        "0.0105")
            loose = pt.uncovered(conn)
        self.assertEqual([r["canonical_key"] for r in loose],
                         ["bank:october-statement-tds"])

    def test_august_is_one_month_and_needs_neither_a_pin_nor_a_split(self):
        """Rs 43,000 on 7 Sep reads "TDS august". One month, named on the line:
        `accrual` reads it, so there is no pin (an override that agrees with the
        reading later looks like a decision nobody made) and nothing to split."""
        from modules.finance import payroll_tax as pt, accrual
        self.assertEqual(pt.declared_minor("2026-08"), 4300000)
        self.assertEqual(pt.REMITTANCES["bank:6435ebd60cc8b361"], ("2026-08",))
        self.assertNotIn("bank:6435ebd60cc8b361", pt.accrual_pins())
        self.assertEqual(accrual.month_named(self.AUGUST, "2026-09-07"), "2026-08")
        with db.connect(self.db_path) as conn:
            self._remit_all(conn)
            pt.apply(conn)
            keys = [r[0] for r in conn.execute(
                "SELECT canonical_key FROM transactions WHERE source='Direct'")]
        self.assertTrue(all("04607cb1178e7003" in k for k in keys), keys)
        self.assertEqual(len(keys), 2)               # only May/June's pair

    def test_the_march_advance_tax_is_before_the_regime_and_is_not_scanned(self):
        """"we started giving tds from apr that is this year" — January to March
        carry none, and Rs 1,16,000 of 9 March is advance tax, not payroll."""
        from modules.finance import payroll_tax as pt
        self.assertEqual(pt.SINCE, "2026-04-01")
        with db.connect(self.db_path) as conn:
            self._remit(conn, "bank:899f5c4ed0ac91fb", -11600000, "2026-03-09",
                        "IFT/10171724864/PRITI BORA & CO /19225121/Advance tax and"
                        " pt payments", "0.0116")
            self.assertEqual(pt.uncovered(conn), [])

    # ── the split ────────────────────────────────────────────────────────────

    def test_the_split_carries_june_out_of_may_and_nets_to_zero(self):
        """One bank row cannot sit in two months. The remittance lands whole in
        May (pinned by accrual) and these carry June's share across — in paise
        AND in the USD presentation, at the remittance's own pinned rate."""
        from modules.finance import payroll_tax as pt
        with db.connect(self.db_path) as conn:
            self._remit_all(conn)
            res = pt.apply(conn)
            rows = [dict(r) for r in conn.execute(
                "SELECT pnl_month, pnl_bucket, category, amount_minor,"
                " amount_base_minor, fx_rate FROM transactions WHERE source='Direct'"
                " ORDER BY pnl_month")]
        self.assertEqual(res["written"], 2)
        self.assertEqual([r["pnl_month"] for r in rows], ["2026-05", "2026-06"])
        self.assertEqual([r["amount_minor"] for r in rows], [3290500, -3290500])
        self.assertEqual(sum(r["amount_minor"] for r in rows), 0)
        self.assertEqual(sum(r["amount_base_minor"] for r in rows), 0)
        self.assertTrue(all(r["pnl_bucket"] == "payroll" for r in rows))
        self.assertTrue(all(r["fx_rate"] == "0.01053" for r in rows))

    def test_the_split_rows_bucket_back_to_payroll_so_10_stays_reproducible(self):
        """They are written with pnl_bucket already set; bucket() must agree, or
        the reproducibility check would go RED the moment they are inserted."""
        from modules.finance import payroll_tax as pt, pnl
        with db.connect(self.db_path) as conn:
            self._remit_all(conn)
            pt.apply(conn)
            rows = [dict(r) for r in conn.execute(
                "SELECT source, type, category, description, pnl_bucket,"
                " amount_base_minor FROM transactions WHERE source='Direct'")]
        for r in rows:
            self.assertEqual(pnl.bucket(r), r["pnl_bucket"])

    def test_no_split_without_the_payment_behind_it(self):
        """An adjustment to a payment that does not exist is a number with
        nothing behind it."""
        from modules.finance import payroll_tax as pt
        with db.connect(self.db_path) as conn:
            self.assertEqual(pt.rows_to_write(conn), [])
            self.assertEqual(pt.apply(conn)["written"], 0)

    def test_no_split_when_the_remittance_carries_no_pinned_rate(self):
        """The USD legs only net to zero when both use the remittance's own
        rate; pricing them at some other day's would leave cents in the P&L."""
        from modules.finance import payroll_tax as pt
        with db.connect(self.db_path) as conn:
            self._remit_all(conn)
            conn.execute("UPDATE transactions SET fx_rate=NULL"
                         " WHERE canonical_key='bank:04607cb1178e7003'")
            self.assertEqual(pt.rows_to_write(conn), [])

    def test_applying_twice_writes_nothing_new(self):
        from modules.finance import payroll_tax as pt
        with db.connect(self.db_path) as conn:
            self._remit_all(conn)
            pt.apply(conn)
            self.assertEqual(pt.apply(conn)["written"], 0)
            n = conn.execute("SELECT COUNT(*) FROM transactions"
                             " WHERE source='Direct'").fetchone()[0]
        self.assertEqual(n, 2)

    def test_the_pin_and_the_split_cannot_disagree(self):
        """The month the bank row is attributed to and the month the split
        carries out of it are ONE decision. Two copies of a decision drift, so
        accrual merges the pin from here rather than re-typing it."""
        from modules.finance import payroll_tax as pt, accrual
        for key, months in pt.REMITTANCES.items():
            if len(months) > 1:
                self.assertEqual(accrual.EXPLICIT[key], months[0])

    def test_the_split_rows_are_not_bank_rows_and_cannot_move_the_tie_out(self):
        """#3 sums ledger rows by the statement's source_file. An adjustment
        that claimed a statement file would break the reconcile it depends on."""
        from modules.finance import payroll_tax as pt
        with db.connect(self.db_path) as conn:
            self._remit_all(conn)
            pt.apply(conn)
            files = {r[0] for r in conn.execute(
                "SELECT DISTINCT source_file FROM transactions WHERE source='Direct'")}
        self.assertEqual(files, {"derived:tds-split"})


class TestDeposits(unittest.TestCase):
    """The fixed-deposit principal — the biggest number in 'in the bank'.

    Abhijeet, 2026-09-05: "cash in the bank doesn't seems updated". IDFC's
    auto-sweep pins savings near Rs 5,00,000, so the deposits ARE the cash
    position, and the figure was hand-entered on 26 June and had not moved in
    71 days. It is now rolled forward through the sweep legs."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / "t.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _bal(self, conn, minor, day, source):
        conn.execute("INSERT INTO account_balances (account, currency, opening_minor,"
                     " closing_minor, statement_date, source_file)"
                     " VALUES ('idfc-fd','INR',0,?,?,?)", (minor, day, source))

    def _sweep(self, conn, rid, minor, day, desc):
        conn.execute("INSERT INTO transactions (record_id, canonical_key, type, source,"
                     " category, amount_minor, currency, description, creation_dt)"
                     " VALUES (?,?,'Other','IDFC','internal_sweep',?,'INR',?,?)",
                     (rid, "b-" + rid, minor, desc, day + "T00:00:00+0000"))

    def test_no_anchor_is_none_and_never_zero(self):
        """Without a figure from him there is nothing to say. Zero would present
        an invented cash position as fact."""
        from modules.finance import deposits
        with db.connect(self.db_path) as conn:
            self._sweep(conn, "s1", -800000, "2026-07-04", "Sweepout FD 1029 booked")
            self.assertIsNone(deposits.rolled(conn))

    def test_the_anchor_rolls_forward_through_the_sweep_legs(self):
        from modules.finance import deposits
        with db.connect(self.db_path) as conn:
            self._bal(conn, 586878353, "2026-06-26", "authoritative:idfc-app-fd:2026-06-26")
            self._sweep(conn, "s1", -800000, "2026-07-04", "Sweepout FD 10297298229 booked")
            self._sweep(conn, "s2", 304800, "2026-07-05",
                        "Auto sweep in from 10297298229/Principal:3048/ TDS return filing")
            roll = deposits.rolled(conn, as_of="2026-09-05")
            self.assertEqual(roll["into_minor"], 800000)
            self.assertEqual(roll["out_minor"], 304800)
            self.assertEqual(roll["balance_minor"], 586878353 + 495200)
            self.assertEqual((roll["anchor_date"], roll["as_of"]),
                             ("2026-06-26", "2026-09-05"))

    def test_interest_does_not_shrink_the_deposit_that_paid_it(self):
        """A sweep-in credits principal PLUS net interest ('Principal:21917/
        Gross Interest:14/TDS:1' arrives as Rs 21,930). Subtracting the CREDITED
        amount would quietly erode the FD by its own interest every quarter."""
        from modules.finance import deposits
        with db.connect(self.db_path) as conn:
            self._bal(conn, 10000000, "2026-06-26", "authoritative:idfc-app-fd:2026-06-26")
            self._sweep(conn, "s1", 2193000, "2026-08-06",
                        "Auto sweep in from 10296227258/Principal:21917/Gross Interest:14/TDS:1/")
            roll = deposits.rolled(conn)
            self.assertEqual(roll["out_minor"], 2191700)      # the principal, not 2193000
            self.assertEqual(roll["interest_minor"], 1300)
            self.assertEqual(roll["balance_minor"], 10000000 - 2191700)

    def test_a_sweep_in_with_no_principal_stated_is_taken_whole_and_counted(self):
        from modules.finance import deposits
        with db.connect(self.db_path) as conn:
            self._bal(conn, 10000000, "2026-06-26", "authoritative:idfc-app-fd:2026-06-26")
            self._sweep(conn, "s1", 500000, "2026-08-06", "Auto sweep in from 102962 (no label)")
            roll = deposits.rolled(conn)
            self.assertEqual((roll["out_minor"], roll["unparsed"]), (500000, 1))

    def test_a_derived_row_never_anchors_the_next_roll(self):
        """Otherwise the estimate compounds on itself and drifts away from the
        last fact he actually supplied, with no way back."""
        from modules.finance import deposits
        with db.connect(self.db_path) as conn:
            self._bal(conn, 10000000, "2026-06-26", "authoritative:idfc-app-fd:2026-06-26")
            self._bal(conn, 99999999, "2026-09-05", "derived:fd-rolled-from-authoritative:2026-06-26")
            self._sweep(conn, "s1", -400000, "2026-07-04", "Sweepout FD 1 booked")
            roll = deposits.rolled(conn)
            self.assertEqual(roll["anchor_minor"], 10000000)
            self.assertEqual(roll["balance_minor"], 10400000)

    def test_the_anchor_days_own_legs_are_already_in_his_figure(self):
        from modules.finance import deposits
        with db.connect(self.db_path) as conn:
            self._bal(conn, 10000000, "2026-06-26", "authoritative:idfc-app-fd:2026-06-26")
            self._sweep(conn, "s0", -700000, "2026-06-26", "Sweepout FD 0 booked")
            self._sweep(conn, "s1", -300000, "2026-06-27", "Sweepout FD 1 booked")
            roll = deposits.rolled(conn)
            self.assertEqual((roll["legs"], roll["balance_minor"]), (1, 10300000))


class TestFxPin(unittest.TestCase):
    def _fetch(self, date, base, quote):
        return (Decimal("0.012"), date)             # 1 INR = 0.012 USD

    def test_inr_pins_to_usd_cents(self):
        from modules.finance import fx
        rows = [{"currency": "INR", "amount_minor": 5000000,     # ₹50,000 paise
                 "value_date": "2026-05-02", "date": "2026-05-02"}]
        fx.pin_base(rows, fetch=self._fetch)
        self.assertEqual(rows[0]["amount_base_minor"], 60000)    # $600.00 cents
        self.assertEqual(rows[0]["fx_rate"], "0.012")
        self.assertEqual(rows[0]["fx_date"], "2026-05-02")

    def test_usd_row_rate_one_no_fetch(self):
        from modules.finance import fx
        called = []
        rows = [{"currency": "USD", "amount_minor": 161500, "date": "2026-05-30"}]
        fx.pin_base(rows, fetch=lambda *a: called.append(a) or (Decimal("1"), a[0]))
        self.assertEqual(rows[0]["amount_base_minor"], 161500)
        self.assertEqual(rows[0]["fx_rate"], "1")
        self.assertEqual(called, [])                  # base currency never fetched

    def test_realized_rate_respected_not_refetched(self):
        from modules.finance import fx
        called = []
        # a Wise transfer leg already carrying its realized rate
        rows = [{"currency": "USD", "amount_minor": -158223, "fx_rate": "94.965",
                 "date": "2026-05-30"}]
        fx.pin_base(rows, base_currency="ZZZ",
                    fetch=lambda *a: called.append(a) or (Decimal("1"), a[0]))
        self.assertEqual(called, [])                  # realized rate not refetched
        self.assertEqual(rows[0]["fx_rate"], "94.965")

    def test_quarantined_row_skipped(self):
        from modules.finance import fx
        rows = [{"currency": "INR", "amount_minor": None, "date": "2026-05-02"}]
        res = fx.pin_base(rows, fetch=self._fetch)
        self.assertEqual(res["skipped"], 1)
        self.assertNotIn("amount_base_minor", rows[0])


# ── Phase 2: transfers bucket classifier (§3.10) ─────────────────────────────
class TestTransfers(unittest.TestCase):
    def test_is_transfer_leg_markers(self):
        from modules.finance import transfers
        self.assertTrue(transfers.is_transfer_leg({"upwork_type": "APPayment"}))
        self.assertTrue(transfers.is_transfer_leg({"accounting_subtype": "Withdrawal"}))
        self.assertTrue(transfers.is_transfer_leg({"accounting_subtype": "Withdrawal Fee"}))
        self.assertTrue(transfers.is_transfer_leg({"kind": "transfer_out"}))
        self.assertTrue(transfers.is_transfer_leg({"category": "transfer_in"}))
        self.assertTrue(transfers.is_transfer_leg({"description": "Bank Transfer · Withdrawal fee"}))
        # NOT transfers
        self.assertFalse(transfers.is_transfer_leg({"type": "Hourly Earning"}))
        self.assertFalse(transfers.is_transfer_leg({"accounting_subtype": "GST"}))
        self.assertFalse(transfers.is_transfer_leg({"category": "expense"}))

    def test_leg_role(self):
        from modules.finance import transfers
        self.assertEqual(transfers.leg_role({"upwork_type": "APPayment"}), "out_principal")
        self.assertEqual(transfers.leg_role({"accounting_subtype": "Withdrawal Fee"}), "fee")
        self.assertEqual(transfers.leg_role({"category": "transfer_in"}), "in_principal")

    def test_exclude_from_pnl(self):
        from modules.finance import transfers
        rows = [{"type": "Hourly Earning"}, {"upwork_type": "APPayment"},
                {"accounting_subtype": "Withdrawal Fee"}, {"category": "income"}]
        kept = list(transfers.exclude_from_pnl(rows))
        self.assertEqual(len(kept), 2)               # the 2 non-transfer rows
        self.assertNotIn({"upwork_type": "APPayment"}, kept)


# ── Phase 3: P&L category map ────────────────────────────────────────────────
class TestPnl(unittest.TestCase):
    def test_bucket_map(self):
        from modules.finance import pnl
        b = pnl.bucket
        self.assertEqual(b({"type": "Hourly Earning"}), "revenue")
        self.assertEqual(b({"category": "income"}), "revenue")
        self.assertEqual(b({"type": "Refund"}), "refund")
        self.assertEqual(b({"type": "Connect Purchase"}), "platform_fee")
        self.assertEqual(b({"accounting_subtype": "Service Fee"}), "platform_fee")
        self.assertEqual(b({"upwork_type": "Service Fee"}), "platform_fee")   # either field
        self.assertEqual(b({"category": "fee"}), "platform_fee")              # Wise
        self.assertEqual(b({"accounting_subtype": "WHT"}), "tax")
        self.assertEqual(b({"accounting_subtype": "GST"}), "tax")
        self.assertEqual(b({"category": "salary"}), "payroll")
        self.assertEqual(b({"type": "Salary"}), "payroll")                   # legacy bank
        self.assertEqual(b({"category": "expense"}), "opex")
        self.assertEqual(b({"type": "Other Expense"}), "opex")
        self.assertEqual(b({"type": "Tools / SaaS"}), "opex")

    def test_bucket_excludes_and_owner_draw(self):
        from modules.finance import pnl
        b = pnl.bucket
        self.assertEqual(b({"category": "transfer_in"}), "transfer")
        self.assertEqual(b({"upwork_type": "APPayment"}), "transfer")
        self.assertEqual(b({"type": "Withdrawal"}), "transfer")
        self.assertEqual(b({"category": "internal_sweep"}), "sweep")
        self.assertEqual(b({"category": "cc_settlement"}), "cc_settlement")
        self.assertEqual(b({"category": "owner_draw"}), "owner_draw")        # below the line
        self.assertEqual(b({"type": "Personal"}), "owner_draw")
        self.assertEqual(b({"type": "Mystery"}), "uncategorized")            # flagged

    def test_compute_operating_profit_and_exclusions(self):
        from modules.finance import pnl
        rows = [
            {"type": "Hourly Earning", "amount_base_minor": 100000, "source": "Upwork"},
            {"category": "income", "amount_base_minor": 50000, "source": "Wise"},
            # opex = the credit-card statement (2026-07-08 rule)
            {"type": "Tools / SaaS", "amount_base_minor": -30000, "source": "Card"},
            {"accounting_subtype": "Service Fee", "amount_base_minor": -10000, "source": "Upwork"},
            {"category": "owner_draw", "amount_base_minor": -20000, "source": "IDFC"},   # below line
            # a bank-statement UPI spend: personal -> owner_draw, NOT opex
            {"category": "expense", "amount_base_minor": -7000, "source": "IDFC",
             "description": "UPI/MOB/551610097279/payment on CRED"},
            {"category": "internal_sweep", "amount_base_minor": -999999, "source": "IDFC"},  # excluded
            {"category": "transfer_in", "amount_base_minor": 888888, "source": "IDFC"},      # excluded
            {"type": "Mystery", "amount_base_minor": -123, "source": "Bank"},               # flagged
        ]
        r = pnl.compute(rows)
        # operating = 100000 + 50000 - 30000 - 10000 = 110000 ; draws/sweeps/transfers OUT
        self.assertEqual(r["operating_profit_minor"], 110000)
        self.assertEqual(r["owner_draw_minor"], -27000)          # deliberate + UPI spend
        self.assertEqual(r["excluded_minor"]["sweep"], -999999)
        self.assertEqual(r["excluded_minor"]["transfer"], 888888)
        self.assertEqual(r["uncategorized_count"], 1)

    def test_a_named_upwork_hire_is_opex_and_the_rest_stay_platform_fee(self):
        """One bank line carries two unrelated things: Connects/membership on his
        FREELANCER side and the CLIENT side paying someone he HIRED. Every row
        reads "POS-VISA/Upwork -<ref>REF/.../Dublin" and the merchant string is
        truncated differently by the day, so nothing in the text decides it.
        Abhijeet, 2026-09-05: "16661 is a hire everything else is connects" — 26
        rows since Feb 2024, one of them a hire. So the default holds and the
        hire is named by its reference. A DATE FENCE WOULD BE WRONG: he will buy
        Connects on this card again and a fence would file it as a hire."""
        from modules.finance import pnl
        idfc = lambda d: {"category": "expense", "source": "IDFC", "description": d}
        self.assertEqual(pnl.bucket(idfc(
            "POS-VISA/Upwork -948419050REF/624422352895/Dublin/22:50:09")), "opex")
        for connects in ("POS-VISA/Upwork -882708855REF/601218347342/Dublin/18:47:49",
                         "POS-VISA/Upwork -743830485ConneDublin/429012383409/Dublin/",
                         "POS-VISA/Upwork -799420395REF/1234"):
            self.assertEqual(pnl.bucket(idfc(connects)), "platform_fee", connects)

    def test_a_salary_line_to_a_personal_counterparty_is_a_draw(self):
        """A payroll line is a cost the business BORE. Where the money comes
        back to him (2026-09-05), calling it payroll puts an expense in the P&L
        that was never paid — the personal-counterparty rule already exists for
        exactly that shape and it outranks the category."""
        from modules.finance import pnl
        row = {"category": "salary", "source": "IDFC", "amount_base_minor": -73000,
               "description": "NEFT/IDFB6227M1102099/Mr PRASHANT JAWAHARLAL"
                              " BHANDARI/MAHB0000978/Salary august"}
        self.assertEqual(pnl.bucket(row), "owner_draw")
        row["description"] = ("NEFT/IDFB6245M6033101/AVANTIKA DNYANESHWAR"
                              " BHAMBURE/BARB0GOREAS/Salary august")
        self.assertEqual(pnl.bucket(row), "owner_draw")
        # everyone else on the payroll is untouched
        row["description"] = "NEFT/IDFB6243M5276361/YOGITA SUDHIR SOMA/BKID/Salary august"
        self.assertEqual(pnl.bucket(row), "payroll")

    def test_the_second_upwork_hire_is_named_by_its_own_reference(self):
        """Rs 38,124 on 14 Sep, the 27th Upwork line on this card. Abhijeet,
        2026-10-06: "A freelancer I hired". Named by its reference like the
        first; the line a day either side of it would still be Connects."""
        from modules.finance import pnl
        idfc = lambda d: {"category": "expense", "source": "IDFC", "description": d}
        self.assertEqual(pnl.bucket(idfc(
            "POS-VISA/Upwork -951702482REF/625716351633/Dublin/16:18:05")), "opex")
        self.assertEqual(pnl.bucket(idfc(
            "POS-VISA/Upwork -951702483REF/625716351634/Dublin/16:18:06")),
            "platform_fee")

    def test_hanuman_is_a_draw_and_a_salary_narration_cannot_change_that(self):
        """Rs 1,50,000 on 22-23 Sep, no narration. Abhijeet, 2026-10-06: "this is
        the employee we paid for but it for tax purposes. so we actualy received
        cash for that". The money came back, so no wage was borne. Unnarrated it
        would be a draw by default; he is NAMED so a later line that says
        "Salary" (or the unnarrated-salary pass setting category='salary')
        cannot turn it into payroll."""
        from modules.finance import pnl
        for desc in ("IMPS-OPM/626513990250/HANUMAN SOMEENATH MORE/CNRB0000205/9761/",
                     "NEFT/IDFB6265M1906076/HANUMAN SOMEENATH MORE/CNRB0000205",
                     "NEFT/IDFB6266M2201464/HANUMAN SOMEENATH MORE/CNRB0000205"):
            self.assertEqual(pnl.bucket({"category": "expense", "source": "IDFC",
                                         "amount_base_minor": -112500,
                                         "description": desc}), "owner_draw", desc)
        self.assertEqual(pnl.bucket(
            {"category": "salary", "source": "IDFC", "amount_base_minor": -112500,
             "description": "NEFT/IDFB6299M0000001/HANUMAN SOMEENATH MORE/CNRB0000205"
                            "/Salary october"}), "owner_draw")

    def test_the_coworking_desk_is_office_space(self):
        """myHQ, Rs 26,622.64 by UPI on 22 Sep — ticked as the company's on
        2026-10-06. A vendor rule beside the office rent: a coworking membership
        is an office by definition, and it recurs."""
        from modules.finance import pnl
        self.assertEqual(pnl.bucket(
            {"category": "expense", "source": "IDFC",
             "description": "UPI/DR/663131352681/MYHQ ANA/NSPB/cf.myhq/6553598130"}),
            "opex")

    def test_a_payment_he_confirmed_is_business_and_its_neighbours_are_not(self):
        """The default for an unlisted bank spend is a draw, and it is the right
        default. Four payments are business because he said so about each
        (2026-10-06) — by the bank's own reference, NOT by vendor: the identical
        Jio bill a month earlier, another MakeMyTrip booking and the CA's next
        filing fee all stay draws until he says the same of them."""
        from modules.finance import pnl
        idfc = lambda d: {"category": "expense", "source": "IDFC", "description": d}
        for desc in (
                "NEFT/IDFB6259M0398252/TAMALISTO/ICIC0002587/Tamalisto advance 2R 3N",
                "POS-VISA/PYU*Makemytrip India PGURGAON /6272125289/GURGAON/12:38:02",
                "UPI/DR/662601147108/MYJIO/HDFC/myjio4./Pay",
                "IFT/10171724864/PRITI BORA & CO /14039720/TDS return filing Q1"):
            self.assertTrue(pnl.is_confirmed_business(desc), desc)
            self.assertEqual(pnl.bucket(idfc(desc)), "opex", desc)
        for desc in (
                "UPI/DR/659808386668/JIO/KKBK/relianc/Pay",              # 20 Aug, same bill
                "UPI/DR/317863592263/MYJIO/HDFC/myjio.e/Pay",            # Jan, Rs 49
                "POS-VISA/PYU*Makemytrip India PGURGAON /6300000001/GURGAON/09:00:00",
                "NEFT/IDFB6300M0000001/TAMALISTO/ICIC0002587/Tamalisto balance",
                "IFT/10171724864/PRITI BORA & CO /15151515/TDS return filing Q2"):
            self.assertFalse(pnl.is_confirmed_business(desc), desc)
            self.assertEqual(pnl.bucket(idfc(desc)), "owner_draw", desc)

    def test_a_confirmed_reference_must_be_a_whole_field(self):
        """'14039720' is eight digits. Matched as a substring it would also
        claim any payment whose longer reference happens to contain it."""
        from modules.finance import pnl
        for desc in ("UPI/DR/914039720555/SOMEONE/HDFC/x/UPI",
                     "NEFT/IDFB14039720M01/SOMEONE ELSE/HDFC0000001",
                     "POS-VISA/SHOP 6272125289X/100000000001/PUNE/10:00:00"):
            self.assertFalse(pnl.is_confirmed_business(desc), desc)
            self.assertEqual(pnl.bucket({"category": "expense", "source": "IDFC",
                                         "description": desc}), "owner_draw", desc)
        self.assertFalse(pnl.is_confirmed_business(None))
        self.assertFalse(pnl.is_confirmed_business(""))

    def test_an_atm_that_kept_the_money_is_not_revenue(self):
        """"ONUS refund/EN413562/2716/11.09.2026", +Rs 10,000 on 12 Sep: the bank
        handing back what its ATM debited and never dispensed (withdrawal 2716
        the day before). It arrives as a bank CREDIT with no other marker, so
        the classifier calls it income — and Rs 10,000 of his own cash would
        have been September revenue. It reverses a draw, so it is one. Same for
        Apple's "Mandate Refund Test", which had put Rs 399 into July."""
        from modules.finance import pnl
        for desc in ("ONUS refund/EN413562/2716/11.09.2026",
                     "UPI/CR/103600150354/APPLE ME/HDFC/applese/Mandate Refund Test"):
            self.assertEqual(pnl.bucket({"category": "income", "source": "IDFC",
                                         "amount_base_minor": 11270,
                                         "description": desc}), "owner_draw", desc)
        # the withdrawals it reverses, and a real client credit, are untouched
        self.assertEqual(pnl.bucket(
            {"category": "owner_draw", "source": "IDFC",
             "description": "ATM/CASH WITHDRAWAL/2716/IMAGE HEIGHTS CHINCHWAPUNE MH"
                            " IN/SELF"}), "owner_draw")
        self.assertEqual(pnl.bucket(
            {"category": "income", "source": "IDFC",
             "description": "IMPS/616221595127/VISAPAYMENTSLIM/HDFC0000240/6389/"
                            "RDAVostroFIRINWEnsureKYCAMLFEMAcompliance"}), "revenue")

    def test_bank_spend_rules_2026_07(self):
        # Abhijeet's reclassification: opex = credit-card statement; bank spends
        # are personal (owner_draw) except the explicit business rules.
        from modules.finance import pnl
        b = pnl.bucket
        idfc = lambda desc, **kw: dict({"category": "expense", "source": "IDFC",
                                        "description": desc}, **kw)
        # Upwork platform charges off the bank -> platform_fee (25 of 26 rows)
        self.assertEqual(b(idfc("POS-VISA/UPWORK -799420395REF/1234")), "platform_fee")
        self.assertEqual(b(idfc("POS-VISA/PAYPAL *UPWRKESCROW/99")), "platform_fee")
        # business-SaaS vendor allowlist on debit card -> opex
        self.assertEqual(b(idfc("POS-VISA/GOOGLE WORKSPACE CYBS 22/9")), "opex")
        self.assertEqual(b(idfc("POS-VISA/OPENAI *CHATGPT SUBSCR+1415879")), "opex")
        # any other POS spend -> personal
        self.assertEqual(b(idfc("POS-VISA/PLAYSTATION NETWORK/77")), "owner_draw")
        # office rent -> opex; past employees/contractors -> payroll
        self.assertEqual(b(idfc("NEFT/IDFB1/RAKHI MUKESH BOHRA/KKBK00013")), "opex")
        self.assertEqual(b(idfc("NEFT/IDFB2/HARSHIT SINGHAL/HDFC0000001")), "payroll")
        self.assertEqual(b(idfc("NEFT/IDFB3/ATHARVA AJAY BARAWKA/SBIN01")), "payroll")
        # employees: salary-shaped (>= $1,000 base) -> payroll; smaller -> personal
        self.assertEqual(b(idfc("NEFT/IDFB4/JAY SUSHIL POKHARNA/BARB0GOR",
                                amount_base_minor=-135120)), "payroll")
        self.assertEqual(b(idfc("NEFT/IDFB5/SHRUTI DEEPAK DHUMNE/HDFC000",
                                amount_base_minor=-112700)), "payroll")
        self.assertEqual(b(idfc("UPI/DR/1/JAY SUSHIL POKHARNA/BARB/x",
                                amount_base_minor=-5000)), "owner_draw")
        # credit-card bill payment on the bank -> settlement (never a spend)
        self.assertEqual(b(idfc("Ecom/62881027/IndiaIdeas/AMEXCARD/Creditcardbill")),
                         "cc_settlement")
        # UPI / EMI / everything else -> personal
        self.assertEqual(b(idfc("UPI/MOB/548709957603/Pay")), "owner_draw")
        self.assertEqual(b(idfc("NACH/BOI EMI COLLECTION/035D646A9")), "owner_draw")
        # legacy 'Bank'-source rows route the same way; 'Card' stays opex
        self.assertEqual(b({"type": "Tools / SaaS", "source": "Bank",
                            "description": "POS-VISA/OPENAI/509"}), "opex")
        self.assertEqual(b({"type": "Other Expense", "source": "Bank",
                            "description": "IFT/95381102014/x/UPI"}), "owner_draw")
        self.assertEqual(b({"type": "Tools / SaaS", "source": "Card",
                            "description": "RENDER.COM, RENDER.COM"}), "opex")
        # non-bank sources with category=expense keep opex (unchanged behavior)
        self.assertEqual(b({"category": "expense", "source": "Wise"}), "opex")

    def test_personal_counterparty_return_is_not_revenue(self):
        # Dynatech: a private transfer Abhijeet sent (-) and got back (+). The
        # RETURN carried category='income' and was mis-booked revenue; a personal
        # counterparty overrides that -> owner_draw, both directions (2026-07-30).
        from modules.finance import pnl
        b = pnl.bucket
        ret = {"category": "income", "source": "IDFC",
               "description": "NEFT/002651944142/DYNATECH ENGINEERING/UBIN05",
               "amount_base_minor": 1055000}
        self.assertEqual(b(ret), "owner_draw")           # NOT revenue
        out = {"category": "expense", "source": "IDFC",
               "description": "NEFT/IDFB5/DYNATECH ENGINEERING/UB",
               "amount_base_minor": -1114000}
        self.assertEqual(b(out), "owner_draw")
        # a genuine income row from a real client is untouched
        self.assertEqual(b({"category": "income", "source": "IDFC",
                            "description": "IMPS/1/RealClient/HDFC"}), "revenue")

    def test_arpayment_settlement_leg_is_not_revenue(self):
        # 'Payment - Visa' funding an Upwork connects/membership ARInvoice is the
        # settlement leg — the cost is the paired ARInvoice (platform_fee), so
        # the payment is non-P&L, never revenue (Abhijeet, 2026-07-30)
        from modules.finance import pnl
        self.assertEqual(pnl.bucket({
            "upwork_type": "ARPayment", "accounting_subtype": "Payment",
            "description": "Payment - Visa 3368", "amount_base_minor": 4500}),
            "transfer")
        # a real earning is still revenue
        self.assertEqual(pnl.bucket({"type": "Hourly Earning",
                                     "description": "work"}), "revenue")
        # the CSV export spells the same leg 'Payment' + the card in the text
        # (Abhijeet, 2026-08-26 17:55: the $45 Visa row was booked as revenue)
        self.assertEqual(pnl.bucket({
            "source": "Upwork-CSV", "type": "Other", "upwork_type": "Payment",
            "accounting_subtype": "Payment",
            "description": "300 Connects · Visa 3368 - Ref ID 941421323"}), "transfer")
        # a 'Payment' subtype WITHOUT an instrument stays what it was: an
        # earning mis-typed 'Other' by Upwork
        self.assertEqual(pnl.bucket({"type": "Other", "accounting_subtype": "Payment",
                                     "description": "Milestone payment for phase 2"}), "revenue")

    def test_plan_subscriptions_are_platform_fees(self):
        # the CSV's 'Subscription' and the API's 'Membership Fee' are the same
        # Freelancer Plus / Agency Plus charge (2026-08-26: two -$19.99 CSV rows
        # sat uncategorized because their text says nothing about membership)
        from modules.finance import pnl
        self.assertEqual(pnl.bucket({
            "source": "Upwork-CSV", "type": "Other", "upwork_type": "Subscription",
            "accounting_subtype": "Subscription",
            "description": "Subscription Renewal Charges · Subscription Renewal Charges"}),
            "platform_fee")
        self.assertEqual(pnl.bucket({"accounting_subtype": "Membership Fee"}), "platform_fee")

    def test_fd_interest_and_refunds_are_not_revenue(self):
        # bank interest + expense refunds/reversals -> owner_draw, not revenue
        from modules.finance import pnl
        for desc in ("QUARTERLY FD INTEREST CREDIT FOR 10241213363/Gross",
                     "REF/POS-VISA/ADITYA BIRLA HE/160395/080725",
                     "POS-VISA/WWW.MAKE.COM-CANCELLED/525418121293",
                     "UPI/REV/618028066528/TRUPTI A/SBIN/truptig/UPI"):
            self.assertEqual(pnl.bucket({"category": "income", "source": "IDFC",
                                         "description": desc}), "owner_draw", desc)
        # a normal IMPS client credit stays revenue
        self.assertEqual(pnl.bucket({"category": "income", "source": "IDFC",
                                     "description": "IMPS/1/SomeClient/HDFC"}),
                         "revenue")


class TestBucketizeAmountDependent(unittest.TestCase):
    """Regression (2026-07-08): bucket() became amount-dependent (salary floor),
    but bucketize + doctor #10 selected rows WITHOUT amount_base_minor — the rule
    saw 0 on both sides, so salaries mis-bucketed to owner_draw AND #10 stayed
    green. Both loaders must feed bucket() every field it reads."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fin-bucketize-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type, category,"
                " description, amount_minor, amount_base_minor, currency, source,"
                " pnl_bucket) VALUES ('jay1','kjay1','expense','expense',"
                " 'NEFT/IDFB527349128872/JAY SUSHIL POKHARNA/BARB0GOR',"
                " -12000000, -135120, 'INR', 'IDFC', 'opex')")   # stale stored bucket

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _bucketize(self):
        import importlib.machinery, importlib.util
        path = Path(__file__).resolve().parents[1] / "bin" / "finance-bucketize"
        loader = importlib.machinery.SourceFileLoader("finance_bucketize", str(path))
        spec = importlib.util.spec_from_loader("finance_bucketize", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod

    def test_salary_floor_survives_the_loader_roundtrip(self):
        from unittest import mock
        from modules.finance import doctor
        # doctor #10 must CATCH the stale bucket (needs the amount to see it)
        pre = next(c for c in doctor.run(self.db_path).checks
                   if c.invariant == "#10 P&L reproducibility")
        self.assertEqual(pre.status, "RED")
        mod = self._bucketize()
        with mock.patch.object(mod.config, "DB_PATH", self.db_path):
            out = mod.run(apply=True)
        self.assertEqual(out["changed"], 1)
        with db.connect(self.db_path) as conn:
            stored = conn.execute("SELECT pnl_bucket FROM transactions"
                                  " WHERE record_id='jay1'").fetchone()["pnl_bucket"]
        self.assertEqual(stored, "payroll")            # the $1,351 salary, not a draw
        post = next(c for c in doctor.run(self.db_path).checks
                    if c.invariant == "#10 P&L reproducibility")
        self.assertEqual(post.status, "GREEN")


class TestCardPdf(unittest.TestCase):
    """IDFC Mayura credit-card PDF importer — parser + self-validating reconcile
    (the card statement IS opex, Abhijeet 2026-07-08). Fixtures mirror the real
    pypdf extraction, including wrapped descriptions and the DR/CR layout."""

    # summary consistent with PAGE1's rows: ΣDR 28,603.58, ΣCR 1,45,004.43,
    # flow +1,16,400.85; opening 1,45,002.43 -> total due 28,601.58. The
    # payments/purchases pair (1,45,004.43 -> 28,603.58) ALSO satisfies the
    # equation (the 2.00 reversal degeneracy) — only the DR tag disambiguates.
    PAGE0 = ("ABHIJEET ASHOK GANDHI \nMayura Credit Card Statement\n"
             "Statement Period: 23/May/2026 - 22/Jun/2026\n"
             "r1,45,002.43 r0.00 r1,45,004.43r28,603.58 r0.00 r28,601.58 DR\n"
             "Minimum Amount Due\nr2,006.00 DR\n(Mayura Credit Card XX3368)\n"
             "APR applicable on this credit card is 40.00%")
    PAGE1 = ("Statement Date:\n22/Jun/2026\n"
             "21/05/2026 CLICKUP, 8886254258 Convert USD 80.00  7,757.58 DR\n"
             "26/05/2026 Credit Card Payment Received 1,45,002.43 CR\n"
             "04/06/2026 IND*LINKEDIN, HTTP://WWW.LI  2.00 DR\n"
             "05/06/2026 OPENAI *CHATGPT SUBSCR, \nOPENAI.COM Convert  1,694.07 DR\n"
             "06/06/2026 IND*LINKEDIN, HTTP://WWW.LI 2.00 CR\n"
             "10/06/2026 ANTHROPIC* CLAUDE SUB, \nANTHROPIC.COM Convert USD 200.00  19,149.93 DR\n")

    def test_parse_rows_and_meta(self):
        from modules.finance.importers import card_pdf
        p = card_pdf.parse_text([self.PAGE0, self.PAGE1])
        self.assertEqual(p["card4"], "3368")
        self.assertEqual(p["period_start"], "2026-05-23")
        self.assertEqual(p["period_end"], "2026-06-22")
        self.assertEqual(len(p["rows"]), 6)
        by_desc = {r["description"][:8]: r for r in p["rows"]}
        self.assertEqual(by_desc["CLICKUP,"]["amount_minor"], -775758)  # DR neg
        # wrapped description joined; 'Convert' noise stripped
        openai = [r for r in p["rows"] if r["description"].startswith("OPENAI")][0]
        self.assertIn("OPENAI.COM", openai["description"])
        self.assertNotIn("Convert", openai["description"])
        self.assertEqual(openai["amount_minor"], -169407)
        # payment CR -> cc_settlement (mirrors the bank-side leg, excluded)
        pay = [r for r in p["rows"] if "Payment Received" in r["description"]][0]
        self.assertEqual(pay["category"], "cc_settlement")
        self.assertEqual(pay["amount_minor"], 14500243)
        # a CR reversal stays 'expense' with POSITIVE paise (opex offset)
        rev = [r for r in p["rows"] if r["drcr"] == "CR"
               and "LINKEDIN" in r["description"]][0]
        self.assertEqual(rev["category"], "expense")
        self.assertEqual(rev["amount_minor"], 200)
        # canonical keys unique + stable
        keys = [r["canonical_key"] for r in p["rows"]]
        self.assertEqual(len(set(keys)), 6)
        again = card_pdf.parse_text([self.PAGE0, self.PAGE1])
        self.assertEqual(keys, [r["canonical_key"] for r in again["rows"]])

    def test_identical_rows_get_occurrence_index(self):
        from modules.finance.importers import card_pdf
        page = ("01/06/2026 CLICKUP, 8886254258  100.00 DR\n"
                "01/06/2026 CLICKUP, 8886254258  100.00 DR\n")
        p = card_pdf.parse_text(["", page])
        self.assertEqual(len(p["rows"]), 2)
        self.assertNotEqual(p["rows"][0]["canonical_key"],
                            p["rows"][1]["canonical_key"])

    def test_reconcile_dr_tag_breaks_degeneracy(self):
        from modules.finance.importers import card_pdf
        # the real statement: payments/purchases ALSO satisfy the equation
        # (both differ from opening/total by the same 2.00 reversal); only the
        # DR-tagged total picks the true pair.
        p = card_pdf.parse_text([self.PAGE0, self.PAGE1])
        rec = card_pdf.reconcile(p)
        self.assertTrue(rec["ok"], rec)
        self.assertEqual(rec["opening_minor"], 14500243)
        self.assertEqual(rec["total_due_minor"], 2860158)

    def test_reconcile_ambiguous_aborts(self):
        from modules.finance.importers import card_pdf
        p = card_pdf.parse_text(
            ["r1,000.00 r850.00 r150.00 r0.00", "01/06/2026 X  850.00 CR\n"])
        # no DR-tagged figures; both 1,000.00 and 0.00... produce >1 or 0 pairs
        rec = card_pdf.reconcile(p)
        self.assertFalse(rec["ok"])


class TestCardImporterEndToEnd(unittest.TestCase):
    PAGE0 = "Statement Period: 23/May/2026 - 22/Jun/2026\nXX3368\nr1,000.00 r150.00 DR r999.99"
    PAGE1 = ("21/05/2026 CLICKUP, 8886254258  100.00 DR\n"
             "26/05/2026 Credit Card Payment Received 1,000.00 CR\n"
             "05/06/2026 OPENAI SUBSCR  50.00 DR\n")

    def setUp(self):
        import importlib.machinery
        import importlib.util
        self.tmp = Path(tempfile.mkdtemp(prefix="card-import-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        (self.tmp / "inbox").mkdir()
        (self.tmp / "inbox" / "stmt.pdf").write_bytes(b"%PDF-fake")
        path = Path(__file__).resolve().parents[1] / "bin" / "finance-import-card"
        loader = importlib.machinery.SourceFileLoader("fin_imp_card", str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.mod = importlib.util.module_from_spec(spec)
        loader.exec_module(self.mod)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, apply):
        from unittest import mock
        with mock.patch.object(self.mod, "INBOX", self.tmp / "inbox"), \
                mock.patch.object(self.mod.config, "DB_PATH", self.db_path), \
                mock.patch.object(self.mod.card_pdf, "extract_text",
                                  return_value=[self.PAGE0, self.PAGE1]), \
                mock.patch.object(self.mod.fx, "pin_base",
                                  side_effect=lambda rows, **k: (
                                      [r.update({"amount_base_minor": r["amount_minor"],
                                                 "fx_rate": "0.012", "fx_date": r["date"]})
                                       for r in rows],
                                      {"pinned": len(rows), "fetched_dates": []})[1]):
            return self.mod.run(apply)

    def test_apply_idempotent_buckets_and_no_balances(self):
        out = self._run(apply=True)
        self.assertEqual(out["inserted"], 3)
        self.assertEqual(out["aborted_files"], [])
        with db.connect(self.db_path) as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM transactions WHERE source='Card' ORDER BY creation_dt")]
            nbal = conn.execute("SELECT COUNT(*) c FROM account_balances").fetchone()["c"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(nbal, 0)                     # liability, never cash
        spends = [r for r in rows if r["category"] == "expense"]
        self.assertEqual({r["pnl_bucket"] for r in spends}, {"opex"})   # his rule
        pay = [r for r in rows if r["category"] == "cc_settlement"][0]
        self.assertEqual(pay["pnl_bucket"], "cc_settlement")
        # stamped bucket reproduces bucket() -> doctor #10 stays GREEN
        from modules.finance import doctor
        c10 = [c for c in doctor.run(self.db_path).checks
               if c.invariant == "#10 P&L reproducibility"][0]
        self.assertEqual(c10.status, "GREEN")
        # re-run: canonical-guarded no-op
        out2 = self._run(apply=True)
        self.assertEqual(out2["inserted"], 0)
        self.assertEqual(out2["new_rows"], 0)


class TestIdfcImporterPicksNewest(unittest.TestCase):
    """Regression (2026-07-07 cockpit upload): 'newest statement' must be newest
    by MTIME. Filename sort picked the old 'idfc-*.xlsx' over a freshly-uploaded
    'IDFCFIRSTBankstatement*.xlsx' (uppercase sorts first in ASCII) and silently
    dry-ran the stale file."""

    def test_newest_by_mtime_not_filename(self):
        import importlib.machinery
        import importlib.util
        import os
        import time
        from unittest import mock
        tmp = Path(tempfile.mkdtemp(prefix="idfc-inbox-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        old = tmp / "idfc-60606200234.xlsx"          # lexically LAST (lowercase)
        new = tmp / "IDFCFIRSTBankstatement.xlsx"    # lexically first, mtime newest
        old.write_bytes(b"o")
        new.write_bytes(b"n")
        past = time.time() - 3600
        os.utime(old, (past, past))
        path = Path(__file__).resolve().parents[1] / "bin" / "finance-import-idfc"
        loader = importlib.machinery.SourceFileLoader("fin_imp_idfc", str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        seen = {}

        class Stop(Exception):
            pass

        def fake_parse(p):
            seen["file"] = Path(p).name
            raise Stop()

        with mock.patch.object(mod, "INBOX", tmp), \
                mock.patch.object(mod.bank_xlsx, "parse", side_effect=fake_parse):
            with self.assertRaises(Stop):
                mod.run(False)
        self.assertEqual(seen["file"], "IDFCFIRSTBankstatement.xlsx")


class TestIdfcImporterDepositSource(unittest.TestCase):
    """Where an authoritative deposit figure CAME from is written down.

    It was always "idfc-app-fd" because it always was his app's FD list, typed
    in. On 2026-10-06 September's figure came from the bank's consolidated
    statement instead, which prints every deposit's principal (44 of them,
    summing to the Rs 77,65,875.55 on its own summary page). A row saying "app"
    about a number nobody read off the app is a small lie that gets believed."""

    GRID = ([["ACCOUNT NUMBER", "60606200234"], ["CUSTOMER NAME", "ETHERWISE"],
             ["STATEMENT PERIOD", "29-Sep-2026 TO 30-Sep-2026"]] + [[]] * 10
            + [TestBankXlsx.HDR,
               ["29-Sep-2026", "29-Sep-2026", "Sweepout FD 10306819768 booked", "",
                "78000.00", "", "500643.55"],
               ["30-Sep-2026", "30-Sep-2026", "NEFT/INDBH30097541206/PAYMENT ESCROW"
                " INC/INDB0000006/PAYMENT ESCROW INC", "", "", "69676.08",
                "570319.63"]])

    def setUp(self):
        import importlib.machinery
        import importlib.util
        self.tmp = Path(tempfile.mkdtemp(prefix="idfc-fd-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        (self.tmp / "inbox").mkdir()
        (self.tmp / "inbox" / "sep.xlsx").write_bytes(_make_idfc_xlsx(self.GRID))
        path = Path(__file__).resolve().parents[1] / "bin" / "finance-import-idfc"
        loader = importlib.machinery.SourceFileLoader("fin_imp_idfc_fd", str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.mod = importlib.util.module_from_spec(spec)
        loader.exec_module(self.mod)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, **kw):
        def pin(rows, **_):
            for r in rows:
                r.update({"amount_base_minor": money.apply_rate(r["amount_minor"],
                                                                "0.01127"),
                          "fx_rate": "0.01127", "fx_date": r["date"]})
            return {"pinned": len(rows), "fetched_dates": []}
        with mock.patch.object(self.mod, "INBOX", self.tmp / "inbox"), \
                mock.patch.object(self.mod.config, "DB_PATH", self.db_path), \
                mock.patch.object(self.mod.fx, "pin_base", side_effect=pin):
            return self.mod.run(True, **kw)

    def _fd_rows(self):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(
                "SELECT closing_minor, statement_date, source_file FROM"
                " account_balances WHERE account='idfc-fd' ORDER BY id")]

    def test_the_default_still_says_his_app(self):
        res = self._run(fd_override=776587555, fd_as_of="2026-09-30")
        self.assertEqual(res["v3_inserted"], 2)
        self.assertEqual(res["fd_authoritative_source"], "idfc-app-fd")
        self.assertEqual(self._fd_rows(), [
            {"closing_minor": 776587555, "statement_date": "2026-09-30",
             "source_file": "authoritative:idfc-app-fd:2026-09-30"}])

    def test_a_named_source_is_recorded_and_still_anchors(self):
        """`deposits.anchor` takes any 'authoritative:' row, so the roll forward
        starts from this figure exactly as it would from his app's."""
        from modules.finance import deposits
        res = self._run(fd_override=776587555, fd_as_of="2026-09-30",
                        fd_source="idfc-consolidated-statement")
        self.assertEqual(res["fd_authoritative_source"], "idfc-consolidated-statement")
        self.assertEqual(res["fd_principal_inr"], "7765875.55")
        self.assertEqual(self._fd_rows(), [
            {"closing_minor": 776587555, "statement_date": "2026-09-30",
             "source_file": "authoritative:idfc-consolidated-statement:2026-09-30"}])
        with db.connect(self.db_path) as conn:
            self.assertEqual(deposits.anchor(conn), (776587555, "2026-09-30"))

    def test_no_figure_from_him_means_no_source_and_no_anchor(self):
        res = self._run()
        self.assertIsNone(res["fd_authoritative_source"])
        self.assertEqual(self._fd_rows(), [])

    def test_the_source_must_be_a_plain_slug(self):
        """It becomes part of a stored label and of a LIKE-matched prefix."""
        ok = self.mod._FD_SOURCE_RE.fullmatch
        self.assertTrue(ok("idfc-consolidated-statement"))
        self.assertTrue(ok("idfc-app-fd"))
        for bad in ("", "x", "Has Space", "UPPER", "semi;colon", "a%b", "-lead"):
            self.assertIsNone(ok(bad), bad)


# ── Phase 5: payroll compute + reconcile (prep-only) ─────────────────────────
class TestPayroll(unittest.TestCase):
    def test_split_integer_paise_sums_to_gross(self):
        from modules.finance import payroll
        line = payroll.compute_employee(
            {"employee_id": "E1", "name": "X", "gross_salary": 130000,
             "monthly_tds": 8905, "monthly_pt": 200})
        e = line["earnings"]
        self.assertEqual(e["basic"], 6500000)        # 50% of 130000 in paise
        self.assertEqual(e["hra"], 3250000)
        self.assertEqual(e["lta"], 1300000)
        self.assertEqual(e["special"], 1950000)      # residual = 15%
        self.assertEqual(sum(e.values()), 13000000)  # == gross exactly
        self.assertEqual(line["net_minor"], 13000000 - 890500 - 20000)
        self.assertIsNone(payroll.reconcile_line(line))

    def test_missing_tds_is_surfaced_not_computed(self):
        from modules.finance import payroll
        line = payroll.compute_employee(
            {"employee_id": "E1", "name": "Jay", "gross_salary": 240000,
             "monthly_tds": None, "monthly_pt": 200})
        self.assertEqual(line["missing"], ["monthly_tds"])
        self.assertNotIn("net_minor", line)           # never guessed

    def test_run_blocks_when_input_missing(self):
        from modules.finance import payroll
        emps = [
            {"employee_id": "E1", "name": "Jay", "gross_salary": 240000,
             "monthly_tds": None, "monthly_pt": 200, "status": "Active"},
            {"employee_id": "E2", "name": "Sh", "gross_salary": 130000,
             "monthly_tds": 8905, "monthly_pt": 200, "status": "Active"},
            {"employee_id": "E9", "name": "Gone", "gross_salary": 50000,
             "monthly_tds": 0, "monthly_pt": 200, "status": "Withdrawn"}]
        run = payroll.compute_run(emps, "2026-06")
        self.assertEqual(run["headcount"], 1)          # only the reconciling active one
        self.assertEqual(len(run["missing"]), 1)       # Jay surfaced
        self.assertFalse(run["finalizable"])           # missing input blocks the run

    def test_last_working_day_skips_weekend(self):
        from modules.finance import payroll
        self.assertEqual(payroll.last_working_day(2026, 6), "2026-06-30")   # Tue
        self.assertEqual(payroll.last_working_day(2026, 5), "2026-05-29")   # 31 May = Sun -> Fri


class TestDoctorPayroll(DoctorCase):
    def _run(self, conn, recon=True):
        conn.execute("INSERT INTO payroll_runs (period,pay_date,headcount,gross_minor,"
                     "tds_minor,pt_minor,net_minor) VALUES ('2026-06','2026-06-30',1,"
                     "13000000,890500,20000,12089500)")
        net = 12089500 if recon else 12000000      # tamper net to break reconcile
        conn.execute("INSERT INTO payroll_lines (run_id,employee_id,name,gross_minor,"
                     "basic_minor,hra_minor,special_minor,lta_minor,tds_minor,pt_minor,"
                     "net_minor) VALUES (1,'E2','Sh',13000000,6500000,3250000,1950000,"
                     "1300000,890500,20000,?)", (net,))

    def test_payroll_reconciles_green_6(self):
        with db.connect(self.db_path) as conn:
            self._run(conn, recon=True)
        c6 = [c for c in doctor.run(self.db_path).checks if c.invariant == "#6 payroll"][0]
        self.assertEqual(c6.status, "GREEN")

    def test_payroll_mismatch_red_6(self):
        with db.connect(self.db_path) as conn:
            self._run(conn, recon=False)
        res = doctor.run(self.db_path)
        c6 = [c for c in res.checks if c.invariant == "#6 payroll"][0]
        self.assertEqual(c6.status, "RED")
        self.assertFalse(res.green)


# ── Phase 6: independent-audit surface ───────────────────────────────────────
class TestAuditSurface(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fin-audit-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, **kw):
        import importlib.machinery
        import importlib.util
        path = Path(__file__).resolve().parents[1] / "bin" / "finance-audit"
        loader = importlib.machinery.SourceFileLoader("finance_audit", str(path))
        spec = importlib.util.spec_from_loader("finance_audit", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod.run(self.db_path, with_tieout=False, **kw)

    def _txn(self, rid, amount_minor, ckey):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO transactions (record_id, amount_minor, currency,"
                         " canonical_key) VALUES (?,?,?,?)", (rid, amount_minor, "USD", ckey))

    def test_pass_and_dynamic_invariants(self):
        self._txn("t1", 10000, "k1")                  # clean -> doctor green
        out = self._run()
        self.assertEqual(out["AUDIT"], "PASS")
        self.assertTrue(out["doctor_green"])
        names = {i["invariant"] for i in out["invariants"]}   # DYNAMIC, not hardcoded
        for inv in ("#3 ledger-statement tie-out", "#6 payroll", "#10 P&L reproducibility"):
            self.assertIn(inv, names)
        self.assertIn("money", out)

    def test_fail_gate_on_red(self):
        self._txn("t1", None, "k1")                   # NULL amount -> #8 RED
        out = self._run()
        self.assertEqual(out["AUDIT"], "FAIL")
        self.assertFalse(out["doctor_green"])
        self.assertIn("#8 sanity bounds", out["reds"])


# ── Agency-gap weekly CSV import: recovery round-trip on the importer path ───
class TestUpworkImportRecovery(unittest.TestCase):
    """The agency financial gap (no API amount until/without scope) is closed by
    the weekly Transaction-History CSV. Proves the bin wires the quarantine
    resolution: an amountless API-minted entry flips to imported when the CSV
    supplies the amount — inserted once, no dup, doctor #8 stays GREEN."""
    HEADER = ("Date,Transaction ID,Transaction type,Transaction summary,"
              "Transaction summary details,Description 1,Description 2,Description 3,"
              "Entered description,Agency team,Freelancer,Client team,Account name,PO,"
              "Ref ID,Amount $,Amount in local currency,Currency,Current balance $,"
              "Payment method")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="upw-import-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.inbox = self.tmp / "inbox"
        self.inbox.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _bin(self):
        import importlib.machinery
        import importlib.util
        path = Path(__file__).resolve().parents[1] / "bin" / "finance-import-upwork"
        loader = importlib.machinery.SourceFileLoader("finance_import_upwork", str(path))
        spec = importlib.util.spec_from_loader("finance_import_upwork", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod

    def test_csv_import_resolves_quarantined_agency_row(self):
        from modules.upwork import sync_state
        from modules.finance import doctor
        # 1) API sync saw the agency txn with NO amount -> quarantined, no live row
        with db.connect(self.db_path) as conn:
            self.assertEqual(sync_state.upsert_transaction(
                conn, {"recordId": "931008627", "type": "Hourly",
                       "description": "agency hourly"}, "agency"), "quarantined")
        # 2) the weekly agency CSV now carries that ref WITH its amount
        csv = (self.HEADER + "\n"
               + '"Jun 28, 2026",TXN1,Hourly,Agency hourly,,Job work,,,,Team,Free,'
                 'Client,Agency Acc,,931008627,123.45,,,123.45,')
        (self.inbox / "2026-06-28-upwork-agency-fullhistory.csv").write_text(csv)
        mod = self._bin()
        with mock.patch.object(mod, "INBOX", self.inbox), \
                mock.patch.object(mod.config, "DB_PATH", self.db_path), \
                mock.patch.object(mod, "REPORT", self.tmp / "rep.md"):
            res = mod.run(commit=True)
        # 3) inserted once, quarantine flipped to imported, doctor #8 GREEN
        self.assertEqual(res["new_inserted"], 1)
        self.assertEqual(res["quarantine_resolved"], 1)
        with db.connect(self.db_path) as conn:
            rows = conn.execute("SELECT amount_minor FROM transactions"
                                " WHERE canonical_key='upwork:931008627'").fetchall()
            self.assertEqual(len(rows), 1)                       # no dup
            self.assertEqual(rows[0]["amount_minor"], 12345)     # CSV amount, integer cents
            q = conn.execute("SELECT resolved_at, resolution FROM quarantine"
                             " WHERE reason LIKE '%(upwork:931008627)%'").fetchone()
            self.assertIsNotNone(q["resolved_at"])
            self.assertEqual(q["resolution"], "imported")
            san = next(c for c in doctor.run(self.db_path).checks
                       if c.invariant == "#8 sanity bounds")
            self.assertEqual(san.status, "GREEN")
        # 4) idempotent re-import resolves nothing further, inserts nothing
        with mock.patch.object(mod, "INBOX", self.inbox), \
                mock.patch.object(mod.config, "DB_PATH", self.db_path), \
                mock.patch.object(mod, "REPORT", self.tmp / "rep.md"):
            res2 = mod.run(commit=True)
        self.assertEqual(res2["new_inserted"], 0)
        self.assertEqual(res2["quarantine_resolved"], 0)

    def test_withdrawal_legs_are_inserted_as_transfers_not_held(self):
        """Abhijeet, 2026-08-26 16:50: "insert them as transfers". The hold of
        2026-06-25 had kept every withdrawal since 27 May out of the ledger."""
        from modules.finance import pnl
        csv = (self.HEADER + "\n"
               + '"Aug 26, 2026",TXN2,Withdrawal Fee,Withdrawal Fee,,Direct to Local Bank,,,,,,,'
                 'Acc,,946740501,-0.99,,,100.00,\n'
               + '"Aug 26, 2026",TXN1,Withdrawal,Withdrawal,,Direct to Local Bank Transfer,,,,,,,'
                 'Acc,,946740500,-2335.14,,,100.99,')
        (self.inbox / "2026-08-26-upwork-agency-fullhistory.csv").write_text(csv)
        mod = self._bin()
        with mock.patch.object(mod, "INBOX", self.inbox), \
                mock.patch.object(mod.config, "DB_PATH", self.db_path), \
                mock.patch.object(mod, "REPORT", self.tmp / "rep.md"):
            res = mod.run(commit=True)
        self.assertEqual((res["new_inserted"], res["new_transfer_legs_inserted"]), (2, 2))
        with db.connect(self.db_path) as conn:
            rows = {r["record_id"]: dict(r) for r in conn.execute(
                "SELECT record_id, amount_minor, type, accounting_subtype, description"
                " FROM transactions")}
        self.assertEqual(rows["946740500"]["amount_minor"], -233514)      # the CSV's own sign
        self.assertEqual(rows["946740500"]["type"], "Withdrawal")
        self.assertEqual({pnl.bucket(r) for r in rows.values()}, {"transfer"})


# ── Phase 5/6: finance-calendar nudges (pinned biweekly anchors) ─────────────
class TestNudges(unittest.TestCase):
    def test_pinned_anchors_project_biweekly_no_flag(self):
        import datetime
        from modules.finance import nudges
        # Horizons is ledger-derived (token); Hermon/Adelye are pinned anchors
        ev = nudges.upcoming(datetime.date(2026, 6, 30), 40, anchors={"HORIZON": "2026-06-19"})
        self.assertEqual([e for e in ev if e["type"] == "flag"], [])      # all anchored -> no flags
        for who, anchor in [("Hermon", datetime.date(2025, 10, 15)),
                            ("Adelye", datetime.date(2026, 1, 12))]:
            dates = [datetime.date.fromisoformat(e["date"]) for e in ev if who in e["who"]]
            self.assertTrue(dates)                                        # projected
            for d in dates:                                              # exactly on the 14-day cycle
                self.assertEqual((d - anchor).days % 14, 0)
        self.assertTrue(any(e["type"] == "payroll" for e in ev))         # payroll nudge present


# ── Phase 6: roster-sync (Airtable -> renderer, graceful fallback) ───────────
class TestRosterSync(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="roster-"))
        self.path = self.tmp / "employees.json"
        self.path.write_text(__import__("json").dumps({
            "company": {"name": "Etherwise"}, "salary_split": {},
            "employees": [{"employee_id": "E001", "name": "X", "email": "x@e.io",
                           "gross_salary": 50000, "monthly_tds": 0, "monthly_pt": 200,
                           "pan": "LOCALPAN1Z", "status": "Active"}]}))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _client(self, fields):
        class Fake:
            def list_records(self, base, table, **kw):
                return [{"fields": fields}]
        return lambda: Fake()

    def test_airtable_merges_dob_pan_department_over_local(self):
        from modules.finance import roster
        cfg = roster.load(self.path, client_factory=self._client(
            {"Employee ID": "E001", "Date of Birth": "2002-06-27",
             "PAN": "AIRPAN9999Z", "Department": "Engineering", "Status": "Active"}))
        e = cfg["employees"][0]
        self.assertEqual(e["date_of_birth"], "2002-06-27")   # -> PDF password
        self.assertEqual(e["pan"], "AIRPAN9999Z")            # Airtable wins
        self.assertEqual(e["department"], "Engineering")
        self.assertEqual(e["gross_salary"], 50000)           # money NOT overridden
        self.assertIn("airtable+local", cfg["_roster_source"])

    def test_blank_airtable_value_does_not_clobber_local(self):
        from modules.finance import roster
        cfg = roster.load(self.path, client_factory=self._client(
            {"Employee ID": "E001", "PAN": "", "Status": "Active"}))
        self.assertEqual(cfg["employees"][0]["pan"], "LOCALPAN1Z")   # blank ignored

    def test_graceful_fallback_when_airtable_unavailable(self):
        from modules.finance import roster

        def boom():
            raise RuntimeError("AIRTABLE_API_KEY not found")
        cfg = roster.load(self.path, client_factory=boom)
        self.assertTrue(cfg["_roster_source"].startswith("local"))
        self.assertNotIn("date_of_birth", cfg["employees"][0])       # nothing fabricated
        self.assertEqual(cfg["employees"][0]["pan"], "LOCALPAN1Z")


if __name__ == "__main__":
    unittest.main()
