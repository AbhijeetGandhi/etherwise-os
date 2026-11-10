"""The board's daily hygiene (his R1/R2 of 2026-06-11, ported 2026-11-10).

v3 never ran the v2 reaper: on 10 Nov the board held 368 open `new` cards, 302
for jobs posted 1-4 months earlier. Pins: R1 expires a `new` card whose job is
past EXPIRY_DAYS; R2 closes the extra `new` cards of a repost and keeps the one
he worked, else the newest; applied / interview cards and jobs with a proposal
are never touched; a short generic title is not a duplicate; the scan runs it
once per IST day and only against the real board.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import db
from modules.upwork import board_hygiene as bh

NOW = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)


def ms(days_ago):
    return int((NOW - timedelta(days=days_ago)).timestamp() * 1000)


def card(cid, name, status="new", days_ago=1, lst="hot"):
    return {"id": cid, "name": name, "status": status, "list": lst,
            "created_ms": ms(days_ago), "url": f"https://app.clickup.com/t/{cid}"}


class TestPlan(unittest.TestCase):
    def test_r1_expires_old_new_cards_only(self):
        cards = [card("a", "Old job", days_ago=40), card("b", "Fresh job", days_ago=3),
                 card("c", "Old but applied", status="applied", days_ago=60),
                 card("d", "Old interview", status="interview", days_ago=60),
                 card("e", "Exactly fourteen days", days_ago=14)]
        acts = {a["id"]: a for a in bh.plan(cards, {}, {}, set(), now=NOW)}
        self.assertEqual(set(acts), {"a"})
        self.assertEqual((acts["a"]["action"], acts["a"]["days"]), ("expire", 40))

    def test_the_posted_date_beats_the_card_date(self):
        cards = [card("a", "Carded late", days_ago=2)]
        posted = {"a": (NOW - timedelta(days=20)).isoformat()}
        self.assertEqual([x["action"] for x in bh.plan(cards, posted, {}, set(), now=NOW)],
                         ["expire"])

    def test_a_job_with_a_proposal_is_never_touched(self):
        cards = [card("a", "Old job he bid on", days_ago=40)]
        self.assertEqual(bh.plan(cards, {}, {"a": "211"}, {"211"}, now=NOW), [])

    def test_r2_keeps_the_worked_card_else_the_newest(self):
        cards = [card("a", "AI Automation Expert Needed to Streamline Ops — score 22", days_ago=5),
                 card("b", "AI Automation Expert Needed to Streamline Ops — score 24 · ⚠ 50+ proposals", days_ago=2),
                 card("c", "Python Workflow Automation Specialist", status="applied", days_ago=9),
                 card("d", "Python Workflow Automation Specialist — score 20", days_ago=1)]
        acts = {a["id"]: a for a in bh.plan(cards, {}, {}, set(), now=NOW)}
        self.assertEqual(set(acts), {"a", "d"})
        self.assertEqual((acts["a"]["action"], acts["a"]["keep"]), ("duplicate", "b"))
        self.assertEqual(acts["d"]["keep"], "c")          # the one he applied to stays

    def test_a_short_generic_title_is_two_clients_not_a_repost(self):
        cards = [card("a", "AI Developer — score 21", days_ago=3),
                 card("b", "AI Developer — score 23", days_ago=1)]
        self.assertEqual(bh.plan(cards, {}, {}, set(), now=NOW), [])

    def test_r3_todays_rules_reach_the_cards_already_on_the_board(self):
        from modules.upwork import crowd, scoring
        cards = [card("h", "HubSpot CRM Setup — score 24"),
                 card("i", "HubSpot CRM Setup for invited client", lst="invites"),
                 card("k", "Crowded generic AI job — score 22"),
                 card("m", "Crowded Airtable rebuild — score 22"),
                 card("p", "HubSpot audit he bid on — score 25"),
                 card("ok", "Make.com scenario repair — score 23")]
        rows = {"h": {"title": "HubSpot CRM Setup", "contract_type": "Fixed", "fixed_budget": 2000.0},
                "i": {"title": "HubSpot CRM Setup", "contract_type": "Fixed", "fixed_budget": 2000.0},
                "k": {"title": "AI agent for our ops team", "contract_type": "Fixed",
                      "fixed_budget": 2000.0, "proposals_now": 180},
                "m": {"title": "Airtable rebuild", "contract_type": "Fixed",
                      "fixed_budget": 2000.0, "proposals_now": 180},
                "p": {"title": "HubSpot audit", "contract_type": "Fixed", "fixed_budget": 2000.0},
                "ok": {"title": "Make.com scenario repair", "contract_type": "Fixed",
                       "fixed_budget": 2000.0}}
        job_of = {k: f"21100000000000{n:05d}" for n, k in enumerate(rows)}
        acts = {a["id"]: a for a in bh.plan(
            cards, {}, job_of, {job_of["p"]}, now=NOW, rows=rows,
            rule_check=scoring.hard_rule_skip,
            crowd_check=lambda r: crowd.decide(r.get("proposals_now"), r) == "skip")}
        self.assertEqual(set(acts), {"h", "k"})
        self.assertEqual((acts["h"]["action"], acts["h"]["reason"]), ("rule", "hubspot_only"))
        self.assertIn("HubSpot in the title", acts["h"]["why"])
        self.assertEqual(acts["k"]["reason"], "crowded")
        self.assertIn("180 proposals", acts["k"]["why"])

    def test_titles_normalise_across_eras(self):
        self.assertEqual(bh.norm_title("[Invite] 🎥 [26] — Airtable Base Rebuild"),
                         "airtable base rebuild")
        self.assertEqual(bh.norm_title("Airtable Base Rebuild — score 26 · under 20 proposals"),
                         "airtable base rebuild")


class FakeClickUp:
    def __init__(self, cards):
        self.cards = cards
        self.calls = []

    def __call__(self, method, path, tok, body=None):
        self.calls.append((method, path, body))
        if method == "GET":
            lst = path.split("/")[2]
            from core import config
            name = {v: k for k, v in config.CLICKUP_LISTS.items()}[lst]
            if "page=0" not in path:
                return {"tasks": []}
            return {"tasks": [{"id": c["id"], "name": c["name"], "url": c["url"],
                               "status": {"status": c["status"]},
                               "date_created": str(c["created_ms"])}
                              for c in self.cards if c["list"] == name]}
        return {}


class TestSweep(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hygiene-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO scored_jobs (id, title, status, clickup_task_id,"
                         " created_dt, card_status) VALUES (?,?,?,?,?,?)",
                         ("2110000000000000001", "Old job", "ClickUp Created", "a",
                          (NOW - timedelta(days=40)).isoformat(), "new"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_live_comments_then_moves_then_records(self):
        fake = FakeClickUp([card("a", "Old job", days_ago=1), card("b", "Fresh job", days_ago=1)])
        m = bh.sweep(self.db_path, "tok", fake, lambda x: None, live=True, now=NOW,
                     sleep=lambda s: None)
        self.assertEqual((m["due"], m["closed"], m["remaining"]), (1, 1, 0))
        writes = [(c[0], c[1]) for c in fake.calls if c[0] != "GET"]
        self.assertEqual(writes, [("POST", "/task/a/comment"), ("PUT", "/task/a")])
        self.assertEqual(fake.calls[-1][2], {"status": "expired"})
        with db.connect(self.db_path) as conn:
            st = conn.execute("SELECT card_status FROM scored_jobs WHERE clickup_task_id='a'").fetchone()[0]
        self.assertEqual(st, "expired")

    def test_dry_run_and_the_per_run_limit(self):
        fake = FakeClickUp([card(f"x{i}", f"Old job number {i}", days_ago=30) for i in range(5)])
        m = bh.sweep(self.db_path, "tok", fake, lambda x: None, live=False, now=NOW)
        self.assertEqual((m["due"], m["closed"]), (5, 0))
        self.assertFalse([c for c in fake.calls if c[0] != "GET"])
        m = bh.sweep(self.db_path, "tok", fake, lambda x: None, live=True, limit=2, now=NOW,
                     sleep=lambda s: None)
        self.assertEqual((m["closed"], m["remaining"]), (2, 3))

    def test_once_per_ist_day(self):
        marker = self.tmp / "board-hygiene-day.txt"
        self.assertTrue(bh.due_today(marker, now=NOW))
        bh.mark_done(marker, now=NOW)
        self.assertFalse(bh.due_today(marker, now=NOW + timedelta(hours=10)))
        self.assertTrue(bh.due_today(marker, now=NOW + timedelta(days=1)))


if __name__ == "__main__":
    unittest.main()
