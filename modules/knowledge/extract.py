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
                    "scope": {"type": "string", "enum": list(kb.SCOPES)},
                    "locator": {"type": "string"},  # timestamp/quote anchor
                },
                "required": ["category", "fact_text", "confidence", "scope",
                             "locator"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}

SYSTEM = (
    "You extract durable, relevant FACTS from a transcript for an automation "
    "agency's knowledge base. The call is WITH a specific client, but speakers "
    "also discuss the agency's general methods and OTHER clients' projects as "
    "examples. Tag each fact's scope so it lands in the right place. Rules "
    "(enforced):\n"
    "- Emit ONLY what the transcript supports. No fabrication, no guessing.\n"
    "- Each fact: a concrete claim (what was built, tools/stack, numbers, "
    "people, decisions, commitments, or a verbatim quote).\n"
    "- scope: 'client' = a fact ABOUT this call's client (their systems, their "
    "numbers, their people, their decisions). 'agency' = the agency's general "
    "positioning/method/capability not specific to any one client (e.g. 'we do "
    "no-code automation, not custom platforms'). 'cross-client' = a fact about "
    "a DIFFERENT client's project mentioned as an example (e.g. a voice-agent "
    "project for some other customer). When in doubt between client and agency, "
    "ask: is this true only of THIS client? If yes -> client.\n"
    "- confidence: CONFIRMED (directly stated) · CROSS-VERIFIED (stated +"
    " corroborated elsewhere in this source) · INFERRED (reasonable, not"
    " stated). When unsure, prefer INFERRED or omit.\n"
    "- locator: a transcript timestamp (HH:MM:SS) or a short verbatim quote"
    " anchoring the fact — REQUIRED; a fact with no anchor is useless.\n"
    "- NEVER emit as a fact (these dilute a dossier, they are not knowledge):\n"
    "  1. participant rosters — WHO attended or was on the call ('X and Y "
    "participated', 'X is the Etherwise contact'). A person's ROLE (title, what "
    "they own) is fine; their mere presence is not.\n"
    "  2. the mere fact that a call/recording happened, or how many there were "
    "('a call took place', 'this is a recorded walkthrough', 'at least six "
    "recorded calls'). Extract what was SAID/SHOWN, never that it exists.\n"
    "  3. anything from a signature, footer, legal disclaimer, "
    "company-registration line, or unsubscribe block — including marketing "
    "taglines ('positions itself as', 'pioneer of', 'leading provider') and "
    "registration numbers (HRB, Amtsgericht, VAT).\n"
    "  4. calendar logistics — a meeting time, reschedule, or invite. True for a "
    "day, then misleading forever.\n"
    "- The bar for a fact: something we could ACT on or would need to know months "
    "from now — a decision, requirement, system/stack detail, number, "
    "constraint, preference, problem, a person's ROLE. Practical test before "
    "emitting each fact: would this still be useful in SIX MONTHS, and does it "
    "say something the title does not already? If no, drop it.\n"
    "- Skip small talk, scheduling, pleasantries. Quality over volume."
)


def extract_facts(source: dict, text: str, db_path=None,
                  max_tokens: int = 4000, model_key: str = EXTRACT_MODEL_KEY,
                  metrics: Optional[dict] = None) -> List[dict]:
    """Returns validated cited facts: [{category, fact_text, confidence,
    citation}]. Uncited/untagged facts from the model are dropped, and the
    shared kb.noise_reason gate drops roster/meeting-meta/boilerplate/calendar
    filler (never persisted).

    citation = '<cite_ref> @ <locator>'. cite_ref defaults to the source_ref
    but a caller may pass `source['cite_ref']` to deep-link the artifact — e.g.
    a Loom share URL (https://www.loom.com/share/<id>) so a fact clicks straight
    to the recording at the anchoring phrase.

    model_key selects the tier (default Sonnet 'drafting', matching the Fathom
    backfill; loom uses the cheaper Haiku 'classify' pin — single-narrator
    walkthroughs extract cleanly and the 97-source batch stays under the daily
    cap). If `metrics` is passed, cost_usd and dropped_noise accumulate into it
    (non-breaking; existing callers ignore it)."""
    cite_ref = source.get("cite_ref") or source.get("source_ref")
    result = runner.claude_call(
        task_name="kb_extract", model_key=model_key,
        system=SYSTEM, schema=SCHEMA,
        user_content=f"Source: {source.get('source_ref')}\n\n{text[:20000]}",
        purpose=f"extract {source.get('source_ref')}",
        max_output_tokens=max_tokens, db_path=db_path)
    if metrics is not None:
        metrics["cost_usd"] = metrics.get("cost_usd", 0.0) + float(
            getattr(result, "cost_usd", 0.0) or 0.0)
    raw = (result.parsed or {}).get("facts", [])
    out = []
    for f in raw:
        locator = (f.get("locator") or "").strip()
        fact_text = (f.get("fact_text") or "").strip()
        if fact_text and kb.noise_reason(fact_text):     # shared quality gate
            if metrics is not None:
                metrics["dropped_noise"] = metrics.get("dropped_noise", 0) + 1
            continue
        fact = {
            "category": f.get("category") or "other",
            "fact_text": fact_text,
            "confidence": f.get("confidence"),
            "scope": f.get("scope") or "client",
            "citation": f"{cite_ref} @ {locator}" if locator else "",
        }
        try:
            out.append(kb.validate_fact(fact))   # drops uncited/untagged
        except kb.UncitedFact:
            continue
    return out
