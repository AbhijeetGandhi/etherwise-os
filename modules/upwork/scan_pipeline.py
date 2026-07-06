"""M1a hourly scan pipeline — SHADOW-ONLY until cutover review.

Pure-Python orchestration of the same browser feeds (SOURCING INVARIANT,
2026-06-12, permanent: leads come from the three personalized feed tabs in
his logged-in Chrome — never an API substitute):

  navigate dedicated tab -> feed_bridge extraction -> claim into V3
  scored_jobs -> hard rules (code) -> score via gateway (structured outputs,
  anchored exemplars) -> drafts >=16 -> INTENDED ClickUp tasks + digest into
  shadow_ledger (zero external writes; bin/shadow-diff compares against the
  v4.14.1 Cowork task's actuals daily).

Dedicated-tab contract: the pipeline NEVER touches the user's active tab. It
finds (or once creates, restoring focus immediately) a tab whose URL carries
the #ew-scan fragment, then drives THAT tab by (window id, tab index) —
`set URL of tab i` does not steal focus. The tab stays parked on my_feed
between runs.

Writes: v3 scored_jobs + shadow_ledger + runs only. Reads nothing from v2.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from typing import Optional

from core import config, db, notify, runner
from modules.upwork import scoring

sys.path.insert(0, str(config.V3_ROOT / "scripts"))
import clickup_push  # noqa: E402  (routing/list names for intents)
import feed_bridge   # noqa: E402  (extraction library)

TASK_NAME = "upwork_scan"
TABLE = "scored_jobs"
MARKER = "#ew-scan"
FEED_URLS = {
    "most_recent": "https://www.upwork.com/nx/find-work/most-recent",
    "best_matches": "https://www.upwork.com/nx/find-work/best-matches",
    "my_feed": "https://www.upwork.com/nx/find-work/my-feed",
}
SETTLE_SECONDS = 9

_FIND_TAB = f'''
tell application "Google Chrome"
  repeat with w in windows
    set i to 1
    repeat with t in tabs of w
      if URL of t contains "{MARKER}" then
        return (id of w as string) & ":" & (i as string)
      end if
      set i to i + 1
    end repeat
  end repeat
  return ""
end tell'''

_CREATE_TAB = f'''
tell application "Google Chrome"
  set prior to active tab index of front window
  make new tab at end of tabs of front window with properties {{URL:"{FEED_URLS['my_feed']}{MARKER}"}}
  set active tab index of front window to prior
  return (id of front window as string) & ":" & ((count of tabs of front window) as string)
end tell'''


def _osascript(script: str) -> str:
    proc = subprocess.run(["osascript", "-e", script],
                          capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise feed_bridge.BridgeError(
            f"osascript failed: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def ensure_scan_tab():
    """(window_id, tab_index) of the dedicated tab; creates it once."""
    found = _osascript(_FIND_TAB)
    if not found:
        found = _osascript(_CREATE_TAB)
    window_id, tab_index = found.split(":")
    return int(window_id), int(tab_index)


def navigate_tab(tab_ref, url: str) -> None:
    window_id, tab_index = tab_ref
    _osascript(f'tell application "Google Chrome" to set URL of tab'
               f' {tab_index} of window id {window_id} to "{url}"')


def _update_scored(conn, jid: str, verdict: dict, draft: Optional[str],
                   skip_reason: Optional[str]) -> None:
    if skip_reason:
        cur = conn.execute(
            f"UPDATE {TABLE} SET status='Skipped', hard_rule_skip=?,"
            " updated_at=datetime('now') WHERE id=? AND status='New'",
            (skip_reason, jid))
    else:
        cur = conn.execute(
            f"""UPDATE {TABLE} SET score=?, score_breakdown_json=?,
                loom_flag=?, draft_proposal=?, draft_word_count=?, status=?,
                first_scored_at=datetime('now'), updated_at=datetime('now')
                WHERE id=? AND status='New'""",
            (verdict["score"], json.dumps(verdict["breakdown"]),
             verdict["loom_flag"], draft,
             len(draft.split()) if draft else None,
             "Drafted" if draft else "Scored", jid))
    assert cur.rowcount == 1, f"{jid}: expected exactly one New row"


def _digest_html(dbp, hot_ids) -> str:
    """Hot-lead email body: one line per hot job — title (linked), score,
    client, and the ClickUp card (set by the push just above, so the email
    links straight to the card with the draft on it)."""
    items = []
    with db.connect(dbp) as conn:
        for jid in hot_ids:
            r = conn.execute(f"SELECT * FROM {TABLE} WHERE id=?",
                             (jid,)).fetchone()
            if not r:
                continue
            r = dict(r)
            link = r.get("job_url") or f"https://www.upwork.com/jobs/~02{jid}"
            client = " · ".join(p for p in (r.get("client_company"),
                                            r.get("client_country")) if p)
            card = (f' · <a href="{r["clickup_task_url"]}">ClickUp card</a>'
                    if r.get("clickup_task_url") else "")
            items.append(f'<li><a href="{link}">{r.get("title") or jid}</a>'
                         f' — score {r.get("score")}'
                         f'{" · " + client if client else ""}{card}</li>')
    return ("<h2>Hot Upwork leads</h2><ul>" + "\n".join(items) + "</ul>"
            "<p>Scored ≥16 this scan; drafts are on the ClickUp cards.</p>")


def scan(ctx, _sleep=time.sleep) -> dict:
    dbp = ctx.db_path or config.DB_PATH
    tab = ensure_scan_tab()

    feeds = {}
    redirected = []
    for feed, url in FEED_URLS.items():
        navigate_tab(tab, url + MARKER)
        _sleep(SETTLE_SECONDS)
        raw = feed_bridge.chrome_eval(feed_bridge.EXTRACT_JS, tab_ref=tab)
        payload = json.loads(raw)
        # SESSION GUARD (2026-07-06 incident): a logged-out/challenged session
        # bounces feed URLs to the PUBLIC search page (or a CF interstitial).
        # Never ingest tiles from a non-feed page — they are unpersonalized
        # public results, not our lead feeds. Detect by landing URL.
        landed = payload.get("url") or ""
        if "/nx/find-work/" not in landed:
            feeds[feed] = {"extracted": 0, "valid": 0, "new": 0, "known": 0,
                           "invalid": 0, "redirected_to": landed[:80]}
            redirected.append(feed)
            ctx.log(f"{feed}: REDIRECTED off-feed -> {landed[:80]} (tiles ignored)")
            continue
        jobs = [j for j in (feed_bridge.parse_tile(t)
                            for t in payload.get("tiles", [])) if j]
        summary = feed_bridge.process(feed, jobs, db_path=dbp, table=TABLE)
        feeds[feed] = {k: summary[k] for k in
                       ("extracted", "valid", "new", "known", "invalid")}
        ctx.log(f"{feed}: {json.dumps(feeds[feed])}")
    if redirected and len(redirected) == len(FEED_URLS):
        # all feeds off-feed = the session is logged out (or challenge-walled):
        # lead-gen is DOWN until a human logs in. Fail LOUD (-> critical anomaly
        # -> interrupt email), never silently skip.
        raise RuntimeError(
            "Upwork session LOGGED OUT / challenge-walled: all feed URLs"
            f" redirected (e.g. {feeds[redirected[0]]['redirected_to']})."
            " Log into upwork.com in Chrome on the Mac — the scan self-heals"
            " on the next :15 run.")

    # score everything 'New' — includes leftovers from a crashed prior run
    with db.connect(dbp) as conn:
        new_rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM {TABLE} WHERE status='New' ORDER BY fetched_at")]

    scored, skipped, drafted = [], [], 0
    for row in new_rows:
        skip = scoring.hard_rule_skip(row)
        verdict, draft = None, None
        if not skip:
            verdict = scoring.score_job(row, task_name=ctx.task_name,
                                        db_path=dbp)
            if verdict["score"] >= config.HOT_LEAD_THRESHOLD:
                draft = scoring.draft_proposal(row, task_name=ctx.task_name,
                                               db_path=dbp)
                drafted += 1
        with db.connect(dbp) as conn:
            _update_scored(conn, row["id"], verdict, draft, skip)
        (skipped if skip else scored).append(
            {"id": row["id"], "score": verdict["score"] if verdict else None,
             "skip": skip})

    # intended ClickUp tasks (run-scoped by construction: this run's verdicts)
    intents = 0     # shadow-recorded intents
    pushed = 0      # LIVE cards actually created (0 in shadow) — separate metric
    for entry in scored:
        if (entry["score"] or 0) < 8:
            continue
        with db.connect(dbp) as conn:
            row = dict(conn.execute(
                f"SELECT * FROM {TABLE} WHERE id=?",
                (entry["id"],)).fetchone())
        list_id = clickup_push.list_for_score(
            row["score"], invite=clickup_push.is_invite(row))
        if not list_id:
            continue
        if ctx.shadow:
            ctx.record_shadow_write(
                target="clickup", operation="create", entity="task",
                entity_key=row["id"],
                payload={"list": clickup_push.LIST_NAMES[list_id],
                         "name": clickup_push.task_name(row),
                         "score": row["score"],
                         "loom": bool(row["loom_flag"])})
            intents += 1
        else:
            clickup_push.push_one(clickup_push.token(), dbp, row, list_id)
            pushed += 1

    hot = [e for e in scored if (e["score"] or 0)
           >= config.HOT_LEAD_THRESHOLD]
    digest_status = None
    if hot:
        digest = {"subject": f"Upwork scan: {len(scored)} scored,"
                             f" {len(hot)} hot",
                  "hot_ids": [e["id"] for e in hot]}
        if ctx.shadow:
            ctx.record_shadow_write(target="email", operation="send",
                                    entity="digest",
                                    entity_key=datetime.now(
                                        config.TZ).strftime("%Y-%m-%dT%H"),
                                    payload=digest)
            digest_status = "shadow"
        else:
            # LIVE hot-lead notification (wired at the 2026-07-06 scanner
            # cutover — keeps Cowork-scanner alert parity). A send failure
            # NEVER fails the scan: the cards are already pushed; log +
            # surface in metrics instead.
            try:
                notify.send_gmail(digest["subject"],
                                  _digest_html(dbp, digest["hot_ids"]))
                digest_status = "sent"
            except Exception as exc:                  # noqa: BLE001
                ctx.log(f"hot-lead digest send FAILED: {exc!r}")
                digest_status = "send_failed"

    if not new_rows and all(f["extracted"] == 0 for f in feeds.values()):
        raise runner.TaskSkip("no tiles extracted on any feed")

    return {"feeds": feeds, "claimed": sum(f["new"] for f in feeds.values()),
            "scored": len(scored), "skipped": len(skipped),
            "drafts": drafted, "clickup_intents": intents,
            "clickup_pushed": pushed,
            "hot": len(hot), "digest": digest_status}


def main() -> int:
    result = runner.run_task(TASK_NAME, scan, module="upwork")
    print(json.dumps({"run_id": result.run_id, "status": result.status,
                      "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
