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
from modules.knowledge import (kb, ingest, extract, index, crm, dossier,
                               librarian, clients, proof_points, upwork_msgs)

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

    def test_loom_citation_deep_links_the_recording(self):
        # a loom source passes cite_ref = the share URL; facts click straight to
        # the recording at the anchoring phrase (locator), not "loom/<id>".
        facts = [{"category": "architecture", "scope": "client",
                  "fact_text": "Built a cancellation automation in Make.com",
                  "confidence": "CONFIRMED",
                  "locator": "the cancellation flow fires a webhook"}]
        vid = "ddd4b01c912c45e28cb16445ad3101f6"
        url = f"https://www.loom.com/share/{vid}"
        with mock.patch.object(extract.runner, "claude_call",
                               return_value=self.gw_result(facts)):
            out = extract.extract_facts(
                {"source_ref": f"loom/{vid}", "client_id": "recHorizons",
                 "cite_ref": url},
                "transcript", db_path=self.db_path)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["citation"].startswith(url + " @ "))
        self.assertIn("cancellation flow fires a webhook", out[0]["citation"])

    def test_noise_gate_drops_filler_and_counts_it(self):
        # the shared kb.noise_reason gate drops roster/meeting-meta/boilerplate/
        # calendar filler even when the model cites it; real signal survives.
        facts = [
            {"category": "architecture", "scope": "client",
             "fact_text": "Client runs a licensure board in Airtable",
             "confidence": "CONFIRMED", "locator": "licensure board"},   # keep
            {"category": "other", "scope": "client",
             "fact_text": "Alexander and Abhijeet participated in the call",
             "confidence": "CONFIRMED", "locator": "at 00:01"},          # roster
            {"category": "other", "scope": "client",
             "fact_text": "This is a recorded walkthrough; the meeting happened",
             "confidence": "CONFIRMED", "locator": "recorded"},          # meta
        ]
        metrics = {}
        with mock.patch.object(extract.runner, "claude_call",
                               return_value=self.gw_result(facts)):
            out = extract.extract_facts(
                {"source_ref": "loom/x", "client_id": "recHorizons"},
                "t", db_path=self.db_path, metrics=metrics)
        self.assertEqual([f["fact_text"] for f in out],
                         ["Client runs a licensure board in Airtable"])
        self.assertEqual(metrics["dropped_noise"], 2)
        self.assertGreater(metrics["cost_usd"], 0)

    def test_model_key_is_honored(self):
        facts = [{"category": "numbers", "fact_text": "ok",
                  "confidence": "CONFIRMED", "locator": "00:01"}]
        with mock.patch.object(extract.runner, "claude_call",
                               return_value=self.gw_result(facts)) as m:
            extract.extract_facts(
                {"source_ref": "loom/x", "client_id": "c"}, "t",
                db_path=self.db_path, model_key="classify")
        self.assertEqual(m.call_args.kwargs["model_key"], "classify")


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


# ── asset-zip Fathom shape (transcript timestamps + participant emails) ───────
class TestAssetParse(KBCase):
    def test_parses_asset_shape(self):
        payload = {
            "recording_id": 9911, "title": "Endura kickoff",
            "date": "2026-04-01T10:00:00Z",
            "url": "https://fathom.video/calls/9911",
            "participants": [{"name": "Sam", "email": "sam@endurasolutions.com.au"},
                             {"name": "Abhijeet", "email": "a@etherwise.io"}],
            "transcript": [
                {"timestamp": "00:00:05", "speaker": "Sam", "text": "We need RPA."},
                {"timestamp": "00:01:10", "speaker": "Abhijeet", "text": "On it."}],
        }
        parsed = ingest.parse_fathom(payload)
        self.assertEqual(parsed["source_ref"], "fathom/9911")
        self.assertEqual(parsed["occurred_dt"], "2026-04-01T10:00:00Z")
        self.assertIn("sam@endurasolutions.com.au", parsed["participants"])
        self.assertIn("[00:00:05] Sam: We need RPA.", parsed["text"])

    def test_live_api_speaker_object(self):
        # the live Fathom API gives speaker as an OBJECT (not a string) and
        # leaves participants empty -> kb_daily crashed on .strip() (2026-06-18..21)
        payload = {
            "recording_id": 156775218, "title": "Client sync",
            "created_at": "2026-06-19T10:00:00Z", "participants": [],
            "transcript": [
                {"timestamp": "00:00:01",
                 "speaker": {"display_name": "Sam",
                             "matched_calendar_invitee_email": "sam@adelye.com"},
                 "text": "We need the register."},
                {"timestamp": "00:00:05", "speaker": None, "text": "ok"}],
        }
        parsed = ingest.parse_fathom(payload)   # must NOT raise
        self.assertIn("[00:00:01] Sam: We need the register.", parsed["text"])
        self.assertIn("sam@adelye.com", parsed["participants"])  # seeds classify
        self.assertIn("Sam", parsed["participants"])

    def test_asset_classifies_by_email_domain(self):
        client_map = [{"client_id": "endura", "name": "Endura Solutions",
                       "identifiers": ["endurasolutions.com.au", "endura"]}]
        payload = {"recording_id": 1, "title": "call", "date": "2026-04-01",
                   "participants": [{"name": "S", "email": "s@endurasolutions.com.au"}],
                   "transcript": [{"timestamp": "0", "speaker": "S", "text": "hi there"}]}
        p = self.drop(payload, "e.json")
        r = ingest.ingest_file(p, db_path=self.db_path, client_map=client_map)
        self.assertEqual(r["client_id"], "endura")


# ── CRM numbers → cited facts (deterministic, no model) ───────────────────────
class TestCRM(KBCase):
    def seed(self):
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO transactions (record_id, type, description,"
                " assignment_company, amount, creation_dt, canonical_key)"
                " VALUES ('t1','Hourly Earning','work','Adélye',100.0,"
                "'2026-01-01','k1'),"
                " ('t2','Fixed Earning','work','Adélye',400.0,'2026-02-01','k2'),"
                " ('t3','Connect Purchase','noise','Adélye',-5.0,'2026-02-01','k3')")

    def test_emits_cited_revenue_fact(self):
        self.seed()
        facts = crm.crm_facts(self.db_path, "adelye", "Adelye", ["adélye"])
        self.assertTrue(facts)
        rev = [f for f in facts if "Revenue" in f["fact_text"]][0]
        self.assertIn("$500", rev["fact_text"])           # 100+400, excludes -5
        self.assertEqual(rev["confidence"], "CONFIRMED")
        self.assertEqual(rev["citation"], "CRM:transactions")

    def test_ingest_crm_idempotent(self):
        self.seed()
        r1 = crm.ingest_crm(self.db_path, "adelye", "Adelye", ["adélye"])
        r2 = crm.ingest_crm(self.db_path, "adelye", "Adelye", ["adélye"])
        self.assertEqual(r1["inserted"], r2["inserted"])
        n = self.rows("SELECT COUNT(*) c FROM sources WHERE source_type='crm'")
        self.assertEqual(n[0]["c"], 1)                     # one synthetic source
        f = self.rows("SELECT COUNT(*) c FROM facts WHERE client_id='adelye'")
        self.assertEqual(f[0]["c"], r1["inserted"])        # not duplicated


# ── dossier render is deterministic + cites everything ────────────────────────
class TestDossier(KBCase):
    FACTS = [
        {"category": "numbers", "fact_text": "Revenue $19,457.",
         "citation": "CRM:transactions", "confidence": "CONFIRMED"},
        {"category": "stack", "fact_text": "Uses Make.com.",
         "citation": "fathom/1 @ 00:02", "confidence": "CROSS-VERIFIED"},
        {"category": "overview", "fact_text": "Might expand next year.",
         "citation": "fathom/1 @ 00:09", "confidence": "INFERRED"},
    ]
    SOURCES = [{"source_type": "fathom", "source_ref": "fathom/1",
                "title": "kickoff", "occurred_dt": "2026-04-01", "density": 0.5}]

    def test_render_deterministic(self):
        a = dossier.render_dossier("Adelye", "adelye", self.FACTS,
                                   self.SOURCES, "2026-06-16")
        b = dossier.render_dossier("Adelye", "adelye", self.FACTS,
                                   self.SOURCES, "2026-06-16")
        self.assertEqual(a, b)

    def test_every_fact_cited_and_inferred_flagged(self):
        md = dossier.render_dossier("Adelye", "adelye", self.FACTS,
                                    self.SOURCES, "2026-06-16")
        self.assertIn("CRM:transactions", md)
        self.assertIn("fathom/1 @ 00:02", md)
        self.assertIn("_INFERRED_", md)                    # inferred italicized
        self.assertIn("CONFIRMED", md)

    def test_compile_writes_file_and_index(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO sources (source_type, source_ref,"
                         " content_hash, client_id) VALUES"
                         " ('fathom','fathom/1','h1','adelye')")
            sid = conn.execute("SELECT id FROM sources").fetchone()["id"]
        index.index_facts(self.db_path, sid, "adelye", self.FACTS)
        out = dossier.compile_dossier(self.db_path, "adelye", "Adelye",
                                      "2026-06-16", dossier_dir=self.tmp / "dz")
        self.assertTrue(Path(out["path"]).exists())
        self.assertEqual(out["facts"], 3)
        idx = self.rows("SELECT * FROM dossier_index WHERE client_id='adelye'")
        self.assertEqual(idx[0]["fact_count"], 3)

    def test_anti_clobber_refuses_empty_overwrite(self):
        # the 2026-06-18 02:07 incident: a transient empty read must NOT destroy
        # a populated dossier
        dz = self.tmp / "dz"
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO sources (source_type, source_ref,"
                         " content_hash, client_id) VALUES"
                         " ('fathom','fathom/1','h1','adelye')")
            sid = conn.execute("SELECT id FROM sources").fetchone()["id"]
        index.index_facts(self.db_path, sid, "adelye", self.FACTS)
        good = dossier.compile_dossier(self.db_path, "adelye", "Adelye",
                                       "2026-06-16", dossier_dir=dz)
        before = Path(good["path"]).read_text()
        # now simulate a transient empty db view: wipe facts, recompile
        with db.connect(self.db_path) as conn:
            conn.execute("DELETE FROM facts")
        out = dossier.compile_dossier(self.db_path, "adelye", "Adelye",
                                      "2026-06-17", dossier_dir=dz)
        self.assertEqual(out["status"], "skipped_empty")
        self.assertEqual(out["prev_facts"], 3)
        self.assertEqual(Path(good["path"]).read_text(), before)  # untouched
        idx = self.rows("SELECT fact_count FROM dossier_index WHERE"
                        " client_id='adelye'")
        self.assertEqual(idx[0]["fact_count"], 3)                 # index intact

    def test_file_guard_blocks_wrong_db_clobber(self):
        # the real 2026-06-18 cause: a compile against an EMPTY db (no index
        # row) writing to a dir whose .md is already populated must NOT clobber
        dz = self.tmp / "dz"
        dz.mkdir()
        (dz / "adelye.md").write_text("# Adelye\n\n## Stack\n\n- Real fact "
                                      "[CONFIRMED · f/1 @ 1]\n")
        out = dossier.compile_dossier(self.db_path, "adelye", "Adelye",
                                      "2026-06-17", dossier_dir=dz)
        self.assertEqual(out["status"], "skipped_empty")        # file guard fired
        self.assertIn("Real fact", (dz / "adelye.md").read_text())

    def test_force_empty_allows_real_emptying(self):
        dz = self.tmp / "dz"
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO dossier_index (client_id, client_name,"
                         " fact_count) VALUES ('adelye','Adelye',5)")
        out = dossier.compile_dossier(self.db_path, "adelye", "Adelye",
                                      "2026-06-17", dossier_dir=dz,
                                      force_empty=True)
        self.assertEqual(out["status"], "compiled")
        self.assertEqual(out["facts"], 0)

    def test_proof_points_anti_clobber(self):
        kbdir = self.tmp / "kb"
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO sources (source_type, source_ref,"
                         " content_hash) VALUES ('fathom','f/1','h')")
            sid = conn.execute("SELECT id FROM sources").fetchone()["id"]
        index.index_facts(self.db_path, sid, None, [
            {"category": "overview", "fact_text": "Agency does automation.",
             "citation": "f/1 @ 1", "confidence": "CONFIRMED", "scope": "agency"}])
        first = proof_points.compile_proof_points(self.db_path, "2026-06-16",
                                                  out_dir=kbdir)
        self.assertEqual(first["agency"], 1)
        before = (kbdir / "proof-points.md").read_text()
        with db.connect(self.db_path) as conn:
            conn.execute("DELETE FROM facts")
        out = proof_points.compile_proof_points(self.db_path, "2026-06-17",
                                                out_dir=kbdir)
        self.assertEqual(out["status"], "skipped_empty")
        self.assertEqual((kbdir / "proof-points.md").read_text(), before)


# ── librarian: cites when grounded, refuses when kb is silent ─────────────────
class TestLibrarian(KBCase):
    def seed_fact(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO sources (source_type, source_ref,"
                         " content_hash, client_id) VALUES"
                         " ('fathom','fathom/1','h1','adelye')")
            sid = conn.execute("SELECT id FROM sources").fetchone()["id"]
        index.index_facts(self.db_path, sid, "adelye", [
            {"category": "stack", "fact_text": "Adelye uses Make.com for ops.",
             "citation": "fathom/1 @ 00:02", "confidence": "CONFIRMED"}])

    def gw_result(self, obj):
        return gw.GatewayResult(
            text=json.dumps(obj), parsed=obj, model="claude-sonnet-4-6",
            route="payg", usage={}, cost_usd=0.01, stop_reason="end_turn",
            duration_ms=5)

    def test_grounded_answer_cites(self):
        self.seed_fact()
        ans = {"answer": "They use Make.com.", "grounded": True,
               "citations": ["fathom/1 @ 00:02"]}
        with mock.patch.object(librarian.runner, "claude_call",
                               return_value=self.gw_result(ans)):
            out = librarian.answer("What stack does Adelye use?",
                                   db_path=self.db_path, client_id="adelye")
        self.assertTrue(out["grounded"])
        self.assertIn("fathom/1 @ 00:02", out["citations"])
        self.assertEqual(out["facts_used"], 1)

    def test_refuses_when_no_facts(self):
        # no facts seeded → no FTS hits → refusal WITHOUT calling the model
        with mock.patch.object(librarian.runner, "claude_call",
                               side_effect=AssertionError("must not call model")):
            out = librarian.answer("What is their revenue?",
                                   db_path=self.db_path, client_id="adelye")
        self.assertFalse(out["grounded"])
        self.assertEqual(out["citations"], [])


# ── thread→client linkage by counterparty ────────────────────────────────────
class TestLinkage(KBCase):
    def seed_thread(self, tid, room, org, user, owner_only=False):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, room_name) VALUES (?,?)",
                         (tid, room))
            # counterparty message
            if not owner_only:
                conn.execute(
                    "INSERT INTO messages (id, thread_id, message, sender_user_id,"
                    " sender_user_name, sender_org_name) VALUES"
                    " (?,?,?,?,?,?)", (tid + "_m1", tid, "hi", "999", user, org))
            # our own message (must never drive linkage)
            conn.execute(
                "INSERT INTO messages (id, thread_id, message, sender_user_id,"
                " sender_user_name, sender_org_name) VALUES (?,?,?,?,?,?)",
                (tid + "_m0", tid, "reply", clients._OWNER_USER_ID,
                 "Abhijeet G.", "Etherwise"))

    def test_links_by_counterparty_org(self):
        cmap = [{"client_id": "netfly", "name": "NETFLY",
                 "identifiers": ["netfly", "haleema"]}]
        self.seed_thread("room_a", "Haleema Umair, NETFLY", "NETFLY", "Haleema U.")
        self.seed_thread("room_b", "Some Other Co", "Random LLC", "Bob R.")
        out = clients.link_threads(self.db_path, cmap)
        self.assertEqual(out["linked"], 1)
        rows = self.rows("SELECT id, client_id FROM threads ORDER BY id")
        self.assertEqual(rows[0]["client_id"], "netfly")     # room_a
        self.assertIsNone(rows[1]["client_id"])              # room_b unmatched

    def test_owner_messages_do_not_link(self):
        cmap = [{"client_id": "etherwise", "name": "Etherwise",
                 "identifiers": ["etherwise"]}]
        self.seed_thread("room_c", "Client Co", "Client Co", "Cara C.",
                         owner_only=True)
        out = clients.link_threads(self.db_path, cmap)
        self.assertEqual(out["linked"], 0)    # only owner spoke -> no counterparty

    def test_upwork_double_space_still_matches_the_registry(self):
        """Upwork writes "Tanushant  Sharma" (TWO spaces) into room_name,
        contracts.client_company AND transactions.assignment_company; the
        registry holds one. Until registry.norm() collapsed internal whitespace
        (2026-08-13) this client — $757.23 earned, two ACTIVE contracts, 80
        messages — matched nothing at all."""
        cmap = [{"client_id": "tanushant-sharma", "name": "Tanushant Sharma",
                 "identifiers": ["Tanushant Sharma"]}]
        self.assertEqual(
            clients.match_counterparty(["Tanushant  Sharma"], cmap)[0],
            "tanushant-sharma")
        self.seed_thread("room_dbl", "Tanushant  Sharma", None,
                         "Tanushant  Sharma")
        out = clients.link_threads(self.db_path, cmap)
        self.assertEqual(out["linked"], 1)
        self.assertEqual(out["unmatched"], 0)
        self.assertEqual(
            self.rows("SELECT client_id FROM threads")[0]["client_id"],
            "tanushant-sharma")


# ── scope routing: client→dossier, agency/cross→proof-points ─────────────────
class TestScopeRouting(KBCase):
    FACTS = [
        {"category": "stack", "fact_text": "Client uses Airtable.",
         "citation": "f/1 @ 1", "confidence": "CONFIRMED", "scope": "client"},
        {"category": "overview", "fact_text": "Agency does no-code automation.",
         "citation": "f/1 @ 2", "confidence": "CONFIRMED", "scope": "agency"},
        {"category": "architecture", "fact_text": "Built a voice agent for OtherCo.",
         "citation": "f/1 @ 3", "confidence": "CONFIRMED", "scope": "cross-client"},
    ]

    def seed(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO sources (source_type, source_ref,"
                         " content_hash, client_id) VALUES"
                         " ('fathom','f/1','h','adelye')")
            sid = conn.execute("SELECT id FROM sources").fetchone()["id"]
        return index.index_facts(self.db_path, sid, "adelye", self.FACTS)

    def test_scope_persisted(self):
        res = self.seed()
        self.assertEqual(res["inserted"], 3)
        scopes = {r["scope"] for r in self.rows("SELECT scope FROM facts")}
        self.assertEqual(scopes, {"client", "agency", "cross-client"})

    def test_dossier_only_client_scope(self):
        self.seed()
        out = dossier.compile_dossier(self.db_path, "adelye", "Adelye",
                                      "2026-06-17", dossier_dir=self.tmp / "dz")
        self.assertEqual(out["facts"], 1)                # only the client fact
        md = Path(out["path"]).read_text()
        self.assertIn("Client uses Airtable.", md)
        self.assertNotIn("no-code automation", md)       # agency -> routed out
        self.assertNotIn("voice agent for OtherCo", md)  # cross -> routed out
        self.assertIn("routed to proof-points", md)

    def test_proof_points_gets_non_client(self):
        self.seed()
        out = proof_points.compile_proof_points(self.db_path, "2026-06-17",
                                                out_dir=self.tmp / "kb")
        self.assertEqual(out["agency"], 1)
        self.assertEqual(out["cross"], 1)
        md = Path(out["path"]).read_text()
        self.assertIn("no-code automation", md)
        self.assertIn("voice agent for OtherCo", md)
        self.assertNotIn("Client uses Airtable.", md)    # client stays in dossier

    def test_invalid_scope_rejected(self):
        with self.assertRaises(kb.UncitedFact):
            kb.validate_fact({"fact_text": "x", "citation": "y",
                              "confidence": "CONFIRMED", "scope": "bogus"})


# ── upwork thread → cited source ──────────────────────────────────────────────
class TestUpworkThread(KBCase):
    def test_build_thread_text_anchored(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, room_name) VALUES"
                         " ('room_x','NETFLY')")
            conn.execute(
                "INSERT INTO messages (id, thread_id, message, created_dt,"
                " sender_user_name, direction) VALUES"
                " ('m1','room_x','Can you build outreach?','2026-05-01T10:00:00Z',"
                " 'Haleema U.','Inbound')")
        text = upwork_msgs.build_thread_text(self.db_path, "room_x")
        self.assertIn("[2026-05-01T10:00:00] Haleema U.: Can you build outreach?",
                      text)


# ── loom parser ───────────────────────────────────────────────────────────────
class TestLoomParse(KBCase):
    def test_parses_loom_export_item(self):
        p = ingest.parse_loom({"id": "abc123", "name": "Pitch video",
                               "date": "2026-04-07T23:09:30Z",
                               "transcript": "Hey, I build automations."})
        self.assertEqual(p["source_type"], "loom")
        self.assertEqual(p["source_ref"], "loom/abc123")
        self.assertEqual(p["title"], "Pitch video")
        self.assertEqual(p["participants"], [])           # single-narrator
        self.assertIn("automations", p["text"])
        self.assertIn("loom.com/share/abc123", p["url"])

    def test_loom_ingests_and_is_searchable(self):
        import json as _json
        p = self.inbox.parent / "loom"
        p.mkdir(parents=True, exist_ok=True)
        f = p / "v1.json"
        f.write_text(_json.dumps({"id": "v1", "name": "demo",
                                  "date": "2026-04-01",
                                  "transcript": "We automate invoice reconciliation."}))
        r = ingest.ingest_file(f, db_path=self.db_path, client_map=[],
                               source_type="loom")
        self.assertEqual(r["status"], "ingested")
        hits = index.search_sources(self.db_path, "invoice")
        self.assertTrue(any(h["source_ref"] == "loom/v1" for h in hits))


# ── daily orchestrator (polls mocked; no network/LLM) ────────────────────────
class TestDaily(KBCase):
    def _ctx(self):
        from types import SimpleNamespace
        return SimpleNamespace(db_path=self.db_path)

    def test_runs_phases_and_excludes_loom_from_extract(self):
        from modules.knowledge import daily
        # seed an unextracted fathom source + an unextracted loom source
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO sources (source_type, source_ref,"
                         " content_hash, client_id, raw_path, status) VALUES"
                         " ('loom','loom/x','h1','adelye','/nope','ingested')")
        captured = {}

        def fake_extract_pending(db_path):
            # confirm the SQL filter excludes loom
            with db.connect(db_path) as conn:
                rows = conn.execute("SELECT source_type FROM sources WHERE"
                                    " status='ingested' AND source_type IN"
                                    " ('fathom','email')").fetchall()
            captured["fathom_email_pending"] = len(rows)
            return {"extracted": 0, "facts": 0, "touched": [], "pending": 0}

        from core import config as _config
        with mock.patch.object(_config, "KNOWLEDGE_INBOX", self.tmp / "inbox"), \
             mock.patch.object(_config, "KNOWLEDGE_DIR", self.tmp / "kb"), \
             mock.patch.object(daily, "_poll_fathom", return_value={"saved": 0}), \
             mock.patch.object(daily, "_poll_email",
                               return_value={"skipped": "gmail not connected"}), \
             mock.patch.object(daily, "_extract_pending",
                               side_effect=fake_extract_pending):
            out = daily.run_daily(self._ctx())
        self.assertEqual(captured["fathom_email_pending"], 0)  # loom excluded
        self.assertIn("compile", out)
        self.assertEqual(out["email"], {"skipped": "gmail not connected"})

    def test_email_poll_skipped_when_no_token(self):
        from modules.knowledge import daily, email_poll
        with mock.patch.object(email_poll, "TOKEN_FILE",
                               Path("/nonexistent/gmail.json")):
            self.assertEqual(daily._poll_email(self._ctx())["skipped"],
                             "gmail not connected")


if __name__ == "__main__":
    unittest.main()
