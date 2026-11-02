"""The Upwork heartbeat and money in flight (modules/upwork/feeds/mcp_pulse
+ mcp_inflight — Abhijeet's 26 Aug 22:30 "money in flight + offers" and "the
dashboard heartbeat"), pinned by the captures of 22:40-22:45:
dashboard_check.json, offers_list.json, offers_pending_empty.json, timesheets.json,
contract_get_milestones.json.

Pins: one dashboard read a quarter hour and NO detail read when the counts
are zero; a count above zero fetches that thing and stores it; the offers
paging trap (hasMore is true on an empty page); a missing block reads None,
not 0; the timesheet row (cursor as key, two entries on one contract in one
day, manual hours); the milestone row (state is the authority, not
fundedAmount); the unbilled total and the stalled-milestone query; the cards
those produce on Today.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from core import config, db
from modules.chief_of_staff import rules
from modules.upwork.feeds import mcp_inflight as mi, mcp_pulse as mp, mcp_search

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "mcp"
ORG = config.UPWORK_ORGS["personal"]


def fx(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


class _Session:
    """answers: {(tool, action): payload or callable}."""
    def __init__(self, answers=None, fail_start=None):
        self.answers = answers or {}
        self.calls = []
        self.fail_start = fail_start

    def initialize(self):
        if self.fail_start:
            raise self.fail_start

    def call(self, tool, args):
        self.calls.append((tool, args))
        a = self.answers.get((tool, (args or {}).get("action")))
        return a(args) if callable(a) else (a if a is not None else {})

    def actions(self):
        return [(t, (a or {}).get("action")) for t, a in self.calls]


class _Ctx:
    def __init__(self, db_path):
        self.db_path = db_path
        self.logs, self.anomalies = [], []

    def log(self, m):
        self.logs.append(m)

    def anomaly(self, kind, severity, detail):
        self.anomalies.append((kind, severity, detail))


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self._p = mock.patch.object(mcp_search, "_sleep", lambda s: None)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self, sql, args=()):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, args)]


class TestHeartbeat(Case):
    def _hour_spent(self):
        """The room sweep (use case F) pages the full room list once an IST hour
        and reads any room the dashboard preview says has moved. These tests
        measure a STEADY-STATE quarter hour — hour spent, mirror current — so
        neither fires; the sweep itself is covered in test_mcp_messages."""
        from modules.upwork.feeds import mcp_messages as mm
        with db.connect(self.db_path) as conn:
            mp.mark_rooms_sweep(conn)
            for room in mm.rooms_from_preview(fx("dashboard_check")["check"]):
                mm.record_attempt(conn, room["id"],
                                  mm._iso((room.get("latestStory") or {}).get("created")))

    def test_the_live_dashboard_reads_as_nothing_waiting_and_costs_one_read(self):
        self._hour_spent()
        s = _Session({("get_freelancer_dashboard", "check"): fx("dashboard_check")["check"]})
        m = mp.run(_Ctx(self.db_path), session_factory=lambda: s)
        self.assertEqual(m["pulse"], {"offers": 0, "invitations": 0, "unread": 0, "rooms": 2,
                                      "connects": 256, "matching_jobs": 2,
                                      "matching_competition": [27, 51]})
        self.assertEqual(s.actions(), [("get_freelancer_dashboard", "check")])   # ONE read
        self.assertEqual((m["offers"]["seen"], m["invitations"]["seen"]), (0, 0))

    def test_the_room_list_is_paged_once_an_hour_not_every_heartbeat(self):
        """The full list costs a call, so it rides the FIRST heartbeat of an IST
        hour. The other three read the dashboard's own room preview, which is
        already in the answer they were making anyway."""
        dash = fx("dashboard_check")["check"]
        empty = {"data": {"rooms": []}, "hasMore": False, "status": "ok"}
        s = _Session({("get_freelancer_dashboard", "check"): dash,
                      ("get_messages", "list_rooms"): empty})
        mp.run(_Ctx(self.db_path), session_factory=lambda: s)
        self.assertEqual(s.actions(), [("get_freelancer_dashboard", "check"),
                                       ("get_messages", "list_rooms")])
        s2 = _Session({("get_freelancer_dashboard", "check"): dash,
                       ("get_messages", "list_rooms"): empty,
                       ("get_messages", "list_messages"): {"data": {"roomStories":
                           {"edges": [], "pageInfo": {}}}, "hasMore": False}})
        mp.run(_Ctx(self.db_path), session_factory=lambda: s2)
        self.assertNotIn(("get_messages", "list_rooms"), s2.actions())

    def test_an_empty_page_still_says_hasMore_and_is_not_paged(self):
        self._hour_spent()
        """The live capture: a filtered offers page comes back with zero offers
        and hasMore=True + a next_offset. A pager that loops on hasMore alone
        never terminates. fetch_offers reads one page, full stop."""
        empty = fx("offers_pending_empty")
        self.assertEqual(empty["data"]["offersByAttribute"]["offers"], [])
        self.assertTrue(empty["hasMore"])
        dash = json.loads(json.dumps(fx("dashboard_check")["check"]))
        dash["data"]["offers"] = {"count": 1, "items": [{"id": "x"}]}
        s = _Session({("get_freelancer_dashboard", "check"): dash,
                      ("list_offers", "list"): empty})
        m = mp.run(_Ctx(self.db_path), session_factory=lambda: s)
        self.assertEqual(s.actions(), [("get_freelancer_dashboard", "check"),
                                       ("list_offers", "list")])   # not twice
        self.assertEqual((m["offers"]["seen"], m["offers"]["inserted"]), (0, 0))
        self.assertEqual(self.rows("SELECT 1 FROM offers"), [])

    def test_a_missing_block_is_none_not_zero(self):
        got = mp.read({"data": {"messages": {"rooms": []}}})
        self.assertIsNone(got["offers"])
        self.assertIsNone(got["invitations"])
        self.assertIsNone(got["connects"])
        self.assertEqual(set(mp.read({})), {"offers", "invitations", "unread", "rooms",
                                            "connects", "matching_jobs", "matching_competition"})
        self.assertEqual(mp.read({"data": {"offers": {"items": [{"id": "1"}]}}})["offers"], 1)

    def test_an_offer_waiting_is_fetched_stored_and_carded(self):
        self._hour_spent()
        offer = dict(fx("offers_list")["data"]["offersByAttribute"]["offers"][0])
        offer["state"] = "awaiting_your_acceptance"
        offer["state_label"] = "Awaiting your acceptance"
        pending = {"data": {"offersByAttribute": {"offers": [offer]}}, "hasMore": True,
                   "next_offset": 10, "status": "ok"}
        dash = json.loads(json.dumps(fx("dashboard_check")["check"]))
        dash["data"]["offers"] = {"count": 1, "items": [{"id": offer["id"]}]}
        s = _Session({("get_freelancer_dashboard", "check"): dash,
                      ("list_offers", "list"): pending})
        ctx = _Ctx(self.db_path)
        m = mp.run(ctx, session_factory=lambda: s)
        self.assertEqual(s.actions(), [("get_freelancer_dashboard", "check"), ("list_offers", "list")])
        self.assertEqual(s.calls[1][1]["params"], {"limit": 10, "states": ["awaiting_your_acceptance"]})
        self.assertEqual((m["offers"]["seen"], m["offers"]["inserted"]), (1, 1))
        row = self.rows("SELECT * FROM offers")[0]
        self.assertEqual((row["id"], row["state"], row["type"]),
                         ("112071334", "awaiting_your_acceptance", "MARKET_PLACE_HOURLY"))
        self.assertEqual(row["job_id"], "2079940185327676180")
        # …and it is on Today, due today, with the expiry warning
        cards = rules.rule_upwork_waiting(self.db_path)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["kind"], "upwork_offer")
        self.assertTrue(cards[0]["title"].startswith("Offer waiting: Airtable Configuration"))
        self.assertIn("expires", cards[0]["action"])
        self.assertEqual(cards[0]["due"], datetime.now(config.TZ).strftime("%Y-%m-%d"))
        # a second heartbeat updates, never twins
        m2 = mp.run(_Ctx(self.db_path), session_factory=lambda: s)
        self.assertEqual((m2["offers"]["inserted"], m2["offers"].get("updated")), (0, 1))
        self.assertEqual(len(self.rows("SELECT 1 FROM offers")), 1)

    def test_an_invitation_is_stored_and_carded(self):
        dash = json.loads(json.dumps(fx("dashboard_check")["check"]))
        dash["data"]["invitations"] = {"count": 1, "items": [{"id": "inv1"}]}
        s = _Session({("get_freelancer_dashboard", "check"): dash,
                      ("list_freelancer_proposals", "invitations"): {"invitations": [
                          {"id": "inv1", "status": "pending",
                           "createdDateTime": "2026-08-26T18:00:00Z",
                           "jobPosting": {"id": "job9", "title": "Airtable rescue"},
                           "client": {"name": "New Client Co"}}]}})
        m = mp.run(_Ctx(self.db_path), session_factory=lambda: s)
        self.assertEqual(m["invitations"], {"seen": 1, "inserted": 1, "resolved": 0})
        r = self.rows("SELECT * FROM upwork_invitations")[0]
        self.assertEqual((r["id"], r["job_id"], r["job_title"], r["client_name"], r["state"]),
                         ("inv1", "job9", "Airtable rescue", "New Client Co", "pending"))
        cards = rules.rule_upwork_waiting(self.db_path)
        self.assertEqual([c["kind"] for c in cards], ["upwork_invitation"])
        self.assertIn("Airtable rescue", cards[0]["title"])
        # marking it seen retires the card
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE upwork_invitations SET seen_at=datetime('now')")
        self.assertEqual(rules.rule_upwork_waiting(self.db_path), [])

    def test_an_answered_invitation_leaves_the_board_on_its_own(self):
        """Abhijeet, 2026-11-02: "most of the times I reply to them or decline;
        if I do you should be able to remove those automatically." Upwork lists
        only PENDING invitations, so one he has dealt with simply stops
        appearing — the pulse reads that absence and marks the row applied
        (a proposal exists for its job) or resolved, and the rule drops the
        card. A row Upwork still lists is untouched."""
        dash = {"data": {"offers": {"count": 0}, "invitations": {"count": 1},
                         "messages": {"unread_in_preview": 0, "rooms": []},
                         "connects": {"balance": {"connectsBalance": 10}}}}
        two = {"invitations": [
            {"id": "inv1", "status": "pending", "createdDateTime": "2026-10-30T18:00:00Z",
             "jobPosting": {"id": "job1", "title": "AI Automation Expert"}, "client": {"name": "A"}},
            {"id": "inv2", "status": "pending", "createdDateTime": "2026-10-30T18:00:00Z",
             "jobPosting": {"id": "job2", "title": "Social media automation"}, "client": {"name": "B"}},
            {"id": "inv3", "status": "pending", "createdDateTime": "2026-10-30T18:00:00Z",
             "jobPosting": {"id": "job3", "title": "Workflow expert"}, "client": {"name": "C"}}]}
        dash3 = {"data": dict(dash["data"], invitations={"count": 3})}
        s = _Session({("get_freelancer_dashboard", "check"): dash3,
                      ("list_freelancer_proposals", "invitations"): two})
        mp.run(_Ctx(self.db_path), session_factory=lambda: s)
        self.assertEqual(len(rules.rule_upwork_waiting(self.db_path)), 3)
        # he applied to job1 and declined job2; job3 is still pending on Upwork
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO proposals (id, marketplace_job_id, job_title) VALUES ('p1','job1','x')")
        later = {"invitations": [two["invitations"][2]]}
        s2 = _Session({("get_freelancer_dashboard", "check"): dash,
                       ("list_freelancer_proposals", "invitations"): later})
        m = mp.run(_Ctx(self.db_path), session_factory=lambda: s2)
        self.assertEqual(m["invitations"]["resolved"], 2)
        states = {r["id"]: r["state"] for r in self.rows("SELECT id, state FROM upwork_invitations")}
        self.assertEqual(states, {"inv1": "applied", "inv2": "resolved", "inv3": "pending"})
        self.assertEqual([c["payload"]["invitation_id"] for c in rules.rule_upwork_waiting(self.db_path)],
                         ["inv3"])

    def test_an_unreadable_pending_list_resolves_nothing(self):
        # the dashboard block missing => count None => the absence means
        # nothing, and every open row stays exactly as it was
        dash = {"data": {"offers": {"count": 0}, "messages": {"unread_in_preview": 0, "rooms": []},
                         "connects": {"balance": {"connectsBalance": 10}}}}
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO upwork_invitations (id, job_id, job_title, state, created_dt)"
                         " VALUES ('inv9', 'job9', 'Old one', 'pending', '2026-10-01T00:00:00Z')")
        s = _Session({("get_freelancer_dashboard", "check"): dash})
        m = mp.run(_Ctx(self.db_path), session_factory=lambda: s)
        self.assertNotIn("resolved", m["invitations"])
        self.assertEqual(self.rows("SELECT state FROM upwork_invitations")[0]["state"], "pending")

    def test_a_dead_session_is_an_anomaly_not_a_failed_run(self):
        import socket
        import urllib.error
        s = _Session(fail_start=urllib.error.URLError(socket.gaierror(8, "nodename nor servname")))
        ctx = _Ctx(self.db_path)
        m = mp.run(ctx, session_factory=lambda: s)
        self.assertEqual((m["status"], m["kind"]), ("error", "network"))
        self.assertEqual([a[0] for a in ctx.anomalies], ["pulse_failed"])

    def test_scheduled_every_quarter_hour(self):
        import plistlib
        plist = ROOT / "launchd" / "io.etherwise.v3.upwork-pulse.plist"
        doc = plistlib.loads(plist.read_bytes())
        self.assertEqual(doc["ProgramArguments"][-1], "modules.upwork.feeds.mcp_pulse")
        self.assertEqual([d["Minute"] for d in doc["StartCalendarInterval"]], [5, 20, 35, 50])
        src = next(s for s in config.SOURCES if s["task_name"] == mp.TASK_NAME)
        self.assertEqual((src["key"], src["critical"], src["launchd_label"]),
                         ("upwork_pulse", True, doc["Label"]))
        from modules.cockpit import actions, data
        self.assertEqual(actions.RERUN_JOBS[mp.TASK_NAME], doc["Label"])
        self.assertIn(mp.TASK_NAME, data._RUN_TASK_WORDS)


class TestInFlight(Case):
    def _session(self):
        return _Session({
            ("get_freelancer_financials", "timesheets"): fx("timesheets")["week_2026_08_24"],
            ("list_contracts", "get"): fx("contract_get_milestones")["get"]})

    def test_the_weeks_hours_land_with_their_memos_and_add_up(self):
        s = self._session()
        m = mi.run(_Ctx(self.db_path), s, ORG, today=date(2026, 8, 26))
        self.assertEqual((m["timesheets"]["seen"], m["timesheets"]["inserted"]), (5, 5))
        rows = self.rows("SELECT * FROM upwork_timesheets ORDER BY cursor_id")
        self.assertEqual(len(rows), 5)
        # two entries for one contract on one day: the cursor keeps them apart
        same_day = [r for r in rows if r["contract_id"] == "43406906" and r["date_worked"] == "2026-08-24"]
        self.assertEqual(len(same_day), 2)
        self.assertEqual({r["charges_minor"] for r in same_day}, {2917, 1167})
        self.assertEqual(rows[1]["term_id"], "44336214")            # term != contract id
        self.assertTrue(all(r["online_hours"] == 0 for r in rows))  # every hour manual
        self.assertIn("planning on the three automation", rows[1]["memo"])
        # the headline: $565.83 over 16.17 h, none of it invoiced
        with db.connect(self.db_path) as conn:
            un = mi.unbilled(conn, date(2026, 8, 26))
        # 56584, not 56583: each entry is rounded to the cent as Upwork prints it
        # (29.166667 -> 2917), so a week's total can sit a cent or two off the
        # invoice Upwork eventually writes. It is an estimate of money coming.
        self.assertEqual((un["charges_minor"], un["hours"], un["since"]), (56584, 16.17, "2026-08-24"))
        self.assertEqual([c["title"] for c in un["by_contract"]],
                         ["Ai Automation Engineer", "Ai Automations sales outbound & Inbound"])
        # re-reading the same week changes nothing
        m2 = mi.run(_Ctx(self.db_path), self._session(), ORG, today=date(2026, 8, 26))
        self.assertEqual((m2["timesheets"]["inserted"], m2["timesheets"]["updated"]), (0, 5))

    def test_milestones_read_state_not_funded_amount_and_stall_becomes_a_card(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO contracts (id, title, upwork_status, client_company, profile)"
                         " VALUES ('43986743', 'AI automation architect', 'ACTIVE', 'Mustafa A', 'personal')")
        s = self._session()
        m = mi.run(_Ctx(self.db_path), s, ORG, today=date(2026, 8, 26))
        self.assertEqual((m["milestones"]["contracts_read"], m["milestones"]["inserted"]), (1, 3))
        ms = {r["sequence_id"]: r for r in self.rows("SELECT * FROM upwork_milestones")}
        self.assertEqual((ms[1]["state"], ms[1]["paid_minor"]), ("Paid", 150000))
        # fundedAmount says 1500 on a NOT-funded milestone — state is the truth
        self.assertEqual((ms[2]["state"], ms[2]["funded_minor"], ms[2]["paid_minor"]),
                         ("NotFunded", 150000, 0))
        self.assertEqual(ms[2]["due_dt"][:10], "2026-07-15")
        # the contract gained what the search could not carry
        c = self.rows("SELECT contract_type, job_id FROM contracts WHERE id='43986743'")[0]
        self.assertEqual((c["contract_type"], c["job_id"]), ("FIXED", "2058937200704482542"))
        self.assertEqual(m["stalled_milestones"], 2)
        cards = rules.rule_stalled_milestone(self.db_path)
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0]["title"], "Unfunded milestone 2: $1,500.00 — Mustafa A")
        self.assertEqual(cards[1]["title"], "Unfunded milestone 3: $1,500.00 — Mustafa A")
        self.assertEqual(cards[0]["client"], "Mustafa A")
        self.assertEqual(cards[0]["money_at_stake"], 1500.0)
        self.assertEqual(cards[0]["due"], "2026-07-15")
        self.assertIn("never funded this milestone", cards[0]["action"])
        # a milestone that gets funded and paid stops being a card
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE upwork_milestones SET state='Paid' WHERE sequence_id IN (2,3)")
        self.assertEqual(rules.rule_stalled_milestone(self.db_path), [])

    def test_contract_details_are_re_read_oldest_first_and_bounded(self):
        with db.connect(self.db_path) as conn:
            for i in range(4):
                conn.execute("INSERT INTO contracts (id, title, upwork_status, start_dt)"
                             " VALUES (?,?, 'ACTIVE', ?)", (f"c{i}", f"t{i}", f"2026-0{i+1}-01"))
            conn.execute("INSERT INTO contracts (id, title, upwork_status) VALUES"
                         " ('closed1', 'gone', 'CLOSED')")
            self.assertEqual(len(mi.contracts_to_read(conn, 10)), 4)     # never CLOSED
            self.assertEqual(len(mi.contracts_to_read(conn, 2)), 2)      # bounded
            conn.execute("INSERT INTO upwork_milestones (id, contract_id, state, updated_at)"
                         " VALUES ('m1', 'c0', 'Paid', datetime('now'))")
            self.assertNotIn("c0", mi.contracts_to_read(conn, 10))       # settled + read today
            conn.execute("UPDATE upwork_milestones SET updated_at=datetime('now','-30 days')")
            self.assertIn("c0", mi.contracts_to_read(conn, 10))          # stale, read again

    def test_money_still_moving_is_re_read_every_run_not_every_five_days(self):
        """Abhijeet, 2026-08-27: "mustafa just funded and released the 1500
        milestone" — and v3 still said $3,000 was unfunded, because his
        contract had been read the day before and was not due again for five.
        A contract with a milestone that is neither Paid nor Cancelled is read
        EVERY run; that is the only place the number can change."""
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO contracts (id, upwork_status, start_dt) VALUES"
                         " ('live', 'ACTIVE', '2026-06-01'), ('done', 'ACTIVE', '2026-06-02')")
            conn.execute("INSERT INTO upwork_milestones (id, contract_id, state, updated_at)"
                         " VALUES ('a', 'live', 'NotFunded', datetime('now')),"
                         "        ('b', 'done', 'Paid', datetime('now'))")
            due = mi.contracts_to_read(conn, 10)
            self.assertIn("live", due)                  # read minutes ago, still due
            self.assertNotIn("done", due)               # settled and fresh -> skip
            self.assertEqual(due[0], "live")            # and it comes FIRST
            conn.execute("UPDATE upwork_milestones SET state='Paid' WHERE id='a'")
            self.assertEqual(mi.contracts_to_read(conn, 10), [])

    def test_the_limit_applies_to_what_survives_the_filter(self):
        """The floor used to be applied in Python AFTER the SQL had taken
        `limit` rows, so a run whose oldest few happened to be fresh read fewer
        contracts than it was allowed to while others sat waiting."""
        with db.connect(self.db_path) as conn:
            for i in range(4):
                conn.execute("INSERT INTO contracts (id, upwork_status, start_dt)"
                             " VALUES (?, 'ACTIVE', '2026-06-01')", (f"c{i}",))
                # c0/c1 settled and freshly read; c2/c3 settled and long stale
                age = "now" if i < 2 else "now','-30 days"
                conn.execute("INSERT INTO upwork_milestones (id, contract_id, state,"
                             f" updated_at) VALUES (?,?, 'Paid', datetime('{age}'))",
                             (f"m{i}", f"c{i}"))
            self.assertEqual(sorted(mi.contracts_to_read(conn, 2)), ["c2", "c3"])

    def test_a_short_window_is_halved_until_each_page_fits(self):
        """The live miss on 2026-08-26: 14 days held 24 entries, the tool
        returned the oldest 10, and the current week — the whole point of
        `unbilled` — was invisible. The window is now the paging unit."""
        full = fx("timesheets")["week_2026_08_24"]
        short = fx("timesheets")["week_2026_08_17_page1"]

        def answer(args):
            p = args["params"]
            return short if p["from_date"] <= "2026-08-19" and p["to_date"] >= "2026-08-22" else full

        s = _Session({("get_freelancer_financials", "timesheets"): answer})
        logs = []
        got = mi.fetch_timesheets(s, ORG, "2026-08-17", "2026-08-23", logs.append)
        self.assertFalse(got["truncated"])
        self.assertEqual(got["total"], 17)                       # the root count, kept
        self.assertTrue(any("17 entries, 3 read" in m for m in logs))
        self.assertEqual([(a["params"]["from_date"], a["params"]["to_date"])
                          for _t, a in s.calls][:3],
                         [("2026-08-17", "2026-08-23"), ("2026-08-17", "2026-08-20"),
                          ("2026-08-21", "2026-08-23")])
        self.assertEqual(len(got["edges"]), 5)                   # deduped by wire cursor

    def test_a_day_that_still_overflows_is_reported_not_dropped(self):
        short = json.loads(json.dumps(fx("timesheets")["week_2026_08_17_page1"]))
        s = _Session({("get_freelancer_financials", "timesheets"): short})
        logs = []
        got = mi.fetch_timesheets(s, ORG, "2026-08-17", "2026-08-18", logs.append)
        self.assertTrue(got["truncated"])
        self.assertTrue(any("entries in one day" in m for m in logs))

    def test_the_call_budget_is_reported_when_spent(self):
        short = json.loads(json.dumps(fx("timesheets")["week_2026_08_17_page1"]))
        s = _Session({("get_freelancer_financials", "timesheets"): short})
        logs = []
        got = mi.fetch_timesheets(s, ORG, "2026-08-01", "2026-08-31", logs.append, max_calls=4)
        self.assertEqual(len(s.calls), 4)
        self.assertTrue(got["truncated"])
        self.assertTrue(any("call budget spent" in m for m in logs))

    def test_a_text_answer_fails_loud(self):
        with self.assertRaises(RuntimeError):
            mi.fetch_timesheets(_Session({("get_freelancer_financials", "timesheets"): {"text": "boom"}}),
                                ORG, "2026-08-17", "2026-08-23", lambda m: None)

    def test_released_money_that_has_no_earning_row_yet_is_still_shown(self):
        """Abhijeet, 2026-08-27: "these 1500, why aren't they showing up on the
        current month revenue". Because Upwork marks a milestone Paid when the
        client releases it and only writes the earning days later. In between,
        the money was in no bucket at all: not stalled, not open, not revenue."""
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO contracts (id, upwork_status, title) VALUES"
                         " ('c1','ACTIVE','AI automation architect(Airtable + make.com + x')")
            for seq, state in ((1, "Paid"), (2, "Paid"), (3, "NotFunded")):
                conn.execute("INSERT INTO upwork_milestones (id, contract_id, sequence_id,"
                             " state, amount_minor) VALUES (?,?,?,?,150000)",
                             (f"m{seq}", "c1", seq, state))
            # milestone 1 landed in July; milestone 2 was released today
            conn.execute("INSERT INTO transactions (record_id, dedup_key, amount_minor,"
                         " pnl_bucket, description, creation_dt) VALUES ('t1','t1',150000,"
                         " 'revenue','AI automation architect(Airtable + make.com + x',"
                         " '2026-07-30')")
            got = mi.released_clearing(conn)
        self.assertEqual([r["sequence_id"] for r in got], [2])   # counted, not matched
        self.assertEqual(got[0]["amount_minor"], 150000)

    def test_every_released_milestone_that_landed_stays_off_the_list(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO contracts (id, upwork_status, title) VALUES"
                         " ('c1','ACTIVE','Airtable Configuration and Automation')")
            conn.execute("INSERT INTO upwork_milestones (id, contract_id, sequence_id,"
                         " state, amount_minor) VALUES ('m1','c1',1,'Paid',50000)")
            conn.execute("INSERT INTO transactions (record_id, dedup_key, amount_minor,"
                         " pnl_bucket, assignment_company, creation_dt) VALUES"
                         " ('t1','t1',50000,'revenue','Airtable Configuration and Automation',"
                         " '2026-08-01')")
            self.assertEqual(mi.released_clearing(conn), [])
            # a milestone with no title to join on is never guessed at
            conn.execute("UPDATE contracts SET title=NULL WHERE id='c1'")
            self.assertEqual(len(mi.released_clearing(conn)), 1)

    def test_release_is_stamped_the_first_time_it_is_seen_and_never_moved(self):
        """Upwork's milestone node carries no paid-at, so the release date is
        when the rail first SEES Paid (migration 036). It dates the revenue, so
        it must not drift on every later read."""
        node = {"id": "m1", "sequenceId": 1, "description": "x",
                "dueDateTime": "2026-07-15T00:00:00.000Z", "state": "NotFunded",
                "state_label": "Not funded",
                "depositAmount": {"rawValue": "1500.00"},
                "fundedAmount": {"rawValue": "1500.00"},
                "paid": {"rawValue": "0.00"}}
        with db.connect(self.db_path) as conn:
            row = mi.milestone_rows({"offer": {"milestones": [node]}})[0]
            self.assertEqual(mi.upsert_milestone(conn, "c1", row), "inserted")
        self.assertIsNone(self.rows("SELECT released_at r FROM upwork_milestones")[0]["r"])
        node["state"], node["paid"] = "Paid", {"rawValue": "1500.00"}
        row = mi.milestone_rows({"offer": {"milestones": [node]}})[0]
        with db.connect(self.db_path) as conn:
            self.assertEqual(mi.upsert_milestone(conn, "c1", row), "paid")
        stamped = self.rows("SELECT released_at r FROM upwork_milestones")[0]["r"]
        self.assertIsNotNone(stamped)
        with db.connect(self.db_path) as conn:          # read it again, unchanged
            mi.upsert_milestone(conn, "c1", row)
        self.assertEqual(self.rows("SELECT released_at r FROM upwork_milestones")[0]["r"],
                         stamped)

    def test_released_fixed_price_counts_as_revenue_in_the_month_released(self):
        """Abhijeet, 2026-08-27: "we can consider that fifteen hundred as the
        revenue … I never had this situation where a fixed price was reversed."
        Hourly is deliberately NOT accrued this way — his call."""
        from modules.cockpit import data
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO contracts (id, upwork_status, title)"
                         " VALUES ('c1','ACTIVE','A fixed price job')")
            conn.execute("INSERT INTO upwork_milestones (id, contract_id, sequence_id,"
                         " state, amount_minor, released_at) VALUES"
                         " ('m1','c1',1,'Paid',150000,'2026-08-27 18:00:00')")
        minor, rows = data.released_revenue_minor(self.db_path, "2026-08")
        self.assertEqual((minor, len(rows)), (150000, 1))
        self.assertEqual(data.released_revenue_minor(self.db_path, "2026-07")[0], 0)
        # once the earning lands it stops being an accrual — no double count
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO transactions (record_id, dedup_key, amount_minor,"
                         " pnl_bucket, description, creation_dt) VALUES ('t1','t1',150000,"
                         " 'revenue','A fixed price job','2026-09-02')")
        self.assertEqual(data.released_revenue_minor(self.db_path, "2026-08")[0], 0)

    def test_a_release_we_cannot_date_is_never_counted_into_a_month(self):
        from modules.cockpit import data
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO contracts (id, upwork_status, title)"
                         " VALUES ('c1','ACTIVE','An older job')")
            conn.execute("INSERT INTO upwork_milestones (id, contract_id, sequence_id,"
                         " state, amount_minor) VALUES ('m1','c1',1,'Paid',150000)")
        self.assertEqual(data.released_revenue_minor(self.db_path, "2026-08")[0], 0)
        with db.connect(self.db_path) as conn:          # but it still SHOWS
            self.assertEqual(len(mi.released_clearing(conn)), 1)

    # ── the 31 Aug defect ────────────────────────────────────────────────────

    def _mustafa(self, conn):
        """The real shape: a $4,500 fixed-price contract, three $1,500
        milestones, 1 and 2 released, 3 never funded."""
        conn.execute("INSERT INTO contracts (id, upwork_status, title, client_company)"
                     " VALUES ('43986743','ACTIVE','AI automation architect(Airtable +"
                     " make.com + Gohigh level + Evoto','Mustafa Ahmed, Mustafa A')")
        for seq, state in ((1, "Paid"), (2, "Paid"), (3, "NotFunded")):
            conn.execute("INSERT INTO upwork_milestones (id, contract_id, sequence_id,"
                         " state, amount_minor, due_dt) VALUES (?,?,?,?,150000,?)",
                         (f"m{seq}", "43986743", seq, state,
                          f"2026-07-{seq * 7 + 1:02d}T00:00:00.000Z"))

    def test_upworks_own_invoice_wording_counts_as_the_earning_landing(self):
        """THE 2026-08-31 DEFECT. Abhijeet: "I just ran upwork finance activity
        and 1500$ is cleared even I withdraewd why does it still show like
        this". It did land — record 946830746, 26 Aug. Upwork writes
        "Invoice for <title>: Milestone 2 - ..." on every fixed-price earning
        the API and the MCP return, and the join was anchored at the START of
        the description, so it matched only the CSV export's wording and missed
        every MCP row. The milestone read as clearing for five days AND was
        counted into August revenue on top of its own transaction."""
        with db.connect(self.db_path) as conn:
            self._mustafa(conn)
            conn.execute(
                "INSERT INTO transactions (record_id, dedup_key, amount_minor, pnl_bucket,"
                " description, assignment_company, creation_dt) VALUES"
                " ('938207313','a',150000,'revenue','AI automation architect(Airtable +"
                " make.com + Gohigh level + Evoto - Contract earnings','','2026-07-30')")
            conn.execute(
                "INSERT INTO transactions (record_id, dedup_key, amount_minor, pnl_bucket,"
                " description, assignment_company, creation_dt) VALUES"
                " ('946830746','b',150000,'revenue','Invoice for AI automation"
                " architect(Airtable + make.com + Gohigh level + Evoto: Milestone 2 -"
                " Deliverables * Updated KPI dashboards','Mustafa A','2026-08-26')")
            self.assertEqual(mi.released_clearing(conn), [])
            # and the milestone he was really being shown is a DIFFERENT one
            self.assertEqual([r["sequence_id"] for r in mi.stalled(conn, date(2026, 8, 31))],
                             [3])

    def test_the_milestone_number_beats_the_order_the_money_arrives_in(self):
        """Counting off oldest-first is only a fallback. When Upwork names the
        milestone, a later one landing first must not settle an earlier one."""
        with db.connect(self.db_path) as conn:
            self._mustafa(conn)
            conn.execute(
                "INSERT INTO transactions (record_id, dedup_key, amount_minor, pnl_bucket,"
                " description, creation_dt) VALUES ('t2','t2',150000,'revenue',"
                " 'Invoice for AI automation architect(Airtable + make.com + Gohigh level"
                " + Evoto: Milestone 2 - x','2026-08-26')")
            self.assertEqual([r["sequence_id"] for r in mi.released_clearing(conn)], [1])

    def test_a_numbered_earning_and_a_csv_one_settle_both_milestones(self):
        with db.connect(self.db_path) as conn:
            self._mustafa(conn)
            conn.execute(
                "INSERT INTO transactions (record_id, dedup_key, amount_minor, pnl_bucket,"
                " description, creation_dt) VALUES ('t1','t1',150000,'revenue',"
                " 'AI automation architect(Airtable + make.com + Gohigh level + Evoto -"
                " Contract earnings','2026-07-30')")
            conn.execute(
                "INSERT INTO transactions (record_id, dedup_key, amount_minor, pnl_bucket,"
                " description, creation_dt) VALUES ('t2','t2',150000,'revenue',"
                " 'Invoice for AI automation architect(Airtable + make.com + Gohigh level"
                " + Evoto: Milestone 2 - x','2026-08-26')")
            self.assertEqual(mi.released_clearing(conn), [])

    def test_a_landed_milestone_is_not_counted_into_revenue_a_second_time(self):
        """The expensive half of the same defect: `released_revenue_minor` reads
        this list AS revenue, so a false "clearing" is $1,500 counted twice in
        the month total, not merely an extra line on a screen."""
        from modules.cockpit import data
        with db.connect(self.db_path) as conn:
            self._mustafa(conn)
            conn.execute("UPDATE upwork_milestones SET released_at='2026-08-27 12:00:00'"
                         " WHERE id='m2'")
            conn.execute(
                "INSERT INTO transactions (record_id, dedup_key, amount_minor, pnl_bucket,"
                " description, creation_dt, billed_dt) VALUES ('t1','t1',150000,'revenue',"
                " 'AI automation architect(Airtable + make.com + Gohigh level + Evoto -"
                " Contract earnings','2026-07-30','2026-07-25')")
        self.assertEqual(data.released_revenue_minor(self.db_path, "2026-08")[0], 150000)
        with db.connect(self.db_path) as conn:          # then milestone 2 lands
            conn.execute(
                "INSERT INTO transactions (record_id, dedup_key, amount_minor, pnl_bucket,"
                " description, creation_dt, billed_dt) VALUES ('t2','t2',150000,'revenue',"
                " 'Invoice for AI automation architect(Airtable + make.com + Gohigh level"
                " + Evoto: Milestone 2 - x','2026-08-26','2026-08-26')")
        self.assertEqual(data.released_revenue_minor(self.db_path, "2026-08")[0], 0)

    def test_the_screen_can_tell_two_milestones_of_one_client_apart(self):
        """Both lines said "$1,500.00 - Mustafa Ahmed, Mustafa A" and read as
        one number shown twice. They were milestones 3 and 2."""
        from modules.cockpit import data
        with db.connect(self.db_path) as conn:
            self._mustafa(conn)
            conn.execute("UPDATE upwork_milestones SET released_at='2026-08-27 12:00:00'"
                         " WHERE id='m2'")
        f = data._money_in_flight(self.db_path)["milestones"]
        self.assertEqual([x["seq"] for x in f["stalled"]], [3])
        self.assertEqual([x["seq"] for x in f["clearing"]], [1, 2])
        js = (ROOT / "modules" / "cockpit" / "static" / "js" / "sections" / "money.js").read_text()
        self.assertIn("milestone ${x.seq}", js)

    def test_config_bounds(self):
        self.assertEqual((config.MCP_TIMESHEET_DAYS, config.MCP_MILESTONE_MAX_GETS,
                          config.MCP_CONTRACT_DETAIL_DAYS), (14, 8, 5))
        self.assertEqual(config.MCP_TIMESHEET_MAX_CALLS, 12)
        self.assertEqual((config.PROFILE_RATE_USD, config.BID_RATE_USD), (51.50, 50.00))
        self.assertEqual(config.MCP_OFFER_PENDING_STATES, ("awaiting_your_acceptance",))
        self.assertTrue((ROOT / "core" / "migrations" / "033_upwork_inflight.sql").is_file())
        for r in (rules.rule_upwork_waiting, rules.rule_stalled_milestone):
            self.assertIn(r, rules.ALL_RULES)


if __name__ == "__main__":
    unittest.main()
