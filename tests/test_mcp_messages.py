"""Use case F — the message rail (0.4).

Every shape here is the live capture of 2026-08-26 23:55 (his "Yes, all three"),
`tests/fixtures/mcp/messages.json`. What these tests are guarding:

  * the ids are the SAME id space as the GraphQL rail's, so an MCP story
    UPDATES a `messages` row and never twins it;
  * `from_self` is absent (not False) on inbound — direction must read it as
    truthy, never `is False`;
  * a SYSTEM story stamped `from_self: true` must NOT become an outbound
    message, because that would tell him he replied when he did not;
  * the wrapper tags around participant text are stripped on the way in;
  * epoch-milliseconds become the same UTC ISO instant the GraphQL rail writes;
  * a room that has not moved costs no call.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db
from modules.upwork.feeds import mcp_messages as mm
from modules.upwork.feeds import mcp_search

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "mcp"
ORG = config.UPWORK_ORGS["personal"]
ROOM = "room_8987ec8ac9beae4d85d0e7653911dd61"


def fx(name: str = "messages") -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


class _Session:
    """answers: {(tool, action): payload or callable(args)}."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

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
        self.tmp = Path(tempfile.mkdtemp(prefix="mcp-messages-"))
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

    def session(self, rooms=None):
        """The real tool answers per room, so the fake must too: the captured
        thread for Dhanya's contract room, and for every other room a single
        inbound story carrying exactly the id its room list advertised."""
        page = rooms if rooms is not None else fx()["list_rooms"]
        by_room = {}
        for r in page["data"]["rooms"]:
            latest = r.get("latestStory") or {}
            by_room[r["id"]] = {"data": {"roomStories": {
                "edges": [{"cursor": latest.get("id"),
                           "node": {"id": latest.get("id"),
                                    "createdDateTime": mm._iso(latest.get("created")),
                                    "from": r.get("roomName"),
                                    "message": latest.get("message")}}],
                "pageInfo": {"endCursor": latest.get("id"), "hasNextPage": False}}},
                "hasMore": False, "status": "ok"}
        by_room[ROOM] = fx()["list_messages"]

        def answer(args):
            return by_room.get(args["params"]["room_id"], {"data": {"roomStories":
                               {"edges": [], "pageInfo": {}}}})

        return _Session({(mm.TOOL, "list_rooms"): page,
                         (mm.TOOL, "list_messages"): answer})


class TestShape(Case):
    def test_the_wrapper_tags_are_stripped_and_the_text_is_still_data(self):
        node = fx()["list_messages"]["data"]["roomStories"]["edges"][0]["node"]
        self.assertIn("untrusted_participant_content", node["message"])
        row = mm.message_row(node, ROOM)
        self.assertNotIn("untrusted", row["message"])
        self.assertTrue(row["message"].startswith("we will get started on the zap"))
        self.assertEqual(row["sender_user_name"], "Abhijeet G.")
        self.assertEqual(row["sender_org_name"], "Abhijeet Gandhi")
        self.assertIsNone(mm.unwrap(None))
        self.assertIsNone(mm.unwrap("<untrusted_participant_content>\n\n</untrusted_participant_content>"))

    def test_from_self_is_absent_on_inbound_never_false(self):
        edges = fx()["list_messages"]["data"]["roomStories"]["edges"]
        inbound = edges[2]["node"]
        self.assertNotIn("from_self", inbound)          # the wire OMITS it
        self.assertTrue(edges[0]["node"]["from_self"])

    def test_a_system_story_stamped_as_his_is_not_an_outbound_reply(self):
        """The one that would lie to him: 'The freelancer accepted your offer …
        Sender's note: …' carries from_self=true. Counted as outbound it clears
        an owed-reply card he never answered."""
        edges = fx()["list_messages"]["data"]["roomStories"]["edges"]
        system_but_his = edges[1]["node"]
        self.assertTrue(system_but_his["from_self"])
        self.assertEqual(mm.message_row(system_but_his, ROOM)["direction"], "system")
        self.assertEqual(mm.message_row(edges[0]["node"], ROOM)["direction"], "outbound")
        self.assertEqual(mm.message_row(edges[2]["node"], ROOM)["direction"], "system")
        self.assertTrue(mm.is_system("System event: sent."))
        self.assertFalse(mm.is_system("we will get started on the zap"))
        self.assertFalse(mm.is_system(None))

    def test_epoch_ms_becomes_the_instant_the_graphql_rail_stores(self):
        room = fx()["list_rooms"]["data"]["rooms"][1]
        row = mm.thread_row(room)
        self.assertEqual(row["latest_message_dt"], "2026-08-26T17:07:03.347Z")
        self.assertEqual(row["contract_id"], "44379117")
        self.assertEqual(row["room_name"], "Dhanya Sangolli, Dhanya S")
        self.assertIsNone(mm._iso(None))
        self.assertIsNone(mm._iso("0"))
        self.assertIsNone(mm._iso("not-a-number"))     # never 1970
        self.assertIsNone(mm.thread_row({"roomName": "x"}))

    def test_the_unread_filter_is_a_detector_not_a_recent_list(self):
        """Upwork's own note: the filtered list is ordered by room CREATION.
        Nothing in this rail may treat it as 'the most recent rooms'."""
        empty = fx()["list_rooms_unread_empty"]
        self.assertEqual(empty["data"]["rooms"], [])
        self.assertFalse(empty["hasMore"])
        self.assertIn("ordered by room creation", empty["note"])
        src = (ROOT / "modules" / "upwork" / "feeds" / "mcp_messages.py").read_text()
        self.assertNotIn("unread_only", src.split('"""', 2)[2])   # not in the CODE


class TestRun(Case):
    def test_a_full_sweep_mirrors_rooms_and_reads_only_what_moved(self):
        s = self.session()
        ctx = _Ctx(self.db_path)
        m = mm.run(ctx, s, ORG)
        self.assertEqual(m["rooms"]["seen"], 5)
        self.assertEqual(m["rooms"]["inserted"], 5)
        self.assertEqual(len(self.rows("SELECT 1 FROM threads")), 5)
        # capped at MCP_MESSAGE_MAX_ROOMS, newest first
        self.assertEqual(m["rooms_read"], config.MCP_MESSAGE_MAX_ROOMS)
        first_room_call = [a for t, a in s.calls if a.get("action") == "list_messages"][0]
        self.assertEqual(first_room_call["params"]["room_id"],
                         "room_4a52eb3c795fc42061df689b5e363c47")
        t = self.rows("SELECT * FROM threads WHERE id=?", (ROOM,))[0]
        self.assertEqual((t["contract_id"], t["num_unread"]), ("44379117", 0))
        self.assertEqual(t["latest_message_dt"], "2026-08-26T17:07:03.347Z")

    def test_a_second_sweep_costs_one_call_and_writes_nothing_new(self):
        ctx = _Ctx(self.db_path)
        mm.run(ctx, self.session(), ORG)
        s2 = self.session()
        m2 = mm.run(_Ctx(self.db_path), s2, ORG)
        self.assertEqual(m2["rooms_read"], 0)                     # nothing moved
        self.assertNotIn((mm.TOOL, "list_messages"), s2.actions())  # no detail read
        self.assertEqual(m2["rooms"]["updated"], 5)
        self.assertEqual(m2["stories"]["inserted"], 0)

    def test_an_mcp_story_updates_a_graphql_row_and_never_twins_it(self):
        """The ids are one space. A story the 10:15 poll already wrote must be
        refreshed in place — two rows for one message would double every count
        that reads this table."""
        # an OLDER story the 10:15 poll already wrote, message text not yet in
        # (the room's NEWEST story is still unheld, so the room is read)
        story = "story_82e3d97be34e5de26fad9ee03d4377c9"
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, room_name) VALUES (?, 'x')", (ROOM,))
            conn.execute("INSERT INTO messages (id, thread_id, direction, created_dt)"
                         " VALUES (?,?,?,?)",
                         (story, ROOM, "inbound", "2026-08-26T17:02:27.356Z"))
        m = mm.run(_Ctx(self.db_path), self.session(), ORG)
        self.assertEqual(len(self.rows("SELECT 1 FROM messages WHERE id=?", (story,))), 1)
        self.assertEqual(len(self.rows("SELECT 1 FROM messages WHERE thread_id=?",
                                       (ROOM,))), 3)         # 1 held + 2 newer, no twin
        self.assertEqual(m["stories"]["inserted"], 6)         # 2 here + 1 in each other room
        self.assertEqual(self.rows("SELECT room_name FROM threads WHERE id=?",
                                   (ROOM,))[0]["room_name"], "Dhanya Sangolli, Dhanya S")
        # and the paging stopped AT the story we already held, never past it
        self.assertEqual(self.rows("SELECT direction FROM messages WHERE id=?",
                                   (story,))[0]["direction"], "inbound")

    def test_a_row_the_other_rail_left_thin_is_refreshed_not_twinned(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, room_name) VALUES (?, 'x')", (ROOM,))
            conn.execute("INSERT INTO messages (id, thread_id, direction)"
                         " VALUES ('s1', ?, 'inbound')", (ROOM,))
            r = mm.upsert_message(conn, {"id": "s1", "thread_id": ROOM,
                                         "message": "hello", "created_dt": "2026-08-26T10:00:00Z",
                                         "sender_user_name": "Dhanya S.",
                                         "sender_org_name": "Dhanya S",
                                         "direction": "outbound"})
        self.assertEqual(r, "updated")
        row = self.rows("SELECT * FROM messages WHERE id='s1'")[0]
        self.assertEqual(row["message"], "hello")             # the gap is filled
        self.assertEqual(row["direction"], "inbound")         # the stored verdict stands
        self.assertEqual(len(self.rows("SELECT 1 FROM messages")), 1)

    def test_paging_stops_on_a_repeated_cursor_not_on_hasMore(self):
        """`hasMore` is not trusted anywhere in this codebase — list_offers says
        true on an empty page. A cursor that does not move ends the loop."""
        page = json.loads(json.dumps(fx()["list_rooms"]))
        page["hasMore"] = True
        page["next_cursor"] = "same"
        s = _Session({(mm.TOOL, "list_rooms"): page})
        rooms = mm.fetch_rooms(s, ORG, lambda m: None)
        self.assertEqual(len(s.calls), 2)                     # then the cursor repeats
        self.assertEqual(len(rooms), 5)                       # and the page is deduped

    def test_a_text_answer_fails_loud_on_both_calls(self):
        with self.assertRaises(RuntimeError):
            mm.fetch_rooms(_Session({(mm.TOOL, "list_rooms"): {"text": "boom"}}),
                           ORG, lambda m: None)
        with self.assertRaises(RuntimeError):
            mm.fetch_messages(_Session({(mm.TOOL, "list_messages"): {"text": "boom"}}),
                              ORG, ROOM, lambda m: None)

    def test_the_reply_signal_ignores_system_stories(self):
        mm.run(_Ctx(self.db_path), self.session(), ORG)
        with db.connect(self.db_path) as conn:
            self.assertIsNone(mm.latest_inbound(conn, ROOM))   # both were system/his
            conn.execute("INSERT INTO messages (id, thread_id, direction, created_dt,"
                         " message) VALUES ('s9', ?, 'inbound', '2026-08-26T18:00:00Z',"
                         " 'any update?')", (ROOM,))
            got = mm.latest_inbound(conn, ROOM)
            self.assertEqual(got["id"], "s9")
            self.assertEqual(mm.stale_rooms(conn, hours=100000), 0)   # mirror caught up

    def test_the_dashboard_preview_is_the_free_change_detector(self):
        """The heartbeat's own answer lists the newest rooms with their
        last-message instants. Noticing a reply costs no extra call — only a
        room that moved is read."""
        dash = fx("dashboard_check")["check"]
        rooms = mm.rooms_from_preview(dash)
        # count says 5, the preview carries 2 — it is a PREVIEW, which is why
        # the full list is still swept once an hour
        self.assertEqual(dash["data"]["messages"]["count"], 5)
        self.assertTrue(dash["data"]["messages"]["truncated"])
        self.assertEqual(len(rooms), 2)
        self.assertEqual(rooms[0]["id"], ROOM)
        self.assertEqual(mm.thread_row(rooms[0])["latest_message_dt"],
                         "2026-08-26T17:07:03.347Z")
        self.assertEqual(mm.thread_row(rooms[0])["topic"], "Automated Zap - Weekly report")
        self.assertEqual(mm.rooms_from_preview({}), [])
        self.assertEqual(mm.rooms_from_preview({"data": {"messages": {"rooms": [{}]}}}), [])

    def test_a_quiet_quarter_hour_costs_one_read_once_the_mirror_is_current(self):
        """First heartbeat of the hour pages the full room list; after that the
        dashboard's own preview is the detector, and a quarter hour in which
        nothing moved costs exactly the one read the pulse was already making."""
        from modules.upwork.feeds import mcp_pulse as mp
        dash = json.loads(json.dumps(fx("dashboard_check")["check"]))
        base = self.session()

        def sess():
            s = _Session(dict(base.answers))
            s.answers[("get_freelancer_dashboard", "check")] = dash
            return s

        s1 = sess()
        m = mp.run(_Ctx(self.db_path), session_factory=lambda: s1)
        self.assertEqual(m["messages"]["source"], "list")
        self.assertEqual(m["messages"]["rooms"]["inserted"], 5)

        s2 = sess()
        m2 = mp.run(_Ctx(self.db_path), session_factory=lambda: s2)
        self.assertEqual(m2["messages"]["source"], "preview")     # same IST hour
        self.assertEqual(m2["messages"]["rooms_read"], 0)         # nothing moved
        self.assertEqual(s2.actions(), [("get_freelancer_dashboard", "check")])

    def test_a_reply_on_a_previewed_room_is_read_without_a_room_list_call(self):
        from modules.upwork.feeds import mcp_pulse as mp
        base = self.session()
        s1 = _Session(dict(base.answers))
        mm.run(_Ctx(self.db_path), s1, ORG)                       # mirror current

        dash = json.loads(json.dumps(fx("dashboard_check")["check"]))
        dash["data"]["messages"]["rooms"][0]["last_message_at"] = "2026-08-26T19:30:00.000Z"
        reply = {"data": {"roomStories": {"edges": [{"cursor": "story_new", "node": {
            "id": "story_new", "createdDateTime": "2026-08-26T19:30:00.000Z",
            "from": "<untrusted_participant_content>\nDhanya S.\n</untrusted_participant_content>",
            "message": "<untrusted_participant_content>\nany update?\n</untrusted_participant_content>"}}],
            "pageInfo": {"endCursor": "story_new", "hasNextPage": False}}},
            "hasMore": False, "status": "ok"}
        s2 = _Session({("get_freelancer_dashboard", "check"): dash,
                       (mm.TOOL, "list_messages"): reply})
        with db.connect(self.db_path) as conn:                    # the hour is spent
            mp.mark_rooms_sweep(conn)
        m = mp.run(_Ctx(self.db_path), session_factory=lambda: s2)
        self.assertEqual(m["messages"]["source"], "preview")
        self.assertEqual(m["messages"]["rooms_read"], 1)          # only the one that moved
        self.assertNotIn((mm.TOOL, "list_rooms"), s2.actions())
        with db.connect(self.db_path) as conn:
            got = mm.latest_inbound(conn, ROOM)
        self.assertEqual((got["id"], got["message"]), ("story_new", "any update?"))

    def test_a_failed_message_pass_degrades_the_pulse_and_does_not_fail_it(self):
        from modules.upwork.feeds import mcp_pulse as mp
        dash = json.loads(json.dumps(fx("dashboard_check")["check"]))
        s = _Session({("get_freelancer_dashboard", "check"): dash,
                      (mm.TOOL, "list_rooms"): {"text": "boom"}})
        ctx = _Ctx(self.db_path)
        m = mp.run(ctx, session_factory=lambda: s)
        self.assertEqual(m["messages"], {"status": "error"})
        self.assertEqual([k for k, _s, _d in ctx.anomalies], ["messages_pass_failed"])
        self.assertIn("messages_pass_failed", config.source_by_key("upwork_pulse")["degrade_kinds"]
                      if hasattr(config, "source_by_key") else
                      [k for src in config.SOURCES if src["key"] == "upwork_pulse"
                       for k in src["degrade_kinds"]])

    def test_a_story_the_tool_never_returns_is_asked_for_once_not_for_ever(self):
        """Upwork can advertise a story in the room list and then not return it
        (deleted, or past the page budget). Without the attempt marker that room
        would be re-read every quarter hour until the end of time."""
        page = json.loads(json.dumps(fx()["list_rooms"]))
        page["data"]["rooms"] = page["data"]["rooms"][:1]
        page["hasMore"] = False
        silent = {"data": {"roomStories": {"edges": [], "pageInfo": {}}},
                  "hasMore": False, "status": "ok"}
        s = _Session({(mm.TOOL, "list_rooms"): page, (mm.TOOL, "list_messages"): silent})
        m = mm.run(_Ctx(self.db_path), s, ORG)
        self.assertEqual(m["rooms_read"], 1)
        self.assertEqual(m["stories"]["read"], 0)
        s2 = _Session({(mm.TOOL, "list_rooms"): page, (mm.TOOL, "list_messages"): silent})
        m2 = mm.run(_Ctx(self.db_path), s2, ORG)
        self.assertEqual(m2["rooms_read"], 0)                       # asked once
        self.assertEqual(s2.actions(), [(mm.TOOL, "list_rooms")])
        with db.connect(self.db_path) as conn:
            self.assertEqual(mm.stale_rooms(conn, hours=100000), 0)  # not a gap: we asked

    def test_config_bounds(self):
        self.assertEqual((config.MCP_ROOMS_PAGE, config.MCP_ROOMS_MAX_PAGES,
                          config.MCP_MESSAGE_MAX_ROOMS), (10, 3, 5))
        self.assertEqual((config.MCP_MESSAGES_PAGE, config.MCP_MESSAGES_MAX_PAGES), (20, 3))
        self.assertEqual(config.MCP_MESSAGE_SYSTEM_MARKERS,
                         ("System event:", "Sender's note:"))
        self.assertIn("get_messages",
                      __import__("modules.upwork.feeds.mcp_client", fromlist=["x"]).READ_TOOLS)


if __name__ == "__main__":
    unittest.main()
