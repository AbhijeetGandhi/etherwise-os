"""Phase 3 — the hiring side (his own postings, and who applied to them).

Shapes are the live capture of 2026-08-27 00:45 on the CLIENT org
(`tests/fixtures/mcp/hiring.json`, his "Yes, all four"). What these guard:

  * the postings list is PAGE-based, and it already carries the whole funnel —
    "how many applied" must never cost a second call;
  * SHORTLISTED and READ are ANNOTATIONS, not statuses: a shortlisted applicant
    is still `status.status == "Created"`, and reading shortlisting off the
    status would report six shortlisted people as nobody;
  * `bid` is a display string and the wire's rawValue is EMPTY on this side —
    the number is parsed or it is None, never 0;
  * the client-side status vocabulary is its own (Created / Activated /
    Archived) and must not be mapped through the freelancer-side map;
  * one card per open posting, with what the market pays beside what people bid.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db
from modules.chief_of_staff import rules
from modules.upwork.feeds import mcp_hiring as mh
from modules.upwork.feeds import mcp_search

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "mcp"
ORG = config.UPWORK_ORGS["client"]
LIVE_POSTING = "2090762196989822650"


def fx(name: str = "hiring") -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


class _Session:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def initialize(self):
        pass

    def call(self, tool, args):
        self.calls.append((tool, args))
        a = self.answers.get((tool, (args or {}).get("action")))
        return a(args) if callable(a) else (a if a is not None else {})

    def actions(self):
        return [(t, (a or {}).get("action")) for t, a in self.calls]


class _Ctx:
    def __init__(self, db_path):
        self.db_path = db_path
        self.logs, self.anomalies = [], []

    def log(self, m):
        self.logs.append(m)

    def anomaly(self, kind, severity, detail):
        self.anomalies.append((kind, severity, detail))


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mcp-hiring-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self._p = mock.patch.object(mcp_search, "_sleep", lambda s: None)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self, sql, args=()):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, args)]

    def session(self):
        f = fx()
        postings = json.loads(json.dumps(f["job_posting_list"]))
        postings["has_more"] = False              # one page in the fake
        return _Session({(mh.POSTING_TOOL, "list"): postings,
                         (mh.PROPOSAL_TOOL, "list"): f["client_proposals_list"],
                         (mh.RATE_TOOL, "get"): f["rate_insights"]})


class TestShape(Case):
    def test_the_funnel_comes_free_with_the_postings_list(self):
        node = fx()["job_posting_list"]["postings"][0]
        row = mh.posting_row(node)
        self.assertEqual(row["id"], LIVE_POSTING)
        self.assertEqual((row["applicants"], row["new_applicants"], row["shortlisted"],
                          row["messaged"], row["offered"], row["hired"],
                          row["pending_invitations"]), (9, 4, 6, 2, 0, 0, 2))
        self.assertEqual(row["status"], "PUBLISHED")
        self.assertEqual(row["posting_type"], "HOURLY")
        self.assertIsNone(mh.posting_row({"title": "no id"}))

    def test_shortlisted_and_read_are_annotations_not_statuses(self):
        """The one that would misreport six people as nobody: a shortlisted
        applicant's status is still 'Created'."""
        nodes = fx()["client_proposals_list"]["data"]["proposals"]
        mark = mh.applicant_row(nodes[2], LIVE_POSTING)     # Read + Shortlisted
        self.assertEqual(mark["status"], "Created")          # NOT 'Shortlisted'
        self.assertEqual((mark["shortlisted"], mark["seen_by_him"]), (1, 1))
        joseph = mh.applicant_row(nodes[0], LIVE_POSTING)
        self.assertEqual((joseph["shortlisted"], joseph["seen_by_him"]), (0, 0))
        self.assertEqual(joseph["status_label"], "Invitation accepted")
        binit = mh.applicant_row(nodes[1], LIVE_POSTING)     # no annotations key
        self.assertEqual((binit["shortlisted"], binit["seen_by_him"]), (0, 0))
        self.assertEqual(json.loads(binit["annotations_json"]), [])

    def test_the_bid_is_parsed_out_of_a_display_string_or_it_is_not_known(self):
        self.assertEqual(mh.bid_minor("USD 15.0"), 1500)
        self.assertEqual(mh.bid_minor("USD 8.67"), 867)
        self.assertEqual(mh.bid_minor("USD 1,250"), 125000)
        self.assertIsNone(mh.bid_minor(None))
        self.assertIsNone(mh.bid_minor(""))
        self.assertIsNone(mh.bid_minor("negotiable"))        # never 0
        nodes = fx()["client_proposals_list"]["data"]["proposals"]
        self.assertEqual(mh.applicant_row(nodes[3], LIVE_POSTING)["bid_minor"], 2700)

    def test_the_names_are_unwrapped_and_the_full_name_lives_on_the_org(self):
        node = fx()["client_proposals_list"]["data"]["proposals"][2]
        row = mh.applicant_row(node, LIVE_POSTING)
        self.assertEqual(row["freelancer_name"], "Mark Y.")   # Upwork abbreviates
        self.assertEqual(row["org_name"], "Mark Young")       # the full name
        self.assertNotIn("untrusted", row["cover_snippet"])
        self.assertTrue(row["cover_snippet"].startswith("Hey there"))


class TestRun(Case):
    def test_a_full_round_stores_the_postings_the_people_and_the_market(self):
        s = self.session()
        m = mh.run(_Ctx(self.db_path), session_factory=lambda: s)
        self.assertEqual(m["postings"], {"seen": 3, "inserted": 3, "updated": 0})
        self.assertEqual(m["open"], 1)                       # only PUBLISHED
        self.assertEqual(m["applicants"]["inserted"], 4)
        self.assertEqual(m["rates_read"], 1)
        # the CANCELLED and FILLED postings cost no applicant read
        detail = [a for t, a in s.calls if a.get("action") == "list"
                  and t == mh.PROPOSAL_TOOL]
        self.assertEqual({a["params"]["job_posting_id"] for a in detail}, {LIVE_POSTING})
        p = self.rows("SELECT * FROM upwork_job_postings WHERE id=?", (LIVE_POSTING,))[0]
        self.assertEqual((p["applicants"], p["new_applicants"], p["shortlisted"]), (9, 4, 6))
        self.assertEqual((p["rate_low"], p["rate_high"], p["rate_occupation"],
                          p["rate_sample"]), (15, 35, "Content Strategy", 193))
        self.assertEqual(len(self.rows("SELECT 1 FROM upwork_applicants")), 4)

    def test_a_second_round_updates_and_does_not_re_read_the_market(self):
        mh.run(_Ctx(self.db_path), session_factory=lambda: self.session())
        s2 = self.session()
        m2 = mh.run(_Ctx(self.db_path), session_factory=lambda: s2)
        self.assertEqual(m2["postings"], {"seen": 3, "inserted": 0, "updated": 3})
        self.assertEqual(m2["applicants"]["inserted"], 0)
        self.assertEqual(m2["applicants"]["updated"], 4)
        self.assertEqual(m2["rates_read"], 0)                # cached for 30 days
        self.assertNotIn((mh.RATE_TOOL, "get"), s2.actions())
        self.assertEqual(len(self.rows("SELECT 1 FROM upwork_applicants")), 4)

    def test_the_funnel_moving_is_stored_even_when_it_moves_down(self):
        """new_applicants falls to 0 the moment he opens them. A COALESCE here
        would freeze the badge at 4 for ever."""
        mh.run(_Ctx(self.db_path), session_factory=lambda: self.session())
        f = fx()
        page = json.loads(json.dumps(f["job_posting_list"]))
        page["has_more"] = False
        page["postings"][0].update({"new_applicants": 0, "shortlisted": 6, "offered": 1})
        s = _Session({(mh.POSTING_TOOL, "list"): page,
                      (mh.PROPOSAL_TOOL, "list"): f["client_proposals_list"],
                      (mh.RATE_TOOL, "get"): f["rate_insights"]})
        mh.run(_Ctx(self.db_path), session_factory=lambda: s)
        p = self.rows("SELECT * FROM upwork_job_postings WHERE id=?", (LIVE_POSTING,))[0]
        self.assertEqual((p["new_applicants"], p["offered"]), (0, 1))

    def test_paging_stops_when_the_page_number_does_not_advance(self):
        page = json.loads(json.dumps(fx()["job_posting_list"]))
        page["next_page"] = 2                       # always the same page
        s = _Session({(mh.POSTING_TOOL, "list"): page})
        got = mh.fetch_postings(s, ORG, lambda m: None)
        self.assertEqual(len(s.calls), 2)           # then the page number repeats
        self.assertEqual(len(got), 3)               # and the repeated page is deduped

    def test_a_text_answer_fails_loud(self):
        with self.assertRaises(RuntimeError):
            mh.fetch_postings(_Session({(mh.POSTING_TOOL, "list"): {"text": "boom"}}),
                              ORG, lambda m: None)
        with self.assertRaises(RuntimeError):
            mh.fetch_applicants(_Session({(mh.PROPOSAL_TOOL, "list"): {"text": "boom"}}),
                                ORG, LIVE_POSTING, lambda m: None)


class TestCard(Case):
    def test_one_card_per_open_posting_naming_the_market_and_the_bids(self):
        mh.run(_Ctx(self.db_path), session_factory=lambda: self.session())
        cards = rules.rule_applicants_waiting(self.db_path)
        self.assertEqual(len(cards), 1)                     # not the CANCELLED/FILLED ones
        c = cards[0]
        self.assertEqual(c["dedup_key"], f"applicants:{LIVE_POSTING}")
        self.assertTrue(c["title"].startswith("9 people applied to \"LinkedIn Ghostwriter"))
        self.assertIn("4 you have not opened", c["action"])
        self.assertIn("6 shortlisted", c["action"])
        self.assertIn("$15-35/hr", c["action"])
        self.assertIn("$8.00-27.00", c["action"])           # what they actually bid
        self.assertIn("Nobody has been hired yet", c["action"])
        self.assertEqual(c["payload"]["new"], 4)

    def test_the_rule_is_registered_and_survives_a_db_without_the_tables(self):
        self.assertIn(rules.rule_applicants_waiting, rules.ALL_RULES)
        self.assertEqual(rules.rule_applicants_waiting(self.tmp / "nope.db"), [])
        self.assertEqual(rules.rule_applicants_waiting(self.db_path), [])   # empty DB

    def test_config_and_wiring(self):
        from modules.cockpit import actions
        from modules.upwork.feeds import mcp_client
        self.assertEqual((config.MCP_POSTINGS_PAGE, config.MCP_POSTINGS_MAX_PAGES,
                          config.MCP_APPLICANT_MAX_POSTINGS), (10, 3, 5))
        self.assertEqual((config.MCP_RATE_INSIGHT_DAYS, config.MCP_RATE_MAX_READS), (30, 3))
        for tool in ("list_client_proposals", "list_client_invitations",
                     "get_client_dashboard"):
            self.assertIn(tool, mcp_client.READ_TOOLS)
        self.assertIn("upwork_hiring", actions.RERUN_JOBS)
        self.assertTrue((ROOT / "launchd" / "io.etherwise.v3.upwork-hiring.plist").is_file())
        self.assertTrue((ROOT / "core" / "migrations" / "034_upwork_hiring.sql").is_file())
        self.assertIn("upwork_hiring", [s["key"] for s in config.SOURCES])


if __name__ == "__main__":
    unittest.main()
