#!/bin/bash
# One-shot June-15 calendar action (installed 2026-06-11, Day 3).
# Runs the credit-route validation and emails Abhijeet the outcome + next
# steps. Deterministic — no model in the loop. Self-removes its launchd job.
set -u
V3="/Users/abhijeet/Desktop/Etherwise/etherwise-v3"
SCRIPTS="/Users/abhijeet/Desktop/Etherwise/scripts"
OUT="/tmp/june15-credit-validation.log"
LABEL="io.etherwise.v3.june15-credit-validation"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

cd "$V3" || exit 1
PYTHONPATH=. python3 bin/validate-credit-route > "$OUT" 2>&1
STATUS=$?

{
  echo "<h2>June 15: Agent SDK credit day</h2>"
  echo "<p><b>1.</b> Claim the Agent SDK monthly credit (one-time opt-in,"
  echo "Max 20x account): see support.claude.com article 15036540.</p>"
  if [ $STATUS -eq 0 ]; then
    echo "<p><b>2. Credit-route validation: PASS</b> — route_used=credit"
    echo "engaged with payg fallback disabled.</p>"
    echo "<p><b>3.</b> Flip the default in a reviewed commit:"
    echo "core/config.py BILLING_DEFAULT_ROUTE default \"payg\" →"
    echo "\"credit\". Then validate claude -p context shape (open item in"
    echo "BUILD_BRIEF).</p>"
  else
    echo "<p><b>2. Credit-route validation: FAIL</b> — expected if the"
    echo "credit isn't claimed yet. After claiming, rerun:</p>"
    echo "<pre>cd $V3 && PYTHONPATH=. python3 bin/validate-credit-route</pre>"
  fi
  echo "<h3>Validation output</h3><pre>"
  sed 's/&/\&amp;/g; s/</\&lt;/g' "$OUT"
  echo "</pre>"
} > /tmp/june15-credit-email.html

python3 "$SCRIPTS/send_email.py" \
  --to contact@etherwise.io \
  --subject "June 15: claim Agent SDK credit — validation $( [ $STATUS -eq 0 ] && echo PASS || echo 'pending (claim first)' )" \
  --html-body-file /tmp/june15-credit-email.html

# self-cleanup: remove the one-shot job
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
rm -f "$PLIST"
