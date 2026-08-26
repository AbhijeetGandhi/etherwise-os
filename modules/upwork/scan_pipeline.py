"""M1a hourly scan pipeline (live since the 2026-07-06 cutover).

TWO PROVIDERS, ONE TAIL (Phase 0.1, Upwork chat 2026-08-25; config.JOB_FEED_PROVIDER):

  chrome  navigate the dedicated tab -> feed_bridge extraction of the three
          personalized feed tabs in his logged-in Chrome (the M1 SOURCING
          INVARIANT of 2026-06-12)
  mcp     the bounded standing queries against mcp.upwork.com
          (feeds/mcp_search: smart_search + the term list, one `get` per new
          job; the invariant was LIFTED for MCP find_jobs on 2026-08-25)

  -> claim into V3 scored_jobs -> hard rules (code) -> score via gateway
  (structured outputs, anchored exemplars) -> drafts >= HOT_LEAD_THRESHOLD ->
  ClickUp cards (+ the retry of what an earlier run left uncarded) -> the
  hot-lead digest. The tail is shared, so the providers can differ only in
  where a job came from (feed_source), never in what is done with it.

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
from datetime import datetime, timedelta, timezone
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


def _fetch_chrome(ctx, dbp, _sleep) -> dict:
    """Provider 'chrome': the three feed tabs. Returns the per-feed metrics;
    raises loud when every feed redirected (logged out)."""
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
    return feeds


def _fetch_mcp(ctx, dbp) -> dict:
    """Provider 'mcp': one bounded standing-query round, then the SAME claim
    path as Chrome (feed_bridge.process — the Iron-Law validation, the
    fabrication guards, status 'New'). feed_source = 'mcp:<query>'. Every
    network call happens before the claim transaction opens (finding 12)."""
    from modules.upwork.feeds import mcp_client, mcp_search
    session = mcp_client.MCPSession()
    try:
        session.initialize()
        org = mcp_search.org_uid(session)
    except Exception as exc:                                     # noqa: BLE001
        # the MCP twin of "logged out": lead-gen is DOWN until this is fixed.
        # Fail LOUD (-> critical anomaly -> interrupt email) and say WHICH
        # kind of broken: the 26 Aug DNS outage was reported as a token to
        # repair, and the token was fine (mcp_client.start_failure_kind).
        kind = mcp_client.start_failure_kind(exc)
        raise RuntimeError(
            f"Upwork MCP session could not start ({kind}): {exc!r} —"
            f" {mcp_client.START_FAILURE_ADVICE[kind]}. Lead-gen is down"
            " until then; the scan retries on the next :15 run.") from exc
    with db.connect(dbp) as conn:
        known = {r["id"] for r in conn.execute(f"SELECT id FROM {TABLE}")}
    round_ = mcp_search.fetch_new(session, org, ctx.log, known=known,
                                  claim_since=config.MCP_CLAIM_SINCE)
    m = round_["metrics"]
    # DEGRADED-FEED GUARD, the MCP twin of MIN_HEALTHY_TILES: a search round
    # that surfaces almost nothing is a broken feed (auth, a changed tool
    # shape, a rate limit), never a quiet market — say so loudly, but score
    # whatever did arrive.
    if m["surfaced"] < config.MCP_MIN_HEALTHY_SURFACED:
        ctx.anomaly("scan_feed_degraded", "critical",
                    {"provider": "mcp", "surfaced": m["surfaced"],
                     "min_expected": config.MCP_MIN_HEALTHY_SURFACED,
                     "note": "the standing-query round surfaced far fewer jobs"
                             " than a healthy round — check the MCP session"})
        ctx.log(f"DEGRADED MCP ROUND: {m['surfaced']} surfaced"
                f" (< {config.MCP_MIN_HEALTHY_SURFACED}) — lead-gen is under-scanning")
    feeds = {}
    by_feed: dict = {}
    for job in round_["jobs"]:
        by_feed.setdefault(job.get("feed_source") or "mcp", []).append(job)
    for feed, jobs in by_feed.items():
        summary = feed_bridge.process(feed, jobs, db_path=dbp, table=TABLE,
                                      feeds=(feed,))
        feeds[feed] = {k: summary[k] for k in
                       ("extracted", "valid", "new", "known", "invalid")}
        feeds[feed]["source"] = "mcp"
    # every query the round ran is reported, claimed something or not — a
    # query that surfaced only known/cut rows still proves it ran
    for feed, fm in m["by_feed"].items():
        feeds.setdefault(feed, {"extracted": 0, "valid": 0, "new": 0,
                                "known": 0, "invalid": 0, "source": "mcp"})
        feeds[feed]["surfaced"] = fm["extracted"]
        feeds[feed]["already_known"] = fm["known"]
        feeds[feed]["skipped_before_cut"] = fm["skipped_before_cut"]
        feeds[feed]["get_capped"] = fm["get_capped"]
    ctx.log(f"mcp round: {json.dumps({k: v for k, v in m.items() if k != 'by_feed'})}")
    if m["skipped_before_cut"]:
        ctx.log(f"mcp round: {m['skipped_before_cut']} post(s) older than"
                f" MCP_CLAIM_SINCE={config.MCP_CLAIM_SINCE} not claimed")
    return feeds


def scan(ctx, _sleep=time.sleep) -> dict:
    dbp = ctx.db_path or config.DB_PATH
    provider = config.JOB_FEED_PROVIDER
    if provider == "mcp":
        feeds = _fetch_mcp(ctx, dbp)
    elif provider == "chrome":
        feeds = _fetch_chrome(ctx, dbp, _sleep)
    else:
        raise RuntimeError(f"config.JOB_FEED_PROVIDER={provider!r} is neither"
                           " 'chrome' nor 'mcp' — refusing to guess a feed")

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

    # intended ClickUp tasks: this run's verdicts PLUS what an earlier run left
    # uncarded (finding 13, Upwork chat 2026-08-25). A scan that dies between
    # scoring and this loop — 08-23 23:09: ClaudeBudgetExceeded at the per-run
    # cap after 20 drafts — leaves rows 'Drafted'/'Scored' with no card, and
    # this loop only ever saw the run's own verdicts, so nothing ever came back
    # for them. Same shape as the 'New' leftovers above: what a crashed run
    # left half-done, the next run finishes. Bounded to CLICKUP_PUSH_RETRY_DAYS
    # (config); older uncarded rows are the shadow-era backlog, listings long
    # expired, and stay put. first_scored_at is SQLite datetime('now') = UTC.
    intents = 0     # shadow-recorded intents
    pushed = 0      # LIVE cards actually created (0 in shadow) — separate metric
    retried = 0     # of which: leftovers from an earlier run (either mode)
    push_failed = 0  # rows whose card create failed this run (retried next run)
    icp_hits = []   # ideal-client-profile leads (the tier above 'hot')
    icp_near = []
    run_ids = {e["id"] for e in scored}
    retry_cut = (datetime.now(timezone.utc)
                 - timedelta(days=config.CLICKUP_PUSH_RETRY_DAYS)
                 ).strftime("%Y-%m-%d %H:%M:%S")
    # in shadow mode nothing is ever carded, so "uncarded" would re-select
    # every row each hour: there, an intent already in the ledger IS the card
    shadow_guard = (" AND NOT EXISTS (SELECT 1 FROM shadow_ledger l WHERE"
                    " l.target='clickup' AND l.entity='task'"
                    f" AND l.entity_key = {TABLE}.id)") if ctx.shadow else ""
    with db.connect(dbp) as conn:
        leftovers = [
            {"id": r["id"], "score": r["score"], "skip": None, "retry": True}
            for r in conn.execute(
                f"SELECT id, score FROM {TABLE} WHERE status IN"
                " ('Scored','Drafted') AND clickup_task_id IS NULL"
                f" AND score >= ? AND first_scored_at >= ?{shadow_guard}"
                " ORDER BY first_scored_at",
                (config.SCORE_FLOOR, retry_cut))
            if r["id"] not in run_ids]
    if leftovers:
        ctx.log(f"push retry: {len(leftovers)} uncarded row(s) scored within"
                f" {config.CLICKUP_PUSH_RETRY_DAYS}d by an earlier run")
    for entry in scored + leftovers:
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
            # one card's failure must not take the rest of the batch (or the
            # digest) down with it: push_one already did check-before-retry,
            # so an exception here is a real miss — log it loud, count it, and
            # let the retry window above bring the row back next run.
            try:
                clickup_push.push_one(clickup_push.token(), dbp, row, list_id)
            except Exception as exc:                  # noqa: BLE001
                push_failed += 1
                ctx.log(f"{row['id']}: ClickUp push FAILED: {exc!r}")
                ctx.anomaly("clickup_push_failed", "warn",
                            {"job_id": row["id"], "title": row.get("title"),
                             "list": clickup_push.LIST_NAMES.get(list_id),
                             "error": repr(exc)[:300],
                             "retry_days": config.CLICKUP_PUSH_RETRY_DAYS})
                continue
            pushed += 1
        if entry.get("retry"):
            retried += 1
            entry["carded"] = True

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
    # a retried hot row never made it into a digest (its run died before this
    # point), so it rides along in this one — carded now, linked now
    hot_retried = [e for e in leftovers if (e["score"] or 0)
                   >= config.HOT_LEAD_THRESHOLD and e.get("carded")]
    digest_status = None
    if hot or hot_retried:
        digest = {"subject": f"Upwork scan: {len(scored)} scored,"
                             f" {len(hot)} hot"
                             + (f" (+{len(hot_retried)} carried over)"
                                if hot_retried else ""),
                  "hot_ids": [e["id"] for e in hot + hot_retried]}
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
        raise runner.TaskSkip("no tiles extracted on any feed"
                              if provider == "chrome" else
                              "the MCP round surfaced nothing new")

    return {"provider": provider,
            "feeds": feeds, "claimed": sum(f["new"] for f in feeds.values()),
            "scored": len(scored), "skipped": len(skipped),
            "drafts": drafted, "drafts_grounded": grounded,
            "drafts_ungrounded": ungrounded, "drafts_blocked": blocked,
            "clickup_intents": intents,
            "clickup_pushed": pushed,
            "clickup_retried": retried, "clickup_push_failed": push_failed,
            "hot": len(hot), "hot_retried": len(hot_retried),
            "digest": digest_status,
            "icp": len(icp_hits), "icp_near": len(icp_near),
            "icp_email": icp_status}


def main() -> int:
    result = runner.run_task(TASK_NAME, scan, module="upwork")
    print(json.dumps({"run_id": result.run_id, "status": result.status,
                      "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
