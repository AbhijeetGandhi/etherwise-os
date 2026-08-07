"""Today card engine v2 — atomic cards, no relative-date/count titles, and the
cross-source closure (Jarvis J1.5). Acceptance: an Upwork ask closed on a Fathom
call auto-closes (or offers a confirm chip) — never renders as still-open.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from pathlib import Path

from core import config, db, registry
from modules.chief_of_staff import closure, nudges
from modules.cockpit import actions, data

NOW = datetime(2026, 8, 7, 9, 0, tzinfo=config.TZ)

REG = """\
clients:
  -
    id: adelye
    name: Adelye
    billing:
      canonical: Adelye
"""


class FakeCtx:
    def __init__(self, dbp, conf):
        self.db_path = dbp
        self.conf = conf
        self.calls = []

    def log(self, m):
        pass

    def claude(self, **kw):
        self.calls.append(kw)
        payload = json.loads(kw["user_content"])
        cid = payload["open_cards"][0]["card_id"]
        return SimpleNamespace(parsed={"closures": [
            {"card_id": cid, "resolved": True, "resolved_by_cite": "fathom/9",
             "confidence": self.conf}]})


def _seed_commitment_card(dbp):
    """A commitment card for Adelye + the Fathom call that resolved it."""
    with db.connect(dbp) as conn:
        conn.execute(
            "INSERT INTO commitments (id, who, client_id, text, due, source_cite,"
            " dedup_key, state) VALUES (1,'us','adelye','Ship the register',"
            " '2026-08-20','fathom/1 @ 0','commitment:1','open')")
        conn.execute(
            "INSERT INTO sources (source_type, source_ref, content_hash, client_id,"
            " content_type, density, title, occurred_dt, raw_path, status) VALUES"
            " ('fathom','fathom/9','h9','adelye','client-call',0.6,'Adelye sync',"
            " '2026-08-08T10:00:00','/x','extracted')")
        sid = conn.execute("SELECT id FROM sources WHERE source_ref='fathom/9'"
                           ).fetchone()["id"]
        conn.execute(
            "INSERT INTO facts (source_id, client_id, category, fact_text,"
            " citation, confidence, scope) VALUES (?,'adelye','overview',"
            " 'The register is done and live.','fathom/9 @ 1','CONFIRMED',"
            " 'client')", (sid,))
    nudges.write_nudge(dbp, source="commitment", dept="knowledge",
                       kind="commitment_due", title="Commitment due: Ship the register",
                       client="adelye", due="2026-08-20",
                       dedup_key="commitment:1", payload={"cite": "fathom/1 @ 0"},
                       now=NOW)


class ClosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = Path(self.tmp.name) / "t.db"
        db.migrate(self.dbp)
        rp = Path(self.tmp.name) / "clients.yaml"
        rp.write_text(REG)
        self.reg = registry.load(rp, force=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _card_id(self):
        with db.connect(self.dbp) as conn:
            return conn.execute("SELECT id FROM nudges WHERE"
                                " dedup_key='commitment:1'").fetchone()["id"]

    def test_acceptance_high_confidence_auto_closes(self):
        _seed_commitment_card(self.dbp)
        ctx = FakeCtx(self.dbp, conf=0.9)
        res = closure.find_and_apply(ctx, self.dbp, reg=self.reg, now=NOW)
        self.assertEqual(res["auto_closed"], 1)
        self.assertEqual(len(ctx.calls), 1)               # one Haiku call for the client
        with db.connect(self.dbp) as conn:
            r = conn.execute("SELECT closure_state, closure_source FROM nudges"
                             " WHERE id=?", (self._card_id(),)).fetchone()
        self.assertEqual(r["closure_state"], "auto_closed")
        self.assertEqual(r["closure_source"], "fathom/9")

    def test_render_hides_auto_closed_card(self):
        _seed_commitment_card(self.dbp)
        nudges.set_closure(self.dbp, self._card_id(), closure_state="auto_closed",
                           source="fathom/9", confidence=0.9)
        out = data.today(self.dbp, today="2026-08-07")
        keys = [q.get("item_key") for q in out["queue"]]
        self.assertNotIn("nudge:commitment:1", keys)      # never shown as open
        self.assertGreaterEqual(out["queue_auto_closed"], 1)

    def test_medium_confidence_offers_confirm_chip(self):
        _seed_commitment_card(self.dbp)
        ctx = FakeCtx(self.dbp, conf=0.55)
        res = closure.find_and_apply(ctx, self.dbp, reg=self.reg, now=NOW)
        self.assertEqual(res["chipped"], 1)
        out = data.today(self.dbp, today="2026-08-07")
        card = next(q for q in out["queue"] if q.get("item_key") == "nudge:commitment:1")
        self.assertIn("possibly_resolved", card)
        self.assertEqual(card["possibly_resolved"]["source"], "fathom/9")

    def test_confirm_chip_closes_reject_keeps_open(self):
        _seed_commitment_card(self.dbp)
        nudges.set_closure(self.dbp, self._card_id(),
                           closure_state="possibly_resolved", source="fathom/9")
        # reject -> stays open (render normal, no chip)
        actions.card_closure(self.dbp, {"item_key": "nudge:commitment:1",
                                        "action": "reject"})
        out = data.today(self.dbp, today="2026-08-07")
        card = next(q for q in out["queue"] if q.get("item_key") == "nudge:commitment:1")
        self.assertNotIn("possibly_resolved", card)
        # confirm -> hidden
        actions.card_closure(self.dbp, {"item_key": "nudge:commitment:1",
                                        "action": "confirm"})
        out = data.today(self.dbp, today="2026-08-07")
        self.assertNotIn("nudge:commitment:1",
                         [q.get("item_key") for q in out["queue"]])

    def test_empty_skip_no_llm(self):
        ctx = FakeCtx(self.dbp, conf=0.9)
        res = closure.find_and_apply(ctx, self.dbp, reg=self.reg, now=NOW)
        self.assertEqual(res["checked"], 0)
        self.assertEqual(ctx.calls, [])                   # no closeable cards -> no LLM


class TitleHygieneTests(unittest.TestCase):
    def test_relative_dates_stripped_from_dated_title(self):
        self.assertEqual(
            nudges.clean_card_title("Send the demo tomorrow (Aug 6)"),
            "Send the demo")
        self.assertEqual(
            nudges.clean_card_title("Ship v2 (2026-08-06)"), "Ship v2")
        # a non-dated system title with 'today' mid-string is untouched
        self.assertEqual(
            nudges.clean_card_title("Spend $4/5 today — near cap"),
            "Spend $4/5 today — near cap")

    def test_dated_card_stored_without_relative_date(self):
        with tempfile.TemporaryDirectory() as d:
            dbp = Path(d) / "t.db"
            db.migrate(dbp)
            nudges.write_nudge(dbp, source="commitment", dept="knowledge",
                               kind="commitment_due",
                               title="Commitment due: Send the demo tomorrow (Aug 6)",
                               due="2026-08-06", dedup_key="commitment:9",
                               payload={"cite": "x"}, now=NOW)
            with db.connect(dbp) as conn:
                t = conn.execute("SELECT title FROM nudges WHERE"
                                 " dedup_key='commitment:9'").fetchone()["title"]
            self.assertNotIn("tomorrow", t)
            self.assertNotIn("(Aug 6)", t)

    def test_context_counts_stripped(self):
        self.assertEqual(
            nudges.strip_context_counts(
                "ManyChat/dashboard commitment due Fri sits atop 416 open "
                "commitments —"),
            "ManyChat/dashboard commitment due Fri")
        self.assertEqual(
            nudges.strip_context_counts("KB/POC commitment amid 25 open"),
            "KB/POC commitment")
        # a normal title is untouched
        self.assertEqual(nudges.strip_context_counts("Ship the register"),
                         "Ship the register")

    def test_stale_commitment_is_not_carded(self):
        from modules.chief_of_staff import rules
        with tempfile.TemporaryDirectory() as d:
            dbp = Path(d) / "t.db"
            db.migrate(dbp)
            with db.connect(dbp) as conn:
                conn.execute(
                    "INSERT INTO commitments (id, who, client_id, text, due,"
                    " source_cite, dedup_key, state, review_state) VALUES"
                    " (1,'us','adelye','Old promise','2026-08-07','f/1 @ 0',"
                    " 'commitment:1','open','stale')")
                conn.execute(
                    "INSERT INTO commitments (id, who, client_id, text, due,"
                    " source_cite, dedup_key, state) VALUES"
                    " (2,'us','adelye','Live promise','2026-08-07','f/2 @ 0',"
                    " 'commitment:2','open')")
            cands = rules.rule_commitments_due(dbp, now=NOW)
        keys = {c["dedup_key"] for c in cands}
        self.assertIn("commitment:2", keys)        # live one cards
        self.assertNotIn("commitment:1", keys)     # stale one does not

    def test_generators_have_no_count_in_title(self):
        from modules.chief_of_staff import rules
        with tempfile.TemporaryDirectory() as d:
            dbp = Path(d) / "t.db"
            db.migrate(dbp)
            with db.connect(dbp) as conn:
                conn.execute("INSERT INTO sources (source_type, source_ref,"
                             " content_hash, client_id, status) VALUES"
                             " ('fathom','f/1','h1','adelye','ingested')")
            cands = rules.rule_stale_dossier(dbp)
        self.assertTrue(cands)
        self.assertNotRegex(cands[0]["title"], r"^\d")     # no leading count
        self.assertEqual(cands[0]["payload"]["ingested"], 1)  # count in payload


if __name__ == "__main__":
    unittest.main()
