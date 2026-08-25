"""Phase 0.1 — the MCP job-feed provider (Upwork chat, 2026-08-25).

Pins: the standing-query round is network-only and bounded; the provider path
claims through the same Iron-Law gate as Chrome with 'mcp:<query>' feed
sources and runs the SAME tail (score -> draft -> card -> digest); posts older
than MCP_CLAIM_SINCE are never claimed; a dead MCP session fails loud; a thin
round is a critical anomaly, not a quiet market; the shadow poller never
holds a connection open across a network call (finding 12); the switch is
one config value and refuses to guess.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db, runner
from modules.upwork import scan_pipeline as sp
from modules.upwork import scoring
from modules.upwork.feeds import mcp_search, shadow_poll
from tests.test_mcp_feed import GET_FIXED, GET_HOURLY, SEARCH_FIXED, SEARCH_HOURLY

J_HOURLY = SEARCH_HOURLY["id"]           # 2091139773174847123
J_FIXED = SEARCH_FIXED["id"]             # 2091503480754476388
J_OLD = "2085134559026318204"            # a post from the scraper's era


def old_search_row():
    return {**SEARCH_HOURLY, "id": J_OLD, "title": "Old airtable job",
            "created_date": "2026-08-10T09:00:00+0000",
            "published_date": "2026-08-10T09:05:00+0000"}


class FakeSession:
    """A scripted mcp.upwork.com: smart_search returns the hourly job, the
    'airtable' term returns the fixed job + an old post, every other term is
    empty; `get` answers from the captured payloads."""

    def __init__(self, fail_init=False, smart=None, terms=None, gets=None,
                 conn_probe=None):
        self.calls = []
        self.fail_init = fail_init
        self.smart = [SEARCH_HOURLY] if smart is None else smart
        self.terms = ({"airtable": [SEARCH_FIXED, old_search_row()]}
                      if terms is None else terms)
        self.gets = ({J_HOURLY: GET_HOURLY, J_FIXED: GET_FIXED}
                     if gets is None else gets)
        self.conn_probe = conn_probe        # asserts no DB connection is open

    def initialize(self):
        if self.fail_init:
            raise RuntimeError("401 token rejected")

    def call(self, tool, args):
        if self.conn_probe:
            self.conn_probe()
        self.calls.append((tool, args))
        if tool == "list_accounts":
            return {"accounts": [{"role": "TALENT", "org_uid": "1647672547786633217"}]}
        action = args.get("action")
        if action == "smart_search":
            return {"jobs": self.smart, "pageInfo": {"hasNextPage": False}}
        if action == "search":
            return {"jobs": self.terms.get(args["params"]["query"], [])}
        if action == "get":
            row = self.gets.get(args["params"]["id"])
            if row is None:
                raise RuntimeError("get failed")
            return row
        raise AssertionError(f"unexpected tool call {tool} {args}")


def verdict(score):
    return {"score": score, "gated": False,
            "loom_flag": 1 if score >= config.LOOM_FLAG_THRESHOLD else 0,
            "breakdown": {"total": score, "raw_total": score}}


# ── the round ────────────────────────────────────────────────────────────────

class TestFetchNew(unittest.TestCase):
    def test_round_normalizes_new_jobs_with_one_get_each(self):
        s = FakeSession()
        out = mcp_search.fetch_new(s, "org", lambda m: None, known=set())
        ids = {j["id"] for j in out["jobs"]}
        self.assertEqual(ids, {J_HOURLY, J_FIXED, J_OLD})
        by = {j["id"]: j for j in out["jobs"]}
        self.assertEqual(by[J_HOURLY]["feed_source"], "mcp:smart_search")
        self.assertEqual(by[J_FIXED]["feed_source"], "mcp:airtable")
        self.assertEqual(by[J_HOURLY]["hourly_max"], 50)        # from the get
        self.assertEqual(by[J_FIXED]["fixed_budget"], 500.0)
        self.assertTrue(by[J_HOURLY]["href"].endswith(f"_~02{J_HOURLY}/"))
        gets = [c for c in s.calls if c[1].get("action") == "get"]
        self.assertEqual(len(gets), 3)                            # one per new job
        self.assertEqual(out["metrics"]["surfaced"], 3)
        self.assertEqual(out["metrics"]["new"], 3)

    def test_known_jobs_are_not_fetched_again(self):
        s = FakeSession()
        out = mcp_search.fetch_new(s, "org", lambda m: None, known={J_HOURLY})
        self.assertEqual({j["id"] for j in out["jobs"]}, {J_FIXED, J_OLD})
        self.assertEqual(out["metrics"]["by_feed"]["mcp:smart_search"]["known"], 1)
        gets = [c for c in s.calls if c[1].get("action") == "get"]
        self.assertNotIn(J_HOURLY, [c[1]["params"]["id"] for c in gets])

    def test_claim_since_drops_older_posts_and_reports_them(self):
        s = FakeSession()
        out = mcp_search.fetch_new(s, "org", lambda m: None, known=set(),
                                   claim_since="2026-08-20T00:00:00+00:00")
        self.assertEqual({j["id"] for j in out["jobs"]}, {J_HOURLY, J_FIXED})
        self.assertEqual(out["metrics"]["skipped_before_cut"], 1)
        self.assertEqual(out["metrics"]["by_feed"]["mcp:airtable"]["skipped_before_cut"], 1)
        # and no get was spent on the post that was never going to be claimed
        gets = [c[1]["params"]["id"] for c in s.calls if c[1].get("action") == "get"]
        self.assertNotIn(J_OLD, gets)

    def test_a_post_with_no_parseable_date_is_kept_not_dropped(self):
        undated = {**SEARCH_FIXED, "id": "2091660219337488105",
                   "created_date": "yesterday", "published_date": None}
        s = FakeSession(terms={"airtable": [undated]}, gets={})
        out = mcp_search.fetch_new(s, "org", lambda m: None, known=set(),
                                   claim_since="2026-08-20T00:00:00+00:00")
        self.assertEqual([j["id"] for j in out["jobs"] if j["id"] == undated["id"]],
                         [undated["id"]])

    def test_get_ceiling_is_config_and_capped_rows_still_arrive(self):
        s = FakeSession()
        with mock.patch.object(config, "MCP_MAX_GETS_PER_RUN", 1):
            out = mcp_search.fetch_new(s, "org", lambda m: None, known=set())
        self.assertEqual(out["metrics"]["gets"], 1)
        self.assertEqual(out["metrics"]["get_capped"], 2)
        self.assertEqual(len(out["jobs"]), 3)                     # search-shaped rows
        self.assertEqual(sum(1 for j in out["jobs"] if j["_got_detail"]), 1)

    def test_parse_instant_repairs_the_bare_offset(self):
        self.assertEqual(mcp_search.parse_instant("2026-08-22T12:25:48+0000").isoformat(),
                         "2026-08-22T12:25:48+00:00")
        self.assertEqual(mcp_search.parse_instant("2026-08-22T12:25:48Z").isoformat(),
                         "2026-08-22T12:25:48+00:00")
        self.assertIsNone(mcp_search.parse_instant("yesterday"))
        self.assertIsNone(mcp_search.parse_instant(None))

    def test_the_standing_queries_are_config(self):
        self.assertIs(shadow_poll.TERM_QUERIES, config.MCP_TERM_QUERIES)
        self.assertEqual(shadow_poll.SMART_PAGES, config.MCP_SMART_PAGES)
        self.assertEqual(shadow_poll.MAX_GETS_PER_RUN, config.MCP_MAX_GETS_PER_RUN)


# ── the provider inside the scan ─────────────────────────────────────────────

class McpScanCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mcp-scan-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.tmp / "logs"),
            mock.patch.object(config, "LOCK_DIR", self.tmp / "locks"),
            mock.patch.object(config, "JOB_FEED_PROVIDER", "mcp"),
            mock.patch.object(config, "MCP_MIN_HEALTHY_SURFACED", 2),
            mock.patch.dict(config.SHADOW_MODE, {"upwork": True}),
            # Chrome must never be touched on this provider
            mock.patch.object(sp, "ensure_scan_tab",
                              side_effect=AssertionError("Chrome touched")),
            mock.patch.object(sp, "navigate_tab",
                              side_effect=AssertionError("Chrome touched")),
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

    def run_scan(self, session, score_map=None):
        score_map = score_map or {}

        def fake_score(row, task_name, db_path=None):
            return verdict(score_map.get(row["id"], 14))

        from modules.upwork.feeds import mcp_client
        with mock.patch.object(mcp_client, "MCPSession", return_value=session), \
             mock.patch.object(scoring, "score_job", side_effect=fake_score), \
             mock.patch.object(scoring, "draft_proposal_grounded",
                               return_value=("Drafted proposal text.",
                                             {"status": "supplied"})):
            return runner.run_task(
                sp.TASK_NAME, lambda ctx: sp.scan(ctx, _sleep=lambda s: None),
                module="upwork", db_path=self.db_path)


class TestScanMcpProvider(McpScanCase):
    def test_claims_with_mcp_feed_sources_and_runs_the_same_tail(self):
        result = self.run_scan(FakeSession(),
                               score_map={J_HOURLY: config.LOOM_FLAG_THRESHOLD + 2,
                                          J_FIXED: 14, J_OLD: 14})
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(result.metrics["provider"], "mcp")
        self.assertEqual(result.metrics["claimed"], 3)
        # J_OLD has no `get` payload in the fake -> a search-only hourly row
        # with no rate -> the hard rules skip it before any model call, exactly
        # as they would on the Chrome path (rate_unstated_low_client)
        self.assertEqual(result.metrics["scored"], 2)
        self.assertEqual(result.metrics["skipped"], 1)
        self.assertEqual(result.metrics["drafts"], 1)              # the hot one
        self.assertEqual(result.metrics["hot"], 1)
        rows = {r["id"]: r for r in self.rows("SELECT * FROM scored_jobs")}
        self.assertEqual(rows[J_HOURLY]["feed_source"], "mcp:smart_search")
        self.assertEqual(rows[J_FIXED]["feed_source"], "mcp:airtable")
        self.assertEqual(rows[J_HOURLY]["status"], "Drafted")
        self.assertEqual(rows[J_HOURLY]["hourly_max"], 50)          # the get's field
        self.assertEqual(rows[J_HOURLY]["total_applicants"], 39)    # exact, not a tier
        self.assertEqual(json.loads(rows[J_HOURLY]["skills_json"]), ["Make.com"])
        self.assertIn(f"~02{J_HOURLY}", rows[J_HOURLY]["job_url"])
        # the shared tail: cards intended (shadow), digest intended
        ledger = self.rows("SELECT target, entity_key FROM shadow_ledger")
        self.assertEqual(sum(1 for r in ledger if r["target"] == "clickup"), 2)
        self.assertEqual(sum(1 for r in ledger if r["target"] == "email"), 1)
        # every query the round ran is in the metrics, empty ones included
        self.assertIn("mcp:smart_search", result.metrics["feeds"])
        self.assertIn("mcp:airtable", result.metrics["feeds"])
        self.assertEqual(result.metrics["feeds"]["mcp:airtable"]["new"], 2)

    def test_second_round_claims_nothing_and_skips_empty(self):
        self.run_scan(FakeSession())
        result = self.run_scan(FakeSession())
        self.assertEqual(result.status, "skipped_empty")
        self.assertEqual(len(self.rows("SELECT id FROM scored_jobs")), 3)

    def test_claim_since_is_honoured_at_the_flip(self):
        with mock.patch.object(config, "MCP_CLAIM_SINCE", "2026-08-20T00:00:00+00:00"):
            result = self.run_scan(FakeSession())
        ids = {r["id"] for r in self.rows("SELECT id FROM scored_jobs")}
        self.assertEqual(ids, {J_HOURLY, J_FIXED})                  # J_OLD never claimed
        self.assertEqual(result.metrics["feeds"]["mcp:airtable"]["skipped_before_cut"], 1)

    def test_hard_rules_run_before_any_model_call(self):
        n8n = {**SEARCH_HOURLY, "id": "2091731920468553712",
               "title": "n8n migration expert", "skills": ["n8n"]}
        calls = []
        s = FakeSession(smart=[n8n], terms={}, gets={})
        from modules.upwork.feeds import mcp_client
        with mock.patch.object(mcp_client, "MCPSession", return_value=s), \
             mock.patch.object(scoring, "score_job",
                               side_effect=lambda *a, **k: calls.append(1) or verdict(20)):
            with mock.patch.object(config, "MCP_MIN_HEALTHY_SURFACED", 1):
                result = runner.run_task(
                    sp.TASK_NAME, lambda ctx: sp.scan(ctx, _sleep=lambda s: None),
                    module="upwork", db_path=self.db_path)
        self.assertEqual(calls, [])
        self.assertEqual(result.metrics["skipped"], 1)
        row = self.rows("SELECT status, hard_rule_skip FROM scored_jobs")[0]
        self.assertEqual((row["status"], row["hard_rule_skip"]), ("Skipped", "n8n_exclusion"))

    def test_a_dead_session_fails_loud(self):
        with self.assertRaises(RuntimeError):
            self.run_scan(FakeSession(fail_init=True))
        run = self.rows("SELECT status, error FROM runs")[-1]
        self.assertEqual(run["status"], "failed")
        self.assertIn("MCP session", run["error"])
        crit = self.rows("SELECT kind FROM anomalies WHERE severity='critical'")
        self.assertTrue(crit)

    def test_a_thin_round_is_a_critical_anomaly_not_a_quiet_market(self):
        with mock.patch.object(config, "MCP_MIN_HEALTHY_SURFACED", 10):
            result = self.run_scan(FakeSession())                  # surfaces 3
        self.assertEqual(result.status, "completed")               # still scored
        kinds = self.rows("SELECT kind, severity FROM anomalies")
        self.assertIn(("scan_feed_degraded", "critical"),
                      {(a["kind"], a["severity"]) for a in kinds})

    def test_an_unknown_provider_refuses_to_guess(self):
        with mock.patch.object(config, "JOB_FEED_PROVIDER", "graphql"):
            with self.assertRaises(RuntimeError):
                self.run_scan(FakeSession())
        self.assertEqual(self.rows("SELECT id FROM scored_jobs"), [])

    def test_chrome_provider_never_opens_an_mcp_session(self):
        from modules.upwork.feeds import mcp_client
        with mock.patch.object(config, "JOB_FEED_PROVIDER", "chrome"), \
             mock.patch.object(mcp_client, "MCPSession",
                               side_effect=AssertionError("MCP touched")), \
             mock.patch.object(sp, "ensure_scan_tab", return_value=(1, 1)), \
             mock.patch.object(sp, "navigate_tab"), \
             mock.patch.object(sp.feed_bridge, "chrome_eval",
                               return_value=json.dumps({
                                   "url": "https://www.upwork.com/nx/find-work/my-feed",
                                   "jobs": [], "tiles": []})):
            result = runner.run_task(
                sp.TASK_NAME, lambda ctx: sp.scan(ctx, _sleep=lambda s: None),
                module="upwork", db_path=self.db_path)
        self.assertEqual(result.status, "skipped_empty")


# ── finding 12: the poller's lock discipline ─────────────────────────────────

class TestPollerHoldsNoLockAcrossTheNetwork(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mcp-poll-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.tmp / "logs"),
            mock.patch.object(config, "LOCK_DIR", self.tmp / "locks"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_connection_is_open_during_any_mcp_call(self):
        open_depth = {"n": 0}
        real_connect = db.connect

        class Probe:
            def __init__(self, dbp):
                self.cm = real_connect(dbp)

            def __enter__(self):
                open_depth["n"] += 1
                return self.cm.__enter__()

            def __exit__(self, *a):
                open_depth["n"] -= 1
                return self.cm.__exit__(*a)

        def probe():
            if open_depth["n"]:
                raise AssertionError("MCP call made while a DB connection is open")

        session = FakeSession(conn_probe=probe)
        from modules.upwork.feeds import mcp_client
        with mock.patch.object(shadow_poll.db, "connect", Probe), \
             mock.patch.object(mcp_client, "MCPSession", return_value=session):
            result = runner.run_task(shadow_poll.TASK_NAME, shadow_poll.poll,
                                     module="system", db_path=self.db_path)
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(result.metrics["new"], 3)
        self.assertEqual(result.metrics["gets"], 3)
        with real_connect(self.db_path) as conn:
            rows = {r["id"]: dict(r) for r in conn.execute(
                "SELECT id, found_by, got_detail, hard_rule_skip FROM mcp_shadow_jobs")}
        self.assertEqual(set(rows), {J_HOURLY, J_FIXED, J_OLD})
        self.assertEqual(rows[J_HOURLY]["found_by"], "mcp:smart_search")
        self.assertEqual(rows[J_HOURLY]["got_detail"], 1)

    def test_second_poll_updates_last_seen_and_backfills_thin_rows(self):
        from modules.upwork.feeds import mcp_client
        # first poll with a get budget of 1: two rows stay thin
        with mock.patch.object(config, "MCP_MAX_GETS_PER_RUN", 1), \
             mock.patch.object(shadow_poll, "MAX_GETS_PER_RUN", 1), \
             mock.patch.object(mcp_client, "MCPSession", return_value=FakeSession()):
            first = runner.run_task(shadow_poll.TASK_NAME, shadow_poll.poll,
                                    module="system", db_path=self.db_path)
        self.assertEqual(first.metrics["gets"], 1)
        self.assertEqual(first.metrics["get_capped"], 2)
        with mock.patch.object(mcp_client, "MCPSession", return_value=FakeSession()):
            second = runner.run_task(shadow_poll.TASK_NAME, shadow_poll.poll,
                                     module="system", db_path=self.db_path)
        self.assertEqual(second.metrics["new"], 0)
        self.assertEqual(second.metrics["seen_again"], 3)
        self.assertEqual(second.metrics["backfilled"], 1)          # J_OLD has no get payload
        with db.connect(self.db_path) as conn:
            thin = conn.execute("SELECT COUNT(*) FROM mcp_shadow_jobs WHERE got_detail=0"
                                ).fetchone()[0]
        # J_OLD has no get payload in the fake -> stays thin; J_FIXED got filled
        self.assertEqual(thin, 1)


if __name__ == "__main__":
    unittest.main()
