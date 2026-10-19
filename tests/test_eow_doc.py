"""The branded weekly report document, its plain-text twin, and the PDF path.

2026-10-19, second pass on the report: "it needs to be super awesome UI wise
… proper section for hours which is collapsible … a summary part … etherwise
branded … export as pdf". The document is what a client reads, so the tests
pin what it may claim:

  · hours are grouped the way HIS ClickUp export groups them — by tag — and a
    two-tag task is its own group, never split (splitting invents an allocation)
  · only a status that names the client puts a task under "Waiting on your
    review"; a task BLOCKED for our own reasons is not the client's to unblock
  · a bulk tidy-up close cannot reach "Delivered" through the zero-time path
  · the PDF prints every workstream OPEN — a closed <details> prints nothing
  · the copyable text says the same numbers as the document
  · task names are escaped — they are typed by humans into ClickUp
"""
from __future__ import annotations

import base64
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db
from modules.hours import eow, eow_doc

MS_H = 3_600_000


def ms(day, hh=12, mm=0):
    import datetime as dt
    return int(dt.datetime(*[int(x) for x in day.split("-")], hh, mm,
                           tzinfo=config.TZ).timestamp() * 1000)


def tags(*names):
    return json.dumps([{"name": n, "color": "#2680D4"} for n in names])


class StatusClass(unittest.TestCase):
    def test_his_real_review_status_is_review(self):
        self.assertEqual(eow.status_class("NEEDS CLIENT REVIEW", "custom"),
                         "review")
        self.assertEqual(eow.status_class("waiting on client", "custom"),
                         "review")

    def test_blocked_is_not_the_clients_problem(self):
        """It was in the awaiting list; Sprint 22 then printed one of OUR
        blocked tasks under "Waiting on your review"."""
        for s in ("blocked", "on hold", "in progress", "qa"):
            self.assertEqual(eow.status_class(s, "custom"), "active", s)

    def test_done_is_by_type_not_by_name(self):
        self.assertEqual(eow.status_class("COMPLETE", "closed"), "done")
        self.assertEqual(eow.status_class("shipped", "done"), "done")


class DocCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="eowdoc-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        with db.connect(self.db_path) as c:
            c.execute(
                "INSERT INTO hours_periods (list_id, list_name, space_id,"
                " client_id, starts_on, ends_on, parse_ok, updated_at) VALUES"
                " ('L1','Sprint 1 (10/5 - 10/18)','SP','acme','2026-10-05',"
                " '2026-10-18',1,'2026-10-19 04:31')")
        self.n = 0

    def entry(self, task_id, name, hours, tag_json=None, status="in progress",
              stype="custom", user="Dev", day="2026-10-08"):
        self.n += 1
        with db.connect(self.db_path) as c:
            c.execute(
                "INSERT INTO hours_entries (id, task_id, task_name, space_id,"
                " list_id, client_id, user_id, user_name, duration_ms,"
                " start_ms, task_tags_json, task_status, task_status_type,"
                " first_seen_at, updated_at) VALUES"
                " (?,?,?,'SP','L1','acme','U1',?,?,?,?,?,?,'x','2026-10-19 04:31')",
                (f"e{self.n}", task_id, name, user, int(hours * MS_H),
                 ms(day), tag_json, status, stype))

    def listed(self, tid, name, status="complete", stype="closed",
               changed=None, tag_json=None):
        with db.connect(self.db_path) as c:
            c.execute(
                "INSERT INTO hours_tasks (id, name, status, status_type,"
                " space_id, client_id, list_id, status_changed_ms, created_ms,"
                " tags_json, first_seen_at, updated_at) VALUES"
                " (?,?,?,?,'SP','acme','L1',?,?,?,'x','x')",
                (tid, name, status, stype, changed or ms("2026-10-09"),
                 ms("2026-10-06"), tag_json))

    def report(self):
        with db.connect(self.db_path) as c:
            return eow.build(c, "acme", "L1", "2026-10-19")


class GroupedLikeHisExport(DocCase):
    def test_tags_make_workstreams_and_two_tags_are_one_group(self):
        self.entry("t1", "Onboarding flow", 3, tags("mc"), "complete", "closed")
        self.entry("t2", "Webhook retry", 1.5, tags("mc"))
        self.entry("t3", "Shared login", 2, tags("spm", "cashswipe"))
        ws = self.report()["workstreams"]
        names = [g["name"] for g in ws["groups"]]
        self.assertEqual(names, ["mc", "cashswipe + spm"])
        self.assertEqual(ws["groups"][0]["ms"], int(4.5 * MS_H))
        self.assertEqual(ws["groups"][1]["ms"], 2 * MS_H)   # not split
        self.assertEqual(ws["total_ms"], int(6.5 * MS_H))
        self.assertEqual(ws["done"], 1)

    def test_untagged_work_is_other_work_or_all_work(self):
        self.entry("t1", "Tagged", 2, tags("mc"))
        self.entry("t2", "Untagged", 1)
        ws = self.report()["workstreams"]
        other = [g for g in ws["groups"] if g.get("catch_all")]
        self.assertEqual([g["name"] for g in other], ["Other work"])

    def test_one_untagged_group_is_all_work(self):
        self.entry("t1", "Untagged", 1)
        ws = self.report()["workstreams"]
        self.assertEqual([g["name"] for g in ws["groups"]], ["All work"])

    def test_review_status_lands_in_waiting(self):
        self.entry("t1", "Payment page", 2, tags("mc"), "NEEDS CLIENT REVIEW")
        self.entry("t2", "Our own blocker", 1, tags("mc"), "blocked")
        ws = self.report()["workstreams"]
        self.assertEqual([x["name"] for x in ws["review"]], ["Payment page"])
        self.assertIn("Our own blocker", [x["name"] for x in ws["active"]])


class TidyUpIsNotDeliveredEitherWay(DocCase):
    def test_bulk_closed_zero_time_tasks_stay_out(self):
        for i in range(config.HOURS_BULK_CLOSE_MIN):
            self.listed(f"b{i}", f"Old ticket {i}",
                        changed=ms("2026-10-10", 9, 0))
        self.listed("solo", "Shipped properly", changed=ms("2026-10-14", 16))
        ws = self.report()["workstreams"]
        rows = [x["name"] for g in ws["groups"] for x in g["rows"]]
        self.assertEqual(rows, ["Shipped properly"])

    def test_alert_tickets_at_zero_time_stay_out(self):
        self.listed("a1", "Encountered error in X scenario - 4 Oct")
        ws = self.report()["workstreams"]
        self.assertEqual(ws["groups"], [])


class TheDocument(DocCase):
    def setUp(self):
        super().setUp()
        self.entry("t1", "Onboarding flow", 3, tags("mc"), "complete", "closed")
        self.entry("t2", "<script>alert(1)</script>", 1, tags("eskool"))
        self.entry("t3", "Payment page", 2, tags("spm"), "NEEDS CLIENT REVIEW")
        self.rep = self.report()

    def test_task_names_are_escaped(self):
        html = eow_doc.render(self.rep)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_print_opens_every_workstream(self):
        """A closed <details> prints as a heading with nothing under it."""
        printed = eow_doc.render(self.rep, for_print=True)
        self.assertEqual(printed.count('<details class="ws" open>'), 3)
        self.assertNotIn("Expand all", printed)            # no screen tools
        screen = eow_doc.render(self.rep, for_print=False)
        self.assertEqual(screen.count('<details class="ws" open>'), 0)
        self.assertIn("Expand all", screen)

    def test_it_has_the_sections_and_the_ask(self):
        html = eow_doc.render(self.rep)
        for s in ("Summary", "Waiting on your review", "Delivered",
                  "Hours by workstream", "Weekly delivery report"):
            self.assertIn(s, html)
        self.assertIn("kpi-review", html)          # the ask is highlighted
        self.assertIn("6h 00m", html)               # grand total

    def test_self_contained(self):
        """Rendered offline by Chrome and inside a frame that cannot fetch."""
        html = eow_doc.render(self.rep)
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))
        self.assertNotIn("<link", html)

    def test_text_says_the_same_numbers(self):
        txt = eow_doc.render_text(self.rep)
        self.assertIn("Total — 6h 00m (6.00 h)", txt)
        self.assertIn("Waiting on your review: 1", txt)
        self.assertIn("• Payment page — spm", txt)
        self.assertIn("Onboarding flow", txt)                # delivered
        self.assertNotIn("$", txt)

    def test_hm(self):
        self.assertEqual(eow_doc.hm(0), "—")
        self.assertEqual(eow_doc.hm(5 * 60_000), "5m")
        self.assertEqual(eow_doc.hm(16 * MS_H + 5 * 60_000), "16h 05m")


class CockpitWiring(DocCase):
    def setUp(self):
        super().setUp()
        self.entry("t1", "Onboarding flow", 3, tags("mc"), "complete", "closed")

    def test_reader_returns_document_and_text_only_when_asked(self):
        from modules.cockpit import data
        plain = data.hours_eow(self.db_path, client="acme", period="L1")
        self.assertNotIn("html", plain)
        self.assertNotIn("text", plain)
        doc = data.hours_eow(self.db_path, client="acme", period="L1",
                             fmt="doc")
        self.assertTrue(doc["html"].startswith("<!doctype html>"))
        self.assertIn("Onboarding flow", doc["text"])

    def test_pdf_action_prints_the_print_variant_and_returns_bytes(self):
        from modules.cockpit import actions, server
        from modules.hours import pdf
        self.assertIs(server.POST_ROUTES["/api/hours/eow_pdf"],
                      actions.hours_eow_pdf)
        seen = {}

        def fake_render(html_text, dest):
            seen["html"] = html_text
            dest.write_bytes(b"%PDF-1.4 fake")
            return dest

        dest = self.tmp / "out.pdf"
        with mock.patch.object(pdf, "render", fake_render), \
                mock.patch.object(pdf, "out_path", lambda c, p: dest):
            r = actions.hours_eow_pdf(self.db_path,
                                      {"client": "acme", "period": "L1"})
        self.assertTrue(r["ok"])
        self.assertEqual(base64.b64decode(r["pdf_b64"]), b"%PDF-1.4 fake")
        self.assertTrue(r["filename"].endswith("Sprint 1 - Etherwise report.pdf"))
        self.assertNotIn("/", r["filename"])
        self.assertIn('<details class="ws" open>', seen["html"])

    def test_pdf_action_needs_a_client(self):
        from modules.cockpit import actions
        with self.assertRaises(ValueError):
            actions.hours_eow_pdf(self.db_path, {})


if __name__ == "__main__":
    unittest.main()
