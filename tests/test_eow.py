"""The weekly client report — what may and may not appear in it.

2026-10-19, after Hours shipped: "bro but wasn't the main goal to generate EOW
report. currently i only see hours". The report existed but sat three clicks
deep inside a drill; it now has its own tab. The tests here are about the
CONTENT, because the first real render was unsendable:

Horizons 44 produced 43 "shipped" items, of which 40 were Make alert mails
filed as tickets ("Encountered error in ... scenario", "The scenario ... has
been stopped") and 26 shared a single close timestamp — one sprint tidy-up.
Sending a client their own automation errors as this week's achievements is
worse than sending nothing.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from core import config, db
from modules.hours import eow

MS_H = 3_600_000


def ms(day, hh=12, mm=0):
    import datetime as dt
    return int(dt.datetime(*[int(x) for x in day.split("-")], hh, mm,
                           tzinfo=config.TZ).timestamp() * 1000)


class AlertsAreNotAchievements(unittest.TestCase):
    def test_alert_tickets_are_not_delivery(self):
        for name in (
                "Encountered error in [Tech Interview] 8 Rejected scenario - 4 Oct",
                "The scenario [HIPP] 3 Add Clinician has been stopped - 24 Sep",
                "Fwd: Something went wrong with an automation: Automation 2",
                "Re: Zoho Email Access Check",
                "Fwd: BCBA Introduction - Tynslee"):
            self.assertFalse(eow.is_delivery(name), name)

    def test_real_work_survives_the_filter(self):
        for name in ("Intake - 3 Pathway Flow", "RUN OIG",
                     "Clinician adding to client - issue in bot",
                     "Wordpress - Services Page fix",
                     "Interview Links- Scheduling"):
            self.assertTrue(eow.is_delivery(name), name)

    def test_the_automation_is_named_not_the_ticket(self):
        """A client reading nine identical error subjects learns less than one
        reading 'this automation failed nine times'."""
        self.assertEqual(
            eow.scenario_of("Encountered error in [AuthForm] 1 2 - Initial"
                            " Auth scenario - 5 Oct"),
            "[AuthForm] 1 2 - Initial Auth")
        self.assertEqual(
            eow.scenario_of("The scenario Payroll Logs People Finder has been"
                            " stopped - 1 Oct"),
            "Payroll Logs People Finder")


class EowCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="eow-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        with db.connect(self.db_path) as c:
            c.execute(
                "INSERT INTO hours_periods (list_id, list_name, space_id,"
                " client_id, starts_on, ends_on, parse_ok, updated_at) VALUES"
                " ('L1','Sprint 1 (10/5 - 10/18)','SP','acme','2026-10-05',"
                " '2026-10-18',1,'2026-10-19')")
            self.add = lambda **kw: c.execute(
                "INSERT INTO hours_tasks (id, name, status, status_type,"
                " space_id, client_id, list_id, status_changed_ms, created_ms,"
                " first_seen_at, updated_at) VALUES"
                " (?,?,?,?,'SP','acme','L1',?,?,'x','x')",
                (kw["id"], kw["name"], kw.get("status", "complete"),
                 kw.get("type", "closed"), kw["changed"],
                 kw.get("created", kw["changed"])))

    def _report(self, today="2026-10-19"):
        with db.connect(self.db_path) as c:
            return eow.build(c, "acme", "L1", today)


class BulkCloseIsNotDelivery(EowCase):
    def test_a_tidy_up_is_excluded_and_counted(self):
        """Forty tickets closed in one minute is a cleanup. Listing them as
        forty things delivered is a claim a client checks once, and then stops
        believing the whole report."""
        with db.connect(self.db_path) as c:
            self.add = self.add  # bind
            for i in range(8):                      # same minute = tidy-up
                c.execute(
                    "INSERT INTO hours_tasks (id, name, status, status_type,"
                    " space_id, client_id, list_id, status_changed_ms,"
                    " created_ms, first_seen_at, updated_at) VALUES"
                    " (?,?,'complete','closed','SP','acme','L1',?,?,'x','x')",
                    (f"bulk{i}", f"Real work {i}", ms("2026-10-10", 9, 0),
                     ms("2026-10-06")))
            c.execute(
                "INSERT INTO hours_tasks (id, name, status, status_type,"
                " space_id, client_id, list_id, status_changed_ms, created_ms,"
                " first_seen_at, updated_at) VALUES"
                " ('solo','Shipped properly','complete','closed','SP','acme',"
                " 'L1',?,?,'x','x')",
                (ms("2026-10-14", 16, 30), ms("2026-10-12")))
        rep = self._report()
        self.assertEqual([x["name"] for x in rep["shipped"]],
                         ["Shipped properly"])
        self.assertEqual(rep["shipped_excluded"]["bulk_closed"], 8)
        self.assertEqual(rep["shipped_excluded"]["bulk_close_events"], 1)

    def test_the_exclusions_are_stated_not_hidden(self):
        """They are big enough to change the report's character, so he has to
        be able to disagree with them."""
        with db.connect(self.db_path) as c:
            c.execute(
                "INSERT INTO hours_tasks (id, name, status, status_type,"
                " space_id, client_id, list_id, status_changed_ms, created_ms,"
                " first_seen_at, updated_at) VALUES"
                " ('a','Encountered error in X scenario - 4 Oct','complete',"
                " 'closed','SP','acme','L1',?,?,'x','x')",
                (ms("2026-10-09"), ms("2026-10-09")))
        rep = self._report()
        self.assertEqual(rep["shipped"], [])
        self.assertEqual(rep["shipped_excluded"]["alert_tickets"], 1)
        md = eow.render_markdown(rep)
        self.assertIn("1 alert tickets", md)


class CaughtComesFromTheTickets(EowCase):
    def test_alerts_are_attributed_by_the_sprint_they_sit_in(self):
        """Make's alert MAILS are excluded by the sweep with no client
        attached, so the events table cannot say whose alert it was. The same
        alerts become tasks in the client's own sprint folder, which attributes
        them for free."""
        with db.connect(self.db_path) as c:
            for i in range(3):
                c.execute(
                    "INSERT INTO hours_tasks (id, name, status, status_type,"
                    " space_id, client_id, list_id, status_changed_ms,"
                    " created_ms, first_seen_at, updated_at) VALUES"
                    " (?,?,'complete','closed','SP','acme','L1',?,?,'x','x')",
                    (f"al{i}",
                     "Encountered error in [HIPP] 3 Add Clinician scenario"
                     f" - {i + 1} Oct", ms("2026-10-09"), ms("2026-10-09")))
        rep = self._report()
        c = rep["caught"]
        self.assertEqual(c["total_alerts"], 3)
        self.assertEqual(len(c["scenarios"]), 1)          # grouped, not listed
        self.assertEqual(c["scenarios"][0]["scenario"],
                         "[HIPP] 3 Add Clinician")
        self.assertEqual(c["scenarios"][0]["resolved"], 3)


class TheReportIsHonestAboutItself(EowCase):
    def test_an_empty_section_is_stated_not_dropped(self):
        rep = self._report()
        self.assertIn("shipped", rep["empty_sections"])
        md = eow.render_markdown(rep)
        self.assertIn("Nothing closed in this period", md)

    def test_it_says_the_number_can_still_move(self):
        md = eow.render_markdown(self._report())
        self.assertIn("as-of", md)
        self.assertIn("logged after a period closes", md)

    def test_it_never_names_another_client_or_invents_savings(self):
        """The sample this was modelled on ends by comparing the client to
        another client by name and quoting a dollar figure for time saved.
        Both are template inventions; one is also a confidentiality problem."""
        md = eow.render_markdown(self._report())
        self.assertNotIn("$", md)
        for other in ("Contoso", "Northwind", "Hermon", "Horizons"):
            self.assertNotIn(other, md)


if __name__ == "__main__":
    unittest.main()
