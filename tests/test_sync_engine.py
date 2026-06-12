"""Unit tests for core/sync/engine.py — pure planning logic + shadow
execution against a temp v3 DB. No network; the Airtable client is injected.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from core import db
from core.sync import engine


def at_rec(rid, **fields):
    return {"id": rid, "fields": fields}


OWNERSHIP = {
    "title": engine.FieldSpec("Job Title", "system"),
    "score": engine.FieldSpec("Lead Score", "system"),
    "status": engine.FieldSpec("Status", "system"),
    "manual_note": engine.FieldSpec("Notes", "human"),
    "score_override": engine.FieldSpec("Score Override", "human"),
}


class TestWhitelist(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sync-test-"))
        self.db_path = self.tmp / "t.db"
        db.migrate(self.db_path)
        with db.connect(self.db_path) as conn:
            for jid, status in (("2064908810394200829", "Scored"),
                                ("2064908810394200830", "Drafted"),
                                ("2064908810394200831", "ClickUp Created"),
                                ("2064908810394200832", "Skipped"),
                                ("2064908810394200833", "New"),
                                ("2064908810394200834", "Phantom"),
                                ("2064908810394200835", "Fetched")):
                conn.execute(
                    "INSERT INTO scored_jobs (id, title, status) VALUES"
                    " (?, ?, ?)", (jid, f"job {status}", status))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_whitelist_excludes_new_phantom_fetched(self):
        rows = engine.eligible_jobs(self.db_path)
        statuses = {r["status"] for r in rows}
        self.assertEqual(statuses,
                         {"Scored", "Drafted", "ClickUp Created", "Skipped"})

    def test_fabricated_ids_excluded_even_when_status_eligible(self):
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO scored_jobs (id, title, status) VALUES"
                " ('2064823456789012345', 'walk id', 'Scored')")
        rows = engine.eligible_jobs(self.db_path)
        self.assertNotIn("2064823456789012345", [r["id"] for r in rows])


class TestPlanPush(unittest.TestCase):
    def local(self, **kw):
        row = {"id": "2064908810394200829", "title": "Make Build",
               "score": 18, "status": "Scored", "manual_note": "human text",
               "airtable_record_id": None}
        row.update(kw)
        return row

    def test_unmatched_row_becomes_create_with_system_fields_only(self):
        ops = engine.plan_push([self.local()], {}, OWNERSHIP, "Job ID")
        self.assertEqual(len(ops), 1)
        op = ops[0]
        self.assertEqual(op.kind, "create")
        self.assertEqual(op.fields["Job Title"], "Make Build")
        self.assertEqual(op.fields["Lead Score"], 18)
        self.assertNotIn("Notes", op.fields)          # human field never pushed
        self.assertEqual(op.fields["Job ID"], "2064908810394200829")

    def test_recordid_first_match_updates_changed_fields_only(self):
        local = self.local(airtable_record_id="recAAA", score=21)
        at = {"recAAA": at_rec("recAAA", **{"Job ID": "2064908810394200829",
                                            "Job Title": "Make Build",
                                            "Lead Score": 18,
                                            "Status": "Scored"})}
        ops = engine.plan_push([local], at, OWNERSHIP, "Job ID")
        self.assertEqual(len(ops), 1)
        op = ops[0]
        self.assertEqual(op.kind, "update")
        self.assertEqual(op.record_id, "recAAA")
        self.assertEqual(op.fields, {"Lead Score": 21})  # only the diff

    def test_key_fallback_match_when_no_recordid(self):
        at = {"recBBB": at_rec("recBBB", **{"Job ID": "2064908810394200829",
                                            "Lead Score": 18})}
        ops = engine.plan_push([self.local(score=18, title=None,
                                           status=None)], at,
                               OWNERSHIP, "Job ID")
        self.assertEqual(ops, [])    # matched by key, nothing changed

    def test_identical_row_yields_no_op(self):
        local = self.local(airtable_record_id="recAAA")
        at = {"recAAA": at_rec("recAAA", **{"Job ID": "2064908810394200829",
                                            "Job Title": "Make Build",
                                            "Lead Score": 18,
                                            "Status": "Scored"})}
        self.assertEqual(engine.plan_push([local], at, OWNERSHIP, "Job ID"),
                         [])


class TestBatching(unittest.TestCase):
    def test_batches_of_ten(self):
        ops = [engine.Operation("create", None, str(i), {"f": i})
               for i in range(23)]
        chunks = engine.batch(ops)
        self.assertEqual([len(c) for c in chunks], [10, 10, 3])

    def test_payload_always_typecasts(self):
        creates = [engine.Operation("create", None, "1", {"Job Title": "x"})]
        payload = engine.create_payload(creates)
        self.assertTrue(payload["typecast"])
        updates = [engine.Operation("update", "recA", "1", {"Lead Score": 9})]
        payload = engine.update_payload(updates)
        self.assertTrue(payload["typecast"])
        self.assertEqual(payload["records"][0]["id"], "recA")


class TestShadowExecution(unittest.TestCase):
    def test_shadow_records_intents_instead_of_calling_airtable(self):
        ops = [engine.Operation("create", None, "2064908810394200829",
                                {"Job Title": "x"}),
               engine.Operation("update", "recA", "2064908810394200830",
                                {"Lead Score": 9})]
        recorded = []
        calls = []

        def recorder(**kw):
            recorded.append(kw)

        def airtable(method, path, payload):  # must never fire in shadow
            calls.append(path)

        result = engine.execute_push(ops, shadow=True,
                                     record_shadow=recorder,
                                     airtable=airtable, entity="proposals")
        self.assertEqual(calls, [])
        self.assertEqual(len(recorded), 2)
        self.assertEqual(recorded[0]["operation"], "create")
        self.assertEqual(recorded[0]["entity_key"], "2064908810394200829")
        self.assertEqual(result, {"creates": 1, "updates": 1,
                                  "shadow": True})

    def test_live_calls_airtable_in_batches(self):
        ops = [engine.Operation("create", None, str(i), {"F": i})
               for i in range(12)]
        calls = []

        def airtable(method, path, payload):
            calls.append((method, len(payload["records"])))
            return {"records": [{"id": f"rec{i}"}
                                for i in range(len(payload["records"]))]}

        result = engine.execute_push(ops, shadow=False, record_shadow=None,
                                     airtable=airtable, entity="proposals")
        self.assertEqual(calls, [("POST", 10), ("POST", 2)])
        self.assertEqual(result["creates"], 12)


class TestPullHumanFields(unittest.TestCase):
    def test_human_fields_pull_at_wins_with_audit(self):
        local = {"id": "2064908810394200829", "manual_note": "old",
                 "score_override": None, "airtable_record_id": "recAAA"}
        at = {"recAAA": at_rec("recAAA", **{"Notes": "human edited this",
                                            "Score Override": 25})}
        updates, audits = engine.plan_pull([local], at, OWNERSHIP)
        self.assertEqual(updates,
                         [{"id": "2064908810394200829",
                           "manual_note": "human edited this",
                           "score_override": 25}])
        self.assertEqual(len(audits), 2)
        fields_changed = {a["field"] for a in audits}
        self.assertEqual(fields_changed, {"manual_note", "score_override"})
        self.assertEqual(audits[0]["source"], "airtable")

    def test_no_change_no_pull(self):
        local = {"id": "x", "manual_note": "same", "score_override": None,
                 "airtable_record_id": "recAAA"}
        at = {"recAAA": at_rec("recAAA", **{"Notes": "same"})}
        updates, audits = engine.plan_pull([local], at, OWNERSHIP)
        self.assertEqual(updates, [])
        self.assertEqual(audits, [])


if __name__ == "__main__":
    unittest.main()
