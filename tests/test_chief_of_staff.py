"""M4 Chief of Staff tests. Phase 0: the nudge engine — dedup, decay (TTL +
past-due + commitment-never), state transitions, urgency ranking, snooze
visibility. No network; pure stdlib on a temp DB."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from unittest import mock

from core import config, db, runner
from modules.chief_of_staff import nudges, rules, sweep


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


# ── rules emit expected candidates from fixtures ─────────────────────────────
class TestRules(CoSCase):
    def seed_owed(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, room_name, latest_message_dt)"
                         " VALUES ('t1','Acme chat','2026-06-18T10:00:00Z')")
            conn.execute("INSERT INTO drafts (thread_id, draft_kind, body,"
                         " sent_status) VALUES ('t1','followup','ping','pending')")

    def test_owed_reply_fires_after_idle(self):
        self.seed_owed()
        c = rules.rule_owed_replies(self.db_path, dt(d=22))   # 4d idle >= 1
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["dedup_key"], "followup:t1")
        self.assertEqual(c[0]["kind"], "owed_reply")

    def test_owed_reply_silent_when_fresh(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, latest_message_dt) VALUES"
                         " ('t2','2026-06-22T09:00:00Z')")
            conn.execute("INSERT INTO drafts (thread_id, draft_kind, sent_status)"
                         " VALUES ('t2','followup','pending')")
        self.assertEqual(rules.rule_owed_replies(self.db_path, dt(d=22)), [])

    def test_hotlead_no_loom(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO scored_jobs (id,title,score,loom_flag,status,"
                         "fixed_budget,first_scored_at) VALUES ('j1','Big build',"
                         "26,1,'Scored',8000,'2026-06-22T08:00:00')")
        c = rules.rule_hotlead_no_loom(self.db_path, dt())
        self.assertEqual(c[0]["dedup_key"], "hotlead_no_loom:j1")
        self.assertEqual(c[0]["money_at_stake"], 8000)
        self.assertEqual(c[0]["score"], 26)

    def test_won_no_outcome(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO proposals (id,status,job_title,modified_dt) VALUES"
                         " ('p1','Won','Vapi build','2026-06-20'),"
                         " ('p2','Won','x','2026-06-20')")
            conn.execute("UPDATE proposals SET status_reason='referral' WHERE id='p2'")
        c = rules.rule_won_no_outcome(self.db_path, dt())
        self.assertEqual([x["dedup_key"] for x in c], ["won_outcome:p1"])

    def test_lost_pattern_by_category(self):
        with db.connect(self.db_path) as conn:
            for i in range(3):
                conn.execute("INSERT INTO scored_jobs (id,category) VALUES (?, 'n8n')",
                             (f"j{i}",))
                conn.execute("INSERT INTO proposals (id,status,marketplace_job_id)"
                             " VALUES (?,?,?)", (f"p{i}", "Expired", f"j{i}"))
        c = rules.rule_lost_pattern(self.db_path, dt())
        self.assertEqual(c[0]["dedup_key"], "lost_pattern:n8n")
        self.assertEqual(c[0]["payload"]["losses"], 3)

    def test_stale_dossier_unextracted_sources(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO sources (source_type,source_ref,content_hash,"
                         "client_id,status) VALUES ('fathom','f/1','h1','adelye',"
                         "'ingested'),('fathom','f/2','h2','adelye','ingested')")
        c = rules.rule_stale_dossier(self.db_path, dt())
        self.assertEqual(c[0]["dedup_key"], "stale_dossier:adelye")
        self.assertEqual(c[0]["payload"]["ingested"], 2)

    def test_system_failed_run_and_spend(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO runs (task_name,started_at,status) VALUES"
                         " ('upwork_scan','2026-06-22T11:00:00','failed')")
            conn.execute("INSERT INTO claude_usage (called_at,ist_date,task_name,"
                         "model,billing_route,total_cost_usd) VALUES"
                         " ('x','2026-06-22','cos_sweep','claude-opus-4-8','payg',4.6)")
        c = rules.rule_system(self.db_path, dt(hh=12))
        keys = {x["dedup_key"] for x in c}
        self.assertIn("system:run:1", keys)
        self.assertTrue(any(k.startswith("system:spend:") for k in keys))


# ── sweep: empty-skip + Opus-on-change ───────────────────────────────────────
class TestSweep(CoSCase):
    def _ctx(self):
        from types import SimpleNamespace
        return SimpleNamespace(db_path=self.db_path)

    def seed_one_candidate(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO scored_jobs (id,title,score,loom_flag,status,first_scored_at)"
                         " VALUES ('j1','Lead',24,1,'Scored','2026-06-22T08:00:00')")

    def test_empty_skip_no_opus_when_nothing(self):
        spy = mock.Mock(return_value={"correlations": [], "priorities": [], "narrative": ""})
        with self.assertRaises(runner.TaskSkip):
            sweep.run_sweep(self._ctx(), _opus=spy, now=dt())
        spy.assert_not_called()                         # no active -> no Opus

    def test_opus_fires_on_first_material_change(self):
        self.seed_one_candidate()
        spy = mock.Mock(return_value={"correlations": [], "priorities": ["hotlead_no_loom:j1"], "narrative": "one lead"})
        out = sweep.run_sweep(self._ctx(), _opus=spy, now=dt())
        spy.assert_called_once()
        self.assertEqual(out["candidates"], 1)

    def test_second_sweep_unchanged_empty_skips(self):
        self.seed_one_candidate()
        spy = mock.Mock(return_value={"correlations": [], "priorities": [], "narrative": ""})
        sweep.run_sweep(self._ctx(), _opus=spy, now=dt(hh=12))      # fires
        with self.assertRaises(runner.TaskSkip):
            sweep.run_sweep(self._ctx(), _opus=spy, now=dt(hh=14))  # unchanged
        spy.assert_called_once()                        # Opus only once

    def test_change_refires_opus(self):
        self.seed_one_candidate()
        spy = mock.Mock(return_value={"correlations": [], "priorities": [], "narrative": ""})
        sweep.run_sweep(self._ctx(), _opus=spy, now=dt(hh=12))
        with db.connect(self.db_path) as conn:           # new candidate appears
            conn.execute("INSERT INTO scored_jobs (id,title,score,loom_flag,"
                         "status,first_scored_at) VALUES ('j2','Lead2',23,1,"
                         "'Scored','2026-06-22T08:00:00')")
        sweep.run_sweep(self._ctx(), _opus=spy, now=dt(hh=14))
        self.assertEqual(spy.call_count, 2)

    def test_opus_correlations_become_nudges(self):
        self.seed_one_candidate()
        spy = mock.Mock(return_value={"correlations": [
            {"kind": "escalate_client", "dept": "sales", "title": "Escalate Acme",
             "action": "call", "dedup_key": "escalate_client:acme", "rationale": "quiet"}],
            "priorities": [], "narrative": "x"})
        out = sweep.run_sweep(self._ctx(), _opus=spy, now=dt())
        self.assertEqual(out["correlations"], 1)
        row = self.rows("SELECT * FROM nudges WHERE dedup_key='escalate_client:acme'")
        self.assertEqual(row[0]["source"], "sweep")

    def test_vanished_condition_clears(self):
        self.seed_one_candidate()
        spy = mock.Mock(return_value={"correlations": [], "priorities": [], "narrative": ""})
        sweep.run_sweep(self._ctx(), _opus=spy, now=dt(hh=12))
        with db.connect(self.db_path) as conn:           # job acted on -> no longer candidate
            conn.execute("UPDATE scored_jobs SET clickup_task_id='c1' WHERE id='j1'")
        try:
            sweep.run_sweep(self._ctx(), _opus=spy, now=dt(hh=14))
        except runner.TaskSkip:
            pass
        r = self.rows("SELECT state FROM nudges WHERE dedup_key='hotlead_no_loom:j1'")
        self.assertEqual(r[0]["state"], "decayed")       # condition gone


# ── guard: nothing in the sweep path auto-sends a client message ─────────────
class TestNoAutoSend(CoSCase):
    def test_no_send_calls_in_module(self):
        import inspect
        from modules.chief_of_staff import nudges as n, rules as r, sweep as s
        for mod in (n, r, s):
            src = inspect.getsource(mod)
            for bad in ("smtplib", "sendmail", "send_gmail", ".send("):
                self.assertNotIn(bad, src, f"{mod.__name__} must not send")


if __name__ == "__main__":
    unittest.main()
