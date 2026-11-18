"""PayPal money from email (2026-11-18) — fully offline.

The mails below are REAL (service@intl.paypal.com to inbox 01; amounts, ids and
layout kept, the customer's address and email replaced), so the parser is
pinned to what PayPal sends. Pinned here:

  * "X paid for your invoice" is that payer's USD revenue keyed on PayPal's
    transaction id, with PayPal's fee-and-tax as a platform_fee line, and the
    stored buckets are what pnl.bucket() recomputes (doctor #10);
  * the invoice going out, the funds-hold notice, account and marketing mail
    are quiet; a money-sounding subject no rule knows is `unrecognised`; a
    payment mail missing its facts, or in another currency, is `incomplete`;
  * the bank credit that names PayPal is paired to the payment it settles and
    becomes transfer_in — one payment, the oldest of equal weeks, or the sum
    of the open ones; a card refund through PayPal is not a landing; a credit
    that fits nothing is left alone and reported;
  * finance doctor #17: N/A with no rows, RED while a landing is still income,
    RED when a credit cannot be told apart, RED when a payment is overdue and
    the bank has been read past it, GREEN with the in-flight list otherwise;
  * the bin reads inbox 01 read-only through an injected IMAP client and dates
    the mail in IST; the rail is registered, scheduled and rerunnable.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import shutil
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from core import config, db
from modules.finance import doctor, paypal_mail as pp, pnl

ROOT = Path(__file__).resolve().parents[1]
GAP = " \n \n \n \n "          # PayPal's HTML, flattened: every cell on its own line


def _body(*cells):
    return " \n\n \n\n \n " + GAP.join(cells) + " \n"


PAID = ("Brandi Jordan paid for your invoice # INV-2627-090", _body(
    "Etherwise, you received a $825.50 USD payment.", "Hi Etherwise,",
    "You received a $825.50 USD payment for your invoice",
    "Thanks for using PayPal. Payment details will appear in your invoice.",
    "The address Brandi Jordan provided hasn’t been confirmed.",
    "Transaction ID", "79R6551949983131M", "Invoice No", "INV-2627-090",
    "PayPal Seller Protection - Eligible", "View Your Invoice", "Customer", "Brandi Jordan",
    "customer@example.com 1 Example lane Rosharon, TX 77583 United States",
    "25.4 x AI & Automation Development (Nov 9 - Nov -15) \n $825.50 USD", "$32.50 USD",
    "SAC - 998314", "Total \n $825.50 USD", "Discount \n $0.00 USD", "Shipping \n $0.00 USD",
    "Invoice total \n $825.50 USD", "Amount paid \n $825.50 USD", "Amount due \n $0.00 USD",
    "Fee/tax collected by PayPal \n $43.21 USD", "Help & Contact | Security | Apps"))
SENT = ("We sent your invoice (INV-2627-090) for $825.50\xa0USD", _body(
    "Your invoice was sent", "Dear Etherwise,", "Your invoice is on the way!",
    "We sent your invoice to The Cradle company Global for $825.50 USD", "View Your Invoice"))
HOLD = ("Some of your funds won’t be available straight away", _body(
    "Hello, Abhijeet Ashok Gandhi", "The rest of this month’s funds won’t be available straight away",
    "We've noticed you’ve made $200.00 USD in sales this month."))
SERVICE = '"service@intl.paypal.com" <service@intl.paypal.com>'


class Parse(unittest.TestCase):
    def test_a_paid_invoice_is_the_payers_revenue_and_paypals_cut(self):
        verdict, p = pp.classify(PAID[0], SERVICE, PAID[1])
        self.assertEqual(verdict, "paid")
        self.assertEqual((p["payer"], p["invoice"], p["transaction_id"], p["amount_minor"], p["fee_minor"]),
                         ("Brandi Jordan", "INV-2627-090", "79R6551949983131M", 82550, 4321))
        self.assertEqual(p["line"], "25.4 x AI & Automation Development (Nov 9 - Nov -15)")
        rows = pp.ledger_rows(p, paid_date="2026-11-17")
        self.assertEqual([(r["canonical_key"], r["category"], r["pnl_bucket"], r["amount_minor"]) for r in rows],
                         [("paypal:79R6551949983131M", "income", "revenue", 82550),
                          ("paypal:FEE-79R6551949983131M", "fee", "platform_fee", -4321)])
        self.assertTrue(all(r["assignment_company"] == "Brandi Jordan" for r in rows))
        self.assertIn("INV-2627-090", rows[0]["description"])
        for r in rows:                                   # doctor #10: stored == recomputed
            self.assertEqual(pnl.bucket(dict(r, source=pp.SOURCE, amount_base_minor=r["amount_minor"])),
                             r["pnl_bucket"], r["canonical_key"])

    def test_account_mail_is_quiet_and_an_unknown_money_mail_is_loud(self):
        self.assertEqual(pp.classify(SENT[0], SERVICE, SENT[1])[0], "quiet")
        self.assertEqual(pp.classify(HOLD[0], SERVICE, HOLD[1])[0], "quiet")
        for subj, sender in (
                ("Get paid the way that works best for you", "PayPal <noreply@news.paypal.com>"),
                ("Give your customers more ways to pay.", "PayPal <noreply@news.paypal.com>"),
                ("Your account is ready to use", "PayPal <donotreply@paypal.com>"),
                ("You've added another way to log in to PayPal", SERVICE),
                ("Review your bank accounts", SERVICE)):
            self.assertEqual(pp.classify(subj, sender)[0], "quiet", subj)
        for subj in ("You've got a refund request", "Your money is on its way to your bank",
                     "A dispute was opened on a payment", "You received a payment of $50.00 USD"):
            self.assertEqual(pp.classify(subj, SERVICE)[0], "unrecognised", subj)

    def test_a_payment_mail_without_its_facts_or_in_another_currency_is_incomplete(self):
        self.assertEqual(pp.classify(PAID[0], SERVICE, "a body with none of the fields")[0], "incomplete")
        eur = PAID[1].replace(" USD", " EUR")
        verdict, p = pp.classify(PAID[0], SERVICE, eur)
        self.assertEqual((verdict, p["have"]["usd"]), ("incomplete", False))


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pp-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.conn = db.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pay(self, txn="79R6551949983131M", day="2026-11-17", gross=82550, fee=4321, inv="INV-2627-090"):
        p = {"payer": "Brandi Jordan", "invoice": inv, "transaction_id": txn, "amount_minor": gross,
             "fee_minor": fee, "currency": "USD", "line": None}
        return pp.commit(self.conn, pp.ledger_rows(p, paid_date=day))

    def _bank(self, key, day, usd_minor, category="income",
              desc="NEFT/CITIN26331187654/PAYPAL PAYMENTS PL-OPGSP COLL AC/CITI0000002/"):
        self.conn.execute(
            "INSERT INTO transactions (canonical_key, record_id, source, category, pnl_bucket,"
            " amount_minor, amount_base_minor, currency, description, creation_dt)"
            " VALUES (?,?,?,?,?,?,?,'INR',?,?)",
            (key, key, "IDFC", category, "revenue" if category == "income" else "opex",
             usd_minor * 89, usd_minor, desc, day))

    def _row(self, key):
        return dict(self.conn.execute(
            "SELECT category, pnl_bucket, related_transaction_id FROM transactions"
            " WHERE canonical_key=?", (key,)).fetchone())

    def _c17(self, today="2026-11-18"):
        self.conn.commit()
        res = doctor.run(self.db_path, today=today)
        return [c for c in res.checks if c.invariant.startswith("#17")][0]

    def test_commit_is_idempotent_and_keyed_on_paypals_id(self):
        self.assertEqual(self._pay()["inserted"], 2)
        self.assertEqual(self._pay(), {"inserted": 0, "already_present": 2})
        got = [tuple(r) for r in self.conn.execute(
            "SELECT canonical_key, pnl_bucket, amount_base_minor, fx_rate, assignment_company"
            " FROM transactions WHERE source=? ORDER BY canonical_key", (pp.SOURCE,))]
        self.assertEqual(got, [("paypal:79R6551949983131M", "revenue", 82550, "1", "Brandi Jordan"),
                               ("paypal:FEE-79R6551949983131M", "platform_fee", -4321, "1", "Brandi Jordan")])
        self.assertEqual(pp.payments(self.conn)[0]["net_minor"], 78229)

    def test_the_bank_credit_naming_paypal_becomes_a_transfer(self):
        self._pay()
        self._bank("bank:pp1", "2026-11-27", 75900)                 # 97% of the $782.29 net
        self._bank("bank:refund", "2026-11-20", 78000, desc="POS-VISA/PAYPAL *CALENDLYLLC/refund")
        self.assertEqual(len(pp.double_counted(self.conn)), 1)
        rep = pp.pair_landings(self.conn)
        self.assertEqual([(p["landing"], p["payments"], p["how"]) for p in rep["paired"]],
                         [("bank:pp1", ["paypal:79R6551949983131M"], "one payment")])
        self.assertEqual(self._row("bank:pp1"),
                         {"category": "transfer_in", "pnl_bucket": "transfer",
                          "related_transaction_id": "paypal:79R6551949983131M"})
        self.assertEqual(self._row("paypal:79R6551949983131M")["related_transaction_id"], "bank:pp1")
        self.assertEqual(self._row("bank:refund")["category"], "income")     # a card refund: not ours
        self.assertEqual(pp.double_counted(self.conn), [])
        self.assertEqual(pp.in_flight(self.conn, "2026-12-01"), [])
        rep2 = pp.pair_landings(self.conn)                                   # idempotent
        self.assertEqual((rep2["paired"], len(rep2["already"])), ([], 1))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM audit_log WHERE actor="
                                           "'agent:finance-paypal-mail'").fetchone()[0], 1)

    def test_equal_weeks_pair_oldest_first_and_a_sweep_pairs_the_open_balance(self):
        self._pay("AAAAAAAAAAAA1", "2026-11-17", 65000, 3414, "INV-1")
        self._pay("AAAAAAAAAAAA2", "2026-11-24", 65000, 3414, "INV-2")
        self._bank("bank:a", "2026-11-28", 59800)                    # either week: the older
        rep = pp.pair_landings(self.conn)
        self.assertEqual(rep["paired"][0]["payments"], ["paypal:AAAAAAAAAAAA1"])
        self._pay("AAAAAAAAAAAA3", "2026-12-01", 32500, 1722, "INV-3")
        self._bank("bank:b", "2026-12-08", 89700)                    # weeks 2 + 3 swept together
        rep = pp.pair_landings(self.conn)
        self.assertEqual((rep["paired"][0]["payments"], rep["paired"][0]["how"]),
                         (["paypal:AAAAAAAAAAAA2", "paypal:AAAAAAAAAAAA3"], "the open balance"))
        self.assertEqual(pp.in_flight(self.conn, "2026-12-09"), [])

    def test_a_credit_that_fits_nothing_is_left_alone_and_reported(self):
        self._pay()
        self._bank("bank:odd", "2026-11-27", 40000)                  # half the net: not it
        self._bank("bank:late", "2027-02-01", 75900)                 # right amount, 76 days on
        rep = pp.pair_landings(self.conn)
        self.assertEqual(rep["paired"], [])
        self.assertEqual({u["landing"] for u in rep["unexplained"]}, {"bank:odd", "bank:late"})
        self.assertEqual(self._row("bank:odd")["category"], "income")
        self.assertEqual([u["landing"] for u in pp.ambiguous(self.conn)], ["bank:odd"])
        # his word settles it
        pp.LANDINGS_BY_HAND["bank:odd"] = ("paypal:79R6551949983131M",)
        try:
            rep = pp.pair_landings(self.conn)
        finally:
            pp.LANDINGS_BY_HAND.clear()
        self.assertEqual((rep["paired"][0]["landing"], rep["paired"][0]["how"]), ("bank:odd", "his word"))

    def test_doctor_17(self):
        self.assertEqual(self._c17().status, "N/A")                  # no PayPal rows
        self._pay()
        c = self._c17()
        self.assertEqual(c.status, "GREEN")
        self.assertIn("1 payment(s) not in the bank ledger yet", c.detail)
        self.assertEqual((c.deltas[0]["payer"], c.deltas[0]["net_minor"], c.deltas[0]["expected_by"]),
                         ("Brandi Jordan", 78229, "2027-01-01"))
        self._bank("bank:pp1", "2026-11-27", 75900)
        c = self._c17()
        self.assertEqual(c.status, "RED")                            # counted twice
        self.assertIn("bucketize", c.fix)
        pp.pair_landings(self.conn)
        self.assertEqual(self._c17().status, "GREEN")

    def test_doctor_17_is_red_when_a_credit_cannot_be_told_or_a_payment_is_overdue(self):
        self._pay()
        self._bank("bank:odd", "2026-11-27", 40000)
        c = self._c17()
        self.assertEqual(c.status, "RED")
        self.assertIn("LANDINGS_BY_HAND", c.fix)
        self.conn.execute("DELETE FROM transactions WHERE canonical_key='bank:odd'")
        # the bank has been read to 5 Jan with no credit naming PayPal: the landing
        # is in the statement under another narration, as income
        self._bank("bank:other", "2027-01-05", 1000, desc="UPI/someone")
        c = self._c17(today="2027-01-06")
        self.assertEqual(c.status, "RED")
        self.assertIn("another narration", c.detail)
        self.assertNotRegex(c.fix, r"\d")                            # no figure on a blocked page


class TheBin(unittest.TestCase):
    def test_fetch_reads_inbox_01_read_only_and_dates_in_ist(self):
        loader = importlib.machinery.SourceFileLoader("fpp", str(ROOT / "bin" / "finance-paypal-mail"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("fpp", loader))
        loader.exec_module(mod)
        msg = EmailMessage()
        msg["Subject"], msg["From"] = PAID[0], SERVICE
        msg["Date"] = "Tue, 17 Nov 2026 11:46:20 -0800"              # 18 Nov 01:16 IST
        msg.set_content(PAID[1])
        calls = []

        class Client:
            def select(self, box, readonly=False):
                calls.append(("select", box, readonly))
                return "OK", [b"1"]

            def uid(self, cmd, *args):
                calls.append((cmd,) + args[:3])
                if cmd == "SEARCH":
                    return "OK", [b"7 7"]
                return "OK", [(b"7 (BODY[] {1}", msg.as_bytes())]

            def logout(self):
                calls.append(("logout",))

        seen = {}

        def connect(host, inbox, pw):
            seen["inbox"] = inbox
            return Client()
        mails = mod.fetch(45, _connect=connect, _password=lambda inbox: "x")
        self.assertEqual(seen["inbox"], config.PAYMENT_MAIL_INBOX)
        self.assertEqual(calls[0], ("select", config.PAYMENT_MAIL_MAILBOX, True))
        self.assertEqual(calls[1][:4], ("SEARCH", None, "FROM", "paypal.com"))
        self.assertEqual(calls[2][2], "(BODY.PEEK[])")
        self.assertEqual(len(mails), 1)                              # the duplicate uid collapsed
        subj, body, day, sender = mails[0]
        self.assertEqual((subj, day), (PAID[0], "2026-11-18"))
        self.assertEqual(pp.classify(subj, sender, body)[1]["transaction_id"], "79R6551949983131M")


class Registry(unittest.TestCase):
    def test_the_rail_is_registered_scheduled_and_rerunnable(self):
        from modules.cockpit import actions
        src = [s for s in config.SOURCES if s["key"] == "paypal_mail"][0]
        self.assertEqual((src["task_name"], src["critical"], src["backs"], src["catch_up"]),
                         ("finance_paypal_mail", True, "money", True))
        plist = ROOT / "launchd" / f"{src['launchd_label']}.plist"
        self.assertIn("bin/finance-paypal-mail", plist.read_text())
        self.assertEqual(actions.RERUN_JOBS["finance_paypal_mail"], src["launchd_label"])


if __name__ == "__main__":
    unittest.main()
