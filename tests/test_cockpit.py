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
from datetime import datetime, timezone
from pathlib import Path

from core import config, db
from modules.cockpit import data, server

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

    def test_no_send_endpoint_anywhere(self):
        # drafts-only is sacred: no route may expose a send path
        for p in ("/api/send", "/api/message/send", "/api/upwork/send",
                  "/api/followup/send"):
            gs, _ = self.get(p)
            ps, _ = self.post(p)
            self.assertIn(gs, (401, 404), p)
            self.assertIn(ps, (401, 404), p)
        # and the source carries no send route literal
        src = (Path(server.__file__).read_text()
               + Path(data.__file__).read_text())
        self.assertNotIn("/send", src)


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


if __name__ == "__main__":
    unittest.main()
