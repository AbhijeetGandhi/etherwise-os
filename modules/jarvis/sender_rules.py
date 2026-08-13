"""Decide whether a sender is a client, obvious noise, or genuinely unknown.

The point of this module is to stop spending Abhijeet's attention on things he
should never have been shown. He put it plainly (2026-08-10):

    "a lot of the crude records you can tell yourself... chessbase is the chess
     player... those are promotional letters and things like that. So you don't
     need to add them to the queue. You should be able to sort them out
     yourself. And if you have question about some specific ones, you can always
     ask me, and then I give you the mapping for that, then you should in future
     never ask me for that same mapping again."

Two obligations follow, and the precedence order encodes both:

    1. learned-mappings.json  — anything he has ALREADY answered. Checked first,
                                always, so a resolved question cannot resurface.
    2. registry client domain — a real client always wins over any heuristic.
    3. structural rules       — bulk-mail subdomain, no-reply local part, a
                                .bank.in/.edu.in suffix. These GENERALISE, which
                                matters more than any denylist: 'mailers.',
                                'custcomm.', 'em1.' are the shape of bulk mail
                                from senders nobody has listed yet.
    4. named categories       — bank / investment / vendor / travel / ecommerce
                                / personal-interest / education.
    5. ASK                    — and only here. An unknown business domain or a
                                personal address is a real question; a chess
                                newsletter is not.

`learn()` appends to the store, so the act of answering is also the act of never
being asked again.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Optional

from core import config

RULES_PATH = config.V3_ROOT / "assets" / "sender-rules.json"
LEARNED_PATH = config.V3_ROOT / "assets" / "learned-mappings.json"

ASK = "ASK"
EXCLUDE = "EXCLUDE"

_ADDR = re.compile(r"[\w.+-]+@([\w.-]+)")

# Free mail: never a company domain, so it can't be matched to a client by
# domain. It is a real question (a client contacting from a personal address is
# common), so these go to ASK rather than being excluded.
FREEMAIL = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com",
            "hotmail.com", "icloud.com", "proton.me", "protonmail.com",
            "live.com", "aol.com", "rediffmail.com"}


def _load(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def load_rules() -> dict:
    return _load(RULES_PATH, {})


def load_learned() -> dict:
    return _load(LEARNED_PATH, {"mappings": {}}).get("mappings", {})


def domain_of(sender: str) -> str:
    """Sender domain, lowercase. Accepts a bare domain or a full address."""
    s = (sender or "").strip().lower().strip("<>")
    m = _ADDR.search(s)
    if m:
        return m.group(1)
    return s.split("@")[-1] if "@" in s else s


def local_of(sender: str) -> str:
    s = (sender or "").strip().lower().strip("<>")
    return s.split("@")[0] if "@" in s else ""


def _registered_domains(reg) -> set:
    """Domains a client claims WHOLESALE — bare-domain identifiers only.

    A full address in the registry (`tenni.theurer@gmail.com`) claims that one
    person, NOT the domain. Stripping it to `gmail.com` made every Gmail sender
    look client-claimed and pushed it to ASK — precisely the noise this module
    exists to stop. `reg.match_email` already handles exact addresses.
    """
    out = set()
    if reg is None:
        return out
    for cid in getattr(reg, "matchable_ids", lambda: [])():
        for ident in (reg.by_id.get(cid, {}).get("emails") or []):
            d = str(ident).lower().strip()
            if "@" not in d:                     # a bare domain claims everyone
                out.add(d)
    return out


def classify(sender: str, *, reg=None, rules: Optional[dict] = None,
             learned: Optional[dict] = None,
             display_name: Optional[str] = None) -> dict:
    """{verdict, reason, rule} where verdict is a client_id, EXCLUDE, or ASK.

    `display_name` is worth passing whenever it is known. A client writing from a
    personal address is unmatchable by domain but often trivially matchable by
    name: mikegandia@gmail.com looked like an unanswerable question until the
    display name "Michael Gandia" was checked against the registry aliases,
    where it is already listed under CMF Publishing. Asking about that would
    have been asking Abhijeet something the system already knew.
    """
    rules = load_rules() if rules is None else rules
    learned = load_learned() if learned is None else learned
    dom, local = domain_of(sender), local_of(sender)
    if not dom:
        return {"verdict": ASK, "reason": "no sender", "rule": "none"}

    # 1. already answered — the never-ask-twice guarantee
    for key in (f"{local}@{dom}", dom):
        hit = learned.get(key)
        if hit:
            return {"verdict": hit["verdict"], "reason": hit.get("reason", ""),
                    "rule": "learned"}

    # 2. a real client domain always wins
    if reg is not None:
        cid = None
        try:
            cid = reg.match_email(f"{local}@{dom}") if hasattr(reg, "match_email") \
                else None
        except Exception:
            cid = None
        if cid:
            return {"verdict": cid, "reason": "registry client",
                    "rule": "registry"}
        if dom not in FREEMAIL and dom in _registered_domains(reg):
            return {"verdict": ASK, "reason": "domain claimed by a client but no"
                                              " exact match", "rule": "registry"}

    # 3. structural — generalises to senders nobody has listed
    for suffix, kind in (rules.get("tld_suffixes") or {}).items():
        if dom.endswith(suffix):
            return {"verdict": EXCLUDE, "reason": f"noise:sender:{kind}",
                    "rule": f"tld:{suffix}"}
    first = dom.split(".")[0]
    if first in set(rules.get("bulk_subdomains") or []) and dom.count(".") >= 1:
        return {"verdict": EXCLUDE, "reason": "noise:sender:bulk-mail",
                "rule": f"bulk-subdomain:{first}"}
    if local and local in set(rules.get("noreply_locals") or []):
        return {"verdict": EXCLUDE, "reason": f"noise:sender:{local}",
                "rule": "noreply-local"}
    # plus-addressed tag: `nick+news@leftclick.ai`. The SENDER chose the tag to
    # say what the mail is, which makes it a stronger and far more general
    # signal than any domain list — it catches every marketer who uses the
    # convention, including ones nobody has seen yet. A client writing from
    # `first+last@` is unaffected: only the listed tags fire.
    if "+" in local:
        tag = local.split("+", 1)[1].strip()
        if tag and tag in set(rules.get("plus_tags") or []):
            return {"verdict": EXCLUDE, "reason": f"noise:sender:{tag}",
                    "rule": f"plus-tag:{tag}"}

    # 4. named categories (exact domain, or a parent domain we know)
    for cat, spec in (rules.get("categories") or {}).items():
        for d in spec.get("domains") or []:
            if dom == d or dom.endswith("." + d):
                return {"verdict": EXCLUDE, "reason": spec["reason"],
                        "rule": f"category:{cat}"}

    # 5. the display name may already be a known alias — check before asking
    if reg is not None and display_name:
        try:
            cid = reg.match_name(display_name)
        except Exception:
            cid = None
        if cid:
            return {"verdict": cid, "rule": "registry-alias",
                    "reason": f"display name '{display_name}' is a registered"
                              f" alias of {cid}"}

    # 6. a genuine question
    if dom in FREEMAIL:
        return {"verdict": ASK, "reason": "personal address — could be a client",
                "rule": "freemail"}
    return {"verdict": ASK, "reason": "unknown business domain", "rule": "unknown"}


def learn(sender: str, verdict: str, reason: str, *, note: str = "",
          learned_from: str = "abhijeet", path: Optional[Path] = None) -> dict:
    """Record an answer so it is never asked again. Writes immediately — the
    answer and the memory of it must not be able to come apart."""
    p = Path(path or LEARNED_PATH)
    doc = _load(p, {"mappings": {}})
    doc.setdefault("mappings", {})[domain_of(sender)] = {
        "verdict": verdict, "reason": reason, "note": note,
        "learned_on": date.today().isoformat(), "learned_from": learned_from}
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    return doc["mappings"][domain_of(sender)]
