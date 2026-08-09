"""Mapping-queue approval flow (J1 D3 §4.6) — the ONLY write path to the registry.

Abhijeet's approval in the cockpit is what edits clients.yaml, via an audited git
commit (author 'cockpit-approval', message carries the event evidence). The agent
has no other write path (architecture §4.1, immutable-policy pattern). On assign
the flow LEARNS the identifying pattern (sender domain / participant) so the same
sender auto-maps next sweep, maps the event, and — for a recording (Fathom call
or Loom walkthrough) — heals the dossier (source + facts reattributed, recompiled).
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core import config, db, registry
from modules.jarvis import events, fathom_bridge

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
        return ("emails", a.lower()) if dom in _FREEMAIL else ("emails", dom)
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


def write_registry(client_id: str, field: str, value: str, *, evidence: str,
                   by: str, do_git: bool = True, path: Optional[Path] = None,
                   root: Optional[Path] = None) -> dict:
    """Append `value` to a client's `field` in clients.yaml + (optionally) commit.
    Returns {changed, commit, note}. Refuses to steal a value another client owns."""
    p = Path(path or config.CLIENTS_REGISTRY)
    reg = registry.load(p, force=True)
    idx = reg._email_to_id if field == "emails" else reg._alias_to_id
    owner = idx.get(registry.norm(value))
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


def _git_commit(path: Path, client_id: str, field: str, value: str,
                evidence: str, by: str, root: Optional[Path] = None) -> Optional[str]:
    root = root or config.V3_ROOT
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    msg = (f"registry: {client_id} {field} += {value}\n\n"
           f"via mapping-queue approval ({by}); evidence: {evidence}")
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


def resolve(db_path, event_id: str, action: str, *, client_id: Optional[str] = None,
            reason: Optional[str] = None, by: str = "cockpit-approval",
            do_git: bool = True, registry_path: Optional[Path] = None,
            root: Optional[Path] = None, dossier_dir: Optional[Path] = None,
            now_iso: Optional[str] = None) -> dict:
    """action = assign(client_id) | create_alias(client_id) | ignore(reason)."""
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    with db.connect(db_path) as conn:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not ev:
            return {"error": "unknown event", "event_id": event_id}
        ev = dict(ev)

    if action == "ignore":
        with db.connect(db_path) as conn:
            events.ignore(conn, event_id, reason or "not a client", by)
        return {"ok": True, "action": "ignore", "event_id": event_id}

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
