"""Finance payment-mail rail — PROVISIONAL payment events from Wise/Stripe email.

WHY THIS EXISTS. The ledger's money picture goes silently stale whenever the
manual weekly Wise CSV import slips. A stale import does not just delay data — it
MANUFACTURES false conclusions about clients: on 2026-08-09 the cockpit showed
August revenue $550, making Adelye (who paid Aug 7) look delinquent, purely
because the CSV was not imported. The truth already sits in the Wise/Stripe
notification emails in inbox 01. Reading them read-only gives an independent,
earlier signal so we can (a) know the ledger is behind and say so, and (b) stop
flagging clients who have in fact paid.

WHAT IT NEVER DOES. It never writes `transactions` and never sends anything. It
emits PROVISIONAL rows into their OWN table (`provisional_payments`) that
doctor / pnl / cash NEVER read, so a provisional row cannot enter the real ledger
and cannot double-count. The authoritative CSV/statement import stays the only
writer of the ledger; reconcile only flips a provisional row to 'confirmed' once
its real twin lands.

READ-ONLY BY CONSTRUCTION. The transport reuses modules.jarvis.imap_rail's proven
read-only IMAP substrate (select(readonly=True), BODY.PEEK, SEARCH/FETCH only) so
the two readers cannot diverge. Parsing, attribution, reconcile, staleness and AR
are all PURE over their inputs (no socket, no clock) — fixtures cover them with no
network; the transport is the only injectable side.

STATES: provisional -> confirmed (its ledger twin landed) | zero_value (a
notification for a ZERO amount, which is not a payment — see ZERO_VALUE below) |
superseded. `provisional` is the only OPEN state; everything else is terminal and
is excluded from staleness, the mapping queue and the stale-unmatched anomaly.
"""
from __future__ import annotations

import html as _html
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parseaddr
from typing import Callable, Optional

from core import config
from modules.finance import money
from modules.jarvis import imap_rail

# ══════════════════════════════════════════════════════════════════════════════
# CALIBRATION BLOCK — the ONLY provider-specific patterns. The exact live email
# HTML is not verifiable in the headless build env, so these are built to the
# documented notification format and ISOLATED here for a ~10-minute live
# calibration (run bin/finance-payment-mail dry-run against inbox 01 first).
# Everything below this block is provider-agnostic and fixture-covered.
# ══════════════════════════════════════════════════════════════════════════════

# Wise incoming-payment subjects. Group 'payer' = who paid. Order matters (first
# match wins). LIVE-OBSERVED (inbox 01): "<Payer> just paid you" (Adelye/CMF). The
# outgoing "Your money's been sent (#...)" is DELIBERATELY not here — it is money
# Abhijeet SENT (its body carries fee amounts that would misread as income).
WISE_SUBJECT_PATTERNS = (
    re.compile(r"^(?P<payer>.+?)\s+just paid you\b", re.I),
    re.compile(r"^(?P<payer>.+?)\s+just sent you\b", re.I),
    re.compile(r"^(?P<payer>.+?)\s+sent you money\b", re.I),
    re.compile(r"^You(?:'ve| have) (?:been paid|received)\b", re.I),  # no payer in subject
)
# Amount + currency. 'amt' is the number (US "3,500.00"/"1,615" OR German
# "349,00"/"1.234,56" — _to_minor disambiguates); 'cur' a 3-letter code, 'sym' a
# symbol. LIVE Wise reads "You received 682.50 USD from <payer>" (amount-then-CUR,
# and integers with comma thousands like "1,615 USD"), so the amount is ANCHORED to
# "received" first — the Wise HTML is huge and unanchored scanning risks CSS noise.
_MONEY = r"(?P<amt>\d[\d.,]*\d|\d)"   # digit-bounded: a trailing sentence period is NOT swallowed
# case-sensitive even under re.I: a real ISO code is uppercase. Without (?-i:...)
# a re.I amount pattern grabs "Que" from a trailing "Questions?" as the currency.
_CUR3 = r"(?P<cur>(?-i:[A-Z]{3}))"
_SYM = r"(?P<sym>[$£€₹])"
WISE_AMOUNT_PATTERNS = (
    re.compile(rf"received[:\s]+{_MONEY}\s*{_CUR3}", re.I),   # "You received 682.50 USD"
    re.compile(rf"received[:\s]+{_CUR3}\s*{_MONEY}", re.I),   # documented "received USD 3,500.00"
    re.compile(rf"{_CUR3}\s*{_MONEY}"),                       # generic fallbacks
    re.compile(rf"{_MONEY}\s*{_CUR3}"),
    re.compile(rf"{_SYM}\s*{_MONEY}"),
)
# A payer named in the Wise BODY when the subject carried none. LIVE form is
# "You received 682.50 USD from <payer>"; the documented form is "a payment from
# <name>". The terminator excludes a bare period so "Consulting Systems B.V." is
# not truncated at "B."; it stops at " of "/" on "/comma/newline/end.
_PAYER = r"(?P<payer>[A-Za-z0-9&.'\- ]{2,60}?)\s*(?:\s+of\b|\s+on\b|,|\n|$)"
WISE_BODY_PAYER = (
    re.compile(rf"received\s+[\d.,]+\s*[A-Z]{{3}}\s+from\s+{_PAYER}", re.I),
    re.compile(rf"(?:payment|money)\s+from\s+{_PAYER}", re.I),
)

# Stripe receipt/notification (Beglaubigt.de). LIVE: English "Your receipt from
# <merchant> #..." AND German "Ihr Zahlungsbeleg [#...] von <merchant>". The
# merchant is the payer.
STRIPE_SUBJECT_PATTERNS = (
    re.compile(r"Receipt from\s+(?P<payer>.+?)(?:\s*[\[#(]|$)", re.I),
    re.compile(r"Zahlungsbeleg\b.*?\bvon\s+(?P<payer>.+?)(?:\s*[\[#(]|$)", re.I),
    re.compile(r"Your\s+(?P<payer>.+?)\s+receipt\b", re.I),
    re.compile(r"(?P<payer>.+?)\s+payment (?:received|confirmation)\b", re.I),
)
# LIVE Stripe amounts are EUR: English "Amount paid €989.35"; German "Betrag\n
# 349,00 €" (label, then comma-decimal amount, symbol after). Currency comes from
# the symbol/code, never a default (the old default-USD misread every EUR receipt).
STRIPE_AMOUNT_PATTERNS = (
    re.compile(rf"Amount paid\s*[:\-]?\s*{_SYM}?\s*{_MONEY}\s*{_CUR3}?", re.I),
    re.compile(rf"Betrag\s*[:\s]*{_MONEY}\s*{_SYM}", re.I),   # German
    re.compile(rf"{_SYM}\s*{_MONEY}"),
    re.compile(rf"{_MONEY}\s*{_SYM}"),
    re.compile(rf"{_CUR3}\s*{_MONEY}"),
)
STRIPE_BODY_MERCHANT = (
    re.compile(r"Receipt from\s+(?P<payer>[^\n<#]{2,60})", re.I),
    re.compile(r"Zahlungsbeleg\s+von\s+(?P<payer>[^\n<#]{2,60})", re.I),
)

# ══════════════════════════════════════════════════════════════════════════════
# end CALIBRATION BLOCK
# ══════════════════════════════════════════════════════════════════════════════

SYMBOL_CUR = {"$": "USD", "£": "GBP", "€": "EUR", "₹": "INR"}


class InboxSkip(Exception):
    """No app password configured for the inbox — a clean skip, never a crash
    (mirrors imap_rail.InboxSkip)."""


# ── normalization + attribution ───────────────────────────────────────────────
def _norm(s: str) -> str:
    """lowercase, de-accent, drop punctuation, collapse spaces — so 'Adélye Ltd.'
    and 'adelye ltd' and 'ADELYE  LTD' all key the same."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


# company suffixes stripped when matching a billing entity to an alias
_SUFFIX = re.compile(r"\b(ltd|llc|inc|corp|co|gmbh|bv|b v|pvt|limited|company)\b")


def attribute(payer: Optional[str], reg=None) -> Optional[str]:
    """Payer name -> client_id (registry slug), or None -> mapping queue. NEVER
    guesses. Chain: finance billing-entity aliases (config.PAYER_ALIASES) ->
    registry.match_name -> suffix-stripped retry. Unknown stays None."""
    if not payer:
        return None
    aliases = getattr(config, "PAYER_ALIASES", {})
    key = _norm(payer)
    if key in aliases:
        return aliases[key]
    stripped = re.sub(r"\s+", " ", _SUFFIX.sub(" ", key)).strip()
    if stripped in aliases:
        return aliases[stripped]
    if reg is not None:
        hit = reg.match_name(payer) or reg.match_name(stripped)
        if hit:
            return hit
    return None


# ── pure parsers ──────────────────────────────────────────────────────────────
def _to_minor(amt: str) -> Optional[int]:
    """Amount string -> integer minor units, tolerant of BOTH number locales the
    providers actually emit: US/UK "3,500.00" / "1,615" (comma thousands, dot
    decimal) AND German "349,00" / "1.234,56" (dot thousands, comma decimal). The
    decimal separator is the RIGHTMOST of '.'/','; a lone comma is a decimal only
    when exactly two digits trail it (so "1,615" stays 1615, "349,00" is 349.00)."""
    s = (amt or "").strip()
    if not s:
        return None
    has_dot, has_comma = "." in s, "," in s
    if has_dot and has_comma:
        if s.rfind(",") > s.rfind("."):                  # German: dot thousands, comma decimal
            s = s.replace(".", "").replace(",", ".")
        else:                                            # US: comma thousands, dot decimal
            s = s.replace(",", "")
    elif has_comma:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) == 2:       # "349,00" -> decimal
            s = s.replace(",", ".")
        else:                                            # "1,615" / "1,234,567" -> thousands
            s = s.replace(",", "")
    try:
        return money.to_minor(s)
    except Exception:                                    # noqa: BLE001 — bad number
        return None


def _currency_of(m) -> str:
    """The currency for an amount match: a named 3-letter code wins, else the
    symbol (named 'sym' group or any symbol inside the match), else USD."""
    cur = m.groupdict().get("cur")
    if cur:
        return cur.upper()
    sym = m.groupdict().get("sym")
    if not sym:
        found = re.search(r"[$£€₹]", m.group(0))
        sym = found.group(0) if found else None
    return SYMBOL_CUR.get(sym, "USD")


def _find_amount(body: str, patterns) -> tuple[Optional[int], Optional[str]]:
    for pat in patterns:
        m = pat.search(body or "")
        if not m:
            continue
        minor = _to_minor(m.group("amt"))
        if minor is None:
            continue
        return minor, _currency_of(m)
    return None, None


def _date_only(iso: Optional[str]) -> Optional[str]:
    return iso[:10] if iso else None


# Wise subjects are anchored at the start (payer leads), Stripe subjects are found
# anywhere ("Your receipt from ...", "Ihr Zahlungsbeleg ... von ..."). The subject
# match ALSO gates whether an allow-listed sender is even a payment: a Wise
# marketing/statement mail matches nothing, so it is skipped, never quarantined.
_SUBJECT = {
    "wise": (WISE_SUBJECT_PATTERNS, False),      # (patterns, use_search)
    "stripe": (STRIPE_SUBJECT_PATTERNS, True),
}
_BODY_PAYER = {"wise": WISE_BODY_PAYER, "stripe": STRIPE_BODY_MERCHANT}
_AMOUNT = {"wise": WISE_AMOUNT_PATTERNS, "stripe": STRIPE_AMOUNT_PATTERNS}


def _subject_payer(provider: str, subject: str) -> tuple[bool, Optional[str]]:
    """(subject-is-a-payment, payer-from-subject-or-None) for a provider."""
    patterns, use_search = _SUBJECT.get(provider, ((), True))
    for pat in patterns:
        m = (pat.search if use_search else pat.match)(subject or "")
        if m:
            return True, (m.groupdict().get("payer") or "").strip() or None
    return False, None


def payment_subject_matched(provider: str, subject: str) -> bool:
    """Does the subject look like a payment (vs marketing/statement) for the
    provider? The gate that keeps allow-listed non-payment mail out of quarantine."""
    return _subject_payer(provider, subject)[0]


def _first_payer(body: str, patterns) -> Optional[str]:
    for pat in patterns:
        bm = pat.search(body or "")
        if bm:
            return bm.group("payer").strip() or None
    return None


def _parse(provider: str, subject: str, body: str, *, native_id: str,
           email_date: Optional[str]) -> Optional[dict]:
    matched, payer = _subject_payer(provider, subject)
    if not matched:                                      # not a payment subject
        return None
    if not payer:
        payer = _first_payer(body, _BODY_PAYER[provider])
    minor, cur = _find_amount(body, _AMOUNT[provider])
    if minor is None:                                    # payment subject, no amount
        return None
    return _provisional(provider, payer, minor, cur, native_id, email_date)


def parse_wise(subject: str, body: str, *, native_id: str,
               email_date: Optional[str] = None) -> Optional[dict]:
    """A Wise incoming-payment notification -> provisional dict, or None if it is
    not a payment (not a payment subject, or no amount). Payer from subject else body."""
    return _parse("wise", subject, body, native_id=native_id, email_date=email_date)


def parse_stripe(subject: str, body: str, *, native_id: str,
                 email_date: Optional[str] = None) -> Optional[dict]:
    """A Stripe receipt/notification -> provisional dict, or None."""
    return _parse("stripe", subject, body, native_id=native_id, email_date=email_date)


def _provisional(provider: str, payer: Optional[str], minor: int, cur: Optional[str],
                 native_id: str, email_date: Optional[str]) -> dict:
    return {
        "canonical_key": f"{provider}-email:{native_id}",
        "provider": provider,
        "payer": payer,
        "amount_minor": abs(int(minor)),                 # incoming -> positive
        "currency": (cur or "USD").upper(),
        "paid_date": _date_only(email_date),             # body date pinning is a calibration follow-up
        "provider_ref": None,
        "native_id": native_id,
    }


PARSERS: dict[str, Callable[..., Optional[dict]]] = {
    "wise": parse_wise, "stripe": parse_stripe}


def provider_for(from_addr: str) -> Optional[str]:
    """Which parser (if any) owns a sender. Allow-list only; everything else in
    the inbox belongs to the jarvis client-mail rail, not this one."""
    a = (from_addr or "").strip().lower()
    if not a:
        return None
    senders = getattr(config, "PAYMENT_MAIL_SENDERS", {})
    for provider, needles in senders.items():
        for n in needles:
            if a == n or a.endswith("@" + n) or a.endswith("." + n):
                return provider
    return None


def parse(provider: str, subject: str, body: str, *, native_id: str,
          email_date: Optional[str] = None) -> Optional[dict]:
    fn = PARSERS.get(provider)
    return fn(subject, body, native_id=native_id, email_date=email_date) if fn else None


# ── body extraction (payment-rail local: Wise notifications are HTML-only) ────
def _html_to_text(html: str) -> str:
    """HTML -> text, stripping <style>/<script>/<head> CONTENT first. imap_rail's
    generic _message_text only strips TAGS, so a Wise notification's ~1KB inline
    CSS block leaks into the body as text and can poison an unanchored amount
    regex. This is why the Wise amount patterns are anchored to 'received' — but a
    clean body is still cheaper and safer to scan."""
    t = re.sub(r"(?is)<(style|script|head)[^>]*>.*?</\1>", " ", html or "")
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = _html.unescape(t)
    t = t.replace("​", " ").replace("‌", " ")   # zero-width space / non-joiner
    return re.sub(r"[ \t ]+", " ", t)


def _body_text(msg) -> str:
    """text/plain preferred (Stripe carries clean plain text), else cleaned HTML
    (Wise is HTML-only)."""
    if msg.is_multipart():
        plains, htmls = [], []
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                plains.append(imap_rail._decode_part(part))
            elif ctype == "text/html":
                htmls.append(imap_rail._decode_part(part))
        text = "\n".join(p for p in plains if p and p.strip())
        if text.strip():
            return text
        return _html_to_text("\n".join(htmls))
    body = imap_rail._decode_part(msg)
    if msg.get_content_type() == "text/html":
        return _html_to_text(body)
    return body


# ── sender-filtered UID search (All Mail is huge — filter at the IMAP level) ──
def _sender_or_terms(senders: dict) -> list[str]:
    """IMAP SEARCH criteria matching mail FROM any payment-provider domain, as a
    nested OR chain: [a] -> FROM a; [a,b,c] -> OR FROM a OR FROM b FROM c. Without
    this, a bounded fetch over All Mail only ever reaches the mailbox's OLDEST
    mail and never the recent payments (the live bug this rail hit)."""
    domains = [d for needles in senders.values() for d in needles]
    terms = ["FROM", domains[-1]]
    for d in reversed(domains[:-1]):
        terms = ["OR", "FROM", d] + terms
    return terms


def _search(client, last_uid: Optional[int], first_fetch_days: Optional[int],
            now: Optional[datetime], senders: dict) -> list[int]:
    """UID list of payment-sender mail to consider. Same cursor/cold-start shape as
    imap_rail._search, but ANDed with the sender filter so the candidate set stays
    small (tens, not the whole mailbox)."""
    orterms = _sender_or_terms(senders)
    if last_uid is not None:
        crit = ["UID", f"{last_uid + 1}:*"] + orterms
    elif first_fetch_days:
        since = (now or datetime.now(timezone.utc)) - timedelta(days=first_fetch_days)
        crit = ["SINCE", since.strftime("%d-%b-%Y")] + orterms
    else:
        crit = orterms
    typ, data = client.uid("SEARCH", None, *crit)
    raw = data[0] if data else b""
    if isinstance(raw, bytes):
        raw = raw.decode()
    return [int(x) for x in raw.split()] if raw else []


# ── transport (read-only IMAP, reuses imap_rail's proven substrate) ───────────
def collect(inbox: str, cursor: Optional[str], *, max_items: int,
            host: Optional[str] = None, first_fetch_days: Optional[int] = None,
            conn_factory: Optional[Callable] = None,
            pw_fn: Optional[Callable] = None,
            now: Optional[datetime] = None,
            max_seconds: Optional[float] = None,
            clock: Callable[[], float] = time.monotonic) -> tuple[list[dict], Optional[str]]:
    """Return (provisional-or-unparsed dicts, new_cursor) for one inbox. Header
    PEEK -> provider_for; an allow-listed sender whose SUBJECT is a payment (not
    marketing/statement) triggers the body PEEK + parse. A payment subject that
    fails to parse yields an `_unparsed` dict (surfaced/quarantined by the caller
    — never silently dropped); an allow-listed non-payment subject is skipped
    entirely. Read-only; per-inbox UIDVALIDITY-guarded cursor; reads All Mail
    (PAYMENT_MAIL_MAILBOX), where payer notifications are archived. WALL-CLOCK
    CAPPED: past `max_seconds` the loop stops and the cursor advances only over
    what was processed (oldest-first), so a slow mailbox degrades, never hangs.
    Raises InboxSkip when no app password."""
    pw_fn = pw_fn or imap_rail.app_password
    conn_factory = conn_factory or imap_rail._connect
    pw = pw_fn(inbox)
    if not pw:
        raise InboxSkip(f"no app password for {inbox}")
    host = host or config.JARVIS_IMAP_HOST
    mailbox = config.PAYMENT_MAIL_MAILBOX
    client = conn_factory(host, inbox, pw)
    try:
        client.select(mailbox, readonly=True)
        validity = imap_rail._uidvalidity(client, mailbox)
        prev_validity, last_uid = imap_rail._parse_cursor(cursor)
        if last_uid is not None and prev_validity != validity:
            last_uid = None                              # mailbox recreated -> re-fetch by date
        uids = _search(client, last_uid, first_fetch_days, now,
                       config.PAYMENT_MAIL_SENDERS)
        if last_uid is not None:
            uids = [u for u in uids if u > last_uid]
        uids = sorted(set(uids))[:max_items]             # oldest-first, bounded

        out: list[dict] = []
        max_uid = last_uid or 0
        start = clock()
        for uid in uids:
            if max_seconds is not None and (clock() - start) > max_seconds:
                break                                    # degrade: cursor holds at last processed uid
            hdr = imap_rail._fetch_headers(client, uid)
            _, addr = parseaddr(hdr.get("From", ""))
            provider = provider_for(addr)
            # coerce to str: a raw 8-bit (non-RFC2047) header can come back as an
            # email.header.Header, which the regex parsers reject
            subject = str(hdr.get("Subject", "") or "")
            if provider and payment_subject_matched(provider, subject):
                msgid = imap_rail._strip_msgid(str(
                    hdr.get("Message-ID") or hdr.get("Message-Id") or ""))
                native_id = msgid or f"uid:{uid}"
                email_date = imap_rail._to_iso(imap_rail._parse_date(hdr.get("Date", "")))
                body = _body_text(imap_rail._fetch_body(client, uid))
                p = parse(provider, subject, body, native_id=native_id,
                          email_date=email_date)
                if p:
                    p["permalink"] = imap_rail.permalink(inbox, msgid)
                    p["raw_ref"] = f"imap/{inbox}/{uid}"
                    out.append(p)
                else:
                    out.append({"_unparsed": True, "provider": provider,
                                "native_id": native_id, "subject": subject,
                                "raw_ref": f"imap/{inbox}/{uid}"})
            # allow-listed sender, non-payment subject -> skip (no body, no quarantine)
            max_uid = max(max_uid, uid)
        new_cursor = f"{validity}:{max_uid}" if max_uid else cursor
        return out, new_cursor
    finally:
        try:
            client.logout()
        except Exception:                                # noqa: BLE001
            pass


# ── DAL ────────────────────────────────────────────────────────────────────────
# The only OPEN state. Everything else is terminal: reconcile never revisits it,
# staleness never counts it, the mapping queue never asks a human about it.
OPEN_STATE = "provisional"
# A notification reporting a ZERO amount is not a payment and can never acquire a
# ledger twin, so as 'provisional' it is stale by construction and stays stale
# forever. Live case (2026-08-13): three EUR 0.00 Stripe receipts from
# Beglaubigt.de, dated 2026-06-09/11/22, which had been the sole reason the
# payment-mail rail read DEGRADED. Abhijeet: "stripe payments you see of 0 dollar
# are testing that we do for beglaubigt product".
#
# THE RULE IS THE AMOUNT, NOT THE PROVIDER — deliberately. "Ignore Stripe" would
# also swallow the first REAL Stripe payment, silently, forever. A real payment
# has amount_minor != 0, so it still becomes `provisional`, still reconciles
# against the ledger, and still escalates loudly if the import never arrives.
ZERO_VALUE_STATE = "zero_value"


def _initial_state(amount_minor) -> str:
    return ZERO_VALUE_STATE if int(amount_minor or 0) == 0 else OPEN_STATE


def upsert_provisional(conn, p: dict, *, reg=None) -> bool:
    """Idempotent by canonical_key. Attributes the payer -> client_id here.
    Returns True on a fresh insert, False if the row already existed (no-op).
    A zero-amount notification is stored (the evidence is kept, with its
    permalink) but lands directly in the terminal ZERO_VALUE_STATE."""
    ck = p["canonical_key"]
    if conn.execute("SELECT 1 FROM provisional_payments WHERE canonical_key=?",
                    (ck,)).fetchone():
        return False
    client_id = attribute(p.get("payer"), reg)
    conn.execute(
        "INSERT INTO provisional_payments (canonical_key, provider, payer,"
        " client_id, amount_minor, currency, paid_date, provider_ref,"
        " native_id, permalink, raw_ref, state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (ck, p["provider"], p.get("payer"), client_id, p["amount_minor"],
         p["currency"], p.get("paid_date"), p.get("provider_ref"),
         p["native_id"], p.get("permalink"), p.get("raw_ref"),
         _initial_state(p["amount_minor"])))
    return True


# income buckets in the real ledger a provisional payment can reconcile against
_INCOME_BUCKETS = ("revenue", "refund")


def _ledger_twin(conn, prov: dict, *, window_days: int,
                 tolerance_minor: int) -> Optional[str]:
    """The real ledger canonical_key that matches a provisional payment on
    (amount within tolerance, base-USD) AND (paid_date within +/-window), or None.
    Only USD provisional rows base-match directly (base==minor); a non-USD row is
    left for the CSV import + a later FX-aware pass (never falsely matched)."""
    if (prov.get("currency") or "USD").upper() != "USD" or not prov.get("paid_date"):
        return None
    amt = prov["amount_minor"]
    lo = (date.fromisoformat(prov["paid_date"]) - timedelta(days=window_days)).isoformat()
    hi = (date.fromisoformat(prov["paid_date"]) + timedelta(days=window_days)).isoformat()
    marks = ",".join("?" * len(_INCOME_BUCKETS))
    row = conn.execute(
        f"SELECT canonical_key FROM transactions WHERE pnl_bucket IN ({marks})"
        " AND canonical_key IS NOT NULL AND amount_base_minor IS NOT NULL"
        " AND ABS(amount_base_minor - ?) <= ?"
        " AND substr(creation_dt,1,10) BETWEEN ? AND ?"
        " AND canonical_key NOT IN (SELECT matched_canonical FROM"
        "   provisional_payments WHERE matched_canonical IS NOT NULL)"
        " ORDER BY ABS(amount_base_minor - ?) LIMIT 1",
        (*_INCOME_BUCKETS, amt, tolerance_minor, lo, hi, amt)).fetchone()
    return row["canonical_key"] if row else None


def reconcile(conn, *, window_days: Optional[int] = None,
              tolerance_minor: Optional[int] = None,
              now: Optional[datetime] = None) -> dict:
    """Flip every provisional row whose real ledger twin has landed to 'confirmed'.
    NEVER writes `transactions`. Idempotent (only touches state='provisional').
    Returns {confirmed, unmatched, stale_unmatched, zero_value} —
    stale_unmatched are older than window_days with no twin (surfaced loudly by
    the caller); zero_value are non-payment notifications retired this run.

    The zero-amount sweep lives HERE as well as at insert so rows written before
    the rule existed self-heal on the next scheduled run — no manual migration,
    and re-running it is a no-op."""
    window_days = config.PAYMENT_MAIL_RECONCILE_WINDOW_DAYS if window_days is None else window_days
    tolerance_minor = (config.PAYMENT_MAIL_AMOUNT_TOLERANCE_MINOR
                       if tolerance_minor is None else tolerance_minor)
    now = now or datetime.now(config.TZ)
    today = now.date()
    confirmed, unmatched, stale, zero_value = 0, [], [], []
    for r in [dict(x) for x in conn.execute(
            "SELECT * FROM provisional_payments WHERE state=?", (OPEN_STATE,))]:
        if int(r.get("amount_minor") or 0) == 0:
            conn.execute(
                "UPDATE provisional_payments SET state=?, updated_at=?"
                " WHERE canonical_key=?",
                (ZERO_VALUE_STATE, now.isoformat(timespec="seconds"),
                 r["canonical_key"]))
            zero_value.append({"canonical_key": r["canonical_key"],
                               "provider": r.get("provider"),
                               "payer": r.get("payer"),
                               "paid_date": r.get("paid_date")})
            continue
        twin = _ledger_twin(conn, r, window_days=window_days,
                            tolerance_minor=tolerance_minor)
        if twin:
            conn.execute(
                "UPDATE provisional_payments SET state='confirmed',"
                " matched_canonical=?, confirmed_at=?, updated_at=?"
                " WHERE canonical_key=?",
                (twin, now.isoformat(timespec="seconds"),
                 now.isoformat(timespec="seconds"), r["canonical_key"]))
            confirmed += 1
        else:
            unmatched.append(r["canonical_key"])
            if r.get("paid_date") and (
                    today - date.fromisoformat(r["paid_date"])).days > window_days:
                stale.append({"canonical_key": r["canonical_key"],
                              "client_id": r.get("client_id"),
                              "paid_date": r["paid_date"],
                              "amount_usd": _usd(r)})
    return {"confirmed": confirmed, "unmatched": unmatched,
            "stale_unmatched": stale, "zero_value": zero_value}


# ── staleness (the point of the whole thing) ──────────────────────────────────
def _usd(row) -> Optional[str]:
    """USD amount as a fixed-2dp STRING — money stays exact (Decimal), never a
    float, and serializes cleanly to JSON. None for a non-USD row."""
    if row.get("amount_minor") is None or (row.get("currency") or "USD").upper() != "USD":
        return None
    return str(money.from_minor(int(row["amount_minor"])).quantize(Decimal("0.01")))


def staleness(conn) -> dict:
    """Is the ledger BEHIND the email evidence? Compare the newest un-confirmed
    provisional paid_date against the newest ledger income date. If email is
    ahead, the ledger is stale and the Money tab must SAY SO rather than render a
    confidently-wrong monthly total. Pure over the two queries; no clock."""
    ledger_row = conn.execute(
        "SELECT MAX(substr(creation_dt,1,10)) d FROM transactions"
        " WHERE pnl_bucket IN ('revenue','refund')").fetchone()
    ledger_newest = ledger_row["d"] if ledger_row else None
    provs = [dict(r) for r in conn.execute(
        "SELECT canonical_key, client_id, payer, paid_date, amount_minor, currency"
        " FROM provisional_payments WHERE state=? AND paid_date IS NOT NULL"
        " ORDER BY paid_date DESC", (OPEN_STATE,))]
    if not provs:
        return {"stale": False, "count": 0, "ledger_newest": ledger_newest,
                "email_newest": None, "most_recent": None, "unimported": [],
                "floor_usd": 0.0}
    email_newest = provs[0]["paid_date"]
    ahead = [p for p in provs
             if ledger_newest is None or p["paid_date"] > ledger_newest]
    stale = bool(ahead) and (ledger_newest is None or email_newest > ledger_newest)
    top = provs[0]
    floor_minor = sum(int(p["amount_minor"]) for p in ahead
                      if (p.get("currency") or "USD").upper() == "USD")
    floor = str(money.from_minor(floor_minor).quantize(Decimal("0.01")))
    return {
        "stale": stale, "count": len(ahead),
        "ledger_newest": ledger_newest, "email_newest": email_newest,
        "most_recent": {"client_id": top.get("client_id"), "payer": top.get("payer"),
                        "paid_date": top["paid_date"], "amount_usd": _usd(top)},
        "unimported": [{"client_id": p.get("client_id"), "payer": p.get("payer"),
                        "paid_date": p["paid_date"], "amount_usd": _usd(p)}
                       for p in ahead],
        "floor_usd": floor,
    }


def mapping_queue(conn) -> list[dict]:
    """Provisional payments whose payer did not attribute to a client — surfaced
    for a human to map, never guessed. OPEN rows only: a terminal row (confirmed
    or zero_value) is not a question anyone can still answer usefully."""
    return [dict(r) for r in conn.execute(
        "SELECT canonical_key, provider, payer, amount_minor, currency, paid_date"
        " FROM provisional_payments WHERE client_id IS NULL AND state=?"
        " ORDER BY paid_date DESC", (OPEN_STATE,))]


# ── AR view (overdue) ─────────────────────────────────────────────────────────
def _expected_next(last_paid: date, cadence: str, anchor: Optional[str]) -> Optional[date]:
    """The next date a payment is expected AFTER last_paid, given the cadence."""
    if cadence == "biweekly":
        base = date.fromisoformat(anchor) if anchor else last_paid
        d = base
        while d <= last_paid:
            d += timedelta(days=14)
        return d
    if cadence == "monthly_15_30":
        for cand in _month_slots(last_paid):
            if cand > last_paid:
                return cand
    return None


def _month_slots(after: date):
    """The 15th/30th slots from `after`'s month forward (a few months is plenty)."""
    y, m = after.year, after.month
    for _ in range(4):
        import calendar as _cal
        for day in (15, 30):
            dd = min(day, _cal.monthrange(y, m)[1])
            yield date(y, m, dd)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def overdue_for(last_paid: Optional[str], cadence: str, anchor: Optional[str],
                today: date, *, has_email_after: bool, grace_days: int = 3,
                approx_usd: Optional[float] = None) -> Optional[dict]:
    """Pure AR math for one client. Overdue iff today is past the expected-next
    date + grace AND no email signal already reports a payment after last_paid.
    A provisional email payment counts as 'seen' -> NOT overdue (this is exactly
    what stops a client who paid from looking delinquent while the CSV lags)."""
    if has_email_after:
        return None
    if not last_paid:
        return None
    lp = date.fromisoformat(last_paid[:10])
    nxt = _expected_next(lp, cadence, anchor)
    if not nxt:
        return None
    overdue = (today - nxt).days
    if overdue <= grace_days:
        return None
    return {"expected": nxt.isoformat(), "last_paid": lp.isoformat(),
            "overdue_days": overdue, "approx_usd": approx_usd}
