"""Finance-calendar nudges (Phase 5). Upcoming invoice + payroll reminders.

Invoice cadences (Abhijeet 2026-06-29):
  - Mike / CMF Publishing       -> 15th & 30th of each month (fixed dates)
  - Horizons-ABA / Alexander Apfel, Hermon / Abel, Adelye / Chris Cave -> every
    two weeks, anchored on the client's MOST RECENT ledger receipt (+14d cadence).
Payroll: last working day of each month (prep reminder).

A biweekly client with no usable ledger receipt (no matching revenue row, or rows
without a date) has NO derivable anchor — SURFACED as a flag (needs a first
invoice/anchor date), never guessed. Pure date math; no money, no float.
"""
from __future__ import annotations

import calendar as _cal
import datetime
from typing import Optional

CLIENTS = [
    {"client": "Mike / CMF Publishing", "cadence": "monthly_15_30", "token": "CMF"},
    {"client": "Horizons-ABA / Alexander Apfel", "cadence": "biweekly", "token": "HORIZON"},
    {"client": "Hermon / Abel", "cadence": "biweekly", "token": "HERMON"},
    {"client": "Adelye / Chris Cave", "cadence": "biweekly", "token": "ADELYE"},
]


def _last_working_day(y: int, m: int) -> datetime.date:
    d = datetime.date(y, m, _cal.monthrange(y, m)[1])
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    return d


def _months(start: datetime.date, end: datetime.date):
    y, m = start.year, start.month
    while datetime.date(y, m, 1) <= end:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def upcoming(today: datetime.date, horizon_days: int, anchors: dict) -> list:
    """events within [today, today+horizon]. anchors: {token: 'YYYY-MM-DD'|None}."""
    end = today + datetime.timedelta(days=horizon_days)
    events = []
    for c in CLIENTS:
        if c["cadence"] == "monthly_15_30":
            for y, m in _months(today, end):
                for day in (15, 30):
                    dd = min(day, _cal.monthrange(y, m)[1])
                    d = datetime.date(y, m, dd)
                    if today <= d <= end:
                        events.append({"date": d.isoformat(), "type": "invoice",
                                       "who": c["client"], "detail": f"invoice due ({dd}th)"})
        else:  # biweekly
            a = anchors.get(c["token"])
            if not a:
                events.append({"date": today.isoformat(), "type": "flag", "who": c["client"],
                               "detail": "biweekly — NO ledger anchor (no dated receipt); "
                                         "provide first invoice/anchor date"})
                continue
            d = datetime.date.fromisoformat(a) + datetime.timedelta(days=14)
            while d <= end:
                if d >= today:
                    events.append({"date": d.isoformat(), "type": "invoice", "who": c["client"],
                                   "detail": f"invoice due (biweekly, anchor {a})"})
                d += datetime.timedelta(days=14)
    for y, m in _months(today, end):
        d = _last_working_day(y, m)
        if today <= d <= end:
            events.append({"date": d.isoformat(), "type": "payroll",
                           "who": "Payroll", "detail": "run payroll (last working day)"})
    return sorted(events, key=lambda e: (e["date"], e["type"]))
