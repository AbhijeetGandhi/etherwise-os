"""Client roster for classification + CRM matching + thread linkage.

THE ROSTER IS THE REGISTRY. `config/clients.yaml` (core/registry.py) is the
identity spine; this module is a thin adapter that exposes it in the shape the
knowledge module wants: {client_id, name, identifiers}. Identifiers drive Fathom
classification (participant emails/names, title), CRM revenue matching (txn
description/company), AND Upwork thread->client linkage (counterparty org/user/
room-name).

History worth keeping: this file used to return a hardcoded four-client
PILOT_CLIENTS list dating from before the registry existed. Nothing said so at
the call sites, so three separate features quietly operated on 4 of 72 clients —
`kb-backfill compile` skipped 18 of 22 dossiers, `link_threads` left 136 of 138
threads unattributed, and the email-poll domain gate admitted four domains. The
visible symptom (2026-08-12) was Brandi Jordan surfacing on Today as a cold
Upwork follow-up when The Cradle Company is an active project. A roster that
silently excludes most of the business is worse than no roster: every consumer
returns a confident, wrong, non-empty answer.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from core import db, registry

# a roster identifier is a company DOMAIN if it looks like one (dot, no spaces).
# Personal domains (gmail.com etc.) are intentionally never roster identifiers,
# so email pulling stays company-gated — never vacuums personal mail.
_DOMAIN = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")

# Abhijeet's Upwork user id — the "our side" of every thread (never a counterparty).
_OWNER_USER_ID = "1647672547786633216"

# A bare identifier may only be matched as a SUBSTRING when it is distinctive:
# multi-word, or long. "TCC", "endura", "Vitaliy" inside a sentence is a
# coincidence, not evidence; "the cradle company" is evidence.
_SUBSTR_MIN_LEN = 12


def load_client_map(db_path=None) -> List[dict]:
    """The active roster, from the registry. Excludes `no_match` entries
    (products/internal) exactly as the registry's own matchers do; prospects are
    INCLUDED because they hold their calls' context."""
    reg = registry.load()
    out: List[dict] = []
    for cid in reg.matchable_ids():
        c = reg.by_id[cid]
        idents: List[str] = [c["name"], *c.get("aliases", []),
                             *c.get("fathom_names", []), *c.get("emails", [])]
        seen, uniq = set(), []
        for i in idents:
            k = registry.norm(i)
            if k and k not in seen:
                seen.add(k)
                uniq.append(i)
        out.append({"client_id": cid, "name": c["name"], "identifiers": uniq,
                    "kind": reg.kind(cid),
                    "upwork_rooms": list(c.get("upwork_rooms", []))})
    return out


def domain_map(client_map: List[dict]) -> Dict[str, str]:
    """{company_domain: client_id} from roster identifiers — the sender-domain
    gate for email pulling. Full addresses (they contain '@') never become a
    domain claim, so `chloe@secatexas.com` gates that one mailbox, not the org.
    Clients with no company-domain identifier simply won't match."""
    out: Dict[str, str] = {}
    for c in client_map:
        for ident in c.get("identifiers", []):
            d = ident.lower().strip()
            if _DOMAIN.match(d):
                out.setdefault(d, c["client_id"])
    return out


def _match(hay: str, client_map: List[dict]):
    """Substring match, kept for callers that only have one blob of text.

    Only DISTINCTIVE identifiers are eligible (see _SUBSTR_MIN_LEN). With a
    4-client roster naive substring matching was survivable; against 70+ it
    turns every short alias into a false positive, and a WRONG attribution is
    worse than none — it files a client's material under someone else."""
    hay = registry.norm(hay)
    if not hay:
        return None, None
    for c in client_map:
        for ident in c.get("identifiers", []):
            k = registry.norm(ident)
            if not k or _DOMAIN.match(k):
                continue
            if not (" " in k or len(k) >= _SUBSTR_MIN_LEN):
                continue
            # WORD BOUNDARIES. Plain `k in hay` let the registry alias "rick k"
            # (Celcius Herbs) claim "Patrick Kelly", and "remote office" claim
            # any topic mentioning a remote office. A false attribution is
            # permanent — link_threads never overwrites a non-null client_id —
            # and it then merges a stranger into that client's single card.
            if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", hay):
                return c["client_id"], c["name"]
    return None, None


def match_counterparty(fields: List[Optional[str]],
                       client_map: List[dict]) -> tuple:
    """Resolve a client from DISCRETE counterparty fields, strongest first.

    1. exact name/alias on a whole field (or a comma-separated part of one) —
       an Upwork room is literally titled "Brandi Jordan" / "Dhanya Sangolli,
       Dhanya S", so the parts are the identity, not the punctuation;
    2. distinctive substring anywhere in the joined text.

    Returns (client_id, name) or (None, None). Exact-on-a-field beats substring
    so a long topic line can never outvote the counterparty's actual name."""
    parts: List[str] = []
    for f in fields:
        if not f:
            continue
        parts.append(f)
        parts.extend(p.strip() for p in f.split(",") if p.strip())
    exact: Dict[str, str] = {}
    for c in client_map:
        for ident in c.get("identifiers", []):
            exact.setdefault(registry.norm(ident), c["client_id"])
    names = {c["client_id"]: c["name"] for c in client_map}
    for p in parts:
        cid = exact.get(registry.norm(p))
        if cid:
            return cid, names[cid]
    return _match(" ".join(parts), client_map)


def link_threads(db_path, client_map: Optional[List[dict]] = None) -> dict:
    """Set threads.client_id by matching each thread's COUNTERPARTY (non-owner
    sender org/user names) + room_name/topic against the roster.

    Precedence: a registry `upwork_rooms` claim (deterministic, hand-approved)
    always wins; only then do we fall back to name matching. Idempotent, and it
    never overwrites an existing non-null client_id with a guess — a linkage
    that was approved once is not re-litigated by a fuzzy match later.

    Returns {linked, by_client, unmatched} — `unmatched` is the honest tail:
    threads we could not attribute, which is the mapping queue's input, not a
    number to hide."""
    client_map = client_map if client_map is not None else load_client_map(db_path)
    reg = registry.load()
    linked, unmatched = 0, 0
    by_client: dict = {}
    with db.connect(db_path) as conn:
        threads = [dict(r) for r in conn.execute(
            "SELECT id, room_name, topic, client_id FROM threads")]
        for t in threads:
            if t.get("client_id"):
                by_client[t["client_id"]] = by_client.get(t["client_id"], 0) + 1
                continue
            cid = reg.match_upwork_room(t["id"])
            if not cid:
                parties = conn.execute(
                    "SELECT GROUP_CONCAT(DISTINCT sender_org_name) o,"
                    " GROUP_CONCAT(DISTINCT sender_user_name) u FROM messages"
                    " WHERE thread_id=? AND sender_user_id != ?",
                    (t["id"], _OWNER_USER_ID)).fetchone()
                cid, _ = match_counterparty(
                    [t.get("room_name"),
                     parties["o"] if parties else None,
                     parties["u"] if parties else None,
                     t.get("topic")], client_map)
            if not cid:
                unmatched += 1
                continue
            conn.execute("UPDATE threads SET client_id=? WHERE id=?",
                         (cid, t["id"]))
            linked += 1
            by_client[cid] = by_client.get(cid, 0) + 1
    return {"linked": linked, "by_client": by_client, "unmatched": unmatched}
