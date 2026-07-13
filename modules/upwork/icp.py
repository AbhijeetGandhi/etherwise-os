"""ICP / Prime tier — Abhijeet's ideal-client profile (2026-07-13).

The 16+ 'Hot' band had become a rubber stamp (81% of scored jobs), so this adds
a TOP tier above it. It is deliberately DETERMINISTIC (code, not model): every
input is an objective fact from the feed payload, and objective thresholds are
exactly what the model applies unreliably (the same reason the whale-gate
exemption is code-enforced).

The profile (his words):
  1. tool includes Airtable or Make.com
  2. $51/hr is in the budget range  (fixed-price equivalent: >= $5,000)
  3. client has spent > $50k AND pays real rates (readiness to pay)
  4. long-term opportunity, not a quick fix — and NOT agency-hostile

Shape (his call): MUST-HAVES + a NEAR-MISS tier.
  MUST (all three, or it is not ICP at any level):
      tool  — Airtable/Make.com in skills, title or description
      rate  — hourly top >= $51, or fixed >= $5,000
      agency_ok — the post does not exclude agencies
  QUALIFIERS (both -> ICP · exactly one -> ICP near-miss):
      wealth    — client spent >= $50k
      long_term — ongoing engagement, not a one-off fix

Near-miss exists so the bar is tunable from real data instead of guesswork:
you see what ALMOST qualified and why.

KNOWN GAP — "average hourly rate paid": it is NOT in the feed payload. It lives
on the job-detail panel (Upwork ships it as `avgHourlyJobsRate` inside the job
page's compressed Nuxt payload). Until that enrichment lands, readiness-to-pay
is carried by spend >= $50k plus SPEND_PER_HIRE_USD (totalSpent/totalHires) —
a client averaging $2k+ per hire is not a $5-gig buyer. `wealth_note` always
states which signal was used, so nothing is silently assumed.
"""
from __future__ import annotations

import re
from typing import Optional

from core import config

# core-stack tools that define our wheelhouse
_TOOL_RE = re.compile(r"\b(airtable|make\.com|make dot com)\b", re.I)
# posts that exclude agencies — a hard disqualifier (we ARE an agency)
_NO_AGENCY_RE = re.compile(
    r"\bno\s+agenc(?:y|ies)\b|\bagencies\s+(?:need\s+not|please\s+do\s+not|will\s+be)"
    r"|\bindividual(?:s)?\s+only\b|\bfreelancers?\s+only\b|\bno\s+(?:teams|firms)\b"
    r"|\bno\s+outsourc\w*\b|\bdirect\s+freelancers?\s+only\b", re.I)
# one-off shapes: a quick fix is the opposite of what we want
_QUICK_FIX_RE = re.compile(
    r"\bone[- ]?(?:time|off)\b|\bquick\s+(?:fix|task|job)\b|\bsmall\s+task\b"
    r"|\bsingle\s+(?:task|fix)\b|\bad[- ]?hoc\b", re.I)
_LONG_TERM_RE = re.compile(
    r"\blong[- ]?term\b|\bongoing\b|\bretainer\b|\bpart[- ]?time\b"
    r"|\bfull[- ]?time\b|\bmonthly\b|\bcontinu\w+\b", re.I)
# durationLabel values that mean a real engagement
_LONG_DURATIONS = ("3 to 6 months", "more than 6 months", "1 to 3 months")


def _text(job: dict) -> str:
    skills = job.get("skills") or []
    if isinstance(skills, str):                  # skills_json round-trip
        skills = [skills]
    return "\n".join([str(job.get("title") or ""),
                      str(job.get("description") or ""),
                      " ".join(str(s) for s in skills)])


def has_tool(job: dict) -> bool:
    return bool(_TOOL_RE.search(_text(job)))


def rate_ok(job: dict) -> bool:
    """$51/hr must be IN the budget range (i.e. the top of the range reaches it),
    or the fixed budget clears the fixed-price equivalent."""
    if (job.get("hourly_max") or 0) >= config.ICP_HOURLY_USD:
        return True
    return (job.get("fixed_budget") or 0) >= config.ICP_FIXED_USD


def agency_ok(job: dict) -> bool:
    return not _NO_AGENCY_RE.search(_text(job))


def is_wealthy(job: dict) -> tuple:
    """(bool, note). Spend is the primary gate; spend-per-hire is the interim
    readiness-to-pay signal standing in for the avg hourly rate paid."""
    spent = job.get("client_total_spent") or 0
    hires = job.get("client_hires") or 0
    if spent < config.ICP_CLIENT_SPEND_USD:
        return False, f"client spent ${spent:,.0f} < ${config.ICP_CLIENT_SPEND_USD:,.0f}"
    if hires:
        per_hire = spent / hires
        if per_hire < config.ICP_SPEND_PER_HIRE_USD:
            return False, (f"spent ${spent:,.0f} but only ${per_hire:,.0f}/hire"
                           f" — pays small (avg-rate proxy)")
        return True, (f"spent ${spent:,.0f} · ${per_hire:,.0f}/hire"
                      f" (avg-rate proxy; true avg rate pending enrichment)")
    return True, f"spent ${spent:,.0f} (no hire count — proxy unavailable)"


def is_long_term(job: dict, breakdown: Optional[dict] = None) -> tuple:
    """(bool, note). Structured duration/engagement first; the model's long_term
    bonus is corroborating evidence; an explicit quick-fix phrase vetoes."""
    text = _text(job)
    if _QUICK_FIX_RE.search(text) and not _LONG_TERM_RE.search(text):
        return False, "reads as a one-off / quick fix"
    dur = str(job.get("engagement_duration") or "").strip().lower()
    if dur in _LONG_DURATIONS:
        return True, f"duration: {job.get('engagement_duration')}"
    # breakdown shape drifted over time (early rows stored a list) — anything
    # that is not the dict we expect simply carries no corroborating bonus
    bd = breakdown if isinstance(breakdown, dict) else {}
    bonuses = bd.get("bonuses")
    bonus = (bonuses.get("long_term") or 0) if isinstance(bonuses, dict) else 0
    if bonus >= 3:
        return True, "model flagged long-term/ongoing"
    if _LONG_TERM_RE.search(text):
        return True, "post describes ongoing/long-term work"
    return False, f"no long-term signal (duration: {job.get('engagement_duration') or '—'})"


def classify(job: dict, score: Optional[int] = None,
             breakdown: Optional[dict] = None) -> dict:
    """-> {tier, reasons, misses}. tier: 'icp' | 'icp_near' | None.

    A tier is only awarded to a job that ALSO clears the hot bar — ICP is a
    quality overlay on a good lead, never a way to promote a weak one."""
    must = {"tool": has_tool(job), "rate": rate_ok(job), "agency_ok": agency_ok(job)}
    wealthy, wealth_note = is_wealthy(job)
    longterm, lt_note = is_long_term(job, breakdown)

    reasons, misses = [], []
    if must["tool"]:
        reasons.append("core stack (Airtable/Make.com)")
    else:
        misses.append("no Airtable/Make.com")
    if must["rate"]:
        reasons.append(f"budget reaches ${config.ICP_HOURLY_USD}/hr"
                       if (job.get("hourly_max") or 0) >= config.ICP_HOURLY_USD
                       else f"fixed ${job.get('fixed_budget'):,.0f}")
    else:
        misses.append(f"budget below ${config.ICP_HOURLY_USD}/hr"
                      f" / ${config.ICP_FIXED_USD:,.0f} fixed")
    if not must["agency_ok"]:
        misses.append("post excludes agencies")
    (reasons if wealthy else misses).append(wealth_note)
    (reasons if longterm else misses).append(lt_note)

    hot = score is None or score >= config.HOT_LEAD_THRESHOLD
    tier = None
    if all(must.values()) and hot:
        quals = int(wealthy) + int(longterm)
        if quals == 2:
            tier = "icp"
        elif quals == 1:
            tier = "icp_near"
    return {"tier": tier, "reasons": reasons, "misses": misses,
            "must": must, "wealthy": wealthy, "long_term": longterm}
