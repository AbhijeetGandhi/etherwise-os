#!/usr/bin/env python3
"""SessionStart shim — config integrity + guardrail state, injected as context.

Checks (NemoClaw immutable-policy pattern):
- core/config.py drift vs git HEAD (uncommitted config change = warn loudly)
- build-phase marker state (core writes allowed vs operational lockdown)
- DB presence
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = os.environ.get("CLAUDE_PROJECT_DIR") or str(
    Path(__file__).resolve().parents[2])
sys.path.insert(0, ROOT)


def main() -> None:
    lines = []
    try:
        from core import config, guardrails

        try:
            head_bytes = subprocess.run(
                ["git", "-C", ROOT, "show", "HEAD:core/config.py"],
                capture_output=True, timeout=10).stdout
            import hashlib
            head_sha = hashlib.sha256(head_bytes).hexdigest()
            if head_sha != config.config_sha256():
                lines.append(
                    "WARNING: core/config.py differs from git HEAD —"
                    " uncommitted config change. Config edits arrive only as"
                    " reviewed commits; commit or revert before running tasks.")
        except Exception:
            lines.append("NOTE: could not verify config.py against git HEAD.")

        if (Path(ROOT) / guardrails.MARKER_REL).exists():
            lines.append(
                "Guardrails: build-phase marker ACTIVE — core/.claude writes"
                " allowed (delete .claude/ALLOW_CORE_WRITES at cutover).")
        else:
            lines.append(
                "Guardrails: operational lockdown — agent writes to core/,"
                " .claude/, rails/REGISTRY.md are denied (propose diffs).")

        if not config.DB_PATH.exists():
            lines.append("WARNING: var/etherwise.db missing — run"
                         " PYTHONPATH=. python3 -m core.db")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"sessionstart hook error (non-fatal): {exc!r}")

    if lines:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(lines),
        }}))
    sys.exit(0)


if __name__ == "__main__":
    main()
