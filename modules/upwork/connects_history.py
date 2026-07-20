"""Connects-history scrape — the applied-truth rail (2026-07-08).

Upwork's connects-history page has NO export button, and the underlying
GraphQL (connectsTransactionsHistoryV2) is scope-blocked for our token AND
returns empty via page-context fetch under every tenant id — so the DOM of the
rendered page is the one obtainable source. It shows the recent window
(~8-10 days, no pager); a DAILY scrape with canonical-key dedup accumulates a
continuous history from rail-start (deep backfill simply isn't reachable).

Reuses the scanner's Chrome rails (ensure_scan_tab/navigate/chrome_eval — the
#ew-scan marker tab). Read-only on Upwork; writes only the v3 DB. Same
session-guard idea as the scanner: if the page doesn't show the connects table
(logged out / challenge-walled), the run FAILS loudly, never ingests junk.

Row semantics ('kind'):
  applied      — connects spent WITH a job link (a proposal submission)
  boost        — connects bid on a boost (label mentions boost/outbid)
  boost_refund — boost connects returned (outbid)
  refund       — returned connects (job cancelled etc.)
  purchase     — bought connects
  renewal      — monthly plan connects
  badge        — availability-badge daily spend
  other        — anything new (kept raw; classifier extended as labels appear)
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta

from core import config, db, runner

sys.path.insert(0, str(config.V3_ROOT / "scripts"))
import feed_bridge  # noqa: E402

TASK_NAME = "upwork_connects"
URL = "https://www.upwork.com/nx/plans/connects/history/"

# Extraction + READINESS in one probe, so the poll below can tell three states
# apart that all look like "no rows" from the outside:
#   rows.length > 0   -> the table rendered; take it
#   empty_state       -> the app rendered its own "No Connects transactions"
#                        message: a real answer (or a throttle), not a hang
#   neither           -> still fetching, or genuinely broken
_EXTRACT_JS = """
JSON.stringify({
  url: location.href,
  empty_state: /No Connects transactions/i.test(
      document.body ? document.body.innerText : ''),
  rows: [...document.querySelectorAll('table tbody tr')].map(tr => {
    const tds = [...tr.querySelectorAll('td')];
    const a = tds[1] ? tds[1].querySelector('a[href*="/jobs/"]') : null;
    return {date: tds[0] ? tds[0].innerText.trim() : null,
            action: tds[1] ? tds[1].innerText.trim() : null,
            href: a ? a.getAttribute('href') : null,
            connects: tds[2] ? tds[2].innerText.trim() : null};
  })
})"""

# The table is fetched CLIENT-SIDE and takes ~9s to paint — the old fixed
# 9s sleep sat exactly on that boundary and lost the race about half the time.
# Poll for a definite state instead of guessing a duration.
POLL_EVERY_SECONDS = 2
POLL_TIMEOUT_SECONDS = 45
# ...and once rows appear, let the count settle: the table paints progressively,
# so the first sample is usually short of the full window.
SETTLE_SAMPLES = 2
# CRITICAL: "No Connects transactions" is the app's PRE-FETCH placeholder, not
# an answer — observed true at t+3s and t+6s, with the real rows arriving at
# t+9s. Believing it early makes the rail report an empty window on every run
# and silently lose the applied truth. An empty window is only credible after
# the table has had well past its paint time to appear.
EMPTY_CONFIRM_SECONDS = 24

_JOB_ID = re.compile(r"/jobs/.*?~02(\d+)")
_DELTA = re.compile(r"([+-]\s?\d+)")


def _event_date(raw: str, today) -> str:
    s = (raw or "").strip()
    if s.lower() == "today":
        return today.isoformat()
    if s.lower() == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    return datetime.strptime(s, "%B %d, %Y").date().isoformat()


def _kind(label: str, delta: int, job_id) -> str:
    low = label.lower()
    if "availability badge" in low:
        return "badge"
    if "renewal" in low:
        return "renewal"
    if "purchase" in low or "bought" in low or "buy" in low:
        return "purchase"
    if "boost" in low or "outbid" in low:
        return "boost_refund" if delta > 0 else "boost"
    if delta > 0 and ("cancel" in low or "refund" in low or "return" in low
                      or "expired" in low or job_id):
        return "refund"
    if delta < 0 and job_id:
        return "applied"           # connects spent on a job = a submission
    return "other"


def parse_rows(payload_rows, today) -> list:
    """Raw DOM rows -> normalized events with stable canonical ids. Identical
    same-day events (e.g. two badge spends) get an occurrence index in page
    order, which is stable (newest-first) across daily scrapes."""
    out, seen = [], {}
    for r in payload_rows:
        label_lines = [ln.strip() for ln in (r.get("action") or "").splitlines()
                       if ln.strip()]
        if not label_lines or not r.get("date"):
            continue
        label = label_lines[0][:120]
        job_title = label_lines[1][:160] if len(label_lines) > 1 else None
        m = _DELTA.search(r.get("connects") or "")
        if not m:
            continue
        delta = int(m.group(1).replace(" ", ""))
        jm = _JOB_ID.search(r.get("href") or "")
        job_id = jm.group(1) if jm else None
        try:
            when = _event_date(r["date"], today)
        except ValueError:
            continue                            # unknown date format -> skip row
        key = (when, delta, label.upper(), job_id or "")
        occ = seen[key] = seen.get(key, 0) + 1
        cid = hashlib.md5(
            f"{when}|{delta}|{label.upper()}|{job_id or ''}|{occ}"
            .encode("utf-8")).hexdigest()[:16]
        out.append({"id": cid, "event_date": when,
                    "kind": _kind(label, delta, job_id), "label": label,
                    "job_id": job_id, "job_title": job_title,
                    "connects_delta": delta})
    return out


@contextmanager
def _tab_visible(sp, tab):
    """Make the scan tab the ACTIVE tab of its window for the duration, then put
    back whatever was showing.

    Unlike the job feeds, this page embeds NO data in window.__NUXT__ (its state
    holds `config` and nothing else) — it fetches the table client-side. Chrome
    does not run that fetch/render in a hidden tab, so the SSR-payload trick that
    fixed the scanner has nothing to read here and visibility is unavoidable.
    Note this does NOT `activate` Chrome: the app is never brought to the front,
    so it does not steal focus — it only changes which tab that window shows.
    """
    window_id, tab_index = tab
    prev = None
    try:
        prev = sp._osascript('tell application "Google Chrome" to get active'
                             f' tab index of window id {window_id}')
        sp._osascript('tell application "Google Chrome" to set active tab index'
                      f' of window id {window_id} to {tab_index}')
    except Exception:                     # visibility is best-effort, never fatal
        prev = None
    try:
        yield
    finally:
        if prev:
            try:
                sp._osascript('tell application "Google Chrome" to set active'
                              f' tab index of window id {window_id} to {prev}')
            except Exception:
                pass


def _poll_until_ready(tab, sleep, timeout=POLL_TIMEOUT_SECONDS) -> dict:
    """Wait for a DEFINITE state rather than a fixed duration.

    Rows always win. The empty state is only believed once EMPTY_CONFIRM_SECONDS
    have passed without any row ever appearing — because that message is what the
    page shows WHILE fetching, so an early read of it is a false negative.
    """
    waited, stable, last = 0, 0, {}
    prev_n = -1
    while waited < timeout:
        last = json.loads(feed_bridge.chrome_eval(_EXTRACT_JS, tab_ref=tab))
        n = len(last.get("rows") or [])
        if n:
            # let a progressively-painting table finish before trusting the count
            stable = stable + 1 if n == prev_n else 0
            prev_n = n
            if stable >= SETTLE_SAMPLES:
                return last
        elif last.get("empty_state") and waited >= EMPTY_CONFIRM_SECONDS:
            return last            # still empty long after it should have painted
        sleep(POLL_EVERY_SECONDS)
        waited += POLL_EVERY_SECONDS
    return last


def scrape(ctx, _sleep=None) -> dict:
    import time as _t
    sleep = _sleep or _t.sleep
    from modules.upwork import scan_pipeline as sp
    tab = sp.ensure_scan_tab()
    with _tab_visible(sp, tab):
        sp.navigate_tab(tab, URL + sp.MARKER)
        sleep(POLL_EVERY_SECONDS)
        payload = _poll_until_ready(tab, sleep)
    landed = payload.get("url") or ""
    if "/plans/connects/history" not in landed:
        raise RuntimeError(
            f"connects-history page not reached (landed {landed[:80]}) —"
            " logged out or challenge-walled; log into upwork.com in Chrome.")
    rows = parse_rows(payload.get("rows") or [],
                      datetime.now(config.TZ).date())
    if not rows:
        # An empty window is NOT automatically a fault: with applications paused
        # there may genuinely be nothing in the last 7 days, and the endpoint
        # also serves an empty table when it throttles repeated loads. Failing
        # here would spam criticals daily and (via bin/catch-up) invite a restart
        # loop. Report it as a DEGRADED source instead — visible in the brief and
        # the cockpit, but not a fake emergency. A layout break still surfaces:
        # it shows up as neither rows NOR the app's own empty state.
        if payload.get("empty_state"):
            ctx.anomaly("connects_empty_window", "warn",
                        {"note": "page rendered its empty state — genuinely no"
                                 " connects activity in the window, or the"
                                 " endpoint throttled a repeated load",
                         "url": landed[:120]})
            ctx.log("connects-history: page reported an EMPTY window —"
                    " recorded as degraded, not failed")
            return {"window_rows": 0, "new": 0, "kinds": {}, "empty": True,
                    "window_from": None, "window_to": None}
        raise RuntimeError("connects-history table never rendered and the page"
                           " showed no empty-state either — layout change or a"
                           f" hung fetch (raw rows: {len(payload.get('rows') or [])})")
    new = 0
    with db.connect(ctx.db_path) as conn:
        for r in rows:
            cur = conn.execute(
                "INSERT OR IGNORE INTO connects_history (id, event_date, kind,"
                " label, job_id, job_title, connects_delta)"
                " VALUES (?,?,?,?,?,?,?)",
                (r["id"], r["event_date"], r["kind"], r["label"], r["job_id"],
                 r["job_title"], r["connects_delta"]))
            new += cur.rowcount
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    ctx.log(f"connects-history: {len(rows)} rows in window, {new} new; {kinds}")
    return {"window_rows": len(rows), "new": new, "kinds": kinds,
            "window_from": min(r["event_date"] for r in rows),
            "window_to": max(r["event_date"] for r in rows)}


def main() -> int:
    result = runner.run_task(TASK_NAME, scrape, module="upwork")
    print(json.dumps({"run_id": result.run_id, "status": result.status,
                      "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    raise SystemExit(main())
