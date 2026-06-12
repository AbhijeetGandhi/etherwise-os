#!/usr/bin/env python3
"""
send_email.py — Send email via Gmail SMTP using an App Password.

Used by scheduled tasks to skip the flaky "navigate to Gmail UI + click Send" step.
Reads credentials from ~/Desktop/Etherwise/.credentials/gmail-app-password.txt

Usage:
    python3 send_email.py \
        --to contact@etherwise.io \
        --subject "Daily Morning Briefing — 2026-05-18" \
        --html-body-file /tmp/briefing.html \
        [--text-body-file /tmp/briefing.txt] \
        [--cc someone@x.com] \
        [--bcc backup@x.com] \
        [--from-name "Etherwise Bot"]

Exits 0 on success, 1 on failure. Prints status to stdout.

Credentials file format (one key=value per line, mode 600):
    GMAIL_ADDRESS=contact@etherwise.io
    GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx
    DEFAULT_FROM_NAME=Etherwise Bot  # optional
"""

import argparse
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path


CREDS_PATH = Path.home() / "Desktop" / "Etherwise" / ".credentials" / "gmail-app-password.txt"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def load_credentials():
    if not CREDS_PATH.exists():
        sys.stderr.write(f"ERROR: credentials file not found: {CREDS_PATH}\n")
        sys.stderr.write("Create it with the format:\n  GMAIL_ADDRESS=contact@etherwise.io\n  GMAIL_APP_PASSWORD=<16-char-app-password>\n")
        sys.exit(1)

    creds = {}
    with CREDS_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip()

    if "GMAIL_ADDRESS" not in creds or "GMAIL_APP_PASSWORD" not in creds:
        sys.stderr.write("ERROR: credentials file must contain GMAIL_ADDRESS and GMAIL_APP_PASSWORD\n")
        sys.exit(1)

    # Validate perms — refuse to use a world-readable creds file
    try:
        mode = CREDS_PATH.stat().st_mode & 0o777
        if mode != 0o600:
            sys.stderr.write(f"WARNING: credentials file mode is {oct(mode)}, should be 0o600. Fix: chmod 600 {CREDS_PATH}\n")
    except Exception:
        pass

    return creds


def read_body(path_str):
    if not path_str:
        return None
    p = Path(path_str)
    if not p.exists():
        sys.stderr.write(f"ERROR: body file not found: {p}\n")
        sys.exit(1)
    return p.read_text()


def main():
    ap = argparse.ArgumentParser(description="Send email via Gmail SMTP")
    ap.add_argument("--to", required=True, action="append", help="Recipient (repeatable)")
    ap.add_argument("--cc", action="append", default=[], help="CC recipient (repeatable)")
    ap.add_argument("--bcc", action="append", default=[], help="BCC recipient (repeatable)")
    ap.add_argument("--subject", required=True, help="Email subject")
    ap.add_argument("--html-body-file", help="Path to HTML body file (recommended)")
    ap.add_argument("--text-body-file", help="Path to plain-text body file (fallback)")
    ap.add_argument("--text-body", help="Inline plain-text body (alternative to --text-body-file)")
    ap.add_argument("--from-name", help="Override From display name")
    ap.add_argument("--reply-to", help="Reply-To header")
    args = ap.parse_args()

    creds = load_credentials()
    from_addr = creds["GMAIL_ADDRESS"]
    from_name = args.from_name or creds.get("DEFAULT_FROM_NAME") or "Etherwise"

    # Build message
    msg = EmailMessage()
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = ", ".join(args.to)
    if args.cc:
        msg["Cc"] = ", ".join(args.cc)
    msg["Subject"] = args.subject
    if args.reply_to:
        msg["Reply-To"] = args.reply_to

    # Bodies
    html_body = read_body(args.html_body_file) if args.html_body_file else None
    text_body = read_body(args.text_body_file) if args.text_body_file else args.text_body

    if not html_body and not text_body:
        sys.stderr.write("ERROR: provide --html-body-file and/or --text-body-file/--text-body\n")
        sys.exit(1)

    if text_body and html_body:
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")
    elif html_body:
        # Generate a minimal text fallback
        import re
        text_fallback = re.sub(r"<[^>]+>", "", html_body)
        text_fallback = re.sub(r"\s+", " ", text_fallback).strip()
        msg.set_content(text_fallback)
        msg.add_alternative(html_body, subtype="html")
    else:
        msg.set_content(text_body)

    # All recipients including BCC for the actual envelope
    all_recipients = list(args.to) + list(args.cc) + list(args.bcc)

    # Send via SMTP
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(from_addr, creds["GMAIL_APP_PASSWORD"])
            server.send_message(msg, from_addr=from_addr, to_addrs=all_recipients)
    except smtplib.SMTPAuthenticationError as e:
        sys.stderr.write(f"ERROR: SMTP auth failed (check app password): {e}\n")
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"ERROR: SMTP send failed: {e}\n")
        sys.exit(1)

    print(f"OK: sent '{args.subject}' to {', '.join(all_recipients)}")
    sys.exit(0)


if __name__ == "__main__":
    main()
