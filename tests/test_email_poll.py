"""Tests for modules/knowledge/email_poll.py — Gmail HTTP mocked. Covers the
privacy gate (sender-domain include/exclude), the parser, the domain map, and
the poll loop on temp dirs. No live token is ever needed."""
from __future__ import annotations

import base64
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db, runner
from modules.knowledge import email_poll as ep, clients, ingest


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def gmail_msg(mid, frm, subject="Hi", body="Let's automate your pipeline.",
              internal="1750000000000"):
    return {
        "id": mid, "internalDate": internal, "snippet": body[:40],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": "From", "value": frm},
                        {"name": "Subject", "value": subject},
                        {"name": "Date", "value": "Mon, 01 Jun 2026 10:00:00 +0000"}],
            "parts": [{"mimeType": "text/plain", "body": {"data": _b64(body)}}],
        },
    }


CMAP = [
    {"client_id": "horizons-aba", "name": "Horizons ABA",
     "identifiers": ["horizonsaba.com", "horizons"]},
    {"client_id": "adelye", "name": "Adelye",
     "identifiers": ["adelye.com", "adélye"]},
    {"client_id": "netfly", "name": "NETFLY",        # no company domain
     "identifiers": ["netfly", "haleema"]},
]


class TestDomainMapAndFilter(unittest.TestCase):
    def test_domain_map_only_company_domains(self):
        dm = clients.domain_map(CMAP)
        self.assertEqual(dm, {"horizonsaba.com": "horizons-aba",
                              "adelye.com": "adelye"})
        self.assertNotIn("netfly", dm)               # bare name is not a domain

    def test_sender_domain_parsing(self):
        self.assertEqual(ep.sender_domain("Jane Doe <jane@horizonsaba.com>"),
                         "horizonsaba.com")
        self.assertEqual(ep.sender_domain("bob@sub.adelye.com"), "sub.adelye.com")
        self.assertEqual(ep.sender_domain("garbage"), "")

    def test_match_includes_client_domain(self):
        dm = clients.domain_map(CMAP)
        self.assertEqual(ep.match_client("a@horizonsaba.com", dm), "horizons-aba")

    def test_match_excludes_personal_and_unknown(self):
        dm = clients.domain_map(CMAP)
        self.assertIsNone(ep.match_client("someone@gmail.com", dm))
        self.assertIsNone(ep.match_client("x@randomvendor.com", dm))


class TestParseEmail(unittest.TestCase):
    def test_parse_extracts_fields_and_body(self):
        p = ep.parse_email(gmail_msg("m1", "Jane <jane@adelye.com>",
                                     subject="Cert bug", body="The API fails."))
        self.assertEqual(p["source_type"], "email")
        self.assertEqual(p["source_ref"], "email/m1")
        self.assertEqual(p["title"], "Cert bug")
        self.assertEqual(p["participants"], ["jane@adelye.com"])
        self.assertIn("The API fails.", p["text"])
        self.assertTrue(p["occurred_dt"].startswith("2025") or
                        p["occurred_dt"].startswith("2026"))

    def test_email_classifies_by_sender_domain(self):
        # the ingest 'email' parser + classify route on sender domain
        cid, name, ctype = ingest.classify(
            participants=["jane@adelye.com"], title="Cert bug", client_map=CMAP)
        self.assertEqual(cid, "adelye")

    def test_build_query_company_domains_and_after(self):
        q = ep.build_query({"adelye.com": "adelye", "horizonsaba.com": "h"},
                           after_epoch="1750000000000")
        self.assertIn("from:@adelye.com", q)
        self.assertIn("from:@horizonsaba.com", q)
        self.assertIn("after:1750000000", q)         # ms -> s


class TestPoll(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="email-test-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.inbox = self.tmp / "inbox"
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.tmp / "logs"),
            mock.patch.object(config, "LOCK_DIR", self.tmp / "locks"),
            mock.patch.object(config, "KNOWLEDGE_INBOX", self.inbox),
            mock.patch.object(clients, "load_client_map", return_value=CMAP),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_poll(self, list_pages, msgs):
        feed = list(list_pages)
        store = {m["id"]: m for m in msgs}
        return runner.run_task(
            ep.TASK_NAME,
            lambda ctx: ep.poll(ctx, _list=lambda t, q, pt=None: feed.pop(0),
                                _get=lambda t, mid: store[mid],
                                _token=lambda: "tok"),
            module="knowledge", db_path=self.db_path)

    def test_saves_client_mail_excludes_personal(self):
        msgs = [gmail_msg("m1", "a@horizonsaba.com"),
                gmail_msg("m2", "friend@gmail.com"),          # personal -> excluded
                gmail_msg("m3", "b@adelye.com")]
        res = self.run_poll([{"messages": [{"id": "m1"}, {"id": "m2"},
                                           {"id": "m3"}]}], msgs)
        self.assertEqual(res.metrics["saved"], 2)
        self.assertEqual(res.metrics["excluded_personal"], 1)
        files = sorted(p.name for p in (self.inbox / "email").glob("*.json"))
        self.assertTrue(any("m1" in f for f in files))
        self.assertFalse(any("m2" in f for f in files))      # never written

    def test_cursor_advances_to_max_internaldate(self):
        msgs = [gmail_msg("m1", "a@adelye.com", internal="1750000000000"),
                gmail_msg("m2", "b@adelye.com", internal="1760000000000")]
        self.run_poll([{"messages": [{"id": "m1"}, {"id": "m2"}]}], msgs)
        with db.connect(self.db_path) as conn:
            row = conn.execute("SELECT value FROM sync_cursors WHERE name=?",
                               (ep.CURSOR,)).fetchone()
        self.assertEqual(row["value"], "1760000000000")

    def test_no_new_mail_skips(self):
        res = self.run_poll([{"messages": []}], [])
        self.assertEqual(res.status, "skipped_empty")


class TestTokenGate(unittest.TestCase):
    def test_missing_token_raises_actionable(self):
        with mock.patch.object(ep, "TOKEN_FILE", Path("/nonexistent/gmail.json")):
            with self.assertRaises(RuntimeError) as cm:
                ep.access_token()
        self.assertIn("GATED", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
