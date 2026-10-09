"""The Pipeline tab (2026-10-09): Upwork as the sales engine, end to end.

Abhijeet, before bed: "now Upwork is our sales engine ... why only five people
hired us and what are the reasons that 25 people did not move ahead ...
reactivation of the client ... different agents doing different things but at
the end there is this cohesive knowledge base". Answers the same night: a new
top-level Pipeline tab (the Funnel and the scanner charts move into it); AI
reading up to $25, Globaltize never; win-back drafts, five a week, he sends.

Pins: the reader (/api/lifecycle) — the scanner per day and its hot jobs tied
to the proposal we sent; every proposal conversation COUNTED and no message
text in the payload; the running test's scoreboard; where each client is now
and the win-back list, withheld on a RED finance doctor; the agents and what
blocks them, in words, with the fix; the route takes no params. The pure
halves — reactivation.lifecycle/rank — and the conversation reader: what is
cut before text leaves, who is never read, the reading order, the budget and
the per-run cap. And the shell: Pipeline is a top-level tab again, the Upwork
tab no longer carries Funnel or Analysis, and an old link to them lands here.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from core import config, db, registry
from modules.cockpit import lifecycle, proposal_funnel, server
from modules.upwork import conversation_codes as cc
from modules.upwork import experiments as ex
from modules.upwork import reactivation as ra
from tests.test_cockpit import CockpitServerCase
from tests import test_proposal_funnel as tpf
from tests.test_proposal_funnel import J1, P1, P2, P3, REG_TEXT, FunnelCase, _tx

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "modules" / "cockpit" / "static"
NOW = datetime(2026, 10, 9, 9, 0, tzinfo=config.TZ)
SECRET = "the-zebra-sings-at-midnight"


class LifeCase(FunnelCase):
    def setUp(self):
        super().setUp()
        self.load()
        self.reg = registry.loads_registry(REG_TEXT)
        with db.connect(self.db_path) as conn:
            ex.seed_t1(conn)

    def read(self, green=True):
        stub = mock.Mock(green=green)
        stub.reds.return_value = [mock.Mock(invariant="t1")] if not green else []
        with mock.patch("modules.finance.doctor.run", return_value=stub), \
                mock.patch.object(proposal_funnel, "_registry", return_value=self.reg):
            # never the machine's real grant log: a test must not depend on when
            # the sign-ins were last renewed on the Mac it runs on
            return lifecycle.lifecycle(self.db_path, "2026-10-08", _now=NOW,
                                       _events=self.tmp / "no-grant-events.jsonl")


class TestLifecycleReader(LifeCase):
    def test_not_built_without_the_tables(self):
        import sqlite3
        bare = self.tmp / "bare.db"
        sqlite3.connect(bare).close()
        out = lifecycle.lifecycle(bare, "2026-10-08", _now=NOW)
        self.assertFalse(out["built"])

    def test_the_scanner_per_day_and_its_hot_jobs_tied_to_our_proposal(self):
        with db.connect(self.db_path) as conn:
            for jid, when, score, skip in ((J1, "2026-08-31T10:00:00+00:00", 30, None),
                                           ("2106900000000000777", "2026-08-31T11:00:00+00:00", 12, None),
                                           ("2106900000000000888", "2026-09-01T11:00:00+00:00", None, "low_hourly"),
                                           ("2106900000000000999", "2026-09-01T12:00:00+00:00", 22, None)):
                conn.execute("INSERT INTO scored_jobs (id, title, fetched_at, score, hard_rule_skip)"
                             " VALUES (?, 'Airtable job', ?, ?, ?)", (jid, when, score, skip))
        sc = self.read()["scanner"]
        self.assertEqual(sc["since"], "2026-08-31")
        self.assertEqual(sc["hot_at"], config.HOT_LEAD_THRESHOLD)
        self.assertEqual(sc["days"], [["2026-08-31", 2, 2, 1, 1], ["2026-09-01", 2, 1, 1, 0]])
        hot = {h[0]: h for h in sc["hot"]}
        # P1 and P2 were both sent on J1; the first proposal on a job is the one tied to it
        self.assertEqual(hot[J1][4], P1)
        self.assertIsNone(hot["2106900000000000999"][4])
        self.assertNotIn("2106900000000000777", hot)                 # below the hot bar
        self.assertEqual(sc["seen"][P1][:2], [30, "2026-08-31"])
        self.assertTrue(sc["job_url"].startswith("https://www.upwork.com/jobs/~02"))

    def test_conversations_are_counted_and_no_message_text_leaves(self):
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE proposal_outcomes SET room_id='room_p2' WHERE proposal_id=?", (P2,))
            conn.execute("INSERT INTO threads (id, room_name) VALUES ('room_p2', 'Ann Lee')")
            for i, (d, when, msg) in enumerate((
                    ("inbound", "2026-09-04T10:00:00Z", "Thanks — can we hop on a quick call?"),
                    ("Outbound", "2026-09-04T11:00:00Z", f"Sure. {SECRET}"),
                    ("system", "2026-09-04T11:05:00Z", "Offer sent"),
                    ("Inbound", "2026-09-05T09:00:00Z", "We went another way."))):
                conn.execute("INSERT INTO messages (id, thread_id, message, created_dt, direction)"
                             " VALUES (?, 'room_p2', ?, ?, ?)", (f"m{i}", msg, when, d))
            conn.execute("INSERT INTO offers (id, title, state, thread_id) VALUES ('o1', 'x', 'ACCEPTED', 'room_p2')")
        out = self.read()
        t = out["talks"][P2]
        self.assertEqual((t["th"], t["us"], t["lf"], t["call"], t["off"], t["rm"]),
                         (2, 1, "them", 1, 1, "room_p2"))
        self.assertEqual(t["f"], "2026-09-04T10:00:00")
        self.assertNotIn(SECRET, json.dumps(out, default=str))
        self.assertNotIn("We went another way", json.dumps(out, default=str))

    def test_a_proposal_room_with_no_stored_messages_is_not_measured_not_zero(self):
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE proposal_outcomes SET room_id='room_none' WHERE proposal_id=?", (P3,))
        t = self.read()["talks"][P3]
        self.assertIsNone(t["th"])
        self.assertIsNone(t["us"])

    def test_the_running_test_has_its_scoreboard(self):
        out = self.read()
        t = next(x for x in out["tests"] if x["id"] == ex.T1_ID)
        self.assertEqual(t["status"], "running")
        self.assertEqual([a["arm"] for a in t["arms"]], ["A", "B"])
        self.assertEqual(t["drafts"], {"A": 0, "B": 0})
        self.assertTrue(t["scoreboard"]["verdict"].startswith("too early"))

    def test_a_drafted_and_sent_proposal_counts_for_its_version(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO experiment_assignments (experiment_id, job_id, arm, via, proposal_id, sent_arm)"
                         " VALUES (?, ?, 'B', 'draft', ?, 'B')", (ex.T1_ID, J1, P1))
        t = next(x for x in self.read()["tests"] if x["id"] == ex.T1_ID)
        self.assertEqual(t["drafts"]["B"], 1)
        self.assertEqual(t["sent"]["B"], [P1])
        self.assertEqual(t["scoreboard"]["arms"]["B"]["sent"], 1)

    def test_where_each_client_is_now_and_the_win_back_list(self):
        with db.connect(self.db_path) as conn:
            tpf.TestWhoseMoney.globaltize_shape(self, conn)
            # an old client: paid long ago, stopped, came back once, stopped again
            for rid, when, minor in (("s1", "2025-01-10", 50000), ("s2", "2025-02-10", 50000),
                                     ("s3", "2025-08-10", 80000)):
                _tx(conn, rid, source="Wise-mail", minor=minor, kind="Wise",
                    desc="Received money from SP Labs with reference X", when=when + "T00:00:00+0000")
        out = self.read()
        cl = out["clients"]
        self.assertFalse(cl["blocked"])
        rows = {r["name"]: r for r in cl["rows"]}
        zed = rows["Zed Quill LLC"]
        self.assertEqual(zed["stage"], "paying")                     # paid 2026-10-02, today 2026-10-08
        self.assertEqual(zed["origin"]["how"], "cold")
        self.assertEqual(zed["room"]["room"], "room_zed")
        sim = rows["Sim P"]
        self.assertEqual(sim["stage"], "stopped")
        self.assertEqual(sim["origin"]["how"], "declared")           # he said: found on Upwork
        self.assertTrue(sim["came_back"])
        self.assertEqual(sim["returns"][0]["on"], "2025-08-10")
        top = [r["name"] for r in cl["winback"]["top"]]
        self.assertIn("Sim P", top)
        self.assertNotIn("Zed Quill LLC", top)                        # still paying
        w = next(r for r in cl["winback"]["top"] if r["name"] == "Sim P")
        self.assertIn("came back once before", w["why"])
        self.assertEqual(cl["rules"]["weekly_drafts"], 5)

    def test_who_is_paying_is_withheld_on_a_red_doctor(self):
        with db.connect(self.db_path) as conn:
            tpf.TestWhoseMoney.globaltize_shape(self, conn)
        out = self.read(green=False)
        self.assertTrue(out["clients"]["blocked"])
        self.assertIn("RED", out["clients"]["reason"])
        self.assertNotIn("paid_usd", json.dumps(out["clients"]))
        self.assertTrue(out["built"])                                 # the rest of the page still reads

    def test_the_agents_say_what_blocks_them_and_the_fix(self):
        mcp = ("RuntimeError(\"Upwork MCP session could not start (auth): MCPAuthError('Upwork MCP grant has"
               " been dead since 2026-10-08 16:15:05 IST (invalid_grant: ...)')\")")
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO runs (task_name, started_at, status, error) VALUES"
                         " ('upwork_scan', '2026-10-09T08:15:05+05:30', 'failed', ?)", (mcp,))
            conn.execute("INSERT INTO runs (task_name, started_at, status) VALUES"
                         " ('upwork_pulse', '2026-10-09T08:30:05+05:30', 'completed')")
            conn.execute("INSERT INTO claude_usage (called_at, ist_date, task_name, model, billing_route,"
                         " total_cost_usd) VALUES ('2026-10-08T19:00:00+05:30', '2026-10-08', 'upwork_scan',"
                         " 'm', 'payg', 0.01)")
            for when in ("2026-10-08T20:00:06+05:30", "2026-10-09T05:15:10+05:30"):
                conn.execute("INSERT INTO claude_usage (called_at, ist_date, task_name, model, billing_route,"
                             " total_cost_usd, error) VALUES (?, '2026-10-09', 'kb_extract', 'm', 'payg', 0,"
                             " 'BadRequestError(... Your credit balance is too low to access the Anthropic API ...)')",
                             (when,))
        A = self.read()["agents"]
        by = {a["id"]: a for a in A["agents"]}
        self.assertEqual(by["scout"]["state"], "blocked")
        self.assertEqual(by["scout"]["blockers"], ["upwork_mcp"])
        self.assertEqual(by["reader"]["state"], "off")                # never ran: not switched on yet
        self.assertIn("ai_credit", by["writer"]["blockers"])
        bl = {b["kind"]: b for b in A["blockers"]}
        self.assertEqual(bl["upwork_mcp"]["since"], "2026-10-08 16:15:05 IST")
        self.assertIn("bin/upwork-mcp-auth", bl["upwork_mcp"]["fix"])
        self.assertEqual(bl["ai_credit"]["since"], "2026-10-08T20:00:06+05:30")
        self.assertEqual(bl["ai_credit"]["failed_calls"], 2)
        for a in A["agents"]:
            self.assertTrue(a["does"])
            self.assertNotIn("_", a["name"])

    def test_a_renewed_sign_in_clears_the_blocker_before_the_next_run(self):
        mcp = ("RuntimeError(\"Upwork MCP session could not start (auth): MCPAuthError('Upwork MCP grant has"
               " been dead since 2026-10-08 16:15:05 IST (invalid_grant: ...)')\")")
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO runs (task_name, started_at, status, error) VALUES"
                         " ('upwork_financials', '2026-10-09T06:05:05+05:30', 'failed', ?)", (mcp,))
        ev = self.tmp / "events.jsonl"
        ev.write_text('{"at": "2026-10-09 13:12:59 IST", "file": "upwork-mcp.json", "event": "authorized"}\n'
                      '{"at": "2026-10-08 16:15:05 IST", "file": "upwork-mcp.json", "event": "marked_dead"}\n')
        stub = mock.Mock(green=True)
        stub.reds.return_value = []
        with mock.patch("modules.finance.doctor.run", return_value=stub), \
                mock.patch.object(proposal_funnel, "_registry", return_value=self.reg):
            A = lifecycle.lifecycle(self.db_path, "2026-10-09", _now=NOW, _events=ev)["agents"]
        self.assertNotIn("upwork_mcp", [b["kind"] for b in A["blockers"]])
        acc = next(a for a in A["agents"] if a["id"] == "accounts")
        self.assertNotEqual(acc["state"], "blocked")
        fin = next(t for t in acc["tasks"] if t["task"] == "upwork_financials")
        self.assertTrue(fin["recovered"])
        # a renewal BEFORE the failure clears nothing
        ev.write_text('{"at": "2026-10-09 05:00:00 IST", "file": "upwork-mcp.json", "event": "authorized"}\n')
        with mock.patch("modules.finance.doctor.run", return_value=stub), \
                mock.patch.object(proposal_funnel, "_registry", return_value=self.reg):
            A = lifecycle.lifecycle(self.db_path, "2026-10-09", _now=NOW, _events=ev)["agents"]
        self.assertIn("upwork_mcp", [b["kind"] for b in A["blockers"]])

    def test_the_knowledge_base_lists_its_stores(self):
        kb = self.read()["kb"]
        tables = {s["table"] for s in kb["stores"]}
        for t in ("scored_jobs", "proposal_outcomes", "messages", "conversation_codes", "experiments"):
            self.assertIn(t, tables)
        self.assertIn("proposal_rooms", kb["gaps"])


class TestRoute(CockpitServerCase):
    def test_route_answers_and_takes_no_params(self):
        import inspect
        fn = server.GET_ROUTES["/api/lifecycle"]
        self.assertIs(fn, lifecycle.lifecycle)
        kw = [p.name for p in inspect.signature(fn).parameters.values()
              if p.kind == p.KEYWORD_ONLY and not p.name.startswith("_")]
        self.assertEqual(kw, [])
        status, body = self.get("/api/lifecycle?today=2020-01-01&_now=x")
        self.assertEqual(status, 200, body)
        self.assertNotIn("2020-01-01", body)


# ── the pure halves ──────────────────────────────────────────────────────────

def _money(clients=None, not_upwork=None):
    return {"blocked": False, "clients": clients or {}, "not_upwork": not_upwork or []}


def _pays(*pairs):
    return [{"d": d, "m": d[:7], "usd": u, "ch": "direct"} for d, u in pairs]


class TestReactivation(unittest.TestCase):
    def test_paying_stopped_and_came_back(self):
        m = _money({
            "acme": {"name": "Acme", "by": "cold", "first": "2025-01-01", "pid": "p1",
                     "rows": _pays(("2025-01-05", 1000), ("2025-02-05", 1000), ("2025-09-01", 500))},
            "beta": {"name": "Beta", "by": "invite", "first": "2026-09-01", "pid": "p2",
                     "rows": _pays(("2026-09-20", 700))},
        })
        rows = {r["key"]: r for r in ra.lifecycle(m, "2026-10-08")}
        self.assertEqual(rows["acme"]["stage"], "stopped")
        self.assertEqual(rows["acme"]["months"], 3)
        self.assertEqual(rows["acme"]["returns"], [{"after": "2025-02-05", "on": "2025-09-01", "days": 208}])
        self.assertEqual(rows["beta"]["stage"], "paying")
        self.assertEqual(rows["beta"]["origin"]["words"], "an Upwork invitation")

    def test_referrals_are_credited_to_the_client_who_referred(self):
        m = _money(
            {"sim": {"name": "Sim P", "by": "declared", "first": "2025-01-01", "pid": None,
                     "rows": _pays(("2025-01-05", 900))}},
            [{"d": "2025-06-01", "m": "2025-06", "usd": 400, "who": "Hip H",
              "origin": {"found_via": "referral", "referred_by": "Sim P"}}])
        rows = {r["name"]: r for r in ra.lifecycle(m, "2026-10-08", key_of=lambda n: n.lower())}
        self.assertEqual(rows["Hip H"]["origin"]["how"], "referral")
        self.assertFalse(rows["Hip H"]["origin"]["upwork"])
        self.assertEqual(rows["Sim P"]["referred"], ["Hip H"])

    def test_rank_explains_every_point_and_leaves_out_with_a_reason(self):
        rows = [
            {"key": "a", "name": "A", "stage": "stopped", "former": False, "quiet_days": 120, "paid_usd": 6000,
             "months": 5, "came_back": True, "referred": [], "open_contracts": 0, "room": {"room": "r1"}},
            {"key": "b", "name": "B", "stage": "stopped", "former": False, "quiet_days": 900, "paid_usd": 30000,
             "months": 12, "came_back": False, "referred": ["C"], "open_contracts": 1, "room": None},
            {"key": "c", "name": "C", "stage": "stopped", "former": True, "quiet_days": 200, "paid_usd": 9000,
             "months": 3, "came_back": False, "referred": [], "open_contracts": 0, "room": None},
            {"key": "d", "name": "D", "stage": "stopped", "former": False, "quiet_days": 30, "paid_usd": 9000,
             "months": 3, "came_back": False, "referred": [], "open_contracts": 0, "room": None},
            {"key": "e", "name": "E", "stage": "stopped", "former": False, "quiet_days": 300, "paid_usd": 100,
             "months": 1, "came_back": False, "referred": [], "open_contracts": 0, "room": None},
            {"key": "f", "name": "F", "stage": "paying", "former": False, "quiet_days": 3, "paid_usd": 9000,
             "months": 9, "came_back": False, "referred": [], "open_contracts": 1, "room": None},
        ]
        out = ra.rank(rows, "2026-10-08", drafted={"b": "2026-09-28"}, angles={"a": "the phase two they mentioned"})
        self.assertEqual([r["key"] for r in out["top"]], ["a", "b"])
        a, b = out["top"]
        # 30 (money) + 10 (5 months) + 25 (fresh) + 10 (came back) + 5 (angle)
        self.assertEqual(a["score"], 80)
        self.assertEqual(a["angle"], "the phase two they mentioned")
        # 40 + 15 + 4 + 5 (referred) - 5 (no room)
        self.assertEqual(b["score"], 59)
        self.assertIn("no Upwork conversation on record, so email", b["why"])
        self.assertEqual(b["resting_until"], "2026-11-23")             # drafted 28 Sep, rests 8 weeks
        left = {r["key"]: r["reason"] for r in out["left_out"]}
        self.assertEqual(left, {"c": "marked as not working with again",
                                "d": "stopped under 60 days ago", "e": "paid under $250 in all"})


REG_EXCL = """clients:
  -
    id: globatize
    name: Globaltize LLC
    aliases:
      - Globaltize
      - Nick Esquivel
    upwork_rooms:
      - room_glob
  -
    id: acme
    name: Acme
"""


class TestWinBackDrafts(unittest.TestCase):
    """At most five a week, best first, nobody twice inside eight weeks, never
    Globaltize, no money in what the drafter reads, and saved as a draft he
    sends himself."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="winback-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.reg = registry.loads_registry(REG_EXCL)
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, room_name, client_id) VALUES ('room_1', 'Ann Lee', 'acme')")
            conn.execute("INSERT INTO messages (id, thread_id, message, created_dt, direction) VALUES"
                         " ('m1', 'room_1', 'Phase two next spring? mail ann@example.com', '2026-03-01T10:00:00Z',"
                         " 'inbound')")
            conn.execute("INSERT INTO sources (source_type, source_ref, content_hash) VALUES ('fathom', 'f/1', 'h1')")
            conn.execute("INSERT INTO facts (source_id, client_id, fact_text, citation, confidence)"
                         " VALUES (1, 'acme', 'We built their Airtable CRM', 'f/1 @ 00:10', 'CONFIRMED')")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cand(self, key, name, score, room=None, resting=None):
        return {"key": key, "name": name, "score": score, "why": ["w"], "months": 4,
                "first_paid": "2025-01-01", "last_paid": "2026-03-01", "paid_usd": 5000.0,
                "origin": {"words": "a cold proposal on Upwork"}, "came_back": False, "angle": None,
                "room": {"room": room} if room else None, "resting_until": resting}

    def test_five_a_week_best_first_never_globaltize_never_twice(self):
        top = [self.cand("acme", "Acme", 90, room="room_1"),
               self.cand("globatize", "Globaltize LLC", 85),
               self.cand("b", "B", 80, resting="2026-11-01")] + \
              [self.cand(f"c{i}", f"C{i}", 70 - i) for i in range(6)]
        calls = []

        def call(**kw):
            calls.append(kw)
            return mock.Mock(text="Hi Ann, how did phase two go? Abhijeet", cost_usd=0.01)
        with db.connect(self.db_path) as conn:
            m = ra.draft_week(conn, {"top": top}, "2026-10-08", db_path=self.db_path, reg=self.reg,
                              _call=call)
            drafts = [dict(r) for r in conn.execute("SELECT * FROM drafts ORDER BY id")]
            log = [dict(r) for r in conn.execute("SELECT * FROM reactivation_log ORDER BY score DESC")]
        self.assertEqual(m["week"], "2026-10-05")
        self.assertEqual(m["drafted"], 5)                 # Globaltize never, B resting
        self.assertEqual([r["client_id"] for r in log], ["acme", "c0", "c1", "c2", "c3"])
        self.assertTrue(all(d["draft_kind"] == "reactivation" and d["sent_status"] == "pending" for d in drafts))
        self.assertEqual(drafts[0]["thread_id"], "room_1")
        text = calls[0]["user_content"]
        self.assertIn("We built their Airtable CRM", text)
        self.assertIn("Phase two next spring", text)
        self.assertNotIn("ann@example.com", text)
        self.assertNotIn("5000", text)                    # no money in what the drafter reads
        # the same week again: the five-a-week cap counts what was already drafted
        with db.connect(self.db_path) as conn:
            m2 = ra.draft_week(conn, {"top": top}, "2026-10-09", db_path=self.db_path, reg=self.reg, _call=call)
        self.assertEqual((m2["already"], m2["picked"], m2["drafted"]), (5, 0, 0))

    def test_the_inbox_poll_never_clears_a_win_back_draft(self):
        from tests.test_m1b import room
        from core import claude_gateway as gw, runner
        from modules.upwork import poll_inbox
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE threads SET bucket='hard-skip' WHERE id='room_1'")
            conn.execute("INSERT INTO drafts (thread_id, draft_kind, body, sent_status) VALUES"
                         " ('room_1', 'followup', 'old follow-up', 'pending')")
            conn.execute("INSERT INTO drafts (thread_id, draft_kind, body, sent_status) VALUES"
                         " ('room_1', 'reactivation', 'check-in', 'pending')")
        fake = mock.Mock()
        fake.graphql.side_effect = [{"roomList": {"edges": [{"node": room(rid="r_other", latest_hours=40 * 24)}]}},
                                    {"data": {"room": {"stories": {"edges": []}}}}]
        with mock.patch.object(config, "LOG_DIR", self.tmp / "logs"), \
                mock.patch.object(config, "LOCK_DIR", self.tmp / "locks"), \
                mock.patch.dict(config.SHADOW_MODE, {"upwork": True}), \
                mock.patch.object(runner, "claude_call", side_effect=AssertionError("no model call")):
            runner.run_task(poll_inbox.TASK_NAME,
                            lambda ctx: poll_inbox.poll(ctx, client_factory=lambda **k: fake),
                            module="upwork", db_path=self.db_path)
        with db.connect(self.db_path) as conn:
            st = {r["draft_kind"]: r["sent_status"] for r in conn.execute("SELECT * FROM drafts")}
        self.assertEqual(st, {"followup": "stale", "reactivation": "pending"})


# ── the conversation reader ──────────────────────────────────────────────────

class TestRedact(unittest.TestCase):
    def test_secrets_emails_and_phones_are_cut_and_dates_kept(self):
        text = ("Use password: hunter2 and key sk-live-abcdef123456 or AIzaSyD-abcdefghijk;"
                " mail me at ann@example.com, call +1 (415) 555-0134."
                " We start 2026-10-12 and pay $1,500.00 for 40 hours.")
        out = cc.redact(text)
        for gone in ("hunter2", "sk-live-abcdef123456", "AIzaSyD-abcdefghijk", "ann@example.com", "555-0134"):
            self.assertNotIn(gone, out)
        for kept in ("2026-10-12", "$1,500.00", "40 hours"):
            self.assertIn(kept, out)




class TestConversationReader(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="codes-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.reg = registry.loads_registry(REG_EXCL)
        with db.connect(self.db_path) as conn:
            for room, name, cid in (("room_glob", "Nick Esquivel", None), ("room_g2", "Ryan, Globaltize LLC", None),
                                    ("room_cid", "Someone", "globatize"), ("room_a", "Ann Lee", None),
                                    ("room_b", "Bob Ray", "acme"), ("room_c", "Cy Doe", None)):
                conn.execute("INSERT INTO threads (id, room_name, client_id) VALUES (?, ?, ?)", (room, name, cid))
                conn.execute("INSERT INTO messages (id, thread_id, message, created_dt, direction)"
                             " VALUES (?, ?, 'hello', '2026-09-01T10:00:00Z', 'inbound')", ("m_" + room, room))
            conn.execute("INSERT INTO messages (id, thread_id, message, created_dt, direction)"
                         " VALUES ('m_a2', 'room_a', 'later', '2026-09-03T10:00:00Z', 'outbound')")
            for pid, room, hired, title in (("pa", "room_a", 0, "Job A"), ("pb", "room_b", 1, "Job B")):
                conn.execute("INSERT INTO proposal_outcomes (proposal_id, sent_dt, kind, room_id, hired,"
                             " job_title, source) VALUES (?, '2026-08-30', 'cold', ?, ?, ?, 'test')",
                             (pid, room, hired, title))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_globaltize_is_never_read_however_the_room_is_named(self):
        with db.connect(self.db_path) as conn:
            rooms = [q["room"] for q in cc.queue(conn, self.reg)]
        for r in ("room_glob", "room_g2", "room_cid"):
            self.assertNotIn(r, rooms)
        self.assertTrue(cc.is_excluded(self.reg, "x", "Globaltize", None))
        self.assertFalse(cc.is_excluded(self.reg, "room_a", "Ann Lee", None))

    def test_reading_order_lost_proposals_then_hires_then_client_rooms(self):
        with db.connect(self.db_path) as conn:
            q = cc.queue(conn, self.reg)
        self.assertEqual([x["room"] for x in q], ["room_a", "room_b", "room_c"])

    def fake(self, cost=0.04, raise_after=None):
        calls = []

        def call(**kw):
            calls.append(kw)
            if raise_after is not None and len(calls) > raise_after:
                from core import claude_gateway as gw
                raise gw.ClaudeBudgetExceeded("per-run cap", scope="per_run")
            parsed = {"stage_reached": "talking", "outcome": "lost", "lost_reason": "price",
                      "lost_detail": "Too pricey; mail ann@example.com", "went_quiet": "them",
                      "objections": ["price"], "price_talk": "they_said_too_high", "next_step": "none",
                      "tone": "warm", "reactivation_angle": "none", "summary": "They chose a cheaper bid.",
                      "bogus": 1}
            return mock.Mock(parsed=parsed, model="claude-sonnet-4-6", cost_usd=cost)
        return call, calls

    def test_a_batch_codes_in_order_and_stores_clean_rows(self):
        call, calls = self.fake()
        with db.connect(self.db_path) as conn:
            m = cc.code_batch(conn, db_path=self.db_path, reg=self.reg, _call=call)
            rows = [dict(r) for r in conn.execute("SELECT * FROM conversation_codes ORDER BY thread_id")]
        self.assertEqual((m["due"], m["coded"], m["left"]), (3, 3, 0))
        self.assertEqual([r["thread_id"] for r in rows], ["room_a", "room_b", "room_c"])
        self.assertEqual(rows[0]["proposal_id"], "pa")
        self.assertEqual(rows[0]["coded_through"], "2026-09-03T10:00:00Z")
        self.assertNotIn("ann@example.com", rows[0]["lost_detail"])
        self.assertEqual(json.loads(rows[0]["objections_json"]), ["price"])
        self.assertIn("Job A", calls[0]["user_content"])
        self.assertEqual(calls[0]["task_name"], cc.TASK_NAME)
        # nothing new since: nothing is read twice
        with db.connect(self.db_path) as conn:
            self.assertEqual(cc.queue(conn, self.reg), [])

    def test_the_first_pass_budget_and_the_per_run_cap_stop_a_batch(self):
        call, _ = self.fake(cost=0.30)
        with db.connect(self.db_path) as conn:
            m = cc.code_batch(conn, db_path=self.db_path, reg=self.reg, _call=call, budget=0.5)
        self.assertEqual((m["coded"], m["stopped"]), (2, "budget"))
        call, _ = self.fake(raise_after=1)
        with db.connect(self.db_path) as conn:
            conn.execute("DELETE FROM conversation_codes")
            m = cc.code_batch(conn, db_path=self.db_path, reg=self.reg, _call=call)
        self.assertEqual((m["coded"], m["stopped"]), (1, "per_run"))


# ── the shell ────────────────────────────────────────────────────────────────

def _node():
    from tests.test_pipeline_funnel import _node as _find
    return _find()


class TestPipelineTab(unittest.TestCase):
    def test_pipeline_is_a_top_level_tab_again_after_today(self):
        app = (STATIC / "js" / "app.js").read_text()
        ids = re.findall(r'\{ id: "([a-z]+)"', app)
        self.assertEqual(ids[:3], ["today", "pipeline", "upwork"])
        self.assertIn('import("./sections/lifecycle.js")', app)
        self.assertNotRegex(app, r'pipeline: "upwork"')

    def test_the_upwork_tab_no_longer_carries_funnel_or_analysis(self):
        up = (STATIC / "js" / "sections" / "upwork.js").read_text()
        self.assertNotIn('{ id: "funnel"', up)
        self.assertNotIn('{ id: "analysis"', up)
        self.assertNotIn("renderFunnel", up)
        self.assertNotIn("renderAnalysis", up)
        self.assertIn('const MOVED = { funnel: "proposals", analysis: "scanner" };', up)

    def test_the_section_reads_two_routes_imports_the_house_and_never_sends(self):
        src = (STATIC / "js" / "sections" / "lifecycle.js").read_text()
        self.assertEqual(set(re.findall(r'from "([^"]+)"', src)),
                         {"../ui.js", "../drill.js", "../subtabs.js", "./upwork_funnel.js", "./pipeline.js"})
        self.assertIn('api("/api/lifecycle")', src)
        self.assertIn('api("/api/upwork/funnel")', src)
        for bad in ("fetch(", "innerHTML", "/send", "send_message"):
            self.assertNotIn(bad, src)
        for view in ("flow", "scanner", "proposals", "talks", "after", "tests", "agents"):
            self.assertIn(f'id: "{view}"', src)
        fn = (STATIC / "js" / "sections" / "upwork_funnel.js").read_text()
        self.assertIn("export function drillRows(", fn)

    def test_no_statistics_vocabulary_and_no_task_names_on_screen(self):
        from tests.test_upwork_tab import _visible_strings
        text = _visible_strings((STATIC / "js" / "sections" / "lifecycle.js").read_text()).lower()
        for word in ("baseline", "anecdote", "cohort", "p-value", "significan", "pooled"):
            self.assertNotIn(word, text)

    def test_styles_the_section_uses_exist_and_use_tokens_only(self):
        css = (STATIC / "css" / "components.css").read_text()
        for cls in (".lc-drops", ".lc-drop", ".lc-block", ".lc-cmd", ".lc-after", ".lc-wb", ".lc-agent-grid",
                    ".lc-agent-chip", ".lc-test", ".flow", ".stage .bar"):
            self.assertIn(cls, css, cls)
        block = css[css.index("Pipeline (sections/lifecycle.js"):]
        self.assertNotRegex(block, r"#[0-9a-fA-F]{3,6}\b")

    def test_parses_under_node(self):
        node = _node()
        if not node:
            self.skipTest("node not installed")
        for rel in ("js/sections/lifecycle.js", "js/sections/upwork.js", "js/sections/upwork_funnel.js", "js/app.js"):
            with tempfile.TemporaryDirectory() as td:
                mjs = Path(td) / "check.mjs"
                mjs.write_text((STATIC / rel).read_text())
                r = subprocess.run([node, "--check", str(mjs)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{rel}: {r.stderr[:400]}")


if __name__ == "__main__":
    unittest.main()
