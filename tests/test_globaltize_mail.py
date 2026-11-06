"""Globaltize Workforce money from email (2026-11-06) — fully offline.

The mails below are REAL (info@app.globaltize.com to contact@, amounts kept),
so the parser is pinned to what the platform sends. Pinned here:

  * a "You've been paid" is USD income keyed on the platform's reference — two
    payments of one amount on one day (29 Oct) stay two;
  * a withdrawal is a transfer out of the wallet, its fee a platform_fee line;
  * the LANDING of a withdrawal — the IDFC RDA credit (at the processor's rate,
    within 3% under / 0.5% over, 0-10 days on) or the Wise "Whop Inc /
    GLOBALTIZE" arrival (net of the fee) — is paired and becomes transfer_in,
    so the money counts once; two candidates pair nothing; a landing not seen
    yet is "in flight";
  * finance doctor #16 is RED while a landing is still income beside its
    withdrawal, GREEN once paired, N/A with no wallet rows;
  * the Wise rail itself files a Whop/GLOBALTIZE arrival as transfer_in from
    now on, and a Whop arrival without that reference stays income;
  * the bin's fetch reads contact@ through injected Gmail calls and keys the
    mail date in IST.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core import db
from modules.finance import doctor, globaltize_mail as gm, wise_mail

ROOT = Path(__file__).resolve().parents[1]

PAID_1 = ("You've been paid $1,117.50 by Globaltize",
          "From: Globaltize Workforce <info@app.globaltize.com>\n"
          "Subject: You've been paid $1,117.50 by Globaltize\n\n"
          "You've Been Paid\n\nHi Abhijeet, Globaltize paid you $1,117.50 for Week 10/19-10/25.\n\n"
          "You Receive $1,117.50 : Pending\n\nAmount: $1,117.50\n\nPaid On: October 29, 2026\n\n"
          "Reference: INV-2026-0002\n\nIt's in your balance as pending and becomes available"
          " to withdraw in 2 business days.\n")
PAID_2 = ("You've been paid $1,117.50 by Globaltize",
          "Hi Abhijeet, Globaltize paid you $1,117.50 for 2nd part of Wise invoice.  You Receive"
          " $1,117.50 : Pending  Amount: $1,117.50  Paid On: October 29, 2026  Reference:"
          " PAY-EFE90CE8  It's in your balance as pending")
PAID_3 = ("You've been paid $2,127.00 by Globaltize",
          "Hi Abhijeet, Globaltize paid you $2,127.00 for INV-2627-086 from Wise.\n\nAmount:"
          " $2,127.00\n\nPaid On: November 4, 2026\n\nReference: PAY-33D1D00E\n")
WD_BANK = ("Your withdrawal of $2,100.00 is being processed",
           "Hi Abhijeet, we're processing your withdrawal to Etherwise.\n\nWithdrawal Amount"
           " $2,100.00 : Requested\n\nSent To: Etherwise\n\nTransfer Fee: No fee\n\nExpected By:"
           " Not known yet\n\nReference: cofr_6fuDIVKYxtNL3\n")
WD_WISE = ("Your withdrawal of $135.00 is being processed",
           "Hi Abhijeet, we're processing your withdrawal to Wise.  Withdrawal Amount $135.00 :"
           " Requested  Sent To: Wise  Transfer Fee: $2.50  Expected By: Not known yet"
           "  Reference: cofr_OMLVeEvWcKinW  View Withdrawal")


class Parse(unittest.TestCase):
    def test_a_payment_is_usd_income_keyed_on_the_platform_reference(self):
        p = gm.parse(*PAID_1)
        self.assertEqual((p["kind"], p["amount_minor"], p["paid_date"], p["reference"], p["memo"]),
                         ("paid", 111750, "2026-10-29", "INV-2026-0002", "Week 10/19-10/25"))
        r = gm.income_row(p)
        self.assertEqual((r["canonical_key"], r["category"], r["pnl_bucket"], r["assignment_company"]),
                         ("globaltize:PAY-INV-2026-0002", "income", "revenue", "Globaltize LLC"))
        self.assertEqual(r["creation_dt"], "2026-10-29")
        # the two $1,117.50 of 29 Oct are two payments, not one notified twice
        self.assertNotEqual(gm.income_row(gm.parse(*PAID_2))["canonical_key"], r["canonical_key"])
        self.assertEqual(gm.parse(*PAID_3)["memo"], "INV-2627-086 from Wise")

    def test_a_withdrawal_is_a_transfer_out_with_its_fee(self):
        w = gm.parse(*WD_WISE)
        self.assertEqual((w["kind"], w["amount_minor"], w["fee_minor"], w["destination"], w["reference"]),
                         ("withdrawal", 13500, 250, "Wise", "cofr_OMLVeEvWcKinW"))
        rows = gm.withdrawal_rows(w, mail_date="2026-10-30")
        self.assertEqual([(r["canonical_key"], r["category"], r["pnl_bucket"], r["amount_minor"]) for r in rows],
                         [("globaltize:WD-cofr_OMLVeEvWcKinW", "transfer_out", "transfer", -13500),
                          ("globaltize:FEE-WD-cofr_OMLVeEvWcKinW", "fee", "platform_fee", -250)])
        # the stored bucket must be what pnl.bucket() recomputes (doctor #10)
        from modules.finance import pnl
        for r in rows:
            self.assertEqual(pnl.bucket(dict(r, source=gm.SOURCE, amount_base_minor=r["amount_minor"])),
                             r["pnl_bucket"], r["canonical_key"])
        b = gm.parse(*WD_BANK)
        self.assertEqual((b["fee_minor"], b["destination"]), (0, "Etherwise"))
        self.assertEqual(len(gm.withdrawal_rows(b, mail_date="2026-10-30")), 1)

    def test_other_platform_mail_is_not_money_and_a_changed_template_is_loud(self):
        self.assertIsNone(gm.parse("Globaltize invited you to Globaltize Workforce", "Accept"))
        self.assertIsNone(gm.parse("New device signed in to your Globaltize Workforce account", ""))
        p = gm.parse("You've been paid $50.00 by Globaltize", "a body with none of the fields")
        self.assertTrue(p["incomplete"])


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gm-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.conn = db.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rows(self):
        rows = [gm.income_row(gm.parse(*m)) for m in (PAID_1, PAID_2, PAID_3)]
        rows += gm.withdrawal_rows(gm.parse(*WD_BANK), mail_date="2026-10-30")
        rows += gm.withdrawal_rows(gm.parse(*WD_WISE), mail_date="2026-10-30")
        return rows

    def _bank(self, key, day, inr_minor, usd_minor, category="income", desc=None):
        self.conn.execute(
            "INSERT INTO transactions (canonical_key, record_id, source, category, pnl_bucket,"
            " amount_minor, amount_base_minor, currency, description, creation_dt)"
            " VALUES (?,?,?,?,?,?,?,'INR',?,?)",
            (key, key, "IDFC", category, "revenue" if category == "income" else "transfer",
             inr_minor, usd_minor,
             desc or "IMPS/630389665647/TRANSFERTOMOBIL/YESB0000001/1057/RDAremittanceCrtoKY", day))

    def _wise(self, key, day, minor, payer="Whop Inc", ref="GLOBALTIZE"):
        self.conn.execute(
            "INSERT INTO transactions (canonical_key, record_id, source, category, pnl_bucket,"
            " amount_minor, amount_base_minor, currency, description, creation_dt)"
            " VALUES (?,?,?,?,?,?,?,'USD',?,?)",
            (key, key, "Wise-mail", "income", "revenue", minor, minor,
             f"Received money from {payer} with reference {ref}", day))

    def test_commit_is_idempotent_and_books_revenue_to_the_client(self):
        first = gm.commit(self.conn, self._rows())
        second = gm.commit(self.conn, self._rows())
        self.assertEqual((first["inserted"], second["inserted"], second["already_present"]), (6, 0, 6))
        rev = self.conn.execute("SELECT SUM(amount_minor) FROM transactions WHERE source=?"
                                " AND pnl_bucket='revenue' AND assignment_company='Globaltize LLC'",
                                (gm.SOURCE,)).fetchone()[0]
        self.assertEqual(rev, 111750 * 2 + 212700)
        self.assertEqual(tuple(self.conn.execute("SELECT amount_base_minor, fx_rate FROM transactions"
                                                 " WHERE canonical_key='globaltize:PAY-PAY-33D1D00E'").fetchone()),
                         (212700, "1"))

    def test_the_bank_landing_becomes_a_transfer_paired_to_its_withdrawal(self):
        gm.commit(self.conn, self._rows())
        # the real landing: Rs 1,96,863.08 on 30 Oct = $2,073.36 at the ECB pin (98.7% of $2,100)
        self._bank("bank:c6756ef0261e5c0a", "2026-10-30", 19686308, 207336)
        self.assertEqual(len(gm.double_counted(self.conn)), 1)          # before: counted twice
        rep = gm.pair_landings(self.conn)
        self.assertEqual([p["landing"] for p in rep["paired"]], ["bank:c6756ef0261e5c0a"])
        row = self.conn.execute("SELECT category, pnl_bucket, assignment_company, related_transaction_id"
                                " FROM transactions WHERE canonical_key='bank:c6756ef0261e5c0a'").fetchone()
        self.assertEqual(tuple(row), ("transfer_in", "transfer", "Globaltize LLC",
                                      "globaltize:WD-cofr_6fuDIVKYxtNL3"))
        self.assertEqual(gm.double_counted(self.conn), [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM audit_log WHERE entity='transactions'"
                                           " AND field='category'").fetchone()[0], 1)
        # the Wise leg is still in flight; a second pass changes nothing
        self.assertEqual([f["to"] for f in rep["in_flight"]], ["Wise"])
        rep2 = gm.pair_landings(self.conn)
        self.assertEqual((rep2["paired"], len(rep2["already"])), ([], 1))

    def test_the_wise_arrival_pairs_on_the_net_amount(self):
        gm.commit(self.conn, self._rows())
        self._wise("wise:MAILREF-globaltize-2026-11-02-13250", "2026-11-02", 13250)
        self._wise("wise:MAILREF-other-2026-11-02-13250", "2026-11-02", 13250, payer="Whop Inc", ref="SOMEONE")
        rep = gm.pair_landings(self.conn)
        self.assertEqual([p["landing"] for p in rep["paired"]], ["wise:MAILREF-globaltize-2026-11-02-13250"])
        other = self.conn.execute("SELECT category FROM transactions WHERE canonical_key="
                                  "'wise:MAILREF-other-2026-11-02-13250'").fetchone()[0]
        self.assertEqual(other, "income")                                # not ours to touch

    def test_two_candidates_pair_nothing_and_the_rate_band_holds(self):
        gm.commit(self.conn, self._rows())
        self._bank("bank:a", "2026-10-30", 19686308, 207336)
        self._bank("bank:b", "2026-11-02", 19700000, 207500)
        rep = gm.pair_landings(self.conn)
        self.assertEqual(rep["paired"], [])
        self.assertEqual(len(rep["ambiguous"]), 1)
        self.conn.execute("DELETE FROM transactions WHERE canonical_key='bank:b'")
        # a credit 4% under the withdrawal is not its landing; 12 days later is not either
        self._bank("bank:c", "2026-10-31", 19000000, 201600)
        self._bank("bank:d", "2026-11-12", 19686308, 207336)
        rep = gm.pair_landings(self.conn)
        self.assertEqual([p["landing"] for p in rep["paired"]], ["bank:a"])
        self.assertEqual(self.conn.execute("SELECT category FROM transactions WHERE canonical_key='bank:c'")
                         .fetchone()[0], "income")

    def test_doctor_16_is_red_while_a_landing_is_still_income(self):
        self.assertEqual(self._c16().status, "N/A")                       # no wallet rows
        gm.commit(self.conn, self._rows())
        self._bank("bank:c6756ef0261e5c0a", "2026-10-30", 19686308, 207336)
        self.conn.commit()
        c = self._c16()
        self.assertEqual(c.status, "RED")
        self.assertIn("bucketize", c.fix)
        gm.pair_landings(self.conn)
        self.conn.commit()
        c = self._c16(today="2026-11-06")
        self.assertEqual(c.status, "GREEN")
        self.assertIn("1 withdrawal(s) not landed", c.detail)           # the Wise leg, in flight
        self.assertEqual(c.deltas[0]["to"], "Wise")

    def _c16(self, today=None):
        self.conn.commit()
        res = doctor.run(self.db_path, today=today)
        return [c for c in res.checks if c.invariant.startswith("#16")][0]


class WiseSide(unittest.TestCase):
    def test_a_whop_globaltize_arrival_is_a_transfer_not_income(self):
        a = wise_mail.parse_arrival("Whop Inc just paid you",
                                    "You received 132.50 USD from Whop Inc\nReference:\nGLOBALTIZE\n")
        self.assertTrue(wise_mail.is_wallet_settlement(a))
        r = wise_mail.income_row(a, paid_date="2026-11-02")
        self.assertEqual((r["category"], r["pnl_bucket"]), ("transfer_in", "transfer"))
        # the same payer without the reference is somebody's money — income, unnamed
        b = wise_mail.parse_arrival("Whop Inc just paid you",
                                    "You received 40.00 USD from Whop Inc\nReference:\nCOURSE-77\n")
        self.assertFalse(wise_mail.is_wallet_settlement(b))
        self.assertEqual(wise_mail.income_row(b, paid_date="2026-11-02")["category"], "income")

    def test_existing_income_sees_a_transfer_twin(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE transactions (canonical_key TEXT, source TEXT, category TEXT,"
                     " amount_minor INTEGER, currency TEXT, description TEXT, creation_dt TEXT)")
        conn.execute("INSERT INTO transactions VALUES ('k','Wise-mail','transfer_in',13250,'USD',"
                     "'Received money from Whop Inc with reference GLOBALTIZE','2026-11-02')")
        a = {"payer": "Whop Inc", "reference": "GLOBALTIZE", "amount_minor": 13250, "currency": "USD"}
        self.assertIsNotNone(wise_mail.existing_income(conn, a, "2026-11-02"))


class TheBin(unittest.TestCase):
    def test_fetch_reads_contact_through_the_gmail_api_and_dates_in_ist(self):
        loader = importlib.machinery.SourceFileLoader("fgm", str(ROOT / "bin" / "finance-globaltize-mail"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("fgm", loader))
        loader.exec_module(mod)
        calls = []

        def _list(tok, q, page_token=None):
            calls.append(q)
            return {"messages": [{"id": "m1"}, {"id": "m2"}]}

        def _get(tok, mid):
            subj, body = PAID_3 if mid == "m1" else WD_BANK
            return {"id": mid, "internalDate": "1793797076000",        # 2026-11-04 12:57 UTC
                    "payload": {"headers": [{"name": "From", "value": "Globaltize <info@app.globaltize.com>"},
                                            {"name": "Subject", "value": subj},
                                            {"name": "Date", "value": "Wed, 04 Nov 2026 12:57:56 +0000"}],
                                "mimeType": "text/plain",
                                "body": {"data": __import__("base64").urlsafe_b64encode(body.encode()).decode()}}}
        mails = mod.fetch(45, _list=_list, _get=_get, _token=lambda: "tok")
        self.assertEqual(calls, ["from:info@app.globaltize.com newer_than:45d"])
        self.assertEqual([(m[0], m[2]) for m in mails],
                         [(PAID_3[0], "2026-11-04"), (WD_BANK[0], "2026-11-04")])
        self.assertEqual(gm.parse(mails[0][0], mails[0][1])["reference"], "PAY-33D1D00E")


class Registry(unittest.TestCase):
    def test_the_rail_is_registered_scheduled_and_rerunnable(self):
        from core import config
        from modules.cockpit import actions
        src = [s for s in config.SOURCES if s["key"] == "globaltize_mail"][0]
        self.assertEqual((src["task_name"], src["critical"], src["backs"], src["catch_up"]),
                         ("finance_globaltize_mail", True, "money", True))
        self.assertTrue((ROOT / "launchd" / f"{src['launchd_label']}.plist").exists())
        self.assertEqual(actions.RERUN_JOBS["finance_globaltize_mail"], src["launchd_label"])


if __name__ == "__main__":
    unittest.main()
