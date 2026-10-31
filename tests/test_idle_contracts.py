"""Contracts Upwork still calls ACTIVE that nobody has touched.

Upwork never closes a contract on its own. On 2026-08-27, 17 of his 35 open
contracts had no message, no money and no logged hour for 90 days — the oldest
quiet since 2023 — and every "how much work is committed" reading counted all
35. These pin the three signs of life, the one-card-not-seventeen shape, and
the floor below which it is not worth a card.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from core import config, db
from modules.chief_of_staff import rules
from modules.upwork.feeds import mcp_contracts as mc

NOW = datetime(2026, 8, 27, 1, 0, tzinfo=config.TZ)


def days_ago(n: int) -> str:
    return (NOW.date() - timedelta(days=n)).isoformat()


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="idle-contracts-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def contract(self, cid, who, start, status="ACTIVE"):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO contracts (id, title, upwork_status,"
                         " client_company, start_dt) VALUES (?,?,?,?,?)",
                         (cid, f"work for {who}", status, who, start))

    def idle(self, **kw):
        with db.connect(self.db_path) as conn:
            return mc.idle(conn, now=NOW, **kw)


class TestIdle(Case):
    def test_a_contract_with_no_sign_of_life_is_idle_by_its_start_date(self):
        self.contract("c1", "Orama Solutions LLP", days_ago(600))
        got = self.idle()
        self.assertEqual([r["id"] for r in got], ["c1"])
        self.assertEqual(got[0]["quiet_since"], days_ago(600))
        self.assertIsNone(got[0]["last_life"])

    def test_each_of_the_three_signals_keeps_a_contract_off_the_list(self):
        for i, who in enumerate(("msg", "money", "hours"), start=1):
            self.contract(f"c{i}", who, days_ago(400))
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, contract_id, latest_message_dt)"
                         " VALUES ('r1','c1',?)", (days_ago(3) + "T10:00:00Z",))
            conn.execute("INSERT INTO transactions (record_id, dedup_key,"
                         " assignment_company, creation_dt) VALUES"
                         " ('t1','t1','money',?)", (days_ago(10),))
            conn.execute("INSERT INTO upwork_timesheets (cursor_id, contract_id,"
                         " date_worked, hours, charges_minor) VALUES ('ts1','c3',?,1.0,100)",
                         (days_ago(20),))
        self.assertEqual(self.idle(), [])

    def test_a_signal_older_than_the_window_does_not_save_it(self):
        self.contract("c1", "old money", days_ago(500))
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO transactions (record_id, dedup_key,"
                         " assignment_company, creation_dt) VALUES"
                         " ('t1','t1','old money',?)", (days_ago(200),))
        got = self.idle()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["last_life"], days_ago(200))    # the newest sign
        self.assertEqual(got[0]["quiet_since"], days_ago(200))
        self.assertEqual(self.idle(days=365), [])               # a wider window keeps it

    def test_a_closed_contract_is_never_on_the_list(self):
        self.contract("c1", "done", days_ago(500), status="CLOSED")
        self.contract("c2", "paused", days_ago(500), status="PAUSED")
        self.assertEqual(self.idle(), [])

    def test_the_oldest_quiet_comes_first(self):
        self.contract("c1", "newer", days_ago(120))
        self.contract("c2", "oldest", days_ago(900))
        self.assertEqual([r["id"] for r in self.idle()], ["c2", "c1"])


class TestCard(Case):
    def test_one_card_for_the_pile_never_one_per_contract(self):
        for i in range(17):
            self.contract(f"c{i}", f"client {i}", days_ago(200 + i))
        cards = rules.rule_idle_contracts(self.db_path, now=NOW)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["dedup_key"], "idle_contracts")
        self.assertEqual(cards[0]["title"],
                         "17 Upwork contracts are open with nothing on them")
        self.assertEqual(len(cards[0]["payload"]["contracts"]), 17)
        self.assertIn("client 16", cards[0]["action"])           # the oldest is named
        self.assertIn("end them on Upwork", cards[0]["action"])
        self.assertIsNone(cards[0]["due"])                       # not a deadline

    def test_below_the_floor_there_is_no_card(self):
        for i in range(config.IDLE_CONTRACTS_CARD_MIN - 1):
            self.contract(f"c{i}", f"client {i}", days_ago(400))
        self.assertEqual(rules.rule_idle_contracts(self.db_path, now=NOW), [])

    def test_the_rule_is_off_the_board_and_survives_a_db_without_the_tables(self):
        # 2026-11-01 (the cockpit audit): the pile of idle contracts already
        # lives on Clients › Needs a look; the Today card was the same fact on a
        # third screen. The rule stays callable (the Clients view and this test
        # use it) but no longer mints cards.
        self.assertNotIn(rules.rule_idle_contracts, rules.ALL_RULES)
        self.assertNotIn(rules.rule_stale_dossier, rules.ALL_RULES)
        self.assertEqual(rules.rule_idle_contracts(self.tmp / "nope.db"), [])
        self.assertEqual((config.MCP_CONTRACT_IDLE_DAYS,
                          config.IDLE_CONTRACTS_CARD_MIN), (90, 3))


if __name__ == "__main__":
    unittest.main()
