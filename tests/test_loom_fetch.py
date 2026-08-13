"""Loom FETCH rail — fully offline.

No live browser and no network: a scripted eval/navigate pair stands in for
Chrome (exactly like the Slack/connects rails), and an injected http_get stands in
for the signed-CDN transcript GET. The tests pin the guarantees that matter:

- parse: the GraphQL body becomes a clean {id,name,createdAt} list; the share-page
  HTML yields the JSON-escaped transcript URL; phrases join to the SAME plain-text
  shape ingest.parse_loom already reads (round-tripped here so the fetcher can
  never drift from the parser);
- session loss FAILS LOUD: a logged-out GraphQL body raises + files a CRITICAL
  anomaly, and never returns a silent 0 (the scanner-hidden-tab lesson);
- incremental: already-known ids are skipped; a no-transcript loom is recorded as
  a permanently-excluded source (unique hash) and is NOT re-fetched next run;
  --full retries it;
- writes are gated: --dry-run stores nothing; an up-to-date corpus is a clean skip.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db
from modules.knowledge import ingest, loom_fetch


# ── fixtures ──────────────────────────────────────────────────────────────────
def _video(vid, name="A walkthrough", created="2026-08-08T10:00:00.000Z"):
    return {"id": vid, "name": name, "createdAt": created}


def _graphql_body(videos):
    return json.dumps({"data": {"me": {"my_videos": list(videos)}}})


def _share_html(vid, has_transcript=True):
    """Share-page HTML as Chrome would return it: the transcript URL is embedded
    JSON-escaped (\\/), which extract_transcript_url must unescape."""
    if not has_transcript:
        return "<html><head></head><body>no transcript here</body></html>"
    esc = (f"https:\\/\\/cdn.loom.com\\/mediametadata\\/transcription\\/"
           f"{vid}-1.json")
    return f'<html><body><script>{{"transcriptUrl":"{esc}"}}</script></body></html>'


_TRANSCRIPT = {"phrases": [{"ts": 0.0, "value": "Hey, here's a quick loom."},
                           {"ts": 2.5, "value": "This walks through the flow."}]}


class FakeLoomChrome:
    """Scripted stand-in for the parked Chrome tab on loom.com. Routes the fetcher's
    JS payloads (origin probe, index kick/poll, share kick/poll) to canned results.
    Never opens a socket or shells to osascript."""

    def __init__(self, index_videos, share_by_vid, *, session_ok=True,
                 origin_ok=True):
        self.index_videos = index_videos
        self.share_by_vid = share_by_vid           # vid -> (has_transcript: bool)
        self.session_ok = session_ok
        self.origin_ok = origin_ok
        self.navigations = []

    def navigate(self, url):
        self.navigations.append(url)

    def eval(self, js):
        if "location.origin" in js:
            return json.dumps({"origin": loom_fetch.LOOM_ORIGIN,
                               "ready": self.origin_ok})
        if "graphql" in js:                        # index kick
            return json.dumps({"started": True})
        if "['index']" in js:                      # index poll
            if not self.session_ok:
                return json.dumps({"done": True, "ok": True, "status": 200,
                                   "url": loom_fetch.GRAPHQL_URL,
                                   "body": json.dumps({"data": {"me": None}})})
            return json.dumps({"done": True, "ok": True, "status": 200,
                               "url": loom_fetch.GRAPHQL_URL,
                               "body": _graphql_body(self.index_videos)})
        if "/share/" in js:                        # share kick
            return json.dumps({"started": True})
        if "share:" in js:                         # share poll
            import re
            vid = re.search(r"share:([A-Za-z0-9]+)", js).group(1)
            return json.dumps({"done": True, "ok": True, "status": 200,
                               "url": f"{loom_fetch.LOOM_ORIGIN}/share/{vid}",
                               "body": _share_html(vid, self.share_by_vid.get(vid,
                                                                              True))})
        raise AssertionError(f"unexpected JS: {js[:80]}")


def _http_get(url, timeout):
    """Stand-in for the signed-CDN transcript GET. The transcript text embeds the
    video id so distinct videos yield distinct content (real transcripts differ;
    identical text would legitimately dedup on sources.content_hash)."""
    prefix = "https://cdn.loom.com/mediametadata/transcription/"
    if url.startswith(prefix):
        vid = url[len(prefix):].split("-", 1)[0]
        return json.dumps({"phrases": [
            {"ts": 0.0, "value": f"Hey, here's a quick loom for {vid}."},
            {"ts": 2.5, "value": "This walks through the flow."}]})
    raise RuntimeError(f"unexpected http_get {url}")


# ── pure parsing ──────────────────────────────────────────────────────────────
class ParseTests(unittest.TestCase):
    def test_parse_index_returns_clean_videos_and_drops_empty_ids(self):
        payload = {"status": 200, "url": loom_fetch.GRAPHQL_URL,
                   "body": _graphql_body([_video("aaa"), {"id": "", "name": "x"},
                                          _video("bbb")])}
        vids = loom_fetch.parse_index(payload)
        self.assertEqual([v["id"] for v in vids], ["aaa", "bbb"])

    def test_parse_index_raises_on_null_me(self):
        payload = {"status": 200, "url": loom_fetch.GRAPHQL_URL,
                   "body": json.dumps({"data": {"me": None}})}
        with self.assertRaises(loom_fetch.LoomLoggedOut):
            loom_fetch.parse_index(payload)

    def test_parse_index_raises_on_errors_array(self):
        payload = {"status": 200, "url": loom_fetch.GRAPHQL_URL,
                   "body": json.dumps({"errors": [{"message": "unauthorized"}]})}
        with self.assertRaises(loom_fetch.LoomLoggedOut):
            loom_fetch.parse_index(payload)

    def test_parse_index_raises_on_auth_status_and_login_redirect(self):
        with self.assertRaises(loom_fetch.LoomLoggedOut):
            loom_fetch.parse_index({"status": 401, "url": loom_fetch.GRAPHQL_URL,
                                    "body": "{}"})
        with self.assertRaises(loom_fetch.LoomLoggedOut):
            loom_fetch.parse_index({"status": 200,
                                    "url": "https://www.loom.com/login",
                                    "body": "<html>sign in</html>"})

    def test_parse_index_raises_on_unparseable_body(self):
        with self.assertRaises(loom_fetch.LoomLoggedOut):
            loom_fetch.parse_index({"status": 200, "url": loom_fetch.GRAPHQL_URL,
                                    "body": "<html>Just a Moment...</html>"})

    def test_extract_transcript_url_unescapes_and_matches(self):
        url = loom_fetch.extract_transcript_url(_share_html("vid123"))
        self.assertEqual(
            url,
            "https://cdn.loom.com/mediametadata/transcription/vid123-1.json")

    def test_extract_transcript_url_none_when_absent(self):
        self.assertIsNone(
            loom_fetch.extract_transcript_url(_share_html("v", False)))

    def test_transcript_from_phrases_joins_plain_text_and_ignores_junk(self):
        text, wc = loom_fetch.transcript_from_phrases(
            [{"ts": 0, "value": "one two"}, {"ts": 1, "value": "  "},
             "not a dict", {"ts": 2, "value": "three"}])
        self.assertEqual(text, "one two three")
        self.assertEqual(wc, 3)

    def test_inbox_payload_round_trips_through_parse_loom(self):
        """The whole compatibility contract: what the fetcher writes must be what
        ingest.parse_loom reads, unchanged."""
        payload = loom_fetch.build_inbox_payload(_video("zzz", "Demo"),
                                                 _TRANSCRIPT)
        self.assertEqual(set(payload),
                         {"id", "name", "date", "transcript", "wordCount",
                          "phraseCount", "phrases"})
        self.assertEqual(payload["phraseCount"], 2)
        parsed = ingest.parse_loom(payload)
        self.assertEqual(parsed["source_ref"], "loom/zzz")
        self.assertEqual(parsed["title"], "Demo")
        self.assertEqual(parsed["url"], "https://www.loom.com/share/zzz")
        self.assertIn("quick loom", parsed["text"])

    def test_new_video_ids_newest_first_and_excludes_known(self):
        idx = [_video("old", created="2026-01-01T00:00:00Z"),
               _video("new", created="2026-08-08T00:00:00Z"),
               _video("mid", created="2026-05-01T00:00:00Z")]
        got = loom_fetch.new_video_ids(idx, known={"loom/mid"})
        self.assertEqual([v["id"] for v in got], ["new", "old"])


# ── async fetch layer (scripted eval) ─────────────────────────────────────────
class AsyncFetchTests(unittest.TestCase):
    def test_async_fetch_polls_until_done(self):
        # eval returns 'pending' twice, then 'done' — the poller must wait
        seq = [json.dumps({"started": True}),
               json.dumps({"done": False}), json.dumps({"done": False}),
               json.dumps({"done": True, "ok": True, "body": "hi"})]
        calls = {"i": 0}

        def eval_fn(js):
            v = seq[calls["i"]]
            calls["i"] = min(calls["i"] + 1, len(seq) - 1)
            return v

        slot = loom_fetch._async_fetch(eval_fn, lambda s: None, timeout=10,
                                       kick_js="kick", poll_js="poll")
        self.assertTrue(slot["done"] and slot["ok"])

    def test_async_fetch_marks_timeout(self):
        def eval_fn(js):
            return json.dumps({"done": False})
        slot = loom_fetch._async_fetch(eval_fn, lambda s: None, timeout=0,
                                       kick_js="k", poll_js="p")
        self.assertTrue(slot["timeout"])


# ── run_fetch integration (temp DB + scripted Chrome + injected http_get) ─────
class RunFetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loom-fetch-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.inbox = self.tmp / "knowledge-inbox" / "loom"
        self._patch = mock.patch.object(config, "KNOWLEDGE_INBOX",
                                        self.tmp / "knowledge-inbox")
        self._patch.start()
        self.ctx = mock.Mock(db_path=self.db_path, task_name="loom_fetch",
                             run_id=1)

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, chrome, **kw):
        return loom_fetch.run_fetch(
            self.ctx, eval_fn=chrome.eval, navigate=chrome.navigate,
            http_get=_http_get, sleep=lambda s: None, **kw)

    def _loom_rows(self):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(
                "SELECT source_ref, status, content_type FROM sources"
                " WHERE source_type='loom' ORDER BY source_ref")]

    def test_fetches_and_ingests_a_new_loom_with_transcript(self):
        chrome = FakeLoomChrome([_video("aaa")], {"aaa": True})
        stats = self._run(chrome)
        self.assertEqual(stats["ingested"], 1)
        rows = self._loom_rows()
        self.assertEqual(rows, [{"source_ref": "loom/aaa", "status": "ingested",
                                 "content_type": rows[0]["content_type"]}])
        # inbox file written in the canonical <date>__<id>.json shape
        self.assertTrue((self.inbox / "2026-08-08__aaa.json").exists())

    def test_no_transcript_loom_is_recorded_excluded_and_not_refetched(self):
        chrome = FakeLoomChrome([_video("bbb")], {"bbb": False})
        stats = self._run(chrome)
        self.assertEqual(stats["no_transcript"], 1)
        self.assertEqual(stats["ingested"], 0)
        rows = self._loom_rows()
        self.assertEqual(rows[0]["source_ref"], "loom/bbb")
        self.assertEqual(rows[0]["status"], "skipped")
        self.assertEqual(rows[0]["content_type"], "loom:no-transcript")
        # second incremental run: it is now KNOWN (skipped ids are excluded), so 0
        # attempted -> a clean TaskSkip, never a re-fetch of the dead transcript.
        with self.assertRaises(loom_fetch.runner.TaskSkip):
            self._run(chrome)

    def test_second_no_transcript_does_not_collide_on_hash(self):
        chrome = FakeLoomChrome([_video("b1"), _video("b2")],
                                {"b1": False, "b2": False})
        stats = self._run(chrome)
        self.assertEqual(stats["no_transcript"], 2)
        refs = {r["source_ref"] for r in self._loom_rows()}
        self.assertEqual(refs, {"loom/b1", "loom/b2"})   # both persisted, no clash

    def test_incremental_skips_already_ingested(self):
        chrome = FakeLoomChrome([_video("aaa")], {"aaa": True})
        self._run(chrome)                              # ingest aaa
        # a second run sees aaa as known -> 0 new -> clean TaskSkip (not a re-fetch)
        with self.assertRaises(loom_fetch.runner.TaskSkip):
            self._run(chrome)

    def test_full_retries_a_no_transcript_id(self):
        # first: no transcript -> recorded skipped
        chrome1 = FakeLoomChrome([_video("ccc")], {"ccc": False})
        self._run(chrome1)
        self.assertEqual(self._loom_rows()[0]["status"], "skipped")
        # loom later generated a transcript; --full retries the skipped id
        chrome2 = FakeLoomChrome([_video("ccc")], {"ccc": True})
        stats = self._run(chrome2, full=True)
        self.assertEqual(stats["ingested"], 1)
        statuses = {r["source_ref"]: r["status"] for r in self._loom_rows()}
        self.assertEqual(statuses["loom/ccc"], "ingested")

    def test_dry_run_writes_nothing(self):
        chrome = FakeLoomChrome([_video("aaa")], {"aaa": True})
        stats = self._run(chrome, dry_run=True)
        self.assertEqual(stats["ingested"], 1)          # would-ingest count
        self.assertEqual(self._loom_rows(), [])         # but nothing persisted
        self.assertFalse((self.inbox / "2026-08-08__aaa.json").exists())

    def test_session_loss_fails_loud_and_files_critical_anomaly(self):
        chrome = FakeLoomChrome([], {}, session_ok=False)
        with self.assertRaises(loom_fetch.LoomLoggedOut):
            self._run(chrome)
        kinds = [c.args[0] for c in self.ctx.anomaly.call_args_list]
        self.assertIn("loom_fetch_logged_out", kinds)
        crit = next(c for c in self.ctx.anomaly.call_args_list
                    if c.args[0] == "loom_fetch_logged_out")
        self.assertEqual(crit.args[1], "critical")

    def test_up_to_date_corpus_is_a_clean_skip(self):
        chrome = FakeLoomChrome([_video("aaa")], {"aaa": True})
        self._run(chrome)                              # ingest aaa
        with self.assertRaises(loom_fetch.runner.TaskSkip):
            self._run(chrome)                          # 0 new -> TaskSkip

    def test_limit_bounds_transcripts_per_run(self):
        vids = [_video(f"v{i}", created=f"2026-08-0{i}T00:00:00Z")
                for i in range(1, 6)]
        chrome = FakeLoomChrome(vids, {v["id"]: True for v in vids})
        stats = self._run(chrome, limit=2)
        self.assertEqual(stats["attempted"], 2)
        self.assertEqual(stats["ingested"], 2)

    # ── the silent zero (2026-08-13) ─────────────────────────────────────────
    def test_live_session_with_an_EMPTY_index_degrades_instead_of_up_to_date(self):
        """A valid session returning zero videos is NOT 'corpus current'.

        parse_index only raises LoomLoggedOut on an AUTH signal, so a query that
        stops matching (renamed field, moved `my_videos`) returns a clean,
        believable []. Without this guard the run logs 'up to date', skips, and
        — now that skipped_empty counts as fresh — the source would go GREEN
        while the corpus quietly froze. That is exactly how the last four-month
        freeze went unnoticed. This account has ~590 recordings; 0 means broken."""
        chrome = FakeLoomChrome([], {})                 # session fine, index empty
        with self.assertRaises(loom_fetch.runner.TaskSkip):
            self._run(chrome)
        kinds = [c.args[0] for c in self.ctx.anomaly.call_args_list]
        self.assertIn("loom_fetch_empty_index", kinds)
        crit = next(c for c in self.ctx.anomaly.call_args_list
                    if c.args[0] == "loom_fetch_empty_index")
        self.assertEqual(crit.args[1], "critical")

    def test_empty_index_degrade_kind_is_registered_in_SOURCES(self):
        """An anomaly nobody declared is an anomaly nobody sees: the kind has to
        be in the source's degrade_kinds or source-health ignores it."""
        src = next(s for s in config.SOURCES if s["key"] == "loom_fetch")
        self.assertIn("loom_fetch_empty_index", src["degrade_kinds"])
        self.assertIn("loom_fetch_logged_out", src["degrade_kinds"])

    def test_a_normal_up_to_date_run_raises_NO_empty_index_anomaly(self):
        """The guard must not cry wolf: a non-empty index with nothing new is
        the genuine healthy steady state."""
        chrome = FakeLoomChrome([_video("aaa")], {"aaa": True})
        self._run(chrome)                               # ingest aaa
        self.ctx.anomaly.reset_mock()
        with self.assertRaises(loom_fetch.runner.TaskSkip):
            self._run(chrome)                           # 0 new, index still has 1
        kinds = [c.args[0] for c in self.ctx.anomaly.call_args_list]
        self.assertNotIn("loom_fetch_empty_index", kinds)


if __name__ == "__main__":
    unittest.main()
