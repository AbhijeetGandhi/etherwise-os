"""Unit tests for core/runner.py — real SQLite + real flock on temp dirs.

gateway.call is the mocked boundary for claude_call tests; everything else
(ledger, locks, retries, stagger, shadow helpers) runs real code.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from core import config, db
from core import claude_gateway as gw
from core import runner


class RunnerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="runner-test-"))
        self.db_path = self.tmp / "test.db"
        db.migrate(self.db_path)
        self.log_dir = self.tmp / "logs"
        self.lock_dir = self.tmp / "locks"
        self.log_dir.mkdir()
        self.lock_dir.mkdir()
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.log_dir),
            mock.patch.object(config, "LOCK_DIR", self.lock_dir),
        ]
        for p in self._patches:
            p.start()
        gw.reset_run()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_rows(self):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM runs ORDER BY id")]

    def anomaly_rows(self, kind=None):
        q, args = "SELECT * FROM anomalies", ()
        if kind:
            q, args = q + " WHERE kind = ?", (kind,)
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(q, args)]

    def run_one(self, fn, task="test_task", module="upwork", **kw):
        return runner.run_task(task, fn, module=module, db_path=self.db_path,
                               _sleep=lambda s: None, **kw)


# ── run ledger ────────────────────────────────────────────────────────────────
class TestLedger(RunnerTestCase):
    def test_success_writes_completed_row(self):
        result = self.run_one(lambda ctx: {"scanned": 5})
        rows = self.run_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["task_name"], "test_task")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(json.loads(row["metrics_json"]), {"scanned": 5})
        self.assertEqual(row["shadow"], 1)  # upwork is shadowed
        self.assertEqual(row["config_sha256"], config.config_sha256())
        self.assertIsNotNone(row["completed_at"])
        self.assertIsNotNone(row["duration_ms"])
        self.assertIsNone(row["error"])
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metrics, {"scanned": 5})

    def test_shadow_flag_follows_module_config(self):
        self.run_one(lambda ctx: {}, task="t_cockpit", module="cockpit")
        self.assertEqual(self.run_rows()[0]["shadow"], 0)  # cockpit not shadowed

    def test_unknown_module_defaults_shadow_on(self):
        self.run_one(lambda ctx: {}, task="t_new", module="not_a_module")
        self.assertEqual(self.run_rows()[0]["shadow"], 1)

    def test_failure_writes_failed_row_and_critical_anomaly(self):
        def boom(ctx):
            raise ValueError("kaput")
        with self.assertRaises(ValueError):
            self.run_one(boom)
        row = self.run_rows()[0]
        self.assertEqual(row["status"], "failed")
        self.assertIn("kaput", row["error"])
        anomalies = self.anomaly_rows("task_failure")
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["severity"], "critical")
        self.assertIn("kaput", anomalies[0]["detail_json"])

    def test_skip_empty(self):
        def nothing(ctx):
            raise runner.TaskSkip("no new items")
        result = self.run_one(nothing)
        row = self.run_rows()[0]
        self.assertEqual(row["status"], "skipped_empty")
        self.assertIsNone(row["error"])
        self.assertEqual(result.status, "skipped_empty")
        self.assertEqual(self.anomaly_rows(), [])  # skip is not a failure

    def test_last_success(self):
        self.assertIsNone(runner.last_success("test_task", db_path=self.db_path))
        self.run_one(lambda ctx: {})
        ts = runner.last_success("test_task", db_path=self.db_path)
        self.assertIsNotNone(ts)
        self.assertEqual(ts[:4], datetime.now(config.TZ).strftime("%Y"))

    def test_run_resets_gateway_accumulator(self):
        seen = {}
        gw._run_spent_usd = 99.0
        self.run_one(lambda ctx: seen.update(spent=gw._run_spent_usd) or {})
        self.assertEqual(seen["spent"], 0.0)


# ── retries ───────────────────────────────────────────────────────────────────
class TestRetries(RunnerTestCase):
    def test_transient_failure_retried_then_succeeds(self):
        calls = {"n": 0}
        sleeps = []

        def flaky(ctx):
            calls["n"] += 1
            if calls["n"] < 3:
                raise gw.TransientAPIError("529")
            return {"ok": True}

        result = runner.run_task("flaky", flaky, module="upwork",
                                 db_path=self.db_path, _sleep=sleeps.append)
        self.assertEqual(calls["n"], 3)
        self.assertEqual(result.status, "completed")
        self.assertEqual(sleeps, [config.RUNNER_BACKOFF_BASE_SECONDS,
                                  config.RUNNER_BACKOFF_BASE_SECONDS * 2])
        self.assertEqual(len(self.run_rows()), 1)  # one ledger row per run

    def test_gives_up_after_max_attempts(self):
        calls = {"n": 0}

        def always_down(ctx):
            calls["n"] += 1
            raise gw.TransientAPIError("529 forever")

        with self.assertRaises(gw.TransientAPIError):
            self.run_one(always_down)
        self.assertEqual(calls["n"], config.RUNNER_MAX_ATTEMPTS)
        self.assertEqual(self.run_rows()[0]["status"], "failed")
        self.assertEqual(len(self.anomaly_rows("task_failure")), 1)

    def test_non_retryable_fails_immediately(self):
        calls = {"n": 0}

        def hard_fail(ctx):
            calls["n"] += 1
            raise KeyError("logic bug")

        with self.assertRaises(KeyError):
            self.run_one(hard_fail)
        self.assertEqual(calls["n"], 1)


# ── single-instance locks ─────────────────────────────────────────────────────
class TestLocks(RunnerTestCase):
    def test_second_instance_skips_while_locked(self):
        held = runner._acquire_lock("locked_task")
        self.assertIsNotNone(held)
        try:
            calls = {"n": 0}
            result = self.run_one(lambda ctx: calls.update(n=1) or {},
                              task="locked_task")
            self.assertEqual(result.status, "skipped_locked")
            self.assertEqual(calls["n"], 0)  # fn never ran
            self.assertEqual(self.run_rows()[0]["status"], "skipped_locked")
        finally:
            runner._release_lock(held)

    def test_lock_released_after_run(self):
        self.run_one(lambda ctx: {}, task="serial_task")
        result = self.run_one(lambda ctx: {}, task="serial_task")
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(self.run_rows()), 2)


# ── stagger ───────────────────────────────────────────────────────────────────
class TestStagger(RunnerTestCase):
    def test_deterministic_and_capped(self):
        a = runner._stagger_seconds("upwork_sync")
        b = runner._stagger_seconds("upwork_sync")
        self.assertEqual(a, b)
        self.assertGreaterEqual(a, 0)
        self.assertLess(a, config.RUNNER_STAGGER_MAX_SECONDS)

    def test_stagger_sleeps_when_enabled(self):
        sleeps = []
        runner.run_task("stag_task", lambda ctx: {}, module="upwork",
                        db_path=self.db_path, stagger=True,
                        _sleep=sleeps.append)
        self.assertEqual(sleeps, [runner._stagger_seconds("stag_task")])


# ── shadow helpers ────────────────────────────────────────────────────────────
class TestShadow(RunnerTestCase):
    def test_record_shadow_write(self):
        def fn(ctx):
            ctx.record_shadow_write(target="airtable", operation="create",
                                    entity="proposals", entity_key="job-42",
                                    payload={"Title": "x"})
            return {}
        self.run_one(fn)
        with db.connect(self.db_path) as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM shadow_ledger")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target"], "airtable")
        self.assertEqual(rows[0]["entity_key"], "job-42")
        self.assertEqual(rows[0]["diff_status"], "pending")
        self.assertEqual(rows[0]["run_id"], self.run_rows()[0]["id"])

    def test_require_live_raises_in_shadow(self):
        def fn(ctx):
            ctx.require_live("airtable push")
            return {}
        with self.assertRaises(runner.ShadowViolation):
            self.run_one(fn, module="upwork")  # shadowed

    def test_require_live_passes_when_module_live(self):
        def fn(ctx):
            ctx.require_live("local render")
            return {"ok": 1}
        result = self.run_one(fn, module="cockpit")  # not shadowed
        self.assertEqual(result.status, "completed")


# ── claude_call: retry + failover chain over the gateway ─────────────────────
class TestClaudeCall(RunnerTestCase):
    def _result(self, model="claude-sonnet-4-6"):
        return gw.GatewayResult(text="ok", parsed=None, model=model,
                                route="payg", usage={}, cost_usd=0.001,
                                stop_reason="end_turn", duration_ms=5)

    def test_failover_to_fallback_model_after_exhausting_primary(self):
        attempts = []

        def fake_call(**kw):
            attempts.append(kw.get("model_override"))
            if len(attempts) <= config.RUNNER_MAX_ATTEMPTS:
                raise gw.TransientAPIError("overloaded")
            return self._result(model="claude-haiku-4-5-20251001")

        sleeps = []
        with mock.patch.object(gw, "call", side_effect=fake_call):
            result = runner.claude_call(
                task_name="t", model_key="scoring", user_content="x",
                db_path=self.db_path, _sleep=sleeps.append)
        # primary (override None) tried MAX times, then first fallback
        self.assertEqual(attempts[:config.RUNNER_MAX_ATTEMPTS],
                         [None] * config.RUNNER_MAX_ATTEMPTS)
        self.assertEqual(attempts[config.RUNNER_MAX_ATTEMPTS],
                         "claude-haiku-4-5-20251001")
        self.assertEqual(result.model, "claude-haiku-4-5-20251001")

    def test_chain_exhausted_raises_last_transient(self):
        primary = config.MODELS["scoring"]
        chain_len = 1 + len(config.MODEL_FALLBACKS.get(primary, []))
        with mock.patch.object(
                gw, "call",
                side_effect=gw.TransientAPIError("down")) as c:
            with self.assertRaises(gw.TransientAPIError):
                runner.claude_call(task_name="t", model_key="scoring",
                                   user_content="x", db_path=self.db_path,
                                   _sleep=lambda s: None)
        self.assertEqual(c.call_count,
                         chain_len * config.RUNNER_MAX_ATTEMPTS)

    def test_non_retryable_gateway_error_raises_immediately(self):
        with mock.patch.object(gw, "call",
                               side_effect=gw.GatewayError("bad")) as c:
            with self.assertRaises(gw.GatewayError):
                runner.claude_call(task_name="t", model_key="scoring",
                                   user_content="x", db_path=self.db_path,
                                   _sleep=lambda s: None)
        self.assertEqual(c.call_count, 1)

    def test_budget_exceeded_never_retried(self):
        with mock.patch.object(
                gw, "call",
                side_effect=gw.ClaudeBudgetExceeded("cap", scope="per_run")) as c:
            with self.assertRaises(gw.ClaudeBudgetExceeded):
                runner.claude_call(task_name="t", model_key="scoring",
                                   user_content="x", db_path=self.db_path,
                                   _sleep=lambda s: None)
        self.assertEqual(c.call_count, 1)

    def test_ctx_claude_binds_task_and_db(self):
        captured = {}

        def fake_call(**kw):
            captured.update(kw)
            return self._result()

        def fn(ctx):
            ctx.claude(model_key="scoring", user_content="hi")
            return {}

        with mock.patch.object(gw, "call", side_effect=fake_call):
            self.run_one(fn, task="bound_task")
        self.assertEqual(captured["task_name"], "bound_task")
        self.assertEqual(captured["db_path"], self.db_path)


if __name__ == "__main__":
    unittest.main()
