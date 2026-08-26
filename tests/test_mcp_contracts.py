"""Contracts through the MCP (modules/upwork/feeds/mcp_contracts — use case
D, Phase 0.3, 26 Aug 2026), pinned by tests/fixtures/mcp/
contracts_search_active.json (four pages, 34 active contracts, captured
19:22 on his OK; the agency org refuses the search, its contracts are in the
personal one).

Pins: the envelope and offset paging; the row mapping; a new contract is
inserted with what the search carries, an existing one keeps what the room
pass wrote (fill-or-keep) and gains its status; the agency mark from the
ledger's own evidence; ended / paused counted; once a day inside the sync;
a failure degrades the sync, never fails it; ONE TCC contract.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from core import config, db
from modules.upwork import sync_state
from modules.upwork.feeds import mcp_contracts as mc, mcp_search

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "mcp"
ORG = config.UPWORK_ORGS["personal"]


def _pages() -> list:
    return json.loads((FIX / "contracts_search_active.json").read_text())["pages"]


def _envelope(rows, has_more=False, next_offset=None):
    return {"data": {"vendorContracts": {"contracts": rows, "paging": {"limit": 10, "offset": 0}}},
            "hasMore": has_more, "next_offset": next_offset, "status": "ok"}


class _Session:
    """answers[status] = list of pages served by offset order."""
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def call(self, tool, args):
        self.calls.append((tool, args))
        params = (args or {}).get("params") or {}
        st = (params.get("contract_statuses") or ["?"])[0]
        pages = self.answers.get(st) or []
        idx = int(params.get("offset") or 0) // 10
        return pages[idx] if idx < len(pages) else _envelope([])


class _Ctx:
    def __init__(self, db_path):
        self.db_path = db_path
        self.logs, self.anomalies = [], []

    def log(self, m):
        self.logs.append(m)

    def anomaly(self, kind, severity, detail):
        self.anomalies.append((kind, severity, detail))


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mcpcon-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self._p = mock.patch.object(mcp_search, "_sleep", lambda s: None)
        self._p.start()
        self.now = datetime(2026, 8, 26, 14, 0, tzinfo=timezone.utc)      # 19:30 IST

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self, sql, args=()):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, args)]


class TestShape(unittest.TestCase):
    def test_the_fixture_pages_and_the_row(self):
        pages = _pages()
        self.assertEqual(sum(len(mc._rows_of(p)[0]) for p in pages), 34)
        self.assertEqual([mc._rows_of(p)[1] for p in pages], [True, True, True, False])
        self.assertEqual(mc._rows_of(pages[0])[2], 10)
        tcc = [r for p in pages for r in mc._rows_of(p)[0] if r["clientOrganization"]["name"] == "Brandi Jordan"]
        self.assertEqual([r["id"] for r in tcc], ["44099615"])            # ONE TCC contract
        row = mc.contract_row(tcc[0])
        self.assertEqual((row["id"], row["status"], row["client"], row["offer_id"], row["start_dt"][:10]),
                         ("44099615", "ACTIVE", "Brandi Jordan", "112011283", "2026-07-15"))
        self.assertIsNone(row["end_dt"])
        self.assertIsNone(mc.contract_row({"title": "no id"}))
        self.assertEqual(mc._rows_of({"text": "nope"}), ([], False, None))


class TestSearch(Case):
    def test_pages_by_offset_until_has_more_is_false(self):
        s = _Session({"ACTIVE": _pages()})
        got = mc.search(s, ORG, ["ACTIVE"], lambda m: None)
        self.assertEqual((len(got["rows"]), got["pages"], got["more"]), (34, 4, False))
        self.assertEqual([c[1]["params"]["offset"] for c in s.calls], [0, 10, 20, 30])
        self.assertEqual(s.calls[0][1]["params"], {"contract_statuses": ["ACTIVE"], "limit": 10, "offset": 0})
        got2 = mc.search(_Session({"CLOSED": [_envelope([])]}), ORG, ["CLOSED"], lambda m: None,
                         end_date_after="2026-07-12")
        self.assertEqual((got2["rows"], got2["pages"]), ([], 1))
        with self.assertRaises(RuntimeError):
            mc.search(_Session({"ACTIVE": [{"text": "rate limited"}]}), ORG, ["ACTIVE"], lambda m: None)
        capped = mc.search(_Session({"ACTIVE": _pages()}), ORG, ["ACTIVE"], lambda m: None, max_pages=2)
        self.assertEqual((len(capped["rows"]), capped["more"]), (20, True))


class TestUpsert(Case):
    def test_new_contracts_arrive_existing_ones_keep_the_rooms_knowledge(self):
        with db.connect(self.db_path) as conn:
            # the room pass wrote TCC's contract with its richer fields
            sync_state.upsert_contract(conn, {"id": "44099615", "title": "AI Automation Specialist",
                                              "status": "ACTIVE", "contractType": "HOURLY",
                                              "startDateTime": "2026-07-15T20:21:42Z",
                                              "hourlyChargeRate": {"amount": 32.5},
                                              "job": {"id": "job-tcc", "content": {"title": "AI Automation Specialist"}}},
                                       "room-tcc", profile="personal", room_name="Brandi Jordan")
            # the ledger says the agency invoices Brandi Jordan and Ioannis P
            for rid, company in (("944429716", "Brandi Jordan"), ("944250983", "Ioannis P")):
                conn.execute("INSERT INTO upwork_mcp_rows (record_id, profile, company, outcome)"
                             " VALUES (?, 'Agency', ?, 'updated')", (rid, company))
        s = _Session({"ACTIVE": _pages()})
        m = mc.run(_Ctx(self.db_path), s, ORG, now=self.now)
        self.assertEqual((m["inserted"], m["ended"], m["paused"], m["reads"]), (33, 0, 0, 6))
        self.assertEqual(m["statuses"]["ACTIVE"]["unchanged"], 1)
        rows = {r["id"]: r for r in self.rows("SELECT * FROM contracts")}
        self.assertEqual(len(rows), 34)
        tcc = rows["44099615"]
        self.assertEqual((tcc["contract_type"], tcc["hourly_rate"], tcc["job_id"], tcc["thread_id"], tcc["profile"]),
                         ("HOURLY", 32.5, "job-tcc", "room-tcc", "agency"))   # kept, and marked agency
        io = rows["43406906"]
        self.assertEqual((io["client_company"], io["profile"], io["upwork_status"], io["start_dt"][:10]),
                         ("Ioannis P", "agency", "ACTIVE", "2026-04-14"))
        self.assertEqual(rows["44362102"]["profile"], "personal")           # Rocky Choy: personal
        self.assertEqual(m["agency"], 2)
        # the room pass an hour later does not undo the agency mark
        with db.connect(self.db_path) as conn:
            sync_state.upsert_contract(conn, {"id": "43406906", "title": "Ai Automations", "status": "ACTIVE"},
                                       "room-io", profile="personal", room_name="Ioannis Pirigkos")
        self.assertEqual(self.rows("SELECT profile FROM contracts WHERE id='43406906'")[0]["profile"], "agency")
        # the cursor says today; a second call the same day is not due
        with db.connect(self.db_path) as conn:
            self.assertFalse(mc.due_today(conn, self.now))
            self.assertTrue(mc.due_today(conn, datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc)))

    def test_ended_and_paused_are_counted_and_the_end_date_kept(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO contracts (id, title, upwork_status, profile) VALUES"
                         " ('c1', 'ends', 'ACTIVE', 'personal'), ('c2', 'pauses', 'ACTIVE', 'personal')")
        s = _Session({"CLOSED": [_envelope([{"id": "c1", "title": "ends", "status": "CLOSED",
                                             "endDate": "2026-08-20T00:00:00Z",
                                             "clientOrganization": {"name": "X"}}])],
                      "PAUSED": [_envelope([{"id": "c2", "title": "pauses", "status": "PAUSED",
                                             "clientOrganization": {"name": "Y"}}])]})
        m = mc.run(_Ctx(self.db_path), s, ORG, now=self.now)
        self.assertEqual((m["ended"], m["paused"], m["inserted"]), (1, 1, 0))
        rows = {r["id"]: r for r in self.rows("SELECT id, upwork_status, end_dt FROM contracts")}
        self.assertEqual((rows["c1"]["upwork_status"], rows["c1"]["end_dt"]), ("CLOSED", "2026-08-20T00:00:00Z"))
        self.assertEqual(rows["c2"]["upwork_status"], "PAUSED")
        closed_call = next(c for c in s.calls if c[1]["params"]["contract_statuses"] == ["CLOSED"])
        self.assertEqual(closed_call[1]["params"]["end_date_after"], "2026-07-12")   # 45 days back


class TestInsideTheSync(Case):
    class _Rooms:
        def graphql(self, query, variables=None, tolerate_errors=False):
            if "roomList(" in query:
                return {"roomList": {"totalCount": 0, "edges": [], "pageInfo": {"hasNextPage": False}}}
            return {}

    def test_once_a_day_on_the_proposals_session(self):
        fake = _Session({"ACTIVE": _pages()})
        fake.initialize = lambda: None
        out = sync_state.sync(_Ctx(self.db_path), client_factory=lambda: self._Rooms(),
                              mcp_session_factory=lambda: fake)
        self.assertEqual(out["submitted_proposals"]["contracts"]["inserted"], 34)
        out2 = sync_state.sync(_Ctx(self.db_path), client_factory=lambda: self._Rooms(),
                               mcp_session_factory=lambda: fake)
        self.assertEqual(out2["submitted_proposals"]["contracts"], {"status": "not due"})
        self.assertIn("contracts_pass_failed",
                      next(s for s in config.SOURCES if s["task_name"] == "upwork_sync")["degrade_kinds"])
        self.assertEqual(config.MCP_CONTRACT_STATUSES, ("ACTIVE", "PAUSED", "CLOSED"))
        self.assertIn("list_contracts", __import__("modules.upwork.feeds.mcp_client", fromlist=["READ_TOOLS"]).READ_TOOLS)


if __name__ == "__main__":
    unittest.main()
