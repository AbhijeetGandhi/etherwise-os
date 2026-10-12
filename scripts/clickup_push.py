#!/usr/bin/env python3
"""clickup_push.py — RUN-SCOPED ClickUp task creation (v4.14.1).

Rewrite of the unscoped original (clickup_push_unsafe_unscoped.py) that
flooded 224 tasks on supervised run 1 by pushing the entire historical
backlog. This version refuses to run without an explicit scope:

  --ids FILE     one job id per line — the Phase-3 scored list for THIS run
  --since ISO    rows first_scored_at >= ISO (e.g. the run's start timestamp)

Hard guards, each emitting a per-id audit line (machine-checkable):
  - fabricated-id pattern (job_id_guard) — NEVER pushed regardless of status
  - status must be Scored|Drafted (never Phantom / New / Skipped)
  - never already-tasked (clickup_task_id set or status 'ClickUp Created')
  - score >= config.SCORE_FLOOR (below band gets no task by the v2.3 matrix
    routing)

--dry-run is the DEFAULT; --live is explicit. Routing: invite -> Invites ·
>= HOT_LEAD_THRESHOLD Hot · >= SCORE_STANDARD Standard · >= SCORE_FLOOR Low —
the cuts come from core/config.py (SCORE_BANDS), never from literals here
(effective post-gate score is stored in `score` by Phase 3, so routing uses
it as-is). Payload: verbatim title,
job_url FROM THE ROW (never reconstructed), deterministic body; the only LLM
content is the stored draft. check-before-retry on create errors (v4.11).
0.65s spacing between API calls.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_id_guard  # noqa: E402  (shared fabricated-id patterns)

# the score bands are config-owned (core/config.py SCORE_BANDS); make the v3
# root importable for the standalone CLI (scan_pipeline already has it)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config  # noqa: E402

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "etherwise-os").is_dir())   # workspace root, symlink-safe
DB = ROOT / "etherwise-os/etherwise.db"
ENV_FILE = ROOT / "etherwise-os/.credentials/etherwise-os.env"
API = "https://api.clickup.com/api/v2"
RATE_SECONDS = 0.65

# list ids live in core/config.py (CLICKUP_LISTS) — one source, never a
# second copy here (2026-08-25); the names below are the routing vocabulary
LIST_ICP = config.CLICKUP_LISTS["icp"]        # ICP / Prime — the top tier
LIST_HOT = config.CLICKUP_LISTS["hot"]
LIST_STANDARD = config.CLICKUP_LISTS["standard"]
LIST_LOW = config.CLICKUP_LISTS["low"]
LIST_INVITES = config.CLICKUP_LISTS["invites"]
LIST_NAMES = {list_id: name for name, list_id in config.CLICKUP_LISTS.items()}

PUSHABLE_STATUSES = ("Scored", "Drafted")


def token() -> str:
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("CLICKUP_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("CLICKUP_API_KEY not found in credentials env")


def req(method: str, path: str, tok: str, body=None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{API}{path}", data=data, method=method,
                               headers={"Authorization": tok,
                                        "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode() or "{}")


# ── routing + payload (pure, unit-tested) ─────────────────────────────────────

def list_for_score(total, invite: bool, icp_tier=None):
    # ICP outranks everything except a direct invite: it is the ideal-client
    # profile ON TOP of a hot score, so it must not drown in the Hot list.
    # Since 2026-10-09 a top-band score routes there too (config.ICP_SCORE_MIN,
    # his "28+ go into ICP and the rest go in hot leads"): the ICP list is the
    # short one he works first; nothing below it is dropped.
    if invite:
        return LIST_INVITES
    if icp_tier == "icp":
        return LIST_ICP
    if total is None:
        return None
    if total >= config.ICP_SCORE_MIN:
        return LIST_ICP
    if total >= config.HOT_LEAD_THRESHOLD:
        return LIST_HOT
    if total >= config.SCORE_STANDARD:
        return LIST_STANDARD
    if total >= config.SCORE_FLOOR:
        return LIST_LOW
    return None


def task_name(row) -> str:
    loom = "🎥 " if row.get("loom_flag") else ""
    return f"{loom}{row.get('title')} — score {row.get('score')}"


def _breakdown(row) -> dict:
    try:
        return json.loads(row.get("score_breakdown_json") or "{}")
    except json.JSONDecodeError:
        return {}


def is_invite(row) -> bool:
    return (_breakdown(row).get("bonuses") or {}).get("invite", 0) > 0


def _test_note(row) -> str:
    """One line above a draft written under a test (modules/upwork/experiments),
    so the letter he edits keeps its version and the proposal still counts."""
    try:
        t = (json.loads(row.get("draft_evidence_json") or "{}") or {}).get("test")
    except (TypeError, ValueError):
        t = None
    if not t:
        return ""
    keep = ("keep the first two sentences about the client (no I / we / my / our)"
            if t.get("arm") == "B" else "keep the opening about you and your work")
    return (f"**Test {str(t.get('id', '')).split('-')[0].upper()} · version {t.get('arm')}"
            f" — {t.get('label')}.** To count, {keep}.")


def task_body(row) -> str:
    """v2 _build_clickup_description format — job_url FROM THE ROW."""
    s = _breakdown(row)
    loom_emoji = " 🎥 LOOM" if row.get("loom_flag") else ""
    lines = [
        f"**Job URL**: {row.get('job_url')}",
        f"**Job ID**: `{row.get('id')}`",
        f"**Feed**: {row.get('feed_source')}",
        f"**Posted**: {row.get('created_dt')}",
        "",
        "## Score Breakdown",
        f"- Skill Fit: {s.get('skill_fit')}/5 × 2 = {(s.get('skill_fit') or 0) * 2}/10",
        f"- Budget: {s.get('budget')}/5",
        f"- Client Quality: {s.get('client_quality')}/3",
        f"- Competition: {s.get('competition')}/5",
        f"- Description Quality: {s.get('description_quality')}/5",
        f"- **Base**: {s.get('base_total')}/28",
        "",
        "**Bonuses**:",
    ]
    for k, v in (s.get("bonuses") or {}).items():
        if v:
            lines.append(f"- {k}: +{v}")
    lines.append(f"- **Total: {row.get('score')}**{loom_emoji}")
    if s.get("gate") is not None:
        lines.append(f"**Category gate**: {s.get('gate')}")
    for label, key in (("Recommendation", "recommendation"),
                       ("Boost", "boost"),
                       ("Proposed rate", "proposed_rate")):
        if s.get(key) is not None:
            lines.append(f"**{label}**: {s.get(key)}")
    if row.get("draft_proposal"):
        lines += ["", "## Draft Proposal", ""]
        test = _test_note(row)
        if test:
            lines += [test, ""]
        lines += [row["draft_proposal"], ""]
    lines += [
        "## Client Context",
        f"- Country: {row.get('client_country') or '—'}",
        f"- Total Spent: ${row.get('client_total_spent') or 0:,.0f}",
        f"- Hires: {row.get('client_hires') or 0}",
        f"- Rating: {row.get('client_rating') or '—'}",
        f"- Payment Verified: {'Yes' if row.get('client_payment_verified') else 'No'}",
        "",
        "## Job Details",
        f"- Type: {row.get('contract_type') or '—'}",
    ]
    if row.get("contract_type") == "Hourly":
        lines.append(f"- Hourly: ${row.get('hourly_min')}/hr –"
                     f" ${row.get('hourly_max')}/hr")
    elif row.get("contract_type") == "Fixed":
        lines.append(f"- Fixed: ${row.get('fixed_budget')}")
    lines += [f"- Applicants: {row.get('total_applicants')}",
              f"- Engagement: {row.get('engagement')}"
              f" ({row.get('engagement_duration')})"]
    return "\n".join(lines)


# ── scoped selection with per-id audit ───────────────────────────────────────

def _guard(row) -> str:
    """'' if pushable, else the audit skip-reason."""
    if job_id_guard.looks_fabricated(row["id"]):
        return ("fabricated-pattern id — never receives a task regardless"
                " of status")
    status = row.get("status")
    if status == "Phantom":
        return "status=Phantom"
    if status == "New":
        return "status=New (unscored)"
    if row.get("clickup_task_id") or status == "ClickUp Created":
        return "already-tasked"
    if status not in PUSHABLE_STATUSES:
        return f"status={status!r} not pushable"
    if (row.get("score") or 0) < config.SCORE_FLOOR:
        return f"below band (<{config.SCORE_FLOOR} — no task by matrix)"
    return ""


def select_scoped(db_path, ids=None, since=None):
    """Returns (plan_rows, audit). Every supplied id gets an audit entry."""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    plan, audit = [], []
    try:
        if ids is not None:
            for jid in ids:
                jid = str(jid).strip()
                if not jid:
                    continue
                if job_id_guard.looks_fabricated(jid):
                    audit.append({"id": jid, "action": "skip",
                                  "reason": "fabricated-pattern id"})
                    continue
                row = conn.execute("SELECT * FROM jobs WHERE id=?",
                                   (jid,)).fetchone()
                if row is None:
                    audit.append({"id": jid, "action": "skip",
                                  "reason": "not found in jobs table"})
                    continue
                row = dict(row)
                reason = _guard(row)
                if reason:
                    audit.append({"id": jid, "action": "skip",
                                  "reason": reason})
                else:
                    plan.append(row)
        else:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM jobs WHERE first_scored_at >= ?"
                " ORDER BY score DESC", (since,))]
            for row in rows:
                reason = _guard(row)
                if reason:
                    audit.append({"id": row["id"], "action": "skip",
                                  "reason": reason})
                else:
                    plan.append(row)
    finally:
        conn.close()
    return plan, audit


# ── live push ─────────────────────────────────────────────────────────────────

def find_existing_task(tok: str, list_id: str, name: str):
    """check-before-retry (v4.11 lesson): a failed create may have landed."""
    q = urllib.parse.urlencode({"include_closed": "false",
                                "order_by": "created", "reverse": "true"})
    try:
        for t in req("GET", f"/list/{list_id}/task?{q}", tok).get("tasks", []):
            if t.get("name") == name:
                return t
    except Exception:
        return None
    return None


def attach(db_path, job_id: str, task_id: str, task_url: str) -> None:
    """Mark the v3 scored_jobs row carded. (Was `jobs` — the v2-era table name;
    v3 has no such table, so a live push would create the card then CRASH here.
    Fixed at the 2026-07-06 scanner cutover. The legacy CLI run()/select_scoped
    below still read `jobs` against the v2 DB default — v2-era relics; do not
    use them against v3.)"""
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute(
            "UPDATE scored_jobs SET clickup_task_id=?, clickup_task_url=?,"
            " status='ClickUp Created', updated_at=datetime('now')"
            " WHERE id=?", (task_id, task_url, job_id))
        conn.commit()
    finally:
        conn.close()


def priority_for(row) -> int:
    """ClickUp priority from the stored score: 1 urgent at the Loom cut or a
    direct invite, 2 high at the hot cut, else 3 normal (cuts from config)."""
    score = row.get("score") or 0
    if score >= config.LOOM_FLAG_THRESHOLD or is_invite(row):
        return 1
    return 2 if score >= config.HOT_LEAD_THRESHOLD else 3


def push_one(tok: str, db_path, row, list_id: str) -> dict:
    name = task_name(row)
    payload = {
        "name": name,
        "markdown_description": task_body(row),
        "priority": priority_for(row),
    }
    try:
        task = req("POST", f"/list/{list_id}/task", tok, payload)
    except Exception as exc:
        time.sleep(RATE_SECONDS)
        task = find_existing_task(tok, list_id, name)
        if not task:
            raise exc
        print(f"NOTE {row['id']}: create errored but task exists"
              f" ({task['id']}) — adopting", file=sys.stderr)
    attach(db_path, row["id"], task["id"], task.get("url", ""))
    return task


def run(db_path=DB, live: bool = False, ids=None, since=None,
        limit=None) -> dict:
    if ids is None and since is None:
        sys.exit("clickup_push: a scope is REQUIRED — pass --ids FILE (this"
                 " run's scored ids) or --since RUN_START_ISO. Unscoped push"
                 " is forbidden (2026-06-12 flood: 224 tasks).")
    if ids is not None and since is not None:
        sys.exit("clickup_push: pass --ids OR --since, not both")

    plan, audit = select_scoped(db_path, ids=ids, since=since)
    if limit:
        for row in plan[limit:]:
            audit.append({"id": row["id"], "action": "skip",
                          "reason": f"over --limit {limit}"})
        plan = plan[:limit]

    by_list = {"hot": 0, "standard": 0, "low": 0, "invites": 0}
    routed = []
    for row in plan:
        list_id = list_for_score(row.get("score"), invite=is_invite(row))
        routed.append((row, list_id))
        by_list[LIST_NAMES[list_id]] += 1

    summary = {
        "dry_run": not live,
        "scope": {"ids": len(list(ids))} if ids is not None
        else {"since": since},
        "eligible": len(routed),
        "by_list": by_list,
        "skipped": {},
        "errors": 0,
    }
    for entry in audit:
        key = entry["reason"].split(" — ")[0].split(" (")[0]
        summary["skipped"][key] = summary["skipped"].get(key, 0) + 1

    if not live:
        for row, list_id in routed:
            audit.append({"id": row["id"], "action": "would_push",
                          "reason": LIST_NAMES[list_id]})
        summary["would_push"] = len(routed)
    else:
        tok = token()
        pushed = 0
        for row, list_id in routed:
            try:
                task = push_one(tok, db_path, row, list_id)
                audit.append({"id": row["id"], "action": "pushed",
                              "reason": f"{LIST_NAMES[list_id]}"
                                        f" task={task.get('id')}"})
                pushed += 1
            except Exception as exc:
                summary["errors"] += 1
                audit.append({"id": row["id"], "action": "error",
                              "reason": repr(exc)[:200]})
            time.sleep(RATE_SECONDS)
        summary["pushed"] = pushed

    for entry in audit:
        print(f"{entry['action'].upper()} {entry['id']} | {entry['reason']}",
              file=sys.stderr)
    summary["audit"] = audit
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    scope = p.add_mutually_exclusive_group(required=True)
    scope.add_argument("--ids", help="file with one job id per line"
                                     " (this run's Phase-3 scored list)")
    scope.add_argument("--since", help="ISO timestamp — rows scored at/after"
                                       " this instant (the run's start)")
    p.add_argument("--live", action="store_true",
                   help="actually create tasks (default: dry-run)")
    p.add_argument("--db", default=str(DB))
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    ids = None
    if args.ids:
        ids = [line.strip() for line in
               Path(args.ids).read_text().splitlines() if line.strip()]
    summary = run(db_path=args.db, live=args.live, ids=ids,
                  since=args.since, limit=args.limit)
    out = dict(summary)
    out.pop("audit", None)          # stdout stays one line; audit on stderr
    print(json.dumps(out))
    return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
