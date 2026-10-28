"""The other six tabs in the Upwork tab's style (2026-08-26 overnight).

Abhijeet, 05:20: "I like the way you built UI for the upwork tab or at least
the direction you headed. I want UI of all the tabs improved now in the same
way." Pins what the redesign added on the server (Today's whole ranked queue
for the grouped view, the promise provenance on a commitment card, the rerun
allowlist + words on /api/system) and that every section still parses as an
ES module, imports only from the house, and never sends anything.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db
from modules.cockpit import actions, data
from tests.test_cockpit import seed_today

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "modules" / "cockpit" / "static"
SECTIONS = ("today", "money", "clients", "knowledge", "activity", "review", "upwork", "lifecycle",
            "linkedin")


def _node():
    return shutil.which("node")


class TestServerAdditions(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="redesign-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed_today(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_today_carries_the_whole_ranked_queue_for_the_groups(self):
        with mock.patch.object(config, "DB_PATH", self.db_path):
            out = data.today(self.db_path)
        self.assertIn("queue_all", out)
        self.assertEqual(len(out["queue_all"]), out["queue_total"])
        self.assertEqual(out["queue_all"][:len(out["queue"])], out["queue"])   # same order, the top-N first
        for e in out["queue_all"]:
            self.assertIn("item_key", e)

    def test_a_commitment_card_says_where_and_when_it_was_promised(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO sources (source_type, source_ref, content_hash, client_id,"
                         " title, occurred_dt) VALUES ('fathom', 'fathom/9001', 'h9001', 'acme',"
                         " 'Weekly with Acme', '2026-08-20T10:00:00+00:00')")
            conn.execute("INSERT INTO commitments (who, client_id, text, due, source_cite, dedup_key)"
                         " VALUES ('us', 'acme', 'Send the register', '2026-08-27',"
                         " 'fathom/9001 @ I will send the register', 'commitment:t1')")
        from modules.chief_of_staff import nudges, rules
        for c in rules.rule_commitments_due(self.db_path):
            nudges.write_nudge(self.db_path, **c)
        with mock.patch.object(config, "DB_PATH", self.db_path):
            out = data.today(self.db_path)
        card = next(e for e in out["queue_all"] if str(e.get("item_key", "")).startswith("nudge:commitment:"))
        d = card["detail"]
        self.assertEqual(d["source_kind"], "fathom")
        self.assertEqual(d["promised_on"], "2026-08-20")
        self.assertEqual(d["source_title"], "Weekly with Acme")

    def test_system_names_what_the_cockpit_may_run_and_in_words(self):
        with mock.patch.object(config, "DB_PATH", self.db_path):
            out = data.system(self.db_path)
        self.assertEqual(out["rerun_jobs"], sorted(actions.RERUN_JOBS))
        for task in ("upwork_scan", "kb_daily", "cos_sweep", "finance_payment_mail"):
            self.assertIn(task, out["task_words"])
            self.assertNotIn("_", out["task_words"][task])       # words, not a task name


class TestSectionsStillHouseBuilt(unittest.TestCase):
    def test_every_section_and_the_subtab_helper_parse_as_es_modules(self):
        node = _node()
        if not node:
            self.skipTest("node not installed")
        for rel in [f"js/sections/{s}.js" for s in SECTIONS] + ["js/subtabs.js", "js/conversation.js"]:
            with tempfile.TemporaryDirectory() as td:
                mjs = Path(td) / "check.mjs"
                mjs.write_text((STATIC / rel).read_text())
                r = subprocess.run([node, "--check", str(mjs)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{rel}: {r.stderr[:400]}")

    def test_sections_import_only_from_the_house_and_never_send(self):
        for s in SECTIONS:
            src = (STATIC / "js" / "sections" / f"{s}.js").read_text()
            for m in re.findall(r'^import .* from "([^"]+)";', src, flags=re.M):
                self.assertTrue(m.startswith("../") or m.startswith("./"), f"{s}: {m}")
            self.assertNotIn("send_message", src)
            self.assertNotIn("/api/send", src)

    def test_the_subtab_bar_remembers_the_view_and_keeps_the_hash_honest(self):
        src = (STATIC / "js" / "subtabs.js").read_text()
        self.assertIn("localStorage", src)
        self.assertIn("history.replaceState", src)
        self.assertIn('aria-selected', src)
        # every redesigned section uses it
        for s in ("money", "clients", "knowledge", "activity", "review"):
            self.assertIn('from "../subtabs.js"', (STATIC / "js" / "sections" / f"{s}.js").read_text(), s)

    def test_today_groups_by_what_a_card_asks(self):
        src = (STATIC / "js" / "sections" / "today.js").read_text()
        for title in ("People waiting on you", "Things you promised", "Needs a decision", "Housekeeping"):
            self.assertIn(title, src)
        self.assertIn("queue_all", src)

    def test_no_statistics_vocabulary_in_the_new_words(self):
        """The Upwork tab's copy rule, applied to the helper this redesign added."""
        src = (STATIC / "js" / "subtabs.js").read_text().lower()
        for w in ("baseline", "anecdote", "cohort", "n ≥", "pooled"):
            self.assertNotIn(w, src, w)
        today = (STATIC / "js" / "sections" / "today.js").read_text()
        self.assertIn("late", today)                    # the health line says so in words
        self.assertNotIn("u=", today.split("function renderMore")[0])   # no urgency numbers on the cards

if __name__ == "__main__":
    unittest.main()
