"""The progress email (Mon/Wed/Fri 08:30 and Sunday's week; Abhijeet,
2026-10-09: "monday wed friday and sunday").

Pins: what it counts since the last email (sent, opened, wrote back by WHEN it
happened, connects against the $150-a-week guide that is tracked not
enforced), money only on a GREEN doctor, the test's versions, the
conversations where the client wrote last, the scanner; the blockers lead the
subject; the window falls back to the natural gap; once a day at most; shadow
records an intent instead of sending; it never names anyone but him as a
recipient (notify.send_gmail sends to his own inbox).
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db, runner
from modules.upwork import progress_email as pe

L = {
    "scanner": {"days": [["2026-10-07", 200, 120, 10, 2], ["2026-10-01", 300, 200, 20, 3]],
                "hot": [["j1", "2026-10-07", 30, "Airtable", "p1"], ["j2", "2026-10-08", 22, "Make", None],
                        ["j0", "2026-10-01", 25, "Old", None]]},
    "talks": {"p2": {"lf": "them", "l": "2026-10-06T10:00:00", "rm": "room_2"},
              "p3": {"lf": "us", "l": "2026-10-06T10:00:00", "rm": "room_3"}},
    "tests": [{"title": "T1", "status": "running", "arms": [{"arm": "A", "label": "As today"},
                                                           {"arm": "B", "label": "Client first"}],
               "scoreboard": {"verdict": "too early", "arms": {"A": {"sent": 1, "opened": 1, "replied": 0, "hired": 0},
                                                               "B": {"sent": 2, "opened": 1, "replied": 1, "hired": 0}}}}],
    "clients": {"blocked": False, "rows": [
        {"name": "Acme", "origin": {"upwork": True}, "first_paid": "2026-10-08", "stage": "paying"},
        {"name": "Old Co", "origin": {"upwork": True}, "first_paid": "2025-01-01", "stage": "stopped"}],
        "winback": {"top": [{"name": "Old Co", "why": ["$5,000 paid in all", "stopped 200 days ago"], "draft": None}]}},
    "agents": {"blockers": [{"words": "The Upwork sign-in has expired.", "fix": "run the auth",
                             "since": "2026-10-08T16:15:05+05:30"}]},
}
D = {
    "facts": [
        {"id": "p1", "k": "cold", "sent": "2026-10-07T10:00:00Z", "op": 1, "od": "2026-10-08T09:00:00Z",
         "rep": 0, "hired": 0, "cx": 120, "cb": 20, "boost": 1, "bo": "charged", "title": "Airtable", "co": "Acme"},
        {"id": "p2", "k": "cold", "sent": "2026-10-01T10:00:00Z", "op": 1, "od": "2026-10-02T09:00:00Z",
         "rep": 1, "rl": 150.0, "hired": 0, "cx": 30, "cb": 30, "boost": 0, "title": "Voice AI", "co": "Bee Ltd"},
        {"id": "p3", "k": "invite", "sent": "2026-10-08T10:00:00Z", "op": None, "rep": 1, "rl": 1.0,
         "hired": 0, "cx": 0, "cb": 0, "boost": 0, "title": "Invite job", "co": "Cee"},
    ],
    "money": {"blocked": False, "price_per_connect": 0.15,
              "clients": {"acme": {"name": "Acme", "rows": [{"d": "2026-10-08", "usd": 500.0},
                                                             {"d": "2026-09-01", "usd": 100.0}]}}},
}


class TestCompose(unittest.TestCase):
    def test_counts_since_the_last_email_by_when_it_happened(self):
        p = pe.compose(L, D, "2026-10-07", "2026-10-09")
        self.assertEqual((p["sent_cold"], p["sent_invite"]), (1, 1))
        self.assertEqual(p["opened"], 1)                 # p1 opened on the 8th; p2's open was on the 2nd
        self.assertEqual(p["replied"], 2)                # p2 sent on the 1st, answered 150h later (the 7th); p3
        self.assertEqual(p["connects"], 120)             # p1's boost was charged; p3 cost nothing
        self.assertEqual(p["connects_usd"], 18.0)
        self.assertEqual(p["guide_usd"], round(150 * 2 / 7, 2))
        self.assertEqual(p["money_in"], 500.0)
        self.assertEqual(p["new_clients"], ["Acme"])
        self.assertEqual((p["hot"], p["hot_applied"], p["read"]), (2, 1, 200))
        self.assertEqual([w["co"] for w in p["waiting"]], ["Bee Ltd"])
        self.assertEqual(p["tests"][0]["arms"][1]["replied"], 1)
        self.assertTrue(p["subject"].startswith("Upwork pipeline, Fri 09 Oct: 1 thing stopping the pipeline"))
        self.assertEqual(p["blockers"][0]["since"], "2026-10-08 16:15 IST")
        html = pe.render_html(p)
        for s in ("Bee Ltd", "Old Co", "too early", "tracked, not a cap", "$500"):
            self.assertIn(s, html)

    def test_no_money_on_a_red_doctor(self):
        red = dict(D, money={"blocked": True})
        p = pe.compose(dict(L, clients={"blocked": True}), red, "2026-10-07", "2026-10-09")
        self.assertNotIn("money_in", p)
        self.assertIsNone(p["connects_usd"])
        self.assertNotIn("$500", pe.render_html(p))

    def test_html_escapes_what_it_quotes(self):
        bad = dict(D, facts=[dict(D["facts"][1], title="<script>x</script>", rl=200.0)])
        p = pe.compose(L, bad, "2026-10-07", "2026-10-09")
        self.assertNotIn("<script>", pe.render_html(p))


class TestRun(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="prog-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.p = [mock.patch.object(config, "LOG_DIR", self.tmp / "logs"),
                  mock.patch.object(config, "LOCK_DIR", self.tmp / "locks")]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def go(self, shadow, sent):
        with mock.patch.dict(config.SHADOW_MODE, {"upwork": shadow}), \
                mock.patch("modules.cockpit.lifecycle.lifecycle", return_value=L), \
                mock.patch("modules.cockpit.proposal_funnel.funnel", return_value=D):
            return runner.run_task(pe.TASK_NAME,
                                   lambda ctx: pe.run(ctx, _send=lambda s, h: sent.append(s), _today="2026-10-09"),
                                   module="upwork", db_path=self.db_path)

    def test_live_sends_once_a_day(self):
        sent = []
        r1 = self.go(False, sent)
        r2 = self.go(False, sent)
        self.assertEqual((r1.status, r2.status), ("completed", "skipped_empty"))
        self.assertEqual(len(sent), 1)

    def test_shadow_records_an_intent_and_sends_nothing(self):
        sent = []
        r = self.go(True, sent)
        self.assertEqual(r.metrics["sent"], "shadow")
        self.assertEqual(sent, [])
        with db.connect(self.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM shadow_ledger WHERE task_name=?", (pe.TASK_NAME,)).fetchone()[0]
        self.assertEqual(n, 1)

    def test_the_window_falls_back_to_the_natural_gap(self):
        with db.connect(self.db_path) as conn:
            self.assertEqual(pe.since_last(conn, "2026-10-09"), "2026-10-07")   # Friday: since Wednesday
            self.assertEqual(pe.since_last(conn, "2026-10-12"), "2026-10-09")   # Monday: since Friday
            self.assertEqual(pe.since_last(conn, "2026-10-11"), "2026-10-04")   # Sunday: the week


if __name__ == "__main__":
    unittest.main()
