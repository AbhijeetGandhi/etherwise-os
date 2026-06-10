#!/usr/bin/env python3
"""PostToolUse shim — audit_log row for every tool call. Never blocks,
never raises (a broken audit hook must not brick sessions)."""
import json
import os
import sys
from pathlib import Path

ROOT = os.environ.get("CLAUDE_PROJECT_DIR") or str(
    Path(__file__).resolve().parents[2])
sys.path.insert(0, ROOT)


def main() -> None:
    try:
        data = json.load(sys.stdin)
        from core import config, guardrails
        guardrails.audit_write(config.DB_PATH, "PostToolUse",
                               data.get("tool_name", "") or "unknown",
                               data.get("tool_input") or {},
                               session_id=data.get("session_id"))
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
