"""Unit tests for core/airtable_client.py — the HTTP boundary (_request) is
the only mock; pagination, batching, typecast, and the sync-engine adapter
run real code.
"""
from __future__ import annotations

import unittest
from unittest import mock

from core import airtable_client as ac


class TestListRecords(unittest.TestCase):
    def test_single_page(self):
        with mock.patch.object(ac, "_request", return_value={
                "records": [{"id": "rec1", "fields": {"Name": "Acme"}}]}) as r:
            out = ac.AirtableClient(api_key="k").list_records("appX", "tblY")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["fields"]["Name"], "Acme")
        method, url = r.call_args[0][0], r.call_args[0][1]
        self.assertEqual(method, "GET")
        self.assertIn("/appX/tblY", url)

    def test_pagination_follows_offset(self):
        pages = [
            {"records": [{"id": "r1", "fields": {}}], "offset": "o1"},
            {"records": [{"id": "r2", "fields": {}}]},
        ]
        with mock.patch.object(ac, "_request", side_effect=pages):
            out = ac.AirtableClient(api_key="k").list_records("appX", "tblY")
        self.assertEqual([r["id"] for r in out], ["r1", "r2"])

    def test_max_records_caps(self):
        pages = [{"records": [{"id": f"r{i}", "fields": {}} for i in range(5)],
                  "offset": "o1"}]
        with mock.patch.object(ac, "_request", side_effect=pages):
            out = ac.AirtableClient(api_key="k").list_records(
                "appX", "tblY", max_records=3)
        self.assertEqual(len(out), 3)


class TestWrites(unittest.TestCase):
    def test_create_batches_of_10_with_typecast(self):
        recs = [{"fields": {"N": i}} for i in range(23)]
        calls = []
        with mock.patch.object(ac, "_request",
                               side_effect=lambda m, u, k, payload=None:
                               calls.append(payload) or
                               {"records": payload["records"]}):
            n = ac.AirtableClient(api_key="k").create_records(
                "appX", "tblY", recs)
        self.assertEqual(n, 23)
        self.assertEqual([len(c["records"]) for c in calls], [10, 10, 3])
        self.assertTrue(all(c["typecast"] is True for c in calls))

    def test_update_requires_ids_typecast(self):
        recs = [{"id": "rec1", "fields": {"N": 1}}]
        with mock.patch.object(ac, "_request",
                               side_effect=lambda m, u, k, payload=None:
                               {"records": payload["records"]}) as r:
            ac.AirtableClient(api_key="k").update_records("appX", "tblY", recs)
        method, payload = r.call_args[0][0], r.call_args[1]["payload"]
        self.assertEqual(method, "PATCH")
        self.assertTrue(payload["typecast"])
        self.assertEqual(payload["records"][0]["id"], "rec1")


class TestEngineAdapter(unittest.TestCase):
    def test_engine_writer_fills_sync_slot(self):
        # core/sync/engine.execute_push calls airtable(method, path, payload)
        with mock.patch.object(ac, "_request",
                               return_value={"records": [{"id": "rec1"}]}) as r:
            writer = ac.AirtableClient(api_key="k").engine_writer("appX",
                                                                 "tblY")
            out = writer("POST", "", {"records": [{"fields": {}}],
                                      "typecast": True})
        self.assertEqual(out["records"][0]["id"], "rec1")
        self.assertIn("/appX/tblY", r.call_args[0][1])


class TestApiKey(unittest.TestCase):
    def test_explicit_key_used(self):
        self.assertEqual(ac.AirtableClient(api_key="explicit").key, "explicit")


if __name__ == "__main__":
    unittest.main()
