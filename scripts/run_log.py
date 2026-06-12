#!/usr/bin/env python3
"""run_log.py — honest run ledger for the Upwork scanner (v4.14, P0).

Fixes the run-2098 defect class: the model used to write the ledger itself and
fabricated task_name ('scan_jobs') and timestamps (invented 06:00:00); two
runs wrote no row at all. This helper hardcodes the task name, takes
timestamps from the system clock, and inserts the row as 'running' AT START so
a crashed run is visible instead of absent.

Usage (from the scanner prompt):
  RUN_ID=$(python3 scripts/run_log.py start)
  ... scan ...
  python3 scripts/run_log.py finish "$RUN_ID" --status completed \
      --metrics '{"new":3,"known":42,"tasks_created":2}'
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "etherwise-os").is_dir())   # workspace root, symlink-safe
DB = ROOT / "etherwise-os/etherwise.db"
TASK_NAME = "upwork-job-scanner"          # literal — never model-supplied


def _conn(db_path):
    c = sqlite3.connect(db_path, timeout=10)
    c.row_factory = sqlite3.Row
    return c


MUTEX_MINUTES = 60   # v4.14.1: refuse concurrent runs (2240/2241/2242 lesson)


def start(db_path=DB) -> int:
    now_dt = datetime.now(timezone.utc)
    conn = _conn(db_path)
    try:
        row = conn.execute(
            "SELECT id, started_at FROM runs WHERE task_name=? AND"
            " status='running' ORDER BY id DESC LIMIT 1",
            (TASK_NAME,)).fetchone()
        if row:
            try:
                age_min = (now_dt - datetime.fromisoformat(
                    row["started_at"])).total_seconds() / 60
            except ValueError:
                age_min = 0.0          # unparseable -> treat as fresh, refuse
            if age_min < MUTEX_MINUTES:
                sys.exit(
                    f"run mutex: run {row['id']} is already 'running'"
                    f" ({age_min:.0f} min old) — refusing a concurrent run."
                    f" If that run is dead, close it honestly first:"
                    f" python3 scripts/run_log.py finish {row['id']}"
                    " --status failed --metrics"
                    " '{\"note\":\"closed as stale by next run\"}'")
        cur = conn.execute(
            "INSERT INTO runs (task_name, started_at, status)"
            " VALUES (?, ?, 'running')", (TASK_NAME, now_dt.isoformat()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def finish(run_id: int, status: str, metrics: dict, anomalies=None,
           db_path=DB) -> None:
    now_dt = datetime.now(timezone.utc)
    conn = _conn(db_path)
    try:
        row = conn.execute("SELECT task_name, started_at FROM runs WHERE id=?",
                           (run_id,)).fetchone()
        if row is None:
            sys.exit(f"run_log finish: run {run_id} does not exist")
        if row["task_name"] != TASK_NAME:
            sys.exit(f"run_log finish: run {run_id} belongs to"
                     f" {row['task_name']!r}, refusing to touch it")
        try:
            started = datetime.fromisoformat(row["started_at"])
            duration_ms = int((now_dt - started).total_seconds() * 1000)
        except ValueError:
            duration_ms = None
        conn.execute(
            "UPDATE runs SET completed_at=?, status=?, metrics_json=?,"
            " anomalies_json=?, duration_ms=? WHERE id=?",
            (now_dt.isoformat(), status, json.dumps(metrics),
             json.dumps(anomalies) if anomalies else None, duration_ms,
             run_id))
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start")
    f = sub.add_parser("finish")
    f.add_argument("run_id", type=int)
    f.add_argument("--status", required=True,
                   choices=("completed", "failed"))
    f.add_argument("--metrics", default="{}",
                   help="JSON object, e.g. '{\"new\":3}'")
    f.add_argument("--anomalies", default=None, help="JSON array, optional")
    p.add_argument("--db", default=str(DB))
    args = p.parse_args()

    if args.cmd == "start":
        print(start(db_path=args.db))
        return 0
    finish(args.run_id, status=args.status,
           metrics=json.loads(args.metrics),
           anomalies=json.loads(args.anomalies) if args.anomalies else None,
           db_path=args.db)
    print(f"OK: run {args.run_id} -> {args.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
