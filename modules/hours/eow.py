"""The end-of-week client report.

THE SHAPE IS HIS, NOT INVENTED. Taken from the EOW report Etherwise already
sends (repos/etherwise-ops/verification/evidence/w1-07-eow-sample.html):

  1 · Shipped this week
  2 · Caught before you noticed
  3 · System health
  4 · In progress / next week
  5 · Needs your input

WHAT SECTION 3 DOES NOT SAY, AND WHY. The original carries runs-this-week,
success rate and monitoring coverage, which come from the Make API. Etherwise's
ops platform already polls those orgs, and in August a second Etherwise system
polling the same org 429'd a client for ten days before anyone noticed. So this
report states what we CAUGHT — alerts that arrived, by scenario — and does not
restate stats it would have to duplicate a poller to get (his decision,
2026-10-19). A smaller true section beats a fuller one bought with a client's
API budget.

TWO THINGS FROM THE SAMPLE ARE DELIBERATELY NOT COPIED. Its footer compares the
client to another client by name and quotes a dollar figure for time saved —
the sample is a template and both are invented. Naming one client to another is
a confidentiality problem whichever way it points, and a savings number we did
not measure is the kind of claim that gets checked.

NOTHING IS SENT (hours brief law 2). This renders a report. Abhijeet sends it.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Optional

from core import config
from modules.hours import report as hr

MS_H = 3_600_000


def _ms(day: str, *, end: bool = False) -> int:
    return hr._ms(day, end=end)


def client_name(client_id: str) -> str:
    """The name on the cover — from the registry, which is the only roster in
    this codebase. A raw slug like 'horizons-aba' on a client's report is the
    kind of detail that makes a document look generated."""
    try:
        from core import registry
        name = registry.load().canonical(client_id)
        if name:
            return name
    except Exception:                                            # noqa: BLE001
        pass
    return client_id.replace("-", " ").title()


def _age_words(ms: Optional[int], now_ms: int) -> str:
    if not ms:
        return "age unknown"
    d = max(now_ms - ms, 0) / 86_400_000
    if d < 1:
        return "under a day"
    if d < 2:
        return "a day"
    return f"{int(d)} days"


# Tasks that are INBOUND NOISE wearing a ticket's clothes. Make's alert mails
# and forwarded threads become tasks in the client's sprint list, so a naive
# "what closed this week" listed a client's own automation errors back to them
# as achievements — measured on Horizons 44, 2026-10-19: 40 of 43 "shipped"
# items were alert tickets and forwarded mail. Alerts belong in section 2,
# where they are the point; in section 1 they are an insult.
_NOT_DELIVERY = re.compile(
    r"^\s*(re|fwd|fw)\s*:"                       # forwarded / replied mail
    r"|encountered error in\b"
    r"|has been stopped\b"
    r"|^the scenario\b"
    r"|^something went wrong\b", re.I)


def is_delivery(name: Optional[str]) -> bool:
    """Is this a piece of work, or a notification someone filed as a task?"""
    return not _NOT_DELIVERY.search((name or "").strip())


def _bulk_closed(rows: list) -> dict:
    """Timestamps where many tasks changed status at once.

    A sprint tidy-up closes forty tickets in one minute. Listing those as forty
    things delivered this week is the kind of claim a client checks once and
    then stops believing the whole report. Counted, named, and kept OUT of the
    shipped list rather than silently included.
    """
    by: dict = {}
    for r in rows:
        key = (r.get("status_changed_ms") or 0) // 60_000      # to the minute
        by[key] = by.get(key, 0) + 1
    return {k: n for k, n in by.items()
            if n >= config.HOURS_BULK_CLOSE_MIN and k}


def shipped(conn, client_id: str, lo: str, hi: str) -> list:
    """Tasks that ENTERED a done status inside the window.

    Not "tasks that are done now": a task finished three sprints ago is still
    done today, and listing it every week would make the report a lie by
    repetition. The window is on the status change, which is what puts a
    completion in the week it actually happened.
    """
    qs = ",".join("?" * len(config.HOURS_DONE_TYPES))
    rows = [dict(r) for r in conn.execute(
        f"SELECT name, status, url, status_changed_ms FROM hours_tasks"
        f" WHERE client_id=? AND gone_at IS NULL"
        f" AND status_type IN ({qs})"
        f" AND status_changed_ms BETWEEN ? AND ?"
        f" ORDER BY status_changed_ms",
        (client_id, *config.HOURS_DONE_TYPES, _ms(lo), _ms(hi, end=True)))]
    bulk = _bulk_closed(rows)
    out, filtered_alerts, filtered_bulk = [], 0, 0
    for r in rows:
        if not is_delivery(r["name"]):
            filtered_alerts += 1
            continue
        if ((r.get("status_changed_ms") or 0) // 60_000) in bulk:
            filtered_bulk += 1
            continue
        out.append({"name": r["name"], "status": r["status"], "url": r["url"],
                    "when": dt.datetime.fromtimestamp(
                        (r["status_changed_ms"] or 0) / 1000,
                        config.TZ).strftime("%a %d %b")})
    return {"items": out, "excluded_alerts": filtered_alerts,
            "excluded_bulk_close": filtered_bulk,
            "bulk_close_events": len(bulk)}


# "Encountered error in [Tech Interview] 8 Rejected - Email scenario - 4 Oct"
# "The scenario [HIPP] 3 Add Clinician to Client has been stopped - 24 Sep"
_SCENARIO = re.compile(
    r"encountered error in\s+(?P<a>.+?)\s+scenario\b"
    r"|the scenario\s+(?P<b>.+?)\s+has been stopped\b"
    r"|something went wrong with an automation:\s*(?P<c>.+?)\s*$", re.I)
_TRAILING_DATE = re.compile(r"\s*[-–]\s*\d{1,2}\s+\w{3,}\s*$")


def scenario_of(name: str) -> str:
    """The automation an alert ticket is about, or the ticket's own name."""
    m = _SCENARIO.search(name or "")
    if m:
        got = m.group("a") or m.group("b") or m.group("c") or ""
        return _TRAILING_DATE.sub("", got).strip() or name.strip()
    return _TRAILING_DATE.sub("", (name or "").strip())


def caught(conn, client_id: str, lo: str, hi: str) -> dict:
    """Platform alerts that reached us for this client in the window.

    SOURCED FROM THE ALERT TICKETS, not the mail rail. Make's alert mails are
    excluded by the sweep with no client attached (`ops-alert:...` with a null
    proposed client), so the events table cannot say WHOSE alert it was. The
    same alerts become tasks in the client's own sprint folder, which attributes
    them for free — measured 2026-10-19, that is 54 for Horizons 44 against
    zero findable in events.

    Grouped by scenario: "this scenario failed nine times" is one problem, and
    a client reading nine lines learns less than one reading one.
    """
    qs = ",".join("?" * len(config.HOURS_DONE_TYPES))
    rows = [dict(r) for r in conn.execute(
        "SELECT name, status, status_type, status_changed_ms, created_ms"
        " FROM hours_tasks WHERE client_id=? AND gone_at IS NULL"
        " AND COALESCE(created_ms, status_changed_ms) BETWEEN ? AND ?",
        (client_id, _ms(lo), _ms(hi, end=True)))]
    by: dict = {}
    for r in rows:
        if is_delivery(r["name"]):
            continue                        # a real task, not an alert
        sc = scenario_of(r["name"])
        b = by.setdefault(sc, {"scenario": sc, "occurrences": 0,
                               "resolved": 0, "last": None})
        b["occurrences"] += 1
        if (r.get("status_type") or "") in config.HOURS_DONE_TYPES:
            b["resolved"] += 1
        day = dt.datetime.fromtimestamp(
            (r.get("created_ms") or r.get("status_changed_ms") or 0) / 1000,
            config.TZ).strftime("%Y-%m-%d")
        b["last"] = max(b["last"], day) if b["last"] else day
    items = sorted(by.values(), key=lambda x: -x["occurrences"])
    total = sum(x["occurrences"] for x in items)
    return {"scenarios": items, "total_alerts": total,
            "resolved": sum(x["resolved"] for x in items),
            "note": "alerts that reached us during the week, grouped by the"
                    " automation they came from. Run counts and success rates"
                    " are not here — those live with the monitoring platform."}


def in_progress(conn, client_id: str, now_ms: int) -> list:
    qs = ",".join("?" * len(config.HOURS_DONE_TYPES))
    return [{"name": r["name"], "status": r["status"],
             "open_for": _age_words(r["created_ms"], now_ms)}
            for r in conn.execute(
                f"SELECT name, status, created_ms FROM hours_tasks"
                f" WHERE client_id=? AND gone_at IS NULL"
                f" AND status_type NOT IN ({qs})"
                f" ORDER BY created_ms",
                (client_id, *config.HOURS_DONE_TYPES))]


def needs_input(conn, client_id: str, now_ms: int) -> list:
    """Where the ball is with the client — tasks parked in a waiting status,
    and commitments we recorded as theirs."""
    # classified in Python through status_class, not `lower(status) IN (...)`:
    # the exact match missed "NEEDS CLIENT REVIEW", which is Hermon's real
    # status and the most important line a client report carries
    out = [{"what": r["name"], "why": r["status"], "source": "task",
            "waiting": _age_words(r["status_changed_ms"], now_ms)}
           for r in conn.execute(
               "SELECT name, status, status_type, status_changed_ms"
               " FROM hours_tasks WHERE client_id=? AND gone_at IS NULL",
               (client_id,))
           if status_class(r["status"], r["status_type"]) == "review"]
    try:
        # who='client' / state='open' are the real column values (the first
        # version asked for owner='them' / resolved_at, which do not exist, so
        # the except below swallowed it and the section was always empty)
        out += [{"what": r["text"], "why": "promised to us", "source":
                 "commitment", "waiting": None}
                for r in conn.execute(
                    "SELECT text FROM commitments WHERE client_id=?"
                    " AND who='client' AND state='open'"
                    " AND COALESCE(review_state,'') != 'stale'"
                    " ORDER BY id DESC LIMIT 10",
                    (client_id,))]
    except Exception:                                            # noqa: BLE001
        pass            # commitments schema varies; the task list stands alone
    return out


def status_class(status: Optional[str], status_type: Optional[str]) -> str:
    """done | review | active — the three things a client reads a status as.

    `review` is matched by substring, not membership: the first version looked
    for an exact "client review" and missed Hermon's actual status, "NEEDS
    CLIENT REVIEW", which is the single most important line on the report —
    it is the one asking the client to do something.
    """
    s = (status or "").strip().lower()
    if (status_type or "").lower() in config.HOURS_DONE_TYPES:
        return "done"
    if any(k in s for k in config.HOURS_AWAITING_CLIENT):
        return "review"
    return "active"


def _tags(raw) -> list:
    try:
        tags = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return [t for t in tags if isinstance(t, dict) and t.get("name")]


def workstreams(conn, client_id: str, list_id: Optional[str],
                lo: str, hi: str) -> dict:
    """Hours grouped the way his own export groups them: by the task's TAGS.

    His Sprint 22 export for Hermon is headed "Grouped by tag" — cashswipe,
    eskool, mc, spm, cashcow … each a workstream, task rows under it, a total
    per group. A task with two tags is its own group ("vibe coding cashswipe +
    vibe coding spm"), exactly as ClickUp shows it, because splitting its time
    across both would invent an allocation nobody made.

    Rows are every task with time logged in the window, PLUS tasks in the
    period's sprint list with none yet — scope the team is carrying is part of
    the picture. Alert tickets with no time are left out: fifty-four "the
    scenario has been stopped" rows at zero minutes would bury the work.
    """
    tasks: dict = {}
    for r in conn.execute(
            "SELECT task_id, task_name, user_name, duration_ms, task_tags_json,"
            " task_status, task_status_type, folder_name FROM hours_entries"
            " WHERE client_id=? AND gone_at IS NULL AND duration_ms > 0"
            " AND start_ms BETWEEN ? AND ?",
            (client_id, _ms(lo), _ms(hi, end=True))):
        key = r["task_id"] or f"name:{r['task_name']}"
        t = tasks.setdefault(key, {
            "name": r["task_name"] or "Untitled task", "ms": 0, "team": set(),
            "tags": _tags(r["task_tags_json"]), "status": r["task_status"],
            "status_type": r["task_status_type"], "folder": r["folder_name"]})
        t["ms"] += r["duration_ms"]
        if r["user_name"]:
            t["team"].add(r["user_name"])

    if list_id:
        listed = [dict(r) for r in conn.execute(
            "SELECT id, name, status, status_type, assignees_json, tags_json,"
            " status_changed_ms FROM hours_tasks"
            " WHERE list_id=? AND gone_at IS NULL", (list_id,))]
        # the same tidy-up rule as shipped(): a zero-time task closed in a
        # bulk sweep would otherwise reach "Delivered" through this side door
        bulk = _bulk_closed([r for r in listed if (r["status_type"] or "")
                             .lower() in config.HOURS_DONE_TYPES])
        for r in listed:
            if r["id"] in tasks or not is_delivery(r["name"]):
                continue
            if ((r["status_changed_ms"] or 0) // 60_000) in bulk and \
                    (r["status_type"] or "").lower() in config.HOURS_DONE_TYPES:
                continue
            try:
                team = set(json.loads(r["assignees_json"] or "[]")) - {None}
            except (TypeError, ValueError):
                team = set()
            tasks[r["id"]] = {"name": r["name"], "ms": 0, "team": team,
                              "tags": _tags(r["tags_json"]),
                              "status": r["status"],
                              "status_type": r["status_type"], "folder": None}

    groups: dict = {}
    for t in tasks.values():
        names = sorted({x["name"] for x in t["tags"]})
        key = " + ".join(names) or "No tags"
        g = groups.setdefault(key, {
            "name": key,
            "color": next((x.get("color") for x in t["tags"] if x.get("color")),
                          None),
            "ms": 0, "rows": []})
        g["ms"] += t["ms"]
        cls = status_class(t["status"], t["status_type"])
        g["rows"].append({"name": t["name"], "ms": t["ms"],
                          "team": sorted(t["team"]),
                          "status": t["status"] or "—", "cls": cls})

    out = sorted(groups.values(), key=lambda g: -g["ms"])
    for g in out:
        g["rows"].sort(key=lambda x: (-x["ms"], x["name"].lower()))
        g["task_count"] = len(g["rows"])
    # "No tags" is ClickUp's word, not a client's: on a report it reads like a
    # filing error. One group for the whole period is just "All work"; beside
    # tagged workstreams it is "Other work" — and it is never named in the
    # summary sentence as somewhere the time "went".
    for g in out:
        if g["name"] == "No tags":
            g["name"] = "All work" if len(out) == 1 else "Other work"
            g["catch_all"] = True
    rows = [x for g in out for x in g["rows"]]
    return {"groups": out,
            "total_ms": sum(g["ms"] for g in out),
            "task_count": len(rows),
            "done": sum(1 for x in rows if x["cls"] == "done"),
            "review": [dict(x, workstream=g["name"]) for g in out
                       for x in g["rows"] if x["cls"] == "review"],
            "active": [dict(x, workstream=g["name"]) for g in out
                       for x in g["rows"] if x["cls"] == "active"],
            "people": sorted({p for x in rows for p in x["team"]})}


def summary_text(client_name: str, ws: dict, shipped_n: int,
                 caught_: dict) -> str:
    """Two or three plain sentences built ONLY from the figures above them.

    Templated, not generated: a model asked to summarise a client's week will
    reach for a claim the data does not make, and this paragraph is the one the
    client reads first.
    """
    h = ws["total_ms"] / MS_H
    parts = [f"This period the team logged {h:.1f} hours for {client_name}"
             f" across {ws['task_count']} tasks"
             + (f" in {len(ws['groups'])} workstreams." if len(ws["groups"]) > 1
                else ".")]
    tops = [g for g in ws["groups"]
            if g["ms"] > 0 and not g.get("catch_all")][:3]
    if len(tops) > 1:
        parts.append("Most of the time went to " + ", ".join(
            f"{g['name']} ({g['ms'] / MS_H:.1f}h)" for g in tops) + ".")
    tail = f"{ws['done']} tasks are complete"
    if ws["review"]:
        tail += (f" and {len(ws['review'])} are waiting on your review"
                 " — listed first below")
    parts.append(tail + ".")
    if caught_.get("total_alerts"):
        parts.append(f"We also handled {caught_['total_alerts']} automation"
                     f" alerts, {caught_['resolved']} of them already resolved.")
    return " ".join(parts)


def build(conn, client_id: str, list_id: Optional[str], today: str) -> dict:
    """The whole report for one client and one period."""
    period = hr.client_period(conn, client_id, list_id, today)
    if period.get("error"):
        return {"error": period["error"]}
    lo, hi = period["starts_on"], period["ends_on"]
    now_ms = int(dt.datetime.now(config.TZ).timestamp() * 1000)

    ship = shipped(conn, client_id, lo, hi)
    done = ship["items"]
    alerts = caught(conn, client_id, lo, hi)
    open_items = [x for x in in_progress(conn, client_id, now_ms)
                  if is_delivery(x["name"])]
    waiting = needs_input(conn, client_id, now_ms)
    period_list = next((p["list_id"] for p in period["periods"]
                        if p["name"] == period["period"]), list_id)
    ws = workstreams(conn, client_id, period_list, lo, hi)
    name = client_name(client_id)

    return {
        "as_of": period["as_of"],
        "client_id": client_id,
        "client_name": name,
        "period": period["period"], "starts_on": lo, "ends_on": hi,
        "open": period["open"],
        "workstreams": ws,
        "summary": summary_text(name, ws, len(done), alerts),
        "hours": {"total": period["hours"], "entries": period["entries"],
                  "by_folder": period["by_folder"],
                  # the breakdown a client sees is the one they asked for; the
                  # default is a number (hours brief §6)
                  "breakdown": [
                      x for key in config.HOURS_REPORT_BREAKDOWNS.get(
                          client_id, ())
                      for x in (period.get(f"by_{key}") or [])]},
        "shipped": done,
        # what section 1 LEFT OUT and why — stated on the data, not buried in
        # a code comment, because the exclusions are big enough to change the
        # report's character and he has to be able to disagree with them
        "shipped_excluded": {
            "alert_tickets": ship["excluded_alerts"],
            "bulk_closed": ship["excluded_bulk_close"],
            "bulk_close_events": ship["bulk_close_events"],
            "why": "alert mails filed as tasks belong in section 2, and a"
                   " sprint tidy-up that closes many tickets in one minute is"
                   " not that many things delivered",
        },
        "caught": alerts,
        "in_progress": open_items,
        "needs_input": waiting,
        # said on the report itself, not just in the UI: a client who reads a
        # number on Monday and a different one on Thursday needs to know why
        "basis": ("Hours count toward this period by when the work was logged."
                  " The figure is as-of the stamp above and can still move:"
                  " time is sometimes logged after a period closes."),
        "empty_sections": [k for k, v in (("shipped", done),
                                          ("caught", alerts["scenarios"]),
                                          ("in_progress", open_items),
                                          ("needs_input", waiting)) if not v],
    }


def render_markdown(rep: dict) -> str:
    """The sendable artefact. Markdown, because he pastes it into email and
    Slack — an HTML file he has to open and copy out of is a worse deliverable
    than text he can paste."""
    if rep.get("error"):
        return f"_{rep['error']}_"
    L = [f"# {rep['client_id']} — week of {rep['starts_on']}",
         f"_{rep['period']} · {rep['starts_on']} to {rep['ends_on']}"
         f"{' · still open' if rep['open'] else ''}_", ""]

    L += ["## 1 · Shipped this week", ""]
    L += [f"- **{x['name']}** — {x['status']} ({x['when']})"
          for x in rep["shipped"]] or ["_Nothing closed in this period._"]
    ex = rep.get("shipped_excluded") or {}
    if ex.get("alert_tickets") or ex.get("bulk_closed"):
        bits = []
        if ex.get("alert_tickets"):
            bits.append(f"{ex['alert_tickets']} alert tickets (section 2)")
        if ex.get("bulk_closed"):
            bits.append(f"{ex['bulk_closed']} closed in a bulk tidy-up")
        L += ["", f"<!-- not listed above: {'; '.join(bits)} -->"]

    c = rep["caught"]
    L += ["", "## 2 · Caught before you noticed", ""]
    if c["scenarios"]:
        L.append(f"{c['total_alerts']} alerts across "
                 f"{len(c['scenarios'])} automations, "
                 f"{c['resolved']} already resolved:")
        L += [f"- **{x['scenario']}** — {x['occurrences']} "
              f"occurrence{'s' if x['occurrences'] != 1 else ''}"
              + (f", {x['resolved']} resolved" if x["resolved"] else "")
              for x in c["scenarios"][:10]]
    else:
        L.append("_No platform alerts reached us this week._")

    L += ["", "## 3 · Hours", "",
          f"**{rep['hours']['total']} hours** across "
          f"{rep['hours']['entries']} entries."]
    for x in rep["hours"]["breakdown"][:12]:
        L.append(f"- {x['key']} — {x['hours']} h")

    L += ["", "## 4 · In progress / next week", ""]
    L += [f"- **{x['name']}** — {x['status']}, open {x['open_for']}"
          for x in rep["in_progress"][:12]] or ["_Nothing open._"]

    L += ["", "## 5 · Needs your input", ""]
    L += [f"- {x['what']}" + (f" — waiting {x['waiting']}" if x["waiting"] else "")
          for x in rep["needs_input"][:10]] or ["_Nothing waiting on you._"]

    L += ["", "---", f"_Prepared by Etherwise · hours as of {rep['as_of']}._",
          f"_{rep['basis']}_"]
    return "\n".join(L)
