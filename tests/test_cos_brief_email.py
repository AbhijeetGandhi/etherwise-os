"""The brief's delivery gate.

2026-10-18: v2's `com.etherwise.morning-briefing` was stopped for serving
119-day-old threads off the v2 database, and that exposed something nobody had
noticed — v3's `cos_brief_am` had been running daily, succeeding, and writing a
correct brief to `var/briefs/` that went nowhere. Abhijeet: "wire a v3 brief in
email."

The gate is its OWN flag, not the module's. `modules/chief_of_staff/
interrupts.py` reads `SHADOW_MODE['chief_of_staff']`, and its docstring says
flipping that would double-ping hot leads the upwork module already emails. So
the brief ships and the module stays shadowed.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from core import config, db
from modules.chief_of_staff import brief


class TheGateIsItsOwn(unittest.TestCase):
    def test_the_module_stays_shadowed(self):
        """If this ever flips, interrupts.py starts emailing hot leads that
        the upwork module is already sending. The brief must never be the
        reason that happens."""
        self.assertTrue(config.SHADOW_MODE["chief_of_staff"])

    def test_brief_email_is_live(self):
        self.assertFalse(config.SHADOW_MODE["cos_brief_email"])
        self.assertTrue(brief.email_is_live())

    def test_shadowing_the_brief_blocks_the_send(self):
        calls = []
        with mock.patch.dict(config.SHADOW_MODE, {"cos_brief_email": True}):
            out = brief.send_brief({"slot": "am", "date": "2026-10-18",
                                    "headline": "H"}, "<p>b</p>",
                                   _send=lambda s, h: calls.append(s))
        self.assertFalse(out["sent"])
        self.assertEqual(out["reason"], "shadow: cos_brief_email")
        self.assertEqual(calls, [])          # never reached the transport


class DeliveryIsNotTheBrief(unittest.TestCase):
    def test_a_dead_smtp_does_not_fail_the_run(self):
        """The artifact is already on disk and the cockpit reads it. Raising
        here would throw away the narrative Opus was just paid to write, over
        a delivery problem."""
        def boom(subject, body):
            raise OSError("connection refused")
        out = brief.send_brief({"slot": "am", "date": "2026-10-18",
                                "headline": "H"}, "<p>b</p>", _send=boom)
        self.assertFalse(out["sent"])
        self.assertIn("send failed", out["reason"])

    def test_subject_carries_slot_date_and_headline(self):
        seen = {}
        brief.send_brief({"slot": "pm", "date": "2026-10-18",
                          "headline": "Three commitments due"}, "<p>b</p>",
                         _send=lambda s, h: seen.update(subject=s, body=h))
        self.assertEqual(seen["subject"],
                         "PM brief · 2026-10-18 — Three commitments due")
        self.assertEqual(seen["body"], "<p>b</p>")


class TheBannerTracksTheGate(unittest.TestCase):
    """A mail stamped "not sent" sitting in his inbox is the kind of small lie
    that makes the whole page untrustworthy."""

    def setUp(self):
        # built by the real gatherer against an empty migrated DB, so the
        # fixture cannot drift away from what render_html actually reads
        self.tmp = Path(tempfile.mkdtemp(prefix="cos-brief-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        dbp = self.tmp / "v3.db"
        db.migrate(dbp)
        self.brief = brief.build_brief(dbp, "am",
                                       datetime(2026, 10, 18, 11, 0,
                                                tzinfo=config.TZ))

    def _render(self):
        return brief.render_html(self.brief,
                                 {"headline": "H", "narrative": "N"})

    def test_no_draft_stamp_when_the_email_is_live(self):
        self.assertNotIn("not sent", self._render())

    def test_draft_stamp_returns_when_shadowed(self):
        with mock.patch.dict(config.SHADOW_MODE, {"cos_brief_email": True}):
            self.assertIn("not sent", self._render())


if __name__ == "__main__":
    unittest.main()
