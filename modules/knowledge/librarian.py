"""kb-librarian: answer a question about a client strictly from cited facts in
the kb. Retrieval is FTS over the facts table (client-scoped when known); the
model may ONLY use the supplied facts and must cite them, or say the kb does
not cover it. No fabrication — the answer is grounded or it is a refusal.
"""
from __future__ import annotations

import re
from typing import List, Optional

from core import runner
from modules.knowledge import index, kb

ANSWER_MODEL_KEY = "drafting"            # Sonnet (SCHEMA/K4)

_STOP = {"the", "a", "an", "of", "to", "is", "are", "what", "who", "how",
         "when", "where", "why", "did", "do", "does", "for", "in", "on",
         "and", "or", "with", "this", "that", "their", "our", "we", "they",
         "client", "about", "tell", "me", "give", "have", "has", "was"}

SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "grounded": {"type": "boolean"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "grounded", "citations"],
    "additionalProperties": False,
}

SYSTEM = (
    "You are the kb-librarian for an automation agency. Answer the question "
    "USING ONLY the cited facts provided — never invent, never use outside "
    "knowledge. Rules (enforced):\n"
    "- Ground every claim in the provided facts and include their citations.\n"
    "- Prefer CONFIRMED / CROSS-VERIFIED facts for client-facing statements; "
    "flag any INFERRED fact you rely on as inferred.\n"
    "- If the facts do not answer the question, set grounded=false and say so "
    "plainly ('The knowledge base does not cover this') — do NOT guess.\n"
    "- citations: the exact citation strings of the facts you used."
)


def _terms(question: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", (question or "").lower())
             if w not in _STOP and len(w) > 2]
    return " OR ".join(dict.fromkeys(words)) or (question or "").strip()


def answer(question: str, db_path=None, client_id: Optional[str] = None,
           limit: int = 20) -> dict:
    """Retrieve client-scoped facts via FTS, then compose a grounded, cited
    answer. Returns {answer, grounded, citations, facts_used}."""
    facts = index.search(db_path, _terms(question), client_id=client_id,
                         limit=limit)
    if not facts:
        return {"answer": "The knowledge base does not cover this.",
                "grounded": False, "citations": [], "facts_used": 0}
    block = "\n".join(
        f"- [{f['confidence']} · {f['citation']}] {f['fact_text']}"
        for f in facts)
    result = runner.claude_call(
        task_name="kb_answer", model_key=ANSWER_MODEL_KEY,
        system=SYSTEM, schema=SCHEMA,
        user_content=f"Question: {question}\n\nCited facts:\n{block}",
        purpose=f"librarian answer ({client_id or 'all'})",
        max_output_tokens=1500, db_path=db_path)
    out = result.parsed or {}
    return {"answer": out.get("answer", ""),
            "grounded": bool(out.get("grounded")),
            "citations": out.get("citations", []),
            "facts_used": len(facts)}
