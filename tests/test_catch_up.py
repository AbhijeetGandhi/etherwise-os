"""bin/catch-up — restart only what is ACTUALLY overdue.

macOS skips StartCalendarInterval windows that pass while the Mac is asleep; it
does not queue them. Blanket RunAtLoad would fire everything on every load, so
the shim consults the source registry instead. These tests pin the guards that
make that safe: nothing healthy is touched, opted-out sources are never
restarted, and a broken job is not re-kicked on every wake.
"""
from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from core import config, db

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_loader(
        "catch_up_bin",
        importlib.machinery.SourceFileLoader("catch_up_bin",
                                             str(ROOT / "bin" / "catch-up")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cu = _load()
IST = config.TZ


def _now():
    return datetime(2026, 7, 20, 18, 0, tzinfo=IST)


SRC = (
    {"key": "rail", "label": "Rail", "task_name": "t_rail",
     "max_age_hours": 26, "degrade_kinds": (), "critical": True,
     "backs": "applied", "launchd_label": "io.test.rail", "catch_up": True,
     "why": "applied truth"},
    {"key": "scan", "label": "Scan", "task_name": "t_scan",
     "max_age_hours": 3, "degrade_kinds": ("scan_feed_degraded",),
     "critical": True, "backs": "pipeline",
     "launchd_label": "io.test.scan", "catch_up": True, "why": "the funnel"},
    {"key": "ledger", "label": "Manual imports", "task_name": "t_ledger",
     "max_age_hours": 1080, "degrade_kinds": (), "critical": True,
     "backs": "money", "catch_up": False,          # opted out: a human feeds it
     "freshness_sql": "SELECT MAX(first_seen_at) FROM transactions",
     "why": "manual imports"},
)


def _run_row(conn, task, hours_ago, status="completed"):
    ts = (_now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO runs (task_name, started_at, completed_at,"
                 " status, shadow) VALUES (?,?,?,?,0)",
                 (task, ts, ts if status == "completed" else None, status))


class CatchUpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="catchup-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.kicks = []

        def fake_kick(label, uid, **kw):
            self.kicks.append(label)
            return 0, ""
        self.fake_kick = fake_kick
        self.ctx = mock.Mock(db_path=self.db_path, task_name="catch_up")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _go(self, **kw):
        # default: every label is loaded (tests exercise the kickstart path);
        # the not-installed guard is opted into per-test with _loaded.
        with mock.patch.object(config, "SOURCES", SRC):
            return cu.run(self.ctx, now=_now(), uid=501, _sleep=lambda s: None,
                          _kick=kw.pop("_kick", self.fake_kick),
                          _loaded=kw.pop("_loaded", lambda label, uid: True),
                          **kw)

    def _anomaly(self, conn, kind, key, resolved=False, when_h=1):
        ts = (_now() - timedelta(hours=when_h)).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO anomalies (task_name, kind, severity, detail_json,"
            " detected_at, resolved_at) VALUES ('catch_up',?, 'critical',?,?,?)",
            (kind, f'{{"failed": [{{"key": "{key}"}}]}}', ts,
             ts if resolved else None))

    # ── the core behaviour ───────────────────────────────────────────────────
    def test_healthy_sources_are_never_touched(self):
        with db.connect(self.db_path) as conn:
            _run_row(conn, "t_rail", 1)
            _run_row(conn, "t_scan", 0.5)
        out = self._go()
        self.assertEqual(out["kicked"], [])
        self.assertEqual(self.kicks, [])

    def test_only_the_overdue_source_is_restarted(self):
        # rail is 3 days cold; scan ran 10 minutes ago
        with db.connect(self.db_path) as conn:
            _run_row(conn, "t_rail", 72)
            _run_row(conn, "t_scan", 0.15)
        out = self._go()
        self.assertEqual(out["kicked"], ["rail"])
        self.assertEqual(self.kicks, ["io.test.rail"])   # no thundering herd

    def test_a_sleep_outage_restarts_every_missed_job(self):
        # the real incident: nothing ran for three days
        with db.connect(self.db_path) as conn:
            _run_row(conn, "t_rail", 72)
            _run_row(conn, "t_scan", 72)
        out = self._go()
        self.assertEqual(sorted(out["kicked"]), ["rail", "scan"])

    def test_manual_source_is_reported_but_never_kickstarted(self):
        # the ledger is stale (no rows at all -> down) but a human feeds it
        with db.connect(self.db_path) as conn:
            _run_row(conn, "t_rail", 1)
            _run_row(conn, "t_scan", 0.5)
        out = self._go()
        self.assertNotIn("ledger", out["kicked"])
        self.assertEqual(self.kicks, [])

    # ── guards ───────────────────────────────────────────────────────────────
    def test_cooldown_stops_a_broken_job_being_kicked_every_wake(self):
        # last SUCCESS is ancient (so it reads stale) but it ATTEMPTED a run a
        # moment ago — kicking again would just spin
        with db.connect(self.db_path) as conn:
            _run_row(conn, "t_rail", 72)
            _run_row(conn, "t_rail", 0.05, status="failed")
            _run_row(conn, "t_scan", 0.5)
        out = self._go()
        self.assertEqual(out["kicked"], [])
        self.assertEqual(out["skipped_cooldown"], ["rail"])

    def test_degraded_is_not_restarted(self):
        # a degraded source RAN — it produced thin output. Restarting it does
        # not fix the cause, so catch-up leaves it to the humans/anomaly path.
        with db.connect(self.db_path) as conn:
            _run_row(conn, "t_rail", 1)
            cur = conn.execute(
                "INSERT INTO runs (task_name, started_at, completed_at, status,"
                " shadow) VALUES ('t_scan',?,?, 'completed',0)",
                ((_now() - timedelta(minutes=20)).isoformat(timespec="seconds"),
                 (_now() - timedelta(minutes=20)).isoformat(timespec="seconds")))
            conn.execute("INSERT INTO anomalies (task_name, kind, severity,"
                         " run_id) VALUES ('t_scan','scan_feed_degraded',"
                         " 'critical', ?)", (cur.lastrowid,))
        out = self._go()
        self.assertEqual(out["kicked"], [])

    def test_kickstart_failure_raises_a_critical_anomaly(self):
        with db.connect(self.db_path) as conn:
            _run_row(conn, "t_rail", 72)
            _run_row(conn, "t_scan", 0.5)

        def boom(label, uid, **kw):
            return 1, "Could not find service"
        out = self._go(_kick=boom)
        self.assertEqual(out["failed"], ["rail"])
        self.ctx.anomaly.assert_called_once()
        self.assertEqual(self.ctx.anomaly.call_args[0][1], "critical")

    def test_gives_up_on_a_job_that_fails_every_attempt(self):
        """A job failing every run is broken, not missed. Re-kicking it every
        30 min would loop forever and bury the fault under repeat anomalies —
        found for real: the connects rail fails whenever its Chrome tab is
        parked hidden."""
        with db.connect(self.db_path) as conn:
            _run_row(conn, "t_rail", 72)                     # last success, ancient
            for h in (5, 4, 3):                              # then nothing but failures
                _run_row(conn, "t_rail", h, status="failed")
            _run_row(conn, "t_scan", 0.5)
        out = self._go()
        self.assertEqual(out["kicked"], [])
        self.assertEqual(out["gave_up"], ["rail"])
        self.assertEqual(self.kicks, [])
        self.ctx.anomaly.assert_called_once()
        self.assertEqual(self.ctx.anomaly.call_args[0][0], "catch_up_gave_up")

    def test_a_single_failure_still_gets_retried(self):
        # one bad run is a missed window, not a broken job — still worth a kick
        with db.connect(self.db_path) as conn:
            _run_row(conn, "t_rail", 72)
            _run_row(conn, "t_rail", 5, status="failed")
            _run_row(conn, "t_scan", 0.5)
        out = self._go()
        self.assertEqual(out["kicked"], ["rail"])
        self.assertEqual(out["gave_up"], [])

    def test_a_success_after_failures_clears_the_giveup(self):
        with db.connect(self.db_path) as conn:
            _run_row(conn, "t_rail", 72)
            for h in (9, 8, 7):
                _run_row(conn, "t_rail", h, status="failed")
            _run_row(conn, "t_rail", 30)          # recovered, but now stale again
            _run_row(conn, "t_scan", 0.5)
        out = self._go()
        self.assertEqual(out["kicked"], ["rail"])

    # ── not-installed vs failed-to-start (2026-08-07 jarvis_sweep loop) ────────
    def test_not_installed_is_flagged_once_never_kicked(self):
        """A launchd label that was never bootstrapped is absent from `launchctl
        list`. That is a SETUP error, not a transient failure — flag it loudly
        with the bootstrap command and never enter a retry loop."""
        with db.connect(self.db_path) as conn:
            _run_row(conn, "t_rail", 72)
            _run_row(conn, "t_scan", 0.5)
        # rail's label is not loaded; scan's is
        loaded = lambda label, uid: label != "io.test.rail"
        out = self._go(_loaded=loaded)
        self.assertEqual(out["not_installed"], ["rail"])
        self.assertEqual(out["kicked"], [])           # never kickstarted
        self.assertEqual(out["failed"], [])           # NOT a catch_up_failed
        self.assertEqual(self.kicks, [])
        self.ctx.anomaly.assert_called_once()
        kind, sev = self.ctx.anomaly.call_args[0][0], self.ctx.anomaly.call_args[0][1]
        self.assertEqual(kind, "catch_up_not_installed")
        self.assertEqual(sev, "critical")
        detail = self.ctx.anomaly.call_args[0][2]
        cmd = detail["sources"][0]["bootstrap"]
        self.assertIn("launchctl bootstrap gui/501", cmd)
        self.assertIn("io.test.rail.plist", cmd)

    def test_give_up_holds_per_key_off_the_kickstart_trail(self):
        """The guard the 26x loop needed: 3 consecutive kickstart failures for a
        key ⇒ the 4th attempt is suppressed and ONE catch_up_gave_up is raised,
        not a 4th catch_up_failed — even though the target task has no failed
        RUNS of its own (it was never installed / never ran)."""
        with db.connect(self.db_path) as conn:
            _run_row(conn, "t_rail", 72)              # a stale-making success…
            _run_row(conn, "t_scan", 0.5)
            for h in (3, 2, 1):                       # …then 3 kickstart failures
                self._anomaly(conn, "catch_up_failed", "rail", when_h=h)
        out = self._go()
        self.assertEqual(out["kicked"], [])           # 4th attempt suppressed
        self.assertEqual(out["gave_up"], ["rail"])
        self.assertEqual(out["failed"], [])           # not a 4th catch_up_failed
        self.assertEqual(self.kicks, [])
        self.ctx.anomaly.assert_called_once()
        self.assertEqual(self.ctx.anomaly.call_args[0][0], "catch_up_gave_up")

    def test_two_kickstart_failures_still_get_retried(self):
        # below the give-up bar: still a missed window worth one more kick
        with db.connect(self.db_path) as conn:
            _run_row(conn, "t_rail", 72)
            _run_row(conn, "t_scan", 0.5)
            for h in (2, 1):
                self._anomaly(conn, "catch_up_failed", "rail", when_h=h)
        out = self._go()
        self.assertEqual(out["kicked"], ["rail"])
        self.assertEqual(out["gave_up"], [])

    def test_kickstart_failures_clear_after_a_success(self):
        # once the source runs again, prior kickstart failures no longer count.
        # (recovery at 30h postdates the failures but rail is stale past 26h, so
        # it is still overdue — the give-up is what clears, not the staleness.)
        with db.connect(self.db_path) as conn:
            _run_row(conn, "t_rail", 72)
            _run_row(conn, "t_scan", 0.5)
            for h in (40, 39, 38):
                self._anomaly(conn, "catch_up_failed", "rail", when_h=h)
            _run_row(conn, "t_rail", 30)              # recovered after the failures
        out = self._go()
        self.assertEqual(out["kicked"], ["rail"])     # give-up cleared
        self.assertEqual(out["gave_up"], [])

    def test_dry_run_kicks_nothing(self):
        with db.connect(self.db_path) as conn:
            _run_row(conn, "t_rail", 72)
            _run_row(conn, "t_scan", 72)
        out = self._go(dry_run=True)
        self.assertEqual(self.kicks, [])
        self.assertEqual(out["overdue"], 2)

    def test_kickstart_never_uses_dash_k(self):
        """-k would KILL a run already in flight. Restarting a job mid-run is
        exactly how you get half-written state."""
        calls = []
        cu.kickstart("io.test.rail", 501,
                     _run=lambda a, **kw: calls.append(a) or mock.Mock(
                         returncode=0, stderr="", stdout=""))
        self.assertNotIn("-k", calls[0])
        self.assertEqual(calls[0][:2], ["launchctl", "kickstart"])


class RegistryWiringCase(unittest.TestCase):
    def test_real_registry_labels_match_shipped_plists(self):
        """A label typo would make catch-up silently fail on the one day it
        matters, so the registry is checked against the plists on disk."""
        for s in config.SOURCES:
            label = s.get("launchd_label")
            if not label:
                continue
            self.assertTrue((ROOT / "launchd" / f"{label}.plist").is_file(),
                            f"{s['key']} -> {label}.plist not in launchd/")

    def test_emailing_and_manual_jobs_are_not_catch_up_eligible(self):
        by_key = {s["key"]: s for s in config.SOURCES}
        self.assertFalse(by_key["finance_ledger"].get("catch_up"))
        # nothing that emails is registered for automatic restart
        for s in config.SOURCES:
            self.assertNotIn("brief", s["task_name"])


if __name__ == "__main__":
    unittest.main()
