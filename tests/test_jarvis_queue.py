"""Mapping queue + approval flow + Fathom heal (Jarvis J1 D3)."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
