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
    2b. TRANSACTIONAL sender  — an e-signature / background-check / contract
                                provider. Mail ABOUT a named counterparty, never
                                a campaign, and never auto-excluded by anything
                                below. See `transactional_reason`.
    2c. OPERATIONAL platform  — Upwork, Make, Vapi, Slack, Stripe, Airtable,
                                ClickUp: the systems the business RUNS on. Their
                                mail is about his own work and has the shape of
                                bulk mail, because a platform sends from exactly
                                one no-reply address. The platform's own campaign
                                mail is still swept, per-message, by local part
                                or decoded subject. See `operational_verdict`.
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


# ── transactional protection (runs BEFORE every structural rule) ─────────────
# The structural rules read the SHAPE of an address, and a transactional
# provider has exactly the shape of bulk mail. Measured, 2026-08-19:
# 'docs@email.pandadoc.net' (the signed Globaltize contractor agreement) matched
# bulk-subdomain 'email.' and 'support@eu.checkr.com' ("Background check paused")
# matched noreply-local 'support'. Seven rows on the critical path of the newest
# client would have been filed as promotional noise. A rule that cannot tell an
# e-signature request from a newsletter must not be allowed to decide either.
_MENTION_MIN_LEN = 8     # below this, an alnum key inside a body is a coincidence
_MENTION_TEXT_CAP = 1500  # only the head of a body — footers name everyone


def transactional_reason(sender: str, rules: Optional[dict] = None) -> Optional[str]:
    """'transactional:<domain>' if this sender's mail is a transaction about a
    named counterparty (e-signature, background check, contract), else None.

    A DOMAIN list on purpose. The local part says nothing: 'support@eu.checkr.com'
    is a background check on a live client and 'support@mail.vapi.ai' is a
    product blast, and the only thing that separates them is who sent it.
    """
    rules = load_rules() if rules is None else rules
    spec = (rules.get("transactional") or {})
    dom = domain_of(sender)
    if not dom:
        return None
    for d in spec.get("domains") or []:
        if dom == d or dom.endswith("." + d):
            return f"{spec.get('reason', 'transactional')}:{d}"
    return None


# ── operational protection (runs after transactional, before every rule) ─────
# The platforms Abhijeet OPERATES ON. Their mail is about his own business and
# has exactly the shape of bulk mail, because a platform has one sending address
# and that address is a no-reply. See the `operational._note` in
# assets/sender-rules.json for the measurement that produced this tier.

def _subject_norm(subject: Optional[str], *, fold: bool = True) -> str:
    """Decoded, lowercased subject for rule matching.

    These subjects are stored RAW, and platform mail is routinely RFC2047
    encoded: '=?UTF-8?q?New_job_alert:_Zapier_Expert...'. Matching a plain
    regex against that silently matches NOTHING — which here would have meant
    protecting upwork.com and re-admitting all 85 job alerts to the board. So
    the subject is decoded before any rule sees it, and Q-encoding's underscore
    is restored to a space.
    """
    s = subject or ""
    try:
        import email.header
        s = str(email.header.make_header(email.header.decode_header(s)))
    except Exception:
        pass                                   # malformed encoding: use it raw
    s = re.sub(r"\s+", " ", s.replace("_", " ")).strip()
    # fold=False keeps the ORIGINAL case. Rules match folded (a pattern should
    # not care how Make capitalised 'scenario'), but a scenario name is shown
    # to Abhijeet on the incident feed and '1 doulamatch | bot watcher' is not
    # what his automation is called.
    return s.lower() if fold else s


def operational_verdict(sender: str, subject: Optional[str] = None,
                        rules: Optional[dict] = None) -> Optional[dict]:
    """None if this sender is not an operational platform; otherwise a dict
    saying whether THIS message is protected or is that platform's own campaign.

    Protection is per-DOMAIN, filtering is per-MESSAGE. That split is the whole
    design: 'the platform is important' and 'this particular mail is an advert'
    are different claims, and collapsing them is what produced both failure
    modes — protect nothing and lose the hire, protect everything and drown the
    board in job alerts.
    """
    rules = load_rules() if rules is None else rules
    spec = (rules.get("operational") or {})
    dom = domain_of(sender)
    if not dom:
        return None
    for d in spec.get("never") or []:           # a campaign-only subdomain
        if dom == d or dom.endswith("." + d):
            return None
    hit = next((d for d in spec.get("domains") or []
                if dom == d or dom.endswith("." + d)), None)
    if not hit:
        return None

    local = local_of(sender)
    if local in set((spec.get("marketing_locals") or {}).get(hit) or []):
        return {"verdict": EXCLUDE, "rule": f"operational-marketing:{hit}",
                "reason": f"noise:sender:{local}"}
    subj = _subject_norm(subject)
    for pat in (spec.get("marketing_subjects") or {}).get(hit) or []:
        try:
            if re.search(pat, subj):
                return {"verdict": EXCLUDE,
                        "rule": f"operational-marketing:{hit}",
                        "reason": f"noise:subject:{hit}-campaign"}
        except re.error:
            continue                            # a bad pattern must not protect-fail
    return {"verdict": ASK, "rule": f"operational:{hit}", "protected": True,
            "reason": f"{spec.get('reason', 'operational')}:{hit} — a platform"
                      f" Abhijeet operates on, writing about his own business;"
                      f" never auto-excluded by shape"}


OPS_ALERT = "ops-alert"


def ops_alert(sender: str, subject: Optional[str] = None,
              rules: Optional[dict] = None) -> Optional[dict]:
    """{reason, domain, scenario} if this is an OPERATIONAL ALERT, else None.

    A third thing a protected sender's mail can be. The mapping queue asks one
    question — "which client is this?" — and for 'Encountered error in
    [AUTOFILL] BT RBT Self scenario' that is the wrong question: the answer is
    a scenario-name lookup, not a judgement Abhijeet can make by reading. He
    said so when 134 of these landed on the board after the restore.

    So they are kept — deleting them was the original bug — and routed to the
    incident feed instead, grouped by the scenario named in the subject, which
    collapses 131 rows to the ~8 automations actually failing.

    Detection is deliberately split: `match` decides IF this is an alert,
    `subject_of` decides WHICH scenario. When no `subject_of` pattern fits, the
    whole subject becomes the group — degrading to one group per subject, never
    to silence.
    """
    rules = load_rules() if rules is None else rules
    spec = (rules.get("operational") or {})
    dom = domain_of(sender)
    hit = next((d for d in (spec.get("ops_alerts") or {})
                if dom == d or dom.endswith("." + d)), None)
    if not hit:
        return None
    conf = (spec.get("ops_alerts") or {})[hit]
    subj = _subject_norm(subject)
    if not any(_safe_search(p, subj) for p in conf.get("match") or []):
        return None
    # match on the folded form, SLICE from the original: the group is a name
    # Abhijeet reads on the incident feed, and the scenario is called
    # '1 DoulaMatch | Bot Watcher', not '1 doulamatch | bot watcher'. Same
    # string, same offsets — only the case differs — so the folded match's span
    # indexes the unfolded text exactly.
    raw = _subject_norm(subject, fold=False)
    scenario = raw
    for pat in conf.get("subject_of") or []:
        m = _safe_search(pat, subj)
        if m and m.groups():
            lo, hi = m.span(1)
            scenario = re.sub(r"\s+", " ", raw[lo:hi]).strip(" .").strip()
            break
    scenario = scenario[:120] or "(unnamed)"
    return {"reason": f"{OPS_ALERT}:{hit}:{scenario}", "domain": hit,
            "scenario": scenario}


def _safe_search(pattern: str, text: str):
    """A malformed rule must never decide anything — it skips."""
    try:
        return re.search(pattern, text)
    except re.error:
        return None


def _alnum(s: str) -> str:
    from core import registry as _registry          # lazy: registry imports config
    return re.sub(r"[^a-z0-9]", "", _registry.norm(s or ""))


def mention_index(reg) -> dict:
    """{alnum key: (client_id, the identifier it came from)} over matchable
    clients. Short keys are dropped — a 6-letter alias inside a paragraph of
    boilerplate is a coincidence, and this feeds a PROPOSAL on a live client."""
    idx: dict = {}
    if reg is None:
        return idx
    for cid in getattr(reg, "matchable_ids", lambda: [])():
        c = reg.by_id.get(cid) or {}
        for ident in [cid, c.get("name"), *(c.get("aliases") or [])]:
            k = _alnum(ident or "")
            if len(k) >= _MENTION_MIN_LEN:
                idx.setdefault(k, (cid, ident))
    return idx


def mentions_client(text: str, reg, *, index: Optional[dict] = None) -> tuple:
    """(client_id, matched identifier) iff the text names EXACTLY ONE registry
    client, else (None, None).

    Only ever called for a transactional sender, which is what makes a plain
    substring scan safe here: the population is "mail from a signing/vetting
    provider", where a client name in the subject means the document is that
    client's. Two clients named => ambiguous => Abhijeet decides. It produces a
    PROPOSAL, never a map: attribution writes client-facing content, and the
    only thing this has actually established is that a name appears in a body.
    """
    idx = mention_index(reg) if index is None else index
    if not idx:
        return (None, None)
    hay = _alnum((text or "")[:_MENTION_TEXT_CAP])
    hits = {}
    for k, (cid, ident) in idx.items():
        if k in hay:
            hits.setdefault(cid, ident)
    if len(hits) != 1:
        return (None, None)
    cid, ident = next(iter(hits.items()))
    return (cid, ident)


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
             display_name: Optional[str] = None,
             subject: Optional[str] = None, text: Optional[str] = None) -> dict:
    """{verdict, reason, rule, protected} — verdict is a client_id, EXCLUDE, ASK.

    `protected` True means no rule in this module may exclude the row and none
    did; something downstream (a human, or a client named in the body) owns it.

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

    # 2b. transactional — a signing/vetting provider. Never swept by shape.
    prot = transactional_reason(sender, rules)
    if prot:
        cid, ident = mentions_client(f"{subject or ''}\n{text or ''}", reg)
        if cid:
            return {"verdict": cid, "rule": "transactional-mention",
                    "protected": True,
                    "reason": f"{prot}; names the registry client {cid}"
                              f" ('{ident}') — proposed, never mapped"}
        return {"verdict": ASK, "rule": f"transactional:{dom}", "protected": True,
                "reason": f"{prot} — a document/vetting provider writing about a"
                          f" named counterparty; never auto-excluded"}

    # 2c. OPERATIONAL — a platform he runs the business on. Same defect as 2b
    # one size up: 'donotreply@upwork.com' is not a bulk sender that happens to
    # mention a client, it is the ONLY address Upwork has, so the noreply-local
    # rule was not filtering Upwork's marketing — it was deleting Upwork. The
    # domain is protected from every rule below; the platform's OWN campaign
    # mail is still swept here, by sender local part or by decoded subject.
    op = operational_verdict(sender, subject, rules)
    if op:
        if op["verdict"] == EXCLUDE:
            return op
        cid, ident = mentions_client(f"{subject or ''}\n{text or ''}", reg)
        if cid:
            return {"verdict": cid, "rule": f"{op['rule']}-mention",
                    "protected": True,
                    "reason": f"{op['reason']}; names the registry client {cid}"
                              f" ('{ident}') — proposed, never mapped"}
        return op

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
