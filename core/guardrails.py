"""Hard-line enforcement for agent sessions (architecture §4, SOUL never-do).

Pure decision logic — the .claude/hooks/ shims feed it PreToolUse payloads and
emit deny JSON. Keeping the rules here makes them unit-testable and keeps the
shims trivial.

Rules enforced:
- Airtable writes without typecast: deny (always, even post-cutover)
- External mutations (Airtable/ClickUp/Gmail/any MCP) while ANY module is in
  shadow mode: deny — §9 zero-external-writes. Coarse by design for the kernel;
  per-table module mapping arrives with each cutover.
- Deletes: external delete tools deny always; Bash rm only inside the allowlist
  (var/, /tmp, .git lock debris); SQL mutations only on v3's own DB.
- v2 (../etherwise-os/) is read-only: any mutating command touching it denies.
- Credentials are never echoed: Bash reads of .credentials/ files deny.
- Upwork mutations beyond drafts: deny (drafts-only is sacred).
- Protected paths (core/, .claude/, rails/REGISTRY.md): deny writes unless the
  build-phase marker .claude/ALLOW_CORE_WRITES exists. The marker file itself
  is ALWAYS write-denied — deleting it at cutover is one-way from the agent's
  point of view.

Bash rules are heuristic defense-in-depth, not a sandbox: unresolvable targets
(variables, backticks) deny conservatively.
"""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core import config, db

MARKER_REL = ".claude/ALLOW_CORE_WRITES"

_FILE_WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
_PROTECTED_TOP_DIRS = ("core", ".claude")
_PROTECTED_FILES = ("rails/REGISTRY.md",)

_MCP_READONLY_VERBS = ("list", "get", "search", "read", "download", "ping",
                       "find", "filter", "resolve", "whoami", "check")
_SQL_MUTATION_RE = re.compile(r"\b(DELETE|DROP|UPDATE|INSERT|ALTER)\b")
_RM_RE = re.compile(r"(?:^|\s)(rm|rmdir)\s")
_CRED_READER_RE = re.compile(
    r"\b(cat|head|tail|less|more|grep|sed|awk|strings|base64|xxd|od|cp|mv|"
    r"source|echo|open|vi|vim|nano|code)\b")
_UPWORK_MUTATION_MARKERS = ("mutation", "sendMessage", "submitProposal",
                            "applyTo", "-X POST", "--request POST")


@dataclass
class Decision:
    action: str          # "allow" | "deny"
    reason: str = ""


ALLOW = Decision("allow")


def _deny(reason: str) -> Decision:
    return Decision("deny", reason)


# ── MCP (external services) ───────────────────────────────────────────────────

def _mcp_method(tool_name: str) -> str:
    """mcp__claude_ai_ClickUp__clickup_get_task -> get_task"""
    method = tool_name.rsplit("__", 1)[-1].lower()
    if method.startswith("clickup_"):
        method = method[len("clickup_"):]
    return method


def _evaluate_mcp(tool_name: str, tool_input: dict,
                  shadow_map: dict) -> Decision:
    method = _mcp_method(tool_name)
    if method.startswith(_MCP_READONLY_VERBS):
        return ALLOW

    if "delete" in method:
        return _deny(f"{method}: external deletes are outside the allowlist"
                     " — file a rail/proposal instead (SOUL never-do)")

    is_airtable = "Airtable" in tool_name
    if is_airtable and ("create_records" in method or
                        "update_records" in method):
        if tool_input.get("typecast") is not True:
            return _deny("Airtable writes require typecast: true"
                         " (hard line — set typecast and retry)")

    if any(shadow_map.values()):
        return _deny(f"{method}: external writes are suppressed while shadow"
                     " mode is on (§9) — use ctx.record_shadow_write()"
                     " so the intent lands in shadow_ledger")
    return ALLOW


# ── protected paths ───────────────────────────────────────────────────────────

def _evaluate_file_write(tool_input: dict, core_writes_allowed: bool,
                         v3_root: Path) -> Decision:
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not raw:
        return ALLOW
    path = Path(raw)
    if path.is_absolute():
        try:
            rel = path.resolve().relative_to(v3_root)
        except ValueError:
            return ALLOW  # outside the repo — permission system's concern
    else:
        rel = Path(raw)

    rel_str = rel.as_posix()
    if rel_str == MARKER_REL:
        return _deny("the build-phase marker is never agent-writable;"
                     " Abhijeet manages it directly")
    if rel.parts and rel.parts[0] in _PROTECTED_TOP_DIRS \
            or rel_str in _PROTECTED_FILES:
        if core_writes_allowed:
            return ALLOW
        return _deny(f"{rel_str} is protected (core/.claude/registry)."
                     " Propose a diff for review instead of editing —"
                     " hard line, see SOUL.md")
    return ALLOW


# ── Bash heuristics ───────────────────────────────────────────────────────────

def _rm_target_allowed(token: str, v3_root: Path) -> bool:
    if token.startswith("~") or "$" in token or "`" in token:
        return False
    if token.startswith(".git") or "/.git/" in token:
        return "lock" in token or "tmp_obj" in token
    if token.startswith("/"):
        return token.startswith(("/tmp/", "/private/tmp/",
                                 str(v3_root / "var")))
    return token.startswith("var/")


def _evaluate_rm(command: str, v3_root: Path) -> Optional[Decision]:
    for segment in re.split(r"&&|\|\||;|\n|\|", command):
        if not _RM_RE.search(segment):
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            return _deny("rm with unparseable arguments — denied"
                         " conservatively (deletes allowlist)")
        try:
            idx = next(i for i, t in enumerate(tokens)
                       if t in ("rm", "rmdir"))
        except StopIteration:
            continue
        targets = [t for t in tokens[idx + 1:] if not t.startswith("-")]
        if not targets:
            return _deny("rm without resolvable targets — denied")
        for t in targets:
            if not _rm_target_allowed(t, v3_root):
                return _deny(
                    f"rm {t!r}: deletes are allowed only under var/, /tmp,"
                    " and .git lock debris (deletes allowlist, SOUL never-do)")
    return None


def _evaluate_bash(command: str, v3_root: Path) -> Decision:
    if ".credentials" in command and _CRED_READER_RE.search(command):
        return _deny("reading credential files into the transcript is"
                     " forbidden (never echo credentials) — the gateway"
                     " loads secrets itself")

    if "etherwise-os" in command and (
            _SQL_MUTATION_RE.search(command)
            or re.search(r"\b(rm|mv|chmod|chown|truncate)\b", command)
            or ">" in command):
        return _deny("v2 (etherwise-os) is live production and read-only"
                     " from v3 — do not modify it (incidents only,"
                     " via Abhijeet)")

    rm_decision = _evaluate_rm(command, v3_root)
    if rm_decision:
        return rm_decision

    if ".db" in command and _SQL_MUTATION_RE.search(command):
        if not ("var/etherwise.db" in command
                or str(config.DB_PATH) in command):
            return _deny("SQL mutations are allowed only on v3's own"
                         " var/etherwise.db")

    if "upwork.com" in command and any(
            marker in command for marker in _UPWORK_MUTATION_MARKERS):
        return _deny("Upwork mutations beyond drafts are forbidden"
                     " (drafts only, never send — hard line)")

    return ALLOW


# ── entry points ──────────────────────────────────────────────────────────────

def evaluate_pretooluse(tool_name: str, tool_input: dict, *,
                        shadow_map: Optional[dict] = None,
                        core_writes_allowed: Optional[bool] = None,
                        v3_root: Optional[Path] = None) -> Decision:
    shadow_map = config.SHADOW_MODE if shadow_map is None else shadow_map
    v3_root = v3_root or config.V3_ROOT
    if core_writes_allowed is None:
        core_writes_allowed = (v3_root / MARKER_REL).exists()

    if tool_name in _FILE_WRITE_TOOLS:
        return _evaluate_file_write(tool_input, core_writes_allowed, v3_root)
    if tool_name == "Bash":
        return _evaluate_bash(tool_input.get("command") or "", v3_root)
    if tool_name.startswith("mcp__"):
        return _evaluate_mcp(tool_name, tool_input, shadow_map)
    return ALLOW


def audit_write(db_path, hook_event: str, tool_name: str, tool_input: dict,
                session_id: Optional[str] = None,
                note: Optional[str] = None) -> None:
    """PostToolUse audit row. Must NEVER raise — a broken audit hook would
    brick every session."""
    try:
        payload = json.dumps(tool_input, default=str)[:2000]
        actor = f"agent:{(session_id or 'unknown')[:12]}"
        with db.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO audit_log (actor, entity, field, new_value,"
                " source, note) VALUES (?,?,?,?,?,?)",
                (actor, tool_name, hook_event, payload, "hook",
                 (note or "")[:500] or None))
    except Exception:
        pass
