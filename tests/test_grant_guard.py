"""grant_guard — one refresh at a time, and never a second try on a dead grant.

2026-10-04. Upwork cancelled Abhijeet's grants on 25 Sep and again on 30 Sep.
The first death is in the log, two lines sharing a second:

    [06:05:04 IST] upwork_pulse      | run 14321 started
    [06:05:04 IST] upwork_financials | run 14322 started
    [06:05:05 IST] upwork_financials | FAILED ... invalid_grant
    [06:05:06 IST] upwork_pulse      | FAILED ... invalid_grant

Two processes read one refresh token and both presented it. Upwork rotates
refresh tokens; the second presentation is a replay and the grant is revoked.
`FakeUpwork` below enforces exactly that rule, so these tests fail the way
production failed if the lock is ever taken out.
"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

from core import config
from modules.upwork import grant_guard as guard
from modules.upwork import upwork_client as uc
from modules.upwork.feeds import mcp_client as mc


class FakeUpwork:
    """A token endpoint with ROTATING refresh tokens: each is good once. A
    second presentation revokes the whole family — every later request with
    any token of that family is refused, the way a real replay is handled."""

    def __init__(self, first_refresh: str, delay_s: float = 0.15):
        self.valid = {first_refresh}
        self.used = set()
        self.revoked = False
        self.calls = []                 # refresh tokens presented, in order
        self.delay_s = delay_s
        self.minted = 0
        self._mu = threading.Lock()

    def urlopen(self, req, timeout=None):
        form = urllib.parse.parse_qs((req.data or b"").decode())
        rt = (form.get("refresh_token") or [""])[0]
        with self._mu:
            self.calls.append(rt)
            replay = rt in self.used
            ok = (rt in self.valid) and not self.revoked and not replay
            if replay:
                self.revoked = True      # replay detected: the family dies
            if ok:
                self.used.add(rt)
                self.valid.discard(rt)
                self.minted += 1
                n = self.minted
                self.valid.add(f"refresh-{n}")
        time.sleep(self.delay_s)         # the window two processes shared
        if not ok:
            raise urllib.error.HTTPError(
                req.full_url, 400, "Bad Request", {}, io.BytesIO(json.dumps({
                    "error": "invalid_grant", "error_description":
                    "Refresh token is expired or revoked or not registered"}).encode()))
        body = json.dumps({"access_token": f"access-{n}", "refresh_token": f"refresh-{n}",
                           "expires_in": 86400, "token_type": "Bearer"}).encode()
        resp = mock.MagicMock()
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="grant-guard-"))
        self.events = self.tmp / "events.jsonl"
        self._patches = [mock.patch.object(guard, "EVENTS_PATH", self.events)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        # LAST IN, FIRST OUT. One test patches urllib.request.urlopen twice;
        # stopped in start order, the second stop "restores" the first mock and
        # leaves urlopen faked for every later test in the process — which is
        # how nine unrelated cockpit HTTP tests failed the first full run.
        for p in reversed(self._patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def patch(self, *a, **kw):
        p = mock.patch.object(*a, **kw)
        self._patches.append(p)
        return p.start()

    def event_names(self):
        if not self.events.exists():
            return []
        return [json.loads(l)["event"] for l in self.events.read_text().splitlines()]


class McpCase(GuardCase):
    def setUp(self):
        super().setUp()
        self.tokens = self.tmp / "upwork-mcp.json"
        self.tokens.write_text(json.dumps({
            "access_token": "access-0", "refresh_token": "refresh-0",
            "expires_in": 86400, "expires_at": time.time() - 10}))   # expired
        self.patch(mc, "TOKENS_PATH", self.tokens)
        self.patch(mc, "client_id", return_value="cid")
        self.upwork = FakeUpwork("refresh-0")
        self.patch(urllib.request, "urlopen", side_effect=self.upwork.urlopen)

    def race(self, n=2):
        out, errs = [], []
        gate = threading.Barrier(n)

        def one():
            gate.wait()                  # the same launchd tick
            try:
                out.append(mc.access_token())
            except Exception as exc:     # noqa: BLE001
                errs.append(exc)
        ts = [threading.Thread(target=one) for _ in range(n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        return out, errs


class TestTwoJobsOnOneTick(McpCase):
    def test_the_refresh_token_is_presented_once_and_both_get_the_new_token(self):
        out, errs = self.race(2)
        self.assertEqual(errs, [])
        self.assertEqual(self.upwork.calls, ["refresh-0"])       # ONE presentation
        self.assertEqual(out, ["access-1", "access-1"])
        self.assertFalse(self.upwork.revoked)
        self.assertEqual(json.loads(self.tokens.read_text())["refresh_token"], "refresh-1")
        self.assertIn("reused_fresh_token", self.event_names())

    def test_six_rails_kicked_at_once_is_still_one_presentation(self):
        """What catch-up did on the evening of 4 Oct: financials, scan, inbox,
        sync, pulse and hiring, all inside six seconds."""
        out, errs = self.race(6)
        self.assertEqual(errs, [])
        self.assertEqual(len(self.upwork.calls), 1)
        self.assertEqual(set(out), {"access-1"})

    def test_without_the_lock_this_is_25_september(self):
        """The control. Take the lock away and the same two callers replay the
        token, Upwork revokes the grant, and somebody fails with invalid_grant.
        If this ever stops failing, FakeUpwork has stopped modelling rotation
        and the two tests above prove nothing."""
        import contextlib

        @contextlib.contextmanager
        def no_lock(path, timeout_s=0):
            yield False
        self.patch(guard, "locked", side_effect=no_lock)
        out, errs = self.race(2)
        self.assertEqual(self.upwork.calls, ["refresh-0", "refresh-0"])   # the replay
        self.assertTrue(self.upwork.revoked)
        self.assertTrue(any("invalid_grant" in str(e) for e in errs))

    def test_a_401_from_the_server_does_not_cause_a_second_refresh_either(self):
        """The other road into refresh(): the server refuses an access token
        that is not expired by our clock. Two sessions holding the same refused
        token must still present the refresh token once between them."""
        self.tokens.write_text(json.dumps({
            "access_token": "access-0", "refresh_token": "refresh-0",
            "expires_in": 86400, "expires_at": time.time() + 3600}))
        first = mc.refresh(stale_access="access-0")
        self.assertEqual(first["access_token"], "access-1")
        second = mc.refresh(stale_access="access-0")      # the slower session
        self.assertEqual(second["access_token"], "access-1")
        self.assertEqual(self.upwork.calls, ["refresh-0"])


class TestADeadGrantIsNotReplayed(McpCase):
    def kill(self):
        self.upwork.valid.clear()            # Upwork has revoked it

    def test_the_second_run_makes_no_call_at_all(self):
        self.kill()
        with self.assertRaises(mc.MCPAuthError):
            mc.access_token()
        self.assertEqual(len(self.upwork.calls), 1)
        for _ in range(5):                   # five more scheduled runs
            with self.assertRaises(mc.MCPAuthError) as cm:
                mc.access_token()
        self.assertEqual(len(self.upwork.calls), 1)          # still one
        msg = str(cm.exception)
        self.assertIn("bin/upwork-mcp-auth", msg)
        self.assertIn("Refresh token is expired or revoked", msg)
        self.assertIn("Not retried", msg)

    def test_it_is_still_an_auth_failure_to_the_runbook(self):
        self.kill()
        for _ in range(2):
            try:
                mc.access_token()
            except mc.MCPAuthError as exc:
                self.assertEqual(mc.start_failure_kind(exc), "auth")

    def test_a_new_grant_reopens_the_circuit_by_itself(self):
        """Keyed to the refused token, not to the file: a re-auth by ANY route
        (bin/upwork-mcp-auth, a restored backup, a script nobody updated)
        clears the breaker without anyone remembering to."""
        self.kill()
        with self.assertRaises(mc.MCPAuthError):
            mc.access_token()
        self.assertIsNotNone(guard.dead(self.tokens, "refresh-0"))
        self.tokens.write_text(json.dumps({
            "access_token": "access-new", "refresh_token": "refresh-new",
            "expires_in": 86400, "expires_at": time.time() + 3600}))
        self.assertEqual(mc.access_token(), "access-new")
        self.assertFalse((self.tmp / "upwork-mcp.json.dead").exists())

    def test_the_auth_script_path_clears_it_too(self):
        self.kill()
        with self.assertRaises(mc.MCPAuthError):
            mc.access_token()
        self.upwork.valid.add("")            # the code exchange carries no refresh token
        mc.exchange_code("code", "verifier")
        self.assertFalse((self.tmp / "upwork-mcp.json.dead").exists())
        self.assertIn("authorized", self.event_names())

    def test_a_server_error_is_not_a_death(self):
        """A 503 from the token endpoint says nothing about the grant. Marking
        it dead would lock every rail out until a re-auth nobody needed."""
        err = urllib.error.HTTPError("u", 503, "x", {}, io.BytesIO(b"upstream"))
        self.patch(urllib.request, "urlopen", side_effect=err)
        with self.assertRaises(mc.MCPAuthError):
            mc.access_token()
        self.assertIsNone(guard.dead(self.tokens, "refresh-0"))


class TestOneEmailPerDeath(McpCase):
    def setUp(self):
        super().setUp()
        self.upwork.valid.clear()
        self.sent = self.patch(guard, "_send")
        self.live = self.patch(guard, "_is_live_path", return_value=True)
        self._patches.append(mock.patch.dict(config.SHADOW_MODE, {"upwork": False}))
        self._patches[-1].start()

    def fail(self, n):
        for _ in range(n):
            with self.assertRaises(mc.MCPAuthError):
                mc.access_token()

    def test_ninety_six_failed_scans_send_one_email(self):
        self.fail(96)
        self.assertEqual(self.sent.call_count, 1)
        subject, html = self.sent.call_args[0]
        self.assertIn("Upwork login cancelled", subject)
        self.assertIn("bin/upwork-mcp-auth", html)
        self.assertIn("job scanner", html)

    def test_the_email_carries_no_token(self):
        self.fail(1)
        subject, html = self.sent.call_args[0]
        for secret in ("refresh-0", "access-0"):
            self.assertNotIn(secret, subject + html)
        marker = (self.tmp / "upwork-mcp.json.dead").read_text()
        self.assertNotIn("refresh-0", marker)             # a fingerprint, not the token

    def test_a_dead_smtp_delays_the_email_and_never_hides_the_real_error(self):
        self.sent.side_effect = [OSError("smtp down"), None]
        self.fail(1)                         # MCPAuthError, not OSError
        self.assertEqual(self.sent.call_count, 1)
        self.fail(1)                         # retried by a run that calls nobody
        self.assertEqual(self.sent.call_count, 2)
        self.fail(5)
        self.assertEqual(self.sent.call_count, 2)
        self.assertEqual(len(self.upwork.calls), 1)

    def test_shadow_mode_sends_nothing(self):
        with mock.patch.dict(config.SHADOW_MODE, {"upwork": True}):
            self.fail(3)
        self.sent.assert_not_called()

    def test_a_temp_file_can_never_email_him(self):
        """The safety catch, exercised for real: without the patch that says
        'this is the live credentials directory', nothing is sent."""
        self.live.return_value = False
        self.fail(3)
        self.sent.assert_not_called()


_REAL_URLOPEN = urllib.request.urlopen      # module level: a function kept as a
                                            # class attribute comes back bound


class TestTheseTestsCleanUpAfterThemselves(unittest.TestCase):
    def test_urlopen_is_the_real_one_after_a_double_patched_case(self):
        case = TestADeadGrantIsNotReplayed("test_a_server_error_is_not_a_death")
        result = unittest.TestResult()
        case.run(result)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertIs(urllib.request.urlopen, _REAL_URLOPEN)


class TestOnlyTheRealCredentialsCanEmail(GuardCase):
    def test_the_unpatched_check_tells_a_temp_file_from_the_real_directory(self):
        """The one place the alert tests above put a mock; this is the real
        function. A client pointed anywhere but config.CREDENTIALS_DIR is not
        live, so a test that forgets to mock the sender still cannot mail him."""
        self.assertFalse(guard._is_live_path(self.tmp / "upwork-mcp.json"))
        self.assertTrue(guard._is_live_path(config.CREDENTIALS_DIR / "upwork-mcp.json"))
        self.assertTrue(guard._is_live_path(config.CREDENTIALS_DIR / "upwork-api.json"))

    def test_the_forensic_log_is_not_written_for_a_temp_file_either(self):
        with mock.patch.object(guard, "EVENTS_PATH", guard._DEFAULT_EVENTS), \
                mock.patch("builtins.open") as opened:
            guard.note(self.tmp / "upwork-mcp.json", "refreshed")
        opened.assert_not_called()


class TestTheLockItself(GuardCase):
    def test_a_slow_holder_is_a_timeout_not_an_auth_failure(self):
        """Nobody should be sent to re-authorize because one refresh was slow.
        TimeoutError is what start_failure_kind reads as 'network — nothing to
        repair, the next run self-heals'."""
        target = self.tmp / "t.json"
        with guard.locked(target):
            with self.assertRaises(TimeoutError) as cm:
                with guard.locked(target, timeout_s=0.2):
                    pass
        self.assertEqual(mc.start_failure_kind(cm.exception), "network")

    def test_the_lock_is_released_when_the_holder_raises(self):
        target = self.tmp / "t.json"
        with self.assertRaises(RuntimeError):
            with guard.locked(target):
                raise RuntimeError("holder died")
        with guard.locked(target, timeout_s=0.2):         # immediately free
            pass

    def test_a_write_replaces_and_a_failed_write_leaves_the_old_file(self):
        target = self.tmp / "creds.json"
        target.write_text("OLD")
        guard.write_atomic(target, "NEW")
        self.assertEqual(target.read_text(), "NEW")
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                guard.write_atomic(target, "HALF")
        self.assertEqual(target.read_text(), "NEW")       # never truncated
        self.assertEqual([p.name for p in self.tmp.iterdir() if ".tmp" in p.name], [])


class TestTheGraphQLClientGetsTheSame(GuardCase):
    def setUp(self):
        super().setUp()
        self.creds = self.tmp / "upwork-api.json"
        self.creds.write_text(json.dumps({
            "client_id": "cid", "client_secret": "csecret",
            "tokens": {"access_token": "access-0", "refresh_token": "refresh-0",
                       "expires_at": time.time() - 10}}))
        self.patch(uc, "CREDS_PATH", self.creds)
        self.patch(uc, "token_owner", return_value="v3")
        self.upwork = FakeUpwork("refresh-0")
        self.patch(urllib.request, "urlopen", side_effect=self.upwork.urlopen)

    def test_two_clients_on_one_tick_present_the_token_once(self):
        a, b = uc.UpworkClient(), uc.UpworkClient()
        errs = []
        gate = threading.Barrier(2)

        def go(client):
            gate.wait()
            try:
                client.refresh()
            except Exception as exc:     # noqa: BLE001
                errs.append(exc)
        ts = [threading.Thread(target=go, args=(c,)) for c in (a, b)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        self.assertEqual(self.upwork.calls, ["refresh-0"])
        self.assertEqual((a._token, b._token), ("access-1", "access-1"))
        saved = json.loads(self.creds.read_text())
        self.assertEqual(saved["client_secret"], "csecret")       # the rest survives
        self.assertEqual(saved["tokens"]["refresh_token"], "refresh-1")

    def test_sync_does_not_replay_a_dead_token_223_times(self):
        self.upwork.valid.clear()
        for _ in range(223):
            with self.assertRaises(uc.UpworkAuthError) as cm:
                uc.UpworkClient().graphql("query { user { id } }")
        self.assertEqual(len(self.upwork.calls), 1)
        self.assertIn("bin/upwork-api-auth", str(cm.exception))

    def test_a_client_built_before_someone_elses_refresh_adopts_their_token(self):
        late = uc.UpworkClient()                         # holds access-0
        uc.UpworkClient().refresh()                      # another process re-mints
        self.assertEqual(len(self.upwork.calls), 1)
        late._ensure_fresh_token()
        self.assertEqual(late._token, "access-1")
        self.assertEqual(len(self.upwork.calls), 1)      # and did not refresh again


if __name__ == "__main__":
    unittest.main()
