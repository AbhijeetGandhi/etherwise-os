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


if __name__ == "__main__":
    unittest.main()
