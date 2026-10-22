"""The crowd check (Abhijeet, 2026-10-23): "Re-read each job's proposal count
when the card is made and again before you apply. Flag 50+, and skip 150+
unless it's Make.com or Airtable."

Pins: the title-search reading (words chosen, the job found by id, unknown is
never zero); a Best Matches row borrows the count a keyword row carries; the
decision and the bands; at the card, a crowded job costs no draft and gets no
card, while Make.com/Airtable is flagged and carded; before he applies, open
cards are re-read stalest first, renamed only when the band moves, skipped at
150+ only while still `new`, and never touched once they have left `new`;
invites, applied jobs, old cards and fresh readings are left alone.
"""
from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core import config, db
from modules.upwork import crowd, scoring
from modules.upwork import scan_pipeline as sp
from modules.upwork.feeds import mcp_search
from tests.test_mcp_feed import GET_HOURLY, SEARCH_HOURLY
from tests.test_mcp_provider import FakeSession, McpScanCase, PacedCase

import clickup_push   # scan_pipeline put scripts/ on the path


class TitleSession:
    """find_jobs search by title -> scripted pages; records every call."""

    def __init__(self, pages=None, fail=False):
        self.pages = pages or {}        # title -> [page1_jobs, page2_jobs, ...]
        self.fail = fail
        self.calls = []

    def call(self, tool, args):
        self.calls.append((tool, args))
        if self.fail:
            raise RuntimeError("upstream 503")
        p = args["params"]
        assert "query" not in p, "the lookup must be a title search"
        pages = self.pages.get(p["title"], [[]])
        i = int(p.get("cursor") or 0)
        jobs = pages[i] if i < len(pages) else []
        more = i + 1 < len(pages)
        return {"jobs": jobs, "pageInfo": {"hasNextPage": more,
                                           "endCursor": str(i + 1) if more else None}}


class TestLookup(PacedCase):
    def test_the_words_are_the_distinctive_ones(self):
        self.assertEqual(mcp_search.lookup_words("AI Role Play Agent Development"),
                         ["Role", "Play", "Agent"])
        self.assertEqual(mcp_search.lookup_words("Need an Airtable expert for 2 bases"),
                         ["Airtable", "bases"])
        self.assertEqual(mcp_search.lookup_words(""), [])

    def test_the_job_is_found_by_id_and_its_count_read(self):
        s = TitleSession({"Role Play Agent": [[{"id": "9", "proposal_count": 3},
                                               {"id": "7", "proposal_count": 412}]]})
        self.assertEqual(mcp_search.proposal_count(s, "org", "7", "AI Role Play Agent Development",
                                                   lambda m: None), 412)
        self.assertEqual(s.calls[0][1]["params"]["title"], "Role Play Agent")

    def test_a_second_page_is_read_when_the_first_misses(self):
        s = TitleSession({"Role Play Agent": [[{"id": "9", "proposal_count": 3}],
                                              [{"id": "7", "proposal_count": 41}]]})
        self.assertEqual(mcp_search.proposal_count(s, "org", "7", "AI Role Play Agent",
                                                   lambda m: None), 41)
        self.assertEqual(len(s.calls), 2)

    def test_not_found_or_failed_is_unknown_never_zero(self):
        s = TitleSession({"Role Play Agent": [[{"id": "9", "proposal_count": 0}]]})
        self.assertIsNone(mcp_search.proposal_count(s, "org", "7", "AI Role Play Agent",
                                                    lambda m: None))
        self.assertIsNone(mcp_search.proposal_count(TitleSession(fail=True), "org", "7",
                                                    "AI Role Play Agent", lambda m: None))

    def test_a_best_matches_row_borrows_a_keyword_rows_count(self):
        smart = {"id": "5", "title": "X"}                     # Best Matches: no count
        term = {"id": "5", "title": "X", "proposal_count": 64}
        s = FakeSession(smart=[smart], terms={"airtable": [term]})
        rows = mcp_search.search_rows(s, "org", lambda m: None)
        self.assertEqual(rows["5"]["_feed_source"], mcp_search.SMART_LABEL)
        self.assertEqual(rows["5"]["proposal_count"], 64)


class TestDecide(unittest.TestCase):
    def test_skip_flag_ok_unknown(self):
        job = {"title": "AI Automation Specialist", "skills_json": '["Artificial Intelligence"]'}
        self.assertEqual(crowd.decide(None, job), "unknown")
        self.assertEqual(crowd.decide(150, job), "skip")
        self.assertEqual(crowd.decide(149, job), "flag")
        self.assertEqual(crowd.decide(50, job), "flag")
        self.assertEqual(crowd.decide(49, job), "ok")
        self.assertEqual(crowd.decide(0, job), "ok")

    def test_make_and_airtable_are_flagged_never_skipped(self):
        self.assertEqual(crowd.decide(400, {"title": "Airtable base rebuild"}), "flag")
        self.assertEqual(crowd.decide(400, {"title": "Ops help", "skills_json": '["Make.com"]'}), "flag")
        self.assertEqual(crowd.decide(400, {"title": "Integromat scenarios"}), "flag")
        self.assertEqual(crowd.decide(400, {"title": "Make my website faster"}), "skip")

    def test_bands(self):
        self.assertIsNone(crowd.band(None))
        self.assertEqual(crowd.band(0), "under 20 proposals")
        self.assertEqual(crowd.band(19), "under 20 proposals")
        self.assertEqual(crowd.band(20), "20+ proposals")
        self.assertEqual(crowd.band(50), "⚠ 50+ proposals")
        self.assertEqual(crowd.band(100), "⚠ 100+ proposals")
        self.assertEqual(crowd.band(150), "⚠ 150+ proposals")

    def test_the_card_name_carries_the_band(self):
        row = {"title": "HubSpot Admin", "score": 22}
        self.assertEqual(clickup_push.task_name(row), "HubSpot Admin — score 22")
        self.assertEqual(clickup_push.task_name(dict(row, proposals_now=75)),
                         "HubSpot Admin — score 22 · ⚠ 50+ proposals")
        self.assertIn("Proposals when carded: 75",
                      clickup_push.task_body(dict(row, proposals_now=75)))


def _quiet(get_title, skills=("Artificial Intelligence",)):
    """The hourly fixture as a job with no Make.com in it, and no count."""
    g = copy.deepcopy(GET_HOURLY)
    g["data"]["marketplaceJobPosting"]["content"]["title"] = get_title
    srow = dict(SEARCH_HOURLY, title=get_title, skills=list(skills))
    srow.pop("proposal_count", None)
    return srow, g


class LookupSession(FakeSession):
    """The scan's fake, plus title lookups."""

    def __init__(self, counts=None, **kw):
        super().__init__(**kw)
        self.counts = counts or {}      # job id -> count a title search returns

    def call(self, tool, args):
        if args.get("action") == "search" and "title" in (args.get("params") or {}):
            self.calls.append((tool, args))
            return {"jobs": [{"id": jid, "proposal_count": n}
                             for jid, n in self.counts.items()],
                    "pageInfo": {"hasNextPage": False}}
        return super().call(tool, args)


class TestAtTheCard(McpScanCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(config, "MCP_MIN_HEALTHY_SURFACED", 1)
        p.start()
        self.addCleanup(p.stop)

    def scan(self, session, score=24):
        draft = mock.Mock(return_value=("Drafted proposal text.", {"status": "supplied"}))
        from modules.upwork.feeds import mcp_client
        with mock.patch.object(mcp_client, "MCPSession", return_value=session), \
             mock.patch.object(scoring, "score_job",
                               side_effect=lambda row, task_name, db_path=None:
                               {"score": score, "gated": False, "loom_flag": 0,
                                "breakdown": {"total": score}}), \
             mock.patch.object(scoring, "draft_proposal_grounded", draft):
            from core import runner
            res = runner.run_task(sp.TASK_NAME, lambda ctx: sp.scan(ctx, _sleep=lambda s: None),
                                  module="upwork", db_path=self.db_path)
        return res, draft

    def test_a_crowded_job_gets_no_draft_and_no_card(self):
        srow, g = _quiet("Senior AI Automation Specialist — LLM agents")
        s = LookupSession(counts={srow["id"]: 212}, smart=[srow], terms={},
                          gets={srow["id"]: g})
        res, draft = self.scan(s)
        self.assertEqual(res.status, "completed", res.error)
        row = self.rows("SELECT * FROM scored_jobs WHERE id=?", (srow["id"],))[0]
        self.assertEqual((row["status"], row["hard_rule_skip"]), ("Skipped", "crowded"))
        self.assertEqual(row["score"], 24)                    # kept, for the charts
        self.assertEqual(row["proposals_now"], 212)
        self.assertIsNotNone(row["proposals_checked_at"])
        draft.assert_not_called()
        self.assertEqual(self.rows("SELECT * FROM shadow_ledger WHERE target='clickup'"), [])
        self.assertEqual(res.metrics["crowd"]["skipped"], 1)
        self.assertEqual(res.metrics["crowd"]["looked_up"], 1)

    def test_make_or_airtable_is_flagged_and_carded(self):
        srow, g = _quiet("Airtable + AI agents for ops", skills=("Airtable",))
        s = LookupSession(counts={srow["id"]: 212}, smart=[srow], terms={},
                          gets={srow["id"]: g})
        res, draft = self.scan(s)
        self.assertEqual(res.status, "completed", res.error)
        draft.assert_called_once()
        intents = [json.loads(r["payload_json"]) for r in
                   self.rows("SELECT * FROM shadow_ledger WHERE target='clickup'")]
        self.assertEqual(len(intents), 1)
        self.assertTrue(intents[0]["name"].endswith("· ⚠ 150+ proposals"), intents[0]["name"])

    def test_no_reading_is_carded_as_before(self):
        srow, g = _quiet("Senior AI Automation Specialist — LLM agents")
        s = LookupSession(counts={}, smart=[srow], terms={}, gets={srow["id"]: g})
        res, draft = self.scan(s)
        self.assertEqual(res.status, "completed", res.error)
        draft.assert_called_once()
        row = self.rows("SELECT * FROM scored_jobs WHERE id=?", (srow["id"],))[0]
        self.assertIsNone(row["proposals_now"])
        self.assertIsNotNone(row["proposals_checked_at"])
        self.assertEqual(res.metrics["crowd"]["unknown"], 1)

    def test_a_keyword_rows_count_needs_no_lookup(self):
        s = LookupSession(counts={}, smart=[SEARCH_HOURLY], terms={})   # carries 39
        res, _ = self.scan(s)
        self.assertEqual(res.status, "completed", res.error)
        self.assertFalse([c for c in s.calls if "title" in (c[1].get("params") or {})])
        row = self.rows("SELECT * FROM scored_jobs WHERE id=?", (SEARCH_HOURLY["id"],))[0]
        self.assertEqual(row["proposals_now"], 39)


class TestBeforeHeApplies(unittest.TestCase):
    NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)

    def setUp(self):
        # the lookups pace themselves (MCP_CALL_INTERVAL_S); not in a test
        p = mock.patch.object(mcp_search, "_sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)
        mcp_search._last_call[0] = 0.0
        self.tmp = Path(tempfile.mkdtemp(prefix="crowd-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.log = []
        self.ctx = mock.Mock(log=self.log.append)
        self.statuses = {}
        self.writes = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def card(self, jid, title, prev=None, age_h=2, checked_min=None, skills="[]",
             invite=False, applied=False):
        scored = (self.NOW - timedelta(hours=age_h)).strftime("%Y-%m-%d %H:%M:%S")
        checked = ((self.NOW - timedelta(minutes=checked_min)).strftime("%Y-%m-%d %H:%M:%S")
                   if checked_min is not None else None)
        bd = {"bonuses": {"invite": 4 if invite else 0}}
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO scored_jobs (id, title, status, clickup_task_id, score,"
                " score_breakdown_json, skills_json, first_scored_at, proposals_now,"
                " proposals_checked_at, loom_flag) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                (jid, title, "ClickUp Created", "t" + jid, 22, json.dumps(bd), skills,
                 scored, prev, checked))
            if applied:
                conn.execute("INSERT INTO proposals (id, marketplace_job_id) VALUES (?,?)",
                             ("p" + jid, jid))
        self.statuses["t" + jid] = "new"

    def req(self, method, path, tok, body=None):
        if method == "GET":
            return {"status": {"status": self.statuses[path.split("/")[2]]}}
        self.writes.append((method, path, body))
        return {}

    def run_recheck(self, counts, per_run=8):
        s = LookupSession(counts=counts)
        with mock.patch.object(config, "CROWD_RECHECK_PER_RUN", per_run):
            m = crowd.recheck(self.ctx, self.db_path, (s, "org"), "tok", req=self.req,
                              name_for=clickup_push.task_name, now=self.NOW)
        return m, s

    def row(self, jid):
        with db.connect(self.db_path) as conn:
            return dict(conn.execute("SELECT * FROM scored_jobs WHERE id=?", (jid,)).fetchone())

    def test_a_band_change_renames_the_card(self):
        self.card("1", "HubSpot Admin Support", prev=30)
        m, _ = self.run_recheck({"1": 70})
        self.assertEqual(m["renamed"], 1)
        self.assertEqual(self.writes, [("PUT", "/task/t1",
                                        {"name": "HubSpot Admin Support — score 22 · ⚠ 50+ proposals"})])
        self.assertEqual(self.row("1")["proposals_now"], 70)

    def test_the_same_band_writes_nothing(self):
        self.card("1", "HubSpot Admin Support", prev=25)
        m, _ = self.run_recheck({"1": 31})
        self.assertEqual((m["rechecked"], m["renamed"]), (1, 0))
        self.assertEqual(self.writes, [])
        self.assertEqual(self.row("1")["proposals_now"], 31)

    def test_past_150_an_open_card_is_skipped_with_a_comment(self):
        self.card("1", "AI Automation Specialist", prev=120)
        m, _ = self.run_recheck({"1": 180})
        self.assertEqual(m["skipped"], 1)
        (c_method, c_path, c_body), status = self.writes
        self.assertEqual((c_method, c_path), ("POST", "/task/t1/comment"))
        self.assertIn("180 proposals", c_body["comment_text"])
        self.assertEqual(status, ("PUT", "/task/t1", {"status": "skipped"}))
        self.assertEqual(self.row("1")["crowd_state"], "skipped")

    def test_a_card_that_left_new_is_never_written_and_stops_being_watched(self):
        self.card("1", "AI Automation Specialist", prev=120)
        self.statuses["t1"] = "applied"
        m, _ = self.run_recheck({"1": 400})
        self.assertEqual((m["left_new"], m["skipped"]), (1, 0))
        self.assertEqual(self.writes, [])
        self.assertEqual(self.row("1")["crowd_state"], "closed")
        m, s = self.run_recheck({"1": 400})
        self.assertEqual(m["rechecked"], 0)

    def test_airtable_past_150_is_renamed_not_skipped(self):
        self.card("1", "Airtable expert for grant workflows", prev=120)
        m, _ = self.run_recheck({"1": 400})
        self.assertEqual((m["skipped"], m["renamed"]), (0, 1))
        self.assertTrue(self.writes[0][2]["name"].endswith("· ⚠ 150+ proposals"))

    def test_left_alone_invites_applied_old_and_freshly_read(self):
        self.card("1", "Invited job", prev=10, invite=True)
        self.card("2", "Applied job", prev=10, applied=True)
        self.card("3", "Old card", prev=10, age_h=60)
        self.card("4", "Just read", prev=10, checked_min=10)
        m, s = self.run_recheck({"1": 400, "2": 400, "3": 400, "4": 400})
        self.assertEqual(m["rechecked"], 0)
        self.assertEqual(s.calls, [])
        self.assertEqual(self.writes, [])

    def test_never_read_first_and_capped_per_run(self):
        self.card("1", "Read an hour ago", prev=10, checked_min=90)
        self.card("2", "Never read")
        self.card("3", "Never read either")
        m, s = self.run_recheck({"1": 12, "2": 12, "3": 12}, per_run=2)
        self.assertEqual(m["rechecked"], 2)
        read = {self.row(j)["proposals_checked_at"] is not None for j in ("2", "3")}
        self.assertEqual(read, {True})
        self.assertEqual(self.row("1")["proposals_checked_at"],
                         (self.NOW - timedelta(minutes=90)).strftime("%Y-%m-%d %H:%M:%S"))

    def test_no_reading_stamps_the_time_and_writes_nothing(self):
        self.card("1", "AI Automation Specialist", prev=120)
        m, _ = self.run_recheck({})
        self.assertEqual((m["rechecked"], m["unknown"]), (1, 1))
        self.assertEqual(self.writes, [])
        r = self.row("1")
        self.assertEqual(r["proposals_now"], 120)             # last known kept
        self.assertEqual(r["proposals_checked_at"], self.NOW.strftime("%Y-%m-%d %H:%M:%S"))

    def test_a_clickup_failure_is_counted_not_raised(self):
        self.card("1", "AI Automation Specialist", prev=120)

        def broken(method, path, tok, body=None):
            if method == "GET":
                return {"status": {"status": "new"}}
            raise RuntimeError("clickup 500")
        s = LookupSession(counts={"1": 300})
        m = crowd.recheck(self.ctx, self.db_path, (s, "org"), "tok", req=broken,
                          name_for=clickup_push.task_name, now=self.NOW)
        self.assertEqual(m["errors"], 1)
        self.assertIsNone(self.row("1")["crowd_state"])         # tried again next run


class TestEmails(McpScanCase):
    def test_the_digest_line_says_how_crowded(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO scored_jobs (id, title, score, proposals_now, job_url)"
                         " VALUES ('1', 'HubSpot Admin', 22, 75, 'https://x')")
        self.assertIn("⚠ 50+ proposals", sp._digest_html(self.db_path, ["1"]))


if __name__ == "__main__":
    unittest.main()
