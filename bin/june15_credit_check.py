#!/usr/bin/env python3
"""June-15 credit-day orchestrator (Python; run by the FDA-granted framework
Python from launchd — NOT bash, which can't read scripts under ~/Desktop).

Replaces june15-credit-check.sh: runs bin/validate-credit-route, emails
Abhijeet the outcome + next steps, then self-removes its one-shot launchd job.
Deterministic — no model in the loop here (the validation makes one cheap
Haiku probe). --dry-run runs the validation and prints the email without
sending or removing the job.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO.parent
SEND_EMAIL = WORKSPACE / "scripts/send_email.py"
LABEL = "io.etherwise.v3.june15-credit-validation"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
VAL_OUT = "/tmp/june15-credit-validation.log"
EMAIL_HTML = "/tmp/june15-credit-email.html"


def run_validation() -> tuple:
    """Run validate-credit-route via THIS interpreter (framework python, FDA).
    Returns (exit_status, captured_output)."""
    proc = subprocess.run(
        [sys.executable, str(REPO / "bin/validate-credit-route")],
        capture_output=True, text=True, cwd="/",
        env={**os.environ, "PYTHONPATH": str(REPO)})
    out = (proc.stdout or "") + (proc.stderr or "")
    Path(VAL_OUT).write_text(out)
    return proc.returncode, out


def compose(status: int, output: str) -> str:
    esc = output.replace("&", "&amp;").replace("<", "&lt;")
    lines = ["<h2>June 15: Agent SDK credit day</h2>",
             "<p><b>1.</b> Claim the Agent SDK monthly credit (one-time"
             " opt-in, Max 20x account): support.claude.com article"
             " 15036540.</p>"]
    if status == 0:
        lines += ["<p><b>2. Credit-route validation: PASS</b> —"
                  " route_used=credit engaged with payg fallback"
                  " disabled.</p>",
                  "<p><b>3.</b> Flip the default in a reviewed commit:"
                  " core/config.py BILLING_DEFAULT_ROUTE \"payg\" →"
                  " \"credit\"; then validate the claude -p context shape"
                  " (open item in BUILD_BRIEF).</p>"]
    else:
        lines += ["<p><b>2. Credit-route validation: pending</b> — expected"
                  " if the credit isn't claimed yet. After claiming, rerun:"
                  "</p>",
                  f"<pre>cd {REPO} &amp;&amp; PYTHONPATH=. python3"
                  " bin/validate-credit-route</pre>"]
    lines += ["<h3>Validation output</h3>", f"<pre>{esc}</pre>"]
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="run validation + print email; no send, no self-remove")
    args = p.parse_args()

    status, output = run_validation()
    html = compose(status, output)
    Path(EMAIL_HTML).write_text(html)
    verdict = "PASS" if status == 0 else "pending (claim first)"

    if args.dry_run:
        print(f"[dry-run] validation exit={status} ({verdict})")
        print(f"[dry-run] would email contact@etherwise.io; would NOT"
              f" self-remove {LABEL}")
        print("--- email html ---")
        print(html)
        return 0

    subprocess.run(
        [sys.executable, str(SEND_EMAIL),
         "--to", "contact@etherwise.io",
         "--subject", f"June 15: claim Agent SDK credit — validation {verdict}",
         "--html-body-file", EMAIL_HTML],
        env={**os.environ, "PYTHONPATH": str(REPO)})

    # self-cleanup: one-shot job removes itself
    subprocess.run(["launchctl", "bootout",
                    f"gui/{os.getuid()}/{LABEL}"],
                   capture_output=True)
    try:
        PLIST.unlink()
    except FileNotFoundError:
        pass
    print(f"june15 check done (validation {verdict}); job self-removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
