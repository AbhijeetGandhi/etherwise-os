"""Hours — the rules that decide what a client gets billed.

Built 2026-10-19. Every test here pins something that was WRONG at some point
during the build, against live ClickUp data:

  * the month-chunked sync lost an entry that started 23:52 on the last day of
    September and ended after midnight — ClickUp wants an entry inside the
    window, so it belonged to neither chunk. Hermon Sprint 22 came to 177.06h
    instead of 178.31h, and the only clue was one missing row.
  * sprint-name years were inferred from a per-space config anchor, and Hermon's
    folder opens in December 2025, so Sprint 22 resolved to 2027 and reported
    0.0h against a period with 178 hours in it.
  * a chunk that returns exactly 50 rows is a ceiling, not a count.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from core import config, db
from modules.hours import periods as per
from modules.hours import report as R
from modules.hours import sync as S

MS_H = 3_600_000


def ms(day, hh=12, mm=0):
    import datetime as dt
    return int(dt.datetime(*[int(x) for x in day.split("-")], hh, mm,
                           tzinfo=config.TZ).timestamp() * 1000)


class PeriodWindows(unittest.TestCase):
    def test_window_parses_out_of_the_list_name(self):
        self.assertEqual(per.parse_window("Horizons 44 (10/5 - 10/18)"),
                         ((10, 5), (10, 18)))
        self.assertEqual(per.parse_window("Sprint 22 (9/28 - 10/11)"),
                         ((9, 28), (10, 11)))
        self.assertEqual(per.parse_window("8 (10/5 – 10/18)"),   # en-dash
                         ((10, 5), (10, 18)))

    def test_an_unparsable_name_is_an_error_not_a_skip(self):
        """A period that silently disappears is a period that silently stops
        being billed."""
        out = per.infer_years(["Backlog"], 2026)[0]
        self.assertEqual(out["parse_ok"], 0)
        self.assertIn("window", out["parse_note"])

    def test_entries_date_the_list_not_a_config_anchor(self):
        """Hermon's folder opens with "Sprint 2 (12/29 - 1/11)" — December
        2025 — so a 2026 anchor pushed Sprint 22 to 2027, where it matched
        nothing and showed 0.0h against 178 hours of real work."""
        got = per.year_from_entries("Sprint 22 (9/28 - 10/11)",
                                    ("2026-09-28", "2026-10-11"))
        self.assertEqual((got["starts_on"], got["ends_on"]),
                         ("2026-09-28", "2026-10-11"))

    def test_a_sprint_that_straddles_new_year_rolls_the_end_only(self):
        got = per.year_from_entries("Sprint 2 (12/29 - 1/11)",
                                    ("2025-12-29", "2026-01-11"))
        self.assertEqual((got["starts_on"], got["ends_on"]),
                         ("2025-12-29", "2026-01-11"))


class ChunkingCannotLoseAnEntry(unittest.TestCase):
    def test_month_windows_overlap_at_every_edge(self):
        """The 23:52-on-the-last-of-the-month entry. ClickUp wants an entry
        inside the window it is asked for, so without overlap a late-evening
        entry that ends after midnight belongs to no chunk at all."""
        wins = S._months("2026-09-01", "2026-10-31")
        self.assertEqual(wins[0][0], "2026-08-31")       # reaches back a day
        self.assertEqual(wins[0][1], "2026-10-01")       # and forward a day
        # every day in the range is covered by at least one window
        for day in ("2026-09-30", "2026-10-01"):
            self.assertTrue(any(lo <= day <= hi for lo, hi in wins), day)

    def test_a_round_answer_is_split_not_trusted(self):
        """A chunk returning exactly 50 rows is a ceiling. Trusting it
        under-bills, which is the worst thing this feature can do."""
        calls = []

        class FakeClient:
            @staticmethod
            def ist_ms(day, end=False):
                return ms(day)

            @staticmethod
            def time_entries(tok, *, start_ms, end_ms, member_ids,
                             space_id=None, list_id=None):
                calls.append((start_ms, end_ms))
                # the first (widest) ask looks truncated; the halves do not
                if len(calls) == 1:
                    return [{"id": str(i)} for i in range(50)]
                return [{"id": "deep-%d" % len(calls)}]

        rows = S._fetch(None, FakeClient, space_id="s", lo="2026-09-01",
                        hi="2026-09-30", member_ids=["1"], log=lambda m: None)
        self.assertGreater(len(calls), 1, "a suspicious count must be split")
        self.assertTrue(all(isinstance(r.get("id"), str) for r in rows))


class HoursCase(unittest.TestCase):
    """A client with one task carried across two periods — the case the whole
    'count by when it was logged' rule exists for."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hours-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        with db.connect(self.db_path) as c:
            for lid, name, lo, hi in (
                    ("L1", "Sprint 1 (10/1 - 10/7)", "2026-10-01", "2026-10-07"),
                    ("L2", "Sprint 2 (10/8 - 10/14)", "2026-10-08", "2026-10-14")):
                c.execute(
                    "INSERT INTO hours_periods (list_id, list_name, space_id,"
                    " client_id, starts_on, ends_on, parse_ok, updated_at)"
                    " VALUES (?,?,?,?,?,?,1,'2026-10-19')",
                    (lid, name, "SP", "acme", lo, hi))
            # ONE task, time logged in BOTH periods, and the task now sits in
            # the LATER list — exactly the carry-over shape
            rows = [("e1", "2026-10-03", 2 * MS_H), ("e2", "2026-10-05", 1 * MS_H),
                    ("e3", "2026-10-09", 3 * MS_H)]
            for eid, day, dur in rows:
                c.execute(
                    "INSERT INTO hours_entries (id, task_id, task_name, space_id,"
                    " space_name, list_id, list_name, client_id, user_id,"
                    " user_name, duration_ms, start_ms, created_ms,"
                    " first_seen_at, updated_at) VALUES"
                    " (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (eid, "T1", "Carried task", "SP", "Acme", "L2",
                     "Sprint 2 (10/8 - 10/14)", "acme", "U1", "Dev", dur,
                     ms(day), ms(day), "2026-10-19 10:00", "2026-10-19 10:00"))


class CarryOverIsNeverBilledTwice(HoursCase):
    def test_each_entry_lands_in_exactly_one_period(self):
        with db.connect(self.db_path) as c:
            p1 = R.client_period(c, "acme", "L1", "2026-10-19")
            p2 = R.client_period(c, "acme", "L2", "2026-10-19")
        self.assertEqual(p1["hours"], 3.0)      # 2h + 1h logged in week 1
        self.assertEqual(p2["hours"], 3.0)      # 3h logged in week 2
        # the task sits in L2, so a list-based count would put all 6h there
        self.assertNotEqual(p2["hours"], 6.0)

    def test_the_drill_sums_to_the_headline(self):
        from modules.cockpit import data
        d = data.hours_rows(self.db_path, client="acme", period="L1")
        self.assertEqual(d["sum_hours"], d["headline_hours"])
        self.assertEqual(d["count"], 2)

    def test_the_drill_does_not_drift_over_many_entries(self):
        """Summing the per-row 2dp values instead of the integer milliseconds
        put the drill 0.05h under the headline on a real 179-entry period."""
        from modules.cockpit import data
        with db.connect(self.db_path) as c:
            for i in range(60):          # durations that do not land on 2dp
                c.execute(
                    "INSERT INTO hours_entries (id, space_id, client_id,"
                    " user_id, duration_ms, start_ms, first_seen_at,"
                    " updated_at) VALUES (?,'SP','acme','U1',?,?,'x','x')",
                    (f"drift{i}", 100_001, ms("2026-10-02")))
        d = data.hours_rows(self.db_path, client="acme", period="L1")
        self.assertEqual(d["sum_hours"], d["headline_hours"])


class FiguresCarryTheirAsOf(HoursCase):
    """His decision 2026-10-19: totals are always live and always stamped,
    because hours keep arriving after a period closes — 13.6h landed one
    morning against a sprint that had ended the day before."""

    def test_every_surface_states_as_of(self):
        from modules.cockpit import data
        for d in (data.hours_overview(self.db_path),
                  data.hours_client(self.db_path, client="acme"),
                  data.hours_gaps(self.db_path),
                  data.hours_report(self.db_path, client="acme")):
            self.assertTrue(d.get("as_of"), d)

    def test_an_open_period_says_it_will_move(self):
        with db.connect(self.db_path) as c:
            rep = R.report(c, "acme", "L2", "2026-10-10")     # mid-period
        self.assertTrue(rep["open"])
        self.assertIn("still open", rep["note"])

    def test_a_running_timer_is_not_added_to_the_total(self):
        with db.connect(self.db_path) as c:
            c.execute(
                "INSERT INTO hours_entries (id, space_id, client_id, user_id,"
                " duration_ms, start_ms, first_seen_at, updated_at) VALUES"
                " ('run','SP','acme','U1',?,?,'x','x')",
                (-5 * MS_H, ms("2026-10-09")))
            p = R.client_period(c, "acme", "L2", "2026-10-19")
        self.assertEqual(p["hours"], 3.0)
        self.assertEqual(p["running_timers"], 1)


class ReportDefaultsToATotal(HoursCase):
    def test_no_breakdown_unless_the_client_is_switched_on(self):
        """Default is total billed hours only — most clients get a number.
        Breakdowns are a reviewed config change, not a UI toggle that silently
        alters what a client receives."""
        with db.connect(self.db_path) as c:
            rep = R.report(c, "acme", "L1", "2026-10-19")
        self.assertEqual(rep["total_hours"], 3.0)
        self.assertEqual(rep["breakdowns_enabled"], [])
        self.assertNotIn("by_task", rep)
        self.assertNotIn("by_person", rep)

    def test_an_enabled_client_gets_exactly_what_was_enabled(self):
        from unittest import mock
        with mock.patch.dict(config.HOURS_REPORT_BREAKDOWNS,
                             {"acme": ("task",)}):
            with db.connect(self.db_path) as c:
                rep = R.report(c, "acme", "L1", "2026-10-19")
        self.assertIn("by_task", rep)
        self.assertNotIn("by_person", rep)        # not enabled, not shown


class GapsAreMissingWeekdays(HoursCase):
    def test_a_weekend_is_not_a_gap(self):
        with db.connect(self.db_path) as c:
            g = R.logging_gaps(c, "2026-10-10", "2026-10-11")   # Sat + Sun
        self.assertEqual(g["weekdays"], 0)
        self.assertEqual(g["people"], [])

    def test_the_client_side_seat_is_excluded(self):
        """Jaap holds a seat in the workspace but works for Hermon. Checking
        him reports a teammate who never logs time, every single period."""
        with db.connect(self.db_path) as c:
            c.execute(
                "INSERT INTO hours_entries (id, space_id, client_id, user_id,"
                " user_name, duration_ms, start_ms, first_seen_at, updated_at)"
                " VALUES ('j','SP','acme','101070656','Jaap Meijvogel',?,?,"
                "'x','x')", (MS_H, ms("2026-10-09")))
            g = R.logging_gaps(c, "2026-10-05", "2026-10-09")
        self.assertNotIn("Jaap Meijvogel", [p["person"] for p in g["people"]])
        self.assertIn("101070656", g["excluded"])


class ReadOnlyIsEnforcedInCode(unittest.TestCase):
    def test_a_write_is_refused_before_the_network(self):
        """ClickUp is where the team logs time. This module reads it. The
        refusal is in code so a future edit cannot quietly add a write path."""
        from modules.hours import client as cu
        with self.assertRaises(cu.ClickUpReadError) as e:
            cu._request("/task/123", {}, "tok", method="POST")
        self.assertIn("read-only", str(e.exception))


if __name__ == "__main__":
    unittest.main()
