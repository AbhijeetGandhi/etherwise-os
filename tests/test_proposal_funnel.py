"""The proposal funnel (Upwork tab › Funnel; migration 038).

Pins: the backfill loads the research files idempotently; the letter rules are
the research's own (parity on real letters); the reader's facts — a first open
is permanent, Upwork's all-zero counts are "not measured" not zeros, our own
bid is taken out of the "messaged" average; the money block — doctor-gated,
connects priced from the purchases that name their count, a hire's revenue is
its client's earnings from the contract month on, counted once per client;
the route answers with no forwardable params.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import db
from modules.cockpit import proposal_funnel, server
from modules.upwork import funnel_tags, proposal_letter
from tests.test_cockpit import CockpitServerCase


def _load_backfill():
    import importlib.util
    p = Path(__file__).resolve().parents[1] / "scripts" / "proposal_funnel_backfill.py"
    spec = importlib.util.spec_from_file_location("proposal_funnel_backfill", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


backfill = _load_backfill()

P1, P2, P3, P4 = ("2107000000000000001", "2107000000000000002",
                  "2107000000000000003", "2107000000000000004")
J1, J2 = "2106900000000000011", "2106900000000000022"


def _row(pid, created, kind="cold", room=False, hired=False, boosted=True, bid=120, base=20,
         rate=40.0, title="Airtable expert needed", letter="Hi,\n\nYour base is fine. I'm sure.",
         job_id=J1, **kw):
    r = {"id": pid, "created": created, "kind": kind, "room": room, "hired": hired,
         "boosted": boosted, "bid": bid if boosted else None, "connects_cost": base, "rate": rate,
         "title": title, "letter": letter, "job_id": job_id, "url": f"https://www.upwork.com/jobs/~02{job_id}?x=1",
         "status": "Hired" if hired else ("Activated" if room else "Accepted"), "reason": None,
         "room_created": "2026-09-02T10:00:00+0000" if room else None, "reply_lag_h": 5.0 if room else None,
         "job_have": True, "job_hired": 1 if hired else 0, "job_interviewed": 1 if room else 0,
         "j_type": "HOURLY", "j_exp": "EXPERT", "posted": None, "hours_to_apply": 3.0,
         "client_spend": 1000.0, "client_hire_rate": 80, "client_jobs": 5, "j_country": "United States",
         "sj_score": 30, "boost_outcome": None}
    r.update(kw)
    # the research coded every letter with the same rules: mirror that here
    f = proposal_letter.letter_features(r["letter"])
    r.update({"words": f["words"], "contractions": f["contractions"], "em_dashes": f["em_dashes"],
              "loom": bool(f["has_loom"]), "questions": f["questions"], "opener_family": f["opener_family"]})
    return r


def _chunk(pid, viewed="", total=50, opened=5, short=1, msg=2, avg=40, avgm=35, err=""):
    nums = [total, opened, short, msg, avg, avgm, 38, 90000, 50, 5, 0.98, 0.97]
    return "|".join([pid, viewed] + [str(x) for x in nums] + ["0" if viewed else "1", err])


def make_research_dir(root: Path) -> Path:
    base = root / "prop"
    (base / "cloud" / "viewed_ui_chunks").mkdir(parents=True)
    (base / "raw" / "get").mkdir(parents=True)
    rows = [
        _row(P1, "2026-09-01T10:00:00.000Z", room=True, hired=True, boost_outcome="charged"),
        _row(P2, "2026-09-03T10:00:00.000Z", room=True, letter="Hi, I have built 200+ bases."),
        _row(P3, "2026-09-05T10:00:00.000Z", boost_outcome="outbid, bid returned",
             title="AI agent developer", letter="Hello,\n\nThis is exactly what I do."),
        _row(P4, "2026-09-06T10:00:00.000Z", kind="invite", room=True, boosted=False,
             letter="Hi, thanks for the invite. I'm in."),
    ]
    (base / "rows.json").write_text(json.dumps(rows))
    (base / "raw" / "get" / f"{P1}.json").write_text(json.dumps({"room_id": "room_abc"}))
    (base / "cloud" / "viewed_ui_chunks" / "c000.json").write_text(json.dumps([
        _chunk(P1, "2026-09-01T12:00:00.000Z", msg=3, avgm=38.0),
        _chunk(P2, "2026-09-03T11:00:00.000Z", msg=1, avgm=40.0),
        _chunk(P3, ""),
        _chunk(P4, "", total=0, opened=0, short=0, msg=0, avg=0, avgm=0),
    ]))
    (base / "cloud" / "blind_slim.json").write_text(json.dumps([{"id": P1, "about": "client"},
                                                               {"id": P2, "about": "self"}]))
    (base / "cloud" / "insights_2026-10-08.json").write_text(json.dumps({
        "title": "t", "summary": "s", "window": {"lo": "2026-09-01", "hi": "2026-09-30"},
        "items": [{"id": "x", "tone": "good", "strength": "clear", "title": "T", "text": "X"}]}))
    (base / "cloud" / "invites_monthly.json").write_text(json.dumps({
        "read_at": "2026-10-08T11:10:00Z", "source": "chrome: archived interviews",
        "not_taken": {"2026-08": 3, "2026-09": 2}}))
    return base


class FunnelCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="funnel-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.base = make_research_dir(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rc = backfill.main(["--apply", "--db", str(self.db_path), "--dir", str(self.base)])
        self.assertEqual(rc, 0)

    def facts(self, green=True):
        stub = mock.Mock(green=green)
        stub.reds.return_value = [mock.Mock(invariant="t1")] if not green else []
        with mock.patch("modules.finance.doctor.run", return_value=stub):
            return proposal_funnel.funnel(self.db_path, "2026-10-08")


class TestBackfill(FunnelCase):
    def test_dry_run_writes_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            backfill.main(["--db", str(self.db_path), "--dir", str(self.base)])
        self.assertIn("letter rules disagree with the research on 0 proposal(s)", out.getvalue())
        with db.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM proposal_outcomes").fetchone()[0], 0)

    def test_apply_is_idempotent(self):
        self.load()
        self.load()
        with db.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM proposal_outcomes").fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM proposal_views").fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM proposal_insights").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM upwork_invites_monthly").fetchone()[0], 2)
            o = dict(conn.execute("SELECT * FROM proposal_outcomes WHERE proposal_id = ?", (P1,)).fetchone())
        self.assertEqual(o["room_id"], "room_abc")
        self.assertEqual(o["opening_about"], "client")
        self.assertEqual(o["connects_bid"], 120)
        self.assertEqual(o["client_did"], "hired_us")
        self.assertEqual(o["job_url"], f"https://www.upwork.com/jobs/~02{J1}")

    def test_zero_counts_are_not_measured(self):
        self.load()
        with db.connect(self.db_path) as conn:
            v = dict(conn.execute("SELECT * FROM proposal_views WHERE proposal_id = ?", (P4,)).fetchone())
        self.assertEqual(v["page_state"], "not_opened")
        self.assertIsNone(v["total_proposals"])
        self.assertIsNone(v["avg_bid"])

    def test_the_opening_is_judged_only_for_cold_letters(self):
        self.load()
        with db.connect(self.db_path) as conn:
            about = dict(conn.execute("SELECT proposal_id, opening_about FROM proposal_outcomes").fetchall())
        self.assertIsNone(about[P4])
        self.assertIsNone(about[P3])          # not in the blind set


class TestLetterRules(unittest.TestCase):
    def test_features(self):
        f = proposal_letter.letter_features("PURPLE Hi,\n\nYour setup isn't the problem. It's the dedup key."
                                            " I've fixed this. https://www.loom.com/share/x")
        self.assertEqual(f["contractions"], 3)
        self.assertEqual(f["has_loom"], 1)
        self.assertEqual(f["opener_family"], "their problem / insight first")
        self.assertTrue(f["first_lines"].startswith("Your setup isn't the problem."))

    def test_empty_letter_is_not_measured(self):
        self.assertIsNone(proposal_letter.letter_features("")["words"])

    def test_title_tags(self):
        self.assertEqual(funnel_tags.tool_in_title("Go High Level Automation Specialist"), 1)
        self.assertEqual(funnel_tags.tool_in_title("Grade Prompts and Make Suggestions"), 0)
        self.assertEqual(funnel_tags.job_category("Make.com + Airtable"), "Airtable")
        self.assertEqual(funnel_tags.job_category("Monday.com workflow"), "GoHighLevel / CRM tool")

    def test_parity_with_the_research_coding(self):
        """On the real research rows (when present on this machine) the rules
        reproduce every letter fact the report was built on."""
        rows_path = Path(__file__).resolve().parents[1] / "var" / "prop" / "rows.json"
        if not rows_path.exists():
            self.skipTest("research rows not on this machine")
        rows = json.loads(rows_path.read_text())
        bad = []
        for r in rows:
            f = proposal_letter.letter_features(r.get("letter"))
            if not (r.get("letter") or "").strip():
                continue
            for mine, theirs in (("words", "words"), ("contractions", "contractions"),
                                 ("em_dashes", "em_dashes"), ("questions", "questions"),
                                 ("opener_family", "opener_family")):
                if f[mine] != r.get(theirs):
                    bad.append((r["id"], mine))
            if f["has_loom"] != (1 if r.get("loom") else 0):
                bad.append((r["id"], "has_loom"))
        self.assertEqual(bad, [])


class TestReader(FunnelCase):
    def setUp(self):
        super().setUp()
        self.load()

    def test_not_built_without_the_tables(self):
        bare = self.tmp / "bare.db"
        import sqlite3
        sqlite3.connect(bare).close()
        out = proposal_funnel.funnel(bare, "2026-10-08")
        self.assertFalse(out["built"])
        self.assertEqual(out["facts"], [])

    def test_facts(self):
        out = self.facts()
        self.assertTrue(out["built"])
        f = {x["id"]: x for x in out["facts"]}
        self.assertEqual(len(f), 4)
        self.assertEqual(f[P1]["op"], 1)
        self.assertEqual(f[P3]["op"], 0)
        self.assertEqual(f[P1]["hto"], 2.0)
        self.assertEqual(f[P1]["pile"], 50)
        self.assertIsNone(f[P4]["pile"])                  # zeros on the page = not measured
        # our own bid comes out of the messaged average: (38*3 - 40) / 2 = 37.0
        self.assertEqual(f[P1]["avgm"], 37.0)
        self.assertIsNone(f[P2]["avgm"])                  # we were the only one messaged
        self.assertEqual(f[P3]["avgm"], 35.0)             # not messaged: the average is all others

    def test_a_first_open_is_permanent(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO proposal_views (proposal_id, read_at, page_state, viewed_at)"
                         " VALUES (?, '2026-10-09T10:00:00Z', 'not_opened', NULL)", (P1,))
        f = {x["id"]: x for x in self.facts()["facts"]}
        self.assertEqual(f[P1]["op"], 1)
        self.assertEqual(f[P1]["od"], "2026-09-01T12:00:00.000Z")
        self.assertEqual(f[P1]["pile"], 50)               # the last read that HAD counts

    def test_money_is_withheld_on_a_red_doctor(self):
        out = self.facts(green=False)
        self.assertTrue(out["money"]["blocked"])
        self.assertEqual(out["money"]["reds"], ["t1"])
        self.assertEqual(len(out["facts"]), 4)            # the funnel itself is not gated

    def test_connect_price_and_revenue(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO contracts (id, title, job_id, client_company, start_dt)"
                         " VALUES ('c1', 'Airtable work', ?, 'Acme Ltd', '2026-09-04T00:00:00Z')", (J1,))
            for i, (desc, minor) in enumerate((("300 Connects · 300 Connects fee", -4500),
                                               ("Fees for additional Connects (700).", -10500),
                                               ("Subscription Renewal Charges", -1999))):
                conn.execute(
                    "INSERT INTO transactions (record_id, type, description, amount_base_minor, creation_dt,"
                    " canonical_key, pnl_bucket, source) VALUES (?, 'Connect Purchase', ?, ?, ?, ?, 'platform_fee', 'Upwork-MCP')",
                    (f"cp{i}", desc, minor, "2026-09-1%dT00:00:00+0000" % i, f"upwork:cp{i}"))
            for i, (m, minor) in enumerate((("2026-08", 9999), ("2026-09", 50000), ("2026-10", 25000))):
                conn.execute(
                    "INSERT INTO transactions (record_id, type, description, amount_base_minor, creation_dt,"
                    " canonical_key, pnl_bucket, source, assignment_company)"
                    " VALUES (?, 'Hourly', 'work', ?, ?, ?, 'revenue', 'Upwork-MCP', 'Acme Ltd')",
                    (f"rv{i}", minor, f"{m}-20T00:00:00+0000", f"upwork:rv{i}"))
        m = self.facts()["money"]
        self.assertFalse(m["blocked"])
        self.assertEqual(m["price_per_connect"], 0.15)
        self.assertEqual(len(m["purchases"]), 3)
        w = m["won"][P1]
        self.assertEqual(w["contract_id"], "c1")
        self.assertEqual(w["usd"], 750.0)                 # Sep + Oct; August predates the contract
        self.assertEqual(len(w["rows"]), 2)


class TestRoute(CockpitServerCase):
    def test_route_answers_and_takes_no_params(self):
        import inspect
        fn = server.GET_ROUTES["/api/upwork/funnel"]
        self.assertIs(fn, proposal_funnel.funnel)
        kw = [p for p in inspect.signature(fn).parameters.values() if p.kind == p.KEYWORD_ONLY]
        self.assertEqual(kw, [])
        status, body = self.get("/api/upwork/funnel")
        self.assertEqual(status, 200, body)
        out = json.loads(body)
        self.assertIn("facts", out)
        status, body = self.get("/api/upwork/funnel?today=2020-01-01")
        self.assertNotIn("2020-01-01", body)
