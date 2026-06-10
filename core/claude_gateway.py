"""The only door to Claude (architecture §4).

Routes billing (credit = subscription `claude -p` | payg = Messages API via the
anthropic SDK — the one sanctioned external dependency), applies 1h prompt
caching, enforces per-run and daily ceilings, logs every call to claude_usage,
supports structured outputs (never on Fable), and strips sampling params.

Scope (kernel): single-shot judgment calls — system + user content, optional
JSON schema, NO tools. Tool-enabled headless runs (M4 CoS sweep) get designed
at their module session and added here behind the same ceilings.

Division of labour: this module is single-attempt. Retries, backoff, and model
failover chains belong to core/runner.py — the SDK's auto-retry is disabled so
there is exactly one retry authority.

Per-run accounting: module-level accumulator, reset by runner at run start via
reset_run(). Correct because every scheduled job is a short-lived process
(launchd spawn), never a long-running daemon (claw anti-pattern #2).

Credit-route notes:
- ANTHROPIC_API_KEY is scrubbed from the subprocess env — a set key silently
  overrides subscription OAuth and would bill payg.
- --bare is never used: it disables OAuth/keychain auth entirely.
- The CLI may auto-load CLAUDE.md context from parent dirs; calls run with
  cwd=var/ and tools disabled. Context-shape tuning happens when the route
  flips on (2026-06-15) — open item, tracked in BUILD_BRIEF.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from core import config, db

CLAUDE_BIN = os.environ.get("ETHERWISE_CLAUDE_BIN", "claude")
CREDENTIALS_ENV_FILE = "etherwise-os.env"   # shared with v2 until PL-10


class GatewayError(Exception):
    """Base class for gateway failures."""


class ClaudeBudgetExceeded(GatewayError):
    """A spend ceiling was hit. scope: 'daily_hard' | 'per_run'."""

    def __init__(self, message: str, scope: str):
        super().__init__(message)
        self.scope = scope


class StructuredOutputUnsupported(GatewayError):
    """Schema-constrained call attempted on a model without structured outputs."""


class TransientAPIError(GatewayError):
    """Upstream hiccup (rate limit / overload / 5xx / network). The runner may
    retry these; everything else is permanent for this attempt."""


class CreditRouteError(GatewayError):
    """The subscription/credit path failed (auth, exhaustion, CLI error)."""


@dataclass
class GatewayResult:
    text: str
    parsed: Optional[Any]
    model: str
    route: str
    usage: dict
    cost_usd: float
    stop_reason: Optional[str]
    duration_ms: int
    request_id: Optional[str] = None


_run_spent_usd = 0.0


def reset_run() -> None:
    """Zero the per-run spend accumulator. Runner calls this at run start."""
    global _run_spent_usd
    _run_spent_usd = 0.0


# ── request building (pure) ───────────────────────────────────────────────────

def build_payg_request(model: str, system: Optional[str], user_content: Any,
                       max_tokens: int, schema: Optional[dict] = None,
                       extra: Optional[dict] = None) -> dict:
    """Messages-API kwargs: 1h-cached system block, output_config for schemas,
    forbidden sampling params stripped."""
    request: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_content}],
    }
    if system:
        request["system"] = [{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral", "ttl": config.CACHE_TTL},
        }]
    if schema:
        request["output_config"] = {"format": {"type": "json_schema",
                                               "schema": schema}}
    if extra:
        request.update({k: v for k, v in extra.items()
                        if k not in config.FORBIDDEN_SAMPLING_PARAMS})
    return request


def build_credit_cmd(model: str, prompt: str, system: Optional[str],
                     schema: Optional[dict], max_budget_usd: float,
                     base_env: Optional[dict] = None):
    """argv + env + cwd for a one-shot `claude -p` call on subscription credit."""
    env = {k: v for k, v in (base_env if base_env is not None
                             else os.environ).items()
           if k != "ANTHROPIC_API_KEY"}
    argv = [
        CLAUDE_BIN, "-p", prompt,
        "--model", model,
        "--output-format", "json",
        "--no-session-persistence",
        "--tools", "",
        "--max-budget-usd", str(max_budget_usd),
    ]
    if system:
        argv += ["--system-prompt", system]
    if schema:
        argv += ["--json-schema", json.dumps(schema)]
    return argv, env, str(config.VAR_DIR)


def compute_cost(model: str, usage: dict) -> float:
    p_in, p_out, p_cache_read, p_cache_write = config.PRICING[model]
    return (usage.get("input_tokens", 0) * p_in
            + usage.get("output_tokens", 0) * p_out
            + usage.get("cache_read_input_tokens", 0) * p_cache_read
            + usage.get("cache_creation_input_tokens", 0) * p_cache_write) / 1e6


# ── transports (the only network boundary; mocked in tests) ──────────────────

def _load_api_key() -> str:
    env_path = config.CREDENTIALS_DIR / CREDENTIALS_ENV_FILE
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip()
                if key:
                    return key
    except OSError as exc:
        raise GatewayError(f"cannot read credentials file: {env_path}") from exc
    raise GatewayError(f"ANTHROPIC_API_KEY not found in {env_path}")


def _normalize_usage(usage: Any) -> dict:
    get = (usage.get if isinstance(usage, dict)
           else lambda k, d=0: getattr(usage, k, d) or 0)
    return {k: int(get(k, 0) or 0) for k in (
        "input_tokens", "output_tokens",
        "cache_creation_input_tokens", "cache_read_input_tokens")}


# Matched by class NAME so the runner never imports anthropic and tests don't
# need httpx plumbing to construct SDK exception instances.
_TRANSIENT_SDK_ERROR_NAMES = frozenset({
    "RateLimitError", "OverloadedError", "InternalServerError",
    "APIConnectionError", "APITimeoutError",
})


def _map_sdk_error(exc: BaseException) -> BaseException:
    """Translate SDK exceptions into the gateway taxonomy at the boundary."""
    if type(exc).__name__ in _TRANSIENT_SDK_ERROR_NAMES:
        return TransientAPIError(f"{type(exc).__name__}: {exc}")
    return exc


def _payg_transport(request: dict) -> dict:
    """Single Messages API call via the anthropic SDK. No retries (runner owns)."""
    import anthropic  # the one external dependency; only the gateway imports it

    client = anthropic.Anthropic(api_key=_load_api_key(),
                                 timeout=config.CLAUDE_TIMEOUT_SECONDS,
                                 max_retries=0)
    try:
        resp = client.messages.create(**request)
    except Exception as exc:
        raise _map_sdk_error(exc) from exc
    text = "".join(b.text for b in resp.content if b.type == "text")
    return {
        "text": text,
        "usage": _normalize_usage(resp.usage),
        "stop_reason": resp.stop_reason,
        "reported_cost_usd": None,
        "request_id": getattr(resp, "_request_id", None),
    }


def _credit_transport(model: str, prompt: str, system: Optional[str],
                      schema: Optional[dict], max_budget_usd: float) -> dict:
    """One-shot `claude -p` on subscription auth. Raises CreditRouteError on
    any CLI failure so call() can fall back to payg."""
    argv, env, cwd = build_credit_cmd(model, prompt, system, schema,
                                      max_budget_usd)
    try:
        proc = subprocess.run(argv, env=env, cwd=cwd, capture_output=True,
                              text=True,
                              timeout=config.CLAUDE_TIMEOUT_SECONDS * 2)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CreditRouteError(f"claude CLI unavailable: {exc}") from exc
    if proc.returncode != 0:
        raise CreditRouteError(
            f"claude -p exit {proc.returncode}: {proc.stderr.strip()[-400:]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CreditRouteError(
            f"claude -p returned non-JSON: {proc.stdout[:200]!r}") from exc
    if data.get("is_error"):
        raise CreditRouteError(f"claude -p error result: {data.get('result')}")
    return {
        "text": data.get("result") or "",
        "usage": _normalize_usage(data.get("usage") or {}),
        "stop_reason": data.get("stop_reason") or data.get("subtype"),
        "reported_cost_usd": data.get("total_cost_usd"),
        "request_id": data.get("session_id"),
    }


# ── ledger + ceilings ─────────────────────────────────────────────────────────

def _ist_now() -> datetime:
    return datetime.now(config.TZ)


def _daily_spend(db_path: Optional[Path], ist_date: str) -> float:
    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(total_cost_usd), 0) AS s FROM claude_usage"
            " WHERE ist_date = ?", (ist_date,)).fetchone()
        return float(row["s"])


def _record_anomaly_once(db_path: Optional[Path], task_name: str, kind: str,
                         severity: str, detail: dict) -> None:
    with db.connect(db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM anomalies WHERE kind = ?"
            " AND json_extract(detail_json, '$.ist_date') = ?",
            (kind, detail["ist_date"])).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO anomalies (task_name, kind, detail_json, severity)"
                " VALUES (?, ?, ?, ?)",
                (task_name, kind, json.dumps(detail), severity))


def _log_usage(db_path: Optional[Path], called_at: str, ist_date: str,
               task_name: str, purpose: Optional[str], model: str, route: str,
               usage: dict, cost_usd: float, duration_ms: int,
               stop_reason: Optional[str], error: Optional[str]) -> None:
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO claude_usage (called_at, ist_date, task_name, purpose,"
            " model, billing_route, input_tokens, output_tokens,"
            " cache_creation_tokens, cache_read_tokens, total_cost_usd,"
            " duration_ms, tool_calls, stop_reason, error)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (called_at, ist_date, task_name, purpose, model, route,
             usage.get("input_tokens", 0), usage.get("output_tokens", 0),
             usage.get("cache_creation_input_tokens", 0),
             usage.get("cache_read_input_tokens", 0),
             cost_usd, duration_ms, 0, stop_reason, error))


def _check_ceilings(db_path: Optional[Path], task_name: str,
                    ist_date: str) -> None:
    spent_today = _daily_spend(db_path, ist_date)
    if spent_today >= config.DAILY_HARD_LIMIT_USD:
        _record_anomaly_once(
            db_path, task_name, "budget_hard_cap", "critical",
            {"ist_date": ist_date, "spent_usd": round(spent_today, 4),
             "limit_usd": config.DAILY_HARD_LIMIT_USD})
        raise ClaudeBudgetExceeded(
            f"daily hard cap: ${spent_today:.2f} >= "
            f"${config.DAILY_HARD_LIMIT_USD:.2f}", scope="daily_hard")
    if spent_today >= config.DAILY_SOFT_LIMIT_USD:
        _record_anomaly_once(
            db_path, task_name, "budget_soft_cap", "warn",
            {"ist_date": ist_date, "spent_usd": round(spent_today, 4),
             "limit_usd": config.DAILY_SOFT_LIMIT_USD})
    if _run_spent_usd >= config.PER_RUN_MAX_USD:
        raise ClaudeBudgetExceeded(
            f"per-run cap: ${_run_spent_usd:.2f} >= "
            f"${config.PER_RUN_MAX_USD:.2f}", scope="per_run")


# ── the door ──────────────────────────────────────────────────────────────────

def call(task_name: str, model_key: str, user_content: Any,
         system: Optional[str] = None, schema: Optional[dict] = None,
         purpose: Optional[str] = None, max_output_tokens: Optional[int] = None,
         route: Optional[str] = None, db_path: Optional[Path] = None,
         extra: Optional[dict] = None,
         model_override: Optional[str] = None) -> GatewayResult:
    """Single judgment call. Checks ceilings, routes billing, logs the call.

    model_key is a key into config.MODELS (scoring/classify/drafting/...) —
    never a raw model id, so model strings live in exactly one place.
    model_override is reserved for the runner's failover chains; its values
    come from config.MODEL_FALLBACKS, so the config-only rule still holds.
    """
    global _run_spent_usd

    model = model_override or config.MODELS.get(model_key)
    if not model:
        raise GatewayError(f"unknown model key: {model_key!r}"
                           f" (must be one of {sorted(config.MODELS)})")
    if model not in config.PRICING:
        raise GatewayError(f"no pricing for model {model!r} —"
                           " add it to config.PRICING")
    if schema and model in config.STRUCTURED_OUTPUT_UNSUPPORTED:
        raise StructuredOutputUnsupported(
            f"{model} does not support structured outputs")

    now = _ist_now()
    ist_date = now.strftime("%Y-%m-%d")
    _check_ceilings(db_path, task_name, ist_date)

    max_tokens = min(max_output_tokens or config.PER_RUN_MAX_OUTPUT_TOKENS,
                     config.PER_RUN_MAX_OUTPUT_TOKENS)
    route_used = route or config.BILLING_DEFAULT_ROUTE
    called_at = now.isoformat(timespec="seconds")
    started = time.monotonic()

    try:
        if route_used == "credit":
            if not isinstance(user_content, str):
                raise GatewayError("credit route requires str user_content")
            remaining = round(
                max(0.01, config.PER_RUN_MAX_USD - _run_spent_usd), 4)
            try:
                resp = _credit_transport(model, user_content, system, schema,
                                         remaining)
            except CreditRouteError:
                if not config.BILLING_FALLBACK_TO_PAYG:
                    raise
                route_used = "payg"
                resp = _payg_transport(build_payg_request(
                    model, system, user_content, max_tokens, schema, extra))
        else:
            route_used = "payg"
            resp = _payg_transport(build_payg_request(
                model, system, user_content, max_tokens, schema, extra))
    except Exception as exc:
        _log_usage(db_path, called_at, ist_date, task_name, purpose, model,
                   route_used, {}, 0.0,
                   int((time.monotonic() - started) * 1000), None, repr(exc))
        raise

    duration_ms = int((time.monotonic() - started) * 1000)
    usage = resp["usage"]
    reported = resp.get("reported_cost_usd")
    cost_usd = float(reported) if reported is not None \
        else compute_cost(model, usage)

    _log_usage(db_path, called_at, ist_date, task_name, purpose, model,
               route_used, usage, cost_usd, duration_ms,
               resp.get("stop_reason"), None)
    _run_spent_usd += cost_usd

    parsed = None
    if schema:
        try:
            parsed = json.loads(resp["text"])
        except json.JSONDecodeError as exc:
            raise GatewayError(
                f"schema call returned invalid JSON: {resp['text'][:200]!r}"
            ) from exc

    return GatewayResult(text=resp["text"], parsed=parsed, model=model,
                         route=route_used, usage=usage, cost_usd=cost_usd,
                         stop_reason=resp.get("stop_reason"),
                         duration_ms=duration_ms,
                         request_id=resp.get("request_id"))
