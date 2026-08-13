"""Mapping queue + approval flow + Fathom heal (Jarvis J1 D3)."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from core import db, registry
from modules.jarvis import approve, events, fathom_bridge


TEMP_REG = """\
clients:
  -
    id: diskover-data
    name: Diskover Data
    fathom_names:
      - diskover
    billing:
      canonical: Diskover Data
  -
    id: acme
    name: Acme Co
    emails:
      - acme.com
    billing:
      canonical: Acme Co
"""


def _seed_event(dbp, eid, source, author, *, state="queued", raw_ref=None,
                proposed=None):
    with db.connect(dbp) as conn:
        conn.execute(
            "INSERT INTO events (id, source, native_id, author, subject, state,"
            " text, raw_ref, proposed_client_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (eid, source, eid.split(":")[-1], author, "Subj", state,
             "body text", raw_ref, proposed))


class RegistryWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reg = Path(self.tmp.name) / "clients.yaml"
        self.reg.write_text(TEMP_REG)

    def tearDown(self):
        self.tmp.cleanup()

    def test_learn_domain(self):
        r = approve.write_registry("acme", "emails", "newdomain.com",
                                   evidence="e", by="t", do_git=False,
                                   path=self.reg)
        self.assertTrue(r["changed"])
        reg = registry.load(self.reg, force=True)
        self.assertEqual(reg.match_email("x@newdomain.com"), "acme")

    def test_overlap_refused(self):
        # acme already owns acme.com; another client cannot claim it
        r = approve.write_registry("diskover-data", "emails", "acme.com",
                                   evidence="e", by="t", do_git=False,
                                   path=self.reg)
        self.assertFalse(r["changed"])
        self.assertIn("claimed-by:acme", r["note"])

    def test_already_known(self):
        r = approve.write_registry("acme", "emails", "acme.com", evidence="e",
                                   by="t", do_git=False, path=self.reg)
        self.assertFalse(r["changed"])
        self.assertEqual(r["note"], "already-known")

    def test_learned_pattern_freemail_vs_company(self):
        self.assertEqual(approve.learned_pattern("a@gmail.com"),
                         ("emails", "a@gmail.com"))
        self.assertEqual(approve.learned_pattern("a@acme.com"),
                         ("emails", "acme.com"))
        self.assertEqual(approve.learned_pattern("Jane Doe"),
                         ("aliases", "Jane Doe"))


class GitCommitTests(unittest.TestCase):
    def test_audited_commit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email",
                            "t@t.io"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name",
                            "T"], check=True)
            yaml = root / "clients.yaml"
            yaml.write_text(TEMP_REG)
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"],
                           check=True)
            r = approve.write_registry("acme", "emails", "learned.com",
                                       evidence="event evt:email:x", by="cockpit",
                                       do_git=True, path=yaml, root=root)
            self.assertTrue(r["changed"])
            self.assertRegex(r["commit"], r"^[0-9a-f]{12}$")
            log = subprocess.run(["git", "-C", str(root), "log", "-1",
                                  "--format=%an%n%s"], check=True,
                                 capture_output=True, text=True).stdout
            self.assertIn("cockpit-approval", log)
            self.assertIn("acme emails += learned.com", log)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = Path(self.tmp.name) / "t.db"
        db.migrate(self.dbp)
        self.reg = Path(self.tmp.name) / "clients.yaml"
        self.reg.write_text(TEMP_REG)

    def tearDown(self):
        self.tmp.cleanup()

    def test_assign_email_maps_and_learns(self):
        _seed_event(self.dbp, "evt:email:e1", "email", "bob@newco.com")
        res = approve.resolve(self.dbp, "evt:email:e1", "assign",
                              client_id="acme", do_git=False,
                              registry_path=self.reg)
        self.assertTrue(res["ok"])
        self.assertEqual(res["registry"]["note"], "learned")
        with db.connect(self.dbp) as conn:
            r = conn.execute("SELECT state, client_id, resolved_by FROM events"
                             " WHERE id=?", ("evt:email:e1",)).fetchone()
        self.assertEqual((r["state"], r["client_id"]), ("mapped", "acme"))
        self.assertEqual(r["resolved_by"], "cockpit-approval")
        # the registry LEARNED newco.com -> future mail auto-maps
        self.assertEqual(registry.load(self.reg, force=True)
                         .match_email("x@newco.com"), "acme")

    def test_ignore(self):
        _seed_event(self.dbp, "evt:email:e2", "email", "spam@bulk.com")
        res = approve.resolve(self.dbp, "evt:email:e2", "ignore",
                              reason="newsletter")
        self.assertTrue(res["ok"])
        with db.connect(self.dbp) as conn:
            r = conn.execute("SELECT state, exclude_reason FROM events WHERE id=?",
                             ("evt:email:e2",)).fetchone()
        self.assertEqual((r["state"], r["exclude_reason"]), ("excluded", "newsletter"))

    def test_internal_uses_one_canonical_reason(self):
        # Abhijeet asked for an "internal call" button. It is its own action,
        # not ignore-with-typed-text, so the reason cannot drift into a dozen
        # spellings — internal calls stay findable if we later want to mine
        # them for agency knowledge rather than treat them as junk.
        _seed_event(self.dbp, "evt:fathom:i1", "fathom", "abhijeet@etherwise.io")
        res = approve.resolve(self.dbp, "evt:fathom:i1", "internal")
        self.assertTrue(res["ok"])
        self.assertEqual(res["exclude_reason"], approve.INTERNAL_REASON)
        with db.connect(self.dbp) as conn:
            r = conn.execute("SELECT state, exclude_reason, resolved_by FROM"
                             " events WHERE id=?", ("evt:fathom:i1",)).fetchone()
        self.assertEqual(r["state"], "excluded")
        self.assertEqual(r["exclude_reason"], "internal:team-call")
        self.assertEqual(r["resolved_by"], "cockpit-approval")

    def test_internal_ignores_any_reason_passed_in(self):
        # the canonical reason is enforced server-side, not supplied by the UI
        _seed_event(self.dbp, "evt:fathom:i2", "fathom", "jay@etherwise.io")
        approve.resolve(self.dbp, "evt:fathom:i2", "internal",
                        reason="whatever the caller felt like typing")
        with db.connect(self.dbp) as conn:
            r = conn.execute("SELECT exclude_reason FROM events WHERE id=?",
                             ("evt:fathom:i2",)).fetchone()
        self.assertEqual(r["exclude_reason"], approve.INTERNAL_REASON)

    def test_internal_does_NOT_touch_the_registry(self):
        # excluding must never write clients.yaml or make a git commit
        _seed_event(self.dbp, "evt:fathom:i3", "fathom", "yogita@etherwise.io")
        res = approve.resolve(self.dbp, "evt:fathom:i3", "internal")
        self.assertNotIn("registry", res)

    def test_cockpit_accepts_internal_as_an_action(self):
        from modules.cockpit import actions as cockpit_actions
        self.assertIn("internal", cockpit_actions._QUEUE_ACTIONS)
        res = cockpit_actions.queue_resolve(self.dbp, {"action": "bogus",
                                                       "event_id": "x"})
        self.assertIn("internal", res["error"])   # listed in the error too

    def test_unknown_client_rejected(self):
        _seed_event(self.dbp, "evt:email:e3", "email", "a@b.com")
        res = approve.resolve(self.dbp, "evt:email:e3", "assign",
                              client_id="nope", do_git=False, registry_path=self.reg)
        self.assertIn("error", res)


class FathomHealTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = Path(self.tmp.name) / "t.db"
        db.migrate(self.dbp)
        self.reg = Path(self.tmp.name) / "clients.yaml"
        self.reg.write_text(TEMP_REG)
        self.dossiers = Path(self.tmp.name) / "dossiers"
        # an unmapped Fathom source + a NULL-client fact for it
        with db.connect(self.dbp) as conn:
            cur = conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id, content_type, density, title, occurred_dt, raw_path,"
                " status) VALUES ('fathom','fathom/R1','h1',NULL,'unknown',0.5,"
                " 'Impromptu call','2026-07-01','/x','ingested')")
            sid = cur.lastrowid
            conn.execute(
                "INSERT INTO facts (source_id, client_id, category, fact_text,"
                " citation, confidence, scope) VALUES (?,NULL,'overview',"
                " 'The client uses Make automation.','fathom/R1 @ 0','CONFIRMED',"
                " 'client')", (sid,))

    def tearDown(self):
        self.tmp.cleanup()

    def test_assign_fathom_heals_dossier(self):
        _seed_event(self.dbp, "evt:fathom:R1", "fathom", "martin@diskover.io",
                    raw_ref="fathom/R1")
        res = approve.resolve(self.dbp, "evt:fathom:R1", "assign",
                              client_id="diskover-data", do_git=False,
                              registry_path=self.reg, dossier_dir=self.dossiers,
                              now_iso="2026-08-06T00:00:00")
        self.assertTrue(res["ok"])
        self.assertEqual(res["heal"]["facts_reattributed"], 1)
        self.assertEqual(res["heal"]["dossier"], "compiled")
        # source + fact now attributed
        with db.connect(self.dbp) as conn:
            s = conn.execute("SELECT client_id FROM sources WHERE source_ref=?",
                             ("fathom/R1",)).fetchone()
            f = conn.execute("SELECT client_id FROM facts").fetchone()
        self.assertEqual(s["client_id"], "diskover-data")
        self.assertEqual(f["client_id"], "diskover-data")
        # the dossier file exists and carries the healed fact
        md = (self.dossiers / "diskover-data.md")
        self.assertTrue(md.exists())
        self.assertIn("Make automation", md.read_text())


class LoomHealTests(unittest.TestCase):
    """Human approval of a QUEUED Loom must heal the KB the same as Fathom does
    (source + facts reattributed, dossier recompiled). Approval is human-
    authoritative, so healing here crosses no model-authority hard line."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = Path(self.tmp.name) / "t.db"
        db.migrate(self.dbp)
        self.reg = Path(self.tmp.name) / "clients.yaml"
        self.reg.write_text(TEMP_REG)
        self.dossiers = Path(self.tmp.name) / "dossiers"
        # an unmapped Loom source + a NULL-client fact for it
        with db.connect(self.dbp) as conn:
            cur = conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id, content_type, density, title, occurred_dt, raw_path,"
                " status) VALUES ('loom','loom/L1','hL1',NULL,'unknown',0.5,"
                " 'Walkthrough of the cancellation flow','2026-07-01','/x',"
                " 'ingested')")
            sid = cur.lastrowid
            conn.execute(
                "INSERT INTO facts (source_id, client_id, category, fact_text,"
                " citation, confidence, scope) VALUES (?,NULL,'overview',"
                " 'The client uses Make automation.','loom/L1 @ 0','CONFIRMED',"
                " 'client')", (sid,))

    def tearDown(self):
        self.tmp.cleanup()

    def test_assign_loom_heals_dossier(self):
        # a queued Loom carries no author (one-way recording) -> nothing to learn,
        # but the assign must still heal source + facts + dossier.
        _seed_event(self.dbp, "evt:loom:L1", "loom", None, raw_ref="loom/L1")
        res = approve.resolve(self.dbp, "evt:loom:L1", "assign",
                              client_id="diskover-data", do_git=False,
                              registry_path=self.reg, dossier_dir=self.dossiers,
                              now_iso="2026-08-06T00:00:00")
        self.assertTrue(res["ok"])
        self.assertIsNotNone(res["heal"])
        self.assertEqual(res["heal"]["facts_reattributed"], 1)
        self.assertEqual(res["heal"]["dossier"], "compiled")
        with db.connect(self.dbp) as conn:
            s = conn.execute("SELECT client_id FROM sources WHERE source_ref=?",
                             ("loom/L1",)).fetchone()
            f = conn.execute("SELECT client_id FROM facts").fetchone()
        self.assertEqual(s["client_id"], "diskover-data")
        self.assertEqual(f["client_id"], "diskover-data")
        md = (self.dossiers / "diskover-data.md")
        self.assertTrue(md.exists())
        self.assertIn("Make automation", md.read_text())


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = Path(self.tmp.name) / "t.db"
        db.migrate(self.dbp)
        self.reg = registry.load(
            self._write(TEMP_REG), force=True)
        self.dossiers = Path(self.tmp.name) / "dossiers"
        self.parts = {}
        for ref, title in [("fathom/A", "call a"), ("fathom/B", "call b"),
                           ("fathom/C", "call c")]:
            with db.connect(self.dbp) as conn:
                conn.execute(
                    "INSERT INTO sources (source_type, source_ref, content_hash,"
                    " client_id, content_type, density, title, occurred_dt,"
                    " raw_path, status) VALUES ('fathom',?,?,NULL,'unknown',0.5,?,"
                    " '2026-07-01',?, 'ingested')", (ref, "h" + ref, title, ref))
        # A -> matches registry (auto-heal); B -> external unmatched (queued);
        # C -> only internal participant (skipped)
        self.parts = {
            "/x": ([], None),
            "fathom/A": (["diskover team"], "u"),      # 'diskover' fathom_name
            "fathom/B": (["stranger@bigco.com"], "u"),
            "fathom/C": (["abhijeet@etherwise.io"], "u"),
        }

    def _write(self, text):
        p = Path(tempfile.mkdtemp()) / "clients.yaml"
        p.write_text(text)
        return p

    def tearDown(self):
        self.tmp.cleanup()

    def _read(self, raw_path):
        return self.parts.get(raw_path, ([], None))

    def test_external_signal_skips_owner(self):
        # our own people are not a client signal
        self.assertIsNone(fathom_bridge._external_signal(
            ["abhijeetgandhi02@gmail.com", "abhijeet@etherwise.io"]))
        self.assertEqual(fathom_bridge._external_signal(
            ["abhijeetgandhi02@gmail.com", "bob@bigco.com"]), "bob@bigco.com")

    def test_recheck_excludes_owner_only_call(self):
        with db.connect(self.dbp) as conn:
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id, content_type, density, title, occurred_dt, raw_path,"
                " status) VALUES ('fathom','fathom/OW','hOW',NULL,'unknown',0.5,"
                " 'Impromptu','2026-07-01','fathom/OW','ingested')")
            conn.execute(
                "INSERT INTO events (id, source, native_id, author, subject, state,"
                " raw_ref) VALUES ('evt:fathom:OW','fathom','OW',"
                " 'abhijeetgandhi02@gmail.com','Impromptu','queued','fathom/OW')")
        res = fathom_bridge.recheck_queued(
            self.dbp, self.reg,
            read_participants=lambda p: (["abhijeetgandhi02@gmail.com"], None),
            dossier_dir=self.dossiers)
        self.assertEqual(res["excluded"], 1)
        with db.connect(self.dbp) as conn:
            r = conn.execute("SELECT state, exclude_reason FROM events WHERE"
                             " id='evt:fathom:OW'").fetchone()
        self.assertEqual((r["state"], r["exclude_reason"]),
                         ("excluded", "internal:owner-only"))

    def _mk(self, ref, title, state, reason=None):
        with db.connect(self.dbp) as conn:
            conn.execute(
                "INSERT INTO sources (source_type, source_ref, content_hash,"
                " client_id, content_type, density, title, occurred_dt, raw_path,"
                " status) VALUES ('fathom',?,?,NULL,'unknown',0.5,?,'2026-07-01',"
                "?,'ingested')", (ref, "h" + ref, title, ref))
            conn.execute(
                "INSERT INTO events (id, source, native_id, author, subject,"
                " state, exclude_reason, raw_ref) VALUES (?,'fathom',?,NULL,?,?,?,?)",
                (f"evt:fathom:{ref}", ref, title, state, reason, ref))

    def test_recheck_all_unfreezes_a_wrong_exclusion(self):
        """The unfreeze (2026-08-14). An `excluded` verdict is only as good as the
        evidence it was reached on, and 189 Fathom exclusions — 114 of them
        `fathom-kind:internal-product` — were decided by a model handed a
        participant list that the ingest bug had emptied of every speaker.

        `--recheck` alone can never reach them: it looks at state='queued'.
        With real speakers restored, a registry match must HEAL the source; a
        stranger must go to the QUEUE for a human, never be auto-attributed."""
        self._mk("fathom/X", "call x", "excluded", "fathom-kind:internal-product")
        self._mk("fathom/Y", "call y", "excluded", "fathom:unattributable")
        self._mk("fathom/Z", "call z", "excluded", "fathom-kind:internal-product")
        self.parts.update({
            "fathom/X": (["diskover team"], "u"),        # registry match -> heal
            "fathom/Y": (["stranger@bigco.com"], "u"),   # external -> queue
            "fathom/Z": (["Abhijeet Gandhi"], "u"),      # only us -> stays internal
        })
        # the daily recheck does NOT touch them
        quiet = fathom_bridge.recheck_queued(
            self.dbp, self.reg, read_participants=self._read,
            dossier_dir=self.dossiers)
        self.assertEqual(quiet["rechecked"], 0)

        res = fathom_bridge.recheck_events(
            self.dbp, self.reg, states=("queued", "excluded"),
            read_participants=self._read, dossier_dir=self.dossiers,
            now_iso="2026-08-14T00:00:00")
        self.assertEqual(res["healed"], 1)
        self.assertEqual(res["requeued"], 1)
        self.assertEqual(res["excluded"], 1)
        self.assertEqual(res["healed_from"],
                         {"fathom-kind:internal-product": 1})
        with db.connect(self.dbp) as conn:
            rows = {r["raw_ref"]: (r["state"], r["client_id"], r["author"])
                    for r in conn.execute(
                        "SELECT raw_ref, state, client_id, author FROM events")}
            src = {r["source_ref"]: r["client_id"] for r in conn.execute(
                "SELECT source_ref, client_id FROM sources")}
        self.assertEqual(rows["fathom/X"][:2], ("mapped", "diskover-data"))
        self.assertEqual(src["fathom/X"], "diskover-data")     # source healed too
        self.assertEqual(rows["fathom/Y"], ("queued", None, "stranger@bigco.com"))
        self.assertIsNone(src["fathom/Y"])                     # NEVER auto-attributed
        self.assertEqual(rows["fathom/Z"][0], "excluded")
        self.assertIsNone(src["fathom/Z"])

    def test_recheck_all_dry_run_writes_nothing(self):
        self._mk("fathom/X", "call x", "excluded", "fathom-kind:internal-product")
        self.parts["fathom/X"] = (["diskover team"], "u")
        res = fathom_bridge.recheck_events(
            self.dbp, self.reg, states=("queued", "excluded"),
            read_participants=self._read, dossier_dir=self.dossiers, dry_run=True)
        self.assertEqual(res["healed"], 1)
        with db.connect(self.dbp) as conn:
            r = conn.execute("SELECT state FROM events WHERE"
                             " id='evt:fathom:fathom/X'").fetchone()
            s = conn.execute("SELECT client_id FROM sources WHERE"
                             " source_ref='fathom/X'").fetchone()
        self.assertEqual(r["state"], "excluded")
        self.assertIsNone(s["client_id"])

    def test_recheck_all_queue_cap_never_floods_the_queue(self):
        """A mapping queue of 150 is a wall, not a queue (the IMAP rail hit 94 and
        became unusable). Rows over the cap keep their state and are COUNTED."""
        for i in range(3):
            self._mk(f"fathom/S{i}", f"s{i}", "excluded", "fathom:unattributable")
            self.parts[f"fathom/S{i}"] = ([f"s{i}@bigco.com"], "u")
        res = fathom_bridge.recheck_events(
            self.dbp, self.reg, states=("excluded",), queue_cap=2,
            read_participants=self._read, dossier_dir=self.dossiers)
        self.assertEqual(res["requeued"], 2)
        self.assertEqual(res["queue_capped"], 1)
        with db.connect(self.dbp) as conn:
            n = conn.execute("SELECT COUNT(*) FROM events WHERE"
                             " state='queued'").fetchone()[0]
        self.assertEqual(n, 2)

    def test_external_signal_falls_back_to_an_unknown_NAME(self):
        """A stranger who only ever appears as a transcript speaker IS external
        signal. Before 2026-08-14 this looked at addresses only, so such a call
        produced None and was filed as internal — indistinguishable from a call
        where the only other party really was one of us."""
        self.assertEqual(fathom_bridge._external_signal(
            ["Abhijeet Gandhi", "Michael Gandia"]), "Michael Gandia")
        # an ADDRESS still wins over a name: it carries a domain to learn from
        self.assertEqual(fathom_bridge._external_signal(
            ["Michael Gandia", "mg@cmf.com"]), "mg@cmf.com")
        # our own contractor by NAME is not a client signal
        self.assertIsNone(fathom_bridge._external_signal(
            ["Abhijeet Gandhi", "Alphons Jaimon"]))

    def test_sync_classifies(self):
        res = fathom_bridge.sync_unmapped(
            self.dbp, self.reg, read_participants=self._read,
            dossier_dir=self.dossiers, now_iso="2026-08-06T00:00:00")
        self.assertEqual(res["auto_healed"], 1)   # A
        self.assertEqual(res["queued"], 1)         # B
        self.assertEqual(res["skipped"], 1)        # C (internal only)
        with db.connect(self.dbp) as conn:
            q = conn.execute("SELECT author FROM events WHERE state='queued'"
                             " AND source='fathom'").fetchall()
            healed = conn.execute("SELECT client_id FROM sources WHERE"
                                  " source_ref='fathom/A'").fetchone()
        self.assertEqual([r["author"] for r in q], ["stranger@bigco.com"])
        self.assertEqual(healed["client_id"], "diskover-data")


# ── the thread write paths (Review's third backlog) ──────────────────────────
# An Upwork thread has no `events` row, so resolve() cannot reach it. These two
# are the whole of its write surface, and both are PERMANENT.

THREAD_REG = TEMP_REG + """\
  -
    id: zeta
    name: Zeta Ltd
    upwork_rooms:
      - room_taken
"""


class ThreadWriteCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email",
                        "t@t.io"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "T"],
                       check=True)
        self.reg = self.root / "clients.yaml"
        self.reg.write_text(THREAD_REG)
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "init"],
                       check=True)
        self.dbp = self.root / "t.db"
        db.migrate(self.dbp)
        with db.connect(self.dbp) as conn:
            conn.execute("INSERT INTO threads (id, room_name) VALUES"
                         " ('room_a','Rupali Singh')")
            conn.execute("INSERT INTO threads (id, room_name, client_id) VALUES"
                         " ('room_b','Already','acme')")
            conn.execute("INSERT INTO threads (id, room_name) VALUES"
                         " ('room_taken','Zeta room')")

    def tearDown(self):
        self.tmp.cleanup()

    def log(self):
        return subprocess.run(["git", "-C", str(self.root), "log",
                               "--format=%an|%s"], check=True,
                              capture_output=True, text=True).stdout


class AssignThreadTests(ThreadWriteCase):
    def test_claims_the_room_and_links_the_thread(self):
        out = approve.assign_thread(self.dbp, "room_a", "acme", evidence="22 msgs",
                                    registry_path=self.reg, root=self.root)
        self.assertTrue(out["ok"])
        self.assertEqual(out["linked"], 1)
        self.assertRegex(out["registry"]["commit"], r"^[0-9a-f]{12}$")
        reg = registry.load(self.reg, force=True)
        # the claim link_threads consults FIRST — deterministic, hand-approved
        self.assertEqual(reg.match_upwork_room("room_a"), "acme")
        with db.connect(self.dbp) as conn:
            self.assertEqual(conn.execute(
                "SELECT client_id FROM threads WHERE id='room_a'"
            ).fetchone()["client_id"], "acme")
        self.assertIn("cockpit-approval", self.log())
        self.assertIn("acme upwork_rooms += room_a", self.log())

    def test_it_claims_the_room_not_the_name(self):
        """A one-word Upwork display name as an ALIAS is how 'rick k' once
        claimed 'Patrick Kelly'. Only the room id is learned."""
        approve.assign_thread(self.dbp, "room_a", "acme", evidence="e",
                              registry_path=self.reg, root=self.root)
        reg = registry.load(self.reg, force=True)
        self.assertIsNone(reg.match_name("Rupali Singh"))
        self.assertEqual(reg.by_id["acme"].get("aliases"), [])

    def test_never_overwrites_an_existing_attribution(self):
        out = approve.assign_thread(self.dbp, "room_b", "diskover-data",
                                    evidence="e", registry_path=self.reg,
                                    root=self.root)
        self.assertIn("already attributed", out["error"])
        self.assertNotIn("upwork_rooms", self.log())

    def test_a_room_another_client_owns_is_refused_before_the_db_write(self):
        """The uniqueness index for upwork_rooms is _room_to_id, not the alias
        index. Consulting the wrong one let a second client claim a room, which
        is a registry LOAD ERROR — every reader in the system, not just this
        one."""
        out = approve.assign_thread(self.dbp, "room_taken", "acme", evidence="e",
                                    registry_path=self.reg, root=self.root)
        self.assertIn("already claimed", out["error"])
        with db.connect(self.dbp) as conn:
            self.assertIsNone(conn.execute(
                "SELECT client_id FROM threads WHERE id='room_taken'"
            ).fetchone()["client_id"])
        registry.load(self.reg, force=True)      # still loads

    def test_unknown_thread_and_unknown_client_are_refused(self):
        self.assertIn("unknown client_id", approve.assign_thread(
            self.dbp, "room_a", "nope", evidence="e",
            registry_path=self.reg, root=self.root)["error"])
        self.assertIn("unknown thread", approve.assign_thread(
            self.dbp, "room_zzz", "acme", evidence="e",
            registry_path=self.reg, root=self.root)["error"])

    def test_write_registry_recognises_a_room_this_client_already_has(self):
        approve.assign_thread(self.dbp, "room_a", "acme", evidence="e",
                              registry_path=self.reg, root=self.root)
        r = approve.write_registry("acme", "upwork_rooms", "room_a",
                                   evidence="e", by="t", do_git=False,
                                   path=self.reg)
        self.assertFalse(r["changed"])
        self.assertEqual(r["note"], "already-known")


class CreateClientTests(ThreadWriteCase):
    def test_creates_a_valid_entry_and_commits_it(self):
        out = approve.create_client("rupali-singh", "Rupali Singh",
                                    kind="prospect", upwork_room="room_a",
                                    evidence="upwork room room_a; 22 messages",
                                    path=self.reg, root=self.root)
        self.assertTrue(out["ok"])
        self.assertRegex(out["commit"], r"^[0-9a-f]{12}$")
        reg = registry.load(self.reg, force=True)
        self.assertEqual(reg.kind("rupali-singh"), "prospect")
        self.assertEqual(reg.match_upwork_room("room_a"), "rupali-singh")
        # a prospect is matchable but NOT on the economics roster, so a mistaken
        # create cannot move revenue figures
        self.assertIn("rupali-singh", reg.matchable_ids())
        self.assertNotIn("rupali-singh", reg.client_ids())
        # the subject line is the contract cockpit Activity parses out of git log
        self.assertIn("registry: rupali-singh new_client += Rupali Singh",
                      self.log())
        # inserted in id ORDER rather than appended blindly — a new entry at
        # the bottom of a sorted file is a diff nobody can read. (The fixture is
        # only partly sorted, so the rule is 'before the first larger id'.)
        ids = [c["id"] for c in reg.clients]
        self.assertEqual(ids[ids.index("rupali-singh") + 1], "zeta")

    def test_every_collision_the_loader_would_refuse_is_refused_here(self):
        for kw, needle in (
                (dict(client_id="acme", name="Other"), "already exists"),
                (dict(client_id="acme2", name="Acme Co"), "already claimed by"),
                (dict(client_id="new1", name="New", upwork_room="room_taken"),
                 "already claimed"),
                (dict(client_id="Bad Slug", name="X"), "must be a slug"),
                (dict(client_id="ok-id", name="  "), "name required"),
                (dict(client_id="ok-id", name="X", kind="vendor"),
                 "kind must be")):
            out = approve.create_client(evidence="e", path=self.reg,
                                        root=self.root, **kw)
            self.assertIn(needle, out["error"], kw)
        self.assertEqual(self.log().count("new_client"), 0)
        registry.load(self.reg, force=True)      # untouched and still valid

    def test_multiline_evidence_cannot_break_the_file(self):
        """registry._lines() is line-based: a newline inside a quoted scalar
        does not round-trip, and clients.yaml is the spine — an unreadable one
        takes every reader down at once."""
        out = approve.create_client("multi-line", "Multi Line",
                                    upwork_room="room_a",
                                    evidence="line one\nline two\ttabbed",
                                    path=self.reg, root=self.root)
        self.assertTrue(out["ok"], out)
        reg = registry.load(self.reg, force=True)
        self.assertEqual(reg.by_id["multi-line"]["scope_notes"],
                         "line one line two tabbed")

    def test_a_write_that_would_not_reload_is_rolled_back(self):
        before = self.reg.read_text()
        with mock.patch.object(approve.registry, "dumps",
                               return_value="clients:\n  - not a mapping\n"):
            out = approve.create_client("x-client", "X Client", evidence="e",
                                        path=self.reg, root=self.root)
        self.assertIn("rolled back", out["error"])
        self.assertEqual(self.reg.read_text(), before)
        self.assertEqual(self.log().count("new_client"), 0)
        registry.load(self.reg, force=True)


if __name__ == "__main__":
    unittest.main()
