"""Card identity that survives a re-keying — the forwarding layer between the
founder's DECISION and the card key it happened to be filed under.

Why this module exists (2026-08-14, the THIRD recurrence).
──────────────────────────────────────────────────────────────────────────────
`nudges.followup_key` derives a card's identity from attribution:

    followup:c:<client_id>   the thread is attributed to a registry client
    followup:p:<slug>        it is not — the counterparty's name
    followup:<thread_id>     no usable name either

Done/Dismiss/Snooze is then stored under that string, in `nudge_state` and in
`nudges.dedup_key`. Which means the identity of a HUMAN DECISION is derived from
DATA THAT KEEPS IMPROVING, and every improvement silently discards the decision:

    2026-08-12  room -> person    migrated once, by hand
    2026-08-13  person -> client  not migrated — Rocky Choy and Dhanya Sangolli
                                  were attributed (the work he asked for) and
                                  were back on Today the next morning, for the
                                  third time, after five Dones

A migration per re-keying is not a fix. It is a promise to do it again.

The fix: a decision is about a COUNTERPARTY, and the durable handle for a
counterparty in this system is the set of Upwork THREAD IDS the card stands for.
`threads.id` is a primary key: it does not change when a thread is attributed,
when two clients merge, when a client is renamed, or when a thread moves from
one client to another. So we record what a card key is ABOUT — at mint time
(`nudges.write_nudge`) and at decision time (`cockpit.actions`) — and every
reader looks the decision up THROUGH the subject.

That is deliberately the passive shape. The alternative designs both require
somebody to NOTICE a re-keying:

  * re-key the state inside the same transaction that writes `threads.client_id`
    — but registry merges and renames happen in `config/clients.yaml` via a git
    commit, nowhere near that transaction, and every future attribution writer
    has to remember. Missing one is silent.
  * write an alias row old -> new when a card is re-keyed — same problem one
    level down: something has to detect the re-key to write the alias, and the
    chains then need transitive resolution.

Recording the subject needs no such event: the decision itself carries what it
was about, so the lookup works under any key that comes later.

BACKFILL. Decisions taken before this table existed carry no subject. For those,
`cover()` falls back to DERIVING the thread set from the key: a thread knows
every key form it could ever have been filed under (`nudges.followup_key_forms`)
— its client form, its room-name form, its thread-id form. That recovers the
room->person and person->client re-keyings (which is what today's damage is),
and cannot recover a merge or a rename, because the old client_id is no longer
reachable from the thread. Which is exactly why the recorded subject is the
mechanism and the derivation is only the backfill.

NAMESPACE. Only follow-up keys forward. `review:thread:<id>` is also keyed on a
thread, and it is a verdict about IDENTITY that must never decay or leak into a
Today suppression — see cockpit.actions.review_thread. is_followup_key() is the
gate, and tests assert a review dismissal does not forward.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Set

from modules.chief_of_staff import nudges

SUBJECT_THREAD = "thread"
_NUDGE_PREFIX = "nudge:"
_FOLLOWUP_PREFIX = "followup:"


# ── key hygiene ──────────────────────────────────────────────────────────────

def base_key(card_key: Optional[str]) -> str:
    """The engine's dedup_key behind a cockpit item_key. A NUDGE card posts
    'nudge:<dedup_key>' and a FOLLOW-UP card posts the bare key; both name the
    same card, so subjects are always filed under the bare form."""
    k = card_key or ""
    return k[len(_NUDGE_PREFIX):] if k.startswith(_NUDGE_PREFIX) else k


def is_followup_key(card_key: Optional[str]) -> bool:
    return base_key(card_key).startswith(_FOLLOWUP_PREFIX)


def _table_exists(conn) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='card_subjects'"
    ).fetchone())


# ── write ────────────────────────────────────────────────────────────────────

def record(conn, card_key: Optional[str],
           thread_ids: Optional[Iterable[str]]) -> int:
    """Record that `card_key` stands for these threads. Idempotent, additive:
    subjects ACCUMULATE, they are never rewritten. A card that covered two rooms
    yesterday and one today has still been decided about both, and dropping the
    older one would lose exactly the decision this module exists to keep."""
    key = base_key(card_key)
    if not is_followup_key(key) or not thread_ids or not _table_exists(conn):
        return 0
    rows = [(key, SUBJECT_THREAD, str(t)) for t in thread_ids if t]
    if not rows:
        return 0
    conn.executemany(
        "INSERT OR IGNORE INTO card_subjects (card_key, subject_kind,"
        " subject_id) VALUES (?,?,?)", rows)
    return len(rows)


# ── read ─────────────────────────────────────────────────────────────────────

def capture(conn, card_key: Optional[str],
            thread_ids: Optional[Iterable[str]] = None) -> int:
    """Pin down what a card stands for AT THE MOMENT A DECISION IS TAKEN.

    Resolves the threads three ways and records the union: whatever the caller
    knows, whatever the card's payload says, and whatever the live attribution
    says is filed under this key right now. The founder pressed Done on the card
    he was looking at; this writes down which conversations that was, so the
    decision can still be found when attribution renames the card tomorrow."""
    key = base_key(card_key)
    if not is_followup_key(key):
        return 0
    ids = {str(t) for t in (thread_ids or []) if t}
    ids |= cover(conn, key)
    ids |= _payload_threads(conn, key)
    return record(conn, key, sorted(ids))


def _payload_threads(conn, card_key: str) -> Set[str]:
    """thread_ids the engine already stores on this card (rule_owed_replies puts
    them there). Reads every row for the key, not just the newest: a card that
    covered a second room a week ago was still decided about that room."""
    import json
    out: Set[str] = set()
    for r in conn.execute("SELECT payload_json FROM nudges WHERE dedup_key=?",
                          (base_key(card_key),)):
        try:
            pl = json.loads(r[0] or "{}")
        except (TypeError, ValueError):
            continue
        for t in (pl.get("thread_ids") or []):
            if t:
                out.add(str(t))
        if pl.get("thread_id"):
            out.add(str(pl["thread_id"]))
    return out


def recorded(conn, card_key: str) -> Set[str]:
    if not _table_exists(conn):
        return set()
    return {r[0] for r in conn.execute(
        "SELECT subject_id FROM card_subjects WHERE card_key=? AND"
        " subject_kind=?", (base_key(card_key), SUBJECT_THREAD))}


def _thread_forms(conn) -> List[dict]:
    """Every thread with the key forms it could ever have been filed under."""
    out = []
    for r in conn.execute("SELECT id, room_name, client_id FROM threads"):
        out.append({
            "id": r["id"], "client_id": r["client_id"],
            "room_name": r["room_name"],
            "current": nudges.followup_key(r["client_id"], r["room_name"],
                                           r["id"]),
            "forms": nudges.followup_key_forms(r["client_id"], r["room_name"],
                                               r["id"])})
    return out


def cover(conn, card_key: str, *, forms: Optional[List[dict]] = None) -> Set[str]:
    """The thread ids this card key is — or was ever — about.

    Recorded subjects UNION the threads whose own key forms include this key.
    The union matters: recorded subjects catch merges/renames (the old client_id
    is gone from the thread), derivation catches everything decided before the
    table existed."""
    key = base_key(card_key)
    if not is_followup_key(key):
        return set()
    ids = recorded(conn, key)
    for t in (forms if forms is not None else _thread_forms(conn)):
        if key in t["forms"]:
            ids.add(t["id"])
    return ids


def current_keys(conn, card_key: str, *,
                 forms: Optional[List[dict]] = None) -> Set[str]:
    """The key(s) this card's counterparty is filed under TODAY. Usually one; two
    when a person's rooms have since been split across two registry clients, in
    which case the decision belongs to both cards and each one's own evidence
    decides whether it still stands."""
    forms = forms if forms is not None else _thread_forms(conn)
    by_id = {t["id"]: t for t in forms}
    out = {by_id[t]["current"] for t in cover(conn, card_key, forms=forms)
           if t in by_id}
    out.discard(base_key(card_key))
    return out


def forward_map(conn, card_keys: Sequence[str]) -> Dict[str, List[str]]:
    """{decision key -> the key(s) that same counterparty is filed under now},
    for the keys whose identity has moved. Keys that still name a live card are
    absent — there is nothing to forward."""
    forms = _thread_forms(conn)
    out: Dict[str, List[str]] = {}
    for k in card_keys:
        if not is_followup_key(k):
            continue
        targets = current_keys(conn, k, forms=forms)
        if targets:
            out[base_key(k)] = sorted(targets)
    return out


def aliases(conn, card_key: str,
            thread_ids: Optional[Iterable[str]] = None) -> List[str]:
    """The OTHER card keys that stand for the same counterparty — i.e. the keys
    this card's decisions may also be filed under. Used by the producer
    (`nudges.write_nudge`) so a re-keyed card does not re-mint over a Done."""
    key = base_key(card_key)
    if not is_followup_key(key):
        return []
    forms = _thread_forms(conn)
    ids = set(str(t) for t in (thread_ids or []) if t)
    ids |= cover(conn, key, forms=forms)
    if not ids:
        return []
    found: Set[str] = set()
    if _table_exists(conn):
        marks = ",".join("?" * len(ids))
        found |= {r[0] for r in conn.execute(
            f"SELECT DISTINCT card_key FROM card_subjects WHERE"
            f" subject_kind=? AND subject_id IN ({marks})",
            (SUBJECT_THREAD, *sorted(ids)))}
    for t in forms:
        if t["id"] in ids:
            found |= set(t["forms"])
    found.discard(key)
    return sorted(k for k in found if is_followup_key(k))


# ── backfill (decisions taken before card_subjects existed) ──────────────────

def backfill(conn, card_keys: Sequence[str]) -> Dict[str, List[str]]:
    """Persist the DERIVED subjects for historical keys so they stop depending on
    derivation. Returns what was written. Deriving is lossy by construction (a
    merged-away client_id is unreachable), so this is a one-way improvement: what
    can be recovered is written down, what cannot is left alone rather than
    guessed at."""
    forms = _thread_forms(conn)
    wrote: Dict[str, List[str]] = {}
    for k in card_keys:
        key = base_key(k)
        if not is_followup_key(key):
            continue
        ids = sorted(cover(conn, key, forms=forms) - recorded(conn, key))
        if ids and record(conn, key, ids):
            wrote[key] = ids
    return wrote
