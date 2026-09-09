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
# The Category Priority Gate caps a GHL/Vapi/Retell-centric job so it can never
# be Hot while staying VISIBLE. It was 15 against a hot bar of 20; after the
# 2026-09-09 re-cut the bar is 23, so 15 would have put every gated job under
# the card floor entirely — silently deleting the category rather than demoting
# it. 22 keeps the original meaning: below Hot, above the floor.
_GATE_CAP = 22

# ── v3.0, 2026-09-08: the platform gate + the off-domain titles ───────────────
# Abhijeet: "there is lot of noise we definitely need to improve our socring.
# like past 150 jobs I looked at I probably just opened 10 and skipped 140 or
# afteropening I kep only 2 for applying."
#
# MEASURED, not guessed. 2,006 hot cards from 1 June to 8 September against the
# 50 he actually submitted a proposal on. The split is not in the client fields
# — spend, hires, rating and applicant count all convert within a point of the
# 2.5% base rate — it is in WHAT THE JOB IS. Title words, cards / his
# applications:
#
#     airtable 122/20 (14.1%)   make.com 127/11   zapier 45/3   n8n 82/4
#     claude   182/0            voice     90/0    marketing 199/0   api 82/0
#     full stack 78/0           agent(ic) 245/2   engineer 264/5
#
# He is a no-code systems consultant. The feed had been hunting AI engineers.
# So: a job must NAME a platform he works in, and must not be one of the
# adjacent trades he has never once bid on.
#
# THE VOCABULARY IS PLATFORMS, NOT TECHNOLOGIES. Adding openai/gpt/claude to
# this list passes 218 more cards and keeps exactly the same 47 of his 50
# applications — an AI vendor name predicts nothing about whether he bids. The
# tools below are the ones a client BUYS configuration of.
_PLATFORMS_RE = re.compile(
    r"airtable|make\.com|integromat|zapier|n8n|hubspot|gohighlevel|\bghl\b|twilio"
    r"|clickup|monday\.com|notion|smartsuite|softr|glide|retool|pipedrive"
    r"|activecampaign|klaviyo|salesforce|quickbooks|calendly|webflow|apify"
    r"|squarespace|teachable|pandadoc|workiz|google sheets|google workspace"
    r"|apps script|xero|stripe|shopify|typeform|jotform|vapi|retell|zoho"
    r"|freshdesk|intercom|mailchimp|instantly|apollo\.io|clay\.com|slack|asana"
    r"|trello|wordpress|bubble\.io|supabase|\bwix\b|close\.com|copper crm", re.I)

# Trades that are next door to his and are not his. Every one of these is a
# TITLE test — the words appear harmlessly inside descriptions ("...for our
# marketing team") and would over-fire on the body. 212 cards since June, zero
# applications between them.
_OFF_DOMAIN_TITLE_RE = re.compile(
    r"\bvoice\b|marketing|funnel|\bads\b|\bseo\b|copywrit|\bcontent\b", re.I)

# HIS OWN CATEGORY OVERRIDES THE PLATFORM GATE. Abhijeet, 2026-09-08: "make sure
# that if something says AI automation develoepr it is not skipped and then you
# make decision based on the actual descipriton for."
#
# He is right, and the first cut of the gate proved it: "AI Automation
# Specialist for Business Solutions" scored 30 and he bid on it, and it named
# no platform in either field. A post that calls itself automation, integration
# or workflow work is his trade by its own description; whether it is REALLY
# his is a judgment about the body, and judgment is what the scorer is for. So
# the title exempts it from the gate and it goes to the model, which grades
# skill fit on what the description actually says — and a bad one then dies at
# the score, which is the right place for a close call to die.
#
# Deliberately these three words and no more. Adding systems / ops / no-code /
# crm lets 99 further cards through and recovers not one additional
# application; the exemption is a door, not a hole.
_CATEGORY_TITLE_RE = re.compile(r"automation|integrat|workflow", re.I)

# A description too thin to read cannot be used as evidence of ABSENCE. ~200 of
# 4,451 scored rows have almost no body; they fall through to the model rather
# than being dropped on a technicality (quarantine over silent drop).
_MIN_DESC_FOR_GATE = 200

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
                           "whale_client": {"type": "integer"}},
            # `claude` and `fresh` are gone entirely, not zero-weighted: a field
            # the model is asked to fill is a field it weighs, and both were
            # pulling toward the pile he skips. The four that remain are the
            # four that count.
            "required": ["core_stack", "long_term", "invite", "whale_client"],
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
    # A PLATFORM IN THE TITLE OUTRANKS THIS. Abhijeet, 2026-09-09, on
    # "Airtable + Make.com Expert - Ecommerce Operations, Production Dashboard,
    # and Marketing Hub": "I don't know why this is not even showing up". It was
    # never scored — the word `marketing` matched, inside the NAME OF A THING
    # THEY WANT BUILT. A Marketing Hub is the object of the work, not the trade.
    # The rule killed the single best-converting shape he has (Airtable and
    # Make.com, both in the title) on a substring in a proper noun.
    # When his own tools are named in the title the job goes to the model, which
    # reads the body and grades skill fit; a GHL marketing-ops post then dies at
    # the score, which is where a borderline call belongs. 14 of the 38 titles
    # this rule has killed name a platform, and most of those really are
    # marketing work — they will now cost one model call each and score low.
    if _OFF_DOMAIN_TITLE_RE.search(title) and not _PLATFORMS_RE.search(title):
        return "off_domain_title"       # voice / marketing / funnel / content
    if (len(desc) >= _MIN_DESC_FOR_GATE
            and not _CATEGORY_TITLE_RE.search(title)      # his trade: let it score
            and not _PLATFORMS_RE.search(text)):
        return "no_platform_named"      # an AI-engineering post, not his work
    return None


# ── prompt assembly ───────────────────────────────────────────────────────────

_MATRIX = """\
You score Upwork jobs for Etherwise (Abhijeet Gandhi — Top Rated Plus, $100K+
earned; Upwork profile rate $51.50/hr, he bids $50/hr as standard and $45/hr is
the practical compromise floor; core stack Make.com, Airtable, Vapi,
GoHighLevel, Python, AI/LLM). v2.3 scoring matrix — score each dimension, then
bonuses, then the gate:

Dimensions (base, max 28):
- Skill Fit: RETURN THE PLAIN 1-5 GRADE. Do not pre-multiply it — code applies
  the x4. (Until 2026-09-09 this prompt asked for it doubled, the model returned
  it plain about half the time, and the code halved whatever arrived. Both
  readings are now handled, but return 1-5.)
  Weighted x4, so this IS the score and everything else is a tie-breaker. Measured over 2,006 scored jobs against the 50 he actually bid
  on: skill fit averaged 7.1 (of 10) on the ones he took and 4.6 on the ones he
  ignored, while budget, client quality, competition and description quality
  were flat to within a point. Grade it as "is this the work he sells", not "is
  this a good job".
- Budget 1-5
- Client Quality 1-3 (capped; NEVER penalize a new client — $0 spent with no
  red flags = 2)
- Competition 1-5 — SCORED FOR THE RECORD, NOT IN THE TOTAL. It came out
  slightly backwards (3.95 on the ones he took, 4.26 on the ones he skipped):
  the crowded posts are the good ones. Report it; it does not move the score.
- Description Quality 1-5 — same: scored, reported, not in the total (4.39 vs
  4.33, no signal).

Bonuses: core stack +3 · direct invite +4 · long-term/ongoing (20+ hr/wk or
multi-month) +3 · client spent $100K+ +2. Nothing else.
  Posted-under-an-hour and mentions-Claude were bonuses until 2026-09-08 and
  both fired more often on the jobs he skipped than on the ones he took — the
  Claude one worst of all, +2 for a word that appeared in 182 hot cards and
  produced zero applications. Do not reward a buzzword; reward the work.

Category Priority Gate (v2.3): if the job CENTERS on GoHighLevel/GHL, Vapi,
or Retell, it is gated UNLESS any of: client total spent >= $50,000, hourly
top >= $45/hr, fixed budget >= $3,000. A gated job that fails the whale gate
is capped at effective 15 (Standard — never Hot, never Loom). Make.com /
Airtable / n8n / Python / generic-AI jobs are NEVER gated; direct invites
exempt; GHL/Vapi as one endpoint in a Make/Airtable-primary build is NOT
gated. Set gated=true and cap effective_total accordingly; record raw_total
uncapped.

Report every dimension and both bonuses. THE ARITHMETIC IS NOT YOURS TO DO —
code computes base_total = skill_fit*4 + budget + client_quality, adds the two
bonuses, and applies the gate cap. Fill the numbers in honestly; a total you
return is recorded and ignored.
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

SCORER_VERSION = "3.0"

# All four of long_term, whale_client, fresh and claude fired MORE often on the
# jobs he skipped than on the ones he bid on. HIS CALL, 2026-09-08, on what to
# do about that: "for four bonuses just delete claude and fresh keep the long
# term and whale". Claude and fresh are gone from the schema entirely; long-term
# and whale-client stay, because a signal that has not moved his BIDDING can
# still be the kind of client he wants more of, and that is a business call, not
# a correlation.
#
# The cost is small and real, so it is written down: ranked by this set, the top
# 500 of the gated pool holds 31 of his 52 applications against 32 with
# core_stack+invite alone, and the top 800 holds 40 against 43.
COUNTED_BONUSES = ("core_stack", "invite", "long_term", "whale_client")
_SKILL_FIT_WEIGHT = 4          # of a 1-5 grade; see _MATRIX for the evidence


def _raw_fit(value) -> float:
    """The 1-5 skill-fit grade, whatever shape the row stores it in."""
    v = value or 0
    if v > 5:                       # only a doubled grade can land here
        v = v / 2.0
    return max(0.0, min(5.0, v))


def compute_total(v: dict) -> tuple:
    """(base_total, raw_total) from the graded dimensions. ARITHMETIC IN CODE,
    never the model's — the score has to be re-derivable from the stored
    breakdown, which is also what lets a re-weighting be back-tested over
    history without spending a single model call.

    `skill_fit` IS THE RAW 1-5 GRADE. It was not, for one day: the v2.3 prompt
    said "WEIGHTED x2" and this function divided by two to recover the grade —
    but the model returned the plain 1-5 number about half the time even then,
    and under the v3.0 prompt it returns 1-5 always. Dividing anyway halved the
    single component the score now rests on. Abhijeet caught it on two of his
    own jobs the same morning: an Airtable build the scorer graded 5 of 5 and
    called "Hot — bid this one" came out at 17, and an automation audit it
    called "squarely Abhijeet's lane" came out at 18. Their real scores are 27
    and 26.

    `_raw_fit` recovers both shapes, because a stored value ABOVE 5 can only be
    a doubled one. A stored 2 or 4 under the old prompt is genuinely ambiguous
    and is read as the raw grade — the reading that matches what the model
    returns today, and the one that errs toward showing him a job rather than
    hiding it.

    Competition and description quality are graded and recorded but do not
    enter: measured over 2,006 jobs they carried no signal, and one of them
    carried it backwards."""
    fit = _raw_fit(v.get("skill_fit"))
    base = int(round(fit * _SKILL_FIT_WEIGHT
                     + (v.get("budget") or 0) + (v.get("client_quality") or 0)))
    bon = v.get("bonuses") or {}
    return base, base + sum(bon.get(k) or 0 for k in COUNTED_BONUSES)


def score_job(job: dict, task_name: str, db_path=None) -> dict:
    """Returns {score, gated, loom_flag, breakdown} — effective score, gate
    cap re-enforced in code."""
    result = runner.claude_call(
        task_name=task_name, model_key="scoring",
        system=system_prompt(), user_content=job_input(job),
        schema=SCHEMA, purpose=f"score {job.get('id')}",
        max_output_tokens=700, db_path=db_path)
    v = result.parsed
    base, raw = compute_total(v)
    v["base_total"], v["raw_total"] = base, raw
    gated = bool(v.get("gated"))
    effective = min(raw, _GATE_CAP) if gated else raw
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
    breakdown["scorer_version"] = SCORER_VERSION
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


def _fact_note(f: dict) -> dict:
    """The provenance row for one supplied fact — enough to audit the claim
    later without copying the whole KB into scored_jobs."""
    return {"id": f.get("id"), "tool": f.get("tool"), "grade": f.get("grade"),
            "age_days": f.get("age_days"),
            "citation": (f.get("citation") or "")[:200],
            "fact_text": (f.get("fact_text") or "")[:300]}


def draft_proposal_grounded(job: dict, task_name: str, db_path=None):
    """(draft_text_or_None, provenance) — the whole emit ladder, code-enforced.

    1. Enough graded evidence  -> draft WITH the numbered block, then validate.
    2. Rejected, or there was never enough evidence -> plain draft, validate.
    3. Rejected again -> NO DRAFT. The card is still created; he writes it.

    Step 3 is the point. A claim that traces to nothing must not be able to
    survive the emit path — a hallucinated proof point sitting on a ClickUp
    card, one copy-paste from a client, is worse than an empty card. This has
    always been the honest outcome; before today nothing could tell."""
    from modules.upwork import evidence     # local: keeps import order simple

    ev = evidence.for_job(job, db_path=db_path)
    reference = proposal_reference()
    prov = {"status": ev["status"], "found": ev.get("found", 0),
            "stack": ev.get("stack", [])}
    if ev.get("reason"):
        prov["reason"], prov["min"] = ev["reason"], ev.get("min")

    # Two rungs, always. When evidence exists rung 1 carries it and rung 2 is
    # the plain path; when it does not, BOTH rungs are plain and the second
    # gets the validator's complaint. Measured 2026-08-16: a thin-evidence job
    # with only one rung got exactly one shot and was blocked outright, which
    # is not a fallback, it is a coin flip.
    plan = [("evidence", ev["block"], ev["facts"])] \
        if ev["status"] == "supplied" else [("plain", None, [])]
    plan.append(("plain", None, []))

    last_problems = []
    for attempt, block, facts in plan:
        addendum = evidence.retry_note(last_problems)
        text = draft_proposal(
            job, task_name, db_path=db_path,
            evidence_block="\n\n".join(p for p in (block, addendum) if p)
            or None)
        ok, problems = evidence.validate_draft(text, reference, facts, job)
        if ok:
            prov.update({"attempt": attempt, "validated": True,
                         "retried": bool(last_problems),
                         "facts": [_fact_note(f) for f in facts]})
            if last_problems:
                prov["rejected"] = last_problems[:6]
            if attempt == "plain" and ev["status"] == "supplied":
                # evidence existed; the model still made something up with it
                prov["status"] = "none"
                prov["reason"] = "evidence_draft_failed_validation"
            return text, prov
        last_problems = problems

    prov.update({"attempt": None, "validated": False, "status": "none",
                 "reason": "validation_failed_on_every_attempt",
                 "problems": last_problems[:12]})
    return None, prov
