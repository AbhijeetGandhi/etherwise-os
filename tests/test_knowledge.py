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

from core import db, registry
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

    def test_export_shape_keeps_transcript_speakers(self):
        """THE REGRESSION. A Fathom EXPORT payload always carries a non-empty
        `participants` — the recording's owner, one internal person. The union
        used to be `{...} or spoken`, so that one truthy element made Python
        discard `spoken` entirely and every other human on the call vanished.

        357 of 617 live payloads are this shape; 298 sources lost a speaker; the
        residue then reached an LLM that saw ONE internal name and a title of
        'Impromptu Google Meet Meeting' and excluded 114 real client calls as
        internal-product. If this test fails, that is what broke.

        `participants` present MUST NOT suppress the speakers.
        """
        payload = {
            "recording_id": 4242, "title": "Impromptu Google Meet Meeting",
            "date": "2026-04-01T10:00:00Z",
            # exactly what the export writes: the owner, and only the owner
            "participants": [{"name": "Abhijeet Gandhi"}],
            "transcript": [
                {"timestamp": "00:00:05", "speaker": "Alexander Apfel",
                 "text": "The BCBA scheduling is wrong."},
                {"timestamp": "00:01:10", "speaker": "Abhijeet Gandhi",
                 "text": "On it."}],
        }
        parsed = ingest.parse_fathom(payload)
        self.assertIn("Abhijeet Gandhi", parsed["participants"])   # invitee kept
        self.assertIn("Alexander Apfel", parsed["participants"])   # speaker kept
        # and the classifier can therefore still reach the client
        self.assertEqual(
            ingest.classify(parsed["participants"], parsed["title"],
                            CLIENT_MAP)[0], "recHorizons")

    def test_export_shape_keeps_speaker_emails(self):
        """The union is over names AND matched invitee emails — a domain is the
        strongest signal there is, and it too was being discarded."""
        payload = {
            "recording_id": 4243, "title": "Impromptu Zoom Meeting",
            "date": "2026-04-02T10:00:00Z",
            "participants": ["Abhijeet Gandhi"],          # bare-string shape
            "transcript": [
                {"speaker": {"display_name": "Abel",
                             "matched_calendar_invitee_email": "abel@hermon.io"},
                 "text": "Portal update."}],
        }
        parsed = ingest.parse_fathom(payload)
        self.assertIn("abel@hermon.io", parsed["participants"])
        self.assertIn("Abhijeet Gandhi", parsed["participants"])
        self.assertEqual(
            ingest.classify(parsed["participants"], parsed["title"],
                            CLIENT_MAP)[0], "recHermon")

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
def _txn(rid, key, company, subtype, base_minor, dt, type_="Other",
         desc="work", source="Upwork"):
    """One ledger row in the shape the P&L reads. `amount_base_minor` is the
    pinned-USD integer the P&L (and therefore this module) sums; `amount` is the
    display copy and is deliberately NOT what the facts are built from."""
    return (rid, type_, subtype, desc, company, base_minor / 100.0, base_minor,
            dt, key, source)


_TXN_SQL = ("INSERT INTO transactions (record_id, type, accounting_subtype,"
            " description, assignment_company, amount, amount_base_minor,"
            " creation_dt, canonical_key, source) VALUES (?,?,?,?,?,?,?,?,?,?)")


class TestCRM(KBCase):
    def seed(self):
        """A real Upwork slice: two earnings, the service fee Upwork charged on
        them, the GST on that fee and the WHT on the earning. Gross $500.00,
        fee -$50.00, tax -$9.50 -> net $440.50."""
        with db.connect(self.db_path) as conn:
            conn.executemany(_TXN_SQL, [
                _txn("t1", "k1", "Adélye", "Hourly", 10000, "2026-01-01",
                     type_="Hourly Earning"),
                _txn("t2", "k2", "Adélye", "Milestone", 40000, "2026-02-01",
                     type_="Fixed Earning"),
                _txn("t3", "k3", "Adélye", "Service Fee", -5000, "2026-02-01",
                     desc="Service Fee"),
                _txn("t4", "k4", "Adélye", "GST", -900, "2026-02-01",
                     desc="GST (IN) - Service Fee"),
                _txn("t5", "k5", "Adélye", "WHT", -50, "2026-02-01",
                     desc="Withholding Tax (IN)"),
            ])

    def _money(self, facts):
        return {("gross" if "GROSS" in f["fact_text"] else "net"): f
                for f in facts if "Revenue to date" in f["fact_text"]}

    def test_emits_gross_AND_net_as_two_separately_cited_facts(self):
        """Abhijeet, 2026-08-13: "I want dossier showing both gross and net so
        prepare it that way." One line stating only gross let a dossier claim
        $859.49 for a client who received $757.23."""
        self.seed()
        m = self._money(crm.crm_facts(self.db_path, "adelye", "Adelye", ["adélye"]))
        self.assertEqual(set(m), {"gross", "net"})
        self.assertIn("$500.00", m["gross"]["fact_text"])
        self.assertIn("$440.50", m["net"]["fact_text"])
        # each names its own bucket set, so neither can be mistaken for the other
        self.assertEqual(m["gross"]["citation"],
                         "CRM:transactions[pnl_bucket=revenue]")
        self.assertEqual(m["net"]["citation"],
                         "CRM:transactions[pnl_bucket=revenue+refund+"
                         "platform_fee+tax]")
        for f in m.values():
            self.assertEqual(f["confidence"], "CONFIRMED")
            self.assertEqual(f["category"], "numbers")

    def test_net_is_summed_from_the_ledger_never_a_percentage(self):
        """The 10% haircut is WRONG for real clients — beglaubigt's contract is
        on Upwork's old 30% tier ($921.67 gross / $644.24 net). Net must fall
        out of the fee rows themselves, so a 30% fee produces a 30% delta with
        no code change."""
        with db.connect(self.db_path) as conn:
            conn.executemany(_TXN_SQL, [
                _txn("t1", "k1", "Beglaubigt", "Milestone", 92167, "2026-05-27",
                     type_="Fixed Earning"),
                _txn("t2", "k2", "Beglaubigt", "Service Fee", -27650,
                     "2026-05-27", desc="Service Fee"),
                _txn("t3", "k3", "Beglaubigt", "WHT", -93, "2026-05-27",
                     desc="Withholding Tax (IN)"),
            ])
        m = self._money(crm.crm_facts(self.db_path, "beglaubigt", "Beglaubigt",
                                      ["Beglaubigt"]))
        self.assertIn("$644.24", m["net"]["fact_text"])
        self.assertNotIn("$829.50", m["net"]["fact_text"])   # what 10% would say

    def test_net_matches_the_pnl_over_the_same_rows(self):
        """The reconciliation guarantee: the dossier's net is exactly what
        pnl.compute() — the function bin/finance-pnl and the Money tab call —
        makes of the same rows. If someone re-implements the bucket map here,
        this fails."""
        self.seed()
        rows = self.rows("SELECT record_id, type, upwork_type, accounting_subtype,"
                         " category, source, description, pnl_bucket,"
                         " amount_base_minor, creation_dt FROM transactions")
        from modules.finance import pnl as _pnl
        bb = _pnl.compute(rows)["by_bucket_minor"]
        want = sum(bb.get(b, 0) for b in
                   ("revenue", "refund", "platform_fee", "tax"))
        m = self._money(crm.crm_facts(self.db_path, "adelye", "Adelye", ["adélye"]))
        self.assertEqual(want, 44050)
        self.assertIn(f"${want / 100:,.2f}", m["net"]["fact_text"])

    def test_refund_reduces_net_and_leaves_gross_alone(self):
        """tru-health billed $132.75 and was refunded all of it. Gross is still
        what was billed; net is the true zero, and it says why."""
        with db.connect(self.db_path) as conn:
            conn.executemany(_TXN_SQL, [
                _txn("t1", "k1", "Tru H", "Hourly", 13275, "2025-04-20",
                     type_="Hourly Earning"),
                _txn("t2", "k2", "Tru H", None, -13275, "2025-04-27",
                     type_="Refund", desc="Refund"),
            ])
        m = self._money(crm.crm_facts(self.db_path, "tru-health", "Tru H",
                                      ["Tru H"]))
        self.assertIn("$132.75", m["gross"]["fact_text"])
        self.assertIn("$0.00", m["net"]["fact_text"])
        self.assertIn("refunded", m["net"]["fact_text"])

    def test_no_deductions_says_so_rather_than_printing_zeros(self):
        """No-fabricated-zeros: a client with no fee/tax rows must not render
        '$0.00 fees, $0.00 tax' — an unmeasured deduction and a real one look
        identical once both are printed as 0."""
        with db.connect(self.db_path) as conn:
            conn.executemany(_TXN_SQL, [
                _txn("t1", "k1", "Hipp Health", "Milestone", 40000,
                     "2025-09-02", type_="Fixed Earning")])
        m = self._money(crm.crm_facts(self.db_path, "hipp-health", "Hipp Health",
                                      ["Hipp Health"]))
        self.assertIn("$400.00", m["net"]["fact_text"])
        self.assertNotIn("$0.00", m["net"]["fact_text"])
        self.assertIn("no fee, tax or refund rows", m["net"]["fact_text"])

    def test_fee_rows_without_any_revenue_emit_no_money_fact(self):
        """hero-marketing-agency matches two Service Fee rows and no earning.
        'Net received: -$75.00' would be a lie — that is an orphaned cost row,
        not something the client paid. Nothing is emitted at all."""
        with db.connect(self.db_path) as conn:
            conn.executemany(_TXN_SQL, [
                _txn("t1", "k1", None, "Service Fee", -5000, "2026-03-01",
                     desc="Service Fee Contract title: Hero GHL landing pages")])
        facts = crm.crm_facts(self.db_path, "hero-marketing-agency",
                              "Hero Marketing Agency", ["Hero GHL landing pages"])
        self.assertEqual([f for f in facts if "Revenue to date" in f["fact_text"]],
                         [])

    def test_direct_revenue_counts_and_the_upwork_split_stays_visible(self):
        """Gross is now `pnl_bucket='revenue'` — the Money tab's own definition —
        so Wise/Stripe/bank income stops being invisible (horizons-aba's dossier
        said $22,694.76 while the Clients tab said $74,361.76 on the same page).
        The Upwork share is printed inside the fact so the previous figure is
        still there to check the new one against."""
        with db.connect(self.db_path) as conn:
            conn.executemany(_TXN_SQL, [
                _txn("t1", "k1", "Horizons ABA", "Hourly", 10000, "2026-01-01",
                     type_="Hourly Earning"),
                _txn("t2", "k2", None, None, 90000, "2026-02-01",
                     type_="Other", source="Wise", desc="Received money from"
                     " Horizons ABA with reference INV-1"),
            ])
        with db.connect(self.db_path) as conn:      # Wise rows carry category
            conn.execute("UPDATE transactions SET category='income'"
                         " WHERE record_id='t2'")
        m = self._money(crm.crm_facts(self.db_path, "horizons-aba",
                                      "Horizons ABA", ["Horizons ABA"]))
        self.assertIn("$1,000.00", m["gross"]["fact_text"])
        self.assertIn("Upwork $100.00", m["gross"]["fact_text"])
        self.assertIn("direct $900.00", m["gross"]["fact_text"])

    def test_ingest_crm_idempotent(self):
        self.seed()
        r1 = crm.ingest_crm(self.db_path, "adelye", "Adelye", ["adélye"])
        r2 = crm.ingest_crm(self.db_path, "adelye", "Adelye", ["adélye"])
        self.assertEqual(r1["inserted"], r2["inserted"])
        n = self.rows("SELECT COUNT(*) c FROM sources WHERE source_type='crm'")
        self.assertEqual(n[0]["c"], 1)                     # one synthetic source
        f = self.rows("SELECT COUNT(*) c FROM facts WHERE client_id='adelye'")
        self.assertEqual(f[0]["c"], r1["inserted"])        # not duplicated

    def test_deaccents_the_column_too(self):
        """norm() folds accents on BOTH sides now, so the plain registry
        spelling reaches the accented ledger name (the whole reason
        cockpit/data.py carries _deaccent)."""
        self.seed()
        facts = crm.crm_facts(self.db_path, "adelye", "Adelye", ["Adelye"])
        self.assertIn("$500.00", self._money(facts)["gross"]["fact_text"])

    def _seed_double_space(self):
        with db.connect(self.db_path) as conn:
            conn.executemany(_TXN_SQL, [
                _txn("t1", "k1", "Tanushant  Sharma", "Hourly", 2950,
                     "2025-01-19", type_="Hourly Earning",
                     desc="(39507591) Tanushant  Sharma - 1:00 hrs")])
            conn.execute(
                "INSERT INTO contracts (id, title, client_company, upwork_status)"
                " VALUES ('39507591','Ai automation','Tanushant  Sharma','ACTIVE')")

    def test_double_space_in_the_ledger_still_matches(self):
        """The whitespace bug's SECOND home. registry.norm() was fixed on
        2026-08-13, but this module compared raw SQL `LIKE '%<id>%'` against the
        raw column, which norm() never touched — so `%Tanushant Sharma%` still
        missed 'Tanushant  Sharma' and the dossier compiled to 0 facts while the
        ledger held two ACTIVE contracts and $757.23 net."""
        self._seed_double_space()
        facts = crm.crm_facts(self.db_path, "tanushant-sharma",
                              "Tanushant Sharma", ["Tanushant Sharma"])
        texts = [f["fact_text"] for f in facts]
        self.assertTrue(any("Contract: Ai automation" in t for t in texts), texts)
        self.assertIn("$29.50", self._money(facts)["gross"]["fact_text"])

    def _seed_masked(self):
        with db.connect(self.db_path) as conn:
            conn.executemany(_TXN_SQL, [
                _txn("t1", "k1", "S", "Milestone", 45000, "2025-05-26",
                     type_="Fixed Earning", desc="Invoice for AI Engineer"),
                _txn("t2", "k2", "Sunny Systems", "Milestone", 90000,
                     "2025-05-26", type_="Fixed Earning",
                     desc="Invoice for Some Other Job")])

    def test_ledger_label_matches_exactly_not_as_a_substring(self):
        """Upwork masked Saman Shafigh's company down to 'S'. The registry
        records that as billing.ledger_label and refuses to make it an alias —
        a one-character alias would claim everything. Matched here by EXACT
        equality on the company column, it reaches $450 that was in the ledger
        and in no dossier; it must NOT drag in 'Sunny Systems'."""
        self._seed_masked()
        facts = crm.crm_facts(self.db_path, "saman-shafigh", "Saman Shafigh",
                              ["Saman Shafigh"], ledger_labels=["S"])
        gross = self._money(facts)["gross"]
        self.assertIn("$450.00", gross["fact_text"])
        self.assertIn("1 revenue transaction", gross["fact_text"])
        self.assertNotIn("$1,350.00", gross["fact_text"])   # Sunny Systems folded in

    def test_no_labels_means_the_masked_money_stays_out(self):
        self._seed_masked()
        facts = crm.crm_facts(self.db_path, "saman-shafigh", "Saman Shafigh",
                              ["Saman Shafigh"])
        self.assertEqual(facts, [])

    def test_blank_identifier_does_not_claim_the_whole_ledger(self):
        """A whitespace-only identifier normalises to '' and `LIKE '%%'` matches
        every row — one blank alias would hand one client the entire business."""
        self.seed()
        self.assertEqual(
            crm.crm_facts(self.db_path, "ghost", "Ghost", ["  ", "\n"]), [])


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

    def test_reattribution_empties_the_losing_dossier(self):
        """A source moved to another client (jarvis heal / mapping correction)
        leaves its old client with 0 facts while the KB stays full. That is a
        REAL emptying, not a transient read: before 2026-08-13 the guard
        swallowed it and froze dossier_index forever — `optimize-is` claimed 17
        facts for four days after its only call was unmapped, and those 17 were
        double-counted against the Knowledge cards."""
        dz = self.tmp / "dz"
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO sources (source_type, source_ref,"
                         " content_hash, client_id) VALUES"
                         " ('fathom','fathom/1','h1','adelye'),"
                         " ('fathom','fathom/2','h2','hermon')")
            sids = [r["id"] for r in conn.execute("SELECT id FROM sources ORDER BY id")]
        index.index_facts(self.db_path, sids[0], "adelye", self.FACTS)
        index.index_facts(self.db_path, sids[1], "hermon", self.FACTS)
        dossier.compile_dossier(self.db_path, "adelye", "Adelye",
                                "2026-06-16", dossier_dir=dz)
        # the source is re-attributed away from adelye; hermon's facts remain
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE facts SET client_id=NULL WHERE source_id=?",
                         (sids[0],))
        out = dossier.compile_dossier(self.db_path, "adelye", "Adelye",
                                      "2026-06-17", dossier_dir=dz)
        self.assertEqual(out["status"], "compiled")
        self.assertEqual(out["facts"], 0)
        self.assertEqual(out["prev_facts"], 3)
        idx = self.rows("SELECT fact_count FROM dossier_index WHERE"
                        " client_id='adelye'")
        self.assertEqual(idx[0]["fact_count"], 0)          # index tells the truth
        self.assertNotIn("Uses Make.com", (dz / "adelye.md").read_text())

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

    def test_roster_carries_ledger_labels_but_never_as_identifiers(self):
        """billing.ledger_label reaches CRM matching through its own key. It
        must NOT leak into `identifiers`, which are matched as substrings — 'S'
        as a substring would claim the whole ledger."""
        reg = registry.loads_registry(
            "clients:\n  -\n    id: saman-shafigh\n    name: Saman Shafigh\n"
            "    billing:\n      canonical: Saman Shafigh\n"
            '      ledger_label: "S"\n'
            "  -\n    id: adelye\n    name: Adelye\n")
        with mock.patch.object(registry, "load", return_value=reg):
            cmap = {c["client_id"]: c for c in clients.load_client_map()}
        self.assertEqual(cmap["saman-shafigh"]["ledger_labels"], ["S"])
        self.assertNotIn("S", cmap["saman-shafigh"]["identifiers"])
        self.assertEqual(cmap["adelye"]["ledger_labels"], [])

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
