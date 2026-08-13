"""Loom KIND + domain-vocabulary classifier (QUEUE-12).

Fully offline: the one Haiku classify call is injected via FakeCtx. Covers the
pure routing bands, classify_loom normalization (hallucinated id / bad kind /
clamp), the KIND gate (job-application + personal Looms must NOT become client
work), `unknown` respected, high->provisional-map / med->queue / low->exclude,
the LEDGER-ONLY guarantee (a provisional map never touches sources/facts), the
kept deterministic pass-0 heal, the JARVIS_LOOM_HEAL_ON_HIGH opt-in, idempotency,
--reclassify-queued, --dry-run, and the empty-skip.
"""
from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path

from core import config, db, registry, runner
from modules.jarvis import events, loom_classifier


REG_YAML = """\
clients:
  -
    id: adelye
    name: Adelye
    aliases:
      - "Adelye Ltd."
    emails:
      - adelye.com
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
    def __init__(self, db_path, responder, run_id=1):
        self.db_path = db_path
        self.run_id = run_id
        self.responder = responder
        self.claude_calls = []

    def log(self, msg):
        pass

    def claude(self, **kw):
        self.claude_calls.append(kw)
        loom = json.loads(kw["user_content"])["loom"]
        return types.SimpleNamespace(parsed=self.responder(loom["title"],
                                                           loom["transcript"]))


def _kind(kind, pid=None, conf=0.9, ev="signal"):
    return lambda t, tr: {"kind": kind, "proposed_client_id": pid,
                          "confidence": conf, "evidence": ev}


# ── pure functions (no DB) ───────────────────────────────────────────────────
class PinHealOnHigh:
    """Pin JARVIS_LOOM_HEAL_ON_HIGH for a test class.

    The ledger-only assertions below describe ONE BRANCH of that flag, not the
    shipped default. They used to read the live config, so flipping the default
    to True on 2026-08-13 broke three of them and would have looked like a
    regression in the classifier rather than what it was — a test coupled to a
    setting. Pin it, exactly as the CRM mirror tests pin SHADOW_MODE, so the
    suite says the same thing whichever way the flag is set."""

    def setUpPin(self, value: bool) -> None:
        self._heal_orig = config.JARVIS_LOOM_HEAL_ON_HIGH
        config.JARVIS_LOOM_HEAL_ON_HIGH = value

    def tearDownPin(self) -> None:
        config.JARVIS_LOOM_HEAL_ON_HIGH = self._heal_orig


class ShippedDefaultTests(unittest.TestCase):
    def test_heal_on_high_is_on(self):
        """Abhijeet's call, 2026-08-13. With this False the classifier produced
        no KB value at all: its high-confidence maps left sources.client_id NULL,
        and a Loom fact with a NULL client_id reaches proof-points and the search
        corpus but never the dossier of the client it is about. Reverting this
        should require deleting a test, not editing one line of config."""
        self.assertTrue(config.JARVIS_LOOM_HEAL_ON_HIGH)


class RouteTests(unittest.TestCase):
    HIGH, MED = 0.80, 0.50

    def test_high_conf_client_work_maps(self):
        self.assertEqual(loom_classifier.route("client-work", "adelye", 0.9,
                                               self.HIGH, self.MED),
                         ("map", "adelye"))

    def test_medium_conf_queues(self):
        self.assertEqual(loom_classifier.route("client-work", "adelye", 0.6,
                                               self.HIGH, self.MED),
                         ("queue", "adelye"))

    def test_low_conf_client_work_unattributable(self):
        self.assertEqual(loom_classifier.route("client-work", "adelye", 0.3,
                                               self.HIGH, self.MED),
                         ("exclude", "loom:unattributable"))

    def test_client_work_null_id_unattributable(self):
        # unknown is RESPECTED -- high confidence but no client is not a forced map
        self.assertEqual(loom_classifier.route("client-work", None, 0.95,
                                               self.HIGH, self.MED),
                         ("exclude", "loom:unattributable"))

    def test_non_client_kinds_exclude_by_kind(self):
        for k in ("upwork-proposal", "internal-product", "personal-noise"):
            self.assertEqual(loom_classifier.route(k, None, 0.9,
                                                   self.HIGH, self.MED),
                             ("exclude", f"loom-kind:{k}"))

    def test_unrecognized_kind_unattributable(self):
        self.assertEqual(loom_classifier.route(None, None, 0.9,
                                               self.HIGH, self.MED),
                         ("exclude", "loom:unattributable"))


class PureHelperTests(unittest.TestCase):
    def test_truncate_words(self):
        self.assertEqual(loom_classifier.truncate_words("a b c d e", 3), "a b c")
        self.assertEqual(loom_classifier.truncate_words("", 3), "")
        self.assertEqual(loom_classifier.truncate_words(None, 3), "")

    def test_fingerprint_candidates_filters_to_valid_ids(self):
        fps = [{"client_id": "adelye", "name": "Adelye", "terms": ["FORS"]},
               {"client_id": "ghost", "name": "Ghost", "terms": ["x"]}]
        out = loom_classifier.fingerprint_candidates(fps, {"adelye"})
        self.assertEqual([c["id"] for c in out], ["adelye"])
        self.assertEqual(out[0]["terms"], ["FORS"])

    def test_classify_loom_drops_hallucinated_id_and_bad_kind(self):
        ctx = FakeCtx(None, _kind("client-work", pid="not-a-client", conf=0.9))
        r = loom_classifier.classify_loom(ctx, "t", "tr", [], {"adelye"})
        self.assertEqual(r["kind"], "client-work")
        self.assertIsNone(r["proposed_client_id"])          # hallucinated -> null

    def test_classify_loom_non_client_forces_null_client(self):
        ctx = FakeCtx(None, _kind("personal-noise", pid="adelye", conf=0.9))
        r = loom_classifier.classify_loom(ctx, "t", "tr", [], {"adelye"})
        self.assertIsNone(r["proposed_client_id"])

    def test_classify_loom_clamps_confidence(self):
        ctx = FakeCtx(None, _kind("client-work", pid="adelye", conf=9.0))
        r = loom_classifier.classify_loom(ctx, "t", "tr", [], {"adelye"})
        self.assertEqual(r["confidence"], 1.0)

    def test_classify_loom_none_on_failure(self):
        def boom(**kw):
            raise RuntimeError("budget")
        ctx = types.SimpleNamespace(log=lambda m: None, claude=boom)
        self.assertIsNone(loom_classifier.classify_loom(ctx, "t", "tr", [], set()))


# ── the run (temp DB) ────────────────────────────────────────────────────────
class ClassifyRunTests(unittest.TestCase, PinHealOnHigh):
    def setUp(self):
        self.setUpPin(False)          # these assert the LEDGER-ONLY branch
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
        self.tearDownPin()
        self.tmp.cleanup()

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
                (f"loom/{vid}", f"h{vid}{self._n}", client_id, "unknown",
                 title, occurred, str(raw)))
            sid = conn.execute("SELECT id FROM sources WHERE source_ref=?",
                               (f"loom/{vid}",)).fetchone()["id"]
            conn.execute(
                "INSERT INTO facts (source_id, client_id, category, fact_text,"
                " citation, confidence) VALUES (?,?, 'architecture', ?, ?,"
                " 'CONFIRMED')", (sid, client_id, f"fact {vid}", f"loom/{vid} @ 0:10"))
        return f"loom/{vid}"

    def _run(self, responder, **kw):
        ctx = FakeCtx(self.dbp, responder)
        kw.setdefault("fingerprints", [])
        kw.setdefault("dossier_dir", self.dossiers)
        return loom_classifier.classify_unmapped(ctx, reg=self.reg, **kw), ctx

    def _source_client(self, ref):
        with db.connect(self.dbp) as conn:
            return conn.execute("SELECT client_id FROM sources WHERE source_ref=?",
                                (ref,)).fetchone()["client_id"]

    def _fact_client(self, ref):
        with db.connect(self.dbp) as conn:
            return conn.execute(
                "SELECT f.client_id FROM facts f JOIN sources s ON s.id=f.source_id"
                " WHERE s.source_ref=?", (ref,)).fetchone()["client_id"]

    def _event(self, rid):
        with db.connect(self.dbp) as conn:
            r = conn.execute(
                "SELECT state, client_id, proposed_client_id, proposal_confidence,"
                " proposal_evidence, exclude_reason, resolved_by FROM events"
                " WHERE id=?", (events.canonical_id("loom", rid),)).fetchone()
            return dict(r) if r else None

    # KIND gate -----------------------------------------------------------------
    def test_job_application_loom_not_client_work(self):
        self._loom("v1", "Excited to Apply for the AI and Automation Engineer "
                   "Position", "Here is my background and why I would be great.")
        m, _ = self._run(_kind("upwork-proposal"))
        self.assertEqual(m["excluded"], 1)
        self.assertEqual(m["provisional_mapped"], 0)
        self.assertEqual(self._event("v1")["exclude_reason"],
                         "loom-kind:upwork-proposal")

    def test_personal_noise_loom_not_client_work(self):
        self._loom("v2", "Men's Jackets & Coats | REI", "browsing some jackets")
        m, _ = self._run(_kind("personal-noise"))
        self.assertEqual(m["excluded"], 1)
        self.assertEqual(self._event("v2")["exclude_reason"],
                         "loom-kind:personal-noise")

    def test_unknown_client_is_respected_not_forced(self):
        ref = self._loom("v3", "System walkthrough", "opaque narration")
        m, _ = self._run(_kind("client-work", pid=None, conf=0.9))
        self.assertEqual(m["excluded"], 1)
        self.assertEqual(m["provisional_mapped"], 0)
        self.assertIsNone(self._source_client(ref))
        self.assertEqual(self._event("v3")["exclude_reason"], "loom:unattributable")

    # routing -------------------------------------------------------------------
    def test_high_conf_provisional_map_is_ledger_only(self):
        ref = self._loom("v4", "Session cancellation update",
                         "the automation cancels sessions and exports payroll")
        m, _ = self._run(_kind("client-work", pid="horizons-aba", conf=0.9,
                               ev="session cancellation + payroll"))
        self.assertEqual(m["provisional_mapped"], 1)
        ev = self._event("v4")
        self.assertEqual(ev["state"], "mapped")
        self.assertEqual(ev["client_id"], "horizons-aba")
        self.assertEqual(ev["resolved_by"], "loom_classifier:high")
        self.assertEqual(ev["proposal_evidence"], "session cancellation + payroll")
        # the LEDGER-ONLY guarantee: sources + facts are NOT touched by the model
        self.assertIsNone(self._source_client(ref))
        self.assertIsNone(self._fact_client(ref))
        self.assertEqual(m["facts_reattributed"], 0)

    def test_medium_conf_queues_with_proposal(self):
        self._loom("v5", "Register walkthrough", "course registration + FORS")
        m, _ = self._run(_kind("client-work", pid="adelye", conf=0.6,
                               ev="FORS course registration"))
        self.assertEqual(m["queued"], 1)
        ev = self._event("v5")
        self.assertEqual(ev["state"], "queued")
        self.assertEqual(ev["proposed_client_id"], "adelye")
        self.assertEqual(ev["proposal_evidence"], "FORS course registration")

    # deterministic pass-0 (kept) ----------------------------------------------
    def test_deterministic_title_match_still_full_heals(self):
        ref = self._loom("v6", "Horizons ABA - onboarding demo", "no vocab needed")
        m, ctx = self._run(_kind("personal-noise"))   # LLM would exclude; det wins
        self.assertEqual(m["det_healed"], 1)
        self.assertEqual(len(ctx.claude_calls), 0)     # no LLM spend on a det match
        self.assertEqual(self._source_client(ref), "horizons-aba")
        self.assertEqual(self._fact_client(ref), "horizons-aba")
        self.assertEqual(m["facts_reattributed"], 1)

    # heal-on-high opt-in -------------------------------------------------------
    def test_heal_on_high_flag_promotes_to_full_heal(self):
        ref = self._loom("v7", "Session update", "cancels sessions")
        orig = config.JARVIS_LOOM_HEAL_ON_HIGH
        config.JARVIS_LOOM_HEAL_ON_HIGH = True
        try:
            m, _ = self._run(_kind("client-work", pid="horizons-aba", conf=0.9))
        finally:
            config.JARVIS_LOOM_HEAL_ON_HIGH = orig
        self.assertEqual(m["llm_healed"], 1)
        self.assertEqual(m["provisional_mapped"], 0)
        self.assertEqual(self._source_client(ref), "horizons-aba")  # facts promoted
        self.assertEqual(self._event("v7")["resolved_by"], "loom_classifier:high")

    # idempotency ---------------------------------------------------------------
    def test_idempotent_second_run_skips_and_spends_nothing(self):
        self._loom("v8", "walkthrough", "opaque")
        self._run(_kind("client-work", pid="adelye", conf=0.6))
        m2, ctx2 = self._run(_kind("client-work", pid="adelye", conf=0.6))
        self.assertEqual(m2["skipped_existing"], 1)
        self.assertEqual(len(ctx2.claude_calls), 0)    # decided event gates re-work

    def test_max_calls_ceiling_holds_residue(self):
        self._loom("v9", "a", "x")
        self._loom("v10", "b", "y")
        m, ctx = self._run(_kind("client-work", pid="adelye", conf=0.6), max_calls=1)
        self.assertEqual(len(ctx.claude_calls), 1)
        self.assertEqual(m["held"], 1)

    # dry-run -------------------------------------------------------------------
    def test_dry_run_spends_but_writes_nothing(self):
        self._loom("v11", "walkthrough", "opaque")
        m, ctx = self._run(_kind("client-work", pid="adelye", conf=0.9),
                           dry_run=True)
        self.assertEqual(len(ctx.claude_calls), 1)     # cost is measured
        self.assertEqual(m["provisional_mapped"], 1)   # intent counted
        self.assertIsNone(self._event("v11"))          # but nothing persisted

    # empty skip ----------------------------------------------------------------
    def test_empty_scan_task_skips(self):
        with self.assertRaises(runner.TaskSkip):
            self._run(_kind("client-work"))


# ── reclassify the existing queued backlog ───────────────────────────────────
class ReclassifyQueuedTests(unittest.TestCase, PinHealOnHigh):
    def setUp(self):
        self.setUpPin(False)          # these assert the LEDGER-ONLY branch
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

    def tearDown(self):
        self.tearDownPin()
        self.tmp.cleanup()

    def _queued_loom(self, vid, title, transcript, proposed="horizons-aba"):
        raw = self.raws / f"{vid}.json"
        raw.write_text(json.dumps({"id": vid, "name": title,
                                   "transcript": transcript}))
        ref = f"loom/{vid}"
        with db.connect(self.dbp) as conn:
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id, content_type, title, occurred_dt, raw_path, status)"
                " VALUES ('loom',?,?,NULL,'unknown',?, '2026-04-01T00:00:00Z',?,"
                " 'ingested')", (ref, f"h{vid}", title, str(raw)))
            eid = events.canonical_id("loom", vid)
            conn.execute(
                "INSERT INTO events (id, source, native_id, subject, state,"
                " raw_ref, proposed_client_id, proposal_confidence) VALUES"
                " (?, 'loom', ?, ?, 'queued', ?, ?, 0.5)",
                (eid, vid, title, ref, proposed))
        return vid

    def _run(self, responder, **kw):
        ctx = FakeCtx(self.dbp, responder)
        kw.setdefault("fingerprints", [])
        kw.setdefault("dossier_dir", self.dossiers)
        return loom_classifier.reclassify_queued(ctx, reg=self.reg, **kw), ctx

    def _event(self, vid):
        with db.connect(self.dbp) as conn:
            return dict(conn.execute(
                "SELECT state, client_id, exclude_reason, resolved_by,"
                " proposal_evidence FROM events WHERE id=?",
                (events.canonical_id("loom", vid),)).fetchone())

    def test_queued_noise_gets_excluded(self):
        self._queued_loom("q1", "Untitled recording", "generic")
        m, _ = self._run(_kind("personal-noise"))
        self.assertEqual(m["excluded"], 1)
        ev = self._event("q1")
        self.assertEqual(ev["state"], "excluded")
        self.assertEqual(ev["exclude_reason"], "loom-kind:personal-noise")
        self.assertEqual(ev["resolved_by"], "loom_classifier")

    def test_queued_high_conf_becomes_provisional_map(self):
        self._queued_loom("q2", "Session update", "cancels sessions")
        m, _ = self._run(_kind("client-work", pid="adelye", conf=0.9, ev="regi"))
        self.assertEqual(m["provisional_mapped"], 1)
        ev = self._event("q2")
        self.assertEqual(ev["state"], "mapped")
        self.assertEqual(ev["client_id"], "adelye")
        self.assertEqual(ev["resolved_by"], "loom_classifier:high")

    def test_queued_deterministic_match_wins_first(self):
        self._queued_loom("q3", "Hermon intake walkthrough", "for the client")
        m, ctx = self._run(_kind("personal-noise"))
        self.assertEqual(m["det_healed"], 1)
        self.assertEqual(len(ctx.claude_calls), 0)
        self.assertEqual(self._event("q3")["client_id"], "hermon")


class FingerprintDisambiguationTests(unittest.TestCase):
    """Horizons vs Adelye both do 'course automation' — the committed fingerprint
    asset must carry the vocabulary that separates them (RBT/BCBA is Horizons;
    FORS/CPC/transport is Adelye), so the classifier stops mis-proposing Adelye
    for an 'RBT Course Automation' Loom (it did, at 0.72)."""

    def setUp(self):
        self.fps = {f["client_id"]: f
                    for f in loom_classifier.load_fingerprints()}

    def test_horizons_carries_rbt_vocabulary(self):
        terms = " ".join(self.fps["horizons-aba"]["terms"]).lower()
        for needle in ("rbt", "bcba", "registered behavior technician"):
            self.assertIn(needle, terms)

    def test_adelye_is_transport_certification_not_rbt(self):
        f = self.fps["adelye"]
        blob = (f["domain"] + " " + " ".join(f["terms"])).lower()
        for needle in ("fors", "cpc", "transport certification"):
            self.assertIn(needle, blob)
        # the confusing overlap must NOT be attributed to Adelye
        self.assertNotIn("rbt", " ".join(f["terms"]).lower())


# ── heal-mapped backfill (QUEUE-23) ──────────────────────────────────────────
class HealMappedTests(unittest.TestCase):
    """The backfill that lands tonight's classification in the KB: for every event
    already MAPPED to a client whose joined source is still unattributed, run the
    SAME shared heal the approval path performs. Idempotent + re-runnable; generic
    over source_type (loom now, fathom after its residue is classified)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dbp = self.root / "t.db"
        db.migrate(self.dbp)
        rp = self.root / "clients.yaml"
        rp.write_text(REG_YAML)
        self.reg = registry.load(rp, force=True)
        self.dossiers = self.root / "dossiers"
        self._n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def _mapped(self, vid, client_id, source_type="loom", *, source_client=None):
        """A provisional map: event state='mapped' w/ client_id, but the source is
        left unattributed (client_id=source_client, NULL by default) + one fact."""
        ref = f"{source_type}/{vid}"
        with db.connect(self.dbp) as conn:
            self._n += 1
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id, content_type, title, occurred_dt, raw_path, status)"
                " VALUES (?,?,?,?, 'unknown', ?, '2026-04-01T00:00:00Z', ?,"
                " 'ingested')", (source_type, ref, f"h{vid}{self._n}",
                                 source_client, f"title {vid}", f"/tmp/{vid}.json"))
            sid = conn.execute("SELECT id FROM sources WHERE source_ref=?",
                               (ref,)).fetchone()["id"]
            conn.execute(
                "INSERT INTO facts (source_id, client_id, category, fact_text,"
                " citation, confidence) VALUES (?,?, 'architecture', ?, ?,"
                " 'CONFIRMED')", (sid, source_client, f"fact {vid}",
                                  f"{ref} @ 0:10"))
            eid = events.canonical_id(source_type, vid)
            conn.execute(
                "INSERT INTO events (id, source, native_id, subject, client_id,"
                " state, raw_ref, resolved_by) VALUES (?,?,?,?,?, 'mapped', ?,"
                " ?)", (eid, source_type, vid, f"title {vid}", client_id, ref,
                        f"{source_type}_classifier:high"))
        return ref

    def _src_client(self, ref):
        with db.connect(self.dbp) as conn:
            return conn.execute("SELECT client_id FROM sources WHERE source_ref=?",
                                (ref,)).fetchone()["client_id"]

    def _fact_client(self, ref):
        with db.connect(self.dbp) as conn:
            return conn.execute(
                "SELECT f.client_id FROM facts f JOIN sources s ON s.id=f.source_id"
                " WHERE s.source_ref=?", (ref,)).fetchone()["client_id"]

    def _run(self, **kw):
        ctx = FakeCtx(self.dbp, None)
        kw.setdefault("dossier_dir", self.dossiers)
        return loom_classifier.heal_mapped(ctx, reg=self.reg, **kw)

    def test_backfill_heals_source_and_facts(self):
        r1 = self._mapped("v1", "horizons-aba")
        r2 = self._mapped("v2", "adelye")
        m = self._run()
        self.assertEqual(m["candidates"], 2)
        self.assertEqual(m["healed"], 2)
        self.assertEqual(m["facts_reattributed"], 2)
        self.assertEqual(m["dossiers_recompiled"], 2)
        self.assertEqual(self._src_client(r1), "horizons-aba")
        self.assertEqual(self._fact_client(r1), "horizons-aba")
        self.assertEqual(self._src_client(r2), "adelye")

    def test_idempotent_second_run_is_a_noop(self):
        self._mapped("v1", "horizons-aba")
        self._run()
        m2 = self._run()                       # source now attributed -> not a candidate
        self.assertEqual(m2["candidates"], 0)
        self.assertEqual(m2["healed"], 0)

    def test_top_clients_counts_newly_attributed(self):
        self._mapped("v1", "horizons-aba")
        self._mapped("v2", "horizons-aba")
        self._mapped("v3", "adelye")
        m = self._run()
        top = {c["client_id"]: c["looms"] for c in m["top_clients"]}
        self.assertEqual(top["horizons-aba"], 2)
        self.assertEqual(top["adelye"], 1)

    def test_already_attributed_source_is_not_a_candidate(self):
        # event mapped to horizons but source ALREADY attributed -> nothing to do
        self._mapped("v1", "horizons-aba", source_client="horizons-aba")
        m = self._run()
        self.assertEqual(m["candidates"], 0)

    def test_unknown_client_id_skipped_not_crashed(self):
        self._mapped("v1", "ghost-client")     # not in the registry
        m = self._run()
        self.assertEqual(m["skipped_unknown_client"], 1)
        self.assertEqual(m["healed"], 0)

    def test_generic_over_fathom_source_type(self):
        ref = self._mapped("f1", "hermon", source_type="fathom")
        # a loom-only run must NOT touch the fathom row
        self.assertEqual(self._run(source_type="loom")["candidates"], 0)
        self.assertIsNone(self._src_client(ref))
        m = self._run(source_type="fathom")
        self.assertEqual(m["healed"], 1)
        self.assertEqual(self._src_client(ref), "hermon")


# ── the classifier generalised to the Fathom residue (QUEUE-23 / QUEUE-22) ────
class FathomClassifyTests(unittest.TestCase, PinHealOnHigh):
    """The SAME classifier over source_type='fathom' -- the owner-only/internal
    calls the deterministic bridge skipped. Reads the Fathom transcript, KIND-gates,
    attributes by the same vocabulary, and labels the ledger 'fathom'."""

    def setUp(self):
        self.setUpPin(False)          # these assert the LEDGER-ONLY branch
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
        self.tearDownPin()
        self.tmp.cleanup()

    def _fathom(self, rid, title, transcript_text):
        raw = self.raws / f"{rid}.json"
        raw.write_text(json.dumps({
            "recording_id": rid, "title": title, "url": f"https://f/{rid}",
            "date": "2026-04-01T00:00:00Z", "participants": [],
            "transcript": [{"speaker": "Abhijeet", "text": transcript_text}]}))
        with db.connect(self.dbp) as conn:
            self._n += 1
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id, content_type, title, occurred_dt, raw_path, status)"
                " VALUES ('fathom',?,?,NULL,'unknown',?, '2026-04-01T00:00:00Z',?,"
                " 'ingested')", (f"fathom/{rid}", f"h{rid}{self._n}", title,
                                 str(raw)))
        return f"fathom/{rid}"

    def _run(self, responder, **kw):
        ctx = FakeCtx(self.dbp, responder)
        kw.setdefault("fingerprints", [])
        kw.setdefault("dossier_dir", self.dossiers)
        kw.setdefault("source_type", "fathom")
        return loom_classifier.classify_unmapped(ctx, reg=self.reg, **kw), ctx

    def _event(self, rid):
        with db.connect(self.dbp) as conn:
            r = conn.execute(
                "SELECT source, state, client_id, exclude_reason, resolved_by"
                " FROM events WHERE id=?",
                (events.canonical_id("fathom", rid),)).fetchone()
            return dict(r) if r else None

    def test_fathom_transcript_is_read_and_sent_to_the_model(self):
        self._fathom("100", "Intake sync", "we discussed the BCBA licensure board")
        _, ctx = self._run(_kind("internal-product"))
        loom = json.loads(ctx.claude_calls[0]["user_content"])["loom"]
        self.assertIn("BCBA licensure board", loom["transcript"])
        # the fathom system prompt (call, not one-way narration) was used
        self.assertIn("FATHOM CALL", ctx.claude_calls[0]["system"])

    def test_internal_call_excluded_with_fathom_label(self):
        self._fathom("101", "Team standup", "internal planning, no client")
        m, _ = self._run(_kind("internal-product"))
        self.assertEqual(m["excluded"], 1)
        self.assertEqual(m["source_type"], "fathom")
        ev = self._event("101")
        self.assertEqual(ev["source"], "fathom")
        self.assertEqual(ev["exclude_reason"], "fathom-kind:internal-product")

    def test_high_conf_client_work_provisional_maps_fathom(self):
        self._fathom("102", "Cancellation review", "session cancellation automation")
        m, _ = self._run(_kind("client-work", pid="horizons-aba", conf=0.9))
        self.assertEqual(m["provisional_mapped"], 1)
        ev = self._event("102")
        self.assertEqual(ev["state"], "mapped")
        self.assertEqual(ev["client_id"], "horizons-aba")
        self.assertEqual(ev["resolved_by"], "fathom_classifier:high")
        # ledger-only by default: the source stays unattributed until --heal-mapped
        self.assertEqual(m["facts_reattributed"], 0)


if __name__ == "__main__":
    unittest.main()
