"""Loom FETCH rail — pull NEW recordings from loom.com into the KB.

`assets/loom-video-index.json` was a one-time manual export (mtime 2026-04-08), so
with no fetcher the Loom corpus froze four months ago while live GraphQL kept
growing (the newest recordings are the most valuable — live client-work
walkthroughs). This rail closes that gap incrementally.

WHY the osascript->Chrome substrate (not urllib for everything): the Loom GraphQL
endpoint and the /share page are gated by Abhijeet's LOGGED-IN loom.com session
(there is no API key). `credentials: include` only carries that session when the
fetch runs SAME-ORIGIN from a loom.com page, and headless CC cannot reach Chrome,
so the index + share-HTML fetches run INSIDE the scanner's parked Chrome tab
(reusing feed_bridge.chrome_eval + scan_pipeline, exactly like the Slack rail).
The transcript itself is a PUBLIC signed CloudFront URL (cdn.loom.com) — no
session, and a cross-origin browser fetch would hit CORS — so step 3 is a plain
Python urllib GET. Faithful to the proven three-step method, split by what each
step actually needs.

WHY fire-then-poll: chrome_eval returns the last expression SYNCHRONOUSLY, but
fetch() is async — a Promise cannot be returned through osascript. So each fetch
is kicked off writing its result onto a window global, then a reader JS is polled
until `done` (the same bounded-poll pattern slack_rail._extract uses). The tab is
made VISIBLE for the run because Chrome does not run fetch/render in a hidden tab
(the connects-rail lesson), then restored.

Incremental by default: diff the live index against sources.source_ref
(`loom/<id>`); fetch transcripts only for ids not already ingested/skipped. --full
retries the permanently-no-transcript ids too. Session loss FAILS LOUD ("log into
loom.com in Chrome") — it never silently returns 0 (the failure mode that cost a
day on the Upwork scanner).

Every guarantee below is proven offline against a scripted transport + injected
http_get, exactly like the other browser rails; nothing here needs Chrome to test.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core import config, db, runner                       # noqa: E402
from modules.knowledge import ingest, clients             # noqa: E402

SOURCE = "loom"
TASK_NAME = "loom_fetch"
LOOM_ORIGIN = "https://www.loom.com"
GRAPHQL_URL = f"{LOOM_ORIGIN}/graphql"
POLL_EVERY_SECONDS = 1.0

# The signed transcript URL, as it appears in the share-page HTML (JSON-escaped
# slashes and all). Anchored to the CDN host + path so a stray match elsewhere in
# the HTML cannot be mistaken for it.
_TRANSCRIPT_URL_RE = re.compile(
    r"https://cdn\.loom\.com/mediametadata/transcription/[A-Za-z0-9]+-\d+\.json")


class LoomLoggedOut(Exception):
    """The loom.com session is gone (GraphQL returned no `me`, an auth error, or a
    redirect to sign-in). Carries the exact remediation. FAIL LOUD — never a
    silent zero (the scanner-hidden-tab lesson)."""


# ── JS payloads (run inside Chrome, same-origin on loom.com) ──────────────────
# The kick starts an async fetch and stashes {done, ok, status, url, body|error}
# onto window.__ew_loom[<key>]; it returns immediately. The poll reads that slot.
# The Python layer kicks once then polls until `done` or a bounded timeout.

_ORIGIN_READY_JS = f"""
(function(){{
  return JSON.stringify({{
    origin: location.origin,
    ready: location.origin === {LOOM_ORIGIN!r}
           && document.readyState !== 'loading'
  }});
}})()
"""


def _index_kick_js(limit: int) -> str:
    query = "{ me { my_videos(limit: %d) { id name createdAt } } }" % int(limit)
    return """
(function(){
  window.__ew_loom = window.__ew_loom || {};
  window.__ew_loom['index'] = {done:false};
  fetch(%s, {
    method:'POST', credentials:'include',
    headers:{'content-type':'application/json'},
    body: JSON.stringify({query: %s})
  }).then(function(r){ return r.text().then(function(t){
      window.__ew_loom['index'] =
        {done:true, ok:r.ok, status:r.status, url:r.url, body:t};
  }); }).catch(function(e){
      window.__ew_loom['index'] =
        {done:true, ok:false, status:0, error:String(e)};
  });
  return JSON.stringify({started:true});
})()
""" % (json.dumps(GRAPHQL_URL), json.dumps(query))


_INDEX_POLL_JS = """
(function(){
  var s = (window.__ew_loom||{})['index'];
  return JSON.stringify(s || {done:false, missing:true});
})()
"""


def _share_kick_js(vid: str) -> str:
    url = f"{LOOM_ORIGIN}/share/{vid}"
    key = f"share:{vid}"
    return """
(function(){
  window.__ew_loom = window.__ew_loom || {};
  window.__ew_loom[%s] = {done:false};
  fetch(%s, {credentials:'include'})
    .then(function(r){ return r.text().then(function(t){
        window.__ew_loom[%s] =
          {done:true, ok:r.ok, status:r.status, url:r.url, body:t};
    }); }).catch(function(e){
        window.__ew_loom[%s] =
          {done:true, ok:false, status:0, error:String(e)};
    });
  return JSON.stringify({started:true});
})()
""" % (json.dumps(key), json.dumps(url), json.dumps(key), json.dumps(key))


def _share_poll_js(vid: str) -> str:
    key = f"share:{vid}"
    return """
(function(){
  var s = (window.__ew_loom||{})[%s];
  return JSON.stringify(s || {done:false, missing:true});
})()
""" % json.dumps(key)


# ── pure parsing (transport-agnostic, unit-tested) ────────────────────────────

def _looks_logged_out(status: int, url: str, parsed: Optional[dict]) -> bool:
    """A loom.com session loss shows up as an auth status, a redirect to sign-in,
    or a GraphQL body with no `me` / an errors array. Any one => logged out."""
    if status in (401, 403):
        return True
    if url and re.search(r"/(login|signin|sign-in|sso)", url):
        return True
    if isinstance(parsed, dict):
        if parsed.get("errors"):
            return True
        data = parsed.get("data")
        if isinstance(data, dict) and data.get("me") is None:
            return True
    return False


def parse_index(payload: dict) -> list:
    """GraphQL fetch result payload -> list of {id, name, createdAt} videos.

    Raises LoomLoggedOut on any session-loss signal. Never returns [] silently for
    a session problem — an empty list here means the account genuinely has no
    videos, which for this account it does not."""
    body = payload.get("body") or ""
    parsed = None
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        parsed = None
    if _looks_logged_out(payload.get("status") or 0, payload.get("url") or "",
                         parsed):
        raise LoomLoggedOut(
            "loom.com GraphQL returned no session — log into loom.com in Chrome"
            " (the fetch is read-only and cannot sign in for you).")
    if not isinstance(parsed, dict):
        raise LoomLoggedOut(
            f"loom.com GraphQL returned an unparseable body (status "
            f"{payload.get('status')}) — log into loom.com in Chrome and retry.")
    videos = (((parsed.get("data") or {}).get("me") or {}).get("my_videos")) or []
    out = []
    for v in videos:
        vid = str(v.get("id") or "").strip()
        if vid:
            out.append({"id": vid, "name": v.get("name"),
                        "createdAt": v.get("createdAt")})
    return out


def extract_transcript_url(html: str) -> Optional[str]:
    """Regex the signed CloudFront transcript URL out of the share-page HTML. The
    HTML carries it JSON-escaped (\\/), so unescape first."""
    if not html:
        return None
    m = _TRANSCRIPT_URL_RE.search(html.replace("\\/", "/"))
    return m.group(0) if m else None


def transcript_from_phrases(phrases) -> tuple:
    """Loom transcript JSON `phrases` [{ts, value}] -> (plain text, word count).

    The text is the phrase values joined with spaces — the SAME plain-text shape
    the existing 485 inbox files use, so ingest.parse_loom stays unchanged. The
    timestamped `phrases` are preserved verbatim on the inbox payload (an extra
    key parse_loom ignores) for a future timestamped-citation enhancement."""
    vals = []
    for p in (phrases or []):
        if isinstance(p, dict):
            v = p.get("value")
            if isinstance(v, str) and v.strip():
                vals.append(v.strip())
    text = " ".join(vals)
    return text, len(text.split())


def build_inbox_payload(video: dict, transcript_json: dict) -> dict:
    """Assemble the inbox JSON in the exact shape ingest.parse_loom reads
    ({id, name, date, transcript, wordCount, phraseCount}) + the raw `phrases`."""
    phrases = transcript_json.get("phrases") or []
    text, wc = transcript_from_phrases(phrases)
    return {
        "id": video["id"],
        "name": video.get("name"),
        "date": video.get("createdAt"),
        "transcript": text,
        "wordCount": wc,
        "phraseCount": len(phrases),
        "phrases": phrases,          # preserved; parse_loom ignores extra keys
    }


# ── transport-agnostic fetch layer (scripted in tests, live Chrome in prod) ───

def _async_fetch(eval_fn: Callable[[str], str], sleep: Callable[[float], None],
                 timeout: float, kick_js: str, poll_js: str) -> dict:
    """Kick an async in-page fetch, then poll its window-global slot until `done`
    or the bounded timeout. Returns the slot dict (with `timeout: True` if it
    never completed)."""
    eval_fn(kick_js)
    waited = 0.0
    while True:
        slot = json.loads(eval_fn(poll_js))
        if slot.get("done"):
            return slot
        if waited >= timeout:
            slot = dict(slot)
            slot["timeout"] = True
            return slot
        sleep(POLL_EVERY_SECONDS)
        waited += POLL_EVERY_SECONDS


def fetch_index(eval_fn, sleep, timeout: float, limit: int) -> list:
    slot = _async_fetch(eval_fn, sleep, timeout,
                        _index_kick_js(limit), _INDEX_POLL_JS)
    if slot.get("timeout"):
        raise RuntimeError(
            "loom index fetch did not complete within the poll window"
            " (Chrome slow/offline)")
    return parse_index(slot)


def fetch_share_html(eval_fn, sleep, timeout: float, vid: str) -> Optional[str]:
    slot = _async_fetch(eval_fn, sleep, timeout,
                        _share_kick_js(vid), _share_poll_js(vid))
    if slot.get("timeout") or not slot.get("ok"):
        return None
    return slot.get("body")


def _urllib_get(url: str, timeout: float) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "etherwise-loom-fetch"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (signed CDN URL)
        return r.read().decode("utf-8", "replace")


def fetch_transcript_json(url: str, http_get: Callable[[str, float], str],
                          timeout: float) -> Optional[dict]:
    try:
        return json.loads(http_get(url, timeout))
    except Exception:
        return None


# ── DB: incremental diff + no-transcript exclusion ────────────────────────────

def _known_refs(conn, statuses: tuple) -> set:
    qs = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"SELECT source_ref FROM sources WHERE source_type='loom'"
        f" AND status IN ({qs})", statuses).fetchall()
    return {r["source_ref"] for r in rows}


def new_video_ids(index: list, known: set) -> list:
    """Ids in the live index whose `loom/<id>` ref is not already known, newest
    first (the newest recordings are the ones worth fetching first)."""
    ordered = sorted(index, key=lambda v: v.get("createdAt") or "", reverse=True)
    return [v for v in ordered if f"loom/{v['id']}" not in known]


def _record_no_transcript(conn, video: dict) -> None:
    """Mark a loom that has no transcript (and never will) as a permanently
    excluded source so it stops re-appearing as 'new'. A synthetic unique
    content_hash avoids the empty-text UNIQUE collision that would otherwise let
    only the first no-transcript loom persist."""
    vid = video["id"]
    ref = f"loom/{vid}"
    if conn.execute("SELECT 1 FROM sources WHERE source_ref=? AND source_type='loom'",
                    (ref,)).fetchone():
        return
    chash = "loom-no-transcript:" + hashlib.sha256(ref.encode()).hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO sources (source_type, source_ref, content_hash,"
        " content_type, title, occurred_dt, status) VALUES"
        " ('loom', ?, ?, 'loom:no-transcript', ?, ?, 'skipped')",
        (ref, chash, video.get("name"), (video.get("createdAt") or "")[:10]))


# ── the task: fetch -> ingest (internal only; NEVER an external write) ────────

def _live_browser():
    """(eval_fn, navigate, teardown) driving the scanner's parked Chrome tab, made
    VISIBLE for the run (Chrome does not run fetch in a hidden tab — the connects
    lesson) and restored on teardown. Mirrors slack_rail._live_browser."""
    from modules.upwork import scan_pipeline as sp
    from modules.upwork import connects_history as ch
    sys.path.insert(0, str(config.V3_ROOT / "scripts"))
    import feed_bridge                                            # noqa

    tab = sp.ensure_scan_tab()
    vis = ch._tab_visible(sp, tab)
    vis.__enter__()

    def navigate(url: str) -> None:
        sp.navigate_tab(tab, url)

    def eval_fn(js: str) -> str:
        return feed_bridge.chrome_eval(js, tab_ref=tab)

    def teardown() -> None:
        try:
            vis.__exit__(None, None, None)
        except Exception:
            pass

    return eval_fn, navigate, teardown


def _wait_origin(eval_fn, sleep, timeout: float) -> bool:
    """Poll until the tab is actually on the loom.com origin (navigation is async;
    a fetch fired before the origin settles would be cross-origin)."""
    waited = 0.0
    while True:
        try:
            st = json.loads(eval_fn(_ORIGIN_READY_JS))
        except Exception:
            st = {}
        if st.get("ready"):
            return True
        if waited >= timeout:
            return False
        sleep(POLL_EVERY_SECONDS)
        waited += POLL_EVERY_SECONDS


def run_fetch(ctx, *, eval_fn=None, navigate=None, http_get=None, sleep=None,
              full: bool = False, limit: Optional[int] = None,
              dry_run: bool = False, index_limit: Optional[int] = None,
              poll_timeout: Optional[float] = None,
              http_timeout: Optional[float] = None) -> dict:
    """Fetch the live index, diff against sources, fetch transcripts for the new
    ids, write inbox files + ingest. Read-external + local-write only."""
    sleep = sleep or time.sleep
    http_get = http_get or _urllib_get
    limit = config.LOOM_FETCH_MAX_PER_RUN if limit is None else limit
    index_limit = config.LOOM_INDEX_LIMIT if index_limit is None else index_limit
    poll_timeout = config.LOOM_FETCH_POLL_TIMEOUT_SECONDS \
        if poll_timeout is None else poll_timeout
    http_timeout = config.LOOM_TRANSCRIPT_HTTP_TIMEOUT_SECONDS \
        if http_timeout is None else http_timeout
    dbp = ctx.db_path or config.DB_PATH
    inbox = config.KNOWLEDGE_INBOX / "loom"
    inbox.mkdir(parents=True, exist_ok=True)

    teardown = None
    if eval_fn is None or navigate is None:
        eval_fn, navigate, teardown = _live_browser()

    try:
        try:
            navigate(LOOM_ORIGIN + "/")
            if not _wait_origin(eval_fn, sleep, poll_timeout):
                raise LoomLoggedOut(
                    "could not reach the loom.com origin in Chrome — open a"
                    " loom.com tab and log in, then retry.")
            index = fetch_index(eval_fn, sleep, poll_timeout, index_limit)
        except LoomLoggedOut as lo:
            # FAIL LOUD — never a silent zero. The critical anomaly reaches the
            # brief/interrupt queue with the exact remediation.
            ctx.anomaly("loom_fetch_logged_out", "critical",
                        {"instruction": "log into loom.com in Chrome",
                         "detail": str(lo)})
            ctx.log(f"loom fetch: LOGGED OUT — {lo}")
            raise

        # A VALID session that returns an EMPTY index is the silent zero this
        # registry exists to catch. parse_index only raises LoomLoggedOut on an
        # auth signal, so a query that stops matching (a renamed field, a moved
        # `my_videos`) comes back as a clean, believable `[]` — and the run would
        # then log 'up to date' and go green while the corpus quietly froze. That
        # is precisely how the last four-month freeze went unnoticed. This account
        # has ~590 recordings; zero means the fetch broke, not that Loom is empty.
        if not index:
            ctx.anomaly("loom_fetch_empty_index", "critical",
                        {"detail": "loom.com returned a live session but ZERO"
                                   " videos — the index query has almost"
                                   " certainly stopped matching. Do NOT read"
                                   " this as 'corpus current'.",
                         "index_limit": index_limit})
            ctx.log("loom fetch: session OK but index came back EMPTY —"
                    " degrading (this is not 'up to date')")

        # incremental skips already-ingested AND no-transcript; --full retries the
        # no-transcript ids (Loom may have generated a transcript since).
        with db.connect(dbp) as conn:
            if full:
                known = _known_refs(conn, ("ingested", "extracted"))
            else:
                known = _known_refs(conn, ("ingested", "extracted", "skipped"))
        candidates = new_video_ids(index, known)
        capped = candidates[:limit]

        cmap = clients.load_client_map(dbp)
        stats = {"index_count": len(index), "known": len(known),
                 "new_candidates": len(candidates), "attempted": len(capped),
                 "ingested": 0, "duplicate": 0, "no_transcript": 0,
                 "share_failed": 0, "transcript_failed": 0}

        for v in capped:
            vid = v["id"]
            html = fetch_share_html(eval_fn, sleep, poll_timeout, vid)
            if html is None:
                stats["share_failed"] += 1     # transient — left for the next run
                ctx.log(f"loom {vid}: share page fetch failed (skip, retry next run)")
                continue
            turl = extract_transcript_url(html)
            if not turl:
                stats["no_transcript"] += 1
                if not dry_run:
                    with db.connect(dbp) as conn:
                        _record_no_transcript(conn, v)
                continue
            tjson = fetch_transcript_json(turl, http_get, http_timeout)
            if not tjson or not (tjson.get("phrases")):
                stats["transcript_failed"] += 1
                ctx.log(f"loom {vid}: transcript fetch/parse failed (retry next run)")
                continue

            payload = build_inbox_payload(v, tjson)
            occ = (payload.get("date") or "unknown")[:10]
            path = inbox / f"{occ}__{vid}.json"
            if dry_run:
                stats["ingested"] += 1        # would-ingest count, no writes
                continue
            path.write_text(json.dumps(payload, default=str))
            r = ingest.ingest_file(path, db_path=dbp, client_map=cmap,
                                   source_type="loom")
            stats[r["status"]] = stats.get(r["status"], 0) + 1

        with db.connect(dbp) as conn:
            sources_after = conn.execute(
                "SELECT count(*) c FROM sources WHERE source_type='loom'"
                " AND status IN ('ingested','extracted')").fetchone()["c"]
        stats["sources_after"] = sources_after
    finally:
        if teardown:
            teardown()

    # An empty new-candidate set is a legitimate no-op (corpus already current),
    # NOT a failure — record a clean skip so freshness still advances.
    if stats["attempted"] == 0:
        ctx.log(f"loom fetch: index {stats['index_count']}, "
                f"{stats['known']} known, 0 new — up to date.")
        raise runner.TaskSkip("loom corpus already current (0 new)")

    ctx.log(
        f"loom fetch: index {stats['index_count']} | new {stats['new_candidates']}"
        f" (attempted {stats['attempted']}) -> ingested {stats['ingested']}"
        f", duplicate {stats['duplicate']}, no-transcript {stats['no_transcript']}"
        f", share-failed {stats['share_failed']}, transcript-failed "
        f"{stats['transcript_failed']} | sources_after {stats['sources_after']}"
        + (" | DRY-RUN (no writes)" if dry_run else ""))
    return stats


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Loom fetch rail (read-only browser)")
    ap.add_argument("--full", action="store_true",
                    help="retry permanently-no-transcript ids too (rebuild scan)")
    ap.add_argument("--limit", type=int, default=None,
                    help="max transcripts fetched this run (default config)")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch + report; write NOTHING (no inbox, no sources)")
    args = ap.parse_args(argv)

    result = runner.run_task(
        TASK_NAME,
        lambda ctx: run_fetch(ctx, full=args.full, limit=args.limit,
                              dry_run=args.dry_run),
        module="knowledge")
    print(json.dumps({"run_id": result.run_id, "status": result.status,
                      "metrics": result.metrics}))
    if result.status in ("completed", "skipped_empty"):
        print("\nNEXT (gated, chain): attribute + extract the new looms:\n"
              "  PYTHONPATH=. python3 bin/jarvis-loom-classify         # KIND+vocab\n"
              "  PYTHONPATH=. python3 bin/kb-backfill extract loom --attributed"
              " --model classify --limit 50")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
