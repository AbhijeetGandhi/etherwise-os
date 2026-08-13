#!/bin/bash
# Install every v3 launchd agent: repo plist -> ~/Library/LaunchAgents -> loaded.
#
# WHY THIS EXISTS (2026-08-13 incident). The repo's launchd/ directory looked
# like the source of truth, but NOTHING ever copied a plist out of it. Jobs were
# brought up by hand with `launchctl bootstrap gui/501 <repo>/launchd/x.plist`,
# which loads the job into the CURRENT boot session only. So:
#
#   * jarvis-sweep ran fine Aug 7-11, then the Mac rebooted (Aug 11 04:54) and
#     the job simply ceased to exist. Today, the mapping queue and both IMAP
#     inboxes had been dead 64h.
#   * kb-loom-fetch and finance-payment-mail were authored, registered in
#     core/config.py SOURCES as live sources, and never bootstrapped at all.
#     loom_fetch had NEVER run once.
#
# Worse, the remediation catch-up prints points at
# ~/Library/LaunchAgents/<label>.plist — a path that did not exist for exactly
# the three broken jobs, so following the instruction verbatim failed.
#
# Copying to ~/Library/LaunchAgents is what makes a job survive a reboot. Run
# this after adding or editing any plist; it is idempotent.
#
#   bash launchd/install.sh            # install + load everything missing
#   bash launchd/install.sh --check    # report only, change nothing
#   bash launchd/install.sh --only io.etherwise.v3.jarvis-sweep
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO/launchd"
DEST="$HOME/Library/LaunchAgents"
UID_N="$(id -u)"
CHECK=0
ONLY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1; shift ;;
    --only)  ONLY="${2:-}"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$DEST"
installed=0; updated=0; loaded=0; already=0; failed=0

for plist in "$SRC"/*.plist; do
  [ -e "$plist" ] || continue
  base="$(basename "$plist")"
  label="${base%.plist}"
  [ -n "$ONLY" ] && [ "$label" != "$ONLY" ] && continue

  # 1. file present and identical?
  if [ ! -f "$DEST/$base" ]; then
    action="INSTALL (was never copied — could not survive a reboot)"
  elif ! cmp -s "$plist" "$DEST/$base"; then
    action="UPDATE (installed copy differs from the repo)"
  else
    action=""
  fi

  if [ -n "$action" ]; then
    echo "  $label: $action"
    if [ "$CHECK" -eq 0 ]; then
      cp "$plist" "$DEST/$base" || { echo "    cp FAILED"; failed=$((failed+1)); continue; }
      # a changed plist must be re-read: bootout first, ignore 'not loaded'
      launchctl bootout "gui/$UID_N/$label" >/dev/null 2>&1
      case "$action" in INSTALL*) installed=$((installed+1));; *) updated=$((updated+1));; esac
    fi
  fi

  # 2. known to launchd?
  if launchctl list "$label" >/dev/null 2>&1; then
    [ -z "$action" ] && already=$((already+1))
  else
    echo "  $label: NOT LOADED -> bootstrap"
    if [ "$CHECK" -eq 0 ]; then
      if err="$(launchctl bootstrap "gui/$UID_N" "$DEST/$base" 2>&1)"; then
        loaded=$((loaded+1))
      else
        echo "    bootstrap FAILED: $err"
        failed=$((failed+1))
      fi
    fi
  fi
done

echo
echo "installed=$installed updated=$updated bootstrapped=$loaded already-ok=$already failed=$failed"

# 3. the guarantee that actually matters: every source the registry declares
#    live must have a launchd label launchd has actually heard of. A source
#    registered in SOURCES but never bootstrapped reads as stale/down forever
#    and no amount of catch-up can fix it.
echo
echo "verifying core/config.py SOURCES launchd labels:"
PYTHONPATH="$REPO" /usr/bin/python3 - "$UID_N" <<'PY'
import subprocess, sys
sys.path.insert(0, __import__("os").environ["PYTHONPATH"])
from core import config

uid = sys.argv[1]
seen, bad = set(), []
for s in config.SOURCES:
    lbl = s.get("launchd_label")
    if not lbl or lbl in seen:
        continue
    seen.add(lbl)
    ok = subprocess.run(["launchctl", "list", lbl],
                        capture_output=True).returncode == 0
    if not ok:
        bad.append((s["key"], lbl))
    print(f"  {'OK  ' if ok else 'MISSING'}  {s['key']:22} {lbl}")
if bad:
    print("\n  UNLOADED SOURCES — these will read stale/down forever:")
    for k, l in bad:
        print(f"    {k}: launchctl bootstrap gui/{uid} "
              f"~/Library/LaunchAgents/{l}.plist")
    sys.exit(1)
print(f"\n  all {len(seen)} registered source agents are loaded")
PY
