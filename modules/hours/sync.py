"""Pull ClickUp time entries into hours_entries. Read-only, idempotent.

ONE ROW PER ENTRY, KEYED ON CLICKUP'S ENTRY ID, so re-running changes no total.
An entry whose duration was edited updates in place; an entry ClickUp stops
returning is marked `gone_at` rather than deleted, because a number that was
reported to a client has to stay explicable afterwards.

THE WINDOW IS WALKED IN CHUNKS. The ClickUp MCP wrapper caps at 50 rows and the
raw endpoint has its own ceiling; a silent truncation here UNDER-BILLS, which is
the worst failure this feature has. So the sync asks month by month and records
how many rows each chunk returned — a chunk that comes back suspiciously round
(50/100/200) is logged as `suspicious_chunks` for the run rather than trusted.

SPACE -> CLIENT comes from config/clients.yaml via the registry. It is the only
roster in this codebase; a space with no registry match is reported as a gap,
never guessed at.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Optional

from core import config, db, registry, runner
from modules.hours import client as cu
from modules.hours import periods as per

TASK_NAME = "hours_sync"

# Round numbers a paginated API returns when it has truncated rather than
# finished. Not proof — a flag to look at, which is better than silence.
_SUSPICIOUS = {50, 100, 200, 250, 500, 1000}


def _now() -> str:
    return dt.datetime.now(config.TZ).strftime("%Y-%m-%d %H:%M:%S")


def _months(lo: str, hi: str) -> list:
    """[(start, end)] month-sized windows covering lo..hi, OVERLAPPING BY A DAY
    at each edge.

    The overlap is not belt-and-braces, it is a fix. ClickUp wants an entry to
    fall inside the window it is asked for, so an entry that starts at 23:52 on
    the last day of a month and ends after midnight belongs to NEITHER a
    clean September chunk nor a clean October one, and vanishes. Measured
    2026-10-19: Hermon Sprint 22 came to 177.06h against the API's 178.31h, and
    the whole difference was one 1.25h entry starting 2026-09-30 23:52.
    Overlapping is safe because storage is keyed on ClickUp's entry id and
    upserts — seeing a row twice costs nothing, missing it bills wrong.
    """
    out = []
    y, m = (int(x) for x in lo.split("-")[:2])
    end_y, end_m = (int(x) for x in hi.split("-")[:2])
    while (y, m) <= (end_y, end_m):
        nm_y, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        last = (dt.date(nm_y, nm, 1) - dt.timedelta(days=1)).day
        first = dt.date(y, m, 1) - dt.timedelta(days=1)
        final = dt.date(y, m, last) + dt.timedelta(days=1)
        out.append((first.isoformat(), final.isoformat()))
        y, m = nm_y, nm
    return out


def _fetch(tok, _client, *, space_id, lo, hi, member_ids, log,
           depth: int = 0) -> list:
    """One window's entries, SPLITTING when the answer looks truncated.

    The endpoint has a ceiling and does not say when it hit one — it just
    returns a round number. A truncated read under-bills a client, which is the
    worst thing this feature can do, so a suspicious count is halved and asked
    again rather than trusted. Depth is bounded: below a day there is nothing
    left to split and the count is reported as genuinely large.
    """
    rows = _client.time_entries(tok, start_ms=_client.ist_ms(lo),
                                end_ms=_client.ist_ms(hi, end=True),
                                member_ids=member_ids, space_id=space_id)
    a, b = dt.date.fromisoformat(lo), dt.date.fromisoformat(hi)
    if len(rows) not in _SUSPICIOUS or depth >= 5 or (b - a).days <= 1:
        return rows
    mid = a + (b - a) / 2
    log(f"hours: {len(rows)} rows for {lo}..{hi} looks truncated — splitting")
    left = _fetch(tok, _client, space_id=space_id, lo=lo, hi=mid.isoformat(),
                  member_ids=member_ids, log=log, depth=depth + 1)
    right = _fetch(tok, _client, space_id=space_id,
                   lo=(mid).isoformat(), hi=hi,
                   member_ids=member_ids, log=log, depth=depth + 1)
    merged = {str(r.get("id")): r for r in left + right}
    return list(merged.values())


def _client_for_space(reg, space_name: str) -> Optional[str]:
    """Space name -> client id, through the registry's own matcher."""
    try:
        return reg.match_name(space_name)
    except Exception:                                            # noqa: BLE001
        return None


def _row(e: dict, client_id: Optional[str], space_name: str, now: str) -> tuple:
    loc = e.get("task_location") or {}
    task = e.get("task") or {}
    user = e.get("user") or {}
    return (
        str(e.get("id")), str(task.get("id") or "") or None, task.get("name"),
        str(loc.get("space_id") or ""), space_name,
        str(loc.get("folder_id") or "") or None, loc.get("folder_name"),
        str(loc.get("list_id") or "") or None, loc.get("list_name"),
        client_id,
        str(user.get("id") or ""), user.get("username"),
        int(e.get("duration") or 0),
        int(e.get("start") or 0),
        int(e.get("end") or 0) or None,
        int(e.get("at") or 0) or None,
        1 if e.get("billable") else 0,
        json.dumps([t.get("name") for t in (e.get("tags") or [])]) or None,
        (e.get("description") or "").strip() or None,
        e.get("source"),
        now, now,
    )


def sync(ctx, *, since: Optional[str] = None,
         _client=cu) -> dict:
    dbp = ctx.db_path or config.DB_PATH
    tok = _client.token()
    reg = registry.load()

    member_ids = [m["id"] for m in _client.members(tok)]
    if not member_ids:
        raise RuntimeError("no workspace members — refusing to sync, because"
                           " an empty assignee list silently returns only the"
                           " authenticated user's entries")

    lo = since or config.HOURS_SYNC_SINCE
    hi = dt.datetime.now(config.TZ).strftime("%Y-%m-%d")
    seen_ids, suspicious, chunks = set(), [], 0
    inserted = updated = 0
    now = _now()

    with db.connect(dbp) as conn:
        for space_id, space_name in config.HOURS_SPACES.items():
            client_id = _client_for_space(reg, space_name)
            for c_lo, c_hi in _months(lo, hi):
                rows = _fetch(tok, _client, space_id=space_id, lo=c_lo,
                              hi=c_hi, member_ids=member_ids, log=ctx.log)
                chunks += 1
                if len(rows) in _SUSPICIOUS:
                    # still round after splitting — report it rather than
                    # quietly accept a number that may be a ceiling
                    suspicious.append(f"{space_name} {c_lo}: {len(rows)} rows")
                for e in rows:
                    eid = str(e.get("id"))
                    seen_ids.add(eid)
                    r = _row(e, client_id, space_name, now)
                    cur = conn.execute(
                        "SELECT duration_ms FROM hours_entries WHERE id=?",
                        (eid,)).fetchone()
                    if cur is None:
                        conn.execute(
                            "INSERT INTO hours_entries (id, task_id, task_name,"
                            " space_id, space_name, folder_id, folder_name,"
                            " list_id, list_name, client_id, user_id, user_name,"
                            " duration_ms, start_ms, end_ms, created_ms,"
                            " billable, tags_json, description, source,"
                            " first_seen_at, updated_at)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            r)
                        inserted += 1
                    else:
                        conn.execute(
                            "UPDATE hours_entries SET task_name=?, folder_id=?,"
                            " folder_name=?, list_id=?, list_name=?,"
                            " client_id=?, duration_ms=?, start_ms=?, end_ms=?,"
                            " created_ms=?, billable=?, tags_json=?,"
                            " description=?, gone_at=NULL, updated_at=?"
                            " WHERE id=?",
                            (r[2], r[5], r[6], r[7], r[8], r[9], r[12], r[13],
                             r[14], r[15], r[16], r[17], r[18], now, eid))
                        if cur["duration_ms"] != r[12]:
                            updated += 1
                    # the task's tags and CURRENT status ride on the entry
                    # (migration 043). Written for new and existing rows alike,
                    # because a task is re-tagged and moves status long after
                    # its time was logged — and the report groups by tag.
                    tstat = (e.get("task") or {}).get("status") or {}
                    if not isinstance(tstat, dict):
                        tstat = {"status": tstat}
                    conn.execute(
                        "UPDATE hours_entries SET task_tags_json=?,"
                        " task_status=?, task_status_type=? WHERE id=?",
                        (_tags_json(e.get("task_tags")),
                         tstat.get("status"), tstat.get("type"), eid))

        # entries the API no longer returns, inside the window we just read
        gone = conn.execute(
            "SELECT id FROM hours_entries WHERE gone_at IS NULL"
            " AND start_ms >= ? AND start_ms <= ?",
            (_client.ist_ms(lo), _client.ist_ms(hi, end=True))).fetchall()
        vanished = [g["id"] for g in gone if g["id"] not in seen_ids]
        for gid in vanished:
            conn.execute("UPDATE hours_entries SET gone_at=? WHERE id=?",
                         (now, gid))

        periods_written = _sync_periods(conn, tok, reg, now, _client)
        tasks = _sync_tasks(conn, tok, reg, now, _client, ctx.log)

    metrics = {"entries_seen": len(seen_ids), "inserted": inserted,
               "duration_changed": updated, "marked_gone": len(vanished),
               "chunks": chunks, "spaces": len(config.HOURS_SPACES),
               "periods": periods_written, "tasks": tasks, "since": lo,
               "as_of": now, "suspicious_chunks": suspicious}
    ctx.log(f"hours sync: {json.dumps(metrics)}")
    return metrics


def _sync_periods(conn, tok, reg, now, _client) -> int:
    """Sprint lists and their parsed windows, one row each."""
    n = 0
    for space_id, space_name in config.HOURS_SPACES.items():
        folder_id = config.HOURS_SPRINT_FOLDERS.get(space_id)
        if not folder_id:
            continue
        lists = _client.lists_in_folder(tok, folder_id)
        anchor = config.HOURS_SPRINT_ANCHOR_YEAR.get(space_id,
                                                     config.HOURS_DEFAULT_YEAR)
        parsed = per.infer_years([x["name"] for x in lists], anchor)
        client_id = _client_for_space(reg, space_name)
        for meta, p in zip(lists, parsed):
            # PREFER THE ENTRIES' OWN DATES over the name-sequence guess. The
            # sequence walk needs a correct anchor per folder and is wrong by a
            # whole year when it does not have one; the time logged in a list
            # is direct evidence of when that list was. Sequence remains the
            # fallback for a list nobody has logged against yet.
            seen = conn.execute(
                "SELECT MIN(date(start_ms/1000,'unixepoch','+5 hours','+30 minutes')) lo,"
                " MAX(date(start_ms/1000,'unixepoch','+5 hours','+30 minutes')) hi"
                " FROM hours_entries WHERE list_id=? AND gone_at IS NULL",
                (meta["id"],)).fetchone()
            if seen and seen["lo"]:
                better = per.year_from_entries(meta["name"],
                                               (seen["lo"], seen["hi"]))
                if better:
                    p = {**p, **better}
            conn.execute(
                "INSERT INTO hours_periods (list_id, list_name, space_id,"
                " client_id, starts_on, ends_on, parse_ok, parse_note,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(list_id) DO UPDATE SET list_name=excluded.list_name,"
                " starts_on=excluded.starts_on, ends_on=excluded.ends_on,"
                " parse_ok=excluded.parse_ok, parse_note=excluded.parse_note,"
                " updated_at=excluded.updated_at",
                (meta["id"], meta["name"], space_id, client_id, p["starts_on"],
                 p["ends_on"], p["parse_ok"], p["parse_note"], now))
            n += 1
    return n


def _sync_tasks(conn, tok, reg, now, _client, log) -> int:
    """Tasks for the RECENT sprint lists only.

    Not every list ever: a client with 44 sprints would be 44 paged reads per
    night for history that cannot change the current report. The EOW report
    looks at the period being reported and the one before it, so the sync
    follows the same horizon — config.HOURS_TASK_PERIODS back from today.
    """
    import json as _json
    seen = 0
    for space_id, space_name in config.HOURS_SPACES.items():
        client_id = _client_for_space(reg, space_name)
        recent = [dict(r) for r in conn.execute(
            "SELECT list_id, list_name FROM hours_periods"
            " WHERE space_id=? AND parse_ok=1 ORDER BY starts_on DESC LIMIT ?",
            (space_id, config.HOURS_TASK_PERIODS))]
        for p in recent:
            try:
                tasks = _client.tasks_in_list(tok, p["list_id"])
            except Exception as exc:                             # noqa: BLE001
                log(f"hours tasks {p['list_name']}: {exc!r}")
                continue
            for t in tasks:
                st = t.get("status") or {}
                loc_folder = (t.get("folder") or {})
                conn.execute(
                    "INSERT INTO hours_tasks (id, name, status, status_type,"
                    " space_id, space_name, folder_id, folder_name, list_id,"
                    " list_name, client_id, assignees_json, created_ms,"
                    " updated_ms, status_changed_ms, closed_ms, due_ms, url,"
                    " first_seen_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(id) DO UPDATE SET name=excluded.name,"
                    " status=excluded.status, status_type=excluded.status_type,"
                    " list_id=excluded.list_id, list_name=excluded.list_name,"
                    " client_id=excluded.client_id,"
                    " assignees_json=excluded.assignees_json,"
                    " updated_ms=excluded.updated_ms,"
                    " status_changed_ms=excluded.status_changed_ms,"
                    " closed_ms=excluded.closed_ms, due_ms=excluded.due_ms,"
                    " gone_at=NULL, updated_at=excluded.updated_at",
                    (str(t.get("id")), t.get("name"), st.get("status"),
                     st.get("type"), space_id, space_name,
                     str(loc_folder.get("id") or "") or None,
                     loc_folder.get("name"), p["list_id"], p["list_name"],
                     client_id,
                     _json.dumps([a.get("username")
                                  for a in (t.get("assignees") or [])]),
                     _int(t.get("date_created")), _int(t.get("date_updated")),
                     _int(t.get("date_done")) or _int(t.get("date_updated")),
                     _int(t.get("date_closed")), _int(t.get("due_date")),
                     t.get("url"), now, now))
                conn.execute("UPDATE hours_tasks SET tags_json=? WHERE id=?",
                             (_tags_json(t.get("tags")), str(t.get("id"))))
                seen += 1
    return seen


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _tags_json(tags) -> Optional[str]:
    """[{name, color}] — ClickUp's own tag colour is kept, so a workstream looks
    the same in the report as it does in the client's ClickUp."""
    out = [{"name": (t.get("name") or "").strip(),
            "color": t.get("tag_bg") or t.get("tag_fg")}
           for t in (tags or []) if (t.get("name") or "").strip()]
    return json.dumps(out) if out else None


def main() -> int:
    import sys
    since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else None
    res = runner.run_task(TASK_NAME, lambda ctx: sync(ctx, since=since),
                          module="hours")
    print(json.dumps({"status": res.status, "metrics": res.metrics},
                     default=str))
    return 0 if res.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    raise SystemExit(main())
