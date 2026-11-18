"""PayPal money from email — the ledger source for a client who pays a PayPal
invoice instead of an Upwork week (2026-11-18).

Why this exists
---------------
Abhijeet, 18 Nov: "check my email for abhijeetgandhi01, there should be paypal
emails now which are cradle company — now they are paying through paypal
instead of upwork so we need that in there." The Cradle Company's weeks were
Upwork rows (contract 44099615, last week billed 8 Nov). From the week of
9 Nov they are PayPal invoices, and PayPal has no rail here: the Wise and
Stripe rails read other senders, and the Jarvis sweep files "X paid for your
invoice" as noise (it is money, not a message).

What PayPal sends (a real mail, service@intl.paypal.com to inbox 01):

    Subject: Brandi Jordan paid for your invoice # INV-2627-090
    Body:    You received a $825.50 USD payment for your invoice
             Transaction ID   79R6551949983131M
             Invoice No       INV-2627-090
             25.4 x AI & Automation Development (Nov 9 - Nov -15)
             Fee/tax collected by PayPal   $43.21 USD

That is the whole sale: who paid, the gross, PayPal's cut (their fee plus the
GST on it — 4.4% + $0.30, x1.18), and PayPal's own transaction id, which is the
canonical key (`paypal:<id>` / `paypal:FEE-<id>`). Re-reading a month is free.

The money model — the Wise and Globaltize one
---------------------------------------------
The "paid" mail is the REVENUE, in USD, on the day it was paid, under the
payer's name (the client registry turns "Brandi Jordan" into The Cradle
Company, as it does for her Upwork rows). PayPal's cut is a platform_fee line.
The money then reaches the bank on its own — PayPal India sweeps a balance to
the linked account once it releases it (a new seller's funds are held first:
"Some of your funds won't be available straight away", 17 Nov) — and THAT
credit is the settlement, not a second sale. `pair_landings` finds it (an IDFC
credit whose narration names PayPal) and files it `transfer_in`, so the money
counts once. finance doctor #17 holds the line.

What is NOT known yet, and is said rather than guessed (18 Nov): no PayPal
landing has reached the bank ledger, so the narration ("…PAYPAL…" is what
PayPal's Indian entity writes on its NEFT credits) and PayPal's conversion
spread are assumptions with a wide band, and the doctor goes RED rather than
silent if they turn out wrong: a payment the bank statement has had 45 days to
show and no credit names PayPal means the landing is sitting in the statement
under a narration this module does not recognise — as income, counted twice.

Every other PayPal mail is either known and quiet (the invoice going out, the
funds-availability notice, account and marketing mail) or, if its subject
smells of money and no rule here knows it, LOUD — a refund, a dispute, a
direct payment or a transfer notice must not be dropped because the first
mail this was built from was an invoice.

Reads only on the mailbox. Writes `transactions` rows + the pairing fields.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

SOURCE = "PayPal-mail"
SENDER_DOMAIN = "paypal.com"
CURRENCY = "USD"                      # the ledger's base; another currency is loud, not guessed

PAID_SUBJECT = re.compile(
    r"^\s*(?P<payer>.+?)\s+paid for your invoice\s*#?\s*(?P<inv>[A-Za-z0-9][\w\-/]*)", re.I)
RECEIVED = re.compile(
    r"You received a\s+\$(?P<amt>\d[\d,]*\.?\d*)\s*(?P<cur>[A-Z]{3})\s+payment", re.I)
TRANSACTION_ID = re.compile(r"Transaction ID\s+(?P<txn>[A-Z0-9]{10,24})\b")
FEE = re.compile(
    r"Fee/tax collected by PayPal\s+\$(?P<fee>\d[\d,]*\.?\d*)\s*(?P<cur>[A-Z]{3})", re.I)
LINE_ITEM = re.compile(r"(?P<qty>\d+(?:\.\d+)?)\s+x\s+(?P<what>[^\n$]{3,140}?)\s*\n")

# Mail that is PayPal talking about the account, not money moving. Matched on
# the subject; anything from the marketing senders is quiet whatever it says
# ("Get paid the way that works best for you" is an advert).
QUIET_SENDERS = ("news.paypal.com", "donotreply@paypal.com")
QUIET_SUBJECTS = (
    re.compile(r"^We sent your invoice", re.I),                 # the invoice going out
    re.compile(r"funds won.t be available", re.I),              # the new-seller hold notice
    re.compile(r"another way to log in|account is ready|Review your bank accounts"
               r"|password|security|verification code|confirm your email", re.I),
)
# A subject with one of these words that no rule above knows is a money mail of
# a shape this module has not met. It is reported, never dropped.
MONEY_WORDS = re.compile(
    r"\b(paid|payment|received|refund\w*|revers\w*|dispute\w*|chargeback|claim|withdr\w*"
    r"|transfer\w*|money|on hold|released|invoice)\b", re.I)

# The landing: an IDFC credit naming PayPal, on or after the payment and within
# this many days (a held payment is released after up to 21 days, then swept).
LANDING_DAYS = 45
# …for the payment's net (gross less PayPal's cut) at PayPal's own rate, which
# is worse than the ECB pin the ledger values the INR at: up to 7% under, never
# meaningfully over. Unmeasured until the first landing — see the docstring.
LANDING_LOW, LANDING_HIGH = Decimal("0.93"), Decimal("1.005")
# His word, when the rule cannot tell which payment(s) a credit settles:
# {bank canonical_key: (payment canonical_key, ...)}. Empty until needed.
LANDINGS_BY_HAND: dict = {}


def _minor(d: Decimal) -> int:
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _dec(s: str) -> Decimal:
    return Decimal(str(s).replace(",", "").strip())


def parse_paid(subject: str, body: str) -> Optional[dict]:
    """The sale in a "X paid for your invoice" mail, or None if it is not one.
    `incomplete` when the subject matches and the body does not give the three
    facts a ledger row needs (amount in USD, PayPal's transaction id, the fee) —
    a template change, or a currency this ledger does not hold, must alarm."""
    ms = PAID_SUBJECT.search(subject or "")
    if not ms:
        return None
    body = body or ""
    ma, mt, mf = RECEIVED.search(body), TRANSACTION_ID.search(body), FEE.search(body)
    have = {"amount": bool(ma), "transaction_id": bool(mt), "fee": bool(mf),
            "usd": bool(ma and ma.group("cur").upper() == CURRENCY
                        and (not mf or mf.group("cur").upper() == CURRENCY))}
    if not all(have.values()):
        return {"kind": "paid", "incomplete": True, "have": have,
                "invoice": ms.group("inv")}
    ml = LINE_ITEM.search(body)
    what = re.sub(r"[ \t]+", " ", ml.group("what")).strip() if ml else None
    return {
        "kind": "paid", "incomplete": False,
        "payer": re.sub(r"\s+", " ", ms.group("payer")).strip(),
        "invoice": ms.group("inv"), "transaction_id": mt.group("txn"),
        "amount_minor": _minor(_dec(ma.group("amt"))),
        "fee_minor": _minor(_dec(mf.group("fee"))), "currency": CURRENCY,
        "line": f"{ml.group('qty')} x {what}" if ml else None,
    }


def classify(subject: str, sender: str, body: str = "") -> tuple:
    """(verdict, parsed): 'paid' | 'incomplete' | 'quiet' | 'unrecognised'."""
    p = parse_paid(subject, body)
    if p is not None:
        return ("incomplete" if p.get("incomplete") else "paid"), p
    s = (sender or "").lower()
    if any(q in s for q in QUIET_SENDERS) or any(r.search(subject or "") for r in QUIET_SUBJECTS):
        return "quiet", None
    if MONEY_WORDS.search(subject or ""):
        return "unrecognised", None
    return "quiet", None


# ── the ledger rows ──────────────────────────────────────────────────────────

def ledger_rows(p: dict, *, paid_date: str) -> list:
    """The sale and PayPal's cut, keyed on PayPal's own transaction id."""
    txn, inv = p["transaction_id"], p["invoice"]
    line = f" — {p['line']}" if p.get("line") else ""
    rows = [{
        "canonical_key": f"paypal:{txn}", "category": "income", "pnl_bucket": "revenue",
        "amount_minor": p["amount_minor"], "currency": p["currency"],
        "creation_dt": paid_date, "assignment_company": p["payer"],
        "description": f"PayPal payment from {p['payer']} for invoice {inv}{line} [{txn}]",
    }]
    if p.get("fee_minor"):
        rows.append({
            "canonical_key": f"paypal:FEE-{txn}", "category": "fee",
            "pnl_bucket": "platform_fee", "amount_minor": -p["fee_minor"],
            "currency": p["currency"], "creation_dt": paid_date,
            "assignment_company": p["payer"],
            "description": f"PayPal fee and tax on invoice {inv} [{txn}]",
        })
    return rows


def commit(conn, rows: list) -> dict:
    """Insert rows whose canonical_key is new; never update an existing one."""
    ins = skipped = 0
    for r in rows:
        if conn.execute("SELECT 1 FROM transactions WHERE canonical_key=?",
                        (r["canonical_key"],)).fetchone():
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO transactions (canonical_key, dedup_key, source, category,"
            " pnl_bucket, amount_minor, amount_base_minor, currency, fx_rate,"
            " description, assignment_company, creation_dt, pnl_month,"
            " first_seen_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,'1',?,?,?,?,datetime('now'),datetime('now'))",
            (r["canonical_key"], r["canonical_key"], SOURCE, r["category"],
             r["pnl_bucket"], r["amount_minor"], r["amount_minor"], r["currency"],
             r["description"], r["assignment_company"], r["creation_dt"],
             (r["creation_dt"] or "")[:7]))
        ins += 1
    return {"inserted": ins, "already_present": skipped}


# ── the landings: a payment's money reaching the bank ───────────────────────

def payments(conn) -> list:
    """Every PayPal sale in the ledger with its net (gross less PayPal's cut)
    and the bank credit that settled it, if one has been paired."""
    out = []
    for r in conn.execute(
            "SELECT canonical_key, amount_minor, substr(creation_dt,1,10) d,"
            " related_transaction_id landing, assignment_company payer FROM transactions"
            " WHERE source=? AND category='income' ORDER BY creation_dt, canonical_key",
            (SOURCE,)):
        p = dict(r)
        fee = conn.execute(
            "SELECT amount_minor FROM transactions WHERE canonical_key=?",
            (p["canonical_key"].replace("paypal:", "paypal:FEE-", 1),)).fetchone()
        p["net_minor"] = p["amount_minor"] + (fee[0] if fee else 0)      # the fee row is negative
        out.append(p)
    return out


def bank_credits(conn, since: str) -> list:
    """IDFC credits whose narration names PayPal, from `since` on — a card
    refund through PayPal ('POS-VISA/PAYPAL *…') is not one."""
    return [dict(r) for r in conn.execute(
        "SELECT record_id, canonical_key, category, amount_minor, amount_base_minor,"
        " substr(creation_dt,1,10) d, related_transaction_id paired FROM transactions"
        " WHERE source='IDFC' AND amount_minor > 0 AND upper(description) LIKE '%PAYPAL%'"
        " AND upper(description) NOT LIKE 'POS-%' AND category IN ('income','transfer_in')"
        " AND substr(creation_dt,1,10) >= ? ORDER BY creation_dt, canonical_key", (since,))]


def _in_band(base_minor, net_minor) -> bool:
    if base_minor is None:
        return False
    return (Decimal(net_minor) * LANDING_LOW) <= Decimal(base_minor) <= (Decimal(net_minor) * LANDING_HIGH)


def _window(p: dict, day: str) -> bool:
    last = (datetime.fromisoformat(p["d"]) + timedelta(days=LANDING_DAYS)).strftime("%Y-%m-%d")
    return p["d"] <= day <= last


def pair_landings(conn, *, apply: bool = True) -> dict:
    """Pair each PayPal bank credit with the payment(s) it settles and file it
    `transfer_in`. In order: his word (LANDINGS_BY_HAND); ONE open payment whose
    net fits (the oldest, when several weeks are the same amount — any of them
    gives the same books); the SUM of every open payment in the window (PayPal
    sweeps a balance, not a payment). Anything else is left exactly as it is and
    reported `unexplained` — never guessed. Idempotent."""
    pays = payments(conn)
    rep = {"paired": [], "already": [], "unexplained": []}
    if not pays:
        return rep
    by_key = {p["canonical_key"]: p for p in pays}
    for c in bank_credits(conn, pays[0]["d"]):
        if (c["paired"] or "").startswith("paypal:") and c["category"] == "transfer_in":
            rep["already"].append({"landing": c["canonical_key"], "payment": c["paired"]})
            continue
        open_ = [p for p in pays if not p["landing"] and _window(p, c["d"])]
        hand = LANDINGS_BY_HAND.get(c["canonical_key"])
        if hand:
            chosen, how = [by_key[k] for k in hand if k in by_key], "his word"
        else:
            singles = [p for p in open_ if _in_band(c["amount_base_minor"], p["net_minor"])]
            total = sum(p["net_minor"] for p in open_)
            if singles:
                chosen, how = [singles[0]], "one payment"
            elif len(open_) > 1 and _in_band(c["amount_base_minor"], total):
                chosen, how = list(open_), "the open balance"
            else:
                chosen, how = [], None
        if not chosen:
            rep["unexplained"].append({
                "landing": c["canonical_key"], "date": c["d"], "category": c["category"],
                "amount_base_minor": c["amount_base_minor"],
                "open_payments": [p["canonical_key"] for p in open_]})
            continue
        keys = [p["canonical_key"] for p in chosen]
        rep["paired"].append({"landing": c["canonical_key"], "landing_date": c["d"],
                              "was": c["category"], "payments": keys, "how": how,
                              "landing_base_minor": c["amount_base_minor"]})
        for p in chosen:                         # this run's later credits must not reuse them
            p["landing"] = c["canonical_key"]
        if not apply:
            continue
        conn.execute(
            "UPDATE transactions SET category='transfer_in', pnl_bucket='transfer',"
            " related_transaction_id=?, updated_at=datetime('now') WHERE canonical_key=?",
            (keys[0], c["canonical_key"]))
        conn.executemany(
            "UPDATE transactions SET related_transaction_id=?, updated_at=datetime('now')"
            " WHERE canonical_key=?", [(c["canonical_key"], k) for k in keys])
        conn.execute(
            "INSERT INTO audit_log (actor, entity, entity_id, field, old_value, new_value,"
            " source, note) VALUES ('agent:finance-paypal-mail','transactions',?,"
            " 'category',?,'transfer_in','sqlite',?)",
            (c["record_id"] or c["canonical_key"], c["category"],
             f"the bank landing of PayPal payment(s) {', '.join(keys)} ({how}) — the"
             f" 'paid for your invoice' mail is the revenue, this row is the settlement"))
    return rep


def double_counted(conn) -> list:
    """Bank credits still booked as income that `pair_landings` would pair:
    the same dollars as a PayPal sale, on the books twice. Doctor #17."""
    return [p for p in pair_landings(conn, apply=False)["paired"] if p["was"] == "income"]


def ambiguous(conn) -> list:
    """PayPal bank credits booked as income that fit no open payment and not
    their sum, WHILE payments are open in the window — which payment they settle
    cannot be told, and one of them is probably on the books twice."""
    return [u for u in pair_landings(conn, apply=False)["unexplained"]
            if u["category"] == "income" and u["open_payments"]]


def bank_read_through(conn) -> Optional[str]:
    row = conn.execute("SELECT MAX(substr(creation_dt,1,10)) FROM transactions"
                       " WHERE source='IDFC'").fetchone()
    return row[0] if row and row[0] else None


def in_flight(conn, today: str) -> list:
    """Sales whose landing the bank ledger has not shown yet. `overdue` once the
    bank has been read past the last day a landing could arrive: by then the
    credit is in the statement under a narration this module does not know."""
    through = bank_read_through(conn) or ""
    out = []
    for p in payments(conn):
        if p["landing"]:
            continue
        last = (datetime.fromisoformat(p["d"]) + timedelta(days=LANDING_DAYS)).strftime("%Y-%m-%d")
        out.append({"payment": p["canonical_key"], "date": p["d"], "payer": p["payer"],
                    "net_minor": p["net_minor"], "expected_by": last,
                    "age_days": (datetime.fromisoformat(today) - datetime.fromisoformat(p["d"])).days,
                    "overdue": bool(through and through > last)})
    return out
