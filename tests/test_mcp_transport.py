"""Transport + shadow-poll invariants that must hold offline.

The live path was proven interactively on 2026-08-24 (initialize ok, 46 tools,
three accounts listed, write tool refused). These tests pin the parts that
could silently regress: response parsing in both wire shapes, the read-only
allowlist, and the wire-name mapping that produced a live 400 before it was
fixed.
"""
from __future__ import annotations

import json
import unittest

from modules.upwork.feeds import mcp_client as mc


class BodyParsing(unittest.TestCase):
    def test_plain_json(self):
        raw = json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"ok": 1}}).encode()
        msg = mc._parse_body(raw, "application/json", 3)
        self.assertEqual(msg["result"], {"ok": 1})

    def test_sse_takes_the_last_message_for_our_id(self):
        """Progress notifications precede the answer on the same stream."""
        raw = ("event: message\n"
               "data: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\"}\n\n"
               "data: {\"jsonrpc\":\"2.0\",\"id\":7,\"result\":{\"n\":1}}\n\n"
               "data: {\"jsonrpc\":\"2.0\",\"id\":7,\"result\":{\"n\":2}}\n\n"
               ).encode()
        msg = mc._parse_body(raw, "text/event-stream; charset=utf-8", 7)
        self.assertEqual(msg["result"], {"n": 2})

    def test_sse_ignores_other_ids_and_garbage(self):
        raw = ("data: not json\n\n"
               "data: {\"jsonrpc\":\"2.0\",\"id\":99,\"result\":{}}\n\n").encode()
        self.assertIsNone(mc._parse_body(raw, "text/event-stream", 7))

    def test_unparseable_json_returns_none(self):
        self.assertIsNone(mc._parse_body(b"<html>edge error</html>",
                                         "application/json", 1))


class ReadOnlyLaw(unittest.TestCase):
    """The shadow poller must be INCAPABLE of writing to Upwork, not merely
    told not to. Every write tool the server exposes must be refused before
    any network I/O happens."""

    def _session(self):
        s = mc.MCPSession()
        s._post = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("network reached — the refusal must come first"))
        return s

    def test_write_tools_are_refused_before_network(self):
        s = self._session()
        for tool in ("post_job", "send_message", "manage_proposals",
                     "confirm_draft", "submit_milestones", "end_contract",
                     "boost_profile", "update_profile", "respond_to_offer",
                     "upwork__send_message"):
            with self.assertRaises(mc.MCPCallError, msg=tool):
                s.call(tool, {})

    def test_every_allowlisted_tool_is_a_read(self):
        for t in mc.READ_TOOLS:
            self.assertTrue(t.startswith(("list_", "find_", "get_")), t)

    def test_wire_name_is_namespaced(self):
        """Live 400 'invalid resource name: list_accounts' on 2026-08-24 —
        callers use bare verbs, the wire wants upwork__<verb>. The mapping
        lives in call() so the allowlist and the wire can never disagree."""
        s = mc.MCPSession()
        seen = {}

        def fake_rpc(method, params=None):
            seen["name"] = params["name"]
            return {"content": [{"type": "text", "text": "{}"}]}
        s._rpc = fake_rpc
        s.call("find_jobs", {"action": "search"})
        self.assertEqual(seen["name"], "upwork__find_jobs")
        s.call("upwork__find_jobs", {})
        self.assertEqual(seen["name"], "upwork__find_jobs")


class ShadowQueries(unittest.TestCase):
    def test_the_four_test_b_gaps_are_covered(self):
        """Test B found four >=16 jobs no standing term matched; the fix was
        adding terms, and this pins them so a future edit can't quietly drop
        the coverage they bought."""
        from modules.upwork.feeds import shadow_poll as sp
        qs = " ".join(sp.TERM_QUERIES)
        for term in ("pipedrive", "activecampaign", "clay", "automated reports"):
            self.assertIn(term, qs)

    def test_n8n_is_not_a_standing_query(self):
        """The n8n EXCLUSION belongs to hard_rule_skip; searching for n8n would
        pull hundreds of sole-tool jobs just to skip them."""
        from modules.upwork.feeds import shadow_poll as sp
        self.assertNotIn("n8n", sp.TERM_QUERIES)


if __name__ == "__main__":
    unittest.main()
