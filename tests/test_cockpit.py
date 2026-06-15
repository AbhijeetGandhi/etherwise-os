"""Cockpit tests — real ThreadingHTTPServer on an ephemeral localhost port,
hit over urllib so token auth + routing are genuinely exercised. Data readers
run against a fixture SQLite DB. No network, no Airtable (Phases 0-2 are
SQLite-local).
"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from core import config, db
from modules.cockpit import actions, data, server

TOKEN = "test-cockpit-token-123"


def seed(db_path):
    with db.connect(db_path) as conn:
        # runs: a few tasks, latest per task matters
        conn.execute("INSERT INTO runs (task_name, started_at, completed_at,"
                     " status, shadow) VALUES ('upwork_sync',"
                     " '2026-06-15T04:45:00+00:00', '2026-06-15T04:45:30+00:00',"
                     " 'completed', 1)")
        conn.execute("INSERT INTO runs (task_name, started_at, completed_at,"
                     " status, shadow) VALUES ('upwork_scan',"
                     " '2026-06-15T05:15:00+00:00', '2026-06-15T05:15:20+00:00',"
                     " 'completed', 1)")
        conn.execute("INSERT INTO runs (task_name, started_at, status, shadow)"
                     " VALUES ('upwork_scan', '2026-06-15T03:15:00+00:00',"
                     " 'failed', 1)")  # older — must not win
        today = datetime.now(config.TZ).strftime("%Y-%m-%d")
        for cost in (0.02, 0.018):
            conn.execute("INSERT INTO claude_usage (called_at, ist_date,"
                         " task_name, model, billing_route, total_cost_usd)"
                         " VALUES (?,?,?,?,?,?)",
                         (today, today, "upwork_scan", "claude-sonnet-4-6",
                          "payg", cost))
        conn.execute("INSERT INTO shadow_ledger (task_name, target, operation,"
                     " payload_json, diff_status) VALUES ('upwork_scan',"
                     " 'clickup', 'create', '{}', 'pending')")


class CockpitServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cockpit-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed(self.db_path)
        self.srv = server.make_server(host="127.0.0.1", port=0,
                                      db_path=self.db_path, token=TOKEN)
        self.host, self.port = self.srv.server_address
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.t.join(timeout=5)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, path, token=TOKEN):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url)
        if token is not None:
            req.add_header("X-Cockpit-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def post(self, path, token=TOKEN, body=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data_b = json.dumps(body or {}).encode()
        req = urllib.request.Request(url, data=data_b, method="POST")
        if token is not None:
            req.add_header("X-Cockpit-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()


class TestAuth(CockpitServerCase):
    def test_api_without_token_401(self):
        status, _ = self.get("/api/system", token=None)
        self.assertEqual(status, 401)

    def test_api_wrong_token_401(self):
        status, _ = self.get("/api/system", token="nope")
        self.assertEqual(status, 401)

    def test_api_correct_token_200(self):
        status, _ = self.get("/api/system")
        self.assertEqual(status, 200)

    def test_static_shell_needs_no_token(self):
        # the inert SPA shell must load so it can then send the token
        status, body = self.get("/", token=None)
        self.assertEqual(status, 200)
        self.assertIn("<html", body.lower())

    def test_bind_is_loopback(self):
        self.assertEqual(self.host, "127.0.0.1")


class TestRouting(CockpitServerCase):
    def test_system_payload_shape(self):
        status, body = self.get("/api/system")
        self.assertEqual(status, 200)
        d = json.loads(body)
        for key in ("jobs", "spend", "shadow", "doctor"):
            self.assertIn(key, d)

    def test_unknown_api_404(self):
        status, _ = self.get("/api/does-not-exist")
        self.assertEqual(status, 404)

    def test_money_endpoint_200(self):
        status, body = self.get("/api/money")
        self.assertEqual(status, 200)
        self.assertIn("revenue", json.loads(body))

    def test_today_endpoint_200(self):
        status, body = self.get("/api/today")
        self.assertEqual(status, 200)
        d = json.loads(body)
        for key in ("follow_ups", "hot_leads", "proto_nudges", "metrics"):
            self.assertIn(key, d)

    def test_pipeline_endpoint_200(self):
        status, body = self.get("/api/pipeline")
        self.assertEqual(status, 200)
        d = json.loads(body)
        for key in ("applied", "by_status", "win_rate", "bands", "to_triage"):
            self.assertIn(key, d)

    def test_no_send_endpoint_anywhere(self):
        # drafts-only is sacred: no route may expose a send path
        for p in ("/api/send", "/api/message/send", "/api/upwork/send",
                  "/api/followup/send"):
            gs, _ = self.get(p)
            ps, _ = self.post(p)
            self.assertIn(gs, (401, 404), p)
            self.assertIn(ps, (401, 404), p)
        # and no source carries a send route literal
        src = (Path(server.__file__).read_text()
               + Path(data.__file__).read_text()
               + Path(actions.__file__).read_text())
        self.assertNotIn("/send", src)

    def test_nudge_post_authed(self):
        gs, _ = self.post("/api/nudge",
                          body={"item_key": "followup:rA", "action": "done"})
        self.assertEqual(gs, 200)
        ps, _ = self.post("/api/nudge", token=None,
                          body={"item_key": "x", "action": "done"})
        self.assertEqual(ps, 401)


class TestSystemReader(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cockpit-data-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_latest_run_per_task(self):
        out = data.system(self.db_path)
        jobs = {j["task_name"]: j for j in out["jobs"]}
        # scan's latest is the 05:15 completed, not the 03:15 failed
        self.assertEqual(jobs["upwork_scan"]["status"], "completed")
        self.assertEqual(jobs["upwork_scan"]["started_at"][:10], "2026-06-15")

    def test_spend_today_and_ceilings(self):
        out = data.system(self.db_path)
        self.assertAlmostEqual(out["spend"]["today_usd"], 0.038, places=3)
        self.assertEqual(out["spend"]["soft_limit_usd"],
                         config.DAILY_SOFT_LIMIT_USD)
        self.assertEqual(out["spend"]["hard_limit_usd"],
                         config.DAILY_HARD_LIMIT_USD)

    def test_shadow_volume(self):
        out = data.system(self.db_path)
        self.assertGreaterEqual(out["shadow"]["pending"], 1)

    def test_doctor_runs_offline(self):
        out = data.system(self.db_path)
        # offline doctor: a list of checks with statuses, no network
        self.assertIsInstance(out["doctor"]["checks"], list)
        self.assertIn(out["doctor"]["worst"], ("PASS", "WARN", "FAIL"))


def seed_today(db_path):
    with db.connect(db_path) as conn:
        # threads + pending drafts → follow-ups
        for tid, bucket, tier, lastdt in [
                ("rA", "owed", "HOT", "2026-06-10T09:00:00+00:00"),
                ("rB", "followup", "WARM", "2026-06-05T09:00:00+00:00"),
                ("rC", "snoozed", "COLD", "2026-06-01T09:00:00+00:00")]:
            conn.execute("INSERT INTO threads (id, room_name, topic, bucket,"
                         " tier, latest_message_dt) VALUES (?,?,?,?,?,?)",
                         (tid, f"Room {tid}", f"Topic {tid}", bucket, tier,
                          lastdt))
        conn.execute("INSERT INTO drafts (thread_id, draft_kind, tier, body,"
                     " word_count, sent_status) VALUES"
                     " ('rA','owed','HOT','Reply draft',2,'pending')")
        conn.execute("INSERT INTO drafts (thread_id, draft_kind, tier, body,"
                     " word_count, sent_status) VALUES"
                     " ('rB','followup','WARM','Nudge draft',2,'pending')")
        conn.execute("INSERT INTO drafts (thread_id, draft_kind, tier, body,"
                     " word_count, sent_status) VALUES"
                     " ('rC','followup','COLD','Old',1,'stale')")  # not pending
        # hot leads: recent, not tasked
        conn.execute("INSERT INTO scored_jobs (id, title, score, status,"
                     " job_url, first_scored_at) VALUES"
                     " ('2065400000000000111','Make build',24,'Drafted',"
                     " 'https://www.upwork.com/jobs/~022065400000000000111/',"
                     " '2026-06-15T03:00:00+00:00')")
        conn.execute("INSERT INTO scored_jobs (id, title, score, status,"
                     " clickup_task_id, first_scored_at) VALUES"
                     " ('2065400000000000112','Tasked',20,'ClickUp Created',"
                     " 'abc','2026-06-15T03:00:00+00:00')")  # tasked → excluded
        # OLD untasked hot lead: excluded from Today (recent), counted in
        # Pipeline to-triage backlog
        conn.execute("INSERT INTO scored_jobs (id, title, score, status,"
                     " first_scored_at) VALUES"
                     " ('2065400000000000113','Old hot',18,'Scored',"
                     " '2026-05-01T03:00:00+00:00')")
        # proposals for applied counts (created_dt windows) + active
        for pid, status, dt in [
                ("p1", "Submitted", "2026-06-15T09:00:00+0000"),   # today
                ("p2", "Interview", "2026-06-12T09:00:00+0000"),   # this week
                ("p3", "Submitted", "2026-06-05T09:00:00+0000"),   # last week
                ("p4", "Won", "2026-04-01T09:00:00+0000")]:
            conn.execute("INSERT INTO proposals (id, status, created_dt)"
                         " VALUES (?,?,?)", (pid, status, dt))
        conn.execute("INSERT INTO contracts (id, upwork_status) VALUES"
                     " ('c1','ACTIVE')")


class TestTodayReader(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cockpit-today-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed_today(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_followups_are_pending_drafts_with_thread_link(self):
        out = data.today(self.db_path, today="2026-06-15")
        fu = {f["thread_id"]: f for f in out["follow_ups"]}
        self.assertIn("rA", fu)   # owed, pending
        self.assertIn("rB", fu)   # followup, pending
        self.assertNotIn("rC", fu)  # draft is stale, not pending
        self.assertIn("messages/rooms/rA", fu["rA"]["thread_url"])
        self.assertEqual(fu["rA"]["draft"], "Reply draft")

    def test_owed_sorts_before_followup(self):
        out = data.today(self.db_path, today="2026-06-15")
        self.assertEqual(out["follow_ups"][0]["bucket"], "owed")

    def test_followup_has_real_age_not_wordcount(self):
        # redline 2: age_days is the true thread age (not word_count-as-weeks)
        out = data.today(self.db_path, today="2026-06-15")
        fu = {f["thread_id"]: f for f in out["follow_ups"]}
        self.assertEqual(fu["rA"]["age_days"], 5)   # Jun10 → Jun15
        self.assertEqual(fu["rB"]["age_days"], 10)  # Jun05 → Jun15

    def test_hot_leads_recent_untasked_only(self):
        out = data.today(self.db_path, today="2026-06-15")
        ids = [h["id"] for h in out["hot_leads"]]
        self.assertIn("2065400000000000111", ids)
        self.assertNotIn("2065400000000000112", ids)  # tasked → excluded

    def test_applied_counts_windows(self):
        out = data.today(self.db_path, today="2026-06-15")
        a = out["metrics"]["applied"]
        self.assertEqual(a["today"], 1)       # p1
        self.assertGreaterEqual(a["week"], 2)  # p1 + p2 within 7d
        self.assertEqual(a["last_week"], 1)   # p3

    def test_active_and_counts(self):
        out = data.today(self.db_path, today="2026-06-15")
        m = out["metrics"]
        self.assertEqual(m["active"]["interviews"], 1)
        self.assertEqual(m["active"]["contracts"], 1)
        self.assertEqual(m["follow_ups_due"], 2)
        self.assertEqual(m["hot_leads"], 1)  # recent only; old (May) excluded
        self.assertEqual(m["revenue"]["target_usd"],
                         config.MONTHLY_REVENUE_TARGET_USD)

    def test_hot_leads_today_is_recent_only(self):
        out = data.today(self.db_path, today="2026-06-15")
        ids = [h["id"] for h in out["hot_leads"]]
        self.assertNotIn("2065400000000000113", ids)  # May job excluded


class TestPipelineReader(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cockpit-pipe-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed_today(self.db_path)
        with db.connect(self.db_path) as conn:  # add terminal outcomes
            for pid, status, dt in [
                    ("w1", "Won", "2026-05-01T00:00:00+0000"),
                    ("w2", "Won", "2026-05-02T00:00:00+0000"),
                    ("l1", "Lost", "2026-05-03T00:00:00+0000"),
                    ("e1", "Expired", "2026-05-04T00:00:00+0000")]:
                conn.execute("INSERT INTO proposals (id, status, created_dt)"
                             " VALUES (?,?,?)", (pid, status, dt))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_to_triage_is_open_untasked_hot(self):
        out = data.pipeline(self.db_path, today="2026-06-15")
        # 111(24)+113(18) untasked hot; 112 is ClickUp Created (tasked) → out
        self.assertEqual(out["to_triage"], 2)

    def test_score_bands_open_board_only(self):
        out = data.pipeline(self.db_path, today="2026-06-15")
        b = out["bands"]
        self.assertEqual(b["hot"], 2)        # untasked hot only (not 112)
        self.assertEqual(b["hot"], out["to_triage"])  # coherent
        self.assertIn("note", b)             # honest caveat present

    def test_win_rate_from_status_with_caveat(self):
        out = data.pipeline(self.db_path, today="2026-06-15")
        wr = out["win_rate"]
        # seed_today p4=Won + w1,w2=Won → 3 won; +Lost1 +Expired1 → decided 5
        self.assertEqual(wr["won"], 3)
        self.assertEqual(wr["decided"], 5)
        self.assertEqual(wr["pct"], 60.0)
        self.assertIn("threads-only", wr["caveat"])  # honest skew note

    def test_proposals_by_status(self):
        out = data.pipeline(self.db_path, today="2026-06-15")
        self.assertEqual(out["by_status"].get("Won"), 3)

    def test_applied_trend_filled_to_current_week(self):
        out = data.pipeline(self.db_path, today="2026-06-15")
        t = out["applied_trend"]
        self.assertEqual(len(t), 10)               # zero-filled window
        self.assertTrue(t[-1]["current"])          # ends at current week
        d = datetime(2026, 6, 15).date()
        expected_mon = (d - timedelta(days=d.weekday())).isoformat()
        self.assertEqual(t[-1]["week"], expected_mon)


class TestNudgeActions(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cockpit-nudge-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed_today(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def state(self, key):
        with db.connect(self.db_path) as conn:
            r = conn.execute("SELECT state, snooze_until FROM nudge_state"
                             " WHERE item_key=?", (key,)).fetchone()
        return dict(r) if r else None

    def test_done_persists_and_hides_followup(self):
        actions.nudge(self.db_path, {"item_key": "followup:rA",
                                     "action": "done"})
        self.assertEqual(self.state("followup:rA")["state"], "done")
        # re-read today: rA gone, rB still there (persists across "restart")
        out = data.today(self.db_path, today="2026-06-15")
        ids = [f["thread_id"] for f in out["follow_ups"]]
        self.assertNotIn("rA", ids)
        self.assertIn("rB", ids)

    def test_dismiss_hides(self):
        actions.nudge(self.db_path, {"item_key": "followup:rB",
                                     "action": "dismiss"})
        out = data.today(self.db_path, today="2026-06-15")
        self.assertNotIn("rB", [f["thread_id"] for f in out["follow_ups"]])

    def test_snooze_hides_until_date_then_returns(self):
        actions.nudge(self.db_path, {"item_key": "followup:rA",
                                     "action": "snooze", "snooze_days": 3},
                      today="2026-06-15")
        self.assertEqual(self.state("followup:rA")["snooze_until"],
                         "2026-06-18")
        # still snoozed on the 17th
        self.assertNotIn("rA", [f["thread_id"] for f in
                                data.today(self.db_path,
                                           today="2026-06-17")["follow_ups"]])
        # back on the 18th
        self.assertIn("rA", [f["thread_id"] for f in
                             data.today(self.db_path,
                                        today="2026-06-18")["follow_ups"]])

    def test_invalid_action_rejected(self):
        with self.assertRaises(ValueError):
            actions.nudge(self.db_path, {"item_key": "x", "action": "send"})

    def test_done_count_drops_followups_due(self):
        before = data.today(self.db_path, today="2026-06-15")["metrics"]["follow_ups_due"]
        actions.nudge(self.db_path, {"item_key": "followup:rA", "action": "done"})
        after = data.today(self.db_path, today="2026-06-15")["metrics"]["follow_ups_due"]
        self.assertEqual(after, before - 1)


def seed_money(db_path):
    rows = [
        # (type, amount, creation_dt, profile, currency, description)
        ("Fixed Earning", 2000.0, "2026-06-10T00:00:00+0000", "Personal"),
        ("Hourly Earning", 1500.0, "2026-06-12T00:00:00+0000", "Personal"),
        ("Bonus", 200.0, "2026-06-05T00:00:00+0000", "Agency"),
        ("Fixed Earning", 4000.0, "2026-05-20T00:00:00+0000", "Personal"),  # last month
        ("Hourly Earning", -50.0, "2026-06-11T00:00:00+0000", "Personal"),  # refund-ish, amount<=0 excluded
        ("Salary", -3000.0, "2026-06-01T00:00:00+0000", "Personal"),        # expense, not revenue
        ("Connect Purchase", 45.0, "2026-06-08T00:00:00+0000", "Personal"),
        ("Connect Purchase", 45.0, "2026-05-08T00:00:00+0000", "Personal"),
        ("Fixed Earning", 999.0, "2026-04-10T00:00:00+0000", "Personal"),   # older month (chart)
    ]
    with db.connect(db_path) as conn:
        for i, (typ, amt, dt, prof) in enumerate(rows):
            conn.execute(
                "INSERT INTO transactions (record_id, type, amount, currency,"
                " creation_dt, profile, description) VALUES (?,?,?,?,?,?,?)",
                (f"t{i}", typ, amt, "USD", dt, prof, f"{typ} row"))


class TestMoneyReader(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cockpit-money-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed_money(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_revenue_this_month_canonical(self):
        out = data.money(self.db_path, today="2026-06-15")
        # 2000 + 1500 + 200 (earnings, amount>0); excludes -50, Salary, Connect
        self.assertEqual(out["revenue"]["month_usd"], 3700.0)
        self.assertEqual(out["revenue"]["target_usd"],
                         config.MONTHLY_REVENUE_TARGET_USD)
        self.assertEqual(out["revenue"]["month"], "2026-06")

    def test_revenue_last_month(self):
        out = data.money(self.db_path, today="2026-06-15")
        self.assertEqual(out["revenue"]["last_month"], "2026-05")
        self.assertEqual(out["revenue"]["last_month_usd"], 4000.0)

    def test_last_month_mtd_apples_to_apples(self):
        # seed_money's May earning is dated May-20; through May-15 (same DOM
        # as today June-15) it should NOT be counted → MTD=0, full=4000
        out = data.money(self.db_path, today="2026-06-15")
        self.assertEqual(out["revenue"]["last_month_mtd_usd"], 0.0)
        self.assertEqual(out["revenue"]["last_month_usd"], 4000.0)

    def test_connects_spend(self):
        out = data.money(self.db_path, today="2026-06-15")
        self.assertEqual(out["connects"]["this_month_usd"], 45.0)
        self.assertEqual(out["connects"]["lifetime_usd"], 90.0)

    def test_transactions_feed(self):
        out = data.money(self.db_path, today="2026-06-15")
        self.assertLessEqual(len(out["transactions"]), 15)
        newest = out["transactions"][0]
        self.assertEqual(newest["creation_dt"][:10], "2026-06-12")  # latest
        for key in ("type", "amount", "currency", "profile"):
            self.assertIn(key, newest)

    def test_by_month_chart_series(self):
        out = data.money(self.db_path, today="2026-06-15")
        by = {m["month"]: m["usd"] for m in out["revenue"]["by_month"]}
        self.assertEqual(by.get("2026-06"), 3700.0)
        self.assertEqual(by.get("2026-05"), 4000.0)
        self.assertEqual(by.get("2026-04"), 999.0)

    def test_cash_position_not_fabricated(self):
        # honest: no bank source in v3 SQLite — flagged, not a fake number
        out = data.money(self.db_path, today="2026-06-15")
        self.assertIn("cash", out)
        self.assertIsNone(out["cash"]["value_usd"])
        self.assertTrue(out["cash"]["note"])


if __name__ == "__main__":
    unittest.main()
