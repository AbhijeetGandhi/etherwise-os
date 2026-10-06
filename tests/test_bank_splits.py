"""One bank payment that was two things (modules/finance/bank_splits.py).

Abhijeet, 2026-10-06, on Rs 1,15,000 paid to his CA on 19 Sep 2026 reading
"Professional fees": "55000 were fees. rest we got cash". The bank row stays
whole; a net-zero pair of derived rows carries the fee across to opex. These
tests hold the three things that must stay true: the P&L sees Rs 55,000 of cost
and Rs 60,000 of draw, the statement still ties out untouched, and the pair can
never quietly double-count. No network.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import db
from modules.finance import bank_splits, doctor, money, pnl

PARENT = "bank:9c2696cf7d475e96"
LINE = "IFT/10171724864/PRITI BORA & CO /900638497/Professional fees"
RATE = "0.01127"
CONTRA = "split|" + PARENT + "|contra"
COST = "split|" + PARENT + "|cost"


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bank-split-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pay(self, conn, minor=-11500000, rate=RATE, key=PARENT, desc=LINE,
             bucket="owner_draw", day="2026-09-19"):
        """The CA's payment as finance-import-idfc + bucketize leave it."""
        base = money.apply_rate(minor, rate) if rate else None
        conn.execute(
            "INSERT INTO transactions (record_id, canonical_key, dedup_key, type,"
            " accounting_subtype, category, source, source_file, pnl_bucket,"
            " amount_minor, amount_base_minor, currency, fx_rate, fx_date,"
            " description, creation_dt) VALUES"
            " (?,?,?,'expense','expense','expense','IDFC','sep.xlsx',?,?,?,'INR',?,?,?,?)",
            (key, key, key, bucket, minor, base, rate, day, desc, day))

    def _legs(self, conn):
        return [dict(r) for r in conn.execute(
            "SELECT * FROM transactions WHERE source_file='derived:bank-split'"
            " ORDER BY canonical_key")]

    def _by_bucket(self, conn, col="amount_minor"):
        return {r[0]: r[1] for r in conn.execute(
            f"SELECT pnl_bucket, SUM({col}) FROM transactions GROUP BY pnl_bucket")}


class TestBankSplits(_Case):
    def test_the_list_is_the_one_payment_he_described(self):
        spec = bank_splits.SPLITS[PARENT]
        self.assertEqual(spec["share_minor"], 5500000)         # "55000 were fees"
        self.assertEqual((spec["from_bucket"], spec["to_bucket"]),
                         ("owner_draw", "opex"))

    def test_the_fee_is_a_cost_and_the_rest_stays_a_draw(self):
        """Rs 55,000 of opex and Rs 60,000 of draw — in paise and in the USD
        the Money page shows — and together exactly what left the account."""
        with db.connect(self.db_path) as conn:
            self._pay(conn)
            res = bank_splits.apply(conn)
            inr, usd = self._by_bucket(conn), self._by_bucket(conn, "amount_base_minor")
        self.assertEqual(res, {"written": 2, "keys": [CONTRA, COST]})
        self.assertEqual(inr, {"opex": -5500000, "owner_draw": -6000000})
        fee_usd = money.apply_rate(5500000, RATE)                      # 61985
        paid_usd = money.apply_rate(-11500000, RATE)                   # -129605
        self.assertEqual(usd, {"opex": -fee_usd, "owner_draw": paid_usd + fee_usd})
        self.assertEqual(sum(inr.values()), -11500000)
        self.assertEqual(sum(usd.values()), paid_usd)

    def test_the_pair_nets_to_zero_in_paise_and_in_usd(self):
        with db.connect(self.db_path) as conn:
            self._pay(conn)
            bank_splits.apply(conn)
            legs = self._legs(conn)
        self.assertEqual([r["canonical_key"] for r in legs], [CONTRA, COST])
        self.assertEqual([r["amount_minor"] for r in legs], [5500000, -5500000])
        self.assertEqual(sum(r["amount_base_minor"] for r in legs), 0)
        self.assertEqual({r["fx_rate"] for r in legs}, {RATE})         # the payment's own
        self.assertEqual([r["pnl_bucket"] for r in legs], ["owner_draw", "opex"])

    def test_an_odd_rate_still_cannot_leave_a_cent_behind(self):
        """Magnitude first, sign after: rounding each leg on its own signed
        amount could split a half-cent two ways."""
        with db.connect(self.db_path) as conn:
            self._pay(conn, rate="0.0112745")
            bank_splits.apply(conn)
            legs = self._legs(conn)
        self.assertEqual(sum(r["amount_base_minor"] for r in legs), 0)
        self.assertEqual(abs(legs[0]["amount_base_minor"]),
                         money.apply_rate(5500000, "0.0112745"))

    def test_the_bank_row_is_never_touched(self):
        """The statement stays exactly what the bank sent."""
        with db.connect(self.db_path) as conn:
            self._pay(conn)
            before = dict(conn.execute("SELECT * FROM transactions WHERE"
                                       " canonical_key=?", (PARENT,)).fetchone())
            bank_splits.apply(conn)
            after = dict(conn.execute("SELECT * FROM transactions WHERE"
                                      " canonical_key=?", (PARENT,)).fetchone())
        self.assertEqual(before, after)

    def test_the_legs_bucket_back_so_10_stays_reproducible(self):
        """They are written with pnl_bucket already set; bucket() must agree
        from the columns doctor #10 reads, or the gate goes RED on insert."""
        with db.connect(self.db_path) as conn:
            self._pay(conn)
            bank_splits.apply(conn)
            rows = [dict(r) for r in conn.execute(
                "SELECT record_id, source, type, upwork_type, category,"
                " accounting_subtype, description, pnl_bucket, amount_base_minor"
                " FROM transactions")]
        self.assertEqual(len(rows), 3)
        for r in rows:
            self.assertEqual(pnl.bucket(r), r["pnl_bucket"], r["record_id"])

    def test_the_legs_follow_the_payment_into_its_month(self):
        with db.connect(self.db_path) as conn:
            self._pay(conn)
            conn.execute("UPDATE transactions SET pnl_month='2026-08'"
                         " WHERE canonical_key=?", (PARENT,))
            bank_splits.apply(conn)
            legs = self._legs(conn)
        self.assertEqual({r["creation_dt"] for r in legs}, {"2026-09-19"})
        self.assertEqual({r["pnl_month"] for r in legs}, {"2026-08"})

    def test_the_legs_say_what_they_are(self):
        with db.connect(self.db_path) as conn:
            self._pay(conn)
            bank_splits.apply(conn)
            legs = {r["canonical_key"]: r for r in self._legs(conn)}
        for r in legs.values():
            self.assertIn("Rs 55,000.00", r["description"])
            self.assertIn("Rs 1,15,000.00", r["description"])
            self.assertIn("2026-09-19", r["description"])
            self.assertEqual(r["type"], "CA fee")
        self.assertIn("Rs 60,000.00", legs[COST]["description"])
        # and nothing in the wording can be read as a transfer leg
        for r in legs.values():
            self.assertNotIn("withdrawal fee", r["description"].lower())

    def test_rupees_are_written_the_way_he_reads_them(self):
        """Lakh grouping. money.fmt would print Rs 1,15,000 as '115,000.00'."""
        rs = bank_splits._rs
        self.assertEqual(rs(11500000), "Rs 1,15,000.00")
        self.assertEqual(rs(5500000), "Rs 55,000.00")
        self.assertEqual(rs(776587555), "Rs 77,65,875.55")
        self.assertEqual(rs(123456789012), "Rs 1,23,45,67,890.12")
        self.assertEqual(rs(100000), "Rs 1,000.00")
        self.assertEqual(rs(99900), "Rs 999.00")
        self.assertEqual(rs(5), "Rs 0.05")
        self.assertEqual(rs(0), "Rs 0.00")
        self.assertEqual(rs(-6000000), "-Rs 60,000.00")

    def test_applying_twice_writes_nothing_new(self):
        with db.connect(self.db_path) as conn:
            self._pay(conn)
            bank_splits.apply(conn)
            self.assertEqual(bank_splits.apply(conn), {"written": 0, "keys": []})
            self.assertEqual(len(self._legs(conn)), 2)

    def test_no_split_without_the_payment_behind_it(self):
        """An adjustment to a payment that does not exist is a number with
        nothing behind it — and an empty ledger is not an error."""
        with db.connect(self.db_path) as conn:
            self.assertEqual(bank_splits.rows_to_write(conn), [])
            self.assertEqual(bank_splits.apply(conn)["written"], 0)
            self.assertEqual(bank_splits.problems(conn), [])
            self.assertEqual(bank_splits.in_ledger(conn), 0)

    def test_no_split_when_the_payment_has_no_pinned_rate(self):
        with db.connect(self.db_path) as conn:
            self._pay(conn, rate=None)
            self.assertEqual(bank_splits.rows_to_write(conn), [])
            bad = bank_splits.problems(conn)
        self.assertEqual(len(bad), 1)
        self.assertIn("no pinned FX rate", bad[0]["error"])

    def test_a_share_that_is_all_of_the_payment_is_refused(self):
        whole = dict(bank_splits.SPLITS[PARENT], share_minor=11500000)
        with mock.patch.dict(bank_splits.SPLITS, {PARENT: whole}), \
                db.connect(self.db_path) as conn:
            self._pay(conn)
            self.assertEqual(bank_splits.rows_to_write(conn), [])
            bad = bank_splits.problems(conn)
        self.assertEqual(len(bad), 1)
        self.assertIn("not strictly inside the payment", bad[0]["error"])

    def test_a_credit_cannot_be_split_as_a_payment(self):
        with db.connect(self.db_path) as conn:
            self._pay(conn, minor=11500000)
            self.assertEqual(bank_splits.rows_to_write(conn), [])
            self.assertIn("not a payment", bank_splits.problems(conn)[0]["error"])

    def test_no_split_once_the_payment_is_a_cost_wholesale(self):
        """THE FAILURE THIS MODULE MUST NOT COMMIT. If a later rule files the
        CA's payments under opex outright, carrying Rs 55,000 "out of draws"
        would book the fee twice — with every total still reconciling."""
        with mock.patch.object(pnl, "_RENT_PAYEES", pnl._RENT_PAYEES + ("PRITI BORA",)), \
                db.connect(self.db_path) as conn:
            self._pay(conn, bucket="opex")
            self.assertEqual(pnl.bucket(dict(conn.execute(
                "SELECT * FROM transactions").fetchone())), "opex")
            self.assertEqual(bank_splits.rows_to_write(conn), [])
            bad = bank_splits.problems(conn)
        self.assertEqual(len(bad), 1)
        self.assertIn("filed under opex, not owner_draw", bad[0]["error"])

    def test_a_changed_share_is_reported_not_silently_kept(self):
        """The legs are keyed without the amount, so editing the list after they
        are written would leave the old figure moving money."""
        with db.connect(self.db_path) as conn:
            self._pay(conn)
            bank_splits.apply(conn)
            less = dict(bank_splits.SPLITS[PARENT], share_minor=5000000)
            with mock.patch.dict(bank_splits.SPLITS, {PARENT: less}):
                bad = bank_splits.problems(conn)
                self.assertEqual(bank_splits.rows_to_write(conn), [])
        self.assertEqual(sorted(b["key"] for b in bad), [CONTRA, COST])
        self.assertTrue(all("no longer equals the share" in b["error"] for b in bad))

    def test_a_leg_whose_entry_was_deleted_is_reported(self):
        with db.connect(self.db_path) as conn:
            self._pay(conn)
            bank_splits.apply(conn)
            with mock.patch.dict(bank_splits.SPLITS, {}, clear=True):
                bad = bank_splits.problems(conn)
        self.assertEqual(sorted(b["key"] for b in bad), [CONTRA, COST])
        self.assertTrue(all("no longer in bank_splits.SPLITS" in b["error"] for b in bad))

    def test_legs_without_their_payment_are_reported(self):
        with db.connect(self.db_path) as conn:
            self._pay(conn)
            bank_splits.apply(conn)
            conn.execute("DELETE FROM transactions WHERE canonical_key=?", (PARENT,))
            bad = bank_splits.problems(conn)
        self.assertEqual(len(bad), 1)
        self.assertIn("not in the ledger", bad[0]["error"])

    def test_the_legs_are_not_bank_rows(self):
        """Three things downstream key on the bank's rows, and a derived row
        that looked like one would corrupt each of them:
          - #1/#3 sum by statement file and by source='IDFC' + 'bank:%';
          - finance-import-idfc quarantines every non-IDFC 'bank:%' row inside a
            statement window as a legacy v2 row — it would DELETE these;
          - the deposit roll and the TDS scan read source IN ('IDFC','Bank')."""
        with db.connect(self.db_path) as conn:
            self._pay(conn)
            bank_splits.apply(conn)
            legs = self._legs(conn)
            flow = doctor._window_flow(conn, "idfc", "INR", "2026-09-01", "2026-09-30")
            legacy = conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE canonical_key LIKE 'bank:%'"
                " AND (source IS NULL OR source != 'IDFC')").fetchone()[0]
        self.assertEqual({r["source"] for r in legs}, {"Direct"})
        self.assertEqual({r["source_file"] for r in legs}, {"derived:bank-split"})
        self.assertTrue(all(not r["canonical_key"].startswith("bank") for r in legs))
        self.assertEqual(flow, -11500000)            # the bank row alone
        self.assertEqual(legacy, 0)


class TestDoctorBankSplits(_Case):
    def _check(self):
        res = doctor.run(self.db_path)
        return res, [c for c in res.checks if c.invariant == "#15 bank splits"][0]

    def test_15_is_na_on_a_ledger_without_the_payment(self):
        res, c = self._check()
        self.assertEqual(c.status, "N/A")
        self.assertTrue(res.green)

    def test_15_red_while_the_payment_sits_whole_then_green_once_split(self):
        """Self-healing, like #12-#14: bin/finance-bucketize writes the pair and
        the cockpit runs that after every import."""
        with db.connect(self.db_path) as conn:
            self._pay(conn)
        res, c = self._check()
        self.assertEqual(c.status, "RED")
        self.assertIn("bucketize", c.detail)
        self.assertEqual(sorted(d["key"] for d in c.deltas), [CONTRA, COST])
        self.assertFalse(res.green)
        with db.connect(self.db_path) as conn:
            bank_splits.apply(conn)
        res, c = self._check()
        self.assertEqual(c.status, "GREEN")
        self.assertTrue(res.green, res.summary())

    def test_15_is_the_only_check_that_sees_the_fee_counted_twice(self):
        """Split written; later a rule (and a re-bucketize) files the CA's
        payment under opex wholesale. Every other invariant still holds — the
        pair nets to zero, the statement ties, the stored buckets reproduce —
        while opex now carries the Rs 55,000 twice."""
        with db.connect(self.db_path) as conn:
            self._pay(conn)
            bank_splits.apply(conn)
        with mock.patch.object(pnl, "_RENT_PAYEES", pnl._RENT_PAYEES + ("PRITI BORA",)):
            with db.connect(self.db_path) as conn:
                conn.execute("UPDATE transactions SET pnl_bucket='opex'"
                             " WHERE canonical_key=?", (PARENT,))      # the re-bucketize
                opex = conn.execute("SELECT SUM(amount_minor) FROM transactions"
                                    " WHERE pnl_bucket='opex'").fetchone()[0]
            res, c = self._check()
        self.assertEqual(opex, -11500000 - 5500000)                    # the damage
        self.assertEqual(c.status, "RED")
        self.assertEqual([x.invariant for x in res.reds()], ["#15 bank splits"])

    def test_15_red_on_a_leg_left_behind_by_a_deleted_entry(self):
        with db.connect(self.db_path) as conn:
            self._pay(conn)
            bank_splits.apply(conn)
        with mock.patch.dict(bank_splits.SPLITS, {}, clear=True):
            res, c = self._check()
        self.assertEqual(c.status, "RED")
        self.assertEqual(len(c.deltas), 2)


class TestBucketizeWritesTheSplit(_Case):
    """The pass runs inside bin/finance-bucketize, which the cockpit runs after
    every import — so a split never waits on someone remembering it exists."""

    def _bucketize(self, apply):
        import importlib.machinery
        import importlib.util
        path = Path(__file__).resolve().parents[1] / "bin" / "finance-bucketize"
        loader = importlib.machinery.SourceFileLoader("fin_bucketize_split", str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        with mock.patch.object(mod.config, "DB_PATH", self.db_path):
            return mod.run(apply)

    def test_a_dry_run_counts_the_pair_and_writes_nothing(self):
        with db.connect(self.db_path) as conn:
            self._pay(conn, bucket=None)             # fresh from the importer
        res = self._bucketize(False)
        self.assertEqual(res["bank_split_rows"], 2)
        self.assertEqual(res["bank_split_problems"], 0)
        with db.connect(self.db_path) as conn:
            self.assertEqual(self._legs(conn), [])

    def test_apply_writes_the_pair_buckets_everything_and_the_doctor_is_green(self):
        with db.connect(self.db_path) as conn:
            self._pay(conn, bucket=None)
        res = self._bucketize(True)
        self.assertEqual(res["bank_split_rows"], 2)
        self.assertEqual(res["by_bucket"], {"owner_draw": 2, "opex": 1})
        with db.connect(self.db_path) as conn:
            self.assertEqual(self._by_bucket(conn),
                             {"opex": -5500000, "owner_draw": -6000000})
        self.assertTrue(doctor.run(self.db_path).green)
        again = self._bucketize(True)
        self.assertEqual((again["bank_split_rows"], again["changed"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
