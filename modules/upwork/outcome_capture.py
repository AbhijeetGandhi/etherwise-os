"""M1b outcome_capture — the Sunday 11 AM outcome rail (composer + sender).

The first v3 production RAIL (registry: rails/REGISTRY.md, tier internal):
every Sunday 11:00 IST, email the week's closed proposals, each with a
PREFILLED Airtable form link (intake table -> automation writes Outcome
Reason/Notes onto the proposal). In shadow the email is an INTENT in
shadow_ledger; live, it is sent through core.notify.send_gmail.

Finding 14 (Upwork chat, 2026-08-25): from the upwork module's cutover until
this change the live branch was `ctx.require_live(...)` and nothing else — a
guard, not a send — so the rail composed the email every Sunday and delivered
it nowhere. Loop 2 (skip review / outcome reasons) had no intake. The send is
the whole point of this task, so a failed send FAILS the run (critical anomaly
-> the interrupt email), unlike the scan digest which is a side note to cards
already pushed. Sending twice in one week is refused (the plist fires once;
the cockpit's deliberate rail button is the only other caller).

Form URL comes from rails/REGISTRY.md (`form_url:` on the outcome-capture
entry). Composer runs regardless; metrics flag form_url_missing.
"""
from __future__ import annotations

import html as _html
import json
import re
import sys
import urllib.parse
from datetime import datetime

from core import config, db, notify, runner

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


def render_html(payload: dict) -> str:
    """The email body: one line per closed proposal, each with its prefilled
    form link — or, when the registry has no form_url, a plain statement of
    that instead of a dead link. Fifteen seconds per proposal is the ask."""
    esc = _html.escape
    items = []
    for it in payload["items"]:
        label = " · ".join(esc(str(v)) for v in (it.get("client"),
                                                  it.get("status")) if v)
        link = (f'<a href="{esc(it["form_link"], quote=True)}">record the'
                f' outcome</a>' if it.get("form_link")
                else "<i>no form link — rails/REGISTRY.md form_url is missing</i>")
        items.append(f"<li><b>{esc(str(it.get('title') or it['proposal_id']))}"
                     f"</b>{' — ' + label if label else ''} · {link}</li>")
    return (f"<h2>Outcome capture — week {esc(payload['week'])}</h2>"
            f"<p>{len(items)} proposal{'s' if len(items) != 1 else ''} closed"
            " this week without an outcome reason. One click each; the"
            " reasons feed the scoring review (Loop 2).</p><ul>"
            + "\n".join(items) + "</ul>"
            "<p style='color:#6b7688;font-size:12px'>Sent by the v3"
            " outcome-capture rail (rails/REGISTRY.md).</p>")


def already_sent(conn, week: str) -> bool:
    """A completed live run that reported this week's email as sent."""
    for r in conn.execute(
            "SELECT metrics_json FROM runs WHERE task_name=? AND"
            " status='completed' ORDER BY id DESC LIMIT 20", (TASK_NAME,)):
        try:
            m = json.loads(r["metrics_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if m.get("week") == week and m.get("sent") == "sent":
            return True
    return False


def capture(ctx, _send=None) -> dict:
    dbp = ctx.db_path or config.DB_PATH
    send = _send or notify.send_gmail
    with db.connect(dbp) as conn:
        candidates = week_candidates(conn)
    base = form_url()
    if not candidates:
        raise runner.TaskSkip("no closed proposals lacking outcomes"
                              " this week")
    payload = compose(candidates, base)
    metrics = {"candidates": len(candidates),
               "with_links": sum(1 for i in payload["items"]
                                 if i["form_link"]),
               "form_url_missing": not base,
               "week": payload["week"]}
    if ctx.shadow:
        ctx.record_shadow_write(
            target="email", operation="send", entity="outcome_capture",
            entity_key=payload["week"], payload=payload)
        return {**metrics, "sent": "shadow"}
    ctx.require_live("Sunday outcome email")
    with db.connect(dbp) as conn:
        if already_sent(conn, payload["week"]):
            raise runner.TaskSkip(f"outcome email for {payload['week']}"
                                  " already sent")
    try:
        send(payload["subject"], render_html(payload))
    except Exception as exc:                  # noqa: BLE001
        ctx.log(f"outcome email send FAILED: {exc!r}")
        # the email IS the task: fail loud (critical anomaly + interrupt
        # email via the runner), never a quiet 'completed' with nothing sent
        raise RuntimeError(f"Sunday outcome email not sent: {exc!r}") from exc
    return {**metrics, "sent": "sent"}


def main() -> int:
    result = runner.run_task(TASK_NAME, capture, module="upwork")
    print(json.dumps({"run_id": result.run_id, "status": result.status,
                      "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
