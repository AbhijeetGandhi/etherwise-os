# Etherwise · Scripts

Utility scripts used by scheduled tasks and ad-hoc workflows.

## `send_email.py` — Gmail SMTP sender

Replaces the flaky "create draft in Gmail API → navigate browser → click Send in UI" pattern that was causing stranded drafts across all scheduled tasks.

### One-time setup

1. **Generate a Gmail App Password** (16 characters):
   - Go to: https://myaccount.google.com/apppasswords
   - Sign in to `contact@etherwise.io`
   - Make sure 2-Step Verification is already enabled (App Passwords require 2SV)
   - In the "App name" field: enter something like `Etherwise Scripts`
   - Click "Create"
   - Copy the 16-character password shown (no spaces — Google sometimes shows them with spaces; remove them)

2. **Save the credentials** (one-time):

   ```bash
   mkdir -p ~/Desktop/Etherwise/.credentials
   chmod 700 ~/Desktop/Etherwise/.credentials

   cat > ~/Desktop/Etherwise/.credentials/gmail-app-password.txt <<'EOF'
   GMAIL_ADDRESS=contact@etherwise.io
   GMAIL_APP_PASSWORD=<paste-the-16-char-password-here-with-no-spaces>
   DEFAULT_FROM_NAME=Etherwise Bot
   EOF

   chmod 600 ~/Desktop/Etherwise/.credentials/gmail-app-password.txt
   ```

3. **Make the script executable** (optional, convenience):

   ```bash
   chmod +x ~/Desktop/Etherwise/scripts/send_email.py
   ```

4. **Test it works** — send yourself a one-off email:

   ```bash
   echo "<h1>Test from send_email.py</h1><p>If you see this, SMTP send is working.</p>" > /tmp/test.html

   python3 ~/Desktop/Etherwise/scripts/send_email.py \
       --to contact@etherwise.io \
       --subject "SMTP send test — $(date)" \
       --html-body-file /tmp/test.html
   ```

   Should print `OK: sent '...' to contact@etherwise.io` and the email should land in your inbox within seconds. If you see an authentication error, double-check the app password (most common gotcha: extra spaces).

### Usage in scheduled tasks

Replace the old "create draft + click Send in UI" flow with a single shell call:

```bash
# 1. Build email body (HTML)
cat > /tmp/digest-$RUN_ID.html <<'EOF'
<!-- your full HTML body, inline-styled per email-template.md -->
EOF

# 2. Send it
python3 /Users/abhijeet/Desktop/Etherwise/scripts/send_email.py \
    --to contact@etherwise.io \
    --subject "Daily Briefing — 2026-05-18" \
    --html-body-file /tmp/digest-$RUN_ID.html

# 3. Check exit code
if [ $? -ne 0 ]; then
    # send failed — fall back to creating a Gmail API draft as backup
    # so the user still has the message accessible
    ...
fi
```

### CLI reference

| Flag | Required | Description |
|---|---|---|
| `--to <addr>` | yes | Recipient email. Pass multiple times for multiple recipients. |
| `--cc <addr>` | no | CC recipient (repeatable). |
| `--bcc <addr>` | no | BCC recipient (repeatable). |
| `--subject <text>` | yes | Email subject. |
| `--html-body-file <path>` | one of these | Path to HTML body file (recommended). |
| `--text-body-file <path>` | one of these | Path to plain-text body file. |
| `--text-body <text>` | one of these | Inline plain-text body. |
| `--from-name <name>` | no | Override From display name. Defaults to `DEFAULT_FROM_NAME` from credentials, else "Etherwise". |
| `--reply-to <addr>` | no | Reply-To header. |

Exit code: `0` on success, `1` on any failure. Errors go to stderr.

### Security notes

- The app password gives full Gmail-send access to `contact@etherwise.io`. Treat it like a real password.
- Credentials file is enforced at mode 600 (owner-read-only). The script warns if perms are wrong.
- **Never** commit `.credentials/` to git. Already in `.gitignore` patterns.
- **Never** paste the app password into chats, docs, or screenshots.
- To revoke: go to https://myaccount.google.com/apppasswords and delete the "Etherwise Scripts" entry. Generate a new one if needed.

### Migration plan for scheduled tasks

Scheduled tasks that currently end with "create draft + click Send in UI" should be migrated to use this script. Tasks to migrate (in order of frequency / value):

1. ☐ `upwork-state-sync` (daily) — most-used; biggest win
2. ☐ `client-comms-sync` (2× daily) — same
3. ☐ `morning-briefing` (daily)
4. ☐ `weekly-sales-report` (weekly)
5. ☐ `monthly-connect-roi-report` (monthly)
6. ☐ `upwork-job-scanner` (hourly when in active window)

Each migration: edit the task's SKILL.md, replace the "Phase X: Email send" block with a bash call to `send_email.py`, keep the optional "also create a Gmail API draft" as a backup (write-only — no UI navigation). Test by running once manually.
