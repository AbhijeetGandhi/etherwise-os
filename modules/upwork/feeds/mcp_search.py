"""The standing-query round against mcp.upwork.com — shared by the shadow
poller and the job-feed provider path (Phase 0.1, Upwork chat 2026-08-25).

NETWORK ONLY, NO DATABASE. Both callers used to interleave MCP calls with an
open write transaction (finding 12: the poller held the lock for 43 s against
a 10 s busy timeout and killed a concurrent writer). Splitting the round into
"fetch everything" (here) and "write once" (the callers) is the fix, and it is
the same house rule as "model calls outside the with-block".

Bounded by construction (API & MCP Terms §4.1): first pages only per query,
one `get` per NEW job, a hard per-run ceiling on gets, a fixed cadence (hourly
until 2026-10-18, every quarter hour since, his decision:
config.UPWORK_SCAN_MINUTES). The bounds are config (MCP_SMART_PAGES,
MCP_PER_QUERY_LIMIT, MCP_MAX_GETS_PER_RUN, MCP_TERM_QUERIES), never literals
here.

THE DAILY ALLOWANCE OF JOB OPENS (2026-11-10). From 6 Nov Upwork refuses
`get` once a day's allowance is spent: "This job can't be opened right now. It
may be private or invite-only, or jobs are being opened too quickly. Slow down
and try again later, and don't retry the same job repeatedly." Measured 6-9
Nov: 75-98 opens a day (the funnel's nightly re-read included) and then
nothing until 00:00 UTC (05:30 IST). So, three things, each one his ask of us:
  * no get is spent on a job its search row already settles (the hard rules
    reject it on the full text include_full_details gives the keyword rows);
  * two refusals in a row — across runs: most runs now ask for one post or
    none — and gets PAUSE until the next 00:00 UTC, kept in the pause file the
    scan passes (beside the database), so nobody asks all evening;
  * the rate and the client's spend are read off the row (mcp_feed), so a job
    judged without its get is judged on what Upwork showed, not on a blank.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from core import config
from modules.upwork.feeds import mcp_feed

SMART_LABEL = "mcp:smart_search"
GET_REFUSALS_TO_PAUSE = 2    # refusals in a row before gets wait for the reset
_REFUSED = "opened too quickly"     # Upwork's words; a private job says it too
_sleep = time.sleep          # injectable for tests
_last_call = [0.0]           # monotonic stamp of the previous paced call


def paced_call(session, tool: str, args: dict, log: Callable[[str], None]):
    """session.call with the server's pace respected: wait out
    MCP_CALL_INTERVAL_S since the previous call, and on a rate-limit refusal
    wait MCP_RATE_LIMIT_WAIT_S and retry ONCE. Measured 2026-08-25 22:41: the
    tool allows ~12 find_jobs requests/min sustained; a refused query would
    otherwise be an hour of silent under-scanning for that term."""
    gap = config.MCP_CALL_INTERVAL_S - (time.monotonic() - _last_call[0])
    if gap > 0 and _last_call[0]:
        _sleep(gap)
    try:
        return session.call(tool, args)
    except Exception as exc:                                     # noqa: BLE001
        if "rate limit" not in repr(exc).lower():
            raise
        log(f"{tool} {args.get('action')}: rate-limited, retrying once after"
            f" {config.MCP_RATE_LIMIT_WAIT_S:.0f}s")
        _sleep(config.MCP_RATE_LIMIT_WAIT_S)
        return session.call(tool, args)
    finally:
        _last_call[0] = time.monotonic()


def in_scan_window(minute: int) -> bool:
    """True from one minute before each scan slot (config.UPWORK_SCAN_MINUTES)
    to five after: the minutes the job scan is reading find_jobs. Another reader
    of the same tool (the funnel's job fetch) waits these out, so the two never
    share the tool's per-minute allowance. The window is the one the hourly
    scan had (:14-:20), kept for every slot."""
    return any((minute - m + 1) % 60 <= 6 for m in config.UPWORK_SCAN_MINUTES)


def org_uid(session) -> str:
    accounts = (session.call("list_accounts", {}) or {}).get("accounts") or []
    for a in accounts:
        if a.get("role") == "TALENT":
            return str(a["org_uid"])
    raise RuntimeError("no TALENT account on this token")


def search_rows(session, org: str, log: Callable[[str], None]) -> dict:
    """{job_id: search_row} across every standing query, first pages only.
    A row keeps the FIRST query that found it in `_feed_source`; later finders
    are appended to `_also` so the report can rank query yield."""
    rows: dict = {}

    def absorb(jobs, label):
        for j in jobs or []:
            jid = str(j.get("id") or "")
            if not jid:
                continue
            if jid in rows:
                rows[jid].setdefault("_also", []).append(label)
                # Best Matches rows carry NO proposal count (0 of 494 in the
                # two weeks to 22 Oct); a keyword row for the same job does.
                # Keep it: it is the crowd check's free reading.
                if (not isinstance(rows[jid].get("proposal_count"), int)
                        and isinstance(j.get("proposal_count"), int)):
                    rows[jid]["proposal_count"] = j["proposal_count"]
                # Best Matches rows carry a 300-character snippet; a keyword
                # row asked for include_full_details carries the whole post
                if not rows[jid].get("description") and j.get("description"):
                    rows[jid]["description"] = j["description"]
            else:
                rows[jid] = {**j, "_feed_source": label, "_also": []}

    cursor = None
    for page in range(config.MCP_SMART_PAGES):
        params = {"cursor": cursor} if cursor else {}
        try:
            res = paced_call(session, "find_jobs",
                             {"action": "smart_search", "org_uid": org,
                              "params": params}, log)
        except Exception as exc:                                 # noqa: BLE001
            log(f"smart_search p{page + 1} failed: {exc!r}")
            break
        absorb(res.get("jobs"), SMART_LABEL)
        pi = res.get("pageInfo") or {}
        cursor = pi.get("endCursor")
        if not pi.get("hasNextPage"):
            break

    for q in config.MCP_TERM_QUERIES:
        try:
            res = paced_call(session, "find_jobs", {
                "action": "search", "org_uid": org,
                "params": {"query": q, "sort": "recency",
                           "limit": config.MCP_PER_QUERY_LIMIT,
                           # the whole post on the row (probed 10 Nov: 5,362
                           # characters while `get` was refused) — the rules
                           # and the scorer read it when the get cannot run
                           "include_full_details": True}}, log)
        except Exception as exc:                                 # noqa: BLE001
            log(f"search {q!r} failed: {exc!r}")
            continue
        absorb(res.get("jobs"), f"mcp:{q}")
    return rows


# ── the crowd check's reading (2026-10-23) ────────────────────────────────────
# Best Matches never says how many proposals a job has, and `get` does not
# either. A `title` search does: it matches the job title only ("1-3 key words,
# ANDed, stemmed, whole words"), newest first, and every row carries the live
# proposal_count. Probed on 23 Oct against the 12 newest Best Matches cards:
# 12 of 12 found on the first page.
_LOOKUP_STOP = frozenset(
    "a an and the for of to in on with by or at from your our we you need needed "
    "looking expert specialist developer freelancer help seeking wanted urgent "
    "long term part full time project build builder setup set up into via using "
    "new needs is are be will can this that it ai".split())
_LOOKUP_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+#-]*")


def lookup_words(title: str, n: int = 3) -> list:
    """The few title words most likely to find this job and few others."""
    words = [w.strip(".-") for w in _LOOKUP_WORD.findall(title or "")]
    return [w for w in words if len(w) > 2 and not w.isdigit()
            and w.lower() not in _LOOKUP_STOP][:n]


def proposal_count(session, org: str, job_id: str, title: str,
                   log: Callable[[str], None], pages: int = 2) -> Optional[int]:
    """The live proposal count on one job, read off a title search; None when
    the search does not return it (the caller treats None as 'not known',
    never as zero)."""
    words = lookup_words(title)
    if not words:
        return None
    cursor = None
    for _ in range(pages):
        params = {"title": " ".join(words), "limit": config.MCP_PER_QUERY_LIMIT}
        if cursor:
            params["cursor"] = cursor
        try:
            res = paced_call(session, "find_jobs", {"action": "search",
                             "org_uid": org, "params": params}, log) or {}
        except Exception as exc:                                 # noqa: BLE001
            log(f"crowd lookup {job_id} failed: {exc!r}")
            return None
        for r in res.get("jobs") or []:
            if str(r.get("id")) == str(job_id):
                pc = r.get("proposal_count")
                return pc if isinstance(pc, int) else None
        info = res.get("pageInfo") or {}
        cursor = info.get("endCursor")
        if not info.get("hasNextPage") or not cursor:
            return None
    return None


def get_detail(session, org: str, jid: str, log: Callable[[str], None]):
    """(row, refused): one `get`. row is None (logged) when it fails — the
    search row still carries the rate, the spend and the rules' text, so a
    failed get degrades, never aborts. refused is True when Upwork said the
    day's allowance is spent ("jobs are being opened too quickly")."""
    try:
        return paced_call(session, "find_jobs",
                          {"action": "get", "org_uid": org, "params": {"id": jid}},
                          log), False
    except Exception as exc:                                     # noqa: BLE001
        log(f"get {jid} failed: {exc!r}")
        return None, _REFUSED in str(exc).lower()


def get_row(session, org: str, jid: str, log: Callable[[str], None]):
    """One `get`; None (logged) when it fails (get_detail without the flag)."""
    return get_detail(session, org, jid, log)[0]


def next_reset(now: Optional[datetime] = None) -> datetime:
    """The next 00:00 UTC — where the allowance came back on 7, 8 and 9 Nov
    (05:31 IST, the first scan after it, every day)."""
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                             microsecond=0)


def _read_state(path: Optional[Path]) -> dict:
    """The pause file: {"until": when gets resume (absent = not paused),
    "streak": refusals in a row since the last reset, "last_refusal": when}."""
    if not path:
        return {}
    try:
        state = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write_state(path: Optional[Path], state: dict, log: Callable[[str], None]):
    if not path:
        return
    try:
        Path(path).write_text(json.dumps(state))
    except OSError as exc:
        log(f"get pause state not saved ({exc!r}); it holds for this run only")


def paused_until(path: Optional[Path], now: Optional[datetime] = None
                 ) -> Optional[datetime]:
    """When gets resume, if they are paused now; None when they are not (or
    there is no pause file to read: tests, the shadow poller)."""
    until = parse_instant(_read_state(path).get("until"))
    now = now or datetime.now(timezone.utc)
    return until if until and until > now else None


def refusal_streak(path: Optional[Path], now: Optional[datetime] = None) -> int:
    """Refusals in a row carried over from earlier runs — only those since the
    last reset: yesterday's refusal says nothing about today's allowance."""
    state = _read_state(path)
    last = parse_instant(state.get("last_refusal"))
    now = now or datetime.now(timezone.utc)
    if not last or last < next_reset(now) - timedelta(days=1):
        return 0
    try:
        return int(state.get("streak") or 0)
    except (TypeError, ValueError):
        return 0


def pause_gets(path: Optional[Path], log: Callable[[str], None], reason: str,
               now: Optional[datetime] = None, streak: int = GET_REFUSALS_TO_PAUSE
               ) -> datetime:
    """Stop asking for posts until the next reset; persisted when a path is
    given, so the next quarter hour does not ask again."""
    now = now or datetime.now(timezone.utc)
    until = next_reset(now)
    _write_state(path, {"until": until.isoformat(), "since": now.isoformat(),
                        "streak": streak, "last_refusal": now.isoformat(),
                        "reason": reason[:300]}, log)
    log(f"gets PAUSED until {until.isoformat()} — Upwork refused"
        f" {streak} in a row (the day's job-open allowance is spent); jobs are"
        " judged on their search rows until then")
    return until


def settled_without_get(job: dict, srow: dict, rule_check) -> bool:
    """True when the search row alone settles a job the hard rules reject, so
    a get would be spent on a job that cannot be scored. Only with the whole
    post on the row (a snippet may hide a placeholder price, or the other tool
    that keeps an n8n post in), and never for the reasons a get can overturn:
    the rate it could not read, or a hire count the row did not carry."""
    if not srow.get("description") or rule_check is None:
        return False
    reason = rule_check(job)
    if not reason or reason == "rate_unread":
        return False
    if (reason == "rate_unstated_low_client" and job.get("client_hires") is None
            and job.get("client_payment_verified")):
        return False
    return True


def parse_instant(value) -> Optional[datetime]:
    """An MCP created_date ('2026-08-22T12:25:48+0000') or a config instant
    ('2026-08-26T09:00:00+00:00') as an aware datetime; None if unparseable.
    3.9's fromisoformat rejects the bare '+0000' offset, hence the repair."""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    if len(s) >= 5 and s[-5] in "+-" and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch_new(session, org: str, log: Callable[[str], None], *,
              known: Iterable[str], max_gets: Optional[int] = None,
              claim_since: Optional[str] = None, rule_check=None,
              pause_file: Optional[Path] = None) -> dict:
    """The provider round: search, then normalize every job not in `known`
    (one bounded `get` each). Returns {"jobs": [normalized...],
    "metrics": {...}} — the caller writes. Nothing here opens a connection.

    claim_since: an ISO instant; posts created before it are reported
    (`skipped_before_cut`) and not returned. A post with NO parseable
    created_date is kept — dropping fresh work on a parsing quirk would be
    the silent loss this pipeline exists to prevent.

    rule_check: scoring.hard_rule_skip from the scan — a job its search row
    already settles is not opened (settled_without_get). pause_file: where a
    get pause is kept between runs (None = this run only)."""
    known = set(known)
    max_gets = config.MCP_MAX_GETS_PER_RUN if max_gets is None else max_gets
    cut = parse_instant(claim_since)
    pause = paused_until(pause_file)
    if pause:
        log(f"gets paused until {pause.isoformat()} (Upwork's job-open"
            " allowance); jobs are judged on their search rows")
    rows = search_rows(session, org, log)
    jobs, gets, skipped_cut, get_capped = [], 0, 0, 0
    saved = refused = paused = 0
    streak = 0 if pause else refusal_streak(pause_file)
    rate = {"hourly_rows": 0, "hourly_with_budget": 0, "unread": []}
    by_feed: dict = {}
    for jid, srow in rows.items():
        feed = srow["_feed_source"]
        m = by_feed.setdefault(feed, {"extracted": 0, "known": 0, "new": 0,
                                      "skipped_before_cut": 0, "get_capped": 0})
        if str(srow.get("job_type") or "").lower() == "hourly":
            rate["hourly_rows"] += 1
            rate["hourly_with_budget"] += 1 if srow.get("budget") else 0
        m["extracted"] += 1
        if jid in known:
            m["known"] += 1
            continue
        if cut is not None:
            created = parse_instant(srow.get("created_date")
                                    or srow.get("published_date"))
            if created is not None and created < cut:
                m["skipped_before_cut"] += 1
                skipped_cut += 1
                continue
        detail = None
        bare = mcp_feed.normalize(srow, None)
        if settled_without_get(bare, srow, rule_check):
            saved += 1                    # the row settles it: no open spent
        elif pause:
            paused += 1
        elif gets < max_gets:
            detail, was_refused = get_detail(session, org, jid, log)
            gets += 1
            if was_refused:
                refused += 1
                streak += 1
                if streak >= GET_REFUSALS_TO_PAUSE:
                    pause = pause_gets(pause_file, log, f"get {jid} refused",
                                       streak=streak)
                else:
                    _write_state(pause_file, {
                        "streak": streak, "last_refusal":
                        datetime.now(timezone.utc).isoformat()}, log)
            elif detail:
                if streak:
                    _write_state(pause_file, {"streak": 0}, log)
                streak = 0
        else:
            m["get_capped"] += 1
            get_capped += 1
        job = mcp_feed.normalize(srow, detail) if detail else bare
        job["_got_detail"] = bool(detail)
        if job.get("rate_source") == "unread":
            rate["unread"].append({"id": jid, "budget": srow.get("budget")})
        jobs.append(job)
        m["new"] += 1
    return {"jobs": jobs,
            "metrics": {"queries": len(config.MCP_TERM_QUERIES) + config.MCP_SMART_PAGES,
                        "surfaced": len(rows), "new": len(jobs), "gets": gets,
                        "get_capped": get_capped, "skipped_before_cut": skipped_cut,
                        "get_saved": saved, "get_refused": refused,
                        "get_paused": paused,
                        "paused_until": pause.isoformat() if pause else None,
                        "rate": rate, "by_feed": by_feed}}
