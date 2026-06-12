"""Interrupt-queue drainer (decision #9): critical anomalies -> ONE email.

The runner files failures as anomalies(severity='critical'); this module
drains everything past the cursor into a single interrupt email. Cursor lives
in sync_cursors ('notify_drained_anomaly_id') and advances ONLY after a
successful send, so a dead SMTP retries on the next drain instead of
swallowing interrupts.

System-failure emails are the explicitly interrupt-worthy class (SOUL: calm
by default — ONLY hot leads and system failures interrupt). Everything
warn-level stays for the briefs.

Entry: bin/notify (runner-wrapped) or notify.drain_critical_anomalies().
"""
from __future__ import annotations

import json
import smtplib
import ssl
from email.mime.text import MIMEText
from typing import Callable, Optional

from core import config, db

CURSOR_NAME = "notify_drained_anomaly_id"


def _gmail_creds() -> dict:
    out = {}
    path = config.CREDENTIALS_DIR / "gmail-app-password.txt"
    for line in path.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def send_gmail(subject: str, html: str) -> None:
    creds = _gmail_creds()
    msg = MIMEText(html, "html")
    msg["Subject"] = subject
    msg["From"] = (f"{creds.get('DEFAULT_FROM_NAME', 'Etherwise Bot')}"
                   f" <{creds['GMAIL_ADDRESS']}>")
    msg["To"] = config.NOTIFY_EMAIL
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as smtp:
        smtp.login(creds["GMAIL_ADDRESS"], creds["GMAIL_APP_PASSWORD"])
        smtp.sendmail(creds["GMAIL_ADDRESS"], [config.NOTIFY_EMAIL],
                      msg.as_string())


def _cursor(conn) -> int:
    row = conn.execute("SELECT value FROM sync_cursors WHERE name=?",
                       (CURSOR_NAME,)).fetchone()
    return int(row["value"]) if row else 0


def drain_critical_anomalies(db_path=None,
                             send: Optional[Callable] = None) -> dict:
    send = send or send_gmail
    with db.connect(db_path) as conn:
        last = _cursor(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM anomalies WHERE id > ? AND severity='critical'"
            " ORDER BY id", (last,))]
    if not rows:
        return {"drained": 0}

    items = []
    for r in rows:
        detail = (r.get("detail_json") or "")[:400]
        items.append(
            f"<li><b>{r['kind']}</b> · {r['task_name']} ·"
            f" {r['detected_at']}<br><code>{detail}</code></li>")
    html = (f"<h2>{len(rows)} critical anomalies</h2><ul>"
            + "\n".join(items) + "</ul>"
            "<p>Source: v3 anomalies table (interrupt queue, decision #9)."
            " Investigate, then mark resolved_at.</p>")
    subject = (f"INTERRUPT: {len(rows)} critical"
               f" anomal{'y' if len(rows) == 1 else 'ies'} —"
               f" {rows[-1]['kind']}")

    send(subject, html)   # raises on failure -> cursor untouched, retry later

    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sync_cursors (name, value, updated_at) VALUES"
            " (?, ?, datetime('now')) ON CONFLICT(name) DO UPDATE SET"
            " value=excluded.value, updated_at=excluded.updated_at",
            (CURSOR_NAME, str(rows[-1]["id"])))
    return {"drained": len(rows), "max_id": rows[-1]["id"],
            "kinds": sorted({json.dumps(r["kind"])[1:-1] for r in rows})}
