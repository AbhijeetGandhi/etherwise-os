"""Promises typed on Upwork become tasks on Today (modules/upwork/promises).

Abhijeet, 2026-08-26 05:20: "we told David that we will let him know
immediately — that should become part of the Today tab because it's a task".
Pins: the phrase gate (no model call for "thanks!"); one extractor call per
promise-shaped outbound message with the room's context; the commitments row
with its Upwork cite and dedup; the cursor (a failed extraction is retried,
never skipped); the auto-clear when we write again in the room (UI law 5);
the Today card via rule_commitments_due — undated → due UPWORK_PROMISE_DAYS
later, "You promised <room>: …", the room in the payload; the poll hook.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core import config, db
from core import claude_gateway as gw
from modules.chief_of_staff import rules
from modules.upwork import poll_inbox, promises


def _result(parsed):
    return gw.GatewayResult(text="{}", parsed=parsed, model="m", route="payg",
                            usage={}, cost_usd=0.001, stop_reason="end_turn",
                            duration_ms=5)


class _Ctx:
    """A ctx whose claude() answers from a script and records every call."""
    def __init__(self, db_path, answers=None, raise_on=None):
        self.db_path = db_path
        self.shadow = True
        self.calls = []
        self.answers = list(answers or [])
        self.raise_on = raise_on

    def claude(self, **kw):
        self.calls.append(kw)
        if self.raise_on and self.raise_on in kw.get("user_content", ""):
            raise RuntimeError("model down")
        if self.answers:
            return _result(self.answers.pop(0))
        return _result({"commitments": []})


class PromiseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="promises-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.now = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc)
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, room_name, room_type, client_id)"
                         " VALUES ('room_nova', 'Nabil Tayeb Cherif, Recherches Super Nova Inc.',"
                         " 'INTERVIEW', NULL)")
            conn.execute("INSERT INTO threads (id, room_name, room_type, client_id)"
                         " VALUES ('room_tcc', 'Brandi Jordan', 'INTERVIEW', 'the-cradle-company')")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def msg(self, mid, room, text, direction="outbound", hours_ago=1.0, ingested=None):
        at = (self.now - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        ing = ingested or (self.now - timedelta(hours=hours_ago) + timedelta(minutes=5)
                           ).strftime("%Y-%m-%d %H:%M:%S")
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO messages (id, thread_id, message, created_dt, sender_user_name,"
                " direction, ingested_at) VALUES (?,?,?,?,?,?,?)",
                (mid, room, text, at, "Abhijeet G." if direction == "outbound" else "David S.",
                 direction, ing))

    def rows(self, sql, args=()):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, args)]


class TestGate(unittest.TestCase):
    def test_promise_shaped_messages_pass_and_thanks_does_not(self):
        yes = ["thank you david, I will take a look and let you know.",
               "I'll send the register on Friday", "We will share the POC by Monday",
               "Let me get back to you by end of week", "Sure — I can do that within a day",
               "will keep you posted", "I'm going to follow up with the team"]
        no = ["thanks!", "Sounds good", "Great, talk soon", "ok", "Received, cheers.",
              "What is your budget for this?"]
        for t in yes:
            self.assertTrue(promises.looks_like_promise(t), t)
        for t in no:
            self.assertFalse(promises.looks_like_promise(t), t)
        self.assertFalse(promises.looks_like_promise(None))

    def test_cite_round_trips(self):
        cite = promises.source_cite("room_nova", "story_1")
        self.assertEqual(cite, "upwork/room_nova @ story_1")
        self.assertEqual(promises.parse_cite(cite), ("room_nova", "story_1"))
        self.assertIsNone(promises.parse_cite("fathom/123 @ 00:12"))
        self.assertIsNone(promises.parse_cite(None))


class TestExtractAndRecord(PromiseCase):
    def test_only_promise_shaped_outbound_messages_reach_the_model_with_context(self):
        self.msg("in1", "room_nova", "I've sent you an email with a workflow brief.",
                 direction="inbound", hours_ago=3)
        self.msg("out1", "room_nova", "thank you david, I will take a look and let you know.",
                 hours_ago=2)
        self.msg("out2", "room_tcc", "thanks!", hours_ago=1.5)          # gated out
        self.msg("in2", "room_tcc", "I'll send the data tomorrow", direction="inbound",
                 hours_ago=1)                                             # theirs, not ours
        ctx = _Ctx(self.db_path, answers=[{"commitments": [
            {"who": "us", "text": "Read David's workflow brief and reply",
             "due": None, "quote": "I will take a look and let you know"},
            {"who": "client", "text": "x", "due": None, "quote": "x"}]}])
        m = promises.run(ctx, self.db_path, now=self.now)
        self.assertEqual((m["promise_scanned"], m["promise_candidates"], m["promises_found"]),
                         (2, 1, 1))
        self.assertEqual(len(ctx.calls), 1)
        call = ctx.calls[0]
        self.assertEqual(call["model_key"], "drafting")
        self.assertIn("I've sent you an email", call["user_content"])   # the question before
        self.assertIn("us: thank you david", call["user_content"])
        self.assertIn("promise room_nova", call["purpose"])
        rows = self.rows("SELECT * FROM commitments")
        self.assertEqual(len(rows), 1)                                   # who='client' dropped
        r = rows[0]
        self.assertEqual((r["who"], r["state"], r["client_id"], r["due"]), ("us", "open", None, None))
        self.assertEqual(r["source_cite"], "upwork/room_nova @ out1")
        self.assertTrue(r["dedup_key"].startswith("commitment:"))
        # the cursor moved to the newest message read; a second run reads nothing new
        m2 = promises.run(ctx, self.db_path, now=self.now)
        self.assertEqual((m2["promise_scanned"], len(ctx.calls)), (0, 1))

    def test_a_failed_extraction_is_retried_next_time_not_skipped(self):
        self.msg("out1", "room_nova", "I will send the proposal tomorrow", hours_ago=3)
        self.msg("out2", "room_tcc", "we'll get back to you by Friday", hours_ago=2)
        ctx = _Ctx(self.db_path, raise_on="we'll get back to you",
                   answers=[{"commitments": [{"who": "us", "text": "Send the proposal",
                                              "due": "2026-08-27", "quote": "send the proposal"}]}])
        m = promises.run(ctx, self.db_path, now=self.now)
        self.assertEqual((m["promises_found"], m["promise_errors"]), (1, 1))
        # the cursor stopped at the first message; the second is read next run
        ctx2 = _Ctx(self.db_path, answers=[{"commitments": [
            {"who": "us", "text": "Get back to Brandi", "due": "2026-08-28", "quote": "get back"}]}])
        m2 = promises.run(ctx2, self.db_path, now=self.now)
        self.assertEqual(m2["promises_found"], 1)
        self.assertIn("we'll get back to you", ctx2.calls[0]["user_content"])
        self.assertEqual(len(self.rows("SELECT id FROM commitments")), 2)
        self.assertEqual(self.rows("SELECT due FROM commitments WHERE source_cite LIKE '%out2'")[0]["due"],
                         "2026-08-28")

    def test_same_promise_twice_is_one_row(self):
        self.msg("out1", "room_nova", "I will take a look and let you know", hours_ago=2)
        with db.connect(self.db_path) as conn:
            msg = {"id": "out1", "thread_id": "room_nova", "client_id": None}
            self.assertTrue(promises.record(conn, msg, {"text": "Reply to David", "due": None}))
            self.assertFalse(promises.record(conn, msg, {"text": "reply  to DAVID", "due": None}))
        self.assertEqual(len(self.rows("SELECT id FROM commitments")), 1)

    def test_extractor_calls_per_run_are_capped_and_the_rest_wait(self):
        for i in range(4):
            self.msg(f"o{i}", "room_nova", f"I will send part {i} tomorrow", hours_ago=5 - i)
        ctx = _Ctx(self.db_path)
        with mock.patch.object(config, "UPWORK_PROMISE_MAX_PER_RUN", 3):
            m = promises.run(ctx, self.db_path, now=self.now)
        self.assertEqual((m["promise_candidates"], m["promise_deferred"], len(ctx.calls)), (3, 1, 3))
        m2 = promises.run(ctx, self.db_path, now=self.now)
        self.assertEqual((m2["promise_scanned"], m2["promise_candidates"]), (1, 1))
        self.assertIn("part 3", ctx.calls[-1]["user_content"])

    def test_the_first_run_looks_back_a_bounded_window(self):
        self.msg("old", "room_nova", "I will send it over next week", hours_ago=24 * 40)
        self.msg("new", "room_nova", "I will send it over tomorrow", hours_ago=2)
        ctx = _Ctx(self.db_path)
        m = promises.run(ctx, self.db_path, now=self.now)
        self.assertEqual(m["promise_scanned"], 1)
        self.assertIn("tomorrow", ctx.calls[0]["user_content"])

    def test_an_old_message_ingested_today_is_not_a_new_promise(self):
        """First live run, 26 Aug: a room's 2025 history was re-pulled this
        week, so two year-old messages were 'new' by ingested_at and cost two
        extractor calls. Said-when matters as much as stored-when."""
        self.msg("ancient", "room_nova", "I will fix the trigger quickly", hours_ago=24 * 400,
                 ingested=(self.now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"))
        self.msg("fresh", "room_nova", "I will send it over tomorrow", hours_ago=2)
        ctx = _Ctx(self.db_path)
        m = promises.run(ctx, self.db_path, now=self.now)
        self.assertEqual(m["promise_scanned"], 1)
        self.assertIn("send it over tomorrow", ctx.calls[0]["user_content"])
        self.assertIn("NOT a commitment", ctx.calls[0]["user_content"])   # the precision rule rides along


class TestAutoClear(PromiseCase):
    def test_our_next_message_in_the_room_keeps_the_promise(self):
        self.msg("out1", "room_nova", "I will take a look and let you know", hours_ago=5)
        with db.connect(self.db_path) as conn:
            promises.record(conn, {"id": "out1", "thread_id": "room_nova", "client_id": None},
                            {"text": "Read the brief and reply", "due": None})
            self.assertEqual(promises.auto_close(conn), 0)          # nothing after it yet
        self.msg("in2", "room_nova", "great, thanks", direction="inbound", hours_ago=4)
        with db.connect(self.db_path) as conn:
            self.assertEqual(promises.auto_close(conn), 0)          # THEIR message is not ours
        self.msg("out2", "room_nova", "Read it — the clustering step is doable, here is how", hours_ago=1)
        with db.connect(self.db_path) as conn:
            self.assertEqual(promises.auto_close(conn), 1)
        self.assertEqual(self.rows("SELECT state FROM commitments")[0]["state"], "done")
        # a Fathom commitment is never touched by this rule
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO commitments (who, text, source_cite, dedup_key) VALUES"
                         " ('us', 'x', 'fathom/1 @ 00:01', 'commitment:f1')")
            self.assertEqual(promises.auto_close(conn), 0)
        self.assertEqual(self.rows("SELECT state FROM commitments WHERE dedup_key='commitment:f1'")[0]["state"], "open")


class TestTodayCard(PromiseCase):
    def test_an_undated_upwork_promise_is_a_card_now_due_in_two_days(self):
        self.msg("in0", "room_nova", "I've sent you an email with a workflow brief.",
                 direction="inbound", hours_ago=3)
        self.msg("out1", "room_nova", "thank you david, I will take a look and let you know.",
                 hours_ago=2)
        with db.connect(self.db_path) as conn:
            promises.record(conn, {"id": "out1", "thread_id": "room_nova", "client_id": None},
                            {"text": "Read David's workflow brief and reply", "due": None})
        cards = rules.rule_commitments_due(self.db_path, now=self.now.astimezone(config.TZ))
        self.assertEqual(len(cards), 1)
        c = cards[0]
        self.assertEqual(c["kind"], "commitment_due")
        # promised to the person we answered (David S. wrote last), in Nabil's room
        self.assertEqual(c["title"], "You promised David S.: Read David's workflow brief and reply")
        self.assertEqual(c["payload"]["to"], "David S.")
        self.assertEqual(c["payload"]["room_name"], "Nabil Tayeb Cherif, Recherches Super Nova Inc.")
        # the IST day of the promise (22:00 UTC on the 25th is the 26th in Pune)
        promised = (self.now - timedelta(hours=2)).astimezone(config.TZ).strftime("%Y-%m-%d")
        self.assertEqual(c["due"], (datetime.strptime(promised, "%Y-%m-%d")
                                    + timedelta(days=config.UPWORK_PROMISE_DAYS)).strftime("%Y-%m-%d"))
        self.assertEqual(c["payload"]["room_id"], "room_nova")
        self.assertEqual(c["payload"]["promised_on"], promised)
        self.assertTrue(c["payload"]["undated"])
        self.assertTrue(c["dedup_key"].startswith("commitment:"))
        # done → no card (the sweep clears the nudge it wrote)
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE commitments SET state='done'")
        self.assertEqual(rules.rule_commitments_due(self.db_path, now=self.now.astimezone(config.TZ)), [])

    def test_a_dated_upwork_promise_is_carded_once_with_its_own_date(self):
        self.msg("out1", "room_tcc", "I'll send the register on Friday", hours_ago=2)
        with db.connect(self.db_path) as conn:
            promises.record(conn, {"id": "out1", "thread_id": "room_tcc",
                                   "client_id": "the-cradle-company"},
                            {"text": "Send the register", "due": "2026-08-27"})
        cards = rules.rule_commitments_due(self.db_path, now=self.now.astimezone(config.TZ))
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["due"], "2026-08-27")                 # its own date, kept
        self.assertEqual(cards[0]["client"], "the-cradle-company")
        # one card, from the Upwork branch: named, with the room to open
        self.assertEqual(cards[0]["title"], "You promised Brandi Jordan: Send the register")
        self.assertEqual(cards[0]["payload"]["room_id"], "room_tcc")
        self.assertFalse(cards[0]["payload"]["undated"])

    def test_today_hands_the_room_to_the_card(self):
        from modules.cockpit import data as cdata
        from modules.chief_of_staff import nudges
        self.msg("out1", "room_nova", "I will take a look and let you know", hours_ago=2)
        with db.connect(self.db_path) as conn:
            promises.record(conn, {"id": "out1", "thread_id": "room_nova", "client_id": None},
                            {"text": "Read the brief and reply", "due": None})
        for c in rules.rule_commitments_due(self.db_path, now=self.now.astimezone(config.TZ)):
            nudges.write_nudge(self.db_path, **c)
        with mock.patch.object(config, "DB_PATH", self.db_path):
            out = cdata.today(self.db_path)
        cards = [e for e in out.get("queue", []) if str(e.get("item_key", "")).startswith("nudge:commitment:")]
        self.assertEqual(len(cards), 1)
        d = cards[0]["detail"]
        self.assertEqual(d["thread_url"], cdata.ROOM_URL.format(tid="room_nova"))
        self.assertEqual(d["room_name"], "Nabil Tayeb Cherif, Recherches Super Nova Inc.")
        self.assertEqual(d["to"], "Nabil Tayeb Cherif")     # nobody wrote before: the room's name
        self.assertTrue(d["undated"])
        self.assertEqual(d["source"], "upwork/room_nova @ out1")


class TestPollHook(PromiseCase):
    def test_the_inbox_poll_runs_the_pass_and_reports_it(self):
        self.msg("out1", "room_nova", "I will take a look and let you know", hours_ago=2)
        at = (self.now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        room = {"id": "room_nova", "roomName": "Nabil", "topic": "t", "roomType": "INTERVIEW",
                "latestStory": {"id": "out1", "createdDateTime": at}, "numUnread": 0,
                "contract": None, "vendorProposal": None}
        fake_client = mock.Mock()
        fake_client.graphql.side_effect = [{"roomList": {"edges": [{"node": room}]}}]
        ctx = _Ctx(self.db_path)
        ctx.record_shadow_write = lambda **k: None
        with mock.patch.object(poll_inbox, "compute_awaiting", lambda conn, r: None):
            m = poll_inbox.poll(ctx, client_factory=lambda **k: fake_client)
        for k in ("promise_scanned", "promise_candidates", "promises_found",
                  "promises_kept", "promise_deferred", "promise_errors"):
            self.assertIn(k, m)
        self.assertEqual((m["promise_scanned"], m["promise_candidates"]), (1, 1))
        self.assertEqual(len(ctx.calls), 1)                 # the extractor was asked


if __name__ == "__main__":
    unittest.main()
