"""Client registry — the identity spine (Jarvis J1, architecture v2 §4.1).

`config/clients.yaml` is the ONE canonical, git-versioned, human-readable map that
binds the four identity islands (dossiers, finance CLIENT_CANONICAL, ClickUp, Airtable).
Every sweep rail maps an event to a client through this file; every surface reads it.

Rules (architecture §4.1):
- Matching is DETERMINISTIC (registry patterns only). Models never write here — the
  file changes only via git commits from the mapping-queue approval flow or by hand.
  This loader is READ-ONLY; there is no write path in this module.
- Validated on load: unique ids; no two clients may claim the same email or the same
  alias (an overlap is a load ERROR, never a silent pick).
- stdlib-only: the repo ships no YAML dependency, so a strict minimal YAML-subset
  reader/writer lives here. It FAILS LOUD on anything it does not understand (a bad
  hand-edit raises, it never silently mis-parses the spine).

Per-client schema (all fields optional except id + name):
    id            short slug, unique, immutable          (str)
    name          canonical display name                 (str)
    aliases       other names / people / legal entities  (list[str])
    emails        sender domains AND/OR full addresses    (list[str])
    slack         {workspace, url, team_id, channels: [..]} (dict)
                  url/team_id drive the Slack browser rail; a team_id is unique
                  across the registry (two clients cannot claim one workspace)
    teams         M365 tenant / team ref                 (str)
    upwork_rooms  Upwork room ids                        (list[str])
    fathom_names  title/participant match fragments      (list[str])
    clickup       ClickUp space/folder ref               (str)
    billing       {rail, canonical, ledger_label}        (dict)
                  `canonical` is the DISPLAY name (cockpit card labels + the CoS
                  follow-up label read it via Registry.canonical); it must never
                  be a placeholder. `ledger_label` records the string the money
                  ledger actually carries when Upwork masked the client ('S',
                  'no company'). It is never an alias — the alias index matches
                  substrings and a one-character token would claim everything —
                  but since 2026-08-13 it IS matched, by EXACT equality on the
                  ledger's company column only (modules/knowledge/crm.py). Two
                  clients may not claim the same label; that is a load ERROR,
                  because a silent pick would put one client's money in two
                  dossiers.
    scope_notes   free text                              (str)
    kind          "client" (default) | "prospect"        (str)
    no_match      truthy => product/internal, never mapped (str/bool)

`kind: prospect` marks an entry that is NOT a paying client (e.g. an expired
proposal that nonetheless produced real calls). UNLIKE `no_match`, a prospect is
fully indexed and matchable, so its calls attribute to it and the context is
kept — but it is excluded from the client economics roster (`client_ids()`).
"""
from __future__ import annotations

import hashlib
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any, Optional

from core import config


class RegistryError(Exception):
    """Malformed registry file, or a validation invariant violated on load."""


# ── strict minimal YAML subset (stdlib-only) ─────────────────────────────────
# Grammar handled (exactly what dump() emits, plus tolerant hand-edits):
#   - block mappings            key: value   /   key:  (nested block)
#   - block sequences           - value      /   -     (nested block under it)
#   - scalars                   bare tokens or "double-quoted" (\" and \\ escapes)
#   - inline empties            []  and  {}
# Anything else (tabs, flow collections with content, anchors, multi-docs) RAISES.

def _strip_comment(line: str) -> str:
    """Drop a trailing `#...` comment, but never one inside a double-quoted scalar."""
    out, in_q, i = [], False, 0
    while i < len(line):
        c = line[i]
        if c == '"':
            in_q = not in_q
        elif c == "\\" and in_q and i + 1 < len(line):
            out.append(c); out.append(line[i + 1]); i += 2; continue
        elif c == "#" and not in_q and (not out or out[-1] in (" ", "\t")):
            break
        out.append(c); i += 1
    return "".join(out).rstrip()


def _scalar(tok: str) -> Any:
    tok = tok.strip()
    if tok == "" or tok in ("null", "~"):
        return None
    if tok == "[]":
        return []
    if tok == "{}":
        return {}
    if tok[0] == '"':
        if len(tok) < 2 or tok[-1] != '"':
            raise RegistryError(f"unterminated quoted scalar: {tok!r}")
        body, out, i = tok[1:-1], [], 0
        while i < len(body):
            c = body[i]
            if c == "\\" and i + 1 < len(body):
                nxt = body[i + 1]
                out.append('"' if nxt == '"' else "\\" if nxt == "\\" else "\\" + nxt)
                i += 2; continue
            out.append(c); i += 1
        return "".join(out)
    if tok[0] in "[{":
        raise RegistryError(f"inline collections with content unsupported: {tok!r}")
    return tok


def _lines(text: str) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for raw in text.split("\n"):
        s = _strip_comment(raw)
        if s.strip() == "":
            continue
        indent = len(s) - len(s.lstrip(" "))
        if "\t" in s[: indent + 1] or s[:indent] != " " * indent:
            raise RegistryError("tabs are not allowed in the registry YAML")
        rows.append((indent, s.strip()))
    return rows


def _is_seq(item: str) -> bool:
    return item == "-" or item.startswith("- ")


def _parse_block(rows: list[tuple[int, str]], i: int) -> tuple[Any, int]:
    indent = rows[i][0]
    if _is_seq(rows[i][1]):
        seq: list[Any] = []
        while i < len(rows) and rows[i][0] == indent and _is_seq(rows[i][1]):
            content = rows[i][1][1:].strip()
            if content == "":
                i += 1
                if i < len(rows) and rows[i][0] > indent:
                    val, i = _parse_block(rows, i)
                else:
                    val = None
            else:
                val = _scalar(content); i += 1
            seq.append(val)
        return seq, i
    mapping: dict[str, Any] = {}
    while i < len(rows) and rows[i][0] == indent and not _is_seq(rows[i][1]):
        key, sep, rest = rows[i][1].partition(":")
        if not sep:
            raise RegistryError(f"expected 'key: value', got {rows[i][1]!r}")
        key, rest = key.strip(), rest.strip()
        if rest == "":
            i += 1
            if i < len(rows) and rows[i][0] > indent:
                val, i = _parse_block(rows, i)
            else:
                val = None
        else:
            val = _scalar(rest); i += 1
        if key in mapping:
            raise RegistryError(f"duplicate key {key!r} in mapping")
        mapping[key] = val
    return mapping, i


def loads(text: str) -> Any:
    rows = _lines(text)
    if not rows:
        return None
    if rows[0][0] != 0:
        raise RegistryError("top-level content must start at column 0")
    value, i = _parse_block(rows, 0)
    if i != len(rows):
        raise RegistryError(
            f"could not parse the whole file (stopped before line {rows[i]!r})")
    return value


_BARE_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
               " ._/&()@+-")   # '-' safe mid-token; a LEADING '-' is still quoted below


def _needs_quote(s: str) -> bool:
    if s == "" or s != s.strip() or s.lower() in ("null", "true", "false", "yes",
                                                   "no", "~", "on", "off"):
        return True
    if s[0] in "-?&*!|>%@`\"'[{#":
        return True
    return any(c not in _BARE_OK for c in s)


def _fmt_scalar(v: Any) -> str:
    if v is None:
        return '""'
    s = str(v)
    if not _needs_quote(s):
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _dump(obj: Any, indent: int, out: list[str]) -> None:
    pad = "  " * indent
    if isinstance(obj, dict):
        if not obj:
            raise RegistryError("cannot dump an empty mapping at block level")
        for k, v in obj.items():
            if isinstance(v, dict) and v:
                out.append(f"{pad}{k}:"); _dump(v, indent + 1, out)
            elif isinstance(v, list) and v:
                out.append(f"{pad}{k}:"); _dump(v, indent + 1, out)
            elif isinstance(v, dict):
                out.append(f"{pad}{k}: {{}}")
            elif isinstance(v, list):
                out.append(f"{pad}{k}: []")
            else:
                out.append(f"{pad}{k}: {_fmt_scalar(v)}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)) and item:
                out.append(f"{pad}-"); _dump(item, indent + 1, out)
            else:
                out.append(f"{pad}- {_fmt_scalar(item)}")
    else:
        raise RegistryError("top-level dump must be a mapping or sequence")


def dumps(obj: Any) -> str:
    out: list[str] = []
    _dump(obj, 0, out)
    return "\n".join(out) + "\n"


# ── normalization (accent-insensitive, matches cockpit data.py convention) ───
def norm(s: str) -> str:
    """Identity key for a name/alias/email. Accent-insensitive, case-insensitive,
    and whitespace-insensitive — INCLUDING internal runs.

    The internal collapse was added 2026-08-13. Upwork emits "Tanushant  Sharma"
    with TWO spaces in `threads.room_name`, `contracts.client_company` AND
    `transactions.assignment_company`; the registry holds one space. The exact
    dict lookup missed, the substring fallback missed, and an EXISTING registry
    client with $757.23 earned across two ACTIVE contracts and 80 messages was
    filed under nobody. Same bug hid "Ippon  Technology" ($333.53). Whitespace is
    never identity.

    Blast radius was MEASURED, not asserted, before this shipped
    (var/agent-scratch/s3/blast.py, 2026-08-13): 17,023 consumer answers across
    every caller of norm() — the registry email/alias indexes, `match_name`,
    `clients._match`, `clients.match_counterparty`, `loom_bridge.title_match`
    and `transcript_match`, `mapping._alnum`, `cockpit._ident_kind` — run over
    the real corpora both ways. 32 changed, ALL of them None -> a client id;
    zero hits were lost and zero were re-pointed. No registry name or alias
    contains a double space, so a collapsed key can only meet a corpus string
    that was previously unreachable.

    Note this also folds NEWLINES into spaces, which matters for
    `transcript_match` (it normalizes whole transcripts): a multi-word needle
    could newly match across a line break. Measured over all 1,180 Loom+Fathom
    transcripts (505 of which contain a whitespace run): zero changes.
    """
    d = unicodedata.normalize("NFKD", s or "")
    d = "".join(c for c in d if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", d).strip().lower()


def _domain(addr: str) -> str:
    a = (addr or "").strip().lower()
    return a.split("@", 1)[1] if "@" in a else a


# ── the loaded registry ──────────────────────────────────────────────────────
_LIST_FIELDS = ("aliases", "emails", "upwork_rooms", "fathom_names")
_STR_FIELDS = ("name", "teams", "clickup", "scope_notes")
_KINDS = ("client", "prospect")


class Registry:
    """Validated, indexed, read-only view over clients.yaml."""

    def __init__(self, clients: list[dict], *, sha: str, path: Optional[Path] = None):
        self.clients = clients
        self.sha = sha
        self.path = path
        self.by_id: dict[str, dict] = {}
        self._email_to_id: dict[str, str] = {}
        self._alias_to_id: dict[str, str] = {}
        self._room_to_id: dict[str, str] = {}
        self._slack_team_to_id: dict[str, str] = {}
        self._ledger_label_to_id: dict[str, str] = {}
        self._validate_and_index()

    def _validate_and_index(self) -> None:
        for c in self.clients:
            if not isinstance(c, dict):
                raise RegistryError(f"client entry is not a mapping: {c!r}")
            cid, name = c.get("id"), c.get("name")
            if not cid or not name:
                raise RegistryError(f"client missing id or name: {c!r}")
            if cid in self.by_id:
                raise RegistryError(f"duplicate client id: {cid!r}")
            for f in _LIST_FIELDS:
                v = c.get(f)
                if v is None:
                    c[f] = []
                elif not isinstance(v, list):
                    raise RegistryError(f"{cid}.{f} must be a list, got {v!r}")
            kind = c.get("kind")
            if kind is not None and kind not in _KINDS:
                raise RegistryError(
                    f"{cid}.kind must be one of {_KINDS}, got {kind!r}")
            self.by_id[cid] = c

            # no_match entries (products/internal) stay in the registry + the
            # trust view, but are NEVER used to map an event or proposed as a
            # candidate — skip all match indexing for them.
            if c.get("no_match"):
                continue

            # emails: unique across the whole registry (domain or full address)
            for e in c["emails"]:
                key = norm(e)
                owner = self._email_to_id.get(key)
                if owner and owner != cid:
                    raise RegistryError(
                        f"email {e!r} claimed by both {owner!r} and {cid!r}")
                self._email_to_id[key] = cid
            # aliases + the canonical name must not collide across clients
            for a in [name, *c["aliases"]]:
                key = norm(a)
                owner = self._alias_to_id.get(key)
                if owner and owner != cid:
                    raise RegistryError(
                        f"alias/name {a!r} claimed by both {owner!r} and {cid!r}")
                self._alias_to_id[key] = cid
            for r in c["upwork_rooms"]:
                owner = self._room_to_id.get(r)
                if owner and owner != cid:
                    raise RegistryError(
                        f"upwork room {r!r} claimed by both {owner!r} and {cid!r}")
                self._room_to_id[r] = cid
            # a Slack workspace (team id) belongs to exactly one client
            team = (c.get("slack") or {}).get("team_id")
            if team:
                owner = self._slack_team_to_id.get(team)
                if owner and owner != cid:
                    raise RegistryError(
                        f"slack team {team!r} claimed by both {owner!r} and {cid!r}")
                self._slack_team_to_id[team] = cid
            # a masked-ledger label ('S', 'no company') belongs to exactly one
            # client. crm.py matches it EXACTLY against the ledger's company
            # column, so an overlap would credit the same money to two dossiers.
            label = (c.get("billing") or {}).get("ledger_label")
            if label:
                key = norm(label)
                owner = self._ledger_label_to_id.get(key)
                if owner and owner != cid:
                    raise RegistryError(
                        f"billing.ledger_label {label!r} claimed by both"
                        f" {owner!r} and {cid!r}")
                self._ledger_label_to_id[key] = cid

    # ── deterministic matchers (used by the map stage) ──────────────────────
    def match_email(self, from_addr: str) -> Optional[str]:
        """A sender maps to a client iff its full address or its domain is claimed."""
        a = (from_addr or "").strip().lower()
        if not a:
            return None
        if a in self._email_to_id:
            return self._email_to_id[a]
        return self._email_to_id.get(_domain(a))

    def match_upwork_room(self, room: str) -> Optional[str]:
        return self._room_to_id.get((room or "").strip())

    def match_slack_team(self, team_id: str) -> Optional[str]:
        """A Slack team id maps to its client — the Slack rail's deterministic
        spine (a client workspace IS the client, no proposer needed)."""
        return self._slack_team_to_id.get((team_id or "").strip())

    def slack_workspaces(self) -> list[dict]:
        """Every matchable client that has a Slack workspace URL, as
        {client_id, workspace, url, team_id}. The Slack browser rail drives one
        loop iteration per entry; internal (non-client) workspaces never appear
        here because they are not registry clients. Ordered by client id for a
        stable, reproducible sweep order."""
        out = []
        for cid in self.matchable_ids():
            slack = self.by_id[cid].get("slack") or {}
            url = slack.get("url")
            if not url:
                continue
            out.append({"client_id": cid, "workspace": slack.get("workspace"),
                        "url": url, "team_id": slack.get("team_id")})
        return sorted(out, key=lambda w: w["client_id"])

    def match_name(self, text: str) -> Optional[str]:
        return self._alias_to_id.get(norm(text))

    def match_ledger_label(self, label: str) -> Optional[str]:
        """A MASKED ledger company string ('S', 'no company') -> its client.
        EXACT (normalised) match only, never a substring — that is the whole
        point of keeping these out of the alias index: 'S' as a substring would
        claim half the ledger. `_validate_and_index` already guarantees one
        owner per label, so this can never split a payment between clients."""
        return self._ledger_label_to_id.get(norm(label))

    def canonical(self, client_id: str) -> Optional[str]:
        c = self.by_id.get(client_id)
        if not c:
            return None
        return (c.get("billing") or {}).get("canonical") or c.get("name")

    def ids(self) -> list[str]:
        return list(self.by_id)

    def matchable_ids(self) -> list[str]:
        """Client ids eligible for matching/assignment (excludes no_match).
        Prospects ARE matchable — they hold their calls' context."""
        return [cid for cid, c in self.by_id.items() if not c.get("no_match")]

    def kind(self, client_id: str) -> Optional[str]:
        """'client' (default) or 'prospect'; None if the id is unknown."""
        c = self.by_id.get(client_id)
        return None if c is None else (c.get("kind") or "client")

    def client_ids(self) -> list[str]:
        """The economics roster: matchable ids that are real clients — excludes
        both no_match products and prospects (unpaid, kept only for context)."""
        return [cid for cid in self.matchable_ids()
                if self.kind(cid) != "prospect"]


# ── cached loader (mtime-invalidated; approval flow calls invalidate()) ──────
_lock = threading.Lock()
_cache: dict[str, tuple[float, Registry]] = {}


def load(path: Optional[Path] = None, *, force: bool = False) -> Registry:
    p = Path(path or config.CLIENTS_REGISTRY)
    key = str(p)
    try:
        mtime = p.stat().st_mtime
    except OSError as exc:
        raise RegistryError(f"registry file not found: {p}") from exc
    with _lock:
        cached = _cache.get(key)
        if cached and not force and cached[0] == mtime:
            return cached[1]
    raw = p.read_bytes()
    doc = loads(raw.decode("utf-8"))
    if doc is None:
        clients: list[dict] = []
    elif isinstance(doc, dict) and "clients" in doc:
        clients = doc.get("clients") or []
    elif isinstance(doc, list):
        clients = doc
    else:
        raise RegistryError("registry must be a mapping with a 'clients:' list")
    if not isinstance(clients, list):
        raise RegistryError("'clients' must be a list")
    reg = Registry(clients, sha=hashlib.sha256(raw).hexdigest(), path=p)
    with _lock:
        _cache[key] = (mtime, reg)
    return reg


def loads_registry(text: str) -> Registry:
    """Parse + validate a registry from an in-memory YAML string (no file, no
    cache). Used by tests and any caller holding registry text directly."""
    raw = text.encode("utf-8")
    doc = loads(text)
    if doc is None:
        clients: list[dict] = []
    elif isinstance(doc, dict) and "clients" in doc:
        clients = doc.get("clients") or []
    elif isinstance(doc, list):
        clients = doc
    else:
        raise RegistryError("registry must be a mapping with a 'clients:' list")
    if not isinstance(clients, list):
        raise RegistryError("'clients' must be a list")
    return Registry(clients, sha=hashlib.sha256(raw).hexdigest())


def invalidate(path: Optional[Path] = None) -> None:
    """Drop the cache (after the approval flow commits a registry change)."""
    with _lock:
        if path is None:
            _cache.clear()
        else:
            _cache.pop(str(Path(path)), None)
