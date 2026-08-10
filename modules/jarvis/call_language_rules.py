"""Abhijeet's two hand-given call-classification rules (2026-08-10).

    "all the calls which included hindi language you need to mark them as
     internal. if yogita was part of the call then it was 99% of the time
     for hermon."

Taken literally these two rules disagree on 65 calls that are BOTH. Abhijeet
resolved it: those go to Hermon (the delivery knowledge is worth keeping), while
Hindi otherwise means internal — including overriding an existing client
attribution. So the rules are a PRECEDENCE ORDER, not a set:

    1. Yogita present            -> hermon, client-work. Wins even if Hindi.
                                    Tagged internal_language when Hindi so the
                                    facts are never quoted as client-facing.
    2. else Hindi at/over cutoff -> internal. Strips an existing client_id.
    3. else                      -> untouched; the normal classifier decides.

Why Devanagari and not the `language` field: Fathom's own field says "hi" for
only 21 of 603 calls, while 165 transcripts actually contain Devanagari. The
field is unreliable; the script is ground truth. Fathom transcribes Hindi speech
in Devanagari, so romanised Hinglish typed in Latin script is not a concern here.

Why a RATIO and not presence: one Hindi word in an otherwise English client call
is not "a Hindi call". 20 calls sit under 1% (a stray word); 90 sit above 10%
(plainly conducted in Hindi). The cutoff is a parameter so it can be tuned
against the counts rather than argued about.

Yogita is detected in the transcript body, not the participants list — she
appears in ZERO structured participant lists but is named in 139 transcripts
(she joins via someone else's device or is only ever addressed by name).
"""
from __future__ import annotations

import json
import re

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
WORDCHAR = re.compile(r"\w")
YOGITA = re.compile(r"yogita", re.I)

# Fraction of word characters in Devanagari at or above which a call counts as
# "included Hindi". 0.01 matches Abhijeet's wording ("included"), while still
# excluding the 20 calls that carry a single stray word.
HINDI_CUTOFF = 0.01

YOGITA_CLIENT = "hermon"          # "99% of the time for hermon"


def transcript_text(payload: dict) -> str:
    tx = payload.get("transcript") or ""
    if isinstance(tx, list):
        tx = " ".join(str(t) for t in tx)
    return tx


def hindi_ratio(text: str) -> float:
    total = len(WORDCHAR.findall(text or ""))
    if not total:
        return 0.0
    return len(DEVANAGARI.findall(text)) / total


def _speakers(payload: dict) -> set:
    """Every name that actually SPOKE. Fathom stores `speaker` either as a plain
    string or as {display_name: ...}; both shapes appear in this corpus."""
    names = set()
    tx = payload.get("transcript")
    if isinstance(tx, list):
        for turn in tx:
            if not isinstance(turn, dict):
                continue
            sp = turn.get("speaker")
            if isinstance(sp, dict):
                sp = sp.get("display_name")
            if sp:
                names.add(str(sp).strip().lower())
    else:
        # serialized form: pull the speaker labels back out
        for m in re.finditer(r"['\"]speaker['\"]\s*:\s*(?:\{[^}]*?['\"]display_name['\"]"
                             r"\s*:\s*)?['\"]([^'\"]{1,60})['\"]", str(tx or "")):
            names.add(m.group(1).strip().lower())
    return names


def has_yogita(payload: dict, text: str = "") -> bool:
    """True only when Yogita SPOKE on the call — not when she was mentioned.

    This distinction is the whole rule. Measured on the live corpus: 99 calls
    have her as a speaker (15+ name occurrences, she is talking), while 14 calls
    name her exactly once in someone else's meeting — "I'll check what tickets
    Yogita is working on", "I will have Yogita work on it". Matching on mention
    swept those 14 into Hermon and would have stripped their real client.
    """
    if any("yogita" in n for n in _speakers(payload)):
        return True
    # a structured invitee also counts; she appears in zero of them today, but
    # that is a fact about Fathom's capture, not a guarantee
    return bool(YOGITA.search(json.dumps(payload.get("participants") or [])))


def classify(payload: dict, *, cutoff: float = HINDI_CUTOFF) -> dict:
    """Apply the precedence order. Returns the verdict plus the evidence that
    produced it, so every reattribution can be explained and reversed."""
    text = transcript_text(payload)
    ratio = hindi_ratio(text)
    hindi = ratio >= cutoff
    yogita = has_yogita(payload)

    if yogita:
        return {"rule": "yogita", "client_id": YOGITA_CLIENT,
                "kind": "client-work", "internal_language": hindi,
                "hindi_ratio": round(ratio, 4),
                "evidence": "Yogita spoke on the call"
                            + (f"; {ratio:.1%} Devanagari (internal team call)"
                               if hindi else "")}
    if hindi:
        return {"rule": "hindi", "client_id": None, "kind": "internal",
                "internal_language": True, "hindi_ratio": round(ratio, 4),
                "evidence": f"{ratio:.1%} of the transcript is Devanagari"}
    return {"rule": None, "client_id": None, "kind": None,
            "internal_language": False, "hindi_ratio": round(ratio, 4),
            "evidence": None}
