"""Where every client is now, and the ones worth a check-in (the Pipeline's
last two stages: still paying / stopped, and winning them back).

Abhijeet, 2026-10-09: "... how much they are paying ... have they stopped ...
reactivation of the client ...". His answer the same night: drafts only, at
most five a week, and he sends them himself.

  lifecycle(money, today, ...)  pure. One row per client who ever paid us:
      how they reached us, first and last payment, how many months they paid
      in, whether they stopped and came back, and where they are now —
      'paying' (paid in the last PAYING_DAYS) or 'stopped'. The money is the
      Funnel's (`proposal_funnel._money`): every payment named by the Money
      page's ladder and made one client by the registry, so a client here is
      the same client, with the same dollars, as on Proposals and Clients.
  rank(rows, today)  pure. The stopped clients worth a check-in, best first,
      scored in the open: every point carries its reason in words, so the
      list can be argued with rather than trusted.
  drafting (the weekly task) lives below the ranking: at most WEEKLY_DRAFTS
      drafts a week, never for someone drafted in the last REST_WEEKS, saved
      as draft_kind='reactivation' — never sent (no send path exists here).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Optional

PAYING_DAYS = 45        # paid in the last 45 days = still paying (a monthly invoice + slack)
QUIET_DAYS = 60         # stopped at least this long before a check-in is suggested
RETURN_GAP_DAYS = 60    # a gap this long, then a payment again = "came back"
MIN_PAID_USD = 250.0    # below this a check-in is not worth his time
WEEKLY_DRAFTS = 5       # his cap, 2026-10-09 ("Drafts, max 5 a week")
REST_WEEKS = 8          # nobody is drafted for twice inside this

# how a client reached us, in the funnel's words (money.clients[*].by) and the
# registry's (found_via)
_HOW = {
    "cold": "a cold proposal on Upwork",
    "invite": "an Upwork invitation",
    "arranged": "a direct offer on Upwork",
    "contract": "an Upwork contract",
    "payments": "Upwork (payments, no proposal on record)",
    "declared": "Upwork (as you said)",
    "referral": "a referral",
    "other": "outside Upwork",
}


def _day(s: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _gap(a: str, b: str) -> int:
    return (_day(b) - _day(a)).days


def lifecycle(money: dict, today: str, *, rooms: Optional[dict] = None,
              open_contracts: Optional[dict] = None, reg=None,
              key_of=None) -> list:
    """One row per client who ever paid us (see the module docstring).

    money           proposal_funnel._money(...) — not blocked
    rooms           {client key: {"room", "name", "last", "from"}} — their
                    newest Upwork conversation, if any
    open_contracts  {client key: n} — Upwork contracts still open
    reg             the client registry (kind / found_via / referred_by)
    key_of          name -> client key, for payments the funnel lists by name
                    only (money.not_upwork); defaults to the name itself
    """
    rooms = rooms or {}
    open_contracts = open_contracts or {}
    t = _day(today)
    pays: dict = {}
    for k, c in (money.get("clients") or {}).items():
        pays[k] = {"key": k, "name": c["name"], "rows": [(r["d"], r["usd"]) for r in c["rows"]],
                   "met": c, "origin": None}
    by_name = {e["name"]: k for k, e in pays.items()}
    for r in money.get("not_upwork") or []:
        who = r.get("who")
        if not who or not r.get("d"):
            continue                      # revenue nobody can be named for stays loose
        k = by_name.get(who) or (key_of(who) if key_of else None) or ("~" + who)
        e = pays.setdefault(k, {"key": k, "name": who, "rows": [], "met": None, "origin": None})
        e["rows"].append((r["d"], r["usd"]))
        o = r.get("origin") or {}
        if o.get("found_via") in ("referral", "other") and not e["origin"]:
            e["origin"] = o

    referred: dict = {}
    for e in pays.values():
        rb = (e["origin"] or {}).get("referred_by")
        if rb:
            referred.setdefault(rb, []).append(e["name"])

    out = []
    for k, e in pays.items():
        rows = sorted((d, u) for d, u in e["rows"] if d)
        paid = [d for d, u in rows if u > 0]
        if not paid:
            continue
        total = round(sum(u for _, u in rows), 2)
        first, last = paid[0], paid[-1]
        returns = [{"after": a, "on": b, "days": _gap(a, b)}
                   for a, b in zip(paid, paid[1:]) if _gap(a, b) >= RETURN_GAP_DAYS]
        quiet = (t - _day(last)).days if t and _day(last) else None
        met = e["met"]
        if met:
            how = met.get("by") or "payments"
            origin = {"how": how, "words": _HOW.get(how, "Upwork"), "upwork": True,
                      "first_met": met.get("first"), "pid": met.get("pid"),
                      "referred_by": None}
        elif e["origin"]:
            fv = e["origin"].get("found_via")
            origin = {"how": fv, "words": _HOW.get(fv, fv), "upwork": False,
                      "first_met": None, "pid": None,
                      "referred_by": e["origin"].get("referred_by")}
        else:
            origin = {"how": None, "words": "not on record", "upwork": False,
                      "first_met": None, "pid": None, "referred_by": None}
        kind = None
        if reg is not None and not str(k).startswith("~"):
            try:
                kind = reg.kind(k)
            except Exception:                              # noqa: BLE001
                kind = None
        out.append({
            "key": k, "name": e["name"], "origin": origin,
            "first_paid": first, "last_paid": last, "paid_usd": total,
            "payments": len(paid), "months": len({d[:7] for d in paid}),
            "quiet_days": quiet,
            "stage": "paying" if quiet is not None and quiet <= PAYING_DAYS else "stopped",
            "returns": returns, "came_back": bool(returns),
            "open_contracts": int(open_contracts.get(k, 0) or 0),
            "room": rooms.get(k),
            "former": kind == "former",
            "referred": sorted(referred.get(e["name"], [])),
        })
    out.sort(key=lambda r: (-(r["paid_usd"] or 0), r["name"]))
    return out


def _money_points(usd: float) -> tuple:
    if usd >= 20000:
        return 40, f"${usd:,.0f} paid in all"
    if usd >= 5000:
        return 30, f"${usd:,.0f} paid in all"
    if usd >= 1000:
        return 20, f"${usd:,.0f} paid in all"
    return 10, f"${usd:,.0f} paid in all"


def _depth_points(months: int) -> tuple:
    if months >= 7:
        return 15, f"paid in {months} different months"
    if months >= 4:
        return 10, f"paid in {months} different months"
    if months >= 2:
        return 5, f"paid in {months} different months"
    return 0, "one month of work"


def _quiet_points(days: int) -> tuple:
    if days <= 180:
        return 25, f"stopped {days} days ago, still fresh"
    if days <= 365:
        return 18, f"stopped {days} days ago"
    if days <= 730:
        return 10, f"stopped over a year ago ({days} days)"
    return 4, f"stopped over two years ago ({days} days)"


def rank(rows: Iterable[dict], today: str, *, drafted: Optional[dict] = None,
         angles: Optional[dict] = None) -> dict:
    """{"top": [...], "left_out": [...]}. `top` is every stopped client worth
    a check-in, best first, each with `score` and `why` (the points in words);
    `left_out` is every other stopped client with the reason it is not
    suggested. `drafted` {key: last week drafted (ISO day)} rests a client for
    REST_WEEKS; `angles` {key: text} is the conversation reader's
    reactivation angle, when it has read them."""
    drafted = drafted or {}
    angles = angles or {}
    t = _day(today)
    top, left = [], []
    for r in rows:
        if r["stage"] != "stopped":
            continue
        if r["former"]:
            left.append(dict(r, reason="marked as not working with again"))
            continue
        if (r["quiet_days"] or 0) < QUIET_DAYS:
            left.append(dict(r, reason=f"stopped under {QUIET_DAYS} days ago"))
            continue
        if (r["paid_usd"] or 0) < MIN_PAID_USD:
            left.append(dict(r, reason=f"paid under ${MIN_PAID_USD:,.0f} in all"))
            continue
        why, score = [], 0
        for pts, words in (_money_points(r["paid_usd"]), _depth_points(r["months"]),
                           _quiet_points(r["quiet_days"])):
            score += pts
            why.append(words)
        if r["came_back"]:
            score += 10
            why.append("came back once before")
        if r["referred"]:
            score += 5
            why.append("referred " + ", ".join(r["referred"]))
        if r["open_contracts"]:
            why.append("an Upwork contract is still open")
        if angles.get(r["key"]):
            score += 5
            why.append("the conversation suggests an angle")
        if not r.get("room"):
            score -= 5
            why.append("no Upwork conversation on record, so email")
        rest = drafted.get(r["key"])
        resting = bool(rest and t and _day(rest) and (t - _day(rest)).days < REST_WEEKS * 7)
        top.append(dict(r, score=score, why=why, angle=angles.get(r["key"]),
                        resting_until=(_day(rest) + timedelta(weeks=REST_WEEKS)).isoformat()
                        if resting else None))
    top.sort(key=lambda x: (-x["score"], -(x["paid_usd"] or 0), x["name"]))
    left.sort(key=lambda x: (-(x["paid_usd"] or 0), x["name"]))
    return {"top": top, "left_out": left}


# ── the weekly drafts (the Win-back agent) ───────────────────────────────────
# At most WEEKLY_DRAFTS a week, best first, nobody twice inside REST_WEEKS,
# never a client the conversation reader may not read (the drafter reads the
# same conversation). Each draft is saved as draft_kind='reactivation' on the
# client's newest Upwork room (or with no room: he sends it by email) and
# logged in reactivation_log. Nothing here sends anything.

TASK_NAME = "upwork_reactivation"
DRAFT_MODEL = "drafting"
LAST_MESSAGES = 20
FACTS = 12

DRAFT_SYSTEM = (
    "You write a short check-in message from Abhijeet, who runs Etherwise (an"
    " automation agency), to a past client who has stopped working with him."
    " The goal is to reopen a friendly conversation, not to sell.\n"
    "Rules:\n"
    "- 40 to 90 words. Warm, plain, specific. Address them by first name if the"
    " material shows it.\n"
    "- Mention ONE concrete thing from the material: what was built for them, or"
    " a next step they mentioned. If the material has nothing concrete, keep it"
    " general rather than invent anything.\n"
    "- No prices, discounts, or claims about results that are not in the material.\n"
    "- No em dashes. No exclamation marks in a row. No 'just checking in'.\n"
    "- End with one simple question that is easy to answer.\n"
    "- Sign off: Abhijeet\n"
    "- Never say this message is automated, part of a list, or how many days it"
    " has been. Never copy a password, key, link with a token, email address or"
    " phone number from the material.")


def week_of(day: str) -> str:
    """The Monday of `day`'s week, ISO — the reactivation_log key."""
    d = _day(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def pick(top: Iterable[dict], already: set, n: int = WEEKLY_DRAFTS) -> list:
    """This week's picks from the ranked list: not resting, not drafted this
    week, at most n."""
    out = []
    for r in top:
        if len(out) >= n:
            break
        if r.get("resting_until") or r["key"] in already:
            continue
        out.append(r)
    return out


def material(conn, cand: dict) -> str:
    """What the drafter reads: who they are, how the work went (no money), the
    reader's angle, what we know about them, and their last messages — every
    line redacted the conversation reader's way."""
    from modules.upwork.conversation_codes import redact
    lines = [f"Client: {cand['name']}",
             f"Worked with us in {cand['months']} different months; first paid"
             f" {cand['first_paid']}, last paid {cand['last_paid']}.",
             f"How they reached us: {(cand.get('origin') or {}).get('words') or 'not on record'}."]
    if cand.get("came_back"):
        lines.append("They came back once before after a long gap.")
    if cand.get("angle"):
        lines.append(f"An angle the conversation suggests: {cand['angle']}")
    key = cand["key"]
    if key and not str(key).startswith("~"):
        facts = [r[0] for r in conn.execute(
            "SELECT fact_text FROM facts WHERE client_id=? AND scope='client'"
            " ORDER BY created_at DESC LIMIT ?", (key, FACTS))]
        if facts:
            lines.append("What we know about them (from calls and documents):")
            lines += ["- " + redact(f)[:300] for f in facts]
    room = (cand.get("room") or {}).get("room")
    if room:
        msgs = [dict(r) for r in conn.execute(
            "SELECT created_dt, lower(direction) AS dir, message FROM messages WHERE thread_id=?"
            " AND lower(direction) IN ('inbound','outbound') ORDER BY created_dt DESC LIMIT ?",
            (room, LAST_MESSAGES))]
        msgs.reverse()
        if msgs:
            lines.append("Their last messages on Upwork (oldest first):")
            lines += [f"[{str(m['created_dt'])[:10]}] {'Client' if m['dir'] == 'inbound' else 'Abhijeet'}:"
                      f" {redact((m['message'] or '').strip())[:800]}" for m in msgs]
    return "\n".join(lines)


def draft_week(conn, ranked: dict, today: str, *, db_path=None, reg=None, _call=None) -> dict:
    """Write this week's drafts. Returns the run's metrics."""
    import json as _json
    from core import runner
    from modules.upwork import conversation_codes as cc
    call = _call or runner.claude_call
    week = week_of(today)
    already = {r[0] for r in conn.execute(
        "SELECT client_id FROM reactivation_log WHERE week=?", (week,))}
    room_name = {r[0]: r[1] for r in conn.execute("SELECT id, room_name FROM threads")}
    eligible = []
    for r in ranked.get("top") or []:
        room = (r.get("room") or {}).get("room")
        if not cc.is_excluded(reg, room, room_name.get(room), r["key"], r["name"]):
            eligible.append(r)
    picks = pick(eligible, already, max(0, WEEKLY_DRAFTS - len(already)))
    m = {"week": week, "already": len(already), "picked": len(picks), "drafted": 0, "cost_usd": 0.0}
    for r in picks:
        res = call(task_name=TASK_NAME, model_key=DRAFT_MODEL, system=DRAFT_SYSTEM,
                   user_content=material(conn, r), purpose=f"win-back {r['key']}",
                   max_output_tokens=400, db_path=db_path)
        body = (getattr(res, "text", "") or "").strip()
        if not body:
            continue
        room = (r.get("room") or {}).get("room")
        cur = conn.execute(
            "INSERT INTO drafts (thread_id, draft_kind, tier, body, word_count, rationale, sent_status)"
            " VALUES (?, 'reactivation', NULL, ?, ?, ?, 'pending')",
            (room, body, len(body.split()), _json.dumps({"score": r["score"], "why": r["why"]})))
        conn.execute(
            "INSERT OR REPLACE INTO reactivation_log (client_id, week, thread_id, draft_id, score, why)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (r["key"], week, room, cur.lastrowid, r["score"], _json.dumps(r["why"])))
        conn.commit()
        m["drafted"] += 1
        m["cost_usd"] = round(m["cost_usd"] + float(getattr(res, "cost_usd", 0) or 0), 4)
    return m


def _run(ctx) -> dict:
    from core import db
    from modules.cockpit import lifecycle
    from modules.cockpit import data as _d
    from core import registry
    today = _d._ist_today()
    out = lifecycle.lifecycle(ctx.db_path, today)
    cl = out.get("clients") or {}
    if cl.get("blocked"):
        from core import runner
        raise runner.TaskSkip("finance doctor RED: who stopped paying is held back")
    try:
        reg = registry.load()
    except Exception:                                      # noqa: BLE001
        reg = None
    with db.connect(ctx.db_path) as conn:
        m = draft_week(conn, cl.get("winback") or {}, today, db_path=ctx.db_path, reg=reg)
    if not m["picked"]:
        from core import runner
        raise runner.TaskSkip("nobody new to draft for this week")
    return m


def main() -> int:
    import sys
    from core import runner
    runner.run_task(TASK_NAME, _run, module="upwork")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
