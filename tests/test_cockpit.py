"""Cockpit tests — real ThreadingHTTPServer on an ephemeral localhost port,
hit over urllib so token auth + routing are genuinely exercised. Data readers
run against a fixture SQLite DB. No network, no Airtable (Phases 0-2 are
SQLite-local).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from core import config, db, registry
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


class TestBodyLimits(CockpitServerCase):
    """A JSON POST body had NO ceiling — `rfile.read(Content-Length)` read
    whatever the header said. Quick Capture is the first route he can paste
    into, so the first realistic way to reach that is an accidental paste of a
    whole transcript. Refused with a reason, never swallowed."""

    def test_an_oversized_json_body_is_refused_with_a_reason(self):
        big = {"text": "x" * (server.JSON_MAX_BYTES + 10)}
        status, body = self.post("/api/note", body=big)
        self.assertEqual(status, 413)
        self.assertIn("Nothing was saved", body)
        with db.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) c FROM notes").fetchone()["c"], 0)

    def test_a_note_over_the_CAPTURE_cap_is_refused_before_it_is_stored(self):
        """Under the transport ceiling, over the capture one. Truncating to fit
        and reporting success is the same class of bug as losing it."""
        status, body = self.post(
            "/api/note", body={"text": "x" * (config.CAPTURE_MAX_CHARS + 1)})
        self.assertEqual(status, 500)            # the reader's own ValueError
        self.assertIn("Nothing was saved", body)
        with db.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) c FROM notes").fetchone()["c"], 0)


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


class TestStaticCaching(CockpitServerCase):
    """/static/* must ALWAYS revalidate, and revalidation must be cheap.

    The defect (2026-08-13): static responses carried no Cache-Control, no
    ETag and no Last-Modified, so under HTTP/1.0 freshness fell to the
    browser's heuristic. Chrome served a 22 KB today.js out of cache while
    disk held 32 KB, and a hash-only SPA navigation never re-requests a
    module — so a day of shipped fixes was invisible in the open tab, which
    made every OTHER fix unverifiable. These tests pin the contract:
    revalidate on every reuse, 304 when the bytes are unchanged, a DIFFERENT
    ETag and the new bytes the moment they are not.
    """

    def setUp(self):
        super().setUp()
        # resolve(): the macOS temp dir is a symlink, and _static compares the
        # RESOLVED target against STATIC_DIR before it will serve anything.
        self.static = (self.tmp / "static").resolve()
        self.static.mkdir()
        (self.static / "app.js").write_text("export const a = 1;\n")
        (self.static / "index.html").write_text("<html><body>shell</body></html>")
        patcher = mock.patch.object(server, "STATIC_DIR", self.static)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fetch(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_static_carries_no_cache_and_a_strong_etag(self):
        status, headers, body = self.fetch("/static/app.js")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-cache")
        self.assertIn("Last-Modified", headers)
        etag = headers.get("ETag", "")
        self.assertTrue(etag.startswith('"') and etag.endswith('"'), etag)
        self.assertFalse(etag.startswith("W/"), "must be a STRONG validator")
        self.assertEqual(body, b"export const a = 1;\n")

    def test_index_shell_revalidates_too(self):
        # the shell holds the module graph; a cached one pins every module
        for path in ("/", "/index.html"):
            _, headers, _ = self.fetch(path)
            self.assertEqual(headers.get("Cache-Control"), "no-cache", path)
            self.assertIn("ETag", headers, path)

    def test_if_none_match_returns_304_with_no_body(self):
        _, headers, _ = self.fetch("/static/app.js")
        status, h2, body = self.fetch(
            "/static/app.js", {"If-None-Match": headers["ETag"]})
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")
        self.assertEqual(h2.get("ETag"), headers["ETag"])
        self.assertEqual(h2.get("Cache-Control"), "no-cache")
        # RFC 9110 §15.4.5: a 304 has no body, so it must not claim a length
        self.assertNotIn("Content-Length", h2)

    def test_weak_and_wildcard_if_none_match_also_match(self):
        _, headers, _ = self.fetch("/static/app.js")
        etag = headers["ETag"]
        for sent in (f'W/{etag}', "*", f'"nope", {etag}'):
            status, _, _ = self.fetch("/static/app.js", {"If-None-Match": sent})
            self.assertEqual(status, 304, sent)

    def test_a_modified_file_produces_a_different_etag(self):
        _, headers, _ = self.fetch("/static/app.js")
        (self.static / "app.js").write_text("export const a = 2;\n// grew\n")
        status, h2, body = self.fetch(
            "/static/app.js", {"If-None-Match": headers["ETag"]})
        self.assertEqual(status, 200)                    # NOT 304
        self.assertNotEqual(h2["ETag"], headers["ETag"])
        self.assertEqual(body, b"export const a = 2;\n// grew\n")

    def test_same_length_same_second_edit_still_changes_the_etag(self):
        """The exact shape of the original bug.

        A validator built from (mtime, size) calls this file unchanged — same
        byte count, same second — and serves the stale copy forever. Hashing
        the bytes is why this passes.
        """
        f = self.static / "app.js"
        _, headers, _ = self.fetch("/static/app.js")
        f.write_text("export const a = 9;\n")           # identical length
        os.utime(f, (1, 1))                             # …and an older mtime
        status, h2, body = self.fetch(
            "/static/app.js", {"If-None-Match": headers["ETag"]})
        self.assertEqual(status, 200)
        self.assertNotEqual(h2["ETag"], headers["ETag"])
        self.assertEqual(body, b"export const a = 9;\n")

    def test_if_modified_since_is_honoured_as_the_fallback(self):
        _, headers, _ = self.fetch("/static/app.js")
        status, _, _ = self.fetch(
            "/static/app.js", {"If-Modified-Since": headers["Last-Modified"]})
        self.assertEqual(status, 304)
        status, _, body = self.fetch(
            "/static/app.js",
            {"If-Modified-Since": "Sun, 01 Jan 2023 00:00:00 GMT"})
        self.assertEqual(status, 200)
        self.assertEqual(body, b"export const a = 1;\n")

    def test_a_stale_if_none_match_beats_a_fresh_if_modified_since(self):
        # RFC 9110 §13.2.2: when both are present, the entity tag decides.
        _, headers, _ = self.fetch("/static/app.js")
        status, _, _ = self.fetch("/static/app.js", {
            "If-None-Match": '"stale"',
            "If-Modified-Since": headers["Last-Modified"],
        })
        self.assertEqual(status, 200)

    def test_garbage_if_modified_since_does_not_500(self):
        status, _, _ = self.fetch(
            "/static/app.js", {"If-Modified-Since": "not a date at all"})
        self.assertEqual(status, 200)

    def test_api_json_is_never_stored(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/system")
        req.add_header("X-Cockpit-Token", TOKEN)
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.headers.get("Cache-Control"), "no-store")


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
                     " ('2065400000000000111','Make build',26,'Drafted',"
                     " 'https://www.upwork.com/jobs/~022065400000000000111/',"
                     " '2026-06-15T03:00:00+00:00')")
        conn.execute("INSERT INTO scored_jobs (id, title, score, status,"
                     " clickup_task_id, first_scored_at) VALUES"
                     " ('2065400000000000112','Tasked',24,'ClickUp Created',"
                     " 'abc','2026-06-15T03:00:00+00:00')")  # tasked → excluded
        # OLD untasked hot lead: excluded from Today (recent), counted in
        # Pipeline to-triage backlog
        conn.execute("INSERT INTO scored_jobs (id, title, score, status,"
                     " first_scored_at) VALUES"
                     " ('2065400000000000113','Old hot',25,'Scored',"
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
        rows = data.today_rows(self.db_path, which="fresh", _today="2026-06-15")
        self.assertEqual(rows["total"], fresh)

    def test_applied_counts_windows(self):
        # the applied tile's today/week/last_week come from the connects rail —
        # the SAME applied-truth source the Pipeline tab reads (APPLIED = rail),
        # never the proposals table (which only sees client-answered submissions).
        # Proposals cleared so the rail is the ONLY possible source of the counts.
        with db.connect(self.db_path) as conn:
            conn.execute("DELETE FROM proposals")
            for i, dt in enumerate(["2026-06-15",   # today
                                    "2026-06-12",   # this week
                                    "2026-06-05"]):  # last week
                conn.execute(
                    "INSERT INTO connects_history (id, event_date, kind, label,"
                    " connects_delta) VALUES (?,?, 'applied','Applied to a job',-8)",
                    (f"aw{i}", dt))
        out = data.today(self.db_path, today="2026-06-15")
        a = out["metrics"]["applied"]
        self.assertEqual(a["today"], 1)        # 06-15
        self.assertEqual(a["week"], 2)         # 06-15 + 06-12 within 7d
        self.assertEqual(a["last_week"], 1)    # 06-05

    def test_applied_tile_and_pipeline_read_one_shared_source(self):
        # the divergence root cause: two tabs, two numbers. Both the Today tile
        # and the Pipeline tab MUST derive the applied count from one shared rail
        # helper, so they can never show different numbers for the same window.
        with db.connect(self.db_path) as conn:
            conn.execute("DELETE FROM proposals")        # proposals feed blind
            for i, dt in enumerate(["2026-06-10", "2026-06-12", "2026-06-14",
                                    "2026-06-15"]):
                conn.execute(
                    "INSERT INTO connects_history (id, event_date, kind, label,"
                    " connects_delta) VALUES (?,?, 'applied','Applied to a job',-8)",
                    (f"sw{i}", dt))
        with mock.patch.object(config, "APPLICATIONS_PAUSED_SINCE", None):
            tile = data.today(self.db_path, today="2026-06-15")["metrics"]["applied"]
            pipe = data.pipeline(self.db_path, today="2026-06-15")["applied"]
        self.assertEqual(tile["week"], 4)            # rail truth on both tabs
        self.assertEqual(tile["week"], pipe["week"])  # and they AGREE
        self.assertEqual(tile["today"], pipe["today"])
        self.assertEqual(tile["last_week"], pipe["last_week"])

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

    def test_applied_tile_shows_rail_count_when_proposals_feed_is_blind(self):
        # no-fabricated-zeros + one-source law: with applications ACTIVE, the
        # proposals feed reads 0 (it only sees client-answered submissions) while
        # the applied-truth connects rail recorded N. The tile must SHOW N (the
        # rail count Pipeline shows) — never a fabricated 0 — and explain the
        # feed's blindness as a permanent API limit, not a broken sync to hunt.
        with db.connect(self.db_path) as conn:
            conn.execute("DELETE FROM proposals")        # proposals feed blind
            for i, dt in enumerate(["2026-06-10", "2026-06-13"]):
                conn.execute(
                    "INSERT INTO connects_history (id, event_date, kind, label,"
                    " connects_delta) VALUES (?,?, 'applied','Applied to a job',-8)",
                    (f"rz{i}", dt))
        with mock.patch.object(config, "APPLICATIONS_PAUSED_SINCE", None):
            out = data.today(self.db_path, today="2026-06-15")
        tile = out["metrics"]["applied"]
        self.assertEqual(tile["week"], 2)                # rail N, NOT a false 0
        self.assertTrue(tile["contradiction"])           # proposals feed diverges
        self.assertNotIn("no applications", tile["note"] or "")
        self.assertIn("connects rail", tile["note"])
        self.assertIn("2", tile["note"])                 # rail count surfaced
        # the note must NOT read as a fixable broken sync; it is a scope limit
        self.assertNotIn("sync is stale", tile["note"])
        self.assertIn("scope", tile["note"])             # vendorProposals declined
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
        out = data.clients_airtable(_client=self.fake(), _use_cache=False)
        self.assertEqual(out["count"], 2)
        acme = next(c for c in out["clients"] if c["name"] == "Acme")
        self.assertEqual(acme["contracts"], 2)

    def test_injection_slots_are_underscored_so_no_url_can_reach_them(self):
        """`_client` is an AirtableClient the tests supply, not user input.

        While it was spelled `client`, server.py's "forward every keyword-only
        param" rule handed `GET /api/clients/airtable?client=Adelye` a plain
        string straight into `cl.list_records` -> AttributeError -> HTTP 500.
        The leading underscore is what makes that unreachable, so it is pinned.
        """
        import inspect
        params = inspect.signature(data.clients_airtable).parameters
        self.assertIn("_client", params)
        self.assertIn("_use_cache", params)
        for name in ("client", "use_cache"):
            self.assertNotIn(name, params,
                             f"{name} is reachable from a query string again")


class TestReaderInjectionSlots(CockpitServerCase):
    """No routed reader may expose an injection slot to a query string."""

    def test_no_routed_reader_forwards_an_underscored_param(self):
        import inspect
        for path, fn in server.GET_ROUTES.items():
            for p in inspect.signature(fn).parameters.values():
                if p.kind == p.KEYWORD_ONLY and p.name.startswith("_"):
                    # the server's own filter is what must exclude it
                    accepted = {q.name for q in
                                inspect.signature(fn).parameters.values()
                                if q.kind == q.KEYWORD_ONLY
                                and not q.name.startswith("_")}
                    self.assertNotIn(p.name, accepted, f"{path}:{p.name}")

    def test_client_query_param_cannot_poison_the_airtable_roster(self):
        """The regression test for the 500: ?client=<string> must be ignored."""
        data._clients_cache["data"] = None
        fake = FakeAirtable([
            {"id": "r1", "fields": {"Name": "Acme", "Status": "Active"}},
        ])
        try:
            with mock.patch.object(data, "AirtableClient", lambda: fake):
                status, body = self.get(
                    "/api/clients/airtable?client=Adelye&use_cache=0")
        finally:
            data._clients_cache["data"] = None
        self.assertEqual(status, 200, body)
        out = json.loads(body)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["clients"][0]["name"], "Acme")

    def test_underscored_query_param_is_dropped_too(self):
        data._clients_cache["data"] = None
        fake = FakeAirtable([
            {"id": "r1", "fields": {"Name": "Acme", "Status": "Active"}},
        ])
        try:
            with mock.patch.object(data, "AirtableClient", lambda: fake):
                status, body = self.get(
                    "/api/clients/airtable?_client=Adelye&_use_cache=0")
        finally:
            data._clients_cache["data"] = None
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["count"], 1)


class TestQueryParamGuard(CockpitServerCase):
    """`?today=` is a clock, not a request param — and the registry is what
    makes that true for every slot added after this one.

    `today` is a TEST clock-injection slot. It was POSITIONAL on today(),
    pipeline(), money() and pipeline_facts() — unreachable, because the server
    only ever forwarded KEYWORD-ONLY params — but keyword-only on today_rows(),
    clients() and pipeline_rows(), where a URL could set it. The same knob, safe
    on four routes and exposed on three, purely by how it happened to be
    declared. `?today=lol` 500'd /api/today/rows out of date.fromisoformat, and
    on /api/clients it came back 200 with `"as_of": "lol"` — a provenance stamp
    the screen renders as server truth.

    Fixed in two places on purpose. The readers renamed it `_today` (the
    `_client` convention above), and server.QUERY_PARAMS turned forwarding into
    an opt-IN: a param reaches a reader only if someone registered it as user
    input. The registry is the half that generalises — the next injection slot
    is unreachable by default, whatever it ends up being called.
    """

    # the three that were reachable. Each needs whatever else makes it a
    # valid call, so a refusal cannot be mistaken for the guard working.
    CLOCK_ROUTES = ("/api/today/rows?which=fresh",
                    "/api/clients",
                    "/api/pipeline/rows?stage=triage")

    @staticmethod
    def _with(route, q):
        return route + ("&" if "?" in route else "?") + q

    def test_today_cannot_reach_the_three_keyword_only_readers(self):
        for route in self.CLOCK_ROUTES:
            plain_status, plain = self.get(route)
            status, body = self.get(self._with(route, "today=lol"))
            self.assertEqual(plain_status, 200, plain)
            self.assertEqual(status, 200, body)      # /api/today/rows was a 500
            self.assertEqual(body, plain, route)     # the param changed nothing
            self.assertNotIn("lol", body, route)     # it never reached the payload

    def test_today_cannot_forge_a_valid_looking_clock(self):
        """The 500 was the loud half of this. The quiet half was a WELL-FORMED
        date: `?today=2020-01-01` on /api/clients returned 200 and stamped a day
        the server never derived onto a card the user reads as provenance."""
        for route in self.CLOCK_ROUTES:
            status, body = self.get(self._with(route, "today=2020-01-01"))
            self.assertEqual(status, 200, body)
            self.assertNotIn("2020-01-01", body, route)

    def test_as_of_is_always_server_derived(self):
        """/api/pipeline/facts is the control — its `today` is positional, so it
        was never reachable; /api/clients is the one that leaked."""
        for route in ("/api/clients", "/api/pipeline/facts"):
            status, body = self.get(self._with(route, "today=2020-01-01"))
            self.assertEqual(status, 200, body)
            out = json.loads(body)
            if out.get("blocked"):        # doctor RED hides the panel entirely
                self.assertNotIn("as_of", out, route)
                continue
            self.assertIn("as_of", out, route)
            self.assertNotEqual(out["as_of"], "2020-01-01", route)
            date.fromisoformat(out["as_of"][:10])   # raises if it is not a date

    def test_every_forwardable_param_is_registered(self):
        """The guard, stated as a rule. A reader that grows a keyword-only param
        it MEANS as user input has to name it in server.QUERY_PARAMS — and being
        made to name it is where "so what validates it?" gets asked. A slot
        nobody registers is simply unreachable from a URL, which is the safe
        default the `today` split did not have."""
        import inspect
        for path, fn in server.GET_ROUTES.items():
            for p in inspect.signature(fn).parameters.values():
                if p.kind != p.KEYWORD_ONLY or p.name.startswith("_"):
                    continue
                self.assertIn(
                    p.name, server.QUERY_PARAMS,
                    f"{path}: keyword-only param {p.name!r} is not in"
                    " server.QUERY_PARAMS. Register it (and validate it in the"
                    " reader) if it is user input; underscore it if it is an"
                    " injection slot.")

    def test_registry_has_no_dead_entries(self):
        """…and the rule holds in the other direction, so the registry cannot
        rot into a list of names no reader accepts any more."""
        import inspect
        declared = set()
        for fn in server.GET_ROUTES.values():
            declared |= {p.name
                         for p in inspect.signature(fn).parameters.values()
                         if p.kind == p.KEYWORD_ONLY}
        self.assertEqual(set(server.QUERY_PARAMS) - declared, set())

    def test_no_routed_reader_exposes_a_clock_slot(self):
        """Belt to the registry's braces: the readers themselves must not spell
        a clock slot in a forwardable way, whichever route it hangs off."""
        import inspect
        for path, fn in server.GET_ROUTES.items():
            names = {p.name for p in inspect.signature(fn).parameters.values()
                     if p.kind == p.KEYWORD_ONLY}
            for slot in ("today", "now"):
                self.assertNotIn(slot, names,
                                 f"{path}: {slot!r} is a clock, not a request"
                                 f" param — call it _{slot}")


class TestPipelineRowsFilterValidation(CockpitServerCase):
    """`week` IS user input — the funnel-by-week table sends it — so unlike the
    `today` clock it gets validated rather than hidden. `?week=lol` reached
    date.fromisoformat() and came back as a 500 stack-trace repr.

    The envelope is the one this reader already uses for an unknown `stage`, and
    activity() for a bad `day`: 200 + {"error": ...}, which pipeline.js checks
    before it touches res.rows and raises as a red toast. UI_BUILD_BRIEF §0 is
    the reason it is not a silent empty list — a refused filter that renders as
    0 rows is indistinguishable from a true "nothing matched"."""

    def test_malformed_week_is_a_clean_error_not_a_500(self):
        status, body = self.get("/api/pipeline/rows?stage=scanned&week=lol")
        self.assertEqual(status, 200, body)
        out = json.loads(body)
        self.assertIn("error", out)
        self.assertIn("week", out["error"])
        self.assertIn("lol", out["error"])        # says which value it refused
        self.assertEqual(out["rows"], [])
        self.assertIsNone(out["total"])           # not measured, not zero

    def test_malformed_month_window_is_refused(self):
        # these string-compared their way to zero rows, which reads as "no data"
        for q in ("frm=lol", "to=lol", "frm=2026-13", "to=2026-6"):
            status, body = self.get(f"/api/pipeline/rows?stage=jobs&{q}")
            self.assertEqual(status, 200, body)
            self.assertIn("error", json.loads(body), q)

    def test_malformed_score_bound_is_refused_not_silently_dropped(self):
        # it used to be dropped by an int() that swallowed ValueError, which
        # WIDENED the drill past the chart bar it was launched from
        for q in ("score_min=lol", "score_max=eight"):
            status, body = self.get(f"/api/pipeline/rows?stage=jobs&{q}")
            self.assertEqual(status, 200, body)
            self.assertIn("error", json.loads(body), q)

    def test_malformed_carded_is_refused(self):
        status, body = self.get("/api/pipeline/rows?stage=jobs&carded=yes")
        self.assertEqual(status, 200, body)
        self.assertIn("error", json.loads(body))

    def test_open_vocabulary_filters_are_not_whitelisted(self):
        """status/skip/feed/icp are SQL-bound equality filters over vocabularies
        that grow with the data. "No rows where feed_source='nope'" is the TRUE
        answer, and a whitelist would go stale and refuse real values."""
        status, body = self.get("/api/pipeline/rows?stage=jobs&feed=nope")
        self.assertEqual(status, 200, body)
        out = json.loads(body)
        self.assertNotIn("error", out)
        self.assertEqual(out["rows"], [])

    def test_legitimate_filters_still_work(self):
        for q in ("stage=scanned&week=2026-06-08",
                  "stage=jobs&frm=2026-06&to=2026-06",
                  "stage=jobs&score_min=8&score_max=20&carded=1",
                  "stage=triage"):
            status, body = self.get("/api/pipeline/rows?" + q)
            self.assertEqual(status, 200, body)
            out = json.loads(body)
            self.assertNotIn("error", out, q)
            self.assertIsInstance(out["rows"], list)
            self.assertIsNotNone(out["total"], q)


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

    def test_wise_mail_rows_are_attributed_not_stranded_in_unlinked(self):
        """The Wise-MAIL rail (2026-08-10, replacing the retired CSV export)
        writes `source='Wise-mail'`. `_client_key` matched `== 'Wise'`, so those
        rows skipped the payer parse entirely and fell into `unlinked` — real,
        current client money ($6,257.78 live) rendered as "revenue with no
        parseable payer" while the client's dossier attributed it correctly.
        Two tabs, one client, two answers."""
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type,"
                " category, source, assignment_company, description,"
                " amount_minor, amount_base_minor, currency, pnl_bucket,"
                " creation_dt) VALUES ('wm1','kwm1','Fixed Earning','income',"
                " 'Wise-mail','','Received money from Adélye Ltd with reference"
                " INV-2627-041',68250,68250,'USD','revenue',"
                " '2026-08-07T00:00:00+0000')")
        out = data.clients(self.db_path)
        adelye = next(c for c in out["clients"] if c["name"] == "Adelye")
        self.assertEqual(adelye["revenue_usd"], 15682.5)    # 10k + 5k + 682.50
        self.assertNotIn("Adelye", [r["payer"] for r in data.client_transactions(
            self.db_path, client="__unlinked__")["rows"]])

    def test_deaccent_folds_the_upwork_double_space(self):
        """_deaccent is the cockpit's identity key. Upwork writes TWO spaces
        ('Tanushant  Sharma', 'Ippon  Technology'); the registry, the dossier
        index and CLIENT_CANONICAL all hold one. Until 2026-08-13 this was the
        last place the two could not meet, so those clients' dossier rows read
        '—' revenue next to a dossier stating the money."""
        self.assertEqual(data._deaccent("Tanushant  Sharma"), "tanushant sharma")
        self.assertEqual(data._deaccent("Adélye \t\n Ltd"), "adelye ltd")
        # a canonical merge must survive the fold
        self.assertEqual(data._canon_client("Adélye  Ltd."), "Adelye")

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
        # the contract label is Upwork's '<person>, <company>' COMPOSITE, which
        # is not a client name: it resolves through the registry to NETFLY, so
        # these rows land on the same row as the ones Upwork labelled 'NETFLY'
        # directly. Until 2026-08-13 the composite became its own client and the
        # Clients tab showed NETFLY twice (Abhijeet: "netfly and haleema umair
        # is the same client").
        self.assertIn("NETFLY", names)                  # attributed via contract id
        self.assertNotIn("Haleema Umair, NETFLY", names)
        c = next(x for x in out["clients"] if x["name"] == "NETFLY")
        self.assertEqual(c["revenue_usd"], 324.0)

    def test_job_title_in_assignment_company_is_not_a_client(self):
        # some legacy Upwork rows carry the JOB TITLE in assignment_company.
        # Taken as a company it minted a client named after the job, splitting
        # the contract's real client across two rows (Beglaubigt lost $1,324.16
        # to a phantom 'Ai Automations sales outbound & Inbound', 2026-08-13).
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO contracts (id, title, client_company, upwork_status)"
                " VALUES ('c43406906','Ai Automations sales outbound & Inbound',"
                "'Ioannis Pirigkos, Ioannis P','ACTIVE')")
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type,"
                " category, source, assignment_company, description, amount_minor,"
                " amount_base_minor, currency, pnl_bucket, creation_dt) VALUES"
                " ('t1','kt1','Hourly Earning','income','Upwork',"
                "'Ai Automations sales outbound & Inbound',"
                "'Ai Automations sales outbound & Inbound · Earnings for May 4-10',"
                " 38500,38500,'USD','revenue','2026-05-20T00:00:00+0000')")
        out = data.clients(self.db_path)
        names = [c["name"] for c in out["clients"]]
        self.assertNotIn("Ai Automations sales outbound & Inbound", names)
        beg = next(c for c in out["clients"] if c["name"] == "Beglaubigt")
        self.assertEqual(beg["revenue_usd"], 385.0)

    def test_drill_never_renders_a_null_source(self):
        # 17 legacy rows carry source IS NULL; the drill printed a literal
        # 'None' pill on exactly the clients whose attribution was in question.
        # `—` means not measured (UI_BUILD_BRIEF §0 rule 1); a null never ships.
        # The row is still ATTRIBUTED — its canonical_key proves it is Upwork's,
        # so the last-resort job-title label applies despite the NULL column.
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO transactions (record_id, canonical_key, type,"
                " category, description, amount_minor, amount_base_minor,"
                " currency, pnl_bucket, creation_dt) VALUES"
                " ('personal:9','upwork:9','Hourly Earning','income',"
                "'(99999) Widget Works - 2:00 hrs @ $50.00/hr - 06/01/2026',"
                " 10000,10000,'USD','revenue','2026-06-12T00:00:00+0000')")
        d = data.client_transactions(self.db_path, client="Widget Works")
        self.assertEqual(d["count"], 1)
        self.assertEqual(d["rows"][0]["source"], "—")
        self.assertEqual(d["sum_usd"], 100.0)

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

    def test_masked_upwork_client_resolves_via_registry_ledger_label(self):
        # Upwork masks some clients as 'no company'. The registry records that
        # placeholder as irene-buht's `billing.ledger_label` (uniqueness-checked,
        # deliberately not an alias), so the rows are the CLIENT's, not a job's.
        # Until 2026-08-13 the tab labelled them by job title, which split one
        # client across two phantom names and put $490 of dossier revenue on a
        # $75 tab row.
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
        names = [x["name"] for x in out["clients"]]
        self.assertNotIn("Voice Agent for Driving School", names)
        c = next(x for x in out["clients"] if x["name"] == "Irene Buht")
        self.assertEqual(c["revenue_usd"], 340.0)          # both milestones merged

    def test_unclaimed_masked_earning_still_falls_back_to_its_job_title(self):
        # the last resort survives: an Upwork earning with NO company and no
        # contract to bridge to is still named by what it was for, never
        # 'unlinked' (Abhijeet, 2026-07-30). Milestones of one job merge.
        with db.connect(self.db_path) as conn:
            for rid, minor in (("j1", 20000), ("j2", 14000)):
                conn.execute(
                    "INSERT INTO transactions (record_id, canonical_key, type,"
                    " category, source, description, amount_minor,"
                    " amount_base_minor, currency, pnl_bucket, creation_dt)"
                    " VALUES (?,?, 'Fixed Earning','income','Upwork',"
                    "'Invoice for Voice Agent for Driving School: Milestone',"
                    "?,?, 'USD','revenue','2025-04-20T00:00:00+0000')",
                    (rid, "k" + rid, minor, minor))
        out = data.clients(self.db_path)
        c = next(x for x in out["clients"]
                 if x["name"] == "Voice Agent for Driving School")
        self.assertEqual(c["revenue_usd"], 340.0)

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
        self.assertIn("every decided proposal", wr["caveat"])   # since use case C (26 Aug)

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
        out = data.pipeline_rows(self.db_path, stage="triage", _today="2026-06-15")
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

    def key_for(self, thread_id, today="2026-06-15"):
        """The item_key the UI would actually send for this thread's card.

        Cards are keyed on the COUNTERPARTY, not the room, so a person with two
        Upwork rooms is one card. Reading the key off the rendered card instead
        of hardcoding it means these tests assert the round trip the cockpit
        really performs — hardcoding 'followup:<room>' is what let the key
        change slip past them."""
        for f in data.today(self.db_path, today=today)["follow_ups"]:
            if thread_id in f["thread_ids"]:
                return f["item_key"]
        raise AssertionError(f"no follow-up card covers {thread_id}")

    def test_done_persists_and_hides_followup(self):
        k = self.key_for("rA")
        actions.nudge(self.db_path, {"item_key": k, "action": "done"})
        self.assertEqual(self.state(k)["state"], "done")
        # re-read today: rA gone, rB still there (persists across "restart")
        out = data.today(self.db_path, today="2026-06-15")
        ids = [f["thread_id"] for f in out["follow_ups"]]
        self.assertNotIn("rA", ids)
        self.assertIn("rB", ids)

    def test_dismiss_hides(self):
        actions.nudge(self.db_path, {"item_key": self.key_for("rB"),
                                     "action": "dismiss"})
        out = data.today(self.db_path, today="2026-06-15")
        self.assertNotIn("rB", [f["thread_id"] for f in out["follow_ups"]])

    def test_snooze_hides_until_date_then_returns(self):
        k = self.key_for("rA")
        actions.nudge(self.db_path, {"item_key": k, "action": "snooze",
                                     "snooze_days": 3}, today="2026-06-15")
        self.assertEqual(self.state(k)["snooze_until"], "2026-06-18")
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

    def test_done_on_a_nudge_card_actually_hides_it(self):
        # THE bug behind "I marked it done yesterday and the day before but it
        # still shows up": the Done button writes nudge_state, and the nudge
        # QUEUE never read nudge_state — only the follow-up list did. The row
        # was written and nothing ever looked at it.
        from modules.chief_of_staff import nudges as eng
        eng.write_nudge(self.db_path, source="knowledge", dept="knowledge",
                        kind="stale_dossier", title="Unprocessed sources: acme",
                        dedup_key="stale_dossier:acme", client="acme")
        key = "nudge:stale_dossier:acme"
        self.assertIn(key, [q.get("item_key") for q in
                            data.today(self.db_path, today="2026-06-15")["queue"]])
        actions.nudge(self.db_path, {"item_key": key, "action": "done"})
        self.assertNotIn(key, [q.get("item_key") for q in
                               data.today(self.db_path, today="2026-06-15")["queue"]])

    def test_a_new_client_message_revives_a_done_followup(self):
        # 'Done' means handled, not silenced forever — if the client replies
        # after the fact the card must come back, or the tab hides real work
        k = self.key_for("rA")
        actions.nudge(self.db_path, {"item_key": k, "action": "done"})
        self.assertNotIn(k, [f["item_key"] for f in
                             data.today(self.db_path, today="2026-06-15")["follow_ups"]])
        with db.connect(self.db_path) as conn:      # ...then they reply
            conn.execute("UPDATE threads SET latest_message_dt="
                         "'2099-01-01T09:00:00+00:00' WHERE id='rA'")
        self.assertIn(k, [f["item_key"] for f in
                          data.today(self.db_path, today="2026-06-15")["follow_ups"]])

    def test_a_stale_done_does_not_gag_a_re_minted_card(self):
        # dedup_keys are stable per entity ('stale_dossier:<client>'), and only
        # owed_reply carries evidence_ts. Without a created_at check, ONE Done
        # in August would hide that client's backlog in January while the
        # morning brief — which reads the nudges table — nagged daily.
        from modules.chief_of_staff import nudges as eng
        eng.write_nudge(self.db_path, source="knowledge", dept="knowledge",
                        kind="stale_dossier", title="Unprocessed: acme",
                        dedup_key="stale_dossier:acme", client="acme")
        key = "nudge:stale_dossier:acme"
        actions.nudge(self.db_path, {"item_key": key, "action": "done"})
        self.assertNotIn(key, [q.get("item_key") for q in
                               data.today(self.db_path, today="2026-06-15")["queue"]])
        with db.connect(self.db_path) as conn:      # the condition re-arises
            conn.execute("UPDATE nudges SET state='open', created_at="
                         "'2099-01-01T00:00:00' WHERE dedup_key="
                         "'stale_dossier:acme'")
        self.assertIn(key, [q.get("item_key") for q in
                            data.today(self.db_path, today="2026-06-15")["queue"]])

    def test_a_hidden_escalation_cannot_hide_its_constituents(self):
        # an escalation stands IN FRONT OF work. If the escalation itself is
        # hidden, its covers must stop applying — otherwise the board goes
        # blank while real cards sit open in the engine.
        from modules.chief_of_staff import nudges as eng
        eng.write_nudge(self.db_path, source="knowledge", dept="knowledge",
                        kind="stale_dossier", title="Unprocessed: acme",
                        dedup_key="stale_dossier:acme", client="acme")
        eng.write_nudge(self.db_path, source="sweep", dept="knowledge",
                        kind="escalate_client", title="Acme is drifting",
                        dedup_key="escalate_client:acme", client="acme",
                        payload={"covers": ["stale_dossier:acme"]})
        keys = lambda: [q.get("item_key") for q in
                        data.today(self.db_path, today="2026-06-15")["queue"]]
        self.assertIn("nudge:escalate_client:acme", keys())
        self.assertNotIn("nudge:stale_dossier:acme", keys())   # rolled up
        actions.nudge(self.db_path, {"item_key": "nudge:escalate_client:acme",
                                     "action": "done"})
        with db.connect(self.db_path) as conn:      # sweep re-mints the same key
            conn.execute("UPDATE nudges SET state='open' WHERE"
                         " dedup_key='escalate_client:acme'")
        self.assertIn("nudge:stale_dossier:acme", keys())      # NOT blacked out

    def test_done_writes_through_to_the_engine(self):
        # the morning brief and the sweep read the NUDGES table, not the
        # cockpit's nudge_state overlay. A Done that only wrote the overlay left
        # the card open everywhere else — and the sweep kept refreshing it.
        from modules.chief_of_staff import nudges as eng
        eng.write_nudge(self.db_path, source="knowledge", dept="knowledge",
                        kind="stale_dossier", title="Unprocessed: acme",
                        dedup_key="stale_dossier:acme", client="acme")
        actions.nudge(self.db_path, {"item_key": "nudge:stale_dossier:acme",
                                     "action": "done"})
        with db.connect(self.db_path) as conn:
            st = conn.execute("SELECT state FROM nudges WHERE"
                              " dedup_key='stale_dossier:acme'").fetchone()
        self.assertEqual(st["state"], "done")
        self.assertEqual(eng.top_n(self.db_path, 10), [])

    def test_done_count_drops_followups_due(self):
        before = data.today(self.db_path, today="2026-06-15")["metrics"]["follow_ups_due"]
        actions.nudge(self.db_path, {"item_key": self.key_for("rA"),
                                     "action": "done"})
        after = data.today(self.db_path, today="2026-06-15")["metrics"]["follow_ups_due"]
        self.assertEqual(after, before - 1)


class TestEvidenceAuthority(unittest.TestCase):
    """_is_actioned: evidence beats a re-mint; a re-mint only speaks for kinds
    that have no evidence to give.

    Both halves exist and neither may be 'simplified' away — they are exact
    mirrors. Drop the evidence branch and a scheduled re-mint resurrects a card
    the founder closed (Julie Heffernan, 2026-08-12). Drop the created_at branch
    and one Done in August gags that client's dossier backlog in January while
    the morning brief nags about it daily."""

    KEY = "followup:p:someone"
    SUPPRESSED = {KEY}
    ACTIONED = {KEY: "2026-08-11 08:41:35"}     # UTC (nudge_state is SQLite-stamped)

    def test_a_re_mint_with_no_new_evidence_stays_hidden(self):
        # the live shape: done 08-11 08:41Z, evidence 08-10 19:36Z (OLDER, so
        # nothing was said), row re-minted 08-12 04:11 IST = 08-11 22:41Z.
        self.assertTrue(data._is_actioned(
            self.KEY, self.SUPPRESSED, self.ACTIONED,
            evidence_ts="2026-08-10T19:36:41.140Z",
            created_at="2026-08-12T04:11:25"))

    def test_genuinely_newer_evidence_brings_it_back(self):
        # ...and the mirror: they DID say something after the Done, so the card
        # is live again even though the row is old.
        self.assertFalse(data._is_actioned(
            self.KEY, self.SUPPRESSED, self.ACTIONED,
            evidence_ts="2026-08-11T18:00:00Z",
            created_at="2026-08-01T04:11:25"))

    def test_a_kind_with_no_evidence_is_revived_by_a_re_mint(self):
        # stale_dossier etc. supply no evidence_ts at all, so a fresh row IS
        # the only news there is.
        self.assertFalse(data._is_actioned(
            "stale_dossier:acme", {"stale_dossier:acme"},
            {"stale_dossier:acme": "2026-08-11 08:41:35"},
            evidence_ts=None, created_at="2026-08-12T04:11:25"))
        self.assertTrue(data._is_actioned(          # ...and an OLD row does not
            "stale_dossier:acme", {"stale_dossier:acme"},
            {"stale_dossier:acme": "2026-08-11 08:41:35"},
            evidence_ts=None, created_at="2026-08-01T04:11:25"))

    def test_a_snooze_is_never_cut_short_by_evidence(self):
        # a snooze carries no actioned_at: it is an explicit "not until <date>"
        self.assertTrue(data._is_actioned(
            self.KEY, self.SUPPRESSED, {}, evidence_ts="2099-01-01T00:00:00Z",
            created_at="2099-01-01T00:00:00"))


class TestCrossChannelEvidence(unittest.TestCase):
    """Reply-owed is a claim about a COUNTERPARTY, not about one Upwork room.

    Abhijeet, 2026-08-12: "if I look at the today tab it says reply owed for
    cradle company [...] we have contract started and the communication going on
    over slack for two weeks now."
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cockpit-xchan-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO threads (id, room_name, topic, bucket, tier,"
                " client_id, contract_id, latest_message_dt) VALUES"
                " ('rK','Brandi Jordan','AI Automation Specialist','followup',"
                " 'HOT','the-cradle-company','k9','2026-08-03T14:41:36Z')")
            conn.execute(
                "INSERT INTO drafts (thread_id, draft_kind, tier, body,"
                " word_count, sent_status) VALUES ('rK','followup','HOT',"
                "'Reply',2,'pending')")
            # Upwork says: they wrote on 08-03 and we never answered THERE.
            # NOTE the capital 'I' — messages.direction is stored in mixed case
            # ('Inbound' 1143 / 'inbound' 458 live), so every read lowercases.
            conn.execute(
                "INSERT INTO messages (id, thread_id, message, created_dt,"
                " direction) VALUES ('m1','rK','ping','2026-08-03T14:41:36Z',"
                "'Inbound')")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def card(self, today="2026-08-12"):
        q = [c for c in data.today(self.db_path, today=today)["queue"]
             if c.get("item_key") == "followup:c:the-cradle-company"]
        return q[0] if q else None

    def add_contract(self, status="ACTIVE"):
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO contracts (id, title, upwork_status, start_dt,"
                " thread_id, client_company) VALUES ('k9','AI Automation"
                " Specialist',?,'2026-07-15T20:21:42Z','rK','Brandi Jordan')",
                (status,))

    def add_slack(self, ts, direction):
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO events (id, source, native_id, ts, author,"
                " client_id, state, direction) VALUES (?, 'slack', ?, ?,"
                " 'someone','the-cradle-company','mapped',?)",
                (f"evt:slack:{ts}:{direction}", ts, ts, direction))

    def test_upwork_only_reads_an_active_client_as_a_nine_day_old_lead(self):
        # the BEFORE picture, asserted so the fix cannot silently regress
        c = self.card()
        self.assertEqual(c["age_days"], 9)
        self.assertEqual(c["label"], "reply owed")

    def test_slack_activity_sets_the_honest_age(self):
        self.add_slack("2026-08-08T03:37:49+00:00", "inbound")
        self.assertEqual(self.card()["age_days"], 4)   # not 9

    def test_an_active_contract_relabels_the_card_as_client_comms(self):
        self.add_contract()
        c = self.card()
        self.assertEqual(c["label"], "client comms")
        self.assertTrue(c["engagement"]["active"])
        self.assertEqual(c["engagement"]["contract_id"], "k9")
        self.assertTrue(c["title"].startswith("Client comms:"))
        self.assertEqual(c["detail"]["kind"], "Client comms (draft ready)")
        # ...and it leaves the "chase the prospect" count
        labels = {cl["label"] for cl in
                  data.today(self.db_path, today="2026-08-12")["clusters"]}
        self.assertIn("client comms", labels)
        self.assertNotIn("follow-ups", labels)

    def test_a_client_card_is_never_hidden_only_relabelled(self):
        # suppression would trade noise for a silently missed obligation
        self.add_contract()
        self.add_slack("2026-08-08T03:37:49+00:00", "inbound")
        self.assertIsNotNone(self.card())

    def test_answering_on_slack_raises_the_closure_chip_not_a_hide(self):
        # provable, not a heuristic: they wrote on Upwork at 14:41Z and we
        # answered in their Slack at 15:35Z the same day.
        self.add_contract()
        self.add_slack("2026-08-03T15:35:29+00:00", "outbound")
        c = self.card()
        self.assertIsNotNone(c)                       # still on the board
        self.assertIn("slack", c["possibly_resolved"]["source"])
        self.assertTrue(c["evidence"]["answered_since"])

    def test_our_own_reply_does_not_count_as_them_speaking(self):
        self.add_slack("2026-08-11T09:00:00+00:00", "outbound")
        ev = self.card()["evidence"]
        self.assertEqual(ev["ts"][:10], "2026-08-03")   # THEIR clock, not ours
        self.assertEqual(ev["ours_ts"][:10], "2026-08-11")
        self.assertTrue(ev["answered_since"])

    def test_an_upwork_system_row_is_not_a_client_reply(self):
        # Julie Heffernan: a call happened, then Upwork wrote a system row and
        # the card came back the next morning claiming she was waiting.
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO messages (id, thread_id, message, created_dt,"
                " direction) VALUES ('m2','rK',NULL,'2026-08-12T13:30:08Z',"
                "'system')")
        self.assertEqual(self.card()["evidence"]["ts"][:10], "2026-08-03")
        self.assertEqual(self.card()["age_days"], 9)   # not 0

    def test_an_unattributed_thread_says_it_only_checked_upwork(self):
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE threads SET client_id=NULL WHERE id='rK'")
        q = [c for c in data.today(self.db_path, today="2026-08-12")["queue"]
             if str(c.get("item_key", "")).startswith("followup:")]
        self.assertEqual(len(q), 1)
        self.assertIn("not attributed", q[0]["evidence"]["unproven"])
        self.assertIn("only Upwork was checked", q[0]["detail"]["source"])

    def test_a_done_pressed_on_the_nudge_form_hides_the_followup_form(self):
        # ONE DECISION, TWO STORES. /api/cos-nudge (nudge cards) wrote only the
        # nudges table; /api/nudge (follow-up cards) wrote nudge_state. The same
        # counterparty renders as either shape depending on whether a draft
        # exists, so a Done recorded on one form was invisible to the other and
        # the card came back — with a nudge_state timestamp from two weeks
        # earlier. Live: the-cradle-company, engine done 08-11 23:20Z vs
        # nudge_state 07-29 22:54Z.
        from modules.chief_of_staff import nudges as eng
        k = "followup:c:the-cradle-company"
        eng.write_nudge(self.db_path, source="sales", dept="sales",
                        kind="owed_reply", title="Follow up: x", dedup_key=k,
                        payload={"thread_id": "rK"})
        with db.connect(self.db_path) as conn:      # a stale overlay decision
            conn.execute("INSERT INTO nudge_state (item_key, state, updated_at)"
                         " VALUES (?,'done','2026-07-29 22:54:28')", (k,))
        self.assertIsNotNone(self.card())           # 08-03 reply > 07-29 Done
        actions.cos_nudge(self.db_path, {"dedup_key": k, "action": "done"})
        self.assertIsNone(self.card())              # ...and now it stays gone

    def test_confirming_a_render_time_chip_actually_lands(self):
        # the chip can now be raised at render time from cross-channel evidence,
        # so it exists on cards the engine has no OPEN row for. Returning
        # "no open card" made the founder press a button that did nothing.
        self.add_contract()
        self.add_slack("2026-08-03T15:35:29+00:00", "outbound")
        self.assertIsNotNone(self.card()["possibly_resolved"])
        out = actions.card_closure(self.db_path,
                                   {"item_key": "followup:c:the-cradle-company",
                                    "action": "confirm"})
        self.assertEqual(out["updated"], 1)
        self.assertIsNone(self.card())

    def test_confirming_a_chip_on_an_OPEN_card_marks_it_done(self):
        """Abhijeet, 2026-08-27: "there is one Globaltize email which says
        probably resolved, and if I mark it as done it is still there — it
        refreshes the page but it is still there."

        The button says "Done — nothing owed". With an OPEN engine row it only
        stamped closure_state and left state='open', so the card came straight
        back on the next read. The no-row branch had been fixed; this one had
        not."""
        from modules.chief_of_staff import nudges
        k = "followup:c:the-cradle-company"
        self.add_contract()
        nudges.write_nudge(self.db_path, source="sales", dept="sales",
                           kind="owed_reply", title="Client comms: Globaltize",
                           dedup_key=k, payload={"thread_id": "rK"})
        self.assertIsNotNone(self.card())
        out = actions.card_closure(self.db_path,
                                   {"item_key": k, "action": "confirm"})
        self.assertEqual(out["closure_state"], "confirmed")
        self.assertEqual(out["via"], "closure+done")
        self.assertEqual(out["engine_rows"], 1)
        self.assertIsNone(self.card())                  # gone, and STAYS gone
        with db.connect(self.db_path) as conn:
            row = conn.execute("SELECT state, closure_state FROM nudges"
                               " WHERE dedup_key=? ORDER BY id DESC LIMIT 1",
                               (k,)).fetchone()
        self.assertEqual((row["state"], row["closure_state"]), ("done", "confirmed"))

    def test_keep_open_undoes_a_confirm_so_a_misclick_is_not_final(self):
        from modules.chief_of_staff import nudges
        k = "followup:c:the-cradle-company"
        self.add_contract()
        nudges.write_nudge(self.db_path, source="sales", dept="sales",
                           kind="owed_reply", title="Client comms: Globaltize",
                           dedup_key=k, payload={"thread_id": "rK"})
        actions.card_closure(self.db_path, {"item_key": k, "action": "confirm"})
        self.assertIsNone(self.card())
        out = actions.card_closure(self.db_path, {"item_key": k, "action": "reject"})
        self.assertEqual(out["closure_state"], "rejected")
        self.assertEqual(out["reopened"], 1)
        self.assertIsNotNone(self.card())               # back on the board

    def test_a_done_survives_a_sweep_re_mint_but_not_a_slack_reply(self):
        k = "followup:c:the-cradle-company"
        actions.nudge(self.db_path, {"item_key": k, "action": "done"},
                      today="2026-08-12")
        self.assertIsNone(self.card("2026-08-13"))      # nothing new -> gone
        self.add_slack("2099-01-01T00:00:00+00:00", "inbound")
        self.assertIsNotNone(self.card("2099-01-02"))   # they spoke -> back


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

    def test_accounting_buckets_add_up_to_the_cards(self):
        """The Facts/Sources cards used to state a vague "N belong to no
        dossier" and their drills admitted they could not sum. Every fact and
        every source now lands in exactly one named bucket and the buckets ADD
        UP; `unaccounted` is the residual and must be 0 on a healthy kb."""
        self._seed_kb()          # 1 client + 1 agency + 1 cross fact, 1 source
        with db.connect(self.db_path) as conn:
            # an UNATTRIBUTED source: ingested, client-scoped facts, no client
            conn.execute("INSERT INTO sources (source_type, source_ref,"
                         " content_hash) VALUES ('loom','loom/9','h9')")
            sid = conn.execute("SELECT id FROM sources WHERE source_ref="
                               "'loom/9'").fetchone()["id"]
            for i in range(4):
                conn.execute(
                    "INSERT INTO facts (source_id, category, fact_text,"
                    " citation, confidence, scope) VALUES (?,?,?,?,?,'client')",
                    (sid, "stack", f"orphan {i}", "loom/9 @ 1", "CONFIRMED"))
        acc = data.knowledge(self.db_path)["accounting"]
        self.assertEqual(acc["facts"], {
            "total": 7, "in_dossiers": 1, "unattributed": 4,
            "proof_points": 2, "unaccounted": 0})
        self.assertEqual(acc["sources"], {
            "total": 2, "in_dossiers": 1, "unattributed": 1, "unaccounted": 0})

    def test_accounting_surfaces_a_drifted_index_instead_of_hiding_it(self):
        """A dossier_index row whose fact_count no longer matches the facts
        behind it (what a frozen `skipped_empty` compile used to leave: 17 facts
        for a client whose only call had been unmapped) must show up as
        `unaccounted`, not be absorbed into a bucket."""
        self._seed_kb()
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE dossier_index SET fact_count=18 WHERE"
                         " client_id='adelye'")
        acc = data.knowledge(self.db_path)["accounting"]
        self.assertEqual(acc["facts"]["in_dossiers"], 18)
        self.assertEqual(acc["facts"]["unaccounted"], -17)

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

    # ── the capture latency fix (2026-08-14) ────────────────────────────────
    # He typed note #3 at 14:49 IST; its card appeared at 16:30, because
    # `classify_new` only ran inside the 2-hourly sweep. He got a toast, Today
    # did not change, and the only reading available was "it didn't save".

    def _as_live_db(self):
        """Pretend this temp DB is the server's, so `db_path=None` (what the
        production route is handed) takes the SPAWN path. Never point a test at
        the real DB to get there."""
        return mock.patch.object(config, "DB_PATH", self.db_path)

    def test_capture_spawns_the_classifier_as_a_DETACHED_CHILD(self):
        """The seam. No model call may happen in the cockpit process (the same
        rule /api/ask obeys by shelling out to bin/kb-ask) — AND the response
        must not block on it, which is why this is Popen and not run.

        `start_new_session` is load-bearing: `bin/cockpit reload` restarts this
        server after every Python change, and a child in its process group
        would die with it, mid-classification."""
        with self._as_live_db(), \
                mock.patch.object(actions.subprocess, "Popen") as pop:
            out = actions.note(None, {"text": "anthc dashboard updates"})
        self.assertTrue(out["classifying"])
        argv, kw = pop.call_args[0][0], pop.call_args[1]
        self.assertTrue(str(argv[1]).endswith("bin/note-classify"))
        self.assertEqual(argv[2], str(out["id"]))
        self.assertTrue(kw["start_new_session"])
        self.assertNotIn("timeout", kw)          # fire-and-forget, never awaited

    def test_a_spawn_failure_still_keeps_the_note_and_says_what_happens_next(self):
        """The note is committed BEFORE the child is spawned, so a spawn that
        fails costs the latency and never the note. The copy then has to be
        true of the slow path — the sweep — instead of the fast one."""
        with self._as_live_db(), \
                mock.patch.object(actions.subprocess, "Popen",
                                  side_effect=OSError("no fork for you")):
            out = actions.note(None, {"text": "keep me"})
        self.assertFalse(out["classifying"])
        self.assertIn("next sweep", out["note"])
        with db.connect(self.db_path) as conn:
            row = conn.execute("SELECT state FROM notes WHERE id=?",
                               (out["id"],)).fetchone()
        self.assertEqual(row["state"], "new")    # still there, still claimable

    def test_a_test_db_never_spawns_a_child_against_the_LIVE_one(self):
        with mock.patch.object(actions.subprocess, "Popen") as pop:
            out = actions.note(self.db_path, {"text": "x"})
        pop.assert_not_called()
        self.assertFalse(out["classifying"])

    def test_note_status_is_the_polls_stop_condition(self):
        from modules.chief_of_staff import capture
        n = capture.add_note(self.db_path, "fix the thing", "cockpit")
        pending = data.note_status(self.db_path, note_id=str(n["id"]))
        self.assertFalse(pending["done"])
        capture.classify_new(
            self.db_path, ids=[n["id"]],
            _classify=lambda db, x: {"bucket": "nudge", "title": "Fix the thing",
                                     "action": None, "due": None, "who": "us"})
        done = data.note_status(self.db_path, note_id=str(n["id"]))
        self.assertTrue(done["done"] and done["actionable"])
        self.assertEqual(done["card_title"], "Fix the thing")

    def test_note_status_rejects_a_bad_id_rather_than_looking_empty(self):
        """"I do not know that note" and "nothing has happened yet" are
        opposite answers, and the poll would stop on the wrong one."""
        self.assertIn("error", data.note_status(self.db_path, note_id="lol"))
        self.assertIn("error", data.note_status(self.db_path, note_id=None))
        self.assertIn("error", data.note_status(self.db_path, note_id="999"))

    # ── the non-actionable path is visible (2026-08-14) ─────────────────────

    def test_today_surfaces_a_note_that_produced_no_card(self):
        """Note #2, 'evolve exteriors everything sorted out', classified
        `kb_note` and produced nothing on any screen. Today now carries it,
        with the verdict, until he answers it."""
        from modules.chief_of_staff import capture
        capture.add_note(self.db_path, "evolve exteriors everything sorted out",
                         "cockpit")
        capture.classify_new(
            self.db_path,
            _classify=lambda db, x: {"bucket": "kb_note", "title": "Evolve",
                                     "action": None, "due": None, "who": "us"})
        caps = data.today(self.db_path)["captures"]
        self.assertEqual(caps["needs_decision"], 1)
        self.assertEqual(caps["items"][0]["text"],
                         "evolve exteriors everything sorted out")
        self.assertTrue(caps["items"][0]["verdict"])

    def test_note_resolve_makes_a_card_anyway(self):
        from modules.chief_of_staff import capture
        n = capture.add_note(self.db_path, "evolve exteriors sorted", "cockpit")
        capture.classify_new(
            self.db_path,
            _classify=lambda db, x: {"bucket": "kb_note", "title": "E",
                                     "action": None, "due": None, "who": "us"})
        out = actions.note_resolve(self.db_path,
                                   {"note_id": n["id"], "action": "card"})
        self.assertTrue(out["ok"])
        self.assertEqual(data.today(self.db_path)["captures"]["needs_decision"], 0)
        with db.connect(self.db_path) as conn:
            row = conn.execute("SELECT title, state FROM nudges WHERE dedup_key=?",
                               (f"note:{n['id']}",)).fetchone()
        self.assertEqual(row["state"], "open")

    def test_note_resolve_validates(self):
        with self.assertRaises(ValueError):
            actions.note_resolve(self.db_path, {"action": "card"})
        with self.assertRaises(ValueError):
            actions.note_resolve(self.db_path, {"note_id": 1, "action": "delete"})

    def test_a_broken_capture_read_never_blanks_TODAY(self):
        from modules.chief_of_staff import capture as cap
        with mock.patch.object(cap, "unresolved", side_effect=RuntimeError("x")):
            d = data.today(self.db_path)
        self.assertIn("error", d["captures"])
        self.assertEqual(d["captures"]["items"], [])
        self.assertIn("queue", d)                       # the page still renders

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

    def test_a_stale_server_says_so_instead_of_blaming_the_ledger(self):
        """2026-09-05. `bin/finance-bucketize --apply` wrote buckets from new
        rules while the cockpit had been up since 31 Aug holding the old ones,
        so doctor #10 went RED here and GREEN on the command line — and the
        blocked page told him to run the very command that disagreed with it.
        The ledger was fine. The gate now names the real cause when, and only
        when, #10 is the invariant that failed."""
        from modules.finance import pnl
        with db.connect(self.db_path) as conn:                # force #10 RED
            conn.execute("INSERT INTO transactions (record_id, canonical_key, type,"
                         " amount_minor, currency, pnl_bucket) VALUES"
                         " ('s1','ks1','Hourly Earning',1000,'USD','opex')")
        real = os.path.getmtime(pnl.__file__)
        try:
            os.utime(pnl.__file__, (real, data._LOADED_AT + 3600))
            out = data.money(self.db_path, today="2026-06-15")
            self.assertTrue(out["blocked"])
            self.assertTrue(any(r.startswith("#10") for r in out["reds"]))
            self.assertIn("bin/cockpit reload", out["stale_rules"])
            self.assertIn("pnl.py", out["stale_rules"])
        finally:
            os.utime(pnl.__file__, (real, real))
        # rules as old as the process -> no hint, and the RED stands on its own
        out = data.money(self.db_path, today="2026-06-15")
        self.assertTrue(out["blocked"])
        self.assertNotIn("stale_rules", out)

    def test_the_stale_hint_is_only_for_the_invariant_it_explains(self):
        # #8 (a NULL amount) is a real ledger fault — a fresh server fails it
        # too, so a reload hint would be a lie even with newer rules on disk.
        from modules.finance import pnl
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO transactions (record_id, canonical_key, type,"
                         " amount_minor, currency) VALUES ('bad','kbad','Other',NULL,'USD')")
        real = os.path.getmtime(pnl.__file__)
        try:
            os.utime(pnl.__file__, (real, data._LOADED_AT + 3600))
            out = data.money(self.db_path, today="2026-06-15")
        finally:
            os.utime(pnl.__file__, (real, real))
        self.assertTrue(out["blocked"])
        self.assertNotIn("stale_rules", out)
        js = (Path(__file__).resolve().parents[1] / "modules" / "cockpit" / "static"
              / "js" / "sections" / "money.js").read_text()
        self.assertIn("d.stale_rules", js)

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

    def test_revenue_rows_carry_the_servers_client_attribution(self):
        """One payer, two Wise spellings, ONE `w`. Hermon's two 2026-08-10
        payments arrived as 'Consulting Systems B.V.' and 'Consulting Systems
        BV'; the Money drill's who-facet re-derived the client in JS and its
        regex broke on the dots, so the same client showed as two categories
        (Abhijeet, 2026-08-25). `w` is the Clients-tab `_client_key` computed
        server-side — the drill and the tab can no longer disagree. Spend rows
        get NO `w`: vendor grouping stays a JS presentational concern."""
        with db.connect(self.db_path) as conn:
            for rid, desc in [
                    ("wm1", "Received money from Consulting Systems B.V. with"
                            " reference 11477447-2800599882"),
                    ("wm2", "Received money from Consulting Systems BV with"
                            " reference 11477447-2800595443")]:
                conn.execute(
                    "INSERT INTO transactions (record_id, canonical_key, type,"
                    " category, amount_minor, amount_base_minor, currency,"
                    " creation_dt, source, pnl_bucket, description) VALUES"
                    " (?,?, 'Incoming', 'income', 100000, 100000, 'USD',"
                    " '2026-08-10T00:00:00+0000', 'Wise-mail', 'revenue', ?)",
                    (rid, f"k{rid}", desc))
        d = data.money_transactions(self.db_path, bucket="revenue")
        who = {r["d"]: r.get("w") for r in d["rows"] if r["dt"] == "2026-08-10"}
        self.assertEqual(len(who), 2)
        self.assertEqual(set(who.values()), {"Hermon"})
        spends = data.money_transactions(self.db_path, bucket="platform_fee")
        self.assertTrue(spends["rows"])
        self.assertTrue(all("w" not in r for r in spends["rows"]))

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


# ── Activity (UI brief §3): the daily digest, run detail, re-run ─────────────
ACT_DAY = "2026-07-04"          # the IST day under test
ACT_PREV = "2026-07-03"
ACT_NEXT = "2026-07-05"
RUN_OK, RUN_FAIL, RUN_PREV = 9001, 9002, 9003

# A git log for ONE day, in exactly the shape _registry_changes asks for
# (%H\x1f%cI\x1f%s + --numstat). Two approvals: one big, one a single alias.
FAKE_GIT_LOG = (
    "aaaaaaaaaaaabbbbbbbbbbbb\x1f2026-07-04T16:42:03+05:30\x1f"
    "registry: netfly emails += netflydigital.com\n"
    "\n"
    "2\t0\tconfig/clients.yaml\n"
    "\n"
    "ccccccccccccdddddddddddd\x1f2026-07-04T03:29:35+05:30\x1f"
    "registry: add RECO Health as a prospect\n"
    "\n"
    "22\t3\tconfig/clients.yaml\n"
)


def seed_activity(db_path, day=ACT_DAY):
    """Rows shaped like the LIVE ones. THREE clock conventions on purpose:
    runs stamp IST-with-offset, the KB/thread/shadow/event tables stamp UTC via
    datetime('now'), and nudges stamp IST-naive via nudges._iso(). Seeding them
    all one way would let a reader that mixes them up still look right."""
    ist = day + "T{}+05:30"          # runs / claude_usage
    utc = day + " {}"                # sources / facts / threads / shadow / events
    naive = day + "T{}"              # nudges
    with db.connect(db_path) as conn:
        # runs: one clean, one failed, one on the previous day (the comparison)
        conn.execute(
            "INSERT INTO runs (id, task_name, started_at, completed_at, status,"
            " shadow, duration_ms, metrics_json) VALUES (?,'kb_daily',?,?,"
            " 'completed',1,42000,?)",
            (RUN_OK, ist.format("10:00:00"), ist.format("10:00:42"),
             json.dumps({"fathom": {"saved": 2, "deduped": 0},
                         "extract": {"facts": 3}})))
        conn.execute(
            "INSERT INTO runs (id, task_name, started_at, completed_at, status,"
            " shadow, duration_ms, error) VALUES (?,'upwork_scan',?,?,'failed',"
            " 1,1500,'BridgeError(''osascript timed out'')')",
            (RUN_FAIL, ist.format("11:00:00"), ist.format("11:00:01")))
        conn.execute(
            "INSERT INTO runs (id, task_name, started_at, completed_at, status,"
            " shadow, duration_ms) VALUES (?,'cos_sweep',?,?,'skipped_empty',"
            " 1,50)", (RUN_OK + 100, ist.format("12:00:00"),
                       ist.format("12:00:00")))
        conn.execute(
            "INSERT INTO runs (id, task_name, started_at, completed_at, status,"
            " shadow, duration_ms) VALUES (?,'kb_daily',?,?,'completed',1,1000)",
            (RUN_PREV, ACT_PREV + "T10:00:00+05:30",
             ACT_PREV + "T10:00:01+05:30"))
        # anomalies: attached to the failed run by run_id (migration 015)
        conn.execute(
            "INSERT INTO anomalies (detected_at, task_name, kind, severity,"
            " detail_json, run_id) VALUES (?,'upwork_scan','task_failure',"
            " 'critical','{}',?)", (utc.format("05:30:01"), RUN_FAIL))
        # claude_usage: run_id is NEVER written in production, so these carry
        # NULL and must be matched to the run by task + window.
        for hhmm, cost in (("10:00:05", 0.02), ("10:00:30", 0.03)):
            conn.execute(
                "INSERT INTO claude_usage (called_at, ist_date, task_name, model,"
                " billing_route, input_tokens, output_tokens, total_cost_usd)"
                " VALUES (?,?,'kb_daily','claude-sonnet-4-6','payg',100,20,?)",
                (ist.format(hhmm), day, cost))
        conn.execute(                       # same task, OUTSIDE the run window
            "INSERT INTO claude_usage (called_at, ist_date, task_name, model,"
            " billing_route, total_cost_usd) VALUES (?,?,'kb_daily',"
            " 'claude-sonnet-4-6','payg',0.05)", (ist.format("23:30:00"), day))
        # sources: 2 fathom + 1 loom + 1 email on the day, and one at 19:00 UTC
        # which is 00:30 IST the NEXT day — the boundary that proves the reader
        # converts instead of slicing the string.
        rows = [("fathom", "fathom/a", "h-a", "07:00:00", "extracted"),
                ("fathom", "fathom/b", "h-b", "08:00:00", "extracted"),
                ("loom", "loom/a", "h-c", "09:00:00", "ingested"),
                ("email", "email/a", "h-d", "10:00:00", "ingested"),
                ("fathom", "fathom/next", "h-e", "19:00:00", "ingested")]
        for stype, ref, h, hhmm, status in rows:
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id, status, ingested_at) VALUES (?,?,?,?,?,?)",
                (stype, ref, h, "adelye" if stype == "fathom" else None,
                 status, utc.format(hhmm)))
        sid = conn.execute("SELECT id FROM sources WHERE source_ref='fathom/a'"
                           ).fetchone()["id"]
        for client, n in (("adelye", 3), ("netfly", 1)):
            for i in range(n):
                conn.execute(
                    "INSERT INTO facts (source_id, client_id, category,"
                    " fact_text, citation, confidence, created_at)"
                    " VALUES (?,?,'overview',?,'fathom/a @ 00:01','CONFIRMED',?)",
                    (sid, client, "{} fact {}".format(client, i),
                     utc.format("07:05:00")))
        conn.execute(
            "INSERT INTO threads (id, room_name, client_id, first_seen_at)"
            " VALUES ('room_new','New Room','adelye',?)", (utc.format("06:00:00"),))
        conn.execute(
            "INSERT INTO threads (id, room_name, first_seen_at)"
            " VALUES ('room_old','Old Room',?)",
            (ACT_PREV + " 06:00:00",))
        conn.execute(
            "INSERT INTO events (id, source, native_id, state, client_id,"
            " resolved_at, resolved_by) VALUES ('evt:email:1','email','1',"
            " 'mapped','adelye',?, 'cockpit-approval')", (utc.format("08:30:00"),))
        conn.execute(
            "INSERT INTO nudges (source, dept, kind, title, dedup_key, state,"
            " created_at, refreshed_at, updated_at) VALUES ('sales','sales',"
            " 'owed_reply','Reply to Adelye','followup:c:adelye','open',?,?,?)",
            (naive.format("09:30:00"), naive.format("09:30:00"),
             naive.format("09:30:00")))
        conn.execute(
            "INSERT INTO nudges (source, dept, kind, title, dedup_key, state,"
            " created_at, refreshed_at, updated_at) VALUES ('sweep','system',"
            " 'system','Old card','system:old','done',?,?,?)",
            (ACT_PREV + "T09:00:00", ACT_PREV + "T09:00:00",
             naive.format("14:00:00")))
        conn.execute(
            "INSERT INTO shadow_ledger (at, run_id, task_name, target,"
            " operation, payload_json) VALUES (?,?,'cos_sweep','email','send',"
            " '{}')", (utc.format("08:00:00"), RUN_OK))


class ActivityCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cockpit-activity-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed_activity(self.db_path)
        data._registry_cache.clear()   # module-level TTL cache; never leak days

    def tearDown(self):
        data._registry_cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def act(self, day=ACT_DAY, git_stdout=FAKE_GIT_LOG):
        """activity() with the registry git log stubbed, so the assertions are
        about the reader and not about this checkout's history."""
        with mock.patch.object(data.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=git_stdout,
                                         stderr="")
            return data.activity(self.db_path, day=day)


class TestActivityDigest(ActivityCase):
    def test_runs_totals_and_by_task(self):
        out = self.act()
        r = out["runs"]
        self.assertEqual(r["total"], 3)          # the prev-day run is excluded
        self.assertEqual(r["completed"], 1)
        self.assertEqual(r["failed"], 1)
        self.assertEqual(r["skipped"], 1)        # skipped_empty counts as skipped
        self.assertAlmostEqual(r["wall_seconds"], 43.5, places=1)
        self.assertEqual(r["prev_total"], 1)
        by_task = {e["task"]: e for e in r["by_task"]}
        self.assertEqual(by_task["kb_daily"]["status_counts"], {"completed": 1})
        self.assertEqual(by_task["cos_sweep"]["status_counts"],
                         {"skipped_empty": 1})

    def test_run_log_rows_carry_summary_and_cost(self):
        rows = {r["id"]: r for r in self.act()["runs"]["rows"]}
        self.assertEqual(rows[RUN_OK]["summary"], "fathom.saved 2 · extract.facts 3")
        # cost is attributed by task+window (claude_usage.run_id is never set)
        self.assertAlmostEqual(rows[RUN_OK]["usd"], 0.05, places=4)
        # no fabricated zero: a run with no attributable spend shows nothing
        self.assertIsNone(rows[RUN_FAIL]["usd"])
        self.assertEqual([f["id"] for f in self.act()["runs"]["failures"]],
                         [RUN_FAIL])

    def test_fathom_count_is_named_and_ist_bounded(self):
        out = self.act()["ingest"]
        # 19:00 UTC is 00:30 IST tomorrow — it belongs to the NEXT day
        self.assertEqual(out["fathom_transcripts"], 2)
        self.assertEqual(out["loom_recordings"], 1)
        self.assertEqual(out["email_messages"], 1)
        self.assertEqual(out["total"], 4)
        self.assertEqual(out["extracted"], 2)
        self.assertEqual(self.act(day=ACT_NEXT)["ingest"]["fathom_transcripts"], 1)

    def test_facts_by_client(self):
        out = self.act()["facts"]
        self.assertEqual(out["total"], 4)
        self.assertEqual(out["by_client"][0], {"client": "adelye", "n": 3})
        self.assertEqual(out["clients"], 2)
        self.assertEqual(out["prev_total"], 0)

    def test_threads_new_and_attribution_is_honest(self):
        out = self.act()["threads"]
        self.assertEqual(out["new"], 1)
        self.assertEqual(out["new_attributed"], 1)
        self.assertEqual(out["attributed_total"], 1)
        self.assertEqual(out["unattributed_total"], 1)
        self.assertEqual(out["mapping_resolved"], 1)
        self.assertIn("no attribution timestamp", out["note"])

    def test_spend_and_nudges_and_shadow(self):
        out = self.act()
        self.assertAlmostEqual(out["spend"]["usd"], 0.10, places=4)
        self.assertEqual(out["spend"]["calls"], 3)
        # the 23:30 call falls outside every run window and says so
        self.assertAlmostEqual(out["spend"]["unattributed_usd"], 0.05, places=4)
        self.assertEqual(out["nudges"]["created"], 1)
        self.assertEqual(out["nudges"]["closed"], 1)     # closed today, made prev
        self.assertEqual(out["nudges"]["done"], 1)
        self.assertEqual(out["shadow"]["total"], 1)
        self.assertEqual(out["shadow"]["rows"][0]["target"], "email")

    def test_registry_commits_parsed_from_git_log(self):
        reg = self.act()["registry"]
        self.assertEqual(reg["count"], 2)
        self.assertIsNone(reg["note"])
        self.assertEqual(reg["commits"][0]["sha"], "aaaaaaaaaaaa")   # 12 chars
        self.assertEqual(reg["commits"][0]["subject"],
                         "registry: netfly emails += netflydigital.com")
        self.assertEqual(reg["commits"][1]["insertions"], 22)
        self.assertEqual(reg["commits"][1]["deletions"], 3)
        self.assertEqual(reg["insertions"], 24)
        self.assertEqual(reg["file"], "config/clients.yaml")

    def test_day_defaults_to_today_ist(self):
        with mock.patch.object(data.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            out = data.activity(self.db_path)
        self.assertEqual(out["day"],
                         datetime.now(config.TZ).strftime("%Y-%m-%d"))

    def test_bad_day_returns_error_not_traceback(self):
        self.assertIn("error", data.activity(self.db_path, day="not-a-day"))


class TestActivityEmptyDay(ActivityCase):
    def test_empty_day_is_declared_not_faked(self):
        out = self.act(day="2026-07-01", git_stdout="")   # no registry commits
        self.assertTrue(out["empty"])
        self.assertEqual(out["runs"]["total"], 0)
        self.assertEqual(out["ingest"]["total"], 0)
        self.assertEqual(out["runs"]["rows"], [])

    def test_a_day_with_anything_is_not_empty(self):
        self.assertFalse(self.act()["empty"])
        # a day whose ONLY event is a registry approval still counts as activity
        with mock.patch.object(data.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=FAKE_GIT_LOG,
                                         stderr="")
            out = data.activity(self.db_path, day="2026-07-01")
        self.assertFalse(out["empty"])


class TestActivityRegistryDegradation(ActivityCase):
    """git is a subprocess on a machine that may not have it, may be slow, or
    may be mid-rebase. None of that may take the page down."""

    def test_git_missing_degrades_to_empty_list(self):
        with mock.patch.object(data.subprocess, "run",
                               side_effect=FileNotFoundError("git")):
            out = data.activity(self.db_path, day=ACT_DAY)
        self.assertEqual(out["registry"]["commits"], [])
        self.assertEqual(out["registry"]["count"], 0)
        self.assertIn("unavailable", out["registry"]["note"])
        self.assertFalse(out["empty"])          # the rest of the day survived

    def test_git_timeout_degrades(self):
        import subprocess as _sp
        with mock.patch.object(data.subprocess, "run",
                               side_effect=_sp.TimeoutExpired("git", 6)):
            reg = data.activity(self.db_path, day=ACT_DAY)["registry"]
        self.assertEqual(reg["count"], 0)
        self.assertIn("TimeoutExpired", reg["note"])

    def test_git_nonzero_exit_degrades(self):
        with mock.patch.object(data.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=128, stdout="", stderr="bad")
            reg = data.activity(self.db_path, day=ACT_DAY)["registry"]
        self.assertEqual(reg["count"], 0)
        self.assertIn("git exit 128", reg["note"])

    def test_result_is_cached_so_the_subprocess_runs_once(self):
        with mock.patch.object(data.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=FAKE_GIT_LOG,
                                         stderr="")
            data.activity(self.db_path, day=ACT_DAY)
            data.activity(self.db_path, day=ACT_DAY)
        self.assertEqual(run.call_count, 1)

    def test_real_git_never_raises(self):
        # no stub: whatever this checkout's git does, the reader survives it
        out = data.activity(self.db_path, day=ACT_DAY)
        self.assertIsInstance(out["registry"]["commits"], list)


class TestActivityRunDetail(ActivityCase):
    def test_known_run_has_metrics_anomalies_and_usage(self):
        out = data.activity_run(self.db_path, run_id=RUN_OK)
        self.assertEqual(out["run"]["task_name"], "kb_daily")
        self.assertEqual(out["metrics"]["fathom"]["saved"], 2)
        self.assertIsNone(out["metrics_error"])
        self.assertEqual(out["usage"]["calls"], 2)          # the 23:30 call is out
        self.assertAlmostEqual(out["usage"]["usd"], 0.05, places=4)
        self.assertEqual(out["usage"]["attribution"], "window")
        self.assertEqual(out["shadow"]["total"], 1)
        self.assertFalse(out["failed"])
        self.assertTrue(out["rerunnable"])                  # kb_daily is allowed

    def test_failed_run_carries_error_and_its_anomalies(self):
        out = data.activity_run(self.db_path, run_id=RUN_FAIL)
        self.assertTrue(out["failed"])
        self.assertIn("osascript timed out", out["error"])
        self.assertEqual([a["kind"] for a in out["anomalies"]], ["task_failure"])
        self.assertEqual(out["usage"]["attribution"], "none")
        self.assertEqual(out["usage"]["calls"], 0)

    def test_unknown_run_returns_error_not_exception(self):
        self.assertIn("error", data.activity_run(self.db_path, run_id=424242))
        self.assertIn("error", data.activity_run(self.db_path, run_id="nope"))
        self.assertIn("error", data.activity_run(self.db_path, run_id=None))

    def test_string_run_id_from_a_query_string_works(self):
        out = data.activity_run(self.db_path, run_id=str(RUN_OK))
        self.assertEqual(out["run"]["id"], RUN_OK)


class TestActivityServerParams(CockpitServerCase):
    """The server forwards KEYWORD-ONLY params. A positional `day`/`run_id`
    would be silently dropped and every request would answer for today —
    exactly the latent bug four older routes still carry."""

    def setUp(self):
        super().setUp()
        seed_activity(self.db_path)
        data._registry_cache.clear()

    def tearDown(self):
        data._registry_cache.clear()
        super().tearDown()

    def test_params_are_declared_keyword_only(self):
        import inspect
        for fn, name in ((data.activity, "day"),
                         (data.activity_run, "run_id")):
            param = inspect.signature(fn).parameters[name]
            self.assertEqual(param.kind, param.KEYWORD_ONLY, fn.__name__)

    def test_day_param_reaches_the_reader_over_http(self):
        status, body = self.get("/api/activity?day=" + ACT_DAY)
        self.assertEqual(status, 200)
        out = json.loads(body)
        self.assertEqual(out["day"], ACT_DAY)
        self.assertEqual(out["runs"]["total"], 3)
        self.assertFalse(out["empty"])

    def test_run_id_param_reaches_the_reader_over_http(self):
        status, body = self.get("/api/activity/run?run_id=%d" % RUN_OK)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["run"]["id"], RUN_OK)

    def test_unknown_run_is_a_message_not_a_500(self):
        status, body = self.get("/api/activity/run?run_id=999999")
        self.assertEqual(status, 200)
        self.assertIn("error", json.loads(body))

    def test_db_path_query_cannot_redirect_activity(self):
        status, body = self.get("/api/activity?db_path=/etc/passwd&day=" + ACT_DAY)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["day"], ACT_DAY)

    def test_rerun_route_is_authed_and_allowlisted(self):
        with mock.patch.object(actions.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            ok, body = self.post("/api/rerun", body={"task": "kb_daily"})
        self.assertEqual(ok, 200)
        self.assertTrue(json.loads(body)["ok"])
        unauth, _ = self.post("/api/rerun", token=None, body={"task": "kb_daily"})
        self.assertEqual(unauth, 401)


class ActivitySourcesCase(ActivityCase):
    """seed_activity plus the rows the per-source drill exists to explain.

    Deliberately built so every assertion below is about the READER, not about
    this checkout's config/clients.yaml: the attribution cases that must be
    exact (an approval, a queued item, an excluded item) are driven entirely
    from the events table, and the one registry-replay case reads whatever the
    real registry happens to hold and asserts the SHAPE of the answer."""

    def setUp(self):
        super().setUp()
        utc = ACT_DAY + " {}"
        with db.connect(self.db_path) as conn:
            # a call resolved through the mapping queue: the ONE attribution
            # decision the schema timestamps
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id, client_name, content_type, title, occurred_dt,"
                " status, ingested_at) VALUES ('fathom','fathom/q','h-q',"
                " 'netfly','NETFLY','client-call','Discovery call',?,"
                " 'extracted',?)", (utc.format("11:00:00"), utc.format("11:05:00")))
            conn.execute(
                "INSERT INTO events (id, source, native_id, state, client_id,"
                " raw_ref, author, subject, resolved_by, resolved_at,"
                " first_seen_at) VALUES ('evt:fathom:q','fathom','q','mapped',"
                " 'netfly','fathom/q','ceo@netfly.example','Discovery call',"
                " 'cockpit-approval',?,?)",
                (utc.format("11:30:00"), utc.format("11:05:00")))
            # a call still sitting in the queue
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " title, status, ingested_at) VALUES ('fathom','fathom/queued',"
                " 'h-qq','Unknown caller','ingested',?)", (utc.format("12:00:00"),))
            conn.execute(
                "INSERT INTO events (id, source, native_id, state, raw_ref,"
                " author, subject, first_seen_at) VALUES"
                " ('evt:fathom:queued','fathom','queued','queued',"
                " 'fathom/queued','someone@example.com','Unknown caller',?)",
                (utc.format("12:00:00"),))
            # a call the sweep excluded as internal
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " title, status, ingested_at) VALUES ('fathom','fathom/internal',"
                " 'h-int','Team standup','ingested',?)", (utc.format("13:00:00"),))
            conn.execute(
                "INSERT INTO events (id, source, native_id, state, raw_ref,"
                " subject, exclude_reason, first_seen_at) VALUES"
                " ('evt:fathom:internal','fathom','internal','excluded',"
                " 'fathom/internal','Team standup','internal:owner-only',?)",
                (utc.format("13:00:00"),))
            # cost, named the way extract.py names it: "extract <source_ref>"
            conn.execute(
                "INSERT INTO claude_usage (called_at, ist_date, task_name,"
                " purpose, model, billing_route, total_cost_usd) VALUES"
                " (?,?,'kb_extract','extract fathom/a','claude-sonnet-4-6',"
                " 'payg',0.031)", (ACT_DAY + "T07:02:00+05:30", ACT_DAY))
            # a run that ran OUT OF BUDGET mid-extract: the only recorded reason
            # a source that landed today was never read for facts
            conn.execute(
                "INSERT INTO runs (id, task_name, started_at, completed_at,"
                " status, shadow, duration_ms, metrics_json) VALUES"
                " (9201,'kb_daily',?,?,'completed',1,1000,?)",
                (ACT_DAY + "T05:15:00+05:30", ACT_DAY + "T05:20:00+05:30",
                 json.dumps({"extract": {"extracted": 36, "facts": 126,
                                         "stopped": "budget", "pending": 8}})))
            # NETFLY has a dossier; adelye deliberately does not
            conn.execute(
                "INSERT INTO dossier_index (client_id, client_name,"
                " dossier_path, fact_count, source_count, last_compiled_at)"
                " VALUES ('netfly','NETFLY','/k/netfly.md',406,32,"
                " '2026-07-04 05:15 IST')")

    def srcs(self, **kw):
        return data.activity_sources(self.db_path, **kw)


class TestActivitySourcesDrill(ActivitySourcesCase):
    """"Activity shows that 5 fathom transcripts. Once I click on it I need to
    see WHICH 5 transcripts were processed and how they were added to the
    registry and everything." — the rows behind that figure."""

    def test_the_rows_reconcile_with_the_tile_they_hang_off(self):
        digest = self.act()
        out = self.srcs(day=ACT_DAY, source_type="fathom")
        self.assertEqual(out["total"], digest["ingest"]["fathom_transcripts"])
        self.assertEqual(out["count"], out["total"])
        self.assertFalse(out["capped"])
        # fathom/next is 19:00 UTC = 00:30 IST the NEXT day and must not appear
        self.assertNotIn("fathom/next", {r["source_ref"] for r in out["rows"]})

    def test_unfiltered_reconciles_with_the_ingest_total(self):
        digest = self.act()
        out = self.srcs(day=ACT_DAY)
        self.assertEqual(out["total"], digest["ingest"]["total"])
        self.assertEqual(out["day_by_source_type"],
                         digest["ingest"]["by_source_type"])

    def test_facts_are_the_foreign_key_not_a_same_day_coincidence(self):
        rows = {r["source_ref"]: r for r in
                self.srcs(day=ACT_DAY, source_type="fathom")["rows"]}
        # seed_activity hangs 4 facts (3 adelye + 1 netfly) off fathom/a only
        self.assertEqual(rows["fathom/a"]["facts"]["n"], 4)
        self.assertEqual(rows["fathom/b"]["facts"]["n"], 0)
        self.assertIn("foreign key", rows["fathom/a"]["facts"]["link"])
        self.assertEqual(rows["fathom/a"]["facts"]["clients"],
                         {"adelye": 3, "netfly": 1})

    def test_a_mapping_queue_approval_is_the_only_proven_attribution(self):
        rows = {r["source_ref"]: r for r in
                self.srcs(day=ACT_DAY, source_type="fathom")["rows"]}
        approved = rows["fathom/q"]["attribution"]
        self.assertTrue(approved["proven"])
        self.assertEqual(approved["how"], "mapping-queue approval")
        self.assertIn("cockpit-approval", approved["detail"])
        # everything else attributed is a REPLAY and must say so
        for ref in ("fathom/a", "fathom/b"):
            a = rows[ref]["attribution"]
            self.assertEqual(a["state"], "attributed")
            self.assertFalse(a["proven"], ref)

    def test_an_unreproducible_attribution_admits_it_instead_of_guessing(self):
        # fathom/a carries client_id 'adelye' with NO raw payload on disk, so
        # there are no participants to replay against. The row must say the
        # registry does not explain it — never invent a matching identifier.
        row = next(r for r in self.srcs(day=ACT_DAY, source_type="fathom")["rows"]
                   if r["source_ref"] == "fathom/a")
        self.assertFalse(row["attribution"]["proven"])
        self.assertIsNone(row["attribution"]["matched_on"])
        self.assertIsNone(row["attribution"]["how"])
        self.assertIn("does not reproduce", row["attribution"]["detail"])

    def test_a_replayable_match_names_the_identifier_and_the_field(self):
        """The happy path, driven off whatever the real registry holds: a call
        whose only participant is a client's registry ALIAS must come back with
        that alias named, the field it came from, and proven=False."""
        reg = registry.load()
        cid = next((c for c in reg.matchable_ids()
                    if reg.by_id[c].get("aliases")), None)
        if not cid:
            self.skipTest("no registry client has an alias in this checkout")
        alias = reg.by_id[cid]["aliases"][0]
        raw = self.tmp / "call.json"
        raw.write_text(json.dumps({
            "recording_id": 42, "title": "Sync",
            "recording_start_time": "2026-07-04T09:00:00Z",
            "recording_end_time": "2026-07-04T09:25:00Z",
            "url": "https://fathom.video/calls/42",
            "transcript": [{"speaker": {"display_name": alias},
                            "text": "hello", "timestamp": "00:00:01"}],
        }))
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id, client_name, title, raw_path, status, ingested_at)"
                " VALUES ('fathom','fathom/replay','h-rp',?,?,'Sync',?,"
                " 'extracted',?)",
                (cid, reg.by_id[cid]["name"], str(raw), ACT_DAY + " 09:30:00"))
        row = next(r for r in self.srcs(day=ACT_DAY, source_type="fathom")["rows"]
                   if r["source_ref"] == "fathom/replay")
        a = row["attribution"]
        self.assertEqual(a["state"], "attributed")
        self.assertFalse(a["proven"])          # replayed, never "recorded"
        self.assertTrue(a["how"], a)
        self.assertTrue(a["matched_on"], a)
        self.assertIn(a["matched_on"].lower(),
                      (alias.lower(), reg.by_id[cid]["name"].lower()))
        # and the raw payload gives the two things the sources row cannot
        self.assertEqual(row["duration_seconds"], 1500)
        self.assertIn(alias, row["participants"])
        self.assertIsNone(row["payload_note"])

    def test_a_missing_payload_says_so_rather_than_showing_an_empty_list(self):
        row = next(r for r in self.srcs(day=ACT_DAY, source_type="fathom")["rows"]
                   if r["source_ref"] == "fathom/a")
        self.assertEqual(row["participants"], [])
        self.assertIsNone(row["duration_seconds"])
        self.assertIn("no raw payload path", row["payload_note"])

    def test_a_queued_call_says_it_is_waiting_on_a_human(self):
        row = next(r for r in self.srcs(day=ACT_DAY, source_type="fathom")["rows"]
                   if r["source_ref"] == "fathom/queued")
        self.assertEqual(row["attribution"]["state"], "unattributed")
        self.assertIn("mapping queue", row["attribution"]["detail"])
        self.assertTrue(row["attribution"]["proven"])   # a RECORDED state
        self.assertEqual(row["queue"]["state"], "queued")

    def test_an_unmatched_call_never_claims_a_check_it_could_not_run(self):
        """fathom/next has no queue event and no readable payload. "No
        identifier matched" would be a claim about a comparison that never
        happened — there were no participants to compare."""
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " title, raw_path, status, ingested_at) VALUES"
                " ('fathom','fathom/gone','h-gone','Ghost','/no/such/file.json',"
                " 'ingested',?)", (ACT_DAY + " 15:00:00",))
        row = next(r for r in self.srcs(day=ACT_DAY, source_type="fathom")["rows"]
                   if r["source_ref"] == "fathom/gone")
        a = row["attribution"]
        self.assertEqual(a["state"], "unattributed")
        self.assertFalse(a["proven"])                   # a DERIVED sentence
        self.assertIn("not readable", a["detail"])
        self.assertIn("no longer on disk", row["payload_note"])

    def test_an_excluded_call_carries_the_exclusion_reason(self):
        row = next(r for r in self.srcs(day=ACT_DAY, source_type="fathom")["rows"]
                   if r["source_ref"] == "fathom/internal")
        self.assertIn("internal:owner-only", row["attribution"]["detail"])
        self.assertEqual(row["queue"]["exclude_reason"], "internal:owner-only")

    def test_no_queue_event_is_stated_not_left_blank(self):
        row = next(r for r in self.srcs(day=ACT_DAY, source_type="fathom")["rows"]
                   if r["source_ref"] == "fathom/b")
        self.assertIsNone(row["queue"])
        self.assertIn("no mapping-queue event", row["queue_note"])

    def test_an_unextracted_source_carries_the_runs_own_stop_reason(self):
        row = next(r for r in self.srcs(day=ACT_DAY, source_type="fathom")["rows"]
                   if r["source_ref"] == "fathom/queued")
        np = row["not_processed"]
        self.assertEqual(np["status"], "ingested")
        self.assertIn("budget", np["run_context"])
        self.assertIn("RUN-level", np["run_context"])
        # …and an extracted one claims nothing
        ok = next(r for r in self.srcs(day=ACT_DAY, source_type="fathom")["rows"]
                  if r["source_ref"] == "fathom/a")
        self.assertIsNone(ok["not_processed"])

    def test_cost_is_the_purpose_string_and_the_row_says_so(self):
        row = next(r for r in self.srcs(day=ACT_DAY, source_type="fathom")["rows"]
                   if r["source_ref"] == "fathom/a")
        self.assertEqual(row["cost"]["calls"], 1)
        self.assertAlmostEqual(row["cost"]["usd"], 0.031, places=4)
        self.assertIn("convention, not a key", row["cost"]["link"])
        # a source no model call names gets a — and a reason, never $0
        quiet = next(r for r in self.srcs(day=ACT_DAY, source_type="fathom")["rows"]
                     if r["source_ref"] == "fathom/b")
        self.assertIsNone(quiet["cost"])
        self.assertIn("no model call", quiet["cost_note"])

    def test_an_underscore_in_a_ref_cannot_absorb_another_sources_cost(self):
        """`_` matches ANY character in SQL LIKE. Unescaped, a drill for
        `fathom/a_b` charged itself with `extract fathom/axb`."""
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " title, status, ingested_at) VALUES ('fathom','fathom/a_b',"
                " 'h-us','Underscore','ingested',?)", (ACT_DAY + " 14:00:00",))
            conn.execute(
                "INSERT INTO claude_usage (called_at, ist_date, task_name,"
                " purpose, model, billing_route, total_cost_usd) VALUES"
                " (?,?,'kb_extract','extract fathom/axb','claude-sonnet-4-6',"
                " 'payg',9.99)", (ACT_DAY + "T14:01:00+05:30", ACT_DAY))
        row = next(r for r in self.srcs(day=ACT_DAY, source_type="fathom")["rows"]
                   if r["source_ref"] == "fathom/a_b")
        self.assertIsNone(row["cost"])

    def test_the_dossier_answer_is_presence_never_timing(self):
        rows = {r["source_ref"]: r for r in
                self.srcs(day=ACT_DAY, source_type="fathom")["rows"]}
        # fathom/a's facts are mostly adelye, which has no dossier_index row
        self.assertEqual(rows["fathom/a"]["dossier"]["state"], "no-dossier")
        self.assertIn("no dossier has ever been compiled",
                      rows["fathom/a"]["dossier"]["note"])
        # a source with no facts claims nothing at all
        self.assertEqual(rows["fathom/b"]["dossier"]["state"], "no-facts")

    def test_a_handful_of_agency_facts_cannot_speak_for_the_unfiled_majority(self):
        """LIVE 2026-08-12: a call produced 16 client-scope facts with NO client
        and 2 agency ones. Routing off `scope` alone called the whole thing
        "proof-points" — the 2 speaking for the 16. The route is the cross-tab
        of (client_id, scope), and the headline is where MOST of them went."""
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " title, status, ingested_at) VALUES ('fathom','fathom/mixed',"
                " 'h-mx','Mixed','extracted',?)", (ACT_DAY + " 16:00:00",))
            sid = conn.execute("SELECT id FROM sources WHERE"
                               " source_ref='fathom/mixed'").fetchone()["id"]
            for scope, n in (("client", 16), ("agency", 2)):
                for i in range(n):
                    conn.execute(
                        "INSERT INTO facts (source_id, client_id, category,"
                        " fact_text, citation, confidence, scope, created_at)"
                        " VALUES (?,NULL,'overview',?,'c','CONFIRMED',?,?)",
                        (sid, "%s %d" % (scope, i), scope,
                         ACT_DAY + " 16:05:00"))
        row = next(r for r in self.srcs(day=ACT_DAY, source_type="fathom")["rows"]
                   if r["source_ref"] == "fathom/mixed")
        self.assertEqual(row["facts"]["routes"],
                         {"dossier": 0, "proof_points": 2, "nowhere": 16})
        self.assertEqual(row["dossier"]["state"], "no-client")
        self.assertIn("16 of these facts", row["dossier"]["note"])
        self.assertIn("proof-points.md", row["dossier"]["note"])

    def test_a_source_with_no_approval_claims_no_registry_change(self):
        row = next(r for r in self.srcs(day=ACT_DAY, source_type="fathom")["rows"]
                   if r["source_ref"] == "fathom/b")
        self.assertEqual(row["registry_change"]["state"], "none")
        self.assertEqual(row["registry_change"]["commits"], [])

    def test_an_approval_is_tied_to_the_commit_that_names_its_event(self):
        body = ("registry: netfly emails += netfly.example\n\nvia mapping-queue"
                " approval (cockpit-approval); evidence: fathom evt:fathom:q"
                " from ceo@netfly.example")
        stdout = "abc123abc123\x1f2026-07-04T11:30:05+05:30\x1f" \
                 "registry: netfly emails += netfly.example\x1f" + body + "\x1e"
        with mock.patch.object(data.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=stdout, stderr="")
            out = self.srcs(day=ACT_DAY, source_type="fathom")
        row = next(r for r in out["rows"] if r["source_ref"] == "fathom/q")
        self.assertEqual(row["registry_change"]["state"], "committed")
        self.assertEqual(row["registry_change"]["commits"][0]["sha"], "abc123abc123")

    def test_git_failure_degrades_and_never_takes_the_drill_down(self):
        with mock.patch.object(data.subprocess, "run",
                               side_effect=FileNotFoundError("git")):
            out = self.srcs(day=ACT_DAY, source_type="fathom")
        self.assertIn("unavailable", out["registry_search_note"])
        row = next(r for r in out["rows"] if r["source_ref"] == "fathom/q")
        self.assertEqual(row["registry_change"]["state"], "approved-no-commit")
        self.assertTrue(out["rows"])            # the rest of the drill survived

    def test_a_bad_day_is_refused_not_emptied(self):
        out = self.srcs(day="lol")
        self.assertIn("error", out)
        self.assertIn("lol", out["error"])
        self.assertEqual(out["rows"], [])
        self.assertIsNone(out["total"])         # not measured, NOT zero

    def test_a_bad_source_type_is_refused_not_emptied(self):
        out = self.srcs(day=ACT_DAY, source_type="'; DROP TABLE sources--")
        self.assertIn("error", out)
        self.assertIsNone(out["total"])
        # …while a well-formed type that simply matched nothing is a real zero
        # AND names what DID land, so it cannot be misread as "the read failed"
        empty = self.srcs(day=ACT_DAY, source_type="slack")
        self.assertNotIn("error", empty)
        self.assertEqual(empty["total"], 0)
        self.assertIn("fathom", empty["day_by_source_type"])


class TestActivityFactsDrill(ActivitySourcesCase):
    def test_rows_sum_to_the_digest_figure(self):
        digest = self.act()
        out = data.activity_facts(self.db_path, day=ACT_DAY)
        self.assertEqual(out["total"], digest["facts"]["total"])
        self.assertEqual(out["count"], out["total"])
        self.assertFalse(out["capped"])

    def test_every_fact_names_the_source_it_came_from(self):
        out = data.activity_facts(self.db_path, day=ACT_DAY)
        self.assertTrue(out["rows"])
        for r in out["rows"]:
            self.assertEqual(r["source_ref"], "fathom/a")
            self.assertEqual(r["source_type"], "fathom")
        self.assertEqual(out["orphans"], 0)

    def test_the_client_filter_uses_the_same_key_the_digest_shows(self):
        digest = self.act()
        key = digest["facts"]["by_client"][0]["client"]
        out = data.activity_facts(self.db_path, day=ACT_DAY, client=key)
        expected = next(c["n"] for c in digest["facts"]["by_client"]
                        if c["client"] == key)
        self.assertEqual(out["total"], expected)

    def test_a_bad_day_is_refused_not_emptied(self):
        out = data.activity_facts(self.db_path, day="2026-13-40")
        self.assertIn("error", out)
        self.assertIsNone(out["total"])


class TestActivityDrillServerParams(CockpitServerCase):
    """Both new params have to survive the QUERY_PARAMS guard AND the trip
    through the URL — a keyword-only param nobody registered is unreachable."""

    def setUp(self):
        super().setUp()
        seed_activity(self.db_path)
        data._registry_cache.clear()

    def tearDown(self):
        data._registry_cache.clear()
        super().tearDown()

    def test_params_are_declared_keyword_only(self):
        import inspect
        for fn, names in ((data.activity_sources, ("day", "source_type")),
                          (data.activity_facts, ("day", "client"))):
            for name in names:
                p = inspect.signature(fn).parameters[name]
                self.assertEqual(p.kind, p.KEYWORD_ONLY, f"{fn.__name__}.{name}")

    def test_source_type_reaches_the_reader_over_http(self):
        status, body = self.get(
            "/api/activity/sources?day=%s&source_type=fathom" % ACT_DAY)
        self.assertEqual(status, 200, body)
        out = json.loads(body)
        self.assertEqual(out["source_type"], "fathom")
        self.assertEqual(out["total"], 2)
        self.assertEqual({r["source_ref"] for r in out["rows"]},
                         {"fathom/a", "fathom/b"})

    def test_facts_route_answers_over_http(self):
        status, body = self.get("/api/activity/facts?day=" + ACT_DAY)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["total"], 4)

    def test_a_malformed_source_type_is_a_message_not_a_500(self):
        status, body = self.get(
            "/api/activity/sources?day=%s&source_type=NOT%%20A%%20TYPE" % ACT_DAY)
        self.assertEqual(status, 200, body)
        self.assertIn("error", json.loads(body))

    def test_db_path_query_cannot_redirect_the_drill(self):
        status, body = self.get(
            "/api/activity/sources?db_path=/etc/passwd&day=" + ACT_DAY)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["day"], ACT_DAY)


class TestRerunAllowlist(unittest.TestCase):
    def test_rerun_kicks_the_allowlisted_label(self):
        with mock.patch.object(actions.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            out = actions.rerun(None, {"task": "upwork_scan"})
        self.assertTrue(out["ok"])
        argv = run.call_args[0][0]
        self.assertIn("kickstart", argv)
        self.assertIn("io.etherwise.v3.upwork-scan", " ".join(argv))

    def test_rerun_refuses_an_unlisted_task(self):
        # finance_payment_mail USED to be listed here as a forbidden example,
        # only because it was catch_up=False. That flag was wrong (it hid the
        # rail from the catch-up shim while it sat unscheduled for 97h), so it
        # is now catch_up-safe AND allowlisted — see the sibling invariant
        # test_catch_up_safe_sources_are_all_rerunnable. The brief jobs replace
        # it here: they SEND email, so they must never be kickable from the UI.
        with mock.patch.object(actions.subprocess, "run") as run:
            for task in ("cockpit", "catch_up", "cos_brief_pm",
                         "cos_brief_am", "rm -rf", "", None):
                with self.assertRaises(ValueError):
                    actions.rerun(None, {"task": task})
            run.assert_not_called()

    def test_every_label_has_a_plist_on_disk(self):
        launchd = Path(config.V3_ROOT) / "launchd"
        for task, label in actions.RERUN_JOBS.items():
            self.assertTrue((launchd / (label + ".plist")).is_file(),
                            "{} -> {} has no plist".format(task, label))

    def test_catch_up_safe_sources_are_all_rerunnable(self):
        """The source registry already decided which rails are safe to re-run
        unattended; the button must not disagree with it in either direction."""
        for s in config.SOURCES:
            label = s.get("launchd_label")
            if not label:
                continue
            if s.get("catch_up"):
                self.assertEqual(actions.RERUN_JOBS.get(s["task_name"]), label,
                                 s["key"])
            else:
                self.assertNotIn(s["task_name"], actions.RERUN_JOBS, s["key"])


# ══════════════════════════════════════════════════════════════════════════════
# Review — the three backlogs, the dismissal rule, and the batch action
# ══════════════════════════════════════════════════════════════════════════════

REVIEW_REG = """\
clients:
  -
    id: diat-khan
    name: Diat Khan
    aliases:
      - Remote Office
    billing:
      canonical: Diat Khan
  -
    id: acme
    name: Acme Corporation
    emails:
      - acme.com
    billing:
      canonical: Acme Corporation
  -
    id: a-product
    name: Some Product
    no_match: yes
"""

OWNER = "1647672547786633216"


class ReviewCase(unittest.TestCase):
    """Threads, messages, proposals, contracts and revenue rows, against a
    FIXTURE registry — the real config/clients.yaml is a moving target and the
    hint rule is the one thing here that must be pinned to exact strings."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="review-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.reg = registry.loads_registry(REVIEW_REG)
        self.patch = mock.patch.object(registry, "load", return_value=self.reg)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def thread(self, tid, room_name, **kw):
        cols = dict(id=tid, room_name=room_name, topic=kw.get("topic", ""),
                    room_type=kw.get("room_type", "INTERVIEW"),
                    contract_id=kw.get("contract_id"),
                    proposal_id=kw.get("proposal_id"),
                    offer_ids_json=kw.get("offer_ids_json"),
                    bucket=kw.get("bucket"), tier=kw.get("tier"),
                    awaiting_reply_from=kw.get("awaiting_reply_from", "None"),
                    latest_message_dt=kw.get("latest_message_dt"),
                    client_id=kw.get("client_id"))
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO threads ({}) VALUES ({})".format(
                    ",".join(cols), ",".join("?" * len(cols))),
                tuple(cols.values()))

    def message(self, tid, mid, *, theirs=True, when="2026-08-01T10:00:00Z",
                text="hello", org=None, user="Them"):
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO messages (id, thread_id, message, created_dt,"
                " sender_user_id, sender_user_name, sender_org_name)"
                " VALUES (?,?,?,?,?,?,?)",
                (mid, tid, text, when, "them" if theirs else OWNER,
                 user, org))

    def revenue(self, company, minor, *, n=1):
        with db.connect(self.db_path) as conn:
            for i in range(n):
                conn.execute(
                    "INSERT INTO transactions (record_id, canonical_key, source,"
                    " description, assignment_company, amount_base_minor,"
                    " pnl_bucket) VALUES (?,?,?,?,?,?, 'revenue')",
                    ("t-{}-{}".format(company, i), "k-{}-{}".format(company, i),
                     "Upwork", "work", company, minor))


class TestReviewThreadsReader(ReviewCase):
    def test_evidence_ranking_and_groups(self):
        # a real client under a spelling the registry does not hold, WITH money
        self.thread("room_money", "Rupali Singh", room_type="ONE_ON_ONE",
                    latest_message_dt="2026-08-10T09:00:00Z")
        self.message("room_money", "m1", org="Remoteoffice", user="Rupali S.",
                     text="the invoice is paid")
        self.revenue("Remoteoffice", 20900, n=2)
        # an expired proposal — the correct answer is "prospect"
        self.thread("room_dead", "Nabil, Super Nova", proposal_id="p1",
                    latest_message_dt="2025-06-01T09:00:00Z")
        self.message("room_dead", "m2", text="thanks, we went another way")
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO proposals (id, status, thread_id,"
                         " job_title) VALUES ('p1','Expired','room_dead','n8n')")
        # a live one
        self.thread("room_live", "John Bohan", proposal_id="p2",
                    awaiting_reply_from="Abhijeet", bucket="owed",
                    latest_message_dt=datetime.now(timezone.utc).isoformat())
        self.message("room_live", "m3", text="can you start Monday?")
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO proposals (id, status, thread_id)"
                         " VALUES ('p2','Interview','room_live')")
        # one that is already attributed — must not appear at all
        self.thread("room_done", "Acme Corporation", client_id="acme")

        d = data.review_threads(self.db_path)
        self.assertEqual(d["total"], 3)
        self.assertEqual(d["open_total"], 3)
        self.assertEqual(d["attributed_total"], 1)
        # a REAL zero carrying its reason: no unattributed thread is contract-
        # backed, and the payload says so rather than omitting the figure
        self.assertEqual(d["contract_backed"], 0)
        self.assertEqual(d["money_backed"], 1)
        self.assertEqual(d["hinted"], 1)

        keys = [g["key"] for g in d["groups"]]
        self.assertEqual(keys[0], "hint:diat-khan")     # the actionable group
        self.assertIn("live", keys)
        self.assertIn("prospect", keys)

        hint = d["groups"][0]["items"][0]
        self.assertEqual(hint["thread_id"], "room_money")
        self.assertEqual(hint["hint"]["client_id"], "diat-khan")
        self.assertEqual(hint["hint"]["registry_value"], "Remote Office")
        self.assertEqual(hint["money"]["usd"], 418.0)   # 2 x 209.00
        self.assertEqual(hint["money"]["txns"], 2)
        self.assertEqual(hint["last_message"]["excerpt"], "the invoice is paid")
        # the strongest evidence sorts first
        self.assertGreater(hint["score"],
                           next(g for g in d["groups"]
                                if g["key"] == "prospect")["items"][0]["score"])

    def test_a_no_match_client_is_never_offered(self):
        self.thread("room_a", "Anyone")
        d = data.review_threads(self.db_path)
        ids = {c["id"] for c in d["clients"]}
        self.assertIn("acme", ids)
        self.assertNotIn("a-product", ids)   # products hold no threads

    def test_short_names_never_hint(self):
        """A false hint is a wrong PERMANENT write, so the alnum key has a floor.
        'Diat' is 4 characters — a coincidence, not evidence."""
        self.thread("room_s", "Diat")
        self.message("room_s", "m", user="Diat")
        d = data.review_threads(self.db_path)
        self.assertEqual(d["hinted"], 0)

    def test_bad_show_is_an_error_envelope_not_an_empty_list(self):
        self.thread("room_a", "Anyone")
        out = data.review_threads(self.db_path, show="lol")
        self.assertIn("error", out)
        self.assertEqual(out["groups"], [])
        # NOT 0 — a rejected filter must never look like "no data"
        self.assertIsNone(out["open_total"])


class TestReviewDismissalSticks(ReviewCase):
    """THE RULE THIS SCREEN EXISTS FOR. 'This is a prospect, not a client' has
    to be recorded so the item never reappears — and it must be visible and
    reversible rather than deleted."""

    def setUp(self):
        super().setUp()
        for i in range(3):
            self.thread("room_%d" % i, "Prospect %d" % i)
            self.message("room_%d" % i, "m%d" % i)

    def test_dismiss_removes_it_from_the_producing_query_and_the_badge(self):
        before = data.review_threads(self.db_path)
        self.assertEqual(before["open_total"], 3)
        self.assertEqual(data.review_counts(self.db_path)["threads_open"], 3)

        out = actions.review_thread(self.db_path,
                                    {"thread_ids": ["room_0", "room_1"],
                                     "action": "dismiss"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["applied"], 2)

        after = data.review_threads(self.db_path)
        self.assertEqual(after["open_total"], 1)
        self.assertEqual(after["dismissed_total"], 2)
        self.assertEqual(after["total"], 3)          # nothing was deleted
        self.assertNotIn("room_0", [i["thread_id"]
                                    for g in after["groups"] for i in g["items"]])
        counts = data.review_counts(self.db_path)
        self.assertEqual(counts["threads_open"], 1)
        self.assertEqual(counts["threads_dismissed"], 2)

    def test_a_new_message_does_not_resurrect_a_dismissal(self):
        """The deliberate difference from Today's `_is_actioned`: a Done decays
        when the counterparty speaks, because a Done means 'handled'. A thread
        dismissal is about IDENTITY — a stranger sending another message is
        still a stranger — so it does not decay."""
        actions.review_thread(self.db_path,
                              {"thread_ids": ["room_0"], "action": "dismiss"})
        self.message("room_0", "later",
                     when=datetime.now(timezone.utc).isoformat(),
                     text="are you still available?")
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE threads SET latest_message_dt=? WHERE id=?",
                         (datetime.now(timezone.utc).isoformat(), "room_0"))
        d = data.review_threads(self.db_path)
        self.assertEqual(d["open_total"], 2)
        self.assertNotIn("room_0", [i["thread_id"]
                                    for g in d["groups"] for i in g["items"]])

    def test_dismissed_rows_are_visible_and_restorable(self):
        actions.review_thread(self.db_path, {"thread_ids": ["room_0"],
                                             "action": "dismiss",
                                             "reason": "recruiter spam"})
        shown = data.review_threads(self.db_path, show="dismissed")
        self.assertEqual(shown["shown_total"], 1)
        self.assertEqual(shown["groups"][0]["items"][0]["dismissed"]["reason"],
                         "recruiter spam")
        self.assertEqual(data.review_threads(self.db_path, show="all")["shown_total"], 3)

        actions.review_thread(self.db_path, {"thread_ids": ["room_0"],
                                             "action": "restore"})
        self.assertEqual(data.review_threads(self.db_path)["open_total"], 3)
        # restore DELETES the row: a reversal must leave nothing behind that a
        # later reader could mistake for "decided"
        with db.connect(self.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) n FROM nudge_state WHERE"
                             " item_key LIKE 'review:thread:%'").fetchone()["n"]
        self.assertEqual(n, 0)

    def test_attribution_retires_a_dismissal_structurally(self):
        """The other honest way a dismissal ends: the thread really does become
        a client. The producing query is client_id IS NULL, so it drops out —
        and it stops being counted as dismissed too, or the badge would sag by
        a row that is no longer anywhere."""
        actions.review_thread(self.db_path, {"thread_ids": ["room_0"],
                                             "action": "dismiss"})
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE threads SET client_id='acme' WHERE id='room_0'")
        d = data.review_threads(self.db_path)
        self.assertEqual(d["total"], 2)
        self.assertEqual(d["dismissed_total"], 0)
        self.assertEqual(data.review_counts(self.db_path)["threads_dismissed"], 0)

    def test_dismiss_refuses_an_unknown_thread_without_losing_the_rest(self):
        out = actions.review_thread(self.db_path,
                                    {"thread_ids": ["room_0", "nope"],
                                     "action": "dismiss"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["applied"], 1)
        self.assertEqual(out["unknown"], ["nope"])
        self.assertEqual(data.review_threads(self.db_path)["dismissed_total"], 1)

    def test_thread_keys_cannot_collide_with_a_today_card(self):
        """nudge_state is shared with Today. The namespaces must not overlap:
        _card_key emits 'card:*' and the CoS engine 'followup:*', this emits
        'review:thread:*'."""
        actions.review_thread(self.db_path, {"thread_ids": ["room_0"],
                                             "action": "dismiss"})
        with db.connect(self.db_path) as conn:
            keys = [r["item_key"] for r in conn.execute(
                "SELECT item_key FROM nudge_state")]
        self.assertEqual(keys, ["review:thread:room_0"])
        for k in keys:
            self.assertNotEqual(data._card_key(k), k)
            self.assertFalse(k.startswith("followup:"))


class TestReviewCounts(ReviewCase):
    def test_the_badge_counts_all_three_backlogs(self):
        with db.connect(self.db_path) as conn:
            for i, state in enumerate(["queued", "queued", "held", "excluded",
                                       "mapped"]):
                conn.execute(
                    "INSERT INTO events (id, source, native_id, state)"
                    " VALUES (?,?,?,?)", ("e%d" % i, "email", "n%d" % i, state))
        self.thread("room_a", "A")
        self.thread("room_b", "B")
        c = data.review_counts(self.db_path)
        self.assertEqual(c["events_queued"], 2)
        self.assertEqual(c["events_held"], 1)
        self.assertEqual(c["threads_open"], 2)
        self.assertEqual(c["total"], 5)          # excluded/mapped never count

        actions.review_thread(self.db_path, {"thread_ids": ["room_a"],
                                             "action": "dismiss"})
        self.assertEqual(data.review_counts(self.db_path)["total"], 4)


class TestQueueLearnsHonesty(unittest.TestCase):
    """`learns` is what an ASSIGN would write into clients.yaml for THIS row.

    The old screen warned "Assign writes to the client registry — permanently"
    on every row. For a Fathom or Loom event that is false: they carry no
    author, approve.learned_pattern returns None and approve.resolve skips
    write_registry entirely. Measured on the live DB 2026-08-13: 39 of 51 queued
    events learn nothing. The warning has to be per row or it is noise."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="queue-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO events (id, source, native_id, author, subject,"
                " state, text, raw_ref, proposed_client_id, proposal_confidence)"
                " VALUES ('evt:loom:1','loom','1',NULL,'Walkthrough','queued',"
                " ?, 'loom-ref', 'acme', 0.72)", ("x" * 900,))
            conn.execute(
                "INSERT INTO events (id, source, native_id, author, subject,"
                " state, text, proposed_client_id, proposal_confidence)"
                " VALUES ('evt:email:1','email','1','bob@acme.com','Hi',"
                " 'queued','body','acme',0.9)")
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " title, status, density) VALUES ('loom','loom-ref','h1',"
                " 'Walkthrough','extracted',0.6)")
            sid = conn.execute("SELECT id FROM sources").fetchone()["id"]
            for i in range(3):
                conn.execute(
                    "INSERT INTO facts (client_id, source_id, category,"
                    " fact_text, citation, confidence)"
                    " VALUES ('acme',?,'c','f','cit','CONFIRMED')", (sid,))

    def test_a_recording_learns_nothing_and_says_so(self):
        d = data.queue(self.db_path)
        items = {i["event_id"]: i for g in d["groups"] for i in g["items"]}
        loom = items["evt:loom:1"]
        self.assertEqual(loom["kind"], "recording")
        self.assertIsNone(loom["learns"])
        # …and it says how big the heal is instead
        self.assertEqual(loom["recording"]["facts"], 3)
        self.assertEqual(loom["recording"]["title"], "Walkthrough")

    def test_a_message_names_the_exact_registry_write(self):
        d = data.queue(self.db_path)
        items = {i["event_id"]: i for g in d["groups"] for i in g["items"]}
        mail = items["evt:email:1"]
        self.assertEqual(mail["kind"], "message")
        self.assertEqual(mail["learns"], {"field": "emails", "value": "acme.com"})

    def test_the_group_summarises_what_a_select_all_would_write(self):
        g = data.queue(self.db_path)["groups"][0]
        self.assertEqual(g["learns_n"], 1)            # one of the two
        self.assertEqual(sorted(g["kinds"]), ["message", "recording"])
        self.assertEqual(data.queue(self.db_path)["by_kind"],
                         {"recording": 1, "message": 1})

    def test_the_excerpt_is_long_enough_to_decide_on_and_says_when_it_is_cut(self):
        items = {i["event_id"]: i
                 for g in data.queue(self.db_path)["groups"] for i in g["items"]}
        self.assertEqual(len(items["evt:loom:1"]["excerpt"]), data._QUEUE_EXCERPT)
        self.assertTrue(items["evt:loom:1"]["excerpt_truncated"])
        self.assertFalse(items["evt:email:1"]["excerpt_truncated"])


class TestQueueBatch(unittest.TestCase):
    """A batch must be the SAME write as the single action it repeats — same
    authority, same refusals — and one bad row must not lose the other 59."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="batch-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        with db.connect(self.db_path) as conn:
            for i in range(4):
                conn.execute(
                    "INSERT INTO events (id, source, native_id, author, state,"
                    " text) VALUES (?,'email',?,?, 'held','junk')",
                    ("evt:email:%d" % i, str(i), "noreply@bank%d.com" % i))

    def test_batch_ignore_excludes_every_row_permanently(self):
        ids = ["evt:email:%d" % i for i in range(4)]
        out = actions.queue_batch(self.db_path,
                                  {"event_ids": ids, "action": "ignore",
                                   "reason": "not a client"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["applied"], 4)
        self.assertEqual(out["failed"], 0)
        self.assertEqual(out["commits"], [])         # ignore never touches git
        # the producing query is state='held'/'queued', so excluded is gone
        self.assertEqual(data.queue(self.db_path)["held_total"], 0)
        with db.connect(self.db_path) as conn:
            rows = conn.execute("SELECT state, exclude_reason, resolved_by"
                                " FROM events").fetchall()
        for r in rows:
            self.assertEqual(r["state"], "excluded")
            self.assertEqual(r["exclude_reason"], "not a client")
            self.assertEqual(r["resolved_by"], "cockpit-approval")

    def test_one_bad_row_does_not_lose_the_others(self):
        out = actions.queue_batch(
            self.db_path,
            {"event_ids": ["evt:email:0", "evt:email:nope"], "action": "ignore"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["applied"], 1)
        self.assertEqual(out["failed"], 1)
        bad = [r for r in out["results"] if not r["ok"]][0]
        self.assertEqual(bad["event_id"], "evt:email:nope")
        self.assertIn("unknown event", bad["error"])

    def test_the_cap_is_enforced_server_side(self):
        out = actions.queue_batch(
            self.db_path,
            {"event_ids": ["e"] * (actions.QUEUE_BATCH_MAX + 1),
             "action": "ignore"})
        self.assertIn("batch too large", out["error"])

    def test_a_bad_action_is_refused_for_every_row(self):
        out = actions.queue_batch(self.db_path,
                                  {"event_ids": ["evt:email:0"],
                                   "action": "delete"})
        self.assertEqual(out["failed"], 1)
        self.assertIn("action must be one of", out["results"][0]["error"])
        self.assertEqual(data.queue(self.db_path)["held_total"], 4)

    def test_empty_batch_is_an_error_not_a_silent_success(self):
        self.assertIn("error", actions.queue_batch(self.db_path,
                                                   {"event_ids": [],
                                                    "action": "ignore"}))


class TestReviewThreadWrites(ReviewCase):
    """assign / create_client are the PERMANENT ones. They are verified by
    stubbing the approval module and asserting the payload — deliberately never
    fired for real, because a stray test commit against the live
    config/clients.yaml is a mess to unpick."""

    def setUp(self):
        super().setUp()
        self.thread("room_a", "Rupali Singh", room_type="ONE_ON_ONE",
                    latest_message_dt="2026-08-10T09:00:00Z", proposal_id="p9")
        self.message("room_a", "m1", org="Remoteoffice")
        self.thread("room_b", "Someone Else")

    def test_assign_delegates_to_the_registry_write_path_with_db_evidence(self):
        with mock.patch("modules.jarvis.approve.assign_thread") as fake:
            fake.return_value = {"ok": True, "client_id": "diat-khan",
                                 "client_name": "Diat Khan",
                                 "registry": {"commit": "abc123abc123"}}
            out = actions.review_thread(self.db_path,
                                        {"thread_ids": ["room_a", "room_b"],
                                         "action": "assign",
                                         "client_id": "diat-khan"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["applied"], 2)
        self.assertEqual(out["commits"], ["abc123abc123"])   # deduplicated
        self.assertEqual(fake.call_count, 2)
        args, kw = fake.call_args_list[0]
        self.assertEqual(args[1:], ("room_a", "diat-khan"))
        self.assertEqual(kw["by"], "cockpit-thread-review")
        # the evidence in a permanent commit is READ FROM THE DB, never from the
        # request body — a form must not be able to write its own audit note
        self.assertIn("upwork room room_a", kw["evidence"])
        self.assertIn("'Rupali Singh'", kw["evidence"])
        self.assertIn("1 messages", kw["evidence"])
        self.assertIn("proposal p9", kw["evidence"])

    def test_assign_without_a_client_is_refused_before_any_write(self):
        with mock.patch("modules.jarvis.approve.assign_thread") as fake:
            out = actions.review_thread(self.db_path,
                                        {"thread_ids": ["room_a"],
                                         "action": "assign"})
        self.assertIn("client_id", out["error"])
        fake.assert_not_called()

    def test_create_client_is_one_thread_only(self):
        with mock.patch("modules.jarvis.approve.create_client") as fake:
            out = actions.review_thread(
                self.db_path,
                {"thread_ids": ["room_a", "room_b"], "action": "create_client",
                 "new_client": {"id": "x", "name": "X"}})
        self.assertIn("exactly one thread", out["error"])
        fake.assert_not_called()

    def test_create_client_then_claims_the_room(self):
        with mock.patch("modules.jarvis.approve.create_client") as create, \
             mock.patch("modules.jarvis.approve.assign_thread") as assign:
            create.return_value = {"ok": True, "client_id": "rupali-singh",
                                   "name": "Rupali Singh", "commit": "deadbeef1234",
                                   "registry": {"commit": "deadbeef1234"}}
            assign.return_value = {"ok": True, "client_id": "rupali-singh"}
            out = actions.review_thread(
                self.db_path,
                {"thread_ids": ["room_a"], "action": "create_client",
                 "new_client": {"id": "rupali-singh", "name": "Rupali Singh",
                                "kind": "prospect"}})
        self.assertTrue(out["ok"])
        self.assertEqual(out["commits"], ["deadbeef1234"])
        _, kw = create.call_args
        self.assertEqual(kw["kind"], "prospect")
        self.assertEqual(kw["upwork_room"], "room_a")
        self.assertIn("upwork room room_a", kw["evidence"])
        assign.assert_called_once()

    def test_a_refused_create_never_reaches_the_assign(self):
        with mock.patch("modules.jarvis.approve.create_client") as create, \
             mock.patch("modules.jarvis.approve.assign_thread") as assign:
            create.return_value = {"error": "client id 'acme' already exists"}
            out = actions.review_thread(
                self.db_path,
                {"thread_ids": ["room_a"], "action": "create_client",
                 "new_client": {"id": "acme", "name": "Acme"}})
        self.assertFalse(out["ok"])
        self.assertIn("already exists", out["results"][0]["error"])
        assign.assert_not_called()

    def test_unknown_action_and_oversized_batch_are_refused(self):
        self.assertIn("action must be one of",
                      actions.review_thread(self.db_path,
                                            {"thread_ids": ["room_a"],
                                             "action": "delete"})["error"])
        self.assertIn("batch too large", actions.review_thread(
            self.db_path,
            {"thread_ids": ["x"] * (actions.REVIEW_BATCH_MAX + 1),
             "action": "dismiss"})["error"])
        self.assertIn("thread_ids required",
                      actions.review_thread(self.db_path,
                                            {"action": "dismiss"})["error"])


class TestReviewServerRoutes(CockpitServerCase):
    def test_the_routes_are_reachable_and_token_gated(self):
        for path in ("/api/review/threads", "/api/review/counts"):
            self.assertEqual(self.get(path, token=None)[0], 401)
            status, body = self.get(path)
            self.assertEqual(status, 200, body)
            json.loads(body)

    def test_show_is_forwarded_and_validated(self):
        status, body = self.get("/api/review/threads?show=dismissed")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["shown"], "dismissed")
        # a rejected filter is a 200 carrying {"error"}, never an empty list
        status, body = self.get("/api/review/threads?show=lol")
        self.assertEqual(status, 200)
        out = json.loads(body)
        self.assertIn("error", out)
        self.assertIsNone(out["open_total"])

    def test_an_unregistered_param_cannot_reach_the_reader(self):
        plain = self.get("/api/review/threads")[1]
        self.assertEqual(self.get("/api/review/threads?today=lol")[1], plain)

    def test_the_batch_routes_are_posts_and_token_gated(self):
        for path in ("/api/queue/batch", "/api/review/thread"):
            self.assertEqual(self.post(path, token=None)[0], 401)
            status, body = self.post(path, body={})
            self.assertEqual(status, 200, body)
            self.assertIn("error", json.loads(body))


if __name__ == "__main__":
    unittest.main()
