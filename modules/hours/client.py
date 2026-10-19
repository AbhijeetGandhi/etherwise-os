"""ClickUp transport for the hours sync — GET ONLY, enforced in code.

READ-ONLY IS A LAW, NOT A HABIT (hours brief §1.1). ClickUp is where the team
logs time; this module reads it and nothing else. `_request` refuses any method
but GET before it touches the network, so a future edit cannot quietly add a
write path — the same construction as
`modules/upwork/feeds/mcp_client.py`, which refuses non-read tools rather than
being trusted not to call them.

WHAT THE API ACTUALLY DOES (probed live 2026-10-19; these are measurements):

  * `include_location_names=true` fills `task_location` with list/folder/space
    ids AND names. Without it the field is null — which is what the ClickUp MCP
    wrapper returns, and why the first draft of the brief assumed the sync would
    have to resolve task -> list -> folder -> space itself. It does not.
  * `space_id` and `list_id` filter server-side.
  * `assignee` MUST be passed explicitly. Omit it and the API silently returns
    only the authenticated user's entries — a sync that looks like it worked and
    under-bills every client. There is no "any" on the raw endpoint (that is an
    MCP convenience); pass every member id.
  * `duration` is integer milliseconds and is authoritative; the "2h 45m" string
    is display. A NEGATIVE duration is a running timer, not a value to sum.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from typing import Optional

from core import config

API = "https://api.clickup.com/api/v2"
TEAM_ID = "9016735299"
_ENV_FILE = config.CREDENTIALS_DIR / "etherwise-os.env"
_TIMEOUT = 60


class ClickUpReadError(Exception):
    pass


def token() -> str:
    """The key, read the same way scripts/clickup_push.py reads it. Never
    logged, never echoed, never returned in metrics."""
    try:
        for line in _ENV_FILE.read_text().splitlines():
            if line.startswith("CLICKUP_API_KEY="):
                return line.split("=", 1)[1].strip()
    except OSError as exc:
        raise ClickUpReadError(f"cannot read {_ENV_FILE.name}: {exc}") from exc
    raise ClickUpReadError("CLICKUP_API_KEY not found in credentials env")


# ClickUp rate-limits per minute and answers 429 when you cross it. A full
# backfill is ~130 windowed reads plus splits, which crosses it comfortably —
# measured 2026-10-19, the first full sync died on APP_002 partway through.
# Pace the calls rather than discovering the ceiling each run, and honour
# Retry-After when it still says no.
_MIN_INTERVAL_S = 0.7
_MAX_RETRIES = 4
_last_call = [0.0]


def _throttle() -> None:
    import time
    wait = _MIN_INTERVAL_S - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()


def _request(path: str, params: dict, tok: str, *, method: str = "GET",
             _sleep=None) -> dict:
    if method != "GET":
        raise ClickUpReadError(
            f"hours sync is read-only: refused {method} {path}")
    import time
    sleep = _sleep or time.sleep
    qs = urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(f"{API}{path}?{qs}", method="GET",
                                 headers={"Authorization": tok})
    for attempt in range(_MAX_RETRIES):
        _throttle()
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as exc:                    # noqa: PERF203
            body = exc.read().decode()[:200]
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or attempt == _MAX_RETRIES - 1:
                raise ClickUpReadError(
                    f"GET {path} -> {exc.code}: {body}") from exc
            delay = float(exc.headers.get("Retry-After") or 0) or 2 ** (attempt + 1)
            sleep(delay)
        except (urllib.error.URLError, OSError) as exc:          # noqa: PERF203
            # A dropped connection is as retryable as a 429 and arrives as a
            # DIFFERENT exception — measured 2026-10-19, "Connection reset by
            # peer" killed a full sync two-thirds through because only
            # HTTPError was being caught. A nightly job that dies on one reset
            # is a job that silently stops producing numbers.
            if attempt == _MAX_RETRIES - 1:
                raise ClickUpReadError(f"GET {path}: {exc}") from exc
            sleep(2 ** (attempt + 1))
    raise ClickUpReadError(f"GET {path}: retries exhausted")      # unreachable


def ist_ms(day: str, *, end: bool = False) -> int:
    """A YYYY-MM-DD IST day boundary as epoch ms.

    IST, not UTC, deliberately: Jay logs in the evening, and a UTC boundary
    moves an evening entry into the next day — which at a period edge is the
    wrong invoice.
    """
    d = dt.date.fromisoformat(day)
    t = dt.time(23, 59, 59, 999000) if end else dt.time(0, 0)
    return int(dt.datetime.combine(d, t, tzinfo=config.TZ).timestamp() * 1000)


def time_entries(tok: str, *, start_ms: int, end_ms: int,
                 member_ids: list, space_id: Optional[str] = None,
                 list_id: Optional[str] = None) -> list:
    """Time entries in a window. Raises rather than returning a short list on
    error — an hours sync that fails quietly bills the wrong number."""
    params = {
        "start_date": start_ms,
        "end_date": end_ms,
        "assignee": ",".join(str(m) for m in member_ids),
        "include_location_names": "true",
        # the task's tags, with ClickUp's own colours — the workstream a
        # client recognises their bill by (migration 043)
        "include_task_tags": "true",
    }
    if space_id:
        params["space_id"] = space_id
    if list_id:
        params["list_id"] = list_id
    out = _request(f"/team/{TEAM_ID}/time_entries", params, tok)
    return out.get("data") or []


def members(tok: str) -> list:
    """[{id, username, email}] for the workspace."""
    out = _request(f"/team/{TEAM_ID}", {}, tok)
    team = (out.get("team") or {})
    return [{"id": str(m["user"]["id"]),
             "username": m["user"].get("username"),
             "email": m["user"].get("email")}
            for m in (team.get("members") or []) if m.get("user")]


def tasks_in_list(tok: str, list_id: str) -> list:
    """Every task in a list, including closed ones.

    `include_closed=true` is the whole point: the EOW report's first section is
    what SHIPPED, and ClickUp omits closed tasks by default — ask without it and
    the report says nothing was finished. Paged at 100 (ClickUp's own page
    size), walked to the end rather than trusted to be one page.
    """
    out, page = [], 0
    while True:
        res = _request(f"/list/{list_id}/task",
                       {"include_closed": "true", "subtasks": "true",
                        "page": page}, tok)
        batch = res.get("tasks") or []
        out.extend(batch)
        if res.get("last_page") or not batch:
            break
        page += 1
        if page > 20:                       # 2000 tasks in one sprint: report it
            break
    return out


def lists_in_folder(tok: str, folder_id: str) -> list:
    """[{id, name}] — the sprint lists, whose NAMES carry the period window."""
    out = _request(f"/folder/{folder_id}/list", {"archived": "false"}, tok)
    return [{"id": str(x["id"]), "name": x.get("name")}
            for x in (out.get("lists") or [])]
