"""Drafts follow his ClickUp board (2026-11-02).

Abhijeet: "you have drafts ready to send. check their clickup status — if I
have skipped that or moved them to another bucket it should be updated."
Pinned here:

  * the reader stores the card's live status, its type, and the list it sits
    in; a 404 is `deleted`; a terminal status is never re-read; the token is
    not even fetched when nothing is carded (test DBs, offline runs);
  * "ready to send" keeps a draft only while its card is `new` (an unread card
    counts as new); Today's five also drop a card he moved to Low Priority,
    while the full Upwork › Jobs list keeps it with the bucket shown;
  * the pass rides upwork_sync and a ClickUp failure never fails that sync.
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from core import config, db
from modules.cockpit import data
from modules.upwork import card_status

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import clickup_push  # noqa: E402  (the script is a bare module, not a package)

NOW = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
TODAY = "2026-11-02"
J = [f"20669000000001{n:05d}" for n in range(1, 12)]
HOT = config.HOT_LEAD_THRESHOLD


class _Ctx:
    def __init__(self, db_path):
        self.db_path = db_path
        self.logs = []

    def log(self, m):
        self.logs.append(m)


def _job(conn, jid, *, day=TODAY, score=HOT + 2, carded=True, draft=True,
         card_status=None, card_list=None):
    conn.execute(
        "INSERT INTO scored_jobs (id, title, score, status, clickup_task_id,"
        " clickup_task_url, draft_proposal, fetched_at, first_scored_at, job_url,"
        " card_status, card_list_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (jid, f"job {jid[-3:]}", score, "Drafted",
         f"cu{jid[-3:]}" if carded else None,
         f"https://app.clickup.com/t/cu{jid[-3:]}" if carded else None,
         "Drafted proposal text." if draft else None,
         f"{day}T10:00:00+00:00", f"{day} 10:05:00",
         f"https://www.upwork.com/jobs/x_~02{jid}/", card_status, card_list))


def _fake_req(board):
    """board: task_id -> (status, type, list_id) | None (404)."""
    calls = []

    def req(method, path, tok, body=None):
        calls.append((method, path))
        tid = path.rsplit("/", 1)[-1]
        hit = board.get(tid)
        if hit is None:
            raise urllib.error.HTTPError(path, 404, "not found", {}, io.BytesIO(b""))
        st, ty, lid = hit
        return {"id": tid, "status": {"status": st, "type": ty}, "list": {"id": lid}}
    req.calls = calls
    return req


class TheReader(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cards-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.L = config.CLICKUP_LISTS

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _row(self, jid):
        with db.connect(self.db_path) as conn:
            return dict(conn.execute(
                "SELECT card_status, card_status_type, card_list_id, card_checked_at"
                " FROM scored_jobs WHERE id=?", (jid,)).fetchone())

    def test_stores_status_type_and_list_and_marks_a_gone_card_deleted(self):
        with db.connect(self.db_path) as conn:
            _job(conn, J[0]); _job(conn, J[1]); _job(conn, J[2]); _job(conn, J[3])
            _job(conn, J[4], carded=False)                     # nothing to read
        req = _fake_req({"cu001": ("new", "open", self.L["hot"]),
                         "cu002": ("Skipped", "done", self.L["hot"]),
                         "cu003": ("new", "open", self.L["low"])})   # cu004 → 404
        tokens = []
        m = card_status.sync(_Ctx(self.db_path), self.db_path, req=req,
                             token=lambda: tokens.append(1) or "t", now=NOW, sleep=lambda s: None)
        self.assertEqual((m["read"], m["deleted"], m["errors"], m["changed"]), (3, 1, 0, 3))
        self.assertEqual(m["by_status"], {"new": 2, "skipped": 1})
        self.assertEqual(self._row(J[0]), {"card_status": "new", "card_status_type": "open",
                                           "card_list_id": self.L["hot"],
                                           "card_checked_at": "2026-11-02 18:00:00"})
        self.assertEqual(self._row(J[1])["card_status"], "skipped")      # lower-cased
        self.assertEqual(self._row(J[2])["card_list_id"], self.L["low"])  # the move is visible
        self.assertEqual(self._row(J[3])["card_status"], "deleted")
        self.assertIsNone(self._row(J[4])["card_checked_at"])
        self.assertEqual(len(tokens), 1)
        self.assertEqual(len(req.calls), 4)

    def test_terminal_cards_are_never_re_read_and_no_card_means_no_token(self):
        with db.connect(self.db_path) as conn:
            _job(conn, J[0], card_status="skipped")
            _job(conn, J[1], card_status="won")
            _job(conn, J[2], card_status="applied")              # can still change
            _job(conn, J[3], day="2026-08-01")                   # too old to care
        req = _fake_req({"cu003": ("interview", "custom", self.L["hot"])})
        m = card_status.sync(_Ctx(self.db_path), self.db_path, req=req, token=lambda: "t",
                             now=NOW, sleep=lambda s: None)
        self.assertEqual([p for _, p in req.calls], ["/task/cu003"])
        self.assertEqual(m["by_status"], {"interview": 1})
        # an empty DB never asks for the token (the credentials file may be absent)
        with db.connect(self.db_path) as conn:
            conn.execute("DELETE FROM scored_jobs")

        def boom():
            raise SystemExit("CLICKUP_API_KEY not found")
        m = card_status.sync(_Ctx(self.db_path), self.db_path, req=req, token=boom, now=NOW)
        self.assertEqual(m["read"], 0)

    def test_a_clickup_error_is_counted_not_raised(self):
        with db.connect(self.db_path) as conn:
            _job(conn, J[0])

        def req(method, path, tok, body=None):
            raise urllib.error.HTTPError(path, 429, "slow down", {}, io.BytesIO(b""))
        ctx = _Ctx(self.db_path)
        m = card_status.sync(ctx, self.db_path, req=req, token=lambda: "t", now=NOW)
        self.assertEqual((m["errors"], m["read"]), (1, 0))
        self.assertIsNone(self._row(J[0])["card_checked_at"])
        self.assertTrue(any("HTTP 429" in x for x in ctx.logs))

    def test_the_sync_hook_survives_a_missing_token(self):
        """upwork_sync must complete even when the cards pass cannot start."""
        from modules.upwork import sync_state
        with db.connect(self.db_path) as conn:
            _job(conn, J[0])

        class _Client:
            def graphql(self, q, v):
                return {"roomList": {"edges": []}}
        with mock.patch.object(clickup_push, "token", side_effect=SystemExit("no key")):
            out = sync_state.sync(_Ctx(self.db_path), client_factory=_Client,
                                  mcp_session_factory=None)
        self.assertEqual(out["cards"]["status"], "error")
        self.assertIn("SystemExit", out["cards"]["error"])


class TheReaders(unittest.TestCase):
    """What Today and Upwork › Jobs do with the stored card state."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cards-readers-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        L = config.CLICKUP_LISTS
        with db.connect(self.db_path) as conn:
            _job(conn, J[0])                                         # unread → counts as new
            _job(conn, J[1], card_status="new", card_list=L["hot"])
            _job(conn, J[2], card_status="skipped", card_list=L["hot"])
            _job(conn, J[3], card_status="applied", card_list=L["hot"])
            _job(conn, J[4], card_status="expired", card_list=L["standard"])
            _job(conn, J[5], card_status="new", card_list=L["low"])  # moved to Low Priority
            _job(conn, J[6], card_status="deleted")
            _job(conn, J[7], carded=False)                           # never pushed; still a draft

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_upwork_jobs_keeps_only_new_cards_and_shows_the_bucket(self):
        r = data.upwork_rows(self.db_path, stage="drafts_waiting", _today=TODAY)
        self.assertEqual({row["id"] for row in r["rows"]}, {J[0], J[1], J[5], J[7]})
        by = {row["id"]: row for row in r["rows"]}
        self.assertEqual(by[J[1]]["bucket"], "Hot")
        self.assertEqual(by[J[5]]["bucket"], "Low Priority")
        self.assertIsNone(by[J[0]]["bucket"])
        self.assertEqual(by[J[1]]["card_status"], "new")
        self.assertEqual(data.upwork(self.db_path, TODAY)["needs_me"]["drafts_waiting"]["count"], 4)

    def test_today_also_drops_a_card_moved_to_low_priority(self):
        with mock.patch.object(config, "DB_PATH", self.db_path):
            out = data.today(self.db_path, TODAY)
        dr = out["drafts_ready"]
        self.assertEqual({row["id"] for row in dr["items"]}, {J[0], J[1], J[7]})
        self.assertEqual(dr["total"], 3)
        self.assertIn("bucket", dr["items"][0])
        self.assertIn("clickup_task_url", dr["items"][0])

    def test_the_bucket_words(self):
        L = config.CLICKUP_LISTS
        self.assertEqual(data._card_bucket(L["icp"]), "ICP / Prime")
        self.assertEqual(data._card_bucket(L["invites"]), "Invites")
        self.assertIsNone(data._card_bucket(None))
        self.assertIsNone(data._card_bucket("nope"))


if __name__ == "__main__":
    unittest.main()
