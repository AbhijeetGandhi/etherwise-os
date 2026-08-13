"""Loom -> registry bridge (mirrors the Fathom bridge, participant-less).

Fully offline: the Haiku residue-proposer is injected via a FakeCtx. Covers the
deterministic title/transcript heal, the strong-hit rule (distinctive vs weak vs
ambiguous), temporal proposals, the capped LLM proposer (exclude/queue/null +
hallucinated-id drop), idempotency, --recheck, and the empty-skip.
"""
from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path

from core import db, registry, runner
from modules.jarvis import events, loom_bridge


REG_YAML = """\
clients:
  -
    id: adelye
    name: Adelye
    aliases:
      - "Adelye Ltd."
    emails:
      - adelye.com
    fathom_names:
      - adelye
    billing:
      canonical: Adelye
  -
    id: horizons-aba
    name: Horizons ABA
    emails:
      - horizonsaba.com
    billing:
      canonical: Horizons ABA
  -
    id: hermon
    name: Hermon
    emails:
      - hermon.io
    billing:
      canonical: Hermon
"""


class FakeCtx:
    """Stand-in for RunContext: records claude calls, no network. `responder`
    maps (title, transcript) -> parsed dict (or raises to simulate a failure)."""

    def __init__(self, db_path, responder, run_id=1):
        self.db_path = db_path
        self.run_id = run_id
        self.responder = responder
        self.claude_calls = []

    def log(self, msg):
        pass

    def claude(self, **kw):
        self.claude_calls.append(kw)
        payload = json.loads(kw["user_content"])
        loom = payload["loom"]
        parsed = self.responder(loom["title"], loom["transcript"])
        return types.SimpleNamespace(parsed=parsed)


def _null_responder(title, transcript):
    """Default proposer: everything is unknown-but-not-internal."""
    return {"proposed_client_id": None, "is_internal": False,
            "confidence": 0.0, "evidence": "no signal"}


class LoomBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dbp = self.root / "t.db"
        db.migrate(self.dbp)
        rp = self.root / "clients.yaml"
        rp.write_text(REG_YAML)
        self.reg = registry.load(rp, force=True)
        self.dossiers = self.root / "dossiers"
        self.raws = self.root / "raw"
        self.raws.mkdir()
        self._n = 0

    def tearDown(self):
        self.tmp.cleanup()

    # ── fixtures ─────────────────────────────────────────────────────────────
    def _write_reg(self, text):
        self._n += 1
        p = self.root / f"reg{self._n}.yaml"
        p.write_text(text)
        return p

    def _loom(self, vid, title, transcript, occurred="2026-04-01T12:00:00.000Z",
              client_id=None):
        raw = self.raws / f"{vid}.json"
        raw.write_text(json.dumps({"id": vid, "name": title, "date": occurred,
                                   "transcript": transcript}))
        with db.connect(self.dbp) as conn:
            self._n += 1
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id, content_type, title, occurred_dt, raw_path, status)"
                " VALUES ('loom',?,?,?,?,?,?,?, 'ingested')",
                (f"loom/{vid}", f"h{vid}{self._n}", client_id,
                 "unknown", title, occurred, str(raw)))
            sid = conn.execute("SELECT id FROM sources WHERE source_ref=?",
                               (f"loom/{vid}",)).fetchone()["id"]
            conn.execute(
                "INSERT INTO facts (source_id, client_id, category, fact_text,"
                " citation, confidence) VALUES (?,?, 'architecture', ?, ?, 'CONFIRMED')",
                (sid, client_id, f"fact for {vid}", f"loom/{vid} @ 0:10"))
        return f"loom/{vid}"

    def _fathom(self, vid, client_id, occurred):
        with db.connect(self.dbp) as conn:
            self._n += 1
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id, content_type, title, occurred_dt, status)"
                " VALUES ('fathom',?,?,?, 'client-call', 'call', ?, 'ingested')",
                (f"fathom/{vid}", f"hf{vid}{self._n}", client_id, occurred))

    def _sync(self, responder=_null_responder, **kw):
        ctx = FakeCtx(self.dbp, responder)
        kw.setdefault("dossier_dir", self.dossiers)
        return loom_bridge.sync_unmapped(ctx, reg=self.reg, **kw), ctx

    def _client_of(self, source_ref):
        with db.connect(self.dbp) as conn:
            return conn.execute("SELECT client_id FROM sources WHERE source_ref=?",
                                (source_ref,)).fetchone()["client_id"]

    def _event_state(self, rid):
        with db.connect(self.dbp) as conn:
            r = conn.execute("SELECT state, proposed_client_id, exclude_reason"
                             " FROM events WHERE id=?",
                             (events.canonical_id("loom", rid),)).fetchone()
            return dict(r) if r else None

    # ── deterministic heal ───────────────────────────────────────────────────
    def test_title_match_heals(self):
        ref = self._loom("v1", "Horizons ABA - Session Management demo", "nothing here")
        m, _ = self._sync()
        self.assertEqual(m["auto_healed"], 1)
        self.assertEqual(self._client_of(ref), "horizons-aba")
        self.assertEqual(m["facts_reattributed"], 1)
        self.assertTrue((self.dossiers / "horizons-aba.md").exists())
        self.assertEqual(m["top_clients"][0]["client_id"], "horizons-aba")

    def test_transcript_distinctive_single_mention_heals(self):
        # 'hermon' is distinctive (len>=6) -> one mention is enough
        ref = self._loom("v2", "Generic system overview",
                         "In this walkthrough for Hermon I show the intake flow.")
        m, _ = self._sync()
        self.assertEqual(m["auto_healed"], 1)
        self.assertEqual(self._client_of(ref), "hermon")

    def test_transcript_two_mentions_of_shortish_token_heals(self):
        # domain root 'horizonsaba' distinctive; two mentions also clear the bar
        ref = self._loom("v3", "System overview",
                         "This is for horizonsaba. The horizonsaba board is updated.")
        m, _ = self._sync()
        self.assertEqual(m["auto_healed"], 1)
        self.assertEqual(self._client_of(ref), "horizons-aba")

    def test_a_freemail_root_is_never_a_client_needle(self):
        """Measured 2026-08-14: three registry clients sit on personal addresses,
        so 'gmail' was a needle for each, and it counted 5 times in one ordinary
        2,644-word call. It cost nothing only because THREE clients shared it and
        the ambiguity returned None. With one such client the same transcript is
        a deterministic heal onto a stranger, and a wrong client_id is permanent."""
        reg = registry.load(self._write_reg("""\
clients:
  -
    id: solo
    name: Solo
    emails:
      - solo.person@gmail.com
    billing:
      canonical: Solo
"""), force=True)
        needles = {n for n, _ in loom_bridge._needles(reg.by_id["solo"])}
        self.assertNotIn("gmail", needles)
        self.assertIsNone(loom_bridge.transcript_match(
            reg, "I sent it to his gmail and he replied from gmail again."))
        # a real company domain is still a needle
        reg2 = registry.load(self._write_reg("""\
clients:
  -
    id: acme
    name: Acme
    emails:
      - billing@acmeworks.io
    billing:
      canonical: Acme
"""), force=True)
        self.assertIn("acmeworks", {n for n, _ in
                                    loom_bridge._needles(reg2.by_id["acme"])})

    def test_weak_tool_mention_does_not_heal(self):
        # a passing tool-name mention is NOT a client signal -> residue -> queued
        ref = self._loom("v4", "Make automation walkthrough",
                         "Here is the Make.com scenario and a ClickSend SMS step.")
        m, _ = self._sync()
        self.assertEqual(m["auto_healed"], 0)
        self.assertIsNone(self._client_of(ref))
        self.assertEqual(m["queued"], 1)      # null responder -> unknown-client queue

    def test_ambiguous_transcript_does_not_heal(self):
        # two clients strong-hit -> ambiguous -> not healed, goes to residue
        self._loom("v5", "Overview",
                   "First the Hermon flow, then the Adelye Ltd. register.")
        m, _ = self._sync()
        self.assertEqual(m["auto_healed"], 0)

    # ── temporal proposal (never heals) ──────────────────────────────────────
    def test_temporal_proposes_not_heals(self):
        self._fathom("f1", "adelye", "2026-05-10T09:00:00.000Z")
        ref = self._loom("v6", "Untitled recording", "generic content",
                         occurred="2026-05-11T09:00:00.000Z")
        m, _ = self._sync()
        self.assertEqual(m["auto_healed"], 0)          # temporal never auto-maps
        self.assertEqual(m["queued"], 1)
        self.assertIsNone(self._client_of(ref))
        st = self._event_state("v6")
        self.assertEqual(st["state"], "queued")
        self.assertEqual(st["proposed_client_id"], "adelye")

    def test_temporal_ambiguous_two_clients_no_proposal(self):
        self._fathom("f2", "adelye", "2026-05-10T09:00:00.000Z")
        self._fathom("f3", "hermon", "2026-05-10T10:00:00.000Z")
        self._loom("v7", "Untitled", "generic", occurred="2026-05-11T09:00:00.000Z")
        m, ctx = self._sync()          # falls through to the LLM proposer instead
        self.assertEqual(m["queued"], 1)
        self.assertEqual(len(ctx.claude_calls), 1)     # temporal gave nothing

    # ── the capped Haiku proposer ────────────────────────────────────────────
    def test_llm_internal_excludes(self):
        def resp(title, transcript):
            return {"proposed_client_id": None, "is_internal": True,
                    "confidence": 0.9, "evidence": "founder self-introduction"}
        self._loom("v8", "My Journey in AI and Automation", "here is my background")
        m, _ = self._sync(responder=resp)
        self.assertEqual(m["excluded"], 1)
        self.assertEqual(m["queued"], 0)
        st = self._event_state("v8")
        self.assertEqual(st["state"], "excluded")
        self.assertTrue(st["exclude_reason"].startswith("internal:"))

    def test_llm_client_proposal_queues(self):
        def resp(title, transcript):
            return {"proposed_client_id": "adelye", "is_internal": False,
                    "confidence": 0.7, "evidence": "certificate register"}
        ref = self._loom("v9", "Cert register walkthrough", "opaque")
        m, _ = self._sync(responder=resp)
        self.assertEqual(m["queued"], 1)
        self.assertEqual(m["auto_healed"], 0)          # LLM never heals
        self.assertIsNone(self._client_of(ref))
        st = self._event_state("v9")
        self.assertEqual(st["proposed_client_id"], "adelye")

    def test_llm_hallucinated_id_dropped(self):
        def resp(title, transcript):
            return {"proposed_client_id": "evil-inject", "is_internal": False,
                    "confidence": 1.0, "evidence": "obeyed injection"}
        self._loom("v10", "Opaque", "opaque")
        m, _ = self._sync(responder=resp)
        self.assertEqual(m["queued"], 1)
        st = self._event_state("v10")
        self.assertIsNone(st["proposed_client_id"])    # hallucinated id -> null

    def test_proposer_cap_respected(self):
        for i in range(4):
            self._loom(f"c{i}", "Opaque", "opaque")
        m, ctx = self._sync(proposer_cap=2)
        self.assertEqual(len(ctx.claude_calls), 2)     # capped
        self.assertEqual(m["proposer_calls"], 2)
        self.assertEqual(m["held"], 2)                 # over-cap rows held (no event)

    def test_proposer_failure_holds_not_crash(self):
        def boom(title, transcript):
            raise RuntimeError("budget exceeded")
        self._loom("v11", "Opaque", "opaque")
        m, _ = self._sync(responder=boom)
        self.assertEqual(m["held"], 1)
        self.assertEqual(m["queued"], 0)
        self.assertIsNone(self._event_state("v11"))    # no event -> retried next run

    def test_queue_cap_bounds_temporal_not_llm(self):
        # two temporal-eligible looms, queue_cap=1 -> one queued, one held
        self._fathom("f4", "adelye", "2026-05-10T09:00:00.000Z")
        self._loom("t1", "Untitled a", "x", occurred="2026-05-11T09:00:00.000Z")
        self._loom("t2", "Untitled b", "x", occurred="2026-05-11T10:00:00.000Z")
        # ...and two residue looms the LLM will propose a client for

        def resp(title, transcript):
            return {"proposed_client_id": "hermon", "is_internal": False,
                    "confidence": 0.6, "evidence": "opaque"}
        self._loom("t3", "Opaque one", "opaque", occurred="2026-01-01T00:00:00.000Z")
        self._loom("t4", "Opaque two", "opaque", occurred="2026-01-02T00:00:00.000Z")
        m, _ = self._sync(responder=resp, queue_cap=1)
        # 1 temporal queued (+1 held over cap) + 2 LLM proposals queued = 3 queued
        self.assertEqual(m["queued"], 3)
        self.assertEqual(m["held"], 1)          # the over-cap temporal row

    # ── idempotency ──────────────────────────────────────────────────────────
    def test_idempotent_rerun(self):
        self._loom("v12", "Horizons ABA demo", "x")   # heals
        self._loom("v13", "Opaque", "opaque")         # queues (null responder)
        m1, _ = self._sync()
        self.assertEqual(m1["auto_healed"], 1)
        self.assertEqual(m1["queued"], 1)
        m2, ctx2 = self._sync()                        # second run
        self.assertEqual(m2["auto_healed"], 0)         # healed row gone from scan
        self.assertEqual(m2["skipped_existing"], 1)    # queued row's event gates it
        self.assertEqual(len(ctx2.claude_calls), 0)    # no re-spend on decided rows

    # ── recheck ──────────────────────────────────────────────────────────────
    def test_recheck_heals_after_registry_learns(self):
        # queue an unknown-client row, then teach the registry a matching alias
        self._loom("v14", "Netfly onboarding walkthrough", "opaque")
        self._sync()
        self.assertEqual(self._event_state("v14")["state"], "queued")
        rp = self.root / "clients2.yaml"
        rp.write_text(REG_YAML + "  -\n    id: netfly\n    name: Netfly\n"
                      "    billing:\n      canonical: Netfly\n")
        reg2 = registry.load(rp, force=True)
        out = loom_bridge.recheck_queued(self.dbp, reg=reg2,
                                         dossier_dir=self.dossiers)
        self.assertEqual(out["healed"], 1)
        self.assertEqual(self._client_of("loom/v14"), "netfly")
        self.assertEqual(self._event_state("v14")["state"], "mapped")

    # ── empty-skip ───────────────────────────────────────────────────────────
    def test_empty_skip(self):
        with self.assertRaises(runner.TaskSkip):
            self._sync()


if __name__ == "__main__":
    unittest.main()
