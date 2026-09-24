"""The budgeted shadow scoring pass (scripts/mcp_shadow_score.py, migration 028).

Pins: the two selections (MCP-only survivors; shared jobs with a scraper score),
idempotency (already-scored ids are skipped), chunking into runner runs with
the priority task prefix, that the pass writes ONLY the shadow tables (027's
principle — scored_jobs, shadow_ledger and friends are byte-identical after a
run), that --dry-run makes no model call and no write, that a budget stop ends
the pass loudly, and — by grepping the script — that no live-path name appears
in it. The model is always a fake here; nothing in this file spends.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import claude_gateway as gw
from core import config, db, runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import mcp_shadow_score as mss  # noqa: E402


def _job(jid, title="Make.com build", hourly_max=60.0):
    return {"id": jid, "href": f"https://www.upwork.com/jobs/x_~02{jid}/",
            "title": title, "description": "Automate CRM to sheets",
            "contract_type": "Hourly", "hourly_min": 40.0, "hourly_max": hourly_max,
            "fixed_budget": None, "weekly_budget": None, "total_applicants": 12,
            "experience_level": "Expert", "engagement": "30+ hrs/week",
            "engagement_duration": "3 to 6 months", "skills_json": '["Make.com"]',
            "client_country": "United States", "client_total_spent": 12000.0,
            "client_hires": 9, "client_rating": 4.9, "client_payment_verified": 1,
            "created_dt": "2026-08-24T10:00:00+0000", "feed_source": "mcp:make.com"}


class ShadowScoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shadow-score-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.tmp / "logs"),
            mock.patch.object(config, "LOCK_DIR", self.tmp / "locks"),
        ]
        for p in self._patches:
            p.start()
        with db.connect(self.db_path) as conn:
            # 3 MCP-only survivors, 1 MCP-only hard-rule skip, 2 shared with a
            # scraper score, 1 shared without a scraper score (New, unscored)
            for jid, skip in (("2091000000000000001", None), ("2091000000000000002", None),
                              ("2091000000000000003", None),
                              ("2091000000000000004", "hourly_below_30")):
                self._shadow(conn, jid, skip)
            for jid, score in (("2092000000000000001", 18), ("2092000000000000002", 11),
                               ("2092000000000000003", None)):
                self._shadow(conn, jid, None)
                conn.execute("INSERT INTO scored_jobs (id, title, score, status, fetched_at)"
                             " VALUES (?,?,?,?, '2026-08-24T09:00:00+00:00')",
                             (jid, "scraper copy", score, "Scored" if score else "New"))

    def _shadow(self, conn, jid, skip):
        conn.execute(
            "INSERT INTO mcp_shadow_jobs (id, title, normalized_json, found_by,"
            " hard_rule_skip, in_scored_jobs, got_detail, first_seen_at, last_seen_at)"
            " VALUES (?,?,?,?,?,0,1,'2026-08-24T12:00:00Z','2026-08-24T12:00:00Z')",
            (jid, f"job {jid[-1]}", json.dumps(_job(jid)), "mcp:make.com", skip))

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def snapshot(self, table):
        with db.connect(self.db_path) as conn:
            return [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]

    def rows(self, sql, args=()):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, args)]


def fake_scorer(score=17):
    calls = []

    def _score(job, task_name, db_path=None):
        calls.append((job["id"], task_name))
        return {"score": score, "gated": False,
                "loom_flag": 1 if score >= config.LOOM_FLAG_THRESHOLD else 0,
                "breakdown": {"total": score, "skill_fit": 4}}
    _score.calls = calls
    return _score


class TestSelection(ShadowScoreCase):
    def test_sets_are_survivors_and_shared_with_a_scraper_score(self):
        with db.connect(self.db_path) as conn:
            only, shared = mss.select_sets(conn, 100, 100)
        self.assertEqual({r["id"] for r in only},
                         {"2091000000000000001", "2091000000000000002",
                          "2091000000000000003"})          # skip row excluded
        self.assertEqual({(r["id"], r["scraper_score"]) for r in shared},
                         {("2092000000000000001", 18), ("2092000000000000002", 11)})
        # the unscored scraper twin is in neither set: no truth to drift against,
        # and it is not MCP-only either
        self.assertNotIn("2092000000000000003",
                         {r["id"] for r in only} | {r["id"] for r in shared})

    def test_limits_apply_per_set(self):
        with db.connect(self.db_path) as conn:
            only, shared = mss.select_sets(conn, 2, 1)
        self.assertEqual((len(only), len(shared)), (2, 1))


class TestPass(ShadowScoreCase):
    def test_writes_only_the_shadow_tables(self):
        before = {t: self.snapshot(t) for t in
                  ("scored_jobs", "shadow_ledger", "proposals", "drafts", "anomalies")}
        out = []
        res = mss.run_pass(self.db_path, _score=fake_scorer(17), out=out.append)
        self.assertFalse(res["dry_run"])
        self.assertEqual(res["calls"], 5)
        after = {t: self.snapshot(t) for t in before}
        self.assertEqual(before, after)                      # untouched, byte for byte
        scores = self.rows("SELECT id, score, scraper_score, model, run_id FROM"
                           " mcp_shadow_scores ORDER BY id")
        self.assertEqual(len(scores), 5)
        self.assertTrue(all(s["model"] == config.MODELS["scoring"] for s in scores))
        self.assertTrue(all(s["run_id"] for s in scores))
        self.assertEqual({s["scraper_score"] for s in scores if s["id"].startswith("2092")},
                         {18, 11})
        self.assertEqual(self.rows("SELECT COUNT(*) c FROM mcp_shadow_jobs"
                                   " WHERE score=17")[0]["c"], 5)
        self.assertEqual(self.rows("SELECT score FROM mcp_shadow_jobs WHERE id="
                                   "'2091000000000000004'")[0]["score"], None)

    def test_rerun_is_idempotent_and_free(self):
        mss.run_pass(self.db_path, _score=fake_scorer(17), out=lambda s: None)
        scorer = fake_scorer(25)
        res = mss.run_pass(self.db_path, _score=scorer, out=lambda s: None)
        self.assertEqual(scorer.calls, [])                    # nothing left to score
        self.assertEqual(res["calls"], 0)
        self.assertEqual(self.rows("SELECT COUNT(*) c FROM mcp_shadow_scores"
                                   " WHERE score=25")[0]["c"], 0)

    def test_chunks_into_priority_named_runs(self):
        with db.connect(self.db_path) as conn:
            for n in range(30):
                self._shadow(conn, f"2093{n:015d}", None)
        scorer = fake_scorer(9)
        with mock.patch.object(mss, "CHUNK", 25):
            res = mss.run_pass(self.db_path, _score=scorer, out=lambda s: None)
        self.assertEqual(len(res["runs"]), 2)                 # 35 rows -> 25 + 10
        names = [r["task_name"] for r in self.rows(
            "SELECT task_name FROM runs ORDER BY id")]
        self.assertEqual(names, ["upwork_shadow_score_1", "upwork_shadow_score_2"])
        self.assertTrue(all(n.startswith(config.BUDGET_PRIORITY_PREFIXES[0])
                            for n in names))
        self.assertEqual({t for _, t in scorer.calls},
                         {"upwork_shadow_score_1", "upwork_shadow_score_2"})
        self.assertEqual(res["runs"][0]["metrics"]["scored"], 25)
        self.assertEqual(res["runs"][1]["metrics"]["scored"], 10)

    def test_dry_run_calls_nothing_and_writes_nothing(self):
        scorer = fake_scorer()
        out = []
        res = mss.run_pass(self.db_path, dry_run=True, _score=scorer, out=out.append)
        self.assertTrue(res["dry_run"])
        self.assertEqual((res["mcp_only"], res["shared"], res["calls"]), (3, 2, 5))
        self.assertEqual(scorer.calls, [])
        self.assertEqual(self.snapshot("mcp_shadow_scores"), [])
        self.assertEqual(self.snapshot("runs"), [])
        self.assertTrue(any("projected" in line for line in out))

    def test_budget_stop_ends_the_pass_loudly(self):
        def _score(job, task_name, db_path=None):
            raise gw.ClaudeBudgetExceeded("per-run cap: $0.50 >= $0.50", "per_run")
        out = []
        res = mss.run_pass(self.db_path, _score=_score, out=out.append)
        self.assertEqual(res["runs"][0]["status"], "failed")
        self.assertEqual(len(res["runs"]), 1)                 # stopped after chunk 1
        self.assertEqual(self.snapshot("mcp_shadow_scores"), [])
        self.assertTrue(any("stopping" in line for line in out))
        self.assertEqual(self.rows("SELECT COUNT(*) c FROM anomalies WHERE"
                                   " kind='task_failure'")[0]["c"], 1)

    def test_a_gateway_error_skips_the_row_and_files_a_warn(self):
        bad = {"2091000000000000002"}

        def _score(job, task_name, db_path=None):
            if job["id"] in bad:
                raise gw.GatewayError("schema mismatch")
            return {"score": 14, "gated": False, "loom_flag": 0, "breakdown": {}}
        res = mss.run_pass(self.db_path, _score=_score, out=lambda s: None)
        self.assertEqual(res["runs"][0]["metrics"], {"scored": 4, "failed": 1, "rows": 5})
        self.assertEqual(self.rows("SELECT COUNT(*) c FROM mcp_shadow_scores")[0]["c"], 4)
        self.assertEqual(self.rows("SELECT kind, severity FROM anomalies")[0],
                         {"kind": "shadow_score_failed", "severity": "warn"})


class TestSummary(ShadowScoreCase):
    def test_two_answers_from_the_table(self):
        with db.connect(self.db_path) as conn:
            hot, loom = config.HOT_LEAD_THRESHOLD, config.LOOM_FLAG_THRESHOLD
            # seeded RELATIVE to the live cuts: one loom, one hot-not-loom, one
            # under the card floor; the shared trio drifts -2 with hot agreement
            # on two. `floor - 1` rather than `floor + 1` since 2026-09-23: the
            # hot bar now sits ON the floor, so floor+1 is hot and there is no
            # band between them to seed.
            for jid, score, scr in (("a1", loom + 1, None), ("a2", hot, None),
                                    ("a3", config.SCORE_FLOOR - 1, None),
                                    ("b1", hot, hot + 2), ("b2", 12, 11),
                                    ("b3", loom - 2, loom)):
                conn.execute("INSERT INTO mcp_shadow_scores (id, score, scraper_score,"
                             " model) VALUES (?,?,?,'m')", (jid, score, scr))
        text = mss.summary(self.db_path)
        self.assertIn("MCP-only scored: 3", text)
        self.assertIn(f"hot (>={config.HOT_LEAD_THRESHOLD}): 2", text)
        self.assertIn(f"loom (>={config.LOOM_FLAG_THRESHOLD}): 1", text)
        self.assertIn("drift on 3 shared jobs: median (mcp - scraper) -2.0", text)
        self.assertIn("within +-2: 3/3", text)
        self.assertIn("hot by scraper 2, by mcp 2, by both 2", text)

    def test_empty_table_says_so(self):
        text = mss.summary(self.db_path)
        self.assertIn("MCP-only scored: 0", text)
        self.assertIn("no shared jobs scored yet", text)


class TestNoLivePath(unittest.TestCase):
    def test_script_never_names_a_live_write_path(self):
        src = (ROOT / "scripts/mcp_shadow_score.py").read_text()
        for forbidden in ("UPDATE scored_jobs", "INSERT INTO scored_jobs",
                          "clickup", "send_gmail", "draft_proposal",
                          "record_shadow_write", "smtplib"):
            self.assertNotIn(forbidden, src, forbidden)
        self.assertIn("INSERT OR REPLACE INTO mcp_shadow_scores", src)

    def test_migration_028_is_additive(self):
        sql = (ROOT / "core/migrations/028_mcp_shadow_scores.sql").read_text()
        self.assertIn("CREATE TABLE IF NOT EXISTS mcp_shadow_scores", sql)
        for forbidden in ("DROP ", "DELETE ", "ALTER TABLE scored_jobs"):
            self.assertNotIn(forbidden, sql)


if __name__ == "__main__":
    unittest.main()
