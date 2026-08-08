"""Finance payment-mail rail tests (no network). The rail reads Wise/Stripe
NOTIFICATIONS read-only and emits PROVISIONAL payment events that NEVER enter the
real ledger. Fixtures cover parsing, attribution, idempotency, reconcile (and the
cardinal no-double-count guarantee), staleness (email ahead of ledger), the
mapping queue, AR overdue math, and the read-only transport assertion.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from core import config, db
from modules.finance import payment_mail_rail as rail

# ── representative notification bodies (documented format; live-calibrated later) ──
WISE_SUBJECT = "Adélye Ltd. just paid you"
WISE_BODY = ("Hi Abhijeet,\n\nAdélye Ltd. just paid you.\n\n"
             "You received USD 3,500.00 on 7 August 2026.\n"
             "The money is in your USD balance.\n\nWise")

WISE_BODY_PAYER_IN_BODY = ("You've been paid!\n\nYou received a payment from "
                           "Consulting Systems B.V. of USD 2,000.00.\nWise")
WISE_SUBJECT_NO_PAYER = "You've been paid"

STRIPE_SUBJECT = "Receipt from Beglaubigt.de [#1234-5678]"
STRIPE_BODY = ("Receipt from Beglaubigt.de\n\nAmount paid: $420.00 USD\n"
               "Paid August 5, 2026\nThanks for your business.")


class ParseWise(unittest.TestCase):
    def test_parses_amount_currency_payer(self):
        p = rail.parse_wise(WISE_SUBJECT, WISE_BODY, native_id="m1",
                            email_date="2026-08-07T09:00:00+00:00")
        self.assertEqual(p["provider"], "wise")
        self.assertEqual(p["amount_minor"], 350000)          # $3,500.00 -> cents
        self.assertEqual(p["currency"], "USD")
        self.assertEqual(p["payer"], "Adélye Ltd.")
        self.assertEqual(p["paid_date"], "2026-08-07")       # from the email Date header
        self.assertEqual(p["canonical_key"], "wise-email:m1")

    def test_payer_from_body_when_subject_has_none(self):
        p = rail.parse_wise(WISE_SUBJECT_NO_PAYER, WISE_BODY_PAYER_IN_BODY,
                            native_id="m2", email_date="2026-07-15T00:00:00+00:00")
        self.assertEqual(p["amount_minor"], 200000)
        self.assertEqual(p["payer"], "Consulting Systems B.V.")

    def test_non_payment_subject_returns_none(self):
        self.assertIsNone(rail.parse_wise("Your Wise statement is ready",
                                          "nothing here", native_id="m3"))

    def test_no_amount_returns_none(self):
        self.assertIsNone(rail.parse_wise(WISE_SUBJECT, "no money mentioned",
                                          native_id="m4"))


class ParseStripe(unittest.TestCase):
    def test_parses_stripe_receipt(self):
        p = rail.parse_stripe(STRIPE_SUBJECT, STRIPE_BODY, native_id="s1",
                              email_date="2026-08-05T12:00:00+00:00")
        self.assertEqual(p["provider"], "stripe")
        self.assertEqual(p["amount_minor"], 42000)
        self.assertEqual(p["currency"], "USD")
        self.assertEqual(p["payer"], "Beglaubigt.de")
        self.assertEqual(p["canonical_key"], "stripe-email:s1")


class SenderClassify(unittest.TestCase):
    def test_provider_for_allowlist(self):
        self.assertEqual(rail.provider_for("noreply@wise.com"), "wise")
        self.assertEqual(rail.provider_for("payments@e.transferwise.com"), "wise")
        self.assertEqual(rail.provider_for("receipts@stripe.com"), "stripe")

    def test_provider_for_rejects_others(self):
        self.assertIsNone(rail.provider_for("someone@gmail.com"))
        self.assertIsNone(rail.provider_for(""))


class Attribution(unittest.TestCase):
    def test_known_billing_entities(self):
        self.assertEqual(rail.attribute("Consulting Systems B.V."), "hermon")
        self.assertEqual(rail.attribute("Adélye Ltd."), "adelye")
        self.assertEqual(rail.attribute("Horizons ABA MIdCo LLC"), "horizons-aba")
        self.assertEqual(rail.attribute("CMF Publishing"), "cmf-publishing")
        self.assertEqual(rail.attribute("Beglaubigt.de"), "beglaubigt")

    def test_suffix_stripped_match(self):
        # "Adelye Limited" -> suffix strip -> "adelye" alias
        self.assertEqual(rail.attribute("Adelye Limited"), "adelye")

    def test_unknown_payer_is_none_never_guessed(self):
        self.assertIsNone(rail.attribute("Some Random GmbH"))
        self.assertIsNone(rail.attribute(None))


class _DBCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dbp = self.tmp / "t.db"
        db.migrate(self.dbp)
        self.conn = db.connect(self.dbp)

    def tearDown(self):
        self.conn.close()

    def _prov(self, ck, payer, amount, cur="USD", date_="2026-08-07",
              provider="wise", native="n"):
        return {"canonical_key": ck, "provider": provider, "payer": payer,
                "amount_minor": amount, "currency": cur, "paid_date": date_,
                "provider_ref": None, "native_id": native}

    def _ledger_income(self, ck, base_minor, date_, company="Adelye"):
        self.conn.execute(
            "INSERT INTO transactions (record_id, canonical_key, pnl_bucket,"
            " amount_base_minor, currency, creation_dt, assignment_company, source)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (ck, ck, "revenue", base_minor, "USD", date_, company, "Wise"))


class Idempotency(_DBCase):
    def test_upsert_is_a_noop_on_reinsert(self):
        p = self._prov("wise-email:a", "Adélye Ltd.", 350000)
        self.assertTrue(rail.upsert_provisional(self.conn, p))
        self.assertFalse(rail.upsert_provisional(self.conn, p))     # second time no-op
        n = self.conn.execute("SELECT COUNT(*) c FROM provisional_payments").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_upsert_attributes_client_id(self):
        rail.upsert_provisional(self.conn, self._prov("wise-email:b", "Adélye Ltd.", 350000))
        row = self.conn.execute("SELECT client_id FROM provisional_payments"
                                " WHERE canonical_key='wise-email:b'").fetchone()
        self.assertEqual(row["client_id"], "adelye")


class Reconcile(_DBCase):
    def test_matches_and_flips_confirmed(self):
        rail.upsert_provisional(self.conn, self._prov("wise-email:c", "Adélye Ltd.", 350000,
                                                      date_="2026-08-07"))
        # the real CSV lands a few days later, same amount, near date
        self._ledger_income("wise:TW-1", 350000, "2026-08-09")
        rec = rail.reconcile(self.conn, now=datetime(2026, 8, 12, tzinfo=config.TZ))
        self.assertEqual(rec["confirmed"], 1)
        row = self.conn.execute("SELECT state, matched_canonical FROM"
                                " provisional_payments WHERE canonical_key='wise-email:c'").fetchone()
        self.assertEqual(row["state"], "confirmed")
        self.assertEqual(row["matched_canonical"], "wise:TW-1")

    def test_provisional_never_enters_transactions(self):
        # THE cardinal guarantee: the provisional store is invisible to the ledger
        rail.upsert_provisional(self.conn, self._prov("wise-email:d", "Adélye Ltd.", 350000))
        rail.reconcile(self.conn, now=datetime(2026, 8, 12, tzinfo=config.TZ))
        n = self.conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"]
        self.assertEqual(n, 0)                              # nothing written to the real ledger

    def test_unmatched_past_window_is_surfaced(self):
        rail.upsert_provisional(self.conn, self._prov("wise-email:e", "Adélye Ltd.", 350000,
                                                      date_="2026-07-01"))
        rec = rail.reconcile(self.conn, now=datetime(2026, 8, 12, tzinfo=config.TZ))
        self.assertEqual(rec["confirmed"], 0)
        self.assertEqual(len(rec["stale_unmatched"]), 1)
        self.assertEqual(rec["stale_unmatched"][0]["client_id"], "adelye")

    def test_reconcile_idempotent(self):
        rail.upsert_provisional(self.conn, self._prov("wise-email:f", "Adélye Ltd.", 350000,
                                                      date_="2026-08-07"))
        self._ledger_income("wise:TW-2", 350000, "2026-08-08")
        r1 = rail.reconcile(self.conn, now=datetime(2026, 8, 12, tzinfo=config.TZ))
        r2 = rail.reconcile(self.conn, now=datetime(2026, 8, 12, tzinfo=config.TZ))
        self.assertEqual(r1["confirmed"], 1)
        self.assertEqual(r2["confirmed"], 0)                # already confirmed


class Staleness(_DBCase):
    def test_triggers_when_email_ahead_of_ledger(self):
        self._ledger_income("wise:old", 100000, "2026-07-15")     # ledger newest = Jul 15
        rail.upsert_provisional(self.conn, self._prov("wise-email:g", "Adélye Ltd.", 350000,
                                                      date_="2026-08-07"))
        s = rail.staleness(self.conn)
        self.assertTrue(s["stale"])
        self.assertEqual(s["count"], 1)
        self.assertEqual(s["most_recent"]["client_id"], "adelye")
        self.assertEqual(s["most_recent"]["paid_date"], "2026-08-07")
        self.assertEqual(s["floor_usd"], "3500.00")

    def test_silent_when_ledger_caught_up(self):
        self._ledger_income("wise:new", 350000, "2026-08-09")     # ledger already ahead
        rail.upsert_provisional(self.conn, self._prov("wise-email:h", "Adélye Ltd.", 350000,
                                                      date_="2026-08-07"))
        s = rail.staleness(self.conn)
        self.assertFalse(s["stale"])

    def test_confirmed_rows_do_not_count_as_stale(self):
        self._ledger_income("wise:x", 350000, "2026-08-09")
        rail.upsert_provisional(self.conn, self._prov("wise-email:i", "Adélye Ltd.", 350000,
                                                      date_="2026-08-07"))
        rail.reconcile(self.conn, now=datetime(2026, 8, 12, tzinfo=config.TZ))
        s = rail.staleness(self.conn)
        self.assertFalse(s["stale"])
        self.assertEqual(s["count"], 0)


class MappingQueue(_DBCase):
    def test_unmapped_payer_surfaces(self):
        rail.upsert_provisional(self.conn, self._prov("wise-email:j", "Some Random GmbH", 500000))
        q = rail.mapping_queue(self.conn)
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["payer"], "Some Random GmbH")


class AROverdue(unittest.TestCase):
    def test_overdue_when_no_email_and_past_cadence(self):
        # Hermon biweekly, anchor Oct-15-2025; last paid Jul 15, today Aug 9 -> overdue
        r = rail.overdue_for("2026-07-15", "biweekly", "2025-10-15",
                             date(2026, 8, 9), has_email_after=False)
        self.assertIsNotNone(r)
        self.assertGreater(r["overdue_days"], 0)

    def test_not_overdue_when_email_signal_exists(self):
        # a provisional email payment means the client HAS paid -> never flagged
        r = rail.overdue_for("2026-07-15", "biweekly", "2025-10-15",
                             date(2026, 8, 9), has_email_after=True)
        self.assertIsNone(r)

    def test_not_overdue_within_grace(self):
        # last paid Aug 1, next biweekly Aug 15 style -> not yet due on Aug 9
        r = rail.overdue_for("2026-08-01", "biweekly", "2026-08-01",
                             date(2026, 8, 9), has_email_after=False)
        self.assertIsNone(r)


# ── read-only transport: a fake IMAP whose surface OMITS every mutating verb ──
class FakeIMAP:
    """SEARCH/FETCH only. Any STORE/EXPUNGE/APPEND/COPY attempt is an AttributeError."""
    def __init__(self, messages):
        self.messages = messages            # {uid: {"headers": bytes, "body": bytes}}
        self.selected_readonly = None

    def select(self, mailbox, readonly=False):
        self.selected_readonly = readonly
        return ("OK", [b"1"])

    def status(self, mailbox, spec):
        return ("OK", [b"INBOX (UIDVALIDITY 42)"])

    def uid(self, command, *args):
        if command == "SEARCH":
            return ("OK", [" ".join(str(u) for u in self.messages).encode()])
        if command == "FETCH":
            uid = int(args[0])
            spec = args[1]
            part = "headers" if "HEADER" in spec else "body"
            return ("OK", [(b"1 (UID)", self.messages[uid][part])])
        raise AssertionError(f"unexpected verb {command}")

    def logout(self):
        return ("BYE", [b""])


class Transport(unittest.TestCase):
    def _msg(self, frm, subject, body, msgid, date_="Fri, 07 Aug 2026 09:00:00 +0000"):
        hdr = (f"From: {frm}\r\nSubject: {subject}\r\nDate: {date_}\r\n"
               f"Message-ID: <{msgid}>\r\n\r\n").encode()
        full = hdr + (f"Content-Type: text/plain\r\n\r\n{body}").encode()
        return {"headers": hdr, "body": full}

    def test_collect_is_readonly_and_filters_to_finance_senders(self):
        msgs = {
            1: self._msg("Wise <noreply@wise.com>", WISE_SUBJECT, WISE_BODY, "m1"),
            2: self._msg("Client <a@gmail.com>", "Hi there", "just a chat", "m2"),
        }
        fake = FakeIMAP(msgs)
        events, cursor = rail.collect(
            "abhijeetgandhi01@gmail.com", None, max_items=50, first_fetch_days=60,
            conn_factory=lambda *a, **k: fake, pw_fn=lambda inbox: "app-pw")
        # opened read-only
        self.assertTrue(fake.selected_readonly)
        # only the Wise sender parsed; the gmail chat ignored entirely
        parsed = [e for e in events if not e.get("_unparsed")]
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["provider"], "wise")
        self.assertEqual(parsed[0]["amount_minor"], 350000)
        self.assertTrue(cursor.startswith("42:"))

    def test_allowlisted_but_unparseable_becomes_unparsed_not_dropped(self):
        msgs = {1: self._msg("Wise <noreply@wise.com>",
                             "Your monthly statement", "no payment here", "m9")}
        fake = FakeIMAP(msgs)
        events, _ = rail.collect(
            "abhijeetgandhi01@gmail.com", None, max_items=50, first_fetch_days=60,
            conn_factory=lambda *a, **k: fake, pw_fn=lambda inbox: "app-pw")
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["_unparsed"])

    def test_missing_app_password_is_inbox_skip(self):
        with self.assertRaises(rail.InboxSkip):
            rail.collect("abhijeetgandhi01@gmail.com", None, max_items=1,
                         pw_fn=lambda inbox: None)


if __name__ == "__main__":
    unittest.main()
