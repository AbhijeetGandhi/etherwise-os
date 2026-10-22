"""The crowd check (Abhijeet, 2026-10-23).

His pick from the open-rate diagnosis: "Re-read each job's proposal count when
the card is made and again before you apply. Flag 50+, and skip 150+ unless
it's Make.com or Airtable."

WHY. Since 1 Aug, on the jobs he applied to: under 100 proposals, 7 of 32 were
opened by the client; 100-249, 1 of 8; 250+, 0 of 10. The generic AI posts that
fill the board collect 50-400 proposals in their first hours (probe of 23 Oct:
a Best Matches job half an hour old already at 76, one eight hours old at 412),
while his Make.com and Airtable jobs stay small and get opened.

TWO MOMENTS.
  at the card       scan_pipeline asks count_now() for every job about to be
                    carded: the search row's count when it carries one (keyword
                    rows do), else one title search (Best Matches rows never
                    do). A skip here also saves the draft.
  before he applies recheck() re-reads open cards younger than
                    CROWD_RECHECK_HOURS, stalest first, CROWD_RECHECK_PER_RUN a
                    run. The card name carries the band ("· ⚠ 50+ proposals")
                    and is renamed only when the band moves; at
                    CROWD_SKIP_AT a card still in `new` gets a comment and goes
                    to `skipped`.

NEVER TOUCHED. Applied, interview, or anything he moved by hand: the card's
status is read before any write, and a card that has left `new` stops being
watched. Invites: a client asked for him. Jobs with a proposal on them.
Make.com and Airtable (in the title or Upwork's skills) are flagged, never
skipped for a crowd.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from core import config, db
from modules.upwork.feeds import mcp_search

SKIP_REASON = "crowded"
COMMENT = ("Skipped by the crowd check: {count} proposals are already in on this"
           " job. Your rule: skip at {skip}+ unless it's Make.com or Airtable."
           " Reopen it if you still want it.")


def _skills(row) -> list:
    skills = row.get("skills")
    if skills is None:
        try:
            skills = json.loads(row.get("skills_json") or "[]")
        except (TypeError, ValueError):
            skills = []
    return [str(s) for s in (skills or [])]


def exempt(row) -> bool:
    """Make.com or Airtable in the title or among Upwork's skills on the job."""
    text = " ".join([row.get("title") or ""] + _skills(row))
    return bool(re.search(config.CROWD_EXEMPT_PATTERN, text, re.I))


def decide(count, row) -> str:
    """'unknown' (no reading — never treated as zero), 'skip', 'flag' or 'ok'."""
    if not isinstance(count, int):
        return "unknown"
    if count >= config.CROWD_SKIP_AT and not exempt(row):
        return "skip"
    if count >= config.CROWD_FLAG_AT:
        return "flag"
    return "ok"


def band(count) -> Optional[str]:
    """The words on the card. Bands, not the exact count, so a card is renamed
    a handful of times in its life, not every hour."""
    if not isinstance(count, int):
        return None
    if count >= config.CROWD_SKIP_AT:
        return f"⚠ {config.CROWD_SKIP_AT}+ proposals"
    if count >= 100 and config.CROWD_FLAG_AT < 100:
        return "⚠ 100+ proposals"
    if count >= config.CROWD_FLAG_AT:
        return f"⚠ {config.CROWD_FLAG_AT}+ proposals"
    if count >= 20:
        return "20+ proposals"
    return "under 20 proposals"


def count_now(row, mcp, log: Callable[[str], None]) -> tuple:
    """(count or None, where it came from). The search row's count is minutes
    old when the scan claims a job; a row without one gets one title search."""
    pc = row.get("total_applicants")
    if isinstance(pc, int):
        return pc, "search"
    if not mcp:
        return None, "none"
    session, org = mcp
    return (mcp_search.proposal_count(session, org, str(row["id"]),
                                      row.get("title") or "", log), "lookup")


def store(conn, jid: str, count, now: Optional[str] = None) -> None:
    """Record a reading. A failed reading still stamps the time, so the next
    run moves on to the next card instead of retrying this one forever."""
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(count, int):
        conn.execute("UPDATE scored_jobs SET proposals_now=?, proposals_checked_at=?"
                     " WHERE id=?", (count, now, jid))
    else:
        conn.execute("UPDATE scored_jobs SET proposals_checked_at=? WHERE id=?",
                     (now, jid))


def candidates(conn, now: datetime, limit: int) -> list:
    """Open cards worth a fresh reading: carded, still watched, young enough,
    not read within CROWD_RECHECK_EVERY_MIN, no proposal against them, not an
    invite. Stalest (or never read) first."""
    young = (now - timedelta(hours=config.CROWD_RECHECK_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    fresh = (now - timedelta(minutes=config.CROWD_RECHECK_EVERY_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    bid = {r[0] for r in conn.execute(
        "SELECT marketplace_job_id FROM proposals WHERE marketplace_job_id IS NOT NULL")}
    out = []
    for r in conn.execute(
            "SELECT * FROM scored_jobs WHERE clickup_task_id IS NOT NULL"
            " AND status='ClickUp Created' AND crowd_state IS NULL"
            " AND first_scored_at >= ?"
            " AND (proposals_checked_at IS NULL OR proposals_checked_at <= ?)"
            " ORDER BY proposals_checked_at IS NOT NULL, proposals_checked_at,"
            " first_scored_at DESC", (young, fresh)):
        row = dict(r)
        if row["id"] in bid:
            continue
        try:
            bonuses = (json.loads(row.get("score_breakdown_json") or "{}")
                       .get("bonuses") or {})
        except (TypeError, ValueError):
            bonuses = {}
        if bonuses.get("invite", 0) > 0:
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def recheck(ctx, dbp, mcp, tok: str, *, req: Callable, name_for: Callable,
            now: Optional[datetime] = None) -> dict:
    """Re-read the open cards and act on what changed. Every ClickUp write is
    preceded by a read of the card's status; a card not in `new` is left alone
    and stops being watched."""
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    m = {"rechecked": 0, "unknown": 0, "renamed": 0, "skipped": 0,
         "left_new": 0, "errors": 0}
    if not mcp:
        return m
    session, org = mcp
    with db.connect(dbp) as conn:
        rows = candidates(conn, now, config.CROWD_RECHECK_PER_RUN)
    for row in rows:
        count = mcp_search.proposal_count(session, org, row["id"],
                                          row.get("title") or "", ctx.log)
        m["rechecked"] += 1
        old_band = band(row.get("proposals_now"))
        with db.connect(dbp) as conn:
            store(conn, row["id"], count, stamp)
        if not isinstance(count, int):
            m["unknown"] += 1
            continue
        verdict = decide(count, row)
        new_band = band(count)
        if verdict != "skip" and new_band == old_band:
            continue
        tid = row["clickup_task_id"]
        try:
            status = ((req("GET", f"/task/{tid}", tok) or {}).get("status") or {})
            status = (status.get("status") or "").lower()
            if status != "new":
                with db.connect(dbp) as conn:
                    conn.execute("UPDATE scored_jobs SET crowd_state='closed'"
                                 " WHERE id=?", (row["id"],))
                m["left_new"] += 1
                continue
            if verdict == "skip":
                req("POST", f"/task/{tid}/comment", tok,
                    {"comment_text": COMMENT.format(count=count, skip=config.CROWD_SKIP_AT),
                     "notify_all": False})
                req("PUT", f"/task/{tid}", tok, {"status": "skipped"})
                with db.connect(dbp) as conn:
                    conn.execute("UPDATE scored_jobs SET crowd_state='skipped'"
                                 " WHERE id=?", (row["id"],))
                m["skipped"] += 1
                ctx.log(f"crowd: {row['id']} skipped at {count} proposals")
            else:
                req("PUT", f"/task/{tid}", tok,
                    {"name": name_for(dict(row, proposals_now=count))})
                m["renamed"] += 1
                ctx.log(f"crowd: {row['id']} now {new_band} ({count})")
        except Exception as exc:                                 # noqa: BLE001
            m["errors"] += 1
            ctx.log(f"crowd: card {tid} not updated: {exc!r}")
    return m
