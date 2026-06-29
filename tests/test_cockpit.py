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
from unittest import mock
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

    def test_wake_post_authed_and_allowlisted(self):
        with mock.patch.object(actions.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            gs, _ = self.post("/api/wake", body={"job": "sync"})
        self.assertEqual(gs, 200)
        ps, _ = self.post("/api/wake", token=None, body={"job": "sync"})
        self.assertEqual(ps, 401)

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


class FakeAirtable:
    def __init__(self, records):
        self._records = records
    def list_records(self, base, table, **kw):
        return self._records


class TestClientsReader(unittest.TestCase):
    def setUp(self):
        data._clients_cache["data"] = None  # reset module cache between tests

    def fake(self):
        return FakeAirtable([
            {"id": "rec1", "fields": {"Name": "Acme", "Status": "Active",
             "Contracts": ["c1", "c2"], "Transactions": ["t1"],
             "Tags": ["make.com"], "First Contract Date": "2025-04-01"}},
            {"id": "rec2", "fields": {"Name": "Beta", "Status": "Paused",
             "Contracts": ["c3"], "Transactions": []}},
        ])

    def test_maps_fields_and_counts_links(self):
        out = data.clients(client=self.fake(), use_cache=False)
        self.assertEqual(out["count"], 2)
        acme = next(c for c in out["clients"] if c["name"] == "Acme")
        self.assertEqual(acme["status"], "Active")
        self.assertEqual(acme["contracts"], 2)
        self.assertEqual(acme["transactions"], 1)

    def test_active_sorts_first(self):
        out = data.clients(client=self.fake(), use_cache=False)
        self.assertEqual(out["clients"][0]["status"], "Active")

    def test_by_status_summary(self):
        out = data.clients(client=self.fake(), use_cache=False)
        self.assertEqual(out["by_status"], {"Active": 1, "Paused": 1})

    def test_cache_avoids_second_fetch(self):
        calls = {"n": 0}

        class Counting(FakeAirtable):
            def list_records(self, *a, **k):
                calls["n"] += 1
                return []
        data.clients(client=Counting([]), use_cache=True)
        data.clients(client=Counting([]), use_cache=True)
        self.assertEqual(calls["n"], 1)  # second served from cache


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


class TestWakeAndRail(unittest.TestCase):
    def test_wake_kicks_allowlisted_label(self):
        with mock.patch.object(actions.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            out = actions.wake(None, {"job": "scan"})
        self.assertTrue(out["ok"])
        argv = run.call_args[0][0]
        self.assertIn("io.etherwise.v3.upwork-scan", " ".join(argv))
        self.assertIn("kickstart", argv)

    def test_wake_rejects_unknown_job(self):
        with mock.patch.object(actions.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                actions.wake(None, {"job": "rm-rf"})  # not allowlisted
            run.assert_not_called()

    def test_wake_brief_not_available_until_m4(self):
        with self.assertRaises(ValueError):
            actions.wake(None, {"job": "brief"})

    def test_rail_kicks_outcome_capture(self):
        with mock.patch.object(actions.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            out = actions.rail(None, {"rail": "outcome-capture"})
        self.assertTrue(out["ok"])
        self.assertIn("io.etherwise.v3.upwork-outcomes",
                      " ".join(run.call_args[0][0]))

    def test_rail_rejects_unknown(self):
        with self.assertRaises(ValueError):
            actions.rail(None, {"rail": "send-money"})


class TestKnowledgeReader(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cockpit-kn-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_kb(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO sources (source_type, source_ref,"
                         " content_hash, client_id) VALUES"
                         " ('fathom','fathom/1','h1','adelye')")
            sid = conn.execute("SELECT id FROM sources").fetchone()["id"]
            for txt, scope in [("Adelye uses Make.com", "client"),
                               ("Agency does no-code automation", "agency"),
                               ("Built voice agent for OtherCo", "cross-client")]:
                conn.execute(
                    "INSERT INTO facts (source_id, client_id, category,"
                    " fact_text, citation, confidence, scope) VALUES"
                    " (?,?,?,?,?,?,?)", (sid, "adelye", "stack", txt,
                    "fathom/1 @ 1", "CONFIRMED", scope))
            conn.execute("INSERT INTO dossier_index (client_id, client_name,"
                         " dossier_path, fact_count, source_count,"
                         " last_compiled_at) VALUES ('adelye','Adelye','p',1,1,"
                         "'2026-06-17')")

    def test_overview_counts_and_fathom(self):
        self._seed_kb()
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO sync_cursors (name, value) VALUES"
                         " ('fathom_created_after','2026-06-10T00:00:00Z')")
        out = data.knowledge(self.db_path)
        self.assertEqual(out["mode"], "overview")
        self.assertEqual(out["totals"]["facts"], 3)
        self.assertEqual(out["totals"]["client_facts"], 1)
        self.assertEqual(out["proof_points"], {"agency": 1, "cross": 1})
        self.assertEqual(out["fathom"]["cursor"], "2026-06-10T00:00:00Z")
        self.assertEqual(out["dossiers"][0]["client_id"], "adelye")

    def test_dossier_detail_client_scope_only(self):
        self._seed_kb()
        out = data.knowledge(self.db_path, client="adelye")
        self.assertEqual(out["mode"], "dossier")
        texts = [f["fact_text"] for f in out["facts"]]
        self.assertEqual(texts, ["Adelye uses Make.com"])  # agency/cross excluded

    def test_search_fts(self):
        self._seed_kb()
        out = data.knowledge(self.db_path, q="Make.com")
        self.assertEqual(out["mode"], "search")
        self.assertTrue(any("Make.com" in r["fact_text"] for r in out["results"]))

    def test_search_empty_terms(self):
        self._seed_kb()
        self.assertEqual(data.knowledge(self.db_path, q="!!!")["results"], [])


class TestNoteAction(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cockpit-note-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_quick_capture_adds_note(self):
        out = actions.note(self.db_path, {"text": "follow up with Acme"})
        self.assertEqual(out["source"], "cockpit")
        with db.connect(self.db_path) as conn:
            r = conn.execute("SELECT state, source FROM notes").fetchone()
        self.assertEqual((r["state"], r["source"]), ("new", "cockpit"))

    def test_empty_note_rejected(self):
        with self.assertRaises(ValueError):
            actions.note(self.db_path, {"text": "  "})

    def test_cos_nudge_acts_on_real_table(self):
        from modules.chief_of_staff import nudges as eng
        eng.write_nudge(self.db_path, source="sales", dept="sales",
                        kind="owed_reply", title="x", dedup_key="followup:t9")
        out = actions.cos_nudge(self.db_path, {"dedup_key": "followup:t9",
                                               "action": "done"})
        self.assertTrue(out["ok"])
        with db.connect(self.db_path) as conn:
            st = conn.execute("SELECT state FROM nudges WHERE dedup_key="
                              "'followup:t9'").fetchone()["state"]
        self.assertEqual(st, "done")

    def test_cos_nudge_rejects_bad_action(self):
        with self.assertRaises(ValueError):
            actions.cos_nudge(self.db_path, {"dedup_key": "x", "action": "send"})


class TestAskAction(unittest.TestCase):
    def _proc(self, stdout="", rc=0, stderr=""):
        import subprocess
        return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)

    def test_ask_requires_question(self):
        with self.assertRaises(ValueError):
            actions.ask(None, {"question": "  "})

    def test_ask_returns_librarian_json(self):
        payload = {"answer": "They use Make.", "grounded": True,
                   "citations": ["fathom/1 @ 1"]}
        with mock.patch.object(actions.subprocess, "run",
                               return_value=self._proc(json.dumps(payload))):
            out = actions.ask(None, {"question": "stack?", "client": "adelye"})
        self.assertTrue(out["grounded"])
        self.assertEqual(out["citations"], ["fathom/1 @ 1"])

    def test_ask_nonzero_rc_is_error_not_raise(self):
        with mock.patch.object(actions.subprocess, "run",
                               return_value=self._proc("", rc=1, stderr="boom")):
            out = actions.ask(None, {"question": "x"})
        self.assertFalse(out["grounded"])
        self.assertIn("boom", out["error"])

    def test_ask_model_call_is_out_of_process(self):
        # the cockpit action must shell out, never import the gateway in-process
        called = {}
        def fake_run(args, **kw):
            called["args"] = args
            return self._proc(json.dumps({"grounded": False, "answer": ""}))
        with mock.patch.object(actions.subprocess, "run", side_effect=fake_run):
            actions.ask(None, {"question": "hi"})
        self.assertIn("kb-ask", " ".join(called["args"]))


class TestKnowledgeServerParams(CockpitServerCase):
    def test_query_params_reach_reader(self):
        status, body = self.get("/api/knowledge?q=anything")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["mode"], "search")

    def test_client_param_dossier_mode(self):
        status, body = self.get("/api/knowledge?client=adelye")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["mode"], "dossier")

    def test_no_param_overview(self):
        status, body = self.get("/api/knowledge")
        self.assertEqual(json.loads(body)["mode"], "overview")

    def test_db_path_query_cannot_redirect(self):
        # ?db_path= must be ignored (db_path is the fixed positional arg)
        status, body = self.get("/api/knowledge?db_path=/etc/passwd")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["mode"], "overview")


def seed_money(db_path):
    """M5-shaped rows: integer minor units, canonical_key, pnl_bucket (== bucket()),
    amount_base_minor (base-USD), + account_balances. Keeps the doctor GREEN so the
    gated money() returns figures. (typ, category, base_minor, dt, source, bucket)"""
    rows = [
        ("Fixed Earning", None, 200000, "2026-06-10", "Upwork", "revenue"),
        ("Hourly Earning", None, 150000, "2026-06-12", "Wise", "revenue"),   # non-Upwork rev
        ("Fixed Earning", None, 400000, "2026-05-20", "Upwork", "revenue"),  # last month
        ("Fixed Earning", None, 99900, "2026-04-10", "Upwork", "revenue"),   # older (chart)
        ("Other", "expense", -300000, "2026-06-01", "IDFC", "opex"),         # expense, not revenue
        ("Connect Purchase", None, -4500, "2026-06-08", "Upwork", "platform_fee"),
        ("Connect Purchase", None, -4500, "2026-05-08", "Upwork", "platform_fee"),
    ]
    with db.connect(db_path) as conn:
        for i, (typ, cat, base, dt, src, bucket) in enumerate(rows):
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type, category,"
                " amount_minor, amount_base_minor, currency, creation_dt, source,"
                " pnl_bucket) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"t{i}", f"k{i}", typ, cat, base, base, "USD",
                 dt + "T00:00:00+0000", src, bucket))
        # an IDFC INR row carrying the pinned INR->USD rate (drives cash conversion)
        conn.execute(
            "INSERT INTO transactions (record_id, canonical_key, type, category,"
            " amount_minor, amount_base_minor, currency, creation_dt, source, fx_rate,"
            " fx_date, pnl_bucket) VALUES ('fx','kfx','Other','expense',-100000,-1200,"
            "'INR','2026-06-05T00:00:00+0000','IDFC','0.012','2026-06-05','opex')")
        # cash: savings + FD (INR) + Wise (USD). 'authoritative:' source so #1 skips
        # them (aggregates, not statements) -> doctor stays GREEN for this panel test.
        for acct, ccy, closing in [("idfc", "INR", 50000073),
                                    ("idfc-fd", "INR", 586878353),
                                    ("wise", "USD", 0)]:
            conn.execute(
                "INSERT INTO account_balances (account, currency, opening_minor,"
                " closing_minor, statement_date, source_file) VALUES (?,?,?,?,?,?)",
                (acct, ccy, closing, closing, "2026-06-30", f"authoritative:{acct}"))


class TestMoneyReader(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cockpit-money-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed_money(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_revenue_this_month_from_pnl_bucket(self):
        out = data.money(self.db_path, today="2026-06-15")
        self.assertFalse(out["blocked"])
        self.assertEqual(out["doctor"], "GREEN")
        # June revenue bucket: 2000 (Upwork) + 1500 (Wise) ; excludes opex + connects
        self.assertEqual(out["revenue"]["month_usd"], 3500.0)
        self.assertEqual(out["revenue"]["month"], "2026-06")

    def test_revenue_last_month_and_mtd(self):
        out = data.money(self.db_path, today="2026-06-15")
        self.assertEqual(out["revenue"]["last_month"], "2026-05")
        self.assertEqual(out["revenue"]["last_month_usd"], 4000.0)
        # May earning is dated May-20; through May-15 (same DOM) -> MTD 0
        self.assertEqual(out["revenue"]["last_month_mtd_usd"], 0.0)

    def test_connects_spend_signed_fee(self):
        out = data.money(self.db_path, today="2026-06-15")
        self.assertEqual(out["connects"]["this_month_usd"], -45.0)
        self.assertEqual(out["connects"]["lifetime_usd"], -90.0)

    def test_transactions_feed_base_usd_and_bucket(self):
        out = data.money(self.db_path, today="2026-06-15")
        self.assertLessEqual(len(out["transactions"]), 15)
        newest = out["transactions"][0]
        self.assertEqual(newest["creation_dt"][:10], "2026-06-12")  # latest
        self.assertEqual(newest["type"], "revenue")                 # pnl_bucket
        self.assertEqual(newest["amount"], 1500.0)                  # base-USD
        for key in ("type", "amount", "currency", "profile"):
            self.assertIn(key, newest)

    def test_by_month_chart_series(self):
        out = data.money(self.db_path, today="2026-06-15")
        by = {m["month"]: m["usd"] for m in out["revenue"]["by_month"]}
        self.assertEqual(by.get("2026-06"), 3500.0)
        self.assertEqual(by.get("2026-05"), 4000.0)
        self.assertEqual(by.get("2026-04"), 999.0)

    def test_cash_position_from_account_balances(self):
        out = data.money(self.db_path, today="2026-06-15")
        # savings 500000.73 + FD 5868783.53 INR @ 0.012 + Wise $0
        self.assertEqual(out["cash"]["value_usd"], 76425.41)
        accts = {a["account"]: a["usd"] for a in out["cash"]["by_account"]}
        self.assertEqual(accts["wise"], 0.0)
        self.assertEqual(round(sum(accts.values()), 2), out["cash"]["value_usd"])

    def test_pnl_matches_finance_pnl(self):
        # the cockpit panel must equal the audited compute (bin/finance-pnl source)
        from modules.finance import pnl, money as fmoney
        import sqlite3
        c = sqlite3.connect(self.db_path); c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute(
            "SELECT type, category, accounting_subtype, upwork_type, description,"
            " pnl_bucket, amount_base_minor FROM transactions WHERE canonical_key IS NOT NULL")]
        want = pnl.compute(rows)
        out = data.money(self.db_path, today="2026-06-15")
        self.assertEqual(out["pnl"]["operating_profit_usd"],
                         round(float(fmoney.from_minor(want["operating_profit_minor"])), 2))
        self.assertEqual(out["pnl"]["net_revenue_usd"],
                         round(float(fmoney.from_minor(want["net_revenue_minor"])), 2))

    def test_doctor_gate_blocks_on_red(self):
        # a NULL-amount_minor row -> doctor #8 RED -> money() shows nothing
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO transactions (record_id, canonical_key, type,"
                         " amount_minor, currency) VALUES ('bad','kbad','Other',NULL,'USD')")
        out = data.money(self.db_path, today="2026-06-15")
        self.assertTrue(out["blocked"])
        self.assertEqual(out["doctor"], "RED")
        self.assertNotIn("revenue", out)         # no figures leak on RED
        self.assertNotIn("cash", out)


if __name__ == "__main__":
    unittest.main()
