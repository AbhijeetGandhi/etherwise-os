"""The Upwork tab reader + drills (Phase 1, docs/upwork/PHASE1_TAB_SPEC.md).

Pins the four decisions of 2026-08-25 (brief decision 10): the job-COHORT
funnel with its closure rule, interviewed = Activated/Hired, won = a contract
on the job, connects spent = applications + boosts net of boost refunds; the
"—" tiles for the stages without a spine row; the parity of every figure with
its drill; the shared card builder feeding Today and the tab; the rails strip
with `built: False` rows; the query-param guard on the new route.

Amended 2026-08-26 ("0 won this month is incorrect"): the Interviewing and
Won tiles count what HAPPENED in the period from wherever it came (an
INTERVIEW room a client opened; a contract that started), split by source;
the cohort's own share stays as `cohort_n`; a cohort job is also interviewed
when an INTERVIEW room exists on its proposal and won when the same client
hired us on another posting after we applied.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from pathlib import Path

from core import config, db
from modules.cockpit import data, server
from tests.test_cockpit import CockpitServerCase, seed_today

ROOT = Path(__file__).resolve().parents[1]
TODAY = "2026-08-26"            # a Wednesday; the current week is Mon 08-24
WEEK = "2026-08-24"
MONTH = "2026-08"

# one synthetic cohort in the current week + one matured cohort four weeks
# earlier for the baseline. Realistic 19-digit ids (the fabricated-id guard).
J = [f"20669000000000{n:05d}" for n in range(1, 40)]


def _job(conn, jid, day, score=None, skip=None, status="Scored", carded=False,
         draft=False, loom=False):
    conn.execute(
        "INSERT INTO scored_jobs (id, title, score, hard_rule_skip, status,"
        " clickup_task_id, clickup_task_url, draft_proposal, loom_flag,"
        " fetched_at, first_scored_at, job_url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (jid, f"job {jid[-3:]}", score, skip, status,
         f"cu{jid[-3:]}" if carded else None,
         f"https://app.clickup.com/t/cu{jid[-3:]}" if carded else None,
         "Drafted proposal text." if draft else None, 1 if loom else 0,
         f"{day}T10:00:00+00:00", f"{day} 10:05:00",
         f"https://www.upwork.com/jobs/x_~02{jid}/"))


def seed_cohort(db_path):
    hot, loom = config.HOT_LEAD_THRESHOLD, config.LOOM_FLAG_THRESHOLD
    with db.connect(db_path) as conn:
        # ── the current-week cohort (fetched 08-24 / 08-25) ──────────────────
        # 12 fetched: 4 killed by hard rules, 8 passed; of the 8: 5 hot
        # (2 of them Loom), 3 standard; 3 applied by the rail (2 hot, 1 not),
        # 2 more applied by CLOSURE (a Hired proposal with no rail row, a
        # contract with no proposal at all), 3 interviewed (J7 Activated, J10
        # Hired, J6 inferred from its contract), 2 won.
        for i in range(4):                               # hard-rule kills
            _job(conn, J[i], "2026-08-24", score=None, skip="n8n_exclusion",
                 status="Skipped")
        for i in range(4, 7):                            # standard 12-19
            _job(conn, J[i], "2026-08-24", score=hot - 2, status="ClickUp Created",
                 carded=True)
        for i in range(7, 10):                           # hot, carded, drafted
            _job(conn, J[i], "2026-08-25", score=hot, status="ClickUp Created",
                 carded=True, draft=True)
        for i in range(10, 12):                          # loom, one missed the push
            _job(conn, J[i], "2026-08-25", score=loom + 2,
                 status="Drafted" if i == 11 else "ClickUp Created",
                 carded=(i != 11), draft=True, loom=True)
        # rail: applied to J[7], J[8] (hot) and J[4] (standard)
        for n, jid in enumerate((J[7], J[8], J[4])):
            conn.execute(
                "INSERT INTO connects_history (id, event_date, kind, label,"
                " job_id, job_title, connects_delta) VALUES (?,?,?,?,?,?,?)",
                (f"ap{n}", "2026-08-25", "applied", "Applied to a job", jid,
                 "t", -16))
        # boosts on J[7] (+ a refund), badge, a purchase
        conn.execute("INSERT INTO connects_history (id, event_date, kind, label,"
                     " job_id, connects_delta) VALUES ('bo1','2026-08-25','boost',"
                     " 'Boosted proposal', ?, -100)", (J[7],))
        conn.execute("INSERT INTO connects_history (id, event_date, kind, label,"
                     " job_id, connects_delta) VALUES ('br1','2026-08-25',"
                     " 'boost_refund','Boost refund', ?, 40)", (J[7],))
        conn.execute("INSERT INTO connects_history (id, event_date, kind, label,"
                     " connects_delta) VALUES ('bd1','2026-08-25','badge',"
                     " 'Availability Badge', -1)")
        conn.execute("INSERT INTO connects_history (id, event_date, kind, label,"
                     " connects_delta) VALUES ('pu1','2026-08-25','other',"
                     " 'Added extra Connects', 300)")
        # proposals: J[7] interviewing (rail-applied), J[10] Hired (contract,
        # no rail row -> applied + interviewed by closure), J[9] Archived
        for pid, jid, ust in (("pr7", J[7], "Activated"), ("pr10", J[10], "Hired"),
                              ("pr9", J[9], "Archived")):
            conn.execute(
                "INSERT INTO proposals (id, marketplace_job_id, upwork_status,"
                " job_title, created_dt, thread_id) VALUES (?,?,?,?,?,?)",
                (pid, jid, ust, "t", "2026-08-25T12:00:00+0000", f"room_{pid}"))
        conn.execute(
            "INSERT INTO contracts (id, job_id, title, upwork_status, start_dt,"
            " contract_type, hourly_rate) VALUES ('ct10', ?, 'won job', 'ACTIVE',"
            " '2026-08-25T15:00:00+0000', 'HOURLY', 60)", (J[10],))
        # J6 (standard, never applied per the rail, no proposal row) got a
        # contract: applied AND interviewed by closure, both inferred from 'won'
        conn.execute(
            "INSERT INTO contracts (id, job_id, title, upwork_status, start_dt,"
            " contract_type) VALUES ('ct6', ?, 'quiet hire', 'ACTIVE',"
            " '2026-08-25T16:00:00+0000', 'FIXED')", (J[6],))
        # a contract started this week whose job was NEVER fetched (outside)
        conn.execute(
            "INSERT INTO contracts (id, job_id, title, upwork_status, start_dt,"
            " contract_type, client_company) VALUES ('ctX', '2066900000000099999',"
            " 'direct hire', 'ACTIVE', '2026-08-24T15:00:00+0000', 'FIXED', 'Xavier X')")
        # the rooms Upwork opens when a client talks: one per proposal that
        # got an answer (J7 Activated, J10 Hired, J9 Archived AFTER a
        # conversation), one for the direct hire (ctX, no proposal), one for
        # the quiet hire (ct6); Dhanya's old room from June must not count
        for tid, ptype, pid, cid, name, first in (
                ("room_pr7", "INTERVIEW", "pr7", None, "Paula P", "2026-08-25T13:00:00Z"),
                ("room_pr10", "INTERVIEW", "pr10", "ct10", "Tina T", "2026-08-25T14:00:00Z"),
                ("room_pr9", "INTERVIEW", "pr9", None, "Nina N", "2026-08-25T13:30:00Z"),
                ("room_ctX", "INTERVIEW", None, "ctX", "Xavier X", "2026-08-24T14:00:00Z"),
                ("room_ct6", "INTERVIEW", None, "ct6", "Sixto S", "2026-08-25T15:30:00Z"),
                ("room_old", "INTERVIEW", None, None, "Olga O", "2026-06-29T10:00:00Z")):
            conn.execute(
                "INSERT INTO threads (id, room_name, room_type, proposal_id,"
                " contract_id, first_seen_at) VALUES (?,?,?,?,?,?)",
                (tid, name, ptype, pid, cid, first[:19].replace("T", " ")))
            conn.execute(
                "INSERT INTO messages (id, thread_id, message, created_dt,"
                " sender_user_name, direction) VALUES (?,?,?,?,?,?)",
                (f"m_{tid}", tid, "hello", first, name, "inbound"))
        conn.execute("UPDATE proposals SET client_company = 'Nina N' WHERE id='pr9'")
        # ── a matured baseline cohort, four weeks earlier (07-27 .. 07-28) ──
        # 10 fetched, all passed, 5 hot, 2 applied (rail), 1 interviewed, 0 won
        for i in range(12, 22):
            _job(conn, J[i], "2026-07-27", score=hot if i < 17 else hot - 3,
                 status="ClickUp Created", carded=True, draft=(i < 17))
        for n, jid in enumerate((J[12], J[13])):
            conn.execute(
                "INSERT INTO connects_history (id, event_date, kind, label,"
                " job_id, connects_delta) VALUES (?,?,?,?,?,?)",
                (f"bp{n}", "2026-07-27", "applied", "Applied to a job", jid, -16))
        conn.execute(
            "INSERT INTO proposals (id, marketplace_job_id, upwork_status,"
            " created_dt) VALUES ('prb', ?, 'Activated', '2026-07-29T12:00:00+0000')",
            (J[12],))
        # ── a PRE-RAIL cohort (06-01): must never enter the outcome baselines ─
        for i in range(22, 27):
            _job(conn, J[i], "2026-06-01", score=hot, status="ClickUp Created",
                 carded=True)


class UpworkTabCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="upwork-tab-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed_cohort(self.db_path)
        self.out = data.upwork(self.db_path, TODAY)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def stage(self, view, k):
        return next(s for s in self.out["funnel"][view]["stages"] if s["k"] == k)

    def rows(self, **kw):
        kw.setdefault("_today", TODAY)
        return data.upwork_rows(self.db_path, **kw)


class TestUpworkReader(UpworkTabCase):
    def test_payload_keys_are_pinned(self):
        self.assertEqual(set(self.out), {"as_of", "bands", "summary", "inbox",
                                         "needs_me", "funnel", "rails", "connects",
                                         "digest"})
        self.assertEqual(self.out["as_of"], TODAY)
        self.assertEqual(set(self.out["funnel"]), {"week", "month"})
        self.assertFalse(self.out["digest"]["built"])

    def test_band_labels_come_from_config(self):
        b = self.out["bands"]
        self.assertEqual(b["hot"], "Hot " + config.score_band_label("hot"))
        self.assertEqual(b["loom"], "Loom " + config.score_band_label("loom"))
        self.assertEqual(b["hot_cut"], config.HOT_LEAD_THRESHOLD)
        with mock.patch.object(config, "HOT_LEAD_THRESHOLD",
                               config.LOOM_FLAG_THRESHOLD - 1):
            out = data.upwork(self.db_path, TODAY)
            self.assertEqual(out["bands"]["hot"],
                             f"Hot {config.LOOM_FLAG_THRESHOLD - 1}-{config.LOOM_FLAG_THRESHOLD - 1}")

    def test_the_eight_stages_in_the_briefs_order(self):
        ks = [s["k"] for s in self.out["funnel"]["week"]["stages"]]
        self.assertEqual(ks, ["fetched", "passed", "hot", "applied", "viewed",
                              "interviewed", "offer", "won"])

    def test_cohort_counts_this_week(self):
        w = self.out["funnel"]["week"]
        self.assertEqual(w["period"]["key"], WEEK)
        self.assertTrue(w["period"]["so_far"])
        n = {s["k"]: s["n"] for s in w["stages"]}
        self.assertEqual(n["fetched"], 12)
        self.assertEqual(n["passed"], 8)
        self.assertEqual(n["hot"], 5)
        # applied: 3 by the rail + 3 by closure (J10 Hired, J6 contract, J9's
        # room — the client talked, then archived the proposal)
        self.assertEqual(n["applied"], 6)
        # the last two tiles count what HAPPENED this week, from wherever:
        # 5 people started talking (J7, J9, J10, the direct hire, the quiet
        # hire — Olga's June room does not count), 3 contracts started
        self.assertEqual(n["interviewed"], 5)
        self.assertEqual(n["won"], 3)
        # the feed's own share of each (the cohort): J7, J9, J10, J6 / J10, J6
        self.assertEqual(self.stage("week", "interviewed")["cohort_n"], 4)
        self.assertEqual(self.stage("week", "won")["cohort_n"], 2)
        self.assertEqual(self.stage("week", "interviewed")["sources"],
                         {"this": 4, "earlier": 0, "repeat": 0, "outside": 1})
        self.assertEqual(self.stage("week", "won")["sources"],
                         {"this": 2, "earlier": 0, "repeat": 0, "outside": 1})

    def test_closure_makes_the_outcome_chain_nested_and_says_what_it_inferred(self):
        applied = self.stage("week", "applied")
        # since 0.2 (26 Aug) a proposals row IS the application, so J10 and J9
        # (proposal rows, no rail row) are observed, not inferred; J6 (a
        # contract, no proposal at all) is the one closure still infers
        self.assertEqual(applied["inferred"], 1)
        self.assertEqual(applied["hot_split"], {"hot": 4, "not_hot": 2})
        self.assertEqual(self.stage("week", "interviewed")["inferred"], 1)   # J6
        # inside the cohort every later stage is a subset of the earlier one
        n = {s["k"]: s.get("cohort_n", s["n"])
             for s in self.out["funnel"]["week"]["stages"] if s["measured"]}
        self.assertLessEqual(n["won"], n["interviewed"])
        self.assertLessEqual(n["interviewed"], n["applied"])
        self.assertLessEqual(n["applied"], n["fetched"])
        rows = self.rows(stage="applied", week=WEEK)["rows"]
        byid = {r["id"]: r for r in rows}
        self.assertIsNone(byid[J[10]]["inferred"])                  # its proposal row is the application
        self.assertEqual(byid[J[6]]["inferred"], "won")             # its contract
        self.assertTrue(byid[J[6]]["interview_inferred"])
        self.assertIsNone(byid[J[7]]["inferred"])
        self.assertEqual(byid[J[7]]["applied_on"], "2026-08-25")
        self.assertEqual(byid[J[6]]["contract_id"], "ct6")
        self.assertEqual(byid[J[10]]["upwork_status"], "Hired")

    def test_hot_sub_lines(self):
        hot = self.stage("week", "hot")
        self.assertEqual(hot["label"], f"Hot ≥{config.HOT_LEAD_THRESHOLD}")
        self.assertEqual(hot["loom"], 2)
        self.assertEqual(hot["carded"], 4)
        self.assertEqual(hot["missed_push"], 1)     # J11: Drafted, no card
        self.assertEqual(self.rows(stage="hot_missed_push", week=WEEK)["total"], 1)

    def test_unmeasured_stages_render_a_dash_with_the_reason_and_no_drill(self):
        for k in ("viewed", "offer"):
            st = self.stage("week", k)
            self.assertFalse(st["measured"])
            self.assertIsNone(st["n"])                 # never a fabricated 0
            self.assertTrue(st["reason"])
            self.assertIsNone(st["drill"])
            r = self.rows(stage=k, week=WEEK)
            self.assertIn("not measured", r["error"])
            self.assertIsNone(r["total"])

    def test_conversion_chain_skips_the_unmeasured_stages(self):
        inter = self.stage("week", "interviewed")
        self.assertEqual(inter["of_prev"]["k"], "applied")   # not 'viewed'
        won = self.stage("week", "won")
        self.assertEqual(won["of_prev"]["k"], "interviewed")  # not 'offer'
        self.assertEqual(won["of_prev"]["pct"], 50.0)         # cohort: 2 of 4

    def test_applied_step_is_of_the_hot_jobs(self):
        applied = self.stage("week", "applied")
        self.assertEqual(applied["of_prev"]["k"], "hot")
        self.assertEqual(applied["of_prev"]["n_num"], 4)      # applied ∧ hot
        self.assertEqual(applied["of_prev"]["n_den"], 5)
        self.assertEqual(applied["of_prev"]["pct"], 80.0)

    def test_baseline_is_pooled_over_completed_weeks_and_rail_covered_only(self):
        w = self.out["funnel"]["week"]
        self.assertEqual(w["baseline"]["periods"], config.UPWORK_BASELINE_WEEKS)
        self.assertEqual(w["baseline"]["rail_since"], "2026-07-27")
        # 8 weeks before 08-24 = 06-29 .. 08-23: the 07-27 cohort is in, the
        # 06-01 pre-rail cohort is out of the window entirely
        self.assertEqual(w["baseline"]["cohort_n"], 10)
        hot = self.stage("week", "hot")
        self.assertEqual(hot["baseline"]["n_den"], 10)         # passed, pooled
        self.assertEqual(hot["baseline"]["pct"], 50.0)
        applied = self.stage("week", "applied")
        self.assertEqual(applied["baseline"]["n_den"], 5)      # hot, covered
        self.assertEqual(applied["baseline"]["pct"], 40.0)     # 2 of 5
        self.assertEqual(applied["delta_pp"], 40.0)            # 80 - 40

    def test_pre_rail_cohorts_never_enter_an_outcome_baseline(self):
        # the month view's 3 completed months (May-Jul) include the 06-01
        # cohort: it counts for the scoring steps, never for applied/won
        m = self.out["funnel"]["month"]
        self.assertEqual(m["baseline"]["cohort_n"], 15)         # 10 + 5
        self.assertEqual(m["baseline"]["cohort_n_covered"], 10)
        hot = self.stage("month", "hot")
        self.assertEqual(hot["baseline"]["n_den"], 15)
        applied = self.stage("month", "applied")
        self.assertEqual(applied["baseline"]["n_den"], 5)       # hot AND covered
        self.assertEqual(applied["baseline"]["covered_since"], "2026-07-27")

    def test_no_rail_at_all_means_no_outcome_baseline_not_a_zero(self):
        with db.connect(self.db_path) as conn:
            conn.execute("DELETE FROM connects_history")
        out = data.upwork(self.db_path, TODAY)
        applied = next(s for s in out["funnel"]["week"]["stages"] if s["k"] == "applied")
        self.assertIsNone(applied["baseline"]["pct"])
        self.assertIsNone(applied["delta_pp"])
        self.assertIsNone(out["funnel"]["week"]["baseline"]["rail_since"])
        # closure still counts the proposals' and contracts' jobs as applied
        self.assertEqual(applied["n"], 4)                        # J7, J9, J10, J6

    def test_small_n_is_anecdote_on_either_side(self):
        # every step here is under the small-N line: no SIGNAL anywhere
        for s in self.out["funnel"]["week"]["stages"]:
            if s["measured"] and s.get("of_prev"):
                self.assertFalse(s["signal"], s["k"])
        with mock.patch.object(config, "UPWORK_SIGNAL_MIN_N", 5):
            out = data.upwork(self.db_path, TODAY)
            hot = next(s for s in out["funnel"]["week"]["stages"] if s["k"] == "hot")
            self.assertTrue(hot["signal"])                       # 8 and 10 >= 5
            won = next(s for s in out["funnel"]["week"]["stages"] if s["k"] == "won")
            self.assertFalse(won["signal"])                      # 4 and 1 < 5

    def test_contracts_started_line_names_the_wins_outside_the_funnel(self):
        c = self.out["funnel"]["week"]["contracts_started"]
        self.assertEqual((c["total"], c["this"], c["earlier"], c["outside"]),
                         (3, 2, 0, 1))
        rows = self.rows(stage="contracts_started", week=WEEK)
        self.assertEqual(rows["total"], 3)
        tags = {r["id"]: r["cohort"] for r in rows["rows"]}
        self.assertEqual(tags, {"ct10": "this", "ct6": "this", "ctX": "outside"})
        # the Won tile reads the same rows, and names them
        self.assertEqual(self.rows(stage="won", week=WEEK)["total"], 3)
        won = self.stage("week", "won")
        self.assertEqual([n["who"] for n in won["names"]], ["quiet hire", "won job", "Xavier X"])
        self.assertEqual([n["cohort"] for n in won["names"]], ["this", "this", "outside"])

    def test_interviews_are_the_rooms_clients_opened_this_period(self):
        """Abhijeet, 2026-08-26: "I know we had more than 1 interview this
        week" — the tile counts every INTERVIEW room a client opened in the
        period (a reply, an invitation, an offer), one per person, tagged
        with where it came from; an old room that is still active does not
        count as new; a proposal the client archived AFTER talking still was
        an interview."""
        iv = self.stage("week", "interviewed")
        self.assertEqual(iv["n"], 5)
        names = {n["who"]: n for n in iv["names"]}
        self.assertEqual(set(names), {"Paula P", "Tina T", "Nina N", "Xavier X", "Sixto S"})
        self.assertEqual(names["Xavier X"]["cohort"], "outside")
        self.assertEqual(names["Tina T"]["contract_start"], "2026-08-25")   # → hired
        self.assertIsNone(names["Paula P"]["contract_start"])
        rows = self.rows(stage="interviewed", week=WEEK)
        self.assertEqual(rows["total"], 5)
        by = {r["who"]: r for r in rows["rows"]}
        self.assertEqual(by["Nina N"]["upwork_status"], "Archived")
        self.assertEqual(by["Nina N"]["job_id"], J[9])
        self.assertEqual(by["Nina N"]["cohort"], "this")
        self.assertTrue(by["Paula P"]["url"].endswith("room_pr7/"))
        self.assertEqual(self.out["funnel"]["week"]["interviews_started"]["total"], 5)
        # the month view: the same five (all in August)
        self.assertEqual(self.stage("month", "interviewed")["n"], 5)

    def test_a_client_who_hires_on_a_new_posting_is_a_win_of_the_job_we_applied_to(self):
        """Royan Stomberg, 17 Aug: our proposal sat on job A (fetched 13 Aug);
        the client hired us on a fresh posting B the feed never saw. The
        contract carries B, so the cohort of A showed 0 won and the contract
        was "outside the funnel". The same-client join fixes both."""
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE proposals SET client_company = 'Roy R',"
                         " upwork_status = 'Archived' WHERE id = 'pr7'")
            conn.execute(
                "INSERT INTO contracts (id, job_id, title, upwork_status, start_dt,"
                " contract_type, client_company) VALUES ('ctR', '2066900000000088888',"
                " 'new posting hire', 'ACTIVE', '2026-08-26T09:00:00+0000', 'HOURLY', 'Roy R')")
        out = data.upwork(self.db_path, TODAY)
        won = next(s for s in out["funnel"]["week"]["stages"] if s["k"] == "won")
        self.assertEqual(won["n"], 4)
        self.assertEqual(won["cohort_n"], 3)                 # J10, J6, J7 via the client
        self.assertEqual(won["sources"]["this"], 3)
        rows = self.rows(stage="cohort_won", week=WEEK)["rows"]
        j7 = next(r for r in rows if r["id"] == J[7])
        self.assertEqual(j7["won_via"], "client")
        self.assertEqual(j7["contract_id"], "ctR")            # named in the drill
        # the contract row says which job it came through, and how
        crow = next(r for r in self.rows(stage="won", week=WEEK)["rows"] if r["id"] == "ctR")
        self.assertEqual((crow["cohort"], crow["via"], crow["via_job_id"]), ("this", "client", J[7]))
        # a client with no proposal of ours on any fetched job stays outside
        ctx = next(r for r in self.rows(stage="won", week=WEEK)["rows"] if r["id"] == "ctX")
        self.assertEqual(ctx["cohort"], "outside")

    def test_a_client_who_comes_back_is_a_repeat_not_the_feed(self):
        """Rocky Choy, 24 Aug: a June contract, then a new one in August with
        no new job of ours behind it — a repeat client, not "from the jobs of
        June" and not "came to us"."""
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO contracts (id, job_id, title, upwork_status, start_dt,"
                " contract_type, client_company) VALUES ('ctX0', NULL, 'the first one',"
                " 'ENDED', '2026-06-10T09:00:00+0000', 'FIXED', 'Xavier X')")
        out = data.upwork(self.db_path, TODAY)
        won = next(s for s in out["funnel"]["week"]["stages"] if s["k"] == "won")
        self.assertEqual(won["sources"], {"this": 2, "earlier": 0, "repeat": 1, "outside": 0})
        self.assertEqual(next(n for n in won["names"] if n["who"] == "Xavier X")["via"], "repeat")
        iv = next(s for s in out["funnel"]["week"]["stages"] if s["k"] == "interviewed")
        self.assertEqual(iv["sources"]["repeat"], 1)

    def test_month_view_uses_the_calendar_month(self):
        m = self.out["funnel"]["month"]
        self.assertEqual(m["period"]["key"], MONTH)
        self.assertEqual((m["period"]["lo"], m["period"]["hi"]),
                         ("2026-08-01", "2026-08-31"))
        self.assertEqual(m["baseline"]["periods"], config.UPWORK_BASELINE_MONTHS)
        self.assertEqual(self.stage("month", "fetched")["n"], 12)


class TestUpworkParity(UpworkTabCase):
    def test_every_measured_stage_equals_its_drill_in_both_views(self):
        for view, key in (("week", WEEK), ("month", MONTH)):
            for s in self.out["funnel"][view]["stages"]:
                if not s["measured"]:
                    continue
                r = self.rows(stage=s["k"], **{view: key})
                self.assertEqual(r["total"], s["n"], (view, s["k"]))
                self.assertEqual(r["count"], s["n"], (view, s["k"]))
                self.assertEqual(s["drill"], {"stage": s["k"], view: key})
                if "cohort_n" in s:                 # the feed's share has its own drill
                    rc = self.rows(stage="cohort_" + s["k"], **{view: key})
                    self.assertEqual(rc["total"], s["cohort_n"], (view, s["k"]))

    def test_hot_sublines_equal_their_drills(self):
        hot = self.stage("week", "hot")
        self.assertEqual(self.rows(stage="hot_missed_push", week=WEEK)["total"],
                         hot["missed_push"])

    def test_connects_tile_equals_the_sum_of_its_rows(self):
        wk = self.out["connects"]["week"]
        r = self.rows(stage="connects", frm=wk["lo"], to=wk["hi"])
        by_kind = {}
        for row in r["rows"]:
            by_kind.setdefault(row["kind"], []).append(row["connects_delta"])
        self.assertEqual(-sum(by_kind["applied"]), wk["applied_cost"])
        self.assertEqual(-sum(by_kind["boost"]), wk["boost_cost"])
        self.assertEqual(sum(by_kind["boost_refund"]), wk["boost_refunds"])
        self.assertEqual(wk["spent_net"],
                         wk["applied_cost"] + wk["boost_cost"] - wk["boost_refunds"])
        self.assertEqual(wk["applications"], len(by_kind["applied"]))
        kind_only = self.rows(stage="connects", frm=wk["lo"], to=wk["hi"], kind="boost")
        self.assertEqual(kind_only["total"], 1)

    def test_loom_flags_tile_equals_its_drill(self):
        lf = self.out["needs_me"]["loom_flags"]
        r = self.rows(stage="loom_flags")
        self.assertEqual(r["total"], lf["count"])
        # J10 (Hired, but the rail never saw an application) + J11 (missed push);
        # the baseline cohort's flagged jobs are outside the 7-day window
        self.assertEqual({row["id"] for row in r["rows"]}, {J[10], J[11]})
        self.assertEqual(lf["days"], config.COS_HOTLEAD_RECENT_DAYS)
        self.assertEqual(lf["icp"], 0)

    def test_drafts_waiting_equals_its_drill(self):
        dw = self.out["needs_me"]["drafts_waiting"]
        r = self.rows(stage="drafts_waiting")
        self.assertEqual(r["total"], dw["count"])
        # hot + drafted + not applied + fresh: J9 (Archived proposal, never
        # applied per the rail), J11 (missed push); J7/J8 applied, J10 no rail
        # row but... no: J10 has no rail row so it counts too
        self.assertEqual({row["id"] for row in r["rows"]}, {J[9], J[10], J[11]})


class TestUpworkConnects(UpworkTabCase):
    def test_spent_is_applications_plus_boosts_net_of_refunds(self):
        wk = self.out["connects"]["week"]
        self.assertEqual(wk["applications"], 3)
        self.assertEqual(wk["applied_cost"], 48)
        self.assertEqual(wk["boost_cost"], 100)
        self.assertEqual(wk["boost_refunds"], 40)
        self.assertEqual(wk["spent_net"], 108)
        self.assertEqual(wk["badge"], 1)                     # its own line
        self.assertEqual(wk["inflows"], 300)                 # never inside spent
        self.assertEqual(wk["per_application"], 36.0)
        self.assertEqual(wk["interviews"], 2)                # pr7 + pr10 this week
        self.assertEqual(wk["per_interview"], 54.0)

    def test_zero_applications_is_a_dash_not_a_division(self):
        with db.connect(self.db_path) as conn:
            conn.execute("DELETE FROM connects_history WHERE kind='applied'")
        out = data.upwork(self.db_path, TODAY)
        wk = out["connects"]["week"]
        self.assertEqual(wk["applications"], 0)
        self.assertIsNone(wk["per_application"])

    def test_connects_carry_the_rail_verdict(self):
        src = self.out["connects"]["source"]
        self.assertEqual(src["key"], "connects_rail")
        self.assertEqual(src["status"], "down")              # never ran in the fixture


class TestUpworkRails(UpworkTabCase):
    def test_the_briefs_rails_in_order_with_not_built_rows(self):
        keys = [r["key"] for r in self.out["rails"]["items"]]
        self.assertEqual(keys, ["scanner", "proposals_sync",
                                "messages_sync", "offers_sync", "applied",
                                "outcome_email", "clickup_push", "cards_sweep"])
        offers = next(r for r in self.out["rails"]["items"] if r["key"] == "offers_sync")
        self.assertFalse(offers["built"])
        self.assertIsNone(offers["status"])
        self.assertIn("0.5", offers["reason"])
        self.assertEqual(self.out["rails"]["not_built"], ["offers_sync"])

    def test_a_rail_that_never_ran_is_down_and_the_strip_says_so(self):
        scanner = next(r for r in self.out["rails"]["items"] if r["key"] == "scanner")
        self.assertEqual(scanner["status"], "down")
        self.assertEqual(scanner["task_name"], "upwork_scan")
        self.assertTrue(scanner["rerun"])
        self.assertEqual(self.out["rails"]["worst"], "down")

    def test_latest_run_result_and_rerun_follow_the_registry(self):
        with db.connect(self.db_path) as conn:
            now = datetime.now(config.TZ).isoformat()
            conn.execute("INSERT INTO runs (task_name, started_at, completed_at,"
                         " status, shadow, metrics_json) VALUES ('upwork_inbox',"
                         " ?, ?, 'completed', 0, ?)",
                         (now, now, json.dumps({"threads": 12, "new_messages": 3})))
        out = data.upwork(self.db_path, TODAY)
        inbox = next(r for r in out["rails"]["items"] if r["key"] == "messages_sync")
        self.assertEqual(inbox["status"], "ok")
        self.assertIn("threads 12", inbox["result"])
        self.assertTrue(inbox["rerun"])
        outcome = next(r for r in out["rails"]["items"] if r["key"] == "outcome_email")
        # 2026-08-27: this one has a button now. It SENDS, which is why it had
        # none, but outcome_capture.already_sent() refuses a second send inside
        # one ISO week — so a kick can only deliver a week that was missed
        # (19 Jul and 23 Aug both were), never a duplicate.
        self.assertTrue(outcome["rerun"])

    def test_clickup_line_is_derived_from_the_scan_and_the_missed_pushes(self):
        cu = next(r for r in self.out["rails"]["items"] if r["key"] == "clickup_push")
        self.assertTrue(cu["derived"])
        self.assertEqual(cu["missed_push"], 1)               # J11, inside the window
        self.assertEqual(cu["status"], "down")               # no completed scan yet
        with db.connect(self.db_path) as conn:
            now = datetime.now(config.TZ).isoformat()
            conn.execute("INSERT INTO runs (task_name, started_at, completed_at,"
                         " status, shadow, metrics_json) VALUES ('upwork_scan',"
                         " ?, ?, 'completed', 0, ?)",
                         (now, now, json.dumps({"clickup_pushed": 4,
                                                "clickup_retried": 1,
                                                "clickup_push_failed": 0})))
        cu = next(r for r in data.upwork(self.db_path, TODAY)["rails"]["items"]
                  if r["key"] == "clickup_push")
        self.assertEqual(cu["result"], "4 carded · 1 retried")
        self.assertEqual(cu["status"], "degraded")           # a missed push remains
        self.assertIn("next scan retries", cu["reason"])

    def test_a_source_missing_from_config_is_down_with_the_reason(self):
        trimmed = tuple(s for s in config.SOURCES if s["key"] != "cos_sweep")
        with mock.patch.object(config, "SOURCES", trimmed):
            out = data.upwork(self.db_path, TODAY)
        sweep = next(r for r in out["rails"]["items"] if r["key"] == "cards_sweep")
        self.assertEqual(sweep["status"], "down")
        self.assertIn("not in config.SOURCES", sweep["reason"])


class TestUpworkCards(unittest.TestCase):
    """One engine, two views (decision 5): Today = the shared builder minus
    Loom flags, sliced; the tab = the builder's Upwork subset, Loom flags in,
    no cap. A Done or a snooze disappears from both."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="upwork-cards-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed_today(self.db_path)
        from modules.chief_of_staff import nudges as eng
        now = datetime(2026, 6, 15, tzinfo=config.TZ)
        eng.write_nudge(self.db_path, source="sales", dept="sales",
                        kind="hotlead_no_loom", title="Record a Loom: Make build",
                        dedup_key="hotlead_no_loom:2065400000000000111",
                        payload={"job_url": "https://www.upwork.com/jobs/~02x/"},
                        now=now)
        eng.write_nudge(self.db_path, source="knowledge", dept="knowledge",
                        kind="stale_dossier", title="Recompile: acme",
                        dedup_key="stale_dossier:acme",
                        payload={"cite": "kb/acme"}, now=now)
        eng.write_nudge(self.db_path, source="commitment", dept="knowledge",
                        kind="commitment_due", title="Ship the thing",
                        dedup_key="commitment:c1",
                        payload={"cite": "fathom/x @ 0"}, now=now)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_today_is_the_builder_minus_loom_flags_sliced(self):
        today = data.today(self.db_path, today="2026-06-15")
        built = data._act_now_queue(self.db_path, "2026-06-15",
                                    include_loom_flags=False)
        self.assertEqual([e["item_key"] for e in today["queue"]],
                         [e["item_key"] for e in built["queue"]][:config.TODAY_QUEUE_TOPN])
        self.assertNotIn("hotlead_no_loom",
                         {e.get("nudge_kind") for e in today["queue"]})

    def test_the_tab_lists_every_upwork_card_with_loom_flags_and_no_cap(self):
        nm = data.upwork(self.db_path, "2026-06-15")["needs_me"]
        kinds = {(e.get("kind"), e.get("nudge_kind")) for e in nm["cards"]}
        self.assertIn(("followup", None), kinds)                 # the drafts
        self.assertIn(("nudge", "hotlead_no_loom"), kinds)        # shown here
        self.assertNotIn(("nudge", "stale_dossier"), kinds)       # not Upwork
        self.assertNotIn(("nudge", "commitment_due"), kinds)      # not Upwork
        self.assertEqual(nm["total"], len(nm["cards"]))
        self.assertEqual(nm["groups"]["loom_flags"], 1)               # the CARD
        self.assertEqual(nm["loom_flags"]["cards"], 1)
        self.assertEqual(nm["loom_flags"]["count"], 0)                  # the population (seed_today has no loom_flag rows)
        self.assertEqual(nm["groups"]["replies_owed"], 2)          # rA + rB
        self.assertEqual(nm["drafts_waiting"]["days"], config.TODAY_FRESH_LEAD_DAYS)
        self.assertFalse(nm["offers"]["measured"])
        with mock.patch.object(config, "TODAY_QUEUE_TOPN", 1):
            nm1 = data.upwork(self.db_path, "2026-06-15")["needs_me"]
            self.assertEqual(nm1["total"], nm["total"])            # no cap here

    def _key(self, tid):
        # follow-up keys are per COUNTERPARTY (nudges.followup_key), never the
        # bare thread id — the same key form Today posts to /api/nudge
        from modules.chief_of_staff import nudges as eng
        return eng.followup_key(None, f"Room {tid}", tid)

    def test_a_done_hides_the_card_in_both_views(self):
        from modules.cockpit import actions
        ka, kb = self._key("rA"), self._key("rB")
        actions.nudge(self.db_path, {"item_key": ka, "action": "done"})
        today_keys = {e["item_key"] for e in data.today(self.db_path, today="2026-06-15")["queue"]}
        tab_keys = {e["item_key"] for e in data.upwork(self.db_path, "2026-06-15")["needs_me"]["cards"]}
        self.assertNotIn(ka, today_keys)
        self.assertNotIn(ka, tab_keys)
        self.assertIn(kb, tab_keys)

    def test_a_snooze_hides_until_its_date_and_is_counted(self):
        from datetime import timedelta
        from modules.cockpit import actions
        kb = self._key("rB")
        # the action dates its snooze from the wall clock; the fixture's
        # 'today' is earlier than that, so the card is hidden there...
        actions.nudge(self.db_path, {"item_key": kb, "action": "snooze",
                                     "snooze_days": 3})
        nm = data.upwork(self.db_path, "2026-06-15")["needs_me"]
        self.assertNotIn(kb, {e["item_key"] for e in nm["cards"]})
        self.assertEqual(nm["snoozed"]["count"], 1)
        self.assertTrue(nm["snoozed"]["next_back"])
        # ...and back, counted no longer, once the date has passed (honoured —
        # the v2 relics at 2099-12-31 were the failure this replaces)
        after = (datetime.now(config.TZ) + timedelta(days=10)).strftime("%Y-%m-%d")
        later = data.upwork(self.db_path, after)["needs_me"]
        self.assertEqual(later["snoozed"]["count"], 0)


class TestUpworkServerParams(CockpitServerCase):
    def test_routes_are_up_and_keyed(self):
        st, body = self.get("/api/upwork")
        self.assertEqual(st, 200, body)
        out = json.loads(body)
        self.assertEqual(set(out), {"as_of", "bands", "summary", "inbox", "needs_me",
                                    "funnel", "rails", "connects", "digest"})
        st, body = self.get(f"/api/upwork/rows?stage=fetched&week={WEEK}")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["stage"], "fetched")

    def test_bad_filters_come_back_as_error_envelopes_not_500s(self):
        for q in ("stage=bogus", "stage=fetched&week=lol", "stage=fetched&month=13",
                  f"stage=fetched&week={WEEK}&month={MONTH}",
                  "stage=connects&frm=2026-08", "stage=viewed"):
            st, body = self.get("/api/upwork/rows?" + q)
            self.assertEqual(st, 200, q)
            out = json.loads(body)
            self.assertIn("error", out, q)
            self.assertEqual(out["rows"], [], q)
            self.assertIsNone(out["total"], q)

    def test_the_clock_slots_are_unreachable_from_the_url(self):
        st, body = self.get("/api/upwork?today=2020-01-01")
        self.assertEqual(st, 200)
        self.assertNotEqual(json.loads(body)["as_of"], "2020-01-01")
        st, body = self.get(f"/api/upwork/rows?stage=fetched&week={WEEK}&_today=lol")
        self.assertEqual(st, 200)
        self.assertNotIn("error", json.loads(body))

    def test_new_params_are_registered(self):
        for p in ("kind", "week", "month", "frm", "to", "stage", "task"):
            self.assertIn(p, server.QUERY_PARAMS)

    def test_run_status_is_allowlisted_and_cheap(self):
        """The "Run now" buttons poll this after their kick. Only a task the
        cockpit may kick is readable here; anything else is an envelope."""
        st, body = self.get("/api/upwork/run?task=upwork_inbox")
        self.assertEqual(st, 200)
        out = json.loads(body)
        self.assertEqual(out["task"], "upwork_inbox")
        self.assertIn("running", out)
        st, body = self.get("/api/upwork/run?task=rm_-rf")
        out = json.loads(body)
        self.assertEqual(st, 200)
        self.assertIn("error", out)
        self.assertIsNone(out["run"])
        self.assertFalse(out["running"])


class TestRunStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="upwork-run-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_newest_run_with_its_words_running_flag_and_summary(self):
        self.assertIsNone(data.upwork_run_status(self.db_path, task="upwork_inbox")["run"])
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO runs (task_name, status, started_at, completed_at,"
                         " duration_ms, metrics_json) VALUES ('upwork_inbox','completed',"
                         " '2026-08-26T04:31:00+05:30','2026-08-26T04:31:40+05:30', 40000,"
                         " '{\"threads\": 120, \"new_messages\": 3}')")
            conn.execute("INSERT INTO runs (task_name, status, started_at)"
                         " VALUES ('upwork_inbox','running','2026-08-26T04:45:00+05:30')")
        out = data.upwork_run_status(self.db_path, task="upwork_inbox")
        self.assertTrue(out["running"])
        self.assertEqual(out["run"]["status"], "running")
        self.assertEqual(out["words"], "Checked Upwork for new messages")
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE runs SET status='completed', completed_at='2026-08-26T04:45:30+05:30',"
                         " metrics_json='{\"threads\": 121, \"new_messages\": 1}' WHERE status='running'")
        out = data.upwork_run_status(self.db_path, task="upwork_inbox")
        self.assertFalse(out["running"])
        self.assertIn("new_messages 1", out["run"]["summary"])
        # the allowlist is the registry's, not a second copy
        from modules.cockpit import actions
        for task in actions.RERUN_JOBS:
            self.assertNotIn("error", data.upwork_run_status(self.db_path, task=task))
        # upwork_outcomes joined RERUN_JOBS on 2026-08-27 (see above); a task
        # OUTSIDE the allowlist is still an error envelope, never a 500.
        self.assertNotIn("error", data.upwork_run_status(self.db_path, task="upwork_outcomes"))
        self.assertIn("error", data.upwork_run_status(self.db_path, task="not_a_task"))


class TestNoLiteralBandsOnTheTab(unittest.TestCase):
    def test_the_tab_block_compares_against_config_only(self):
        from tests.test_score_bands import _LITERAL_BAND
        src = (ROOT / "modules/cockpit/data.py").read_text()
        start = src.index("# ── Upwork tab (Phase 1")
        block = src[start:src.index("def _money_source_health")]
        offenders = [ln for ln in block.splitlines() if _LITERAL_BAND.search(ln)]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()


# ── the section (the screen), pinned where Python can see it ─────────────────

STATIC = ROOT / "modules" / "cockpit" / "static"


def _node():
    # the same lookup test_pipeline_funnel uses (launchd/osascript shells on the
    # Mac carry a minimal PATH; Homebrew's node lives off it)
    from tests.test_pipeline_funnel import _node as _find
    return _find()


class TestUpworkSection(unittest.TestCase):
    """The Upwork tab's JS is a browser module; what a Python test can hold it
    to: it exists, it is registered where Pipeline was (digit 2), the old id is
    aliased, it names no send route, it imports only the house primitives, the
    modules it depends on export what it imports, the styles it uses exist,
    the server serves it as JavaScript, and it parses under node."""

    def test_registered_in_pipelines_slot_with_the_alias(self):
        app = (STATIC / "js" / "app.js").read_text()
        ids = re.findall(r'\{ id: "([a-z]+)"', app)
        self.assertIn("upwork", ids)
        self.assertNotIn("pipeline", ids)
        self.assertEqual(ids.index("upwork"), 1)               # keyboard digit 2
        self.assertRegex(app, r'pipeline: "upwork"')          # #/pipeline still opens

    def test_section_file_imports_house_primitives_only_and_never_sends(self):
        src = (STATIC / "js" / "sections" / "upwork.js").read_text()
        imports = re.findall(r'from "([^"]+)"', src)
        self.assertEqual(set(imports), {"../ui.js", "../drill.js", "./today.js",
                                        "./pipeline.js", "../conversation.js"})
        self.assertNotIn("/send", src)
        self.assertNotIn("fetch(", src)                        # ctx.api / ctx.post only
        self.assertIn('ctx.api("/api/upwork")', src)
        self.assertIn("/api/upwork/rows?", src)
        # "Run now" on every system (his ask, 08-26 04:00): the kick is the
        # allowlisted /api/rerun and the button polls /api/upwork/run to the end
        self.assertIn('ctx.post("/api/rerun", { task })', src)
        self.assertIn("/api/upwork/run?", src)
        for task in ("upwork_inbox", "upwork_scan", "upwork_connects"):
            self.assertIn(f'"{task}"', src)
        for k in ("ew_cockpit_upwork_view", "ew_cockpit_upwork_tab", "ew_cockpit_upwork_list"):
            self.assertIn(k, src)                              # state only in these keys
        conv = (STATIC / "js" / "conversation.js").read_text()
        self.assertEqual(set(re.findall(r'from "([^"]+)"', conv)), {"./ui.js"})
        self.assertNotIn("/send", conv)
        self.assertNotIn("fetch(", conv)

    def test_dependencies_export_what_the_section_imports(self):
        today = (STATIC / "js" / "sections" / "today.js").read_text()
        pipeline = (STATIC / "js" / "sections" / "pipeline.js").read_text()
        self.assertIn("export function queueCard(", today)
        self.assertIn("export async function renderAnalysis(", pipeline)
        conv = (STATIC / "js" / "conversation.js").read_text()
        for fn in ("export function conversationRow(", "export async function act(",
                   "export async function closure("):
            self.assertIn(fn, conv)
        # ONE card component: Today delegates its conversation cards to the row
        self.assertIn('from "../conversation.js"', today)
        self.assertIn("if (it.conversation) return conversationRow(it, ctx, opts);", today)
        self.assertNotIn("async function act(", today)          # no second copy
        # the fold must not re-render the blocks the tab owns
        self.assertIn("analysisOnly ? [] : funnelRegion", pipeline)
        self.assertIn("analysisOnly ? [] : outcomesRegion", pipeline)

    def test_styles_the_section_uses_exist(self):
        css = (STATIC / "css" / "components.css").read_text()
        for cls in (".subtab", ".subtab .n.hot", ".up-lede", ".fchip.on",
                    ".cv-row", ".cv-state.owed", ".cv-state.quiet", ".cv-state.maybe",
                    ".cv-quote.ours", ".cv-bubble.out", ".cv-draftbox",
                    ".flow", ".stage .bar", ".callout.ok", ".strip",
                    ".rails-table", "tr.attn td", ".dot.warn"):
            self.assertIn(cls, css, cls)
        # tokens only — no raw colour in the new block
        block = css[css.index("Upwork tab (Phase 1"):]
        self.assertNotRegex(block, r"#[0-9a-fA-F]{3,6}\b")

    def test_no_literal_band_numbers_in_the_section(self):
        src = (STATIC / "js" / "sections" / "upwork.js").read_text()
        self.assertNotRegex(src, r"[≥>]=?\s*(16|20|22)\b")     # labels come from the payload

    def test_parses_under_node_as_an_es_module(self):
        node = _node()
        if not node:
            self.skipTest("node not installed")
        import subprocess
        for rel in ("js/sections/upwork.js", "js/sections/pipeline.js",
                    "js/sections/today.js", "js/conversation.js", "js/app.js"):
            with tempfile.TemporaryDirectory() as td:
                mjs = Path(td) / "check.mjs"
                mjs.write_text((STATIC / rel).read_text())
                r = subprocess.run([node, "--check", str(mjs)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{rel}: {r.stderr[:400]}")


def _visible_strings(src: str) -> str:
    """The section's user-facing text: string literals, comments stripped."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    src = re.sub(r'\bkey: "[^"]*"', "", src)               # column keys are field names, not copy
    src = re.sub(r"\$\{[^}]*\}", "", src)                   # ${expr} is code, not copy
    return " ".join(a or b for a, b in re.findall(r'"([^"\n]*)"|`([^`]*)`', src))


class TestPlainWordsOnTheTab(unittest.TestCase):
    """Abhijeet, 2026-08-25: "too complicated for me to understand anything".
    The redesign's rule: no statistics vocabulary in anything the screen
    prints. Comments may say "baseline"; the screen says "usually"."""

    def test_no_statistics_vocabulary_in_the_sections_strings(self):
        for rel in ("js/sections/upwork.js", "js/conversation.js"):
            text = _visible_strings((STATIC / rel).read_text()).lower()
            for word in ("baseline", "anecdote", "cohort", "n ≥", " pp ", "pooled",
                         "provenance law", "closure rule"):
                self.assertNotIn(word, text, f"{rel} prints {word!r}")
            self.assertIn("usually", text) if rel.endswith("upwork.js") else None


class TestConversations(unittest.TestCase):
    """The client-comms card as a PERSON (data._conversation): who they are
    and what they are to us, the last thing they actually said, the draft, one
    state word with its days — derived from the same evidence the card carried,
    plus the message text and the draft row. One builder feeds Today and the
    Upwork inbox."""

    DAY = "2026-06-15"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="upwork-convo-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        seed_today(self.db_path)
        with db.connect(self.db_path) as conn:
            # rA: they wrote last (owed) — on Upwork, with a draft written the
            # day BEFORE their message (stale by construction)
            conn.executemany(
                "INSERT INTO messages (id, thread_id, message, created_dt,"
                " sender_user_name, direction) VALUES (?,?,?,?,?,?)", [
                    ("mA1", "rA", "Can you send the scope doc?", "2026-06-08T09:00:00.000Z", "Ann A.", "inbound"),
                    ("mA2", "rA", "On it — tomorrow.", "2026-06-08T10:00:00.000Z", "Abhijeet G.", "outbound"),
                    ("mA3", "rA", "Any update on the <http://x.io|scope> doc?", "2026-06-10T09:00:00.000Z", "Ann A.", "inbound"),
                    ("mA4", "rA", None, "2026-06-10T09:30:00.000Z", None, "system"),
                    # rB: WE wrote last (gone quiet)
                    ("mB1", "rB", "Thanks, will review.", "2026-06-04T09:00:00.000Z", "Bob B.", "inbound"),
                    ("mB2", "rB", "Here is the revised estimate.", "2026-06-05T09:00:00.000Z", "Abhijeet G.", "outbound"),
                ])
            conn.execute("UPDATE drafts SET generated_at='2026-06-09 04:45:00',"
                         " body=\"Here's a draft:\n\n---\n\nHi Ann, the scope doc is attached.\n\n---\""
                         " WHERE thread_id='rA'")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _convo(self, tid):
        q = data._act_now_queue(self.db_path, self.DAY, include_loom_flags=True)
        for e in q["queue"]:
            if e.get("kind") == "followup" and tid in (e.get("detail") or {}).get("thread_ids", []):
                return e
        self.fail(f"no follow-up card for {tid}")

    def test_reply_owed_reads_their_last_message_and_flags_the_older_draft(self):
        e = self._convo("rA")
        c = e["conversation"]
        self.assertEqual(c["who"]["initials"], "RR")                  # "Room rA"
        self.assertEqual(c["state"]["kind"], "owed")
        self.assertEqual(c["state"]["label"], "Reply owed")
        self.assertEqual(c["state"]["days"], 5)                       # 06-10 → 06-15
        self.assertEqual(c["their"]["channel"], "upwork")
        self.assertEqual(c["their"]["text"], "Any update on the http://x.io doc?")
        self.assertEqual(c["their"]["sender"], "Ann A.")
        self.assertEqual(c["ours"]["text"], "On it — tomorrow.")
        # the draft, without the writer's preamble and fences
        self.assertEqual(c["draft"]["body"], "Hi Ann, the scope doc is attached.")
        self.assertTrue(c["draft"]["stale"])                           # 06-09 < 06-10
        self.assertIn("read that first", c["draft"]["stale_note"])
        # the open row: bubbles oldest → newest, system rows dropped
        th = c["threads"][0]
        self.assertEqual([m["who"] for m in th["messages"]], ["them", "you", "them"])
        self.assertEqual(th["url"], e["thread_url"])
        self.assertEqual(c["relationship"]["label"], "Lead")           # no contract row
        self.assertEqual(c["links"]["upwork"], e["thread_url"])
        self.assertIsNone(c["links"]["email"])

    def test_gone_quiet_reads_our_last_message_and_counts_from_it(self):
        c = self._convo("rB")["conversation"]
        self.assertEqual(c["state"]["kind"], "quiet")
        self.assertEqual(c["state"]["days"], 10)                      # 06-05 → 06-15
        self.assertEqual(c["ours"]["text"], "Here is the revised estimate.")
        self.assertEqual(c["draft"]["kind"], "follow-up")
        self.assertFalse(c["draft"]["stale"])

    def test_both_screens_carry_the_same_conversation(self):
        today = data.today(self.db_path, today=self.DAY)
        tab = data.upwork(self.db_path, self.DAY)
        t_by = {e["item_key"]: e for e in today["queue"] if e.get("conversation")}
        i_by = {e["item_key"]: e for e in tab["inbox"]["conversations"]}
        self.assertTrue(t_by)
        for k, e in t_by.items():
            self.assertIn(k, i_by)
            self.assertEqual(e["conversation"], i_by[k]["conversation"])
        # inbox order: reply owed before gone quiet; counts add up
        kinds = [e["conversation"]["state"]["kind"] for e in tab["inbox"]["conversations"]]
        self.assertEqual(kinds, sorted(kinds, key=lambda k: {"owed": 0, "quiet": 1, "maybe": 2}[k]))
        counts = tab["inbox"]["counts"]
        self.assertEqual(counts["all"], len(kinds))
        self.assertEqual(counts["owed"] + counts["quiet"] + counts["maybe"], counts["all"])
        s = tab["summary"]
        self.assertEqual(s["people_waiting"], counts["owed"] + counts["quiet"])
        self.assertEqual(s["replies_owed"], counts["owed"])
        self.assertIn("rails_attention", s)

    def test_a_done_conversation_is_named_as_cleared_not_lost(self):
        from modules.chief_of_staff import nudges as eng
        from modules.cockpit import actions
        key = eng.followup_key(None, "Room rA", "rA")
        actions.nudge(self.db_path, {"item_key": key, "action": "done"})
        # the action stamps the wall clock, so read the tab on the wall-clock day
        today = datetime.now(config.TZ).strftime("%Y-%m-%d")
        tab = data.upwork(self.db_path, today)
        self.assertNotIn(key, {e["item_key"] for e in tab["inbox"]["conversations"]})
        cleared = tab["inbox"]["cleared"]
        self.assertEqual([c["how"] for c in cleared if c["name"] == "Room rA"], ["done"])

    def test_a_linked_email_shows_on_the_row_and_becomes_their_last_word_when_newer(self):
        """David's brief (room_links): the row names the email; when it is newer
        than their Upwork message it IS their last word, with the Gmail link."""
        from modules.upwork import room_link
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO events (id, source, native_id, ts, author, subject, state, text, permalink)"
                         " VALUES ('evt:email:x:1','email','1','2026-06-12T09:00:00+00:00','ann@x.io',"
                         " 'The scope doc','queued','From: Ann Adams <ann@x.io>\n\nHere is the scope doc.','https://mail/1')")
            room_link.record(conn, "evt:email:x:1", {"thread_id": "rA", "how": "name-match", "matched_name": "Ann A."})
        c = self._convo("rA")["conversation"]
        self.assertEqual(c["their"]["channel"], "email")           # 06-12 > the 06-10 Upwork message
        self.assertEqual(c["their"]["text"], "The scope doc")
        self.assertEqual(c["their"]["link"], "https://mail/1")
        self.assertEqual(c["their"]["sender"], "Ann Adams")
        self.assertEqual(c["state"]["kind"], "owed")
        self.assertEqual(c["state"]["days"], 3)                    # counted from the email
        self.assertEqual([m["subject"] for m in c["emails"]], ["The scope doc"])
        self.assertEqual(c["emails"][0]["body"], "Here is the scope doc.")
        self.assertEqual(c["links"]["email"], "https://mail/1")

    def test_timestamp_shapes_share_one_clock(self):
        u = data._utc_dt
        self.assertEqual(u("2026-07-31T06:37:17.390Z").isoformat(), "2026-07-31T06:37:17.390000+00:00")
        self.assertEqual(u("2026-08-22T08:37:35-07:00").isoformat(), "2026-08-22T15:37:35+00:00")
        self.assertEqual(u("2026-07-31 13:45:23").isoformat(), "2026-07-31T13:45:23+00:00")
        self.assertIsNone(u("not a date"))
        self.assertIsNone(u(None))
        self.assertEqual(data._initials("Royan Stomberg, Ryan Stomberg"), "RS")
        self.assertEqual(data._initials("Globaltize LLC"), "GL")
        self.assertEqual(data._draft_text("Here's a draft reply:\n\n---\n\nHey there.\n\n---"), "Hey there.")


class TestUpworkSectionServed(CockpitServerCase):
    def test_the_module_is_served_as_javascript(self):
        st, body = self.get("/static/js/sections/upwork.js")
        self.assertEqual(st, 200)
        self.assertIn('export const meta = { title: "Upwork"', body)
