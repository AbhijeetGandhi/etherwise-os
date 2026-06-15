"""M1b tests — sync_state upserts (terminal protection), poll_inbox
classification (verbatim port cases), outcome composer, client token rules.
GraphQL transport and gateway are mocked; DB logic runs real on temp dirs.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core import config, db, runner
from core import claude_gateway as gw
from modules.upwork import outcome_capture, poll_inbox, sync_state
from modules.upwork import upwork_client


def iso_hours_ago(h):
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()


class M1bCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m1b-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self._patches = [
            mock.patch.object(config, "LOG_DIR", self.tmp / "logs"),
            mock.patch.object(config, "LOCK_DIR", self.tmp / "locks"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self, sql, args=()):
        with db.connect(self.db_path) as conn:
            return [dict(r) for r in conn.execute(sql, args)]


# ── sync_state ────────────────────────────────────────────────────────────────
def vp(pid="prop1", status="Pending", title="Make build"):
    return {"id": pid, "status": {"status": status},
            "chargeRate": {"amount": "1200", "currency": "USD"},
            "coverLetter": "hello", "job": {"id": "j1", "title": title},
            "client": {"companyName": "Acme", "country": "US",
                       "totalSpent": {"amount": "5000"}},
            "createdDateTime": "2026-06-01T00:00:00Z",
            "modifiedDateTime": "2026-06-10T00:00:00Z"}


class TestIsoDt(unittest.TestCase):
    def test_epoch_millis_to_iso(self):
        # "1779100550433" -> 2026-04-... ISO (the proposals.created_dt format)
        out = sync_state.iso_dt("1779100550433")
        self.assertTrue(out.startswith("2026-"))
        self.assertIn("T", out)

    def test_epoch_seconds_to_iso(self):
        self.assertTrue(sync_state.iso_dt("1779100550").startswith("20"))

    def test_iso_passthrough(self):
        self.assertEqual(sync_state.iso_dt("2026-06-09T23:12:05+00:00"),
                         "2026-06-09T23:12:05+00:00")

    def test_none_and_empty(self):
        self.assertIsNone(sync_state.iso_dt(None))
        self.assertIsNone(sync_state.iso_dt(""))


class TestSyncUpserts(M1bCase):
    def test_proposal_stores_iso_not_epoch(self):
        with db.connect(self.db_path) as conn:
            sync_state.upsert_proposal(
                conn, vp(pid="pX", status="Submitted"), "t1")
            # simulate Upwork handing epoch-millis like v2 does
            v = vp(pid="pE", status="Submitted")
            v["createdDateTime"] = "1779100550433"
            sync_state.upsert_proposal(conn, v, "t2")
            row = conn.execute("SELECT created_dt FROM proposals WHERE id='pE'"
                               ).fetchone()
        self.assertTrue(row["created_dt"].startswith("2026-"))  # ISO, not epoch
        self.assertNotIn("17791", row["created_dt"])

    def test_proposal_insert_then_update(self):
        with db.connect(self.db_path) as conn:
            self.assertEqual(sync_state.upsert_proposal(conn, vp(), "t1"),
                             "inserted")
            self.assertEqual(
                sync_state.upsert_proposal(conn, vp(status="Active"), "t1"),
                "updated")
        row = self.rows("SELECT * FROM proposals")[0]
        self.assertEqual(row["upwork_status"], "Active")
        self.assertEqual(row["thread_id"], "t1")

    def test_terminal_status_never_flipped(self):
        with db.connect(self.db_path) as conn:
            sync_state.upsert_proposal(conn, vp(), "t1")
            conn.execute("UPDATE proposals SET status='Won' WHERE id='prop1'")
        with db.connect(self.db_path) as conn:
            r = sync_state.upsert_proposal(conn, vp(status="Declined"), "t1")
        self.assertEqual(r, "terminal-preserved")
        row = self.rows("SELECT status, upwork_status FROM proposals")[0]
        self.assertEqual(row["status"], "Won")            # verdict permanent
        self.assertEqual(row["upwork_status"], "Declined")  # telemetry moves

    def test_transaction_dedup_key_profile_scoped(self):
        row = {"recordId": "T1", "amount": {"amount": "100"},
               "createdDateTime": "2026-06-10"}
        k1 = sync_state.build_dedup_key(row, "personal")
        k2 = sync_state.build_dedup_key(row, "agency")
        self.assertNotEqual(k1, k2)
        with db.connect(self.db_path) as conn:
            self.assertEqual(sync_state.upsert_transaction(
                conn, row, "personal"), "inserted")
            self.assertEqual(sync_state.upsert_transaction(
                conn, row, "personal"), "updated")
            self.assertEqual(sync_state.upsert_transaction(
                conn, row, "agency"), "inserted")


# ── poll_inbox classification (verbatim cases) ────────────────────────────────
def room(rid="r1", contract_status=None, proposal_status=None,
         latest_hours=24):
    r = {"id": rid, "roomName": "Room", "topic": "Topic",
         "latestStory": {"id": "s-l",
                         "createdDateTime": iso_hours_ago(latest_hours)}}
    if contract_status:
        r["contract"] = {"id": "c1", "status": contract_status}
    if proposal_status:
        r["vendorProposal"] = {"id": "p1",
                               "status": {"status": proposal_status}}
    return r


class TestClassification(M1bCase):
    def seed_messages(self, thread_id, directions, hours_start=100):
        """directions oldest->newest; timestamps ascending."""
        with db.connect(self.db_path) as conn:
            conn.execute("INSERT OR IGNORE INTO threads (id) VALUES (?)",
                         (thread_id,))
            for i, d in enumerate(directions):
                conn.execute(
                    "INSERT INTO messages (id, thread_id, message,"
                    " created_dt, sender_user_id, direction)"
                    " VALUES (?,?,?,?,?,?)",
                    (f"{thread_id}-m{i}", thread_id, f"msg {i}",
                     iso_hours_ago(hours_start - i * 10),
                     config.UPWORK_USER_ID if d == "outbound" else "client9",
                     d))

    def test_tiers(self):
        self.assertEqual(poll_inbox.compute_tier(
            room(contract_status="ACTIVE")), "HOT")
        self.assertEqual(poll_inbox.compute_tier(
            room(proposal_status="Offered")), "HOT")
        self.assertEqual(poll_inbox.compute_tier(
            room(proposal_status="Pending")), "WARM")
        self.assertEqual(poll_inbox.compute_tier(
            room(contract_status="CLOSED")), "COLD")
        self.assertEqual(poll_inbox.compute_tier(room()), "UNKNOWN")

    def test_owed_when_client_sent_last(self):
        self.seed_messages("r1", ["outbound", "inbound"])
        with db.connect(self.db_path) as conn:
            r = room(proposal_status="Pending", latest_hours=20)
            awaiting = poll_inbox.compute_awaiting(conn, r)
            self.assertEqual(awaiting, "Abhijeet")
            bucket, _ = poll_inbox.classify_thread(conn, r, "WARM", awaiting)
        self.assertEqual(bucket, "owed")

    def test_followup_thresholds_by_tier(self):
        self.seed_messages("r1", ["inbound", "outbound"])
        with db.connect(self.db_path) as conn:
            r = room(rid="r1", contract_status="ACTIVE", latest_hours=50)
            b, _ = poll_inbox.classify_thread(conn, r, "HOT", "Client")
            self.assertEqual(b, "followup")          # 50h > 48h HOT
            r2 = room(rid="r1", proposal_status="Pending", latest_hours=50)
            b2, _ = poll_inbox.classify_thread(conn, r2, "WARM", "Client")
            self.assertEqual(b2, "healthy")          # 50h < 72h WARM

    def test_max_two_consecutive_outbound(self):
        self.seed_messages("r1", ["inbound", "outbound", "outbound"])
        with db.connect(self.db_path) as conn:
            r = room(rid="r1", contract_status="ACTIVE", latest_hours=100)
            b, reason = poll_inbox.classify_thread(conn, r, "HOT", "Client")
        self.assertEqual(b, "healthy")
        self.assertIn("2 prior followups", reason)

    def test_too_soon_under_12h(self):
        self.seed_messages("r1", ["inbound", "outbound"])
        with db.connect(self.db_path) as conn:
            r = room(rid="r1", contract_status="ACTIVE", latest_hours=6)
            b, _ = poll_inbox.classify_thread(conn, r, "HOT", "Client")
        self.assertEqual(b, "healthy")

    def test_snooze_respected(self):
        self.seed_messages("r1", ["inbound"])
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE threads SET snooze_until=date('now','+3 day')"
                         " WHERE id='r1'")
        with db.connect(self.db_path) as conn:
            b, _ = poll_inbox.classify_thread(
                conn, room(rid="r1", latest_hours=99), "WARM", "Abhijeet")
        self.assertEqual(b, "snoozed")

    def test_stale_thread_left_alone(self):
        self.seed_messages("r1", ["inbound"], hours_start=31 * 24)
        with db.connect(self.db_path) as conn:
            r = room(rid="r1", latest_hours=31 * 24)
            awaiting = poll_inbox.compute_awaiting(conn, r)
            self.assertEqual(awaiting, "None")
            b, _ = poll_inbox.classify_thread(conn, r, "UNKNOWN", awaiting)
        self.assertEqual(b, "hard-skip")


# ── poll task end-to-end (mocked transport + gateway) ─────────────────────────
class TestPollTask(M1bCase):
    def test_digest_intent_recorded_in_shadow(self):
        r = room(rid="r1", proposal_status="Pending", latest_hours=20)
        list_resp = {"roomList": {"edges": [{"node": r}]}}
        stories_resp = {"room": {"stories": {"edges": [
            {"node": {"id": "s1", "message": "we sent",
                      "createdDateTime": iso_hours_ago(40),
                      "user": {"id": config.UPWORK_USER_ID, "name": "A"}}},
            {"node": {"id": "s2", "message": "client replied",
                      "createdDateTime": iso_hours_ago(20),
                      "user": {"id": "client9", "name": "C"}}},
        ]}}}

        fake_client = mock.Mock()
        # stories fetch uses tolerate_errors=True -> full {data, errors} envelope
        fake_client.graphql.side_effect = [list_resp,
                                           {"data": stories_resp}]

        def fake_claude(**kw):
            if kw.get("schema"):
                return gw.GatewayResult(
                    text="{}", parsed={"intent": "new_lead",
                                       "summary": "prospect asks for quote"},
                    model="m", route="payg", usage={}, cost_usd=0.001,
                    stop_reason="end_turn", duration_ms=5)
            return gw.GatewayResult(
                text="Thanks for the reply — here's the next step.",
                parsed=None, model="m", route="payg", usage={},
                cost_usd=0.001, stop_reason="end_turn", duration_ms=5)

        with mock.patch.object(runner, "claude_call",
                               side_effect=lambda **kw: fake_claude(**kw)):
            result = runner.run_task(
                poll_inbox.TASK_NAME,
                lambda ctx: poll_inbox.poll(
                    ctx, client_factory=lambda **k: fake_client),
                module="upwork", db_path=self.db_path)

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metrics["buckets"].get("owed"), 1)
        self.assertEqual(result.metrics["drafts"], 1)
        drafts = self.rows("SELECT * FROM drafts")
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["draft_kind"], "owed")
        ledger = self.rows("SELECT * FROM shadow_ledger WHERE target='email'")
        self.assertEqual(len(ledger), 1)
        payload = json.loads(ledger[0]["payload_json"])
        self.assertEqual(payload["to"], config.NOTIFY_EMAIL)
        self.assertEqual(payload["items"][0]["intent"]["intent"], "new_lead")


# ── outcome composer ──────────────────────────────────────────────────────────
class TestOutcome(M1bCase):
    def seed_proposal(self, pid, status="Lost", reason=None, rec="recABC"):
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO proposals (id, status, status_reason, job_title,"
                " client_company, airtable_record_id, modified_dt,"
                " updated_at) VALUES (?,?,?,?,?,?,datetime('now'),"
                " datetime('now'))",
                (pid, status, reason, "Job T", "Acme", rec))

    def test_prefill_link_shape(self):
        link = outcome_capture.prefill_link("https://airtable.com/shrX",
                                            "rec123")
        self.assertIn("prefill_Proposal=rec123", link)
        self.assertIn("hide_Proposal=true", link)

    def test_composer_shadow_intent(self):
        self.seed_proposal("p1")
        self.seed_proposal("p2", status="Won")
        self.seed_proposal("p3", status="Lost", reason="price")  # has reason
        with mock.patch.object(outcome_capture, "form_url",
                               return_value="https://airtable.com/shrX"):
            result = runner.run_task(
                outcome_capture.TASK_NAME, outcome_capture.capture,
                module="upwork", db_path=self.db_path)
        self.assertEqual(result.metrics["candidates"], 2)
        self.assertEqual(result.metrics["with_links"], 2)
        ledger = self.rows(
            "SELECT * FROM shadow_ledger WHERE entity='outcome_capture'")
        self.assertEqual(len(ledger), 1)

    def test_missing_form_url_flagged(self):
        self.seed_proposal("p1")
        with mock.patch.object(outcome_capture, "form_url",
                               return_value=""):
            result = runner.run_task(
                outcome_capture.TASK_NAME, outcome_capture.capture,
                module="upwork", db_path=self.db_path)
        self.assertTrue(result.metrics["form_url_missing"])
        self.assertEqual(result.metrics["with_links"], 0)

    def test_registry_form_url_pending_parses_empty(self):
        self.assertEqual(outcome_capture.form_url(), "")


# ── client token rules ────────────────────────────────────────────────────────
class TestClientToken(M1bCase):
    def test_refresh_refused_while_v2_owns(self):
        with mock.patch.object(upwork_client, "read_access_token",
                               return_value="tok"), \
             mock.patch.object(upwork_client, "token_owner",
                               return_value="v2"):
            c = upwork_client.UpworkClient()
            with self.assertRaises(upwork_client.UpworkAPIError):
                c.refresh()

    def test_default_owner_is_v2(self):
        with mock.patch.object(upwork_client, "OWNER_MARKER",
                               self.tmp / "absent"):
            self.assertEqual(upwork_client.token_owner(), "v2")


if __name__ == "__main__":
    unittest.main()
