"""upwork_client.refresh() and the file it refreshes — 2026-09-25.

Upwork revoked both of Abhijeet's OAuth grants that morning. The MCP rail
failed with an error that named its fix command and was back within the hour.
upwork_sync and upwork_inbox failed with `<HTTPError 400: 'Bad Request'>`, the
body that said why was thrown away, and they stayed down all day. Then the only
re-auth tool for their grant turned out to write a different file from the one
v3 reads. These pin all three lessons.
"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from core import config
from modules.upwork import upwork_client as uc

ROOT = Path(__file__).resolve().parents[1]


def _http_error(code: int, body: dict | str):
    raw = body if isinstance(body, str) else json.dumps(body)
    return urllib.error.HTTPError("https://www.upwork.com/api/v3/oauth2/token",
                                  code, "Bad Request", {}, io.BytesIO(raw.encode()))


class RefreshCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="upw-refresh-"))
        self.creds = self.tmp / "upwork-api.json"
        self.creds.write_text(json.dumps({
            "client_id": "cid", "client_secret": "csecret",
            "tokens": {"access_token": "old-access", "refresh_token": "old-refresh",
                       "expires_at": 0}}))
        self.before = self.creds.read_text()
        self.p1 = mock.patch.object(uc, "CREDS_PATH", self.creds)
        self.p2 = mock.patch.object(uc, "token_owner", return_value="v3")
        self.p3 = mock.patch.object(uc, "read_access_token", return_value="old-access")
        for p in (self.p1, self.p2, self.p3):
            p.start()

    def tearDown(self):
        for p in (self.p1, self.p2, self.p3):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestARevokedGrantSaysWhatToRun(RefreshCase):
    def test_invalid_grant_names_the_command_and_keeps_upworks_reason(self):
        err = _http_error(400, {"error": "invalid_grant", "error_description":
                                "Refresh token is expired or revoked or not registered"})
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(uc.UpworkAuthError) as cm:
                uc.UpworkClient().refresh()
        msg = str(cm.exception)
        self.assertIn("bin/upwork-api-auth", msg)
        self.assertIn("Refresh token is expired or revoked", msg)   # not discarded
        self.assertIn("upwork_sync", msg)
        self.assertNotIn("Bad Request", msg)                          # the old message

    def test_a_refused_refresh_never_touches_the_file(self):
        err = _http_error(400, {"error": "invalid_grant"})
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(uc.UpworkAuthError):
                uc.UpworkClient().refresh()
        self.assertEqual(self.creds.read_text(), self.before)

    def test_it_is_still_an_api_error_for_every_caller_that_catches_those(self):
        self.assertTrue(issubclass(uc.UpworkAuthError, uc.UpworkAPIError))

    def test_a_non_grant_failure_reports_its_body_without_blaming_the_grant(self):
        err = _http_error(503, "upstream unavailable")
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(uc.UpworkAuthError) as cm:
                uc.UpworkClient().refresh()
        msg = str(cm.exception)
        self.assertIn("503", msg)
        self.assertIn("upstream unavailable", msg)
        self.assertNotIn("re-authorize", msg)

    def test_the_proactive_refresh_path_carries_the_same_message(self):
        """sync and inbox never call refresh() directly — they hit it through
        _ensure_fresh_token on an expired token, which is exactly how the
        25 Sep failure arrived. The message has to survive that path."""
        err = _http_error(400, {"error": "invalid_grant"})
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(uc.UpworkAuthError) as cm:
                uc.UpworkClient().graphql("query { user { id } }")
        self.assertIn("bin/upwork-api-auth", str(cm.exception))


class TestTheReauthWritesWhereV3Reads(unittest.TestCase):
    def test_the_v3_wrapper_targets_the_file_the_client_reads(self):
        src = (ROOT / "bin" / "upwork-api-auth").read_text()
        self.assertIn('config.CREDENTIALS_DIR / "upwork-api.json"', src)
        self.assertEqual(uc.CREDS_PATH, config.CREDENTIALS_DIR / "upwork-api.json")
        self.assertIn("uc.CREDS_PATH != TARGET", src)       # it refuses on drift

    def test_the_v2_script_on_its_own_would_write_somewhere_else(self):
        """Pinned so nobody 'simplifies' the wrapper into `upwork_api.py auth`:
        that writes ~/Desktop/Etherwise/.credentials, a file v3 has not read
        since the cutover. If this ever fails because the two paths now agree,
        the wrapper can go — not before."""
        src = (ROOT / "scripts" / "upwork_api.py").read_text()
        self.assertIn('Path.home() / "Desktop" / "Etherwise" / ".credentials"', src)
        self.assertNotIn("etherwise-os", src.split("CREDS_PATH =", 1)[1].split("\n", 1)[0])


if __name__ == "__main__":
    unittest.main()
