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

    def test_done_sticks_when_nothing_has_changed(self):
        # THE BUG (found 2026-08-12): a follow-up the founder marked done came
        # back on the very next sweep, forever. Rocky Choy had 10 rows for one
        # dedup_key, Mustafa 11. 'Done' was meaningless and the Today tab
        # stopped being trustworthy. A follow-up must only return when the
        # CLIENT says something new.
        pay = {"thread_id": "t1", "evidence_ts": "2026-08-01T10:00:00"}
        a = self.nudge(payload=pay)
        self.assertEqual(a["op"], "created")
        nudges.set_state(self.db_path, state="done", dedup_key="followup:t1")
        b = self.nudge(payload=pay)             # same thread, nothing new said
        self.assertEqual(b["op"], "suppressed-unchanged")
        self.assertEqual(len(self.rows(
            "SELECT * FROM nudges WHERE state='open'")), 0)

    def test_a_new_client_message_DOES_bring_it_back(self):
        # the other half: suppression must not become a black hole. If the
        # client replies after we marked it done, that is a genuinely live card.
        a = self.nudge(payload={"thread_id": "t1",
                                "evidence_ts": "2026-08-01T10:00:00"})
        nudges.set_state(self.db_path, state="done", dedup_key="followup:t1")
        b = self.nudge(payload={"thread_id": "t1",
                                "evidence_ts": "2099-01-01T10:00:00"})
        self.assertEqual(b["op"], "created")
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(self.rows(
            "SELECT * FROM nudges WHERE state='open'")), 1)

    def test_same_day_done_beats_an_older_message_across_ts_formats(self):
        # timestamps reach this comparison in three shapes. 'T' (0x54) sorts
        # ABOVE ' ' (0x20), so a naive string compare made ANY same-day
        # Upwork message look newer than the founder's SQLite-stamped Done —
        # which put Adam Goldsmith straight back on the board.
        self.assertEqual(nudges.ts_key("2026-07-29T14:00:00Z"),
                         nudges.ts_key("2026-07-29 14:00:00"))
        self.assertLess(nudges.ts_key("2026-07-29T14:00:00Z"),
                        nudges.ts_key("2026-07-29 22:54:11"))
        a = self.nudge(payload={"thread_id": "t1",
                                "evidence_ts": "2026-07-29T14:00:00Z"})
        with db.connect(self.db_path) as conn:      # Done, later the same day
            conn.execute("UPDATE nudges SET state='done',"
                         " updated_at='2026-07-29 22:54:11' WHERE id=?",
                         (a["id"],))
        b = self.nudge(payload={"thread_id": "t1",
                                "evidence_ts": "2026-07-29T14:00:00Z"})
        self.assertEqual(b["op"], "suppressed-unchanged")

    def test_a_reply_inside_the_ist_utc_gap_is_not_swallowed(self):
        # nudges.updated_at is IST-naive (_iso); Upwork message times are UTC.
        # Treating both as the same clock discarded any reply landing in the
        # 5h30m after a Done as "nothing changed".
        a = self.nudge(payload={"thread_id": "t1",
                                "evidence_ts": "2026-08-12T09:00:00Z"})
        with db.connect(self.db_path) as conn:      # Done at 15:30 IST = 10:00Z
            conn.execute("UPDATE nudges SET state='done',"
                         " updated_at='2026-08-12T15:30:00' WHERE id=?",
                         (a["id"],))
        quiet = self.nudge(payload={"thread_id": "t1",       # 09:00Z < 10:00Z
                                    "evidence_ts": "2026-08-12T09:00:00Z"})
        self.assertEqual(quiet["op"], "suppressed-unchanged")
        live = self.nudge(payload={"thread_id": "t1",        # 11:00Z > 10:00Z
                                   "evidence_ts": "2026-08-12T11:00:00Z"})
        self.assertEqual(live["op"], "created")

    def test_owed_reply_rule_publishes_evidence_ts(self):
        # the suppression is only as good as the evidence the rule supplies —
        # if rules.py stops sending evidence_ts the bug silently returns
        from modules.chief_of_staff import rules
        import inspect
        self.assertIn("evidence_ts", inspect.getsource(rules.rule_owed_replies))

    def test_done_then_refire_creates_new_when_no_evidence(self):
        # kinds that supply NO evidence_ts keep the old recurring behaviour —
        # this fix must not silently mute other rules
        a = self.nudge()
        nudges.set_state(self.db_path, state="done", dedup_key="followup:t1")
        c = self.nudge()                       # condition recurred
        self.assertEqual(c["op"], "created")
        self.assertNotEqual(a["id"], c["id"])
        self.assertEqual(len(self.rows("SELECT * FROM nudges")), 2)  # history kept
        self.assertEqual(len(self.rows(
            "SELECT * FROM nudges WHERE state='open'")), 1)

    def test_one_shot_done_is_terminal_not_resurrected(self):
        # a commitment, once done, must NOT come back on the next sweep — the
        # bug that flooded Today with months-old items (2026-07-30)
        w = lambda: nudges.write_nudge(
            self.db_path, source="knowledge", dept="knowledge",
            kind="commitment_due", title="Ship the POC",
            dedup_key="commitment:99", now=dt())
        a = w()
        self.assertEqual(a["op"], "created")
        nudges.set_state(self.db_path, state="done", dedup_key="commitment:99")
        b = w()                                # next sweep re-derives it
        self.assertEqual(b["op"], "suppressed-actioned")
        self.assertEqual(len(self.rows(
            "SELECT * FROM nudges WHERE state='open' AND dedup_key='commitment:99'")), 0)

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
        self.assertEqual(c[0]["dedup_key"], "followup:p:acme-chat")
        self.assertEqual(c[0]["kind"], "owed_reply")

    def test_one_person_two_rooms_is_one_card(self):
        # Upwork opens a room per job. Rocky Choy held two and Dhanya Sangolli
        # two more, so 'Done' on one left its twin behind and read as a bug.
        with db.connect(self.db_path) as conn:
            for i, ts in enumerate(("2026-06-18T10:00:00Z",
                                    "2026-06-19T10:00:00Z")):
                conn.execute("INSERT INTO threads (id, room_name,"
                             " latest_message_dt) VALUES (?,'Rocky Choy',?)",
                             (f"room_r{i}", ts))
                conn.execute("INSERT INTO drafts (thread_id, draft_kind, body,"
                             " sent_status) VALUES (?,'followup','ping',"
                             "'pending')", (f"room_r{i}",))
        c = rules.rule_owed_replies(self.db_path, dt(d=22))
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["dedup_key"], "followup:p:rocky-choy")
        # both rooms are named, and the newest message is the evidence — so
        # acting on the person settles both threads at once
        self.assertEqual(sorted(c[0]["payload"]["thread_ids"]),
                         ["room_r0", "room_r1"])
        self.assertEqual(c[0]["payload"]["evidence_ts"], "2026-06-19T10:00:00Z")

    def test_attributed_thread_is_keyed_and_labelled_by_client(self):
        # Brandi Jordan is The Cradle Company, an active client — an attributed
        # thread must never render as an anonymous cold Upwork room
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, room_name, client_id,"
                         " latest_message_dt) VALUES ('room_b','Brandi Jordan',"
                         "'the-cradle-company','2026-06-18T10:00:00Z')")
            conn.execute("INSERT INTO drafts (thread_id, draft_kind, body,"
                         " sent_status) VALUES ('room_b','followup','p',"
                         "'pending')")
        c = rules.rule_owed_replies(self.db_path, dt(d=22))
        self.assertEqual(c[0]["dedup_key"], "followup:c:the-cradle-company")
        self.assertEqual(c[0]["payload"]["client_id"], "the-cradle-company")
        self.assertNotEqual(c[0]["client"], "Brandi Jordan")

    def test_followup_key_falls_back_to_thread_not_a_blank_slug(self):
        # a nameless room must keep its own identity; merging unrelated people
        # under an empty slug would hide real work
        self.assertEqual(nudges.followup_key(None, "", "room_x"),
                         "followup:room_x")
        self.assertEqual(nudges.followup_key(None, None, "room_x"),
                         "followup:room_x")

    def test_owed_reply_silent_when_fresh(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, latest_message_dt) VALUES"
                         " ('t2','2026-06-22T09:00:00Z')")
            conn.execute("INSERT INTO drafts (thread_id, draft_kind, sent_status)"
                         " VALUES ('t2','followup','pending')")
        self.assertEqual(rules.rule_owed_replies(self.db_path, dt(d=22)), [])

    # ── cross-channel evidence (2026-08-12) ──────────────────────────────────
    # "this cross platform sync is not at all working" — a contracted client
    # with two weeks of live Slack was being minted as a cold Upwork lead
    # because the only signal the rule read was threads.latest_message_dt.

    def seed_client(self, contract_status="ACTIVE"):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, room_name, client_id,"
                         " contract_id, latest_message_dt) VALUES ('room_b',"
                         "'Brandi Jordan','the-cradle-company','k9',"
                         "'2026-06-10T10:00:00Z')")
            conn.execute("INSERT INTO drafts (thread_id, draft_kind, body,"
                         " sent_status) VALUES ('room_b','followup','p',"
                         "'pending')")
            conn.execute("INSERT INTO messages (id, thread_id, created_dt,"
                         " direction) VALUES ('m1','room_b',"
                         "'2026-06-10T10:00:00Z','Inbound')")
            conn.execute("INSERT INTO contracts (id, upwork_status, start_dt,"
                         " thread_id) VALUES ('k9',?,'2026-05-01T00:00:00Z',"
                         "'room_b')", (contract_status,))

    def slack(self, ts, direction):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO events (id, source, native_id, ts,"
                         " client_id, state, direction) VALUES (?,'slack',?,?,"
                         "'the-cradle-company','mapped',?)",
                         (f"evt:slack:{ts}:{direction}", ts, ts, direction))

    def test_an_active_client_is_not_minted_as_a_sales_follow_up(self):
        self.seed_client()
        c = rules.rule_owed_replies(self.db_path, dt(d=22))[0]
        self.assertEqual(c["kind"], "owed_reply")        # identity unchanged
        self.assertTrue(c["title"].startswith("Client comms:"))
        self.assertIn("UNSENT Upwork draft", c["action"])
        self.assertTrue(c["payload"]["engagement"]["active"])
        self.assertEqual(c["payload"]["engagement"]["contract_id"], "k9")

    def test_a_prospect_still_reads_as_a_follow_up(self):
        self.seed_client(contract_status="CLOSED")
        c = rules.rule_owed_replies(self.db_path, dt(d=22))[0]
        self.assertTrue(c["title"].startswith("Follow up:"))
        self.assertFalse(c["payload"]["engagement"]["active"])

    def test_evidence_ts_is_the_max_across_channels(self):
        self.seed_client()
        self.slack("2026-06-20T08:00:00+00:00", "inbound")   # newer than Upwork
        c = rules.rule_owed_replies(self.db_path, dt(d=22))[0]
        self.assertEqual(c["payload"]["evidence_ts"][:10], "2026-06-20")
        self.assertEqual(c["payload"]["idle_days"], 2)       # not 12

    def test_a_counterparty_who_spoke_elsewhere_today_is_not_idle(self):
        # COS_FOLLOWUP_IDLE_DAYS applies to the CONVERSATION, not to one room.
        # Dropping is safe: the sweep decays the nudge, and it re-mints the
        # moment the condition holds again.
        self.seed_client()
        self.slack("2026-06-22T08:00:00+00:00", "inbound")
        self.assertEqual(rules.rule_owed_replies(self.db_path, dt(d=22)), [])

    def test_our_own_slack_reply_is_not_the_client_speaking(self):
        """Our own outbound must never be read as the counterparty speaking —
        `evidence_ts` stays THEIR last word, 06-10."""
        from modules.chief_of_staff import evidence as _ev
        self.seed_client()
        self.slack("2026-06-22T08:00:00+00:00", "outbound")
        with db.connect(self.db_path) as conn:
            ev = _ev.counterparty(conn, thread_ids=["room_b"],
                                  client_id="the-cradle-company")
        self.assertEqual(ev["ts"][:10], "2026-06-10")
        self.assertTrue(ev["answered_since"])
        self.assertEqual(ev["ours_channel"], "slack")

    def test_answering_them_anywhere_retires_the_owed_reply_card(self):
        """2026-08-27, on his "we are still seeing if my reply was pending on
        the old chat": `answered_since` was computed and shown but never
        allowed to SUPPRESS. A reply he had already sent — on another Upwork
        room or on another channel — left the card up, pointing at an unsent
        draft for a conversation that had moved on."""
        self.seed_client()
        self.assertEqual(len(rules.rule_owed_replies(self.db_path, dt(d=22))), 1)
        self.slack("2026-06-22T08:00:00+00:00", "outbound")   # he answered
        self.assertEqual(rules.rule_owed_replies(self.db_path, dt(d=22)), [])

    def test_an_unattributed_thread_admits_it_only_saw_upwork(self):
        self.seed_owed()          # no client_id -> no cross-channel key exists
        c = rules.rule_owed_replies(self.db_path, dt(d=22))[0]
        self.assertIn("not attributed", c["payload"]["evidence"]["unproven"])

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
        # Seed DERIVED from config, not a literal: this used to be a hardcoded
        # 4.6 chosen against a $5 hard cap, so raising the cap to $8 (2026-08-13)
        # silently stopped the near-cap card firing and failed here. The rule
        # under test is "spend >= WARN_FRACTION * cap", so the fixture must move
        # with the cap.
        near_cap = config.COS_SPEND_WARN_FRACTION * config.DAILY_HARD_LIMIT_USD \
            + 0.10
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO runs (task_name,started_at,status) VALUES"
                         " ('upwork_scan','2026-06-22T11:00:00','failed')")
            conn.execute("INSERT INTO claude_usage (called_at,ist_date,task_name,"
                         "model,billing_route,total_cost_usd) VALUES"
                         " ('x','2026-06-22','cos_sweep','claude-opus-4-8','payg',?)",
                         (near_cap,))
        c = rules.rule_system(self.db_path, dt(hh=12))
        keys = {x["dedup_key"] for x in c}
        self.assertIn("system:run:1", keys)
        self.assertTrue(any(k.startswith("system:spend:") for k in keys))

    def test_failure_a_later_run_recovered_from_is_not_a_card(self):
        # upwork_scan runs hourly; three transient timeouts put three "Job
        # failed" cards on Today while the scan had been green for hours. A
        # card you cannot act on teaches you to ignore the board.
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO runs (task_name,started_at,status) VALUES"
                         " ('upwork_scan','2026-06-22T09:00:00','failed'),"
                         " ('upwork_scan','2026-06-22T10:00:00','completed'),"
                         " ('cos_sweep','2026-06-22T11:00:00','failed')")
        keys = {x["dedup_key"] for x in rules.rule_system(self.db_path, dt(hh=12))}
        self.assertNotIn("system:run:1", keys)   # healed by the 10:00 run
        self.assertIn("system:run:3", keys)      # still broken -> still a card


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

    def _corr(self, key, covers, title="Escalate"):
        return {"kind": "escalate_client", "dept": "sales", "title": title,
                "action": "look", "dedup_key": key, "rationale": "r",
                "covers": covers}

    def test_same_situation_under_a_new_name_refreshes_not_multiplies(self):
        # Aug 6-11: one delivery crunch arrived as escalate_commitment:554,
        # escalate_client:554-546, escalate_workstream:aug10-crunch,
        # escalate_capacity:aug8-10 and escalate_workstream:aug8-10-capacity.
        # Five keys, one problem, none reconciled -> "manychat is in there
        # like 10 times". Identity must come from WHAT it is about.
        self.seed_one_candidate()
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO scored_jobs (id,title,score,loom_flag,"
                         "status,first_scored_at) VALUES ('j2','L2',23,1,"
                         "'Scored','2026-06-22T08:00:00')")
        # the real ladder covered four commitments; one shared card is enough
        # to call it one situation (sweep._same_situation, 2026-11-01)
        covers = ["hotlead_no_loom:j1", "hotlead_no_loom:j2"]
        s1 = mock.Mock(return_value={"correlations": [
            self._corr("escalate_capacity:aug8-10", covers)],
            "priorities": [], "narrative": "x"})
        sweep.run_sweep(self._ctx(), _opus=s1, now=dt(hh=12))
        s2 = mock.Mock(return_value={"correlations": [
            self._corr("escalate_workstream:aug8-10-capacity", covers,
                       "Same crunch, new name")],
            "priorities": [], "narrative": "x"})
        with db.connect(self.db_path) as conn:      # force a material change
            conn.execute("INSERT INTO scored_jobs (id,title,score,loom_flag,"
                         "status,first_scored_at) VALUES ('j3','L3',22,1,"
                         "'Scored','2026-06-22T08:00:00')")
        out = sweep.run_sweep(self._ctx(), _opus=s2, now=dt(hh=14))
        self.assertEqual(out["escalations_merged"], 1)
        esc = self.rows("SELECT * FROM nudges WHERE source='sweep'"
                        " AND state IN ('open','snoozed')")
        self.assertEqual(len(esc), 1)               # one card, not two
        self.assertEqual(esc[0]["dedup_key"], "escalate_capacity:aug8-10")
        self.assertEqual(esc[0]["title"], "Same crunch, new name")  # refreshed

    def test_two_claims_about_one_card_merge_and_keep_the_earlier_wording(self):
        # Until 2026-11-01 these were kept apart ("a destroyed escalation is
        # worse than a duplicate one"). Measured on 30 Oct: fifteen open
        # escalations for about five situations, CacheSwipe six times under six
        # keys. One shared card is one situation now — and the merge keeps the
        # earlier wording in payload.history, so nothing is destroyed either.
        self.seed_one_candidate()
        one = ["hotlead_no_loom:j1"]
        s1 = mock.Mock(return_value={"correlations": [
            self._corr("escalate_client:acme", one, "Acme will churn")],
            "priorities": [], "narrative": "x"})
        sweep.run_sweep(self._ctx(), _opus=s1, now=dt(hh=12))
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO scored_jobs (id,title,score,loom_flag,"
                         "status,first_scored_at) VALUES ('j2','L2',23,1,"
                         "'Scored','2026-06-22T08:00:00')")
        s2 = mock.Mock(return_value={"correlations": [
            self._corr("escalate_deadline:acme", one, "Acme deadline slipping")],
            "priorities": [], "narrative": "x"})
        out = sweep.run_sweep(self._ctx(), _opus=s2, now=dt(hh=14))
        self.assertEqual(out["escalations_merged"], 1)
        esc = self.rows("SELECT dedup_key, title, payload_json FROM nudges"
                        " WHERE source='sweep' AND state IN ('open','snoozed')")
        self.assertEqual(len(esc), 1)
        self.assertEqual(esc[0]["dedup_key"], "escalate_client:acme")
        self.assertEqual(esc[0]["title"], "Acme deadline slipping")
        pl = json.loads(esc[0]["payload_json"])
        self.assertEqual(pl["history"], ["Acme will churn"])
        self.assertEqual(pl["covers"], one)

    def test_an_escalation_that_names_only_itself_stands_alone(self):
        # Opus lists its own earlier escalation in `covers`; that must not let a
        # card match itself (or an unrelated one) through the escalate* key.
        self.assertIsNone(sweep.collapse_target(
            {"dedup_key": "escalate_client:b",
             "covers": ["escalate_client:a"]},
            {"escalate_client:a": {"escalate_client:a"}}))
        # ...and the largest overlap wins when several share a card
        self.assertEqual(sweep.collapse_target(
            {"dedup_key": "escalate_client:new", "covers": ["c:1", "c:2", "c:3"]},
            {"escalate_client:x": {"c:1"}, "escalate_client:y": {"c:2", "c:3"}}),
            "escalate_client:y")

    def test_a_new_escalation_never_inherits_someone_elses_snooze(self):
        self.seed_one_candidate()
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO scored_jobs (id,title,score,loom_flag,"
                         "status,first_scored_at) VALUES ('j2','L2',23,1,"
                         "'Scored','2026-06-22T08:00:00')")
        cov = ["hotlead_no_loom:j1", "hotlead_no_loom:j2"]
        s1 = mock.Mock(return_value={"correlations": [
            self._corr("escalate_client:acme", cov, "Old")],
            "priorities": [], "narrative": "x"})
        sweep.run_sweep(self._ctx(), _opus=s1, now=dt(hh=12))
        nudges.set_state(self.db_path, state="snoozed",
                         dedup_key="escalate_client:acme",
                         snooze_until="2026-12-31T00:00:00")
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO scored_jobs (id,title,score,loom_flag,"
                         "status,first_scored_at) VALUES ('j3','L3',22,1,"
                         "'Scored','2026-06-22T08:00:00')")
        s2 = mock.Mock(return_value={"correlations": [
            self._corr("escalate_churn:acme", cov, "URGENT: churn risk")],
            "priorities": [], "narrative": "x"})
        sweep.run_sweep(self._ctx(), _opus=s2, now=dt(hh=14))
        new = self.rows("SELECT state, snooze_until FROM nudges WHERE"
                        " dedup_key='escalate_churn:acme'")
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["state"], "open")       # not silenced till Dec
        self.assertIsNone(new[0]["snooze_until"])

    def test_distinct_situations_stay_distinct(self):
        # containment, not similarity: merging two real issues would bury one
        self.seed_one_candidate()
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO scored_jobs (id,title,score,loom_flag,"
                         "status,first_scored_at) VALUES ('j2','L2',23,1,"
                         "'Scored','2026-06-22T08:00:00')")
        spy = mock.Mock(return_value={"correlations": [
            self._corr("escalate_client:a", ["hotlead_no_loom:j1"], "A"),
            self._corr("escalate_client:b", ["hotlead_no_loom:j2"], "B")],
            "priorities": [], "narrative": "x"})
        out = sweep.run_sweep(self._ctx(), _opus=spy, now=dt())
        self.assertEqual(out["escalations_merged"], 0)
        self.assertEqual(len(self.rows(
            "SELECT * FROM nudges WHERE source='sweep'")), 2)

    def test_escalation_retires_when_what_it_covers_is_gone(self):
        # an escalation is a claim ABOUT other cards; once they are gone it is
        # history. Unreconciled escalations were the ones living for weeks.
        self.seed_one_candidate()
        spy = mock.Mock(return_value={"correlations": [
            self._corr("escalate_client:acme", ["hotlead_no_loom:j1"])],
            "priorities": [], "narrative": "x"})
        sweep.run_sweep(self._ctx(), _opus=spy, now=dt(hh=12))
        with db.connect(self.db_path) as conn:      # the covered card is acted on
            conn.execute("UPDATE scored_jobs SET clickup_task_id='c1' WHERE id='j1'")
            conn.execute("INSERT INTO scored_jobs (id,title,score,loom_flag,"
                         "status,first_scored_at) VALUES ('j2','L2',23,1,"
                         "'Scored','2026-06-22T08:00:00')")
        spy2 = mock.Mock(return_value={"correlations": [], "priorities": [],
                                       "narrative": "x"})
        out = sweep.run_sweep(self._ctx(), _opus=spy2, now=dt(hh=14))
        self.assertEqual(out["escalations_reconciled"], 1)
        self.assertEqual(self.rows(
            "SELECT state FROM nudges WHERE dedup_key='escalate_client:acme'"
            )[0]["state"], "decayed")

    def test_covers_that_name_nothing_real_are_dropped(self):
        # a hallucinated key in covers would hide nothing today but could
        # silently suppress a genuine card later
        self.seed_one_candidate()
        spy = mock.Mock(return_value={"correlations": [
            self._corr("escalate_client:acme",
                       ["hotlead_no_loom:j1", "commitment:99999"])],
            "priorities": [], "narrative": "x"})
        sweep.run_sweep(self._ctx(), _opus=spy, now=dt())
        pl = json.loads(self.rows(
            "SELECT payload_json p FROM nudges WHERE"
            " dedup_key='escalate_client:acme'")[0]["p"])
        self.assertEqual(pl["covers"], ["hotlead_no_loom:j1"])

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

    def test_rule_drops_stale_overdue_commitments(self):
        # a 'demo tomorrow' from an old call is permanently overdue and must NOT
        # nag Today forever; only recently-due / upcoming ones surface
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO commitments (who,text,due,source_cite,"
                         "dedup_key,state) VALUES"
                         " ('us','ancient demo','2026-04-15','c1','k1','open'),"
                         " ('us','recent deliverable','2026-06-20','c2','k2','open')")
        c = rules.rule_commitments_due(self.db_path, dt(d=22))   # 'now' = 2026-06-22
        titles = [x["title"] for x in c]
        self.assertIn("Commitment due: recent deliverable", titles)
        self.assertNotIn("Commitment due: ancient demo", titles)   # >30d overdue

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

    def test_write_brief_writes_the_artifact_then_mails_it(self):
        """The artifact is written FIRST and unconditionally — it is the
        record; the email is a delivery of it. (Before 2026-10-18 this asserted
        the body said DRAFT, because the brief was never sent; it is sent now,
        and the banner tracks the gate — see tests/test_cos_brief_email.py.)"""
        self.seed_state()
        from modules.chief_of_staff import brief
        sent = []
        with mock.patch.object(config, "COS_BRIEFS_DIR", self.tmp / "briefs"):
            b = brief.write_brief(self.db_path, "am", now=dt(d=22),
                                  _narrate=self._narr,
                                  _send=lambda s, h: sent.append((s, h)))
        self.assertTrue(Path(b["path"]).exists())
        body = Path(b["path"]).read_text()
        self.assertIn("commitment_due item", body)                  # digest rendered
        self.assertIn("OVERDUE", body)
        self.assertTrue(b["emailed"])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], body)        # mailed exactly what was filed

    def test_a_dead_smtp_still_leaves_the_brief_on_disk(self):
        self.seed_state()
        from modules.chief_of_staff import brief

        def boom(subject, html):
            raise OSError("connection refused")
        with mock.patch.object(config, "COS_BRIEFS_DIR", self.tmp / "briefs"):
            b = brief.write_brief(self.db_path, "am", now=dt(d=22),
                                  _narrate=self._narr, _send=boom)
        self.assertTrue(Path(b["path"]).exists())
        self.assertFalse(b["emailed"])
        self.assertIn("send failed", b["email_reason"])

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

    def test_a_long_note_is_refused_not_truncated(self):
        """Silently storing 20,000 of the 25,000 characters he pasted and then
        saying "captured" is the same class of lie as losing it outright."""
        from modules.chief_of_staff import capture
        with self.assertRaises(ValueError) as e:
            capture.add_note(self.db_path, "x" * (config.CAPTURE_MAX_CHARS + 1))
        self.assertIn("Nothing was saved", str(e.exception))
        self.assertEqual(self.rows("SELECT * FROM notes"), [])

    def test_only_the_head_of_a_long_note_reaches_the_model(self):
        """…but the WHOLE note is stored and the whole note is what the UI
        shows. The cap is on the prompt, not on the record."""
        from modules.chief_of_staff import capture
        text = ("follow up with Acme. " + ("filler " * 2000)
                )[:config.CAPTURE_MAX_CHARS].strip()
        capture.add_note(self.db_path, text)
        seen = {}

        def fake_call(**kw):
            seen["content"] = kw["user_content"]
            return mock.Mock(parsed={"bucket": "idea", "title": "t",
                                     "action": None, "due": None, "who": "us"})

        with mock.patch.object(capture.runner, "claude_call", fake_call):
            capture.classify_new(self.db_path, now=dt())
        self.assertLessEqual(len(seen["content"]),
                             config.CAPTURE_CLASSIFY_CHARS + 200)
        self.assertEqual(len(self.rows("SELECT * FROM notes")[0]["text"]),
                         len(text))


# ── Quick Capture: nothing he types may become invisible ─────────────────────
class TestCaptureVisibility(CoSCase):
    """The 2026-08-14 defect, both halves.

    *"whenever I typed anything in the capture box, like things I wanted to add
    to today, it didn't get added."*  Note #3 waited 101 minutes for the sweep;
    note #2 was classified `kb_note` and produced nothing on any screen at all.
    """

    def _classified(self, bucket, **over):
        d = {"bucket": bucket, "title": "T", "action": None, "due": None,
             "who": "us"}
        d.update(over)
        return lambda db, n: d

    def test_a_targeted_classify_makes_the_card_immediately(self):
        """LATENCY. The card must exist as soon as the classifier has run —
        which is now seconds after the POST (a spawned child), not up to two
        hours later (the next sweep)."""
        from modules.chief_of_staff import capture
        n = capture.add_note(self.db_path, "anthc dashboard updates and email",
                             "cockpit")
        out = capture.classify_new(self.db_path, ids=[n["id"]],
                                   _classify=self._classified(
                                       "nudge", title="Update ANTHC dashboard"),
                                   now=dt())
        self.assertEqual(out["nudge"], 1)
        card = self.rows("SELECT * FROM nudges WHERE dedup_key=?",
                         (f"note:{n['id']}",))
        self.assertEqual(len(card), 1)
        self.assertEqual(card[0]["state"], "open")
        st = capture.status(self.db_path, n["id"])
        self.assertTrue(st["done"] and st["actionable"])
        self.assertEqual(st["card_title"], "Update ANTHC dashboard")

    def test_a_non_actionable_note_is_VISIBLE_with_its_verdict(self):
        """Note #2, 'evolve exteriors everything sorted out'. `kb_note` is a
        defensible verdict — it is a status note, not a to-do — but it used to
        produce nothing anywhere, so a thing he wrote down in order not to
        forget became unreachable."""
        from modules.chief_of_staff import capture
        n = capture.add_note(self.db_path,
                             "evolve exteriors everything sorted out", "cockpit")
        capture.classify_new(self.db_path, _classify=self._classified("kb_note"),
                             now=dt())
        u = capture.unresolved(self.db_path)
        self.assertEqual(u["needs_decision"], 1)
        item = u["items"][0]
        self.assertEqual(item["id"], n["id"])
        self.assertEqual(item["text"], "evolve exteriors everything sorted out")
        self.assertIn("not a to-do", item["verdict"])

    def test_he_can_disagree_and_the_note_becomes_a_card(self):
        from modules.chief_of_staff import capture
        n = capture.add_note(self.db_path, "evolve exteriors sorted", "cockpit")
        capture.classify_new(self.db_path, _classify=self._classified("kb_note"),
                             now=dt())
        out = capture.resolve(self.db_path, n["id"], "card", now=dt())
        self.assertTrue(out["ok"])
        self.assertEqual(len(self.rows("SELECT * FROM nudges WHERE dedup_key=?",
                                       (f"note:{n['id']}",))), 1)
        self.assertEqual(capture.unresolved(self.db_path)["needs_decision"], 0)

    def test_he_can_agree_and_it_stops_asking_without_deleting_anything(self):
        from modules.chief_of_staff import capture
        n = capture.add_note(self.db_path, "acme uses make + airtable")
        capture.classify_new(self.db_path, _classify=self._classified("kb_note"),
                             now=dt())
        capture.resolve(self.db_path, n["id"], "filed", now=dt())
        self.assertEqual(capture.unresolved(self.db_path)["needs_decision"], 0)
        row = self.rows("SELECT * FROM notes WHERE id=?", (n["id"],))[0]
        self.assertEqual(row["text"], "acme uses make + airtable")   # never gone
        self.assertEqual(row["resolved_by"], "filed")

    def test_resolve_is_idempotent(self):
        from modules.chief_of_staff import capture
        n = capture.add_note(self.db_path, "note")
        capture.classify_new(self.db_path, _classify=self._classified("idea"),
                             now=dt())
        capture.resolve(self.db_path, n["id"], "card", now=dt())
        again = capture.resolve(self.db_path, n["id"], "filed", now=dt())
        self.assertTrue(again["already"])
        self.assertEqual(again["action"], "card")

    def test_an_UNDATED_commitment_is_treated_as_invisible_too(self):
        """The third hole in this path: `rule_commitments_due` requires
        `due IS NOT NULL`, so a captured promise with no date is accepted and
        then never reaches any screen."""
        from modules.chief_of_staff import capture
        capture.add_note(self.db_path, "told them the register ships soon")
        capture.classify_new(self.db_path,
                             _classify=self._classified("commitment", due=None),
                             now=dt())
        u = capture.unresolved(self.db_path)
        self.assertEqual(u["needs_decision"], 1)
        self.assertIn("never reach Today", u["items"][0]["verdict"])

    def test_a_DATED_commitment_is_not_nagged_about(self):
        from modules.chief_of_staff import capture
        capture.add_note(self.db_path, "POC to the client on Friday")
        capture.classify_new(self.db_path,
                             _classify=self._classified("commitment",
                                                        due="2026-06-27"),
                             now=dt())
        self.assertEqual(capture.unresolved(self.db_path)["needs_decision"], 0)

    def test_a_failed_classification_keeps_the_note_and_says_why(self):
        """The classifier failing must cost the VERDICT, never the note. The
        row stays claimable (the sweep retries) and stays visible meanwhile."""
        from modules.chief_of_staff import capture

        def boom(db, n):
            raise RuntimeError("model unavailable")

        n = capture.add_note(self.db_path, "fix the broken automation")
        out = capture.classify_new(self.db_path, _classify=boom, now=dt())
        self.assertEqual(out["failed"], 1)
        row = self.rows("SELECT * FROM notes WHERE id=?", (n["id"],))[0]
        self.assertEqual(row["state"], "new")
        self.assertIsNone(row["claimed_at"])           # lease handed back
        self.assertEqual(row["attempts"], 1)
        self.assertIn("model unavailable", row["last_error"])
        u = capture.unresolved(self.db_path)
        self.assertEqual(u["total"], 1)
        self.assertIn("model unavailable", u["items"][0]["verdict_detail"])

    def test_it_stops_paying_after_MAX_ATTEMPTS_but_never_stops_showing(self):
        from modules.chief_of_staff import capture
        calls = []

        def boom(db, n):
            calls.append(n["id"])
            raise RuntimeError("nope")

        capture.add_note(self.db_path, "something")
        for _ in range(config.CAPTURE_MAX_ATTEMPTS + 3):
            capture.classify_new(self.db_path, _classify=boom, now=dt())
        self.assertEqual(len(calls), config.CAPTURE_MAX_ATTEMPTS)
        u = capture.unresolved(self.db_path)
        self.assertEqual(u["needs_decision"], 1)       # now HIS to decide
        self.assertIn("could not be classified", u["items"][0]["verdict"])

    def test_two_classifiers_racing_do_not_both_pay(self):
        """The child the cockpit spawns and the 2-hourly sweep can reach the
        same row. The lease is what stops the second one paying for it."""
        from modules.chief_of_staff import capture
        capture.add_note(self.db_path, "one note")
        first = capture.claim(self.db_path, now=dt())
        second = capture.claim(self.db_path, now=dt())
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_a_stale_lease_is_reclaimable_so_a_crash_cannot_strand_a_note(self):
        from datetime import timedelta
        from modules.chief_of_staff import capture
        capture.add_note(self.db_path, "one note")
        capture.claim(self.db_path, now=dt())          # a child that then died
        later = dt() + timedelta(
            minutes=config.CAPTURE_CLAIM_TTL_MINUTES + 1)
        self.assertEqual(len(capture.claim(self.db_path, now=later)), 1)


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
    """Nothing here reaches the outside world except through a named gate.

    AMENDED 2026-10-18. The rule used to be "no module sends, full stop",
    enforced by scanning for transport symbols. Abhijeet then asked for the
    AM/PM brief by email (v2's morning-briefing had just been stopped for
    serving 119-day-old threads), so `brief` now sends — behind
    SHADOW_MODE['cos_brief_email'], and ONLY there. The scan is kept for every
    other module, because the thing it was protecting against is a send
    appearing somewhere nobody is looking, not a send existing at all.
    """

    SILENT_MODULES = ("nudges", "rules", "sweep", "commitments", "capture")

    def test_no_send_calls_in_the_silent_modules(self):
        import importlib
        import inspect
        for name in self.SILENT_MODULES:
            mod = importlib.import_module(f"modules.chief_of_staff.{name}")
            src = inspect.getsource(mod)
            for bad in ("smtplib", "sendmail", "send_gmail", ".send("):
                self.assertNotIn(bad, src, f"{mod.__name__} must not send")

    def test_the_brief_sends_only_through_its_gate(self):
        """The one module allowed to send must route every send through
        send_brief, so there is exactly one place the gate can be checked."""
        import inspect
        from modules.chief_of_staff import brief as br
        src = inspect.getsource(br)
        self.assertNotIn("smtplib", src)        # transport stays in core.notify
        self.assertEqual(src.count("notify.send_gmail"), 1)
        self.assertIn("def email_is_live", src)
        # and the gate is actually consulted by the sender
        self.assertIn("if not email_is_live():",
                      inspect.getsource(br.send_brief))

    def test_interrupts_still_rides_the_module_flag(self):
        """The brief's gate is separate on purpose: flipping the module flag
        would start double-pinging hot leads the upwork module already mails."""
        self.assertTrue(config.SHADOW_MODE["chief_of_staff"])


if __name__ == "__main__":
    unittest.main()
