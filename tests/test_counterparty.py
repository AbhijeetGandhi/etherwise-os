"""One person, several rooms.

Abhijeet, 2026-08-27: *"if I'm talking with Tanya, and we have an old chat, but
now they send out a new contract, and it starts the new chat … the context
moves on to the new chat. But then in the Today tab, we are still seeing if my
reply was pending on the old chat."*

The live cases these are modelled on, all three real that morning:

  Dhanya Sangolli   pending draft on a room last touched 30 Jul; he spoke to
                    her 26 Aug and started a $300 contract that day
  Rocky Choy        pending draft on a room he never replied in at all; spoke
                    to Rocky 25 Aug in another room
  Royan Stomberg    draft on the 16 Aug room; answered on the 17th, next door

What is guarded: the key groups a person's rooms and never merges two people;
`nudges.followup_key` still returns byte-for-byte what it did (re-keying a live
card strands the founder's Done); the owed-reply decision sees EVERY room;
a room we spoke last in stops counting as an obligation; and a stale draft is
superseded rather than deleted, never on an unparseable timestamp.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import config, db
from modules.chief_of_staff import evidence, nudges, rules
from modules.upwork import counterparty as cp

NOW = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)


def ago(days=0, hours=0) -> str:
    return (NOW - timedelta(days=days, hours=hours)).isoformat().replace("+00:00", "Z")


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="counterparty-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def room(self, tid, name, latest=None, client_id=None):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, room_name, latest_message_dt,"
                         " client_id, bucket) VALUES (?,?,?,?,'owed')",
                         (tid, name, latest, client_id))
            cp.refresh(conn, [tid])

    def msg(self, mid, tid, direction, at, text="hi"):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO messages (id, thread_id, direction,"
                         " created_dt, message) VALUES (?,?,?,?,?)",
                         (mid, tid, direction, at, text))

    def draft(self, tid, written, status="pending"):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO drafts (thread_id, draft_kind, body,"
                         " generated_at, sent_status) VALUES (?,'owed','x',?,?)",
                         (tid, written, status))

    def rows(self, sql, args=()):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, args)]


class TestKey(Case):
    def test_the_room_name_s_first_field_is_the_person(self):
        self.assertEqual(cp.slug_for("Dhanya Sangolli, Dhanya S"), "dhanya-sangolli")
        self.assertEqual(cp.slug_for("Rocky Choy"), "rocky-choy")
        self.assertEqual(cp.slug_for("Royan Stomberg, Ryan Stomberg"), "royan-stomberg")
        self.assertEqual(cp.slug_for(None), "")
        self.assertEqual(cp.slug_for("   ,  "), "")

    def test_an_attributed_thread_keys_on_the_client_and_an_unnamed_one_alone(self):
        self.assertEqual(cp.key_for("globatize", "Nick E.", "r1"), "c:globatize")
        self.assertEqual(cp.key_for(None, "Nick Esquivel, Globaltize", "r1"),
                         "p:nick-esquivel")
        # no usable name -> a group of ONE, never a pool of every unnamed room
        self.assertEqual(cp.key_for(None, None, "r1"), "t:r1")
        self.assertEqual(cp.key_for(None, "", "r2"), "t:r2")
        self.assertNotEqual(cp.key_for(None, None, "r1"), cp.key_for(None, None, "r2"))

    def test_the_followup_key_is_byte_for_byte_what_it_was(self):
        """Re-keying a live card would strand the founder's Done under the old
        key — the whole reason the key moved here instead of being redefined."""
        self.assertEqual(nudges.followup_key("globatize", "Nick E.", "r1"),
                         "followup:c:globatize")
        self.assertEqual(nudges.followup_key(None, "Dhanya Sangolli, Dhanya S", "r1"),
                         "followup:p:dhanya-sangolli")
        self.assertEqual(nudges.followup_key(None, None, "r1"), "followup:r1")
        self.assertEqual(nudges.followup_key_forms(None, "Rocky Choy", "r9"),
                         ["followup:p:rocky-choy", "followup:r9"])
        self.assertEqual(nudges.followup_key_forms("c1", "Rocky Choy", "r9"),
                         ["followup:c:c1", "followup:p:rocky-choy", "followup:r9"])

    def test_the_key_is_recomputed_when_attribution_changes_it(self):
        self.room("r1", "Nick Esquivel, Globaltize")
        self.assertEqual(self.rows("SELECT counterparty_key k FROM threads")[0]["k"],
                         "p:nick-esquivel")
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE threads SET client_id='globatize' WHERE id='r1'")
            self.assertEqual(cp.refresh(conn), 1)
            self.assertEqual(cp.refresh(conn), 0)              # idempotent
        self.assertEqual(self.rows("SELECT counterparty_key k FROM threads")[0]["k"],
                         "c:globatize")


class TestExpand(Case):
    def setUp(self):
        super().setUp()
        # Dhanya's four rooms; the talking moved to the newest
        self.room("d_old", "Dhanya Sangolli, Dhanya S", ago(28))
        self.room("d_mid", "Dhanya Sangolli, Dhanya S", ago(14))
        self.room("d_new", "Dhanya Sangolli, Dhanya S", ago(0, 10))
        self.room("other", "Rocky Choy", ago(2))

    def test_one_room_expands_to_every_room_that_person_holds(self):
        with db.connect(self.db_path) as conn:
            got = cp.expand(conn, ["d_old"])
        self.assertEqual(set(got), {"d_old", "d_mid", "d_new"})
        self.assertEqual(got[0], "d_new")                 # newest-active first
        with db.connect(self.db_path) as conn:
            self.assertEqual(set(cp.expand(conn, ["d_old", "other"])),
                             {"d_old", "d_mid", "d_new", "other"})
            self.assertEqual(cp.expand(conn, ["nope"]), ["nope"])   # unknown id survives

    def test_two_different_people_never_merge(self):
        with db.connect(self.db_path) as conn:
            self.assertNotIn("other", cp.expand(conn, ["d_new"]))
            self.assertEqual(cp.expand(conn, ["other"]), ["other"])

    def test_last_ours_reads_every_room_and_ignores_system_stories(self):
        self.msg("m1", "d_old", "outbound", ago(28))
        self.msg("m2", "d_new", "outbound", ago(0, 10))
        self.msg("m3", "d_new", "system", ago(0, 1))      # Upwork's own voice
        with db.connect(self.db_path) as conn:
            self.assertEqual(cp.last_ours(conn, "p:dhanya-sangolli"), ago(0, 10))
            self.assertIsNone(cp.last_ours(conn, "p:rocky-choy"))


class TestOwedReply(Case):
    def _dhanya(self):
        """The live shape: an old room with an unsent draft, and a new room
        where he answered yesterday."""
        self.room("d_old", "Dhanya Sangolli, Dhanya S", ago(28))
        self.room("d_new", "Dhanya Sangolli, Dhanya S", ago(0, 10))
        self.msg("i1", "d_old", "inbound", ago(29))       # never answered HERE
        self.msg("i2", "d_new", "inbound", ago(1))
        self.msg("o2", "d_new", "outbound", ago(0, 10))   # answered next door
        self.draft("d_old", "2026-07-30 21:42:00")

    def test_a_reply_in_the_sibling_room_is_seen(self):
        self._dhanya()
        cards = rules.rule_owed_replies(self.db_path, now=NOW)
        self.assertEqual(cards, [])                        # nothing is owed

    def test_the_evidence_covers_every_room_not_just_the_drafted_one(self):
        self._dhanya()
        with db.connect(self.db_path) as conn:
            only_drafted = evidence.counterparty(conn, thread_ids=["d_old"])
            both = evidence.counterparty(conn, thread_ids=cp.expand(conn, ["d_old"]))
        self.assertFalse(only_drafted["answered_since"])    # the old, blind read
        self.assertTrue(both["answered_since"])             # the honest one
        self.assertEqual(both["ours_ts"], ago(0, 10))

    def test_a_room_we_spoke_last_in_is_not_an_obligation(self):
        """Once the group covers ALL of a person's rooms, a long-finished room
        must not drag the age clock backwards and make an answered person look
        MORE overdue."""
        self._dhanya()
        self.msg("o1", "d_old", "outbound", ago(28))        # answer it too
        with db.connect(self.db_path) as conn:
            sig = evidence.upwork_signals(conn, cp.expand(conn, ["d_old"]))
        self.assertIsNone(sig["oldest_inbound"])            # both rooms answered
        self.msg("i3", "d_new", "inbound", ago(0, 2))       # they come back
        with db.connect(self.db_path) as conn:
            sig = evidence.upwork_signals(conn, cp.expand(conn, ["d_old"]))
        self.assertEqual(sig["oldest_inbound"], ago(0, 2))  # only the live room

    def test_a_person_still_waiting_still_gets_the_card(self):
        self.room("w1", "Tousif Ahmed", ago(6))
        self.msg("wi", "w1", "inbound", ago(6))
        self.draft("w1", "2026-08-21 10:00:00")
        cards = rules.rule_owed_replies(self.db_path, now=NOW)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["dedup_key"], "followup:p:tousif-ahmed")
        self.assertEqual(cards[0]["payload"]["thread_ids"], ["w1"])

    def test_the_card_carries_every_room_so_the_conversation_shows_the_new_chat(self):
        self._dhanya()
        with db.connect(self.db_path) as conn:
            conn.execute("DELETE FROM messages WHERE id='o2'")   # he never replied
        cards = rules.rule_owed_replies(self.db_path, now=NOW)
        self.assertEqual(len(cards), 1)
        self.assertEqual(set(cards[0]["payload"]["thread_ids"]), {"d_old", "d_new"})


class TestSupersede(Case):
    def test_a_draft_on_a_room_the_conversation_left_is_superseded(self):
        self.room("d_old", "Dhanya Sangolli, Dhanya S", ago(28))
        self.room("d_new", "Dhanya Sangolli, Dhanya S", ago(0, 10))
        self.msg("o2", "d_new", "outbound", ago(0, 10))
        self.draft("d_old", "2026-07-30 21:42:00")
        with db.connect(self.db_path) as conn:
            gone = cp.supersede_stale(conn)
        self.assertEqual(len(gone), 1)
        self.assertEqual(gone[0]["thread_id"], "d_old")
        row = self.rows("SELECT sent_status FROM drafts")[0]
        self.assertEqual(row["sent_status"], "superseded")   # kept, not deleted

    def test_a_draft_newer_than_our_last_word_is_left_alone(self):
        self.room("r1", "Tousif Ahmed", ago(2))
        self.msg("o1", "r1", "outbound", ago(5))
        self.draft("r1", "2026-08-26 09:00:00")              # written after
        with db.connect(self.db_path) as conn:
            self.assertEqual(cp.supersede_stale(conn), [])
        self.assertEqual(self.rows("SELECT sent_status s FROM drafts")[0]["s"], "pending")

    def test_nothing_is_superseded_on_an_unreadable_timestamp(self):
        self.room("r1", "Tousif Ahmed", ago(2))
        self.msg("o1", "r1", "outbound", "not-a-date")
        self.draft("r1", "2026-07-01 09:00:00")
        with db.connect(self.db_path) as conn:
            self.assertEqual(cp.supersede_stale(conn), [])
            self.assertEqual(cp.supersede_stale(conn, dry_run=True), [])

    def test_dry_run_reports_and_writes_nothing(self):
        self.room("d_old", "Dhanya Sangolli, Dhanya S", ago(28))
        self.room("d_new", "Dhanya Sangolli, Dhanya S", ago(0, 10))
        self.msg("o2", "d_new", "outbound", ago(0, 10))
        self.draft("d_old", "2026-07-30 21:42:00")
        with db.connect(self.db_path) as conn:
            self.assertEqual(len(cp.supersede_stale(conn, dry_run=True)), 1)
        self.assertEqual(self.rows("SELECT sent_status s FROM drafts")[0]["s"], "pending")

    def test_the_migration_and_the_index_exist(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / "core" / "migrations"
                         / "035_counterparty_key.sql").is_file())
        cols = [r["name"] for r in self.rows("PRAGMA table_info(threads)")]
        self.assertIn("counterparty_key", cols)


if __name__ == "__main__":
    unittest.main()
