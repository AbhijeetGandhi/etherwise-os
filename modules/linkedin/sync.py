"""Copy the LinkedIn engagement tracker out of Airtable. Read-only, idempotent.

The tracker (base applkzewYyLbRmcss) is OWNED by the social sessions, which
edit it by hand while they work. This module only reads it — every two hours,
12:05-04:05 IST (launchd/io.etherwise.v3.linkedin-sync.plist). Spec:
etherwise-v3-linkedin-brief.md.

ONE VERB. The only Airtable call in this file is `_ReadOnly.list`, which wraps
AirtableClient.list_records. There is no create, update or delete anywhere in
the module, and tests/test_linkedin.py fails the build if one appears — the
same "refuse it in code, not by convention" rule as the hours client.

WHAT A RUN DOES
  1. Reads EVERY table first. A table that fails to read fails the run before
     anything is written, so a half-read can never mark live rows gone.
  2. Upserts each record into li_records (fields as JSON under their Airtable
     names), and the day's copy into li_snapshots — Airtable keeps only the
     latest value of a metric, so the history has to be kept here.
  3. Marks a record Airtable stopped returning `gone_at` (never deletes it).
     Except when a table that HAD live rows comes back completely empty: that
     is far likelier a permissions or API hiccup than the social agent
     deleting a whole table, so nothing is marked and the run says so.
  4. Checks the fields the readers depend on are still there
     (config.LINKEDIN_REQUIRED_FIELDS). A rename in Airtable is filed as the
     `linkedin_schema_drift` degrade, which turns the source amber, instead of
     the tab quietly reading zeros.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Callable, Optional

from core import config, db, runner

TASK_NAME = "linkedin_sync"
DRIFT_KIND = "linkedin_schema_drift"


class _ReadOnly:
    """The module's only door to Airtable, and it opens one way."""

    def __init__(self, client=None):
        from core.airtable_client import AirtableClient
        self._c = client or AirtableClient()

    def list(self, base: str, table: str) -> list:
        return self._c.list_records(base, table)


def _now() -> str:
    return dt.datetime.now(config.TZ).strftime("%Y-%m-%d %H:%M:%S")


def _missing_fields(key: str, recs: list) -> list:
    """Required fields absent from EVERY record. Airtable omits empty cells,
    so one record without a field proves nothing; all of them without it does.
    An empty table proves nothing either and is skipped."""
    need = config.LINKEDIN_REQUIRED_FIELDS.get(key, ())
    if not need or not recs:
        return []
    present = set()
    for r in recs:
        present.update((r.get("fields") or {}).keys())
    return [f for f in need if f not in present]


def store(conn, fetched: dict, now: str) -> dict:
    """Write one complete read into the store. Pure over (conn, fetched, now),
    so the tests drive it without a network. Returns per-table counts plus the
    drift and refusals the run should report."""
    day = now[:10]
    out, drift, refused = {}, {}, []
    for key, recs in fetched.items():
        ins = upd = changed = 0
        seen = set()
        for r in recs:
            rid = r.get("id")
            if not rid:
                continue
            seen.add(rid)
            fj = json.dumps(r.get("fields") or {}, sort_keys=True,
                            ensure_ascii=False)
            row = conn.execute(
                "SELECT fields_json FROM li_records WHERE table_key=? AND"
                " record_id=?", (key, rid)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO li_records (table_key, record_id, fields_json,"
                    " created_time, first_seen_at, updated_at, changed_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (key, rid, fj, r.get("createdTime"), now, now, now))
                ins += 1
            else:
                differs = row[0] != fj
                conn.execute(
                    "UPDATE li_records SET fields_json=?, updated_at=?,"
                    " gone_at=NULL, changed_at=CASE WHEN ? THEN ? ELSE"
                    " changed_at END WHERE table_key=? AND record_id=?",
                    (fj, now, 1 if differs else 0, now, key, rid))
                upd += 1
                changed += 1 if differs else 0
            conn.execute(
                "INSERT INTO li_snapshots (day, table_key, record_id, fields_json)"
                " VALUES (?,?,?,?) ON CONFLICT(day, table_key, record_id)"
                " DO UPDATE SET fields_json=excluded.fields_json",
                (day, key, rid, fj))

        live_before = conn.execute(
            "SELECT COUNT(*) FROM li_records WHERE table_key=? AND gone_at IS NULL",
            (key,)).fetchone()[0]
        gone = 0
        if not recs and live_before:
            refused.append(key)
        else:
            for (rid,) in conn.execute(
                    "SELECT record_id FROM li_records WHERE table_key=? AND"
                    " gone_at IS NULL", (key,)).fetchall():
                if rid not in seen:
                    conn.execute(
                        "UPDATE li_records SET gone_at=? WHERE table_key=? AND"
                        " record_id=?", (now, key, rid))
                    gone += 1
        miss = _missing_fields(key, recs)
        if miss:
            drift[key] = miss
        out[key] = {"read": len(recs), "new": ins, "changed": changed,
                    "gone": gone}
    return {"tables": out, "drift": drift, "refused_empty": refused}


def sync(ctx, *, _reader: Optional[Callable] = None) -> dict:
    reader = _reader or _ReadOnly().list
    fetched = {}
    for key, table_id in config.LINKEDIN_TABLES.items():
        fetched[key] = reader(config.LINKEDIN_BASE, table_id)
        ctx.log(f"linkedin: {key} -> {len(fetched[key])} records")

    with db.connect(ctx.db_path) as conn:
        res = store(conn, fetched, _now())

    if res["drift"] or res["refused_empty"]:
        ctx.anomaly(DRIFT_KIND, "warn", {
            "missing_fields": res["drift"],
            "came_back_empty": res["refused_empty"],
            "what_to_do": "a field the cockpit reads was renamed or removed in"
                          " Airtable (or a table returned nothing). Rename it"
                          " back, or update config.LINKEDIN_REQUIRED_FIELDS and"
                          " the reader in modules/cockpit/linkedin.py."})
    return {"tables": res["tables"],
            "records": sum(t["read"] for t in res["tables"].values()),
            "changed": sum(t["changed"] + t["new"] for t in res["tables"].values()),
            "drift": res["drift"], "refused_empty": res["refused_empty"]}


def main() -> int:
    res = runner.run_task(TASK_NAME, sync, module="linkedin")
    print(json.dumps({"status": res.status, "metrics": res.metrics},
                     default=str))
    return 0 if res.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    raise SystemExit(main())
