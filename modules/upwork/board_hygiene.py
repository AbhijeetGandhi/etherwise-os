"""The board's daily hygiene: his rules of 2026-06-11, running in v3.

His rules, approved 2026-06-11 (scripts/board_cleanup.py, the v2 reaper):
  R1 EXPIRED    a card still `new` whose job was posted more than EXPIRY_DAYS
                ago and never applied to
  R2 DUPLICATE  open cards for the same job (a repost under the same title):
                the one he worked (applied / interview) stays, else the newest;
                the other `new` ones close
and, from 2026-11-10 ("I only want valid jobs"):
  R3 TODAY'S RULES  a `new` card whose job fails the scanner's hard rules as
                they stand today (a rule he adds reaches the cards already on
                the board), or whose last proposal count is past the crowd line
  never         an applied or interview card; a job with a proposal against
                it; R3 never touches the Invites list (a client asked for him)
The v2 scanner ran the reaper once a day. v3 never did: it last ran on
2026-07-06, and on 2026-11-10 the board held 368 open `new` cards, 302 of them
for jobs posted 1 to 4 months earlier (his "I only want valid jobs" that day).
Now the scan runs this once a day (scan_pipeline, live only).

R2 needs FOUR or more title words: "AI Developer" twice is two clients, not a
repost. The posted date comes from the v3 row; a card v3 never made (the v2
era) falls back to the card's own creation date.
"""
from __future__ import annotations

import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from core import config, db

EXPIRY_DAYS = 14
PER_RUN = 40                  # closures a scan run makes; the rest the next run
SPACING_S = 0.65              # between ClickUp writes (two per closure)
OPEN_STATUSES = ("new", "applied", "interview")
WORKED = ("applied", "interview")
R2_MIN_WORDS = 4
IST = timezone(timedelta(hours=5, minutes=30))

EXPIRE_COMMENT = ("Expired by the board's daily hygiene (your rule R1, 11 Jun): this job "
                  "was posted {days} days ago and never applied to. Reopen the card if "
                  "you still want it.")
DUP_COMMENT = ("Closed as a duplicate (your rule R2, 11 Jun): the same job has another "
               "card, which stays open: {keep_url}")
RULE_COMMENT = ("Skipped by the board's daily check: this job fails your rules as they "
                "stand today ({why}). Reopen the card if you want it.")


def _money(x) -> str:
    return f"${x:,.0f}" if isinstance(x, (int, float)) else "?"


def rule_words(reason: str, row: dict) -> str:
    """His rules in his words, for the card comment."""
    lo, hi = row.get("hourly_min"), row.get("hourly_max")
    return {
        "hourly_below_30": "$30/hr is not inside the client's range ("
                           + (f"{_money(lo)}-{_money(hi)}/hr" if lo else f"up to {_money(hi)}/hr") + ")",
        "fixed_below_500": f"fixed price under $500 ({_money(row.get('fixed_budget'))})",
        "rate_unstated_low_client": "no rate posted and the client has no proven spend",
        "rate_unread": "the posted rate could not be read",
        "hubspot_only": "HubSpot in the title and none of Make.com, Airtable, Zapier or n8n",
        "n8n_exclusion": "n8n is the subject of the job",
        "no_asia_timezone": "the post rules out Asia time zones",
        "onsite_required": "on-site work",
        "crowded": f"{row.get('proposals_now')} proposals already (the line is 150,"
                   " except Make.com and Airtable)",
    }.get(reason, f"rule {reason}")

# "<title> — score 26 · under 20 proposals" (v3) and "[Invite] 🎥 [26] — <title>" (v2)
_V3_SUFFIX = re.compile(r"\s+—\s+score\s+\d+.*$")
_V2_PREFIX = re.compile(r"^(\[Invite\]\s*)?(🎥\s*)?(\[\d+\]\s*[—-]?\s*)?(\d+\s*[—-]\s*)?")


def norm_title(name: str) -> str:
    t = _V3_SUFFIX.sub("", _V2_PREFIX.sub("", name or ""))
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def _when(posted: Optional[str], created_ms: int) -> Optional[datetime]:
    if posted:
        try:
            dt = datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    if created_ms:
        return datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
    return None


def plan(cards: list, posted: dict, job_of: dict, bid_on: set,
         now: Optional[datetime] = None, rows: Optional[dict] = None,
         rule_check: Optional[Callable] = None,
         crowd_check: Optional[Callable] = None) -> list:
    """The closures due, pure. cards: [{id, name, status, list, created_ms,
    url}]; posted / job_of: task id -> the v3 row's created_dt / job id;
    bid_on: job ids with a proposal; rows: task id -> the v3 row (R3);
    rule_check(row) -> skip reason or None; crowd_check(row) -> True when the
    row's last count is past the line. -> [{id, action, ...}]"""
    now = now or datetime.now(timezone.utc)
    out, taken = [], set()
    for c in cards:
        if c["status"] != "new" or job_of.get(c["id"]) in bid_on:
            continue
        when = _when(posted.get(c["id"]), c.get("created_ms") or 0)
        if when and (now - when).days > EXPIRY_DAYS:
            out.append({"id": c["id"], "action": "expire", "list": c.get("list"),
                        "days": (now - when).days, "name": c.get("name", "")})
            taken.add(c["id"])
    for c in cards:
        row = (rows or {}).get(c["id"])
        if (c["id"] in taken or c["status"] != "new" or c.get("list") == "invites"
                or row is None or job_of.get(c["id"]) in bid_on):
            continue
        reason = rule_check(row) if rule_check else None
        if not reason and crowd_check and crowd_check(row):
            reason = "crowded"
        if reason:
            out.append({"id": c["id"], "action": "rule", "list": c.get("list"),
                        "reason": reason, "why": rule_words(reason, row),
                        "name": c.get("name", "")})
            taken.add(c["id"])
    groups: dict = {}
    for c in cards:
        if c["id"] in taken:
            continue
        key = norm_title(c.get("name", ""))
        if len(key.split()) >= R2_MIN_WORDS:
            groups.setdefault(key, []).append(c)
    for members in groups.values():
        if len(members) < 2:
            continue
        def rank(c):
            when = _when(posted.get(c["id"]), c.get("created_ms") or 0)
            return (c["status"] in WORKED, when or datetime.min.replace(tzinfo=timezone.utc))
        keep = max(members, key=rank)
        for c in members:
            if c is keep or c["status"] != "new" or job_of.get(c["id"]) in bid_on:
                continue
            out.append({"id": c["id"], "action": "duplicate", "list": c.get("list"),
                        "keep": keep["id"], "keep_url": keep.get("url", ""),
                        "name": c.get("name", "")})
    return out


def read_open_cards(tok: str, req: Callable) -> list:
    """Every card still open for him (new / applied / interview) on the lead
    lists — status-filtered, so a list's thousands of skipped cards are not read."""
    cards = []
    for name, list_id in config.CLICKUP_LISTS.items():
        page = 0
        while True:
            q = urllib.parse.urlencode([("page", page), ("subtasks", "false")]
                                       + [("statuses[]", s) for s in OPEN_STATUSES])
            tasks = (req("GET", f"/list/{list_id}/task?{q}", tok) or {}).get("tasks") or []
            for t in tasks:
                cards.append({"id": t["id"], "name": t.get("name") or "", "list": name,
                              "status": ((t.get("status") or {}).get("status") or "").lower(),
                              "created_ms": int(t.get("date_created") or 0),
                              "url": t.get("url", "")})
            if len(tasks) < 100:
                break
            page += 1
    return cards


def sweep(dbp, tok: str, req: Callable, log: Callable[[str], None], *,
          live: bool, limit: Optional[int] = PER_RUN, now: Optional[datetime] = None,
          sleep: Callable = time.sleep) -> dict:
    """Read the board, plan R1/R2, and (live) close what is due: a comment,
    then the status, then the v3 row's card_status. At most `limit` closures."""
    from modules.upwork import crowd, scoring       # local: no import cycle
    cards = read_open_cards(tok, req)
    ids = [c["id"] for c in cards]
    with db.connect(dbp) as conn:
        full = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in conn.execute("SELECT * FROM scored_jobs WHERE clickup_task_id IN (%s)"
                                  % ",".join("?" * len(chunk)), chunk):
                full[r["clickup_task_id"]] = dict(r)
        bid_on = {str(r[0]) for r in conn.execute(
            "SELECT DISTINCT marketplace_job_id FROM proposals"
            " WHERE marketplace_job_id IS NOT NULL AND marketplace_job_id != ''")}
    posted = {t: r["created_dt"] for t, r in full.items()}
    job_of = {t: r["id"] for t, r in full.items()}
    due = plan(cards, posted, job_of, bid_on, now=now, rows=full,
               rule_check=scoring.hard_rule_skip,
               crowd_check=lambda r: crowd.decide(r.get("proposals_now"), r) == "skip")
    batch = due if limit is None else due[:limit]
    done, failed = 0, []
    if live:
        for a in batch:
            if a["action"] == "expire":
                text, status = EXPIRE_COMMENT.format(days=a["days"]), "expired"
            elif a["action"] == "rule":
                text, status = RULE_COMMENT.format(why=a["why"]), "skipped"
            else:
                text, status = DUP_COMMENT.format(keep_url=a["keep_url"]), "skipped"
            try:
                req("POST", f"/task/{a['id']}/comment", tok,
                    {"comment_text": text, "notify_all": False})
                sleep(SPACING_S)
                req("PUT", f"/task/{a['id']}", tok, {"status": status})
                sleep(SPACING_S)
            except Exception as exc:                      # noqa: BLE001
                failed.append({"id": a["id"], "error": repr(exc)[:200]})
                continue
            with db.connect(dbp) as conn:
                conn.execute("UPDATE scored_jobs SET card_status=?,"
                             " card_checked_at=datetime('now') WHERE clickup_task_id=?",
                             (status, a["id"]))
            done += 1
    m = {"open_cards": len(cards), "due": len(due),
         "expire": sum(1 for a in due if a["action"] == "expire"),
         "duplicate": sum(1 for a in due if a["action"] == "duplicate"),
         "rule": sum(1 for a in due if a["action"] == "rule"),
         "closed": done, "failed": len(failed),
         "remaining": len(due) - done if live else len(due)}
    if due or failed:
        log(f"board hygiene: {m}" + (f" failures {failed[:3]}" if failed else ""))
    return m


def due_today(marker: Path, now: Optional[datetime] = None) -> bool:
    """Once per IST day: False once today's sweep has left nothing due."""
    today = (now or datetime.now(timezone.utc)).astimezone(IST).strftime("%Y-%m-%d")
    try:
        return Path(marker).read_text().strip() != today
    except OSError:
        return True


def mark_done(marker: Path, now: Optional[datetime] = None) -> None:
    today = (now or datetime.now(timezone.utc)).astimezone(IST).strftime("%Y-%m-%d")
    try:
        Path(marker).write_text(today)
    except OSError:
        pass
