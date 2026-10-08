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
ledger"): connects are priced at what the ledger shows was paid per connect;
revenue per hire is the won client's own earnings from the contract's month on,
attributed by the SAME client ladder the Money and Clients pages use
(data._client_key), so the figures here agree with theirs. Doctor-gated like
Money: on a RED finance doctor the money block is withheld, the funnel is not.

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


def _money(db_path: Optional[Path], facts: list) -> dict:
    from modules.finance import doctor, money as fmoney

    dbp = db_path or config.DB_PATH
    dr = doctor.run(dbp)
    if not dr.green:
        return {"blocked": True, "reds": [c.invariant for c in dr.reds()]}

    conn = _d._ro(db_path)
    try:
        tx = [dict(r) for r in conn.execute(
            "SELECT record_id, source, type, description, pnl_bucket, amount_base_minor, creation_dt,"
            " billed_dt, pnl_month, assignment_company, canonical_key"
            " FROM transactions WHERE canonical_key IS NOT NULL")]
        title_map, id_map = _d._contract_maps(conn)
        contracts = [dict(r) for r in conn.execute(
            "SELECT id, title, job_id, client_company, start_dt FROM contracts")]
    finally:
        conn.close()

    def usd(minor):
        return round(float(fmoney.from_minor(int(minor or 0))), 2)

    purchases = [{"d": (t["creation_dt"] or "")[:10], "m": _d._pnl_month_of(t),
                  "usd": -usd(t["amount_base_minor"]), "desc": t["description"]}
                 for t in tx if t["type"] == "Connect Purchase"]
    purchases.sort(key=lambda p: p["d"])
    price = _price_per_connect(purchases)

    # revenue rows by canonical client — the Money page's own ladder
    by_client: dict = {}
    for t in tx:
        if (t.get("pnl_bucket") or "uncategorized") not in ("revenue", "refund"):
            continue
        who = _d._client_key(t, title_map, id_map)
        if not who:
            continue
        by_client.setdefault(who, []).append(
            {"d": (t["creation_dt"] or "")[:10], "m": _d._pnl_month_of(t),
             "usd": usd(t["amount_base_minor"]), "desc": t["description"]})

    by_job: dict = {}
    for c in contracts:
        if c["job_id"]:
            by_job.setdefault(str(c["job_id"]), []).append(c)

    won: dict = {}
    claimed: dict = {}                       # client -> the first hire that carries its revenue
    for f in sorted((f for f in facts if f["hired"]), key=lambda f: f["sent"] or ""):
        # the contract on the job we applied to, else the same client's first
        # contract after our proposal (clients often hire on a fresh posting)
        cands = list(by_job.get(str(f.get("job") or ""), []))
        if not cands and f.get("co"):
            cands = [c for c in contracts if c["client_company"] == f["co"]
                     and (c["start_dt"] or "") >= (f["sent"] or "")[:10]]
        contract = sorted(cands, key=lambda c: c["start_dt"] or "")[0] if cands else None
        who = (_d._contract_client(contract["client_company"]) if contract else None) \
            or _d._canon_client(f.get("co"))
        start_m = ((contract or {}).get("start_dt") or f["sent"] or "")[:7]
        entry = {"client": who, "contract_id": (contract or {}).get("id"),
                 "contract_title": (contract or {}).get("title"),
                 "start": ((contract or {}).get("start_dt") or "")[:10] or None,
                 "usd": None, "rows": [], "shared_with": None}
        if who and who in claimed:
            entry["shared_with"] = claimed[who]       # counted once, on the first hire
        elif who:
            rows = [r for r in by_client.get(who, []) if (r["m"] or "") >= start_m]
            if rows:
                entry["rows"] = sorted(rows, key=lambda r: r["d"])
                entry["usd"] = round(sum(r["usd"] for r in rows), 2)
            claimed[who] = f["id"]
        won[f["id"]] = entry

    return {"blocked": False, "price_per_connect": price,
            "price_note": "median price per connect over purchases that name their count",
            "purchases": purchases, "won": won}


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
        invites = _invites(conn, facts)
    finally:
        conn.close()
    money = _money(db_path, facts)
    return {"as_of": day, "built": True, "fresh": fresh, "facts": facts, "money": money,
            "insights": insights, "invites": invites}
