"""Send scan digest email for this run."""
import subprocess, sys

HTML = """<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto; padding: 20px;">
<h2>🔍 Upwork Scan: 38 new jobs, 1 hot lead</h2>

<h3>🔥 Hot Leads (score ≥16)</h3>
<table style="width:100%; border-collapse:collapse; margin-bottom:20px;">
<tr style="background:#f0f0f0;">
  <th style="padding:8px; text-align:left;">Title</th>
  <th style="padding:8px;">Score</th>
  <th style="padding:8px;">Budget</th>
  <th style="padding:8px;">Client</th>
  <th style="padding:8px;">List</th>
</tr>
<tr>
  <td style="padding:8px;"><a href="https://www.upwork.com/jobs/Airtable-drayage-dispatch-Development-Specialist_~022065227354869353126/">Airtable drayage dispatch Development Specialist</a></td>
  <td style="padding:8px; text-align:center;"><strong>16</strong></td>
  <td style="padding:8px;">$20–50/hr</td>
  <td style="padding:8px;">$50K spent, 4.9★, USA ✓</td>
  <td style="padding:8px;">🔥 Hot</td>
</tr>
</table>

<h3>📊 Feed Summary</h3>
<table style="width:100%; border-collapse:collapse; margin-bottom:20px;">
<tr style="background:#f0f0f0;">
  <th style="padding:8px;">Feed</th><th style="padding:8px;">Extracted</th><th style="padding:8px;">New</th><th style="padding:8px;">Known</th>
</tr>
<tr><td style="padding:8px;">most_recent</td><td style="padding:8px; text-align:center;">10</td><td style="padding:8px; text-align:center;">10</td><td style="padding:8px; text-align:center;">0</td></tr>
<tr><td style="padding:8px;">best_matches</td><td style="padding:8px; text-align:center;">30</td><td style="padding:8px; text-align:center;">26</td><td style="padding:8px; text-align:center;">4</td></tr>
<tr><td style="padding:8px;">my_feed</td><td style="padding:8px; text-align:center;">10</td><td style="padding:8px; text-align:center;">2</td><td style="padding:8px; text-align:center;">8</td></tr>
<tr style="font-weight:bold; background:#f9f9f9;"><td style="padding:8px;">Total</td><td style="padding:8px; text-align:center;">50</td><td style="padding:8px; text-align:center;">38</td><td style="padding:8px; text-align:center;">12</td></tr>
</table>

<h3>🎯 Scoring Results</h3>
<table style="width:100%; border-collapse:collapse; margin-bottom:20px;">
<tr style="background:#f0f0f0;">
  <th style="padding:8px;">Outcome</th><th style="padding:8px;">Count</th>
</tr>
<tr><td style="padding:8px;">Drafted (≥12)</td><td style="padding:8px; text-align:center;"><strong>1</strong></td></tr>
<tr><td style="padding:8px;">Scored (8–11)</td><td style="padding:8px; text-align:center;">9</td></tr>
<tr><td style="padding:8px;">Scored (below 8)</td><td style="padding:8px; text-align:center;">18</td></tr>
<tr><td style="padding:8px;">Skipped (hard rules)</td><td style="padding:8px; text-align:center;">10</td></tr>
</table>

<h3>🚀 ClickUp Push</h3>
<table style="width:100%; border-collapse:collapse; margin-bottom:20px;">
<tr style="background:#f0f0f0;">
  <th style="padding:8px;">List</th><th style="padding:8px;">Tasks Created</th>
</tr>
<tr><td style="padding:8px;">🔥 Hot</td><td style="padding:8px; text-align:center;">61</td></tr>
<tr><td style="padding:8px;">📋 Standard</td><td style="padding:8px; text-align:center;">179</td></tr>
<tr><td style="padding:8px;">📌 Low Priority</td><td style="padding:8px; text-align:center;">24</td></tr>
<tr style="font-weight:bold; background:#f9f9f9;"><td style="padding:8px;">Total</td><td style="padding:8px; text-align:center;">264</td></tr>
</table>
<p style="color:#666; font-size:12px;">Note: 113 legacy DB rows with old breakdown format failed to push (pre-existing data issue — needs separate fix). Today's 38 new jobs all processed correctly.</p>

<h3>⚠️ Warnings</h3>
<ul>
  <li>Reaper (board_cleanup.py) crashed: <code>TypeError: can't subtract offset-naive and offset-aware datetimes</code> — pre-existing bug, needs fix.</li>
  <li>113 legacy rows in clickup_push failed with breakdown format errors — separate backlog cleanup needed.</li>
  <li>Job 2065300627006621171 (Airtable Automations, score 8) explicitly requests Philippines-based freelancer — flagged for awareness.</li>
</ul>

<p style="color:#888; font-size:11px;">Run completed · June 12, 2026 · Upwork Scanner v4.14</p>
</body>
</html>"""

with open("/Users/abhijeet/Desktop/Etherwise/scripts/scan_digest.html", "w") as f:
    f.write(HTML)

result = subprocess.run(
    [sys.executable, "/Users/abhijeet/Desktop/Etherwise/scripts/send_email.py",
     "--to", "contact@etherwise.io",
     "--subject", "Upwork scan: 38 new, 1 hot",
     "--html-body-file", "/Users/abhijeet/Desktop/Etherwise/scripts/scan_digest.html"],
    capture_output=True, text=True,
    cwd="/Users/abhijeet/Desktop/Etherwise"
)
print(f"Exit: {result.returncode}")
print(result.stdout[:500])
if result.stderr:
    print("[STDERR]", result.stderr[:300])
