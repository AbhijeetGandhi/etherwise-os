"""Tests for modules/upwork/scan_pipeline.py — Chrome and the gateway are
mocked; claiming, scoring flow, shadow intents, and dedup run real code
against a temp v3 DB.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db
from modules.upwork import scan_pipeline as sp
from modules.upwork import scoring


def tile(jid, title="Make.com Automation Build", posted="Posted 2 hours ago",
         budget_line="Hourly: $35.00 - $50.00 - Expert - Est. Time: More"
                     " than 6 months, 30+ hrs/week"):
    return {
        "href": f"https://www.upwork.com/jobs/Make_~02{jid}/",
        "title": title,
        "text": f"{posted}\n•\nProposals: 5 to 10\n{title}\n{budget_line}\n"
                f"Automate things with Make and Airtable.\nSkills\nMake.com\n"
                f"Airtable\nVerified\n\nPayment verified\n\n"
                f"Rating is 4.9 out of 5.\n $12K+ spent\n  USA",
    }


def payload(*tiles_, url="https://www.upwork.com/nx/find-work/my-feed"):
    return json.dumps({"url": url,
                       "tile_count": len(tiles_), "tiles": list(tiles_)})


def verdict(score=18):
    return {"score": score, "gated": False,
            "loom_flag": 1 if score >= 22 else 0,
            "breakdown": {"skill_fit": 4, "budget": 4, "client_quality": 2,
                          "competition": 4, "description_quality": 4,
                          "base_total": 22, "bonuses": {"core_stack": 3},
                          "raw_total": score, "recommendation": "bid",
                          "gate": None, "total": score}}


def ssr_job(jid, title="Airtable + Make.com Automation Specialist", **kw):
    """One job as it appears in the __NUXT__ SSR payload."""
    j = {"uid": jid, "ciphertext": "~02" + jid, "title": title,
         "description": "Automate the intake pipeline with Make and Airtable.",
         "type": 2, "hourlyBudget": {"min": 35, "max": 50},
         "amount": {"amount": 0}, "tierText": "Expert",
         "durationLabel": "More than 6 months", "proposalsTier": "5 to 10",
         "publishedOn": "2026-07-13T07:54:56.303Z",
         # real payload: named skills live in `attrs`; `skills` is often nameless
         "attrs": [{"prettyName": "Make.com"}, {"prettyName": "Airtable"}],
         "skills": [{"uid": "1"}, {"uid": "2"}],
         "client": {"paymentVerificationStatus": 1, "totalSpent": 12000.0,
                    "totalHires": 14, "totalFeedback": 4.9,
                    "location": {"country": "United States"}}}
    j.update(kw)
    return j


def ssr(*jobs, url="https://www.upwork.com/nx/find-work/most-recent"):
    return json.dumps({"url": url, "jobs": list(jobs)})


class ScanPipelineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scan-test-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.tmp / "logs"),
            mock.patch.object(config, "LOCK_DIR", self.tmp / "locks"),
            # these are the CHROME-path tests: pin the provider regardless of
            # the live switch (flipped to "mcp" on 2026-08-26; the Chrome path
            # stays in scan_pipeline as the rollback and must keep passing)
            mock.patch.object(config, "JOB_FEED_PROVIDER", "chrome"),
            mock.patch.object(sp, "ensure_scan_tab",
                              return_value=(111, 3)),
            mock.patch.object(sp, "navigate_tab"),
            # pin the gate SHADOW regardless of live config ('upwork' went live
            # at the 2026-07-06 scanner cutover); live-path tests re-patch False.
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

    def run_scan(self, chrome_payloads, score_map=None, ssr=None):
        """chrome_payloads: one DOM payload per feed (the fallback path).
        ssr: optional SSR __NUXT__ payloads per feed (the PRIMARY path since
        2026-07-13). With no ssr, the payload comes back empty and the scan
        falls back to the DOM — which is how the pre-existing tests still run."""
        score_map = score_map or {}

        def fake_score(row, task_name, db_path=None):
            return verdict(score_map.get(row["id"], 21))

        ssr_iter = iter(ssr or [])
        dom_iter = iter(chrome_payloads)

        def fake_eval(js, tab_ref=None, _sleep=None):
            if "__NUXT__" in js:                      # PAYLOAD_JS
                return next(ssr_iter, json.dumps(
                    {"url": "https://www.upwork.com/nx/find-work/my-feed",
                     "jobs": []}))
            return next(dom_iter)                     # EXTRACT_JS (fallback)

        from core import runner
        with mock.patch.object(sp.feed_bridge, "chrome_eval",
                               side_effect=fake_eval), \
             mock.patch.object(scoring, "score_job",
                               side_effect=fake_score), \
             mock.patch.object(scoring, "draft_proposal",
                               return_value="Drafted proposal text."):
            return runner.run_task(
                sp.TASK_NAME, lambda ctx: sp.scan(ctx, _sleep=lambda s: None),
                module="upwork", db_path=self.db_path)

    def anomalies(self, kind=None):
        q = "SELECT * FROM anomalies"
        args = ()
        if kind:
            q, args = q + " WHERE kind=?", (kind,)
        return self.rows(q, args)


JID_A = "2064908810394200829"
JID_B = "2065069560236358341"


class TestScan(ScanPipelineCase):
    def test_full_run_claims_scores_and_records_intents(self):
        result = self.run_scan(
            [payload(tile(JID_A)), payload(), payload(tile(JID_B))],
            score_map={JID_A: 25, JID_B: 21})
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metrics["claimed"], 2)
        self.assertEqual(result.metrics["scored"], 2)
        # Since 2026-09-23 the hot bar sits on the card floor, so a scan drafts
        # for everything it cards: JID_B at 21 used to be a Standard card with
        # no draft, and there is no Standard list any more. "Carded but not hot"
        # is still a reachable code path and is still tested — in
        # TestScanLive.test_live_non_hot_pushes_without_email, which patches the
        # ladder back apart to reach it.
        self.assertEqual(result.metrics["drafts"], 2)
        self.assertEqual(result.metrics["clickup_intents"], 2)
        self.assertEqual(result.metrics["hot"], 2)

        rows = {r["id"]: r for r in self.rows("SELECT * FROM scored_jobs")}
        self.assertEqual(rows[JID_A]["status"], "Drafted")
        self.assertEqual(rows[JID_A]["score"], 25)
        self.assertEqual(rows[JID_A]["loom_flag"], 1)
        self.assertEqual(rows[JID_B]["status"], "Drafted")
        self.assertEqual(rows[JID_B]["loom_flag"], 0)       # hot, not loom
        self.assertIsNotNone(rows[JID_B]["draft_proposal"])

        ledger = self.rows("SELECT * FROM shadow_ledger ORDER BY id")
        clickup = [r for r in ledger if r["target"] == "clickup"]
        email = [r for r in ledger if r["target"] == "email"]
        self.assertEqual(len(clickup), 2)
        self.assertEqual(clickup[0]["diff_status"], "pending")
        hot_payload = json.loads(clickup[0]["payload_json"])
        self.assertEqual(hot_payload["list"], "hot")
        self.assertEqual(len(email), 1)
        self.assertIn(JID_A, json.loads(email[0]["payload_json"])["hot_ids"])

        run = self.rows("SELECT * FROM runs")[0]
        self.assertEqual(run["shadow"], 1)   # upwork is shadowed

    def test_second_run_dedups_and_skips_empty(self):
        self.run_scan([payload(tile(JID_A)), payload(), payload()],
                      score_map={JID_A: 21})
        result = self.run_scan([payload(tile(JID_A)), payload(), payload()])
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metrics["claimed"], 0)
        self.assertEqual(result.metrics["scored"], 0)       # nothing New
        self.assertEqual(len(self.rows(
            "SELECT * FROM scored_jobs")), 1)               # no dup rows
        self.assertEqual(len(self.rows(
            "SELECT * FROM shadow_ledger WHERE target='clickup'")), 1)

    def test_hard_rule_job_skipped_without_model_call(self):
        calls = []
        with mock.patch.object(scoring, "score_job",
                               side_effect=lambda *a, **k:
                               calls.append(1) or verdict()):
            # via the SSR payload (the primary path) — the n8n title must be
            # hard-skipped before any model call, same as on the DOM path
            result = self.run_scan(
                [payload(), payload(), payload()],
                ssr=[ssr(ssr_job(JID_A, title="n8n migration expert")),
                     ssr(), ssr()])
        self.assertEqual(calls, [])          # model never invoked
        row = self.rows("SELECT * FROM scored_jobs")[0]
        self.assertEqual(row["status"], "Skipped")
        self.assertEqual(row["hard_rule_skip"], "n8n_exclusion")
        self.assertEqual(result.metrics["skipped"], 1)
        self.assertEqual(result.metrics["clickup_intents"], 0)

    def test_crashed_run_leftover_new_rows_get_scored(self):
        # seed a stray New row (prior crash), then run with empty feeds
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO scored_jobs (id, title, job_url, feed_source,"
                " status, fetched_at, contract_type, hourly_max)"
                " VALUES (?,?,?,?,?,datetime('now'),'Hourly',40)",
                (JID_B, "Leftover", f"https://www.upwork.com/jobs/X_~02{JID_B}/",
                 "my_feed", "New"))
        # below the card floor on purpose: this test is about the leftover row
        # being PICKED UP and scored, and a score that also cards it would hide
        # that behind a push. Relative to config so the next re-cut cannot
        # silently turn it into a drafted row again.
        result = self.run_scan([payload(), payload(), payload()],
                               score_map={JID_B: config.SCORE_FLOOR - 1})
        self.assertEqual(result.metrics["scored"], 1)
        self.assertEqual(self.rows("SELECT status FROM scored_jobs")[0]
                         ["status"], "Scored")

    def test_logged_out_redirect_fails_loud_ingests_nothing(self):
        # 2026-07-06 incident: logged-out/CF-challenged session bounces feed URLs
        # to the PUBLIC search page. Tiles there are NOT our feeds — never ingest;
        # all-feeds-redirected must FAIL (-> critical anomaly -> interrupt email).
        search = "https://www.upwork.com/nx/search/jobs/#ew-scan"
        with self.assertRaises(RuntimeError):          # runner re-raises after routing
            self.run_scan([payload(tile(JID_A), url=search),
                           payload(tile(JID_B), url=search),
                           payload(url=search)])
        self.assertEqual(self.rows("SELECT * FROM scored_jobs"), [])   # nothing ingested
        run = self.rows("SELECT status, error FROM runs ORDER BY id DESC")[0]
        self.assertEqual(run["status"], "failed")
        self.assertIn("LOGGED OUT", run["error"])
        self.assertIn("Log into upwork.com", run["error"])

    def test_partial_redirect_processes_good_feeds_only(self):
        # one feed healthy, two bounced: ingest the healthy one, log the rest,
        # do NOT fail the run.
        search = "https://www.upwork.com/nx/search/jobs/#ew-scan"
        result = self.run_scan([payload(tile(JID_A)),
                                payload(tile(JID_B), url=search),
                                payload(url=search)],
                               score_map={JID_A: 21})
        self.assertEqual(result.status, "completed")
        rows = self.rows("SELECT id FROM scored_jobs")
        self.assertEqual([r["id"] for r in rows], [JID_A])   # only the feed tile
        self.assertEqual(
            result.metrics["feeds"]["best_matches"].get("redirected_to"), search)

    def test_all_empty_feeds_skip_empty(self):
        result = self.run_scan([payload(), payload(), payload()])
        self.assertEqual(result.status, "skipped_empty")


class TestSsrPayload(ScanPipelineCase):
    """The 2026-07-13 hydration incident: Upwork lazily hydrates job cards, and
    Chrome never runs the render lifecycle in our hidden scan tab, so DOM
    scraping saw ~2 of ~50 jobs. The SSR __NUXT__ payload carries the FULL feed
    and is immune to that — it is now the primary source."""

    def test_payload_is_primary_and_carries_full_feed(self):
        jobs = [ssr_job(str(2076575652992019450 + i * 7919)) for i in range(10)]
        result = self.run_scan([payload(), payload(), payload()],
                               ssr=[ssr(*jobs), ssr(), ssr()])
        self.assertEqual(result.status, "completed")
        f = result.metrics["feeds"]
        self.assertEqual(f["most_recent"]["extracted"], 10)   # not 2
        self.assertEqual(f["most_recent"]["source"], "payload")
        self.assertEqual(f["most_recent"]["new"], 10)
        rows = self.rows("SELECT * FROM scored_jobs")
        self.assertEqual(len(rows), 10)

    def test_payload_fields_beat_regex(self):
        result = self.run_scan([payload(), payload(), payload()],
                               ssr=[ssr(ssr_job(JID_A)), ssr(), ssr()])
        self.assertEqual(result.status, "completed")
        r = self.rows("SELECT * FROM scored_jobs WHERE id=?", (JID_A,))[0]
        self.assertEqual(r["contract_type"], "Hourly")
        self.assertEqual(r["hourly_min"], 35)
        self.assertEqual(r["hourly_max"], 50)
        self.assertEqual(r["client_total_spent"], 12000.0)
        self.assertEqual(r["client_country"], "United States")
        self.assertEqual(r["client_payment_verified"], 1)
        self.assertEqual(r["total_applicants"], 5)            # "5 to 10" tier
        self.assertIn("~02" + JID_A, r["job_url"])            # href from ciphertext
        # skills come from `attrs` — the double-weighted skill_fit signal must
        # never be blank again (2026-07-13 regression)
        self.assertEqual(json.loads(r["skills_json"]), ["Make.com", "Airtable"])

    def test_dom_fallback_when_payload_missing(self):
        # payload gone (shape change) -> DOM path still works, and says so
        result = self.run_scan([payload(tile(JID_A)), payload(), payload()],
                               ssr=[json.dumps({"url": "https://www.upwork.com/"
                                                       "nx/find-work/most-recent",
                                                "jobs": [], "err": "no __NUXT__"}),
                                    ssr(), ssr()])
        self.assertEqual(result.status, "completed")
        f = result.metrics["feeds"]
        self.assertEqual(f["most_recent"]["extracted"], 1)
        self.assertEqual(f["most_recent"]["source"], "dom-fallback")

    def test_degraded_feed_raises_critical_anomaly(self):
        # 2 jobs/feed is the exact silent-starvation signature — never quiet again
        result = self.run_scan([payload(), payload(), payload()],
                               ssr=[ssr(ssr_job(JID_A), ssr_job(JID_B)),
                                    ssr(), ssr()])
        self.assertEqual(result.status, "completed")          # not fatal
        a = self.anomalies("scan_feed_degraded")
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["severity"], "critical")        # -> interrupt email
        detail = json.loads(a[0]["detail_json"])
        self.assertIn("most_recent=2", detail["feeds"])
        self.assertEqual(detail["min_expected"], sp.MIN_HEALTHY_TILES)

    def test_healthy_feed_raises_no_anomaly(self):
        jobs = [ssr_job(str(2076575652992019450 + i * 7919)) for i in range(10)]
        self.run_scan([payload(), payload(), payload()],
                      ssr=[ssr(*jobs), ssr(*jobs), ssr(*jobs)])
        self.assertEqual(self.anomalies("scan_feed_degraded"), [])


class TestScanLive(ScanPipelineCase):
    """The 2026-07-06 scanner-cutover live path: ClickUp POST + attach marks
    scored_jobs (NOT the v2 `jobs` table), hot-lead digest EMAILS, and a send
    failure never fails the scan. ClickUp HTTP + SMTP are mocked."""

    def run_live(self, send_mock, score=None):
        # the hot bar moved 16 -> 20 on 2026-08-25; a live-path fixture that
        # pinned 18 as "hot" would have gone quiet without saying why
        score = config.HOT_LEAD_THRESHOLD if score is None else score
        self.req_calls = []

        def fake_req(method, path, tok, body=None):
            self.req_calls.append((method, path, body))
            return {"id": "t123", "url": "https://app.clickup.com/t/t123"}

        with mock.patch.dict(config.SHADOW_MODE, {"upwork": False}), \
             mock.patch.object(sp.clickup_push, "token", return_value="tok"), \
             mock.patch.object(sp.clickup_push, "req", side_effect=fake_req), \
             mock.patch.object(sp.notify, "send_gmail", send_mock):
            return self.run_scan([payload(tile(JID_A)), payload(), payload()],
                                 score_map={JID_A: score})

    def test_live_pushes_card_marks_row_and_emails_digest(self):
        send = mock.MagicMock()
        result = self.run_live(send)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metrics["digest"], "sent")
        self.assertEqual(result.metrics["clickup_pushed"], 1)   # LIVE counter
        self.assertEqual(result.metrics["clickup_intents"], 0)  # shadow-only counter
        # card POSTed once to a list endpoint
        posts = [c for c in self.req_calls if c[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertIn("/task", posts[0][1])
        # attach hit scored_jobs (the v2 `jobs` UPDATE would have crashed here)
        row = self.rows("SELECT * FROM scored_jobs WHERE id=?", (JID_A,))[0]
        self.assertEqual(row["status"], "ClickUp Created")
        self.assertEqual(row["clickup_task_id"], "t123")
        # digest emailed once, carrying title/score/card link
        send.assert_called_once()
        subject, html = send.call_args[0]
        self.assertIn("1 hot", subject)
        self.assertIn("Make.com Automation Build", html)
        self.assertIn(f"score {config.HOT_LEAD_THRESHOLD}", html)
        self.assertIn("app.clickup.com/t/t123", html)
        # live run: nothing shadowed
        self.assertEqual(self.rows("SELECT * FROM shadow_ledger"), [])
        self.assertEqual(self.rows("SELECT * FROM runs")[0]["shadow"], 0)

    def test_live_digest_send_failure_never_fails_scan(self):
        send = mock.MagicMock(side_effect=RuntimeError("smtp down"))
        result = self.run_live(send)
        self.assertEqual(result.status, "completed")      # scan survives
        self.assertEqual(result.metrics["digest"], "send_failed")
        row = self.rows("SELECT * FROM scored_jobs WHERE id=?", (JID_A,))[0]
        self.assertEqual(row["status"], "ClickUp Created")  # card still pushed

    def test_live_non_hot_pushes_without_email(self):
        """A card below the hot bar gets pushed and sends no digest.

        At the live cuts (2026-09-23) the hot bar sits ON the card floor, so no
        score can land in that gap — the branch is unreachable from live config
        but is NOT dead code: the collapse is a setting, and the day the ladder
        comes back apart this path runs again. Patched apart here so it stays
        tested either way."""
        send = mock.MagicMock()
        with mock.patch.object(config, "HOT_LEAD_THRESHOLD", 23):
            result = self.run_live(send, score=21)         # carded, not hot
        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.metrics["digest"])
        send.assert_not_called()
        row = self.rows("SELECT * FROM scored_jobs WHERE id=?", (JID_A,))[0]
        self.assertEqual(row["status"], "ClickUp Created")


class TestPushRetry(ScanPipelineCase):
    """Finding 13 (Upwork chat, 2026-08-25): a scan that died between scoring
    and its push loop (08-23 23:09, ClaudeBudgetExceeded at the per-run cap
    after 20 drafts) left its rows 'Drafted' with no card, and no later run
    ever looked at them. The next run now pushes what an earlier run left
    uncarded, inside CLICKUP_PUSH_RETRY_DAYS; a failed create is loud, counted,
    and retried next run instead of failing the scan."""

    LEFT = "2065100000000000011"

    def seed_uncarded(self, days_ago: float, score=None, status="Drafted"):
        score = config.HOT_LEAD_THRESHOLD if score is None else score
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO scored_jobs (id, title, score, status, loom_flag,"
                " fetched_at, first_scored_at, job_url, draft_proposal,"
                " score_breakdown_json, skills_json)"
                " VALUES (?,?,?,?,0,?,datetime('now', ?),?,?,'{}','[]')",
                (self.LEFT, "Left behind by a dead run", score, status,
                 "2026-08-23T17:00:00+00:00", f"-{days_ago * 24:.0f} hours",
                 f"https://www.upwork.com/jobs/x_~02{self.LEFT}/",
                 "Drafted proposal text." if status == "Drafted" else None))

    def test_uncarded_leftover_is_carded_by_the_next_run(self):
        self.seed_uncarded(days_ago=1)
        result = self.run_scan([payload(tile(JID_A)), payload(), payload()],
                               score_map={JID_A: 21})
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metrics["clickup_retried"], 1)
        self.assertEqual(result.metrics["clickup_intents"], 2)   # new + leftover
        keys = {r["entity_key"] for r in self.rows(
            "SELECT entity_key FROM shadow_ledger WHERE target='clickup'")}
        self.assertEqual(keys, {JID_A, self.LEFT})

    def test_leftover_outside_the_window_stays_where_it_is(self):
        self.seed_uncarded(days_ago=config.CLICKUP_PUSH_RETRY_DAYS + 3)
        result = self.run_scan([payload(tile(JID_A)), payload(), payload()],
                               score_map={JID_A: 21})
        self.assertEqual(result.metrics["clickup_retried"], 0)
        self.assertEqual(result.metrics["clickup_intents"], 1)

    def test_leftover_below_the_floor_is_not_a_card(self):
        self.seed_uncarded(days_ago=1, score=config.SCORE_FLOOR - 1,
                           status="Scored")
        result = self.run_scan([payload(tile(JID_A)), payload(), payload()],
                               score_map={JID_A: 21})
        self.assertEqual(result.metrics["clickup_retried"], 0)

    def test_live_retry_cards_the_row_and_carries_it_in_the_digest(self):
        # seeded ABOVE the patched bar below (25 > 23), not at the live
        # HOT_LEAD_THRESHOLD: the leftover has to stay hot inside the patch or
        # the digest it is supposed to carry never gets built.
        self.seed_uncarded(days_ago=1, score=25)            # hot, drafted, no card
        send = mock.MagicMock()
        calls = []

        def fake_req(method, path, tok, body=None):
            calls.append((method, path))
            return {"id": "t777", "url": "https://app.clickup.com/t/t777"}

        with mock.patch.dict(config.SHADOW_MODE, {"upwork": False}), \
             mock.patch.object(sp.clickup_push, "token", return_value="tok"), \
             mock.patch.object(sp.clickup_push, "req", side_effect=fake_req), \
             mock.patch.object(sp.notify, "send_gmail", send):
            # the ladder patched apart so THIS run's own row is carded but not
            # hot — the point of the test is that the digest is carried by the
            # RETRIED row alone. At the live collapsed cuts a carded row is
            # always hot and the distinction could not be drawn.
            with mock.patch.object(config, "HOT_LEAD_THRESHOLD", 23):
                result = self.run_scan([payload(tile(JID_A)), payload(), payload()],
                                       score_map={JID_A: 21})
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metrics["clickup_retried"], 1)
        self.assertEqual(result.metrics["hot"], 0)          # this run's own
        self.assertEqual(result.metrics["hot_retried"], 1)
        row = self.rows("SELECT * FROM scored_jobs WHERE id=?", (self.LEFT,))[0]
        self.assertEqual(row["status"], "ClickUp Created")
        self.assertEqual(row["clickup_task_id"], "t777")
        send.assert_called_once()
        subject, html = send.call_args[0]
        self.assertIn("carried over", subject)
        self.assertIn("Left behind by a dead run", html)
        self.assertIn("app.clickup.com/t/t777", html)

    def test_a_failed_create_is_loud_and_never_fails_the_scan(self):
        self.seed_uncarded(days_ago=1)
        send = mock.MagicMock()

        def fake_req(method, path, tok, body=None):
            if method == "POST":
                raise RuntimeError("clickup 502")
            return {"tasks": []}                             # find_existing: none

        with mock.patch.dict(config.SHADOW_MODE, {"upwork": False}), \
             mock.patch.object(sp.clickup_push, "token", return_value="tok"), \
             mock.patch.object(sp.clickup_push, "req", side_effect=fake_req), \
             mock.patch.object(sp.clickup_push.time, "sleep"), \
             mock.patch.object(sp.notify, "send_gmail", send):
            result = self.run_scan([payload(tile(JID_A)), payload(), payload()],
                                   score_map={JID_A: config.HOT_LEAD_THRESHOLD})
        self.assertEqual(result.status, "completed")         # the scan survives
        self.assertEqual(result.metrics["clickup_pushed"], 0)
        self.assertEqual(result.metrics["clickup_push_failed"], 2)
        self.assertEqual(result.metrics["clickup_retried"], 0)
        kinds = [a["kind"] for a in self.anomalies("clickup_push_failed")]
        self.assertEqual(len(kinds), 2)
        # both rows stay pushable — the next run's retry window picks them up
        for jid in (JID_A, self.LEFT):
            row = self.rows("SELECT * FROM scored_jobs WHERE id=?", (jid,))[0]
            self.assertIsNone(row["clickup_task_id"])
            self.assertIn(row["status"], ("Scored", "Drafted"))
        # the digest still goes out for this run's own hot job (no card link)
        send.assert_called_once()


class TestTabScripts(unittest.TestCase):
    def test_find_script_matches_marker(self):
        self.assertIn(sp.MARKER, sp._FIND_TAB)
        self.assertIn("return \"\"", sp._FIND_TAB)

    def test_create_script_restores_focus(self):
        self.assertIn("set prior to active tab index", sp._CREATE_TAB)
        self.assertIn("set active tab index of front window to prior",
                      sp._CREATE_TAB)
        self.assertIn(sp.MARKER, sp._CREATE_TAB)

    def test_navigate_does_not_use_active_tab(self):
        with mock.patch.object(sp, "_osascript") as osa:
            sp.navigate_tab((42, 7), "https://x.example/")
        script = osa.call_args[0][0]
        self.assertIn("tab 7 of window id 42", script)
        self.assertNotIn("active tab", script)


if __name__ == "__main__":
    unittest.main()
