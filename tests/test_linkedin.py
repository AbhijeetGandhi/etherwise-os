"""LinkedIn — the engagement tracker in the cockpit (2026-10-28).

Spec: etherwise-v3-linkedin-brief.md. What these pin:
  * the sync is READ-ONLY on Airtable, in code, not by convention;
  * a re-sync is idempotent, a vanished record is marked gone (never deleted),
    and a table that comes back EMPTY after having rows marks nothing;
  * a renamed field is loud (linkedin_schema_drift), not a quiet zero;
  * a comment posted at 23:18 UTC counts on the NEXT IST day — the pace bar
    is his day, not Airtable's;
  * an empty Messages table is "no DMs yet", never a 0% reply rate;
  * Today lists only fresh items, and the growth slice reads the same reader.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db
from modules.cockpit import actions
from modules.cockpit import linkedin as L
from modules.linkedin import sync as S

ROOT = Path(__file__).resolve().parents[1]
P1, P2 = "recPERSON0000001", "recPERSON0000002"


def fetched(**over):
    base = {
        "people": [
            {"id": P1, "createdTime": "2026-10-11T00:00:00.000Z",
             "fields": {"Name": "Luba", "Stage": "Engaged", "ICP fit": "Strong",
                        "Segment": "ABA / behavioural health",
                        "Next action": "Ready to DM: reference their post",
                        "Comments posted": 2, "Times they replied": 1,
                        "Last checked for posts": "2026-10-28",
                        "LinkedIn URL": "https://www.linkedin.com/in/x/"}},
            {"id": P2, "createdTime": "2026-10-11T00:00:00.000Z",
             "fields": {"Name": "Seth", "Stage": "Watching",
                        "Next action": "Wait for a post worth a first comment",
                        "Last checked for posts": "2026-10-28"}},
        ],
        "comments": [
            # 23:18 UTC on the 27th is 04:48 IST on the 28th
            {"id": "recC1", "fields": {"Commented at": "2026-10-27T23:18:00.000Z",
                                        "Person": [P1], "Author replied": True,
                                        "Hours after post": 12,
                                        "Impressions on our comment": 330}},
            {"id": "recC2", "fields": {"Commented at": "2026-10-26T11:46:00.000Z",
                                        "Person": [P2], "Hours after post": 107,
                                        "Impressions on our comment": 15}},
        ],
        "messages": [],
        "candidates": [
            {"id": "recK1", "fields": {"Post": "fresh draft", "Verdict": "Comment",
                                        "Found on": "2026-10-27",
                                        "Draft comment": "a draft"}},
            {"id": "recK2", "fields": {"Post": "old draft", "Verdict": "Comment",
                                        "Found on": "2026-10-10"}},
            {"id": "recK3", "fields": {"Post": "needs him", "Verdict": "Brainstorm",
                                        "Found on": "2026-10-28",
                                        "Question for Abhijeet": "what did you live?"}},
            {"id": "recK4", "fields": {"Post": "skip", "Verdict": "Skipped",
                                        "Found on": "2026-10-28"}},
        ],
        "posts": [
            {"id": "recP1", "fields": {"Post": "Hi LinkedIn", "Posted at": "2026-10-27T11:35:00.000Z",
                                        "Impressions": 740, "Reactions": 29, "Comments": 8,
                                        "Reposts": 1, "Followers gained": 2}},
        ],
        "profile": [],
    }
    base.update(over)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="li-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def store(self, data=None, now="2026-10-28 12:05:00"):
        with db.connect(self.db_path) as conn:
            return S.store(conn, data or fetched(), now)

    def mark_synced(self, at="2026-10-28T12:05:09+05:30"):
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO runs (task_name, status, started_at, completed_at)"
                         " VALUES ('linkedin_sync','completed',?,?)", (at, at))


class ReadOnlyByConstruction(unittest.TestCase):
    def test_the_sync_has_no_write_path_to_airtable(self):
        src = (ROOT / "modules" / "linkedin" / "sync.py").read_text()
        code = re.sub(r'"""[\s\S]*?"""', "", src)      # docstrings may SAY the words
        for verb in ("create_records", "update_records", "delete", "upsert_records",
                     '"POST"', '"PATCH"', '"PUT"'):
            self.assertNotIn(verb, code, verb)
        self.assertEqual([m for m in dir(S._ReadOnly) if not m.startswith("_")], ["list"])

    def test_it_reads_every_configured_table_by_id(self):
        seen = []

        class Ctx:
            db_path = None
            def log(self, m): pass
            def anomaly(self, *a): pass

        tmp = Path(tempfile.mkdtemp(prefix="li-"))
        try:
            dbp = tmp / "v3.db"
            db.migrate(dbp)
            ctx = Ctx()
            ctx.db_path = dbp
            S.sync(ctx, _reader=lambda b, t: seen.append((b, t)) or [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(seen, [(config.LINKEDIN_BASE, t)
                                for t in config.LINKEDIN_TABLES.values()])
        self.assertTrue(all(t.startswith("tbl") for _, t in seen))


class Store(Base):
    def test_a_resync_is_idempotent_and_snapshots_once_a_day(self):
        self.store()
        r = self.store(now="2026-10-28 14:05:00")
        self.assertEqual(r["tables"]["people"], {"read": 2, "new": 0, "changed": 0, "gone": 0})
        with db.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM li_snapshots"
                                          " WHERE table_key='people'").fetchone()[0], 2)
        self.store(now="2026-10-29 12:05:00")
        with db.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(DISTINCT day) FROM li_snapshots").fetchone()[0], 2)

    def test_a_changed_cell_is_stored_and_the_old_day_keeps_its_value(self):
        self.store()
        d = fetched()
        d["posts"][0]["fields"]["Impressions"] = 900
        r = self.store(d, now="2026-10-29 12:05:00")
        self.assertEqual(r["tables"]["posts"]["changed"], 1)
        with db.connect(self.db_path) as conn:
            days = dict(conn.execute("SELECT day, fields_json FROM li_snapshots"
                                     " WHERE table_key='posts'").fetchall())
        self.assertEqual(json.loads(days["2026-10-28"])["Impressions"], 740)
        self.assertEqual(json.loads(days["2026-10-29"])["Impressions"], 900)

    def test_a_vanished_record_is_marked_gone_not_deleted(self):
        self.store()
        d = fetched()
        d["people"] = d["people"][:1]
        r = self.store(d, now="2026-10-28 14:05:00")
        self.assertEqual(r["tables"]["people"]["gone"], 1)
        with db.connect(self.db_path) as conn:
            row = conn.execute("SELECT gone_at FROM li_records WHERE record_id=?", (P2,)).fetchone()
        self.assertEqual(row[0], "2026-10-28 14:05:00")

    def test_a_table_that_comes_back_empty_marks_nothing(self):
        """A permissions blip must not blank the whole tab."""
        self.store()
        r = self.store(fetched(people=[]), now="2026-10-28 14:05:00")
        self.assertEqual(r["refused_empty"], ["people"])
        with db.connect(self.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM li_records WHERE table_key='people'"
                             " AND gone_at IS NULL").fetchone()[0]
        self.assertEqual(n, 2)

    def test_a_renamed_field_is_drift_not_a_quiet_zero(self):
        d = fetched()
        for r in d["candidates"]:
            r["fields"]["Decision"] = r["fields"].pop("Verdict")
        res = self.store(d)
        self.assertEqual(res["drift"], {"candidates": ["Verdict"]})
        # one record missing a field is just an empty cell — not drift
        d2 = fetched()
        d2["candidates"][3]["fields"].pop("Verdict")
        self.assertEqual(self.store(d2)["drift"], {})

    def test_drift_is_a_registered_degrade_of_the_source(self):
        src = next(s for s in config.SOURCES if s["key"] == "linkedin_sync")
        self.assertIn(S.DRIFT_KIND, src["degrade_kinds"])
        self.assertEqual(src["task_name"], S.TASK_NAME)
        self.assertEqual(actions.RERUN_JOBS[S.TASK_NAME], src["launchd_label"])
        plist = ROOT / "launchd" / (src["launchd_label"] + ".plist")
        self.assertTrue(plist.exists())
        self.assertIn("modules.linkedin.sync", plist.read_text())


class Readers(Base):
    def setUp(self):
        super().setUp()
        self.store()
        self.mark_synced()

    def view(self):
        return L.linkedin(self.db_path, "2026-10-28")

    def test_never_synced_says_so(self):
        tmp = Path(tempfile.mkdtemp(prefix="li-"))
        try:
            dbp = tmp / "v3.db"
            db.migrate(dbp)
            d = L.linkedin(dbp, "2026-10-28")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertTrue(d["never_synced"])

    def test_no_messages_is_a_note_not_a_reply_rate(self):
        r = self.view()["results"]
        self.assertEqual(r["messages"], 0)
        self.assertEqual(r["buyer_conversations"], 0)
        self.assertIn("no DMs", r["note"])
        self.assertEqual((r["day_n"], r["day90"], r["days_left"]), (19, "2027-01-07", 71))

    def test_buyer_conversations_count_people_not_messages(self):
        msgs = [{"id": f"recM{i}", "fields": {"Type": "DM", "Sent on": "2026-10-28",
                                              "Person": [P1], "Buyer conversation": True,
                                              "Replied": True}} for i in range(3)]
        self.store(fetched(messages=msgs))
        r = self.view()["results"]
        self.assertEqual(r["buyer_conversations"], 1)
        self.assertEqual(r["messages"], 3)
        self.assertIsNone(r["note"])

    def test_pace_counts_his_ist_day(self):
        p = self.view()["pace"]
        self.assertEqual(p["comments_today"], 1)      # 23:18Z on the 27th
        self.assertEqual(p["comments_7d"], 2)
        self.assertEqual(p["comments_target_day"], 10)
        self.assertEqual(p["posts_this_week"], 1)
        self.assertEqual(len(p["by_day"]), 14)
        self.assertEqual(sum(x["n"] for x in p["by_day"]), 2)

    def test_funnel_figures_equal_their_rows(self):
        f = self.view()["funnel"]
        self.assertEqual(f["candidates"], len(f["rows"]["candidates"]))
        self.assertEqual(f["author_replied"], len(f["rows"]["replied"]))
        for v, n in f["by_verdict"].items():
            self.assertEqual(n, len(f["rows"]["verdict:" + v]), v)
        self.assertEqual(f["commented"], 2)
        self.assertTrue(f["early_vs_late"]["small_sample"])

    def test_relationship_stages_equal_their_rows(self):
        r = self.view()["relationships"]
        self.assertEqual(r["people"], 2)
        for s, n in r["by_stage"].items():
            self.assertEqual(n, len(r["rows"]["stage:" + s]), s)
        self.assertEqual([p["name"] for p in r["act_now"]], ["Luba"])

    def test_posts_engagement_is_computed_not_read_as_text(self):
        p = self.view()["posts"]["rows"][0]
        self.assertEqual(p["engagement_rate"], 5.1)      # (29+8+1)/740

    def test_today_lists_only_fresh_items(self):
        n = L.needs_you(self.db_path, "2026-10-28")
        self.assertEqual([c["post"] for c in n["drafts"]], ["fresh draft"])
        self.assertEqual(n["older"]["drafts"], 1)
        self.assertEqual([c["post"] for c in n["brainstorm"]], ["needs him"])
        self.assertEqual([p["name"] for p in n["people"]], ["Luba"])
        self.assertEqual(n["total"], 3)

    def test_growth_slice_reads_the_same_reader(self):
        s = L.summary(self.db_path, "2026-10-28")
        self.assertTrue(s["has_data_surface"])
        self.assertEqual(s["funnel"]["commented"], 2)
        self.assertEqual(s["day90"]["target"], 5)


class Wiring(unittest.TestCase):
    def test_the_route_and_the_tab_exist(self):
        from modules.cockpit import server
        self.assertIs(server.GET_ROUTES["/api/linkedin"], L.linkedin)
        app = (ROOT / "modules/cockpit/static/js/app.js").read_text()
        self.assertIn('import("./sections/linkedin.js")', app)
        today = (ROOT / "modules/cockpit/static/js/sections/today.js").read_text()
        self.assertIn("linkedinBlock(d.linkedin", today)

    def test_nothing_on_the_tab_sends(self):
        src = (ROOT / "modules/cockpit/static/js/sections/linkedin.js").read_text()
        for bad in ("/api/send", "send_message", "/api/post", "ctx.post("):
            self.assertNotIn(bad, src)


if __name__ == "__main__":
    unittest.main()
