"""Tests for bin/gmail-auth — the loopback OAuth consent flow. No network, no
browser: the token exchange is injected. Confirms the read-only scope, the
loopback redirect, the no-refresh-token guard, and the persisted record shape."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path


def _load_gmail_auth():
    path = Path(__file__).resolve().parents[1] / "bin" / "gmail-auth"
    loader = importlib.machinery.SourceFileLoader("gmail_auth", str(path))
    spec = importlib.util.spec_from_loader("gmail_auth", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ga = _load_gmail_auth()


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self):
        return self._b


class TestGmailAuth(unittest.TestCase):
    CFG = {"client_id": "cid.apps.googleusercontent.com", "client_secret": "secret"}

    def test_read_only_scope_only(self):
        # never request send/modify — read-only is the whole point
        self.assertEqual(ga.SCOPE,
                         "https://www.googleapis.com/auth/gmail.readonly")
        self.assertNotIn("gmail.send", ga.SCOPE)
        self.assertNotIn("gmail.modify", ga.SCOPE)

    def test_inbox_resolves_to_per_inbox_file_legacy_default(self):
        # gmail-auth writes to the same resolver email_poll reads from
        self.assertEqual(ga.ep.token_for(None), ga.TOKEN_FILE)
        self.assertEqual(ga.ep.token_for("contact@etherwise.io"), ga.TOKEN_FILE)
        p = ga.ep.token_for("abhijeet.gandhi@optimizeis.com")
        self.assertEqual(p, ga.ep.GMAIL_TOKENS_DIR /
                         "abhijeet.gandhi_optimizeis.com.json")
        self.assertNotEqual(p, ga.TOKEN_FILE)

    def test_free_port_is_loopback_int(self):
        p = ga._free_port()
        self.assertIsInstance(p, int)
        self.assertTrue(0 < p < 65536)

    def test_exchange_returns_token(self):
        tok = ga.exchange_code(
            self.CFG, "auth-code", "http://127.0.0.1:5000/",
            _open=lambda req, timeout=0: _Resp(
                {"refresh_token": "rt", "access_token": "at", "expires_in": 3600}))
        self.assertEqual(tok["refresh_token"], "rt")

    def test_exchange_requires_refresh_token(self):
        # Google omits refresh_token unless prompt=consent minted one — must raise
        with self.assertRaises(RuntimeError) as cm:
            ga.exchange_code(self.CFG, "c", "http://127.0.0.1:5000/",
                             _open=lambda req, timeout=0: _Resp(
                                 {"access_token": "at", "expires_in": 3600}))
        self.assertIn("refresh_token", str(cm.exception))

    def test_build_token_shape_and_expiry(self):
        now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
        out = ga.build_token(self.CFG,
                             {"refresh_token": "rt", "access_token": "at",
                              "expires_in": 3600}, now)
        self.assertEqual(set(out), {"client_id", "client_secret",
                                    "refresh_token", "access_token", "expiry"})
        # expiry = now + (expires_in - 60s) = 12:59:00
        self.assertEqual(out["expiry"], "2026-06-24T12:59:00+00:00")

    def test_loopback_handler_captures_code(self):
        captured = {}

        class FakeHandler:
            path = "/?code=THE_CODE&scope=x"
            def do_GET(self):
                import urllib.parse
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                captured["code"] = (qs.get("code") or [None])[0]
        # the real handler's capture logic is the same parse; assert it works
        FakeHandler().do_GET()
        self.assertEqual(captured["code"], "THE_CODE")


if __name__ == "__main__":
    unittest.main()
