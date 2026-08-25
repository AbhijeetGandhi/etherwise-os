"""The MCP shapes captured read-only on 2026-08-25 (tests/fixtures/mcp/), and
what Phase 0.2/0.3/0.5 may assume from them. Captured through the Upwork MCP
connected to the Upwork chat, on Abhijeet's approval; ids are real.

The two facts that de-risk 0.2: list_freelancer_proposals returns the SAME
vendorProposals node shape the GraphQL sync already normalizes, and its
marketplaceJobPosting.id is the bare numeric id scored_jobs / connects_history
/ contracts carry — no mapping table, no doubled proposals.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from modules.upwork import sync_state

FX = Path(__file__).resolve().parent / "fixtures" / "mcp"
NUMERIC_ID = re.compile(r"^\d{15,25}$")


def load(name):
    return json.loads((FX / name).read_text())


class ProposalsShape(unittest.TestCase):
    def test_list_nodes_parse_through_the_existing_adapter(self):
        fx = load("proposals_list.json")
        edges = fx["data"]["vendorProposals"]["edges"]
        self.assertEqual(len(edges), 10)
        for e in edges:
            flat = sync_state._vp_node_to_proposal(e["node"])
            self.assertTrue(NUMERIC_ID.fullmatch(flat["id"]), flat["id"])
            self.assertTrue(NUMERIC_ID.fullmatch(flat["job"]["id"]), flat["job"]["id"])
            self.assertTrue(flat["job"]["title"])
            self.assertEqual(flat["status"]["status"], "Accepted")
            self.assertTrue(flat["createdDateTime"].isdigit())    # epoch ms string
            self.assertTrue(float(flat["chargeRate"]["rawValue"]) > 0)

    def test_the_job_id_is_the_one_every_v3_table_carries(self):
        fx = load("proposals_list.json")
        first = fx["data"]["vendorProposals"]["edges"][0]["node"]
        # 2091881079178928768 is a connects_history.job_id (applied 2026-08-24)
        # and a scored_jobs.id on the live DB — the same 19-digit form
        self.assertEqual(first["marketplaceJobPosting"]["id"], "2091881079178928768")

    def test_status_vocabulary_matches_the_0_3_map(self):
        # the tool's own status list: Accepted=submitted, Offered, Hired,
        # Activated=invitation accepted, Pending, Declined, Withdrawn, Archived
        note = load("proposals_list.json")["_note"]
        self.assertIn("status:Accepted", note)

    def test_the_get_shape_carries_no_per_proposal_viewed_flag(self):
        """Phase 1 spec §3.3: 'viewed' becomes a stage only if 0.2's shape
        carries a viewed signal. It does not — `insights` is job-level (the
        other applicants); the stage stays unmeasured or is dropped."""
        fx = load("proposal_get.json")
        node = fx["data"]["vendorProposal"]
        self.assertNotIn("viewed", json.dumps(node).lower())
        ins = fx["insights"]
        self.assertIn("proposals_opened", ins)
        self.assertIn("proposals_total", ins)
        self.assertEqual(ins["opening_id"], node["marketplaceJobPosting"]["id"])
        self.assertTrue(node["proposalCoverLetter"])               # Loop 1's text


class OffersAndContractsShape(unittest.TestCase):
    def test_offers_carry_the_job_id_and_a_role_state(self):
        fx = load("offers_list.json")
        offers = fx["data"]["offersByAttribute"]["offers"]
        self.assertEqual(len(offers), 5)
        for o in offers:
            self.assertTrue(NUMERIC_ID.fullmatch(o["job"]["id"]))
            self.assertTrue(o["id"].isdigit())
            self.assertEqual(o["state"], "contract_started")
            self.assertIn(o["type"], ("MARKET_PLACE_HOURLY", "MARKET_PLACE_FIXED_PRICE"))
        # the 07-23 offer is the contract the live funnel already joins
        self.assertEqual(offers[0]["job"]["id"], "2079940185327676180")

    def test_contracts_link_to_offers_not_jobs(self):
        fx = load("contracts_search.json")
        contracts = fx["data"]["vendorContracts"]["contracts"]
        for c in contracts:
            self.assertTrue(c["id"].isdigit())
            self.assertTrue(c["offerId"].isdigit())
            self.assertIn(c["status"], ("ACTIVE", "CLOSED", "PAUSED"))
            self.assertNotIn("job", c)                             # the offer carries it


if __name__ == "__main__":
    unittest.main()
