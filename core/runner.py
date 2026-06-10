"""Job wrapper for every scheduled task (architecture §4).

Provides: run ledger row per invocation, IST daily-file logging, retry with
backoff on transient errors, model failover chains over the gateway, top-of-
hour stagger, flock single-instance locks, shadow-mode helpers, and failure
routing (critical anomaly row — the interrupt queue notify.py will drain).

Usage:
    from core import runner

    def my_task(ctx):
        ctx.log("starting")
        result = ctx.claude(model_key="scoring", user_content=...)
        if nothing_to_do:
            raise runner.TaskSkip("no new items")
        if not ctx.shadow:
            push_to_airtable(...)        # live path
        else:
            ctx.record_shadow_write(...)  # shadow path
        return {"processed": n}           # -> runs.metrics_json

    runner.run_task("upwork_sync", my_task, module="upwork")

Locks use flock, which the kernel releases automatically if the process dies —
no stale-lock cleanup needed. Stagger is a deterministic hash so a task always
gets the same offset (launchd entrypoints opt in with stagger=True).

Retry layering: run_task retries the whole (idempotent) task on transient
errors, and claude_call retries per model before failing over. Worst-case call
amplification is bounded by the gateway's per-run USD ceiling, which is NOT
reset between attempts of the same run.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from core import config, db
from core import claude_gateway as gw


class TaskSkip(Exception):
    """Raised by a task to record a clean empty-skip (status skipped_empty)."""


class ShadowViolation(Exception):
    """An external write was attempted while the module is in shadow mode."""


# Transient = worth retrying the whole idempotent task. Budget exceptions are
# GatewayError but never transient; CreditRouteError already fell back in-gateway.
_RETRYABLE = (gw.TransientAPIError, ConnectionError, TimeoutError)


@dataclass
class RunResult:
    run_id: Optional[int]
    status: str                      # completed|failed|skipped_empty|skipped_locked
    metrics: Optional[dict] = None
    error: Optional[str] = None


@dataclass
class RunContext:
    task_name: str
    run_id: int
    module: str
    shadow: bool
    db_path: Optional[Path] = None

    def log(self, msg: str) -> None:
        _log(self.task_name, msg)

    def claude(self, **kwargs) -> gw.GatewayResult:
        kwargs.setdefault("task_name", self.task_name)
        kwargs.setdefault("db_path", self.db_path)
        return claude_call(**kwargs)

    def record_shadow_write(self, target: str, operation: str, entity: str,
                            entity_key: str, payload: Any) -> None:
        """Record an intended external write instead of executing it (§9)."""
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO shadow_ledger (run_id, task_name, target,"
                " operation, entity, entity_key, payload_json, diff_status)"
                " VALUES (?,?,?,?,?,?,?, 'pending')",
                (self.run_id, self.task_name, target, operation, entity,
                 entity_key, json.dumps(payload)))

    def require_live(self, action: str) -> None:
        if self.shadow:
            raise ShadowViolation(
                f"{action} blocked: module {self.module!r} is in shadow mode"
                " (record_shadow_write instead)")


# ── logging (IST, daily files) ────────────────────────────────────────────────

def _log(task_name: str, msg: str) -> None:
    now = datetime.now(config.TZ)
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = config.LOG_DIR / f"{now.strftime('%Y-%m-%d')}.log"
    with open(path, "a") as f:
        f.write(f"[{now.strftime('%H:%M:%S')} IST] {task_name} | {msg}\n")


# ── locks + stagger ───────────────────────────────────────────────────────────

def _acquire_lock(task_name: str) -> Optional[int]:
    """flock-based single-instance lock. Returns fd to hold, None if held
    elsewhere. The kernel releases flock on process death — no stale locks."""
    config.LOCK_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(config.LOCK_DIR / f"{task_name}.lock",
                 os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()} {datetime.now(config.TZ).isoformat()}\n"
             .encode())
    return fd


def _release_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _stagger_seconds(task_name: str) -> int:
    """Deterministic per-task offset (sha256, not hash() — that's seeded
    per-process) so launchd jobs spread out instead of bursting at :00."""
    digest = hashlib.sha256(task_name.encode()).hexdigest()
    return int(digest, 16) % config.RUNNER_STAGGER_MAX_SECONDS


# ── ledger ────────────────────────────────────────────────────────────────────

def _insert_run(db_path: Optional[Path], task_name: str, started_at: str,
                shadow: bool, status: str = "running") -> int:
    with db.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO runs (task_name, started_at, status, shadow,"
            " config_sha256) VALUES (?,?,?,?,?)",
            (task_name, started_at, status, int(shadow),
             config.config_sha256()))
        return cur.lastrowid


def _finish_run(db_path: Optional[Path], run_id: int, status: str,
                duration_ms: int, metrics: Optional[dict] = None,
                error: Optional[str] = None) -> None:
    with db.connect(db_path) as conn:
        conn.execute(
            "UPDATE runs SET status=?, completed_at=?, duration_ms=?,"
            " metrics_json=?, error=? WHERE id=?",
            (status, datetime.now(config.TZ).isoformat(timespec="seconds"),
             duration_ms,
             json.dumps(metrics) if metrics is not None else None,
             error, run_id))


def last_success(task_name: str, db_path: Optional[Path] = None) -> Optional[str]:
    """completed_at of the task's most recent completed run (catch-up helper)."""
    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT completed_at FROM runs WHERE task_name=?"
            " AND status='completed' ORDER BY id DESC LIMIT 1",
            (task_name,)).fetchone()
        return row["completed_at"] if row else None


def _route_failure(db_path: Optional[Path], task_name: str, run_id: int,
                   error: str) -> None:
    """Decision #9 routing: critical anomaly row = the interrupt queue.
    notify.py (when built) drains critical anomalies into interrupt emails."""
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO anomalies (task_name, kind, detail_json, severity)"
            " VALUES (?, 'task_failure', ?, 'critical')",
            (task_name, json.dumps({
                "run_id": run_id, "error": error[:1000],
                "ist_date": datetime.now(config.TZ).strftime("%Y-%m-%d")})))


# ── the wrapper ───────────────────────────────────────────────────────────────

def run_task(task_name: str, fn: Callable[[RunContext], Optional[dict]],
             module: str, db_path: Optional[Path] = None,
             stagger: bool = False,
             _sleep: Callable[[float], None] = time.sleep) -> RunResult:
    """Run one task under the full harness. fn(ctx) returns a metrics dict
    (-> runs.metrics_json), raises TaskSkip for a clean empty-skip, or raises
    to fail the run. Transient errors retry with backoff; the final failure
    re-raises after ledger + anomaly routing."""
    if stagger:
        _sleep(_stagger_seconds(task_name))

    shadow = config.SHADOW_MODE.get(module, True)  # unknown module: shadow ON
    started_at = datetime.now(config.TZ).isoformat(timespec="seconds")
    started = time.monotonic()

    lock_fd = _acquire_lock(task_name)
    if lock_fd is None:
        _log(task_name, "skipped: another instance holds the lock")
        run_id = _insert_run(db_path, task_name, started_at, shadow,
                             status="skipped_locked")
        _finish_run(db_path, run_id, "skipped_locked", 0)
        return RunResult(run_id, "skipped_locked")

    try:
        run_id = _insert_run(db_path, task_name, started_at, shadow)
        gw.reset_run()
        ctx = RunContext(task_name=task_name, run_id=run_id, module=module,
                         shadow=shadow, db_path=db_path)
        _log(task_name, f"run {run_id} started"
             f" ({'shadow' if shadow else 'LIVE'})")

        attempt = 0
        while True:
            attempt += 1
            try:
                metrics = fn(ctx)
                duration_ms = int((time.monotonic() - started) * 1000)
                _finish_run(db_path, run_id, "completed", duration_ms,
                            metrics=metrics or {})
                _log(task_name, f"run {run_id} completed in {duration_ms}ms"
                     f" metrics={json.dumps(metrics or {})}")
                return RunResult(run_id, "completed", metrics=metrics or {})
            except TaskSkip as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                _finish_run(db_path, run_id, "skipped_empty", duration_ms,
                            metrics={"skip_reason": str(exc)})
                _log(task_name, f"run {run_id} skipped_empty: {exc}")
                return RunResult(run_id, "skipped_empty")
            except _RETRYABLE as exc:
                if attempt >= config.RUNNER_MAX_ATTEMPTS:
                    _fail(db_path, task_name, run_id, started, exc)
                    raise
                backoff = config.RUNNER_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                _log(task_name, f"attempt {attempt} transient failure: {exc!r}"
                     f" — retrying in {backoff}s")
                _sleep(backoff)
            except Exception as exc:
                _fail(db_path, task_name, run_id, started, exc)
                raise
    finally:
        _release_lock(lock_fd)


def _fail(db_path: Optional[Path], task_name: str, run_id: int,
          started: float, exc: BaseException) -> None:
    duration_ms = int((time.monotonic() - started) * 1000)
    _finish_run(db_path, run_id, "failed", duration_ms, error=repr(exc))
    _route_failure(db_path, task_name, run_id, repr(exc))
    _log(task_name, f"run {run_id} FAILED after {duration_ms}ms: {exc!r}")


# ── reliable Claude calls (retry + failover over the gateway) ────────────────

def claude_call(task_name: str, model_key: str,
                db_path: Optional[Path] = None,
                _sleep: Callable[[float], None] = time.sleep,
                **gw_kwargs) -> gw.GatewayResult:
    """gateway.call with retries per model and failover down
    config.MODEL_FALLBACKS (OpenClaw pattern). Budget exceptions never retry."""
    primary = config.MODELS.get(model_key)
    if not primary:
        raise gw.GatewayError(f"unknown model key: {model_key!r}")
    chain: list = [None] + list(config.MODEL_FALLBACKS.get(primary, []))

    last_exc: Optional[BaseException] = None
    for override in chain:
        model_label = override or primary
        for attempt in range(1, config.RUNNER_MAX_ATTEMPTS + 1):
            try:
                return gw.call(task_name=task_name, model_key=model_key,
                               model_override=override, db_path=db_path,
                               **gw_kwargs)
            except gw.ClaudeBudgetExceeded:
                raise
            except gw.TransientAPIError as exc:
                last_exc = exc
                _log(task_name,
                     f"{model_label} attempt {attempt} transient: {exc}")
                if attempt < config.RUNNER_MAX_ATTEMPTS:
                    _sleep(config.RUNNER_BACKOFF_BASE_SECONDS
                           * (2 ** (attempt - 1)))
        _log(task_name, f"failover: {model_label} exhausted")
    assert last_exc is not None
    raise last_exc
