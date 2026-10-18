"""The job scan's schedule (Abhijeet, 2026-10-18: "can we do scann for job
posts more frequently like every 15 or 30 minutes so that we can apply
quickly"; with the terms trade-off in front of him he chose every 15 minutes
for all 14 searches).

Pins: the plist fires at exactly config.UPWORK_SCAN_MINUTES; the scan never
shares a minute with the pulse; the funnel's job fetch, the other reader of
find_jobs, is silent around EVERY slot (it used to know only :15), and leaves
it enough of each hour to finish its nightly pass.
"""
from __future__ import annotations

import plistlib
import unittest
from pathlib import Path

from core import config
from modules.upwork.feeds import mcp_search

ROOT = Path(__file__).resolve().parents[1]


def _minutes(label: str) -> list:
    doc = plistlib.loads((ROOT / "launchd" / f"{label}.plist").read_bytes())
    sci = doc["StartCalendarInterval"]
    sci = sci if isinstance(sci, list) else [sci]
    for d in sci:
        # minute-only entries: a Hour key would make it once a day, not hourly
        assert set(d) == {"Minute"}, d
    return sorted(d["Minute"] for d in sci)


class TestScanSchedule(unittest.TestCase):

    def test_the_plist_fires_at_the_configured_minutes(self):
        self.assertEqual(_minutes("io.etherwise.v3.upwork-scan"),
                         sorted(config.UPWORK_SCAN_MINUTES))
        self.assertEqual(sorted(config.UPWORK_SCAN_MINUTES), [0, 15, 30, 45])

    def test_it_runs_the_scan_module(self):
        doc = plistlib.loads((ROOT / "launchd" / "io.etherwise.v3.upwork-scan.plist").read_bytes())
        self.assertEqual(doc["ProgramArguments"][-1], "modules.upwork.scan_pipeline")
        src = next(s for s in config.SOURCES if s["task_name"] == "upwork_scan")
        self.assertEqual(src["launchd_label"], doc["Label"])

    def test_the_scan_and_the_pulse_never_share_a_minute(self):
        pulse = set(_minutes("io.etherwise.v3.upwork-pulse"))
        self.assertFalse(pulse & set(config.UPWORK_SCAN_MINUTES))

    def test_the_job_fetch_is_silent_around_every_slot(self):
        # one minute before each slot to five after: the :14-:20 the hourly
        # scan had, now at every slot
        for m in config.UPWORK_SCAN_MINUTES:
            for off in range(-1, 6):
                self.assertTrue(mcp_search.in_scan_window((m + off) % 60), (m, off))
            self.assertFalse(mcp_search.in_scan_window((m + 6) % 60), m)
            self.assertFalse(mcp_search.in_scan_window((m - 2) % 60), m)

    def test_the_job_fetch_keeps_most_of_each_hour(self):
        free = [m for m in range(60) if not mcp_search.in_scan_window(m)]
        self.assertEqual(len(free), 32)            # 4 x 8 minutes
        self.assertIn(10, free)
        self.assertNotIn(14, free)
        self.assertNotIn(59, free)

    def test_the_funnel_fetch_asks_the_shared_window(self):
        # fetch_jobs.py opens its log at import, so it is read, not imported
        src = (ROOT / "scripts" / "funnel" / "fetch_jobs.py").read_text()
        self.assertIn("mcp_search.in_scan_window(time.localtime().tm_min)", src)
        self.assertNotIn("14 <= time.localtime().tm_min <= 20", src)


if __name__ == "__main__":
    unittest.main()
