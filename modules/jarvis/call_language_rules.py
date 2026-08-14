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
    2. else Hindi at/over cutoff
       AND one of US spoke
       AND nobody else did       -> 'internal-team'. The strong form (below).
    3. else Hindi at/over cutoff -> internal. Strips an existing client_id.
                                    The weak form: language alone.
    4. else                      -> untouched; the normal classifier decides.

RULE 2, AND WHY IT IS A RULE AND NOT A ROW (2026-08-14)
------------------------------------------------------
    "all devnagri call with jay are internal. tanmay is new emplyee so its
     internal as well."   — Abhijeet

Resolving those calls one at a time would answer today's queue and nothing else;
the next Hindi standup with a teammate arrives as a fresh unknown. So it is
encoded here, as its own named verdict with its own evidence string, and it
holds for every internal person on the roster — not just Jay, and not just the
rows that happened to be queued when he said it.

The strong form is deliberately NARROWER than rule 3, and that is the safety
property. It requires an Etherwise person (`config.is_internal_person`, the one
roster) to have SPOKEN and requires that NOBODY ELSE did. So it cannot fire on
`fathom/117473152` — Zeeshan Rasheed's own client call, held in Urdu, which sat
on `fathom-kind:internal-language` asserting the opposite of the truth. That
call has an outside speaker, so it falls through to the WEAK rule, where the
existing speaker guard at the call site still gets its say. Two independent
protections, and the stronger verdict never even applies when an outsider is in
the room. A label we cannot identify (a device name, an initial) counts as
external on purpose: withholding the strong verdict costs nothing, since rule 3
reaches the same `internal` kind by the guarded path.

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

WHAT THIS MODULE CANNOT SEE, AND WHO COMPENSATES (2026-08-14)
------------------------------------------------------------
`classify()` reads the SCRIPT and Yogita, and nothing else — it has no notion of
who else was in the room. So rule 2 is capable of calling an EXTERNAL client call
internal purely because it was not held in English, and it did:
`fathom/117473152` is Zeeshan Rasheed's own call, conducted in Urdu (Fathom
transcribes the Hindi-adjacent stretches in Devanagari, 16.3%), and it sat on
`fathom-kind:internal-language` — a bucket asserting the opposite of the truth.

Measured over the live corpus (var/agent-scratch/four/07-langaudit): the Hindi
rule fires on 88 Fathom calls, exactly 3 of which have any external speaker, and
0 of those 3 resolved to a registered client at the time. So the exposure is real
but small, and it grows by exactly one row each time a speaker on such a call is
registered.

The guard therefore does NOT live here — this file stays a faithful statement of
the rule Abhijeet gave. It lives at the one call site that acts on the verdict,
`loom_classifier.reclassify_queued` pass 0a, where the deterministic speaker
signals are already loaded: a registered client audibly on the call falsifies
"internal" and the exclusion is withheld. That is the same `speaker_guard` law
every other exclusion in this system is already subject to.

Rule 2 (`internal-team`) adds a SECOND, independent protection in front of that
one — it declines to fire at all when anyone outside the roster spoke — but it
does not replace it. Both stay.
"""
from __future__ import annotations

import json
import re

from core import config

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


def split_speakers(payload: dict) -> tuple:
    """(ours, theirs) — the speakers on this call, split by the ONE roster.

    `config.is_internal_person` is the only place that answers "is this person
    us?"; asking it here rather than matching a name locally is what keeps a new
    hire from having to be added twice. Anything it does not recognise is
    counted as theirs, INCLUDING a junk device label — see the module header:
    an unidentifiable speaker withholds the strong verdict, which is free.
    """
    ours, theirs = [], []
    for name in sorted(_speakers(payload)):
        (ours if config.is_internal_person(name) else theirs).append(name)
    return ours, theirs


def _title(name: str) -> str:
    return " ".join(w.capitalize() for w in (name or "").split())


def classify(payload: dict, *, cutoff: float = HINDI_CUTOFF) -> dict:
    """Apply the precedence order. Returns the verdict plus the evidence that
    produced it, so every reattribution can be explained and reversed.

    Keys: rule · client_id · kind · internal_language · hindi_ratio · evidence,
    plus `internal_speakers` / `external_speakers` — the split that rule 2 was
    decided on, carried out so a verdict can be audited without re-reading the
    transcript (`events.ignore`'s whole argument for storing evidence)."""
    text = transcript_text(payload)
    ratio = hindi_ratio(text)
    hindi = ratio >= cutoff
    yogita = has_yogita(payload)
    ours, theirs = split_speakers(payload)
    base = {"hindi_ratio": round(ratio, 4), "internal_speakers": ours,
            "external_speakers": theirs}

    if yogita:
        return {**base, "rule": "yogita", "client_id": YOGITA_CLIENT,
                "kind": "client-work", "internal_language": hindi,
                "evidence": "Yogita spoke on the call"
                            + (f"; {ratio:.1%} Devanagari (internal team call)"
                               if hindi else "")}
    if hindi and ours and not theirs:
        # THE STRONG FORM. Not "it was in Hindi" but "it was in Hindi and the
        # only people in the room were ours" — which is a fact about the call,
        # not an inference from its language, and is therefore durable.
        return {**base, "rule": "internal-team", "client_id": None,
                "kind": "internal", "internal_language": True,
                "evidence": f"{ratio:.1%} of the transcript is Devanagari and"
                            f" the only speakers were "
                            + ", ".join(_title(n) for n in ours)
                            + " (Etherwise) — no external speaker on the call"}
    if hindi:
        return {**base, "rule": "hindi", "client_id": None, "kind": "internal",
                "internal_language": True,
                "evidence": f"{ratio:.1%} of the transcript is Devanagari"}
    return {**base, "rule": None, "client_id": None, "kind": None,
            "internal_language": False, "evidence": None}
