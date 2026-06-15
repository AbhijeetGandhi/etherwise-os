"""Phase 0 tests for the M3 knowledge pipeline core — dedup, classify,
extract (mock gateway), the no-uncited-fact guard, and FTS round-trip.
No network: the gateway is mocked; everything else runs real on a temp DB.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import db
from core import claude_gateway as gw
from modules.knowledge import kb, ingest, extract, index

CLIENT_MAP = [
    {"client_id": "recHorizons", "name": "Horizons ABA",
     "identifiers": ["horizons", "alexander apfel", "@horizonsaba.com"]},
    {"client_id": "recHermon", "name": "Hermon",
     "identifiers": ["hermon", "@hermon.io"]},
]


def fathom_payload(rid="rec1", title="Horizons ABA — architecture sync",
                   created="2026-06-10T23:25:20.734Z",
                   speakers=("Alexander Apfel", "Abhijeet"),
                   text="We built 49 Airtable bases. Client uses Make.com."):
    return {
        "recording_id": rid, "title": title, "created_at": created,
        "url": f"https://fathom.video/calls/{rid}",
        "transcript": [{"speaker": speakers[0], "text": text},
                       {"speaker": speakers[1], "text": "Confirmed."}],
    }


class KBCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-test-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.inbox = self.tmp / "inbox" / "fathom"
        self.inbox.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def drop(self, payload, name="2026-06-10__rec1.json"):
        p = self.inbox / name
        p.write_text(json.dumps(payload))
        return p

    def rows(self, sql, args=()):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, args)]


# ── content-hash dedup ────────────────────────────────────────────────────────
class TestDedup(KBCase):
    def test_same_content_ingests_once(self):
        p1 = self.drop(fathom_payload(), "a.json")
        p2 = self.drop(fathom_payload(), "b.json")  # same content, diff name
        r1 = ingest.ingest_file(p1, db_path=self.db_path, client_map=CLIENT_MAP)
        r2 = ingest.ingest_file(p2, db_path=self.db_path, client_map=CLIENT_MAP)
        self.assertEqual(r1["status"], "ingested")
        self.assertEqual(r2["status"], "duplicate")
        self.assertEqual(len(self.rows("SELECT * FROM sources")), 1)

    def test_content_hash_stable(self):
        h1 = kb.content_hash("hello  world\n")
        h2 = kb.content_hash("hello world")  # normalized whitespace
        self.assertEqual(h1, h2)


# ── classify routes to the right client ──────────────────────────────────────
class TestClassify(KBCase):
    def test_matches_by_speaker_name(self):
        cid, name, _ = ingest.classify(
            participants=["Alexander Apfel", "Abhijeet"], title="sync",
            client_map=CLIENT_MAP)
        self.assertEqual(cid, "recHorizons")
        self.assertEqual(name, "Horizons ABA")

    def test_matches_by_email_domain(self):
        cid, _, _ = ingest.classify(
            participants=["someone@hermon.io"], title="call",
            client_map=CLIENT_MAP)
        self.assertEqual(cid, "recHermon")

    def test_unmatched_is_none_unknown(self):
        cid, name, ctype = ingest.classify(
            participants=["random@gmail.com"], title="misc",
            client_map=CLIENT_MAP)
        self.assertIsNone(cid)
        self.assertEqual(ctype, "unknown")

    def test_ingest_sets_client_on_source(self):
        r = ingest.ingest_file(self.drop(fathom_payload()),
                               db_path=self.db_path, client_map=CLIENT_MAP)
        src = self.rows("SELECT * FROM sources")[0]
        self.assertEqual(src["client_id"], "recHorizons")
        self.assertEqual(src["source_type"], "fathom")
        self.assertIsNotNone(src["occurred_dt"])
        self.assertGreater(src["density"], 0)


# ── extract: SCHEMA-valid cited facts (gateway mocked) ───────────────────────
class TestExtract(KBCase):
    def gw_result(self, facts):
        return gw.GatewayResult(
            text=json.dumps({"facts": facts}), parsed={"facts": facts},
            model="claude-sonnet-4-6", route="payg", usage={},
            cost_usd=0.01, stop_reason="end_turn", duration_ms=5)

    def test_builds_citation_from_source_ref_and_locator(self):
        facts = [{"category": "numbers", "fact_text": "49 Airtable bases",
                  "confidence": "CONFIRMED", "locator": "00:03:10"}]
        with mock.patch.object(extract.runner, "claude_call",
                               return_value=self.gw_result(facts)):
            out = extract.extract_facts(
                {"source_ref": "fathom/rec1.json", "client_id": "recHorizons"},
                "transcript text", db_path=self.db_path)
        self.assertEqual(len(out), 1)
        self.assertIn("fathom/rec1.json", out[0]["citation"])
        self.assertIn("00:03:10", out[0]["citation"])
        self.assertEqual(out[0]["confidence"], "CONFIRMED")

    def test_drops_facts_missing_citation_or_tag(self):
        facts = [
            {"category": "numbers", "fact_text": "good", "confidence":
             "CONFIRMED", "locator": "00:01"},
            {"category": "numbers", "fact_text": "no locator",
             "confidence": "CONFIRMED", "locator": ""},          # uncitable
            {"category": "numbers", "fact_text": "bad tag",
             "confidence": "MAYBE", "locator": "00:02"},          # bad tag
        ]
        with mock.patch.object(extract.runner, "claude_call",
                               return_value=self.gw_result(facts)):
            out = extract.extract_facts(
                {"source_ref": "fathom/rec1.json", "client_id": "c"},
                "t", db_path=self.db_path)
        self.assertEqual([f["fact_text"] for f in out], ["good"])


# ── the guard: no uncited fact persists ──────────────────────────────────────
class TestNoUncitedPersists(KBCase):
    def seed_source(self):
        with db.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id) VALUES ('fathom','rec1','h1','c')")
            return cur.lastrowid

    def test_index_rejects_uncited(self):
        sid = self.seed_source()
        facts = [
            {"category": "numbers", "fact_text": "cited", "citation":
             "rec1 @ 00:01", "confidence": "CONFIRMED"},
            {"category": "numbers", "fact_text": "uncited", "citation": "",
             "confidence": "CONFIRMED"},
            {"category": "numbers", "fact_text": "untagged", "citation":
             "rec1 @ 00:02", "confidence": "NOPE"},
        ]
        res = index.index_facts(self.db_path, sid, "c", facts)
        self.assertEqual(res["inserted"], 1)
        self.assertEqual(res["rejected"], 2)
        texts = [r["fact_text"] for r in self.rows("SELECT fact_text FROM facts")]
        self.assertEqual(texts, ["cited"])

    def test_validate_fact_raises(self):
        with self.assertRaises(kb.UncitedFact):
            kb.validate_fact({"fact_text": "x", "citation": "",
                              "confidence": "CONFIRMED"})
        with self.assertRaises(kb.UncitedFact):
            kb.validate_fact({"fact_text": "x", "citation": "y",
                              "confidence": "BOGUS"})


# ── FTS round-trip ────────────────────────────────────────────────────────────
class TestFTS(KBCase):
    def test_search_finds_indexed_fact(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO sources (source_type, source_ref,"
                         " content_hash) VALUES ('fathom','r','h')")
            sid = conn.execute("SELECT id FROM sources").fetchone()["id"]
        index.index_facts(self.db_path, sid, "recHorizons", [
            {"category": "numbers", "fact_text": "built 49 Airtable bases",
             "citation": "r @ 00:01", "confidence": "CONFIRMED"}])
        hits = index.search(self.db_path, "airtable")
        self.assertEqual(len(hits), 1)
        self.assertIn("49 Airtable", hits[0]["fact_text"])
        self.assertEqual(hits[0]["client_id"], "recHorizons")

    def test_search_respects_client_filter(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO sources (source_type, source_ref,"
                         " content_hash) VALUES ('fathom','r','h')")
            sid = conn.execute("SELECT id FROM sources").fetchone()["id"]
        index.index_facts(self.db_path, sid, "recHermon", [
            {"category": "stack", "fact_text": "uses Vapi voice", "citation":
             "r @ 1", "confidence": "CONFIRMED"}])
        self.assertEqual(len(index.search(self.db_path, "Vapi",
                                          client_id="recHermon")), 1)
        self.assertEqual(len(index.search(self.db_path, "Vapi",
                                          client_id="recHorizons")), 0)


if __name__ == "__main__":
    unittest.main()
