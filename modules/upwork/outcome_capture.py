"""M1b outcome_capture — the Sunday 11 AM outcome rail composer (SHADOW).

The first v3 production RAIL (registry: rails/REGISTRY.md, tier internal):
every Sunday 11:00 IST, email the week's closed proposals, each with a
PREFILLED Airtable form link (intake table -> automation writes Outcome
Reason/Notes onto the proposal). Until M1b cutover the email is an INTENT in
shadow_ledger.

Form URL comes from rails/REGISTRY.md (`form_url:` on the outcome-capture
entry) — pending until Abhijeet creates the form (Omni agent prompt
delivered Day 6). Composer runs regardless; metrics flag form_url_missing.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
from datetime import datetime

from core import config, db, runner

TASK_NAME = "upwork_outcomes"
REGISTRY = config.V3_ROOT / "rails/REGISTRY.md"
TERMINAL = ("Won", "Lost", "Expired", "Withdrawn", "Skipped")


def form_url() -> str:
    """Parse the outcome-capture rail's form_url from the registry."""
    try:
        text = REGISTRY.read_text()
    except OSError:
        return ""
    m = re.search(r"^- form_url:\s*(\S+)", text, re.M)
    url = m.group(1) if m else ""
    return "" if url.upper() in ("", "PENDING", "TBD") else url


def prefill_link(base: str, record_id: str) -> str:
    q = urllib.parse.urlencode({
        "prefill_Proposal": record_id,
        "hide_Proposal": "true",
        "prefill_Source": "sunday-email",
        "hide_Source": "true",
    })
    return f"{base}?{q}"


def week_candidates(conn):
    """Closed-this-week proposals still missing an outcome reason."""
    marks = ",".join("?" * len(TERMINAL))
    # modified_dt is Upwork's own change timestamp — updated_at moves on
    # every sync touch (e.g. terminal-preserved telemetry) and would flood
    # the email with old closures (caught live, Day 6 run 20).
    return [dict(r) for r in conn.execute(
        f"""SELECT id, status, job_title, client_company,
            airtable_record_id, updated_at FROM proposals
            WHERE status IN ({marks})
              AND (status_reason IS NULL OR TRIM(status_reason) = '')
              AND COALESCE(modified_dt, first_seen_at)
                  >= datetime('now', '-7 days')
            ORDER BY COALESCE(modified_dt, first_seen_at) DESC""",
        TERMINAL)]


def compose(candidates, base_url: str) -> dict:
    items = []
    for p in candidates:
        link = (prefill_link(base_url, p["airtable_record_id"])
                if base_url and p["airtable_record_id"] else None)
        items.append({
            "proposal_id": p["id"],
            "title": p["job_title"], "client": p["client_company"],
            "status": p["status"], "form_link": link,
        })
    week = datetime.now(config.TZ).strftime("%Y-W%W")
    return {
        "to": config.NOTIFY_EMAIL,
        "subject": f"Outcome capture — {len(items)} proposals closed"
                   f" this week",
        "week": week,
        "items": items,
    }


def capture(ctx) -> dict:
    dbp = ctx.db_path or config.DB_PATH
    with db.connect(dbp) as conn:
        candidates = week_candidates(conn)
    base = form_url()
    if not candidates:
        raise runner.TaskSkip("no closed proposals lacking outcomes"
                              " this week")
    payload = compose(candidates, base)
    if ctx.shadow:
        ctx.record_shadow_write(
            target="email", operation="send", entity="outcome_capture",
            entity_key=payload["week"], payload=payload)
    else:
        ctx.require_live("Sunday outcome email")   # raises until cutover
    return {"candidates": len(candidates),
            "with_links": sum(1 for i in payload["items"]
                              if i["form_link"]),
            "form_url_missing": not base}


def main() -> int:
    result = runner.run_task(TASK_NAME, capture, module="upwork")
    print(json.dumps({"run_id": result.run_id, "status": result.status,
                      "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
