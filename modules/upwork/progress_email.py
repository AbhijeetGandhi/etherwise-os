"""The progress email — Monday, Wednesday and Friday at 08:30, and the week on
Sunday (Abhijeet, 2026-10-09: "monday wed friday and sunday").

What it says, in the Pipeline tab's words and from the same two reads
(/api/lifecycle and /api/upwork/funnel, called in-process), since the last one:

  · what is stopping the machine, with the fix (first, when anything is)
  · proposals sent (cold, invitations accepted), opened, wrote back, hired
  · connects spent, against the $150-a-week guide (tracked, not a cap — his
    word the same night: "let's not finalize a cap")
  · money in from clients found on Upwork (only on a GREEN finance doctor)
  · the running test, version against version, and its verdict in words
  · conversations where the client wrote last and nobody answered yet
  · past clients worth a check-in, and the drafts ready to send
  · the scanner: hot jobs found, and how many got a proposal

Once a day at most (a second run the same day is a clean skip). In shadow the
email is an intent in shadow_ledger; live it goes through notify.send_gmail to
his own inbox. It sends nothing to anyone else and writes nothing else.
"""
from __future__ import annotations

import html as _html
import json
import sys
from datetime import date, datetime, timedelta
from typing import Optional

from core import config, db, notify, runner

TASK_NAME = "upwork_progress"
CONNECTS_GUIDE_USD_WEEK = 150.0          # tracked, not enforced (2026-10-09)
COCKPIT_URL = "http://127.0.0.1:5051/#/pipeline"
WAITING_DAYS = 30                        # a conversation older than this is not "waiting"


def _d(s) -> Optional[date]:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def since_last(conn, today: str) -> str:
    """The day of the last email sent, else the natural gap (Sunday: the week;
    otherwise back to the previous email day)."""
    r = conn.execute(
        "SELECT MAX(substr(started_at,1,10)) FROM runs WHERE task_name=? AND status='completed'"
        " AND started_at < ?", (TASK_NAME, today)).fetchone()[0]
    if r:
        return r
    t = _d(today)
    back = 7 if t.weekday() == 6 else (3 if t.weekday() == 0 else 2)
    return (t - timedelta(days=back)).isoformat()


def _when(s) -> Optional[str]:
    """'2026-10-08T20:00:06+05:30' or '2026-10-08 16:15:05 IST' → '2026-10-08 20:00 IST'."""
    if not s:
        return None
    t = str(s).replace("T", " ")
    return t[:16] + " IST" if len(t) >= 16 and t[4] == "-" else str(s)


def _connects_spent(f: dict) -> Optional[float]:
    if f.get("cx") is None and f.get("cb") is None:
        return None
    if f.get("boost") == 1 and f.get("bo") in ("outbid, bid returned", "job closed, bid returned"):
        return f.get("cb")
    return f.get("cx") if f.get("cx") is not None else f.get("cb")


def compose(L: dict, D: dict, since: str, today: str) -> dict:
    """Pure: the email's facts, from the two reads."""
    facts = D.get("facts") or []
    M = D.get("money") or {}
    fresh = [f for f in facts if (f.get("sent") or "")[:10] >= since]
    cold = [f for f in fresh if f.get("k") == "cold"]
    inv = [f for f in fresh if f.get("k") == "invite"]
    opened = [f for f in facts if f.get("op") == 1 and (f.get("od") or "")[:10] >= since]
    replied = []
    for f in facts:
        if f.get("rep") == 1 and f.get("rl") is not None and f.get("sent"):
            try:
                at = datetime.fromisoformat(str(f["sent"]).replace("Z", "+00:00")) + timedelta(hours=float(f["rl"]))
            except ValueError:
                continue
            if at.date().isoformat() >= since:
                replied.append(f)
    spent = [c for c in (_connects_spent(f) for f in fresh) if c is not None]
    price = None if M.get("blocked") else M.get("price_per_connect")
    usd = round(sum(spent) * price, 2) if (price and spent) else None
    days = max(1, (_d(today) - _d(since)).days)
    out = {
        "since": since, "today": today, "days": days,
        "sent_cold": len(cold), "sent_invite": len(inv),
        "opened": len(opened), "replied": len(replied),
        "replied_rows": [{"title": f.get("title"), "co": f.get("co")} for f in replied][:6],
        "connects": sum(spent), "connects_usd": usd,
        "guide_usd": round(CONNECTS_GUIDE_USD_WEEK * days / 7, 2),
        "money_blocked": bool(M.get("blocked")),
    }
    # money in, from every client first met on Upwork (the Funnel's own block)
    if not M.get("blocked"):
        tot, who = 0.0, {}
        for c in (M.get("clients") or {}).values():
            for r in c.get("rows") or []:
                if (r.get("d") or "") >= since:
                    tot += r["usd"]
                    who[c["name"]] = who.get(c["name"], 0) + r["usd"]
        out["money_in"] = round(tot, 2)
        out["money_top"] = sorted(((n, round(u, 2)) for n, u in who.items()), key=lambda x: -x[1])[:5]
        cl = (L.get("clients") or {})
        if not cl.get("blocked"):
            rows = cl.get("rows") or []
            out["new_clients"] = [r["name"] for r in rows
                                  if (r.get("origin") or {}).get("upwork") and (r.get("first_paid") or "") >= since]
            out["paying_now"] = sum(1 for r in rows if r.get("stage") == "paying")
            wb = (cl.get("winback") or {}).get("top") or []
            out["winback"] = [{"name": r["name"], "why": (r.get("why") or [])[:2],
                               "draft": bool(r.get("draft"))} for r in wb[:5]]
    # the test
    out["tests"] = [{"title": t["title"], "verdict": (t.get("scoreboard") or {}).get("verdict"),
                     "arms": [{"arm": a["arm"], "label": a["label"],
                               **{k: ((t.get("scoreboard") or {}).get("arms") or {}).get(a["arm"], {}).get(k, 0)
                                  for k in ("sent", "opened", "replied", "hired")}}
                              for a in t.get("arms") or []]}
                    for t in L.get("tests") or [] if t.get("status") == "running"]
    # conversations where they wrote last
    talks = L.get("talks") or {}
    by_id = {f["id"]: f for f in facts}
    cut = (_d(today) - timedelta(days=WAITING_DAYS)).isoformat()
    waiting = []
    for pid, t in talks.items():
        f = by_id.get(pid)
        if not f or f.get("hired") == 1 or t.get("lf") != "them" or (t.get("l") or "")[:10] < cut:
            continue
        waiting.append({"title": f.get("title"), "co": f.get("co"), "last": (t.get("l") or "")[:10],
                        "room": t.get("rm")})
    out["waiting"] = sorted(waiting, key=lambda w: w["last"], reverse=True)[:8]
    # the scanner
    sc = L.get("scanner") or {}
    hot = [h for h in sc.get("hot") or [] if h[1] >= since]
    out["hot"] = len(hot)
    out["hot_applied"] = sum(1 for h in hot if h[4])
    out["read"] = sum(x[1] for x in sc.get("days") or [] if x[0] >= since)
    out["blockers"] = [{"words": b["words"], "fix": b["fix"], "since": _when(b.get("since"))}
                       for b in (L.get("agents") or {}).get("blockers") or []]
    weekday = _d(today).strftime("%A")
    head = f"{out['sent_cold']} sent, {out['opened']} opened, {out['replied']} wrote back"
    if out["blockers"]:
        head = f"{len(out['blockers'])} thing{'s' if len(out['blockers']) > 1 else ''} stopping the pipeline · " + head
    out["subject"] = (f"Upwork {'week' if weekday == 'Sunday' else 'pipeline'}, "
                      f"{_d(today).strftime('%a %d %b')}: {head}")
    return out


def _e(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def _usd(x) -> str:
    return "—" if x is None else f"${x:,.0f}"


def render_html(p: dict) -> str:
    h = ['<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:14px;'
         'line-height:1.55;color:#111;max-width:640px">']

    def sec(title):
        h.append(f'<h3 style="margin:22px 0 6px;font-size:15px">{_e(title)}</h3>')

    if p["blockers"]:
        h.append('<div style="border:1px solid #f1b8b8;background:#fdf0f0;border-radius:8px;padding:10px 14px">')
        h.append("<b>Stopping the pipeline right now</b><ul style='margin:6px 0 0;padding-left:18px'>")
        for b in p["blockers"]:
            h.append(f"<li>{_e(b['words'])}{' Since ' + _e(b['since']) + '.' if b.get('since') else ''}"
                     f"<br><span style='color:#555'>Fix: {_e(b['fix'])}</span></li>")
        h.append("</ul></div>")
    sec(f"Since {_e(p['since'])} ({p['days']} day{'s' if p['days'] != 1 else ''})")
    inv = (f" and accepted <b>{p['sent_invite']}</b> invitation{'s' if p['sent_invite'] != 1 else ''}"
           if p["sent_invite"] else "")
    h.append(f"<p style='margin:0'>Sent <b>{p['sent_cold']}</b> cold proposal{'s' if p['sent_cold'] != 1 else ''}"
             f"{inv}. Clients opened <b>{p['opened']}</b> and wrote back to <b>{p['replied']}</b>.</p>")
    if p["replied_rows"]:
        h.append("<ul style='margin:6px 0 0;padding-left:18px'>" + "".join(
            f"<li>{_e(r['title'])}{' · ' + _e(r['co']) if r.get('co') else ''}</li>" for r in p["replied_rows"]) + "</ul>")
    spend = (f"{p['connects']:,} connects" + (f" ({_usd(p['connects_usd'])})" if p["connects_usd"] is not None else ""))
    h.append(f"<p style='margin:8px 0 0'>Spent {spend}; the guide for {p['days']} day"
             f"{'s' if p['days'] != 1 else ''} is {_usd(p['guide_usd'])} (tracked, not a cap).</p>")
    if not p.get("money_blocked") and "money_in" in p:
        sec("Money in from clients found on Upwork")
        h.append(f"<p style='margin:0'><b>{_usd(p['money_in'])}</b>"
                 + (" — " + ", ".join(f"{_e(n)} {_usd(u)}" for n, u in p["money_top"]) if p["money_top"] else "")
                 + (f". New client{'s' if len(p.get('new_clients') or []) != 1 else ''}: "
                    + ", ".join(_e(n) for n in p["new_clients"]) if p.get("new_clients") else "")
                 + f". {p.get('paying_now', 0)} clients are paying now.</p>")
    for t in p["tests"]:
        sec(f"The test: {t['title']}")
        h.append("<table style='border-collapse:collapse;font-size:13px'><tr>"
                 + "".join(f"<th style='text-align:left;padding:2px 10px 2px 0;color:#555'>{x}</th>"
                           for x in ("Version", "Sent", "Opened", "Wrote back", "Hired")) + "</tr>")
        for a in t["arms"]:
            h.append("<tr>" + "".join(f"<td style='padding:2px 10px 2px 0'>{_e(v)}</td>" for v in (
                f"{a['arm']} · {a['label']}", a["sent"], a["opened"], a["replied"], a["hired"])) + "</tr>")
        h.append(f"</table><p style='margin:6px 0 0'>Verdict: <b>{_e(t['verdict'])}</b></p>")
    if p["waiting"]:
        sec("They wrote last — worth a look")
        h.append("<ul style='margin:0;padding-left:18px'>" + "".join(
            f"<li>{_e(w['co'] or 'a client')}: {_e(w['title'])} <span style='color:#555'>(last message {_e(w['last'])})</span></li>"
            for w in p["waiting"]) + "</ul>")
    if p.get("winback"):
        sec("Past clients worth a check-in")
        h.append("<ul style='margin:0;padding-left:18px'>" + "".join(
            f"<li><b>{_e(w['name'])}</b>: {_e('; '.join(w['why']))}"
            f"{' · <i>draft ready</i>' if w['draft'] else ''}</li>" for w in p["winback"]) + "</ul>")
    sec("The scanner")
    h.append(f"<p style='margin:0'>Read {p['read']:,} jobs and flagged <b>{p['hot']}</b> hot; "
             f"{p['hot_applied']} of them got a proposal.</p>")
    h.append(f"<p style='margin:22px 0 0;color:#555'>Everything here opens in the cockpit: "
             f"<a href='{COCKPIT_URL}'>Pipeline</a>.</p></div>")
    return "".join(h)


def run(ctx, _send=None, _today: Optional[str] = None) -> dict:
    from modules.cockpit import lifecycle, proposal_funnel
    from modules.cockpit import data as _cd
    dbp = ctx.db_path or config.DB_PATH
    today = _today or _cd._ist_today()
    with db.connect(dbp) as conn:
        sent_today = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE task_name=? AND status='completed'"
            " AND substr(started_at,1,10)=? AND id != ?", (TASK_NAME, today, ctx.run_id)).fetchone()[0]
        if sent_today:
            raise runner.TaskSkip(f"the progress email for {today} already went")
        since = since_last(conn, today)
    L = lifecycle.lifecycle(dbp, today)
    D = proposal_funnel.funnel(dbp, today)
    p = compose(L, D, since, today)
    metrics = {k: p[k] for k in ("since", "sent_cold", "opened", "replied", "connects", "hot")}
    if ctx.shadow:
        ctx.record_shadow_write(target="email", operation="send", entity="upwork_progress",
                                entity_key=today, payload={"subject": p["subject"]})
        return {**metrics, "sent": "shadow"}
    ctx.require_live("progress email")
    try:
        (_send or notify.send_gmail)(p["subject"], render_html(p))
    except Exception as exc:                               # noqa: BLE001
        raise RuntimeError(f"progress email not sent: {exc!r}") from exc
    return {**metrics, "sent": "sent"}


def main() -> int:
    result = runner.run_task(TASK_NAME, run, module="upwork")
    print(json.dumps({"run_id": result.run_id, "status": result.status, "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
