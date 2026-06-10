#!/usr/bin/env python3
"""PreToolUse shim — feeds the payload to core.guardrails and emits deny JSON.

Fail-closed: if guardrails itself errors on a mutating tool, deny; reads stay
allowed so a guardrails bug can't brick investigation of itself.
"""
import json
import os
import sys
from pathlib import Path

ROOT = os.environ.get("CLAUDE_PROJECT_DIR") or str(
    Path(__file__).resolve().parents[2])
sys.path.insert(0, ROOT)

_MUTATING = ("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit")


def _emit_deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))


def main() -> None:
    tool_name = ""
    try:
        data = json.load(sys.stdin)
        tool_name = data.get("tool_name", "") or ""
        tool_input = data.get("tool_input") or {}

        from core import config, guardrails
        decision = guardrails.evaluate_pretooluse(tool_name, tool_input)
        if decision.action == "deny":
            guardrails.audit_write(config.DB_PATH, "PreToolUse-deny",
                                   tool_name, tool_input,
                                   session_id=data.get("session_id"),
                                   note=decision.reason)
            _emit_deny(decision.reason)
    except Exception as exc:  # noqa: BLE001 — fail-closed on mutations
        if tool_name in _MUTATING or tool_name.startswith("mcp__"):
            _emit_deny(f"guardrails internal error (fail-closed): {exc!r}")
    sys.exit(0)


if __name__ == "__main__":
    main()
