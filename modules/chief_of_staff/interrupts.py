"""Interrupts (decision #9): the ONLY things that email immediately instead of
waiting for a brief. System failures are handled by core/notify.py (it sends —
real, internal, immediacy is the point). M4 adds the hot-lead interrupt, but it
is SHADOW-GATED: while chief_of_staff is in shadow it records the intended send
to shadow_ledger and does NOT email (v5 still emails hot leads — sending here
too would double-ping). At M1 cutover, shadow flips and it sends. Revisit the
>=16 threshold at cutover (per-lead may be noisier than the scanner digest).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional

from core import config, db

CURSOR = "cos_hotlead_interrupt_id"   # last scored_jobs.id interrupted on


def _subject(lead: dict) -> str:
    return f"HOT LEAD ({lead['score']}): {lead['title'][:80]}"


def _html(lead: dict) -> str:
    return (f"<h2>Hot lead — score {lead['score']}</h2>"
            f"<p><b>{lead['title']}</b></p>"
            f"<p>{lead.get('job_url') or ''}</p>"
            "<p>Interrupt (decision #9): hot leads email immediately; everything "
            "else waits for the brief.</p>")


def check_hot_leads(ctx, now: Optional[datetime] = None,
                    _send: Optional[Callable] = None) -> dict:
    """Find NEW hot leads (score >= threshold, recent, not yet interrupted) and
    interrupt. SHADOW: record_shadow_write only (no email). LIVE: send. Advances
    the cursor either way so a lead interrupts once."""
    now = now or datetime.now(config.TZ)
    db_path = ctx.db_path or config.DB_PATH
    recent_lo = (now - timedelta(days=config.COS_HOTLEAD_RECENT_DAYS)
                 ).strftime("%Y-%m-%d")
    with db.connect(db_path) as conn:
        row = conn.execute("SELECT value FROM sync_cursors WHERE name=?",
                           (CURSOR,)).fetchone()
        last = int(row["value"]) if row else 0
        leads = [dict(r) for r in conn.execute(
            "SELECT id, title, score, job_url FROM scored_jobs WHERE score >= ?"
            " AND CAST(id AS INTEGER) > ? AND substr(first_scored_at,1,10) >= ?"
            " AND status IN ('Scored','Drafted') ORDER BY CAST(id AS INTEGER)",
            (config.HOT_LEAD_THRESHOLD, last, recent_lo))]
    sent = drafted = 0
    maxid = last
    for lead in leads:
        try:
            lid = int(lead["id"])
        except (TypeError, ValueError):
            continue
        if ctx.shadow:
            ctx.record_shadow_write(
                target="email", operation="send", entity="hot_lead_interrupt",
                entity_key=str(lead["id"]),
                payload={"subject": _subject(lead), "score": lead["score"],
                         "title": lead["title"]})
            drafted += 1
        else:
            (_send or _live_send)(_subject(lead), _html(lead))
            sent += 1
        maxid = max(maxid, lid)
    if maxid > last:
        with db.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO sync_cursors (name, value, updated_at) VALUES"
                " (?,?,datetime('now')) ON CONFLICT(name) DO UPDATE SET"
                " value=excluded.value, updated_at=datetime('now')",
                (CURSOR, str(maxid)))
    return {"hot_leads": len(leads), "sent": sent, "drafted_shadow": drafted}


def _live_send(subject: str, html: str) -> None:
    from core import notify
    notify.send_gmail(subject, html)
