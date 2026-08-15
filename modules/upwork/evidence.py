"""Cited delivery evidence for a proposal draft — retrieved, graded, redacted
and validated in CODE. Zero model calls.

WHY THIS EXISTS. The retired proposal skill ORDERED a knowledge-base lookup
("Before writing ANY proposal, you MUST read knowledge-base/wiki/proof-points.md")
inside a single-shot, tool-less gateway call. The model could not open the file
and invented instead. Live draft 2058152699613066610 claimed Abhijeet had
"built almost this exact stack for Horizons ABA's healthcare onboarding
pipeline, 49+ active boards, 7,000+ automation runs/month" — 49+ is HERMON's
number transplanted onto a different client, and 7,000+/month exists in no
source at all. Evidence is now SUPPLIED, never ordered.

WHAT THE RECON ESTABLISHED, and this module encodes:

1. Retrieval by tool name alone FAILS. BM25 ranks tool-INVENTORY facts top
   ("Primary tools used: Airtable, Make.com, Zapier…") because they are dense
   in tool names, while the facts that win a proposal — an API limit, a design
   decision, a measured outcome — name the tool once and rank low. The grade()
   gate inverts that ordering.
2. `monday.com` crashes FTS5 (`syntax error near "."`). Every term is quoted.
3. Recency is mandatory. The same claim reads "80+ automations" (2024-11) and
   "3,000+" (2026-04). A recency-blind retriever cites a 37x understatement,
   in his own voice, to a paying client.
4. Agency scope is NOT a safety boundary. It holds employee salaries in ₹,
   "Etherwise charges $25 per hour for retainer work", and "Upwork rate is
   approximately $30 per hour". Any rate fact would undercut $51.50/hr
   positioning. redact_reason() is a hard gate, not a ranking penalty.
5. (found here, 2026-08-16) Fathom mis-transcribes "Horizons ABA" nine ways —
   "Verizon CBA" ×26, "HorizonCBA" ×24, "Horizons EBA" ×70, "Items ABA" ×8.
   Emitting those verbatim would put a wrong client name in a client-facing
   proposal. Names are normalised on the way out.

Naming real clients is DELIBERATE (Abhijeet, 2026-08-16): he already names
Horizons ABA in 4 sent proposals and Hermon in 5. `facts.access_scope` is
currently an unused column, uniformly 'team'; it is the natural place to hang a
per-fact disclosure policy if one is ever needed.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from core import config, db
from modules.knowledge import kb

# ── the closed tool vocabulary ────────────────────────────────────────────────
# A job's stack is a small closed set. Guessing needles from free text is how
# you end up retrieving on "gmail". Key = canonical tool, value = the surface
# forms that appear in a job post.
TOOLS: Dict[str, Tuple[str, ...]] = {
    "airtable": ("airtable",),
    "make.com": ("make.com", "make .com", "integromat"),
    "n8n": ("n8n",),
    "zapier": ("zapier",),
    "monday.com": ("monday.com", "monday .com", "monday board"),
    "clickup": ("clickup",),
    "notion": ("notion",),
    "hubspot": ("hubspot",),
    "gohighlevel": ("gohighlevel", "ghl", "go high level"),
    "pipedrive": ("pipedrive",),
    "salesforce": ("salesforce",),
    "shopify": ("shopify",),
    "stripe": ("stripe",),
    "pandadoc": ("pandadoc",),
    "docusign": ("docusign",),
    "twilio": ("twilio",),
    "sendgrid": ("sendgrid",),
    "vapi": ("vapi", "wapi"),
    "retell": ("retell",),
    "elevenlabs": ("elevenlabs", "eleven labs"),
    "openai": ("openai", "chatgpt", "gpt-4", "gpt4"),
    "claude": ("claude", "anthropic"),
    "python": ("python",),
    "javascript": ("javascript", "node.js", "nodejs"),
    "webhook": ("webhook",),
    "api": ("rest api", "graphql", "api integration"),
    "google sheets": ("google sheet", "google sheets"),
    "slack": ("slack",),
    "whatsapp": ("whatsapp",),
    "calendly": ("calendly",),
    "supabase": ("supabase",),
    "instantly": ("instantly.ai", "instantly ai"),
    "manychat": ("manychat",),
    "activecampaign": ("activecampaign", "active campaign"),
    "scraping": ("scrap", "crawler", "playwright", "puppeteer"),
    "voice agent": ("voice agent", "voice ai", "ai receptionist", "cold call"),
}

TRUSTED = kb.TRUSTED_TAGS               # CONFIRMED / CROSS-VERIFIED only
SCOPES = ("agency", "cross-client")     # never a client-scoped dossier fact

# FTS5 barewords that must never reach MATCH unquoted (the monday.com crash).
_FTS_UNSAFE = re.compile(r"[^A-Za-z0-9 ]+")


def fts_phrase(term: str) -> Optional[str]:
    """Quote every term as a phrase and strip punctuation. Deterministic, and
    it never raises — `monday.com` unquoted is an FTS5 syntax error."""
    cleaned = _FTS_UNSAFE.sub(" ", term or "").strip()
    return '"%s"' % cleaned if cleaned else None


def job_stack(job: dict) -> Dict[str, int]:
    """{canonical tool: weight}. A tool named in the TITLE is the job (3); in
    the skills tags it is a requirement (2); in paragraph nine it is context (1)."""
    title = (job.get("title") or "").lower()
    try:
        skills = " ".join(json.loads(job.get("skills_json") or "[]")).lower()
    except (TypeError, ValueError):
        skills = ""
    desc = (job.get("description") or "").lower()
    hits: Dict[str, int] = {}
    for canon, forms in TOOLS.items():
        w = 0
        for f in forms:
            if f in title:
                w = max(w, 3)
            elif f in skills:
                w = max(w, 2)
            elif f in desc:
                w = max(w, 1)
        if w:
            hits[canon] = w
    return dict(sorted(hits.items(), key=lambda kv: -kv[1]))


# ── the quality gate ──────────────────────────────────────────────────────────
# DEMOTE: inventory / roster shapes. "I use tool X" is not delivery evidence,
# and it is exactly what BM25 puts on top.
_INVENTORY = re.compile(
    r"^\s*(primary|main)\s+(tools?|automation platform|programming language)"
    r"|^\s*(uses?|using|works? with|has (experience|expertise)|is (used|proficient))\b"
    r"|\btools? (used|include|are)\b"
    r"|^\s*(speaker|applicant|agency|team)('s)?\s+(primary|main|preferred)\b"
    r"|\bprimary (tools?|automation platform|programming language)\b"
    r"|\bexperience with (automation|all)\b", re.I)

# PROMOTE: a constraint, a limit, a design decision — the "genuine technical
# insight" the house style asks for and the model otherwise fabricates.
_INSIGHT = re.compile(
    r"\b(cannot|can not|does not|doesn'?t|lacks?|no (native|bulk|direct)|not possible"
    r"|limit(ation|ed|s)?|deprecat|breaks?|fails?|requires?|must be|only|instead of"
    r"|rather than|because|so that|in order to|workaround|gotcha|caveat)\b", re.I)

# PROMOTE: a measured outcome.
_MEASURED = re.compile(
    r"\b\d[\d,\.]*\s*(\+|%|k\b|hours?|records?|tickets?|days?|minutes?"
    r"|scenarios?|automations?|agents?|bases?|clients?|per\b)"
    r"|\$\s?\d", re.I)


def grade(fact: dict) -> int:
    """0..3. 0 = tool inventory (never emitted). 3 = a cited insight WITH a
    number. MIN_GRADE is the floor for anything that reaches the prompt."""
    text = fact.get("fact_text") or ""
    if _INVENTORY.search(text):
        return 0
    g = 1
    if _INSIGHT.search(text):
        g += 1
    if _MEASURED.search(text):
        g += 1
    if fact.get("category") in ("architecture", "numbers") and g > 1:
        g = min(3, g + 1)
    return min(g, 3)


# ── the redaction layer (a hard gate, never a ranking penalty) ────────────────
# Nothing that states a rate, a salary, or an internal cost may reach a
# client-facing prompt. Every pattern below was written against a REAL string
# found in agency scope; tests/test_upwork_evidence.py pins each one.
_REDACT: Tuple[Tuple[str, "re.Pattern"], ...] = (
    # any hourly rate, ours or anyone's — "$30 per hour", "$32.50/hr", "$25 an hour"
    ("hourly_rate", re.compile(
        r"[$€£₹]\s?\d[\d,]*(?:\.\d+)?\s*(?:/|per\s+|an\s+|a\s+)\s*(?:hr|hour)\b", re.I)),
    # rate/charge/bill language even without a currency symbol next to it
    ("rate_language", re.compile(
        r"\b(?:hourly|billing|billable)\s+rate\b"
        r"|\brate\s+(?:is|was|of|listed|starts?)\b"
        r"|\b(?:charges?|charging|bills?|billed|quotes?)\s+[$€£₹]?\s?\d"
        r"|\bper[- ]hour\s+(?:rate|price|cost)\b"
        r"|\b(?:retainer|maintenance)\s+(?:rate|fee|pricing)\b", re.I)),
    # rupee amounts — every ₹ figure in this KB is an employee salary
    ("inr_amount", re.compile(
        r"₹|\bINR\s?\d|\bRs\.?\s?\d|\blakh\b|\bcrore\b", re.I)),
    # staff compensation
    ("compensation", re.compile(
        r"\b(?:salary|salaries|salaried|payslip|take[- ]home|CTC|stipend"
        r"|compensation package|pay\s?scale)\b"
        r"|\b(?:paid|pays|paying|earning|earns)\b[^.]{0,40}\b(?:per month|monthly|a month|/month)\b"
        r"|\binternship\b[^.]{0,40}\d", re.I)),
    # our own economics
    ("internal_economics", re.compile(
        r"\b(?:profit margin|gross margin|net margin|markup|our margin)\b"
        r"|\b(?:agency|etherwise|we|our)\b[^.]{0,30}\b(?:revenue|turnover|profit)\b"
        r"|\bcost per (?:hire|employee|head)\b"
        r"|\bcommission percentage\b", re.I)),
    # anything naming Upwork and a rate/earning in the same breath
    ("upwork_rate", re.compile(
        r"\bupwork\b[^.]{0,60}\b(?:rate|per hour|hourly|earned|earnings)\b", re.I)),
)


def redact_reason(text: str) -> Optional[str]:
    """The class name if this text may never reach a client-facing prompt.

    Applied to fact_text AND citation — the citation quotes the transcript
    verbatim, which is where several of the rate statements actually live."""
    for name, rx in _REDACT:
        if rx.search(text or ""):
            return name
    return None


# ── client-name normalisation ─────────────────────────────────────────────────
# Fathom mis-transcribes "Horizons ABA" nine ways. Left alone, a proposal goes
# out naming a client that does not exist.
_NAME_FIXES: Tuple[Tuple["re.Pattern", str], ...] = (
    (re.compile(r"\b(?:Horizons?|Verizon|Items)\s*"
                r"(?:ABA|CBA|EBA|EPA|APA|AVA|CEBA)\b", re.I), "Horizons ABA"),
    (re.compile(r"\bHorizonCBA\b", re.I), "Horizons ABA"),
    (re.compile(r"\bHorizon\s*C?EBA\b", re.I), "Horizons ABA"),
)


def normalise_names(text: str) -> str:
    out = text or ""
    for rx, canon in _NAME_FIXES:
        out = rx.sub(canon, out)
    return out


# ── recency ───────────────────────────────────────────────────────────────────

def _as_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def age_days(occurred_dt: Optional[str], today: Optional[date] = None) -> Optional[int]:
    d = _as_date(occurred_dt)
    if d is None:
        return None
    return ((today or date.today()) - d).days


def recency_weight(days: Optional[int]) -> float:
    """Deterministic decay. An undated source is treated as mid-old, never as
    fresh — a missing date must not win a tie against a known-recent fact."""
    if days is None:
        return 0.4
    if days <= 180:
        return 1.0
    if days <= 365:
        return 0.8
    if days <= 730:
        return 0.5
    return 0.25


# A MEASURED claim decays: a count from 2024 is not a smaller version of the
# 2026 truth, it is a wrong number. An architectural constraint ("Airtable
# automations cannot use third-party JS libraries") does not decay the same way.
MEASURED_MAX_AGE_DAYS = config.EVIDENCE_MEASURED_MAX_AGE_DAYS
FACT_MAX_AGE_DAYS = config.EVIDENCE_MAX_AGE_DAYS
MIN_GRADE = config.EVIDENCE_MIN_GRADE
MIN_FACTS = config.EVIDENCE_MIN_FACTS
MAX_FACTS = config.EVIDENCE_MAX_FACTS
PER_TOOL = config.EVIDENCE_PER_TOOL

_UNIT_RE = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s*\+?\s*"
    r"(automation|scenario|base|lead|contract|agent|client|script|workflow"
    r"|record|operation|call|minute|step|module|month|year|hour|line)s?\b", re.I)


def claim_key(fact: dict) -> Optional[Tuple[str, str, bool]]:
    """Identity of a MEASURED claim: (tool, unit, names-a-client). Two facts
    with the same key are the same claim at two points in time — 80+ (2024-11)
    and 3,000+ (2026-04) are both (make.com, automation, False) — and only the
    newest may be emitted. Whether a client is named separates a per-client
    count from a portfolio count, so one never suppresses the other."""
    m = _UNIT_RE.search(fact.get("fact_text") or "")
    if not m:
        return None
    return (fact.get("tool") or "", m.group(1).lower(),
            bool(fact.get("names_client")))


# ── retrieval ─────────────────────────────────────────────────────────────────

_ROSTER_CACHE: Optional[Dict[str, List[str]]] = None

# A name made only of these is a common noun phrase, not an identity. The
# registry really does hold a client called "AI Agency", and without this every
# draft that says "I run a small AI agency" trips the client-attribution check.
_GENERIC_NAME_WORDS = frozenset("""
ai agency agencies group media systems digital consulting solutions services
tech technologies labs lab studio global partners ventures health care company
co corp inc llc ltd gmbh bv b.v. the and of holdings enterprises marketing
""".split())
# A surface form containing a bare function word matches ordinary prose:
# "Optimize IS" would fire on "Optimize is Airtable's #1 services partner".
_FUNCTION_WORDS = frozenset("""
is are was were be the a an and or of for in on at to with by from as it this
that i we you he she they
""".split())
_MIN_FORM_LEN = 5


def _usable_form(form: str) -> bool:
    words = [w for w in re.split(r"[^A-Za-z0-9.]+", (form or "").lower()) if w]
    if not words or len(form) < _MIN_FORM_LEN:
        return False
    if any(w in _FUNCTION_WORDS for w in words):
        return False
    return not all(w in _GENERIC_NAME_WORDS for w in words)


def client_vocabulary() -> Dict[str, List[str]]:
    """{canonical client name: [surface forms]}.

    From the REGISTRY, per CLAUDE.md — the registry is the only roster, never a
    constant. Aliases matter here and the name alone does not: the master
    reference writes "Horizons" and "Hermon" while the registry's canonical is
    "Horizons ABA", so a name-only vocabulary silently failed to attribute
    Section 4's entire Horizons block and rejected every legitimate Horizons
    claim. Falls back to the two clients the reference names if the registry is
    unreadable, so a broken yaml degrades the check instead of disabling it."""
    global _ROSTER_CACHE
    if _ROSTER_CACHE is not None:
        return _ROSTER_CACHE
    vocab: Dict[str, List[str]] = {"Horizons ABA": ["Horizons ABA", "Horizons"],
                                   "Hermon": ["Hermon"]}
    try:
        from modules.knowledge import clients as _clients
        for c in _clients.load_client_map():
            name = (c.get("name") or "").strip()
            forms = [f for f in ([name] + list(c.get("identifiers") or []))
                     if _usable_form(f)]
            if not forms:
                continue
            vocab.setdefault(name, [])
            for f in forms:
                if f not in vocab[name]:
                    vocab[name].append(f)
    except Exception:                                        # noqa: BLE001
        pass
    # longest form first so "Horizons ABA" is preferred over "Horizons"
    for name in vocab:
        vocab[name].sort(key=len, reverse=True)
    _ROSTER_CACHE = vocab
    return _ROSTER_CACHE


def _clients_named(text: str,
                   vocab: Optional[Dict[str, List[str]]] = None) -> List[str]:
    """Canonical client names mentioned in `text`, by any registered surface
    form. Canonical, not surface: "Horizons" in the reference and "Horizons
    ABA" in a draft are one client, and the pair check only works if they
    resolve to the same key."""
    hay = (text or "").lower()
    found = []
    for name, forms in (vocab if vocab is not None
                        else client_vocabulary()).items():
        for form in forms:
            if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])"
                         % re.escape(form.lower()), hay):
                found.append(name)
                break
    return found


def retrieve(conn: sqlite3.Connection, job: dict,
             today: Optional[date] = None) -> List[dict]:
    """Graded, redacted, recency-resolved evidence for one job's stack.

    Every filter is deterministic and every survivor carries its citation. The
    return is ordered best-first and capped at MAX_FACTS."""
    stack = job_stack(job)
    vocab = client_vocabulary()
    picked: List[dict] = []
    seen_ids = set()
    for tool, weight in stack.items():
        phrase = fts_phrase(tool)
        if not phrase:
            continue
        try:
            rows = conn.execute(
                "SELECT f.id, f.fact_text, f.citation, f.confidence, f.scope,"
                "       f.category, f.client_id, s.occurred_dt, s.source_type"
                "  FROM facts_fts"
                "  JOIN facts f ON f.id = facts_fts.rowid"
                "  JOIN sources s ON s.id = f.source_id"
                " WHERE facts_fts MATCH ?"
                "   AND f.confidence IN (%s)"
                "   AND f.scope IN (%s)"
                " ORDER BY rank LIMIT 80"
                % (",".join("?" * len(TRUSTED)), ",".join("?" * len(SCOPES))),
                [phrase] + list(TRUSTED) + list(SCOPES)).fetchall()
        except sqlite3.OperationalError:
            # an FTS term that still explodes must never take the scan with it
            continue
        kept = 0
        for row in rows:
            if row["id"] in seen_ids or kept >= PER_TOOL:
                continue
            f = dict(row)
            f["tool"], f["stack_weight"] = tool, weight
            if _classify(f, vocab, today) is None:
                continue
            seen_ids.add(f["id"])
            picked.append(f)
            kept += 1

    picked = _resolve_stale_claims(picked)
    picked.sort(key=lambda f: (-f["grade"], -f["recency"], -f["stack_weight"],
                               f["age_days"] if f["age_days"] is not None
                               else 10_000))
    return picked[:MAX_FACTS]


def _classify(f: dict, vocab: Dict[str, List[str]],
              today: Optional[date]) -> Optional[dict]:
    """Grade / redact / age one row in place. None means it is out."""
    reason = redact_reason(f["fact_text"]) or redact_reason(f["citation"])
    if reason:
        f["redacted"] = reason
        return None
    f["grade"] = grade(f)
    if f["grade"] < MIN_GRADE:
        return None
    f["fact_text"] = normalise_names(f["fact_text"])
    f["citation"] = normalise_names(f["citation"])
    f["names_client"] = bool(_clients_named(f["fact_text"], vocab))
    f["age_days"] = age_days(f["occurred_dt"], today)
    f["measured"] = bool(_MEASURED.search(f["fact_text"]))
    if f["age_days"] is not None:
        if f["measured"] and f["age_days"] > MEASURED_MAX_AGE_DAYS:
            return None                    # a stale count is a wrong number
        if f["age_days"] > FACT_MAX_AGE_DAYS:
            return None
    elif f["measured"]:
        return None                        # undated number: unverifiable vintage
    f["recency"] = recency_weight(f["age_days"])
    return f


def _resolve_stale_claims(facts: List[dict]) -> List[dict]:
    """One claim, one vintage: the freshest wins. Without this the retriever
    happily hands the model both "80+ automations" and "3,000+ automations"
    and lets it pick."""
    best: Dict[Tuple[str, str, bool], dict] = {}
    passthrough: List[dict] = []
    for f in facts:
        key = claim_key(f)
        if key is None:
            passthrough.append(f)
            continue
        cur = best.get(key)
        if cur is None:
            best[key] = f
            continue
        # smaller age_days wins; an undated fact never displaces a dated one
        a = f["age_days"] if f["age_days"] is not None else 10_000
        b = cur["age_days"] if cur["age_days"] is not None else 10_000
        if a < b:
            best[key] = f
    return passthrough + list(best.values())


# ── the prompt block ──────────────────────────────────────────────────────────

BLOCK_HEADER = (
    "EVIDENCE — verified, cited facts from Abhijeet's own call and demo"
    " recordings. These are the ONLY project specifics you may add on top of"
    " the reference's Section 4 anchor numbers. Do not restate a citation in"
    " the proposal; use the fact, drop the reference id.")


def render_block(facts: List[dict]) -> str:
    """The numbered block that goes in the USER message (never the cached
    system block). Numbering is what makes the post-draft validator able to
    say which claim came from where."""
    if not facts:
        return ""
    lines = [BLOCK_HEADER, ""]
    for i, f in enumerate(facts, 1):
        lines.append("[E%d] %s" % (i, f["fact_text"].strip()))
        lines.append("     source: %s" % (f["citation"] or "").strip()[:160])
    return "\n".join(lines)


def for_job(job: dict, db_path=None, today: Optional[date] = None) -> dict:
    """The whole retrieval, ready to hand to scoring.draft_proposal().

    HONEST DEGRADATION: below MIN_FACTS graded facts we do not pad and we do
    not stretch — the draft falls through to the plain path and records
    `evidence: none`. This fires often and is meant to: the KB holds 374 facts
    for make.com and 283 for airtable, but 3 for Shopify and none at all for
    Retell or Kommo."""
    with db.connect(db_path) as conn:
        facts = retrieve(conn, job, today=today)
    stack = job_stack(job)
    if len(facts) < MIN_FACTS:
        return {"facts": [], "block": "", "status": "none",
                "reason": "insufficient_graded_facts",
                "found": len(facts), "min": MIN_FACTS,
                "stack": list(stack)}
    return {"facts": facts, "block": render_block(facts), "status": "supplied",
            "found": len(facts), "stack": list(stack)}


# ── the post-draft validator ──────────────────────────────────────────────────
# Structural non-fabrication. Every number and every named-client claim in the
# draft must trace to something we actually supplied. A claim that cannot be
# traced does not get to survive the emit path.

# A digit glued to a letter is part of a NAME, not a claim: "360dialog", "n8n",
# "GPT-4o", "Claude 4.5". Measured 2026-08-16 — the validator blocked a draft
# twice over "360" read out of the WhatsApp BSP's company name.
_NUM = re.compile(r"(?<![A-Za-z0-9.])\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9])")
_NUM_K = re.compile(r"(?<![A-Za-z0-9.])(\d[\d,]*(?:\.\d+)?)\s*[kK]\b")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?\n]?")

# Small integers are enumeration, not a claim: "three phases", "2 weeks",
# "1." in a numbered answer, hours of the day. Anything above this must trace.
FREE_INTEGER_MAX = config.EVIDENCE_FREE_INTEGER_MAX
# Round durations that appear in any scheduling sentence and assert nothing
# about delivered work.
FREE_VALUES = frozenset({15.0, 20.0, 30.0, 45.0, 60.0, 90.0, 100.0})


def numbers_in(text: str) -> set:
    out = set()
    for m in _NUM.finditer(text or ""):
        raw = m.group(0).rstrip(".").replace(",", "")
        try:
            out.add(float(raw))
        except ValueError:
            continue
    for m in _NUM_K.finditer(text or ""):
        try:
            out.add(float(m.group(1).replace(",", "")) * 1000)
        except ValueError:
            continue
    return out


def _reference_pairs(reference: str, vocab: Dict[str, List[str]]) -> Dict[str, set]:
    """{client name: numbers that may be attached to it}, built from the
    reference at TWO granularities:

      - a `### ` section whose HEADER names a client donates every number in
        its body (so "### Horizons …" + a bullet "19+ months" is legitimate);
      - any single LINE that names a client donates that line's numbers (so
        "- Hermon (49+ bases…)" under a neutral header still works).

    Section granularity alone is too loose — Section 10's "Airtable-heavy
    roles" lists Hermon's 49+ and Horizons in the same block, which is exactly
    the transplant we are trying to catch. Line granularity alone is too tight."""
    pairs: Dict[str, set] = {}
    current: List[str] = []
    body: List[str] = []

    def flush():
        if current and body:
            nums = numbers_in("\n".join(body))
            for name in current:
                pairs.setdefault(name, set()).update(nums)

    for line in (reference or "").splitlines():
        if line.startswith("### "):
            flush()
            current, body = _clients_named(line, vocab), []
        else:
            body.append(line)
        for name in _clients_named(line, vocab):
            pairs.setdefault(name, set()).update(numbers_in(line))
    flush()
    return pairs


def allowed_claims(reference: str, facts: List[dict], job: dict) -> dict:
    """The universe of things a draft is permitted to assert."""
    vocab = client_vocabulary()
    job_text = " ".join(str(job.get(k) or "") for k in (
        "title", "description", "skills_json", "hourly_min", "hourly_max",
        "fixed_budget", "weekly_budget", "total_applicants",
        "client_total_spent", "client_hires", "engagement_duration"))
    numbers = numbers_in(reference) | numbers_in(job_text) | FREE_VALUES
    pairs = _reference_pairs(reference, vocab)
    for f in facts:
        nums = numbers_in(f["fact_text"])
        numbers |= nums
        for name in _clients_named(f["fact_text"], vocab):
            pairs.setdefault(name, set()).update(nums)
    return {"numbers": numbers, "pairs": pairs, "vocab": vocab,
            "clients": set(pairs)}


def validate_draft(draft: str, reference: str, facts: List[dict],
                   job: dict) -> Tuple[bool, List[dict]]:
    """(ok, problems). A problem is a claim that traces to nothing we supplied.

    Two independent checks, because the live fabrication tripped both:
      unsourced_number   — the value appears in no source at all (7,000+).
      client_number_mismatch — the value exists, but not for THAT client
                               (Hermon's 49+ attached to Horizons ABA)."""
    allowed = allowed_claims(reference, facts, job)
    problems: List[dict] = []
    for value in numbers_in(draft):
        if value in allowed["numbers"]:
            continue
        if 0 <= value <= FREE_INTEGER_MAX:
            continue
        problems.append({"kind": "unsourced_number", "value": value})
    for sentence in _SENTENCE.findall(draft or ""):
        named = _clients_named(sentence, allowed["vocab"])
        if not named:
            continue
        for value in numbers_in(sentence):
            if 0 <= value <= FREE_INTEGER_MAX:
                continue
            if value in FREE_VALUES:
                continue
            if any(value in allowed["pairs"].get(n, set()) for n in named):
                continue
            problems.append({"kind": "client_number_mismatch",
                             "client": named[0], "value": value,
                             "sentence": sentence.strip()[:160]})
    return (not problems), problems


RETRY_HEADER = (
    "REWRITE. The previous attempt asserted specifics that trace to nothing —"
    " they are in neither the reference, the evidence, nor the job post, so"
    " they were invented. Write the proposal again WITHOUT them. Do not"
    " substitute different invented numbers; drop the claim, or make it"
    " qualitative. In particular never invent third-party pricing, a fixed"
    " price you have not been asked for, or a volume figure.")


def retry_note(problems: List[dict]) -> str:
    """What the validator objected to, in the model's own terms. The fallback
    path is a second call either way; spending it re-rolling the same dice is
    a waste, so it is told what failed."""
    if not problems:
        return ""
    lines = [RETRY_HEADER, "", "Untraceable in the previous attempt:"]
    seen = set()
    for p in problems[:8]:
        value = p.get("value")
        if value in seen:
            continue
        seen.add(value)
        pretty = ("%g" % value) if isinstance(value, float) else str(value)
        if p["kind"] == "client_number_mismatch":
            lines.append("- %s is not a figure on record for %s"
                         % (pretty, p.get("client")))
        else:
            lines.append("- %s appears in no source" % pretty)
    return "\n".join(lines)
