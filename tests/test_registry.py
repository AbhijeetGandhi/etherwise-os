"""Registry loader + bundled YAML-subset reader/writer tests (Jarvis J1 §4.1)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core import registry


SAMPLE = """\
# managed file
clients:
  -
    id: adelye
    name: Adelye
    aliases:
      - "Adelye Ltd."
      - Chris Cave
    emails:
      - adelye.com
    upwork_rooms: []
    fathom_names:
      - adelye
    billing:
      rail: upwork
      canonical: Adelye
    scope_notes: ""
  -
    id: horizons-aba
    name: Horizons ABA
    aliases:
      - Alexander Apfel
    emails:
      - horizonsaba.com
      - alex@horizonsaba.com
    billing:
      rail: wise
      canonical: Horizons ABA
"""


class YamlSubsetTests(unittest.TestCase):
    def test_roundtrip(self):
        doc = registry.loads(SAMPLE)
        self.assertEqual(len(doc["clients"]), 2)
        self.assertEqual(doc["clients"][0]["aliases"], ["Adelye Ltd.", "Chris Cave"])
        self.assertEqual(doc["clients"][0]["upwork_rooms"], [])
        self.assertEqual(doc["clients"][0]["billing"]["canonical"], "Adelye")
        self.assertIsNone(doc["clients"][0]["scope_notes"] or None)
        # dump -> load is stable
        again = registry.loads(registry.dumps(doc))
        self.assertEqual(again, doc)

    def test_quoted_scalar_with_colon_and_hash(self):
        doc = registry.loads('k: "a: b # c"\n')
        self.assertEqual(doc["k"], "a: b # c")

    def test_comment_not_stripped_inside_quotes(self):
        doc = registry.loads('k: "keep # this"  # drop this\n')
        self.assertEqual(doc["k"], "keep # this")

    def test_tabs_rejected(self):
        with self.assertRaises(registry.RegistryError):
            registry.loads("k:\n\t- v\n")

    def test_needs_quote_on_dump(self):
        s = registry.dumps({"clients": [{"id": "x", "name": "PT. Bank, Tbk."}]})
        self.assertIn('"PT. Bank, Tbk."', s)
        self.assertEqual(registry.loads(s)["clients"][0]["name"], "PT. Bank, Tbk.")

    def test_partial_parse_raises(self):
        # a stray dedent that leaves unconsumed lines must fail loud
        with self.assertRaises(registry.RegistryError):
            registry.loads("a: 1\n  b: 2\n")


class RegistryValidationTests(unittest.TestCase):
    def _load(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "clients.yaml"
            p.write_text(text)
            return registry.load(p, force=True)

    def test_loads_and_indexes(self):
        reg = self._load(SAMPLE)
        self.assertEqual(set(reg.ids()), {"adelye", "horizons-aba"})
        self.assertEqual(reg.canonical("adelye"), "Adelye")
        self.assertEqual(len(reg.sha), 64)

    def test_match_email_domain_and_full(self):
        reg = self._load(SAMPLE)
        self.assertEqual(reg.match_email("chris@adelye.com"), "adelye")
        self.assertEqual(reg.match_email("ALEX@HORIZONSABA.COM"), "horizons-aba")
        self.assertEqual(reg.match_email("someone@unknown.com"), None)
        self.assertEqual(reg.match_email(""), None)

    def test_match_name_accent_insensitive(self):
        reg = self._load(SAMPLE)
        self.assertEqual(reg.match_name("Adélye Ltd."), "adelye")
        self.assertEqual(reg.match_name("Alexander Apfel"), "horizons-aba")

    def test_duplicate_id_rejected(self):
        with self.assertRaises(registry.RegistryError):
            self._load(SAMPLE + "  -\n    id: adelye\n    name: Dup\n")

    def test_overlapping_email_rejected(self):
        bad = (
            "clients:\n"
            "  -\n    id: a\n    name: A\n    emails:\n      - shared.com\n"
            "  -\n    id: b\n    name: B\n    emails:\n      - shared.com\n"
        )
        with self.assertRaises(registry.RegistryError):
            self._load(bad)

    def test_overlapping_alias_rejected(self):
        bad = (
            "clients:\n"
            "  -\n    id: a\n    name: A\n    aliases:\n      - Shared Name\n"
            "  -\n    id: b\n    name: Shared Name\n"
        )
        with self.assertRaises(registry.RegistryError):
            self._load(bad)

    def test_our_own_person_rejected_as_an_alias(self):
        """One of US may never be a client identifier (2026-08-14).

        Alphons Jaimon is an Etherwise contractor who speaks on 12 Fathom calls
        across two different clients. The roster miner proposed him as an alias
        for BOTH horizons-aba and cmf-publishing in one pass: he is on their
        calls, so by name-frequency he looks like their person. Accepting either
        would file every internal working session under that client and, because
        an identifier is substring-matched, claim the other client's calls too.
        A wrong attribution is permanent, so this is a load ERROR."""
        bad = (SAMPLE + "  -\n    id: x\n    name: X\n"
               "    aliases:\n      - Alphons Jaimon\n")
        with self.assertRaises(registry.RegistryError) as e:
            self._load(bad)
        self.assertIn("Etherwise person", str(e.exception))

    def test_our_own_person_rejected_as_a_client_name(self):
        with self.assertRaises(registry.RegistryError):
            self._load(SAMPLE + "  -\n    id: y\n    name: Yogita Soma\n")

    def test_internal_person_check_is_exact_not_substring(self):
        """'abhijeet' is in INTERNAL_PEOPLE as a bare token because Slack renders
        him that way. If the check were a substring it would reject a client
        genuinely named 'Abhijeet Sharma' — and, worse, silently swallow that
        stranger's Slack messages as ours."""
        from core import config
        self.assertTrue(config.is_internal_person("Abhijeet Gandhi"))
        self.assertTrue(config.is_internal_person("  ALPHONS   JAIMON "))
        self.assertTrue(config.is_internal_person("alphons@etherwise.io"))
        self.assertTrue(config.is_internal_person("abhijeetgandhi02@gmail.com"))
        self.assertFalse(config.is_internal_person("Abhijeet Sharma"))
        self.assertFalse(config.is_internal_person("Alphons Jaimonsson"))
        self.assertFalse(config.is_internal_person("abel@hermon.io"))
        self.assertFalse(config.is_internal_person(""))
        reg = self._load(SAMPLE + "  -\n    id: z\n    name: Abhijeet Sharma\n")
        self.assertEqual(reg.match_name("Abhijeet Sharma"), "z")

    def test_slack_self_authors_is_the_same_roster(self):
        """Not a copy. A teammate added to INTERNAL_PEOPLE and forgotten in the
        Slack list would read as the CLIENT talking in that client's workspace."""
        from core import config
        self.assertIs(config.JARVIS_SLACK_SELF_AUTHORS, config.INTERNAL_PEOPLE)

    def test_no_match_entry_registered_but_not_matchable(self):
        text = (SAMPLE + "  -\n    id: phonicflow\n    name: PhonicFlow\n"
                "    aliases:\n      - PhonicFlow\n    no_match: true\n")
        reg = self._load(text)
        self.assertIn("phonicflow", reg.ids())            # in the registry
        self.assertNotIn("phonicflow", reg.matchable_ids())  # excluded from match
        self.assertIsNone(reg.match_name("PhonicFlow"))    # never maps an event

    def test_prospect_is_matchable_but_not_a_client(self):
        # A prospect (expired proposal with real calls) is UNLIKE no_match: it
        # still maps events (so the call keeps its context), but it is excluded
        # from the client economics roster.
        text = (SAMPLE + "  -\n    id: clara\n    name: Clara\n"
                "    kind: prospect\n    emails:\n      - tryclara.org\n")
        reg = self._load(text)
        self.assertIn("clara", reg.ids())
        self.assertIn("clara", reg.matchable_ids())          # CAN hold a call
        self.assertEqual(reg.match_email("david@tryclara.org"), "clara")
        self.assertEqual(reg.kind("clara"), "prospect")
        self.assertEqual(reg.kind("adelye"), "client")       # default
        self.assertNotIn("clara", reg.client_ids())          # not in economics
        self.assertIn("adelye", reg.client_ids())

    def test_former_client_is_still_a_client_but_not_active(self):
        """"no longer active" had no machine-readable form. The convention for a
        finished engagement is a CLOSED contract row, which the Clients tab
        already reads — and `zeeshan-rasheed` never had an Upwork contract, so
        the fact lived in prose nobody can act on (Abhijeet 2026-08-14: "just
        have him but I know we are not going to work with him ever again").

        `former` is NOT `prospect`: a prospect has not converted YET, a former
        client DID pay. So it stays on the economics roster — dropping it would
        quietly delete past revenue — and `is_active()` is the one thing that
        answers False."""
        text = (SAMPLE + "  -\n    id: zeeshan-rasheed\n    name: Zeeshan Rasheed\n"
                "    kind: former\n")
        reg = self._load(text)
        self.assertEqual(reg.kind("zeeshan-rasheed"), "former")
        self.assertIn("zeeshan-rasheed", reg.matchable_ids())   # history attributes
        self.assertIn("zeeshan-rasheed", reg.client_ids())      # it paid
        self.assertFalse(reg.is_active("zeeshan-rasheed"))      # never chased
        self.assertTrue(reg.is_active("adelye"))                # default kind
        self.assertFalse(reg.is_active("nobody-at-all"))        # unknown != active

    def test_unknown_kind_rejected(self):
        with self.assertRaises(registry.RegistryError):
            self._load(SAMPLE + "  -\n    id: x\n    name: X\n    kind: lead\n")

    def test_duplicate_ledger_label_rejected(self):
        """billing.ledger_label became a MATCHER on 2026-08-13 (exact equality
        on the ledger's company column, modules/knowledge/crm.py). Two clients
        claiming 'no company' would put the same money in two dossiers, so it
        is a load error like every other identity claim — never a silent pick."""
        bad = (SAMPLE
               + "  -\n    id: a\n    name: A\n    billing:\n"
                 '      ledger_label: "no company"\n'
               + "  -\n    id: b\n    name: B\n    billing:\n"
                 '      ledger_label: "No  Company"\n')     # same after norm()
        with self.assertRaises(registry.RegistryError):
            self._load(bad)

    def test_distinct_ledger_labels_load(self):
        ok = (SAMPLE
              + "  -\n    id: a\n    name: A\n    billing:\n"
                '      ledger_label: "S"\n'
              + "  -\n    id: b\n    name: B\n    billing:\n"
                '      ledger_label: "no company"\n')
        reg = self._load(ok)
        self.assertEqual(reg._ledger_label_to_id,
                         {"s": "a", "no company": "b"})

    def test_norm_collapses_internal_whitespace(self):
        """Upwork emits the DOUBLE space; the registry holds one. Before this,
        an existing client with $757.23 earned and 80 messages across two ACTIVE
        contracts was unmatchable (2026-08-13)."""
        self.assertEqual(registry.norm("Tanushant  Sharma"), "tanushant sharma")
        self.assertEqual(registry.norm("Tanushant Sharma"), "tanushant sharma")
        # runs of any length, tabs and newlines all fold to one space
        self.assertEqual(registry.norm("  Ippon \t\n  Technology  "),
                         "ippon technology")
        # and it is still accent-insensitive
        self.assertEqual(registry.norm("Adélye  Ltd."), "adelye ltd.")

    def test_match_name_survives_a_double_space(self):
        reg = self._load(SAMPLE)
        self.assertEqual(reg.match_name("Horizons  ABA"), "horizons-aba")
        self.assertEqual(reg.match_name("Alexander\tApfel"), "horizons-aba")
        # a double space must not make two distinct clients collide
        self.assertIsNone(reg.match_name("Horizons  ABBA"))

    def test_double_space_alias_indexes_to_its_single_space_form(self):
        """A hand-edit that pastes Upwork's double-spaced name as an ALIAS now
        collides with the same client's own name instead of creating a second,
        never-matched key — and a genuine cross-client overlap still raises."""
        reg = self._load(
            "clients:\n"
            "  -\n    id: t\n    name: Tanushant Sharma\n"
            "    aliases:\n      - \"Tanushant  Sharma\"\n")
        self.assertEqual(reg.match_name("Tanushant  Sharma"), "t")
        self.assertEqual(reg.match_name("Tanushant Sharma"), "t")
        with self.assertRaises(registry.RegistryError):
            self._load(
                "clients:\n"
                "  -\n    id: a\n    name: Tanushant Sharma\n"
                "  -\n    id: b\n    name: B\n"
                "    aliases:\n      - \"Tanushant  Sharma\"\n")

    def test_cache_invalidation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "clients.yaml"
            p.write_text(SAMPLE)
            r1 = registry.load(p)
            r2 = registry.load(p)
            self.assertIs(r1, r2)  # cached
            registry.invalidate(p)
            r3 = registry.load(p)
            self.assertIsNot(r1, r3)


if __name__ == "__main__":
    unittest.main()
