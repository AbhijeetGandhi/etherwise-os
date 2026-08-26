"""Upwork financials through the MCP (modules/upwork/feeds/mcp_financials) —
built to the tool reference on 2026-08-26, the row mapping to be pinned by a
fixture once Abhijeet OKs the capture calls (docs/upwork/MCP_USE_CASES.md §12).
Pins: the request shapes (action, org_uid, dated window, limit), the org
resolution (TALENT = personal, AGENCY = agency), the tolerant row mapping on
both key spellings, the canonical key shared with the CSV/GraphQL rows (an
MCP row UPDATES a CSV row, never twins it), a row without an id is skipped
and counted, the connects usage rows into connects_history with the rail's
own kind rule, and that nothing here is scheduled.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from core import config, db
from modules.upwork.feeds import mcp_financials as mf, mcp_search

ROOT = Path(__file__).resolve().parents[1]


class _Session:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def initialize(self):
        pass

    def call(self, tool, args):
        self.calls.append((tool, args))
        key = (tool, (args or {}).get("action"))
        a = self.answers.get(key)
        return a(args) if callable(a) else (a or {})


class _Ctx:
    def __init__(self, db_path):
        self.db_path = db_path
        self.logs = []

    def log(self, m):
        self.logs.append(m)


class TestRequests(unittest.TestCase):
    def setUp(self):
        self._p = mock.patch.object(mcp_search, "_sleep", lambda s: None)
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def test_orgs_resolve_talent_and_agency(self):
        s = _Session({("list_accounts", None): {"accounts": [
            {"role": "TALENT", "org_uid": "111"}, {"role": "AGENCY", "org_uid": "222"}]}})
        self.assertEqual(mf.orgs(s), {"personal": "111", "agency": "222"})
        s2 = _Session({("list_accounts", None): {"accounts": [{"role": "CLIENT", "org_uid": "9"}]}})
        with self.assertRaises(RuntimeError):
            mf.orgs(s2)

    def test_transactions_request_carries_the_documented_params_and_reports_truncation(self):
        s = _Session({(mf.TOOL, "transactions"): {"transactions": [{"recordId": "1"}], "total_count": 5}})
        logs = []
        got = mf.fetch_transactions(s, "111", "2026-08-01", "2026-08-26", logs.append, limit=200)
        tool, args = s.calls[-1]
        self.assertEqual((tool, args["action"], args["org_uid"]), (mf.TOOL, "transactions", "111"))
        self.assertEqual(args["params"], {"from_date": "2026-08-01", "to_date": "2026-08-26", "limit": 200})
        self.assertEqual((got["total"], got["truncated"], len(got["rows"])), (5, True, 1))
        self.assertTrue(any("5 exist" in m for m in logs))

    def test_summary_and_connects_requests(self):
        s = _Session({(mf.TOOL, "summary"): {"earnings_available": True},
                      (mf.TOOL, "connects_balance"): {"balance": {"total": 40}, "history": []}})
        mf.fetch_summary(s, "111", "2026-08-01", "2026-08-26", lambda m: None)
        mf.fetch_connects(s, "111", lambda m: None, limit=25)
        self.assertEqual(s.calls[0][1]["params"], {"from_date": "2026-08-01", "to_date": "2026-08-26"})
        self.assertEqual(s.calls[1][1]["params"], {"limit": 10})      # the tool's ceiling


class TestRowMapping(unittest.TestCase):
    def test_graphql_spelling(self):
        row = mf.transaction_row({
            "recordId": "555", "type": "Hourly", "accountingSubtype": "Hourly Earning",
            "description": "Airtable Configuration - Earnings for Aug 10-Aug 16",
            "transactionAmount": {"rawValue": "1234.50", "currency": "USD"},
            "transactionCreationDate": "2026-08-18T00:00:00Z", "paymentStatus": "PAID"})
        self.assertEqual(row["recordId"], "555")
        self.assertEqual(row["amount"], {"rawValue": "1234.50", "currency": "USD"})
        self.assertEqual(row["createdDateTime"], "2026-08-18T00:00:00Z")
        self.assertEqual(row["status"], "PAID")

    def test_snake_case_spelling_and_a_bare_amount(self):
        row = mf.transaction_row({"record_id": 556, "transaction_type": "Service Fee",
                                  "amount": -45.5, "currency": "USD", "date": "2026-08-18"})
        self.assertEqual(row["recordId"], "556")
        self.assertEqual(row["amount"]["rawValue"], -45.5)
        self.assertEqual(row["type"], "Service Fee")

    def test_a_row_without_an_id_is_none(self):
        self.assertIsNone(mf.transaction_row({"amount": 1}))

    def test_connects_usage_row_uses_the_rails_kind_rule(self):
        e = mf.connects_event({"date": "2026-08-25", "action": "Proposal submitted", "connects": "-16",
                               "job": {"id": "2091681309915385639", "title": "Twilio + Make.com"}})
        self.assertEqual((e["kind"], e["connects_delta"], e["job_id"]), ("applied", -16, "2091681309915385639"))
        b = mf.connects_event({"date": "2026-08-25", "action": "Boost refund — outbid", "connects": "+40"})
        self.assertEqual(b["kind"], "boost_refund")
        self.assertIsNone(mf.connects_event({"date": "2026-08-25", "action": "x", "connects": "many"}))


class TestRunWritesOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mcpfin-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self._p = mock.patch.object(mcp_search, "_sleep", lambda s: None)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_an_mcp_row_updates_the_csv_row_and_a_new_one_is_tagged(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO transactions (record_id, canonical_key, dedup_key, type,"
                         " amount_minor, amount_base_minor, currency, creation_dt, source)"
                         " VALUES ('700', 'upwork:700', 'upwork-csv:700', 'Hourly Earning', 100000,"
                         " 100000, 'USD', '2026-08-18T00:00:00+0000', 'Upwork-CSV')")
        s = _Session({
            ("list_accounts", None): {"accounts": [{"role": "TALENT", "org_uid": "111"}]},
            (mf.TOOL, "transactions"): {"transactions": [
                {"recordId": "700", "type": "Hourly", "transactionAmount": {"rawValue": "1000.00"},
                 "transactionCreationDate": "2026-08-18T00:00:00Z", "paymentStatus": "PAID"},
                {"recordId": "701", "type": "Hourly", "transactionAmount": {"rawValue": "250.00"},
                 "transactionCreationDate": "2026-08-20T00:00:00Z"},
                {"nope": 1}], "total_count": 3},
            (mf.TOOL, "connects_balance"): {"balance": {"total": 40, "free": 10},
                                              "history": [{"date": "2026-08-25", "action": "Proposal submitted",
                                                           "connects": "-16", "job": {"id": "1", "title": "t"}}]},
        })
        m = mf.run(_Ctx(self.db_path), session_factory=lambda: s, lookback_days=14, today=date(2026, 8, 26))
        t = m["transactions"]["personal"]
        self.assertEqual((t["fetched"], t["inserted"], t["updated"], t["no_id"]), (3, 1, 1, 1))
        with db.connect(self.db_path) as conn:
            rows = {r["record_id"]: dict(r) for r in conn.execute("SELECT * FROM transactions")}
        self.assertEqual(len(rows), 2)                            # no twin of the CSV row
        self.assertEqual(rows["700"]["source"], "Upwork-CSV")     # the CSV row keeps its provenance
        self.assertEqual(rows["700"]["status"], "PAID")           # …and gains the status
        self.assertEqual(rows["701"]["source"], mf.SOURCE)
        self.assertEqual(rows["701"]["amount_minor"], 25000)
        self.assertEqual(m["connects"]["new"], 1)
        self.assertEqual(m["connects"]["balance"], {"total": 40, "free": 10})
        self.assertEqual(m["window"], ["2026-08-12", "2026-08-26"])
        # the second run inserts nothing new
        m2 = mf.run(_Ctx(self.db_path), session_factory=lambda: s, lookback_days=14, today=date(2026, 8, 26))
        self.assertEqual((m2["transactions"]["personal"]["inserted"], m2["connects"]["new"]), (0, 0))


class TestNotScheduled(unittest.TestCase):
    def test_no_plist_no_source_no_rerun_entry_yet(self):
        plists = " ".join(p.name for p in (ROOT / "launchd").glob("*.plist"))
        self.assertNotIn("financials", plists)
        self.assertFalse(any(s.get("task_name") == mf.TASK_NAME for s in config.SOURCES))
        from modules.cockpit import actions
        self.assertNotIn(mf.TASK_NAME, actions.RERUN_JOBS)
        self.assertIn("get_freelancer_financials", __import__("modules.upwork.feeds.mcp_client", fromlist=["READ_TOOLS"]).READ_TOOLS)


if __name__ == "__main__":
    unittest.main()
