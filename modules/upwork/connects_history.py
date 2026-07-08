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
from datetime import datetime, timedelta

from core import config, db, runner

sys.path.insert(0, str(config.V3_ROOT / "scripts"))
import feed_bridge  # noqa: E402

TASK_NAME = "upwork_connects"
URL = "https://www.upwork.com/nx/plans/connects/history/"

_EXTRACT_JS = """
JSON.stringify({
  url: location.href,
  rows: [...document.querySelectorAll('table tbody tr')].map(tr => {
    const tds = [...tr.querySelectorAll('td')];
    const a = tds[1] ? tds[1].querySelector('a[href*="/jobs/"]') : null;
    return {date: tds[0] ? tds[0].innerText.trim() : null,
            action: tds[1] ? tds[1].innerText.trim() : null,
            href: a ? a.getAttribute('href') : null,
            connects: tds[2] ? tds[2].innerText.trim() : null};
  })
})"""

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


def scrape(ctx, _sleep=None) -> dict:
    import time as _t
    sleep = _sleep or _t.sleep
    from modules.upwork import scan_pipeline as sp
    tab = sp.ensure_scan_tab()
    sp.navigate_tab(tab, URL + sp.MARKER)
    sleep(sp.SETTLE_SECONDS)
    payload = json.loads(feed_bridge.chrome_eval(_EXTRACT_JS, tab_ref=tab))
    landed = payload.get("url") or ""
    if "/plans/connects/history" not in landed:
        raise RuntimeError(
            f"connects-history page not reached (landed {landed[:80]}) —"
            " logged out or challenge-walled; log into upwork.com in Chrome.")
    rows = parse_rows(payload.get("rows") or [],
                      datetime.now(config.TZ).date())
    if not rows:
        raise RuntimeError("connects-history table rendered 0 parseable rows —"
                           " layout change? (raw rows:"
                           f" {len(payload.get('rows') or [])})")
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
