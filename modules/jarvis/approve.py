"""Mapping-queue approval flow (J1 D3 §4.6) — the ONLY write path to the registry.

Abhijeet's approval in the cockpit is what edits clients.yaml, via an audited git
commit (author 'cockpit-approval', message carries the event evidence). The agent
has no other write path (architecture §4.1, immutable-policy pattern). On assign
the flow LEARNS the identifying pattern (sender domain / participant) so the same
sender auto-maps next sweep, maps the event, and — for a recording (Fathom call
or Loom walkthrough) — heals the dossier (source + facts reattributed, recompiled).

THREE WRITE PATHS LIVE HERE, and they are deliberately different sizes:

  write_registry()  append one value to one field of an EXISTING client.
  assign_thread()   an Upwork THREAD -> an existing client. Upwork threads never
                    enter the `events` ledger (the sweep collects mail, Slack,
                    Fathom and Loom; rooms come in through the Upwork rail), so
                    resolve() cannot see them at all. The claim it writes is
                    `upwork_rooms += <room id>`, which is the field the registry
                    docs already describe as the deterministic, hand-approved
                    linkage and which knowledge.clients.link_threads consults
                    FIRST, before any name matching. It claims THAT ROOM, never
                    the counterparty's name — an alias claim from a one-word
                    Upwork display name is how "rick k" once claimed "Patrick
                    Kelly".
  create_client()   a whole NEW registry entry. The largest possible write, so it
                    is the narrowest possible action: one thread, an explicit
                    slug the human types, hard refusal on any id/name/alias/room
                    collision, and a post-write RELOAD that rolls the file back
                    if the result would not validate. resolve() refuses an
                    unknown client_id on purpose and still does; creating a
                    client is never a side effect of assigning something.
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core import config, db, registry
from modules.jarvis import events, fathom_bridge

# Canonical exclude_reason for "this was an internal Etherwise call/thread".
# Matches the existing `internal:owner-only` convention so the vocabulary stays
# one family, and the Hindi-call rule (`kind='internal'`) lands in the same
# bucket rather than inventing a parallel one.
INTERNAL_REASON = "internal:team-call"

_FREEMAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com",
             "yahoo.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
             "aol.com", "live.com", "msn.com"}


def learned_pattern(author: str) -> Optional[tuple[str, str]]:
    """What to teach the registry from an event's author.
    A company domain -> emails (covers the whole company); a freemail address ->
    that exact address (never claim all of gmail.com); a bare name -> aliases."""
    a = (author or "").strip()
    if not a:
        return None
    if "@" in a:
        dom = a.rsplit("@", 1)[-1].lower()
        # A shared platform domain is nobody's to claim (registry refuses it at
        # load and warns). Three Assign clicks on "X sent you a message" rows
        # had written `email.upwork.com` under three different clients by
        # 2026-11-02 — three ignored lines and three warnings. Learn the full
        # address on such a domain, as for freemail: it names one room.
        if dom in _FREEMAIL or dom in registry.SHARED_SENDER_DOMAINS:
            return ("emails", a.lower())
        return ("emails", dom)
    return ("aliases", a)


def _header_and_doc(text: str) -> tuple[str, dict]:
    header = []
    for ln in text.split("\n"):
        if ln.strip() == "" or ln.lstrip().startswith("#"):
            header.append(ln)
        else:
            break
    doc = registry.loads(text)
    return "\n".join(header).rstrip() + "\n" if header else "", doc


def _owner_index(reg, field: str) -> tuple:
    """(index, key_fn) — WHO already owns a value of this field.

    Each list field has its own uniqueness index inside Registry, and they are
    not keyed the same way: emails/aliases are `norm()`-folded, upwork_rooms are
    the raw room id (Registry._room_to_id stores it verbatim, and
    match_upwork_room looks it up verbatim). Before this existed the lookup was
    `_email_to_id if field == 'emails' else _alias_to_id`, so an upwork_rooms
    write consulted the ALIAS index — it could neither see a room another client
    already claimed nor recognise one this client already had, and a duplicate
    room claim is a registry LOAD ERROR that takes every reader down."""
    if field == "emails":
        return reg._email_to_id, registry.norm
    if field == "upwork_rooms":
        return reg._room_to_id, lambda v: (v or "").strip()
    return reg._alias_to_id, registry.norm


def write_registry(client_id: str, field: str, value: str, *, evidence: str,
                   by: str, do_git: bool = True, path: Optional[Path] = None,
                   root: Optional[Path] = None) -> dict:
    """Append `value` to a client's `field` in clients.yaml + (optionally) commit.
    Returns {changed, commit, note}. Refuses to steal a value another client owns."""
    p = Path(path or config.CLIENTS_REGISTRY)
    reg = registry.load(p, force=True)
    idx, key = _owner_index(reg, field)
    owner = idx.get(key(value))
    if owner and owner != client_id:
        return {"changed": False, "commit": None,
                "note": f"not-learned:claimed-by:{owner}"}
    if owner == client_id:
        return {"changed": False, "commit": None, "note": "already-known"}

    header, doc = _header_and_doc(p.read_text())
    client = next((c for c in doc["clients"] if c["id"] == client_id), None)
    if client is None:
        return {"changed": False, "commit": None, "note": "unknown-client"}
    client.setdefault(field, [])
    if value in client[field]:
        return {"changed": False, "commit": None, "note": "already-present"}
    client[field].append(value)
    p.write_text(header + registry.dumps(doc))
    registry.invalidate(p)

    commit = None
    if do_git:
        commit = _git_commit(p, client_id, field, value, evidence, by, root)
    return {"changed": True, "commit": commit, "note": "learned"}


def _commit_file(path: Path, msg: str, root: Optional[Path] = None) -> Optional[str]:
    """Stage + commit exactly one file as 'cockpit-approval'. Returns the short
    sha, or a `commit-failed:` marker — NEVER raises: the registry file is
    already written at this point and losing the receipt must not lose the
    change. The UI shows the marker verbatim so a silent half-write is
    impossible to mistake for a clean one."""
    root = root or config.V3_ROOT
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    try:
        subprocess.run(["git", "-C", str(root), "add", "--", rel],
                       check=True, capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(root), "commit",
                        "--author", "cockpit-approval <cockpit@etherwise.io>",
                        "-m", msg, "--", rel],
                       check=True, capture_output=True, timeout=30)
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             check=True, capture_output=True, text=True, timeout=30)
        return out.stdout.strip()[:12]
    except (subprocess.SubprocessError, OSError) as exc:
        return f"commit-failed:{exc!r}"[:80]


def _git_commit(path: Path, client_id: str, field: str, value: str,
                evidence: str, by: str, root: Optional[Path] = None) -> Optional[str]:
    # The subject line is a CONTRACT, not prose: cockpit data._REGISTRY_SUBJECT_RE
    # parses "registry: <client> <field> += <value>" out of `git log` to render
    # Activity's "what the registry learned today". Every write path here emits
    # that shape (create_client uses the pseudo-field `new_client`) so no
    # approval can change the spine invisibly.
    return _commit_file(
        path, f"registry: {client_id} {field} += {value}\n\n"
              f"via mapping-queue approval ({by}); evidence: {evidence}", root)


def resolve(db_path, event_id: str, action: str, *, client_id: Optional[str] = None,
            reason: Optional[str] = None, by: str = "cockpit-approval",
            evidence: Optional[str] = None,
            do_git: bool = True, registry_path: Optional[Path] = None,
            root: Optional[Path] = None, dossier_dir: Optional[Path] = None,
            now_iso: Optional[str] = None) -> dict:
    """action = assign(client_id) | create_alias(client_id) | ignore(reason)
    | internal | lead (2026-11-01: the prospect bucket).

    `evidence` is the deciding PHRASE, stored next to the bucket name on an
    ignore/internal (see events.ignore). A reason says which bucket the row went
    into and nothing about the call that put it there — the state 114 rows were
    found in on 2026-08-14, correctly labelled and unauditable."""
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    with db.connect(db_path) as conn:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not ev:
            return {"error": "unknown event", "event_id": event_id}
        ev = dict(ev)

    if action == "lead":
        # a prospect, not a client to map (2026-11-01): its own table, the
        # event filed under the lead id. His one click says "this is someone
        # who might buy", which is a different fact from "this is Acme".
        from modules.jarvis import leads
        with db.connect(db_path) as conn:
            lead_id = leads.from_event(conn, ev, note=reason, by=by)
        return {"ok": True, "action": action, "event_id": event_id,
                "lead_id": lead_id, "exclude_reason": f"{leads.FILE_PREFIX}{lead_id}"}

    if action in ("ignore", "internal"):
        # 'internal' is deliberately its own action rather than ignore-with-text.
        # An internal team call is NOT junk — it is our own work, and it may be
        # worth mining later for agency capability/proof-points. Keeping it
        # under one canonical reason means those can be found again; free-text
        # would drift into a dozen spellings and be unrecoverable.
        excl_reason = (INTERNAL_REASON if action == "internal"
                       else (reason or "not a client"))
        with db.connect(db_path) as conn:
            events.ignore(conn, event_id, excl_reason, by, evidence=evidence)
        return {"ok": True, "action": action, "event_id": event_id,
                "exclude_reason": excl_reason, "evidence": evidence}

    if action not in ("assign", "create_alias"):
        return {"error": f"unknown action {action!r}"}
    if not client_id:
        return {"error": "assign requires client_id"}
    reg = registry.load(registry_path, force=True)
    if client_id not in reg.by_id:
        return {"error": f"unknown client_id {client_id!r}"}

    learned = learned_pattern(ev["author"])
    reg_result = {"changed": False, "commit": None, "note": "no-pattern"}
    if learned:
        field, value = learned
        reg_result = write_registry(
            client_id, field, value, by=by, do_git=do_git, path=registry_path,
            root=root, evidence=f"{ev['source']} {event_id} from {ev['author']}")

    with db.connect(db_path) as conn:
        events.resolve(conn, event_id, client_id, by)

    # A human-approved recording (Fathom call OR Loom walkthrough) heals the KB:
    # reattribute the source + its facts and recompile the dossier. Approval is
    # human-authoritative, so this crosses no model-authority hard line -- and it
    # goes through the SAME shared heal_source both bridges already reuse.
    heal = None
    if ev["source"] in ("fathom", "loom") and ev["raw_ref"]:
        heal = fathom_bridge.heal_source(
            db_path, ev["raw_ref"], client_id, reg.by_id[client_id]["name"],
            now_iso=now_iso, dossier_dir=dossier_dir)

    return {"ok": True, "action": action, "event_id": event_id,
            "client_id": client_id, "registry": reg_result, "heal": heal}


# ── Upwork threads: the second review backlog, and its own write path ────────
# A thread is not an event. It has no `events` row, so resolve() cannot reach it,
# and the thing that identifies it is not a sender pattern but a ROOM ID.

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")


def _one_line(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def assign_thread(db_path, thread_id: str, client_id: str, *, evidence: str,
                  by: str = "cockpit-approval", do_git: bool = True,
                  registry_path: Optional[Path] = None,
                  root: Optional[Path] = None) -> dict:
    """Attribute one Upwork thread to an EXISTING registry client.

    Two writes, in this order and no other: the registry learns
    `upwork_rooms += <thread id>` (audited commit), then threads.client_id is
    set. Registry first because that claim is the durable one — if the process
    died between them the next `link_threads` would set client_id from the claim
    anyway, whereas the reverse order would leave a linkage nothing could
    reproduce.

    NEVER overwrites a non-null client_id. That mirrors link_threads' own rule
    ("a linkage that was approved once is not re-litigated"), and it is the
    reason a wrong attribution here is expensive: the UPDATE carries
    `AND client_id IS NULL` so a race with the rail cannot silently repoint a
    thread, and a 0-row update is REPORTED rather than smoothed over."""
    reg = registry.load(registry_path, force=True)
    if client_id not in reg.by_id:
        return {"error": "unknown client_id {!r}".format(client_id)}
    if reg.by_id[client_id].get("no_match"):
        return {"error": "client {!r} is marked no_match — it is a product or"
                         " internal entry and never holds threads".format(client_id)}
    with db.connect(db_path) as conn:
        row = conn.execute("SELECT id, room_name, client_id FROM threads"
                           " WHERE id=?", (thread_id,)).fetchone()
    if not row:
        return {"error": "unknown thread", "thread_id": thread_id}
    if row["client_id"]:
        return {"error": "thread already attributed to {!r} — attribution is"
                         " never overwritten".format(row["client_id"]),
                "thread_id": thread_id, "client_id": row["client_id"]}

    reg_result = write_registry(
        client_id, "upwork_rooms", thread_id, by=by, do_git=do_git,
        path=registry_path, root=root, evidence=evidence)
    if reg_result.get("note", "").startswith("not-learned:claimed-by"):
        # another client owns this room: writing threads.client_id anyway would
        # put the two records in direct contradiction.
        return {"error": "room already claimed in the registry ({})".format(
            reg_result["note"]), "thread_id": thread_id, "registry": reg_result}

    with db.connect(db_path) as conn:
        cur = conn.execute("UPDATE threads SET client_id=?,"
                           " updated_at=datetime('now')"
                           " WHERE id=? AND client_id IS NULL",
                           (client_id, thread_id))
        linked = cur.rowcount
    return {"ok": True, "action": "assign", "thread_id": thread_id,
            "client_id": client_id, "client_name": reg.by_id[client_id]["name"],
            "room_name": row["room_name"], "registry": reg_result,
            "linked": linked}


def _write_registry_doc(p: Path, header: str, doc: dict) -> str:
    """Serialize + write, and give the caller back the ORIGINAL text so it can
    roll back. clients.yaml is the spine: a file that does not parse takes every
    reader in the system down at once, not just this one action."""
    before = p.read_text()
    p.write_text(header + registry.dumps(doc))
    registry.invalidate(p)
    return before


def create_client(client_id: str, name: str, *, kind: str = "prospect",
                  upwork_room: Optional[str] = None, evidence: str,
                  by: str = "cockpit-approval", do_git: bool = True,
                  path: Optional[Path] = None,
                  root: Optional[Path] = None) -> dict:
    """Add a NEW client to the registry. The biggest write in the system.

    Everything about it is deliberately narrow: the caller must supply the slug
    (no id is derived from a display name, because a derived id is a name you
    cannot see before it is committed), the entry carries exactly the identity
    the human actually evidenced — the name and, when it came from a thread,
    that one room — and nothing is inferred. `kind` defaults to 'prospect':
    a prospect is fully matchable and keeps its context, but is excluded from
    the client-economics roster, so a mistaken create cannot silently move
    revenue figures.

    Refuses on every collision the loader would refuse on (duplicate id, a name
    another client already claims as name-or-alias, a room already claimed), and
    then RELOADS the written file — if the result does not validate, the
    original text is restored and the error returned. Never commits a file it
    could not read back."""
    kind = (kind or "prospect").strip().lower()
    if kind not in registry._KINDS:
        return {"error": "kind must be one of {}, got {!r}".format(
            registry._KINDS, kind)}
    client_id = (client_id or "").strip().lower()
    # ONE LINE, ALWAYS. registry._lines() is line-based, so a newline inside a
    # double-quoted scalar does not round-trip — it would write a file the
    # loader cannot read. Fold every scalar that comes from a form or from a
    # message body before it goes anywhere near dumps().
    name = _one_line(name)
    evidence = _one_line(evidence)[:400]
    if not _SLUG_RE.match(client_id):
        return {"error": "id must be a slug: lowercase letters, digits and"
                         " hyphens, 2-49 chars (got {!r})".format(client_id)}
    if not name:
        return {"error": "name required"}

    p = Path(path or config.CLIENTS_REGISTRY)
    reg = registry.load(p, force=True)
    if client_id in reg.by_id:
        return {"error": "client id {!r} already exists ({})".format(
            client_id, reg.by_id[client_id].get("name"))}
    owner = reg._alias_to_id.get(registry.norm(name))
    if owner:
        return {"error": "the name {!r} is already claimed by {!r} — assign the"
                         " thread to that client instead of creating a second"
                         " one".format(name, owner)}
    room = (upwork_room or "").strip() or None
    if room:
        room_owner = reg._room_to_id.get(room)
        if room_owner:
            return {"error": "upwork room already claimed by {!r}".format(room_owner)}

    header, doc = _header_and_doc(p.read_text())
    if not isinstance(doc, dict) or not isinstance(doc.get("clients"), list):
        return {"error": "registry is not a mapping with a 'clients:' list —"
                         " refusing to rewrite it"}
    entry: dict = {"id": client_id, "name": name}
    if room:
        entry["upwork_rooms"] = [room]
    entry["kind"] = kind
    entry["billing"] = {"canonical": name}
    entry["scope_notes"] = evidence
    clients = doc["clients"]
    # keep the file's id ordering if it has one — a new entry appended to the
    # bottom of an alphabetical file is a diff nobody can read.
    pos = next((i for i, c in enumerate(clients)
                if str(c.get("id") or "") > client_id), len(clients))
    clients.insert(pos, entry)

    before = _write_registry_doc(p, header, doc)
    try:
        registry.load(p, force=True)
    except Exception as exc:                     # noqa: BLE001 — roll back, always
        p.write_text(before)
        registry.invalidate(p)
        return {"error": "registry would not validate after the write, so it was"
                         " rolled back: {}".format(exc)}

    commit = None
    if do_git:
        commit = _commit_file(
            p, "registry: {} new_client += {}\n\n"
               "via cockpit thread review ({}); evidence: {}".format(
                   client_id, name, by, evidence), root)
    return {"ok": True, "created": True, "client_id": client_id, "name": name,
            "kind": kind, "upwork_room": room, "commit": commit,
            "registry": {"changed": True, "commit": commit, "note": "created"}}
