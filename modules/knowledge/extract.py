"""Extract cited facts from a source transcript via Sonnet (through the
gateway). Output is schema-constrained; every fact is then validated against
the protocol (kb.validate_fact) and uncited/untagged facts are dropped before
they can reach the DB.
"""
from __future__ import annotations

from typing import List, Optional

from core import runner
from modules.knowledge import kb

# Sonnet tier for extraction quality (SCHEMA/K4). Reuses the drafting key
# (claude-sonnet-4-6); M3 may get its own MODELS["extraction"] key later.
EXTRACT_MODEL_KEY = "drafting"

SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": list(kb.CATEGORIES)},
                    "fact_text": {"type": "string"},
                    "confidence": {"type": "string",
                                   "enum": list(kb.CONFIDENCE_TAGS)},
                    "locator": {"type": "string"},  # timestamp/quote anchor
                },
                "required": ["category", "fact_text", "confidence", "locator"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}

SYSTEM = (
    "You extract durable, client-relevant FACTS from a transcript for an "
    "automation agency's per-client knowledge base. Rules (enforced):\n"
    "- Emit ONLY what the transcript supports. No fabrication, no guessing.\n"
    "- Each fact: a concrete claim (what was built, tools/stack, numbers, "
    "people, decisions, commitments, or a verbatim quote).\n"
    "- confidence: CONFIRMED (directly stated) · CROSS-VERIFIED (stated +"
    " corroborated elsewhere in this source) · INFERRED (reasonable, not"
    " stated). When unsure, prefer INFERRED or omit.\n"
    "- locator: a transcript timestamp (HH:MM:SS) or a short verbatim quote"
    " anchoring the fact — REQUIRED; a fact with no anchor is useless.\n"
    "- Skip small talk, scheduling, pleasantries. Quality over volume."
)


def extract_facts(source: dict, text: str, db_path=None,
                  max_tokens: int = 2000) -> List[dict]:
    """Returns validated cited facts: [{category, fact_text, confidence,
    citation}]. citation = '<source_ref> @ <locator>'. Uncited/untagged
    facts from the model are dropped (never persisted)."""
    result = runner.claude_call(
        task_name="kb_extract", model_key=EXTRACT_MODEL_KEY,
        system=SYSTEM, schema=SCHEMA,
        user_content=f"Source: {source.get('source_ref')}\n\n{text[:20000]}",
        purpose=f"extract {source.get('source_ref')}",
        max_output_tokens=max_tokens, db_path=db_path)
    raw = (result.parsed or {}).get("facts", [])
    out = []
    for f in raw:
        locator = (f.get("locator") or "").strip()
        fact = {
            "category": f.get("category") or "other",
            "fact_text": (f.get("fact_text") or "").strip(),
            "confidence": f.get("confidence"),
            "citation": f"{source.get('source_ref')} @ {locator}"
            if locator else "",
        }
        try:
            out.append(kb.validate_fact(fact))   # drops uncited/untagged
        except kb.UncitedFact:
            continue
    return out
