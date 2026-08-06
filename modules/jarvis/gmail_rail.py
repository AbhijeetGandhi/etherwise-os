"""Gmail COLLECT stage for the Jarvis sweep (contact@) — J1 §4.2/§4.3 stage 1.

Read-only Gmail API, metadata-first (architecture §4.2 email hardening): list new
message ids since the cursor, fetch HEADERS ONLY to classify against a noise
blocklist, and pull the full body ONLY for messages that pass — cutting the
prompt-injection surface (and the token bill) by not loading bulk/automated mail.

Reuses the tested token + HTTP seams from modules.knowledge.email_poll; HTTP is
injectable (_list/_meta/_full) so all logic is covered without a live token.
Produces normalized, un-stored event dicts — the sweep stores + maps them.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from core import config
from modules.jarvis import events
from modules.knowledge import email_poll as ep

API = ep.API
CURSOR = "jarvis_gmail_contact_epoch"     # distinct from the knowledge poller's cursor
SOURCE = "email"

# ── noise blocklist (metadata-first gate) ────────────────────────────────────
# Automated/bulk senders + subjects that are structurally NOT a client 1:1
# message. Kept conservative: a false-exclude only means an item won't reach the
# queue this sweep; a real client thread never matches these.
_NOISE_SENDER_SUBSTR = (
    "no-reply", "noreply", "no_reply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "bounce", "notifications@", "notification@",
    "notify@", "newsletter", "digest@", "updates@", "mailer@", "alerts@",
    "alert@", "automated", "auto-confirm", "receipts@", "billing@", "invoice@",
)
_NOISE_SENDER_DOMAINS = (
    "google.com", "accounts.google.com", "github.com", "linkedin.com",
    "slack.com", "stripe.com", "mailchimp.com", "sendgrid.net", "substack.com",
    "notion.so", "calendly.com", "atlassian.net", "intercom.io", "zoom.us",
    "docusign.net", "quickbooks.com", "amazonses.com",
)
_NOISE_SUBJECT_SUBSTR = (
    "unsubscribe", "your receipt", "payment received", "verify your email",
    "sign-in", "sign in to", "security alert", "your invoice", "newsletter",
    "weekly digest", "[automated]", "password reset", "confirm your",
)


def noise_reason(from_addr: str, subject: str) -> str | None:
    a = (from_addr or "").lower()
    dom = ep.sender_domain(from_addr)
    for s in _NOISE_SENDER_SUBSTR:
        if s in a:
            return f"noise:sender:{s}"
    for d in _NOISE_SENDER_DOMAINS:
        if dom == d or dom.endswith("." + d):
            return f"noise:domain:{dom}"
    subj = (subject or "").lower()
    for s in _NOISE_SUBJECT_SUBSTR:
        if s in subj:
            return f"noise:subject:{s}"
    return None


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


def _classify(msg_meta: dict, msg_id: str) -> dict:
    """Header-level normalization + noise gate. Returns a partial event dict
    (no body yet); `_pass` False => excluded."""
    payload = msg_meta.get("payload") or {}
    frm = ep._header(payload, "From")
    subject = ep._header(payload, "Subject")
    date_h = ep._header(payload, "Date")
    internal = msg_meta.get("internalDate")
    ts = date_h
    if internal:
        try:
            ts = datetime.fromtimestamp(int(internal) / 1000,
                                        timezone.utc).isoformat()
        except (ValueError, TypeError):
            pass
    from email.utils import parseaddr
    _, addr = parseaddr(frm)
    reason = noise_reason(addr or frm, subject)
    return {
        "id": events.canonical_id(SOURCE, msg_id),
        "source": SOURCE, "native_id": msg_id, "ts": ts,
        "author": addr or frm, "subject": subject,
        "permalink": permalink(msg_id), "raw_ref": f"email/{msg_id}",
        "_pass": reason is None, "_reason": reason, "_from": frm,
    }


def collect(token: str, cursor: str | None, *, max_items: int,
            _list=_list, _meta=_meta, _full=_full) -> tuple[list[dict], str | None]:
    """Return (normalized events, new_cursor). Events passing the noise gate carry
    `state='held'` + body text; excluded ones carry `state='excluded'` + reason."""
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
            meta = _meta(token, ref["id"])
            max_internal = max(max_internal, int(meta.get("internalDate") or 0))
            ev = _classify(meta, ref["id"])
            if not ev["_pass"]:
                ev["state"] = "excluded"
                ev["exclude_reason"] = ev["_reason"]
                ev["text"] = None
            else:
                full = _full(token, ref["id"])
                parsed = ep.parse_email(full)
                ev["state"] = "held"
                ev["text"] = parsed["text"]
                ev["ts"] = ev["ts"] or parsed["occurred_dt"]
            out.append(ev)
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
