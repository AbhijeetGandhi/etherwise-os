"""Unit tests for core/doctor_checks.py — deterministic checks only; the
/v1/models network check stays thin and untested here."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core import config, db
from core import doctor_checks as dc


def statuses(checks):
    return [c.status for c in checks]


class TestPricingCoverage(unittest.TestCase):
    def test_full_coverage_passes(self):
        checks = dc.check_pricing_coverage(
            models={"scoring": "m-a"}, fallbacks={"m-a": ["m-b"]},
            pricing={"m-a": (1, 1, 1, 1), "m-b": (1, 1, 1, 1)},
            structured_unsupported=())
        self.assertTrue(all(c.status == "PASS" for c in checks))

    def test_missing_pricing_fails(self):
        checks = dc.check_pricing_coverage(
            models={"scoring": "m-a"}, fallbacks={}, pricing={},
            structured_unsupported=())
        self.assertIn("FAIL", statuses(checks))

    def test_fallback_without_structured_outputs_fails(self):
        checks = dc.check_pricing_coverage(
            models={"x": "m-a"}, fallbacks={"m-a": ["m-fable"]},
            pricing={"m-a": (1, 1, 1, 1), "m-fable": (1, 1, 1, 1)},
            structured_unsupported=("m-fable",))
        self.assertIn("FAIL", statuses(checks))

    def test_live_config_is_coherent(self):
        checks = dc.check_pricing_coverage()
        self.assertNotIn("FAIL", statuses(checks),
                         [c.detail for c in checks if c.status == "FAIL"])


class TestSecretScan(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="doctor-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clean_tree_passes(self):
        (self.tmp / "ok.py").write_text("x = 1\n")
        checks = dc.check_secret_scan(files=[self.tmp / "ok.py"])
        self.assertEqual(statuses(checks), ["PASS"])

    def test_planted_secret_fails_without_echoing_it(self):
        # concatenated so this test file itself never contains the pattern
        secret = "sk-ant-" + "api03-FAKEFAKEFAKEFAKE"
        bad = self.tmp / "oops.py"
        bad.write_text(f'KEY = "{secret}"\n')
        checks = dc.check_secret_scan(files=[bad])
        self.assertIn("FAIL", statuses(checks))
        for c in checks:
            self.assertNotIn(secret, c.detail)        # never echo the secret
        self.assertIn("oops.py", checks[0].detail)    # but do name the file

    def test_airtable_pat_detected(self):
        bad = self.tmp / "x.md"
        bad.write_text("token: " + "pat" + "AAAABBBBCCCCDD" + ".0123456789abcdef\n")
        self.assertIn("FAIL", statuses(dc.check_secret_scan(files=[bad])))


class TestCredentials(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="doctor-cred-"))
        os.chmod(self.tmp, 0o700)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_env(self, mode=0o600, key="sk-ant-test"):
        p = self.tmp / "etherwise-os.env"
        p.write_text(f"ANTHROPIC_API_KEY={key}\n")
        os.chmod(p, mode)
        return p

    def test_good_creds_pass(self):
        self.write_env()
        checks = dc.check_credentials(creds_dir=self.tmp)
        self.assertNotIn("FAIL", statuses(checks))

    def test_missing_env_file_fails(self):
        checks = dc.check_credentials(creds_dir=self.tmp)
        self.assertIn("FAIL", statuses(checks))

    def test_world_readable_env_warns(self):
        self.write_env(mode=0o644)
        checks = dc.check_credentials(creds_dir=self.tmp)
        self.assertIn("WARN", statuses(checks))

    def test_missing_api_key_fails(self):
        p = self.tmp / "etherwise-os.env"
        p.write_text("AIRTABLE_API_KEY=pat\n")
        os.chmod(p, 0o600)
        checks = dc.check_credentials(creds_dir=self.tmp)
        self.assertIn("FAIL", statuses(checks))

    def test_missing_dir_fails(self):
        checks = dc.check_credentials(creds_dir=self.tmp / "nope")
        self.assertEqual(checks[0].status, "FAIL")


class TestDbCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="doctor-db-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_migrated_db_passes(self):
        path = self.tmp / "t.db"
        db.migrate(path)
        checks = dc.check_db(db_path=path)
        self.assertNotIn("FAIL", statuses(checks))

    def test_missing_db_fails(self):
        checks = dc.check_db(db_path=self.tmp / "missing.db")
        self.assertEqual(checks[0].status, "FAIL")


class TestCalendar(unittest.TestCase):
    def test_all_far_future_no_warns(self):
        checks = dc.check_calendar(today="2026-05-01")
        self.assertNotIn("WARN", statuses(checks))
        self.assertNotIn("FAIL", statuses(checks))

    def test_within_week_warns(self):
        checks = dc.check_calendar(today="2026-06-10")
        warns = [c for c in checks if c.status == "WARN"]
        self.assertTrue(any("credit" in c.detail.lower() for c in warns))

    def test_overdue_warns(self):
        checks = dc.check_calendar(today="2026-09-01")
        warns = [c for c in checks if c.status == "WARN"]
        self.assertTrue(any("review pin" in c.detail for c in warns))


class TestGuardrailsSelfTest(unittest.TestCase):
    def test_rules_hold(self):
        checks = dc.check_guardrails_selftest()
        self.assertEqual(statuses(checks), ["PASS"],
                         [c.detail for c in checks])


class TestCockpitCheck(unittest.TestCase):
    def test_loopback_bind_passes(self):
        checks = dc.check_cockpit()
        binds = [c for c in checks if c.name == "cockpit bind"]
        self.assertEqual(binds[0].status, "PASS")  # config is 127.0.0.1

    def test_flags_non_loopback(self):
        with __import__("unittest").mock.patch.object(
                dc.config, "COCKPIT_HOST", "0.0.0.0"):
            checks = dc.check_cockpit()
        bind = [c for c in checks if c.name == "cockpit bind"][0]
        self.assertEqual(bind.status, "FAIL")

    def test_no_skip_stub_remains(self):
        details = " ".join(c.detail for c in dc.check_cockpit())
        self.assertNotIn("lands at M2", details)


class TestHooksCheck(unittest.TestCase):
    def test_real_repo_wiring_passes(self):
        checks = dc.check_hooks(root=config.V3_ROOT)
        self.assertNotIn("FAIL", statuses(checks),
                         [c.detail for c in checks])


if __name__ == "__main__":
    unittest.main()
