"""M4 Chief of Staff tests. Phase 0: the nudge engine — dedup, decay (TTL +
past-due + commitment-never), state transitions, urgency ranking, snooze
visibility. No network; pure stdlib on a temp DB."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from unittest import mock

from core import config, db, runner
from core import claude_gateway as gw
from modules.chief_of_staff import nudges, rules, sweep, commitments


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

    def test_sweep_runs_commitment_extraction(self):
        self.seed_one_candidate()
        spy = mock.Mock(return_value={"correlations": [], "priorities": [], "narrative": ""})
        cspy = mock.Mock(return_value={"sources": 0, "inserted": 0})
        sweep.run_sweep(self._ctx(), _opus=spy, _commitments=cspy, now=dt())
        cspy.assert_called_once()

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


# ── commitments: Sonnet extraction (mocked) + nag-only-ours ──────────────────
class TestCommitments(CoSCase):
    def seed_source(self, text="We'll have the register done Friday.", sid_ref="fathom/x"):
        with db.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id, occurred_dt, status) VALUES ('fathom',?,?,'adelye',"
                "'2026-06-20T10:00:00','extracted')", (sid_ref, sid_ref))
            sid = cur.lastrowid
            conn.execute("INSERT INTO source_fts (text, source_id) VALUES (?,?)",
                         (text, sid))
        return sid

    def gw_result(self, commitments):
        return gw.GatewayResult(
            text=json.dumps({"commitments": commitments}),
            parsed={"commitments": commitments}, model="claude-sonnet-4-6",
            route="payg", usage={}, cost_usd=0.01, stop_reason="end_turn",
            duration_ms=5)

    def test_extract_persists_cited_due_dated(self):
        sid = self.seed_source()
        payload = [
            {"who": "us", "text": "Deliver the mythical register",
             "due": "2026-06-26", "quote": "register done Friday"},
            {"who": "client", "text": "Provide TechEdge API access",
             "due": None, "quote": "I'll get you access"},
        ]
        with mock.patch.object(commitments.runner, "claude_call",
                               return_value=self.gw_result(payload)):
            out = commitments.extract_from_source(self.db_path, sid)
        self.assertEqual(out["inserted"], 2)
        rows = self.rows("SELECT * FROM commitments ORDER BY who")
        self.assertEqual(rows[1]["who"], "us")
        self.assertEqual(rows[1]["due"], "2026-06-26")
        self.assertIn("fathom/x @", rows[1]["source_cite"])   # cited
        self.assertEqual(rows[0]["who"], "client")

    def test_extract_idempotent(self):
        sid = self.seed_source()
        payload = [{"who": "us", "text": "Deliver register", "due": "2026-06-26",
                    "quote": "register done Friday"}]
        with mock.patch.object(commitments.runner, "claude_call",
                               return_value=self.gw_result(payload)):
            commitments.extract_from_source(self.db_path, sid)
            again = commitments.extract_from_source(self.db_path, sid)
        self.assertEqual(again["inserted"], 0)                # dedup
        self.assertEqual(len(self.rows("SELECT * FROM commitments")), 1)

    def test_drops_uncited(self):
        sid = self.seed_source()
        payload = [{"who": "us", "text": "vague thing", "due": None, "quote": ""}]
        with mock.patch.object(commitments.runner, "claude_call",
                               return_value=self.gw_result(payload)):
            out = commitments.extract_from_source(self.db_path, sid)
        self.assertEqual(out["inserted"], 0)                  # no quote anchor

    def test_rule_nags_only_ours(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO commitments (who,text,due,source_cite,"
                         "dedup_key,state) VALUES"
                         " ('us','ours due','2026-06-23','c1','k1','open'),"
                         " ('client','theirs due','2026-06-23','c2','k2','open')")
        c = rules.rule_commitments_due(self.db_path, dt(d=22))
        self.assertEqual([x["title"] for x in c], ["Commitment due: ours due"])

    def test_extract_new_advances_cursor(self):
        s1 = self.seed_source(sid_ref="fathom/a")
        s2 = self.seed_source(sid_ref="fathom/b")
        calls = []
        def fake(db_path, sid, **kw):
            calls.append(sid)
            return {"inserted": 1}
        out = commitments.extract_new(self.db_path, _extract=fake)
        self.assertEqual(calls, [s1, s2])
        self.assertEqual(out["cursor"], s2)
        # re-run: cursor advanced, nothing new
        calls.clear()
        commitments.extract_new(self.db_path, _extract=fake)
        self.assertEqual(calls, [])


# ── briefs: full-digest render + draft-only ──────────────────────────────────
class TestBrief(CoSCase):
    def seed_state(self):
        # 4 nudges across depts + a due-soon and an overdue commitment
        for k, dk, dept in [("commitment_due", "c:1", "knowledge"),
                            ("owed_reply", "f:1", "sales"),
                            ("hotlead_no_loom", "h:1", "sales"),
                            ("system", "s:1", "system")]:
            nudges.write_nudge(self.db_path, source=dept, dept=dept, kind=k,
                               title=f"{k} item", dedup_key=dk, now=dt())
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO commitments (who,text,due,source_cite,"
                         "dedup_key,state) VALUES"
                         " ('us','Ship the register','2026-06-20','cite1','k1','open'),"
                         " ('us','Send the POC','2026-06-23','cite2','k2','open'),"
                         " ('client','Grant access',NULL,'cite3','k3','open')")

    def _narr(self, db_path, brief):
        return {"headline": "AM brief test", "narrative": "Two things matter."}

    def test_build_brief_digest_and_top3(self):
        self.seed_state()
        from modules.chief_of_staff import brief
        b = brief.build_brief(self.db_path, "am", now=dt(d=22))
        self.assertEqual(len(b["top"]), config.COS_BRIEF_TOPN)
        self.assertEqual(b["top"][0]["kind"], "commitment_due")     # highest base
        self.assertEqual(len(b["commitments"]["overdue"]), 1)       # 2026-06-20 < 22
        self.assertEqual(len(b["commitments"]["due_soon"]), 1)      # 2026-06-23
        self.assertEqual(b["commitments"]["awaiting_client"], 1)

    def test_write_brief_creates_draft_no_send(self):
        self.seed_state()
        from modules.chief_of_staff import brief
        with mock.patch.object(config, "COS_BRIEFS_DIR", self.tmp / "briefs"):
            b = brief.write_brief(self.db_path, "am", now=dt(d=22),
                                  _narrate=self._narr)
        self.assertTrue(Path(b["path"]).exists())
        body = Path(b["path"]).read_text()
        self.assertIn("DRAFT", body)
        self.assertIn("commitment_due item", body)                  # digest rendered
        self.assertIn("OVERDUE", body)

    # ── source health reaches Abhijeet through the brief ─────────────────────
    # bin/doctor is pull-based; the brief is what actually lands in front of
    # him. The line is ALWAYS rendered so a missing line means the check broke,
    # not that everything is fine.
    def _brief_with_sources(self, report):
        from modules.chief_of_staff import brief
        b = brief.build_brief(self.db_path, "am", now=dt(d=22))
        b["sources"] = report
        return b

    def test_healthy_sources_render_a_compact_always_present_line(self):
        from modules.chief_of_staff import brief
        b = self._brief_with_sources(
            {"ok": True, "headline": "all 6 sources fresh", "lines": [],
             "worst_escalation": 0, "count": 6, "unhealthy": 0})
        txt = brief.render_text(b, self._narr(None, None))
        self.assertIn("SOURCES: all 6 sources fresh", txt)
        html_body = brief.render_html(b, self._narr(None, None))
        self.assertIn("all 6 sources fresh", html_body)

    def test_dead_source_is_loud_in_both_renderers(self):
        from modules.chief_of_staff import brief
        b = self._brief_with_sources(
            {"ok": False, "headline": "DEAD — 1 of 6 sources need attention",
             "lines": ["DEAD 6d: Upwork sync has produced nothing since 2026-07-06."],
             "worst_escalation": 3, "count": 6, "unhealthy": 1})
        txt = brief.render_text(b, self._narr(None, None))
        self.assertIn("DEAD — 1 of 6", txt)
        self.assertIn("DEAD 6d: Upwork sync", txt)
        self.assertIn("not current", txt)
        html_body = brief.render_html(b, self._narr(None, None))
        self.assertIn("DEAD 6d: Upwork sync", html_body)
        self.assertIn("#d23f31", html_body)          # escalation 3 -> red, not amber

    def test_level_one_stale_is_amber_not_red(self):
        from modules.chief_of_staff import brief
        b = self._brief_with_sources(
            {"ok": False, "headline": "STALE — 1 of 6 sources need attention",
             "lines": ["STALE: Upwork sync last produced 5h ago."],
             "worst_escalation": 1, "count": 6, "unhealthy": 1})
        html_body = brief.render_html(b, self._narr(None, None))
        self.assertIn("#e8a33d", html_body)
        self.assertNotIn("#d23f31", html_body)

    def test_real_brief_always_carries_a_sources_block(self):
        from modules.chief_of_staff import brief
        b = brief.build_brief(self.db_path, "am", now=dt(d=22))
        self.assertIn("sources", b)
        for k in ("ok", "headline", "lines", "count"):
            self.assertIn(k, b["sources"])
        # and it survives rendering even on an empty DB
        self.assertIn("SOURCES", brief.render_text(b, self._narr(None, None)))

    def test_all_quiet_skips_opus(self):
        from modules.chief_of_staff import brief
        spy = mock.Mock()
        b = brief.build_brief(self.db_path, "pm", now=dt())          # empty db
        narr = brief.narrate.__wrapped__ if hasattr(brief.narrate, "__wrapped__") else None
        # narrate must NOT call the model when nothing to say
        with mock.patch.object(brief.runner, "claude_call", spy):
            out = brief.narrate(self.db_path, b)
        spy.assert_not_called()
        self.assertIn("all quiet", out["headline"])


# ── Quick Capture: add + classify routing ────────────────────────────────────
class TestCapture(CoSCase):
    def test_add_note(self):
        from modules.chief_of_staff import capture
        out = capture.add_note(self.db_path, "follow up with Acme", "terminal")
        self.assertEqual(out["id"], 1)
        r = self.rows("SELECT * FROM notes")[0]
        self.assertEqual(r["state"], "new")
        self.assertEqual(r["source"], "terminal")

    def test_empty_note_raises(self):
        from modules.chief_of_staff import capture
        with self.assertRaises(ValueError):
            capture.add_note(self.db_path, "   ")

    def test_classify_routes_each_bucket(self):
        from modules.chief_of_staff import capture
        capture.add_note(self.db_path, "fix the broken automation")   # nudge
        capture.add_note(self.db_path, "promised client the POC Friday")  # commitment
        capture.add_note(self.db_path, "Acme uses Make + Airtable")   # kb_note
        capture.add_note(self.db_path, "maybe build a Loom library")  # idea
        plan = {1: {"bucket": "nudge", "title": "Fix automation", "action": "do it",
                    "due": None, "who": "us"},
                2: {"bucket": "commitment", "title": "Deliver POC",
                    "action": None, "due": "2026-06-27", "who": "us"},
                3: {"bucket": "kb_note", "title": "Acme stack", "action": None,
                    "due": None, "who": "us"},
                4: {"bucket": "idea", "title": "Loom library", "action": None,
                    "due": None, "who": "us"}}
        out = capture.classify_new(self.db_path,
                                   _classify=lambda db, n: plan[n["id"]], now=dt())
        self.assertEqual((out["nudge"], out["commitment"], out["kb_note"], out["idea"]),
                         (1, 1, 1, 1))
        # nudge + commitment rows created; kb_note/idea stay as notes
        self.assertEqual(len(self.rows("SELECT * FROM nudges WHERE source='capture'")), 1)
        self.assertEqual(len(self.rows("SELECT * FROM commitments")), 1)
        self.assertEqual(len(self.rows("SELECT * FROM notes WHERE state='new'")), 0)
        self.assertEqual(len(capture.ideas(self.db_path)), 1)

    def test_sweep_invokes_capture(self):
        from types import SimpleNamespace
        ospy = mock.Mock(return_value={"correlations": [], "priorities": [], "narrative": ""})
        cap = mock.Mock(return_value={"classified": 0})
        try:                              # empty queue -> TaskSkip after capture runs
            sweep.run_sweep(SimpleNamespace(db_path=self.db_path), _opus=ospy,
                            _commitments=mock.Mock(return_value={}), _capture=cap,
                            now=dt())
        except runner.TaskSkip:
            pass
        cap.assert_called_once()


# ── grouped queue (cockpit/terminal display) ─────────────────────────────────
class TestGroupedQueue(CoSCase):
    def test_groups_high_count_kinds(self):
        for i in range(8):                       # > COS_QUEUE_GROUP_MIN
            self.nudge(kind="hotlead_no_loom", dedup_key=f"h{i}", score=20)
        self.nudge(kind="system", dedup_key="s1", dept="system")
        q = nudges.grouped_queue(self.db_path, now=dt())
        groups = [e for e in q["entries"] if e.get("grouped")]
        singles = [e for e in q["entries"] if not e.get("grouped")]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["count"], 8)
        self.assertEqual(len(groups[0]["items"]), 8)
        self.assertTrue(any(s["kind"] == "system" for s in singles))
        self.assertEqual(q["total"], 9)

    def test_ranked_by_urgency(self):
        self.nudge(kind="stale_dossier", dedup_key="d", dept="knowledge")
        self.nudge(kind="commitment_due", dedup_key="c")
        q = nudges.grouped_queue(self.db_path, now=dt())
        self.assertEqual(q["entries"][0]["kind"], "commitment_due")


# ── hot-lead interrupts (shadow-gated) ───────────────────────────────────────
class TestInterrupts(CoSCase):
    class Ctx:
        def __init__(self, db_path, shadow):
            self.db_path, self.shadow = db_path, shadow
            self.writes = []

        def record_shadow_write(self, **kw):
            self.writes.append(kw)

    def seed_lead(self, jid="101", score=24):    # numeric id (prod shape)
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO scored_jobs (id,title,score,status,"
                         "first_scored_at) VALUES (?,?,?,'Scored',"
                         "'2026-06-22T08:00:00')", (jid, f"Lead {jid}", score))

    def test_shadow_records_no_send(self):
        from modules.chief_of_staff import interrupts
        self.seed_lead()
        ctx = self.Ctx(self.db_path, shadow=True)
        send = mock.Mock()
        out = interrupts.check_hot_leads(ctx, now=dt(), _send=send)
        self.assertEqual(out["drafted_shadow"], 1)
        self.assertEqual(out["sent"], 0)
        send.assert_not_called()                 # HOLD in shadow
        self.assertEqual(ctx.writes[0]["target"], "email")
        self.assertEqual(ctx.writes[0]["entity"], "hot_lead_interrupt")

    def test_live_sends(self):
        from modules.chief_of_staff import interrupts
        self.seed_lead()
        ctx = self.Ctx(self.db_path, shadow=False)
        send = mock.Mock()
        out = interrupts.check_hot_leads(ctx, now=dt(), _send=send)
        self.assertEqual(out["sent"], 1)
        send.assert_called_once()

    def test_interrupts_once_then_cursor_holds(self):
        from modules.chief_of_staff import interrupts
        self.seed_lead()
        ctx = self.Ctx(self.db_path, shadow=True)
        interrupts.check_hot_leads(ctx, now=dt())
        out2 = interrupts.check_hot_leads(ctx, now=dt())   # cursor advanced
        self.assertEqual(out2["hot_leads"], 0)             # not re-interrupted

    def test_below_threshold_ignored(self):
        from modules.chief_of_staff import interrupts
        self.seed_lead(jid="102", score=10)                # < HOT_LEAD_THRESHOLD
        ctx = self.Ctx(self.db_path, shadow=True)
        out = interrupts.check_hot_leads(ctx, now=dt())
        self.assertEqual(out["hot_leads"], 0)


# ── guard: nothing in the sweep path auto-sends a client message ─────────────
class TestNoAutoSend(CoSCase):
    def test_no_send_calls_in_module(self):
        import inspect
        from modules.chief_of_staff import (nudges as n, rules as r, sweep as s,
                                            commitments as cm, brief as br,
                                            capture as cp)
        for mod in (n, r, s, cm, br, cp):
            src = inspect.getsource(mod)
            for bad in ("smtplib", "sendmail", "send_gmail", ".send("):
                self.assertNotIn(bad, src, f"{mod.__name__} must not send")


if __name__ == "__main__":
    unittest.main()
