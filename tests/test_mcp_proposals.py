"""Proposals through the MCP (modules/upwork/feeds/mcp_proposals — use case C,
Phase 0.2, 26 Aug 2026), pinned by tests/fixtures/mcp/proposals_list.json
(captured 25 Aug on his OK: ten submitted proposals, `status Accepted`,
paged, totalCount 30).

Pins: the envelope (data.vendorProposals.edges / pageInfo / totalCount);
one list call per status, newest modified first, 10 a page, stopping at the
stored cursor; the page cap on a first round; a text answer fails loud, an
empty status does not; the 0.3 map (Hired → Won, Declined → Lost,
Withdrawn, Activated → Interview; Accepted / Offered / Archived write no
status); a terminal verdict is never flipped and a disagreement is counted;
the cursor per status; the pass inside sync() and its non-fatal failure.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db
from modules.upwork import sync_state
from modules.upwork.feeds import mcp_proposals as mp, mcp_search

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "mcp"
ORG = config.UPWORK_ORGS["personal"]


def _fixture() -> dict:
    return json.loads((FIX / "proposals_list.json").read_text())


def _nodes() -> list:
    return [e["node"] for e in _fixture()["data"]["vendorProposals"]["edges"]]


def _ms(iso: str) -> str:
    from datetime import datetime, timezone
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return str(int(dt.timestamp() * 1000))


def node(pid: str, status: str, modified: str, created: str = None, title: str = "A job",
         job_id: str = None) -> dict:
    """A vendorProposals node in the fixture's shape (rawValue = epoch ms, as
    on the wire; the adapter reads rawValue)."""
    created = created or modified
    return {"id": pid,
            "auditDetails": {"createdDateTime": {"displayValue": created, "rawValue": _ms(created)},
                             "modifiedDateTime": {"displayValue": modified, "rawValue": _ms(modified)}},
            "marketplaceJobPosting": {"content": {"title": title}, "id": job_id or f"job-{pid}"},
            "status": {"status": status, "status_label": status},
            "terms": {"chargeRate": {"currency": "USD", "displayValue": "USD 50.0", "rawValue": "50.0"}}}


def envelope(nodes: list, has_next: bool = False, cursor: str = None, total: int = None) -> dict:
    return {"data": {"vendorProposals": {
        "edges": [{"node": n} for n in nodes],
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        "totalCount": len(nodes) if total is None else total}},
        "hasMore": has_next, "next_cursor": cursor, "status": "ok",
        "totalCount": len(nodes) if total is None else total}


class _Session:
    """pages: {status: [envelope, envelope, ...]} served in order per status
    (a cursor on the request picks the next page); anything else -> empty."""
    def __init__(self, pages=None, fail_start=None):
        self.pages = pages or {}
        self.calls = []
        self.fail_start = fail_start
        self.served = {}

    def initialize(self):
        if self.fail_start:
            raise self.fail_start

    def call(self, tool, args):
        self.calls.append((tool, args))
        params = (args or {}).get("params") or {}
        status = params.get("status")
        pages = self.pages.get(status)
        if not pages:
            return envelope([])
        idx = self.served.get(status, 0) if params.get("cursor") else 0
        self.served[status] = idx + 1
        return pages[idx] if idx < len(pages) else envelope([], total=0)


class _Ctx:
    def __init__(self, db_path):
        self.db_path = db_path
        self.logs = []
        self.anomalies = []

    def log(self, m):
        self.logs.append(m)

    def anomaly(self, kind, severity, detail):
        self.anomalies.append((kind, severity, detail))


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mcpprop-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self._p = mock.patch.object(mcp_search, "_sleep", lambda s: None)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self, sql, args=()):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, args)]


class TestShape(unittest.TestCase):
    def test_the_fixture_envelope(self):
        nodes, page_info, total = mp._nodes_of(_fixture())
        self.assertEqual((len(nodes), total, page_info["hasNextPage"]), (10, 30, True))
        self.assertTrue(page_info["endCursor"])
        self.assertEqual(mp.status_of(nodes[0]), "Accepted")
        self.assertEqual(mp.modified_of(nodes[0]), "2026-08-24T14:07:37.443Z")
        # the adapter the room pass uses reads the same node
        flat = sync_state._vp_node_to_proposal(nodes[0])
        self.assertEqual((flat["id"], flat["job"]["id"], flat["job"]["title"]),
                         ("2091886792750665729", "2091881079178928768", "Expert Airtable Solution Architect"))
        self.assertEqual(mp._nodes_of({"text": "boom"}), ([], {}, None))


class TestFetch(Case):
    def test_one_status_newest_first_ten_a_page_stopping_at_the_cursor(self):
        page1 = _fixture()
        older = [node("old1", "Accepted", "2026-08-01T00:00:00.000Z"), node("old2", "Accepted", "2026-07-20T00:00:00.000Z")]
        s = _Session({"Accepted": [page1, envelope(older, total=30)]})
        logs = []
        got = mp.fetch_status(s, ORG, "Accepted", None, logs.append)      # no cursor: everything
        self.assertEqual((len(got["nodes"]), got["pages"], got["total"], got["more"]), (12, 2, 30, False))
        tool, args = s.calls[0]
        self.assertEqual((tool, args["action"], args["org_uid"]), (mp.TOOL, "list", ORG))
        self.assertEqual(args["params"], {"status": "Accepted", "sort_field": "MODIFIEDDATETIME",
                                          "sort_order": "DESC", "limit": 10})
        self.assertEqual(s.calls[1][1]["params"]["cursor"], page1["data"]["vendorProposals"]["pageInfo"]["endCursor"])
        # a cursor from the last round: stop at the first older node, one page
        s2 = _Session({"Accepted": [page1, envelope(older, total=30)]})
        got2 = mp.fetch_status(s2, ORG, "Accepted", "2026-08-23T23:25:24.576Z", logs.append)
        self.assertEqual([n["id"] for n in got2["nodes"]],
                         ["2091886792750665729", "2091668156895518721"])
        self.assertEqual(got2["pages"], 1)

    def test_the_page_cap_says_more_and_a_text_answer_fails_loud(self):
        page = envelope([node("p", "Archived", "2026-08-20T00:00:00.000Z")], has_next=True, cursor="c-1", total=500)
        s = _Session({"Archived": [page] * 5})
        got = mp.fetch_status(s, ORG, "Archived", None, lambda m: None, max_pages=3)
        self.assertEqual((got["pages"], got["more"]), (3, True))
        with self.assertRaises(RuntimeError):
            mp.fetch_status(_Session({"Hired": [{"text": "rate limited"}]}), ORG, "Hired", None, lambda m: None)
        got_empty = mp.fetch_status(_Session({}), ORG, "Withdrawn", None, lambda m: None)
        self.assertEqual((got_empty["nodes"], got_empty["pages"]), ([], 1))


class TestOutcomes(Case):
    def test_the_first_round_inserts_the_submitted_backlog_without_a_verdict(self):
        s = _Session({"Accepted": [_fixture()]})
        m = mp.run(_Ctx(self.db_path), s, ORG)
        self.assertEqual((m["seen"], m["inserted"], m["outcomes"]), (10, 10, {}))
        r = self.rows("SELECT status, upwork_status, job_title, charge_rate, marketplace_job_id, thread_id,"
                      " mcp_list_status, upwork_status_label, mcp_listed_at"
                      " FROM proposals WHERE id='2091886792750665729'")[0]
        self.assertEqual((r["status"], r["upwork_status"], r["charge_rate"], r["marketplace_job_id"], r["thread_id"]),
                         (None, "Accepted", 7000.0, "2091881079178928768", None))
        # the list it came from and Upwork's label ride on the row (032)
        self.assertEqual((r["mcp_list_status"], r["upwork_status_label"]), ("Accepted", "Submitted"))
        self.assertTrue(r["mcp_listed_at"])
        self.assertEqual(m["statuses"]["Accepted"]["node_statuses"], {"Accepted": 10})
        self.assertEqual(m["statuses"]["Accepted"]["total"], 30)
        # the cursor is the newest modified instant of the status
        cur = self.rows("SELECT value FROM sync_cursors WHERE name=?", (mp.CURSOR_PREFIX + "Accepted",))[0]["value"]
        self.assertEqual(cur, "2026-08-24T14:07:37.443Z")
        # every status was read, in config order; the fixture page says more exist, so
        # Accepted took a second (empty) page — a quiet round is one call per status
        self.assertEqual([c[1]["params"]["status"] for c in s.calls],
                         ["Accepted"] + list(config.MCP_PROPOSAL_STATUSES))
        # the second round passes the cursor on and re-reads only the newest
        s2 = _Session({"Accepted": [_fixture()]})
        m2 = mp.run(_Ctx(self.db_path), s2, ORG)
        self.assertEqual((m2["seen"], m2["inserted"], m2["statuses"]["Accepted"]["since"]),
                         (1, 0, "2026-08-24T14:07:37.443Z"))

    def test_the_status_map_writes_won_lost_withdrawn_interview_and_nothing_for_the_rest(self):
        s = _Session({
            "Hired": [envelope([node("h1", "Hired", "2026-08-20T10:00:00.000Z", title="Won job")])],
            "Declined": [envelope([node("d1", "Declined", "2026-08-19T10:00:00.000Z")])],
            "Withdrawn": [envelope([node("w1", "Withdrawn", "2026-08-18T10:00:00.000Z")])],
            "Activated": [envelope([node("a1", "Activated", "2026-08-17T10:00:00.000Z")])],
            "Offered": [envelope([node("o1", "Offered", "2026-08-16T10:00:00.000Z")])],
            "Archived": [envelope([node("x1", "Archived", "2026-08-15T10:00:00.000Z")])],
        })
        ctx = _Ctx(self.db_path)
        m = mp.run(ctx, s, ORG)
        self.assertEqual(m["outcomes"], {"won": 1, "lost": 1, "withdrawn": 1, "interview": 1, "expired": 1})
        got = {r["id"]: (r["status"], r["upwork_status"]) for r in self.rows("SELECT id, status, upwork_status FROM proposals")}
        self.assertEqual(got, {"h1": ("Won", "Hired"), "d1": ("Lost", "Declined"), "w1": ("Withdrawn", "Withdrawn"),
                               "a1": ("Interview", "Activated"), "o1": (None, "Offered"),
                               "x1": ("Expired", "Archived")})            # "Archived (job closed)" → Expired
        self.assertEqual(ctx.anomalies, [])
        # modified_dt carries Upwork's instant, so the Sunday email's window sees the closure
        self.assertEqual(self.rows("SELECT modified_dt FROM proposals WHERE id='h1'")[0]["modified_dt"],
                         "2026-08-20T10:00:00+00:00")                  # epoch-ms rawValue → ISO

    def test_the_second_half_reads_the_list_the_proposal_sits_in(self):
        """His 26 Aug 19:05 answer: submitted + the job closed or went to someone
        else → Expired; an invitation accepted + the same → Lost; a stale
        Interview on a closed job → Lost. The proposal's own status still wins."""
        with db.connect(self.db_path) as conn:                      # the room pass left an Interview on i1
            sync_state.upsert_proposal(conn, sync_state._vp_node_to_proposal(
                node("i1", "Archived", "2026-06-01T00:00:00.000Z")), "room-9")
            conn.execute("UPDATE proposals SET status='Interview' WHERE id='i1'")
        s = _Session({
            "Hired": [envelope([node("s1", "Accepted", "2026-08-21T10:00:00.000Z"),      # someone else won
                                node("s2", "Activated", "2026-08-21T11:00:00.000Z")])],  # we interviewed, someone else won
            "Withdrawn": [envelope([node("s3", "Accepted", "2025-03-01T10:00:00.000Z")])],   # job gone
            "Archived": [envelope([node("s4", "Activated", "2026-05-01T10:00:00.000Z"),
                                   node("i1", "Archived", "2026-06-01T00:00:00.000Z")])],
            "Accepted": [envelope([node("open1", "Accepted", "2026-08-24T10:00:00.000Z")])],   # still open
        })
        ctx = _Ctx(self.db_path)
        m = mp.run(ctx, s, ORG)
        got = {r["id"]: (r["status"], r["mcp_list_status"]) for r in self.rows("SELECT id, status, mcp_list_status FROM proposals")}
        self.assertEqual(got, {"s1": ("Expired", "Hired"), "s2": ("Lost", "Hired"), "s3": ("Expired", "Withdrawn"),
                               "s4": ("Lost", "Archived"), "i1": ("Lost", "Archived"), "open1": (None, "Accepted")})
        self.assertEqual(m["outcomes"], {"expired": 2, "lost": 3})
        self.assertEqual(m["statuses"]["Hired"]["node_statuses"], {"Accepted": 1, "Activated": 1})
        self.assertEqual(mp.verdict_for("Hired", "Archived", None), "Won")          # own status first
        self.assertIsNone(mp.verdict_for("Accepted", "Accepted", None))               # open stays open
        self.assertIsNone(mp.verdict_for("Offered", "Hired", None))                   # an offer is not a verdict
        # one proposal sits in several lists (Hired, Declined, Withdrawn at once on 17 Aug):
        # the first list's verdict stands, the others are not disagreements
        with db.connect(self.db_path) as conn:
            self.assertEqual(mp.apply_outcome(conn, "s1", "Accepted", "Declined"), "kept")   # Expired stands
            self.assertEqual(mp.apply_outcome(conn, "s1", "Hired", "Hired"), "terminal")     # Upwork's own word disagrees
        # the one-off backfill over rows already listed writes the same and is idempotent
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE proposals SET status=NULL WHERE id IN ('s1','s3')")
            self.assertEqual(mp.backfill_verdicts(conn), {"expired": 2, "none": 1})
            self.assertEqual(mp.backfill_verdicts(conn), {"none": 1})

    def test_a_terminal_verdict_is_never_flipped_and_a_disagreement_is_counted(self):
        with db.connect(self.db_path) as conn:
            sync_state.upsert_proposal(conn, sync_state._vp_node_to_proposal(
                node("won1", "Hired", "2026-05-01T00:00:00.000Z")), "room-1")
            conn.execute("UPDATE proposals SET status='Won' WHERE id='won1'")
            sync_state.upsert_proposal(conn, sync_state._vp_node_to_proposal(
                node("exp1", "Archived", "2026-05-01T00:00:00.000Z")), "room-2")
            conn.execute("UPDATE proposals SET status='Expired' WHERE id='exp1'")
            sync_state.upsert_proposal(conn, sync_state._vp_node_to_proposal(
                node("int1", "Activated", "2026-06-01T00:00:00.000Z")), "room-3")
            conn.execute("UPDATE proposals SET status='Interview' WHERE id='int1'")
        s = _Session({
            "Declined": [envelope([node("won1", "Declined", "2026-08-20T10:00:00.000Z")])],   # Upwork disagrees
            "Hired": [envelope([node("exp1", "Hired", "2026-08-20T11:00:00.000Z"),           # …and here
                                node("int1", "Hired", "2026-08-20T12:00:00.000Z")])],        # interview → won
        })
        ctx = _Ctx(self.db_path)
        m = mp.run(ctx, s, ORG)
        got = {r["id"]: (r["status"], r["upwork_status"], r["thread_id"]) for r in
               self.rows("SELECT id, status, upwork_status, thread_id FROM proposals")}
        self.assertEqual(got["won1"], ("Won", "Declined", "room-1"))       # verdict kept, telemetry recorded, room kept
        self.assertEqual(got["exp1"], ("Expired", "Hired", "room-2"))
        self.assertEqual(got["int1"], ("Won", "Hired", "room-3"))         # non-terminal → written
        self.assertEqual((m["terminal_disagreements"], m["outcomes"]), (2, {"won": 1}))
        self.assertEqual([a[0] for a in ctx.anomalies], ["proposal_terminal_disagreement"])

    def test_apply_outcome_states(self):
        with db.connect(self.db_path) as conn:
            sync_state.upsert_proposal(conn, sync_state._vp_node_to_proposal(
                node("p1", "Accepted", "2026-08-01T00:00:00.000Z")), None)
            self.assertEqual(mp.apply_outcome(conn, "p1", "Accepted"), "none")
            self.assertEqual(mp.apply_outcome(conn, "p1", "Activated"), "interview")
            self.assertEqual(mp.apply_outcome(conn, "p1", "Activated"), "kept")
            self.assertEqual(mp.apply_outcome(conn, "p1", "Hired"), "won")
            self.assertEqual(mp.apply_outcome(conn, "p1", "Declined"), "terminal")
            self.assertEqual(mp.apply_outcome(conn, "nope", "Hired"), "none")


class TestInsideTheSync(Case):
    class _Rooms:
        def graphql(self, query, variables=None, tolerate_errors=False):
            if "roomList(" in query:
                return {"roomList": {"totalCount": 0, "edges": [], "pageInfo": {"hasNextPage": False}}}
            return {}

    def test_the_sync_runs_the_pass_and_reports_it(self):
        fake = _Session({"Hired": [envelope([node("h1", "Hired", "2026-08-20T10:00:00.000Z")])]})
        out = sync_state.sync(_Ctx(self.db_path), client_factory=lambda: self._Rooms(),
                              mcp_session_factory=lambda: fake)
        self.assertEqual(out["submitted_proposals"]["outcomes"], {"won": 1})
        self.assertEqual(out["transactions"], {"retired": "upwork_financials (MCP) since 2026-08-26"})
        self.assertEqual(self.rows("SELECT status FROM proposals WHERE id='h1'")[0]["status"], "Won")
        # None = no MCP session (tests, offline): the pass is skipped, not failed
        out2 = sync_state.sync(_Ctx(self.db_path), client_factory=lambda: self._Rooms(),
                               mcp_session_factory=None)
        self.assertEqual(out2["submitted_proposals"]["status"], "skipped")

    def test_a_pass_failure_degrades_the_sync_and_names_the_kind(self):
        import socket
        import urllib.error
        fake = _Session(fail_start=urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided")))
        ctx = _Ctx(self.db_path)
        out = sync_state.sync(ctx, client_factory=lambda: self._Rooms(), mcp_session_factory=lambda: fake)
        self.assertEqual((out["submitted_proposals"]["status"], out["submitted_proposals"]["kind"]),
                         ("error", "network"))
        self.assertEqual([a[0] for a in ctx.anomalies], ["proposals_pass_failed"])
        self.assertIn("proposals_pass_failed",
                      next(s for s in config.SOURCES if s["task_name"] == "upwork_sync")["degrade_kinds"])


class TestConfigPins(unittest.TestCase):
    def test_decision_4_is_the_map_and_pending_is_not_read(self):
        self.assertEqual(config.MCP_PROPOSAL_STATUS_MAP["Hired"], "Won")
        self.assertEqual(config.MCP_PROPOSAL_STATUS_MAP["Declined"], "Lost")
        self.assertEqual(config.MCP_PROPOSAL_STATUS_MAP["Withdrawn"], "Withdrawn")
        self.assertEqual(config.MCP_PROPOSAL_STATUS_MAP["Activated"], "Interview")
        for s in ("Accepted", "Offered", "Archived", "Pending"):
            self.assertIsNone(config.MCP_PROPOSAL_STATUS_MAP[s])
        self.assertNotIn("Pending", config.MCP_PROPOSAL_STATUSES)
        self.assertTrue(set(config.MCP_PROPOSAL_STATUSES) <= set(config.MCP_PROPOSAL_STATUS_MAP))
        self.assertIn("list_freelancer_proposals",
                      __import__("modules.upwork.feeds.mcp_client", fromlist=["READ_TOOLS"]).READ_TOOLS)


if __name__ == "__main__":
    unittest.main()
