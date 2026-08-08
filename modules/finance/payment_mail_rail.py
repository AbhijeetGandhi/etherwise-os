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
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
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
# match wins); keep the documented "<Payer> just paid you" first.
WISE_SUBJECT_PATTERNS = (
    re.compile(r"^(?P<payer>.+?)\s+just paid you\b", re.I),
    re.compile(r"^(?P<payer>.+?)\s+just sent you\b", re.I),
    re.compile(r"^(?P<payer>.+?)\s+sent you money\b", re.I),
    re.compile(r"^You(?:'ve| have) (?:been paid|received)\b", re.I),  # no payer in subject
)
# Amount + currency anywhere in the body. Supports 'USD 3,500.00', '3,500.00 USD',
# '$3,500.00'. Group 'cur' optional (defaults USD), 'amt' is the number.
_MONEY = r"(?P<amt>\d[\d,]*(?:\.\d{1,2})?)"
_CUR3 = r"(?P<cur>[A-Z]{3})"
WISE_AMOUNT_PATTERNS = (
    re.compile(rf"{_CUR3}\s*{_MONEY}"),
    re.compile(rf"{_MONEY}\s*{_CUR3}"),
    re.compile(rf"[$£€]\s*{_MONEY}"),
)
# A payer named in the Wise BODY ("received a payment from <name>") when the
# subject form carried none. The terminator deliberately does NOT include a bare
# period, so a company name with internal dots ("Consulting Systems B.V.") is not
# truncated at "B."; it stops at " of "/" on "/comma/newline/end.
WISE_BODY_PAYER = re.compile(
    r"(?:payment|money)\s+from\s+(?P<payer>[A-Za-z0-9&.'\- ]{2,60}?)"
    r"\s*(?:\s+of\b|\s+on\b|,|\n|$)", re.I)

# Stripe receipt/notification (Beglaubigt.de). 'Receipt from <merchant>' names the
# business; 'Amount paid <amt>' carries the money.
STRIPE_SUBJECT_PATTERNS = (
    re.compile(r"Receipt from\s+(?P<payer>.+?)(?:\s*[\[#(]|$)", re.I),
    re.compile(r"Your\s+(?P<payer>.+?)\s+receipt\b", re.I),
    re.compile(r"(?P<payer>.+?)\s+payment (?:received|confirmation)\b", re.I),
)
STRIPE_AMOUNT_PATTERNS = (
    re.compile(rf"Amount paid\s*[:\-]?\s*[$£€]?\s*{_MONEY}\s*{_CUR3}?", re.I),
    re.compile(rf"[$£€]\s*{_MONEY}"),
    re.compile(rf"{_CUR3}\s*{_MONEY}"),
)
STRIPE_BODY_MERCHANT = re.compile(r"Receipt from\s+(?P<payer>[^\n<]{2,60})", re.I)

# ══════════════════════════════════════════════════════════════════════════════
# end CALIBRATION BLOCK
# ══════════════════════════════════════════════════════════════════════════════

SYMBOL_CUR = {"$": "USD", "£": "GBP", "€": "EUR"}


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
    try:
        return money.to_minor(amt.replace(",", ""))
    except Exception:                                    # noqa: BLE001 — bad number
        return None


def _find_amount(body: str, patterns) -> tuple[Optional[int], Optional[str]]:
    for pat in patterns:
        m = pat.search(body or "")
        if not m:
            continue
        minor = _to_minor(m.group("amt"))
        if minor is None:
            continue
        cur = m.groupdict().get("cur")
        if not cur:                                      # symbol form ($/£/€) -> map the leading symbol
            cur = SYMBOL_CUR.get(m.group(0)[0], "USD")
        return minor, cur.upper()
    return None, None


def _date_only(iso: Optional[str]) -> Optional[str]:
    return iso[:10] if iso else None


def parse_wise(subject: str, body: str, *, native_id: str,
               email_date: Optional[str] = None) -> Optional[dict]:
    """A Wise incoming-payment notification -> provisional dict, or None if it is
    not a payment (no amount found). Payer from the subject, else the body."""
    payer = None
    subject_matched = False
    for pat in WISE_SUBJECT_PATTERNS:
        m = pat.match(subject or "")
        if m:
            subject_matched = True
            payer = (m.groupdict().get("payer") or "").strip() or None
            break
    if not subject_matched:
        return None
    if not payer:
        bm = WISE_BODY_PAYER.search(body or "")
        payer = bm.group("payer").strip() if bm else None
    minor, cur = _find_amount(body, WISE_AMOUNT_PATTERNS)
    if minor is None:
        return None
    return _provisional("wise", payer, minor, cur, native_id, email_date)


def parse_stripe(subject: str, body: str, *, native_id: str,
                 email_date: Optional[str] = None) -> Optional[dict]:
    """A Stripe receipt/notification -> provisional dict, or None."""
    payer = None
    subject_matched = False
    for pat in STRIPE_SUBJECT_PATTERNS:
        m = pat.search(subject or "")
        if m:
            subject_matched = True
            payer = (m.groupdict().get("payer") or "").strip() or None
            break
    if not subject_matched:
        return None
    if not payer:
        bm = STRIPE_BODY_MERCHANT.search(body or "")
        payer = bm.group("payer").strip() if bm else None
    minor, cur = _find_amount(body, STRIPE_AMOUNT_PATTERNS)
    if minor is None:
        return None
    return _provisional("stripe", payer, minor, cur, native_id, email_date)


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


# ── transport (read-only IMAP, reuses imap_rail's proven substrate) ───────────
def collect(inbox: str, cursor: Optional[str], *, max_items: int,
            host: Optional[str] = None, first_fetch_days: Optional[int] = None,
            conn_factory: Optional[Callable] = None,
            pw_fn: Optional[Callable] = None,
            now: Optional[datetime] = None) -> tuple[list[dict], Optional[str]]:
    """Return (provisional-or-unparsed dicts, new_cursor) for one inbox. Header
    PEEK -> provider_for; only an allow-listed sender triggers the body PEEK +
    parse. An allow-listed sender that fails to parse yields an `_unparsed` dict
    (surfaced/quarantined by the caller — never silently dropped). Read-only;
    per-inbox UIDVALIDITY-guarded cursor. Raises InboxSkip when no app password."""
    pw_fn = pw_fn or imap_rail.app_password
    conn_factory = conn_factory or imap_rail._connect
    pw = pw_fn(inbox)
    if not pw:
        raise InboxSkip(f"no app password for {inbox}")
    host = host or config.JARVIS_IMAP_HOST
    client = conn_factory(host, inbox, pw)
    try:
        client.select(imap_rail.MAILBOX, readonly=True)
        validity = imap_rail._uidvalidity(client)
        prev_validity, last_uid = imap_rail._parse_cursor(cursor)
        if last_uid is not None and prev_validity != validity:
            last_uid = None                              # mailbox recreated -> re-fetch by date
        uids = imap_rail._search(client, last_uid, first_fetch_days, now)
        if last_uid is not None:
            uids = [u for u in uids if u > last_uid]
        uids = sorted(set(uids))[:max_items]

        out: list[dict] = []
        max_uid = last_uid or 0
        for uid in uids:
            hdr = imap_rail._fetch_headers(client, uid)
            _, addr = parseaddr(hdr.get("From", ""))
            provider = provider_for(addr)
            if provider:
                # coerce to str: a raw 8-bit (non-RFC2047) header can come back
                # as an email.header.Header, which the regex parsers reject
                subject = str(hdr.get("Subject", "") or "")
                msgid = imap_rail._strip_msgid(str(
                    hdr.get("Message-ID") or hdr.get("Message-Id") or ""))
                native_id = msgid or f"uid:{uid}"
                email_date = imap_rail._to_iso(imap_rail._parse_date(hdr.get("Date", "")))
                body = imap_rail._message_text(imap_rail._fetch_body(client, uid))
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
            max_uid = max(max_uid, uid)
        new_cursor = f"{validity}:{max_uid}" if max_uid else cursor
        return out, new_cursor
    finally:
        try:
            client.logout()
        except Exception:                                # noqa: BLE001
            pass


# ── DAL ────────────────────────────────────────────────────────────────────────
def upsert_provisional(conn, p: dict, *, reg=None) -> bool:
    """Idempotent by canonical_key. Attributes the payer -> client_id here.
    Returns True on a fresh insert, False if the row already existed (no-op)."""
    ck = p["canonical_key"]
    if conn.execute("SELECT 1 FROM provisional_payments WHERE canonical_key=?",
                    (ck,)).fetchone():
        return False
    client_id = attribute(p.get("payer"), reg)
    conn.execute(
        "INSERT INTO provisional_payments (canonical_key, provider, payer,"
        " client_id, amount_minor, currency, paid_date, provider_ref,"
        " native_id, permalink, raw_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (ck, p["provider"], p.get("payer"), client_id, p["amount_minor"],
         p["currency"], p.get("paid_date"), p.get("provider_ref"),
         p["native_id"], p.get("permalink"), p.get("raw_ref")))
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
    Returns {confirmed, unmatched, stale_unmatched} — stale_unmatched are older
    than window_days with no twin (surfaced loudly by the caller)."""
    window_days = config.PAYMENT_MAIL_RECONCILE_WINDOW_DAYS if window_days is None else window_days
    tolerance_minor = (config.PAYMENT_MAIL_AMOUNT_TOLERANCE_MINOR
                       if tolerance_minor is None else tolerance_minor)
    now = now or datetime.now(config.TZ)
    today = now.date()
    confirmed, unmatched, stale = 0, [], []
    for r in [dict(x) for x in conn.execute(
            "SELECT * FROM provisional_payments WHERE state='provisional'")]:
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
            "stale_unmatched": stale}


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
        " FROM provisional_payments WHERE state!='confirmed' AND paid_date IS NOT NULL"
        " ORDER BY paid_date DESC")]
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
    for a human to map, never guessed."""
    return [dict(r) for r in conn.execute(
        "SELECT canonical_key, provider, payer, amount_minor, currency, paid_date"
        " FROM provisional_payments WHERE client_id IS NULL AND state!='confirmed'"
        " ORDER BY paid_date DESC")]


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
