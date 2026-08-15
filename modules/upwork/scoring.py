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
_N8N_TITLE_START_RE = re.compile(r"^\W*n8n\b", re.I)
# Refined rule (Abhijeet, 2026-06-12): skip only when n8n is THE subject —
# title starts with it, OR it's the only stack tool named. Multi-tool
# pipe-lists score normally; tags never trigger. The "centers on n8n"
# judgment stays a scoring-stage note, not a hard rule.
# bare 'make' is the English verb — only the canonical tool name counts
_OTHER_TOOLS_RE = re.compile(
    r"\b(make\.com|airtable|claude|zapier|vapi|retell|gohighlevel|ghl|"
    r"python|openai|gpt|hubspot|salesforce|notion|monday\.com)\b", re.I)
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

def _rate_unstated(job: dict) -> bool:
    """True when the client stated NO hourly rate. Upwork ships a blank rate as
    {type:'NotProvided', min:0, max:0}, which feed_bridge normalises to None —
    so 'no min AND no max' is unstated, distinct from a stated-low '$15-20'."""
    return not (job.get("hourly_min") or 0) and not (job.get("hourly_max") or 0)


def _proven_spender(job: dict) -> bool:
    """Readiness-to-pay proxy for a MISSING rate (option B, 2026-07-16): a real
    spender who just left the rate blank is worth scoring; an unproven one is
    not. Rate-unstated is not low-value, but we don't score all of them either."""
    if (job.get("client_total_spent") or 0) >= config.RATE_UNSTATED_MIN_CLIENT_SPEND_USD:
        return True
    return bool(job.get("client_payment_verified")) \
        and (job.get("client_hires") or 0) >= config.RATE_UNSTATED_MIN_HIRES


def hard_rule_skip(job: dict) -> Optional[str]:
    """Skip reason or None. Title/description only for n8n — tags do NOT
    trigger (2026-06-10 decision). No placeholder escape (v4.9)."""
    title = job.get("title") or ""
    desc = job.get("description") or ""
    text = f"{title}\n{desc}"
    if _N8N_TITLE_START_RE.search(title):
        return "n8n_exclusion"
    if _N8N_RE.search(text) and not _OTHER_TOOLS_RE.search(text):
        return "n8n_exclusion"          # sole-tool: n8n is THE subject
    if job.get("contract_type") == "Hourly":
        if _rate_unstated(job):
            # no rate stated: the $30 floor read a blank as $0 and killed ~370
            # jobs, 2/3 of that bucket, many of them real. Option B: let the
            # client's proven spend stand in for the absent rate.
            if not _proven_spender(job):
                return "rate_unstated_low_client"
            # proven spender + blank rate -> fall through and SCORE it
        elif (job.get("hourly_max") or 0) < config.HARD_FLOOR_HOURLY:
            return "hourly_below_30"    # a rate WAS stated, and it's too low
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
earned; Upwork profile rate $51.50/hr, he bids $50/hr as standard and $45/hr is
the practical compromise floor; core stack Make.com, Airtable, Vapi,
GoHighLevel, Python, AI/LLM). v2.3 scoring matrix — score each dimension, then
bonuses, then the gate:

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
    gated = bool(v.get("gated"))
    raw = v.get("raw_total")
    gate_note = None
    # CODE-ENFORCE the whale-gate EXEMPTION (v2.3). The model identifies whether a
    # job centers on GHL/Vapi/Retell (the gated category), but it applies the
    # numeric exemption unreliably -> capping real Hot leads (2026-06-22 audit:
    # 9 leads with raw 16-31 wrongly gated to 15). If the model gated a job that
    # OBJECTIVELY clears an exemption, lift the gate and restore the raw score.
    if gated and _gate_exempt(job):
        gated = False
        if raw is not None:
            effective = raw
        gate_note = (f"gate lifted by code — exemption met"
                     f" (hourly_max ${job.get('hourly_max')},"
                     f" fixed ${job.get('fixed_budget')},"
                     f" spent ${job.get('client_total_spent')}); raw {raw}")
    if gated:
        if effective > _GATE_CAP:
            effective = _GATE_CAP          # model failed the cap — code wins
        gate_note = (f"gated: capped at {_GATE_CAP}"
                     f" (raw {raw})")
    breakdown = {k: v.get(k) for k in (
        "skill_fit", "budget", "client_quality", "competition",
        "description_quality", "base_total", "bonuses", "raw_total",
        "recommendation")}
    breakdown["gate"] = gate_note
    breakdown["total"] = effective
    return {"score": effective, "gated": gated,
            "loom_flag": 1 if effective >= config.LOOM_FLAG_THRESHOLD else 0,
            "breakdown": breakdown}


def _gate_exempt(job: dict) -> bool:
    """Objective whale-gate exemptions (v2.3): ANY one lifts the category gate.
    Code-enforced because the model applies these numeric thresholds unreliably
    (Hot-lead loss). Mirrors the scoring prompt's exemption clause."""
    return ((job.get("hourly_max") or 0) >= config.GATE_HOURLY_EXEMPT_USD
            or (job.get("fixed_budget") or 0) >= config.GATE_FIXED_EXEMPT_USD
            or (job.get("client_total_spent") or 0)
            >= config.GATE_WHALE_SPEND_USD)


# ── proposal drafting (>=16 at scan time, M1 decision) ───────────────────────
#
# THE SOURCE OF TRUTH IS upwork/proposal-writing-master-reference.md, IN FULL.
#
# Until 2026-08-16 this read `skills/upwork-proposal-writer/SKILL.md`, sliced
# `[:6000]` against a 24,271-char file. Three quarters of the playbook — every
# hard rule that matters — never reached the model, and the cut landed
# mid-sentence in "2. **Budget** — Is it worth t". Measured across 2,279 live
# drafts: 1,710 contain an em dash (the ban lives at char ~11,700), 603 exceed
# the length cap (char ~10,300), 18 quote the retired $32.50 rate.
#
# The surviving quarter was worse than nothing: it ORDERED a knowledge-base
# lookup ("Before writing ANY proposal, you MUST read ... proof-points.md") that
# a single-shot, tool-less gateway call cannot perform. Told to cite a file it
# could not open, the model invented. Evidence is now SUPPLIED, in the user
# message, by modules/upwork/evidence.py — never ordered.
#
# Whole file, not a condensation: a scanner-sized digest is a third artifact
# that drifts from the file Abhijeet edits by hand, which is the exact failure
# this fixes. It costs ~1 dollar a month at observed cache-hit rates.
PROPOSAL_REFERENCE = (config.WORKSPACE_ROOT
                      / "upwork/proposal-writing-master-reference.md")

# The ONE deterministic exclusion, by exact header. Section 16 is a list of the
# founder's absolute local paths plus an Airtable base id: useless to a drafter
# and no reason to ship a filesystem layout to the API 25 times a day.
EXCLUDED_SECTIONS = ("## Section 16: File Locations",)

_DRAFT_SYSTEM_PREFIX = (
    "You draft Upwork proposals for Abhijeet Gandhi (Etherwise).\n\n"
    "The reference below is the COMPLETE and ONLY source of his voice rules,"
    " anchor numbers, rate and positioning. Follow it exactly.\n\n"
    "You have no tools and cannot open files. Everything you are permitted to"
    " assert is either in this reference or in the EVIDENCE block of the user"
    " message. If a number, a client name or a project detail is in neither,"
    " you may not write it — write the sentence without it, or leave the claim"
    " out. An unsupported specific is worse than no specific.\n\n"
    "--- REFERENCE ---\n\n")


def proposal_reference() -> str:
    """The master reference, in full, minus EXCLUDED_SECTIONS.

    Never sliced. tests/test_upwork_drafting.py pins the rules that used to
    fall off the end (em dashes, contractions, length, $50/hr) so a future
    `[:n]` cannot come back quietly."""
    text = PROPOSAL_REFERENCE.read_text()
    for header in EXCLUDED_SECTIONS:
        text = _drop_section(text, header)
    return text.strip()


def _drop_section(text: str, header: str) -> str:
    """Remove one markdown section: the header line through the line before the
    next `## ` heading or `---` rule. Header matched as a line prefix, so a
    mention of the section elsewhere is not a cut point."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines)
                  if ln.startswith(header)), None)
    if start is None:
        return text
    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].rstrip()
        if stripped.startswith("## ") or stripped == "---":
            end = i
            break
    return "".join(lines[:start] + lines[end:])


def draft_system() -> str:
    return _DRAFT_SYSTEM_PREFIX + proposal_reference()


def job_brief(job: dict) -> str:
    return ("Draft a proposal for this job. Return ONLY the proposal text.\n\n"
            f"Title: {job.get('title')}\n"
            f"Description: {(job.get('description') or '')[:3000]}\n"
            f"Type: {job.get('contract_type')}"
            f" Budget: {job.get('fixed_budget') or ''}"
            f" ${job.get('hourly_min')}-{job.get('hourly_max')}/hr\n"
            f"Skills: {job.get('skills_json')}")


def draft_proposal(job: dict, task_name: str, db_path=None,
                   evidence_block: Optional[str] = None) -> str:
    """One drafting call. `evidence_block` (from modules.upwork.evidence) goes
    in the USER message, never the system block — the system block is prompt-
    cached for an hour and per-job evidence would bust the cache on every job."""
    user = job_brief(job)
    if evidence_block:
        user = user + "\n\n" + evidence_block
    result = runner.claude_call(
        task_name=task_name, model_key="drafting",
        system=draft_system(), user_content=user,
        purpose=f"draft {job.get('id')}", db_path=db_path)
    return result.text.strip()
