"""Card identity + provenance law (Jarvis J1 D4).

Kills the "follow-up ×2" (a follow-up CARD and its owed_reply NUDGE for the same
thread must render as ONE card, rolled forward with an honest age) and enforces
the provenance law (a client-facing card with no evidence link may not render).
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from core import config, db
from modules.chief_of_staff import nudges as eng
from modules.cockpit import data
from tests.test_cockpit import seed_today

NOW = datetime(2026, 6, 15, tzinfo=config.TZ)


class CardIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cards-"))
        self.dbp = self.tmp / "v3.db"
        db.migrate(self.dbp)
        seed_today(self.dbp)
        # a thread with a pending draft (-> follow-up card) whose age is 2 days
        with db.connect(self.dbp) as conn:
            conn.execute(
                "INSERT INTO threads (id, room_name, topic, bucket, tier,"
                " latest_message_dt) VALUES ('rX','AI Voice Coach','AI Voice"
                " Coach','owed','HOT','2026-06-13T09:00:00+00:00')")
            conn.execute(
                "INSERT INTO drafts (thread_id, draft_kind, tier, body, word_count,"
                " sent_status) VALUES ('rX','owed','HOT','Reply',2,'pending')")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_followup_and_owed_reply_collapse_to_one_card(self):
        # the M4 sweep ALSO produces an owed_reply nudge for the same thread.
        # Both sides key on the COUNTERPARTY (nudges.followup_key) — asking the
        # engine for the key rather than hardcoding 'followup:<room>' is what
        # keeps this test honest if the identity rule changes again.
        key = eng.followup_key(None, "AI Voice Coach", "rX")
        eng.write_nudge(self.dbp, source="sales", dept="sales", kind="owed_reply",
                        title="Follow up: AI Voice Coach", dedup_key=key,
                        payload={"idle_days": 2, "thread_id": "rX"}, now=NOW)
        out = data.today(self.dbp, today="2026-06-15")
        for_thread = [q for q in out["queue"]
                      if q.get("item_key", "").endswith(key.split(":", 1)[1])]
        self.assertEqual(len(for_thread), 1)                 # ONE card, not ×2
        self.assertEqual(for_thread[0]["kind"], "followup")   # the actionable one
        self.assertEqual(for_thread[0]["detail"]["age_days"], 2)   # honest age

    def test_two_rooms_one_person_render_as_one_card(self):
        # Rocky Choy held two Upwork rooms; each rendered its own card, so
        # 'Done' on one left the other and the board looked broken
        with db.connect(self.dbp) as conn:
            conn.execute(
                "INSERT INTO threads (id, room_name, topic, bucket, tier,"
                " latest_message_dt) VALUES ('rX2','AI Voice Coach','Second"
                " job','followup','WARM','2026-06-12T09:00:00+00:00')")
            conn.execute(
                "INSERT INTO drafts (thread_id, draft_kind, tier, body,"
                " word_count, sent_status) VALUES ('rX2','followup','WARM',"
                "'Reply',2,'pending')")
        out = data.today(self.dbp, today="2026-06-15")
        cards = [f for f in out["follow_ups"]
                 if f["item_key"] == eng.followup_key(None, "AI Voice Coach", "rX")]
        self.assertEqual(len(cards), 1)
        self.assertEqual(sorted(cards[0]["thread_ids"]), ["rX", "rX2"])
        # both rooms remain reachable — collapsed, not hidden
        q = [c for c in out["queue"] if c.get("item_key") == cards[0]["item_key"]]
        self.assertEqual(len(q[0]["detail"]["thread_urls"]), 2)
        # ...and so do both DRAFTS. Rendering only the head thread's body would
        # make the second room's pre-written reply unreachable from Today,
        # which is hiding work, not collapsing a duplicate row.
        self.assertEqual(sorted(d["thread_id"] for d in q[0]["detail"]["drafts"]),
                         ["rX", "rX2"])
        # the OLDEST thread sets the age (rX2 is a day older than rX)
        self.assertEqual(q[0]["detail"]["age_days"], cards[0]["age_days"])
        self.assertEqual(cards[0]["age_days"], 3)

    def test_no_provenance_client_card_hidden_and_flagged(self):
        # a client-facing nudge with NO evidence link (no commitment row -> no
        # source_cite) must not render, and must raise a loud anomaly
        eng.write_nudge(self.dbp, source="sales", dept="sales",
                        kind="commitment_due", title="Do the thing",
                        dedup_key="commitment:ghost", now=NOW)  # no cite anywhere
        out = data.today(self.dbp, today="2026-06-15")
        keys = [q.get("item_key") for q in out["queue"]]
        self.assertNotIn("nudge:commitment:ghost", keys)     # hidden
        self.assertGreaterEqual(out["queue_hidden_no_provenance"], 1)
        with db.connect(self.dbp) as conn:
            n = conn.execute("SELECT COUNT(*) c FROM anomalies WHERE"
                             " kind='card_no_provenance'").fetchone()["c"]
        self.assertGreaterEqual(n, 1)                         # loud, not silent

    def test_provenance_present_renders(self):
        # a commitment WITH its mandatory source_cite renders normally
        with db.connect(self.dbp) as conn:
            conn.execute(
                "INSERT INTO commitments (id, who, text, due, source_cite,"
                " dedup_key, state) VALUES (1,'client','Ship v2','2026-06-20',"
                " 'fathom/9 @ 3','commitment:1','open')")
        eng.write_nudge(self.dbp, source="sales", dept="sales",
                        kind="commitment_due", title="Ship v2",
                        dedup_key="commitment:1", payload={"client": "Acme"},
                        now=NOW)
        out = data.today(self.dbp, today="2026-06-15")
        keys = [q.get("item_key") for q in out["queue"]]
        self.assertIn("nudge:commitment:1", keys)

    def test_anomaly_flag_is_once_per_day(self):
        eng.write_nudge(self.dbp, source="sales", dept="sales",
                        kind="commitment_due", title="Ghost",
                        dedup_key="commitment:ghost", now=NOW)
        data.today(self.dbp, today="2026-06-15")
        data.today(self.dbp, today="2026-06-15")     # second load, same day
        with db.connect(self.dbp) as conn:
            n = conn.execute("SELECT COUNT(*) c FROM anomalies WHERE"
                             " kind='card_no_provenance'").fetchone()["c"]
        self.assertEqual(n, 1)                        # guarded, not spammed


if __name__ == "__main__":
    unittest.main()
