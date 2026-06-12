#!/usr/bin/env python3
"""verify_restoration.py — re-verify the June-11 evidence-derived rows.

Restoration tooling per the supervised-run-2 blockers: embeds the
fabricated-id guard (job_id_guard) and externally verifies what the Jun-11
evidence-derivation trusted blindly. READ-ONLY on the jobs table — findings
are reported, never auto-written (heuristics != verification).

Checks, for every row the restoration evidence-derived (born after the 03:00
backup, Phantom in the 14:10 forensic snapshot):
  1. where it stands now (live vs re-quarantined)
  2. id-guard pattern check on the live ones (and: would the guard have
     caught the 23 escapees?)
  3. for live rows holding clickup_task_id: the task must exist in ClickUp
     and not be closed as EXPIRED/SKIPPED (the flood-close / dead-sweep
     signatures)

Usage: python3 scripts/verify_restoration.py [--json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_id_guard  # noqa: E402

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "etherwise-os").is_dir())
DB = ROOT / "etherwise-os/etherwise.db"
ENV_FILE = ROOT / "etherwise-os/.credentials/etherwise-os.env"
BACKUPS = ROOT / "etherwise-os/etherwise_os/backups"
FORENSIC = BACKUPS / "etherwise-2026-06-11_1410-FORENSIC-pre-restoration.db"
BACKUP_0300 = BACKUPS / "etherwise-2026-06-11_03-00-05.db"


def token() -> str:
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("CLICKUP_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("CLICKUP_API_KEY not found")


def derived_rows():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute(f"ATTACH 'file:{FORENSIC}?mode=ro' AS f")
    conn.execute(f"ATTACH 'file:{BACKUP_0300}?mode=ro' AS b")
    try:
        return [dict(r) for r in conn.execute("""
            SELECT main.jobs.id, main.jobs.status, main.jobs.title,
                   main.jobs.clickup_task_id, main.jobs.score,
                   main.jobs.job_url
            FROM f.jobs LEFT JOIN b.jobs ON b.jobs.id = f.jobs.id
            JOIN main.jobs ON main.jobs.id = f.jobs.id
            WHERE f.jobs.status='Phantom'
              AND f.jobs.updated_at LIKE '2026-06-11 09:44%'
              AND b.jobs.id IS NULL""")]
    finally:
        conn.close()


def check_task(tok: str, task_id: str):
    import fabrication_cleanup as fc   # sibling module: urllib+curl fallback
    try:
        t = fc.http("GET", f"https://api.clickup.com/api/v2/task/{task_id}",
                    {"Authorization": tok})
        return (t.get("status") or {}).get("status", "?").lower()
    except Exception as exc:  # noqa: BLE001
        return f"ERROR:{exc!r}"[:80]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--skip-clickup", action="store_true",
                   help="offline mode: id-guard only")
    args = p.parse_args()

    rows = derived_rows()
    live = [r for r in rows if r["status"] != "Phantom"]
    requarantined = [r for r in rows if r["status"] == "Phantom"]

    report = {
        "derived_total": len(rows),
        "live_now": len(live),
        "requarantined_now": len(requarantined),
        "guard_would_catch_of_requarantined": sum(
            1 for r in requarantined
            if job_id_guard.looks_fabricated(r["id"])),
        "requarantined_sample_ids": [r["id"] for r in requarantined[:8]],
        "live_flagged_by_guard": [
            {"id": r["id"], "status": r["status"], "title": (r["title"]
             or "")[:60]}
            for r in live if job_id_guard.looks_fabricated(r["id"])],
        "live_by_status": {},
    }
    for r in live:
        report["live_by_status"][r["status"]] = \
            report["live_by_status"].get(r["status"], 0) + 1

    if not args.skip_clickup:
        tok = token()
        tasked = [r for r in live if r["clickup_task_id"]]
        verdicts = {"open_ok": 0, "closed_dead": [], "missing": []}
        for r in tasked:
            status = check_task(tok, r["clickup_task_id"])
            if status.startswith("ERROR"):
                verdicts["missing"].append(
                    {"id": r["id"], "task": r["clickup_task_id"],
                     "err": status})
            elif status in ("expired", "skipped"):
                verdicts["closed_dead"].append(
                    {"id": r["id"], "task": r["clickup_task_id"],
                     "task_status": status,
                     "title": (r["title"] or "")[:60]})
            else:
                verdicts["open_ok"] += 1
            time.sleep(0.3)
        report["clickup_tasked_checked"] = len(tasked)
        report["clickup"] = verdicts

    print(json.dumps(report, indent=1) if args.json
          else json.dumps(report, indent=1))
    suspicious = bool(report["live_flagged_by_guard"]) or bool(
        report.get("clickup", {}).get("missing"))
    return 1 if suspicious else 0


if __name__ == "__main__":
    sys.exit(main())
