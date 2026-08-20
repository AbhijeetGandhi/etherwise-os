"""A card kind must be one the lifecycle tables can price.

2026-08-20. `kind` arrives as free text (Opus names the situation, and
`_is_cluster_card` reads those words on purpose). The failure was that an
invented kind missed EVERY table keyed by kind simultaneously — urgency,
decay TTL, and sweep.RULE_KINDS reconciliation — so the card was mis-ranked
AND un-closable, silently. Live symptom: 'system_recurring', one word off
'system', kept "upwork_scan pipeline down — 10+ consecutive failures" on Today
for 14 hours after the scan recovered, with 13 more days to run.

Measured that morning: 44 of the 54 kinds ever stored were model inventions.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config, db                                  # noqa: E402
from modules.chief_of_staff import nudges, sweep             # noqa: E402


class TestCanonicalKind(unittest.TestCase):

    def test_canonical_kinds_pass_through(self):
        for k in config.COS_CARD_KINDS:
            self.assertEqual(nudges.canonical_kind(k), k)

    def test_the_live_bug(self):
        """'system_recurring' is a system card, and must decay like one."""
        self.assertEqual(nudges.canonical_kind("system_recurring"), "system")

    def test_built_from_a_known_kind(self):
        cases = {
            "escalate_system":        "system",
            "escalate_client_risk":   "escalate_client",
            "commitment_due_soon":    "commitment_due",
            "urgent_owed_reply":      "owed_reply",
            "stale_dossier_backlog":  "stale_dossier",
        }
        for raw, want in cases.items():
            self.assertEqual(nudges.canonical_kind(raw), want, raw)

    def test_longest_match_wins(self):
        """'escalate_client' contains no other kind, but were a shorter kind to
        appear inside a longer one, the specific reading must win."""
        self.assertEqual(
            nudges.canonical_kind("escalate_client"), "escalate_client")

    def test_substrings_are_not_matches(self):
        """Word boundaries. 'subsystem_health' is not a 'system' card, and
        'recapture' is not a 'capture'. A wrong bucket is worse than the
        default, because it inherits a schedule chosen for something else."""
        for raw in ("subsystem_health", "recapture_flow", "systemic_risk"):
            self.assertEqual(nudges.canonical_kind(raw), config.COS_KIND_DEFAULT,
                             raw)

    def test_unknown_falls_back_by_provenance(self):
        """A rail reporting a failure must not decay on a sales card's clock."""
        self.assertEqual(
            nudges.canonical_kind("repeated_job_failure", source="system"),
            "system")
        self.assertEqual(
            nudges.canonical_kind("loom_backlog_bottleneck", source="sweep"),
            "escalate_client")
        self.assertEqual(
            nudges.canonical_kind("anything", source="commitment"),
            "commitment_due")

    def test_never_raises_never_returns_unpriced(self):
        """Losing a card to a strict validator would be worse than filing it
        roughly, so this path has no failure mode."""
        for raw in (None, "", "   ", "!!!", "x" * 500, 12345):
            got = nudges.canonical_kind(raw if isinstance(raw, str) or raw is None
                                        else str(raw))
            self.assertIn(got, config.COS_CARD_KINDS, repr(raw))

    def test_every_canonical_kind_is_priced_in_every_table(self):
        """The point of the whole exercise: the vocabulary is derived from the
        urgency table, so a kind cannot be added to one table and forgotten in
        the others."""
        for k in config.COS_CARD_KINDS:
            self.assertIn(k, config.COS_URGENCY_BASE, k)
            self.assertIn(k, config.COS_DECAY_TTL_DAYS, k)

    def test_defaults_are_themselves_canonical(self):
        self.assertIn(config.COS_KIND_DEFAULT, config.COS_CARD_KINDS)
        for src, k in config.COS_KIND_DEFAULT_BY_SOURCE.items():
            self.assertIn(k, config.COS_CARD_KINDS, src)


class TestWriteNudgeCanonicalises(unittest.TestCase):
    """The choke point, not the caller. Every writer gets this for free."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")
        db.migrate(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _row(self, key):
        with db.connect(self.db) as conn:
            return conn.execute(
                "SELECT kind, urgency, payload_json FROM nudges WHERE dedup_key=?",
                (key,)).fetchone()

    def test_stored_kind_is_canonical(self):
        nudges.write_nudge(self.db, source="sweep", dept="system",
                           kind="system_recurring", title="scan down",
                           dedup_key="system_recurring:upwork_scan")
        row = self._row("system_recurring:upwork_scan")
        self.assertEqual(row["kind"], "system")

    def test_the_model_wording_survives_in_the_payload(self):
        """It describes; it no longer controls."""
        nudges.write_nudge(self.db, source="sweep", dept="system",
                           kind="system_recurring", title="scan down",
                           dedup_key="k1")
        import json
        pl = json.loads(self._row("k1")["payload_json"])
        self.assertEqual(pl["kind_raw"], "system_recurring")

    def test_payload_is_preserved_alongside_kind_raw(self):
        nudges.write_nudge(self.db, source="sweep", dept="sales",
                           kind="loom_capacity_crunch", title="t",
                           dedup_key="k2", payload={"covers": ["a", "b"]})
        import json
        pl = json.loads(self._row("k2")["payload_json"])
        self.assertEqual(pl["covers"], ["a", "b"])
        self.assertEqual(pl["kind_raw"], "loom_capacity_crunch")

    def test_canonical_kind_writes_no_kind_raw(self):
        nudges.write_nudge(self.db, source="sales", dept="sales",
                           kind="owed_reply", title="t", dedup_key="k3")
        self.assertIsNone(self._row("k3")["payload_json"])

    def test_urgency_uses_the_canonical_base_not_the_fallback(self):
        """The mis-ranking half of the bug: 'system_recurring' scored 25 (the
        compute_urgency fallback) where 'system' scores 35."""
        nudges.write_nudge(self.db, source="sweep", dept="system",
                           kind="system_recurring", title="t", dedup_key="k4")
        nudges.write_nudge(self.db, source="system", dept="system",
                           kind="system", title="t", dedup_key="k5")
        self.assertEqual(self._row("k4")["urgency"], self._row("k5")["urgency"])

    def test_the_card_is_now_reachable_by_the_reconciler(self):
        """The un-closable half. RULE_KINDS is the list the sweep clears when a
        condition vanishes; the invented kind was not in it, so nothing could
        ever close the card but a human click."""
        nudges.write_nudge(self.db, source="sweep", dept="system",
                           kind="system_recurring", title="t", dedup_key="k6")
        self.assertIn(self._row("k6")["kind"], sweep.RULE_KINDS)

    def test_decay_now_applies_the_system_ttl(self):
        """14-day fallback vs the 7 days a system alarm is meant to get."""
        self.assertEqual(config.COS_DECAY_TTL_DAYS[
            nudges.canonical_kind("system_recurring")], 7)


if __name__ == "__main__":
    unittest.main()
