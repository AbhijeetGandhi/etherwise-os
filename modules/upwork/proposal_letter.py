"""What a sent cover letter looks like, as plain facts (Upwork brief Phase 2,
Loop 1: "join every proposal's text + structure to its outcome chain").

Pure functions of the letter text: no network, no DB, no model call. They are
the SAME rules the 8 Oct 2026 research coded all 595 proposals with
(var/prop/features.py), so a letter tagged here and a letter tagged by that
research are tagged alike; tests/test_proposal_funnel.py holds the two to each
other on the backfilled rows.

    letter_features(text) -> {words, contractions, em_dashes, has_loom,
                              questions, opener_family, first_lines}

`first_lines` is the opening as the client's proposal list shows it: the
greeting and any code word the client asked for ("PURPLE") stripped, then the
first two sentences, capped. That is the only part of the letter a client sees
before deciding whether to open the proposal.
"""
from __future__ import annotations

import re
from typing import Optional

CONTRACTION = re.compile(
    r"\b\w+(?:n['’]t|['’]ve|['’]m|['’]re|['’]ll|['’]d)\b"
    r"|\b(?:it|that|there|here|what|let|he|she|who)['’]s\b", re.I)
WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
LOOM = re.compile(r"loom\.com|\bloom\b", re.I)

# What the first lines DO. First match wins, on the first ~240 characters after
# the greeting and any code word. Verbatim from the research coding.
OPEN_RULES = [
    ("invite thanks", r"thank(?:s| you)[^.]{0,50}\b(?:invit|reaching out|offer|referral|opportunity to apply)"),
    ("loom first", r"\brecorded (?:a|an|this)\b[^.]{0,40}\bloom\b|^[^.]{0,80}\bloom\b"),
    ("self intro", r"^i(?:['’]m| am) abhijeet|^my name is|^this is abhijeet"),
    ("their problem / insight first",
     r"^(?:the )?(?:real|hard|hardest|tricky|trickiest|core|biggest|interesting|key|main|most important)\b[^.]{0,40}\b(?:part|challenge|risk|lever|piece|thing|decision|issue|problem|layer)\b"
     r"|\b(?:isn['’]?t|is not|is rarely|usually is not)\b[^.]{0,90}\bit['’]?s\b|^the (?:part|line|fact|pattern|shape|framing|distinction|relational|stack)\b"
     r"|^one thing worth|^your system does not|^replacing |^managing |^maintaining |^connecting |^setting up |^cleaning up |^automating |^building |^streamlining "
     r"|\btend to hit\b|\bhave specific\b|^zero automation|^i would not touch|^i don['’]t see|usual culprits|^your (?:workflow|operations|team|setup|stack)\b"),
    ("fit claim / excitement",
     r"\bexactly\b|\bstrong (?:match|fit)\b|\bgreat fit\b|\bdirect match\b|\bwheelhouse\b|caught my (?:eye|attention)|\bexcit|i(?:['’]d| would) love"
     r"|saw your (?:job )?post|came across your|read (?:through )?your post|your (?:job )?post\b|\bthis (?:role|one|project|looks|sounds|is a)\b|very (?:close|aligned|familiar)|closely aligned|identical to"),
    ("self / credentials first",
     r"^(?:i|we|my)\b|^i['’]|^over the (?:last|past)|^most of my|^for [A-Z][\w.]+,? i\b|^for (?:a|an|one|my) [^.]{3,90}\bi (?:have|built|designed)\b|^\d"
     r"|^one automation i am|^with (?:over|more than)"),
]
OPENER_FAMILIES = tuple(name for name, _ in OPEN_RULES) + ("other",)

# a leading code word the client asked for ("PURPLE Hi, ...", "🟣 Hi, ...")
_CODE_WORD = re.compile(r"^(?:[A-Z][A-Za-z ]{0,24}|[^\w\s]{1,3})[.!]?\s+(?=(?:[Hh]i|[Hh]ey|[Hh]ello)\b)")
_GREETING = re.compile(r"^(?:hi|hey|hello|dear)\b(?:\s+(?:there|team|all|[A-Z][a-z]+)(?=\s*[,.!\]]))?\s*[,.!\]]?\s*",
                       re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")

FIRST_LINES_MAX = 240


def _after_greeting(text: Optional[str]) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    t = _CODE_WORD.sub("", t)
    return _GREETING.sub("", t)


def opener_family(text: Optional[str]) -> Optional[str]:
    """Which kind of opening the letter leads with (see OPEN_RULES)."""
    if not (text or "").strip():
        return None
    head = _after_greeting(text)[:240]
    for name, pat in OPEN_RULES:
        if re.search(pat, head, re.I):
            return name
    return "other"


def first_lines(text: Optional[str], limit: int = FIRST_LINES_MAX) -> Optional[str]:
    """The first two sentences after the greeting, as the list card shows them."""
    body = _after_greeting(text)
    if not body:
        return None
    parts = [p.strip() for p in _SENTENCE.split(body) if p.strip()]
    out = " ".join(parts[:2]) if parts else body
    if len(out) > limit:
        cut = out[:limit].rsplit(" ", 1)[0].rstrip(",;:")
        out = cut + "…"
    return out


def letter_features(text: Optional[str]) -> dict:
    """Every letter fact the funnel splits on. An empty letter is all None
    (not measured), never zeros."""
    body = (text or "").strip()
    if not body:
        return {"words": None, "contractions": None, "em_dashes": None, "has_loom": None,
                "questions": None, "opener_family": None, "first_lines": None}
    return {
        "words": len(WORD.findall(body)),
        "contractions": len(CONTRACTION.findall(body)),
        "em_dashes": body.count("—") + body.count(" – "),
        "has_loom": 1 if LOOM.search(body) else 0,
        "questions": body.count("?"),
        "opener_family": opener_family(body),
        "first_lines": first_lines(body),
    }
