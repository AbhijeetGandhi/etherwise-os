#!/usr/bin/env python3
"""Close ClickUp tasks listed in a file (one task id per line) as EXPIRED with
an explanatory comment. Used to apply externally-verified liveness verdicts.
Usage: python3 scripts/apply_verdicts.py /tmp/sweep/close_list.txt"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from board_cleanup import req, token  # noqa: E402

COMMENT = ("Auto-closed (external liveness sweep 2026-06-11): this job URL was "
           "verified directly against Upwork and returns 'Job not found' — the "
           "posting was removed/filled, or the task came from a corrupted scan "
           "batch. Reopen if you disagree; nothing was deleted.")


def main() -> None:
    ids = [line.strip() for line in Path(sys.argv[1]).read_text().splitlines()
           if line.strip()]
    tok = token()
    done, failed = 0, []
    for tid in ids:
        try:
            req("POST", f"/task/{tid}/comment", tok, {"comment_text": COMMENT})
            time.sleep(0.65)
            req("PUT", f"/task/{tid}", tok, {"status": "EXPIRED"})
            time.sleep(0.65)
            done += 1
        except Exception as exc:
            failed.append({"task": tid, "error": repr(exc)[:120]})
    print(json.dumps({"closed": done, "failed": failed}, indent=1))


if __name__ == "__main__":
    main()
