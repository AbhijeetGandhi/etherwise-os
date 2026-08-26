"""Upwork financials through the MCP (modules/upwork/feeds/mcp_financials),
pinned by the four captures Abhijeet approved on 2026-08-26 16:20 IST
(tests/fixtures/mcp/accounts.json, financials_transactions_{personal,agency}
.json, connects_balance.json — structure verbatim, ids and amounts real).

Pins: the org resolution (TALENT = personal, FL_AGENCY = agency, CLIENT
ignored); the request shapes and the one-split paging; every row kind's
signed amount against the ledger's own CSV rows (Service Fee / WHT /
Membership Fee negative, Hourly positive, withdrawals negative as transfers
— his 26 Aug 16:50 word — the card payment positive); the ledger type and P&L bucket an inserted row gets;
that an MCP row UPDATES a CSV row and never twins it; the upwork_mcp_rows
audit twin; the connects usage rows read into the Chrome rail's vocabulary
(UTC day, '~02' stripped, pairs → applied + boost, twins with the rail's
rows), SHADOW until MCP_CONNECTS_SINCE; the start-failure wording; and that
the rail IS scheduled (plist, SOURCES, RERUN_JOBS, words, migration 030).
"""
from __future__ import annotations

import json
import shutil
import socket
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest import mock

from core import config, db, doctor_checks
from modules.upwork.feeds import mcp_client, mcp_financials as mf, mcp_search

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "mcp"
PERSONAL, AGENCY = "1647672547786633217", "1910033523997712724"


def _fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


def _rows(fx: dict) -> list:
    return fx["data"]["transactionHistory"]["transactionDetail"]["transactionHistoryRow"]


class _Session:
    """Answers keyed on (tool, action, org) with (tool, action) as fallback."""
    def __init__(self, answers, fail_start=None):
        self.answers = answers
        self.calls = []
        self.fail_start = fail_start

    def initialize(self):
        if self.fail_start:
            raise self.fail_start

    def call(self, tool, args):
        self.calls.append((tool, args))
        args = args or {}
        for key in ((tool, args.get("action"), args.get("org_uid")), (tool, args.get("action"))):
            if key in self.answers:
                a = self.answers[key]
                return a(args) if callable(a) else a
        return {}


class _Ctx:
    def __init__(self, db_path):
        self.db_path = db_path
        self.logs = []
        self.anomalies = []

    def log(self, m):
        self.logs.append(m)

    def anomaly(self, kind, severity, detail):
        self.anomalies.append((kind, severity, detail))


def _complete(fx: dict) -> dict:
    """The fixtures keep 12 of 32 and 9 of 27 rows (dropped for size); a run
    over them is 'complete' when total_count says so — the truncation path has
    its own test."""
    return dict(fx, total_count=len(_rows(fx)))


def _live_session(over=None):
    answers = {
        ("list_accounts", None): _fixture("accounts"),
        (mf.TOOL, "transactions", PERSONAL): _complete(_fixture("financials_transactions_personal")),
        (mf.TOOL, "transactions", AGENCY): _complete(_fixture("financials_transactions_agency")),
        (mf.TOOL, "connects_balance"): _fixture("connects_balance"),
        (mf.TOOL, "summary"): _fixture("financials_summary_personal"),
    }
    answers.update(over or {})
    return _Session(answers)


# the personal summary of 1-26 Aug, as rows the rail would have read: three
# clients, each hourly invoice with its service fee and withholding tax
SUMMARY_ROWS = [  # (record_id, subtype, minor, invoice day, company)
    ("940917308", "Hourly", 40667, "2026-08-02", "Amanda T"), ("940917310", "Service Fee", -4067, "2026-08-02", "Amanda T"),
    ("940917311", "WHT", -41, "2026-08-02", "Amanda T"),
    ("942600001", "Hourly", 22000, "2026-08-09", "Amanda T"), ("942600002", "Service Fee", -2200, "2026-08-09", "Amanda T"),
    ("942600003", "WHT", -22, "2026-08-09", "Amanda T"),
    ("944448026", "Hourly", 24000, "2026-08-16", "Amanda T"), ("944448040", "Service Fee", -2400, "2026-08-16", "Amanda T"),
    ("944448033", "WHT", -24, "2026-08-16", "Amanda T"),
    ("942600011", "Hourly", 8100, "2026-08-09", "NETFLY"), ("942600012", "Service Fee", -810, "2026-08-09", "NETFLY"),
    ("942600013", "WHT", -8, "2026-08-09", "NETFLY"),
    ("944268907", "Hourly", 8100, "2026-08-16", "NETFLY"), ("944268922", "Service Fee", -810, "2026-08-16", "NETFLY"),
    ("944268913", "WHT", -8, "2026-08-16", "NETFLY"),
    ("945000001", "Hourly", 9500, "2026-08-23", "Tomas Eklund"), ("945000002", "Service Fee", -950, "2026-08-23", "Tomas Eklund"),
    ("945000003", "WHT", -10, "2026-08-23", "Tomas Eklund"),
    # not in a summary: a membership fee, a card payment, a withdrawal
    ("944697095", "Membership Fee", -4500, "2026-08-17", None), ("941421323", "Payment", 4500, "2026-08-05", None),
    ("946740500", "Withdrawal", -233514, "2026-08-26", None),
]


def _seed_summary_rows(conn, rows=SUMMARY_ROWS, profile="Personal"):
    for rid, sub, minor, day, company in rows:
        conn.execute("INSERT INTO upwork_mcp_rows (record_id, profile, accounting_subtype, amount_minor,"
                     " invoice_dt, company, outcome) VALUES (?,?,?,?,?,?,'inserted')",
                     (rid, profile, sub, minor, day + "T00:00:00+0000", company))


# the Chrome rail's rows for the same days, as the live DB held them at 16:24
# IST on 26 Aug (event_date is the UTC day; job_id the digits after ~02)
CHROME_ROWS = [
    ("2026-08-25", "badge", -1, None), ("2026-08-25", "badge", -1, None),
    ("2026-08-24", "boost", -50, "2091881079178928768"),
    ("2026-08-24", "applied", -17, "2091881079178928768"),
    ("2026-08-24", "badge", -1, None), ("2026-08-24", "badge", -1, None),
    ("2026-08-24", "boost_refund", 100, "2089446685358007253"),
    ("2026-08-23", "boost", -103, "2091139773174847123"),
    ("2026-08-23", "applied", -19, "2091139773174847123"),
    ("2026-08-23", "applied", -18, "2090865622486570617"),
    ("2026-08-23", "badge", -1, None), ("2026-08-23", "badge", -1, None),
]


def _seed_chrome(conn):
    for i, (day, kind, delta, job) in enumerate(CHROME_ROWS):
        conn.execute("INSERT INTO connects_history (id, event_date, kind, label, job_id,"
                     " connects_delta) VALUES (?,?,?,?,?,?)",
                     (f"chrome{i:02d}", day, kind, kind.upper(), job, delta))


class NoSleep(unittest.TestCase):
    def setUp(self):
        self._p = mock.patch.object(mcp_search, "_sleep", lambda s: None)
        self._p.start()

    def tearDown(self):
        self._p.stop()


class TestOrgs(NoSleep):
    def test_fixture_accounts_resolve_talent_and_fl_agency_and_skip_client(self):
        self.assertEqual(mf.orgs(_live_session()), {"personal": PERSONAL, "agency": AGENCY})

    def test_no_talent_is_an_error_and_no_agency_is_none(self):
        s = _Session({("list_accounts", None): {"accounts": [{"role": "CLIENT", "org_uid": "9"}]}})
        with self.assertRaises(RuntimeError):
            mf.orgs(s)
        s2 = _Session({("list_accounts", None): {"accounts": [{"role": "TALENT", "org_uid": "1"}]}})
        self.assertEqual(mf.orgs(s2), {"personal": "1", "agency": None})


class TestRequests(NoSleep):
    def test_transactions_request_carries_the_documented_params(self):
        s = _live_session()
        got = mf.fetch_transactions(s, PERSONAL, "2026-08-01", "2026-08-26", lambda m: None)
        tool, args = s.calls[-1]
        self.assertEqual((tool, args["action"], args["org_uid"]), (mf.TOOL, "transactions", PERSONAL))
        self.assertEqual(args["params"], {"from_date": "2026-08-01", "to_date": "2026-08-26", "limit": 200})
        self.assertEqual((got["total"], got["truncated"], len(got["rows"]), got["calls"]), (12, False, 12, 1))
        # the raw capture says 32 exist and carries 12: one split, still short, reported
        s2 = _live_session({(mf.TOOL, "transactions", PERSONAL): _fixture("financials_transactions_personal")})
        got2 = mf.fetch_transactions(s2, PERSONAL, "2026-08-01", "2026-08-26", lambda m: None)
        self.assertEqual((got2["total"], got2["truncated"], len(got2["rows"]), got2["calls"]), (32, True, 12, 3))

    def test_more_rows_than_one_read_returns_splits_the_window_once_then_says_so(self):
        seen = []

        def answer(args):
            p = args["params"]
            seen.append((p["from_date"], p["to_date"]))
            return {"transactions": [{"recordId": p["from_date"]}], "total_count": 5}
        s = _Session({(mf.TOOL, "transactions"): answer})
        logs = []
        got = mf.fetch_transactions(s, "1", "2026-08-12", "2026-08-26", logs.append)
        self.assertEqual(seen, [("2026-08-12", "2026-08-26"), ("2026-08-12", "2026-08-19"),
                               ("2026-08-20", "2026-08-26")])
        self.assertEqual((got["calls"], got["truncated"], len(got["rows"])), (3, True, 2))
        self.assertTrue(any("splitting" in m for m in logs))

    def test_a_text_answer_is_not_mistaken_for_zero_transactions(self):
        s = _Session({(mf.TOOL, "transactions"): {"text": "Something went wrong"}})
        with self.assertRaises(RuntimeError):
            mf.fetch_transactions(s, "1", "2026-08-12", "2026-08-26", lambda m: None)

    def test_summary_and_connects_requests(self):
        s = _live_session()
        mf.fetch_summary(s, PERSONAL, "2026-08-01", "2026-08-26", lambda m: None)
        mf.fetch_connects(s, PERSONAL, lambda m: None, limit=25)
        self.assertEqual(s.calls[0][1]["params"], {"from_date": "2026-08-01", "to_date": "2026-08-26"})
        self.assertEqual(s.calls[1][1]["params"], {"limit": 10})      # the tool's ceiling


class TestRowMapping(unittest.TestCase):
    """The signs are the ledger's own: the same record ids sit in the live
    ledger as Upwork-CSV rows with exactly these amounts (read 26 Aug 16:30)."""
    LEDGER = {  # record_id: (signed minor, ledger type, pnl bucket)
        "944448040": (-2400, "Other", "platform_fee"),        # Service Fee
        "944448033": (-24, "Other", "tax"),                   # WHT
        "944448026": (24000, "Hourly Earning", "revenue"),    # Hourly
        "941421345": (-4500, "Connect Purchase", "platform_fee"),   # Membership Fee: 300 connects
        "941421323": (4500, "Other", "transfer"),             # ARPayment: the card settlement leg
        "944838018": (-1999, "Connect Purchase", "platform_fee"),   # Membership Fee: Agency Plus
        "946740500": (-233514, "Withdrawal", "transfer"),     # APPayment Withdrawal
        "946740501": (-99, "Other", "transfer"),              # Withdrawal Fee
    }

    def _by_id(self):
        out = {}
        for name in ("financials_transactions_personal", "financials_transactions_agency"):
            for raw in _rows(_fixture(name)):
                row = mf.transaction_row(raw)
                out[row["recordId"]] = row
        return out

    def test_every_fixture_row_maps_with_an_id_an_amount_and_the_invoice_date(self):
        rows = self._by_id()
        self.assertEqual(len(rows), 21)
        for row in rows.values():
            self.assertIsNotNone(mf.signed_minor(row))
            self.assertRegex(row["createdDateTime"], r"^2026-0[78]-\d\dT00:00:00\+0000$")
            self.assertIn(row["type"], {"APInvoice", "APAdjustment", "ARInvoice", "ARPayment", "APPayment"})
        self.assertEqual(rows["944448026"]["company"], "Amanda T")
        self.assertEqual(rows["946740500"]["status"], "SUCCESS")
        self.assertIsNone(rows["944448026"]["status"])

    def test_signs_types_and_buckets_match_the_ledgers_csv_rows(self):
        rows = self._by_id()
        for rid, (minor, ltype, bucket) in self.LEDGER.items():
            row = rows[rid]
            self.assertEqual(mf.signed_minor(row), minor, rid)
            self.assertEqual(mf.ledger_type(row), ltype, rid)
            self.assertEqual(mf.bucket_for(row), bucket, rid)

    def test_withdrawal_legs_are_transfer_legs_and_nothing_else_is(self):
        rows = self._by_id()
        legs = sorted(rid for rid, r in rows.items() if mf.is_transfer_leg(r))
        self.assertEqual(legs, ["946740500", "946740501"])

    def test_an_explicitly_negative_row_of_an_unknown_kind_stays_negative(self):
        row = mf.transaction_row({"recordId": "1", "type": "APAdjustment", "accountingSubtype": "Refund",
                                  "transactionAmount": {"rawValue": "-40.00", "currency": "USD"},
                                  "transactionCreationDate": "2026-08-20T00:00:00+0000"})
        self.assertEqual(mf.signed_minor(row), -4000)
        self.assertEqual(mf.ledger_type(row), "Refund")

    def test_snake_case_spelling_a_bare_amount_and_no_id(self):
        row = mf.transaction_row({"record_id": 556, "transaction_type": "APAdjustment",
                                  "subtype": "Service Fee", "amount": -45.5, "date": "2026-08-18"})
        self.assertEqual((row["recordId"], row["accountingSubtype"], mf.signed_minor(row)),
                         ("556", "Service Fee", -4550))
        self.assertIsNone(mf.transaction_row({"amount": 1}))


class TestConnectsEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mcpfin-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_usage_rows_read_into_the_rails_vocabulary_on_the_utc_day(self):
        events = mf.connects_events(_fixture("connects_balance"))
        self.assertEqual(len(events), 10)
        self.assertEqual(len({e["id"] for e in events}), 10)
        by = {(e["event_date"], e["connects_delta"], e["job_id"]): e for e in events}
        # the pair at one instant on one job: the smaller debit applied, the larger its boost
        self.assertEqual(by[("2026-08-24", -17, "2091881079178928768")]["kind"], "applied")
        self.assertEqual(by[("2026-08-24", -50, "2091881079178928768")]["kind"], "boost")
        # the UTC day, as the Chrome rail stored it (23:25Z on the 23rd is the 23rd)
        self.assertEqual(by[("2026-08-23", -18, "2090865622486570617")]["kind"], "applied")
        self.assertEqual(by[("2026-08-24", 100, "2089446685358007253")]["kind"], "boost_refund")
        self.assertEqual(sum(1 for e in events if e["kind"] == "badge"), 5)
        self.assertTrue(all(e["job_id"] is None or not e["job_id"].startswith("~") for e in events))
        self.assertEqual(by[("2026-08-24", -17, "2091881079178928768")]["job_title"],
                         "Expert Airtable Solution Architect")
        self.assertEqual(by[("2026-08-24", -17, "2091881079178928768")]["label"], "Applied to job")
        # same input, same ids (the dedup key across daily reads)
        self.assertEqual([e["id"] for e in mf.connects_events(_fixture("connects_balance"))],
                         [e["id"] for e in events])

    def test_a_lone_debit_on_a_job_already_applied_to_is_a_boost(self):
        fx = _fixture("connects_balance")
        # the page edge: the -103 boost is on the page, its -19 proposal fell off it
        with db.connect(self.db_path) as conn:
            self.assertEqual({e["kind"] for e in mf.connects_events(fx, conn)
                              if e["job_id"] == "2091139773174847123"}, {"applied"})
            _seed_chrome(conn)
            self.assertEqual({e["kind"] for e in mf.connects_events(fx, conn)
                              if e["job_id"] == "2091139773174847123"}, {"boost"})

    def test_an_events_own_chrome_twin_does_not_turn_it_into_a_boost(self):
        """The first live run (16:53, run 7232) read the -18 proposal of 23 Aug
        as a boost: the Chrome rail's row for the SAME event said 'applied'
        and the lone-debit rule took that as an earlier proposal."""
        fx = _fixture("connects_balance")
        with db.connect(self.db_path) as conn:
            _seed_chrome(conn)
            kinds = {(e["job_id"], e["connects_delta"]): e["kind"] for e in mf.connects_events(fx, conn)}
        self.assertEqual(kinds[("2090865622486570617", -18)], "applied")
        self.assertEqual(kinds[("2091881079178928768", -17)], "applied")
        self.assertEqual(kinds[("2091881079178928768", -50)], "boost")
        self.assertEqual(kinds[("2091139773174847123", -103)], "boost")   # its -19 proposal is on record

    def test_balance_and_twins_against_the_chrome_rail(self):
        fx = _fixture("connects_balance")
        self.assertEqual(mf._balance_of(fx), {"total": 256, "free": 0, "paid": 256, "rollover": 0})
        with db.connect(self.db_path) as conn:
            _seed_chrome(conn)
            events = mf.connects_events(fx, conn)
            twins = [e for e in events if mf.connects_twin(conn, e)]
        self.assertEqual(len(twins), 9)                          # the 26 Aug badge is newer than the rail
        self.assertEqual([e["event_date"] for e in events if e not in twins], ["2026-08-26"])


class TestWeeklySummary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mcpfin-sum-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_completed_weeks_and_what_is_due(self):
        self.assertEqual(mf.completed_weeks(date(2026, 8, 26)),
                         [("2026-08-10", "2026-08-16"), ("2026-08-17", "2026-08-23")])
        self.assertEqual(mf.completed_weeks(date(2026, 8, 24)),          # a Monday: last week just closed
                         [("2026-08-10", "2026-08-16"), ("2026-08-17", "2026-08-23")])
        with db.connect(self.db_path) as conn:
            self.assertEqual(len(mf.summaries_due(conn, date(2026, 8, 26), ["Personal", "Agency"])), 4)
            mf.store_summary(conn, "Personal", "2026-08-17", "2026-08-23",
                             mf.summary_row(_fixture("financials_summary_personal")))
            due = mf.summaries_due(conn, date(2026, 8, 26), ["Personal", "Agency"])
        self.assertEqual(due, [("Personal", "2026-08-10", "2026-08-16"),
                               ("Agency", "2026-08-10", "2026-08-16"), ("Agency", "2026-08-17", "2026-08-23")])

    def test_the_fixture_summary_maps_to_minor_units(self):
        row = mf.summary_row(_fixture("financials_summary_personal"))
        self.assertEqual((row["billed_minor"], row["fee_minor"], row["tax_minor"], row["earned_minor"]),
                         (112367, -11237, -113, 101017))
        self.assertEqual((row["currency"], row["earnings_available"]), ("USD", 1))
        self.assertIsNone(mf.summary_row({"text": "Something went wrong"}))
        na = mf.summary_row({"earnings_available": False, "note": "No earnings data", "status": "ok"})
        self.assertEqual((na["earnings_available"], na["billed_minor"]), (0, None))

    def test_a_weeks_billed_amounts_are_sets_of_the_rails_invoice_rows(self):
        """The summary's week is the week the invoice was CREATED — not what
        the transactions date shows — so the match is per client, a subset of
        invoice rows in the lag window, each row used once across weeks."""
        with db.connect(self.db_path) as conn:
            _seed_summary_rows(conn)
            by_client = _fixture("financials_summary_personal")["by_client"]
            used = set()
            res = mf.match_week(conn, "Personal", "2026-08-01", "2026-08-26", by_client, used)
        self.assertEqual(res["gaps"], [])
        self.assertEqual(sorted(res["ok"]), ["Amanda T", "NETFLY", "Tomas Eklund"])
        self.assertEqual(len(used), 6)                            # 3 Amanda + 2 NETFLY + 1 Tomas invoices
        self.assertIn("945000001", used)
        self.assertNotIn("944697095", used)                       # a membership fee is never a billing
        # the same rows cannot pay for a second week: NETFLY 162 has no rows left
        with db.connect(self.db_path) as conn:
            res2 = mf.match_week(conn, "Personal", "2026-08-01", "2026-08-26", by_client, used)
        self.assertEqual([g["client"] for g in res2["gaps"]], ["Amanda T", "NETFLY", "Tomas Eklund"])

    def test_subset_sum_prefers_fewer_rows_and_says_none(self):
        self.assertEqual(mf._subset_sum([8100, 8100, 24000], 16200), [0, 1])
        self.assertEqual(mf._subset_sum([8100, 8100, 24000], 24000), [2])
        self.assertIsNone(mf._subset_sum([8100, 8100], 9000))
        self.assertEqual(mf._subset_sum([], 0), [])

    def test_doctor_passes_on_a_matching_week_and_names_the_client_on_a_gap(self):
        with db.connect(self.db_path) as conn:
            _seed_summary_rows(conn)
            mf.store_summary(conn, "Personal", "2026-08-01", "2026-08-26",
                             mf.summary_row(_fixture("financials_summary_personal")))
            conn.execute("INSERT INTO sync_cursors (name, value) VALUES (?, '2026-07-01')", (mf.FLOOR_CURSOR,))
        c = doctor_checks.check_upwork_weekly_tieout(self.db_path, now=date(2026, 9, 9))
        self.assertEqual([x.status for x in c], ["PASS"], [x.detail for x in c])
        self.assertIn("billed 112367 over 3 client(s) = 6 invoice rows", c[0].detail)
        with db.connect(self.db_path) as conn:                      # the rail misses NETFLY's 16 Aug invoice
            conn.execute("DELETE FROM upwork_mcp_rows WHERE record_id='944268907'")
        c = doctor_checks.check_upwork_weekly_tieout(self.db_path, now=date(2026, 9, 9))
        self.assertEqual(c[0].status, "WARN")
        self.assertIn("NETFLY billed 16200: no set of the rail's rows adds up (2026-08-09 8100)", c[0].detail)
        self.assertNotIn("Amanda", c[0].detail)

    def test_doctor_skips_a_week_before_the_floor_a_settling_one_and_an_unavailable_one(self):
        with db.connect(self.db_path) as conn:
            mf.store_summary(conn, "Personal", "2026-08-10", "2026-08-16",
                             mf.summary_row(_fixture("financials_summary_personal")))
            mf.store_summary(conn, "Personal", "2026-08-17", "2026-08-23",
                             mf.summary_row(_fixture("financials_summary_personal")))
            mf.store_summary(conn, "Agency", "2026-08-17", "2026-08-23",
                             mf.summary_row({"earnings_available": False, "note": "No earnings data for this period"}))
            conn.execute("INSERT INTO sync_cursors (name, value) VALUES (?, '2026-08-12')", (mf.FLOOR_CURSOR,))
        c = {x.name: x for x in doctor_checks.check_upwork_weekly_tieout(self.db_path, now=date(2026, 8, 26))}
        self.assertEqual(c["upwork tie-out Personal 2026-08-10"].status, "SKIP")
        self.assertIn("before the rail's floor", c["upwork tie-out Personal 2026-08-10"].detail)
        self.assertEqual(c["upwork tie-out Personal 2026-08-17"].status, "SKIP")
        self.assertIn("still settling — its invoices post to transactions by 2026-09-02",
                      c["upwork tie-out Personal 2026-08-17"].detail)
        self.assertEqual(c["upwork tie-out Agency 2026-08-17"].status, "SKIP")
        self.assertIn("No earnings data", c["upwork tie-out Agency 2026-08-17"].detail)
        self.assertIn(doctor_checks.check_upwork_weekly_tieout, [fn for _, fn in doctor_checks.ALL_CHECKS])
        self.assertEqual(config.MCP_TIEOUT_SETTLE_DAYS, 10)


class TestRun(NoSleep):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="mcpfin-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.today = date(2026, 8, 26)

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, session=None, **kw):
        ctx = _Ctx(self.db_path)
        s = session or _live_session()
        m = mf.run(ctx, session_factory=lambda: s, lookback_days=14, today=self.today, **kw)
        return ctx, s, m

    def _t(self, sql, args=()):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, args)]

    def test_the_fixture_run_fills_the_ledger_updates_the_csv_row_and_books_withdrawals_as_transfers(self):
        with db.connect(self.db_path) as conn:      # the CSV import got here first for one row
            conn.execute("INSERT INTO transactions (record_id, canonical_key, dedup_key, type,"
                         " accounting_subtype, amount_minor, amount_base_minor, currency,"
                         " creation_dt, profile, source, pnl_bucket)"
                         " VALUES ('944448026', 'upwork:944448026', 'upwork-csv:944448026',"
                         " 'Hourly Earning', 'Hourly', 24000, 24000, 'USD',"
                         " '2026-08-26T00:00:00+0000', 'Personal', 'Upwork-CSV', 'revenue')")
            _seed_chrome(conn)
        ctx, s, m = self._run()
        self.assertEqual(m["window"], ["2026-08-12", "2026-08-26"])
        p, a = m["transactions"]["personal"], m["transactions"]["agency"]
        self.assertEqual((p["fetched"], p["inserted"], p["updated"], p["transfer_legs"], p["total"]),
                         (12, 11, 1, 0, 12))
        self.assertEqual((a["fetched"], a["inserted"], a["updated"], a["transfer_legs"], a["total"]),
                         (9, 9, 0, 2, 9))
        self.assertEqual([c[1]["org_uid"] for c in s.calls if c[1] and c[1].get("action") == "transactions"],
                         [PERSONAL, AGENCY])
        rows = {r["record_id"]: r for r in self._t("SELECT * FROM transactions")}
        self.assertEqual(len(rows), 21)                                 # 21 read, no twin of the CSV row
        self.assertEqual(rows["944448026"]["source"], "Upwork-CSV")      # the CSV row keeps its provenance
        self.assertEqual(rows["944448026"]["creation_dt"], "2026-08-26T00:00:00+0000")   # …and its payment date
        fee = rows["944448040"]
        self.assertEqual((fee["source"], fee["type"], fee["upwork_type"], fee["accounting_subtype"]),
                         (mf.SOURCE, "Other", "APAdjustment", "Service Fee"))
        self.assertEqual((fee["amount_minor"], fee["amount_base_minor"], fee["fx_rate"], fee["pnl_bucket"]),
                         (-2400, -2400, "1", "platform_fee"))
        self.assertEqual(fee["creation_dt"], "2026-08-16T00:00:00+0000")   # the invoice date — all the MCP gives
        self.assertEqual(fee["assignment_company"], "Amanda T")
        self.assertEqual(fee["canonical_key"], "upwork:944448040")
        self.assertEqual(rows["941421323"]["pnl_bucket"], "transfer")     # the card settlement, not revenue
        self.assertEqual(rows["944429716"]["profile"], "Agency")           # the ledger's spelling
        wd = rows["946740500"]                                            # the withdrawal: a transfer leg, in
        self.assertEqual((wd["type"], wd["accounting_subtype"], wd["amount_minor"], wd["pnl_bucket"], wd["status"]),
                         ("Withdrawal", "Withdrawal", -233514, "transfer", "SUCCESS"))
        self.assertEqual((rows["946740501"]["amount_minor"], rows["946740501"]["pnl_bucket"]), (-99, "transfer"))
        seen = {r["record_id"]: r for r in self._t("SELECT * FROM upwork_mcp_rows")}
        self.assertEqual(len(seen), 21)
        self.assertEqual(seen["946740500"]["outcome"], "inserted")
        self.assertEqual(seen["946740500"]["amount_minor"], -233514)
        self.assertEqual(seen["944448026"]["outcome"], "updated")
        self.assertEqual((seen["944448040"]["outcome"], seen["944448040"]["profile"],
                          seen["944448040"]["invoice_dt"]), ("inserted", "Personal", "2026-08-16T00:00:00+0000"))
        # connects: shadow — compared, never written
        c = m["connects"]
        self.assertEqual((c["mode"], c["inserted"], c["shadow_rows"], c["twins"], c["untwinned"]),
                         ("shadow", 0, 10, 9, 1))
        self.assertEqual(c["balance"]["total"], 256)
        self.assertEqual(self._t("SELECT COUNT(*) n FROM connects_history")[0]["n"], len(CHROME_ROWS))
        self.assertEqual(ctx.anomalies, [])
        self.assertEqual(self._t("SELECT value FROM sync_cursors WHERE name=?", (mf.FLOOR_CURSOR,))[0]["value"],
                         "2026-08-12")                                  # the coverage line the doctor draws
        # the two completed weeks' summaries, both orgs, stored once
        self.assertEqual((m["summaries"]["due"], m["summaries"]["stored"]), (4, 4))
        sums = self._t("SELECT profile, period_start, period_end, billed_minor FROM upwork_mcp_summaries ORDER BY 1,2")
        self.assertEqual([(r["profile"], r["period_start"], r["period_end"]) for r in sums],
                         [("Agency", "2026-08-10", "2026-08-16"), ("Agency", "2026-08-17", "2026-08-23"),
                          ("Personal", "2026-08-10", "2026-08-16"), ("Personal", "2026-08-17", "2026-08-23")])
        self.assertEqual({r["billed_minor"] for r in sums}, {112367})
        summary_calls = [c for c in s.calls if c[1] and c[1].get("action") == "summary"]
        self.assertEqual([c[1]["params"] for c in summary_calls][:2],
                         [{"from_date": "2026-08-10", "to_date": "2026-08-16"},
                          {"from_date": "2026-08-17", "to_date": "2026-08-23"}])
        # the second run inserts nothing, sees everything again
        ctx2, _, m2 = self._run()
        self.assertEqual((m2["transactions"]["personal"]["inserted"], m2["transactions"]["personal"]["updated"]),
                         (0, 12))
        self.assertEqual(m2["summaries"]["due"], 0)                    # nothing re-read
        self.assertEqual(self._t("SELECT seen_runs FROM upwork_mcp_rows WHERE record_id='944448040'")[0]["seen_runs"], 2)
        self.assertEqual(self._t("SELECT outcome FROM upwork_mcp_rows WHERE record_id='944448040'")[0]["outcome"], "inserted")
        self.assertEqual(len(self._t("SELECT 1 FROM transactions")), 21)

    def test_connects_go_live_from_the_cut_day_only(self):
        with db.connect(self.db_path) as conn:
            _seed_chrome(conn)
        _, _, m = self._run(connects_since="2026-08-25")
        self.assertEqual((m["connects"]["mode"], m["connects"]["inserted"], m["connects"]["shadow_rows"]),
                         ("live", 3, 7))
        new = self._t("SELECT event_date, kind, connects_delta FROM connects_history WHERE id LIKE '%' AND id NOT LIKE 'chrome%' ORDER BY event_date")
        self.assertEqual([(r["event_date"], r["kind"]) for r in new],
                         [("2026-08-25", "badge"), ("2026-08-25", "badge"), ("2026-08-26", "badge")])
        _, _, m2 = self._run(connects_since="2026-08-25")
        self.assertEqual(m2["connects"]["inserted"], 0)

    def test_no_agency_org_is_a_warn_anomaly_not_a_silent_half_ledger(self):
        s = _live_session({("list_accounts", None): {"accounts": [{"role": "TALENT", "org_uid": PERSONAL}]}})
        ctx, _, m = self._run(session=s)
        self.assertEqual(sorted(m["transactions"]), ["personal"])
        self.assertEqual([a[0] for a in ctx.anomalies], ["financials_no_agency"])

    def test_more_rows_than_returned_is_a_warn_anomaly(self):
        fx = _fixture("financials_transactions_personal")
        fx = dict(fx, total_count=500)
        s = _live_session({(mf.TOOL, "transactions", PERSONAL): fx})
        ctx, _, m = self._run(session=s)
        self.assertTrue(m["transactions"]["personal"]["truncated"])
        self.assertEqual([a[0] for a in ctx.anomalies], ["financials_truncated"])

    def test_a_network_failure_at_start_is_named_as_such_not_as_a_token_to_repair(self):
        err = urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided, or not known"))
        s = _Session({}, fail_start=err)
        with self.assertRaises(RuntimeError) as cm:
            self._run(session=s)
        self.assertIn("(network)", str(cm.exception))
        self.assertNotIn("upwork-mcp-auth", str(cm.exception))
        s2 = _Session({}, fail_start=mcp_client.MCPCallError("mcp 403: forbidden"))
        with self.assertRaises(RuntimeError) as cm2:
            self._run(session=s2)
        self.assertIn("(auth)", str(cm2.exception))
        self.assertIn("upwork-mcp-auth", str(cm2.exception))


class TestStartFailureKind(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(mcp_client.start_failure_kind(
            urllib.error.URLError(socket.gaierror(8, "nodename nor servname"))), "network")
        self.assertEqual(mcp_client.start_failure_kind(socket.timeout("timed out")), "network")
        self.assertEqual(mcp_client.start_failure_kind(mcp_client.MCPAuthError("no tokens")), "auth")
        self.assertEqual(mcp_client.start_failure_kind(mcp_client.MCPCallError("mcp 401: nope")), "auth")
        self.assertEqual(mcp_client.start_failure_kind(mcp_client.MCPCallError("initialize: unparseable")), "unknown")
        wrapped = RuntimeError("outer")
        wrapped.__cause__ = mcp_client.MCPAuthError("inner")
        self.assertEqual(mcp_client.start_failure_kind(wrapped), "auth")
        self.assertIn("nothing to repair", mcp_client.START_FAILURE_ADVICE["network"])


class TestDoctorTwins(unittest.TestCase):
    HEADER = ("Date,Transaction ID,Transaction type,Transaction summary,"
              "Transaction summary details,Description 1,Description 2,"
              "Description 3,Agency team,Freelancer,Client team,Account name,PO,"
              "Ref ID,Amount $,Amount in local currency,Currency,"
              "Current balance $,Payment method")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mcpfin-doc-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.inbox = self.tmp / "inbox"
        self.inbox.mkdir()
        self.now = date(2026, 9, 9)          # a Wednesday: completed weeks 24-30 Aug and 31 Aug-6 Sep

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _csv(self, rows):
        # newest first, running balance consistent (the parser reconciles it)
        lines, bal = [], 100000
        for day, ref, kind, amount in rows:
            lines.append(f'"{day}",T{ref},{kind},x,,y,,,,,,Acc,,{ref},{amount / 100:.2f},,,{bal / 100:.2f},')
            bal -= amount
        (self.inbox / "2026-09-09-upwork-personal-fullhistory.csv").write_text(
            self.HEADER + "\n" + "\n".join(lines))

    def _ran(self, day):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO runs (task_name, started_at, status) VALUES"
                         " ('upwork_financials', ?, 'completed')", (day + "T06:05:00+05:30",))

    def _seen(self, rows, floor=None):
        with db.connect(self.db_path) as conn:
            for ref, amount, *inv in rows:
                conn.execute("INSERT INTO upwork_mcp_rows (record_id, profile, amount_minor, outcome,"
                             " invoice_dt) VALUES (?, 'personal', ?, 'inserted', ?)",
                             (ref, amount, (inv[0] + "T00:00:00+0000") if inv else None))
            if floor:
                conn.execute("INSERT INTO sync_cursors (name, value) VALUES (?, ?)", (mf.FLOOR_CURSOR, floor))

    def test_skips_until_the_rail_has_read_something(self):
        c = doctor_checks.check_upwork_mcp_twins(self.db_path, self.inbox, now=self.now)[0]
        self.assertEqual(c.status, "SKIP")

    def test_two_clean_completed_weeks_pass_with_the_retire_verdict(self):
        self._csv([("Sep 8, 2026", "9500", "Hourly", 10000), ("Sep 2, 2026", "9400", "Service Fee", -1000),
                   ("Aug 27, 2026", "9300", "Hourly", 20000), ("Aug 26, 2026", "9000", "Hourly", 5000)])
        self._seen([("9500", 10000), ("9400", -1000), ("9300", 20000), ("9200", 1)],
                   floor="2026-07-20")                                          # 9000 predates the rail
        self._ran("2026-08-20")
        c = doctor_checks.check_upwork_mcp_twins(self.db_path, self.inbox, now=self.now)[0]
        self.assertEqual(c.status, "PASS", c.detail)
        self.assertIn("clean completed weeks 2/2", c.detail)
        self.assertIn("audit-only", c.detail)
        self.assertIn("1 older CSV rows predate the rail", c.detail)

    def test_a_wide_one_off_read_does_not_shortcut_the_two_weeks_of_daily_running(self):
        """The 90-day read of 26 Aug put every week since May in view at once;
        the daily rail had run for one afternoon."""
        self._csv([("Sep 8, 2026", "9500", "Hourly", 10000), ("Aug 27, 2026", "9300", "Hourly", 20000)])
        self._seen([("9500", 10000), ("9300", 20000)], floor="2026-05-28")
        self._ran("2026-09-01")
        c = doctor_checks.check_upwork_mcp_twins(self.db_path, self.inbox, now=self.now)[0]
        self.assertEqual(c.status, "PASS", c.detail)
        self.assertNotIn("audit-only", c.detail)
        self.assertIn("the daily rail has run since 2026-09-01 — keep the CSV import until 2026-09-15", c.detail)

    def test_a_young_rail_does_not_read_two_clean_weeks_on_its_first_morning(self):
        """Run 7232's first doctor read said "can go audit-only": every covered
        row twinned, but the rail had read invoices for one morning."""
        self._csv([("Sep 8, 2026", "9500", "Hourly", 10000), ("Sep 2, 2026", "9400", "Service Fee", -1000),
                   ("Aug 27, 2026", "9300", "Hourly", 20000)])
        self._seen([("9500", 10000), ("9400", -1000), ("9300", 20000)], floor="2026-08-26")
        c = doctor_checks.check_upwork_mcp_twins(self.db_path, self.inbox, now=self.now)[0]
        self.assertEqual(c.status, "PASS", c.detail)
        self.assertNotIn("audit-only", c.detail)
        self.assertIn("0 of the two completed weeks fully in view — keep the CSV import until 2026-09-28", c.detail)

    def test_a_missing_twin_or_a_wrong_amount_is_a_warn_with_the_refs(self):
        self._csv([("Sep 8, 2026", "9500", "Hourly", 10000), ("Sep 2, 2026", "9400", "Service Fee", -1000),
                   ("Aug 27, 2026", "9300", "Hourly", 20000)])
        self._seen([("9500", 10000), ("9300", 20001)])                    # 9400 unseen, 9300 off by one
        c = doctor_checks.check_upwork_mcp_twins(self.db_path, self.inbox, now=self.now)[0]
        self.assertEqual(c.status, "WARN")
        self.assertIn("1 of 3 CSV rows since 2026-08-24 have no MCP twin (9400)", c.detail)
        self.assertIn("9300 csv 20000 mcp 20001", c.detail)
        self.assertIn("clean completed weeks 0/2", c.detail)

    def test_the_coverage_line_is_the_rails_first_window_floor_not_the_smallest_id_it_read(self):
        """Run 7232 (16:53) read a 2 Aug row inside a 12 Aug window (the MCP's
        date filter is loose on the old side) and the check then wanted a twin
        for a 10 Aug connects purchase the window never held."""
        self._csv([("Sep 8, 2026", "9500", "Hourly", 10000), ("Aug 27, 2026", "9300", "Hourly", 20000),
                   ("Aug 26, 2026", "9250", "Connects", -4500)])       # invoiced before the floor, paid in view
        self._seen([("9500", 10000, "2026-09-07"), ("9300", 20000, "2026-08-24"),
                    ("9100", 5, "2026-08-02")], floor="2026-08-24")       # 9100: the loose filter's extra row
        c = doctor_checks.check_upwork_mcp_twins(self.db_path, self.inbox, now=self.now)[0]
        self.assertEqual(c.status, "PASS", c.detail)
        self.assertIn("1 older CSV rows predate the rail", c.detail)
        self.assertIn("keep the CSV import until", c.detail)              # a floor of 24 Aug: not two weeks in view

    def test_registered_with_the_doctor(self):
        self.assertIn(doctor_checks.check_upwork_mcp_twins, [fn for _, fn in doctor_checks.ALL_CHECKS])


class TestScheduled(unittest.TestCase):
    def test_plist_source_rerun_words_migration_and_read_tool(self):
        plist = ROOT / "launchd" / "io.etherwise.v3.upwork-financials.plist"
        self.assertTrue(plist.is_file())
        import plistlib
        doc = plistlib.loads(plist.read_bytes())
        self.assertEqual(doc["Label"], "io.etherwise.v3.upwork-financials")
        self.assertEqual(doc["ProgramArguments"][-2:], ["-m", "modules.upwork.feeds.mcp_financials"])
        self.assertEqual(doc["StartCalendarInterval"], {"Hour": 6, "Minute": 5})
        src = next(s for s in config.SOURCES if s.get("task_name") == mf.TASK_NAME)
        self.assertEqual((src["key"], src["critical"], src["launchd_label"], src["backs"], src["catch_up"]),
                         ("upwork_financials", True, doc["Label"], "money", True))
        self.assertIn("financials_truncated", src["degrade_kinds"])
        from modules.cockpit import actions, data
        self.assertEqual(actions.RERUN_JOBS[mf.TASK_NAME], doc["Label"])
        self.assertIn(mf.TASK_NAME, data._RUN_TASK_WORDS)
        self.assertTrue((ROOT / "core" / "migrations" / "030_upwork_mcp_rows.sql").is_file())
        self.assertTrue((ROOT / "core" / "migrations" / "031_upwork_mcp_summaries.sql").is_file())
        self.assertEqual(config.MCP_SUMMARY_WEEKS, 2)
        self.assertIn("get_freelancer_financials", mcp_client.READ_TOOLS)
        self.assertIsNone(config.MCP_CONNECTS_SINCE)             # connects stay in shadow until he sets the day


if __name__ == "__main__":
    unittest.main()
