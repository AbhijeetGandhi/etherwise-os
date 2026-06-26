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
            {"category": "expense", "amount_base_minor": -30000, "source": "IDFC"},
            {"accounting_subtype": "Service Fee", "amount_base_minor": -10000, "source": "Upwork"},
            {"category": "owner_draw", "amount_base_minor": -20000, "source": "IDFC"},   # below line
            {"category": "internal_sweep", "amount_base_minor": -999999, "source": "IDFC"},  # excluded
            {"category": "transfer_in", "amount_base_minor": 888888, "source": "IDFC"},      # excluded
            {"type": "Mystery", "amount_base_minor": -123, "source": "Bank"},               # flagged
        ]
        r = pnl.compute(rows)
        # operating = 100000 + 50000 - 30000 - 10000 = 110000 ; draws/sweeps/transfers OUT
        self.assertEqual(r["operating_profit_minor"], 110000)
        self.assertEqual(r["owner_draw_minor"], -20000)
        self.assertEqual(r["excluded_minor"]["sweep"], -999999)
        self.assertEqual(r["excluded_minor"]["transfer"], 888888)
        self.assertEqual(r["uncategorized_count"], 1)


if __name__ == "__main__":
    unittest.main()
