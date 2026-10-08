"""The Upwork tab's Funnel view — sent → opened → wrote back → hired, and why
(Upwork brief Phase 2, Loop 1; Abhijeet 2026-10-08: "I want to see our
percentage in every stage ... then I want to go into the details").

ONE reader, ONE route (/api/upwork/funnel). It returns the FACTS, one compact
row per proposal, plus the money that belongs to them. Every funnel, split and
percentage on the screen is computed in the browser by static/js/propfunnel.js
from those rows, and every drill opens the very rows the figure counted — so a
figure and the rows behind it are the same array, and cannot disagree
(the cockpit's traceability law, by construction rather than by parity test).

What each fact means (the stores are migration 038's):
  proposal_outcomes  what we sent and what came of it
  proposal_views     what the proposal's own page said when it was read in the
                     browser: the client's first open + the job's live counts.
                     A first open is permanent: once any read saw a date, the
                     proposal stays opened whatever a later read says.

Money (Abhijeet, 2026-10-08: dollars are allowed on this view, "from the
ledger"): connects are priced at what the ledger shows was paid per connect.
Revenue per hire is everything the won client has paid us since we won them,
on Upwork or off it (Wise, Stripe, the bank), through your profile or through
the agency: each payment is named by the SAME ladder the Money and Clients
pages use (data._client_key), then the client registry makes one client of
its several names (see "WHOSE PAYMENT IS IT" below). The `clients` block is
every client first met on Upwork with every payment since, so the page can say
what Upwork brought in over any period. Doctor-gated like Money: on a RED
finance doctor the money block is withheld, the funnel is not (and a win the
ledger proves but Upwork does not mark is then not applied).

No model calls, no writes (cockpit law). `today` is a positional test clock.
"""
from __future__ import annotations

import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core import config
from modules.cockpit import data as _d

_TABLES = ("proposal_outcomes", "proposal_views", "proposal_insights", "upwork_invites_monthly")
_DID = {"nobody", "interviewed", "hired_other", "hired_us"}
_REFUNDED = ("outbid, bid returned", "job closed, bid returned")
_CONNECTS_N = (re.compile(r"\((\d[\d,]*)\)"), re.compile(r"^(\d[\d,]*)\s+connects", re.I))


def _ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    t = str(s).strip().replace("Z", "+00:00").replace(" ", "T", 1)
    t = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", t)
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _hours(a: Optional[str], b: Optional[str]) -> Optional[float]:
    da, db_ = _ts(a), _ts(b)
    if not da or not db_:
        return None
    return round((db_ - da).total_seconds() / 3600, 2)


def _has_tables(conn) -> bool:
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return all(t in have for t in _TABLES)


def _views(conn) -> dict:
    """{proposal_id: {first open, page state, latest counts}} over every read."""
    out: dict = {}
    for v in conn.execute("SELECT * FROM proposal_views ORDER BY read_at"):
        v = dict(v)
        cur = out.setdefault(v["proposal_id"], {"viewed_at": None, "state": None, "read_at": None,
                                                "counts": None})
        if v["viewed_at"] and not cur["viewed_at"]:
            cur["viewed_at"] = v["viewed_at"]
        cur["state"], cur["read_at"] = v["page_state"], v["read_at"]
        if v["total_proposals"]:
            cur["counts"] = v
    return out


def _others_msg_avg(bid, rep, messaged, avg_msg):
    """The average bid of the OTHER people the client messaged. Upwork's average
    includes ours when the client messaged us, so it is taken back out; with
    nobody else messaged there is nothing to compare with (None, not 0)."""
    if bid is None or bid >= 100 or not messaged or avg_msg is None or avg_msg <= 0:
        return None
    if rep:
        if messaged < 2:
            return None
        return round((avg_msg * messaged - bid) / (messaged - 1), 1)
    return round(avg_msg, 1)


def _facts(conn) -> list:
    views = _views(conn)
    rows = []
    for o in conn.execute("SELECT * FROM proposal_outcomes ORDER BY sent_dt DESC"):
        o = dict(o)
        v = views.get(o["proposal_id"]) or {}
        c = v.get("counts") or {}
        if v.get("viewed_at"):
            op = 1
        elif v.get("state") == "not_opened":
            op = 0
        else:
            op = None                                   # never read, or the page is gone
        bid = o["bid_amount"]
        rows.append({
            "id": o["proposal_id"], "sent": o["sent_dt"], "k": o["kind"],
            "job": o["job_id"], "title": o["job_title"], "url": o["job_url"], "cat": o["job_category"],
            "tool": o["tool_in_title"], "jt": o["job_type"], "lvl": o["job_level"],
            "hta": o["hours_to_apply"], "sc": o["scanner_score"],
            "bid": bid, "cx": o["connects_bid"], "cb": o["connects_base"],
            "boost": o["boosted"], "bo": o["boost_outcome"],
            "st": o["upwork_status"], "sr": o["status_reason"],
            "op": op, "od": v.get("viewed_at"), "hto": _hours(o["sent_dt"], v.get("viewed_at")),
            "rep": o["replied"], "rl": o["reply_lag_h"], "hired": o["hired"],
            "did": o["client_did"] if o["client_did"] in _DID else None,
            "co": o["client_company"], "cc": o["client_country"], "csp": o["client_spend"],
            "chr": o["client_hire_rate"], "cj": o["client_jobs"],
            "w": o["letter_words"], "con": o["contractions"], "em": o["em_dashes"],
            "loom": o["has_loom"], "qn": o["questions"], "opf": o["opener_family"],
            "fl": o["first_lines"], "ab": o["opening_about"],
            "pile": c.get("total_proposals"), "popn": c.get("opened_proposals"),
            "pmsg": c.get("messaged"), "avg": c.get("avg_bid"),
            "avgm": _others_msg_avg(bid, o["replied"], c.get("messaged"), c.get("avg_bid_messaged")),
            "src": o["source"],
        })
    return rows


def _fresh(conn, facts: list) -> dict:
    r = conn.execute("SELECT MAX(updated_at), MAX(sent_dt) FROM proposal_outcomes").fetchone()
    v = conn.execute("SELECT MAX(read_at) FROM proposal_views").fetchone()
    newest = r[1]
    behind = None
    if newest:
        have = {f["id"] for f in facts}
        behind = sum(1 for (pid, cd) in conn.execute(
            "SELECT id, created_dt FROM proposals WHERE created_dt > ?", (newest,))
            if str(pid) not in have)
    return {"outcomes_at": r[0], "newest_sent": newest, "views_at": v[0],
            "not_in_funnel": behind}


def _insights(conn) -> dict:
    rows = [dict(x) for x in conn.execute(
        "SELECT id, created_at, window_lo, window_hi, author, model, body_json"
        " FROM proposal_insights ORDER BY created_at DESC, id DESC")]
    if not rows:
        return {"latest": None, "earlier": []}
    latest = rows[0]
    try:
        body = json.loads(latest["body_json"])
    except ValueError:
        body = None
    return {
        "latest": None if body is None else {
            "id": latest["id"], "created_at": latest["created_at"], "author": latest["author"],
            "model": latest["model"], "window": {"lo": latest["window_lo"], "hi": latest["window_hi"]},
            **body},
        "earlier": [{"id": x["id"], "created_at": x["created_at"], "author": x["author"]}
                    for x in rows[1:]],
    }


def _invites(conn, facts: list) -> list:
    nt = {r["month"]: r["not_taken"] for r in conn.execute(
        "SELECT month, not_taken FROM upwork_invites_monthly")}
    acc, hired = {}, {}
    for f in facts:
        if f["k"] != "invite" or not f["sent"]:
            continue
        m = f["sent"][:7]
        acc[m] = acc.get(m, 0) + 1
        hired[m] = hired.get(m, 0) + (1 if f["hired"] else 0)
    if not nt:
        return []
    lo = min(nt)                       # the list's dated months; earlier ones were not counted
    months = sorted({m for m in set(nt) | set(acc) if m >= lo})
    return [{"month": m, "not_taken": nt.get(m), "accepted": acc.get(m, 0),
             "received": (nt[m] + acc.get(m, 0)) if m in nt else None,
             "hired": hired.get(m, 0)} for m in months]


# ── money ────────────────────────────────────────────────────────────────────
#
# WHOSE PAYMENT IS IT (2026-10-08). Abhijeet, on the first version: "globaltize
# hired us from upwork but then we decided to move off upwork and pays us
# through wise ... lot of agency transactions, are you not able to see those?
# ... we generate a lot more revenue in last six months from upwork". He was
# right, for three reasons, each measured on the live ledger first
# (var/prop/rev/audit*.py):
#
#   1. ONE CLIENT, SEVERAL NAMES. Each rail spells a client the way it saw it.
#      Globaltize paid as "Ryan Stomberg" (the agency's hourly line),
#      "Globaltize LLC" (the agency's weekly CSV, through the contract title)
#      and "Globaltize" (Wise), so matching a hire to payments by NAME found
#      none of its Wise money. Same split for Mustafa A / Mustafa Ahmed,
#      Amanda T / Amanda Thomas, Dhanya S / Dhanya Sangolli. Every name now
#      goes through the client registry (config/clients.yaml — exact,
#      normalised match on a name, alias or billing name; the identity spine),
#      so a client's payments on Upwork and off it, through your profile and
#      through the agency, are one pile.
#   2. A HIRE WITH NO CONTRACT ON ITS JOB (25 of 51). More bridges, each one
#      deterministic and tried in this order: the contract on the job; the
#      conversation's client (threads.client_id, else the registry's
#      upwork_rooms); a contract with the job's exact title that started after
#      the proposal; Upwork earnings that carry the job's title (one client
#      only); the client named on the proposal.
#   3. A WIN UPWORK DOES NOT MARK. Globaltize's contract runs on another job,
#      through the agency; Optimize IS has paid by Stripe from day one. A
#      proposal whose conversation belongs to a client who first paid us AFTER
#      it — and whom no other proposal of ours won — is a win, how="after".
#
# A hire is credited with everything its client paid from the month we won
# them (the contract's start, else the proposal's month), on every rail,
# counted ONCE per client (a later hire of the same client points at the
# first). Gross, before Upwork's fee: the Money page's own `revenue` bucket.
#
# The question the tiles could not answer — "how much did Upwork bring in over
# the last six months?" — is the `clients` block: every client first met on
# Upwork (a proposal, an invitation, a contract with no proposal on record, or
# Upwork payments older than these records) with every payment from the month
# we met them. The page sums it over the period picked; `not_upwork` is the
# rest of the revenue, so the share can be said rather than guessed.

def _price_per_connect(purchases: list) -> Optional[float]:
    """What one connect cost, from the purchases whose label says how many
    connects they bought ('300 Connects · …', 'Fees for additional Connects
    (700).'). A membership renewal names no count and is left out."""
    per = []
    for p in purchases:
        for rx in _CONNECTS_N:
            m = rx.search(p["desc"] or "")
            if m:
                n = int(m.group(1).replace(",", ""))
                if n > 0 and p["usd"] > 0:
                    per.append(p["usd"] / n)
                break
    return round(statistics.median(per), 4) if per else None


def _registry():
    """The client registry, or None: a registry that fails to load costs the
    merges (and says so on the page), never the page."""
    try:
        from core import registry
        return registry.load()
    except Exception:                                     # noqa: BLE001
        return None


class _Who:
    """One key per client, however the ledger spells it: the registry id when
    the name is a registry name, alias or billing name (exact after
    registry.norm — never a substring), else '~' + the name itself."""

    def __init__(self, reg):
        from core import registry
        self.reg, self.norm = reg, registry.norm
        self.names: dict = {}
        self.billing: dict = {}
        if reg is not None:
            for cid in reg.matchable_ids():
                n = reg.canonical(cid)
                if n:
                    self.billing.setdefault(registry.norm(n), cid)

    def __call__(self, name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        cid = None
        if self.reg is not None:
            cid = self.reg.match_name(name) or self.billing.get(self.norm(name))
        if cid:
            self.names.setdefault(cid, self.reg.canonical(cid) or name)
            return cid
        key = "~" + _d._deaccent(name)
        self.names.setdefault(key, name)
        return key

    def of_id(self, cid: Optional[str]) -> Optional[str]:
        """A registry id that came from a conversation, not from a name."""
        if cid and self.reg is not None:
            self.names.setdefault(cid, self.reg.canonical(cid) or cid)
        return cid

    def name(self, key: Optional[str]) -> Optional[str]:
        return self.names.get(key, key) if key else None


def _rail(t: dict) -> tuple:
    """(channel, source): 'freelancer' or 'agency' for money paid through
    Upwork — by the profile the contract ran on — and 'direct' for money paid
    to us straight (Wise, Stripe, the bank)."""
    src = t.get("source") or ""
    if src.startswith("Upwork") or _d._is_upwork_row(t):
        return ("agency" if (t.get("profile") or "").lower() == "agency" else "freelancer"), "Upwork"
    if src.startswith("Wise"):
        return "direct", "Wise"
    if src in _d._BANK_SOURCES:
        return "direct", "bank"
    return "direct", src or "—"


def _money(db_path: Optional[Path], facts: list) -> dict:
    from modules.finance import doctor, money as fmoney

    dbp = db_path or config.DB_PATH
    dr = doctor.run(dbp)
    if not dr.green:
        return {"blocked": True, "reds": [c.invariant for c in dr.reds()]}

    conn = _d._ro(db_path)
    try:
        tx = [dict(r) for r in conn.execute(
            "SELECT record_id, source, profile, type, description, pnl_bucket, amount_base_minor,"
            " creation_dt, billed_dt, pnl_month, assignment_company, canonical_key"
            " FROM transactions WHERE canonical_key IS NOT NULL")]
        title_map, id_map = _d._contract_maps(conn)
        contracts = [dict(r) for r in conn.execute(
            "SELECT id, title, job_id, client_company, start_dt, profile FROM contracts")]
        room_of = {str(r[0]): r[1] for r in conn.execute(
            "SELECT proposal_id, room_id FROM proposal_outcomes WHERE room_id IS NOT NULL")}
        room_client = {r[0]: r[1] for r in conn.execute(
            "SELECT id, client_id FROM threads WHERE client_id IS NOT NULL")}
    finally:
        conn.close()
    reg = _registry()
    who = _Who(reg)

    def usd(minor):
        return round(float(fmoney.from_minor(int(minor or 0))), 2)

    purchases = [{"d": (t["creation_dt"] or "")[:10], "m": _d._pnl_month_of(t),
                  "usd": -usd(t["amount_base_minor"]), "desc": t["description"]}
                 for t in tx if t["type"] == "Connect Purchase"]
    purchases.sort(key=lambda p: p["d"])
    price = _price_per_connect(purchases)

    # every payment, keyed by client — the Money page's ladder names it, the
    # registry makes the names one client
    pays: dict = {}
    loose: list = []                 # revenue no client can be named for
    by_label: dict = {}              # an Upwork earning's job title -> the clients it paid
    for t in tx:
        if (t.get("pnl_bucket") or "uncategorized") not in ("revenue", "refund"):
            continue
        name = _d._client_key(t, title_map, id_map)
        key = who(name)
        ch, src = _rail(t)
        row = {"d": (t["creation_dt"] or "")[:10], "m": _d._pnl_month_of(t),
               "usd": usd(t["amount_base_minor"]), "ch": ch, "src": src, "as": name,
               "desc": (t["description"] or "")[:90]}
        if not key:
            loose.append(row)
            continue
        p = pays.setdefault(key, {"rows": [], "aka": set()})
        p["rows"].append(row)
        p["aka"].add(name)
        if ch != "direct":
            label = _d._upwork_label(t.get("description"))
            if label:
                by_label.setdefault(_d._deaccent(label), set()).add(key)

    def earliest(cs):
        return sorted(cs, key=lambda c: c["start_dt"] or "")[0] if cs else None

    by_job: dict = {}
    by_title: dict = {}
    of_client: dict = {}
    for c in contracts:
        c["key"] = who(_d._contract_client(c["client_company"]))
        if c["job_id"]:
            by_job.setdefault(str(c["job_id"]), []).append(c)
        if c["title"]:
            by_title.setdefault(_d._deaccent(c["title"]), []).append(c)
        if c["key"]:
            of_client.setdefault(c["key"], []).append(c)

    def room_key(pid):
        room = room_of.get(str(pid))
        if not room:
            return None
        return who.of_id(room_client.get(room)
                         or (reg.match_upwork_room(room) if reg is not None else None))

    def after(key, day):
        return earliest([c for c in of_client.get(key, []) if (c["start_dt"] or "")[:10] >= day])

    def client_of(f):
        """(key, contract, via) for a proposal Upwork marks hired."""
        sent = (f.get("sent") or "")[:10]
        c = earliest([x for x in by_job.get(str(f.get("job") or ""), []) if x["key"]])
        if c:
            return c["key"], c, "the contract on this job"
        k = room_key(f["id"])
        if k:
            return k, after(k, sent), "the client in this conversation"
        c = earliest([x for x in by_title.get(_d._deaccent(f.get("title") or ""), [])
                      if x["key"] and (x["start_dt"] or "")[:10] >= sent])
        if c:
            return c["key"], c, "a contract with this job's title"
        ks = by_label.get(_d._deaccent(f.get("title") or ""), set())
        if len(ks) == 1:
            return next(iter(ks)), None, "Upwork earnings with this job's title"
        k = who(_d._contract_client(f.get("co")))
        return (k, None, "the client named on the proposal") if k else (None, None, None)

    wins = [(f, *client_of(f), "upwork") for f in facts if f.get("hired")]
    taken = {w[1] for w in wins if w[1]}
    first_paid = {k: min((r["d"] for r in p["rows"] if r["d"]), default=None)
                  for k, p in pays.items()}
    for f in sorted((f for f in facts if not f.get("hired")), key=lambda f: f.get("sent") or ""):
        k, sent = room_key(f["id"]), (f.get("sent") or "")[:10]
        if not k or k in taken or not first_paid.get(k) or first_paid[k] < sent:
            continue
        wins.append((f, k, after(k, sent), "the client in this conversation, who paid you after it",
                     "after"))
        taken.add(k)

    won: dict = {}
    claimed: dict = {}                       # client -> the first win, which carries its money
    for f, k, c, via, how in sorted(wins, key=lambda w: w[0].get("sent") or ""):
        start = ((c or {}).get("start_dt") or "")[:10] or None
        frm = (start or (f.get("sent") or ""))[:7] or None
        e = {"client": k, "name": who.name(k), "how": how, "via": via,
             "contract_id": (c or {}).get("id"), "contract_title": (c or {}).get("title"),
             "start": start, "from": frm, "usd": None, "n": 0, "shared_with": None}
        if k and k in claimed:
            e["shared_with"] = claimed[k]
        elif k:
            rows = [r for r in pays.get(k, {}).get("rows", []) if (r["m"] or "") >= (frm or "")]
            if rows:
                e["usd"] = round(sum(r["usd"] for r in rows), 2)
                e["n"] = len(rows)
            claimed[k] = f["id"]
        won[f["id"]] = e

    # who we first met on Upwork, and when: a proposal or invitation of ours
    # first (it is the most specific answer), then any Upwork contract, then
    # Upwork payments older than the contract records
    met: dict = {}

    def meet(key, day, how):
        if key and day and (key not in met or day < met[key]["first"]):
            met[key] = {"first": day, **how}

    by_id = {f["id"]: f for f in facts}
    for pid, e in won.items():
        f = by_id[pid]
        meet(e["client"], (f.get("sent") or "")[:10], {"by": f.get("k"), "pid": pid})
    for c in contracts:
        meet(c["key"], (c["start_dt"] or "")[:10],
             {"by": "contract", "contract_id": c["id"], "profile": c["profile"]})
    for k, p in pays.items():
        up = [r["d"] for r in p["rows"] if r["ch"] != "direct" and r["d"]]
        if up:
            meet(k, min(up), {"by": "payments"})

    clients: dict = {}
    for k, p in pays.items():
        o = met.get(k)
        if not o:
            loose.extend(p["rows"])
            continue
        m0 = o["first"][:7]
        loose.extend(r for r in p["rows"] if (r["m"] or "") < m0)
        rows = sorted((r for r in p["rows"] if (r["m"] or "") >= m0), key=lambda r: r["d"])
        if not rows:
            continue
        name = who.name(k)
        clients[k] = {"name": name, "aka": sorted(a for a in p["aka"] if a and a != name),
                      "first": o["first"], "by": o["by"], "pid": o.get("pid"),
                      "contract_id": o.get("contract_id"), "profile": o.get("profile"),
                      "rows": rows}

    return {"blocked": False, "price_per_connect": price,
            "price_note": "median price per connect over purchases that name their count",
            "purchases": purchases, "won": won, "clients": clients,
            "not_upwork": sorted(({"d": r["d"], "m": r["m"], "usd": r["usd"], "src": r["src"],
                                   "as": r["as"], "desc": r["desc"]} for r in loose),
                                 key=lambda r: r["d"]),
            "registry": reg is not None}


def _mark_wins(facts: list, money: Optional[dict]) -> None:
    """`hw` says how a hire is known: 'upwork' — Upwork marks the proposal
    hired; 'after' — Upwork marks nothing, but the client in its conversation
    first paid us after it (the contract ran on another job, or off Upwork).
    The second needs the ledger, so on a RED finance doctor it is not applied
    and the funnel shows only what Upwork marks."""
    won = (money or {}).get("won") or {}
    for f in facts:
        f["hw"] = "upwork" if f.get("hired") else None
        w = won.get(f["id"])
        if w and w.get("how") == "after":
            f["hired"], f["hw"] = 1, "after"


def funnel(db_path: Optional[Path] = None, today: Optional[str] = None) -> dict:
    """GET /api/upwork/funnel — the facts, the money, the insights."""
    day = today or _d._ist_today()
    conn = _d._ro(db_path)
    try:
        if not _has_tables(conn):
            return {"as_of": day, "built": False,
                    "reason": "the funnel tables are not in this database yet (migration 038)",
                    "facts": [], "money": None, "insights": {"latest": None, "earlier": []},
                    "invites": [], "fresh": {}}
        facts = _facts(conn)
        fresh = _fresh(conn, facts)
        insights = _insights(conn)
    finally:
        conn.close()
    money = _money(db_path, facts)
    _mark_wins(facts, money)
    conn = _d._ro(db_path)
    try:
        invites = _invites(conn, facts)      # after the wins, so a client won later counts as hired
    finally:
        conn.close()
    return {"as_of": day, "built": True, "fresh": fresh, "facts": facts, "money": money,
            "insights": insights, "invites": invites}
