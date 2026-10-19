"""Aggregation over hours_entries. Pure reads; every figure carries its as-of.

EVERY NUMBER IS LIVE AND STAMPED (his decision, 2026-10-19). Totals always
reflect everything logged, and every figure states the moment it was read,
because hours keep arriving after a period closes — measured the same day,
13.6h landed at 03:45-03:52 against a sprint that had ended the day before, and
Horizons 43 read 26.05h on 7 Oct and 32.55h twelve days later. Neither was
wrong. A figure without its as-of is the one that will be wrong, because he will
compare a report he sent last week against a screen today and find a difference
nothing on either explains.

PERIOD BY start_ms, NEVER BY THE TASK'S CURRENT LIST. A task carried from
Horizons 43 into 44 keeps its old entries; counting by the task's list would
bill those hours a second time. Verified against live data: on Hermon Sprint 22
the date window reproduces 178.31h exactly, while the list rollup gives 108.06h
because it misses the Vibe Coding folder entirely.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Optional

from core import config

MS_H = 3_600_000


def _ms(day: str, *, end: bool = False) -> int:
    d = dt.date.fromisoformat(day)
    t = dt.time(23, 59, 59, 999000) if end else dt.time(0, 0)
    return int(dt.datetime.combine(d, t, tzinfo=config.TZ).timestamp() * 1000)


def _h(ms: int) -> float:
    return round((ms or 0) / MS_H, 2)


def as_of(conn) -> str:
    """The moment the data was last refreshed — the stamp every figure carries.
    Taken from the rows themselves, not from the clock: what matters is how
    current the DATA is, not when someone happened to open the page."""
    r = conn.execute("SELECT MAX(updated_at) m FROM hours_entries").fetchone()
    return (r["m"] if r and r["m"] else None) or "never synced"


def _window_sum(conn, client_id: str, lo: str, hi: str, extra: str = "",
                params: tuple = ()) -> dict:
    """Billed-hours sum for a client over a window, by START time.

    A RUNNING TIMER (negative duration in ClickUp) is excluded from the total
    and counted separately — adding it would make the figure move while someone
    is simply still working.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN duration_ms > 0 THEN duration_ms END),0) ms,"
        " COUNT(*) n,"
        " COALESCE(SUM(CASE WHEN duration_ms < 0 THEN 1 END),0) running"
        " FROM hours_entries WHERE client_id=? AND gone_at IS NULL"
        " AND start_ms BETWEEN ? AND ?" + extra,
        (client_id, _ms(lo), _ms(hi, end=True)) + params).fetchone()
    return {"hours": _h(row["ms"]), "entries": row["n"],
            "running_timers": row["running"]}


def periods_for(conn, client_id: str) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM hours_periods WHERE client_id=? ORDER BY starts_on",
        (client_id,))]


def overview(conn, today: str) -> dict:
    """Every client's current period, against the same point in the previous
    one. Comparing a part-done period to a whole finished one would make every
    live period look like a collapse, so the previous period is clipped to the
    same number of days elapsed."""
    out = []
    for client_id, name in _clients(conn):
        ps = [p for p in periods_for(conn, client_id) if p["parse_ok"]]
        if not ps:
            out.append({"client_id": client_id, "client": name,
                        "period": None,
                        "note": "no parsable sprint period"})
            continue
        cur = _current(ps, today)
        prev = _previous(ps, cur)
        elapsed = _days_elapsed(cur, today)
        now = _window_sum(conn, client_id, cur["starts_on"], cur["ends_on"])
        cmp_ = None
        if prev:
            clip = _clip(prev, elapsed)
            cmp_ = {"period": prev["list_name"],
                    "hours_same_point": _window_sum(
                        conn, client_id, prev["starts_on"], clip)["hours"],
                    "hours_full": _window_sum(
                        conn, client_id, prev["starts_on"],
                        prev["ends_on"])["hours"]}
        out.append({"client_id": client_id, "client": name,
                    "period": cur["list_name"],
                    "starts_on": cur["starts_on"], "ends_on": cur["ends_on"],
                    "open": cur["ends_on"] >= today,
                    "days_elapsed": elapsed, **now, "previous": cmp_})
    out.sort(key=lambda x: -(x.get("hours") or 0))
    unattributed = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(duration_ms),0) ms FROM hours_entries"
        " WHERE client_id IS NULL AND gone_at IS NULL").fetchone()
    return {"as_of": as_of(conn), "today": today, "clients": out,
            "unattributed": {"entries": unattributed["n"],
                             "hours": _h(unattributed["ms"])},
            "basis": "hours count toward a period by when the work was logged,"
                     " not by which sprint list the task now sits in"}


def client_period(conn, client_id: str, list_id: Optional[str],
                  today: str) -> dict:
    ps = [p for p in periods_for(conn, client_id) if p["parse_ok"]]
    if not ps:
        return {"error": "no parsable sprint period for this client",
                "as_of": as_of(conn)}
    p = next((x for x in ps if x["list_id"] == list_id), None) \
        or _current(ps, today)
    lo, hi = p["starts_on"], p["ends_on"]
    total = _window_sum(conn, client_id, lo, hi)

    def group(col):
        return [{"key": r[col] or "—", "hours": _h(r["ms"]), "entries": r["n"]}
                for r in conn.execute(
                    f"SELECT {col}, SUM(duration_ms) ms, COUNT(*) n"
                    " FROM hours_entries WHERE client_id=? AND gone_at IS NULL"
                    " AND duration_ms > 0 AND start_ms BETWEEN ? AND ?"
                    f" GROUP BY {col} ORDER BY ms DESC",
                    (client_id, _ms(lo), _ms(hi, end=True)))]

    return {"as_of": as_of(conn), "client_id": client_id,
            "period": p["list_name"], "starts_on": lo, "ends_on": hi,
            "open": hi >= today, **total,
            "by_person": group("user_name"),
            "by_task": group("task_name"),
            # the fold-in made visible: Hermon's total includes Vibe Coding
            # Projects, and this is where that is answerable without ClickUp
            "by_folder": group("folder_name"),
            "periods": [{"list_id": x["list_id"], "name": x["list_name"],
                         "starts_on": x["starts_on"], "ends_on": x["ends_on"]}
                        for x in ps]}


def logging_gaps(conn, lo: str, hi: str) -> dict:
    """A WEEKDAY ON WHICH SOMEONE LOGGED NOTHING (his rule, 2026-10-19).

    Not a threshold on thin days: the failure worth catching is a day that was
    never recorded at all. Weekends are not gaps. A person with nothing all
    period is ONE line, not ten — a list nobody can finish gets ignored, which
    is how the Review board stopped being read.
    """
    people = {r["user_id"]: r["user_name"] for r in conn.execute(
        "SELECT DISTINCT user_id, user_name FROM hours_entries"
        " WHERE gone_at IS NULL")}
    skip = set(config.HOURS_GAP_EXCLUDE_USER_IDS)
    logged = {}
    for r in conn.execute(
            "SELECT user_id, date(start_ms/1000,'unixepoch','+5 hours','+30 minutes') d"
            " FROM hours_entries WHERE gone_at IS NULL AND duration_ms > 0"
            " AND start_ms BETWEEN ? AND ? GROUP BY 1,2",
            (_ms(lo), _ms(hi, end=True))):
        logged.setdefault(r["user_id"], set()).add(r["d"])

    days = _weekdays(lo, hi)
    out = []
    for uid, name in sorted(people.items(), key=lambda kv: kv[1] or ""):
        if uid in skip:
            continue
        missing = [d for d in days if d not in logged.get(uid, set())]
        if not missing:
            continue
        out.append({"user_id": uid, "person": name,
                    "missing_days": len(missing),
                    "of_weekdays": len(days),
                    "nothing_all_period": len(missing) == len(days),
                    "days": missing if len(missing) <= 10 else missing[:10]})
    out.sort(key=lambda x: -x["missing_days"])
    return {"as_of": as_of(conn), "from": lo, "to": hi,
            "weekdays": len(days), "people": out,
            "excluded": sorted(skip),
            "rule": "a weekday with nothing logged at all; weekends ignored"}


def report(conn, client_id: str, list_id: Optional[str], today: str) -> dict:
    """What goes to the client. TOTAL BILLED HOURS ONLY by default; breakdowns
    are switched on per client in config as they ask (Alex/Horizons first), so
    turning one on is a reviewed change rather than a UI toggle that silently
    alters what a client receives."""
    d = client_period(conn, client_id, list_id, today)
    if d.get("error"):
        return d
    show = config.HOURS_REPORT_BREAKDOWNS.get(client_id, ())
    out = {"as_of": d["as_of"], "client_id": client_id,
           "period": d["period"], "starts_on": d["starts_on"],
           "ends_on": d["ends_on"], "open": d["open"],
           "total_hours": d["hours"], "entries": d["entries"],
           "breakdowns_enabled": list(show),
           # stated every time, not just when it bites: a period that is still
           # open will keep moving, and he has to see that before he sends it
           "note": ("This period is still open — the total will change as more"
                    " time is logged." if d["open"] else
                    "Period closed, but hours can still be logged against it;"
                    " the figure is as-of above.")}
    for key in show:
        out[f"by_{key}"] = d.get(f"by_{key}")
    if d.get("running_timers"):
        out["running_timers"] = d["running_timers"]
    return out


# ── helpers ──────────────────────────────────────────────────────────────────

def _clients(conn) -> list:
    return [(r["client_id"], r["space_name"]) for r in conn.execute(
        "SELECT client_id, MIN(space_name) space_name FROM hours_entries"
        " WHERE client_id IS NOT NULL AND gone_at IS NULL GROUP BY client_id")]


def _current(ps: list, today: str) -> dict:
    for p in ps:
        if p["starts_on"] <= today <= p["ends_on"]:
            return p
    past = [p for p in ps if p["ends_on"] < today]
    return max(past, key=lambda p: p["starts_on"]) if past else ps[-1]


def _previous(ps: list, cur: dict) -> Optional[dict]:
    before = [p for p in ps if p["starts_on"] < cur["starts_on"]]
    return max(before, key=lambda p: p["starts_on"]) if before else None


def _days_elapsed(p: dict, today: str) -> int:
    lo = dt.date.fromisoformat(p["starts_on"])
    hi = min(dt.date.fromisoformat(p["ends_on"]), dt.date.fromisoformat(today))
    return max((hi - lo).days + 1, 0)


def _clip(p: dict, days: int) -> str:
    lo = dt.date.fromisoformat(p["starts_on"])
    end = dt.date.fromisoformat(p["ends_on"])
    return min(lo + dt.timedelta(days=max(days - 1, 0)), end).isoformat()


def _weekdays(lo: str, hi: str) -> list:
    a, b = dt.date.fromisoformat(lo), dt.date.fromisoformat(hi)
    out, d = [], a
    while d <= b:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out
