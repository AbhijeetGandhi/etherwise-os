"""Client roster for classification + CRM matching + thread linkage. The clients
table (Airtable mirror) is empty until M1b lands, so the roster is defined here.
Each entry's `identifiers` drive Fathom classification (participant emails/names,
title), CRM revenue matching (txn description/company), AND Upwork thread→client
linkage (counterparty org/user/room-name). Include domain forms AND any accented
/legal-name variants seen in the data.
"""
from __future__ import annotations

import re
from typing import Dict, List

from core import db

# a roster identifier is a company DOMAIN if it looks like one (dot, no spaces).
# Personal domains (gmail.com etc.) are intentionally never roster identifiers,
# so email pulling stays company-gated — never vacuums personal mail.
_DOMAIN = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")

# Abhijeet's Upwork user id — the "our side" of every thread (never a counterparty).
_OWNER_USER_ID = "1647672547786633216"

PILOT_CLIENTS: List[dict] = [
    {"client_id": "horizons-aba", "name": "Horizons ABA",
     "identifiers": ["horizonsaba.com", "horizons aba", "horizons"]},
    {"client_id": "adelye", "name": "Adelye",
     # Adélye appears accented in CRM (assignment_company "Adélye")
     "identifiers": ["adelye.com", "adélye", "adelye"]},
    {"client_id": "endura", "name": "Endura Solutions",
     "identifiers": ["endurasolutions.com.au", "endura solutions", "endura"]},
    {"client_id": "netfly", "name": "NETFLY",
     # message-heavy, no Fathom calls — linkage-validation pilot
     "identifiers": ["netfly", "haleema", "umair"]},
]


def load_client_map(db_path=None) -> List[dict]:
    """Return the active client roster (pilot roster until the clients mirror
    is populated at M1b)."""
    return PILOT_CLIENTS


def domain_map(client_map: List[dict]) -> Dict[str, str]:
    """{company_domain: client_id} from roster identifiers — the sender-domain
    gate for email pulling. Clients with no company-domain identifier (e.g. a
    personal-email client) simply won't match; add their domain to the roster."""
    out: Dict[str, str] = {}
    for c in client_map:
        for ident in c.get("identifiers", []):
            d = ident.lower().strip()
            if _DOMAIN.match(d):
                out[d] = c["client_id"]
    return out


def _match(hay: str, client_map: List[dict]):
    hay = (hay or "").lower()
    for c in client_map:
        for ident in c.get("identifiers", []):
            if ident.lower() in hay:
                return c["client_id"], c["name"]
    return None, None


def link_threads(db_path, client_map: List[dict]) -> dict:
    """Set threads.client_id by matching each thread's COUNTERPARTY (non-owner
    sender org/user names) + room_name/topic against the roster identifiers.
    Idempotent; returns {linked, by_client}. Lets message-heavy clients with no
    Fathom calls still get their Upwork signal."""
    linked = 0
    by_client: dict = {}
    with db.connect(db_path) as conn:
        threads = [dict(r) for r in conn.execute(
            "SELECT id, room_name, topic FROM threads")]
        for t in threads:
            parties = conn.execute(
                "SELECT GROUP_CONCAT(DISTINCT sender_org_name) o,"
                " GROUP_CONCAT(DISTINCT sender_user_name) u FROM messages"
                " WHERE thread_id=? AND sender_user_id != ?",
                (t["id"], _OWNER_USER_ID)).fetchone()
            hay = " ".join(filter(None, [
                t.get("room_name"), t.get("topic"),
                parties["o"] if parties else None,
                parties["u"] if parties else None]))
            cid, _ = _match(hay, client_map)
            if not cid:
                continue
            conn.execute("UPDATE threads SET client_id=? WHERE id=?",
                         (cid, t["id"]))
            linked += 1
            by_client[cid] = by_client.get(cid, 0) + 1
    return {"linked": linked, "by_client": by_client}
