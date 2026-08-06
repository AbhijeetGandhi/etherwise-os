"""Cockpit HTTP round-trip for the mapping queue routes (Jarvis J1 D3).

Real ThreadingHTTPServer on an ephemeral port so token auth + route dispatch are
genuinely exercised. Only the git-free `ignore` action is driven over HTTP (assign
writes the real registry — covered at the unit level in test_jarvis_queue).
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from core import db
from modules.cockpit import server

TOKEN = "queue-test-token"


class QueueHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = Path(self.tmp.name) / "t.db"
        db.migrate(self.dbp)
        with db.connect(self.dbp) as conn:
            conn.execute(
                "INSERT INTO events (id, source, native_id, author, subject,"
                " state, text, permalink, proposed_client_id, proposal_confidence,"
                " proposal_evidence) VALUES ('evt:email:h1','email','h1',"
                " 'jane@newco.com','Hello','queued','body','http://link',"
                " 'adelye',0.7,'domain match')")
        self.srv = server.make_server("127.0.0.1", 0, self.dbp, TOKEN)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.srv.shutdown()
        self.tmp.cleanup()

    def _req(self, path, body=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data,
                                     headers={"X-Cockpit-Token": TOKEN})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())

    def test_get_queue(self):
        status, d = self._req("/api/queue")
        self.assertEqual(status, 200)
        self.assertEqual(d["queued_total"], 1)
        self.assertEqual(d["counts"]["queued"], 1)
        self.assertTrue(any(g["proposed_client_id"] == "adelye" for g in d["groups"]))
        self.assertTrue(len(d["clients"]) >= 60)      # the real registry

    def test_unauth_rejected(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/queue")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 401)

    def test_post_resolve_ignore(self):
        status, d = self._req("/api/queue/resolve",
                              {"event_id": "evt:email:h1", "action": "ignore",
                               "reason": "not a client"})
        self.assertEqual(status, 200)
        self.assertTrue(d["ok"])
        with db.connect(self.dbp) as conn:
            r = conn.execute("SELECT state FROM events WHERE id='evt:email:h1'"
                             ).fetchone()
        self.assertEqual(r["state"], "excluded")

    def test_post_resolve_bad_action(self):
        status, d = self._req("/api/queue/resolve",
                              {"event_id": "x", "action": "delete_everything"})
        self.assertIn("error", d)


if __name__ == "__main__":
    import urllib.error
    unittest.main()
