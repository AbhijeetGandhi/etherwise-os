"""Shared mail pipeline — the ONE classify + assemble path for every mail rail.

The Gmail API rail (modules.jarvis.gmail_rail) and the read-only IMAP rail
(modules.jarvis.imap_rail) differ ONLY in transport: how they list messages,
fetch headers, and fetch bodies. Everything else — the noise/self-noise gate,
the metadata-first discipline (classify on headers; fetch the body ONLY for a
message that passes), and the normalized event shape — lives here so the two
code paths can never drift. (Divergence between two readers of the same kind of
data is exactly the bug class J1 spent a night killing; one pipeline forecloses
it.)

A rail hands classify()/assemble() plain header strings (From, Subject, ts) plus
a lazy body thunk. The thunk is invoked ONLY when the message passes the gate, so
noise never costs a body fetch or a token — the same economics on every transport.
"""
from __future__ import annotations

from email.utils import parseaddr
from typing import Callable, Optional

from core import config
from modules.jarvis import events
from modules.jarvis import sender_rules as SR
from modules.knowledge import email_poll as ep

# ── noise blocklist (metadata-first gate) ────────────────────────────────────
# Automated/bulk senders + subjects that are structurally NOT a client 1:1
# message. Kept conservative: a false-exclude only means an item won't reach the
# queue this sweep; a real client thread never matches these.
_NOISE_SENDER_SUBSTR = (
    "no-reply", "noreply", "no_reply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "bounce", "notifications@", "notification@",
    "notify@", "newsletter", "digest@", "updates@", "mailer@", "alerts@",
    "alert@", "automated", "auto-confirm", "receipts@", "billing@", "invoice@",
    # marketing/outreach roles are not a client contact (2026-08-07)
    "marketing@", "productmarketing@", "make-events@", "naukritalentcloud@",
)
_NOISE_SENDER_DOMAINS = (
    "google.com", "accounts.google.com", "github.com", "linkedin.com",
    "slack.com", "stripe.com", "mailchimp.com", "sendgrid.net", "substack.com",
    "notion.so", "calendly.com", "atlassian.net", "intercom.io", "zoom.us",
    "docusign.net", "quickbooks.com", "amazonses.com",
    # SaaS product/newsletter blasts that leaked into 'held' (2026-08-07)
    "neon.tech", "instantly.ai", "getaiperks.com", "smartlead-team.com",
    "mail.airtable.com", "denave.com",
    # dev-platform + recruitment blasts still landing in 'excluded' (2026-08-08).
    # render.com covers dx@render.com (no-reply@render.com already caught by the
    # no-reply substr); naukri.com covers all @naukri.com, not just naukritalentcloud@
    "render.com", "naukri.com",
)
_NOISE_SUBJECT_SUBSTR = (
    "unsubscribe", "your receipt", "payment received", "verify your email",
    "sign-in", "sign in to", "security alert", "your invoice", "newsletter",
    "weekly digest", "[automated]", "password reset", "confirm your",
)


def self_noise_reason(from_header: str, from_addr: str, subject: str) -> Optional[str]:
    """Our OWN automation output — excluded before any body fetch or token spend.
    Matches our sending identities (address, @etherwise.io domain, or the bot
    display name in the raw From) and our template subjects. Config-driven so a
    new system email is one line in config, never a code change."""
    frm = (from_header or "").lower()
    a = (from_addr or "").lower()
    dom = ep.sender_domain(from_header) or (a.rsplit("@", 1)[-1] if "@" in a else "")
    for s in config.SELF_SENDERS:
        if s == a or s in frm:
            return f"self-noise:sender:{s}"
    for o in config.OWNER_IDENTITIES:          # our own people are not clients
        if o == a:
            return f"self-noise:owner:{o}"
    for d in config.SELF_SENDER_DOMAINS:
        if dom == d or dom.endswith("." + d):
            return f"self-noise:domain:{dom}"
    subj = (subject or "").lower()
    for p in config.SELF_SUBJECT_PATTERNS:
        if p in subj:
            return f"self-noise:subject:{p}"
    return None


def noise_reason(from_addr: str, subject: str) -> Optional[str]:
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


def classify(source: str, native_id: str, from_header: str, subject: str,
             ts: Optional[str], permalink: str, raw_ref: str) -> dict:
    """Header-level normalization + noise gate — transport-agnostic. Returns a
    partial event dict (no body yet); `_pass` False => excluded, `_reason` set.

    Order, and why: SELF-noise first (our own automation is ours whatever it
    looks like), then TRANSACTIONAL protection, then the generic noise gate. The
    middle step is not decoration — `_NOISE_SENDER_DOMAINS` holds `docusign.net`
    and `_NOISE_SENDER_SUBSTR` holds `notifications@`, so without it a signature
    request on a live contract is dropped at the door, before a body is ever
    fetched, and leaves no queue row for anyone to find. `sender_rules` owns the
    list because that is where the same protection has to hold for the
    retroactive path (modules/jarvis/resweep.py); one list, two callers, no
    drift.
    """
    _, addr = parseaddr(from_header or "")
    reason = self_noise_reason(from_header, addr, subject)
    protected = None
    if reason is None:
        protected = SR.transactional_reason(addr or from_header or "")
        if not protected:
            reason = noise_reason(addr or from_header, subject)
    return {
        "id": events.canonical_id(source, native_id),
        "source": source, "native_id": native_id, "ts": ts,
        "author": addr or from_header, "subject": subject,
        "permalink": permalink, "raw_ref": raw_ref,
        "_pass": reason is None, "_reason": reason, "_from": from_header,
        "_protected": protected,
    }


def assemble(source: str, native_id: str, from_header: str, subject: str,
             ts: Optional[str], permalink: str, raw_ref: str,
             fetch_body: Callable[[], dict]) -> dict:
    """Classify on headers, then — ONLY if the message passes — call `fetch_body`
    (a transport thunk returning {'text': str, 'ts': optional}). A passing event
    carries state='held' + body text; an excluded one carries state='excluded' +
    reason and NEVER triggers the body fetch (metadata-first: no token, no read)."""
    ev = classify(source, native_id, from_header, subject, ts, permalink, raw_ref)
    if not ev["_pass"]:
        ev["state"] = "excluded"
        ev["exclude_reason"] = ev["_reason"]
        ev["text"] = None
    else:
        body = fetch_body() or {}
        ev["state"] = "held"
        ev["text"] = body.get("text")
        ev["ts"] = ev["ts"] or body.get("ts")
    return ev
