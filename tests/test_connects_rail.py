"""Connects-history rail — the applied-truth source.

The rail scrapes a CLIENT-fetched table, so three outcomes that all look like
"no rows" from the outside have to be told apart:
  rows rendered      -> ingest
  app's empty state  -> a real answer (or a throttle): DEGRADED, never a failure
  neither            -> layout break or hung fetch: fail loudly

It also needs the tab visible. Unlike the job feeds, this page ships no data in
window.__NUXT__ (its state is just `config`), so the scanner's SSR-payload fix
has nothing to read and Chrome will not run the fetch in a hidden tab.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from core import config, db
from modules.upwork import connects_history as ch


def _row(dt="July 14, 2026", action="Connects spent\nAI Automation Engineer",
         href="/jobs/AI-Automation_~021234567890123456789/", connects="-8"):
    return {"date": dt, "action": action, "href": href, "connects": connects}


def _payload(rows=(), empty=False,
             url="https://www.upwork.com/nx/plans/connects/history/#ew-scan"):
    return {"url": url, "rows": list(rows), "empty_state": empty}


def _fake_sp():
    sp = mock.Mock()
    sp.ensure_scan_tab.return_value = (1, 2)
    sp.MARKER = "#ew-scan"
    sp._osascript.return_value = "1"
    return sp


def _patch_sp(sp):
    """Swap the scan_pipeline module scrape() imports.

    `from modules.upwork import scan_pipeline` resolves via the PACKAGE
    attribute, so patching sys.modules alone is silently bypassed the moment
    another test has imported the real module — which would let this test drive
    real Chrome through real osascript. Patch both paths.
    """
    import modules.upwork
    return _Both(mock.patch.object(modules.upwork, "scan_pipeline", sp,
                                   create=True),
                 mock.patch.dict("sys.modules",
                                 {"modules.upwork.scan_pipeline": sp}))


class _Both:
    def __init__(self, *patchers):
        self.patchers = patchers

    def __enter__(self):
        for p in self.patchers:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patchers):
            p.stop()
        return False


class RailCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rail-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.ctx = mock.Mock(db_path=self.db_path, task_name="upwork_connects")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _scrape(self, payloads):
        """Run scrape() against a scripted sequence of browser probes."""
        seq = [json.dumps(p) for p in payloads]
        calls = {"n": 0}

        def fake_eval(js, tab_ref=None, **kw):
            i = min(calls["n"], len(seq) - 1)
            calls["n"] += 1
            return seq[i]

        sp = _fake_sp()
        with _patch_sp(sp), mock.patch.object(ch.feed_bridge, "chrome_eval",
                                              fake_eval):
            return ch.scrape(self.ctx, _sleep=lambda s: None), sp

    # ── the happy path ───────────────────────────────────────────────────────
    def test_rows_are_ingested_once_the_count_settles(self):
        # painting progressively: 1 row, then 2, then 2 again (stable) -> take it
        out, _ = self._scrape([
            _payload([_row()]),
            _payload([_row(), _row(connects="-4", href=None)]),
            _payload([_row(), _row(connects="-4", href=None)]),
            _payload([_row(), _row(connects="-4", href=None)]),
        ])
        self.assertEqual(out["window_rows"], 2)
        self.assertEqual(out["new"], 2)
        with db.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM connects_history").fetchone()[0], 2)

    def test_an_applied_row_is_classified_as_applied(self):
        out, _ = self._scrape([_payload([_row()])] * 4)
        self.assertEqual(out["kinds"].get("applied"), 1)

    # ── empty window: degraded, never failed ─────────────────────────────────
    def test_empty_state_is_degraded_not_a_failure(self):
        out, _ = self._scrape([_payload(empty=True)])
        self.assertTrue(out["empty"])
        self.assertEqual(out["window_rows"], 0)
        self.ctx.anomaly.assert_called_once()
        kind, severity = self.ctx.anomaly.call_args[0][0], self.ctx.anomaly.call_args[0][1]
        self.assertEqual(kind, "connects_empty_window")
        self.assertEqual(severity, "warn")      # warn, not critical: no fake emergency

    def test_early_empty_state_is_the_loading_placeholder_not_an_answer(self):
        """THE regression that matters: 'No Connects transactions' is what the
        page shows WHILE fetching (observed true at t+3s and t+6s, real rows at
        t+9s). Believing it early makes the rail report an empty window every
        run and silently lose the applied truth."""
        out, _ = self._scrape([
            _payload(empty=True),          # t+2  placeholder
            _payload(empty=True),          # t+4  placeholder
            _payload(empty=True),          # t+6  placeholder
            _payload([_row()]),            # t+8  data lands
            _payload([_row()]),
            _payload([_row()]),
        ])
        self.assertEqual(out["window_rows"], 1)
        self.assertNotIn("empty", out)
        self.ctx.anomaly.assert_not_called()

    def test_empty_is_only_believed_after_the_confirm_window(self):
        with mock.patch.object(ch, "EMPTY_CONFIRM_SECONDS", 6):
            out, _ = self._scrape([_payload(empty=True)] * 20)
        self.assertTrue(out["empty"])

    def test_empty_window_kind_is_registered_as_a_degrade_signal(self):
        src = {s["key"]: s for s in config.SOURCES}["connects_rail"]
        self.assertIn("connects_empty_window", src["degrade_kinds"])

    # ── genuine breakage still fails loudly ──────────────────────────────────
    def test_no_rows_and_no_empty_state_fails(self):
        with self.assertRaises(RuntimeError) as e:
            self._scrape([_payload()])
        self.assertIn("never rendered", str(e.exception))

    def test_wrong_page_fails_as_a_session_problem(self):
        with self.assertRaises(RuntimeError) as e:
            self._scrape([_payload([_row()], url="https://www.upwork.com/nx/login")] * 4)
        self.assertIn("not reached", str(e.exception))

    # ── visibility ───────────────────────────────────────────────────────────
    def test_tab_is_made_visible_and_then_restored(self):
        _, sp = self._scrape([_payload([_row()])] * 4)
        scripts = " ".join(c.args[0] for c in sp._osascript.call_args_list)
        self.assertIn("get active tab index", scripts)
        self.assertIn("set active tab index", scripts)
        # never brings Chrome to the front — that would steal focus
        self.assertNotIn("activate", scripts)

    def test_visibility_failure_does_not_abort_the_scrape(self):
        seq = json.dumps(_payload([_row()]))
        sp = _fake_sp()
        sp._osascript.side_effect = RuntimeError("no such window")
        with _patch_sp(sp), mock.patch.object(ch.feed_bridge, "chrome_eval",
                                              lambda *a, **k: seq):
            out = ch.scrape(self.ctx, _sleep=lambda s: None)
        self.assertEqual(out["window_rows"], 1)   # best-effort, never fatal

    # ── polling beats a fixed sleep ──────────────────────────────────────────
    def test_poll_waits_through_an_empty_first_probe(self):
        """The table paints at ~9s; the old fixed 9s sleep sat on that boundary
        and lost the race about half the time."""
        out, _ = self._scrape([
            _payload(),            # still fetching
            _payload(),            # still fetching
            _payload([_row()]),
            _payload([_row()]),
            _payload([_row()]),
        ])
        self.assertEqual(out["window_rows"], 1)

    def test_poll_gives_up_after_the_timeout(self):
        with mock.patch.object(ch, "POLL_TIMEOUT_SECONDS", 4):
            with self.assertRaises(RuntimeError):
                self._scrape([_payload()])


class ParseCase(unittest.TestCase):
    def test_kind_classification(self):
        self.assertEqual(ch._kind("Availability badge", -1, None), "badge")
        self.assertEqual(ch._kind("Connects spent", -8, "123"), "applied")
        self.assertEqual(ch._kind("Boost outbid", +4, None), "boost_refund")
        self.assertEqual(ch._kind("Monthly renewal", +10, None), "renewal")

    def test_same_day_duplicates_get_distinct_ids(self):
        rows = ch.parse_rows(
            [_row(action="Availability badge", href=None, connects="-1"),
             _row(action="Availability badge", href=None, connects="-1")],
            date(2026, 7, 20))
        self.assertEqual(len({r["id"] for r in rows}), 2)


if __name__ == "__main__":
    unittest.main()
