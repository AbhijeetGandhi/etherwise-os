#!/usr/bin/env python3
"""fabrication_cleanup.py — authorized 2026-06-12 (Abhijeet, in-session).

Executes the three approved actions against the fabricated-pattern rows that
survived the June-11 containment (found by verify_restoration re-check):
  1. v2 jobs: re-quarantine ALL guard-flagged live rows -> status='Phantom'
     (explicit id IN-list, rowcount asserted, honest runs-ledger row)
  2. Airtable: delete their mirror records PLUS the previously-approved
     incident-window sweep CSV (trash keeps 7-day recovery; CSVs written
     to reports/ BEFORE any deletion)
  3. ClickUp: close tasks held by fabricated rows (comment + EXPIRED)

Dry-run default; --live executes. Registry lesson applied: the dry SELECT is
the printed plan, the live UPDATE uses the same explicit id list.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_id_guard  # noqa: E402

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "etherwise-os").is_dir())
DB = ROOT / "etherwise-os/etherwise.db"
ENV_FILE = ROOT / "etherwise-os/.credentials/etherwise-os.env"
REPORTS = ROOT / "reports"
SWEEP_CSV = REPORTS / "airtable-junk-sweep-2026-06-11.csv"
AIRTABLE_BASE = "appgE1QoEOXvbrUE4"
AIRTABLE_TABLE = "tbloRHzMBbryPr090"
TODAY = datetime.now(timezone.utc).strftime("%Y%m%d")

CLOSE_COMMENT = ("Closed: this task was created from a FABRICATED job row"
                 " (June 10-12 scanner incidents — id matches the"
                 " zeros/keyboard-walk fabrication signature). The job URL"
                 " points at nothing real. Row re-quarantined as Phantom.")


def keys() -> dict:
    out = {}
    for line in ENV_FILE.read_text().splitlines():
        for name in ("AIRTABLE_API_KEY", "CLICKUP_API_KEY"):
            if line.startswith(name + "="):
                out[name] = line.split("=", 1)[1].strip()
    return out


def http(method: str, url: str, headers: dict, body=None) -> dict:
    """urllib with curl fallback (sandbox DNS quirk hits urllib only)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode() or "{}")
    except Exception:
        cmd = ["curl", "-sf", "-X", method, url, "--max-time", "30"]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        if body is not None:
            cmd += ["-H", "Content-Type: application/json",
                    "-d", json.dumps(body)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"curl {method} {url[:60]}:"
                               f" exit {proc.returncode}")
        return json.loads(proc.stdout or "{}")


def flagged_live_rows() -> list:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, status, title, airtable_record_id, clickup_task_id"
            " FROM jobs WHERE status IN"
            " ('Scored','Drafted','Skipped','ClickUp Created','New')")]
    finally:
        conn.close()
    return [r for r in rows if job_id_guard.looks_fabricated(r["id"])]


def requarantine(ids: list) -> int:
    conn = sqlite3.connect(DB, timeout=30)
    try:
        with conn:
            marks = ",".join("?" * len(ids))
            cur = conn.execute(
                f"UPDATE jobs SET status='Phantom',"
                f" updated_at=datetime('now') WHERE id IN ({marks})"
                f" AND status != 'Phantom'", ids)
            assert cur.rowcount == len(ids), \
                f"expected {len(ids)} updates, got {cur.rowcount}"
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO runs (task_name, started_at, completed_at,"
                " status, metrics_json) VALUES"
                " ('incident-requarantine-fabricated', ?, ?, 'completed', ?)",
                (now, now, json.dumps({
                    "rows": len(ids),
                    "authorized_by": "Abhijeet in-session 2026-06-12",
                    "detector": "job_id_guard (zeros/keyboard-walk)",
                    "csv": f"reports/fabrication-requarantine-{TODAY}.csv"})))
        return cur.rowcount
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    args = p.parse_args()

    flagged = flagged_live_rows()
    REPORTS.mkdir(exist_ok=True)
    requarantine_csv = REPORTS / f"fabrication-requarantine-{TODAY}.csv"
    with open(requarantine_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "status", "title",
                                          "airtable_record_id",
                                          "clickup_task_id"])
        w.writeheader()
        w.writerows({k: (v or "") for k, v in r.items()} for r in flagged)

    at_extra = sorted({r["airtable_record_id"] for r in flagged
                       if r["airtable_record_id"]})
    extra_csv = REPORTS / f"airtable-junk-sweep-extension-{TODAY}.csv"
    with open(extra_csv, "w", newline="") as f:
        f.write("record_id\n")
        for rid in at_extra:
            f.write(rid + "\n")

    with open(SWEEP_CSV) as f:
        approved_29 = [row["record_id"] for row in csv.DictReader(f)]
    at_all = approved_29 + [r for r in at_extra if r not in approved_29]
    cu_tasks = [(r["id"], r["clickup_task_id"]) for r in flagged
                if r["clickup_task_id"]]

    print(f"PLAN: requarantine={len(flagged)} rows"
          f" (csv: {requarantine_csv.name})")
    print(f"PLAN: airtable deletions={len(at_all)}"
          f" ({len(approved_29)} approved-29 + {len(at_extra)} extension,"
          f" csv: {extra_csv.name})")
    print(f"PLAN: clickup closures={len(cu_tasks)}")
    for r in flagged[:10]:
        print(f"  SAMPLE {r['id']} {r['status']} {(r['title'] or '')[:50]}")
    if not args.live:
        print(json.dumps({"dry_run": True, "requarantine": len(flagged),
                          "airtable": len(at_all),
                          "clickup": len(cu_tasks)}))
        return 0

    k = keys()
    summary = {"dry_run": False}

    summary["requarantined"] = requarantine([r["id"] for r in flagged])

    deleted = 0
    headers = {"Authorization": f"Bearer {k['AIRTABLE_API_KEY']}"}
    for i in range(0, len(at_all), 10):
        batch = at_all[i:i + 10]
        q = "&".join(f"records[]={urllib.parse.quote(r)}" for r in batch)
        out = http("DELETE",
                   f"https://api.airtable.com/v0/{AIRTABLE_BASE}/"
                   f"{AIRTABLE_TABLE}?{q}", headers)
        deleted += sum(1 for rec in out.get("records", [])
                       if rec.get("deleted"))
        time.sleep(0.25)
    summary["airtable_deleted"] = deleted
    summary["airtable_planned"] = len(at_all)

    closed = 0
    cu_headers = {"Authorization": k["CLICKUP_API_KEY"],
                  "Content-Type": "application/json"}
    for job_id, task_id in cu_tasks:
        try:
            http("POST", f"https://api.clickup.com/api/v2/task/{task_id}"
                 "/comment", cu_headers, {"comment_text": CLOSE_COMMENT})
            time.sleep(0.65)
            http("PUT", f"https://api.clickup.com/api/v2/task/{task_id}",
                 cu_headers, {"status": "EXPIRED"})
            time.sleep(0.65)
            closed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"WARN task {task_id}: {exc!r}", file=sys.stderr)
    summary["clickup_closed"] = closed

    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
