"""The ClickUp card, read back into scored_jobs (migration 047).

Abhijeet, 2026-11-02: "you have drafts ready to send. check their clickup
status — if I have skipped that or moved them to another bucket it should be
updated." v3 pushes a card per scored job and, until tonight, never read it
again except the crowd check on its own eight candidates. "Ready to send" was
therefore blind to his board: a draft he had skipped, or moved to Low Priority,
stayed on Today until the applied rail happened to notice.

`sync` re-reads the cards that can still change — status NULL (never read),
`new`, `applied` or `interview` — newest-first, a bounded number per run at the
push script's own 0.65 s pace, and stores the status name, its ClickUp type,
the list it sits in now, and when it was read. A card in a terminal status
(won / skipped / expired / lost) is final and never re-read; a 404 is
`deleted`. Reads only; nothing here writes to ClickUp.

The readers then honour it: `data._drafts_waiting_sql` keeps a draft only
while its card is `new` (an unread card counts as new — the old behaviour,
never worse), and Today's strip also drops cards he moved to Low Priority.
"""
from __future__ import annotations

import sqlite3
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from core import config, db

OPEN_STATUSES = ("new", "applied", "interview")        # can still change
PER_RUN = 60                                            # ~40 s at the pace
PACE_SECONDS = 0.65                                     # scripts/clickup_push.py's
FRESH_DAYS = 45                                         # older cards are history


def list_name(list_id: Optional[str]) -> Optional[str]:
    for name, lid in config.CLICKUP_LISTS.items():
        if lid == list_id:
            return name
    return None


def candidates(conn: sqlite3.Connection, now: datetime, limit: int = PER_RUN) -> list:
    """The cards that decide "drafts ready" first (a draft on a hot job),
    then never-read before re-read, oldest read first, newest job first."""
    lo = (now - timedelta(days=FRESH_DAYS)).strftime("%Y-%m-%d")
    marks = ",".join("?" * len(OPEN_STATUSES))
    return [dict(r) for r in conn.execute(
        f"SELECT id, clickup_task_id, card_status FROM scored_jobs"
        f" WHERE clickup_task_id IS NOT NULL AND substr(first_scored_at,1,10) >= ?"
        f" AND (card_status IS NULL OR card_status IN ({marks}))"
        f" ORDER BY (draft_proposal IS NOT NULL AND score >= ?) DESC,"
        f" card_checked_at IS NOT NULL, card_checked_at, first_scored_at DESC"
        f" LIMIT ?", (lo, *OPEN_STATUSES, config.HOT_LEAD_THRESHOLD, limit))]


def store(conn: sqlite3.Connection, job_id: str, status: Optional[str],
          status_type: Optional[str], list_id: Optional[str], stamp: str) -> None:
    conn.execute(
        "UPDATE scored_jobs SET card_status=?, card_status_type=?, card_list_id=?,"
        " card_checked_at=? WHERE id=?",
        ((status or "").lower() or None, (status_type or "").lower() or None,
         list_id, stamp, job_id))


def sync(ctx, dbp, *, req: Callable, token: Callable[[], str],
         now: Optional[datetime] = None, limit: int = PER_RUN,
         sleep: Callable[[float], None] = time.sleep) -> dict:
    """Read up to `limit` cards. Metrics: read, by status, changed, deleted,
    errors. `token` is called only when there is a card to read, so a run
    with nothing carded (every test DB) touches neither the credentials file
    nor the network. The caller wraps this; a ClickUp hiccup must not fail
    the sync it rides."""
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    m = {"read": 0, "changed": 0, "deleted": 0, "errors": 0, "by_status": {}}
    with db.connect(dbp) as conn:
        rows = candidates(conn, now, limit)
    if not rows:
        return m
    tok = token()
    for i, row in enumerate(rows):
        if i:
            sleep(PACE_SECONDS)
        tid = row["clickup_task_id"]
        try:
            task = req("GET", f"/task/{tid}", tok) or {}
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                with db.connect(dbp) as conn:
                    store(conn, row["id"], "deleted", None, None, stamp)
                m["deleted"] += 1
                continue
            m["errors"] += 1
            ctx.log(f"card {tid}: HTTP {exc.code}")
            continue
        except Exception as exc:                                  # noqa: BLE001
            m["errors"] += 1
            ctx.log(f"card {tid}: {exc!r}")
            continue
        st = task.get("status") or {}
        status = (st.get("status") or "").lower() or None
        stype = (st.get("type") or "").lower() or None
        lid = str((task.get("list") or {}).get("id") or "") or None
        with db.connect(dbp) as conn:
            store(conn, row["id"], status, stype, lid, stamp)
        m["read"] += 1
        m["by_status"][status or "?"] = m["by_status"].get(status or "?", 0) + 1
        if status != (row.get("card_status") or None):
            m["changed"] += 1
    if rows:
        ctx.log(f"cards: read {m['read']}, changed {m['changed']}, deleted {m['deleted']},"
                f" errors {m['errors']} — {m['by_status']}")
    return m
