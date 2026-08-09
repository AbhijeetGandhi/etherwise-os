"""Knowledge fact protocol — code-enforced (knowledge/SCHEMA.md).

Citations are mandatory and confidence tags are constrained; nothing else in
the pipeline persists a fact without going through validate_fact() (and the
004_kb.sql CHECK/NOT-NULL constraints are the defense-in-depth backstop).
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

CONFIDENCE_TAGS = ("CONFIRMED", "CROSS-VERIFIED", "INFERRED")
CATEGORIES = ("overview", "people", "numbers", "timeline", "architecture",
              "stack", "commitment", "quote", "other")
# proposal-writer may only use these (SCHEMA Rule 2)
TRUSTED_TAGS = ("CONFIRMED", "CROSS-VERIFIED")
# scope routes a fact to its artifact: client dossier vs proof-points wiki
SCOPES = ("client", "agency", "cross-client")

_WS = re.compile(r"\s+")


class UncitedFact(Exception):
    """A fact missing a citation or a valid confidence tag — never persisted."""


def content_hash(text: str) -> str:
    """Stable sha256 over whitespace-normalized content (dedup key)."""
    normalized = _WS.sub(" ", (text or "")).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def validate_fact(fact: dict) -> dict:
    """Enforce SCHEMA Rules 1-2. Raises UncitedFact; returns the fact if OK."""
    if not (fact.get("citation") or "").strip():
        raise UncitedFact(f"fact has no citation: {fact.get('fact_text')!r}")
    if fact.get("confidence") not in CONFIDENCE_TAGS:
        raise UncitedFact(
            f"invalid confidence {fact.get('confidence')!r}"
            f" (must be one of {CONFIDENCE_TAGS})")
    if not (fact.get("fact_text") or "").strip():
        raise UncitedFact("empty fact_text")
    scope = fact.get("scope", "client")
    if scope not in SCOPES:
        raise UncitedFact(f"invalid scope {scope!r} (must be one of {SCOPES})")
    return fact


# ── Noise gate: deterministic backstop to any extractor's exclusion prompt ────
# Born on the Jarvis EXTRACT path (commit d340f53, recorded 28d661e) after the
# first real batch showed the reader still emits filler even when told not to:
# WHO was on a call, that a call happened, footer/marketing boilerplate, and
# ephemeral calendar times. A prompt asks the model to skip these; this code
# GUARANTEES it (a prompt alone drifts). STRUCTURAL patterns only — never topic
# keywords — so a genuine requirement ("Zoom for registration, live registration
# counts"), a contact/role, or a stack fact is never caught. Validated line-by-
# line against the 23-fact batch: drops the 13 noise lines, keeps all 10 signal
# lines ("speaker prep calls" and "recorded webinar page" survive while
# "recorded calls" drops). SHARED so every extractor (Jarvis + knowledge/loom)
# applies the one identical gate — never a second implementation.
_NOISE_PATTERNS = (
    # 1. participant rosters — WHO attended, not what was said. Already held in
    #    sources/events; as a "fact" it only dilutes a dossier.
    ("participant_roster", re.compile(
        r"\bparticipated in (?:a|an|the)\b"
        r"|\betherwise[\w-]* (?:contact|participant)\b"
        r"|\bparticipants?\b[^.]*\b(?:call|calls|meeting|meetings)\b"
        r"|\b(?:call|calls|meeting|meetings)\b[^.]*\bparticipants?\b", re.I)),
    # 2. "a call happened" meta-facts — a call's existence or count, never its
    #    content. Knowledge comes from INSIDE a call, not a description of it.
    ("meeting_meta", re.compile(
        r"\b(?:call|calls|meeting|meetings)\b[^.]*"
        r"\b(?:took place|occurred|happened)\b"
        r"|\brecorded calls?\b"
        r"|\bat least \w+ (?:recorded )?calls?\b", re.I)),
    # 3. email-signature / marketing / legal-registration boilerplate — scraped
    #    from a footer or disclaimer, never real project knowledge.
    ("boilerplate", re.compile(
        r"\bpositions? itself as\b"
        r"|\bpioneer of\b"
        r"|\bleading provider\b"
        r"|\boffering \d+\+?\s+(?:services|products)\b"
        r"|\bAmtsgericht\b|\bHRB\s*\d|\bUSt-?IdNr\b|\bVAT (?:ID|number)\b"
        r"|\bregistration (?:court|number)\b", re.I)),
    # 4. ephemeral calendar logistics — a meeting time/reschedule/invite. True
    #    for a day, then permanently misleading.
    ("calendar_logistics", re.compile(
        r"\breschedul(?:e|ed|es|ing)\b"
        r"|\b(?:meeting|call|invite)\b[^.]*\b(?:moved|pushed|shifted) to\b"
        r"|\b\d{1,2}:\d{2}[^.]{0,25}"
        r"\b(?:IST|UTC|GMT|EST|EDT|PST|PDT|BST|CET)\b", re.I)),
)


def noise_reason(text: str) -> Optional[str]:
    """Return the noise-class name if this fact is filler that must be dropped
    before it reaches the KB, else None. Backstops an extractor's exclusion
    prompt. STRUCTURAL only — a real requirement/contact/stack fact is safe."""
    t = text or ""
    for name, rx in _NOISE_PATTERNS:
        if rx.search(t):
            return name
    return None
