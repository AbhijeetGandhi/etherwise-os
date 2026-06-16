"""Gmail puller (M3 Phase 4). Reads client-relevant mail ONLY — gated by SENDER
DOMAIN matched to the roster's company domains (clients.domain_map). Never
vacuums personal mail: a sender whose domain isn't a roster company domain is
skipped. Drops each matched message as knowledge-inbox/email/{date}__{id}.json
for the M3 inbox pipeline (parse_email -> classify -> extract).

GATED: the Gmail OAuth scope + consent step (and the Google Cloud OAuth app that
issues client_id/secret) need Abhijeet in the browser — see bin/gmail-auth. This
module READS an existing token (.credentials/gmail-api.json) like the Upwork
pattern; it never runs consent. HTTP is injectable (_list/_get) for tests, so
all parsing + filtering logic is covered without a live token.
"""
from __future__ import annotations

import base64
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parseaddr

from core import config, db, runner
from modules.knowledge import clients

API = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
CURSOR = "gmail_after_epoch"      # internalDate (ms) high-water-mark
TASK_NAME = "email_poll"
PAGE_CAP = 20
TOKEN_FILE = config.CREDENTIALS_DIR / "gmail-api.json"


# ── pure parsing + filtering (no network) ─────────────────────────────────────

def sender_domain(from_header: str) -> str:
    """Domain part of a From: header ('Jane <jane@acme.com>' -> 'acme.com')."""
    _, addr = parseaddr(from_header or "")
    return addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""


def match_client(from_header: str, dmap: dict):
    """Return client_id if the sender's domain is a roster company domain,
    else None (the privacy gate — personal/unknown domains are excluded)."""
    return dmap.get(sender_domain(from_header))


def _header(payload: dict, name: str) -> str:
    for h in (payload.get("headers") or []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_body(payload: dict) -> str:
    """Recursively pull the text/plain body from a Gmail message payload."""
    mime = payload.get("mimeType", "")
    body = payload.get("body") or {}
    if mime == "text/plain" and body.get("data"):
        return base64.urlsafe_b64decode(body["data"] + "===").decode(
            "utf-8", "replace")
    out = []
    for part in (payload.get("parts") or []):
        out.append(_decode_body(part))
    text = "\n".join(t for t in out if t)
    if text:
        return text
    # fallback: any body data (e.g. text/html only) stripped of tags
    if body.get("data"):
        raw = base64.urlsafe_b64decode(body["data"] + "===").decode(
            "utf-8", "replace")
        return re.sub(r"<[^>]+>", " ", raw)
    return ""


def parse_email(msg: dict) -> dict:
    """Gmail API message JSON -> normalized source dict (matches parse_fathom's
    shape: participants drive classification, text is the body)."""
    payload = msg.get("payload") or {}
    frm = _header(payload, "From")
    subject = _header(payload, "Subject")
    date_h = _header(payload, "Date")
    internal = msg.get("internalDate")
    occurred = date_h
    if internal:
        try:
            occurred = datetime.fromtimestamp(
                int(internal) / 1000, timezone.utc).isoformat()
        except (ValueError, TypeError):
            pass
    body = _decode_body(payload) or (msg.get("snippet") or "")
    _, addr = parseaddr(frm)
    return {
        "source_type": "email",
        "source_ref": f"email/{msg.get('id')}",
        "title": subject,
        "occurred_dt": occurred,
        "participants": [addr] if addr else [],
        "text": f"From: {frm}\nSubject: {subject}\n\n{body}",
        "url": None,
    }


def build_query(dmap: dict, after_epoch=None) -> str:
    """Gmail search q: from any roster company domain, after the cursor."""
    froms = " OR ".join(f"from:@{d}" for d in sorted(dmap))
    q = f"({froms})" if froms else ""
    if after_epoch:
        q += f" after:{int(int(after_epoch) / 1000)}"
    return q.strip()


# ── token (read-only; consent is GATED) ───────────────────────────────────────

def access_token() -> str:
    """Read (refreshing if needed) the Gmail token. Raises a clear, actionable
    error if consent hasn't been run — never attempts consent itself."""
    if not TOKEN_FILE.exists():
        raise RuntimeError(
            "Gmail not connected. The OAuth consent step is GATED — run "
            "bin/gmail-auth with Abhijeet (needs a Google Cloud OAuth app + "
            "browser consent). This poller only reads an existing token.")
    tok = json.loads(TOKEN_FILE.read_text())
    if tok.get("access_token") and not _expired(tok):
        return tok["access_token"]
    return _refresh(tok)


def _expired(tok: dict) -> bool:
    exp = tok.get("expiry")
    if not exp:
        return True
    try:
        return datetime.now(timezone.utc) >= datetime.fromisoformat(exp)
    except ValueError:
        return True


def _refresh(tok: dict) -> str:
    data = urllib.parse.urlencode({
        "client_id": tok["client_id"], "client_secret": tok["client_secret"],
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token",
                                 data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        fresh = json.loads(resp.read().decode())
    tok["access_token"] = fresh["access_token"]
    from datetime import timedelta
    tok["expiry"] = (datetime.now(timezone.utc)
                     + timedelta(seconds=fresh.get("expires_in", 3000) - 60)
                     ).isoformat()
    TOKEN_FILE.write_text(json.dumps(tok))
    return tok["access_token"]


# ── network (injectable) ──────────────────────────────────────────────────────

def _list(token: str, q: str, page_token=None) -> dict:
    params = {"q": q, "maxResults": "100"}
    if page_token:
        params["pageToken"] = page_token
    req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _get(token: str, msg_id: str) -> dict:
    req = urllib.request.Request(
        f"{API}/{msg_id}?format=full",
        headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


# ── poll ──────────────────────────────────────────────────────────────────────

def poll(ctx, _list=_list, _get=_get, _token=access_token) -> dict:
    dbp = ctx.db_path or config.DB_PATH
    dmap = clients.domain_map(clients.load_client_map(dbp))
    if not dmap:
        raise runner.TaskSkip("no company domains in roster — nothing to pull")
    with db.connect(dbp) as conn:
        row = conn.execute("SELECT value FROM sync_cursors WHERE name=?",
                           (CURSOR,)).fetchone()
    hwm = row["value"] if row else None

    inbox = config.KNOWLEDGE_INBOX / "email"
    inbox.mkdir(parents=True, exist_ok=True)
    token = _token()
    q = build_query(dmap, hwm)

    saved = skipped = excluded = 0
    max_internal = int(hwm) if hwm else 0
    page_token, pages = None, 0
    while pages < PAGE_CAP:
        pages += 1
        page = _list(token, q, page_token)
        for ref in (page.get("messages") or []):
            msg = _get(token, ref["id"])
            frm = _header(msg.get("payload") or {}, "From")
            if not match_client(frm, dmap):     # privacy gate (belt + braces)
                excluded += 1
                continue
            internal = int(msg.get("internalDate") or 0)
            max_internal = max(max_internal, internal)
            date = (parse_email(msg)["occurred_dt"] or "unknown")[:10]
            path = inbox / f"{date}__{msg['id']}.json"
            if path.exists():
                skipped += 1
            else:
                path.write_text(json.dumps(msg, indent=1, default=str))
                saved += 1
        page_token = page.get("nextPageToken")
        if not page_token:
            break

    if max_internal and str(max_internal) != hwm:
        with db.connect(dbp) as conn:
            conn.execute(
                "INSERT INTO sync_cursors (name, value, updated_at) VALUES"
                " (?,?,datetime('now')) ON CONFLICT(name) DO UPDATE SET"
                " value=excluded.value, updated_at=datetime('now')",
                (CURSOR, str(max_internal)))

    if saved == 0 and skipped == 0:
        raise runner.TaskSkip("no new client emails")
    return {"saved": saved, "deduped": skipped, "excluded_personal": excluded,
            "pages": pages, "polled_at": datetime.now(timezone.utc).isoformat()}


def main() -> int:
    result = runner.run_task(TASK_NAME, poll, module="knowledge")
    print(json.dumps({"run_id": result.run_id, "status": result.status,
                      "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
