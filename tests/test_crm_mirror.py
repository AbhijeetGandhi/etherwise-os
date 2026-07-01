"""CRM Airtable mirror tests — one-way copy-out, dedup, hands-off preservation,
authoritative money, per-table gates. The Airtable client + shadow recorder are
injected; DB logic runs real on a temp DB. No network, no live writes."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config, db
from modules.crm import mirror


class FakeCtx:
    def __init__(self, db_path):
        self.db_path = db_path
        self.run_id = 1
        self.shadow = True
        self.shadow_writes = []

    def record_shadow_write(self, target, operation, entity, entity_key, payload):
        self.shadow_writes.append({"target": target, "operation": operation,
                                   "entity": entity, "entity_key": entity_key,
                                   "payload": payload})


class FakeClient:
    def __init__(self, at_by_table):
        self._t = at_by_table
        self.writes = []

    def list_records(self, base, table):
        return self._t.get(table, [])

    def engine_writer(self, base, table):
        def w(method, path, payload):
            self.writes.append((method, table, payload))
            return {"records": payload.get("records", [])}
        return w


class MirrorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="crm-mirror-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        self.ctx = FakeCtx(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _exec(self, sql, args=()):
        with db.connect(self.db_path) as conn:
            conn.execute(sql, args)

    def _seed_proposal(self, pid, status="Applied", **kw):
        self._exec(
            "INSERT INTO proposals (id, status, job_title, client_company,"
            " client_country, client_total_spent, created_dt) VALUES (?,?,?,?,?,?,?)",
            (pid, status, kw.get("job_title", "Build X"),
             kw.get("client_company", "Acme"), kw.get("client_country", "US"),
             kw.get("client_total_spent", 5000.0),
             kw.get("created_dt", "2026-06-01T00:00:00+0000")))

    def _seed_txn(self, rid, amount_minor, canonical, **kw):
        self._exec(
            "INSERT INTO transactions (record_id, canonical_key, type,"
            " amount_minor, status, profile, source, description, dedup_key,"
            " creation_dt) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, canonical, kw.get("type", "Hourly Earning"), amount_minor,
             kw.get("status", "Completed"), kw.get("profile", "Personal"),
             kw.get("source", "Upwork"), kw.get("description", "work"),
             kw.get("dedup_key", f"upwork-csv:{rid}"),
             kw.get("creation_dt", "2026-06-10")))

    # ── shadow: intents recorded, zero live writes ──────────────────────────
    def test_shadow_records_no_live_write(self):
        self._seed_proposal("P1")
        client = FakeClient({})                       # no AT rows -> create intent
        with mock.patch.dict(config.SHADOW_MODE, {"upwork": True}):
            res = mirror.mirror_table(self.ctx, client, "proposals",
                                      mirror.SPECS["proposals"])
        self.assertTrue(res["shadow"])
        self.assertEqual(client.writes, [])           # nothing hit Airtable
        self.assertEqual(len(self.ctx.shadow_writes), 1)
        w = self.ctx.shadow_writes[0]
        self.assertEqual((w["target"], w["operation"]), ("airtable", "create"))
        self.assertEqual(w["entity_key"], "P1")
        self.assertEqual(w["payload"]["Upwork Proposal ID"], "P1")

    # ── dedup: match v2's row by key -> UPDATE, new row -> CREATE ────────────
    def test_dedup_update_vs_create(self):
        self._seed_proposal("P1", status="Interview")   # exists in AT (stale)
        self._seed_proposal("P2", status="Applied")      # not in AT
        at = {config.AT["proposals"]: [
            {"id": "recAAA", "fields": {           # fully populated except Status
                "Upwork Proposal ID": "P1", "Status": "Applied",
                "Job Title": "Build X", "Client Name": "Acme",
                "Client Country": "US", "Client Total Spent": 5000.0,
                "Date Submitted": "2026-06-01"}}]}
        client = FakeClient(at)
        res = mirror.mirror_table(self.ctx, client, "proposals",
                                  mirror.SPECS["proposals"])
        ops = {w["entity_key"]: w for w in self.ctx.shadow_writes}
        self.assertEqual(ops["P1"]["operation"], "update")   # matched v2 row
        self.assertEqual(ops["P1"]["payload"]["Status"], "Interview")  # only the diff
        self.assertNotIn("Job Title", ops["P1"]["payload"])  # unchanged field not sent
        self.assertEqual(ops["P2"]["operation"], "create")   # brand new
        self.assertEqual(res["local_rows"], 2)

    # ── hands-off fields never appear in any payload ────────────────────────
    def test_hands_off_never_written(self):
        self._seed_proposal("P1")
        client = FakeClient({})
        mirror.mirror_table(self.ctx, client, "proposals",
                            mirror.SPECS["proposals"])
        for w in self.ctx.shadow_writes:
            for banned in mirror.HANDS_OFF["proposals"]:
                self.assertNotIn(banned, w["payload"])       # incl. Lead Score

    # ── transactions: authoritative amount_minor -> Amount USD; upwork-only ──
    def test_transactions_money_and_scope(self):
        self._seed_txn("T1", -5600, "upwork:T1")             # -$56.00 fee
        self._seed_txn("E1", 120000, "upwork:E1")            # +$1200.00 earning
        self._seed_txn("B1", 999, "bank:B1", source="IDFC")  # NOT upwork -> skip
        client = FakeClient({})
        res = mirror.mirror_table(self.ctx, client, "transactions",
                                  mirror.SPECS["transactions"])
        keys = {w["entity_key"]: w["payload"] for w in self.ctx.shadow_writes}
        self.assertEqual(set(keys), {"T1", "E1"})            # bank row excluded
        self.assertEqual(keys["T1"]["Amount USD"], -56.0)    # from amount_minor
        self.assertEqual(keys["E1"]["Amount USD"], 1200.0)
        self.assertEqual(res["local_rows"], 2)

    # ── live path (gate off): writes go to Airtable, not shadow ─────────────
    def test_live_writes_when_gate_off(self):
        self._seed_proposal("P1")
        client = FakeClient({})
        with mock.patch.dict(config.SHADOW_MODE, {"upwork": False}):
            res = mirror.mirror_table(self.ctx, client, "proposals",
                                      mirror.SPECS["proposals"])
        self.assertFalse(res["shadow"])
        self.assertEqual(self.ctx.shadow_writes, [])          # nothing shadowed
        self.assertEqual(len(client.writes), 1)               # one POST batch
        method, table, payload = client.writes[0]
        self.assertEqual(method, "POST")
        self.assertTrue(payload["typecast"])
        fields = payload["records"][0]["fields"]
        for banned in mirror.HANDS_OFF["proposals"]:
            self.assertNotIn(banned, fields)                  # hands-off even live

    # ── transactions gate is independent of the upwork gate ─────────────────
    def test_transactions_gate_decoupled(self):
        self._seed_txn("T1", 1000, "upwork:T1")
        client = FakeClient({})
        # upwork LIVE but crm_transactions still SHADOW -> txn stays shadowed
        with mock.patch.dict(config.SHADOW_MODE,
                             {"upwork": False, "crm_transactions": True}):
            res = mirror.mirror_table(self.ctx, client, "transactions",
                                      mirror.SPECS["transactions"])
        self.assertTrue(res["shadow"])                        # gated by its own flag
        self.assertEqual(client.writes, [])

    # ── orphans reported, never deleted ─────────────────────────────────────
    def test_orphans_reported_not_deleted(self):
        self._seed_proposal("P1")
        at = {config.AT["proposals"]: [
            {"id": "recOLD", "fields": {"Upwork Proposal ID": "GONE1",
                                        "Status": "Lost"}}]}
        client = FakeClient(at)
        res = mirror.mirror_table(self.ctx, client, "proposals",
                                  mirror.SPECS["proposals"])
        self.assertEqual(res["orphans"], 1)
        self.assertIn("GONE1", res["orphan_sample"])
        # execute_push has no delete path -> no DELETE ever issued
        self.assertFalse(any(m == "DELETE" for m, _, _ in client.writes))


if __name__ == "__main__":
    unittest.main()
