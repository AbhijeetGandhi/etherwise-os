"""Fathom -> registry mapping bridge + dossier heal (J1 D3 §4.6).

M3 mapped Fathom recordings against a 4-client PILOT list; the registry knows all
67. This bridge re-maps UNMAPPED Fathom sources through the registry: a match now
heals the source (reattributes it + its facts + recompiles the dossier); a real
external participant with no match becomes a QUEUED event so Abhijeet assigns it
once and every future call from that participant auto-maps.

Bounded on purpose (there are hundreds of old unattributed calls): the runner
processes newest-first up to a cap, and never queues a call with no external
participant signal (those would flood the queue, not inform it).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from core import config, db, registry
from modules.jarvis import events
from modules.knowledge import dossier, ingest

_FREEMAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com",
             "yahoo.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
             "aol.com", "live.com", "msn.com"}
# NB: there is deliberately no local `_INTERNAL = ("etherwise.io",)` here any
# more. "Are these people us?" is answered in exactly one place —
# config.is_internal_person / config.INTERNAL_PEOPLE / config.OWNER_IDENTITIES.

# ── "is this speaker label even a person?" ───────────────────────────────────
# Video tools put WHATEVER the participant typed into the join box on the
# transcript line, and a meaningful share of it is not a name: a device
# ("iPhone", "Galaxy S21"), an initialism ("AP"), a single letter ("j"), a desk
# ("Admin CS", "Conference Room 2"). Measured on the live queue 2026-08-14: 5 of
# the 62 queued Fathom calls had one of these as their ONLY external signal.
#
# Two things go wrong if they are treated as people, and both are permanent:
#   * `_external_signal` makes the junk string the event's AUTHOR, and the
#     cockpit's assign path then offers to write it into clients.yaml as an
#     ALIAS. "iPhone" as an alias of a client would match every later call where
#     anybody dials in from a phone.
#   * a queue row whose entire question is "who is 'j'?" cannot be answered by
#     the human either, so it sits there forever making the badge bigger.
# Structural only — never a denylist of specific strings, which would not
# generalise past the five we happen to have seen.
_JUNK_LABEL = re.compile(
    r"^(?:i-?(?:phone|pad|mac)|mac(?:book)?|android|galaxy|pixel|sm-\w+|"
    r"redmi|oneplus|huawei|windows|linux|desktop|laptop|tablet|phone|"
    r"user|guest|admin|owner|host|moderator|participant|attendee|caller|"
    r"call-?in\s*user|unknown(?:\s+speaker)?|speaker\s*\d*|voice\s*\d*|"
    r"meeting\s*room|conference\s*room|room\s*\d+|boardroom|"
    r"anonymous|test|recorder|notetaker|bot)\b", re.I)
_JUNK_MIN_LEN = 3            # "j", "AP" — an initialism is not an identity
FOLD_MIN_LEN = 6             # matches cockpit's _REVIEW_HINT_MIN_LEN, on purpose

# `sources.content_type` values that state something PERMANENT about the row's
# content and must survive a re-attribution — see heal_source.
TERMINAL_CONTENT_TYPES = ("loom:no-transcript",)


def alnum_key(s: Optional[str]) -> str:
    """registry.norm() with punctuation and spacing thrown away too — the WEAK
    key. Deliberately weaker than anything the attribution rails may heal on;
    see `loom_classifier.folded_identity_match` for the one place it is used."""
    return re.sub(r"[^a-z0-9]", "", registry.norm(s or ""))


def is_junk_label(name: Optional[str]) -> bool:
    """True when a transcript speaker label is a device/role/initial, not a human
    identity. See the _JUNK_LABEL note above for why this is not a denylist."""
    a = (name or "").strip()
    if len(a) < _JUNK_MIN_LEN:
        return True
    if not re.search(r"[aeiouy]", a, re.I):     # 'AP', 'JK', 'MRB' — initials
        return True
    if a.isdigit() or _JUNK_LABEL.match(a):
        return True
    # "Abhijeet's iPhone" / "Jay's iPad" — a device, whoever owns it
    return bool(re.search(r"['’]s\s+(?:i-?(?:phone|pad|mac)|mac|android|"
                          r"galaxy|pixel|laptop|desktop|phone)\b", a, re.I))


def _rid(source_ref: str) -> str:
    return source_ref.split("/", 1)[1] if "/" in source_ref else source_ref


def _read_participants(raw_path: str) -> tuple[list[str], Optional[str]]:
    """(participants, url) from the raw Fathom payload; ([], None) if unreadable."""
    try:
        payload = json.loads(Path(raw_path).read_text())
    except (OSError, ValueError):
        return [], None
    parsed = ingest.parse_fathom(payload)
    return parsed.get("participants") or [], parsed.get("url")


def _match(reg, participants: list[str], title: str) -> Optional[str]:
    for p in participants:
        cid = reg.match_email(p) if "@" in p else reg.match_name(p)
        if cid:
            return cid
    # last resort: a fathom_name / alias token appears in title or a participant
    hay = (title or "").lower() + " " + " ".join(participants).lower()
    for cid in reg.matchable_ids():
        c = reg.by_id[cid]
        for tok in (c.get("fathom_names") or []):
            if tok and tok.lower() in hay:
                return cid
    return None


def _external_signal(participants: list[str]) -> Optional[str]:
    """The first external client participant — skips our own people (a call whose
    only other party is one of US is internal, not a client signal), else None.

    Addresses first: an email is a strictly better queue author than a bare name
    because it carries a domain the registry can learn from. A NAME is only
    returned when no external address exists at all — before 2026-08-14 this
    function looked at emails ONLY, so a call whose external party appeared purely
    as a transcript speaker produced no signal and was silently skipped as
    internal. `config.is_internal_person` answers both halves from ONE roster
    (see the INTERNAL_PEOPLE note in core/config.py).

    A junk device/role label ("iPhone", "AP", "j") is ranked LAST rather than
    dropped: it is still true that somebody external was on the call, so the call
    is not internal — but the label must not become the author of a queue row,
    because the assign path offers the author to clients.yaml as an alias."""
    names, weak = [], []
    for p in participants:
        if config.is_internal_person(p):
            continue
        if "@" in p:
            return p
        p = p.strip()
        if not p:
            continue
        (weak if is_junk_label(p) else names).append(p)
    return (names or weak or [None])[0]


def heal_source(db_path, source_ref: str, client_id: str, client_name: str,
                *, now_iso: Optional[str] = None,
                dossier_dir: Optional[Path] = None) -> dict:
    """Reattribute an unmapped Fathom source + its facts to a client, then
    recompile that client's dossier. Idempotent; only touches unmapped rows.

    `content_type` is set to 'client-call' EXCEPT where it already carries a
    TERMINAL marker. `loom:no-transcript` is the one that exists: the fetcher
    writes it on evidence that Loom will never produce a transcript for that
    recording, and it is the only record anywhere that the row is empty by
    nature rather than unprocessed. A Loom whose TITLE names a client (the
    deterministic pass reaches those without reading a word) is genuinely that
    client's -- both facts are true, and overwriting the marker to record the
    attribution would trade a true fact for a true fact."""
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    with db.connect(db_path) as conn:
        rows = conn.execute("SELECT id FROM sources WHERE source_ref=?",
                            (source_ref,)).fetchall()
        sids = [r["id"] for r in rows]
        facts = 0
        if sids:
            qs = ",".join("?" * len(sids))
            marks = ",".join("?" * len(TERMINAL_CONTENT_TYPES))
            conn.execute(
                f"UPDATE sources SET client_id=?, client_name=?,"
                f" content_type=CASE WHEN content_type IN ({marks})"
                f" THEN content_type ELSE 'client-call' END"
                f" WHERE id IN ({qs})",
                (client_id, client_name, *TERMINAL_CONTENT_TYPES, *sids))
            cur = conn.execute(
                f"UPDATE facts SET client_id=?, scope='client'"
                f" WHERE source_id IN ({qs})", (client_id, *sids))
            facts = cur.rowcount
    comp = dossier.compile_dossier(db_path, client_id, client_name, now_iso,
                                   dossier_dir=dossier_dir)
    return {"sources": len(sids), "facts_reattributed": facts,
            "dossier": comp.get("status")}


def sync_unmapped(db_path, reg=None, *, limit: int = 50, queue_cap: int = 40,
                  run_id: Optional[int] = None,
                  read_participants: Callable = _read_participants,
                  now_iso: Optional[str] = None,
                  dossier_dir: Optional[Path] = None) -> dict:
    """Re-map unmapped Fathom sources through the registry. Auto-heals matches;
    queues external-but-unmatched calls; skips signal-less internal calls."""
    reg = reg or registry.load()
    with db.connect(db_path) as conn:
        unmapped = conn.execute(
            "SELECT source_ref, title, client_name, raw_path FROM sources"
            " WHERE source_type='fathom' AND client_id IS NULL"
            " ORDER BY occurred_dt DESC LIMIT ?", (limit,)).fetchall()

    healed = queued = skipped = 0
    for s in unmapped:
        parts, url = read_participants(s["raw_path"])
        cid = _match(reg, parts, s["title"])
        if cid:
            heal_source(db_path, s["source_ref"], cid, reg.by_id[cid]["name"],
                        now_iso=now_iso, dossier_dir=dossier_dir)
            healed += 1
            continue
        signal = _external_signal(parts)
        if not signal or queued >= queue_cap:
            skipped += 1
            continue
        rid = _rid(s["source_ref"])
        ev = {"id": events.canonical_id("fathom", rid), "source": "fathom",
              "native_id": rid, "ts": None, "author": signal,
              "subject": s["title"], "state": "queued", "text":
              "Participants: " + ", ".join(parts), "permalink": url,
              "raw_ref": s["source_ref"], "client_id": None}
        with db.connect(db_path) as conn:
            if events.store_event(conn, ev, run_id) == "inserted":
                queued += 1
            else:
                skipped += 1
    return {"scanned": len(unmapped), "auto_healed": healed,
            "queued": queued, "skipped": skipped}


RECHECK_STATES = ("queued",)

# EXCLUSIONS THIS MODULE MUST NOT RE-OPEN.
#
# The docstring below argues that a verdict is only as good as the evidence it
# was reached on, and uses that to justify re-reading `excluded` Fathom rows.
# That argument is right, and it CUTS BOTH WAYS: the 114
# `fathom-kind:internal-product` verdicts reached in July were damage, because
# the model was handed a participant list of one internal person (the `or`/`|`
# bug in ingest.parse_fathom). The verdicts `jarvis-loom-classify` reaches NOW
# are reached on the full transcript with real speakers — which is strictly
# more evidence than this module has, since all it reads is the participant
# list. Re-opening those is not repair; it is a worse-informed rule overruling
# a better-informed one.
#
# It cost a full loop: 2026-08-21, jarvis_loom_classify excluded 29 calls at
# 05:30 and jarvis_fathom_bridge requeued all 29 at 05:45, on a nightly
# schedule, so the Review board refilled itself every morning and every fix
# measured clean for fifteen minutes. The founder had been chasing the same
# forty-odd rows for days.
#
# `--force` still overrides, for the case this list exists to protect against:
# a classifier verdict that is itself wrong.
# Keyed on WHO decided (`resolved_by`), never on the exclude_reason. The July
# verdicts wear the SAME `fathom-kind:*` reasons and must stay re-openable —
# they were reached on a participant list of one internal person and carry no
# stamp. Reason says what was decided; only the stamp says on what evidence.
# A SUBSTRING, because the stamp has three live forms: 'fathom_classifier',
# 'fathom_classifier:high' and 'loom_classifier:high'. Matching a prefix list
# missed the bare one and the loop came straight back.
TRANSCRIPT_DECIDER_MARK = "_classifier"


def _classifier_decided(resolved_by: Optional[str]) -> bool:
    return TRANSCRIPT_DECIDER_MARK in (resolved_by or "").strip().lower()


def recheck_events(db_path, reg=None, *, states: tuple = RECHECK_STATES,
                   read_participants: Callable = _read_participants,
                   dossier_dir: Optional[Path] = None,
                   queue_cap: Optional[int] = None,
                   dry_run: bool = False,
                   force: bool = False,
                   now_iso: Optional[str] = None) -> dict:
    """Re-evaluate decided Fathom events against the CURRENT registry + owner
    rules. A registry match heals; a real external party (behind an owner) becomes
    queued with the corrected author; a call whose only other party is one of us is
    excluded as internal.

    `states` exists because a verdict is only as good as the evidence it was
    reached on, and 189 of the 195 unattributed Fathom sources carried an
    `excluded` event — 114 of them `fathom-kind:internal-product`, decided by a
    model that was handed a participant list of ONE INTERNAL PERSON because of the
    `or`/`|` bug in ingest.parse_fathom. Those verdicts are not decisions to
    respect, they are damage; re-running them against real speakers is the repair.
    Default stays ('queued',) so the daily job's behaviour is unchanged — the
    unfreeze is an explicit, auditable `--recheck-all`.

    `queue_cap` bounds how many rows may be MOVED INTO the queue in one pass (a
    mapping queue of 150 is not a queue, it is a wall — measured on the IMAP rail,
    which hit 94 and became unusable). Rows over the cap keep their current state
    and are counted in `queue_capped`, so nothing is silently dropped. Healing is
    never capped: it is deterministic and free.

    A verdict ABHIJEET reached himself is NEVER re-examined here
    (`events.is_human_decision`). The argument above -- "a verdict is only as
    good as the evidence it was reached on" -- is an argument about RULES
    re-reading their own output. It does not extend to his: he read the call.
    Skipped rows are counted in `founder_decided`, never silently dropped.

    NOTHING here is auto-attributed by a model. Every heal is a registry
    name/email/domain match; everything ambiguous goes to a human.
    """
    reg = reg or registry.load()
    qs = ",".join("?" * len(states))
    with db.connect(db_path) as conn:
        all_rows = [dict(r) for r in conn.execute(
            "SELECT e.id, e.raw_ref, e.state, e.exclude_reason, e.resolved_by,"
            " s.raw_path, s.title, s.client_id FROM events e"
            " LEFT JOIN sources s ON s.source_ref = e.raw_ref"
            f" WHERE e.source='fathom' AND e.state IN ({qs})"
            " ORDER BY s.occurred_dt DESC", tuple(states))]
    rows = [r for r in all_rows if not events.is_human_decision(r["resolved_by"])]
    founder_decided = len(all_rows) - len(rows)
    # …and the transcript classifier's verdicts are respected the same way, for
    # the same reason: it read more than we can. See CLASSIFIER_REASONS.
    if not force:
        before = len(rows)
        rows = [r for r in rows if not _classifier_decided(r["resolved_by"])]
        classifier_decided = before - len(rows)
    else:
        classifier_decided = 0
    healed = requeued = excluded = capped = already = 0
    by_client: dict = {}
    from_reason: dict = {}
    for e in rows:
        if e["client_id"]:            # source already attributed -> nothing owed
            already += 1
            continue
        parts, _ = read_participants(e["raw_path"] or "")
        cid = _match(reg, parts, e["title"] or "")
        if cid:
            if not dry_run:
                heal_source(db_path, e["raw_ref"], cid, reg.by_id[cid]["name"],
                            now_iso=now_iso, dossier_dir=dossier_dir)
                with db.connect(db_path) as conn:
                    events.set_mapped(conn, e["id"], cid)
            healed += 1
            by_client[cid] = by_client.get(cid, 0) + 1
            r = e["exclude_reason"] or e["state"]
            from_reason[r] = from_reason.get(r, 0) + 1
            continue
        signal = _external_signal(parts)
        if signal:
            if queue_cap is not None and requeued >= queue_cap:
                capped += 1
                continue
            if not dry_run:
                with db.connect(db_path) as conn:
                    conn.execute(
                        "UPDATE events SET state='queued', author=?,"
                        " exclude_reason=NULL, updated_at=datetime('now')"
                        " WHERE id=?", (signal, e["id"]))
            requeued += 1
        else:
            if not dry_run:
                with db.connect(db_path) as conn:
                    events.set_excluded(conn, e["id"], "internal:owner-only")
            excluded += 1
    return {"rechecked": len(rows), "states": list(states), "healed": healed,
            "requeued": requeued, "excluded": excluded,
            "queue_capped": capped, "already_attributed": already,
            "founder_decided": founder_decided,
            "classifier_decided": classifier_decided,
            "dry_run": dry_run, "healed_by_client": by_client,
            "healed_from": from_reason}


def recheck_queued(db_path, reg=None, **kw) -> dict:
    """The daily recheck: queued Fathom events only (unchanged behaviour)."""
    return recheck_events(db_path, reg, states=("queued",), **kw)
