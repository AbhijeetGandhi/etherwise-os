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

    def test_no_match_entry_registered_but_not_matchable(self):
        text = (SAMPLE + "  -\n    id: phonicflow\n    name: PhonicFlow\n"
                "    aliases:\n      - PhonicFlow\n    no_match: true\n")
        reg = self._load(text)
        self.assertIn("phonicflow", reg.ids())            # in the registry
        self.assertNotIn("phonicflow", reg.matchable_ids())  # excluded from match
        self.assertIsNone(reg.match_name("PhonicFlow"))    # never maps an event

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
