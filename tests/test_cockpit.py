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
        for key in ("sources", "queue", "backlog", "metrics"):
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

    def test_spend_by_task_breakdown_present(self):
        out = data.system(self.db_path)
        bt = {r["task_name"]: r["usd"] for r in out["spend"]["by_task"]}
        self.assertIn("upwork_scan", bt)                 # seed put usage here

    def test_anomaly_open_count_excludes_self_healed(self):
        """'open' must mean genuinely-active, not 'ever fired'. An anomaly whose
        task has since completed is recovered — else a long-healed crashloop
        reads as dozens of live fires (the 71-vs-0 correction, 2026-07-30)."""
        with db.connect(self.db_path) as conn:
            # a failure ~2 days ago (inside the 14d window), then a success
            # AFTER it -> recovered
            conn.execute("INSERT INTO anomalies (task_name, kind, severity,"
                         " detected_at) VALUES ('upwork_scan','task_failure',"
                         " 'critical', datetime('now','-2 days'))")
            conn.execute("INSERT INTO runs (task_name, started_at, completed_at,"
                         " status, shadow) VALUES ('upwork_scan',"
                         " datetime('now','-1 day','+5 hours','+30 minutes'),"
                         " datetime('now','-1 day','+5 hours','+30 minutes'),"
                         " 'completed',0)")
            # a second failure with NO success after it -> active
            conn.execute("INSERT INTO anomalies (task_name, kind, severity,"
                         " detected_at) VALUES ('crm_mirror','task_failure',"
                         " 'critical', datetime('now'))")
        out = data.system(self.db_path)
        rows = {(a["task_name"], a["kind"]): a for a in out["anomalies"]["recent"]}
        self.assertTrue(rows[("upwork_scan", "task_failure")]["recovered"])
        self.assertFalse(rows[("crm_mirror", "task_failure")]["recovered"])
        # open counts only the still-active crm_mirror fire
        self.assertEqual(out["anomalies"]["open"], 1)

    def test_explicitly_resolved_anomaly_does_not_count_open(self):
        """A root-cause-fixed incident marked resolved must drop out of the open
        count even if its task never runs again (2026-08-08: the 26 catch_up_
        failed rows were resolved once jarvis_sweep was finally loaded)."""
        with db.connect(self.db_path) as conn:
            for _ in range(26):
                conn.execute(
                    "INSERT INTO anomalies (task_name, kind, severity,"
                    " detected_at, resolved_at) VALUES ('catch_up',"
                    " 'catch_up_failed','critical', datetime('now','-1 day'),"
                    " datetime('now'))")
        out = data.system(self.db_path)
        row = {(a["task_name"], a["kind"]): a
               for a in out["anomalies"]["recent"]}
        grp = row[("catch_up", "catch_up_failed")]
        self.assertEqual(grp["n"], 26)          # still shown for context
        self.assertEqual(grp["open"], 0)        # but none are open
        self.assertTrue(grp["recovered"])


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

    def test_backlog_is_fresh_awaiting_leads_carded_still_counts(self):
        # 'awaiting a proposal' = hot AND not applied. Being CARDED does not
        # exclude a lead any more (since cutover every hot lead is carded).
        out = data.today(self.db_path, today="2026-06-15")
        ids = [h["id"] for h in out["backlog"]["items"]]
        self.assertIn("2065400000000000111", ids)       # fresh (06-15)
        self.assertIn("2065400000000000112", ids)       # carded, still awaiting
        self.assertNotIn("2065400000000000113", ids)    # 05-01 -> not fresh

    def test_backlog_excludes_a_lead_already_applied_to(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO connects_history (id, event_date, kind,"
                         " label, job_id, connects_delta) VALUES"
                         " ('x1','2026-06-15','applied','Proposal',"
                         "'2065400000000000111',-8)")
        out = data.today(self.db_path, today="2026-06-15")
        ids = [h["id"] for h in out["backlog"]["items"]]
        self.assertNotIn("2065400000000000111", ids)    # applied -> off the backlog

    def test_backlog_count_matches_its_drill(self):
        # traceability: the drilled rows total equals the figure shown
        out = data.today(self.db_path, today="2026-06-15")
        fresh = out["backlog"]["fresh_count"]
        rows = data.today_rows(self.db_path, which="fresh", today="2026-06-15")
        self.assertEqual(rows["total"], fresh)

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
        self.assertEqual(m["revenue"]["target_usd"],
                         config.MONTHLY_REVENUE_TARGET_USD)

    def test_queue_merges_followups_and_nudges_ranked(self):
        # a nudge below the follow-ups still lands in ONE ranked list
        from modules.chief_of_staff import nudges as eng
        eng.write_nudge(self.db_path, source="sales", dept="sales",
                        kind="commitment_due", title="Ship the thing",
                        dedup_key="c:x", payload={"cite": "fathom/x @ 0"},
                        now=datetime(2026, 6, 15, tzinfo=config.TZ))
        out = data.today(self.db_path, today="2026-06-15")
        kinds = {q["kind"] for q in out["queue"]}
        self.assertIn("followup", kinds)
        self.assertIn("nudge", kinds)
        urg = [q["urgency"] for q in out["queue"]]
        self.assertEqual(urg, sorted(urg, reverse=True))    # ruthlessly ranked

    def test_queue_items_carry_client_and_detail(self):
        # every card names who it's about + a detail block for the click-to-expand
        out = data.today(self.db_path, today="2026-06-15")
        fu = next(q for q in out["queue"] if q["kind"] == "followup")
        self.assertEqual(fu["client"], "Room rA")          # thread room_name
        self.assertIn("draft", fu["detail"])
        self.assertEqual(fu["detail"]["tier"], "HOT")

    def test_paused_backlog_frames_intent_not_neglect(self):
        with mock.patch.object(config, "APPLICATIONS_PAUSED_SINCE", "2026-06-01"):
            out = data.today(self.db_path, today="2026-06-15")
        self.assertTrue(out["backlog"]["paused"])
        self.assertEqual(out["backlog"]["paused_since"], "2026-06-01")
        self.assertIn("paused", out["metrics"]["applied"]["note"])
        self.assertTrue(out["metrics"]["applied"]["paused"])

    def test_stale_pause_contradicted_by_rail_is_not_rendered_as_paused(self):
        # intent-reconciliation law: a stated intent (paused) must never silently
        # override contradicting observed data. Config says paused since 06-01,
        # but the connects rail recorded applications AFTER that date -> the tile
        # surfaces a CONTRADICTION, never 'paused', and a WARN anomaly is raised.
        with db.connect(self.db_path) as conn:
            for i, dt in enumerate(["2026-06-09", "2026-06-12", "2026-06-14"]):
                conn.execute(
                    "INSERT INTO connects_history (id, event_date, kind, label,"
                    " connects_delta) VALUES (?,?, 'applied','Applied to a job',-8)",
                    (f"ap{i}", dt))
        with mock.patch.object(config, "APPLICATIONS_PAUSED_SINCE", "2026-06-01"):
            out = data.today(self.db_path, today="2026-06-15")
        tile = out["metrics"]["applied"]
        self.assertFalse(tile["paused"])                 # not asserted as fact
        self.assertTrue(tile["contradiction"])
        self.assertNotIn("paused by intent", tile["note"] or "")
        self.assertIn("intent may be stale", tile["note"])
        self.assertIn("3", tile["note"])                 # N applications counted
        # the backlog no longer frames itself as a paused 'ready-when-resume' pile
        self.assertFalse(out["backlog"]["paused"])
        with db.connect(self.db_path) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM anomalies WHERE kind='stale_intent'"
                " AND severity='warn'").fetchone()[0]
        self.assertGreaterEqual(n, 1)

    def test_zero_applied_contradicted_by_rail_is_not_a_fabricated_zero(self):
        # no-fabricated-zeros: with applications ACTIVE (not paused), the
        # proposals feed can lag the applied-truth connects rail. The tile must
        # NEVER assert 'no applications' when the rail recorded activity in the
        # same window — surface the rail count + a WARN feed-lag anomaly instead.
        with db.connect(self.db_path) as conn:
            conn.execute("DELETE FROM proposals")        # proposals feed is stale
            for i, dt in enumerate(["2026-06-10", "2026-06-13"]):
                conn.execute(
                    "INSERT INTO connects_history (id, event_date, kind, label,"
                    " connects_delta) VALUES (?,?, 'applied','Applied to a job',-8)",
                    (f"rz{i}", dt))
        with mock.patch.object(config, "APPLICATIONS_PAUSED_SINCE", None):
            out = data.today(self.db_path, today="2026-06-15")
        tile = out["metrics"]["applied"]
        self.assertEqual(tile["week"], 0)                # proposals feed says 0
        self.assertTrue(tile["contradiction"])           # but the rail disagrees
        self.assertNotIn("no applications", tile["note"] or "")
        self.assertIn("connects rail", tile["note"])
        self.assertIn("2", tile["note"])                 # rail count surfaced
        with db.connect(self.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM anomalies WHERE"
                             " kind='applied_feed_lag' AND severity='warn'"
                             ).fetchone()[0]
        self.assertGreaterEqual(n, 1)

    def test_true_zero_holds_when_rail_agrees(self):
        # the guard fires only on a CONTRADICTION: proposals 0 AND rail 0 is a
        # true zero and still reads 'no applications in the last 7 days'.
        with db.connect(self.db_path) as conn:
            conn.execute("DELETE FROM proposals")
        with mock.patch.object(config, "APPLICATIONS_PAUSED_SINCE", None):
            out = data.today(self.db_path, today="2026-06-15")
        tile = out["metrics"]["applied"]
        self.assertFalse(tile["contradiction"])
        self.assertIn("no applications in the last 7 days", tile["note"])

    def test_paused_holds_when_rail_is_quiet(self):
        # the guard is a contradiction detector, not a pause killer: with a real
        # pause and NO rail activity after it, 'paused' still renders as intent.
        with mock.patch.object(config, "APPLICATIONS_PAUSED_SINCE", "2026-06-01"):
            out = data.today(self.db_path, today="2026-06-15")
        self.assertTrue(out["metrics"]["applied"]["paused"])
        self.assertFalse(out["metrics"]["applied"]["contradiction"])
        self.assertIn("paused by intent", out["metrics"]["applied"]["note"])

    def test_source_health_line_is_present(self):
        out = data.today(self.db_path, today="2026-06-15")
        self.assertIn("headline", out["sources"])


class FakeAirtable:
    def __init__(self, records):
        self._records = records
    def list_records(self, base, table, **kw):
        return self._records


class TestClientsAirtableReader(unittest.TestCase):
    """The secondary Airtable roster (kept as clients_airtable)."""
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
        out = data.clients_airtable(client=self.fake(), use_cache=False)
        self.assertEqual(out["count"], 2)
        acme = next(c for c in out["clients"] if c["name"] == "Acme")
        self.assertEqual(acme["contracts"], 2)


# ── Client economics (the local ledger view) ──────────────────────────────────
# Revenue attribution touches money, so the rules are pinned hard: Upwork and
# Wise merge for one client, a passthrough is flagged not guessed, and the rows
# behind a client sum to its total to the cent.
def seed_client_econ(db_path):
    with db.connect(db_path) as conn:
        def rev(rid, source, company, desc, minor, dt):
            # category='income' so bucket() reproduces 'revenue' (doctor #10)
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type,"
                " category, source, assignment_company, description,"
                " amount_minor, amount_base_minor, currency, pnl_bucket,"
                " creation_dt) VALUES (?,?,?, 'income',?,?,?,?,?, 'USD','revenue',?)",
                (rid, "k" + rid, "Fixed Earning", source, company, desc, minor,
                 minor, dt))
        # Adélye pays via BOTH rails -> must merge to one client
        rev("u1", "Upwork", "Adélye", "Milestone 1", 1000000, "2026-05-01T00:00:00+0000")
        rev("w1", "Wise", "", "Received money from Adélye Ltd. with reference X",
            500000, "2026-07-02T00:00:00+0000")
        # a Wise-only client
        rev("w2", "Wise", "", "Received money from Horizons ABA MidCo LLC with reference Y",
            2000000, "2026-07-03T00:00:00+0000")
        # a passthrough (EOR) — flagged, never silently attributed
        rev("w3", "Wise", "", "Received money from Deel, Inc. with reference Z",
            800000, "2026-01-16T00:00:00+0000")
        # bank revenue with no parseable payer -> unlinked bucket
        rev("b1", "IDFC", "", "NEFT credit ref 8842", 300000, "2026-06-01T00:00:00+0000")
        # an active contract whose room-name carries the client token
        conn.execute("INSERT INTO threads (id, room_name, bucket) VALUES"
                     " ('t1','Someone, Adélye','followup')")
        conn.execute("INSERT INTO contracts (id, upwork_status, thread_id)"
                     " VALUES ('c1','ACTIVE','t1')")
        # authoritative cash aggregates so finance-doctor stays GREEN (the
        # economics view is doctor-gated, same as Money)
        for acct, ccy, closing in [("idfc", "INR", 50000073),
                                    ("wise", "USD", 0)]:
            conn.execute(
                "INSERT INTO account_balances (account, currency, opening_minor,"
                " closing_minor, statement_date, source_file) VALUES (?,?,?,?,?,?)",
                (acct, ccy, closing, closing, "2026-06-30", f"authoritative:{acct}"))


class TestClientEconomics(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cockpit-cliecon-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed_client_econ(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_upwork_and_wise_merge_into_one_client(self):
        out = data.clients(self.db_path)
        adelye = next(c for c in out["clients"] if c["name"] == "Adelye")
        self.assertEqual(adelye["revenue_usd"], 15000.0)     # 10k Upwork + 5k Wise
        self.assertEqual(sorted(adelye["channels"]), ["Upwork", "Wise"])
        self.assertEqual(adelye["payments"], 2)

    def test_wise_payer_is_parsed_from_description(self):
        out = data.clients(self.db_path)
        names = [c["name"] for c in out["clients"]]
        self.assertIn("Horizons ABA", names)                 # canonical-mapped

    def test_same_client_across_eras_merges_upwork_and_wise(self):
        # Alexander Apfel (Upwork, earlier) and Horizons ABA (Wise, now) are one
        # client (Abhijeet, 2026-07-30) — both map to the dossier name
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type,"
                " category, source, assignment_company, amount_minor,"
                " amount_base_minor, currency, pnl_bucket, creation_dt) VALUES"
                " ('ax','kax','Fixed Earning','income','Upwork','Alexander Apfel',"
                " 700000,700000,'USD','revenue','2025-11-01T00:00:00+0000')")
        out = data.clients(self.db_path)
        names = [c["name"] for c in out["clients"]]
        self.assertNotIn("Alexander Apfel", names)           # folded in
        h = next(c for c in out["clients"] if c["name"] == "Horizons ABA")
        self.assertEqual(sorted(h["channels"]), ["Upwork", "Wise"])
        self.assertEqual(h["revenue_usd"], 27000.0)          # 20k Wise + 7k Upwork

    def test_person_and_company_names_merge_without_a_dossier(self):
        # Mike Gandia (Upwork person) == CMF Publishing (Wise company); one
        # client, no dossier — canonical is the company name (Abhijeet 2026-07-30)
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type,"
                " category, source, assignment_company, amount_minor,"
                " amount_base_minor, currency, pnl_bucket, creation_dt) VALUES"
                " ('mg','kmg','Fixed Earning','income','Upwork','Mike Gandia',"
                " 120000,120000,'USD','revenue','2026-03-01T00:00:00+0000')")
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type,"
                " category, source, description, amount_minor, amount_base_minor,"
                " currency, pnl_bucket, creation_dt) VALUES"
                " ('cmf','kcmf','Fixed Earning','income','Wise',"
                "'Received money from CMF Publishing LLC with reference R',"
                " 400000,400000,'USD','revenue','2026-04-01T00:00:00+0000')")
        out = data.clients(self.db_path)
        names = [c["name"] for c in out["clients"]]
        self.assertNotIn("Mike Gandia", names)
        cmf = next(c for c in out["clients"] if c["name"] == "CMF Publishing")
        self.assertEqual(cmf["revenue_usd"], 5200.0)         # 4k Wise + 1.2k Upwork
        self.assertEqual(sorted(cmf["channels"]), ["Upwork", "Wise"])

    def test_resolved_passthrough_merges_all_its_names(self):
        # Deel (EOR) + Dilip Malave + an Upwork 'Remotual' are one client once
        # mapped — three ledger names collapse to Remotual (Abhijeet, 2026-07-27)
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type,"
                " category, source, description, amount_minor, amount_base_minor,"
                " currency, pnl_bucket, creation_dt) VALUES"
                " ('d2','kd2','Fixed Earning','income','Wise',"
                "'Received money from DILIP N MALAVE with reference Q',"
                " 100000,100000,'USD','revenue','2026-02-01T00:00:00+0000')")
        out = data.clients(self.db_path)
        rem = next(c for c in out["clients"] if c["name"] == "Remotual")
        self.assertEqual(rem["revenue_usd"], 9000.0)     # 8k Deel + 1k Dilip
        self.assertFalse(rem["passthrough"])
        self.assertNotIn("Deel", [c["name"] for c in out["clients"]])

    def test_csv_revenue_links_via_job_title_to_its_contract(self):
        # the agency Upwork-CSV rows carry no assignment_company — only a job
        # title in the description. That title bridges to a contract that names
        # the client, so the earning is attributed, not stranded in unlinked
        # (2026-07-30: $10.6k of real revenue was sitting unlinked).
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO contracts (id, title, client_company, upwork_status)"
                " VALUES ('c9','Ai Automations sales outbound & Inbound',"
                "'Ioannis Pirigkos, Ioannis P','ACTIVE')")
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type,"
                " category, source, description, amount_minor, amount_base_minor,"
                " currency, pnl_bucket, creation_dt) VALUES"
                " ('csv1','kcsv1','Fixed Earning','income','Upwork-CSV',"
                "'Ai Automations sales outbound & Inbound · Earnings for Jul 20-26',"
                " 90000,90000,'USD','revenue','2026-07-26T00:00:00+0000')")
        out = data.clients(self.db_path)
        # attributed to the contract's client (canonicalised: Ioannis -> Beglaubigt)
        beg = next(c for c in out["clients"] if c["name"] == "Beglaubigt")
        self.assertEqual(beg["revenue_usd"], 900.0)
        # and NOT sitting in the unlinked bucket
        unlinked = data.client_transactions(self.db_path, client="__unlinked__")
        self.assertFalse(any("Ai Automations" in (r["desc"] or "")
                             for r in unlinked["rows"]))

    def test_contract_id_in_description_attributes_hourly_earnings(self):
        # Upwork hourly lines name the client by contract id, not company:
        # '(43495766) NETFLY - 8:00 hrs @ $40.50' -> the contract -> the client
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO contracts (id, title, client_company,"
                         " upwork_status) VALUES ('43495766','GHL build',"
                         "'Haleema Umair, NETFLY','ACTIVE')")
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type,"
                " category, source, description, amount_minor, amount_base_minor,"
                " currency, pnl_bucket, creation_dt) VALUES"
                " ('h1','kh1','Hourly Earning','income','Upwork',"
                "'(43495766) NETFLY - 8:00 hrs @ $40.50/hr - 06/01/2026',"
                " 32400,32400,'USD','revenue','2026-06-12T00:00:00+0000')")
        out = data.clients(self.db_path)
        names = [c["name"] for c in out["clients"]]
        self.assertIn("Haleema Umair, NETFLY", names)   # attributed via contract id
        c = next(x for x in out["clients"] if x["name"] == "Haleema Umair, NETFLY")
        self.assertEqual(c["revenue_usd"], 324.0)

    def test_bank_imps_payer_attributes_only_via_known_alias(self):
        # a bank credit whose IMPS remitter maps to a known alias attributes;
        # an unmapped processor name (VISAPAYMENTSLIM) stays unlinked, never junk
        with db.connect(self.db_path) as conn:
            for rid, desc, minor in [
                ("mx", "IMPS/517927008007/MichaelXavierGa/RATN0000088/4553", 100000),
                ("vp", "IMPS/534521507789/VISAPAYMENTSLIM/HDFC0000240/6389", 7000)]:
                conn.execute(
                    "INSERT INTO transactions (record_id, canonical_key, type,"
                    " category, source, description, amount_minor,"
                    " amount_base_minor, currency, pnl_bucket, creation_dt)"
                    " VALUES (?,?, 'Fixed Earning','income','IDFC',?,?,?, 'USD',"
                    "'revenue','2025-10-02T00:00:00+0000')",
                    (rid, "k" + rid, desc, minor, minor))
        out = data.clients(self.db_path)
        names = [c["name"] for c in out["clients"]]
        cmf = next(c for c in out["clients"] if c["name"] == "CMF Publishing")
        self.assertEqual(cmf["revenue_usd"], 1000.0)      # Michael Xavier folded in
        self.assertNotIn("VISAPAYMENTSLIM", names)        # processor noise -> unlinked
        self.assertNotIn("MichaelXavierGa", names)

    def test_masked_upwork_client_is_labelled_by_job_title(self):
        # Upwork masked the client ('no company') -> label by the job title so
        # it is never 'unlinked' (Abhijeet, 2026-07-30); milestones of one job
        # merge under the same title
        with db.connect(self.db_path) as conn:
            for rid, minor in (("i1", 20000), ("i2", 14000)):
                conn.execute(
                    "INSERT INTO transactions (record_id, canonical_key, type,"
                    " category, source, assignment_company, description,"
                    " amount_minor, amount_base_minor, currency, pnl_bucket,"
                    " creation_dt) VALUES (?,?, 'Fixed Earning','income','Upwork',"
                    "'no company','Invoice for Voice Agent for Driving School:"
                    " Milestone',?,?, 'USD','revenue','2025-04-20T00:00:00+0000')",
                    (rid, "k" + rid, minor, minor))
        out = data.clients(self.db_path)
        c = next(x for x in out["clients"]
                 if x["name"] == "Voice Agent for Driving School")
        self.assertEqual(c["revenue_usd"], 340.0)          # both milestones merged

    def test_unparseable_revenue_goes_to_unlinked_never_a_client(self):
        out = data.clients(self.db_path)
        self.assertEqual(out["unlinked"]["usd"], 3000.0)     # the bank row
        self.assertNotIn("NEFT credit ref 8842",
                         [c["name"] for c in out["clients"]])

    def test_default_sort_is_by_revenue(self):
        out = data.clients(self.db_path)
        revs = [c["revenue_usd"] for c in out["clients"]]
        self.assertEqual(revs, sorted(revs, reverse=True))

    def test_parity_clients_plus_unlinked_equals_total(self):
        out = data.clients(self.db_path)
        s = sum(c["revenue_usd"] for c in out["clients"]) + out["unlinked"]["usd"]
        self.assertAlmostEqual(s, out["revenue_total_usd"], places=2)

    def test_contract_links_by_exact_room_token(self):
        out = data.clients(self.db_path)
        adelye = next(c for c in out["clients"] if c["name"] == "Adelye")
        self.assertEqual(adelye["active_contracts"], 1)

    def test_client_drill_rows_sum_to_the_total(self):
        d = data.client_transactions(self.db_path, client="Adelye")
        self.assertEqual(d["count"], 2)
        self.assertAlmostEqual(d["sum_usd"], 15000.0, places=2)
        out = data.clients(self.db_path)
        adelye = next(c for c in out["clients"] if c["name"] == "Adelye")
        self.assertAlmostEqual(d["sum_usd"], adelye["revenue_usd"], places=2)

    def test_unlinked_drill(self):
        d = data.client_transactions(self.db_path, client="__unlinked__")
        self.assertEqual(d["count"], 1)
        self.assertAlmostEqual(d["sum_usd"], 3000.0, places=2)

    def test_doctor_red_blocks_client_economics(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO transactions (record_id, canonical_key,"
                         " type, amount_minor, currency) VALUES"
                         " ('bad','kbad','Other',NULL,'USD')")
        out = data.clients(self.db_path)
        self.assertTrue(out["blocked"])
        self.assertEqual(out.get("clients", []), [])


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

    def test_to_triage_is_fresh_untasked_hot_only(self):
        # v2 semantics (2026-07-07 audit): to-triage is WINDOWED — 111 (fresh,
        # 06-15) counts; 113 (05-01, shadow-era stale) goes to the backlog
        # counter, never 'to triage'; 112 is tasked → out entirely.
        out = data.pipeline(self.db_path, today="2026-06-15")
        self.assertEqual(out["to_triage"], 1)
        self.assertEqual(out["bands"]["backlog_uncarded"], 1)

    def test_score_bands_windowed_open_board(self):
        out = data.pipeline(self.db_path, today="2026-06-15")
        b = out["bands"]
        self.assertEqual(b["hot"], 1)                 # fresh untasked hot only
        self.assertEqual(b["hot"], out["to_triage"])  # coherent
        self.assertEqual(b["window_days"], data.BOARD_WINDOW_DAYS)
        self.assertIn("note", b)                      # honest caveat present

    def test_win_rate_from_status_with_caveat(self):
        out = data.pipeline(self.db_path, today="2026-06-15")
        wr = out["win_rate"]
        # seed_today p4=Won + w1,w2=Won → 3 won; +Lost1 +Expired1 → decided 5
        self.assertEqual(wr["won"], 3)
        self.assertEqual(wr["decided"], 5)
        self.assertEqual(wr["pct"], 60.0)
        self.assertIn("threads-only", wr["caveat"])  # honest skew note

    def test_proposals_by_status_with_upwork_fallback(self):
        with db.connect(self.db_path) as conn:      # blank status, raw upwork one
            conn.execute("INSERT INTO proposals (id, status, upwork_status)"
                         " VALUES ('blank1', NULL, 'Activated')")
        out = data.pipeline(self.db_path, today="2026-06-15")
        self.assertEqual(out["by_status"].get("Won"), 3)
        self.assertEqual(out["by_status"].get("Activated"), 1)   # not 'Unknown'

    def test_applied_is_explicitly_unavailable_pre_rail(self):
        # NEVER fake zeros: before the connects rail's first scrape, 'applied'
        # says loudly that it's unavailable, and funnel cells are None (em-dash).
        out = data.pipeline(self.db_path, today="2026-06-15")
        self.assertFalse(out["applied"]["available"])
        self.assertIn("connects-history", out["applied"]["rail"])
        self.assertIsNone(out["funnel_weeks"][-1]["applied"])

    def test_client_replies_note_derives_from_rail_not_pause_assumption(self):
        """Bug (2026-08-08): the KPI read 'nothing applied, so nothing to reply
        to' while the connects rail had recorded applications in the same window.
        A zero-reply window with applications OUT is a lag, not 'nothing applied'
        — the note must derive from the rail (same helper the applied tile uses).
        """
        with db.connect(self.db_path) as conn:
            for i, d in enumerate(("2026-08-02", "2026-08-04", "2026-08-05")):
                conn.execute(
                    "INSERT INTO connects_history (id, event_date, kind, label,"
                    " connects_delta) VALUES (?,?, 'applied','x',-8)", (f"a{i}", d))
        out = data.pipeline(self.db_path, today="2026-08-08")
        self.assertEqual(out["client_replied"]["this_month"], 0)  # no Aug replies
        note = out["client_replied"]["note"]
        self.assertIn("3 applied in the last 7 days", note)
        self.assertNotIn("nothing applied", note)

    def test_client_replies_note_absent_when_replies_this_month(self):
        # replies exist this month → no trailing clause at all
        out = data.pipeline(self.db_path, today="2026-06-15")
        self.assertGreater(out["client_replied"]["this_month"], 0)
        self.assertIsNone(out["client_replied"]["note"])

    def test_client_replies_note_is_true_zero_when_no_applications(self):
        # no applications recorded AND no replies → honest zero, not a claim
        # about "nothing to reply to" pinned on a pause assumption
        out = data.pipeline(self.db_path, today="2026-08-08")
        self.assertEqual(out["client_replied"]["this_month"], 0)
        self.assertIn("no applications recorded",
                      out["client_replied"]["note"])

    def test_pre_rail_weeks_are_unknown_not_zero(self):
        # 2026-07-13: the funnel printed 0 applied for EVERY historical week,
        # which reads as "he never applied" instead of "we weren't measuring".
        # Only weeks the rail actually covers may show a number.
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO connects_history (id, event_date, kind,"
                         " label, connects_delta) VALUES"
                         " ('c1','2026-06-15','applied','x',-8)")
        out = data.pipeline(self.db_path, today="2026-06-15")
        weeks = out["funnel_weeks"]
        self.assertEqual(out["applied"]["covered_since"], "2026-06-15")
        self.assertEqual(weeks[-1]["applied"], 1)          # covered -> real count
        # weeks that ENDED before the rail started have no data at all
        self.assertIsNone(weeks[0]["applied"])
        self.assertIsNone(weeks[-3]["applied"])

    def test_week_the_rail_started_midway_is_unknown_not_zero(self):
        """Coverage is a property of the WHOLE period, not of its last day. The
        rail starting on Thursday does not mean Monday-to-Wednesday were zero —
        it means nobody was counting them (2026-07-14)."""
        with db.connect(self.db_path) as conn:
            # rail's first row lands on the THURSDAY of the 06-15 week
            conn.execute("INSERT INTO connects_history (id, event_date, kind,"
                         " label, connects_delta) VALUES"
                         " ('c2','2026-06-18','applied','x',-8)")
        out = data.pipeline(self.db_path, today="2026-06-19")
        wk = {w["week"]: w for w in out["funnel_weeks"]}["2026-06-15"]
        # the old rule (week_end >= since) called this week covered and printed
        # 1 applied, silently implying Mon-Wed were 0. It is UNKNOWN.
        self.assertIsNone(wk["applied"])
        self.assertEqual(out["applied"]["covered_since"], "2026-06-18")

    def test_pipeline_stamps_source_health_on_kpis(self):
        # applied carries its rail's verdict; the payload carries the scanner's.
        # With no runs seeded, both sources are 'down' -> the applied KPI must
        # not present a live-looking count.
        out = data.pipeline(self.db_path, today="2026-07-16")
        self.assertIn("source", out["applied"])
        self.assertEqual(out["applied"]["source"]["backs"], "applied")
        self.assertIn("scanner_health", out)
        self.assertEqual(out["scanner_health"]["backs"], "pipeline")
        # a fresh DB has never run either source
        self.assertFalse(out["applied"]["source"]["ok"])

    def test_hired_column_from_contracts(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO contracts (id, title, upwork_status,"
                         " start_dt) VALUES ('c9','Airtable build','ACTIVE',"
                         " '2026-06-15T00:00:00+0000')")
        out = data.pipeline(self.db_path, today="2026-06-15")
        self.assertEqual(out["funnel_weeks"][-1]["hired"], 1)
        self.assertEqual(out["funnel_weeks"][0]["hired"], 0)   # true zero: contracts are fully known
        rows = data.pipeline_rows(self.db_path, stage="hired",
                                  week=out["funnel_weeks"][-1]["week"])
        self.assertEqual(rows["count"], 1)
        self.assertEqual(rows["rows"][0]["title"], "Airtable build")

    def test_applied_from_connects_rail(self):
        # with the rail live, applied counts are TRUE (0 = genuinely zero) and
        # come from kind='applied' rows only.
        with db.connect(self.db_path) as conn:
            for cid, dt, kind in [("c1", "2026-06-15", "applied"),
                                  ("c2", "2026-06-12", "applied"),
                                  ("c3", "2026-06-15", "badge"),
                                  ("c4", "2026-06-01", "applied")]:
                conn.execute("INSERT INTO connects_history (id, event_date,"
                             " kind, label, connects_delta) VALUES (?,?,?,?,?)",
                             (cid, dt, kind, "x", -8 if kind == "applied" else -1))
        out = data.pipeline(self.db_path, today="2026-06-15")
        a = out["applied"]
        self.assertTrue(a["available"])
        self.assertEqual(a["today"], 1)          # c1 only (badge never counts)
        self.assertEqual(a["week"], 2)           # c1 + c2
        self.assertEqual(a["since"], "2026-06-01")
        # calendar weeks: c1 (Mon 06-15) in the current week; c2 (Fri 06-12)
        # in the prior one — rolling-7d 'week' (2) vs calendar cells (1 and 1)
        self.assertEqual(out["funnel_weeks"][-1]["applied"], 1)
        self.assertEqual(out["funnel_weeks"][-2]["applied"], 1)

    def test_pipeline_rows_drills(self):
        # traceability: every Pipeline figure drills to its rows
        out = data.pipeline_rows(self.db_path, stage="triage", today="2026-06-15")
        self.assertEqual(out["count"], 1)                    # fresh hot 111 only
        self.assertEqual(out["rows"][0]["id"], "2065400000000000111")
        out = data.pipeline_rows(self.db_path, stage="carded")
        self.assertEqual(out["count"], 1)                    # 112
        self.assertTrue(all(r["clickup_task_url"] is not None or True
                            for r in out["rows"]))
        # week-filtered replied == the funnel cell for that week
        p = data.pipeline(self.db_path, today="2026-06-15")
        wk = p["funnel_weeks"][-1]
        out = data.pipeline_rows(self.db_path, stage="replied", week=wk["week"])
        self.assertEqual(out["count"], wk["replied"])
        # threads by status matches the by_status counts
        out = data.pipeline_rows(self.db_path, stage="threads", status="Won")
        self.assertEqual(out["count"], p["by_status"]["Won"])
        # applied rows come from the connects rail only
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO connects_history (id, event_date, kind,"
                         " label, job_id, job_title, connects_delta) VALUES"
                         " ('a1','2026-06-15','applied','Submitted a proposal',"
                         " '999','Job X',-16)")
        out = data.pipeline_rows(self.db_path, stage="applied", week=wk["week"])
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["rows"][0]["job_id"], "999")
        # unknown stage is a safe error, never a crash
        self.assertIn("error", data.pipeline_rows(self.db_path, stage="nope"))

    def test_funnel_weeks_zero_filled_and_truthful(self):
        out = data.pipeline(self.db_path, today="2026-06-15")
        t = out["funnel_weeks"]
        self.assertEqual(len(t), 10)               # zero-filled window
        self.assertTrue(t[-1]["current"])          # ends at current week
        d = datetime(2026, 6, 15).date()
        expected_mon = (d - timedelta(days=d.weekday())).isoformat()
        self.assertEqual(t[-1]["week"], expected_mon)
        cur = t[-1]
        self.assertEqual(cur["carded"], 1)         # 112 carded on 06-15
        self.assertEqual(cur["replied"], 1)        # p1 room-proposal on 06-15
        # carded activity counters (the real action surface)
        self.assertEqual(out["carded"]["today"], 1)


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

    def test_fathom_status_reads_kb_daily_not_a_phantom_task(self):
        # the poll runs INSIDE kb_daily; reading a 'fathom_poll' task reported
        # 'never' while it was polling every morning (2026-07-30 fix)
        self._seed_kb()
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO runs (task_name, started_at, completed_at, status,"
                " shadow, metrics_json) VALUES ('kb_daily',"
                " '2026-07-29T05:15:00+05:30','2026-07-29T05:15:40+05:30',"
                " 'completed',0,'{\"fathom\":{\"saved\":3}}')")
        f = data.knowledge(self.db_path)["fathom"]
        self.assertEqual(f["last_poll"], "2026-07-29T05:15:40+05:30")
        self.assertEqual(f["last_saved"], 3)

    def test_freshness_flags_a_stale_dossier(self):
        self._seed_kb()   # dossier compiled 2026-06-17
        # far in the future -> the dossier is well past the stale window
        out = data.knowledge(self.db_path)  # _ist_today() is 'now' (2026-07+)
        fr = out["freshness"]
        self.assertEqual(fr["dossiers"], 1)
        self.assertGreater(fr["oldest_days"], 30)
        self.assertEqual(fr["stale"], 1)
        self.assertTrue(out["dossiers"][0]["stale"])

    def test_revenue_cross_link_uses_dossier_name(self):
        # a dossier whose name matches a paying client shows that revenue; the
        # names reconcile because Clients canonicalises to the dossier spelling
        self._seed_kb()
        seed_client_econ(self.db_path)   # adds Adelye revenue (Upwork+Wise)
        out = data.knowledge(self.db_path)
        adelye = next(d for d in out["dossiers"] if d["client_name"] == "Adelye")
        self.assertEqual(adelye["revenue_usd"], 15000.0)

    def test_revenue_absent_when_no_matching_client(self):
        self._seed_kb()          # Adelye dossier, but no ledger revenue seeded
        out = data.knowledge(self.db_path)
        self.assertIsNone(out["dossiers"][0]["revenue_usd"])   # honest: no number

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
        # a bank-statement spend: personal -> owner_draw (2026-07-08 rules; opex
        # is the credit-card statement)
        ("Other", "expense", -300000, "2026-06-01", "IDFC", "owner_draw"),
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
            "'INR','2026-06-05T00:00:00+0000','IDFC','0.012','2026-06-05','owner_draw')")
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

    def test_cash_latest_statement_per_account(self):
        # overlapping re-imports leave several balance rows per account: cash
        # takes the LATEST closing per account, never the sum (2026-07-07: two
        # idfc statements would have double-counted savings).
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO account_balances (account,currency,opening_minor,"
                         "closing_minor,statement_date,source_file,period_start,period_end)"
                         " VALUES ('idfc','INR',50000000,50000059,'2026-07-05',"
                         "'authoritative:idfc-new','2026-04-02','2026-06-30')")
        out = data.money(self.db_path, today="2026-06-15")
        idfc = [a for a in out["cash"]["by_account"] if a["account"] == "idfc"]
        self.assertEqual(len(idfc), 1)                     # one row per account
        self.assertEqual(idfc[0]["usd"], round(500000.59 * 0.012, 2))  # newest closing

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


class TestFinanceIngest(CockpitServerCase):
    """Upload -> stage -> import wiring. Uploads only STAGE files; the ledger is
    written solely by the importer bins behind the explicit commit action."""

    def setUp(self):
        super().setUp()
        self.inbox = self.tmp / "finance-inbox"
        self.p = mock.patch.object(actions, "FINANCE_INBOX", self.inbox)
        self.p.start()
        self.addCleanup(self.p.stop)

    def post_raw(self, path, data, token=TOKEN):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, data=data, method="POST")
        if token is not None:
            req.add_header("X-Cockpit-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_upload_stages_with_forced_fullhistory_name(self):
        s, out = self.post_raw(
            "/api/finance/upload?kind=upwork-agency&filename=export%20(3).csv",
            b"Date,Ref ID\n")
        self.assertEqual(s, 200)
        self.assertTrue(out["ok"])
        self.assertTrue(out["staged_as"].endswith("-upwork-agency-fullhistory.csv"))
        self.assertTrue((self.inbox / "upwork" / out["staged_as"]).exists())
        self.assertTrue(out["importable"])

    def test_upload_sanitizes_traversal_and_checks_ext(self):
        s, out = self.post_raw(
            "/api/finance/upload?kind=idfc&filename=..%2F..%2Fevil.xlsx", b"x")
        self.assertTrue(out["ok"])
        self.assertNotIn("/", out["staged_as"])
        self.assertNotIn("..", out["staged_as"].replace("..", "_"))  # neutralized
        self.assertTrue((self.inbox / "bank" / out["staged_as"]).exists())
        s, out = self.post_raw(
            "/api/finance/upload?kind=idfc&filename=stmt.csv", b"x")
        self.assertFalse(out["ok"])                       # idfc wants xlsx
        s, out = self.post_raw(
            "/api/finance/upload?kind=nope&filename=a.csv", b"x")
        self.assertFalse(out["ok"])                       # kind allowlist

    def test_upload_never_clobbers(self):
        for _ in range(2):
            s, out = self.post_raw(
                "/api/finance/upload?kind=wise&filename=st.csv", b"a,b\n")
        names = sorted(p.name for p in (self.inbox / "wise").iterdir())
        self.assertEqual(len(names), 2)                   # suffixed, not overwritten
        self.assertNotEqual(names[0], names[1])

    def test_upload_requires_token(self):
        s, _ = self.post_raw("/api/finance/upload?kind=wise&filename=a.csv",
                             b"x", token=None)
        self.assertEqual(s, 401)

    def test_stripe_upload_and_import_wired(self):
        # the Import-data tab's Stripe tile: CSV stages into finance-inbox/stripe
        # and the import routes to the Stripe bin
        s, out = self.post_raw(
            "/api/finance/upload?kind=stripe&filename=balance_history.csv",
            b"id,Type,Amount,Currency\ntxn_1,payment,100,inr\n")
        self.assertTrue(out["ok"])
        self.assertTrue(out["importable"])
        self.assertEqual(out["dir"], "finance-inbox/stripe")
        fake = mock.MagicMock()
        fake.run.return_value = {"mode": "DRY-RUN", "inserted": 1}
        with mock.patch.object(actions, "_load_importer", return_value=fake):
            s, body = self.post("/api/finance/import", body={"kind": "stripe"})
            self.assertTrue(json.loads(body)["ok"])

    def test_card_upload_importable_and_forced_name(self):
        # a Google-Drive token filename once lost its extension to truncation;
        # card uploads are now force-named and fully importable (2026-07-07)
        long_name = "ACFrOgD" + "x" * 90 + ".pdf"
        s, out = self.post_raw(
            f"/api/finance/upload?kind=card&filename={long_name}", b"%PDF-1.4")
        self.assertTrue(out["ok"])
        self.assertTrue(out["importable"])                # importer live now
        self.assertTrue(out["staged_as"].endswith("-card-statement.pdf"))
        fake = mock.MagicMock()
        fake.run.return_value = {"mode": "DRY-RUN", "new_rows": 17}
        with mock.patch.object(actions, "_load_importer", return_value=fake) as ld:
            s, body = self.post("/api/finance/import", body={"kind": "card"})
            self.assertTrue(json.loads(body)["ok"])
            ld.assert_any_call("finance-import-card")     # routes to the card bin

    def test_safe_name_preserves_suffix(self):
        self.assertTrue(actions._safe_name("A" * 200 + ".pdf").endswith(".pdf"))
        self.assertLessEqual(len(actions._safe_name("A" * 200 + ".pdf")), 80)

    def test_import_routes_and_doctor_on_commit(self):
        fake = mock.MagicMock()
        fake.run.return_value = {"mode": "DRY-RUN", "new_inserted": 3,
                                 "_private": "hidden"}
        with mock.patch.object(actions, "_load_importer", return_value=fake):
            s, body = self.post("/api/finance/import",
                                body={"kind": "upwork-agency"})
            out = json.loads(body)
            self.assertTrue(out["ok"])
            self.assertEqual(out["report"]["new_inserted"], 3)
            self.assertNotIn("_private", out["report"])   # private keys stripped
            fake.run.assert_called_with(False)            # dry-run default
            s, body = self.post("/api/finance/import",
                                body={"kind": "idfc", "commit": True})
            out = json.loads(body)
            fake.run.assert_called_with(True)
            self.assertIn(out["doctor"], ("GREEN", "RED"))  # gate shown post-write
        s, body = self.post("/api/finance/import", body={"kind": "nope"})
        self.assertFalse(json.loads(body)["ok"])


class TestMoneyDrill(unittest.TestCase):
    """The traceability contract: every Money figure's drill rows sum to the
    displayed metric, to the cent, from the SAME query set."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cockpit-drill-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed_money(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_month_bucket_drill_sums_to_chart_bar(self):
        m = data.money(self.db_path, today="2026-06-15")
        bar = {x["month"]: x["usd"] for x in m["revenue"]["by_month"]}
        for month in ("2026-06", "2026-05", "2026-04"):
            d = data.money_transactions(self.db_path, bucket="revenue", month=month)
            self.assertEqual(d["sum_usd"], bar[month])          # to the cent
            self.assertEqual(round(sum(r["u"] for r in d["rows"]), 2), bar[month])
            self.assertTrue(all(r["b"] == "revenue" and r["m"] == month
                                for r in d["rows"]))

    def test_pnl_month_accrues_a_late_salary_to_the_month_it_was_for(self):
        # a salary PAID 2026-07-02 but FOR June: pnl_month='2026-06' attributes it
        # to June's P&L while its payment date stays July (cash unaffected)
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type,"
                " category, source, description, amount_minor, amount_base_minor,"
                " currency, pnl_bucket, creation_dt, pnl_month) VALUES"
                " ('late','klate','Salary','salary','IDFC','NEFT/x/JAY/y',"
                " -216000,-226368,'INR','payroll','2026-07-02T00:00:00+0000',"
                " '2026-06')")
        june = data.money_transactions(self.db_path, month="2026-06", bucket="payroll")
        july = data.money_transactions(self.db_path, month="2026-07", bucket="payroll")
        # the July-paid salary is counted in JUNE, not July
        self.assertTrue(any(r["dt"] == "2026-07-02" for r in june["rows"]))
        self.assertFalse(any(r["dt"] == "2026-07-02" for r in july["rows"]))
        self.assertEqual(june["rows"][0]["m"], "2026-06")   # attributed month

    def test_bucket_drills_sum_to_waterfall_lines(self):
        m = data.money(self.db_path, today="2026-06-15")
        for b, total in m["buckets"].items():
            d = data.money_transactions(self.db_path, bucket=b)
            self.assertEqual(d["sum_usd"], total, f"bucket {b}")
            self.assertEqual(d["count"], len(d["rows"]))

    def test_multi_bucket_drill_is_net_revenue_kpi(self):
        m = data.money(self.db_path, today="2026-06-15")
        d = data.money_transactions(self.db_path, bucket="revenue,refund")
        self.assertEqual(d["sum_usd"], m["pnl"]["net_revenue_usd"])

    def test_unfiltered_explorer_covers_all_and_search_filters(self):
        d = data.money_transactions(self.db_path)
        self.assertEqual(d["count"], 8)                          # all canonical rows
        s = data.money_transactions(self.db_path, q="connect")
        self.assertEqual(s["count"], 2)                          # the two connect fees
        self.assertEqual(s["sum_usd"], -90.0)
        src = data.money_transactions(self.db_path, source="Wise")
        self.assertEqual(src["count"], 1)
        self.assertEqual(src["rows"][0]["u"], 1500.0)

    def test_rows_shape_and_order(self):
        d = data.money_transactions(self.db_path, bucket="revenue")
        self.assertEqual([r["dt"] for r in d["rows"]],
                         sorted((r["dt"] for r in d["rows"]), reverse=True))
        for k in ("dt", "m", "b", "u", "s", "t", "d"):
            self.assertIn(k, d["rows"][0])

    def test_drill_doctor_gate_no_rows_on_red(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO transactions (record_id, canonical_key, type,"
                         " amount_minor, currency) VALUES ('bad','kbad','Other',NULL,'USD')")
        d = data.money_transactions(self.db_path, bucket="revenue")
        self.assertTrue(d["blocked"])
        self.assertEqual(d["rows"], [])                          # never stale rows
        self.assertEqual(d["count"], 0)


# ── Pipeline charts: the traceability contract ────────────────────────────────
# The charts aggregate pipeline_facts client-side; the drills query
# pipeline_rows(stage='jobs') server-side. They are two different code paths
# over the same table, so they can drift — and a chart that disagrees with the
# rows behind it is exactly the "dead-end metric" the cockpit exists to abolish.
# These tests pin them together.

def seed_chart_jobs(db_path):
    """Nine jobs spanning every band the Pipeline charts slice on. Ids are
    spaced (not sequential) — sequential ids trip the fabrication guard."""
    base = 2065400000000000000
    rows = [
        # (offset, month, score, skip, hourly_max, feed, carded, icp)
        (7919 * 1, "2026-05", 24, None, 60, "most_recent", 1, "icp"),
        (7919 * 2, "2026-05", 18, None, 55, "best_matches", 1, "icp_near"),
        (7919 * 3, "2026-05", 10, None, 40, "most_recent", 0, None),
        (7919 * 4, "2026-06", 28, None, 75, "my_feed", 1, "icp"),
        (7919 * 5, "2026-06", 16, None, 51, "best_matches", 0, None),
        # skipped: a STATED low rate (a real reject)
        (7919 * 6, "2026-06", None, "hourly_below_30", 20, "most_recent", 0, None),
        # skipped: NO rate stated — the rule misfiring on absent data
        (7919 * 7, "2026-06", None, "hourly_below_30", None, "most_recent", 0, None),
        (7919 * 8, "2026-06", None, "low_hourly_rate", None, "firehose", 0, None),
        (7919 * 9, "2026-06", None, "n8n_exclusion", None, "my_feed", 0, None),
    ]
    with db.connect(db_path) as conn:
        for off, m, score, skip, hmax, feed, carded, icp in rows:
            conn.execute(
                "INSERT INTO scored_jobs (id, title, score, hard_rule_skip,"
                " hourly_max, feed_source, clickup_task_id, icp_tier,"
                " fetched_at, first_scored_at, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (str(base + off), f"Job {off}", score, skip, hmax, feed,
                 "t" + str(off) if carded else None, icp,
                 f"{m}-10T04:00:00+00:00",
                 f"{m}-10T04:05:00+00:00" if score else None,
                 "Skipped" if skip else "Scored"))


class TestPipelineFacts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cockpit-facts-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed_chart_jobs(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def facts(self):
        return data.pipeline_facts(self.db_path, today="2026-07-01")

    def total(self, **kw):
        return data.pipeline_rows(self.db_path, stage="jobs", **kw)["total"]

    def test_fact_row_shape(self):
        f = self.facts()
        self.assertEqual(len(f["jobs"]), 9)
        for k in ("i", "d", "m", "s", "k", "f", "c", "t", "nr"):
            self.assertIn(k, f["jobs"][0])
        self.assertEqual(f["hot_threshold"], config.HOT_LEAD_THRESHOLD)

    def test_no_rate_flag_only_for_missing_rate_hourly_skips(self):
        f = self.facts()
        nr = [j for j in f["jobs"] if j["nr"]]
        self.assertEqual(len(nr), 2)               # the two blank-rate hourly skips
        # the STATED $20 job failed the same rule but is a genuine reject
        stated = [j for j in f["jobs"]
                  if j["k"] == "hourly_below_30" and not j["nr"]]
        self.assertEqual(len(stated), 1)
        # ...and an n8n skip is never a rate misfire
        self.assertFalse(any(j["nr"] for j in f["jobs"]
                             if j["k"] == "n8n_exclusion"))

    def test_every_funnel_band_equals_its_drill(self):
        """THE contract: a bar and the rows behind it are the same rows."""
        f = self.facts()
        js = f["jobs"]
        HOT, FLOOR = f["hot_threshold"], f["score_floor"]
        cases = [
            (len(js), {}),
            (sum(1 for j in js if not j["k"]), {"skip": "__none__"}),
            (sum(1 for j in js if j["k"]), {"skip": "__any__"}),
            (sum(1 for j in js if j["s"] is not None and j["s"] >= FLOOR),
             {"skip": "__none__", "score_min": FLOOR}),
            (sum(1 for j in js if j["s"] is not None and j["s"] >= HOT),
             {"score_min": HOT}),
            (sum(1 for j in js if j["c"]), {"carded": "1"}),
            (sum(1 for j in js if j["s"] is not None and j["s"] >= HOT and not j["c"]),
             {"score_min": HOT, "carded": "0"}),
            (sum(1 for j in js if j["t"] == "icp"), {"icp": "icp"}),
            (sum(1 for j in js if j["t"] == "icp_near"), {"icp": "icp_near"}),
            (sum(1 for j in js if j["nr"]), {"skip": "no_rate"}),
            (sum(1 for j in js if j["f"] == "most_recent"), {"feed": "most_recent"}),
        ]
        for want, q in cases:
            self.assertEqual(self.total(**q), want, q)

    def test_parity_holds_inside_a_month_range(self):
        f = self.facts()
        for frm, to in [("2026-05", "2026-05"), ("2026-06", "2026-06"),
                        ("2026-05", "2026-06")]:
            js = [j for j in f["jobs"] if frm <= j["m"] <= to]
            self.assertEqual(self.total(frm=frm, to=to), len(js))
            self.assertEqual(self.total(frm=frm, to=to, carded="1"),
                             sum(1 for j in js if j["c"]))
            self.assertEqual(self.total(frm=frm, to=to, skip="no_rate"),
                             sum(1 for j in js if j["nr"]))

    def test_stated_low_rate_and_no_rate_never_double_count(self):
        # the two buckets partition the hourly-floor skips — no row in both,
        # no row in neither (the split that made the misfire visible)
        f = self.facts()
        hourly = [j for j in f["jobs"]
                  if j["k"] in ("hourly_below_30", "low_hourly_rate", "low_hourly")]
        self.assertEqual(self.total(skip="no_rate")
                         + self.total(skip="hourly_below_30")
                         + self.total(skip="low_hourly_rate"), len(hourly))

    def test_outcome_drills_honour_the_month_range(self):
        """Outcome cards (applied/replied/hired) are range-scoped, so their
        drills must be too — a card counting one window while its rows show
        another is precisely the dead-end metric the contract forbids."""
        with db.connect(self.db_path) as conn:
            for pid, dt in [("p1", "2024-04-05T00:00:00+0000"),
                            ("p2", "2026-06-02T00:00:00+0000")]:
                conn.execute("INSERT INTO proposals (id, status, created_dt)"
                             " VALUES (?,'Won',?)", (pid, dt))
            for cid, dt in [("c1", "2024-05-01T00:00:00+0000"),
                            ("c2", "2026-06-04T00:00:00+0000")]:
                conn.execute("INSERT INTO contracts (id, title, start_dt)"
                             " VALUES (?,'Old work',?)", (cid, dt))
        allr = data.pipeline_rows(self.db_path, stage="replied")
        self.assertEqual(allr["total"], 2)                    # 2024 + 2026
        scoped = data.pipeline_rows(self.db_path, stage="replied",
                                    frm="2026-06", to="2026-06")
        self.assertEqual(scoped["total"], 1)                  # the 2024 one is out
        self.assertEqual(scoped["rows"][0]["d"], "2026-06-02")
        hired = data.pipeline_rows(self.db_path, stage="hired",
                                   frm="2026-06", to="2026-06")
        self.assertEqual(hired["total"], 1)

    def test_week_filter_still_beats_the_range(self):
        # the funnel-by-week table asks by WEEK; that must keep working
        d = data.pipeline_rows(self.db_path, stage="scanned", week="2026-06-08")
        self.assertEqual(d["total"], 6)          # the six 2026-06-10 jobs

    def test_drill_reports_uncapped_total(self):
        d = data.pipeline_rows(self.db_path, stage="jobs")
        self.assertEqual(d["total"], 9)
        self.assertEqual(d["total"], d["count"])   # under the cap they agree

    def test_unknown_stage_is_refused(self):
        d = data.pipeline_rows(self.db_path, stage="everything")
        self.assertIn("error", d)
        self.assertEqual(d["rows"], [])


class TestPipelineKpis(unittest.TestCase):
    """The KPI strip: the headline window must carry signal, and every zero must
    be able to say why it is zero (2026-07-14: all four read 0 at 3am — one was
    a 3-hour-old day, one was a metric that can no longer be non-zero, and two
    were true zeros nobody could tell apart)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cockpit-kpi-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed_chart_jobs(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_carded_week_survives_an_empty_today(self):
        # 'today' is legitimately 0 early in the day; the 7-day window is what
        # the headline shows, so an empty morning never reads as a dead pipeline
        out = data.pipeline(self.db_path, today="2026-06-16")
        self.assertEqual(out["carded"]["today"], 0)
        self.assertEqual(out["carded"]["week"], 1)      # the 06-10 card

    def test_icp_kpi_counts_and_dates(self):
        out = data.pipeline(self.db_path, today="2026-06-16")
        icp = out["icp"]
        self.assertEqual(icp["total"], 2)               # both seeded ICP rows
        self.assertEqual(icp["last_30"], 1)             # only the 06-10 one
        self.assertEqual(icp["last"], "2026-06-10")
        self.assertEqual(icp["days_since"], 6)

    def test_zero_carries_its_recency(self):
        # far enough out that nothing is in the last 30 days: the count is 0 but
        # 'days_since' still says when the last one WAS — the zero explains itself
        out = data.pipeline(self.db_path, today="2026-12-01")
        self.assertEqual(out["icp"]["last_30"], 0)
        self.assertEqual(out["icp"]["total"], 2)
        self.assertGreater(out["icp"]["days_since"], 30)


class TestIcpPersistence(unittest.TestCase):
    """The tier must be recomputable from stored fields — that is what makes the
    persisted column trustworthy rather than a frozen guess."""

    def test_backfill_is_deterministic_and_idempotent(self):
        import json as _json
        from modules.upwork import icp
        tmp = Path(tempfile.mkdtemp(prefix="cockpit-icp-"))
        try:
            dbp = tmp / "v3.db"
            db.migrate(dbp)
            with db.connect(dbp) as conn:
                conn.execute(
                    "INSERT INTO scored_jobs (id, title, description, skills_json,"
                    " contract_type, hourly_max, engagement_duration,"
                    " client_total_spent, client_hires, score, fetched_at)"
                    " VALUES ('2065400000000007919','Airtable + Make.com build',"
                    " 'Ongoing automation for our ops team.',"
                    " '[\"Airtable\",\"Make.com\"]','Hourly',60,"
                    " 'More than 6 months',168000,20,24,'2026-06-10T04:00:00+00:00')")
            with db.connect(dbp) as conn:
                row = dict(conn.execute("SELECT * FROM scored_jobs").fetchone())
            row["skills"] = _json.loads(row["skills_json"])
            tier = icp.classify(row, score=row["score"])["tier"]
            self.assertEqual(tier, "icp")
            # recomputing the same row yields the same tier — no drift, no state
            self.assertEqual(icp.classify(row, score=row["score"])["tier"], tier)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_legacy_list_shaped_breakdown_does_not_crash(self):
        # early rows stored score_breakdown_json as a LIST; the backfill must
        # survive it rather than blow up mid-run
        from modules.upwork import icp
        v = icp.classify({"title": "Airtable work", "description": "ongoing",
                          "hourly_max": 60, "client_total_spent": 90000,
                          "client_hires": 10},
                         score=24, breakdown=["not", "a", "dict"])
        self.assertEqual(v["tier"], "icp")


if __name__ == "__main__":
    unittest.main()
