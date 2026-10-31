"""Leads — the bucket the Review queue never had (2026-11-01).

A prospect's first message is not a mapping question ("which client is this?")
and it is not noise. Until now it was treated as the first, so it sat on the
Review board until dismissed — "New inquiry from Hannah Melotto (Melotto
Group)", a website-form lead, waited three days in a queue built for
attribution. This module gives it a row of its own (migration 046), reachable
from three places:

  * the sweep, when a message is structurally a website-form lead
    (mapping.structural_shape -> 'website_lead') or the proposer says
    intent='lead' with no existing client;
  * the Review board's "Lead" action (cockpit.actions.queue_resolve);
  * the resweep, retroactively, for rows already queued.

The event is then FILED, not deleted: state='excluded' with
exclude_reason='filed:lead:<id>' — the same findable-by-reason convention
every other filing uses — so the mapping queue stops carrying it and the
leads table is its home. Nothing here sends anything.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Optional

from modules.jarvis import events

FILE_PREFIX = "filed:lead:"
STATUSES = ("new", "contacted", "call", "proposal", "client", "no")

# "New inquiry from Hannah Melotto (Melotto Group)" — the website form's subject
_FORM_SUBJECT = re.compile(
    r"^\s*new\s+(?:inquiry|enquiry|lead|message|case study lead)\s*(?:from)?\s*:?\s*"
    r"(?P<name>[^(<\n]+?)\s*(?:\((?P<company>[^)]+)\))?\s*$", re.I)
_EMAIL = re.compile(r"[\w.+-]+@([\w-]+\.)+[\w-]+")
# "Cole <cole@boostjet.co>" | "cole@boostjet.co" — the name group exists ONLY
# when an angle-bracket address follows it (a lazy optional name swallowed the
# first letter of a bare address: 'nboarding@resend.dev')
_ADDR = re.compile(r"^\s*(?:\"?(?P<name>[^\"<]+?)\"?\s*<(?P<email>[^<>\s]+@[^<>\s]+)>"
                   r"|(?P<bare>[^<>\s]+@[^<>\s]+))\s*$")
# the form's fields arrive concatenated without newlines:
# "Name: Hannah MelottoEmail: h.melotto@melottogroup.comCompany: Melotto GroupMessage:…"
# lazy TLD + a lookahead for the NEXT capitalised label, because the fields run
# together: "Email: h.melotto@melottogroup.comCompany: Melotto Group"
_FIELD_EMAIL = re.compile(r"Email:\s*([\w.+-]+@[\w-]+(?:\.[a-z0-9-]+)+?)(?=[A-Z\s]|$)")
_FIELD_NAME = re.compile(r"Name:\s*(.+?)\s*(?=Email:|Company:|Message:|Type:|$)", re.I)
_FIELD_COMPANY = re.compile(r"Company:\s*(.+?)\s*(?=Message:|Email:|Type:|$)", re.I)
_NOT_A_COMPANY = {"work", "personal", "other"}
_FREEMAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com",
             "icloud.com", "me.com", "proton.me", "protonmail.com", "aol.com",
             "live.com", "msn.com"}


def _split_author(author: Optional[str]) -> tuple:
    """('Name', 'email') from a From header or bare address."""
    a = (author or "").strip()
    m = _ADDR.match(a)
    if m:
        email = (m.group("email") or m.group("bare") or "").lower()
        return (m.group("name") or "").strip() or None, email or None
    return (a or None), None


def _domain(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[-1].lower()


def _company_from_domain(domain: Optional[str]) -> Optional[str]:
    if not domain or domain in _FREEMAIL:
        return None
    root = domain.split(".")[0] if domain.count(".") == 1 else domain.split(".")[-2]
    return root.replace("-", " ").title() if root else None


def describe(ev: dict) -> dict:
    """name / company / email / domain / source for an events row. The website
    form's own mail arrives FROM the form sender, so the person is read from the
    subject; everything else is read from the From header."""
    subject = (ev.get("subject") or "").strip()
    text = ev.get("text") or ""
    source = ev.get("source") or "email"
    name, email = _split_author(ev.get("author"))
    company = None
    kind = "email" if source == "email" else source
    m = _FORM_SUBJECT.match(subject)
    if m and (ev.get("author") or "").lower().startswith(("onboarding@resend.dev",
                                                          "forms@", "website@",
                                                          "noreply@etherwise")):
        kind = "website_form"
        name = (m.group("name") or "").strip() or None
        company = (m.group("company") or "").strip() or None
        if company and company.lower() in _NOT_A_COMPANY:
            company = None                 # "(Work)" / "(Personal)" is a type, not a firm
        # the form's own fields beat the subject line
        fe, fn, fc = (_FIELD_EMAIL.search(text), _FIELD_NAME.search(text),
                      _FIELD_COMPANY.search(text))
        email = fe.group(1).lower() if fe else None
        if fn and fn.group(1).strip():
            name = fn.group(1).strip()
        if fc and fc.group(1).strip() and fc.group(1).strip().lower() not in _NOT_A_COMPANY:
            company = fc.group(1).strip()
        # a bare address in the subject ("New case study lead: x@y.com") is
        # the person's email, not their name
        if name and "@" in name:
            email, name = (email or name.lower()), None
        if not email:
            found = _EMAIL.search(text)
            if found and not found.group(0).lower().endswith("resend.dev"):
                email = found.group(0).lower()
    domain = _domain(email)
    if not company:
        company = _company_from_domain(domain)
    return {"name": name, "company": company, "email": email, "domain": domain,
            "source": kind}


def from_event(conn: sqlite3.Connection, ev: dict, *, note: Optional[str] = None,
               by: str = "sweep") -> int:
    """Create (or find) the lead for an events row and FILE the event under it.
    Idempotent on event_id. Returns the lead id."""
    row = conn.execute("SELECT id FROM leads WHERE event_id=?", (ev["id"],)).fetchone()
    if row:
        lead_id = row[0]
    else:
        d = describe(ev)
        snippet = " ".join((ev.get("text") or "").split())[:400] or None
        cur = conn.execute(
            "INSERT INTO leads (name, company, email, domain, source, event_id,"
            " subject, snippet, permalink, note, first_seen_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?, COALESCE(?, datetime('now')))",
            (d["name"], d["company"], d["email"], d["domain"], d["source"], ev["id"],
             ev.get("subject"), snippet, ev.get("permalink"), note,
             (ev.get("ts") or None)))
        lead_id = cur.lastrowid
    events.ignore(conn, ev["id"], f"{FILE_PREFIX}{lead_id}", by=by,
                  evidence="a prospect, not a client to map — filed as a lead")
    return lead_id


def set_status(conn: sqlite3.Connection, lead_id: int, status: str,
               note: Optional[str] = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown lead status {status!r} (allowed: {STATUSES})")
    resolved = "datetime('now')" if status in ("client", "no") else "NULL"
    conn.execute(
        f"UPDATE leads SET status=?, note=COALESCE(?, note), updated_at=datetime('now'),"
        f" resolved_at={resolved} WHERE id=?", (status, note, lead_id))


def open_leads(conn: sqlite3.Connection, *, limit: int = 50) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM leads WHERE status NOT IN ('client','no')"
        " ORDER BY CASE status WHEN 'new' THEN 0 ELSE 1 END, first_seen_at DESC"
        " LIMIT ?", (limit,))]
