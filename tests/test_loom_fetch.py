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
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from core import config, db
from modules.knowledge import ingest, loom_fetch


# ── fixtures ──────────────────────────────────────────────────────────────────
def _video(vid, name="A walkthrough", created="2026-08-08T10:00:00.000Z"):
    return {"id": vid, "name": name, "createdAt": created}


def _graphql_body(videos):
    return json.dumps({"data": {"me": {"my_videos": list(videos)}}})


# A CloudFront signature as loom.com actually emits it (run 5391 evidence: the
# real query is ~600 chars of Policy/Key-Pair-Id/Signature and the URL is WORTHLESS
# without it — CloudFront answers 403 MissingKey).
_SIG = ("Policy=eyJTdGF0ZW1lbnQiOlt7IlJlc291cmNlIjoiaHR0cHM6Ly9jZG4ubG9vbS5jb20i"
        "fV19&Key-Pair-Id=APKAJQIC5BGSW7XXK7FQ&Signature=eKoMM6tMA9b0UArgk-W1"
        "%7ErmvUruwTqShdjWTNsYag935yWEhny__")


def _share_html(vid, has_transcript=True, *, status=None, has_record=None,
                signed=True):
    """Share-page HTML as Chrome would return it: the transcript URL is embedded
    JSON-escaped (\\/), which extract_transcript_url must unescape, and carries
    its CloudFront signature.

    `status`/`has_record` model the Apollo-cache keys the page really ships
    (`transcription_status`, `hasTranscriptRecord`) so the three distinct causes
    of a missing URL can be told apart in tests."""
    bits = []
    if has_record is not None:
        bits.append('"hasTranscriptRecord":%s' % ("true" if has_record else "false"))
    if status is not None:
        bits.append('"transcription_status":"%s"' % status)
    if has_transcript:
        esc = (f"https:\\/\\/cdn.loom.com\\/mediametadata\\/transcription\\/"
               f"{vid}-1.json") + (f"?{_SIG}" if signed else "")
        bits.append(f'"transcriptUrl":"{esc}"')
    if not bits:
        return "<html><head></head><body>no transcript here</body></html>"
    return "<html><body><script>{%s}</script></body></html>" % ",".join(bits)


_TRANSCRIPT = {"phrases": [{"ts": 0.0, "value": "Hey, here's a quick loom."},
                           {"ts": 2.5, "value": "This walks through the flow."}]}


class FakeLoomChrome:
    """Scripted stand-in for the parked Chrome tab on loom.com. Routes the fetcher's
    JS payloads (origin probe, index kick/poll, share kick/poll) to canned results.
    Never opens a socket or shells to osascript."""

    def __init__(self, index_videos, share_by_vid, *, session_ok=True,
                 origin_ok=True):
        self.index_videos = index_videos
        # vid -> True/False (has_transcript) or a dict of _share_html kwargs
        self.share_by_vid = share_by_vid
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
            spec = self.share_by_vid.get(vid, True)
            html = (_share_html(vid, **spec) if isinstance(spec, dict)
                    else _share_html(vid, spec))
            return json.dumps({"done": True, "ok": True, "status": 200,
                               "url": f"{loom_fetch.LOOM_ORIGIN}/share/{vid}",
                               "body": html})
        raise AssertionError(f"unexpected JS: {js[:80]}")


class _Forbidden(Exception):
    """Stands in for urllib's HTTPError: carries a readable body, like CloudFront's."""

    def __init__(self):
        super().__init__("HTTP Error 403: Forbidden")

    def read(self):
        return (b"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<Error>"
                b"<Code>MissingKey</Code><Message>Missing Key-Pair-Id query"
                b" parameter or cookie value</Message></Error>")


def _http_get(url, timeout):
    """Stand-in for the signed-CDN transcript GET. The transcript text embeds the
    video id so distinct videos yield distinct content (real transcripts differ;
    identical text would legitimately dedup on sources.content_hash).

    An UNSIGNED url 403s exactly as CloudFront does — that is the whole point of
    the fixture. Run 5391 lost 17 of 17 transcripts to a URL pattern that dropped
    the signature, and a fake that answered 200 to a bare `.json` path would have
    called that bug green."""
    prefix = "https://cdn.loom.com/mediametadata/transcription/"
    if url.startswith(prefix):
        if "Key-Pair-Id=" not in url:
            raise _Forbidden()
        vid = url[len(prefix):].split("-", 1)[0]
        if "silent" in vid:      # a screen capture with no speech, as Loom serves it
            return json.dumps({"phrases": [], "schemaVersion": "1.1.3"})
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

    def test_extract_transcript_url_keeps_the_cloudfront_signature(self):
        """The regression that cost run 5391 all 34 recordings.

        cdn.loom.com is a SIGNED CloudFront distribution. The old pattern ended at
        `\\.json`, so it returned a URL that looked perfectly plausible and 403'd
        on every single GET ('MissingKey — Missing Key-Pair-Id query parameter or
        cookie value'). The signature is part of the URL, not decoration."""
        url = loom_fetch.extract_transcript_url(_share_html("vid123"))
        self.assertTrue(url.startswith(
            "https://cdn.loom.com/mediametadata/transcription/vid123-1.json?"))
        self.assertIn("Key-Pair-Id=", url)
        self.assertIn("Signature=", url)

    def test_extract_transcript_url_still_matches_an_unsigned_url(self):
        """The query is optional — an older/unsigned page must not stop matching."""
        self.assertEqual(
            loom_fetch.extract_transcript_url(_share_html("vid123", signed=False)),
            "https://cdn.loom.com/mediametadata/transcription/vid123-1.json")

    def test_extract_transcript_url_stops_at_the_json_string_boundary(self):
        """Greedy matching must not swallow the rest of the page into the URL."""
        html = _share_html("vid123") + '<script>{"next":"https://evil"}</script>'
        url = loom_fetch.extract_transcript_url(html)
        self.assertNotIn("evil", url)
        self.assertNotIn('"', url)

    def test_extract_transcript_url_none_when_absent(self):
        self.assertIsNone(
            loom_fetch.extract_transcript_url(_share_html("v", False)))

    # ── the three reasons a signed URL can be missing (2026-08-13) ───────────
    def test_transcript_state_reads_status_and_record_flag(self):
        st = loom_fetch.transcript_state(
            _share_html("v", False, status="transcoding_failure", has_record=True))
        self.assertEqual(st, {"has_record": True, "status": "transcoding_failure"})

    def test_transcript_state_says_None_not_False_when_the_page_is_silent(self):
        """'the page did not say' and 'the page said no' are different facts, and
        conflating them is what let a temporary state become permanent."""
        self.assertEqual(loom_fetch.transcript_state("<html></html>"),
                         {"has_record": None, "status": None})

    def test_no_url_verdict_permanent_only_for_a_terminal_loom_failure(self):
        # the real observed value on all 17 'no_transcript' ids of run 5391
        self.assertEqual(loom_fetch.no_url_verdict(
            {"has_record": True, "status": "transcoding_failure"}), "permanent")
        # no transcript record at all = a silent recording; Loom never made one
        self.assertEqual(loom_fetch.no_url_verdict(
            {"has_record": False, "status": None}), "permanent")

    def test_no_url_verdict_calls_a_success_with_no_url_OUR_bug(self):
        """Loom says it transcribed it and we cannot find the URL -> that is an
        extraction defect and must count as a FAILURE, not as 'no transcript'."""
        self.assertEqual(loom_fetch.no_url_verdict(
            {"has_record": True, "status": "success"}), "broken")

    def test_no_url_verdict_defaults_unknown_states_to_pending(self):
        """Asymmetric on purpose: a wrong 'permanent' is unrecoverable without
        --full, a wrong 'pending' costs one share fetch."""
        for st in ({"has_record": True, "status": "transcribing"},
                   {"has_record": True, "status": None},
                   {"has_record": None, "status": None},
                   {"has_record": None, "status": "some_new_loom_state"}):
            self.assertEqual(loom_fetch.no_url_verdict(st), "pending", st)

    def test_fetch_transcript_json_returns_the_real_error(self):
        """The 403 body has to survive to the log. Swallowing it into a bare None
        is why 17 identical failures carried no diagnosis at all."""
        payload, err = loom_fetch.fetch_transcript_json(
            "https://cdn.loom.com/mediametadata/transcription/v-1.json",
            _http_get, 5)
        self.assertIsNone(payload)
        self.assertIn("403", err)
        self.assertIn("MissingKey", err)

    def test_fetch_transcript_json_reports_a_phrase_less_payload(self):
        """A payload with no `phrases` KEY is a schema change — a real failure."""
        payload, err = loom_fetch.fetch_transcript_json(
            "u", lambda u, t: json.dumps({"segments": []}), 5)
        self.assertIsNone(payload)
        self.assertIn("phrases", err)
        self.assertIn("segments", err)

    def test_fetch_transcript_json_treats_an_EMPTY_transcript_as_a_success(self):
        """Loom really serves `{"phrases":[],"schemaVersion":"1.1.3"}` (38 bytes,
        HTTP 200, transcription_status success) for a recording with no speech.
        That is a successful fetch of an empty transcript, not a fetch failure —
        11 of run 5391's `transcript_failed` were this, retried forever."""
        payload, err = loom_fetch.fetch_transcript_json(
            "u", lambda u, t: json.dumps({"phrases": [],
                                          "schemaVersion": "1.1.3"}), 5)
        self.assertIsNone(err)
        self.assertEqual(payload["phrases"], [])

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

    # a Loom that Loom itself permanently failed to transcribe — the real state
    # of all 17 'no_transcript' ids on run 5391 (transcription_status
    # transcoding_failure, processing finished in 2024-2026, never retried since)
    _DEAD = {"has_transcript": False, "status": "transcoding_failure",
             "has_record": True}

    def test_no_transcript_loom_is_recorded_excluded_and_not_refetched(self):
        chrome = FakeLoomChrome([_video("bbb")], {"bbb": self._DEAD})
        stats = self._run(chrome)
        self.assertEqual(stats["no_transcript"], 1)
        self.assertEqual(stats["ingested"], 0)
        self.assertEqual(stats["no_transcript_reasons"],
                         {"transcoding_failure": 1})
        rows = self._loom_rows()
        self.assertEqual(rows[0]["source_ref"], "loom/bbb")
        self.assertEqual(rows[0]["status"], "skipped")
        self.assertEqual(rows[0]["content_type"], "loom:no-transcript")
        # second incremental run: it is now KNOWN (skipped ids are excluded), so 0
        # attempted -> a clean TaskSkip, never a re-fetch of the dead transcript.
        with self.assertRaises(loom_fetch.runner.TaskSkip):
            self._run(chrome)

    def test_a_silent_share_page_is_PENDING_not_permanently_written_off(self):
        """A page that says nothing about the transcript is not evidence there is
        none. The old code recorded a `skipped` source on any missing URL, so one
        bad regex permanently excluded 17 recordings that Loom had transcribed
        fine — recoverable only via --full, which nothing runs on a schedule."""
        chrome = FakeLoomChrome([_video("qqq")], {"qqq": False})
        stats = self._run(chrome)
        self.assertEqual(stats["transcript_pending"], 1)
        self.assertEqual(stats["no_transcript"], 0)
        self.assertEqual(self._loom_rows(), [])          # nothing written off
        # and it is offered again next run, at no cost
        stats2 = self._run(chrome)
        self.assertEqual(stats2["attempted"], 1)

    def test_success_with_no_url_counts_as_a_FAILURE_not_no_transcript(self):
        """Loom says it transcribed the video and we could not find the URL: that
        is our extraction breaking, and burying it as 'no transcript' is exactly
        how the corpus froze."""
        chrome = FakeLoomChrome([_video("sss")],
                                {"sss": {"has_transcript": False,
                                         "status": "success", "has_record": True}})
        stats = self._run(chrome)
        self.assertEqual(stats["transcript_failed"], 1)
        self.assertEqual(stats["no_transcript"], 0)
        self.assertEqual(self._loom_rows(), [])

    def test_an_unsigned_transcript_url_is_a_failure_not_a_silent_skip(self):
        """End-to-end proof of the 5391 defect: the page carries the URL, the GET
        403s for want of the signature, and that must land in transcript_failed
        WITH the CloudFront reason in the metrics — never as 'no transcript'."""
        chrome = FakeLoomChrome([_video("uuu")],
                                {"uuu": {"has_transcript": True, "signed": False}})
        stats = self._run(chrome)
        self.assertEqual(stats["transcript_failed"], 1)
        self.assertEqual(stats["no_transcript"], 0)
        self.assertIn("MissingKey", json.dumps(stats["fail_reasons"]))

    def test_an_empty_transcript_is_a_permanent_skip_not_a_failure(self):
        """The genuinely-legitimate case: Loom transcribed a silent recording and
        the result is empty. Permanent (it will never gain speech), and it must
        NOT read as a failed fetch — that is what kept 11 ids in the retry loop."""
        chrome = FakeLoomChrome([_video("silent1")], {"silent1": True})
        stats = self._run(chrome)
        self.assertEqual(stats["no_transcript"], 1)
        self.assertEqual(stats["transcript_failed"], 0)
        self.assertEqual(stats["no_transcript_reasons"], {"empty-transcript": 1})
        rows = self._loom_rows()
        self.assertEqual(rows[0]["status"], "skipped")
        # and it never comes back as 'new'
        with self.assertRaises(loom_fetch.runner.TaskSkip):
            self._run(chrome)

    def test_second_no_transcript_does_not_collide_on_hash(self):
        chrome = FakeLoomChrome([_video("b1"), _video("b2")],
                                {"b1": self._DEAD, "b2": self._DEAD})
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
        chrome1 = FakeLoomChrome([_video("ccc")], {"ccc": self._DEAD})
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

    # ── the SECOND silent zero: index fine, ingested nothing (2026-08-13) ────
    def test_attempted_but_ingested_nothing_degrades_the_run(self):
        """Run 5391: index 597, 34 new, 34 attempted, 0 ingested — and the run was
        `completed` with source health `ok`. The empty-index guard only ever looked
        at the index, so 'index is fine, ingested nothing' walked straight past it.
        A run that attempted work and added nothing to the KB is degraded."""
        chrome = FakeLoomChrome([_video("a1"), _video("a2")],
                                {"a1": {"has_transcript": True, "signed": False},
                                 "a2": {"has_transcript": True, "signed": False}})
        stats = self._run(chrome)
        self.assertEqual(stats["attempted"], 2)
        self.assertEqual(stats["ingested"], 0)
        self.assertTrue(stats["degraded"])
        kinds = [c.args[0] for c in self.ctx.anomaly.call_args_list]
        self.assertIn("loom_fetch_zero_ingest", kinds)
        call = next(c for c in self.ctx.anomaly.call_args_list
                    if c.args[0] == "loom_fetch_zero_ingest")
        # broken fetches -> critical (reaches the interrupt queue)
        self.assertEqual(call.args[1], "critical")
        self.assertEqual(call.args[2]["attempted"], 2)
        self.assertEqual(call.args[2]["transcript_failed"], 2)

    def test_zero_ingest_is_only_a_WARN_when_loom_itself_has_no_transcript(self):
        """Thin but not broken: nothing on our side failed, so it degrades the
        source (the operator still sees 'attempted 2, ingested 0') without firing
        an interrupt about videos Loom gave up on years ago."""
        chrome = FakeLoomChrome([_video("d1"), _video("d2")],
                                {"d1": self._DEAD, "d2": self._DEAD})
        stats = self._run(chrome)
        self.assertTrue(stats["degraded"])
        call = next(c for c in self.ctx.anomaly.call_args_list
                    if c.args[0] == "loom_fetch_zero_ingest")
        self.assertEqual(call.args[1], "warn")
        self.assertEqual(call.args[2]["no_transcript_reasons"],
                         {"transcoding_failure": 2})

    def test_a_single_stuck_pending_recording_does_NOT_degrade_the_source(self):
        """A recording Loom has not finished transcribing is not work this rail
        failed at, and it never leaves the candidate set — so degrading on it
        would pin the source amber forever with no way to self-heal. Real case:
        loom/5c26cfbefab549… has been `in_progress` since 2025-06-15."""
        chrome = FakeLoomChrome([_video("p1")],
                                {"p1": {"has_transcript": False,
                                        "status": "in_progress",
                                        "has_record": True}})
        stats = self._run(chrome)
        self.assertEqual(stats["transcript_pending"], 1)
        self.assertEqual(stats["ingested"], 0)
        self.assertNotIn("degraded", stats)
        kinds = [c.args[0] for c in self.ctx.anomaly.call_args_list]
        self.assertNotIn("loom_fetch_zero_ingest", kinds)
        self.assertEqual(self._loom_rows(), [])          # and nothing written off

    def test_a_WHOLE_BATCH_stuck_pending_does_degrade(self):
        """One stuck transcription is life; Loom does not have a whole batch of
        old recordings mid-transcription at once. That shape is the rail lying."""
        vids = [_video(f"p{i}", created=f"2026-08-0{i}T00:00:00Z")
                for i in range(1, 7)]
        spec = {"has_transcript": False, "status": "in_progress", "has_record": True}
        chrome = FakeLoomChrome(vids, {v["id"]: spec for v in vids})
        stats = self._run(chrome)
        self.assertEqual(stats["transcript_pending"], 6)
        self.assertTrue(stats["degraded"])
        kinds = [c.args[0] for c in self.ctx.anomaly.call_args_list]
        self.assertIn("loom_fetch_zero_ingest", kinds)

    def test_a_run_that_ingested_something_raises_NO_zero_ingest_anomaly(self):
        """Must not cry wolf: one transcript through the door is a working rail."""
        chrome = FakeLoomChrome([_video("ok1"), _video("bad1")],
                                {"ok1": True, "bad1": self._DEAD})
        stats = self._run(chrome)
        self.assertEqual(stats["ingested"], 1)
        self.assertNotIn("degraded", stats)
        kinds = [c.args[0] for c in self.ctx.anomaly.call_args_list]
        self.assertNotIn("loom_fetch_zero_ingest", kinds)

    def test_zero_ingest_degrade_kind_is_registered_in_SOURCES(self):
        """An anomaly nobody declared is an anomaly nobody sees."""
        src = next(s for s in config.SOURCES if s["key"] == "loom_fetch")
        self.assertIn("loom_fetch_zero_ingest", src["degrade_kinds"])

    def test_zero_ingest_anomaly_actually_turns_the_SOURCE_degraded(self):
        """The whole point: the anomaly must reach the Today source-health line,
        not just sit in a table. Wired end-to-end through source_health so a
        future refactor of either half cannot quietly break the link."""
        from core import source_health
        src = dict(next(s for s in config.SOURCES if s["key"] == "loom_fetch"))
        now = datetime(2026, 8, 13, 19, 0, tzinfo=timezone.utc)
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO runs (task_name, status, started_at,"
                " completed_at) VALUES ('loom_fetch','completed',?,?)",
                ("2026-08-13T18:10:58+00:00", "2026-08-13T18:11:56+00:00"))
            run_id = conn.execute("SELECT MAX(id) FROM runs").fetchone()[0]
            before = source_health.source_status(conn, src, now)
            self.assertEqual(before["status"], "ok")     # the 5391 lie
            conn.execute(
                "INSERT INTO anomalies (task_name, kind, detail_json, severity,"
                " run_id) VALUES ('loom_fetch','loom_fetch_zero_ingest',?, 'warn',?)",
                (json.dumps({"attempted": 34, "ingested": 0}), run_id))
            after = source_health.source_status(conn, src, now)
        self.assertEqual(after["status"], "degraded")
        self.assertIn("DEGRADED", source_health.source_line(after))

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
