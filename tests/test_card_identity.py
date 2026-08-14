"""A card's identity must not be derived from mutable attribution without a
forwarding path.

Abhijeet, 2026-08-14, for the THIRD time: "why are rocky choy and dhanya
sangoli again showing up on my today list, I marked them done like 5 times."

He was right, and the cause is structural rather than a bug in any one place.
`nudges.followup_key` derives the card's key from attribution — `followup:p:
<slug>` while a thread is unattributed, `followup:c:<client_id>` once it is —
and the founder's Done is filed under that string. So the work he asked for
(attributing the threads) renamed the card and discarded his decision. Twice
migrated by hand, twice the same failure:

    2026-08-12   room -> person     scripts/today_repair_migrate.py
    2026-08-13   person -> client   not migrated at all

These tests cover the three shapes the NEXT change will take, not just the one
that bit: two clients merging (andrew-wang -> endura happened on 2026-08-13), a
client being renamed, and a thread moving from one client to another. Plus the
two things that must NOT change: a Done still decays on newer cross-channel
evidence, and a Review dismissal still lives in its own namespace.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from core import db
from modules.chief_of_staff import decisions, nudges
from modules.cockpit import actions, data


class _Board(unittest.TestCase):
    """One counterparty, one Upwork room, one pending draft, one Done."""

    DAY = "2026-08-14"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="card-identity-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO threads (id, room_name, topic, bucket, tier,"
                " client_id, latest_message_dt) VALUES ('rA','Andrew Wang',"
                "'Make.com build','owed','HOT','andrew-wang',"
                "'2026-08-01T09:00:00Z')")
            conn.execute(
                "INSERT INTO drafts (thread_id, draft_kind, tier, body,"
                " word_count, sent_status) VALUES ('rA','owed','HOT','Reply',"
                "1,'pending')")
            conn.execute(
                "INSERT INTO messages (id, thread_id, message, created_dt,"
                " direction) VALUES ('m1','rA','ping','2026-08-01T09:00:00Z',"
                "'Inbound')")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── helpers ──────────────────────────────────────────────────────────────
    def keys(self, day=None):
        return [f["item_key"] for f in
                data.today(self.db_path, today=day or self.DAY)["follow_ups"]]

    def card_key(self):
        ks = self.keys()
        self.assertEqual(len(ks), 1, f"expected one card, got {ks}")
        return ks[0]

    def expected_key(self):
        """The key the producers WOULD emit for this thread right now —
        computed from attribution, not read off the board, because after a Done
        the board (correctly) has nothing to read."""
        with db.connect(self.db_path) as conn:
            r = conn.execute("SELECT id, room_name, client_id FROM threads"
                             " WHERE id='rA'").fetchone()
        return nudges.followup_key(r["client_id"], r["room_name"], r["id"])

    def press_done(self):
        actions.nudge(self.db_path, {"item_key": self.card_key(),
                                     "action": "done"}, today=self.DAY)

    def reattribute(self, client_id):
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE threads SET client_id=? WHERE id='rA'",
                         (client_id,))

    def rename_room(self, room_name):
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE threads SET room_name=? WHERE id='rA'",
                         (room_name,))


class TestDecisionSurvivesReKeying(_Board):

    def test_the_live_failure_attribution_renames_the_card(self):
        """Rocky Choy / Dhanya Sangolli, 2026-08-13: unattributed -> attributed.

        The key went followup:p:<name> -> followup:c:<client>, and the Done —
        recorded against the old key — stopped matching."""
        self.reattribute(None)                       # start unattributed
        self.assertEqual(self.card_key(), "followup:p:andrew-wang")
        self.press_done()
        self.assertEqual(self.keys(), [])
        self.reattribute("andrew-wang")              # the work he asked for
        self.assertEqual(self.expected_key(), "followup:c:andrew-wang")
        self.assertEqual(self.keys(), [],
                         "attributing the thread must not undo a Done")

    def test_two_clients_merging(self):
        """andrew-wang -> endura, which really happened on 2026-08-13.

        The old client_id is no longer reachable from the thread, so no amount
        of re-deriving the key finds the decision. Only the SUBJECT does."""
        self.assertEqual(self.card_key(), "followup:c:andrew-wang")
        self.press_done()
        self.assertEqual(self.keys(), [])
        self.reattribute("endura")
        self.assertEqual(self.expected_key(), "followup:c:endura")
        self.assertEqual(self.keys(), [], "a merge must not resurrect a Done")

    def test_a_client_being_renamed(self):
        """The registry id changes; the counterparty does not."""
        self.press_done()
        self.reattribute("andrew-wang-consulting")
        self.assertEqual(self.expected_key(),
                         "followup:c:andrew-wang-consulting")
        self.assertEqual(self.keys(), [], "a rename must not resurrect a Done")

    def test_a_thread_re_attributed_to_a_different_client(self):
        """A mis-attributed room is corrected from one client to another. The
        conversation he dealt with is the same conversation."""
        self.press_done()
        self.reattribute("hero-marketing-agency")
        self.assertEqual(self.expected_key(),
                         "followup:c:hero-marketing-agency")
        self.assertEqual(self.keys(), [])

    def test_the_room_name_changing_does_not_resurrect_it_either(self):
        """The unattributed key is a slug of the room name — Upwork renaming a
        room is a fourth way the key can move."""
        self.reattribute(None)
        self.press_done()
        self.rename_room("Andy Wang")
        self.assertEqual(self.expected_key(), "followup:p:andy-wang")
        self.assertEqual(self.keys(), [])


class TestForwardingDoesNotWeakenTheRules(_Board):
    """The forwarding only changes HOW a decision is found. Everything the
    decision then obeys is unchanged — and each of these is load-bearing."""

    def _client_replies(self, ts="2099-01-01T00:00:00Z"):
        # far-future so it is unambiguously AFTER the Done, whose timestamp is
        # the real clock (nudge_state.updated_at = datetime('now'))
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO messages (id, thread_id, message, created_dt,"
                " direction) VALUES ('m2','rA','new',?,'Inbound')", (ts,))

    def test_a_carried_done_still_decays_on_newer_evidence(self):
        self.press_done()
        self.reattribute("endura")
        self.assertEqual(self.keys(), [])
        self._client_replies()                       # ...they write back
        self.assertEqual(self.keys("2099-01-02"), ["followup:c:endura"],
                         "a carried Done must still yield to a real reply")

    def test_evidence_older_than_the_done_does_not_bring_it_back(self):
        self._client_replies("2026-07-01T09:00:00Z")   # older than the Done
        self.press_done()
        self.reattribute("endura")
        self.assertEqual(self.keys(), [])

    def test_a_carried_snooze_is_still_an_explicit_not_until(self):
        actions.nudge(self.db_path, {"item_key": self.card_key(),
                                     "action": "snooze", "snooze_days": 3},
                      today=self.DAY)
        self.reattribute("endura")
        self.assertEqual(self.keys(), [])                       # 08-14
        self.assertEqual(self.keys("2026-08-16"), [])           # still snoozed
        self.assertEqual(self.keys("2026-08-17"),               # wakes up
                         ["followup:c:endura"])

    def test_a_review_dismissal_never_forwards_into_today(self):
        """review:thread:<id> is a verdict about IDENTITY, absolute and in its
        own namespace. It is keyed on a thread too, so it would forward if the
        namespace gate were dropped — and a Review dismissal silently hiding a
        Today card is exactly the kind of leak nobody would notice."""
        actions.review_thread(self.db_path, {"thread_ids": ["rA"],
                                             "action": "dismiss"})
        self.assertEqual(self.keys(), ["followup:c:andrew-wang"])
        self.assertFalse(decisions.is_followup_key("review:thread:rA"))

    def test_a_decision_on_someone_else_does_not_travel(self):
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO threads (id, room_name, topic, bucket, tier,"
                " client_id, latest_message_dt) VALUES ('rB','Someone Else',"
                "'Other','owed','HOT','other-co','2026-08-01T09:00:00Z')")
            conn.execute(
                "INSERT INTO drafts (thread_id, draft_kind, tier, body,"
                " word_count, sent_status) VALUES ('rB','owed','HOT','R',1,"
                "'pending')")
            conn.execute("INSERT INTO nudge_state (item_key, state, updated_at)"
                         " VALUES ('followup:c:other-co','done',"
                         "'2026-08-13 09:00:00')")
        self.reattribute("endura")
        self.assertIn("followup:c:endura", self.keys())


class TestProducerRespectsACarriedDecision(_Board):
    """The renderer hiding the card is not enough: `write_nudge` is what MINTED
    #1110/#1111 over the founder's Done on 2026-08-13, and the morning brief and
    top_n read that table, not the cockpit overlay."""

    def _mint(self, key, evidence_ts="2026-08-01T09:00:00Z"):
        return nudges.write_nudge(
            self.db_path, source="sales", dept="sales", kind="owed_reply",
            title="Follow up: Andrew Wang", dedup_key=key,
            payload={"thread_ids": ["rA"], "evidence_ts": evidence_ts})

    def test_a_re_keyed_card_is_not_re_minted_over_a_done(self):
        self._mint("followup:c:andrew-wang")
        actions.cos_nudge(self.db_path, {"dedup_key": "followup:c:andrew-wang",
                                         "action": "done"})
        self.reattribute("endura")
        out = self._mint("followup:c:endura")        # the next sweep
        self.assertEqual(out["op"], "suppressed-unchanged")
        self.assertEqual(nudges.top_n(self.db_path, 10), [])

    def test_but_a_real_reply_still_re_mints_it(self):
        self._mint("followup:c:andrew-wang")
        actions.cos_nudge(self.db_path, {"dedup_key": "followup:c:andrew-wang",
                                         "action": "done"})
        self.reattribute("endura")
        out = self._mint("followup:c:endura", evidence_ts="2099-01-01T00:00:00Z")
        self.assertEqual(out["op"], "created")

    def test_an_unrelated_card_is_unaffected(self):
        self._mint("followup:c:andrew-wang")
        actions.cos_nudge(self.db_path, {"dedup_key": "followup:c:andrew-wang",
                                         "action": "done"})
        out = nudges.write_nudge(
            self.db_path, source="sales", dept="sales", kind="owed_reply",
            title="Follow up: Someone", dedup_key="followup:p:someone-else",
            payload={"thread_ids": ["rZ"], "evidence_ts": "2026-08-01T09:00:00Z"})
        self.assertEqual(out["op"], "created")


class TestSubjectRecording(unittest.TestCase):
    """The mechanism itself, in isolation."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="card-subjects-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO threads (id, room_name, client_id) VALUES"
                " ('r1','Rocky Choy','rocky-choy')")
            conn.execute(
                "INSERT INTO threads (id, room_name, client_id) VALUES"
                " ('r2','Rocky Choy','rocky-choy')")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_key_forms_are_the_inverse_of_followup_key(self):
        forms = nudges.followup_key_forms("rocky-choy", "Rocky Choy", "r1")
        self.assertEqual(forms, ["followup:c:rocky-choy",
                                 "followup:p:rocky-choy", "followup:r1"])
        self.assertEqual(nudges.followup_key("rocky-choy", "Rocky Choy", "r1"),
                         forms[0])
        self.assertEqual(nudges.followup_key(None, "Rocky Choy", "r1"), forms[1])
        self.assertEqual(nudges.followup_key(None, None, "r1"), forms[2])

    def test_derivation_recovers_a_pre_attribution_key(self):
        with db.connect(self.db_path) as conn:
            self.assertEqual(decisions.cover(conn, "followup:p:rocky-choy"),
                             {"r1", "r2"})
            self.assertEqual(decisions.current_keys(conn,
                                                    "followup:p:rocky-choy"),
                             {"followup:c:rocky-choy"})

    def test_a_recorded_subject_survives_what_derivation_cannot(self):
        with db.connect(self.db_path) as conn:
            decisions.record(conn, "followup:c:old-id", ["r1"])
            conn.execute("UPDATE threads SET client_id='new-id'")
            self.assertEqual(decisions.current_keys(conn, "followup:c:old-id"),
                             {"followup:c:new-id"})

    def test_the_nudge_prefix_files_under_the_bare_key(self):
        with db.connect(self.db_path) as conn:
            decisions.record(conn, "nudge:followup:c:x", ["r1"])
            self.assertEqual(decisions.recorded(conn, "followup:c:x"), {"r1"})

    def test_non_followup_keys_are_refused(self):
        with db.connect(self.db_path) as conn:
            self.assertEqual(decisions.record(conn, "review:thread:r1", ["r1"]), 0)
            self.assertEqual(decisions.record(conn, "commitment:9", ["r1"]), 0)
            self.assertEqual(decisions.cover(conn, "review:thread:r1"), set())
            self.assertEqual(decisions.aliases(conn, "review:thread:r1", ["r1"]),
                             [])

    def test_forward_map_omits_keys_that_have_not_moved(self):
        with db.connect(self.db_path) as conn:
            fwd = decisions.forward_map(conn, ["followup:c:rocky-choy",
                                               "followup:p:rocky-choy"])
        self.assertNotIn("followup:c:rocky-choy", fwd)
        self.assertEqual(fwd["followup:p:rocky-choy"],
                         ["followup:c:rocky-choy"])


class TestMergeClients(unittest.TestCase):
    """`scripts/merge_clients.py` — the general client-merge path.

    Two merges have happened (andrew-wang -> endura 2026-08-13, servicepeople +
    raphael-mahir -> visa-gate 2026-08-14) and a third is likely. The first was
    a throwaway script in var/agent-scratch, and re-deriving the same table list
    from memory is precisely how a table gets missed — that one repointed
    `offers.client_id`, which is UPWORK's numeric company id, and would have
    corrupted the row had any matched.

    So the script carries a schema guard, and this pins it against the REAL
    migrated schema: when a future migration adds a client-bearing column,
    THIS test fails rather than the merge silently leaving rows behind."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="merge-clients-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _module(self):
        import importlib.util
        p = Path(__file__).resolve().parents[1] / "scripts" / "merge_clients.py"
        spec = importlib.util.spec_from_file_location("merge_clients", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_every_client_bearing_column_is_accounted_for(self):
        mod = self._module()
        with db.connect(self.db_path) as conn:
            self.assertEqual(mod._unmapped_columns(conn), [])

    def test_card_keys_are_derived_from_followup_key_not_spelled_out(self):
        """CLAUDE.md: never add a card key form without adding it to
        `nudges.followup_key_forms`. This merge path must read the form from
        `nudges`, so a new one cannot be left behind here."""
        mod = self._module()
        pairs = dict(mod._card_keys("old-id", "new-id"))
        self.assertEqual(pairs[nudges.followup_key("old-id", None, "")],
                         nudges.followup_key("new-id", None, ""))
        self.assertEqual(pairs["stale_dossier:old-id"], "stale_dossier:new-id")

    def test_the_retired_crm_source_is_deleted_never_repointed(self):
        """`crm.ingest_crm` keys its synthetic source on
        content_hash('crm/<id>') and only clears the facts of the source IT
        owns, so repointing the retired one leaves the survivor with TWO and
        double-counts its revenue facts. The next compile re-derives one."""
        mod = self._module()
        with db.connect(self.db_path) as conn:
            for cid in ("old-id", "new-id"):
                conn.execute(
                    "INSERT INTO sources (source_type, source_ref, content_hash,"
                    " client_id, client_name, content_type, status)"
                    " VALUES ('crm', ?, ?, ?, ?, 'crm', 'ingested')",
                    (f"crm/{cid}", f"h-{cid}", cid, cid))
            sid = conn.execute("SELECT id FROM sources WHERE source_ref=?",
                               ("crm/old-id",)).fetchone()["id"]
            conn.execute("INSERT INTO facts (source_id, client_id, category,"
                         " fact_text, citation, confidence) VALUES (?,?,?,?,?,?)",
                         (sid, "old-id", "numbers", "Revenue $1.00", "CRM", "CONFIRMED"))
            out = mod.merge(conn, "old-id", "new-id", "New", apply=True)
            self.assertEqual(out["moved"]["crm source (deleted)"], 1)
            self.assertEqual(out["moved"]["crm facts (deleted)"], 1)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM sources WHERE source_type='crm'"
                             " AND client_id='new-id'").fetchone()[0], 1)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0], 0)

    def test_a_real_source_and_its_facts_move_with_their_display_name(self):
        mod = self._module()
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id, client_name, content_type, status)"
                " VALUES ('fathom','fathom/1','h1','old-id','Old','client-call',"
                "'extracted')")
            sid = conn.execute("SELECT id FROM sources WHERE source_ref='fathom/1'"
                               ).fetchone()["id"]
            conn.execute("INSERT INTO facts (source_id, client_id, category,"
                         " fact_text, citation, confidence) VALUES (?,?,?,?,?,?)",
                         (sid, "old-id", "overview", "A thing", "fathom/1", "CONFIRMED"))
            conn.execute("INSERT INTO threads (id, room_name, client_id)"
                         " VALUES ('r1','Old, Co','old-id')")
            out = mod.merge(conn, "old-id", "new-id", "New Name", apply=True)
            self.assertEqual(out["moved"]["sources.client_id"], 1)
            self.assertEqual(out["moved"]["facts.client_id"], 1)
            self.assertEqual(out["moved"]["threads.client_id"], 1)
            row = conn.execute("SELECT client_id, client_name FROM sources"
                               " WHERE source_ref='fathom/1'").fetchone()
            self.assertEqual((row["client_id"], row["client_name"]),
                             ("new-id", "New Name"))
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM facts WHERE client_id='old-id'"
                             ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
