"""The other call recorders — Fireflies and ClickUp's AI Notetaker (2026-11-24).

Shapes are the real ones (the Fireflies GraphQL transcript, the notetaker's
markdown page), the words are invented. Pinned here:

  * a Fireflies transcript and a ClickUp notes page both become the Fathom
    meeting shape, so `ingest.parse_fathom` reads them unchanged — people,
    addresses, turns, timestamps where the recorder has them;
  * RULE 1: a Fireflies recording none of our addresses took part in is never
    read in, even if the list returned it;
  * RULE 2: one copy per call — a recording that overlaps a call already held
    with the same people is a twin and is not saved; back-to-back calls and
    calls with different people are not twins; ClickUp's estimated clock may
    drift only when the two share someone from outside;
  * the poll: saves what is new, records every outcome, never fetches a
    settled recording twice, waits on a transcript that is not there yet,
    survives one recorder being down, and a dry run writes nothing;
  * a saved recording goes through the unchanged ingest and lands on its client.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core import config, db
from modules.knowledge import call_recorders as cr, daily, ingest

UTC = timezone.utc
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _ms(dt):
    return int(dt.timestamp() * 1000)


def ff(tid="01FFAAA", *, start=datetime(2026, 11, 20, 20, 15, tzinfo=UTC), minutes=30.0,
       title="Nick x Abhijeet", emails=("nick@globaltize.com", "abhijeet@globaltize.com"),
       speakers=("Nick Esquivel", "Abhijeet Gandhi"), turns=8):
    sentences, t = [], 3.0
    for i in range(turns):
        sp = speakers[i % len(speakers)]
        sentences.append({"index": 2 * i, "speaker_name": sp, "text": f"Point {i} first half.", "start_time": t})
        sentences.append({"index": 2 * i + 1, "speaker_name": sp, "text": "And the second half.", "start_time": t + 2})
        t += 60
    return {"id": tid, "title": title, "date": _ms(start), "duration": minutes,
            "organizer_email": emails[0], "host_email": emails[0], "participants": list(emails),
            "transcript_url": f"https://app.fireflies.ai/view/{tid}",
            "meeting_attendees": [{"displayName": None, "email": e, "name": None} for e in emails],
            "speakers": [{"id": i, "name": n} for i, n in enumerate(speakers)],
            "sentences": sentences, "summary": {"overview": "- Staging build ready.", "action_items": "Ship it."}}


def notes_md(attendees="Abhijeet Gandhi, Suthi Nagar, Brandi Jordan", duration="49m 49s",
             people=("Abhijeet Gandhi", "Suthi Nagar", "Brandi Jordan"), turns=9, transcript=True):
    head = (f"**Attendees:** {attendees}\n**Duration:** {duration}\n"
            "[https://t901.p.clickup-attachments.com/t901/abc/abc.mp4?view=open]"
            "(https://t901.p.clickup-attachments.com/t901/abc/abc.mp4?view=open)\n\n"
            "### Overview\n\nThe team reviewed the mockups.\n\n### Key Takeaways\n\n*   Adopted the design.\n\n"
            "### Next Steps\n\n- [ ] **Abhijeet Gandhi**: Send the copy document.\n\n### Key Topics\n\n*   Pricing\n\n")
    if not transcript:
        return head
    body = "*   Transcript\n\n"
    for i in range(turns):
        body += f"    **{people[i % len(people)]}:** Line {i} of the call,\n    carried over a wrap.\n\n"
    return head + body


def cu_doc(doc_id="8cq0xj3-100", name="TCC New Website - 11/17/2026",
           created=datetime(2026, 11, 16, 19, 22, tzinfo=UTC)):
    return {"id": doc_id, "name": name, "type": 3, "deleted": False, "workspace_id": 9016735299,
            "date_created": _ms(created), "parent": {"id": "9016735299", "type": 12}}


def fathom_payload(rid=205255647, *, start, end, speakers=("Abhijeet Gandhi", "Nick Esquivel")):
    return {"recording_id": rid, "title": "Impromptu Google Meet Meeting",
            "url": f"https://fathom.video/calls/{rid}", "created_at": cr._iso(end),
            "recording_start_time": cr._iso(start), "recording_end_time": cr._iso(end),
            "transcript": [{"speaker": {"display_name": s, "matched_calendar_invitee_email": None},
                            "text": "hello there", "timestamp": "00:00:01"} for s in speakers]}


class FakeFireflies:
    def __init__(self, transcripts, fail=False):
        self.t = {t["id"]: t for t in transcripts}
        self.fail, self.gets, self.lists = fail, [], []

    def list(self, email):
        self.lists.append(email)
        if self.fail:
            raise RuntimeError("Fireflies: down")
        return [{k: t[k] for k in ("id", "title", "date", "duration", "participants")}
                for t in self.t.values()]

    def get(self, native_id):
        self.gets.append(native_id)
        return self.t[native_id]


class FakeClickUp:
    def __init__(self, docs_pages):
        self.d = docs_pages                       # [(doc, markdown)]
        self.pages = []

    def docs(self):
        return [d for d, _ in self.d]

    def page(self, doc_id):
        self.pages.append(doc_id)
        md = next(m for d, m in self.d if d["id"] == doc_id)
        return ("pg1", md) if md else (None, "")


class Ctx:
    def __init__(self, db_path):
        self.db_path = db_path
        self.anomalies = []

    def anomaly(self, kind, severity, detail):
        self.anomalies.append((kind, severity, detail))


class Shapes(unittest.TestCase):
    def test_a_fireflies_transcript_becomes_a_fathom_meeting(self):
        p = cr.normalize_fireflies(ff())
        self.assertEqual((p["recorder"], p["recording_id"]), ("fireflies", "fireflies-01FFAAA"))
        self.assertEqual((p["recording_start_time"], p["recording_end_time"], p["created_at"]),
                         ("2026-11-20T20:15:00Z", "2026-11-20T20:45:00Z", "2026-11-20T20:45:00Z"))
        self.assertFalse(p["interval_estimated"])
        # two sentences by one speaker are one turn, stamped at its first sentence
        self.assertEqual(len(p["transcript"]), 8)
        self.assertEqual(p["transcript"][0], {
            "speaker": {"display_name": "Nick Esquivel", "matched_calendar_invitee_email": None},
            "text": "Point 0 first half. And the second half.", "timestamp": "00:00:03"})
        parsed = ingest.parse_fathom(p)                       # the unchanged reader
        self.assertEqual(parsed["source_ref"], "fathom/fireflies-01FFAAA")
        self.assertEqual(parsed["url"], "https://app.fireflies.ai/view/01FFAAA")
        self.assertTrue({"nick@globaltize.com", "abhijeet@globaltize.com", "Nick Esquivel",
                         "Abhijeet Gandhi"} <= set(parsed["participants"]))
        self.assertIn("[00:01:03] Abhijeet Gandhi: Point 1 first half.", parsed["text"])
        self.assertIsNone(cr.normalize_fireflies(ff(turns=1)))          # nothing said yet

    def test_rule_1_a_call_none_of_ours_was_on_is_not_ours(self):
        self.assertTrue(cr.is_ours(ff()))
        theirs = ff(emails=("faris@globaltize.com", "candidate@example.com"),
                    speakers=("Faris", "A Candidate"))
        self.assertFalse(cr.is_ours(theirs))
        self.assertEqual(config.FIREFLIES_PARTICIPANT_EMAILS, ("abhijeet@globaltize.com",))

    def test_a_notetaker_page_becomes_a_fathom_meeting(self):
        md = notes_md(attendees="Hermon Notes (Jaap's Bot) - transcribing, Abhijeet Gandhi, Brandi Jordan",
                      people=("Abhijeet Gandhi", "Brandi Jordan"))
        n = cr.parse_notes(md)
        self.assertEqual((n["seconds"], len(n["lines"])), (49 * 60 + 49, 9))
        self.assertEqual(n["lines"][0], ("Abhijeet Gandhi", "Line 0 of the call, carried over a wrap.", None))
        self.assertIn("### Next Steps", n["summary"])
        self.assertNotIn("Transcript", n["summary"])
        p = cr.normalize_clickup(cu_doc(), "pg1", md)
        self.assertEqual((p["recording_id"], p["title"]), ("clickup-8cq0xj3-100", "TCC New Website"))
        self.assertEqual(p["url"], "https://app.clickup.com/9016735299/v/dc/8cq0xj3-100/pg1")
        self.assertTrue(p["interval_estimated"])
        # made 19:22, the call ended about four minutes earlier and ran 49m49s
        self.assertEqual((p["recording_end_time"], p["recording_start_time"]),
                         ("2026-11-16T19:18:00Z", "2026-11-16T18:28:11Z"))
        self.assertEqual([x["name"] for x in p["participants"]], ["Abhijeet Gandhi", "Brandi Jordan"])  # no bot
        parsed = ingest.parse_fathom(p)
        self.assertEqual(parsed["source_ref"], "fathom/clickup-8cq0xj3-100")
        self.assertIn("Brandi Jordan: Line 1 of the call, carried over a wrap.", parsed["text"])
        self.assertIsNone(cr.normalize_clickup(cu_doc(), "pg1", notes_md(transcript=False)))
        self.assertEqual(cr.parse_notes("**Duration:** 1h 2m 3s\n")["seconds"], 3723)


class Twins(unittest.TestCase):
    S = datetime(2026, 11, 20, 20, 15, tzinfo=UTC)

    def test_the_same_call_on_two_recorders_is_one_call(self):
        a = fathom_payload(start=self.S - timedelta(minutes=2), end=self.S + timedelta(minutes=29))
        b = cr.normalize_fireflies(ff(start=self.S, minutes=30))
        self.assertTrue(cr.same_call(a, b))
        self.assertTrue(cr.same_call(b, a))
        # one recorder stopped early: still the same call
        short = fathom_payload(start=self.S, end=self.S + timedelta(minutes=12))
        self.assertTrue(cr.same_call(short, b))

    def test_the_old_fathom_export_shape_is_read_too(self):
        """The export says recording_start / recording_end / date, and its
        speakers are plain strings. Missing it made every 2025 call look new."""
        export = {"recording_id": "90374579", "title": "Weekly Check In | Chris, Simone",
                  "date": "2025-09-29T11:26:31Z", "recording_start": "2025-09-29T10:36:49Z",
                  "recording_end": "2025-09-29T11:26:24Z",
                  "participants": [{"name": "Abhijeet Gandhi"}],
                  "transcript": [{"timestamp": "00:00:00", "speaker": "Chris Cave", "text": "hello"},
                                 {"timestamp": "00:00:05", "speaker": "Abhijeet Gandhi", "text": "hi"}]}
        self.assertEqual([cr._iso(x) for x in cr.interval(export)],
                         ["2025-09-29T10:36:49Z", "2025-09-29T11:26:24Z"])
        doc = cu_doc("old", "Weekly Check In | Chris, Simone & Abhijeet - 9/29/2025",
                     datetime(2025, 9, 29, 11, 31, tzinfo=UTC))
        note = cr.normalize_clickup(doc, "pg1", notes_md(
            attendees="Chris Cave, Simone, Abhijeet Gandhi", duration="49m 35s",
            people=("Chris Cave", "Abhijeet Gandhi")))
        self.assertTrue(cr.same_call(note, export))

    def test_back_to_back_and_different_people_are_two_calls(self):
        b = cr.normalize_fireflies(ff(start=self.S, minutes=30))
        after = fathom_payload(start=self.S + timedelta(minutes=30), end=self.S + timedelta(minutes=60))
        self.assertFalse(cr.same_call(after, b))
        others = fathom_payload(start=self.S, end=self.S + timedelta(minutes=30),
                                speakers=("Jay Pokharna", "Somebody Else"))
        self.assertFalse(cr.same_call(others, b))

    def test_clickups_estimated_clock_may_drift_only_with_the_same_outsider(self):
        # a 10-minute call; ClickUp's doc appeared 14 minutes after it ended, so
        # its estimated window sits ~10 minutes late and overlaps nothing exactly
        real_end = datetime(2026, 11, 16, 19, 0, tzinfo=UTC)
        fath = fathom_payload(start=real_end - timedelta(minutes=10), end=real_end,
                              speakers=("Abhijeet Gandhi", "Brandi Jordan"))
        doc = cu_doc(created=real_end + timedelta(minutes=14))
        with_her = cr.normalize_clickup(doc, "pg1", notes_md(duration="10m 0s",
                                        people=("Abhijeet Gandhi", "Brandi Jordan"),
                                        attendees="Abhijeet Gandhi, Brandi Jordan"))
        ours = ("abhijeet gandhi", "jay pokharna")
        with mock.patch.object(config, "INTERNAL_PEOPLE", ours):
            self.assertTrue(cr.same_call(fath, with_her))
            internal = cr.normalize_clickup(doc, "pg1", notes_md(
                duration="10m 0s", people=("Abhijeet Gandhi", "Jay Pokharna"),
                attendees="Abhijeet Gandhi, Jay Pokharna"))
            self.assertFalse(cr.same_call(fath, internal))              # only we are shared


class Poll(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rec-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.inbox = self.tmp / "inbox" / "fathom"
        self.ctx = Ctx(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fathom_source(self, payload):
        raw = self.tmp / f"fathom_{payload['recording_id']}.json"
        raw.write_text(json.dumps(payload))
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT INTO sources (source_type, source_ref, content_hash, raw_path,"
                         " occurred_dt, status) VALUES ('fathom',?,?,?,?,'extracted')",
                         (f"fathom/{payload['recording_id']}", f"h{payload['recording_id']}",
                          str(raw), payload["created_at"]))

    def _rows(self):
        with db.connect(self.db_path) as conn:
            return {(r["recorder"], r["native_id"]): dict(r)
                    for r in conn.execute("SELECT * FROM call_recordings")}

    def _poll(self, fireflies, clickup, **kw):
        return cr.poll(self.ctx, fireflies=fireflies, clickup=clickup, now=kw.pop("now", NOW),
                       inbox=self.inbox, **kw)

    def test_saves_what_is_new_and_skips_what_is_already_held(self):
        s1 = datetime(2026, 11, 13, 20, 0, tzinfo=UTC)          # Fathom has this one
        s2 = datetime(2026, 11, 20, 20, 15, tzinfo=UTC)         # nobody has this one
        self._fathom_source(fathom_payload(rid=111, start=s1, end=s1 + timedelta(minutes=21)))
        fireflies = FakeFireflies([ff("FFOLD", start=s1, minutes=21), ff("FFNEW", start=s2, minutes=30)])
        # ClickUp recorded the 20 Nov call too, and one call of its own
        clickup = FakeClickUp([
            (cu_doc("doc-same", "Nick x Abhijeet - 11/21/2026", s2 + timedelta(minutes=35)),
             notes_md(attendees="Abhijeet Gandhi, Nick Esquivel", duration="30m 5s",
                      people=("Abhijeet Gandhi", "Nick Esquivel"))),
            (cu_doc("doc-own", "TCC New Website - 11/17/2026"), notes_md()),
        ])
        m = self._poll(fireflies, clickup)
        self.assertEqual(m["listed"], {"fireflies": 2, "clickup": 2})
        self.assertEqual(sorted(x["recording"] for x in m["saved"]),
                         ["clickup-doc-own", "fireflies-FFNEW"])
        self.assertEqual({t["recording"]: t["twin_of"] for t in m["twins"]},
                         {"fireflies-FFOLD": "111", "clickup-doc-same": "fireflies-FFNEW"})
        rows = self._rows()
        self.assertEqual({k: v["state"] for k, v in rows.items()},
                         {("fireflies", "FFOLD"): "twin", ("fireflies", "FFNEW"): "saved",
                          ("clickup", "doc-same"): "twin", ("clickup", "doc-own"): "saved"})
        self.assertEqual(sorted(p.name for p in self.inbox.iterdir()),
                         ["2026-11-16__clickup-doc-own.json", "2026-11-20__fireflies-FFNEW.json"])
        # settled: a second run lists, and reads nothing again
        fireflies.gets.clear(); clickup.pages.clear()
        m2 = self._poll(fireflies, clickup)
        self.assertEqual((m2["saved"], m2["twins"], fireflies.gets, clickup.pages), ([], [], [], []))

    def test_a_saved_recording_goes_through_the_unchanged_ingest_onto_its_client(self):
        self._poll(FakeFireflies([ff("FFNEW")]), FakeClickUp([]))
        path = self.inbox / "2026-11-20__fireflies-FFNEW.json"
        cmap = [{"client_id": "globatize", "name": "Globaltize LLC", "identifiers": ["globaltize"]}]
        out = ingest.ingest_file(path, db_path=self.db_path, client_map=cmap, source_type="fathom")
        self.assertEqual((out["status"], out["client_id"], out["source_ref"]),
                         ("ingested", "globatize", "fathom/fireflies-FFNEW"))
        # …and once it IS a source, the same call arriving from ClickUp is its twin
        clickup = FakeClickUp([(cu_doc("doc-late", "Nick x Abhijeet - 11/21/2026",
                                       datetime(2026, 11, 20, 20, 50, tzinfo=UTC)),
                                notes_md(attendees="Abhijeet Gandhi, Nick Esquivel", duration="30m 0s",
                                         people=("Abhijeet Gandhi", "Nick Esquivel")))])
        m = self._poll(FakeFireflies([ff("FFNEW")]), clickup)
        self.assertEqual([t["twin_of"] for t in m["twins"]], ["fireflies-FFNEW"])

    def test_waits_for_a_transcript_then_gives_up_and_lets_a_fresh_call_settle(self):
        fresh = cu_doc("doc-fresh", created=NOW - timedelta(minutes=10))      # Fathom may still come
        empty = cu_doc("doc-empty", created=NOW - timedelta(hours=5))
        clickup = FakeClickUp([(fresh, notes_md()), (empty, notes_md(transcript=False))])
        m = self._poll(FakeFireflies([]), clickup)
        self.assertEqual((m["settling"], m["waiting"], m["saved"]), (1, 1, []))
        self.assertEqual(clickup.pages, ["doc-empty"])                        # the fresh one was not read
        self.assertEqual(self._rows()[("clickup", "doc-empty")]["state"], "waiting")
        m = self._poll(FakeFireflies([]), clickup, now=NOW + timedelta(days=4))
        self.assertEqual(self._rows()[("clickup", "doc-empty")]["state"], "skipped")
        self.assertEqual([x["recording"] for x in m["saved"]], ["clickup-doc-fresh"])

    def test_rule_1_holds_in_the_poll_and_one_recorder_down_does_not_stop_the_other(self):
        theirs = ff("THEIRS", emails=("faris@globaltize.com", "candidate@example.com"),
                    speakers=("Faris", "A Candidate"))
        m = self._poll(FakeFireflies([theirs]), FakeClickUp([]))
        self.assertEqual((m["saved"], m["skipped"]), ([], 1))
        self.assertEqual(self._rows()[("fireflies", "THEIRS")]["state"], "skipped")
        self.assertFalse(self.inbox.exists())
        m = self._poll(FakeFireflies([], fail=True), FakeClickUp([(cu_doc("doc-own"), notes_md())]))
        self.assertIn("fireflies", m["errors"])
        self.assertEqual([x["recording"] for x in m["saved"]], ["clickup-doc-own"])

    def test_nothing_before_the_floor_is_read_or_recorded(self):
        old = cu_doc("doc-2025", "automated trading bot - 9/12/2025", datetime(2025, 9, 12, 15, 0, tzinfo=UTC))
        clickup = FakeClickUp([(old, notes_md()), (cu_doc("doc-own"), notes_md())])
        m = self._poll(FakeFireflies([]), clickup)
        self.assertEqual((m["before_floor"], [x["recording"] for x in m["saved"]]), (1, ["clickup-doc-own"]))
        self.assertEqual(clickup.pages, ["doc-own"])
        self.assertNotIn(("clickup", "doc-2025"), self._rows())          # so moving the floor brings it in
        with mock.patch.object(config, "CALL_RECORDER_SINCE", "2025-01-01"):
            m = self._poll(FakeFireflies([]), clickup)
        self.assertEqual([x["recording"] for x in m["saved"]], ["clickup-doc-2025"])

    def test_a_dry_run_writes_nothing_and_still_sees_this_runs_twins(self):
        s = datetime(2026, 11, 20, 20, 15, tzinfo=UTC)
        clickup = FakeClickUp([(cu_doc("doc-same", "Nick x Abhijeet - 11/21/2026", s + timedelta(minutes=35)),
                                notes_md(attendees="Abhijeet Gandhi, Nick Esquivel", duration="30m 5s",
                                         people=("Abhijeet Gandhi", "Nick Esquivel")))])
        m = self._poll(FakeFireflies([ff("FFNEW", start=s)]), clickup, dry_run=True)
        self.assertEqual(([x["recording"] for x in m["saved"]], [t["recording"] for t in m["twins"]]),
                         (["fireflies-FFNEW"], ["clickup-doc-same"]))
        self.assertEqual(self._rows(), {})
        self.assertFalse(self.inbox.exists())

    def test_the_daily_job_reports_a_recorder_that_is_down(self):
        with mock.patch.object(cr, "poll", return_value={"listed": {"fireflies": 0, "clickup": 2},
                               "saved": [{"title": "TCC New Website"}], "twins": [], "waiting": 0,
                               "skipped": 0, "settling": 0, "deferred": 0,
                               "errors": {"fireflies": "RuntimeError('down')"}}):
            out = daily._poll_recorders(self.ctx)
        self.assertEqual((out["saved"], out["saved_titles"]), (1, ["TCC New Website"]))
        self.assertEqual(self.ctx.anomalies[0][:2], ("call_recorder_error", "warn"))
        with mock.patch.object(cr, "poll", side_effect=RuntimeError("no key")):
            out = daily._poll_recorders(self.ctx)                            # never raises
        self.assertIn("poll", out["errors"])


if __name__ == "__main__":
    unittest.main()
