"""M4 Chief of Staff tests. Phase 0: the nudge engine — dedup, decay (TTL +
past-due + commitment-never), state transitions, urgency ranking, snooze
visibility. No network; pure stdlib on a temp DB."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from core import config, db
from modules.chief_of_staff import nudges


def dt(y=2026, m=6, d=22, hh=12, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=config.TZ)


class CoSCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cos-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self, sql, args=()):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, args)]

    def nudge(self, **kw):
        base = dict(source="sales", dept="sales", kind="owed_reply",
                    title="Reply to X", dedup_key="followup:t1", now=dt())
        base.update(kw)
        return nudges.write_nudge(self.db_path, **base)


# ── dedup: one ACTIVE nudge per dedup_key ────────────────────────────────────
class TestDedup(CoSCase):
    def test_refire_refreshes_not_duplicates(self):
        a = self.nudge()
        b = self.nudge(title="Reply to X (ping 2)")
        self.assertEqual(a["op"], "created")
        self.assertEqual(b["op"], "refreshed")
        self.assertEqual(a["id"], b["id"])
        active = self.rows("SELECT * FROM nudges WHERE state IN ('open','snoozed')")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["title"], "Reply to X (ping 2)")

    def test_done_then_refire_creates_new(self):
        a = self.nudge()
        nudges.set_state(self.db_path, state="done", dedup_key="followup:t1")
        c = self.nudge()                       # condition recurred
        self.assertEqual(c["op"], "created")
        self.assertNotEqual(a["id"], c["id"])
        self.assertEqual(len(self.rows("SELECT * FROM nudges")), 2)  # history kept
        self.assertEqual(len(self.rows(
            "SELECT * FROM nudges WHERE state='open'")), 1)

    def test_active_unique_index_backstops(self):
        self.nudge()
        # a direct 2nd active insert with the same dedup_key must be rejected
        with self.assertRaises(Exception):
            with db.connect(self.db_path) as conn:
                conn.execute("INSERT INTO nudges (source,dept,kind,title,"
                             "dedup_key,state) VALUES ('s','sales','owed_reply',"
                             "'dup','followup:t1','open')")


# ── state transitions ─────────────────────────────────────────────────────────
class TestState(CoSCase):
    def test_done_snooze_dismiss(self):
        self.nudge()
        nudges.set_state(self.db_path, state="snoozed", dedup_key="followup:t1",
                         snooze_until="2026-06-25T00:00:00", note="later")
        r = self.rows("SELECT * FROM nudges")[0]
        self.assertEqual(r["state"], "snoozed")
        self.assertEqual(r["snooze_until"], "2026-06-25T00:00:00")
        self.assertEqual(r["note"], "later")
        nudges.set_state(self.db_path, state="done", dedup_key="followup:t1")
        self.assertEqual(self.rows("SELECT state FROM nudges")[0]["state"], "done")

    def test_clear_decays(self):
        self.nudge()
        nudges.clear(self.db_path, "followup:t1")
        r = self.rows("SELECT * FROM nudges")[0]
        self.assertEqual(r["state"], "decayed")
        self.assertIsNotNone(r["decayed_at"])

    def test_invalid_state_raises(self):
        with self.assertRaises(ValueError):
            nudges.set_state(self.db_path, state="bogus", dedup_key="x")


# ── decay: TTL + past-due + commitment-never ─────────────────────────────────
class TestDecay(CoSCase):
    def test_owed_reply_decays_after_ttl(self):
        self.nudge(now=dt(d=10))               # owed_reply TTL = 7d
        out = nudges.decay_expired(self.db_path, now=dt(d=22))  # 12d later
        self.assertEqual(out["decayed"], 1)
        self.assertEqual(self.rows("SELECT state FROM nudges")[0]["state"],
                         "decayed")

    def test_within_ttl_survives(self):
        self.nudge(now=dt(d=20))
        out = nudges.decay_expired(self.db_path, now=dt(d=22))  # 2d < 7d
        self.assertEqual(out["decayed"], 0)

    def test_commitment_never_decays(self):
        self.nudge(kind="commitment_due", dedup_key="commitment:9",
                   now=dt(y=2026, m=1, d=1))   # ancient
        out = nudges.decay_expired(self.db_path, now=dt(d=22))
        self.assertEqual(out["decayed"], 0)    # TTL None

    def test_deadline_clears_once_past_due(self):
        self.nudge(kind="deadline", dedup_key="payroll:2026-06",
                   due="2026-06-20T00:00:00", now=dt(d=18))
        self.assertEqual(nudges.decay_expired(self.db_path, now=dt(d=19))
                         ["decayed"], 0)        # before due
        self.assertEqual(nudges.decay_expired(self.db_path, now=dt(d=21))
                         ["decayed"], 1)        # past due


# ── urgency ranking ───────────────────────────────────────────────────────────
class TestUrgency(CoSCase):
    def test_base_kind_orders(self):
        self.nudge(kind="stale_dossier", dedup_key="d1", dept="knowledge")
        self.nudge(kind="commitment_due", dedup_key="d2")
        ranked = nudges.active_nudges(self.db_path, now=dt())
        self.assertEqual(ranked[0]["kind"], "commitment_due")  # base 100 > 20

    def test_money_stake_lifts(self):
        self.nudge(kind="hotlead_no_loom", dedup_key="lo", title="low")
        self.nudge(kind="hotlead_no_loom", dedup_key="hi", title="high",
                   money_at_stake=20000)
        ranked = nudges.active_nudges(self.db_path, now=dt())
        self.assertEqual(ranked[0]["title"], "high")           # 3x stake

    def test_overdue_bump(self):
        u_due = nudges.compute_urgency("deadline", refreshed_at="2026-06-22T12:00:00",
                                       now=dt(), overdue=False)
        u_over = nudges.compute_urgency("deadline", refreshed_at="2026-06-22T12:00:00",
                                        now=dt(), overdue=True)
        self.assertAlmostEqual(u_over, u_due * 1.5, places=2)

    def test_recency_decays_with_age(self):
        fresh = nudges.compute_urgency("owed_reply", refreshed_at="2026-06-22T12:00:00",
                                       now=dt())
        old = nudges.compute_urgency("owed_reply", refreshed_at="2026-06-15T12:00:00",
                                     now=dt())  # 7d = one half-life
        self.assertAlmostEqual(old, fresh / 2, places=1)

    def test_top_n(self):
        for i in range(5):
            self.nudge(kind="owed_reply", dedup_key=f"f{i}", money_at_stake=i * 1000)
        self.assertEqual(len(nudges.top_n(self.db_path, now=dt())),
                         config.COS_BRIEF_TOPN)


# ── snooze visibility ─────────────────────────────────────────────────────────
class TestSnooze(CoSCase):
    def test_snoozed_hidden_until_wake(self):
        self.nudge()
        nudges.set_state(self.db_path, state="snoozed", dedup_key="followup:t1",
                         snooze_until="2026-06-25T00:00:00")
        self.assertEqual(len(nudges.active_nudges(self.db_path, now=dt(d=23))), 0)
        self.assertEqual(len(nudges.active_nudges(self.db_path, now=dt(d=26))), 1)


if __name__ == "__main__":
    unittest.main()
