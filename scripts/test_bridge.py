#!/usr/bin/env python3
"""Unit tests for feed_bridge.py / run_log.py / clickup_push.py.

Run: python3 scripts/test_bridge.py
No network, no Chrome, no production DB — claim/ledger tests run against a
temp SQLite with the real v2 jobs/runs schemas.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name):
    loader = importlib.machinery.SourceFileLoader(name, str(HERE / f"{name}.py"))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


import sys
sys.path.insert(0, str(HERE))

ig = load("job_id_guard")
fb = load("feed_bridge")
rl = load("run_log")
cp = load("clickup_push")

JOBS_DDL = """
CREATE TABLE jobs (
  id TEXT PRIMARY KEY, title TEXT, description TEXT, created_dt TEXT,
  fetched_at TEXT, feed_source TEXT, contract_type TEXT, hourly_min REAL,
  hourly_max REAL, fixed_budget REAL, weekly_budget REAL,
  total_applicants INTEGER, experience_level TEXT, engagement TEXT,
  engagement_duration TEXT, category TEXT, subcategory TEXT, skills_json TEXT,
  client_company TEXT, client_country TEXT, client_total_spent REAL,
  client_hires INTEGER, client_rating REAL, client_payment_verified INTEGER,
  applied INTEGER DEFAULT 0, score INTEGER, score_breakdown_json TEXT,
  hard_rule_skip TEXT, draft_proposal TEXT, draft_word_count INTEGER,
  clickup_task_id TEXT, clickup_task_url TEXT, airtable_record_id TEXT,
  loom_flag INTEGER DEFAULT 0, status TEXT, first_scored_at TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  score_override INTEGER, manual_note TEXT, job_url TEXT
);
"""
RUNS_DDL = """
CREATE TABLE runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_name TEXT NOT NULL,
  started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL,
  metrics_json TEXT, anomalies_json TEXT, duration_ms INTEGER
);
"""


def raw_job(jid="2064908810394200829", title="Make.com Automation Build",
            href=None, **kw):
    job = {
        "id": jid,
        "href": href if href is not None
        else f"https://www.upwork.com/jobs/Make-com-Automation_~02{jid}/",
        "title": title,
        "description": "Build automations.",
        "created_dt": "2026-06-11T01:00:00Z",
    }
    job.update(kw)
    return job


class TmpDbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bridge-test-"))
        self.db = self.tmp / "test.db"
        conn = sqlite3.connect(self.db)
        conn.executescript(JOBS_DDL + RUNS_DDL)
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self, sql, args=()):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()


# ── job_id_guard: fabricated-id pattern detection ────────────────────────────
class TestIdGuard(unittest.TestCase):
    REAL = ("2064908810394200829", "2065069560236358341",
            "2057659725623266591", "2064484440376044477",  # has 444 — real
            "2064797366430736694", "2064750021372001119",
            "2064660555551234560",  # single 5-run: ~4 expected REAL per 3k
            "2062950266666104394")  # live borderline held for verification

    FABRICATED = (
        "2064750000000000001",     # zeros padding (June-10 signature)
        "2064823456789012345",     # keyboard walk (June-11 escalation)
        "2064001234567890666",     # walk + padded tail
        "2064777777777944411",     # same-digit run of 9
        "2060684444444444006",     # the 206068X series (10-run)
    )

    def test_real_ids_pass(self):
        for jid in self.REAL:
            self.assertFalse(ig.looks_fabricated(jid), jid)

    def test_fabricated_patterns_caught(self):
        for jid in self.FABRICATED:
            self.assertTrue(ig.looks_fabricated(jid), jid)

    def test_bridge_validation_rejects_fabricated_ids(self):
        job = raw_job(jid="2064823456789012345",
                      href="https://www.upwork.com/jobs/X_"
                           "~022064823456789012345/")
        ok, reason = fb.validate_job(job)
        self.assertFalse(ok)
        self.assertIn("fabricated", reason)


# ── feed_bridge: Iron-Law validation ─────────────────────────────────────────
class TestValidation(unittest.TestCase):
    def test_valid_job_passes(self):
        ok, reason = fb.validate_job(raw_job())
        self.assertTrue(ok, reason)

    def test_non_numeric_id_rejected(self):
        ok, reason = fb.validate_job(raw_job(jid="~022064908810394200829"))
        self.assertFalse(ok)
        self.assertIn("id", reason)

    def test_id_length_bounds(self):
        self.assertFalse(fb.validate_job(raw_job(jid="12345"))[0])
        self.assertFalse(fb.validate_job(raw_job(jid="9" * 30))[0])

    def test_href_must_contain_ciphertext_of_id(self):
        # href/id mismatch is the corruption signature — hard reject
        job = raw_job(href="https://www.upwork.com/jobs/Other_~029999999999999999999/")
        ok, reason = fb.validate_job(job)
        self.assertFalse(ok)
        self.assertIn("href", reason)

    def test_missing_href_rejected(self):
        ok, reason = fb.validate_job(raw_job(href=""))
        self.assertFalse(ok)

    def test_foreign_host_rejected(self):
        jid = "2064908810394200829"
        ok, _ = fb.validate_job(raw_job(
            href=f"https://evil.example.com/jobs/x_~02{jid}/"))
        self.assertFalse(ok)

    def test_empty_title_rejected(self):
        ok, reason = fb.validate_job(raw_job(title="  "))
        self.assertFalse(ok)

    def test_sequential_run_guard_trips(self):
        jobs = [raw_job(jid=f"206490881039420082{i}",
                        href=f"https://www.upwork.com/jobs/X_~02206490881039420082{i}/")
                for i in range(1, 5)]  # ...101..104 consecutive — fabrication
        with self.assertRaises(fb.FabricationSuspected):
            fb.batch_sanity([j["id"] for j in jobs])

    def test_nonsequential_batch_passes(self):
        ids = ["2064908810394200829", "2065069560236358341",
               "2057659725623266591"]
        fb.batch_sanity(ids)  # must not raise

    def test_intra_batch_dups_collapsed(self):
        jobs = [raw_job(), raw_job(), raw_job(jid="2065069560236358341",
                href="https://www.upwork.com/jobs/Y_~022065069560236358341/")]
        out = fb.dedupe_batch(jobs)
        self.assertEqual(len(out), 2)


# ── feed_bridge: tile parsing (real my-feed sample, 2026-06-11) ──────────────
REAL_TILE_TEXT = """Posted 10 hours ago
•
Proposals: 20 to 50
Data Scraping assistant
Job feedback Data Scraping assistant
Save job Data Scraping assistant
Fixed-price - Intermediate - Est. Budget: $3,000
You will be charged with scraping data, including images, text, video, and modifying the data in certain ways from the internet. If you have experience scraping certain types of data, please apply. English speaking is heavily required.
Skills
Scheduling
Data Entry
Email Management
Graphic Design
Lead Generation
Social Media Management
Verified

Payment verified

Rating is 5.0 out of 5.
 $20K+ spent
  USA"""

FIXED_NOW = "2026-06-11T12:00:00+00:00"


class TestTileParsing(unittest.TestCase):
    def test_posted_ago_variants(self):
        from datetime import datetime
        now = datetime.fromisoformat(FIXED_NOW)
        cases = [
            ("Posted 10 hours ago", "2026-06-11T02:00:00+00:00"),
            ("Posted 22 minutes ago", "2026-06-11T11:38:00+00:00"),
            ("Posted 3 days ago", "2026-06-08T12:00:00+00:00"),
            ("Posted 1 week ago", "2026-06-04T12:00:00+00:00"),
            ("Posted yesterday", "2026-06-10T12:00:00+00:00"),
        ]
        for text, expected in cases:
            self.assertEqual(fb.parse_posted_ago(text, now=now), expected, text)
        self.assertIsNone(fb.parse_posted_ago("no timestamp here", now=now))

    def test_budget_line_fixed(self):
        out = fb.parse_budget_line(
            "Fixed-price - Intermediate - Est. Budget: $3,000")
        self.assertEqual(out["contract_type"], "Fixed")
        self.assertEqual(out["fixed_budget"], 3000.0)
        self.assertEqual(out["experience_level"], "Intermediate")
        self.assertIsNone(out["hourly_min"])

    def test_budget_line_hourly(self):
        out = fb.parse_budget_line(
            "Hourly: $25.00 - $50.00 - Expert - Est. Time: More than 6"
            " months, 30+ hrs/week")
        self.assertEqual(out["contract_type"], "Hourly")
        self.assertEqual(out["hourly_min"], 25.0)
        self.assertEqual(out["hourly_max"], 50.0)
        self.assertEqual(out["experience_level"], "Expert")
        self.assertEqual(out["engagement"], "30+ hrs/week")

    def test_parse_tile_full_sample(self):
        from datetime import datetime
        job = fb.parse_tile(
            {"href": "https://www.upwork.com/jobs/Data-Scraping-assistant_"
                     "~022064908810394200829/?referrer_url_path=find_work_home",
             "title": "Data Scraping assistant",
             "text": REAL_TILE_TEXT},
            now=datetime.fromisoformat(FIXED_NOW))
        self.assertEqual(job["id"], "2064908810394200829")
        self.assertEqual(job["title"], "Data Scraping assistant")
        self.assertEqual(job["created_dt"], "2026-06-11T02:00:00+00:00")
        self.assertEqual(job["contract_type"], "Fixed")
        self.assertEqual(job["fixed_budget"], 3000.0)
        self.assertEqual(job["total_applicants"], 20)   # tier lower bound
        self.assertIn("scraping data", job["description"])
        self.assertIn("Data Entry", job["skills"])
        self.assertNotIn("Verified", job["skills"])     # trust line ≠ skill
        self.assertEqual(job["client_payment_verified"], 1)
        self.assertEqual(job["client_rating"], 5.0)
        self.assertEqual(job["client_total_spent"], 20000.0)
        self.assertEqual(job["client_country"], "USA")
        ok, reason = fb.validate_job(job)
        self.assertTrue(ok, reason)

    def test_parse_tile_minimal_card_still_validates(self):
        from datetime import datetime
        job = fb.parse_tile(
            {"href": "https://www.upwork.com/jobs/X_~022064908810394200829/",
             "title": "X", "text": "Posted 5 minutes ago"},
            now=datetime.fromisoformat(FIXED_NOW))
        ok, reason = fb.validate_job(job)
        self.assertTrue(ok, reason)
        self.assertIsNone(job["fixed_budget"])

    def test_spent_parsing_units(self):
        self.assertEqual(fb.parse_spent("$20K+ spent"), 20000.0)
        self.assertEqual(fb.parse_spent("$1M+ spent"), 1000000.0)
        self.assertEqual(fb.parse_spent("$600 spent"), 600.0)
        self.assertIsNone(fb.parse_spent("no spend shown"))


# ── feed_bridge: claim path ──────────────────────────────────────────────────
class TestClaim(TmpDbCase):
    def test_claims_new_jobs_with_status_new(self):
        summary = fb.process(feed="my_feed", jobs=[raw_job()], db_path=self.db,
                             dry_run=False)
        self.assertEqual(summary["new"], 1)
        self.assertEqual(summary["known"], 0)
        row = self.rows("SELECT * FROM jobs")[0]
        self.assertEqual(row["status"], "New")
        self.assertEqual(row["feed_source"], "my_feed")
        self.assertEqual(row["id"], "2064908810394200829")
        self.assertIn("~022064908810394200829", row["job_url"])
        self.assertIsNotNone(row["created_dt"])
        self.assertIsNotNone(row["fetched_at"])

    def test_second_pass_is_known_and_free(self):
        fb.process(feed="my_feed", jobs=[raw_job()], db_path=self.db,
                   dry_run=False)
        summary = fb.process(feed="my_feed", jobs=[raw_job()],
                             db_path=self.db, dry_run=False)
        self.assertEqual(summary["new"], 0)
        self.assertEqual(summary["known"], 1)
        self.assertEqual(len(self.rows("SELECT * FROM jobs")), 1)

    def test_dry_run_claims_nothing(self):
        summary = fb.process(feed="my_feed", jobs=[raw_job()], db_path=self.db,
                             dry_run=True)
        self.assertEqual(summary["new"], 1)
        self.assertTrue(summary["dry_run"])
        self.assertEqual(self.rows("SELECT * FROM jobs"), [])

    def test_invalid_job_not_claimed_but_counted(self):
        summary = fb.process(feed="my_feed",
                             jobs=[raw_job(), raw_job(jid="bogus")],
                             db_path=self.db, dry_run=False)
        self.assertEqual(summary["new"], 1)
        self.assertEqual(summary["invalid"], 1)
        self.assertEqual(len(self.rows("SELECT * FROM jobs")), 1)

    def test_bad_feed_name_rejected(self):
        with self.assertRaises(SystemExit):
            fb.process(feed="My Feed", jobs=[], db_path=self.db, dry_run=True)

    def test_claimed_ids_in_summary(self):
        summary = fb.process(feed="best_matches", jobs=[raw_job()],
                             db_path=self.db, dry_run=False)
        self.assertEqual(summary["claimed_ids"], ["2064908810394200829"])


# ── run_log ──────────────────────────────────────────────────────────────────
class TestRunLog(TmpDbCase):
    def test_start_inserts_running_row(self):
        run_id = rl.start(db_path=self.db)
        row = self.rows("SELECT * FROM runs WHERE id=?", (run_id,))[0]
        self.assertEqual(row["task_name"], "upwork-job-scanner")
        self.assertEqual(row["status"], "running")
        self.assertIsNotNone(row["started_at"])
        self.assertIn("T", row["started_at"])  # ISO 8601, not invented

    def test_finish_completes_row(self):
        run_id = rl.start(db_path=self.db)
        rl.finish(run_id, status="completed",
                  metrics={"new": 3, "known": 40}, db_path=self.db)
        row = self.rows("SELECT * FROM runs WHERE id=?", (run_id,))[0]
        self.assertEqual(row["status"], "completed")
        self.assertIsNotNone(row["completed_at"])
        self.assertGreaterEqual(row["duration_ms"], 0)
        self.assertEqual(json.loads(row["metrics_json"])["new"], 3)

    def test_finish_unknown_run_fails(self):
        with self.assertRaises(SystemExit):
            rl.finish(999, status="completed", metrics={}, db_path=self.db)

    def test_finish_rejects_foreign_task_row(self):
        conn = sqlite3.connect(self.db)
        cur = conn.execute("INSERT INTO runs (task_name, started_at, status)"
                           " VALUES ('sync_airtable', '2026-06-11T00:00:00+00:00',"
                           " 'running')")
        conn.commit()
        other_id = cur.lastrowid
        conn.close()
        with self.assertRaises(SystemExit):
            rl.finish(other_id, status="completed", metrics={},
                      db_path=self.db)

    # run mutex (v4.14.1 blocker: concurrent runs 2240/2241/2242)
    def test_start_refuses_while_fresh_run_running(self):
        rl.start(db_path=self.db)
        with self.assertRaises(SystemExit):
            rl.start(db_path=self.db)

    def test_start_allowed_after_finish(self):
        run_id = rl.start(db_path=self.db)
        rl.finish(run_id, status="completed", metrics={}, db_path=self.db)
        self.assertIsInstance(rl.start(db_path=self.db), int)

    def test_start_allowed_over_stale_running_row(self):
        # a crashed run >60 min old must not deadlock the scanner forever
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO runs (task_name, started_at, status) VALUES"
            " ('upwork-job-scanner', ?, 'running')",
            ((__import__('datetime').datetime.now(
                __import__('datetime').timezone.utc)
              - __import__('datetime').timedelta(minutes=75)).isoformat(),))
        conn.commit()
        conn.close()
        self.assertIsInstance(rl.start(db_path=self.db), int)


# ── clickup_push: routing + payload (no network) ─────────────────────────────
class TestPush(TmpDbCase):
    def seed(self, jid="2064908810394200829", score=18, status="Drafted",
             loom=0, breakdown=None, draft="Hi — proposal text.", **kw):
        breakdown = breakdown or {"skill_fit": 4, "budget": 4, "total": score,
                                  "bonuses": {}}
        cols = dict(
            id=jid, title="Make.com Automation Build",
            job_url=f"https://www.upwork.com/jobs/Make_~02{jid}/",
            feed_source="my_feed", created_dt="2026-06-11T01:00:00Z",
            contract_type="Hourly", hourly_min=30.0, hourly_max=45.0,
            client_company="Acme", client_country="US",
            client_total_spent=12000.0, client_hires=8, client_rating=4.9,
            client_payment_verified=1, score=score,
            score_breakdown_json=json.dumps(breakdown), draft_proposal=draft,
            loom_flag=loom, status=status,
        )
        cols.update(kw)
        conn = sqlite3.connect(self.db)
        conn.execute(
            f"INSERT INTO jobs ({','.join(cols)}) VALUES"
            f" ({','.join('?' * len(cols))})", tuple(cols.values()))
        conn.commit()
        conn.close()
        return cols

    def test_list_routing_bands(self):
        self.assertEqual(cp.list_for_score(24, invite=False), cp.LIST_HOT)
        self.assertEqual(cp.list_for_score(16, invite=False), cp.LIST_HOT)
        self.assertEqual(cp.list_for_score(15, invite=False), cp.LIST_STANDARD)
        self.assertEqual(cp.list_for_score(12, invite=False), cp.LIST_STANDARD)
        self.assertEqual(cp.list_for_score(11, invite=False), cp.LIST_LOW)
        self.assertEqual(cp.list_for_score(8, invite=False), cp.LIST_LOW)
        self.assertIsNone(cp.list_for_score(7, invite=False))
        self.assertEqual(cp.list_for_score(9, invite=True), cp.LIST_INVITES)

    def test_title_format(self):
        row = {"title": "CRM Buildout", "score": 17, "loom_flag": 0}
        self.assertEqual(cp.task_name(row), "CRM Buildout — score 17")
        row = {"title": "Big One", "score": 23, "loom_flag": 1}
        self.assertEqual(cp.task_name(row), "🎥 Big One — score 23")

    def test_body_uses_stored_job_url_and_draft(self):
        cols = self.seed()
        row = self.rows("SELECT * FROM jobs")[0]
        body = cp.task_body(row)
        self.assertIn(cols["job_url"], body)        # FROM DB — never rebuilt
        self.assertIn("Draft Proposal", body)
        self.assertIn("Hi — proposal text.", body)
        self.assertIn("Score Breakdown", body)
        self.assertIn("$12,000", body)

    # ── run-scoping contract (supervised-run-1 flood: 224 tasks from an
    # unscoped push; these tests pin the rewrite) ────────────────────────────
    def audit_map(self, summary):
        return {a["id"]: a for a in summary["audit"]}

    def test_unscoped_invocation_impossible(self):
        self.seed()
        with self.assertRaises(SystemExit):
            cp.run(db_path=self.db, live=False)   # no ids, no since → refuse

    def test_ids_scope_pushes_only_listed(self):
        self.seed()                                       # in list
        self.seed(jid="2064908810394200830", score=20,
                  status="Scored")                        # eligible, NOT listed
        summary = cp.run(db_path=self.db, live=False,
                         ids=["2064908810394200829"])
        self.assertEqual(summary["would_push"], 1)
        self.assertEqual(self.audit_map(summary)["2064908810394200829"]
                         ["action"], "would_push")

    def test_hard_status_guards_audit_each_skip(self):
        self.seed(jid="2064908810394200830", status="Phantom", score=22)
        self.seed(jid="2064908810394200831", status="New", score=None)
        self.seed(jid="2064908810394200832", status="ClickUp Created",
                  score=19, clickup_task_id="abc")
        self.seed(jid="2064908810394200833", status="Scored", score=5)
        ids = ["2064908810394200830", "2064908810394200831",
               "2064908810394200832", "2064908810394200833",
               "2064999999999999999"]  # last: not in DB (also same-digit run)
        summary = cp.run(db_path=self.db, live=False, ids=ids)
        self.assertEqual(summary["would_push"], 0)
        audit = self.audit_map(summary)
        self.assertIn("Phantom", audit["2064908810394200830"]["reason"])
        self.assertIn("New", audit["2064908810394200831"]["reason"])
        self.assertIn("already", audit["2064908810394200832"]["reason"])
        self.assertIn("band", audit["2064908810394200833"]["reason"])
        self.assertEqual(audit["2064999999999999999"]["action"], "skip")

    def test_fabricated_id_never_pushed_regardless_of_status(self):
        jid = "2064823456789012345"   # keyboard walk, escaped-quarantine class
        self.seed(jid=jid, status="Scored", score=24,
                  job_url=f"https://www.upwork.com/jobs/X_~02{jid}/")
        summary = cp.run(db_path=self.db, live=False, ids=[jid])
        self.assertEqual(summary["would_push"], 0)
        self.assertIn("fabricated", self.audit_map(summary)[jid]["reason"])

    def test_since_scope(self):
        self.seed(first_scored_at="2026-06-12T13:20:00+00:00")
        self.seed(jid="2064908810394200830", score=17, status="Scored",
                  first_scored_at="2026-05-20T10:00:00+00:00")  # old backlog
        summary = cp.run(db_path=self.db, live=False,
                         since="2026-06-12T13:00:00+00:00")
        self.assertEqual(summary["would_push"], 1)
        audit = self.audit_map(summary)
        self.assertEqual(audit["2064908810394200829"]["action"], "would_push")
        self.assertNotIn("2064908810394200830", audit)  # outside scope

    def test_dry_run_default_writes_nothing(self):
        self.seed()
        summary = cp.run(db_path=self.db, ids=["2064908810394200829"])
        self.assertTrue(summary["dry_run"])
        row = self.rows("SELECT clickup_task_id, status FROM jobs")[0]
        self.assertIsNone(row["clickup_task_id"])
        self.assertEqual(row["status"], "Drafted")


if __name__ == "__main__":
    unittest.main(verbosity=1)
