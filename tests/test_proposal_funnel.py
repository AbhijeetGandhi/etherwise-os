"""The proposal funnel (Upwork tab › Funnel; migration 038).

Pins: the backfill loads the research files idempotently; the letter rules are
the research's own (parity on real letters); the reader's facts — a first open
is permanent, Upwork's all-zero counts are "not measured" not zeros, our own
bid is taken out of the "messaged" average; the money block — doctor-gated,
connects priced from the purchases that name their count, a hire's revenue is
everything its client paid from the month we won them, counted once per client,
on every rail and under every spelling the registry knows (2026-10-08:
"globaltize hired us from upwork ... pays us through wise ... lot of agency
transactions"), a client won in a conversation Upwork does not mark hired is a
win only if they first paid after it, and nothing of that is applied on a RED
doctor; the route answers with no forwardable params.
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
        self.assertEqual((w["how"], w["via"]), ("upwork", "the contract on this job"))
        self.assertEqual(w["usd"], 750.0)                 # Sep + Oct; August predates the contract
        self.assertEqual(w["n"], 2)
        c = m["clients"][w["client"]]
        # Acme already paid through Upwork in August, before this proposal: we
        # met them on Upwork then, so the client's money starts there while the
        # hire's starts at its contract
        self.assertEqual((c["by"], c["pid"], c["first"]), ("payments", None, "2026-08-20"))
        self.assertEqual([r["usd"] for r in c["rows"]], [99.99, 500.0, 250.0])
        self.assertEqual(m["not_upwork"], [])


REG_TEXT = """clients:
  -
    id: zed-quill
    name: Zed Quill LLC
    aliases:
      - Zed Quill
      - Ana Brook
      - "Ana Brook, Ana B"
    upwork_rooms:
      - room_zed_registry
  -
    id: sim-p
    name: Sim P
    aliases:
      - SP Labs
    found_via: upwork
  -
    id: hip-h
    name: Hip H
    found_via: referral
    referred_by: sim-p
"""


def _tx(conn, rid, *, source, desc, minor, when, company=None, profile=None, kind="Hourly"):
    conn.execute(
        "INSERT INTO transactions (record_id, type, description, amount_base_minor, creation_dt,"
        " canonical_key, pnl_bucket, pnl_month, source, assignment_company, profile)"
        " VALUES (?, ?, ?, ?, ?, ?, 'revenue', ?, ?, ?, ?)",
        (rid, kind, desc, minor, when, f"{source.lower()}:{rid}", when[:7], source, company, profile))


class TestWhoseMoney(FunnelCase):
    """Abhijeet, 2026-10-08: "globaltize hired us from upwork but then we
    decided to move off upwork and pays us through wise ... lot of agency
    transactions, are you not able to see those?" One client, three spellings,
    three rails, and a win Upwork never marked: all of it is the hire's."""

    def setUp(self):
        super().setUp()
        self.load()
        from core import registry
        self.reg = registry.loads_registry(REG_TEXT)

    def run_funnel(self, green=True):
        with mock.patch.object(proposal_funnel, "_registry", return_value=self.reg):
            return self.facts(green)

    def globaltize_shape(self, conn):
        """P2's conversation is with Zed Quill. No hire on P2's job: the
        contract came later, on another job, through the agency — then they
        moved to Wise."""
        conn.execute("UPDATE proposal_outcomes SET room_id = 'room_zed' WHERE proposal_id = ?", (P2,))
        conn.execute("INSERT INTO threads (id, room_name, client_id) VALUES ('room_zed', 'Ana Brook', 'zed-quill')")
        conn.execute("INSERT INTO contracts (id, title, job_id, client_company, start_dt, profile)"
                     " VALUES ('c2', 'Zed agency work', '2106900000000000099', 'Ana Brook, Ana B',"
                     " '2026-09-10T00:00:00Z', 'agency')")
        # the agency's hourly line names the person ...
        _tx(conn, "a1", source="Upwork-MCP", profile="Agency", company="Ana Brook", minor=35000,
            desc="(c2) Ana Brook/Abhijeet Gandhi - 10:00 hrs @ $35.00/hr", when="2026-09-14T00:00:00+0000")
        # ... the agency's weekly CSV names only the job title ...
        _tx(conn, "a2", source="Upwork-CSV", profile="Agency", minor=21000,
            desc="Zed agency work · Earnings for Sep 14-Sep 20, 2026", when="2026-09-22T00:00:00+0000")
        # ... and Wise names the company
        _tx(conn, "w1", source="Wise-mail", minor=130000, kind="Wise",
            desc="Received money from Zed Quill LLC with reference INV-1", when="2026-10-02T00:00:00+0000")

    def test_one_client_on_every_rail_and_a_win_upwork_does_not_mark(self):
        with db.connect(self.db_path) as conn:
            self.globaltize_shape(conn)
        out = self.run_funnel()
        f = {x["id"]: x for x in out["facts"]}
        self.assertEqual((f[P2]["hired"], f[P2]["hw"]), (1, "after"))
        self.assertEqual((f[P1]["hired"], f[P1]["hw"]), (1, "upwork"))
        self.assertEqual((f[P3]["hired"], f[P3]["hw"]), (0, None))
        w = out["money"]["won"][P2]
        self.assertEqual((w["client"], w["name"], w["how"]), ("zed-quill", "Zed Quill LLC", "after"))
        self.assertEqual((w["contract_id"], w["from"]), ("c2", "2026-09"))
        self.assertEqual((w["usd"], w["n"]), (1860.0, 3))         # agency hourly + agency CSV + Wise
        c = out["money"]["clients"]["zed-quill"]
        self.assertEqual((c["by"], c["pid"], c["first"]), ("cold", P2, "2026-09-03"))
        self.assertEqual(sorted(r["ch"] for r in c["rows"]), ["agency", "agency", "direct"])
        self.assertEqual(set(c["aka"]), {"Ana Brook", "Ana Brook, Ana B", "Zed Quill"})

    def test_a_client_who_paid_before_the_proposal_is_not_its_win(self):
        with db.connect(self.db_path) as conn:
            self.globaltize_shape(conn)
            _tx(conn, "w0", source="Wise-mail", minor=10000, kind="Wise",
                desc="Received money from Zed Quill LLC with reference INV-0", when="2026-08-01T00:00:00+0000")
        out = self.run_funnel()
        f = {x["id"]: x for x in out["facts"]}
        self.assertEqual((f[P2]["hired"], f[P2]["hw"]), (0, None))
        self.assertNotIn(P2, out["money"]["won"])
        # still a client found on Upwork — by its agency contract, from that month
        c = out["money"]["clients"]["zed-quill"]
        self.assertEqual((c["by"], c["contract_id"], c["first"]), ("contract", "c2", "2026-09-10"))
        self.assertEqual(len(c["rows"]), 3)
        self.assertEqual([r["usd"] for r in out["money"]["not_upwork"]], [100.0])

    def test_on_a_red_doctor_only_what_upwork_marks_counts(self):
        with db.connect(self.db_path) as conn:
            self.globaltize_shape(conn)
        out = self.run_funnel(green=False)
        f = {x["id"]: x for x in out["facts"]}
        self.assertTrue(out["money"]["blocked"])
        self.assertEqual((f[P2]["hired"], f[P2]["hw"]), (0, None))
        self.assertEqual(f[P1]["hw"], "upwork")

    def test_a_hire_with_no_contract_is_found_by_its_conversation(self):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO threads (id, room_name, client_id)"
                         " VALUES ('room_abc', 'Ana Brook', 'zed-quill')")
            _tx(conn, "w2", source="Wise", minor=50000, kind="Wise",
                desc="Received money from Ana Brook with reference X", when="2026-09-20T00:00:00+0000")
            _tx(conn, "s1", source="Stripe", minor=999, company="Northwind Traders", kind="Stripe",
                desc="Charge", when="2026-09-21T00:00:00+0000")
        m = self.run_funnel()["money"]
        w = m["won"][P1]
        self.assertEqual((w["client"], w["via"], w["usd"]), ("zed-quill", "the client in this conversation", 500.0))
        self.assertIsNone(w["contract_id"])
        self.assertEqual([r["as"] for r in m["not_upwork"]], ["Northwind Traders"])   # never met on Upwork

    def test_what_he_said_beats_what_the_records_show(self):
        """Abhijeet, 2026-10-08: "simyon pinsky is someone we found through
        upwork then our engagement closed and we started again after few month
        outside upwork" (no Upwork trace in these records) and "hipp health is
        also someone we found through alex" (a referral)."""
        with db.connect(self.db_path) as conn:
            _tx(conn, "s1", source="Wise", minor=50000, kind="Wise",
                desc="Received money from SP Labs with reference MercuryACH", when="2024-12-17T00:00:00+0000")
            _tx(conn, "s2", source="Wise", minor=82475, kind="Wise",
                desc="Received money from SP Labs with reference MercuryACH", when="2025-04-23T00:00:00+0000")
            _tx(conn, "h1", source="Wise", minor=20000, kind="Wise",
                desc="Received money from Hip H with reference INV1", when="2025-09-02T00:00:00+0000")
            # even an Upwork payment does not make a referral Upwork money
            _tx(conn, "h2", source="Upwork-MCP", minor=10000, company="Hip H",
                desc="(9) Hip H - 2:00 hrs", when="2025-09-09T00:00:00+0000")
        m = self.run_funnel()["money"]
        c = m["clients"]["sim-p"]
        self.assertEqual((c["by"], c["first"], len(c["rows"])), ("declared", "2024-12-17", 2))
        self.assertNotIn("hip-h", m["clients"])
        out = [(r["who"], r["usd"], r["origin"]) for r in m["not_upwork"]]
        self.assertEqual(out, [("Hip H", 200.0, {"found_via": "referral", "referred_by": "Sim P"}),
                               ("Hip H", 100.0, {"found_via": "referral", "referred_by": "Sim P"})])

    def test_the_registry_knows_a_room_the_threads_do_not(self):
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE proposal_outcomes SET room_id = 'room_zed_registry' WHERE proposal_id = ?", (P2,))
            _tx(conn, "w3", source="Wise-mail", minor=20000, kind="Wise",
                desc="Received money from Zed Quill with reference INV-9", when="2026-09-30T00:00:00+0000")
        out = self.run_funnel()
        self.assertEqual(out["money"]["won"][P2]["usd"], 200.0)
        self.assertEqual({x["id"]: x["hw"] for x in out["facts"]}[P2], "after")


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
