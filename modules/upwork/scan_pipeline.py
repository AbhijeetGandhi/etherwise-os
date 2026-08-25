"""M1a hourly scan pipeline — SHADOW-ONLY until cutover review.

Pure-Python orchestration of the same browser feeds (SOURCING INVARIANT,
2026-06-12, permanent: leads come from the three personalized feed tabs in
his logged-in Chrome — never an API substitute):

  navigate dedicated tab -> feed_bridge extraction -> claim into V3
  scored_jobs -> hard rules (code) -> score via gateway (structured outputs,
  anchored exemplars) -> drafts >= HOT_LEAD_THRESHOLD -> INTENDED ClickUp
  tasks + digest into
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
from modules.upwork import icp, scoring

sys.path.insert(0, str(config.V3_ROOT / "scripts"))
import clickup_push  # noqa: E402  (routing/list names for intents)
import feed_bridge   # noqa: E402  (extraction library)

TASK_NAME = "upwork_scan"
TABLE = "scored_jobs"
MARKER = "#ew-scan"
# a healthy feed ships ~10-30 jobs; anything less means the extraction path is
# broken, not that the market is quiet (2026-07-13 hydration incident)
MIN_HEALTHY_TILES = 5
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
                   skip_reason: Optional[str],
                   evidence_prov: Optional[dict] = None) -> None:
    if skip_reason:
        cur = conn.execute(
            f"UPDATE {TABLE} SET status='Skipped', hard_rule_skip=?,"
            " updated_at=datetime('now') WHERE id=? AND status='New'",
            (skip_reason, jid))
    else:
        cur = conn.execute(
            f"""UPDATE {TABLE} SET score=?, score_breakdown_json=?,
                loom_flag=?, draft_proposal=?, draft_word_count=?, status=?,
                draft_evidence_json=?,
                first_scored_at=datetime('now'), updated_at=datetime('now')
                WHERE id=? AND status='New'""",
            (verdict["score"], json.dumps(verdict["breakdown"]),
             verdict["loom_flag"], draft,
             len(draft.split()) if draft else None,
             "Drafted" if draft else "Scored",
             json.dumps(evidence_prov) if evidence_prov else None, jid))
    assert cur.rowcount == 1, f"{jid}: expected exactly one New row"


def _icp_html(hits) -> str:
    """The ICP alert: why it qualified, up front — you should be able to decide
    from the email alone."""
    items = []
    for h in hits:
        r, why = h["row"], h["why"]
        rate = (f"${r.get('hourly_min') or '?'}–${r.get('hourly_max')}/hr"
                if r.get("contract_type") == "Hourly"
                else f"${r.get('fixed_budget') or 0:,.0f} fixed")
        items.append(
            f'<li style="margin-bottom:14px"><a href="{r.get("job_url")}"><b>'
            f'{r.get("title")}</b></a> — score {r.get("score")}<br>'
            f'{rate} · client spent ${r.get("client_total_spent") or 0:,.0f}'
            f' · {r.get("client_hires") or 0} hires · {r.get("client_country") or "—"}'
            f'<br><span style="color:#0f9d58">✓ '
            + " · ".join(why) + "</span>"
            + (f'<br><a href="{r.get("clickup_task_url")}">ClickUp card</a>'
               if r.get("clickup_task_url") else "") + "</li>")
    return ("<h2>★ Ideal-client lead" + ("s" if len(hits) > 1 else "") + "</h2>"
            "<p>Matches the ICP: core stack (Airtable/Make), budget reaches "
            "$51/hr (or $5k fixed), proven spender, long-term, agency-friendly."
            "</p><ul>" + "\n".join(items) + "</ul>"
            "<p style='color:#6b7688;font-size:12px'>Drafts are on the ClickUp "
            "cards in <b>ICP / Prime</b>.</p>")


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
            f"<p>Scored ≥{config.HOT_LEAD_THRESHOLD} this scan; drafts are on"
            " the ClickUp cards.</p>")


def scan(ctx, _sleep=time.sleep) -> dict:
    dbp = ctx.db_path or config.DB_PATH
    tab = ensure_scan_tab()

    feeds = {}
    redirected = []
    degraded = []
    for feed, url in FEED_URLS.items():
        navigate_tab(tab, url + MARKER)
        _sleep(SETTLE_SECONDS)
        # PRIMARY: the SSR __NUXT__ payload — the FULL feed, immune to the lazy
        # hydration that Chrome never runs in our hidden scan tab (2026-07-13:
        # DOM scraping silently returned ~2 tiles/feed for days). DOM stays as
        # the fallback if the payload shape ever disappears.
        payload = json.loads(
            feed_bridge.chrome_eval(feed_bridge.PAYLOAD_JS, tab_ref=tab))
        landed = payload.get("url") or ""
        source = "payload"
        jobs = [feed_bridge.parse_job(j) for j in (payload.get("jobs") or [])]
        if not jobs:
            dom = json.loads(
                feed_bridge.chrome_eval(feed_bridge.EXTRACT_JS, tab_ref=tab))
            landed = dom.get("url") or landed
            jobs = [j for j in (feed_bridge.parse_tile(t)
                                for t in dom.get("tiles", [])) if j]
            source = "dom-fallback"
            ctx.log(f"{feed}: payload empty ({payload.get('err') or 'no jobs'})"
                    f" -> DOM fallback yielded {len(jobs)}")
        # SESSION GUARD (2026-07-06 incident): a logged-out/challenged session
        # bounces feed URLs to the PUBLIC search page (or a CF interstitial).
        # Never ingest tiles from a non-feed page — they are unpersonalized
        # public results, not our lead feeds. Detect by landing URL.
        if "/nx/find-work/" not in landed:
            feeds[feed] = {"extracted": 0, "valid": 0, "new": 0, "known": 0,
                           "invalid": 0, "redirected_to": landed[:80]}
            redirected.append(feed)
            ctx.log(f"{feed}: REDIRECTED off-feed -> {landed[:80]} (tiles ignored)")
            continue
        summary = feed_bridge.process(feed, jobs, db_path=dbp, table=TABLE)
        feeds[feed] = {k: summary[k] for k in
                       ("extracted", "valid", "new", "known", "invalid")}
        feeds[feed]["source"] = source
        # DEGRADED-FEED GUARD: a healthy feed returns ~10-30 jobs. A handful
        # means the extraction path is broken (hydration, layout, selector) —
        # never let that pass as a quiet 'nothing new' again.
        if summary["extracted"] < MIN_HEALTHY_TILES:
            degraded.append(f"{feed}={summary['extracted']}")
        ctx.log(f"{feed}: {json.dumps(feeds[feed])}")
    if degraded:
        # loud, but NOT fatal: whatever WAS extracted is still worth scoring.
        ctx.anomaly("scan_feed_degraded", "critical",
                    {"feeds": degraded, "min_expected": MIN_HEALTHY_TILES,
                     "note": "feed extraction returned far fewer jobs than a"
                             " healthy feed — check the payload/DOM path"})
        ctx.log(f"DEGRADED FEEDS: {', '.join(degraded)}"
                f" (< {MIN_HEALTHY_TILES} jobs) — lead-gen is under-scanning")
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
    grounded = 0        # drafts that carried supplied, cited evidence
    ungrounded = 0      # drafts written from the reference alone (honest)
    blocked = 0         # drafts that failed validation on EVERY attempt
    for row in new_rows:
        skip = scoring.hard_rule_skip(row)
        verdict, draft, prov = None, None, None
        if not skip:
            verdict = scoring.score_job(row, task_name=ctx.task_name,
                                        db_path=dbp)
            if verdict["score"] >= config.HOT_LEAD_THRESHOLD:
                draft, prov = scoring.draft_proposal_grounded(
                    row, task_name=ctx.task_name, db_path=dbp)
                if draft:
                    drafted += 1
                    grounded += 1 if prov.get("status") == "supplied" else 0
                    ungrounded += 1 if prov.get("status") != "supplied" else 0
                else:
                    # every claim must trace or nothing ships. The card still
                    # gets created; it just has no draft on it.
                    blocked += 1
                    ctx.anomaly("draft_validation_failed", "warn",
                                {"job_id": row["id"], "title": row.get("title"),
                                 "problems": prov.get("problems", [])})
                    ctx.log(f"{row['id']}: draft BLOCKED, untraceable claims"
                            f" — {json.dumps(prov.get('problems', []))[:300]}")
        with db.connect(dbp) as conn:
            _update_scored(conn, row["id"], verdict, draft, skip, prov)
        (skipped if skip else scored).append(
            {"id": row["id"], "score": verdict["score"] if verdict else None,
             "skip": skip})

    # intended ClickUp tasks (run-scoped by construction: this run's verdicts)
    intents = 0     # shadow-recorded intents
    pushed = 0      # LIVE cards actually created (0 in shadow) — separate metric
    icp_hits = []   # ideal-client-profile leads (the tier above 'hot')
    icp_near = []
    for entry in scored:
        if (entry["score"] or 0) < config.SCORE_FLOOR:   # below band: no card
            continue
        with db.connect(dbp) as conn:
            row = dict(conn.execute(
                f"SELECT * FROM {TABLE} WHERE id=?",
                (entry["id"],)).fetchone())
        # ICP / Prime: deterministic ideal-client overlay ON TOP of the score
        row["skills"] = json.loads(row.get("skills_json") or "[]")
        verdict_bd = json.loads(row.get("score_breakdown_json") or "{}")
        icp_v = icp.classify(row, score=row["score"], breakdown=verdict_bd)
        # persist the tier (014) — it is deterministic, so history stays
        # reproducible and lead quality becomes chartable instead of living
        # only inside one run's metrics
        with db.connect(dbp) as conn:
            conn.execute(f"UPDATE {TABLE} SET icp_tier=? WHERE id=?",
                         (icp_v["tier"], row["id"]))
        if icp_v["tier"] == "icp":
            icp_hits.append({"row": row, "why": icp_v["reasons"]})
        elif icp_v["tier"] == "icp_near":
            icp_near.append({"id": row["id"], "title": row["title"],
                             "missing": icp_v["misses"]})
        list_id = clickup_push.list_for_score(
            row["score"], invite=clickup_push.is_invite(row),
            icp_tier=icp_v["tier"])
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

    # ICP hits get their OWN immediate email — they must never be buried in the
    # hourly hot digest (81% of scored jobs are 'hot'; these are the few that
    # match the ideal-client profile).
    icp_status = None
    if icp_hits and not ctx.shadow:
        try:
            notify.send_gmail(
                f"★ ICP lead{'s' if len(icp_hits) > 1 else ''}:"
                f" {icp_hits[0]['row']['title'][:60]}"
                + (f" (+{len(icp_hits) - 1} more)" if len(icp_hits) > 1 else ""),
                _icp_html(icp_hits))
            icp_status = "sent"
        except Exception as exc:                  # noqa: BLE001
            ctx.log(f"ICP email FAILED: {exc!r}")
            icp_status = "send_failed"
    elif icp_hits:
        ctx.record_shadow_write(
            target="email", operation="send", entity="icp_alert",
            entity_key=datetime.now(config.TZ).strftime("%Y-%m-%dT%H"),
            payload={"ids": [h["row"]["id"] for h in icp_hits]})
        icp_status = "shadow"
    if icp_hits or icp_near:
        ctx.log(f"ICP: {len(icp_hits)} prime, {len(icp_near)} near-miss")

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
            "drafts": drafted, "drafts_grounded": grounded,
            "drafts_ungrounded": ungrounded, "drafts_blocked": blocked,
            "clickup_intents": intents,
            "clickup_pushed": pushed,
            "hot": len(hot), "digest": digest_status,
            "icp": len(icp_hits), "icp_near": len(icp_near),
            "icp_email": icp_status}


def main() -> int:
    result = runner.run_task(TASK_NAME, scan, module="upwork")
    print(json.dumps({"run_id": result.run_id, "status": result.status,
                      "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
