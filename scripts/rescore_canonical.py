#!/usr/bin/env python3
"""rescore_canonical.py — canonical-matrix re-score for mis-scored v2 rows.

Generalizes the run-1 playbook (rescore_run1.py) for any future mis-score:
takes an explicit --ids file, applies the CURRENT deterministic hard rules
(modules.upwork.scoring — including the refined primary-subject-only n8n
rule, 2026-06-12) in code, scores survivors through the gateway (v2.3 matrix
+ gate + anchored exemplars, structured outputs), drafts >=16, then
run-scoped push for >=8.

Authorized: BUILD_BRIEF Day-5 re-score addendum (run-2256 invented-rubric
rows + n8n rows under the refined rule). Discipline = fabrication_cleanup:
dry-run default prints the plan from the same id list, per-id rowcount
asserts, honest ledger row ('rescore-canonical'), summary report for
external liveness.

Only rows currently in status Skipped|Scored|Drafted are touched; fabricated
ids, Phantom, New and already-tasked rows are refused per-id with audit
lines.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import clickup_push  # noqa: E402
import job_id_guard  # noqa: E402

from modules.upwork import scoring  # noqa: E402

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "etherwise-os").is_dir())
DB = ROOT / "etherwise-os/etherwise.db"
REPORTS = ROOT / "reports"
TASK_NAME = "rescore-canonical"
TOUCHABLE = ("Skipped", "Scored", "Drafted")


def fetch(conn, jid):
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    return dict(row) if row else None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ids", required=True, help="file, one job id per line")
    p.add_argument("--live", action="store_true")
    args = p.parse_args()

    ids = [ln.strip() for ln in Path(args.ids).read_text().splitlines()
           if ln.strip()]
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    plan, refused = [], []
    for jid in ids:
        if job_id_guard.looks_fabricated(jid):
            refused.append((jid, "fabricated-pattern id"))
            continue
        row = fetch(conn, jid)
        if row is None:
            refused.append((jid, "not found"))
            continue
        if row["status"] not in TOUCHABLE:
            refused.append((jid, f"status={row['status']!r} untouchable"))
            continue
        if row.get("clickup_task_id"):
            refused.append((jid, "already-tasked — body refresh only via"
                                 " push tooling"))
            continue
        skip = scoring.hard_rule_skip(row)
        plan.append({"id": jid, "old_score": row["score"],
                     "old_status": row["status"],
                     "old_reason": row.get("hard_rule_skip"),
                     "hard_skip": skip, "row": row})
    conn.close()

    print(json.dumps({"mode": "LIVE" if args.live else "DRY-RUN",
                      "to_process": len(plan), "refused": len(refused)}))
    for jid, why in refused:
        print(f"  REFUSED {jid}: {why}", file=sys.stderr)
    for e in plan:
        act = f"hard-skip:{e['hard_skip']}" if e["hard_skip"] \
            else "canonical-score"
        print(f"  {e['id']}  was {e['old_status']}/{e['old_score']}"
              f" ({e['old_reason'] or '-'}) -> {act}")
    if not args.live:
        print("dry-run: no model calls, no writes.")
        return 0

    wconn = sqlite3.connect(DB, timeout=10)
    cur = wconn.execute(
        "INSERT INTO runs (task_name, started_at, status) VALUES"
        " (?,?, 'running')",
        (TASK_NAME, datetime.now(timezone.utc).isoformat()))
    run_id = cur.lastrowid
    wconn.commit()

    results, drafted = [], 0
    try:
        for e in plan:
            jid, row = e["id"], e["row"]
            if e["hard_skip"]:
                c = wconn.execute(
                    "UPDATE jobs SET status='Skipped', hard_rule_skip=?,"
                    " score=NULL, score_breakdown_json=NULL, loom_flag=0,"
                    " updated_at=datetime('now')"
                    " WHERE id=? AND status IN (?,?,?)",
                    (e["hard_skip"], jid, *TOUCHABLE))
                assert c.rowcount == 1, jid
                results.append({**e, "new_score": None,
                                "action": f"Skipped:{e['hard_skip']}"})
                continue
            v = scoring.score_job(row, task_name=TASK_NAME, db_path=None)
            draft = None
            if v["score"] >= 16 and not row.get("draft_proposal"):
                draft = scoring.draft_proposal(row, task_name=TASK_NAME,
                                               db_path=None)
                drafted += 1
                time.sleep(0.2)
            v["breakdown"]["provenance"] = ("rescore-canonical (run-2256"
                                            " invented-rubric remediation)")
            c = wconn.execute(
                """UPDATE jobs SET score=?, score_breakdown_json=?,
                   loom_flag=?, hard_rule_skip=NULL,
                   draft_proposal=COALESCE(?, draft_proposal),
                   draft_word_count=COALESCE(?, draft_word_count),
                   status=?, first_scored_at=datetime('now'),
                   updated_at=datetime('now')
                   WHERE id=? AND status IN (?,?,?)""",
                (v["score"], json.dumps(v["breakdown"]), v["loom_flag"],
                 draft, len(draft.split()) if draft else None,
                 "Drafted" if (draft or row.get("draft_proposal"))
                 else "Scored", jid, *TOUCHABLE))
            assert c.rowcount == 1, jid
            results.append({**e, "new_score": v["score"],
                            "action": "Drafted" if draft else "Scored"})
        wconn.commit()

        push = clickup_push.run(db_path=DB, live=True,
                                ids=[e["id"] for e in plan])

        wconn.execute(
            "UPDATE runs SET completed_at=?, status='completed',"
            " metrics_json=? WHERE id=? AND task_name=?",
            (datetime.now(timezone.utc).isoformat(),
             json.dumps({"processed": len(plan), "refused": len(refused),
                         "drafts": drafted,
                         "pushed": push.get("pushed", 0),
                         "push_errors": push.get("errors", 0)}),
             run_id, TASK_NAME))
        wconn.commit()
    finally:
        wconn.close()

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    audit = {a["id"]: a for a in push.get("audit", [])}
    lines = [f"# Canonical re-score (run-2256 remediation) — {stamp}", "",
             "| id | was | now | action | push |", "|---|---|---|---|---|"]
    for e in results:
        pa = audit.get(e["id"], {})
        lines.append(
            f"| {e['id']} | {e['old_status']}/{e['old_score']}"
            f" ({e['old_reason'] or '-'}) | {e['new_score']} |"
            f" {e['action']} | {pa.get('action', '-')}:"
            f" {pa.get('reason', '')[:40]} |")
    for jid, why in refused:
        lines.append(f"| {jid} | — | — | REFUSED: {why} | - |")
    out = REPORTS / f"rescore-canonical-{stamp}.md"
    out.write_text("\n".join(lines))
    print(json.dumps({"processed": len(plan), "drafts": drafted,
                      "pushed": push.get("pushed", 0),
                      "errors": push.get("errors", 0)}))
    print(f"summary: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
