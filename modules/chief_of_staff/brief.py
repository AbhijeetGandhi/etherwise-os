"""AM/PM full-digest briefs. Gathers the situational picture (top nudges by
urgency, the full nudge digest by department, open/overdue commitments, sales
movement, system health), lets Opus write the connecting NARRATIVE, and renders
it to: a draft HTML artifact in var/briefs/ (SHADOW — never sent), the terminal
/brief (plain text), and the cockpit (via data.latest_brief). The human reads
and acts; nothing auto-sends.

AM (11:00): overnight movement, owed replies + drafts, commitments, top-3, health.
PM (20:00): a DELTA since the AM brief — replies that came in, new leads, cards
closed, promises made, rails that broke. No repeated lists (his call, 2026-11-01:
the PM brief used to be the AM brief again, and the commitments section in both
was unbounded — 158 OVERDUE lines on 30 Oct, led by April).
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional

from core import config, db, runner, source_health
from modules.chief_of_staff import nudges, commitments

_LOST = config.COS_LOST_STATUSES

NARR_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},     # one-line subject
        "narrative": {"type": "string"},     # 3-6 sentence situational read
    },
    "required": ["headline", "narrative"],
    "additionalProperties": False,
}

NARR_SYSTEM = (
    "You are the Etherwise Chief of Staff writing the {slot} brief. Given the "
    "digest (top nudges, commitments, today's movement, system health), write a "
    "tight, direct situational read — what matters, what's slipping, what to do "
    "first. No fluff, no preamble, no invented numbers (use only what's given). "
    "headline: one line. narrative: 3-6 sentences. You surface; Abhijeet decides."
)


# ── gather ────────────────────────────────────────────────────────────────────

def _movement(conn, slot: str, now: datetime) -> dict:
    """AM = since last evening (~18h); PM = since this morning (~9h). Honest
    counts from modified_dt / first_scored_at."""
    lo = (now - timedelta(hours=18 if slot == "am" else 9)).strftime("%Y-%m-%d")
    qs = ",".join("?" * len(_LOST))
    wins = conn.execute(
        "SELECT COUNT(*) c FROM proposals WHERE status='Won' AND"
        " substr(COALESCE(modified_dt,created_dt),1,10) >= ?", (lo,)).fetchone()["c"]
    losses = conn.execute(
        f"SELECT COUNT(*) c FROM proposals WHERE status IN ({qs}) AND"
        f" substr(COALESCE(modified_dt,created_dt),1,10) >= ?",
        (*_LOST, lo)).fetchone()["c"]
    new_leads = conn.execute(
        "SELECT COUNT(*) c FROM scored_jobs WHERE score >= ? AND"
        " substr(first_scored_at,1,10) >= ?",
        (config.HOT_LEAD_THRESHOLD, lo)).fetchone()["c"]
    return {"window_from": lo, "wins": wins, "losses": losses,
            "new_hot_leads": new_leads}


def _system(conn, now: datetime) -> dict:
    ist_date = now.strftime("%Y-%m-%d")
    spent = conn.execute("SELECT COALESCE(SUM(total_cost_usd),0) s FROM"
                         " claude_usage WHERE ist_date=?", (ist_date,)).fetchone()["s"]
    fails = conn.execute(
        "SELECT COUNT(*) c FROM runs WHERE status='failed' AND started_at >= ?",
        ((now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S"),)).fetchone()["c"]
    sweep = conn.execute("SELECT last_swept_at, last_opus_at FROM sweep_state"
                         " WHERE id=1").fetchone()
    # open criticals mean something again since 2026-11-01 (the runner
    # resolves a run's anomalies on the task's next clean run), so the count
    # is worth one glance here — the only place it reaches him
    crit = conn.execute("SELECT COUNT(*) c FROM anomalies WHERE resolved_at IS NULL"
                        " AND severity='critical'").fetchone()["c"]
    return {"spend_today": round(spent, 2), "cap": config.DAILY_HARD_LIMIT_USD,
            "failed_runs_24h": fails, "open_criticals": crit,
            "last_sweep": sweep["last_swept_at"] if sweep else None}


def build_brief(db_path, slot: str, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(config.TZ)
    active = nudges.active_nudges(db_path, now=now)
    top = active[:config.COS_BRIEF_TOPN]
    by_dept: dict = {}
    for n in active:
        by_dept.setdefault(n["dept"], []).append(n)
    today = now.strftime("%Y-%m-%d")
    # ONE definition of "his commitments", shared with Today's card rule: not
    # stale-flagged, overdue by at most COS_COMMITMENT_STALE_DAYS, or due within
    # COS_COMMITMENT_DUE_DAYS; who='team' / 'client' rows are not his. Grouped
    # by client. The old unbounded list is why "128 commitments due" was the
    # subject he learned to ignore.
    plate = commitments.on_your_plate(db_path, now=now)
    overdue, due_soon = plate["overdue"], plate["due_soon"]
    awaiting = commitments.open_commitments(db_path, who="client")
    with db.connect(db_path) as conn:
        movement = _movement(conn, slot, now)
        system = _system(conn, now)
        # Source health reaches Abhijeet HERE. bin/doctor is pull-based — it
        # only speaks when someone runs it — so a silently dead source could sit
        # broken for days (it did: the connects rail, 84h). The brief is what
        # actually lands in front of him twice a day.
        sources = source_health.report(source_health.source_health(conn, now=now))
    return {
        "slot": slot, "generated_at": now.strftime("%Y-%m-%d %H:%M IST"),
        "date": today, "top": top, "by_dept": by_dept,
        "commitments": {"overdue": overdue, "due_soon": due_soon,
                        "by_client": plate["by_client"],
                        "on_plate": plate["count"],
                        "hidden_old": plate["hidden_old"],
                        # kept for the narrator + older readers: the count that
                        # is actually his, not every open row in the table
                        "open_ours": plate["count"],
                        "awaiting_client": len(awaiting)},
        "movement": movement, "system": system, "sources": sources,
        "active_count": len(active),
    }


# ── Opus narrative (injectable) ──────────────────────────────────────────────

def narrate(db_path, brief: dict) -> dict:
    """Opus writes the situational read. Skipped (cheap 'all quiet') when there
    is nothing to narrate."""
    if not brief["active_count"] and not brief["commitments"]["open_ours"] \
            and not brief["movement"]["wins"] and not brief["movement"]["losses"]:
        return {"headline": f"{brief['slot'].upper()} brief — all quiet",
                "narrative": "No open nudges, commitments, or pipeline movement."}
    digest = {
        "top_nudges": [{"kind": n["kind"], "title": n["title"],
                        "urgency": round(n.get("urgency_live", 0), 1)}
                       for n in brief["top"]],
        "active_by_dept": {d: len(v) for d, v in brief["by_dept"].items()},
        "commitments": {
            # grouped and capped — the narrator used to be handed 158 lines and
            # narrated "a massive overdue backlog" every morning
            "by_client": [{"client": cid or "(unattributed)",
                           "items": [c["text"][:90] for c in rows[:4]],
                           "more": max(0, len(rows) - 4)}
                          for cid, rows in brief["commitments"]["by_client"][:6]],
            "on_plate": brief["commitments"]["on_plate"],
            "older_hidden": brief["commitments"]["hidden_old"]},
        "movement": brief["movement"], "system": brief["system"],
    }
    result = runner.claude_call(
        task_name="cos_brief", model_key="reasoning",
        system=NARR_SYSTEM.format(slot=brief["slot"].upper()),
        schema=NARR_SCHEMA, user_content=json.dumps(digest, indent=1),
        purpose=f"{brief['slot']} brief narrative", max_output_tokens=700,
        db_path=db_path)
    return result.parsed or {"headline": f"{brief['slot'].upper()} brief",
                             "narrative": ""}


# ── render ────────────────────────────────────────────────────────────────────

def _nudge_li(n: dict) -> str:
    due = f" · due {html.escape(n['due'][:10])}" if n.get("due") else ""
    act = f" — {html.escape(n['action'])}" if n.get("action") else ""
    return (f"<li><b>{html.escape(n['title'])}</b>{due}"
            f"<span style='color:#888'>{act}</span></li>")


def _commitments_html(c: dict, today: str) -> str:
    """Grouped by client, newest due first, COS_BRIEF_COMMITMENT_LINES shown,
    the rest named as a count with the Today tab as its home."""
    out = [f"<h3>Commitments <span style='color:#888;font-weight:400;font-size:13px'>"
           f"— {c['on_plate']} on your plate</span></h3>"]
    shown = 0
    for cid, rows in c["by_client"]:
        if shown >= config.COS_BRIEF_COMMITMENT_LINES:
            break
        out.append(f"<div style='margin:6px 0 2px;font-weight:600'>"
                   f"{html.escape(cid or 'No client on the call')}</div><ul style='margin:0'>")
        for x in rows:
            if shown >= config.COS_BRIEF_COMMITMENT_LINES:
                break
            due = (x["due"] or "")[:10]
            late = due < today
            out.append(f"<li style='color:{'#b00' if late else '#333'}'>"
                       f"{'OVERDUE' if late else 'due'} {html.escape(due)}: "
                       f"{html.escape(x['text'][:110])}</li>")
            shown += 1
        out.append("</ul>")
    rest = c["on_plate"] - shown
    if rest > 0:
        out.append(f"<div style='color:#888;font-size:13px'>… and {rest} more on the"
                   f" Today tab.</div>")
    if c.get("hidden_old"):
        out.append(f"<div style='color:#aaa;font-size:12px'>{c['hidden_old']} older or"
                   f" stale-flagged commitments are not listed anywhere; review them"
                   f" with bin/cos-drain-commitments.</div>")
    return "\n".join(out)


def render_html(brief: dict, narr: dict) -> str:
    m, s, c = brief["movement"], brief["system"], brief["commitments"]
    parts = [
        "<div style='font-family:-apple-system,Segoe UI,sans-serif;max-width:680px'>",
        f"<h2 style='margin-bottom:2px'>{html.escape(narr['headline'])}</h2>",
        # the banner has to track the gate, not restate the old default: a
        # mail stamped "not sent" sitting in his inbox is the kind of small
        # lie that makes the whole page untrustworthy
        f"<div style='color:#888;font-size:13px'>{brief['generated_at']} · "
        f"{brief['slot'].upper()} brief"
        f"{'' if email_is_live() else ' · DRAFT (shadow — not sent)'}</div>",
        f"<p style='font-size:15px;line-height:1.5'>{html.escape(narr['narrative'])}</p>",
        "<h3>Top priorities</h3><ol>"
        + ("".join(_nudge_li(n) for n in brief["top"]) or "<li>None</li>") + "</ol>",
    ]
    if c["overdue"] or c["due_soon"]:
        parts.append(_commitments_html(c, brief["date"]))
    parts.append("<h3>Pipeline</h3>"
                 f"<p>Wins {m['wins']} · losses {m['losses']} · new hot leads "
                 f"{m['new_hot_leads']} (since {m['window_from']})</p>")
    parts.append("<h3>The queue</h3><ul>")
    for dept, items in sorted(brief["by_dept"].items()):
        parts.append(f"<li><b>{html.escape(dept)}</b>: {len(items)} open</li>")
    parts.append("</ul>")
    # ── source health: ALWAYS rendered, so a missing line means the check
    # itself broke rather than 'everything is fine'. Loud when anything is off.
    src = brief.get("sources") or {}
    if src:
        if src.get("ok"):
            parts.append("<div style='color:#0f9d58;font-size:13px;margin-top:14px'>"
                         f"Sources: {html.escape(src['headline'])} &#10003;</div>")
        else:
            colour = "#d23f31" if src.get("worst_escalation", 0) >= 3 else "#e8a33d"
            parts.append(
                f"<div style='margin-top:14px;padding:10px 12px;border-radius:6px;"
                f"background:{'#fdeceb' if colour == '#d23f31' else '#fdf4e3'};"
                f"border-left:4px solid {colour}'>"
                f"<b style='color:{colour}'>Sources: {html.escape(src['headline'])}</b>"
                "<ul style='margin:6px 0 0;padding-left:18px'>"
                + "".join(f"<li style='font-size:13px'>{html.escape(x)}</li>"
                          for x in src["lines"])
                + "</ul><div style='color:#888;font-size:12px;margin-top:6px'>"
                  "Numbers fed by these sources are not current.</div></div>")
    parts.append(f"<div style='color:#888;font-size:13px'>System: spend "
                 f"${s['spend_today']}/{s['cap']:.0f} · failed runs (24h) "
                 f"{s['failed_runs_24h']} · open criticals {s.get('open_criticals', 0)}"
                 f" · last sweep {s['last_sweep'] or 'n/a'}"
                 f" · {brief['commitments']['on_plate']} commitments on your plate, "
                 f"{brief['commitments']['awaiting_client']} awaiting client</div>")
    parts.append("</div>")
    return "\n".join(parts)


def render_text(brief: dict, narr: dict) -> str:
    m, s, c = brief["movement"], brief["system"], brief["commitments"]
    L = [f"== {narr['headline']} ==",
         f"{brief['generated_at']} · {brief['slot'].upper()} brief (DRAFT)",
         "", narr["narrative"], "", "TOP PRIORITIES:"]
    L += [f"  {i+1}. {n['title']}" + (f"  (due {n['due'][:10]})" if n.get("due")
          else "") for i, n in enumerate(brief["top"])] or ["  none"]
    if c["overdue"] or c["due_soon"]:
        L.append(f"\nCOMMITMENTS ({c['on_plate']} on your plate):")
        shown = 0
        for cid, rows in c["by_client"]:
            L.append(f"  {cid or '(no client)'}:")
            for x in rows:
                if shown >= config.COS_BRIEF_COMMITMENT_LINES:
                    break
                tag = "OVERDUE" if (x["due"] or "")[:10] < brief["date"] else "due"
                L.append(f"    {tag} {(x['due'] or '')[:10]}: {x['text'][:100]}")
                shown += 1
            if shown >= config.COS_BRIEF_COMMITMENT_LINES:
                break
        rest = c["on_plate"] - shown
        if rest > 0:
            L.append(f"  … and {rest} more on the Today tab")
        if c.get("hidden_old"):
            L.append(f"  ({c['hidden_old']} older or stale-flagged ones are not listed)")
    L.append(f"\nPIPELINE: wins {m['wins']} · losses {m['losses']} · new hot "
             f"leads {m['new_hot_leads']} (since {m['window_from']})")
    L.append("QUEUE: " + " · ".join(f"{d} {len(v)}"
             for d, v in sorted(brief["by_dept"].items())) + f" ({brief['active_count']} total)")
    # always present — see render_html
    src = brief.get("sources") or {}
    if src:
        if src.get("ok"):
            L.append(f"SOURCES: {src['headline']} OK")
        else:
            L.append(f"\nSOURCES: {src['headline']}")
            L += [f"  {x}" for x in src["lines"]]
            L.append("  (numbers fed by these sources are not current)")
    L.append(f"SYSTEM: spend ${s['spend_today']}/{s['cap']:.0f} · fails(24h) "
             f"{s['failed_runs_24h']} · {c['on_plate']} commitments on your plate")
    return "\n".join(L)


# ── the PM delta (2026-11-01) ─────────────────────────────────────────────────

def _since_am(now: datetime) -> str:
    """The AM brief's slot today, as the ledger's naive-IST shape."""
    return now.strftime("%Y-%m-%dT11:00:00")


def build_delta(db_path, now: Optional[datetime] = None) -> dict:
    """What changed since the AM brief — and only that. Replies that came in,
    new hot leads, new leads, cards closed (by him vs by themselves), promises
    made today, runs that failed, money that landed, rails that are off. Every
    figure is a count with the rows behind it; nothing is restated."""
    now = now or datetime.now(config.TZ)
    since = _since_am(now)
    since_date = since[:10]
    out: dict = {"slot": "pm", "since": since,
                 "generated_at": now.strftime("%Y-%m-%d %H:%M IST"),
                 "date": now.strftime("%Y-%m-%d")}
    with db.connect(db_path) as conn:
        out["replies_in"] = [dict(r) for r in conn.execute(
            "SELECT m.created_dt, m.sender_user_name AS who, t.room_name,"
            " substr(m.message,1,120) AS text FROM messages m"
            " JOIN threads t ON t.id=m.thread_id"
            " WHERE m.direction='inbound' AND m.created_dt >= ?"
            " ORDER BY m.created_dt DESC LIMIT 12", (since,))]
        out["mail_in"] = [dict(r) for r in conn.execute(
            "SELECT ts, author, subject, client_id FROM events"
            " WHERE state='mapped' AND COALESCE(direction,'inbound')='inbound'"
            " AND ts >= ? AND client_id IS NOT NULL ORDER BY ts DESC LIMIT 12",
            (since,))]
        out["new_hot_leads"] = [dict(r) for r in conn.execute(
            "SELECT title, score, icp_tier, job_url FROM scored_jobs"
            " WHERE score >= ? AND first_scored_at >= ?"
            " ORDER BY score DESC LIMIT 8", (config.HOT_LEAD_THRESHOLD, since))]
        try:
            out["new_leads"] = [dict(r) for r in conn.execute(
                "SELECT name, company, source, first_seen_at FROM leads"
                " WHERE first_seen_at >= ? ORDER BY first_seen_at DESC LIMIT 8",
                (since,))]
        except Exception:                   # noqa: BLE001 — pre-046 DB
            out["new_leads"] = []
        out["closed_by_you"] = [dict(r) for r in conn.execute(
            "SELECT title, kind FROM nudges WHERE state IN ('done','dismissed')"
            " AND updated_at >= ? ORDER BY updated_at DESC LIMIT 12", (since,))]
        out["self_cleared"] = conn.execute(
            "SELECT COUNT(*) FROM nudges WHERE state='decayed' AND"
            " COALESCE(decayed_at, updated_at) >= ?", (since,)).fetchone()[0]
        out["new_cards"] = [dict(r) for r in conn.execute(
            "SELECT title, kind, client FROM nudges WHERE state='open'"
            " AND created_at >= ? ORDER BY created_at DESC LIMIT 10", (since,))]
        out["promises_made"] = [dict(r) for r in conn.execute(
            "SELECT text, due, client_id FROM commitments WHERE who='us'"
            " AND state='open' AND created_at >= ? ORDER BY id DESC LIMIT 10",
            (since,))]
        out["runs_failed"] = [dict(r) for r in conn.execute(
            "SELECT task_name, COUNT(*) n, MAX(substr(error,1,90)) err FROM runs"
            " WHERE status='failed' AND started_at >= ? GROUP BY task_name"
            " ORDER BY n DESC", (since,))]
        try:
            out["money_in"] = [dict(r) for r in conn.execute(
                "SELECT description, amount_minor, source FROM transactions"
                " WHERE first_seen_at >= ? AND amount_minor > 0"
                " ORDER BY amount_minor DESC LIMIT 8", (since,))]
        except Exception:                   # noqa: BLE001
            out["money_in"] = []
        out["sources"] = source_health.report(
            source_health.source_health(conn, now=now))
    out["quiet"] = not any((out["replies_in"], out["mail_in"], out["new_hot_leads"],
                            out["new_leads"], out["closed_by_you"], out["new_cards"],
                            out["promises_made"], out["runs_failed"], out["money_in"])) \
        and bool(out["sources"].get("ok"))
    bits = []
    if out["replies_in"] or out["mail_in"]:
        bits.append(f"{len(out['replies_in']) + len(out['mail_in'])} replies in")
    if out["new_leads"]:
        bits.append(f"{len(out['new_leads'])} new lead{'s' if len(out['new_leads']) != 1 else ''}")
    if out["new_hot_leads"]:
        bits.append(f"{len(out['new_hot_leads'])} hot jobs")
    if out["closed_by_you"]:
        bits.append(f"{len(out['closed_by_you'])} closed")
    if out["money_in"]:
        bits.append("money landed")
    if out["runs_failed"]:
        bits.append(f"{sum(r['n'] for r in out['runs_failed'])} failed runs")
    if not out["sources"].get("ok"):
        bits.append("a rail is off")
    out["headline"] = ("Since this morning: " + ", ".join(bits)) if bits \
        else "Since this morning: nothing changed"
    out["active_count"] = 0                 # no narrator for a delta
    return out


def render_delta_html(d: dict) -> str:
    def sec(title, items, fmt):
        if not items:
            return ""
        return (f"<h3 style='margin:14px 0 4px'>{html.escape(title)}</h3><ul style='margin:0'>"
                + "".join(f"<li>{fmt(x)}</li>" for x in items) + "</ul>")
    e = html.escape
    parts = ["<div style='font-family:-apple-system,Segoe UI,sans-serif;max-width:680px'>",
             f"<h2 style='margin-bottom:2px'>{e(d['headline'])}</h2>",
             f"<div style='color:#888;font-size:13px'>{d['generated_at']} · PM delta —"
             f" what changed since the 11:00 brief"
             f"{'' if email_is_live() else ' · DRAFT (shadow — not sent)'}</div>"]
    if d["quiet"]:
        parts.append("<p style='font-size:15px'>Nothing came in, nothing closed, every"
                     " rail is fine. Enjoy the evening.</p></div>")
        return "\n".join(parts)
    parts.append(sec("Replies that came in", d["replies_in"],
                     lambda x: f"<b>{e(x['who'] or x['room_name'] or '?')}</b>"
                               f" <span style='color:#888'>{e((x['created_dt'] or '')[11:16])}</span>"
                               f" — {e(x['text'] or '')}"))
    parts.append(sec("Mail from clients", d["mail_in"],
                     lambda x: f"<b>{e(x['client_id'] or '')}</b> · {e(x['author'] or '')}"
                               f" — {e(x['subject'] or '')}"))
    parts.append(sec("New leads", d["new_leads"],
                     lambda x: f"<b>{e(x['name'] or '')}</b> · {e(x['company'] or '')}"
                               f" <span style='color:#888'>via {e(x['source'] or '')}</span>"))
    parts.append(sec("Hot jobs the scanner found", d["new_hot_leads"],
                     lambda x: (f"<a href='{e(x['job_url'] or '#')}'>{e(x['title'] or '')}</a>"
                                f" <span style='color:#888'>score {x['score']}</span>")))
    parts.append(sec("Money that landed", d["money_in"],
                     lambda x: f"${(x['amount_minor'] or 0) / 100:,.2f} — {e(x['description'] or '')}"))
    parts.append(sec("Promises you made today", d["promises_made"],
                     lambda x: f"{e(x['text'] or '')}"
                               f"{' · due ' + e(x['due'][:10]) if x.get('due') else ''}"))
    parts.append(sec("Cards you closed", d["closed_by_you"], lambda x: e(x["title"] or "")))
    if d["self_cleared"]:
        parts.append(f"<div style='color:#888;font-size:13px;margin-top:6px'>"
                     f"{d['self_cleared']} card{'s' if d['self_cleared'] != 1 else ''}"
                     f" cleared by themselves.</div>")
    parts.append(sec("New on Today", d["new_cards"],
                     lambda x: f"{e(x['title'] or '')}"
                               f"{' · ' + e(x['client']) if x.get('client') else ''}"))
    parts.append(sec("Runs that failed", d["runs_failed"],
                     lambda x: f"<code>{e(x['task_name'])}</code> ×{x['n']}"
                               f" <span style='color:#888'>{e(x['err'] or '')}</span>"))
    src = d.get("sources") or {}
    if src and not src.get("ok"):
        parts.append("<div style='margin-top:14px;padding:10px 12px;border-radius:6px;"
                     "background:#fdf4e3;border-left:4px solid #e8a33d'>"
                     f"<b>Sources: {e(src['headline'])}</b><ul style='margin:6px 0 0;padding-left:18px'>"
                     + "".join(f"<li style='font-size:13px'>{e(x)}</li>" for x in src["lines"])
                     + "</ul></div>")
    parts.append("</div>")
    return "\n".join(parts)


def render_delta_text(d: dict) -> str:
    L = [f"== {d['headline']} ==", f"{d['generated_at']} · PM delta since 11:00", ""]
    if d["quiet"]:
        L.append("Nothing came in, nothing closed, every rail is fine.")
        return "\n".join(L)
    def sec(title, items, fmt):
        if items:
            L.append(title.upper() + ":")
            L.extend("  " + fmt(x) for x in items)
    sec("Replies in", d["replies_in"],
        lambda x: f"{x['who'] or x['room_name'] or '?'}: {x['text'] or ''}")
    sec("Mail from clients", d["mail_in"], lambda x: f"{x['client_id']}: {x['subject']}")
    sec("New leads", d["new_leads"], lambda x: f"{x['name']} · {x['company'] or ''} ({x['source']})")
    sec("Hot jobs", d["new_hot_leads"], lambda x: f"{x['title']} (score {x['score']})")
    sec("Money in", d["money_in"], lambda x: f"${(x['amount_minor'] or 0)/100:,.2f} {x['description']}")
    sec("Promises made", d["promises_made"], lambda x: f"{x['text']}")
    sec("Closed by you", d["closed_by_you"], lambda x: x["title"])
    if d["self_cleared"]:
        L.append(f"  ({d['self_cleared']} cleared by themselves)")
    sec("New on Today", d["new_cards"], lambda x: x["title"])
    sec("Failed runs", d["runs_failed"], lambda x: f"{x['task_name']} x{x['n']}")
    src = d.get("sources") or {}
    if src and not src.get("ok"):
        L.append(f"SOURCES: {src['headline']}")
    return "\n".join(L)


# ── write + send ─────────────────────────────────────────────────────────────

def email_is_live() -> bool:
    """Is the brief allowed to leave the machine? Its OWN gate — see
    config.SHADOW_MODE['cos_brief_email']. The module-wide 'chief_of_staff'
    flag stays True, because interrupts.py rides on it and would double-ping
    hot leads the upwork module already sends."""
    return not config.SHADOW_MODE.get("cos_brief_email", True)


def send_brief(brief: dict, body: str, *,
               _send: Optional[Callable] = None) -> dict:
    """Email the rendered brief. Returns {sent, reason}.

    A FAILED SEND IS NOT A FAILED BRIEF. The artifact is already on disk and
    the cockpit already reads it, so SMTP trouble is reported in the metrics
    and left there rather than raised — a brief that exists but could not be
    mailed is a degraded morning, not a broken task, and failing the run would
    also lose the narrative it just paid Opus to write.
    """
    if not email_is_live():
        return {"sent": False, "reason": "shadow: cos_brief_email"}
    send = _send or _default_send()
    subject = f"{brief['slot'].upper()} brief · {brief['date']} — {brief['headline']}"
    try:
        send(subject, body)
    except Exception as exc:                                   # noqa: BLE001
        return {"sent": False, "reason": f"send failed: {type(exc).__name__}"}
    return {"sent": True, "reason": None}


def _default_send() -> Callable:
    from core import notify
    return notify.send_gmail


def write_brief(db_path, slot: str, now: Optional[datetime] = None,
                _narrate: Callable = narrate,
                _send: Optional[Callable] = None) -> dict:
    now = now or datetime.now(config.TZ)
    out_dir = config.COS_BRIEFS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now.strftime('%Y-%m-%d')}-{slot}.html"
    if slot == "pm":
        # the delta: no narrator (it is a list of facts since 11:00), no
        # repeated sections — his call, 2026-11-01
        brief = build_delta(db_path, now)
        body = render_delta_html(brief)
        path.write_text(body)
        brief["narrative"] = ""
    else:
        brief = build_brief(db_path, slot, now)
        narr = _narrate(db_path, brief)
        body = render_html(brief, narr)
        path.write_text(body)
        brief["headline"] = narr["headline"]
        brief["narrative"] = narr["narrative"]
    brief["path"] = str(path)
    # the artifact is written FIRST and unconditionally: the disk copy and the
    # cockpit replay are the record, the email is a delivery of it.
    out = send_brief(brief, body, _send=_send)
    brief["emailed"] = out["sent"]
    if out["reason"]:
        brief["email_reason"] = out["reason"]
    return brief


def latest_brief() -> Optional[dict]:
    """For the cockpit replay — newest draft brief on disk (metadata + html)."""
    d = config.COS_BRIEFS_DIR
    files = sorted(d.glob("*.html")) if d.exists() else []
    if not files:
        return None
    f = files[-1]
    return {"name": f.name, "html": f.read_text()}


def main() -> int:
    import sys
    slot = sys.argv[1] if len(sys.argv) > 1 else "am"
    res = runner.run_task(f"cos_brief_{slot}",
                          lambda ctx: write_brief(ctx.db_path or config.DB_PATH, slot),
                          module="chief_of_staff")
    if res.metrics:
        print(render_text(res.metrics, {"headline": res.metrics.get("headline", ""),
                                        "narrative": res.metrics.get("narrative", "")}))
    print(json.dumps({"run_id": res.run_id, "status": res.status}))
    return 0 if res.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    raise SystemExit(main())
