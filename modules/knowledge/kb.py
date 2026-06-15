"""Knowledge fact protocol — code-enforced (knowledge/SCHEMA.md).

Citations are mandatory and confidence tags are constrained; nothing else in
the pipeline persists a fact without going through validate_fact() (and the
004_kb.sql CHECK/NOT-NULL constraints are the defense-in-depth backstop).
"""
from __future__ import annotations

import hashlib
import re

CONFIDENCE_TAGS = ("CONFIRMED", "CROSS-VERIFIED", "INFERRED")
CATEGORIES = ("overview", "people", "numbers", "timeline", "architecture",
              "stack", "commitment", "quote", "other")
# proposal-writer may only use these (SCHEMA Rule 2)
TRUSTED_TAGS = ("CONFIRMED", "CROSS-VERIFIED")

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
    return fact
