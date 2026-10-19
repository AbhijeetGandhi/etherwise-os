"""Period windows, parsed out of sprint list names.

The names carry the window and nothing else does: "Horizons 44 (10/5 - 10/18)",
"Sprint 22 (9/28 - 10/11)". Three traps, all real in this workspace:

  * NO YEAR. "Horizons 21 (12/29 - 1/11)" spans a new year, and a naive parse
    files December 2025 sprints as 2026.
  * GAPS AND CADENCE CHANGES. Horizons skips 3, 33, 34 and 37, and switched
    weekly -> fortnightly around 19. A window can never be inferred by counting
    forward from a neighbour; every name is read on its own.
  * A NAME THAT DOES NOT PARSE IS A VISIBLE ERROR, not a skipped period. It is
    stored with parse_ok=0 and its raw name, and the page says so. A period that
    silently disappears is a period that silently stops being billed.

The year is inferred from list ORDER: ClickUp returns a folder's lists oldest
first, so the sequence of (month, day) pairs is monotonic apart from year
rollovers, and a rollover is exactly where the month goes backwards.
"""
from __future__ import annotations

import re
from typing import Optional

# "Horizons 44 (10/5 - 10/18)" / "Sprint 22 (9/28 - 10/11)" — tolerant of
# en-dashes and missing spaces, because the names are typed by hand.
_WINDOW = re.compile(
    r"\((\d{1,2})/(\d{1,2})\s*[-–—]\s*(\d{1,2})/(\d{1,2})\)")


def parse_window(name: str) -> Optional[tuple]:
    """((m1, d1), (m2, d2)) or None. Year-free by design — see infer_years."""
    m = _WINDOW.search(name or "")
    if not m:
        return None
    a, b, c, d = (int(x) for x in m.groups())
    if not (1 <= a <= 12 and 1 <= c <= 12 and 1 <= b <= 31 and 1 <= d <= 31):
        return None
    return ((a, b), (c, d))


def year_from_entries(name: str, observed: Optional[tuple]) -> Optional[dict]:
    """Date a list from the time actually logged in it, not from a config
    anchor.

    THE ANCHOR APPROACH FAILED ON REAL DATA (2026-10-19). Hermon's folder opens
    with "Sprint 2 (12/29 - 1/11)" — December 2025 — so a 2026 anchor pushed
    every later sprint forward a year and "Sprint 22 (9/28 - 10/11)" resolved to
    2027, where it matched no entries and reported 0.0h. A per-space anchor
    table would have to be maintained by hand for every client added, and would
    be wrong in exactly the same silent way the first time someone got it wrong.

    `observed` is (min_start, max_start) as YYYY-MM-DD for entries logged in
    this list. The list's own time is the best evidence of when the list was,
    so pick the candidate year whose window contains it.
    """
    w = parse_window(name)
    if not w or not observed:
        return None
    (m1, d1), (m2, d2) = w
    lo_seen, hi_seen = observed
    best, best_cost = None, None
    for year in range(int(lo_seen[:4]) - 1, int(hi_seen[:4]) + 2):
        end_year = year + 1 if (m2, d2) < (m1, d1) else year
        try:
            starts = "%04d-%02d-%02d" % (year, m1, d1)
            ends = "%04d-%02d-%02d" % (end_year, m2, d2)
            _valid(starts), _valid(ends)
        except ValueError:
            continue
        # cost = how far the observed entries sit outside the candidate window
        cost = _days_outside(lo_seen, hi_seen, starts, ends)
        if best_cost is None or cost < best_cost:
            best, best_cost = {"starts_on": starts, "ends_on": ends,
                               "parse_ok": 1, "parse_note": None}, cost
    return best


def _days_outside(lo_seen, hi_seen, starts, ends) -> int:
    import datetime as dt
    a, b = dt.date.fromisoformat(lo_seen), dt.date.fromisoformat(hi_seen)
    s, e = dt.date.fromisoformat(starts), dt.date.fromisoformat(ends)
    return max((s - a).days, 0) + max((b - e).days, 0)


def infer_years(names: list, anchor_year: int) -> list:
    """[{list_name, starts_on, ends_on, parse_ok, parse_note}] for lists given
    OLDEST FIRST.

    `anchor_year` is the year of the FIRST list in the sequence. Each time the
    start month goes backwards relative to the previous start, a year has
    turned over. The end date rolls a year too when it precedes its own start
    (a sprint that straddles 31 December).
    """
    out, year, prev_month = [], anchor_year, None
    for name in names:
        w = parse_window(name)
        if not w:
            out.append({"list_name": name, "starts_on": None, "ends_on": None,
                        "parse_ok": 0,
                        "parse_note": "no (m/d - m/d) window in the list name"})
            continue
        (m1, d1), (m2, d2) = w
        if prev_month is not None and m1 < prev_month:
            year += 1                      # the sequence wrapped past December
        prev_month = m1
        end_year = year + 1 if (m2, d2) < (m1, d1) else year
        try:
            starts = "%04d-%02d-%02d" % (year, m1, d1)
            ends = "%04d-%02d-%02d" % (end_year, m2, d2)
            _valid(starts), _valid(ends)
        except ValueError as exc:
            out.append({"list_name": name, "starts_on": None, "ends_on": None,
                        "parse_ok": 0, "parse_note": f"impossible date: {exc}"})
            continue
        out.append({"list_name": name, "starts_on": starts, "ends_on": ends,
                    "parse_ok": 1, "parse_note": None})
    return out


def _valid(day: str) -> None:
    import datetime as dt
    dt.date.fromisoformat(day)


def current(periods: list, today: str) -> Optional[dict]:
    """The period `today` falls inside, else the most recent closed one.

    Falling back to the latest CLOSED period rather than to nothing matters:
    between sprints, "no current period" on the overview reads as broken, and
    the honest answer is the period he would actually be invoicing.
    """
    usable = [p for p in periods if p.get("parse_ok") and p.get("starts_on")]
    if not usable:
        return None
    for p in usable:
        if p["starts_on"] <= today <= (p.get("ends_on") or p["starts_on"]):
            return p
    past = [p for p in usable if (p.get("ends_on") or "") < today]
    return max(past, key=lambda p: p["starts_on"]) if past else None
