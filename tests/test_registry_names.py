"""The registry has the last word on a client's name (data._client_key step 7).

Abhijeet, 2026-10-08, asked whether to merge Globaltize's three rows on the
Clients tab ("Globaltize" from Wise, "Ryan Stomberg" and "Globaltize LLC" from
the agency): "yes please merge them. remote office is diat khan".

Pins: every spelling the rails produce lands on the registry's one name; the
row's drill sums to the row; the tab's total and its unlinked are untouched; a
contract still links to its row when the row now carries the company's name
and the room the person's; a suffix-free name two clients could claim matches
neither; a name the registry does not hold is left exactly as it was; and a
registry that will not load costs the merges, never the page.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import db, registry
from modules.cockpit import data

REG = """clients:
  -
    id: zed-quill
    name: Zed Quill LLC
    aliases:
      - Zed Quill
      - Ana Brook
      - "Ana Brook, Ana B"
  -
    id: far-office
    name: Lee Park
    aliases:
      - Far Office Australia Pty Ltd
      - Far Office
  -
    id: twin-a
    name: Twin Alpha
    aliases:
      - Twin Works Ltd
  -
    id: twin-b
    name: Twin Beta
    aliases:
      - Twin Works Inc
"""


def _rev(conn, rid, *, source, minor, company=None, desc=None, profile=None):
    conn.execute(
        "INSERT INTO transactions (record_id, canonical_key, type, category, source,"
        " assignment_company, description, amount_minor, amount_base_minor, currency,"
        " pnl_bucket, creation_dt, profile) VALUES (?, ?, 'Fixed Earning', 'income', ?, ?, ?,"
        " ?, ?, 'USD', 'revenue', '2026-09-20T00:00:00+0000', ?)",
        (rid, f"k:{rid}", source, company, desc, minor, minor, profile))


class TestRegistryNames(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="regnames-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.reg = registry.loads_registry(REG)
        with db.connect(self.db_path) as conn:
            # Globaltize's shape: Wise names the company, the agency's hourly
            # line the person, the agency's weekly CSV only the job title
            _rev(conn, "w1", source="Wise-mail", minor=130000,
                 desc="Received money from Zed Quill LLC with reference INV-1")
            _rev(conn, "u1", source="Upwork-MCP", minor=35000, company="Ana Brook", profile="Agency",
                 desc="(c1) Ana Brook/Abhijeet Gandhi - 10:00 hrs @ $35.00/hr")
            _rev(conn, "u2", source="Upwork-CSV", minor=21000, profile="Agency",
                 desc="Zed agency work · Earnings for Sep 14-Sep 20, 2026")
            # Remote Office's shape: an alias WITH its suffix, a payer line without
            _rev(conn, "w2", source="Wise", minor=25000,
                 desc="Received money from Far Office Australia Pty Ltd with reference R1")
            # two clients reduce to one suffix-free name: evidence for neither
            _rev(conn, "w3", source="Wise", minor=10000,
                 desc="Received money from Twin Works Ltd with reference T1")
            # nobody the registry knows
            _rev(conn, "u3", source="Upwork", minor=5000, company="Nobody Known")
            for cid, title, room in (("c1", "Zed agency work", "room_z"), ("c2", "Zed second job", "room_y")):
                conn.execute("INSERT INTO contracts (id, title, upwork_status, client_company, thread_id,"
                             " start_dt) VALUES (?, ?, 'ACTIVE', 'Ana Brook, Ana B', ?, '2026-09-10')",
                             (cid, title, room))
            # one room the linker filed under the client, one it did not
            conn.execute("INSERT INTO threads (id, room_name, client_id) VALUES ('room_z', 'Ana Brook', 'zed-quill')")
            conn.execute("INSERT INTO threads (id, room_name) VALUES ('room_y', 'Ana Brook, Ana B')")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def clients(self, reg_ok=True):
        load = (mock.patch("core.registry.load", return_value=self.reg) if reg_ok
                else mock.patch("core.registry.load", side_effect=registry.RegistryError("bad file")))
        with load, mock.patch("modules.finance.doctor.run",
                              return_value=mock.Mock(green=True, as_of="2026-10-08")):
            out = data.clients(self.db_path)
            return out, {c["name"]: c for c in out["clients"]}, data.client_transactions(
                self.db_path, client="Zed Quill LLC")

    def test_every_spelling_is_one_client_and_the_drill_is_the_row(self):
        out, rows, drill = self.clients()
        z = rows["Zed Quill LLC"]
        self.assertEqual((z["revenue_usd"], z["payments"]), (1860.0, 3))
        self.assertEqual(sorted(z["channels"]), ["Upwork", "Wise-mail"])
        self.assertNotIn("Ana Brook", rows)
        self.assertNotIn("Zed Quill", rows)
        self.assertEqual(z["aka"], ["Ana Brook", "Zed Quill"])       # how the ledger spelled it
        self.assertEqual((drill["count"], drill["sum_usd"]), (3, 1860.0))

    def test_an_alias_with_its_suffix_meets_a_payer_line_without(self):
        _out, rows, _ = self.clients()
        self.assertEqual(rows["Lee Park"]["revenue_usd"], 250.0)     # "remote office is diat khan"
        self.assertNotIn("Far Office Australia", rows)

    def test_an_ambiguous_or_unknown_name_is_left_as_it_was(self):
        _out, rows, _ = self.clients()
        self.assertEqual(rows["Twin Works"]["revenue_usd"], 100.0)    # Twin Alpha? Twin Beta? neither
        self.assertNotIn("Twin Alpha", rows)
        self.assertNotIn("Twin Beta", rows)
        self.assertEqual(rows["Nobody Known"]["revenue_usd"], 50.0)

    def test_totals_untouched_and_contracts_still_link(self):
        out, rows, _ = self.clients()
        self.assertEqual((out["revenue_total_usd"], out["unlinked"]["usd"]), (2260.0, 0.0))
        self.assertEqual(rows["Zed Quill LLC"]["active_contracts"], 2)  # by client_id, and by room name
        self.assertEqual(out["linkage"]["contracts_linked"], 2)

    def test_a_registry_that_will_not_load_costs_the_merges_not_the_page(self):
        out, rows, _ = self.clients(reg_ok=False)
        self.assertEqual(out["revenue_total_usd"], 2260.0)
        self.assertIn("Ana Brook", rows)                              # unmerged, but there
        self.assertIn("Zed Quill", rows)


if __name__ == "__main__":
    unittest.main()
