"""M1b tests — sync_state upserts (terminal protection), poll_inbox
classification (verbatim port cases), outcome composer, client token rules.
GraphQL transport and gateway are mocked; DB logic runs real on temp dirs.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core import config, db, runner
from core import claude_gateway as gw
from modules.upwork import room_link, outcome_capture, poll_inbox, sync_state
from modules.upwork import upwork_client


def iso_hours_ago(h):
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()


class M1bCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m1b-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.tmp / "logs"),
            mock.patch.object(config, "LOCK_DIR", self.tmp / "locks"),
            # pin the gate SHADOW regardless of live config ('upwork' went live
            # at the 2026-07-06 scanner cutover); tests assert shadow behavior.
            mock.patch.dict(config.SHADOW_MODE, {"upwork": True}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self, sql, args=()):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, args)]


# ── sync_state ────────────────────────────────────────────────────────────────
def vp(pid="prop1", status="Pending", title="Make build"):
    return {"id": pid, "status": {"status": status},
            "chargeRate": {"amount": "1200", "currency": "USD"},
            "coverLetter": "hello", "job": {"id": "j1", "title": title},
            "client": {"companyName": "Acme", "country": "US",
                       "totalSpent": {"amount": "5000"}},
            "createdDateTime": "2026-06-01T00:00:00Z",
            "modifiedDateTime": "2026-06-10T00:00:00Z"}


class TestIsoDt(unittest.TestCase):
    def test_epoch_millis_to_iso(self):
        # "1779100550433" -> 2026-04-... ISO (the proposals.created_dt format)
        out = sync_state.iso_dt("1779100550433")
        self.assertTrue(out.startswith("2026-"))
        self.assertIn("T", out)

    def test_epoch_seconds_to_iso(self):
        self.assertTrue(sync_state.iso_dt("1779100550").startswith("20"))

    def test_iso_passthrough(self):
        self.assertEqual(sync_state.iso_dt("2026-06-09T23:12:05+00:00"),
                         "2026-06-09T23:12:05+00:00")

    def test_none_and_empty(self):
        self.assertIsNone(sync_state.iso_dt(None))
        self.assertIsNone(sync_state.iso_dt(""))


def vp_node(pid="p9", status="Submitted", title="Roomless job", rate="1200"):
    """A live vendorProposals node (the 2nd-pass shape, mirrors ROOM_LIST)."""
    return {"id": pid, "coverLetter": "cover", "viewedByClient": True,
            "status": {"status": status, "reason": None},
            "terms": {"chargeRate": {"rawValue": rate, "currency": "USD",
                                     "displayValue": f"${rate}"}},
            "marketplaceJobPosting": {"id": "j" + pid, "content": {"title": title}},
            "organization": {"name": "RoomlessCo", "location": {"country": "US"}},
            "auditDetails": {"createdDateTime": {"rawValue": "2026-06-15T00:00:00Z"},
                             "modifiedDateTime": {"rawValue": "2026-06-16T00:00:00Z"}}}


class FakeClient:
    """Stand-in for UpworkClient.graphql — pages of vendorProposals, or a raise."""
    def __init__(self, pages=None, raises=None):
        self.pages = pages or []
        self.raises = raises
        self.calls = 0

    def graphql(self, query, variables=None, tolerate_errors=False):
        self.calls += 1
        if self.raises:
            raise self.raises
        idx = min(self.calls - 1, len(self.pages) - 1)
        nodes, has_next = self.pages[idx]
        return {"vendorProposals": {
            "totalCount": len(nodes),
            "edges": [{"node": n} for n in nodes],
            "pageInfo": {"hasNextPage": has_next, "endCursor": str(self.calls)}}}


class _Ctx:
    def __init__(self, db_path):
        self.db_path = db_path
        self.logs = []

    def log(self, m):
        self.logs.append(m)


class TestSubmittedProposals(M1bCase):
    def test_scope_blocked_auto_skips(self):
        # the v2 token lacks the scope -> INACTIVE, never fails, no rows touched
        client = FakeClient(raises=Exception(
            "Query.vendorProposals — doesn't have enough oauth2 permissions"))
        out = sync_state.sync_submitted_proposals(_Ctx(self.db_path), client)
        self.assertEqual(out["status"], "scope_blocked")
        self.assertTrue(out["armed"])
        self.assertEqual(self.rows("SELECT COUNT(*) c FROM proposals")[0]["c"], 0)

    def test_active_inserts_roomless(self):
        client = FakeClient(pages=[([vp_node(pid="roomless1")], False)])
        out = sync_state.sync_submitted_proposals(_Ctx(self.db_path), client)
        self.assertEqual(out["status"], "active")
        r = self.rows("SELECT id, thread_id, job_title, charge_rate FROM"
                      " proposals WHERE id='roomless1'")[0]
        self.assertIsNone(r["thread_id"])              # roomless
        self.assertEqual(r["job_title"], "Roomless job")
        self.assertEqual(r["charge_rate"], 1200.0)

    def test_room_having_keeps_room_gets_telemetry(self):
        # the 1st (roomList) pass already inserted this WITH a room
        with db.connect(self.db_path) as conn:
            sync_state.upsert_proposal(conn, vp(pid="hasroom", status="Submitted"),
                                       "thread-77")
        client = FakeClient(pages=[([vp_node(pid="hasroom", status="Viewed")], False)])
        sync_state.sync_submitted_proposals(_Ctx(self.db_path), client)
        r = self.rows("SELECT thread_id, upwork_status FROM proposals"
                      " WHERE id='hasroom'")[0]
        self.assertEqual(r["thread_id"], "thread-77")  # room PRESERVED (COALESCE)
        self.assertEqual(r["upwork_status"], "Viewed")  # telemetry filled

    def test_terminal_preserved(self):
        with db.connect(self.db_path) as conn:
            sync_state.upsert_proposal(conn, vp(pid="won1", status="Won"), "t1")
            conn.execute("UPDATE proposals SET status='Won' WHERE id='won1'")
        client = FakeClient(pages=[([vp_node(pid="won1", status="Declined")], False)])
        sync_state.sync_submitted_proposals(_Ctx(self.db_path), client)
        self.assertEqual(self.rows("SELECT status FROM proposals WHERE id='won1'")
                         [0]["status"], "Won")          # never flipped

    def test_pagination_walks_pages(self):
        client = FakeClient(pages=[([vp_node(pid="a")], True),
                                   ([vp_node(pid="b")], False)])
        out = sync_state.sync_submitted_proposals(_Ctx(self.db_path), client)
        self.assertEqual(out["seen"], 2)
        self.assertEqual(client.calls, 2)

    def test_normalizer_maps_fields(self):
        p = sync_state._vp_node_to_proposal(vp_node(pid="n1", title="T"))
        self.assertEqual(p["id"], "n1")
        self.assertEqual(p["status"]["status"], "Submitted")
        self.assertEqual(p["job"], {"id": "jn1", "title": "T"})
        self.assertEqual(p["client"]["companyName"], "RoomlessCo")
        self.assertEqual(p["createdDateTime"], "2026-06-15T00:00:00Z")

    def test_normalizer_passes_flat_shape_through(self):
        # a dict that is already flat (tests, pre-normalised callers) survives a
        # second normalisation unchanged — the normaliser is safe to call twice
        p = sync_state._vp_node_to_proposal(vp(pid="f1", title="Flat"))
        self.assertEqual(p["chargeRate"], {"amount": "1200", "currency": "USD"})
        self.assertEqual(p["job"], {"id": "j1", "title": "Flat"})
        self.assertEqual(p["client"]["companyName"], "Acme")
        self.assertEqual(p["client"]["totalSpent"], {"amount": "5000"})
        self.assertEqual(p["createdDateTime"], "2026-06-01T00:00:00Z")
        self.assertEqual(p["modifiedDateTime"], "2026-06-10T00:00:00Z")
        self.assertEqual(p["status"]["status"], "Pending")


# ── pass 1 (ROOM_LIST) shape — the 2026-06-30..08-25 NULL-write bug ──────────
def vp_room_node(pid="rp1", status="Activated", title="Room job", rate="55",
                 created="2026-07-02T10:00:00Z", modified="2026-08-20T09:00:00Z"):
    """A vendorProposal node exactly as ROOM_LIST returns it (queries.py:47-60):
    nested terms/marketplaceJobPosting/auditDetails, NO organization/client."""
    return {"id": pid, "coverLetter": "cover text",
            "status": {"status": status},
            "terms": {"chargeRate": {"rawValue": rate, "currency": "USD",
                                     "displayValue": f"${rate}"}},
            "marketplaceJobPosting": {"id": "job-" + pid,
                                      "content": {"title": title}},
            "auditDetails": {"createdDateTime": {"rawValue": created},
                             "modifiedDateTime": {"rawValue": modified}}}


def room_list_node(rid="room_a", name="Amanda Thomas, Amanda T", vp=None,
                   contract=True):
    """One ROOM_LIST edge node: contract nests the job title under
    job.content.title and selects no hourlyChargeRate (queries.py:38-46)."""
    r = {"id": rid, "roomName": name, "topic": "Topic", "roomType": "ROOM",
         "numUnread": 0, "offerIds": [],
         "latestStory": {"id": "s1", "message": "hi",
                         "createdDateTime": "2026-08-20T09:00:00Z"}}
    if vp is not None:
        r["vendorProposal"] = vp
    if contract:
        r["contract"] = {"id": "c-" + rid, "title": "Airtable build",
                         "status": "ACTIVE", "contractType": "HOURLY",
                         "startDateTime": "2026-07-03T00:00:00Z",
                         "endDateTime": None,
                         "job": {"id": "job-c", "content": {"title": "Airtable build"}}}
    return r


class FakeRoomClient:
    """UpworkClient stand-in for sync(): ROOM_LIST -> the given rooms,
    vendorProposals -> scope error (as live), transactions -> empty envelope."""
    def __init__(self, rooms):
        self.rooms = rooms

    def graphql(self, query, variables=None, tolerate_errors=False):
        if "roomList(" in query:
            return {"roomList": {"totalCount": len(self.rooms),
                                 "edges": [{"node": r} for r in self.rooms],
                                 "pageInfo": {"hasNextPage": False,
                                              "endCursor": "0"}}}
        if "vendorProposals(" in query:
            raise Exception("Query.vendorProposals — doesn't have enough"
                            " oauth2 permissions/scopes")
        return {"data": {"transactionHistory": {"transactionDetail": {
            "transactionHistoryRow": []}}}, "errors": []}


class TestRoomListPass(M1bCase):
    """Pass 1 handed the raw nested node to upsert_proposal from 2026-06-30 to
    2026-08-25: live DB showed 9/9 v3-era proposals with NULL created_dt,
    job_title, charge_rate, modified_dt and all 59 contracts with NULL
    job_title. These pin the normalised path and the fill-or-keep updates."""

    def _sync(self, rooms):
        return sync_state.sync(_Ctx(self.db_path),
                               client_factory=lambda: FakeRoomClient(rooms),
                               mcp_session_factory=None)      # the MCP pass has its own tests

    def test_roomlist_pass_populates_proposal_telemetry(self):
        out = self._sync([room_list_node(vp=vp_room_node())])
        self.assertEqual(out["proposals"], {"inserted": 1})
        r = self.rows("SELECT * FROM proposals WHERE id='rp1'")[0]
        self.assertEqual(r["job_title"], "Room job")
        self.assertEqual(r["charge_rate"], 55.0)
        self.assertEqual(r["charge_currency"], "USD")
        self.assertEqual(r["marketplace_job_id"], "job-rp1")
        self.assertEqual(r["created_dt"], "2026-07-02T10:00:00Z")
        self.assertEqual(r["modified_dt"], "2026-08-20T09:00:00Z")
        self.assertEqual(r["upwork_status"], "Activated")
        self.assertEqual(r["cover_letter"], "cover text")
        self.assertEqual(r["thread_id"], "room_a")
        self.assertEqual(r["client_company"], "Amanda Thomas, Amanda T")
        self.assertIsNone(r["status"])                 # verdict is 0.3's job

    def test_roomlist_pass_fills_contract_title_from_nested_job(self):
        self._sync([room_list_node(vp=vp_room_node())])
        c = self.rows("SELECT * FROM contracts WHERE id='c-room_a'")[0]
        self.assertEqual(c["job_title"], "Airtable build")
        self.assertEqual(c["job_id"], "job-c")
        self.assertEqual(c["upwork_status"], "ACTIVE")
        self.assertEqual(c["client_company"], "Amanda Thomas, Amanda T")
        self.assertIsNone(c["hourly_rate"])   # ROOM_LIST selects none; stays NULL

    def test_roomlist_pass_heals_pre_fix_rows(self):
        # rows as the bug left them: identity + status only, telemetry NULL
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO proposals (id, upwork_status, thread_id,"
                         " client_company) VALUES ('rp1','Activated','room_a',"
                         " 'Amanda Thomas, Amanda T')")
            conn.execute("INSERT INTO proposals (id, upwork_status, status,"
                         " thread_id) VALUES ('rpw','Hired','Won','room_w')")
            conn.execute("INSERT INTO contracts (id, upwork_status, thread_id)"
                         " VALUES ('c-room_a','ACTIVE','room_a')")
        out = self._sync([
            room_list_node(vp=vp_room_node()),
            room_list_node(rid="room_w", name="Won Person", contract=False,
                           vp=vp_room_node(pid="rpw", status="Hired",
                                           title="Won job", rate="70",
                                           created="2026-05-01T00:00:00Z"))])
        self.assertEqual(out["proposals"],
                         {"updated": 1, "terminal-preserved": 1})
        r = self.rows("SELECT * FROM proposals WHERE id='rp1'")[0]
        self.assertEqual((r["job_title"], r["charge_rate"], r["created_dt"],
                          r["modified_dt"], r["marketplace_job_id"]),
                         ("Room job", 55.0, "2026-07-02T10:00:00Z",
                          "2026-08-20T09:00:00Z", "job-rp1"))
        w = self.rows("SELECT * FROM proposals WHERE id='rpw'")[0]
        self.assertEqual(w["status"], "Won")                    # untouched
        self.assertEqual(w["job_title"], "Won job")             # filled
        self.assertEqual(w["created_dt"], "2026-05-01T00:00:00Z")
        self.assertEqual(w["charge_rate"], 70.0)
        c = self.rows("SELECT job_title FROM contracts WHERE id='c-room_a'")[0]
        self.assertEqual(c["job_title"], "Airtable build")

    def test_created_dt_is_backfilled_not_overwritten(self):
        self._sync([room_list_node(vp=vp_room_node(created="2026-07-02T10:00:00Z"))])
        # a later payload disagreeing on created_dt must not move it; modified moves
        self._sync([room_list_node(vp=vp_room_node(created="2026-07-09T10:00:00Z",
                                                   modified="2026-08-21T00:00:00Z"))])
        r = self.rows("SELECT created_dt, modified_dt FROM proposals WHERE id='rp1'")[0]
        self.assertEqual(r["created_dt"], "2026-07-02T10:00:00Z")
        self.assertEqual(r["modified_dt"], "2026-08-21T00:00:00Z")

    def test_update_never_nulls_stored_telemetry(self):
        # the exact hourly damage: a payload lacking title/rate/dates used to
        # overwrite stored values with NULL. Now: fill-or-keep.
        with db.connect(self.db_path) as conn:
            sync_state.upsert_proposal(conn, vp(pid="keep", status="Pending"), "t1")
            r = sync_state.upsert_proposal(
                conn, {"id": "keep", "status": {"status": "Activated"}}, "t1")
        self.assertEqual(r, "updated")
        row = self.rows("SELECT * FROM proposals WHERE id='keep'")[0]
        self.assertEqual(row["upwork_status"], "Activated")     # telemetry moved
        self.assertEqual(row["job_title"], "Make build")        # kept
        self.assertEqual(row["charge_rate"], 1200.0)            # kept
        self.assertEqual(row["created_dt"], "2026-06-01T00:00:00Z")
        self.assertEqual(row["modified_dt"], "2026-06-10T00:00:00Z")
        self.assertEqual(row["client_company"], "Acme")         # kept (no room)
        self.assertEqual(row["cover_letter"], "hello")
        # unchanged precedence: a room name still names the counterparty
        with db.connect(self.db_path) as conn:
            sync_state.upsert_proposal(
                conn, {"id": "keep", "status": {"status": "Activated"}}, "t1",
                room_name="Some Person")
        self.assertEqual(self.rows("SELECT client_company FROM proposals"
                                   " WHERE id='keep'")[0]["client_company"],
                         "Some Person")

    def test_terminal_row_fills_missing_telemetry_only(self):
        with db.connect(self.db_path) as conn:
            v = vp(pid="won2", status="Hired", title="Original title")
            sync_state.upsert_proposal(conn, v, "t1")
            conn.execute("UPDATE proposals SET status='Won', created_dt=NULL"
                         " WHERE id='won2'")
            r = sync_state.upsert_proposal(
                conn, vp(pid="won2", status="Declined", title="Renamed"), "t1")
        self.assertEqual(r, "terminal-preserved")
        row = self.rows("SELECT * FROM proposals WHERE id='won2'")[0]
        self.assertEqual(row["status"], "Won")
        self.assertEqual(row["upwork_status"], "Declined")
        self.assertEqual(row["job_title"], "Original title")   # not renamed
        self.assertEqual(row["created_dt"], "2026-06-01T00:00:00Z")  # filled

    def test_contract_update_keeps_hourly_rate_and_title(self):
        with db.connect(self.db_path) as conn:
            sync_state.upsert_contract(
                conn, {"id": "cK", "title": "Build", "status": "ACTIVE",
                       "hourlyChargeRate": {"amount": "51.5"},
                       "job": {"id": "j1", "title": "Build job"}},
                "t1", profile="personal", room_name="Brandi Jordan")
            sync_state.upsert_contract(
                conn, {"id": "cK", "title": "Build", "status": "PAUSED",
                       "job": {"id": "j1", "content": {}}},
                "t1", profile="personal", room_name="Brandi Jordan")
        c = self.rows("SELECT * FROM contracts WHERE id='cK'")[0]
        self.assertEqual(c["upwork_status"], "PAUSED")
        self.assertEqual(c["hourly_rate"], 51.5)
        self.assertEqual(c["job_title"], "Build job")


class TestSyncUpserts(M1bCase):
    def test_proposal_stores_iso_not_epoch(self):
        with db.connect(self.db_path) as conn:
            sync_state.upsert_proposal(
                conn, vp(pid="pX", status="Submitted"), "t1")
            # simulate Upwork handing epoch-millis like v2 does
            v = vp(pid="pE", status="Submitted")
            v["createdDateTime"] = "1779100550433"
            sync_state.upsert_proposal(conn, v, "t2")
            row = conn.execute("SELECT created_dt FROM proposals WHERE id='pE'"
                               ).fetchone()
        self.assertTrue(row["created_dt"].startswith("2026-"))  # ISO, not epoch
        self.assertNotIn("17791", row["created_dt"])

    def test_room_name_fills_client_company_when_payload_has_none(self):
        # ROOM_LIST's contract/proposal nodes carry no client, so the room
        # display name is the only client identity — never leave a row nameless
        # (2026-07-30: 57 contracts + 79 proposals had empty client_company)
        with db.connect(self.db_path) as conn:
            v = vp(pid="pRN", status="Submitted")
            v["client"] = {}                     # payload carries no company
            sync_state.upsert_proposal(conn, v, "t1",
                                       room_name="Amanda Thomas, Amanda T")
            sync_state.upsert_contract(
                conn, {"id": "cRN", "title": "Airtable build", "status": "ACTIVE",
                       "job": {"id": "j1", "title": "Airtable"}},
                "t1", profile="personal", room_name="Brandi Jordan")
            p = conn.execute("SELECT client_company FROM proposals WHERE id='pRN'").fetchone()
            c = conn.execute("SELECT client_company FROM contracts WHERE id='cRN'").fetchone()
        self.assertEqual(p["client_company"], "Amanda Thomas, Amanda T")
        self.assertEqual(c["client_company"], "Brandi Jordan")

    def test_payload_company_wins_over_room_name(self):
        # if Upwork ever DOES supply companyName, it takes precedence
        with db.connect(self.db_path) as conn:
            v = vp(pid="pC", status="Submitted")
            v["client"] = {"companyName": "Real Co Ltd"}
            sync_state.upsert_proposal(conn, v, "t1", room_name="Some Person")
            row = conn.execute("SELECT client_company FROM proposals WHERE id='pC'").fetchone()
        self.assertEqual(row["client_company"], "Real Co Ltd")

    def test_proposal_insert_then_update(self):
        with db.connect(self.db_path) as conn:
            self.assertEqual(sync_state.upsert_proposal(conn, vp(), "t1"),
                             "inserted")
            self.assertEqual(
                sync_state.upsert_proposal(conn, vp(status="Active"), "t1"),
                "updated")
        row = self.rows("SELECT * FROM proposals")[0]
        self.assertEqual(row["upwork_status"], "Active")
        self.assertEqual(row["thread_id"], "t1")

    def test_terminal_status_never_flipped(self):
        with db.connect(self.db_path) as conn:
            sync_state.upsert_proposal(conn, vp(), "t1")
            conn.execute("UPDATE proposals SET status='Won' WHERE id='prop1'")
        with db.connect(self.db_path) as conn:
            r = sync_state.upsert_proposal(conn, vp(status="Declined"), "t1")
        self.assertEqual(r, "terminal-preserved")
        row = self.rows("SELECT status, upwork_status FROM proposals")[0]
        self.assertEqual(row["status"], "Won")            # verdict permanent
        self.assertEqual(row["upwork_status"], "Declined")  # telemetry moves

    def test_no_stub_twin_canonical_key(self):
        # M1 bug #2 fix: a synced txn keys on the CANONICAL namespace, never a
        # `personal:`/`agency:` twin, and dedups against an existing canonical/CSV row.
        row = {"recordId": "T1", "type": "APInvoice", "amount": {"amount": "100"},
               "createdDateTime": "2026-06-10"}
        with db.connect(self.db_path) as conn:
            self.assertEqual(sync_state.upsert_transaction(conn, row, "personal"), "inserted")
            r = conn.execute("SELECT record_id, canonical_key FROM transactions"
                             " WHERE record_id='T1'").fetchone()
            self.assertEqual(r["record_id"], "T1")               # not 'personal:T1'
            self.assertEqual(r["canonical_key"], "upwork:T1")
            self.assertEqual(conn.execute("SELECT COUNT(*) c FROM transactions"
                             " WHERE record_id LIKE 'personal:%'").fetchone()["c"], 0)
            # re-sync (same rid) dedups; agency re-sync of the SAME rid also dedups
            self.assertEqual(sync_state.upsert_transaction(conn, row, "personal"), "updated")
            self.assertEqual(sync_state.upsert_transaction(conn, row, "agency"), "updated")
            # a pre-existing canonical row (CSV-style) is matched, not twinned
            conn.execute("INSERT INTO transactions (record_id, canonical_key, amount_minor,"
                         " currency) VALUES ('T2','upwork:T2',5000,'USD')")
            self.assertEqual(sync_state.upsert_transaction(
                conn, {"recordId": "T2", "type": "APInvoice", "amount": {"amount": "50"}},
                "personal"), "updated")
            self.assertEqual(conn.execute("SELECT COUNT(*) c FROM transactions"
                             " WHERE canonical_key='upwork:T2'").fetchone()["c"], 1)

    def test_fee_sign_is_debit(self):
        # M1 bug #3 fix: fees/charges/withdrawals stored NEGATIVE; earnings POSITIVE
        with db.connect(self.db_path) as conn:
            sync_state.upsert_transaction(conn, {"recordId": "C1", "type": "ARInvoice",
                "subtype": "Membership Fee", "amount": {"amount": "45"}}, "personal")
            sync_state.upsert_transaction(conn, {"recordId": "E1", "type": "APInvoice",
                "subtype": "Hourly", "amount": {"amount": "1200"}}, "personal")
            sync_state.upsert_transaction(conn, {"recordId": "W1", "type": "APPayment",
                "subtype": "Withdrawal", "amount": {"amount": "2000"}}, "personal")
            amt = {r["record_id"]: r["amount_minor"] for r in conn.execute(
                "SELECT record_id, amount_minor FROM transactions")}
        self.assertEqual(amt["C1"], -4500)      # connect fee -> debit
        self.assertEqual(amt["E1"], 120000)     # hourly earning -> credit
        self.assertEqual(amt["W1"], -200000)    # withdrawal -> debit

    def test_amount_coalesce_fill_not_clobber(self):
        # M1 bug #1 fix: UPDATE COALESCE-fills an EXISTING row's NULL amount_minor
        # but never overwrites a good one. (A NULL-amount row only ever exists as a
        # pre-seeded canonical/CSV row now — bug #4 stops the sync minting them.)
        with db.connect(self.db_path) as conn:
            # pre-existing canonical row (CSV-style) with no amount yet
            conn.execute("INSERT INTO transactions (record_id, canonical_key, currency)"
                         " VALUES ('M1','upwork:M1','USD')")
            self.assertIsNone(conn.execute("SELECT amount_minor FROM transactions"
                              " WHERE record_id='M1'").fetchone()["amount_minor"])
            # sync now carries the amount -> COALESCE fills it
            sync_state.upsert_transaction(conn, {"recordId": "M1", "type": "APInvoice",
                "amount": {"amount": "300"}}, "personal")
            self.assertEqual(conn.execute("SELECT amount_minor FROM transactions"
                             " WHERE record_id='M1'").fetchone()["amount_minor"], 30000)
            # a corrected value is NOT clobbered by a later (buggy) sync amount
            conn.execute("UPDATE transactions SET amount_minor=99999 WHERE record_id='M1'")
            sync_state.upsert_transaction(conn, {"recordId": "M1", "type": "APInvoice",
                "amount": {"amount": "300"}}, "personal")
            self.assertEqual(conn.execute("SELECT amount_minor FROM transactions"
                             " WHERE record_id='M1'").fetchone()["amount_minor"], 99999)

    def _q(self, conn, canonical, resolved=None):
        clause = "" if resolved is None else (
            " AND resolved_at IS NOT NULL" if resolved else " AND resolved_at IS NULL")
        return conn.execute(
            "SELECT COUNT(*) c FROM quarantine WHERE source='upwork'"
            " AND reason LIKE ?" + clause, (f"%({canonical})%",)).fetchone()["c"]

    def test_missing_amount_quarantined_not_inserted(self):
        # M1 bug #4 fix: a NEW txn with no amount is QUARANTINED, never inserted as
        # a NULL-amount row (which would trip doctor #8 and break the GREEN gate).
        with db.connect(self.db_path) as conn:
            r = sync_state.upsert_transaction(conn, {"recordId": "Q1", "type": "APInvoice"}, "agency")
            self.assertEqual(r, "quarantined")
            self.assertEqual(conn.execute("SELECT COUNT(*) c FROM transactions"
                             " WHERE record_id='Q1'").fetchone()["c"], 0)   # no live row
            self.assertEqual(self._q(conn, "upwork:Q1", resolved=False), 1)
            # doctor #8 stays GREEN — no NULL-amount row in the live ledger
            self.assertEqual(conn.execute("SELECT COUNT(*) c FROM transactions"
                             " WHERE amount_minor IS NULL").fetchone()["c"], 0)

    def test_requarantine_idempotent(self):
        # the agency-auth gap persists until re-OAuth: the same amountless row recurs
        # every sync and must map to ONE quarantine entry, refreshed not appended.
        with db.connect(self.db_path) as conn:
            self.assertEqual(sync_state.upsert_transaction(
                conn, {"recordId": "Q2", "type": "APInvoice"}, "agency"), "quarantined")
            for _ in range(3):
                self.assertEqual(sync_state.upsert_transaction(
                    conn, {"recordId": "Q2", "type": "APInvoice"}, "agency"), "quarantine-refreshed")
            self.assertEqual(self._q(conn, "upwork:Q2"), 1)                 # exactly one entry
            self.assertEqual(self._q(conn, "upwork:Q2", resolved=False), 1)

    def test_quarantine_recovery_roundtrip(self):
        # quarantined (no amount) -> later sync WITH amount -> inserted once +
        # quarantine entry resolved + no duplicate transaction.
        with db.connect(self.db_path) as conn:
            self.assertEqual(sync_state.upsert_transaction(
                conn, {"recordId": "Q3", "type": "APInvoice"}, "agency"), "quarantined")
            self.assertEqual(self._q(conn, "upwork:Q3", resolved=False), 1)
            # recovery sync now carries the amount
            self.assertEqual(sync_state.upsert_transaction(
                conn, {"recordId": "Q3", "type": "APInvoice", "amount": {"amount": "300"}},
                "agency"), "inserted")
            rows = conn.execute("SELECT amount_minor FROM transactions"
                                " WHERE canonical_key='upwork:Q3'").fetchall()
            self.assertEqual(len(rows), 1)                                  # inserted once, no dup
            self.assertEqual(rows[0]["amount_minor"], 30000)
            self.assertEqual(self._q(conn, "upwork:Q3", resolved=False), 0) # pending entry cleared
            self.assertEqual(self._q(conn, "upwork:Q3", resolved=True), 1)  # resolved, not dropped
            self.assertEqual(conn.execute("SELECT resolution FROM quarantine"
                             " WHERE reason LIKE '%(upwork:Q3)%'").fetchone()["resolution"], "imported")

    def test_csv_path_resolves_api_minted_quarantine(self):
        # CROSS-PATH CONTRACT: the API sync mints the missing-amount quarantine; the
        # weekly CSV-import path (ledger.resolve_missing_amount) must resolve THAT
        # SAME entry. Pins the two paths to one identity so they can't drift.
        from modules.finance import ledger
        with db.connect(self.db_path) as conn:
            # API sync sees an amountless agency txn -> quarantines it
            self.assertEqual(sync_state.upsert_transaction(
                conn, {"recordId": "931008627", "type": "APInvoice"}, "agency"), "quarantined")
            self.assertEqual(self._q(conn, "upwork:931008627", resolved=False), 1)
            # CSV later carries the amount -> the SHARED resolver flips that entry
            n = ledger.resolve_missing_amount(conn, "upwork:931008627")
            self.assertEqual(n, 1)
            self.assertEqual(self._q(conn, "upwork:931008627", resolved=False), 0)
            self.assertEqual(self._q(conn, "upwork:931008627", resolved=True), 1)
            # idempotent: a re-import (or the later scoped API sync) resolves nothing more
            self.assertEqual(ledger.resolve_missing_amount(conn, "upwork:931008627"), 0)

    def test_phantom_tag_is_kept_by_sync(self):
        # API-only phantom: an amountless ref never in any CSV. Once a human re-tags
        # its entry phantom, the sync must KEEP it phantom (not revert to a fresh
        # 'recover from CSV' entry) so it is not mistaken for a pending recovery.
        from modules.finance import ledger
        with db.connect(self.db_path) as conn:
            self.assertEqual(sync_state.upsert_transaction(
                conn, {"recordId": "PH1", "type": "APInvoice"}, "agency"), "quarantined")
            # human classification: re-tag to phantom
            conn.execute("UPDATE quarantine SET reason=? WHERE reason LIKE"
                         " 'upwork txn missing amount (upwork:PH1)%'",
                         (sync_state.phantom_reason("upwork:PH1"),))
            # the recurring sync keeps it phantom, refreshes the SAME entry
            self.assertEqual(sync_state.upsert_transaction(
                conn, {"recordId": "PH1", "type": "APInvoice"}, "agency"), "phantom-refreshed")
            self.assertEqual(self._q(conn, "upwork:PH1", resolved=False), 1)   # one entry, not doubled
            # it never reads as a CSV-pending recovery, and the CSV resolver ignores it
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) c FROM quarantine WHERE reason LIKE"
                " 'upwork txn missing amount (upwork:PH1)%'").fetchone()["c"], 0)
            self.assertEqual(ledger.pending_missing_amount(conn, "upwork:PH1"), 0)
            self.assertEqual(ledger.resolve_missing_amount(conn, "upwork:PH1"), 0)
            # still no live row (GREEN-safe)
            self.assertEqual(conn.execute("SELECT COUNT(*) c FROM transactions"
                             " WHERE record_id='PH1'").fetchone()["c"], 0)


# ── poll_inbox classification (verbatim cases) ────────────────────────────────
def room(rid="r1", contract_status=None, proposal_status=None,
         latest_hours=24):
    r = {"id": rid, "roomName": "Room", "topic": "Topic",
         "latestStory": {"id": "s-l",
                         "createdDateTime": iso_hours_ago(latest_hours)}}
    if contract_status:
        r["contract"] = {"id": "c1", "status": contract_status}
    if proposal_status:
        r["vendorProposal"] = {"id": "p1",
                               "status": {"status": proposal_status}}
    return r


class TestClassification(M1bCase):
    def seed_messages(self, thread_id, directions, hours_start=100):
        """directions oldest->newest; timestamps ascending."""
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT OR IGNORE INTO threads (id) VALUES (?)",
                         (thread_id,))
            for i, d in enumerate(directions):
                conn.execute(
                    "INSERT INTO messages (id, thread_id, message,"
                    " created_dt, sender_user_id, direction)"
                    " VALUES (?,?,?,?,?,?)",
                    (f"{thread_id}-m{i}", thread_id, f"msg {i}",
                     iso_hours_ago(hours_start - i * 10),
                     config.UPWORK_USER_ID if d == "outbound" else "client9",
                     d))

    def test_tiers(self):
        self.assertEqual(poll_inbox.compute_tier(
            room(contract_status="ACTIVE")), "HOT")
        self.assertEqual(poll_inbox.compute_tier(
            room(proposal_status="Offered")), "HOT")
        self.assertEqual(poll_inbox.compute_tier(
            room(proposal_status="Pending")), "WARM")
        self.assertEqual(poll_inbox.compute_tier(
            room(contract_status="CLOSED")), "COLD")
        self.assertEqual(poll_inbox.compute_tier(room()), "UNKNOWN")

    def test_owed_when_client_sent_last(self):
        self.seed_messages("r1", ["outbound", "inbound"])
        with db.connect(self.db_path) as conn:
            r = room(proposal_status="Pending", latest_hours=20)
            awaiting = poll_inbox.compute_awaiting(conn, r)
            self.assertEqual(awaiting, "Abhijeet")
            bucket, _ = poll_inbox.classify_thread(conn, r, "WARM", awaiting)
        self.assertEqual(bucket, "owed")

    def test_followup_thresholds_by_tier(self):
        self.seed_messages("r1", ["inbound", "outbound"])
        with db.connect(self.db_path) as conn:
            r = room(rid="r1", contract_status="ACTIVE", latest_hours=50)
            b, _ = poll_inbox.classify_thread(conn, r, "HOT", "Client")
            self.assertEqual(b, "followup")          # 50h > 48h HOT
            r2 = room(rid="r1", proposal_status="Pending", latest_hours=50)
            b2, _ = poll_inbox.classify_thread(conn, r2, "WARM", "Client")
            self.assertEqual(b2, "healthy")          # 50h < 72h WARM

    def test_max_two_consecutive_outbound(self):
        self.seed_messages("r1", ["inbound", "outbound", "outbound"])
        with db.connect(self.db_path) as conn:
            r = room(rid="r1", contract_status="ACTIVE", latest_hours=100)
            b, reason = poll_inbox.classify_thread(conn, r, "HOT", "Client")
        self.assertEqual(b, "healthy")
        self.assertIn("2 prior followups", reason)

    def test_too_soon_under_12h(self):
        self.seed_messages("r1", ["inbound", "outbound"])
        with db.connect(self.db_path) as conn:
            r = room(rid="r1", contract_status="ACTIVE", latest_hours=6)
            b, _ = poll_inbox.classify_thread(conn, r, "HOT", "Client")
        self.assertEqual(b, "healthy")

    def test_snooze_respected(self):
        self.seed_messages("r1", ["inbound"])
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE threads SET snooze_until=date('now','+3 day')"
                         " WHERE id='r1'")
        with db.connect(self.db_path) as conn:
            b, _ = poll_inbox.classify_thread(
                conn, room(rid="r1", latest_hours=99), "WARM", "Abhijeet")
        self.assertEqual(b, "snoozed")

    def test_stale_thread_left_alone(self):
        self.seed_messages("r1", ["inbound"], hours_start=31 * 24)
        with db.connect(self.db_path) as conn:
            r = room(rid="r1", latest_hours=31 * 24)
            awaiting = poll_inbox.compute_awaiting(conn, r)
            self.assertEqual(awaiting, "None")
            b, _ = poll_inbox.classify_thread(conn, r, "UNKNOWN", awaiting)
        self.assertEqual(b, "hard-skip")


# ── poll task end-to-end (mocked transport + gateway) ─────────────────────────
class TestPollTask(M1bCase):
    def test_digest_intent_recorded_in_shadow(self):
        r = room(rid="r1", proposal_status="Pending", latest_hours=20)
        list_resp = {"roomList": {"edges": [{"node": r}]}}
        stories_resp = {"room": {"stories": {"edges": [
            {"node": {"id": "s1", "message": "we sent",
                      "createdDateTime": iso_hours_ago(40),
                      "user": {"id": config.UPWORK_USER_ID, "name": "A"}}},
            {"node": {"id": "s2", "message": "client replied",
                      "createdDateTime": iso_hours_ago(20),
                      "user": {"id": "client9", "name": "C"}}},
        ]}}}

        fake_client = mock.Mock()
        # stories fetch uses tolerate_errors=True -> full {data, errors} envelope
        fake_client.graphql.side_effect = [list_resp,
                                           {"data": stories_resp}]

        def fake_claude(**kw):
            if kw.get("schema"):
                return gw.GatewayResult(
                    text="{}", parsed={"intent": "new_lead",
                                       "summary": "prospect asks for quote"},
                    model="m", route="payg", usage={}, cost_usd=0.001,
                    stop_reason="end_turn", duration_ms=5)
            return gw.GatewayResult(
                text="Thanks for the reply — here's the next step.",
                parsed=None, model="m", route="payg", usage={},
                cost_usd=0.001, stop_reason="end_turn", duration_ms=5)

        with mock.patch.object(runner, "claude_call",
                               side_effect=lambda **kw: fake_claude(**kw)):
            result = runner.run_task(
                poll_inbox.TASK_NAME,
                lambda ctx: poll_inbox.poll(
                    ctx, client_factory=lambda **k: fake_client),
                module="upwork", db_path=self.db_path)

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metrics["buckets"].get("owed"), 1)
        self.assertEqual(result.metrics["drafts"], 1)
        drafts = self.rows("SELECT * FROM drafts")
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["draft_kind"], "owed")
        ledger = self.rows("SELECT * FROM shadow_ledger WHERE target='email'")
        self.assertEqual(len(ledger), 1)
        payload = json.loads(ledger[0]["payload_json"])
        self.assertEqual(payload["to"], config.NOTIFY_EMAIL)
        self.assertEqual(payload["items"][0]["intent"]["intent"], "new_lead")


class TestPollRefreshesStaleDrafts(M1bCase):
    """A pending draft that predates the client's newest message answers an
    older message. Abhijeet, 2026-08-26 04:48, on the Inbox after "Check
    Upwork now": "it is not pulling the latest" — Nabil's 20:16 message sat
    under a draft written on the 19th, because the poll skipped every room
    that already had a pending draft in the same bucket."""

    def _poll(self, stories, fake_draft="Fresh draft for the new message."):
        r = room(rid="r1", proposal_status="Pending", latest_hours=1)
        list_resp = {"roomList": {"edges": [{"node": r}]}}
        stories_resp = {"room": {"stories": {"edges": [{"node": st} for st in stories]}}}
        fake_client = mock.Mock()
        fake_client.graphql.side_effect = [list_resp, {"data": stories_resp}]

        def fake_claude(**kw):
            if kw.get("schema"):
                return gw.GatewayResult(text="{}", parsed={"intent": "new_lead", "summary": "s"},
                                        model="m", route="payg", usage={}, cost_usd=0.001,
                                        stop_reason="end_turn", duration_ms=5)
            return gw.GatewayResult(text=fake_draft, parsed=None, model="m", route="payg",
                                    usage={}, cost_usd=0.001, stop_reason="end_turn", duration_ms=5)
        with mock.patch.object(runner, "claude_call", side_effect=lambda **kw: fake_claude(**kw)):
            return runner.run_task(
                poll_inbox.TASK_NAME,
                lambda ctx: poll_inbox.poll(ctx, client_factory=lambda **k: fake_client),
                module="upwork", db_path=self.db_path)

    def test_a_newer_client_message_supersedes_the_waiting_draft(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, room_name, topic) VALUES ('r1','Room','Topic')")
            conn.execute("INSERT INTO drafts (thread_id, draft_kind, tier, body, word_count,"
                         " sent_status, generated_at) VALUES ('r1','owed','HOT',"
                         " 'Old draft for the old message', 6, 'pending', '2026-08-19 04:45:00')")
        stories = [
            {"id": "s1", "message": "we sent", "createdDateTime": "2026-08-18T16:58:00.000Z",
             "user": {"id": config.UPWORK_USER_ID, "name": "A"}},
            {"id": "s2", "message": "old ask", "createdDateTime": "2026-08-18T20:00:00.000Z",
             "user": {"id": "client9", "name": "C"}},
            {"id": "s3", "message": "I sent you an email with a brief",
             "createdDateTime": "2026-08-25T14:46:17.390Z", "user": {"id": "client9", "name": "C"}},
        ]
        result = self._poll(stories)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metrics["drafts_refreshed"], 1)
        self.assertEqual(result.metrics["drafts"], 1)
        drafts = self.rows("SELECT body, sent_status FROM drafts ORDER BY id")
        self.assertEqual([(d["body"][:9], d["sent_status"]) for d in drafts],
                         [("Old draft", "superseded"), ("Fresh dra", "pending")])

    def test_a_draft_newer_than_their_last_message_is_kept(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, room_name, topic) VALUES ('r1','Room','Topic')")
            conn.execute("INSERT INTO drafts (thread_id, draft_kind, tier, body, word_count,"
                         " sent_status, generated_at) VALUES ('r1','owed','HOT',"
                         " 'Still the right draft', 4, 'pending', '2026-08-25 15:00:00')")
        stories = [
            {"id": "s1", "message": "we sent", "createdDateTime": "2026-08-18T16:58:00.000Z",
             "user": {"id": config.UPWORK_USER_ID, "name": "A"}},
            {"id": "s3", "message": "their newest", "createdDateTime": "2026-08-25T14:46:17.390Z",
             "user": {"id": "client9", "name": "C"}},
        ]
        result = self._poll(stories)
        self.assertEqual(result.metrics["drafts_refreshed"], 0)
        self.assertEqual(result.metrics["drafts"], 0)
        self.assertEqual([d["sent_status"] for d in self.rows("SELECT sent_status FROM drafts")],
                         ["pending"])

    def test_the_clock_helper(self):
        self.assertTrue(poll_inbox.draft_predates("2026-08-19 04:45:00", "2026-08-25T14:46:17.390Z"))
        self.assertFalse(poll_inbox.draft_predates("2026-08-25 15:00:00", "2026-08-25T14:46:17.390Z"))
        self.assertTrue(poll_inbox.draft_predates("2026-08-25 15:00:00", "2026-08-25T21:00:00+05:30"))
        self.assertFalse(poll_inbox.draft_predates("2026-08-25 15:00:00", "2026-08-25T19:00:00+05:30"))
        self.assertFalse(poll_inbox.draft_predates(None, "2026-08-25T14:46:17Z"))
        self.assertFalse(poll_inbox.draft_predates("2026-08-25 15:00:00", None))
        self.assertFalse(poll_inbox.draft_predates("garbage", "2026-08-25T14:46:17Z"))


class TestRoomLink(M1bCase):
    """A prospect's email tied to their Upwork room (modules/upwork/room_link)."""

    def _room(self, rid, name, days_ago=1, room_name="Room"):
        at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT OR IGNORE INTO threads (id, room_name, topic) VALUES (?,?,?)", (rid, room_name, "t"))
            conn.execute("INSERT INTO messages (id, thread_id, message, created_dt, sender_user_name, direction)"
                         " VALUES (?,?,?,?,?,'inbound')", (f"{rid}-{name}", rid, "hi", at, name))

    def test_sender_name_reads_the_from_header_first(self):
        first, initial, display = room_link.sender_name(
            "david@dng.ai", "From: =?UTF-8?B?RGF2aWQgU8Opam91cm7DqQ==?= <david@dng.ai>\nSubject: x\n\nbody")
        self.assertEqual((first, initial, display), ("david", "s", "David Séjourné"))
        self.assertEqual(room_link.sender_name("rocky.choy@mooymedia.com", None)[:2], ("rocky", "c"))
        self.assertEqual(room_link.sender_name("nabil@nova.ca", "From: nabil@nova.ca\n")[:2], ("nabil", None))
        self.assertEqual(room_link.upwork_name("David S."), ("david", "s"))
        self.assertEqual(room_link.upwork_name("Nabil Tayeb Cherif"), ("nabil", "c"))

    def test_match_first_name_and_initial_inside_the_window(self):
        self._room("room_dng", "David S.", days_ago=2, room_name="Nabil Tayeb Cherif, Recherches Super Nova Inc.")
        self._room("room_old", "David S.", days_ago=90)          # outside the window
        with db.connect(self.db_path) as conn:
            link = room_link.match_event(conn, {"author": "david@dng.ai",
                                                "text": "From: David Séjourné <david@dng.ai>\n\nhi"})
            self.assertEqual(link["thread_id"], "room_dng")
            self.assertEqual(link["how"], "name-match")
            # a different initial is a different person
            self.assertIsNone(room_link.match_event(conn, {"author": "david@x.io", "text": "From: David Brown <david@x.io>\n"}))
            # a bare address still matches on the first name alone (weaker rule, named)
            weak = room_link.match_event(conn, {"author": "david@x.io", "text": None})
            self.assertEqual((weak["thread_id"], weak["how"]), ("room_dng", "name-match-first-only"))
            # nobody in any room
            self.assertIsNone(room_link.match_event(conn, {"author": "zoe@x.io", "text": "From: Zoe Q <zoe@x.io>\n"}))

    def test_same_person_two_rooms_takes_the_newest_two_people_link_nothing(self):
        self._room("room_a", "David S.", days_ago=5)
        self._room("room_b", "David S.", days_ago=1)
        with db.connect(self.db_path) as conn:
            link = room_link.match_event(conn, {"author": "david@x.io", "text": "From: David Stone <david@x.io>\n"})
            self.assertEqual(link["thread_id"], "room_b")
        self._room("room_c", "David Smith", days_ago=1, room_name="Other Co")   # a second David S…
        with db.connect(self.db_path) as conn:
            self.assertIsNone(room_link.match_event(conn, {"author": "david@x.io", "text": "From: David Stone <david@x.io>\n"}))

    def test_backfill_links_and_queues_the_excluded_mail(self):
        self._room("room_dng", "David S.", days_ago=2, room_name="Recherches Super Nova Inc.")
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO events (id, source, native_id, ts, author, subject, state, text,"
                         " exclude_reason, permalink) VALUES ('evt:email:x:1','email','1',?,"
                         " 'david@dng.ai','DNG: Workflow to create','excluded',"
                         " 'From: David Séjourné <david@dng.ai>\nSubject: DNG: Workflow to create\n\nHi Abhijeet, the material is in the drive.',"
                         " 'imap-low-base-rate:no-client-signal','https://mail/1')",
                         ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),))
            out = room_link.backfill(conn)
            self.assertEqual(out["examined"], 1)
            self.assertEqual([l["thread_id"] for l in out["linked"]], ["room_dng"])
            ev = conn.execute("SELECT state, proposal_evidence FROM events WHERE id='evt:email:x:1'").fetchone()
            self.assertEqual(ev["state"], "queued")
            self.assertIn("Recherches Super Nova", ev["proposal_evidence"])
            mails = room_link.linked_mail(conn, ["room_dng"])
            self.assertEqual(mails[0]["sender"], "David Séjourné")
            self.assertEqual(mails[0]["subject"], "DNG: Workflow to create")
            self.assertEqual(mails[0]["body"], "Hi Abhijeet, the material is in the drive.")
            # idempotent
            self.assertEqual(room_link.backfill(conn)["examined"], 0)

    def test_the_drafter_is_shown_the_linked_mail(self):
        self._room("room_dng", "David S.", days_ago=1)
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO events (id, source, native_id, ts, author, subject, state, text)"
                         " VALUES ('evt:email:x:2','email','2',?, 'david@dng.ai','DNG: Workflow to create','queued',"
                         " 'From: David Séjourné <david@dng.ai>\n\nPlease find the brief in the drive folder.')",
                         ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),))
            room_link.record(conn, "evt:email:x:2", {"thread_id": "room_dng", "how": "name-match", "matched_name": "David S."})
        seen = {}

        class Ctx:
            def claude(self, **kw):
                seen.update(kw)
                return gw.GatewayResult(text="Thanks David — got the brief.", parsed=None, model="m",
                                        route="payg", usage={}, cost_usd=0.001, stop_reason="end_turn", duration_ms=5)
        with db.connect(self.db_path) as conn:
            text = poll_inbox.draft_followup(Ctx(), conn, {"id": "room_dng"}, "owed", "HOT")
        self.assertEqual(text, "Thanks David — got the brief.")
        self.assertIn("DNG: Workflow to create", seen["user_content"])
        self.assertIn("Please find the brief in the drive folder.", seen["user_content"])
        self.assertIn("never say the email is missing", seen["user_content"])

    def test_a_linked_mail_newer_than_the_draft_refreshes_it(self):
        """Their brief arrived by email AFTER the draft — same as writing again on Upwork."""
        self._room("r1", "David S.", days_ago=10)
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE threads SET room_name='Room', topic='Topic' WHERE id='r1'")
            conn.execute("INSERT INTO drafts (thread_id, draft_kind, tier, body, word_count, sent_status, generated_at)"
                         " VALUES ('r1','owed','HOT','Old draft', 2, 'pending', '2026-08-19 04:45:00')")
            conn.execute("INSERT INTO events (id, source, native_id, ts, author, subject, state, text)"
                         " VALUES ('evt:email:x:3','email','3','2026-08-24T19:08:00+05:30','david@dng.ai','the brief','queued',"
                         " 'From: David Séjourné <david@dng.ai>\n\nbrief attached')")
            room_link.record(conn, "evt:email:x:3", {"thread_id": "r1", "how": "name-match", "matched_name": "David S."})
        self.assertTrue(poll_inbox.draft_predates("2026-08-19 04:45:00", "2026-08-24T19:08:00+05:30"))


# ── outcome composer ──────────────────────────────────────────────────────────
class TestOutcome(M1bCase):
    def seed_proposal(self, pid, status="Lost", reason=None, rec="recABC"):
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO proposals (id, status, status_reason, job_title,"
                " client_company, airtable_record_id, modified_dt,"
                " updated_at) VALUES (?,?,?,?,?,?,datetime('now'),"
                " datetime('now'))",
                (pid, status, reason, "Job T", "Acme", rec))

    def test_prefill_link_shape(self):
        link = outcome_capture.prefill_link("https://airtable.com/shrX",
                                            "rec123")
        self.assertIn("prefill_Proposal=rec123", link)
        self.assertIn("hide_Proposal=true", link)

    def test_composer_shadow_intent(self):
        self.seed_proposal("p1")
        self.seed_proposal("p2", status="Won")
        self.seed_proposal("p3", status="Lost", reason="price")  # has reason
        with mock.patch.object(outcome_capture, "form_url",
                               return_value="https://airtable.com/shrX"):
            result = runner.run_task(
                outcome_capture.TASK_NAME, outcome_capture.capture,
                module="upwork", db_path=self.db_path)
        self.assertEqual(result.metrics["candidates"], 2)
        self.assertEqual(result.metrics["with_links"], 2)
        ledger = self.rows(
            "SELECT * FROM shadow_ledger WHERE entity='outcome_capture'")
        self.assertEqual(len(ledger), 1)

    def test_missing_form_url_flagged(self):
        self.seed_proposal("p1")
        with mock.patch.object(outcome_capture, "form_url",
                               return_value=""):
            result = runner.run_task(
                outcome_capture.TASK_NAME, outcome_capture.capture,
                module="upwork", db_path=self.db_path)
        self.assertTrue(result.metrics["form_url_missing"])
        self.assertEqual(result.metrics["with_links"], 0)

    def test_registry_form_url_placeholders_parse_empty(self):
        # PENDING/TBD placeholders read as "no form" — against a fixture
        # registry, not the live one (the live file got its real link on
        # 2026-08-25 and this test used to pin the PENDING state)
        for placeholder in ("PENDING", "TBD", "pending (paste the shr… link)"):
            reg = self.tmp / f"REGISTRY-{placeholder[:3]}.md"
            reg.write_text(f"# Rail Registry\n- trigger: x\n- form_url: {placeholder}\n"
                           "- approval tier: internal\n")
            with mock.patch.object(outcome_capture, "REGISTRY", reg):
                self.assertEqual(outcome_capture.form_url(), "", placeholder)

    def test_registry_form_url_real_link_parses(self):
        reg = self.tmp / "REGISTRY-live.md"
        reg.write_text("- form_url: https://airtable.com/appX/pagY/form (Airtable"
                       " form page, verified 2026-08-25)\n")
        with mock.patch.object(outcome_capture, "REGISTRY", reg):
            self.assertEqual(outcome_capture.form_url(),
                             "https://airtable.com/appX/pagY/form")

    def test_live_registry_has_a_form_url(self):
        # the rail is live only while the real registry carries a link
        self.assertTrue(outcome_capture.form_url().startswith("https://"),
                        outcome_capture.form_url())

    # ── the send path (finding 14, 2026-08-25) ───────────────────────────────
    def run_live(self, send):
        with mock.patch.dict(config.SHADOW_MODE, {"upwork": False}), \
             mock.patch.object(outcome_capture, "form_url",
                               return_value="https://airtable.com/appX/pagY/form"):
            return runner.run_task(
                outcome_capture.TASK_NAME,
                lambda ctx: outcome_capture.capture(ctx, _send=send),
                module="upwork", db_path=self.db_path)

    def test_live_sends_one_email_with_a_link_per_proposal(self):
        self.seed_proposal("p1")
        self.seed_proposal("p2", status="Won", rec="recWON")
        send = mock.MagicMock()
        result = self.run_live(send)
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(result.metrics["sent"], "sent")
        self.assertEqual(result.metrics["candidates"], 2)
        send.assert_called_once()
        subject, html = send.call_args[0]
        self.assertIn("2 proposals closed", subject)
        self.assertIn("prefill_Proposal=recABC", html)
        self.assertIn("prefill_Proposal=recWON", html)
        self.assertIn("Job T", html)
        self.assertIn("Acme", html)
        # nothing shadowed on the live path
        self.assertEqual(self.rows(
            "SELECT * FROM shadow_ledger WHERE entity='outcome_capture'"), [])

    def test_live_send_failure_fails_the_run_loudly(self):
        self.seed_proposal("p1")
        send = mock.MagicMock(side_effect=RuntimeError("smtp down"))
        # the runner records the failed run + files the critical anomaly, then
        # re-raises (non-retryable) — so the failure is loud in three places
        with self.assertRaises(RuntimeError):
            self.run_live(send)
        run = self.rows("SELECT * FROM runs WHERE task_name=?",
                        (outcome_capture.TASK_NAME,))[-1]
        self.assertEqual(run["status"], "failed")
        self.assertIn("not sent", run["error"] or "")
        crit = self.rows("SELECT * FROM anomalies WHERE severity='critical'")
        self.assertTrue(crit, "a failed send must reach the interrupt queue")

    def test_live_refuses_to_send_the_same_week_twice(self):
        self.seed_proposal("p1")
        send = mock.MagicMock()
        first = self.run_live(send)
        self.assertEqual(first.metrics["sent"], "sent")
        second = self.run_live(send)
        self.assertEqual(second.status, "skipped_empty")
        send.assert_called_once()                    # still exactly one email

    def test_missing_form_url_still_sends_but_says_so(self):
        self.seed_proposal("p1")
        send = mock.MagicMock()
        with mock.patch.dict(config.SHADOW_MODE, {"upwork": False}), \
             mock.patch.object(outcome_capture, "form_url", return_value=""):
            result = runner.run_task(
                outcome_capture.TASK_NAME,
                lambda ctx: outcome_capture.capture(ctx, _send=send),
                module="upwork", db_path=self.db_path)
        self.assertEqual(result.metrics["sent"], "sent")
        self.assertTrue(result.metrics["form_url_missing"])
        html = send.call_args[0][1]
        self.assertIn("form_url is missing", html)
        self.assertNotIn("href", html)

    def test_shadow_path_records_the_intent_and_sends_nothing(self):
        self.seed_proposal("p1")
        send = mock.MagicMock()
        with mock.patch.object(outcome_capture, "form_url",
                               return_value="https://airtable.com/appX/pagY/form"):
            result = runner.run_task(
                outcome_capture.TASK_NAME,
                lambda ctx: outcome_capture.capture(ctx, _send=send),
                module="upwork", db_path=self.db_path)
        self.assertEqual(result.metrics["sent"], "shadow")
        send.assert_not_called()

    def test_render_escapes_what_it_prints(self):
        html = outcome_capture.render_html({
            "week": "2026-W34",
            "items": [{"proposal_id": "p9", "title": "<b>bold</b> & co",
                       "client": "A&B", "status": "Lost",
                       "form_link": "https://x/form?prefill_Proposal=rec1&hide_Proposal=true"}]})
        self.assertIn("&lt;b&gt;bold&lt;/b&gt; &amp; co", html)
        self.assertIn("A&amp;B", html)
        self.assertIn("prefill_Proposal=rec1&amp;hide_Proposal=true", html)


# ── client token rules ────────────────────────────────────────────────────────
class _Resp:
    """Minimal urlopen() context-manager stand-in returning a JSON payload."""
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


def _http_401():
    return urllib.error.HTTPError("https://api.upwork.com/graphql", 401,
                                  "unauthorized", {}, None)


class TestClientRefresh(M1bCase):
    def _client(self, owner="v3", expires_in=9999):
        creds = self.tmp / "upwork-api.json"
        creds.write_text(json.dumps({
            "client_id": "c", "client_secret": "s",
            "tokens": {"access_token": "tok", "refresh_token": "r",
                       "expires_at": time.time() + expires_in}}))
        for p in (mock.patch.object(upwork_client, "CREDS_PATH", creds),
                  mock.patch.object(upwork_client, "token_owner", return_value=owner)):
            p.start()
            self.addCleanup(p.stop)
        c = upwork_client.UpworkClient()
        c.refresh = mock.MagicMock(name="refresh")     # count calls; no real OAuth/network
        return c

    def test_401_refreshes_and_retries_when_v3(self):
        c = self._client(owner="v3")                   # fresh token -> no proactive refresh
        seq = [_http_401(), _Resp({"data": {"ok": 1}})]

        def fake_urlopen(req, timeout=None):
            x = seq.pop(0)
            if isinstance(x, Exception):
                raise x
            return x
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = c.graphql("query{}", {})
        self.assertEqual(out, {"ok": 1})               # recovered on retry
        c.refresh.assert_called_once()                 # refreshed exactly once

    def test_401_raises_without_refresh_when_v2(self):
        c = self._client(owner="v2")                   # pre-cutover: never refresh
        with mock.patch("urllib.request.urlopen", side_effect=_http_401()):
            with self.assertRaises(gw.TransientAPIError):
                c.graphql("q", {})
        c.refresh.assert_not_called()

    def test_proactive_refresh_before_expiry_when_v3(self):
        c = self._client(owner="v3", expires_in=10)    # within margin -> refresh up front
        with mock.patch("urllib.request.urlopen",
                        side_effect=lambda req, timeout=None: _Resp({"data": {}})):
            c.graphql("q", {})
        c.refresh.assert_called_once()

    def test_no_proactive_refresh_when_token_fresh(self):
        c = self._client(owner="v3", expires_in=9999)  # far from expiry -> no refresh
        with mock.patch("urllib.request.urlopen",
                        side_effect=lambda req, timeout=None: _Resp({"data": {}})):
            c.graphql("q", {})
        c.refresh.assert_not_called()


class TestClientToken(M1bCase):
    def test_refresh_refused_while_v2_owns(self):
        with mock.patch.object(upwork_client, "read_access_token",
                               return_value="tok"), \
             mock.patch.object(upwork_client, "token_owner",
                               return_value="v2"):
            c = upwork_client.UpworkClient()
            with self.assertRaises(upwork_client.UpworkAPIError):
                c.refresh()

    def test_default_owner_is_v2(self):
        with mock.patch.object(upwork_client, "OWNER_MARKER",
                               self.tmp / "absent"):
            self.assertEqual(upwork_client.token_owner(), "v2")


if __name__ == "__main__":
    unittest.main()
