"""AM/PM full-digest briefs. Gathers the situational picture (top nudges by
urgency, the full nudge digest by department, open/overdue commitments, sales
movement, system health), lets Opus write the connecting NARRATIVE, and renders
it to: a draft HTML artifact in var/briefs/ (SHADOW — never sent), the terminal
/brief (plain text), and the cockpit (via data.latest_brief). The human reads
and acts; nothing auto-sends.

AM (11:00): overnight movement, owed replies + drafts, commitments, top-3, health.
PM (20:00): what closed today, what's slipping, tomorrow's setup, open commitments.
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
    return {"spend_today": round(spent, 2), "cap": config.DAILY_HARD_LIMIT_USD,
            "failed_runs_24h": fails,
            "last_sweep": sweep["last_swept_at"] if sweep else None}


def build_brief(db_path, slot: str, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(config.TZ)
    active = nudges.active_nudges(db_path, now=now)
    top = active[:config.COS_BRIEF_TOPN]
    by_dept: dict = {}
    for n in active:
        by_dept.setdefault(n["dept"], []).append(n)
    ours = commitments.open_commitments(db_path, who="us")
    today = now.strftime("%Y-%m-%d")
    overdue = [c for c in ours if c["due"] and c["due"][:10] < today]
    due_soon = [c for c in ours if c["due"] and today <= c["due"][:10]
                <= (now + timedelta(days=config.COS_COMMITMENT_DUE_DAYS)
                    ).strftime("%Y-%m-%d")]
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
                        "open_ours": len(ours), "awaiting_client": len(awaiting)},
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
        "commitments": {"overdue": [c["text"] for c in brief["commitments"]["overdue"]],
                        "due_soon": [c["text"] for c in brief["commitments"]["due_soon"]],
                        "open_ours": brief["commitments"]["open_ours"]},
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
        parts.append("<h3>Commitments</h3><ul>")
        for x in c["overdue"]:
            parts.append(f"<li style='color:#b00'>OVERDUE: {html.escape(x['text'])}"
                         f" ({html.escape((x['due'] or '')[:10])})</li>")
        for x in c["due_soon"]:
            parts.append(f"<li>Due soon: {html.escape(x['text'])}"
                         f" ({html.escape((x['due'] or '')[:10])})</li>")
        parts.append("</ul>")
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
                 f"{s['failed_runs_24h']} · last sweep {s['last_sweep'] or 'n/a'}"
                 f" · {brief['commitments']['open_ours']} open commitments, "
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
        L.append("\nCOMMITMENTS:")
        L += [f"  OVERDUE: {x['text']} ({(x['due'] or '')[:10]})" for x in c["overdue"]]
        L += [f"  due soon: {x['text']} ({(x['due'] or '')[:10]})" for x in c["due_soon"]]
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
             f"{s['failed_runs_24h']} · {c['open_ours']} open commitments")
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
    brief = build_brief(db_path, slot, now)
    narr = _narrate(db_path, brief)
    out_dir = config.COS_BRIEFS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{brief['date']}-{slot}.html"
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
