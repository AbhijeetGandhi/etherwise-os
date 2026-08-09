"""IMAP COLLECT rail — the inboxes the OAuth app cannot reach (Jarvis sweep).

contact@ stays on the Gmail API (OAuth). The personal inboxes (01/02) are read
over IMAP with per-account app passwords, because the OAuth client is registered
Internal and can only ever authorize etherwise.io accounts. No token expiry, no
Google verification — the same mechanism already proven for SMTP.

This module is the IMAP TRANSPORT only. Classification, the noise/self gate, the
metadata-first discipline, and the event shape all come from mail_pipeline —
identical to the Gmail API rail, so the two readers can never diverge.

READ-ONLY BY CONSTRUCTION: the mailbox is opened with select(readonly=True) and
every fetch uses BODY.PEEK (never sets \\Seen). The rail issues only SEARCH and
FETCH — never STORE, EXPUNGE, APPEND, or COPY. A test asserts this against a fake
IMAP server whose surface omits every mutating verb.

BOUNDED BY CONSTRUCTION: every network call has a socket timeout
(config.JARVIS_IMAP_TIMEOUT_SECONDS) and the fetch is bounded (max_items). A rail
that can block forever eventually hangs the sweep, so the timeout is a hard rule,
not a nicety. Live verification lives ONLY in bin/jarvis-imap-check (opt-in); this
module's tests never open a real socket.
"""
from __future__ import annotations

import imaplib
import json
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Optional

from core import config
from modules.jarvis import mail_pipeline
from modules.knowledge import email_poll as ep

SOURCE = "email"                       # same source_type as the Gmail API rail
MAILBOX = "INBOX"


class InboxSkip(Exception):
    """Raised when an inbox cannot be read for a benign, expected reason (no app
    password configured). The sweep logs it and moves on — never a crash."""


# ── credentials (read at runtime; NEVER logged / echoed / prompted) ───────────
def app_password(inbox: str) -> Optional[str]:
    """The app password for `inbox` from GMAIL_APP_PASSWORDS_FILE, or None if the
    file or entry is absent. The value is never returned to a log or a prompt."""
    try:
        data = json.loads(config.GMAIL_APP_PASSWORDS_FILE.read_text())
    except (OSError, ValueError):
        return None
    return (data or {}).get(inbox)


# ── transport (injectable) ────────────────────────────────────────────────────
def _connect(host: str, inbox: str, password: str,
             timeout: float = config.JARVIS_IMAP_TIMEOUT_SECONDS):
    # timeout bounds the connect AND every later SEARCH/FETCH: it is set on the
    # socket at connect and persists on the SSL-wrapped socket for reads. Without
    # it a stalled server hangs the whole sweep forever (this rail's cardinal sin).
    client = imaplib.IMAP4_SSL(host, timeout=timeout)
    client.login(inbox, password)
    return client


def _uidvalidity(client, mailbox: str = MAILBOX) -> int:
    typ, data = client.status(mailbox, "(UIDVALIDITY)")
    raw = data[0].decode() if data and isinstance(data[0], bytes) else str(
        data[0] if data else "")
    m = re.search(r"UIDVALIDITY\s+(\d+)", raw)
    return int(m.group(1)) if m else 0


def _search(client, last_uid: Optional[int], first_fetch_days: Optional[int],
            now: Optional[datetime]) -> list[int]:
    """UID list to consider. With a cursor: the UID range strictly above it. Cold
    start: date-bounded to the last N days (never a full-mailbox vacuum)."""
    if last_uid is not None:
        typ, data = client.uid("SEARCH", None, "UID", f"{last_uid + 1}:*")
    elif first_fetch_days:
        since = (now or datetime.now(timezone.utc)) - timedelta(days=first_fetch_days)
        typ, data = client.uid("SEARCH", None, "SINCE", since.strftime("%d-%b-%Y"))
    else:
        typ, data = client.uid("SEARCH", None, "ALL")
    raw = data[0] if data else b""
    if isinstance(raw, bytes):
        raw = raw.decode()
    return [int(x) for x in raw.split()] if raw else []


def _literal(fetch_data) -> bytes:
    """The message bytes from an imaplib FETCH response (the tuple's payload)."""
    for item in fetch_data or []:
        if isinstance(item, tuple) and len(item) >= 2:
            return item[1] or b""
    return b""


def _fetch_headers(client, uid: int) -> Message:
    """Headers only, with PEEK (no body pulled, \\Seen untouched) — the cheap
    classify fetch, exactly mirroring the Gmail rail's format=metadata."""
    typ, data = client.uid(
        "FETCH", str(uid),
        "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
    return message_from_bytes(_literal(data))


def _fetch_body(client, uid: int) -> Message:
    typ, data = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
    return message_from_bytes(_literal(data))


# ── parsing helpers ───────────────────────────────────────────────────────────
def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, "replace")


def _message_text(msg: Message) -> str:
    """text/plain body, falling back to tag-stripped HTML (mirrors the Gmail
    rail's _decode_body: plain preferred, html as a last resort)."""
    if msg.is_multipart():
        plains, htmls = [], []
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                plains.append(_decode_part(part))
            elif ctype == "text/html":
                htmls.append(_decode_part(part))
        text = "\n".join(p for p in plains if p)
        if text:
            return text
        return re.sub(r"<[^>]+>", " ", "\n".join(htmls))
    body = _decode_part(msg)
    if msg.get_content_type() == "text/html":
        return re.sub(r"<[^>]+>", " ", body)
    return body


def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _strip_msgid(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("<") and s.endswith(">"):
        s = s[1:-1]
    return s.strip()


def permalink(inbox: str, msgid: str) -> str:
    """One-click-to-source deep link built from the RFC822 Message-ID, scoped to
    the right account via authuser. Empty when the message carries no Message-ID."""
    if not msgid:
        return ""
    return ("https://mail.google.com/mail/u/?authuser="
            f"{inbox}#search/rfc822msgid:{urllib.parse.quote(msgid)}")


# ── cursor (per inbox, UIDVALIDITY-guarded) ───────────────────────────────────
def _cursor_name(inbox: str) -> str:
    return f"jarvis_imap_{ep.sanitize_inbox(inbox)}_uid"


def _parse_cursor(cursor: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """'<uidvalidity>:<max uid>' -> (validity, uid). A bare int is a legacy uid."""
    if not cursor:
        return (None, None)
    try:
        if ":" in cursor:
            v, u = cursor.split(":", 1)
            return (int(v), int(u))
        return (None, int(cursor))
    except ValueError:
        return (None, None)


def read_cursor(conn, inbox: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM sync_cursors WHERE name=?",
                       (_cursor_name(inbox),)).fetchone()
    return row["value"] if row else None


def write_cursor(conn, inbox: str, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_cursors (name, value, updated_at) VALUES"
        " (?,?,datetime('now')) ON CONFLICT(name) DO UPDATE SET"
        " value=excluded.value, updated_at=datetime('now')",
        (_cursor_name(inbox), value))


# ── collect ───────────────────────────────────────────────────────────────────
def collect(inbox: str, cursor: Optional[str], *, max_items: int,
            host: Optional[str] = None, first_fetch_days: Optional[int] = None,
            conn_factory=_connect, pw_fn=app_password,
            now: Optional[datetime] = None) -> tuple[list[dict], Optional[str]]:
    """Return (normalized events, new_cursor) for one inbox. Read-only IMAP,
    metadata-first via mail_pipeline.assemble (body pulled ONLY for a message that
    passes the noise gate). Raises InboxSkip when no app password is configured."""
    pw = pw_fn(inbox)
    if not pw:
        raise InboxSkip(f"no app password for {inbox}")
    host = host or config.JARVIS_IMAP_HOST
    stem = ep.sanitize_inbox(inbox)
    client = conn_factory(host, inbox, pw)
    try:
        client.select(MAILBOX, readonly=True)
        validity = _uidvalidity(client)
        prev_validity, last_uid = _parse_cursor(cursor)
        if last_uid is not None and prev_validity != validity:
            last_uid = None            # mailbox recreated -> re-fetch by date, never skip

        uids = _search(client, last_uid, first_fetch_days, now)
        if last_uid is not None:
            uids = [u for u in uids if u > last_uid]   # 'n:*' always returns >=1
        uids = sorted(set(uids))[:max_items]           # oldest-first, bounded

        out: list[dict] = []
        max_uid = last_uid or 0
        for uid in uids:
            hdr = _fetch_headers(client, uid)
            frm = hdr.get("From", "")
            subject = hdr.get("Subject", "")
            ts = _to_iso(_parse_date(hdr.get("Date", "")))
            msgid = _strip_msgid(hdr.get("Message-ID") or hdr.get("Message-Id") or "")
            native_id = f"{stem}:{msgid}" if msgid else f"{stem}:uid:{uid}"
            link = permalink(inbox, msgid)
            raw_ref = f"imap/{inbox}/{uid}"

            def _body(uid=uid, frm=frm, subject=subject, ts=ts):
                body = _message_text(_fetch_body(client, uid))
                return {"text": f"From: {frm}\nSubject: {subject}\n\n{body}",
                        "ts": ts}

            out.append(mail_pipeline.assemble(
                SOURCE, native_id, frm, subject, ts, link, raw_ref, _body))
            max_uid = max(max_uid, uid)

        new_cursor = f"{validity}:{max_uid}" if max_uid else cursor
        return out, new_cursor
    finally:
        try:
            client.logout()
        except Exception:
            pass
