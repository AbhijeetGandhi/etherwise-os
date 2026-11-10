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
from tests.test_mcp_feed import (GET_FIXED, GET_HOURLY, NO_RANGE_ROW_1110,
                                 SEARCH_FIXED, SEARCH_HOURLY, SEARCH_ROW_1110,
                                 SMART_ROW_1110)

J_HOURLY = SEARCH_HOURLY["id"]           # 2091139773174847123
J_FIXED = SEARCH_FIXED["id"]             # 2091503480754476388
J_OLD = "2085134559026318204"            # a post from the scraper's era


def old_search_row():
    # NOTE the neutral title: since 2026-09-23 a platform in the title exempts
    # a job from the rate floors, and this fixture's whole job is to be the row
    # that `rate_unstated_low_client` stops before the model. "Old airtable
    # job" would now sail through and there would be nothing left to skip.
    # The exemption itself is proven in test_mcp_feed and test_upwork_scoring.
    return {**SEARCH_HOURLY, "id": J_OLD, "title": "Old backend job",
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
            # a title search is the crowd check's lookup (2026-10-23); this
            # fake knows none, so the count stays unknown
            return {"jobs": self.terms.get(args["params"].get("query"), [])}
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

class PacedCase(unittest.TestCase):
    """The round paces itself (MCP_CALL_INTERVAL_S); tests replace the sleep
    with a recorder so a 40-call round does not take 3 minutes to test."""

    def setUp(self):
        self.sleeps = []
        self._p = mock.patch.object(mcp_search, "_sleep",
                                    side_effect=lambda s: self.sleeps.append(s))
        self._p.start()
        mcp_search._last_call[0] = 0.0

    def tearDown(self):
        self._p.stop()
        mcp_search._last_call[0] = 0.0


class TestPacing(PacedCase):
    def test_every_call_after_the_first_waits_the_interval(self):
        s = FakeSession()
        with mock.patch.object(config, "MCP_CALL_INTERVAL_S", 5.0):
            mcp_search.fetch_new(s, "org", lambda m: None, known=set())
        find_calls = [c for c in s.calls if c[0] == "find_jobs"]
        # 1 smart page + every standing term + 3 gets, one wait between each.
        # Bound to the config list rather than a literal: the terms were
        # rebalanced on measured yield (2026-09-08) and will be again.
        expect = 1 + len(config.MCP_TERM_QUERIES) + 3
        self.assertEqual(len(find_calls), expect)
        self.assertEqual(len(self.sleeps), expect - 1)
        self.assertTrue(all(0 < w <= 5.0 for w in self.sleeps), self.sleeps)

    def test_a_rate_limited_call_is_retried_once_after_the_advised_wait(self):
        class Limited(FakeSession):
            def __init__(self):
                super().__init__()
                self.refused = 0

            def call(self, tool, args):
                if args.get("action") == "search" and args["params"]["query"] == "airtable" \
                        and self.refused == 0:
                    self.refused += 1
                    raise RuntimeError("find_jobs: Rate limit exceeded for this tool."
                                       " Wait about 5 seconds before retrying")
                return super().call(tool, args)

        s = Limited()
        logs = []
        with mock.patch.object(config, "MCP_RATE_LIMIT_WAIT_S", 6.0):
            out = mcp_search.fetch_new(s, "org", logs.append, known=set())
        self.assertIn(J_FIXED, {j["id"] for j in out["jobs"]})    # the term still landed
        self.assertIn(6.0, self.sleeps)
        self.assertTrue(any("rate-limited" in m for m in logs))

    def test_a_second_refusal_is_the_terms_failure_not_the_rounds(self):
        class Dead(FakeSession):
            def call(self, tool, args):
                if args.get("action") == "search" and args["params"]["query"] == "airtable":
                    raise RuntimeError("Rate limit exceeded")
                return super().call(tool, args)

        logs = []
        out = mcp_search.fetch_new(Dead(), "org", logs.append, known=set())
        self.assertNotIn(J_FIXED, {j["id"] for j in out["jobs"]})
        self.assertIn(J_HOURLY, {j["id"] for j in out["jobs"]})   # the rest of the round ran
        self.assertTrue(any("search 'airtable' failed" in m for m in logs))


class TestFetchNew(PacedCase):
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


# ── Upwork's daily allowance of job opens (2026-11-10) ───────────────────────

REFUSAL = 'find_jobs: {"error_code":"PERMISSION","reason":"This job can\'t be opened right now. It may be private or invite-only, or jobs are being opened too quickly. Slow down and try again later, and don\'t retry the same job repeatedly."}'


class Refusing(FakeSession):
    """Upwork after the day's allowance is spent: every `get` refused."""

    def __init__(self, ok=(), **kw):
        super().__init__(**kw)
        self.ok = set(ok)                 # ids still answered

    def call(self, tool, args):
        if args.get("action") == "get" and args["params"]["id"] not in self.ok:
            self.calls.append((tool, args))
            raise RuntimeError(REFUSAL)
        return super().call(tool, args)


def gets_of(session):
    return [c[1]["params"]["id"] for c in session.calls if c[1].get("action") == "get"]


def low_rate_row(jid, rate="10.00–15.00/hr", **kw):
    """A keyword row with the whole post on it: the rules can settle it."""
    return {**SEARCH_ROW_1110, "id": jid, "title": kw.get("title", "Backend Developer"),
            "budget": rate, "created_date": "2026-11-09T21:00:00Z",
            "description": kw.get("description", "Build our backend, Make.com welcome.")}


class TestGetAllowance(PacedCase):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="get-pause-"))
        self.pause = self.tmp / "upwork-get-pause.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def test_two_refusals_in_a_row_pause_the_rest_of_the_run(self):
        s = Refusing()
        logs = []
        out = mcp_search.fetch_new(s, "org", logs.append, known=set(),
                                   pause_file=self.pause)
        m = out["metrics"]
        self.assertEqual(len(gets_of(s)), 2)               # the third was never asked
        self.assertEqual((m["get_refused"], m["get_paused"]), (2, 1))
        self.assertEqual(len(out["jobs"]), 3)              # every job still arrives
        self.assertTrue(m["paused_until"].endswith("T00:00:00+00:00"))
        self.assertTrue(any("gets PAUSED" in x for x in logs))
        saved = json.loads(self.pause.read_text())
        self.assertEqual(saved["until"], m["paused_until"])

    def test_the_pause_outlives_the_run_and_ends_at_the_reset(self):
        mcp_search.fetch_new(Refusing(), "org", lambda x: None,
                             known=set(), pause_file=self.pause)
        s = FakeSession()                                  # Upwork would answer now...
        logs = []
        out = mcp_search.fetch_new(s, "org", logs.append, known=set(),
                                   pause_file=self.pause)
        self.assertEqual(gets_of(s), [])                   # ...but nobody asks
        self.assertEqual(out["metrics"]["get_paused"], 3)
        self.assertTrue(any("gets paused until" in x for x in logs))
        # past the reset the file is history
        self.pause.write_text(json.dumps({"until": "2020-01-01T00:00:00+00:00"}))
        s = FakeSession()
        mcp_search.fetch_new(s, "org", lambda x: None, known=set(), pause_file=self.pause)
        self.assertEqual(len(gets_of(s)), 3)

    def test_one_refusal_is_a_private_job_not_a_pause(self):
        s = Refusing(ok={J_FIXED, J_OLD})                  # only J_HOURLY refused
        out = mcp_search.fetch_new(s, "org", lambda x: None, known=set(),
                                   pause_file=self.pause)
        self.assertEqual(len(gets_of(s)), 3)
        self.assertEqual(out["metrics"]["get_refused"], 1)
        self.assertIsNone(out["metrics"]["paused_until"])
        self.assertIsNone(mcp_search.paused_until(self.pause))
        self.assertEqual(mcp_search.refusal_streak(self.pause), 0)   # the success reset it

    def one_new(self, session_cls=FakeSession, **kw):
        """A round where only J_FIXED is new: one get, at most."""
        s = session_cls(**kw)
        out = mcp_search.fetch_new(s, "org", lambda x: None,
                                   known={J_HOURLY, J_OLD}, pause_file=self.pause)
        return s, out

    def test_refusals_in_a_row_count_across_runs(self):
        """Most runs now ask for one post or none, so a streak that died with
        each run would never reach two (03:45 IST, 10 Nov: one get, refused)."""
        _, out = self.one_new(Refusing)
        self.assertIsNone(out["metrics"]["paused_until"])
        self.assertEqual(mcp_search.refusal_streak(self.pause), 1)
        _, out = self.one_new(Refusing)
        self.assertIsNotNone(out["metrics"]["paused_until"])
        s, out = self.one_new()                            # the next run asks nothing
        self.assertEqual(gets_of(s), [])
        self.assertEqual(out["metrics"]["get_paused"], 1)

    def test_a_post_that_opens_breaks_the_streak(self):
        self.one_new(Refusing)
        self.one_new()                                     # answered
        self.assertEqual(mcp_search.refusal_streak(self.pause), 0)
        _, out = self.one_new(Refusing)
        self.assertIsNone(out["metrics"]["paused_until"])

    def test_a_refusal_before_the_reset_does_not_count(self):
        from datetime import datetime, timedelta, timezone
        yesterday = datetime.now(timezone.utc) - timedelta(days=1, minutes=5)
        self.pause.write_text(json.dumps({"streak": 1,
                                          "last_refusal": yesterday.isoformat()}))
        self.assertEqual(mcp_search.refusal_streak(self.pause), 0)
        _, out = self.one_new(Refusing)
        self.assertIsNone(out["metrics"]["paused_until"])

    def test_without_a_pause_file_the_pause_holds_for_the_run_only(self):
        out = mcp_search.fetch_new(Refusing(), "org", lambda x: None, known=set())
        self.assertIsNotNone(out["metrics"]["paused_until"])
        s = FakeSession()
        mcp_search.fetch_new(s, "org", lambda x: None, known=set())
        self.assertEqual(len(gets_of(s)), 3)

    def test_no_open_is_spent_on_a_job_its_row_settles(self):
        low = low_rate_row("2119907348261953417")                       # $10-15/hr
        ok = low_rate_row("2119907452813069724", rate="$40.00–$70.00/hr")
        snip = {**low, "id": "2119907519364820157"}
        snip.pop("description")                                          # a snippet only
        s = FakeSession(smart=[snip], terms={"airtable": [low, ok]}, gets={})
        out = mcp_search.fetch_new(s, "org", lambda x: None, known=set(),
                                   rule_check=scoring.hard_rule_skip)
        asked = gets_of(s)
        self.assertNotIn(low["id"], asked)       # whole post + $10-15: settled, no get
        self.assertIn(ok["id"], asked)           # will be scored: worth the open
        self.assertIn(snip["id"], asked)         # snippet: the post may say "placeholder"
        self.assertEqual(out["metrics"]["get_saved"], 1)
        by = {j["id"]: j for j in out["jobs"]}
        self.assertEqual(scoring.hard_rule_skip(by[low["id"]]), "hourly_below_30")

    def test_a_get_is_still_spent_where_it_can_change_the_verdict(self):
        # no range, verified client, spend under the bar, hires unknown on the
        # row: the get's hire count could prove the client -> ask
        blank = {**NO_RANGE_ROW_1110, "id": "2119907683140592876",
                 "title": "Backend Developer", "description": "Build our backend.",
                 "client": {"country": "United States", "total_spent": "$900.00",
                            "verification_status": "VERIFIED"}}
        s = FakeSession(smart=[], terms={"airtable": [blank]}, gets={})
        out = mcp_search.fetch_new(s, "org", lambda x: None, known=set(),
                                   rule_check=scoring.hard_rule_skip)
        self.assertEqual(gets_of(s), [blank["id"]])
        self.assertEqual(out["metrics"]["get_saved"], 0)

    def test_keyword_searches_ask_for_the_whole_post(self):
        s = FakeSession()
        mcp_search.fetch_new(s, "org", lambda x: None, known=set())
        searches = [c[1]["params"] for c in s.calls if c[1].get("action") == "search"]
        self.assertTrue(searches)
        self.assertTrue(all(p.get("include_full_details") is True for p in searches))

    def test_the_whole_post_joins_the_best_matches_row(self):
        s = FakeSession(smart=[SMART_ROW_1110], terms={"airtable": [SEARCH_ROW_1110]},
                        gets={})
        out = mcp_search.fetch_new(s, "org", lambda x: None, known=set())
        job = {j["id"]: j for j in out["jobs"]}[SMART_ROW_1110["id"]]
        self.assertEqual(job["feed_source"], "mcp:smart_search")     # first finder kept
        self.assertEqual(job["post_source"], "search")               # ...with the full text
        self.assertEqual(job["total_applicants"], 24)                # the keyword row's count
        self.assertEqual(scoring.hard_rule_skip(job), "hourly_below_30")

    def test_a_best_matches_snippet_gets_its_whole_post_from_a_search(self):
        """2026-11-10: while gets are refused, a Best Matches job was judged on
        a 300-character snippet. One title search (include_full_details, no
        job-open spent) brings the whole post and the live proposal count."""
        class TitleSearch(FakeSession):
            def call(self, tool, args):
                p = (args.get("params") or {})
                if args.get("action") == "search" and "title" in p:
                    self.calls.append((tool, args))
                    return {"jobs": [SEARCH_ROW_1110] if p.get("include_full_details")
                            else [{**SEARCH_ROW_1110, "description": None}]}
                return super().call(tool, args)
        s = TitleSearch(smart=[SMART_ROW_1110], terms={}, gets={})
        out = mcp_search.fetch_new(s, "org", lambda x: None, known=set(),
                                   rule_check=scoring.hard_rule_skip, lookup_text=True)
        job = out["jobs"][0]
        self.assertEqual(job["post_source"], "search")
        self.assertIn("5,362 characters", job["description"])
        self.assertEqual(job["total_applicants"], 24)          # the live count came too
        self.assertEqual(job["feed_source"], "mcp:smart_search")
        self.assertEqual((out["metrics"]["text_looked_up"], out["metrics"]["text_found"]), (1, 1))
        # whole post + $15-20/hr: the row settles it, no job-open is spent
        self.assertEqual(gets_of(s), [])
        self.assertEqual(out["metrics"]["get_saved"], 1)

    def test_no_lookup_unless_asked_or_when_the_row_has_the_post(self):
        s = FakeSession(smart=[SMART_ROW_1110], terms={"airtable": [SEARCH_ROW_1110]}, gets={})
        out = mcp_search.fetch_new(s, "org", lambda x: None, known=set(), lookup_text=True)
        titled = [c for c in s.calls if "title" in (c[1].get("params") or {})]
        self.assertEqual(titled, [])                  # the keyword row already brought it
        self.assertEqual(out["metrics"]["text_looked_up"], 0)
        s = FakeSession(smart=[SMART_ROW_1110], terms={}, gets={})
        mcp_search.fetch_new(s, "org", lambda x: None, known=set())   # not asked
        self.assertEqual([c for c in s.calls if "title" in (c[1].get("params") or {})], [])

    def test_rows_report_whether_they_carry_a_rate(self):
        s = FakeSession(smart=[SMART_ROW_1110, NO_RANGE_ROW_1110], terms={}, gets={})
        out = mcp_search.fetch_new(s, "org", lambda x: None, known=set())
        r = out["metrics"]["rate"]
        self.assertEqual((r["hourly_rows"], r["hourly_with_budget"]), (2, 1))
        self.assertEqual(r["unread"], [])
        s = FakeSession(smart=[], terms={"airtable": [old_search_row()]}, gets={})
        r = mcp_search.fetch_new(s, "org", lambda x: None, known=set())["metrics"]["rate"]
        self.assertEqual(r["unread"], [{"id": J_OLD, "budget": "0.0"}])


# ── the provider inside the scan ─────────────────────────────────────────────

class McpScanCase(PacedCase):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="mcp-scan-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.tmp / "logs"),
            mock.patch.object(config, "LOCK_DIR", self.tmp / "locks"),
            mock.patch.object(config, "JOB_FEED_PROVIDER", "mcp"),
            # the fixtures predate the live cut (set at the 08-26 flip); the
            # one test about the cut patches its own value
            mock.patch.object(config, "MCP_CLAIM_SINCE", None),
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
        super().tearDown()

    def rows(self, sql, args=()):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, args)]

    def run_scan(self, session, score_map=None):
        score_map = score_map or {}

        def fake_score(row, task_name, db_path=None):
            return verdict(score_map.get(row["id"], 21))

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
                                          J_FIXED: 21, J_OLD: 21})
        self.assertEqual(result.status, "completed", result.error)
        self.assertEqual(result.metrics["provider"], "mcp")
        self.assertEqual(result.metrics["claimed"], 3)
        # J_OLD has no `get` payload in the fake and its row is the August shape
        # ("budget": "0.0" on an hourly job): a rate nobody could read. Since
        # 2026-11-10 that fails closed before any model call (rate_unread);
        # it used to read as rate-unstated, which is how a $15-20/hr job was
        # carded on 9 Nov.
        self.assertEqual(result.metrics["scored"], 2)
        self.assertEqual(result.metrics["skipped"], 1)
        old = self.rows("SELECT hard_rule_skip, rate_source FROM scored_jobs"
                        " WHERE id=?", (J_OLD,))[0]
        self.assertEqual((old["hard_rule_skip"], old["rate_source"]),
                         ("rate_unread", "unread"))
        # BOTH scored rows are hot since 2026-09-23: the hot bar sits on the
        # card floor, so "carded but not hot" is a shape that no longer exists
        # and a scan drafts for everything it cards. J_FIXED at 21 used to be a
        # Standard card with no draft; there is no Standard list any more.
        self.assertEqual(result.metrics["drafts"], 2)
        self.assertEqual(result.metrics["hot"], 2)
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

    def test_screening_questions_are_kept_for_the_drafter(self):
        """2026-10-12: the `get` carries a post's screening questions; the claim
        path dropped them, so the draft never answered them (migration 040)."""
        result = self.run_scan(FakeSession())
        self.assertEqual(result.status, "completed", result.error)
        rows = {r["id"]: r for r in self.rows("SELECT * FROM scored_jobs")}
        self.assertEqual(json.loads(rows[J_HOURLY]["screening_questions_json"]),
                         ["Describe 2\u20133 production AI automation systems you've built"])
        self.assertIsNone(rows[J_FIXED]["screening_questions_json"])     # none asked
        self.assertIsNone(rows[J_OLD]["screening_questions_json"])       # no `get`
        self.assertIn("1. Describe 2\u20133 production AI automation systems",
                      scoring.job_brief(rows[J_HOURLY]))

    def test_the_9_nov_card_never_reaches_the_model(self):
        """2026-11-10: hourly $15-20, the get refused, a proven client. It was
        scored 26 and carded; now the row's rate stops it at the rules, and
        the row records where its rate and text came from."""
        s = Refusing(smart=[SMART_ROW_1110], terms={})
        result = self.run_scan(s, score_map={SMART_ROW_1110["id"]: 26})
        self.assertEqual(result.status, "completed", result.error)
        row = self.rows("SELECT * FROM scored_jobs WHERE id=?", (SMART_ROW_1110["id"],))[0]
        self.assertEqual(row["hard_rule_skip"], "hourly_below_30")
        self.assertIsNone(row["score"])
        self.assertEqual((row["hourly_min"], row["hourly_max"]), (15.0, 20.0))
        self.assertEqual((row["rate_source"], row["post_source"]), ("search", "snippet"))
        self.assertEqual(row["client_total_spent"], 13556.63)
        self.assertEqual(result.metrics["scored"], 0)

    def test_an_unreadable_rate_is_loud(self):
        result = self.run_scan(FakeSession(smart=[], terms={"airtable": [old_search_row()]},
                                           gets={}))
        self.assertEqual(result.status, "completed", result.error)
        kinds = [r["kind"] for r in self.rows("SELECT * FROM anomalies")]
        self.assertIn("rate_unread", kinds)

    def test_the_pause_file_sits_beside_the_database(self):
        self.run_scan(Refusing(smart=[SMART_ROW_1110, NO_RANGE_ROW_1110], terms={
            "airtable": [low_rate_row("2119907791625384903", rate="$40.00\u2013$70.00/hr")]}))
        self.assertTrue((self.db_path.parent / "upwork-get-pause.json").exists())

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
                               side_effect=lambda *a, **k: calls.append(1) or verdict(25)):
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

class TestPollerHoldsNoLockAcrossTheNetwork(PacedCase):
    def setUp(self):
        super().setUp()
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
        super().tearDown()

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
