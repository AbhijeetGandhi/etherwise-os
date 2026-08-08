"""Gmail COLLECT stage for the Jarvis sweep (contact@) — J1 §4.2/§4.3 stage 1.

Read-only Gmail API, metadata-first (architecture §4.2 email hardening): list new
message ids since the cursor, fetch HEADERS ONLY to classify against a noise
blocklist, and pull the full body ONLY for messages that pass — cutting the
prompt-injection surface (and the token bill) by not loading bulk/automated mail.

The classify gate + event shape live in modules.jarvis.mail_pipeline, shared with
the IMAP rail so their filtering can never diverge. This module is the Gmail-API
TRANSPORT only: token, HTTP list/meta/full, and the internalDate cursor. HTTP is
injectable (_list/_meta/_full) so all logic is covered without a live token.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from modules.jarvis import mail_pipeline
from modules.knowledge import email_poll as ep

API = ep.API
CURSOR = "jarvis_gmail_contact_epoch"     # distinct from the knowledge poller's cursor
SOURCE = "email"

# Re-exports: the classify gate is shared. bin/jarvis-reclassify-self-noise and
# any other caller keep working against gmail_rail.* unchanged.
self_noise_reason = mail_pipeline.self_noise_reason
noise_reason = mail_pipeline.noise_reason


def access_token(inbox: str | None = None) -> str:
    """Inbox-aware token accessor for the sweep. Defaults to contact@ (the live
    rail) — delegates to email_poll so every caller shares one resolver + refresh
    path. Extra inboxes pass their address; contact@ stays on the legacy file."""
    return ep.access_token(inbox)


def permalink(msg_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{msg_id}"


# ── network (injectable) ─────────────────────────────────────────────────────
def _list(token: str, q: str, page_token=None) -> dict:
    return ep._list(token, q, page_token)


def _meta(token: str, msg_id: str) -> dict:
    """Headers only (format=metadata) — the cheap classify fetch."""
    params = urllib.parse.urlencode(
        [("format", "metadata")] + [("metadataHeaders", h)
                                    for h in ("From", "Subject", "Date")])
    req = urllib.request.Request(f"{API}/{msg_id}?{params}",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _full(token: str, msg_id: str) -> dict:
    return ep._get(token, msg_id)


def build_query(after_epoch=None) -> str:
    """All inbound mail since the cursor (the sweep discovers unknown clients too)."""
    q = "in:inbox -in:chats"
    if after_epoch:
        q += f" after:{int(int(after_epoch) / 1000)}"
    return q.strip()


def _header_ts(payload: dict, internal) -> str:
    """Occurred timestamp for classify: internalDate (ms) preferred, else Date:."""
    ts = ep._header(payload, "Date")
    if internal:
        try:
            ts = datetime.fromtimestamp(int(internal) / 1000,
                                        timezone.utc).isoformat()
        except (ValueError, TypeError):
            pass
    return ts


def collect(token: str, cursor: str | None, *, max_items: int,
            _list=_list, _meta=_meta, _full=_full) -> tuple[list[dict], str | None]:
    """Return (normalized events, new_cursor). Events passing the noise gate carry
    `state='held'` + body text; excluded ones carry `state='excluded'` + reason.
    Classification + body-only-on-pass are delegated to mail_pipeline.assemble."""
    q = build_query(cursor)
    out: list[dict] = []
    max_internal = int(cursor) if cursor else 0
    page_token, pages, seen = None, 0, 0
    while pages < ep.PAGE_CAP and seen < max_items:
        pages += 1
        page = _list(token, q, page_token)
        for ref in (page.get("messages") or []):
            if seen >= max_items:
                break
            seen += 1
            mid = ref["id"]
            meta = _meta(token, mid)
            max_internal = max(max_internal, int(meta.get("internalDate") or 0))
            payload = meta.get("payload") or {}
            frm = ep._header(payload, "From")
            subject = ep._header(payload, "Subject")
            ts = _header_ts(payload, meta.get("internalDate"))

            def _body(mid=mid):
                parsed = ep.parse_email(_full(token, mid))
                return {"text": parsed["text"], "ts": parsed["occurred_dt"]}

            out.append(mail_pipeline.assemble(
                SOURCE, mid, frm, subject, ts, permalink(mid),
                f"email/{mid}", _body))
        page_token = page.get("nextPageToken")
        if not page_token:
            break
    new_cursor = str(max_internal) if max_internal else cursor
    return out, new_cursor


def read_cursor(conn) -> str | None:
    row = conn.execute("SELECT value FROM sync_cursors WHERE name=?",
                       (CURSOR,)).fetchone()
    return row["value"] if row else None


def write_cursor(conn, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_cursors (name, value, updated_at) VALUES"
        " (?,?,datetime('now')) ON CONFLICT(name) DO UPDATE SET"
        " value=excluded.value, updated_at=datetime('now')", (CURSOR, value))
