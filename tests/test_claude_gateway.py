"""Unit tests for core/claude_gateway.py — no network, real SQLite on a temp dir.

Transports (_payg_transport / _credit_transport) are the only mocked boundary;
caps, logging, request building, and cost math run real code.
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

TODAY_IST = datetime.now(config.TZ).strftime("%Y-%m-%d")

SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "integer"}},
    "required": ["score"],
    "additionalProperties": False,
}


def fake_response(text='{"score": 17}', in_tok=1000, out_tok=200, cache_w=0,
                  cache_r=0, cost=None, stop="end_turn"):
    return {
        "text": text,
        "usage": {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cache_creation_input_tokens": cache_w,
            "cache_read_input_tokens": cache_r,
        },
        "stop_reason": stop,
        "reported_cost_usd": cost,
        "request_id": "req_test",
    }


class GatewayTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gw-test-"))
        self.db_path = self.tmp / "test.db"
        db.migrate(self.db_path)
        gw.reset_run()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── helpers ──────────────────────────────────────────────────────────────
    def seed_spend(self, cost_usd, ist_date=TODAY_IST):
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO claude_usage (called_at, ist_date, task_name, model,"
                " billing_route, total_cost_usd) VALUES (?,?,?,?,?,?)",
                (datetime.now(config.TZ).isoformat(), ist_date, "seed",
                 "claude-sonnet-4-6", "payg", cost_usd),
            )

    def usage_rows(self):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM claude_usage WHERE task_name != 'seed' ORDER BY id")]

    def anomaly_rows(self, kind=None):
        q = "SELECT * FROM anomalies"
        args = ()
        if kind:
            q += " WHERE kind = ?"
            args = (kind,)
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(q, args)]

    def call(self, **kw):
        defaults = dict(task_name="test_task", model_key="scoring",
                        user_content="score this", db_path=self.db_path)
        defaults.update(kw)
        return gw.call(**defaults)


# ── request building (pure functions, no mocks) ──────────────────────────────
class TestBuildPaygRequest(GatewayTestCase):
    def test_applies_1h_cache_to_system_block(self):
        req = gw.build_payg_request(
            model="claude-sonnet-4-6", system="You are a scorer.",
            user_content="job post", max_tokens=1000)
        self.assertEqual(req["system"], [{
            "type": "text", "text": "You are a scorer.",
            "cache_control": {"type": "ephemeral", "ttl": config.CACHE_TTL},
        }])
        self.assertEqual(req["messages"], [{"role": "user", "content": "job post"}])
        self.assertEqual(req["model"], "claude-sonnet-4-6")
        self.assertEqual(req["max_tokens"], 1000)

    def test_no_system_means_no_system_key(self):
        req = gw.build_payg_request(model="claude-sonnet-4-6", system=None,
                                    user_content="x", max_tokens=100)
        self.assertNotIn("system", req)

    def test_schema_becomes_output_config(self):
        req = gw.build_payg_request(model="claude-sonnet-4-6", system=None,
                                    user_content="x", max_tokens=100, schema=SCHEMA)
        self.assertEqual(req["output_config"],
                         {"format": {"type": "json_schema", "schema": SCHEMA}})

    def test_sampling_params_stripped_from_extra(self):
        req = gw.build_payg_request(
            model="claude-sonnet-4-6", system=None, user_content="x",
            max_tokens=100,
            extra={"temperature": 0.7, "top_p": 0.9, "top_k": 5, "stop_sequences": ["END"]})
        for banned in config.FORBIDDEN_SAMPLING_PARAMS:
            self.assertNotIn(banned, req)
        self.assertEqual(req["stop_sequences"], ["END"])  # non-banned extras pass


class TestBuildCreditCmd(GatewayTestCase):
    def test_flags_and_env_scrub(self):
        argv, env, cwd = gw.build_credit_cmd(
            model="claude-sonnet-4-6", prompt="score this",
            system="You are a scorer.", schema=SCHEMA, max_budget_usd=0.50,
            base_env={"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-leak"})
        self.assertNotIn("ANTHROPIC_API_KEY", env)  # would override OAuth
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertIn("-p", argv)
        self.assertIn("--output-format", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertIn("--no-session-persistence", argv)
        self.assertIn("--tools", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--model") + 1], "claude-sonnet-4-6")
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "0.5")
        self.assertEqual(argv[argv.index("--system-prompt") + 1], "You are a scorer.")
        self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1]), SCHEMA)
        self.assertNotIn("--bare", argv)  # --bare kills OAuth — never on credit route

    def test_schema_and_system_omitted_when_absent(self):
        argv, _, _ = gw.build_credit_cmd(
            model="claude-sonnet-4-6", prompt="x", system=None, schema=None,
            max_budget_usd=0.50, base_env={})
        self.assertNotIn("--json-schema", argv)
        self.assertNotIn("--system-prompt", argv)


# ── model + schema gating ─────────────────────────────────────────────────────
class TestGating(GatewayTestCase):
    def test_unknown_model_key_raises(self):
        with self.assertRaises(gw.GatewayError):
            self.call(model_key="nonexistent")

    def test_schema_rejected_on_fable(self):
        with self.assertRaises(gw.StructuredOutputUnsupported):
            self.call(model_key="architect", schema=SCHEMA)

    def test_model_without_pricing_raises(self):
        with mock.patch.dict(config.MODELS, {"scoring": "claude-mystery-9"}):
            with self.assertRaises(gw.GatewayError):
                self.call(model_key="scoring")


# ── cost math ────────────────────────────────────────────────────────────────
class TestCost(GatewayTestCase):
    def test_cost_computed_from_pricing(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 100_000,
                 "cache_creation_input_tokens": 500_000,
                 "cache_read_input_tokens": 2_000_000}
        p_in, p_out, p_cr, p_cw = config.PRICING["claude-sonnet-4-6"]
        expected = (1_000_000 * p_in + 100_000 * p_out
                    + 2_000_000 * p_cr + 500_000 * p_cw) / 1e6
        self.assertAlmostEqual(
            gw.compute_cost("claude-sonnet-4-6", usage), expected, places=6)


# ── call(): logging, ceilings, routing ───────────────────────────────────────
class TestCallPayg(GatewayTestCase):
    def test_success_logs_usage_row(self):
        with mock.patch.object(gw, "_payg_transport",
                               return_value=fake_response()) as t:
            result = self.call(purpose="score job 42", schema=SCHEMA)
        rows = self.usage_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["task_name"], "test_task")
        self.assertEqual(row["purpose"], "score job 42")
        self.assertEqual(row["model"], config.MODELS["scoring"])
        self.assertEqual(row["billing_route"], "payg")
        self.assertEqual(row["ist_date"], TODAY_IST)
        self.assertEqual(row["input_tokens"], 1000)
        self.assertEqual(row["output_tokens"], 200)
        self.assertGreater(row["total_cost_usd"], 0)
        self.assertEqual(row["stop_reason"], "end_turn")
        self.assertIsNone(row["error"])
        self.assertEqual(result.parsed, {"score": 17})
        self.assertEqual(result.route, "payg")
        self.assertAlmostEqual(result.cost_usd, row["total_cost_usd"])

    def test_failure_logged_and_reraised(self):
        with mock.patch.object(gw, "_payg_transport",
                               side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.call()
        rows = self.usage_rows()
        self.assertEqual(len(rows), 1)
        self.assertIn("boom", rows[0]["error"])
        self.assertEqual(rows[0]["total_cost_usd"], 0)

    def test_max_output_tokens_clamped_to_ceiling(self):
        with mock.patch.object(gw, "_payg_transport",
                               return_value=fake_response()) as t:
            self.call(max_output_tokens=999_999)
        req = t.call_args[0][0]
        self.assertEqual(req["max_tokens"], config.PER_RUN_MAX_OUTPUT_TOKENS)

    def test_parsed_is_none_without_schema(self):
        with mock.patch.object(gw, "_payg_transport",
                               return_value=fake_response(text="plain prose")):
            result = self.call()
        self.assertIsNone(result.parsed)
        self.assertEqual(result.text, "plain prose")


class TestCeilings(GatewayTestCase):
    def test_daily_hard_cap_blocks_before_transport(self):
        self.seed_spend(config.DAILY_HARD_LIMIT_USD + 0.10)
        with mock.patch.object(gw, "_payg_transport") as t:
            with self.assertRaises(gw.ClaudeBudgetExceeded):
                self.call()
            t.assert_not_called()
        self.assertEqual(len(self.anomaly_rows("budget_hard_cap")), 1)
        self.assertEqual(self.anomaly_rows("budget_hard_cap")[0]["severity"],
                         "critical")

    def test_daily_soft_cap_warns_once_and_proceeds(self):
        self.seed_spend(config.DAILY_SOFT_LIMIT_USD + 0.10)
        with mock.patch.object(gw, "_payg_transport",
                               return_value=fake_response()):
            self.call()
            self.call()
        self.assertEqual(len(self.usage_rows()), 2)  # calls went through
        self.assertEqual(len(self.anomaly_rows("budget_soft_cap")), 1)
        self.assertEqual(self.anomaly_rows("budget_soft_cap")[0]["severity"],
                         "warn")

    def test_yesterday_spend_does_not_count(self):
        self.seed_spend(config.DAILY_HARD_LIMIT_USD * 2, ist_date="2026-06-09")
        with mock.patch.object(gw, "_payg_transport",
                               return_value=fake_response()):
            self.call()  # should not raise
        self.assertEqual(len(self.usage_rows()), 1)

    def test_per_run_ceiling_blocks(self):
        # 200K input tok on sonnet = $0.60: over per-run $0.50, under daily caps
        big = fake_response(in_tok=200_000, out_tok=0)
        with mock.patch.object(gw, "_payg_transport", return_value=big):
            self.call()
            with self.assertRaises(gw.ClaudeBudgetExceeded) as ctx:
                self.call()
        self.assertEqual(ctx.exception.scope, "per_run")
        self.assertEqual(len(self.usage_rows()), 1)  # second call never executed

    def test_daily_hard_cap_scope(self):
        self.seed_spend(config.DAILY_HARD_LIMIT_USD + 0.10)
        with self.assertRaises(gw.ClaudeBudgetExceeded) as ctx:
            self.call()
        self.assertEqual(ctx.exception.scope, "daily_hard")

    def test_reset_run_clears_per_run_spend(self):
        big = fake_response(in_tok=200_000, out_tok=0)  # $0.60 per call
        with mock.patch.object(gw, "_payg_transport", return_value=big):
            self.call()
            gw.reset_run()
            self.call()  # should not raise after reset ($1.20 day < caps)
        self.assertEqual(len(self.usage_rows()), 2)


class TestCreditRoute(GatewayTestCase):
    def test_credit_route_used_and_logged(self):
        with mock.patch.object(gw, "_credit_transport",
                               return_value=fake_response(cost=0.0123)) as t:
            result = self.call(route="credit")
        t.assert_called_once()
        self.assertEqual(result.route, "credit")
        row = self.usage_rows()[0]
        self.assertEqual(row["billing_route"], "credit")
        self.assertAlmostEqual(row["total_cost_usd"], 0.0123)

    def test_credit_failure_falls_back_to_payg(self):
        with mock.patch.object(gw, "_credit_transport",
                               side_effect=gw.CreditRouteError("exhausted")), \
             mock.patch.object(gw, "_payg_transport",
                               return_value=fake_response()):
            result = self.call(route="credit")
        self.assertEqual(result.route, "payg")
        self.assertEqual(self.usage_rows()[0]["billing_route"], "payg")

    def test_credit_failure_raises_when_fallback_disabled(self):
        with mock.patch.object(config, "BILLING_FALLBACK_TO_PAYG", False), \
             mock.patch.object(gw, "_credit_transport",
                               side_effect=gw.CreditRouteError("exhausted")), \
             mock.patch.object(gw, "_payg_transport") as payg:
            with self.assertRaises(gw.CreditRouteError):
                self.call(route="credit")
            payg.assert_not_called()


class TestModelOverride(GatewayTestCase):
    """model_override is the runner's failover lever — values come from
    config.MODEL_FALLBACKS, so model strings still live only in config."""

    def test_override_replaces_resolved_model(self):
        with mock.patch.object(gw, "_payg_transport",
                               return_value=fake_response()) as t:
            result = self.call(model_override="claude-haiku-4-5-20251001")
        self.assertEqual(t.call_args[0][0]["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(result.model, "claude-haiku-4-5-20251001")
        self.assertEqual(self.usage_rows()[0]["model"],
                         "claude-haiku-4-5-20251001")

    def test_override_must_have_pricing(self):
        with self.assertRaises(gw.GatewayError):
            self.call(model_override="claude-mystery-9")

    def test_schema_gating_applies_to_override(self):
        with self.assertRaises(gw.StructuredOutputUnsupported):
            self.call(model_override="claude-fable-5", schema=SCHEMA)


class TestSdkErrorMapping(GatewayTestCase):
    """SDK exceptions are translated at the gateway boundary so the runner
    never imports anthropic. Mapping is by class name (stable across SDK
    versions, constructible in tests without httpx plumbing)."""

    def test_transient_sdk_errors_become_transient(self):
        for name in ("RateLimitError", "OverloadedError", "InternalServerError",
                     "APIConnectionError", "APITimeoutError"):
            exc = type(name, (Exception,), {})("upstream sad")
            mapped = gw._map_sdk_error(exc)
            self.assertIsInstance(mapped, gw.TransientAPIError, name)
            self.assertIn("upstream sad", str(mapped))

    def test_permanent_errors_pass_through_unchanged(self):
        exc = type("BadRequestError", (Exception,), {})("bad schema")
        self.assertIs(gw._map_sdk_error(exc), exc)

    def test_transient_is_a_gateway_error(self):
        self.assertTrue(issubclass(gw.TransientAPIError, gw.GatewayError))


if __name__ == "__main__":
    unittest.main()
