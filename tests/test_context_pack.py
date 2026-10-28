"""The context pack's honesty invariants.

The pack is what every agent chat reads before it says anything, so its failure
mode is not a crash — it is a CONFIDENT WRONG BRIEFING. These tests pin the
three ways it is allowed to be wrong: it may say "blocked", it may say "no data
surface", it may say "parse failed". It may never quietly render an empty
section as if that were the state of the business.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from modules.context import pack


class StrategyParsing(unittest.TestCase):
    def _with_strategy(self, text):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "business").mkdir()
        (tmp / "business" / "STRATEGY.md").write_text(text)
        return mock.patch.object(pack.config, "WORKSPACE_ROOT", tmp)

    def test_parses_bets_and_status(self):
        with self._with_strategy(
                "## The bets\n"
                "1. **B1 — Work the Upwork machine.** blah blah\n"
                "   **Status: ACTIVE** (resumed)\n"
                "2. **B2 — LinkedIn authority (ghostwriter).** words\n"
                "   **Status: DEFERRED**\n"
                "- 2026-08-06 · a decision\n- 2026-08-09 · another\n"):
            s = pack._strategy_block()
        self.assertTrue(s["available"])
        self.assertEqual([b["id"] for b in s["bets"]], ["B1", "B2"])
        self.assertEqual(s["bets"][0]["status"], "ACTIVE")
        self.assertEqual(s["bets"][1]["status"], "DEFERRED")
        self.assertEqual(s["decision_log_entries"], 2)
        self.assertIsNone(s["parse_warning"])

    def test_format_change_warns_instead_of_returning_nothing(self):
        """A silent empty bet list would let an agent advise as though the
        business had no active strategy — the worst available failure."""
        with self._with_strategy("we changed the format entirely\n"):
            s = pack._strategy_block()
        self.assertEqual(s["bets"], [])
        self.assertIn("format may have changed", s["parse_warning"])

    def test_missing_file_is_reported_not_swallowed(self):
        with mock.patch.object(pack.config, "WORKSPACE_ROOT",
                               Path(tempfile.mkdtemp())):
            s = pack._strategy_block()
        self.assertFalse(s["available"])
        self.assertIn("STRATEGY.md", s["note"])


class HonestyRules(unittest.TestCase):
    def test_growth_declares_it_has_no_surface(self):
        """The empty slice IS the gate on creating a growth agent; if this ever
        starts returning invented metrics, the rule has been broken. Since
        2026-10-28 the surface is the LinkedIn copy — so 'no surface' is what
        it must say whenever that copy does not exist (or cannot be read)."""
        with mock.patch("modules.cockpit.linkedin.summary",
                        return_value={"has_data_surface": False}):
            g = pack._slice_growth()
        self.assertFalse(g["has_data_surface"])
        self.assertIn("build the surface", g["note"])
        with mock.patch("modules.cockpit.linkedin.summary",
                        side_effect=RuntimeError("no such table: li_records")):
            self.assertFalse(pack._slice_growth()["has_data_surface"])

    def test_growth_carries_linkedin_from_the_tabs_own_reader(self):
        s = {"has_data_surface": True, "funnel": {"commented": 8}}
        with mock.patch("modules.cockpit.linkedin.summary", return_value=s):
            g = pack._slice_growth()
        self.assertTrue(g["has_data_surface"])
        self.assertIs(g["channels"]["linkedin"], s)

    def test_money_block_withholds_figures_when_doctor_is_red(self):
        with mock.patch("modules.cockpit.data.money",
                        return_value={"blocked": True, "reds": ["#7 cash"]}):
            m = pack._money_block()
        self.assertFalse(m["available"])
        self.assertNotIn("month_usd", m)
        self.assertEqual(m["reds"], ["#7 cash"])

    def test_unknown_agent_names_the_valid_ones(self):
        with mock.patch.multiple(
                pack, _money_block=lambda: {"available": False, "note": "x"},
                _clients_block=lambda: {"available": False, "note": "x"},
                _pipeline_block=lambda: {}, _health_block=lambda: {},
                _strategy_block=lambda: {"available": False, "note": "x"}):
            p = pack.build("marketing")
        self.assertIn("unknown agent", p["slice"]["error"])
        for a in pack.AGENTS:
            self.assertIn(a, p["slice"]["error"])


class Rendering(unittest.TestCase):
    def test_renders_a_blocked_pack_without_crashing(self):
        doc = {"generated_at": "now", "agent": "core",
               "money": {"available": False, "note": "finance RED",
                         "reds": ["#7"]},
               "clients": {"available": False, "note": "blocked"},
               "pipeline": {}, "health": {}, "strategy": {"available": False,
                                                          "note": "no file"}}
        out = pack.render_md(doc)
        self.assertIn("finance RED", out)
        self.assertIn("no file", out)

    def test_slice_values_are_truncated_so_the_pack_stays_readable(self):
        """It is read at the top of every session — it competes with the work
        for attention. A slice that dumps a 40kB blob defeats its purpose."""
        doc = {"generated_at": "now", "agent": "upwork",
               "money": {"available": False, "note": "x"},
               "clients": {"available": False, "note": "x"},
               "pipeline": {}, "health": {},
               "strategy": {"available": False, "note": "x"},
               "slice": {"big": [{"k": "v" * 50} for _ in range(50)]}}
        line = [l for l in pack.render_md(doc).splitlines()
                if l.startswith("- **big**")][0]
        self.assertLess(len(line), 500)
        self.assertTrue(line.endswith("…"))


if __name__ == "__main__":
    unittest.main()
