"""Hybrid sweep (every 2h, 12 PM-4 AM). Deterministic rules surface candidate
nudges; the sweep writes them, clears conditions that vanished, then decides
whether anything MATERIALLY changed. EMPTY-SKIP is mandatory: if the candidate
set is identical to last sweep AND no nudge crossed a due/overdue boundary, it
TaskSkips with ZERO Opus spend. Only on real change does Opus do what rules
can't — cross-signal correlation (new nudges), prioritization, brief narrative.

Opus is the system's main ongoing cost; the empty-skip + the $5 daily cap are
the guards. Shadow-safe: Opus is a reasoning call (no external write); all
nudges stay local.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from core import config, db, runner
from modules.chief_of_staff import (rules, nudges, commitments, capture,
                                    interrupts)

TASK_NAME = "cos_sweep"

# kinds owned by deterministic rules — reconciled each sweep (vanished -> decay).
# Opus correlation nudges (escalate_client, source='sweep') are NOT reconciled
# here; they decay on their own TTL.
RULE_KINDS = ("owed_reply", "hotlead_no_loom", "won_no_outcome", "lost_pattern",
              "stale_dossier", "commitment_due", "system")

OPUS_SCHEMA = {
    "type": "object",
    "properties": {
        "correlations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "dept": {"type": "string", "enum": ["sales", "knowledge", "system"]},
                    "title": {"type": "string"},
                    "action": {"type": "string"},
                    "dedup_key": {"type": "string"},
                    "due": {"type": ["string", "null"]},
                    "rationale": {"type": "string"},
                },
                "required": ["kind", "dept", "title", "action", "dedup_key", "rationale"],
                "additionalProperties": False,
            },
        },
        "priorities": {"type": "array", "items": {"type": "string"}},
        "narrative": {"type": "string"},
    },
    "required": ["correlations", "priorities", "narrative"],
    "additionalProperties": False,
}

OPUS_SYSTEM = (
    "You are the Etherwise Chief of Staff running a sweep. You are given the "
    "current nudge queue (deterministic rules already produced these) plus fresh "
    "context. Do ONLY what the rules cannot:\n"
    "1. correlations: genuine CROSS-SIGNAL patterns that span multiple nudges/"
    "facts -> a NEW nudge (e.g. a client gone quiet + an overdue commitment -> "
    "'escalate_client'). Emit at most 3, and ONLY when the multi-signal pattern "
    "is real; [] is the right answer when nothing correlates. Use a stable "
    "dedup_key like 'escalate_client:<client_id>'.\n"
    "2. priorities: order the dedup_keys you would put in front of Abhijeet "
    "first (most consequential first).\n"
    "3. narrative: 2-4 sentences, direct, no fluff — the situational picture for "
    "the brief.\n"
    "Never invent facts or numbers; reason only over what is given. You SURFACE; "
    "the human decides. Never propose sending a client message."
)


# ── empty-skip machinery ──────────────────────────────────────────────────────

def candidate_hash(candidates: List[dict]) -> str:
    """Stable hash of the actionable picture: (kind, dedup_key, due) sorted."""
    sig = sorted(f"{c['kind']}|{c['dedup_key']}|{c.get('due') or ''}"
                 for c in candidates)
    return hashlib.sha256("\n".join(sig).encode()).hexdigest()


def _parse(ts):
    return nudges._parse(ts)


def _due_trip(active: List[dict], last_swept_at: Optional[str],
              now: datetime) -> bool:
    """True if any active nudge crossed a due-soon (due - N days) or overdue
    (due) boundary since the last sweep — a time-based change rules can't see in
    the candidate set. First sweep (no last_swept_at) is handled by the caller."""
    last = _parse(last_swept_at)
    if not last:
        return True
    win = timedelta(days=config.COS_COMMITMENT_DUE_DAYS)
    for n in active:
        due = _parse(n.get("due"))
        if not due:
            continue
        for boundary in (due - win, due):
            if last < boundary <= now:
                return True
    return False


def _load_state(conn) -> dict:
    row = conn.execute("SELECT * FROM sweep_state WHERE id=1").fetchone()
    return dict(row) if row else {}


def _save_state(db_path, *, candidate_hash: str, last_swept_at: str,
                last_opus_at: Optional[str], metrics: Optional[dict]) -> None:
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sweep_state (id, candidate_hash, last_swept_at,"
            " last_opus_at, last_metrics_json) VALUES (1,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET candidate_hash=excluded.candidate_hash,"
            " last_swept_at=excluded.last_swept_at,"
            " last_opus_at=COALESCE(excluded.last_opus_at, sweep_state.last_opus_at),"
            " last_metrics_json=excluded.last_metrics_json",
            (candidate_hash, last_swept_at, last_opus_at,
             json.dumps(metrics) if metrics is not None else None))


# ── fresh context for Opus ────────────────────────────────────────────────────

def _context(db_path, now: datetime) -> dict:
    with db.connect(db_path) as conn:
        wins = conn.execute("SELECT COUNT(*) c FROM proposals WHERE status='Won'"
                            ).fetchone()["c"]
        open_comm = conn.execute("SELECT COUNT(*) c FROM commitments WHERE"
                                 " state='open' AND who='us'").fetchone()["c"]
        spent = conn.execute(
            "SELECT COALESCE(SUM(total_cost_usd),0) s FROM claude_usage WHERE"
            " ist_date=?", (now.strftime("%Y-%m-%d"),)).fetchone()["s"]
    return {"won_total": wins, "open_commitments": open_comm,
            "spend_today_usd": round(spent, 2),
            "daily_cap_usd": config.DAILY_HARD_LIMIT_USD}


# ── Opus correlation (injectable for tests) ──────────────────────────────────

def opus_correlate(db_path, active: List[dict], context: dict) -> dict:
    # cap the queue sent to Opus (it arrives ranked) + summarize the tail by
    # kind, so a heavy day of leads can't bloat the prompt or the cost.
    cap = config.COS_SWEEP_OPUS_MAXITEMS
    head, tail = active[:cap], active[cap:]
    queue = [{"kind": n["kind"], "dept": n["dept"], "title": n["title"],
              "dedup_key": n["dedup_key"], "due": n.get("due"),
              "urgency": round(n.get("urgency_live", n.get("urgency") or 0), 1)}
             for n in head]
    tail_summary: dict = {}
    for n in tail:
        tail_summary[n["kind"]] = tail_summary.get(n["kind"], 0) + 1
    payload = {"nudge_queue": queue, "context": context}
    if tail_summary:
        payload["queue_tail_by_kind"] = tail_summary   # not individually listed
    user = json.dumps(payload, indent=1)
    result = runner.claude_call(
        task_name=TASK_NAME, model_key="reasoning", system=OPUS_SYSTEM,
        schema=OPUS_SCHEMA, user_content=user,
        purpose="sweep correlate+prioritize", max_output_tokens=1500,
        db_path=db_path)
    return result.parsed or {"correlations": [], "priorities": [], "narrative": ""}


# ── the sweep ─────────────────────────────────────────────────────────────────

def run_sweep(ctx, _opus: Callable = opus_correlate,
              _commitments: Optional[Callable] = commitments.extract_new,
              _capture: Optional[Callable] = capture.classify_new,
              _interrupts: Optional[Callable] = interrupts.check_hot_leads,
              now: Optional[datetime] = None) -> dict:
    db_path = ctx.db_path or config.DB_PATH
    now = now or datetime.now(config.TZ)
    nowiso = now.strftime("%Y-%m-%dT%H:%M:%S")

    # classify captured notes + extract commitments from NEW sources first, so a
    # just-captured to-do or a freshly-promised due-dated item can surface this
    # same sweep. A Sonnet hiccup in either must not break the sweep.
    for fn in (_capture, _commitments):
        if fn:
            try:
                fn(db_path)
            except Exception:
                pass

    # hot-lead interrupts fire independently of the empty-skip (a new hot lead
    # is interrupt-worthy even if the nudge picture is otherwise unchanged).
    # SHADOW-GATED inside check_hot_leads: records intent, does not email.
    if _interrupts:
        try:
            _interrupts(ctx, now)
        except Exception:
            pass

    candidates = rules.gather(db_path, now)
    # 1. write/refresh candidates
    for c in candidates:
        nudges.write_nudge(db_path, now=now, **c)
    # 2. reconcile rule-owned kinds: clear active nudges whose condition vanished
    live_keys = {c["dedup_key"] for c in candidates}
    qmarks = ",".join("?" * len(RULE_KINDS))
    with db.connect(db_path) as conn:
        stale = [r["dedup_key"] for r in conn.execute(
            f"SELECT dedup_key FROM nudges WHERE state IN ('open','snoozed')"
            f" AND kind IN ({qmarks})", RULE_KINDS)
            if r["dedup_key"] not in live_keys]
    for k in stale:
        nudges.clear(db_path, k, now=now)
    # 3. TTL decay sweep
    nudges.decay_expired(db_path, now=now)

    # 4. empty-skip decision
    new_hash = candidate_hash(candidates)
    with db.connect(db_path) as conn:
        st = _load_state(conn)
    active = nudges.active_nudges(db_path, now=now)
    unchanged = st.get("candidate_hash") == new_hash
    trip = _due_trip(active, st.get("last_swept_at"), now)

    # empty-skip: nothing to reason about, OR the picture is unchanged and no
    # due/overdue boundary was crossed. Either way -> zero Opus spend.
    if not active or (unchanged and not trip):
        _save_state(db_path, candidate_hash=new_hash, last_swept_at=nowiso,
                    last_opus_at=st.get("last_opus_at"),
                    metrics=json.loads(st["last_metrics_json"])
                    if st.get("last_metrics_json") else None)
        raise runner.TaskSkip(f"no change ({len(active)} active nudges)")

    # 5. material change -> Opus correlate + prioritize + narrate
    opus = _opus(db_path, active, _context(db_path, now))
    new_corr = 0
    for c in (opus.get("correlations") or [])[:3]:
        try:
            nudges.write_nudge(
                db_path, now=now, source="sweep", dept=c.get("dept", "sales"),
                kind=c.get("kind", "escalate_client"), title=c["title"],
                action=c.get("action"), due=c.get("due"),
                dedup_key=c["dedup_key"],
                payload={"rationale": c.get("rationale")})
            new_corr += 1
        except Exception:
            continue
    metrics = {"candidates": len(candidates), "active": len(active),
               "cleared": len(stale), "correlations": new_corr,
               "priorities": opus.get("priorities", []),
               "narrative": opus.get("narrative", "")}
    _save_state(db_path, candidate_hash=new_hash, last_swept_at=nowiso,
                last_opus_at=nowiso, metrics=metrics)
    return metrics


def main() -> int:
    res = runner.run_task(TASK_NAME, run_sweep, module="chief_of_staff")
    print(json.dumps({"run_id": res.run_id, "status": res.status,
                      "metrics": res.metrics}))
    return 0 if res.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
