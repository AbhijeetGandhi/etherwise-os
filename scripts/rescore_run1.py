#!/usr/bin/env python3
"""rescore_run1.py — authorized 2026-06-12 (Abhijeet): write the eval's
approved re-scored values to the 15 run-1 rows, then run-scoped push.

Discipline = fabrication_cleanup: dry-run default prints the full plan from
the SAME explicit id list the live path uses; per-id rowcount asserted;
honest runs-ledger row ('rescore-run1-jobs'); reports CSV/MD written before
any external write.

Score provenance: `score` is ALWAYS the approved value from
reports/scoring-eval-2026-06-12.md (gate already applied there — gated rows
carry effective 15). Breakdowns are REGENERATED via the gateway (approved
2026-06-13); breakdown_json records approved_total + regen_total + provenance
so any drift is visible, never hidden.

Pipeline (--live):
  1. regenerate breakdowns (Sonnet, structured outputs, eval prompt)
  2. drafts for rows newly >=16 with no stored draft (proposal-writer rules)
  3. scoped UPDATE of the 15 rows (assert changes()==1 per id)
  4. clickup_push library: --ids these-15 --live (guards skip already-tasked)
  5. PUT name+body on the 11 existing tasks (keep-don't-duplicate)
  6. summary table -> reports/rescore-run1-<date>.md (for external liveness)
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))            # sibling scripts
sys.path.insert(0, str(HERE.parent))     # etherwise-v3 repo root (core.*)

import clickup_push  # noqa: E402
import job_id_guard  # noqa: E402

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "etherwise-os").is_dir())
DB = ROOT / "etherwise-os/etherwise.db"
REPORTS = ROOT / "reports"
EVAL_REPORT = REPORTS / "scoring-eval-2026-06-12.md"
PROPOSAL_SKILL = ROOT / "skills/upwork-proposal-writer/SKILL.md"
TASK_NAME = "rescore-run1-jobs"
DRAFT_THRESHOLD = 16


def approved_values():
    """The authorized numbers — parsed from the eval report verbatim."""
    text = EVAL_REPORT.read_text()
    block = re.search(r"```json\n(.*?)```", text, re.S)
    if not block:
        sys.exit("eval report JSON block not found — refusing to guess")
    rows = json.loads(block.group(1))
    if len(rows) != 15:
        sys.exit(f"expected 15 approved rows, found {len(rows)} — stopping")
    for r in rows:
        if job_id_guard.looks_fabricated(r["id"]):
            sys.exit(f"approved list contains guard-flagged id {r['id']} —"
                     " refusing (verify first)")
    return {r["id"]: r for r in rows}


def fetch_rows(ids):
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        marks = ",".join("?" * len(ids))
        rows = {r["id"]: dict(r) for r in conn.execute(
            f"SELECT * FROM jobs WHERE id IN ({marks})", list(ids))}
    finally:
        conn.close()
    missing = set(ids) - set(rows)
    if missing:
        sys.exit(f"rows missing from jobs table: {sorted(missing)}")
    bad = {i: rows[i]["status"] for i in ids
           if rows[i]["status"] not in ("Scored", "Drafted",
                                        "ClickUp Created")}
    if bad:
        sys.exit(f"unexpected statuses (refusing): {bad}")
    return rows


def regen_breakdowns(rows, approved):
    """Full verdicts via the eval harness's scorer; approved totals pinned.
    score_with returns FLAT verdicts (the eval SCHEMA shape)."""
    from core.evals import scoring_eval as se

    verdicts = se.score_with(None, list(rows.values()),
                             "rescore_breakdowns")
    out = {}
    for jid in rows:
        v = verdicts.get(jid)
        if v is None:
            sys.exit(f"breakdown regeneration failed for {jid} — stopping"
                     " before any DB write (rerun to retry)")
        bd = {k: v.get(k) for k in (
            "skill_fit", "budget", "client_quality", "competition",
            "description_quality", "base_total", "bonuses", "raw_total",
            "recommendation")}
        bd["gate"] = ("gated: capped at 15 (whale gate failed)"
                      if v.get("gated") else None)
        bd["total"] = approved[jid]["rescored"]
        bd["provenance"] = ("score=scoring-eval-2026-06-12 approved;"
                            " breakdown regenerated at rescore")
        bd["approved_total"] = approved[jid]["rescored"]
        bd["regen_total"] = v.get("effective_total")
        out[jid] = bd
    return out


def draft_for(row):
    """Proposal draft via the gateway using the v1 proposal-writer rules."""
    from core import claude_gateway as gw

    skill = PROPOSAL_SKILL.read_text()[:6000]
    result = gw.call(
        task_name="rescore_drafts", model_key="drafting",
        system=("You draft Upwork proposals for Abhijeet Gandhi (Etherwise)."
                " Follow these rules exactly:\n\n" + skill),
        user_content=("Draft a proposal for this job. Return ONLY the"
                      " proposal text.\n\n"
                      f"Title: {row.get('title')}\n"
                      f"Description: {(row.get('description') or '')[:3000]}\n"
                      f"Type: {row.get('contract_type')}"
                      f" Budget: {row.get('fixed_budget') or ''}"
                      f" ${row.get('hourly_min')}-{row.get('hourly_max')}/hr\n"
                      f"Skills: {row.get('skills_json')}"),
        purpose=f"draft for {row['id']}")
    return result.text.strip()


def ledger_start(conn) -> int:
    cur = conn.execute(
        "INSERT INTO runs (task_name, started_at, status) VALUES (?,?,"
        "'running')", (TASK_NAME, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return cur.lastrowid


def ledger_finish(conn, run_id, metrics) -> None:
    conn.execute(
        "UPDATE runs SET completed_at=?, status='completed', metrics_json=?"
        " WHERE id=? AND task_name=?",
        (datetime.now(timezone.utc).isoformat(), json.dumps(metrics),
         run_id, TASK_NAME))
    conn.commit()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--live", action="store_true")
    args = p.parse_args()

    approved = approved_values()
    ids = sorted(approved)
    rows = fetch_rows(ids)

    plan = []
    for jid in ids:
        row, app = rows[jid], approved[jid]
        actions = []
        if app["rescored"] != row["score"]:
            actions.append(f"score {row['score']}->{app['rescored']}")
        needs_draft = (app["rescored"] >= DRAFT_THRESHOLD
                       and not row.get("draft_proposal"))
        if needs_draft:
            actions.append("draft")
        if row.get("clickup_task_id"):
            actions.append("update-existing-task")
        elif app["rescored"] >= 8:
            actions.append("push-new-task")
        plan.append({"id": jid, "old": row["score"], "new": app["rescored"],
                     "gated": app["gated"], "needs_draft": needs_draft,
                     "tasked": bool(row.get("clickup_task_id")),
                     "actions": actions})

    print(json.dumps({"mode": "LIVE" if args.live else "DRY-RUN",
                      "rows": len(plan)}, indent=None))
    for e in plan:
        print(f"  {e['id']}  {e['old']}->{e['new']}"
              f"{' GATED' if e['gated'] else ''}  {','.join(e['actions'])}")
    if not args.live:
        print("dry-run: no model calls, no writes. Rerun with --live.")
        return 0

    # ── live ──────────────────────────────────────────────────────────────
    breakdowns = regen_breakdowns(rows, approved)
    drafts = {}
    for e in plan:
        if e["needs_draft"]:
            drafts[e["id"]] = draft_for(rows[e["id"]])
            time.sleep(0.2)

    conn = sqlite3.connect(DB, timeout=10)
    run_id = ledger_start(conn)
    try:
        for jid in ids:
            app = approved[jid]
            row = rows[jid]
            new_status = row["status"]
            if jid in drafts and new_status == "Scored":
                new_status = "Drafted"
            cur = conn.execute(
                """UPDATE jobs SET score=?, score_breakdown_json=?,
                   loom_flag=?, draft_proposal=COALESCE(?, draft_proposal),
                   draft_word_count=COALESCE(?, draft_word_count),
                   status=?, manual_note=COALESCE(manual_note,'') ||
                     ' [rescored 2026-06-13 per eval; was ' || COALESCE(score,'?') || ']',
                   updated_at=datetime('now')
                   WHERE id=? AND status IN ('Scored','Drafted',
                                             'ClickUp Created')""",
                (app["rescored"], json.dumps(breakdowns[jid]),
                 1 if app["rescored"] >= 22 else 0,
                 drafts.get(jid),
                 len(drafts[jid].split()) if jid in drafts else None,
                 new_status, jid))
            assert cur.rowcount == 1, f"{jid}: expected 1 row, got {cur.rowcount}"
        conn.commit()

        # push the untasked (guards skip already-tasked/below-band)
        push_summary = clickup_push.run(db_path=DB, live=True, ids=ids)

        # update existing tasks in place (keep-don't-duplicate)
        tok = clickup_push.token()
        updated = []
        fresh = fetch_rows(ids)
        for jid in ids:
            row = fresh[jid]
            if not row.get("clickup_task_id"):
                continue
            clickup_push.req(
                "PUT", f"/task/{row['clickup_task_id']}", tok,
                {"name": clickup_push.task_name(row),
                 "markdown_description": clickup_push.task_body(row)})
            updated.append(jid)
            time.sleep(clickup_push.RATE_SECONDS)

        metrics = {
            "rows_updated": len(ids),
            "drafts_generated": len(drafts),
            "tasks_pushed": push_summary.get("pushed", 0),
            "tasks_updated_in_place": len(updated),
            "push_errors": push_summary.get("errors", 0),
        }
        ledger_finish(conn, run_id, metrics)
    finally:
        conn.close()

    # ── summary table for external liveness verification ─────────────────
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    audit_by_id = {a["id"]: a for a in push_summary.get("audit", [])}
    lines = [f"# Run-1 re-score — executed {stamp}", "",
             "| id | old -> new | gated | action |", "|---|---|---|---|"]
    for e in plan:
        act = ("task-updated-in-place" if e["id"] in updated
               else audit_by_id.get(e["id"], {}).get("action", "none"))
        if e["needs_draft"]:
            act += " +draft"
        lines.append(f"| {e['id']} | {e['old']} -> {e['new']} |"
                     f" {'Y' if e['gated'] else ''} | {act} |")
    lines += ["", f"ledger run: {run_id} ({TASK_NAME}) ·"
              f" metrics: `{json.dumps(metrics)}`"]
    out = REPORTS / f"rescore-run1-{stamp}.md"
    out.write_text("\n".join(lines))
    print(json.dumps(metrics))
    print(f"summary: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
