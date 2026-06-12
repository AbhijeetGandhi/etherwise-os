"""Lead scoring for M1a — v2.3 matrix + Category Priority Gate, anchored.

Hard rules run in CODE before any model call (claw lesson #5: guardrails that
live only in prompts get compacted away). The judgment call goes through
runner.claude_call (retry + failover) with structured outputs and ANCHORED
EXEMPLARS per dimension (the eval showed ±2-3 softness on unanchored
dimensions — fixtures drawn from real graded history pin the scale).

The gate cap is enforced in code AFTER the model verdict: a gated verdict
stores effective score <= 15 no matter what the model returned.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from core import config, runner

EXEMPLARS_PATH = Path(__file__).resolve().parent / "fixtures/exemplars.json"

_N8N_RE = re.compile(r"\bn8n\b", re.I)
_NO_ASIA_RE = re.compile(r"no\s+asia", re.I)
_ONSITE_RE = re.compile(r"\bon[- ]?site\b", re.I)
_GATE_CAP = 15

SCHEMA = {
    "type": "object",
    "properties": {
        "skill_fit": {"type": "integer"},
        "budget": {"type": "integer"},
        "client_quality": {"type": "integer"},
        "competition": {"type": "integer"},
        "description_quality": {"type": "integer"},
        "base_total": {"type": "integer"},
        "bonuses": {
            "type": "object",
            "properties": {"core_stack": {"type": "integer"},
                           "long_term": {"type": "integer"},
                           "invite": {"type": "integer"},
                           "claude": {"type": "integer"},
                           "whale_client": {"type": "integer"},
                           "fresh": {"type": "integer"}},
            "required": ["core_stack", "long_term", "invite", "claude",
                         "whale_client", "fresh"],
            "additionalProperties": False,
        },
        "raw_total": {"type": "integer"},
        "gated": {"type": "boolean"},
        "effective_total": {"type": "integer"},
        "recommendation": {"type": "string"},
    },
    "required": ["skill_fit", "budget", "client_quality", "competition",
                 "description_quality", "base_total", "bonuses", "raw_total",
                 "gated", "effective_total", "recommendation"],
    "additionalProperties": False,
}


# ── hard rules (code, pre-model, v4.9 no exceptions) ─────────────────────────

def hard_rule_skip(job: dict) -> Optional[str]:
    """Skip reason or None. Title/description only for n8n — tags do NOT
    trigger (2026-06-10 decision). No placeholder escape (v4.9)."""
    title = job.get("title") or ""
    desc = job.get("description") or ""
    text = f"{title}\n{desc}"
    if _N8N_RE.search(text):
        return "n8n_exclusion"
    if job.get("contract_type") == "Hourly":
        if (job.get("hourly_max") or 0) < config.HARD_FLOOR_HOURLY:
            return "hourly_below_30"
    elif job.get("contract_type") == "Fixed":
        if (job.get("fixed_budget") or 0) < config.HARD_FLOOR_FIXED:
            return "fixed_below_500"
    if _NO_ASIA_RE.search(text):
        return "no_asia_timezone"
    if _ONSITE_RE.search(text):
        return "onsite_required"
    return None


# ── prompt assembly ───────────────────────────────────────────────────────────

_MATRIX = """\
You score Upwork jobs for Etherwise (Abhijeet Gandhi — Top Rated Plus, $100K+
earned, $32.50/hr; core stack Make.com, Airtable, Vapi, GoHighLevel, Python,
AI/LLM). v2.3 scoring matrix — score each dimension, then bonuses, then the
gate:

Dimensions (base, max 28):
- Skill Fit 1-5, WEIGHTED x2 (strongest win predictor)
- Budget 1-5
- Client Quality 1-3 (capped; NEVER penalize a new client — $0 spent with no
  red flags = 2)
- Competition 1-5 (20-50 proposals is normal; only 150+ saturation without a
  whale client is disqualifying)
- Description Quality 1-5

Bonuses: core stack +3 · long-term/ongoing (20+ hr/wk or multi-month) +3 ·
direct invite +4 · mentions Claude/Claude Code +2 · client spent $100K+ +2 ·
posted <1h ago +1.

Category Priority Gate (v2.3): if the job CENTERS on GoHighLevel/GHL, Vapi,
or Retell, it is gated UNLESS any of: client total spent >= $50,000, hourly
top >= $45/hr, fixed budget >= $3,000. A gated job that fails the whale gate
is capped at effective 15 (Standard — never Hot, never Loom). Make.com /
Airtable / n8n / Python / generic-AI jobs are NEVER gated; direct invites
exempt; GHL/Vapi as one endpoint in a Make/Airtable-primary build is NOT
gated. Set gated=true and cap effective_total accordingly; record raw_total
uncapped.

Return effective_total = min(raw_total, 15) when gated else raw_total.
"""


def _exemplar_block() -> str:
    ex = json.loads(EXEMPLARS_PATH.read_text())
    lines = ["ANCHORED EXEMPLARS — real graded history; match this scale:"]
    for dim, items in ex.items():
        lines.append(f"\n{dim}:")
        for e in items:
            extras = []
            if e.get("budget"):
                extras.append(f"budget ${e['budget']:g}")
            if e.get("client_spent") is not None:
                extras.append(f"client ${e['client_spent']:g} spent")
            why = f" — {e['why']}" if e.get("why") else ""
            lines.append(f"  grade {e['grade']}: \"{e['title']}\""
                         f" ({'; '.join(extras)}){why}")
    return "\n".join(lines)


def system_prompt() -> str:
    return _MATRIX + "\n" + _exemplar_block()


def job_input(job: dict) -> str:
    return json.dumps({k: job.get(k) for k in (
        "title", "description", "contract_type", "hourly_min", "hourly_max",
        "fixed_budget", "weekly_budget", "total_applicants",
        "experience_level", "engagement", "engagement_duration",
        "skills_json", "client_country", "client_total_spent",
        "client_hires", "client_rating", "client_payment_verified",
        "created_dt", "feed_source")}, default=str)


# ── the judgment call ─────────────────────────────────────────────────────────

def score_job(job: dict, task_name: str, db_path=None) -> dict:
    """Returns {score, gated, loom_flag, breakdown} — effective score, gate
    cap re-enforced in code."""
    result = runner.claude_call(
        task_name=task_name, model_key="scoring",
        system=system_prompt(), user_content=job_input(job),
        schema=SCHEMA, purpose=f"score {job.get('id')}",
        max_output_tokens=700, db_path=db_path)
    v = result.parsed
    effective = v["effective_total"]
    gate_note = None
    if v.get("gated"):
        if effective > _GATE_CAP:
            effective = _GATE_CAP          # model failed the cap — code wins
        gate_note = (f"gated: capped at {_GATE_CAP}"
                     f" (raw {v.get('raw_total')})")
    breakdown = {k: v.get(k) for k in (
        "skill_fit", "budget", "client_quality", "competition",
        "description_quality", "base_total", "bonuses", "raw_total",
        "recommendation")}
    breakdown["gate"] = gate_note
    breakdown["total"] = effective
    return {"score": effective, "gated": bool(v.get("gated")),
            "loom_flag": 1 if effective >= config.LOOM_FLAG_THRESHOLD else 0,
            "breakdown": breakdown}
