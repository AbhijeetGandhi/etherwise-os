#!/usr/bin/env python3
"""Retire the board cards that the v3.0 rules no longer call leads.

Abhijeet, 2026-09-08: "can you actually just run, all the leads in the new stage
so I only go throuhg avlid ones."

The scoring rebuild (commits fe08076, 415e4bd) changed what counts as a lead:
a platform gate before the model, the trades next door skipped on the title, a
score that is skill fit x4 rather than five flat components, and a hot bar of 16
on the new scale. Every card ALREADY on his board was created under the old
rules. Re-scoring the database fixed the numbers; it did not empty the board he
actually reads. This does.

WHAT IT WILL NOT TOUCH, in order of how badly it would hurt to get wrong:
  - any status but `new` — applied and interview are his work, not the feed's
  - any job with a proposal against it, whatever the card says
  - the Invites list — a client asked for him by name
  - anything ICP-tier — the ideal-client profile outranks a score by design
  - anything inside --fresh-days, if he asks for a window (default 0: he asked
    for the whole `new` stage, and a marketing job is a marketing job on day 1
    — measured, 710 of the 764 cards carded in the last week fail the new rules
    too, so a freshness window would have left the board still unreadable)
  - any card whose job v3 has never seen (a v2-era task): unknown provenance is
    a reason to leave a thing alone, not to close it

Everything it does close gets a comment naming the rule, so the board says why
and he can reopen one by hand. Dry-run by default; --live writes.

Usage: PYTHONPATH=. python3 scripts/board_rescore_sweep.py [--live] [--limit N]
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config  # noqa: E402
from modules.upwork import scoring  # noqa: E402

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "etherwise-os").is_dir())
ENV_FILE = ROOT / "etherwise-os/.credentials/etherwise-os.env"
REPORTS = ROOT / "reports"
API = "https://api.clickup.com/api/v2"

# The lists a lead card can live in. `invites` is listed so the sweep can SEE
# those cards and prove it skipped them, not so it can close them.
LIST_NAMES = {v: k for k, v in config.CLICKUP_LISTS.items()}
NEVER_SWEEP_LISTS = ("invites",)
SWEEPABLE_STATUS = "new"
# ClickUp allows ~100 requests a minute and each close costs two (comment, then
# status). 0.8s spacing lands at ~75/min with room for a retry, which matters
# because this sweep is long: it is resume-safe only in the sense that a closed
# card is no longer fetched as open, so a rate-limit storm mid-run would leave
# a partly-swept board with no comment trail on the failures.
RATE_SLEEP_S = 0.8

# The board has NO "closed" status. Its vocabulary is
# new / applied / interview / won / skipped / expired / lost, and the first live
# run learned that the hard way: 50 tasks took their comment and then refused
# the status, leaving 50 explained-but-open cards. `skipped` is the honest word
# for what this sweep does — the job was read and is not his kind of work —
# and it is where the board already keeps 5,195 of them. EXPIRED belongs to
# board_cleanup's R1, which means something different (the listing died).
CLOSED_STATUS = "skipped"

COMMENT = ("Auto-closed by the 2026-09-08 scoring rebuild (v3.0): {why}.\n\n"
           "This card was created under the old rules, which carded everything "
           "scoring 8+ and rewarded the word 'Claude'. The new rules require a "
           "platform you actually work in (or an automation/integration/workflow "
           "title), skip voice/marketing/funnel/content work, and score skill fit "
           "x4. Reopen if you disagree - nothing else about the job changed.")


def token() -> str:
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("CLICKUP_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("CLICKUP_API_KEY not found")


def req(method: str, path: str, tok: str, body=None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{API}{path}", data=data, method=method,
                               headers={"Authorization": tok,
                                        "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode() or "{}")


def open_cards(tok: str) -> list:
    """Every open card on the lead lists, with the list it sits in."""
    out = []
    for list_id, name in LIST_NAMES.items():
        page = 0
        while True:
            q = urllib.parse.urlencode({"page": page, "include_closed": "false",
                                        "subtasks": "false"})
            data = req("GET", f"/list/{list_id}/task?{q}", tok)
            tasks = data.get("tasks", [])
            for t in tasks:
                out.append({"id": t["id"], "name": t.get("name") or "",
                            "status": (t.get("status") or {}).get("status", "").lower(),
                            "list": name, "url": t.get("url", ""),
                            "created_ms": int(t.get("date_created") or 0)})
            if len(tasks) < 100:
                break
            page += 1
            time.sleep(0.3)
    return out


def _rows(conn) -> dict:
    """clickup_task_id -> the job, for every card v3 knows it created."""
    out = {}
    for r in conn.execute(
            "SELECT clickup_task_id, id, title, description, score, icp_tier,"
            " contract_type, hourly_min, hourly_max, fixed_budget,"
            " client_total_spent, client_hires, client_payment_verified"
            " FROM scored_jobs WHERE clickup_task_id IS NOT NULL"):
        out[r["clickup_task_id"]] = dict(r)
    return out


def _bid_on(conn) -> set:
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT marketplace_job_id FROM proposals"
        " WHERE marketplace_job_id IS NOT NULL AND marketplace_job_id != ''")}


def verdict(card: dict, job, bid_on: set, now_ms: float, fresh_days: float = 0.0):
    """(close_reason, keep_reason) — exactly one is not None.

    The keep reasons are checked FIRST and in order of how expensive the mistake
    would be. A sweep that has to be argued with is worse than no sweep."""
    if card["status"] != SWEEPABLE_STATUS:
        return None, f"status is {card['status']}, not new"
    if card["list"] in NEVER_SWEEP_LISTS:
        return None, f"{card['list']} list is never swept"
    if job is None:
        return None, "v3 has no record of this job"
    if job["id"] in bid_on:
        return None, "there is a proposal against this job"
    if (job.get("icp_tier") or "") in ("icp", "icp_near"):
        return None, f"ICP tier {job['icp_tier']}"
    age_days = (now_ms - card["created_ms"]) / 86_400_000.0
    if fresh_days and age_days < fresh_days:
        return None, f"carded {age_days:.1f}d ago, inside the freshness window"
    skip = scoring.hard_rule_skip(job)
    if skip == "no_platform_named":
        return "it names no platform you work in", None
    if skip == "off_domain_title":
        return "voice / marketing / funnel / content work", None
    if skip:
        return f"hard rule: {skip}", None
    score = job.get("score")
    if score is None:
        return None, "never scored"
    if score < config.HOT_LEAD_THRESHOLD:
        return (f"scores {score} on the v3.0 scale, under the hot bar of"
                f" {config.HOT_LEAD_THRESHOLD}"), None
    return None, f"passes at {score}"


def check_status_vocabulary(tok: str) -> None:
    """Fail before the first write, not after the fiftieth. Every list must
    actually offer CLOSED_STATUS; a run that discovers otherwise mid-flight
    leaves a board full of comments explaining closures that never happened."""
    for list_id, name in LIST_NAMES.items():
        allowed = {(s.get("status") or "").lower()
                   for s in (req("GET", f"/list/{list_id}", tok).get("statuses") or [])}
        if CLOSED_STATUS not in allowed:
            sys.exit(f"list {name} has no '{CLOSED_STATUS}' status"
                     f" (it offers {sorted(allowed)}) — nothing written")
        time.sleep(0.3)


def plan(tok: str, fresh_days: float = 0.0) -> tuple:
    cards = open_cards(tok)
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        jobs, bid = _rows(conn), _bid_on(conn)
    finally:
        conn.close()
    now_ms = time.time() * 1000
    close, keep = [], []
    for c in cards:
        job = jobs.get(c["id"])
        why, why_not = verdict(c, job, bid, now_ms, fresh_days)
        row = dict(c, why=why or why_not,
                   score=(job or {}).get("score"), job_id=(job or {}).get("id"))
        (close if why else keep).append(row)
    return cards, close, keep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--fresh-days", type=float, default=0.0,
                    help="leave cards carded inside this many days alone"
                         " (default 0 — sweep the whole `new` stage)")
    a = ap.parse_args()
    tok = token()
    if a.live:
        check_status_vocabulary(tok)
    cards, close, keep = plan(tok, a.fresh_days)
    if a.limit:
        close = close[:a.limit]

    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPORTS / f"board-rescore-sweep-{stamp}.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["action", "task_id", "list", "status", "score", "why", "name", "url"])
        for r in close:
            w.writerow(["CLOSE", r["id"], r["list"], r["status"], r["score"],
                        r["why"], r["name"][:120], r["url"]])
        for r in keep:
            w.writerow(["keep", r["id"], r["list"], r["status"], r["score"],
                        r["why"], r["name"][:120], r["url"]])

    done, failed = 0, []
    if a.live:
        # progress on stdout, flushed: this run takes the better part of an hour
        # and a silent hour is indistinguishable from a hung one.
        print(f"closing {len(close)} cards, ~{len(close) * RATE_SLEEP_S * 2 / 60:.0f} min",
              flush=True)
        for n, r in enumerate(close, 1):
            try:
                req("POST", f"/task/{r['id']}/comment", tok,
                    {"comment_text": COMMENT.format(why=r["why"]),
                     "notify_all": False})
                time.sleep(RATE_SLEEP_S)
                req("PUT", f"/task/{r['id']}", tok, {"status": CLOSED_STATUS})
                done += 1
            except Exception as exc:                      # noqa: BLE001
                failed.append({"id": r["id"], "error": str(exc)[:120]})
            if n % 50 == 0:
                print(f"  {n}/{len(close)} closed, {len(failed)} failed", flush=True)
            time.sleep(RATE_SLEEP_S)

    print(json.dumps({
        "mode": "LIVE" if a.live else "DRY-RUN",
        "open_cards_seen": len(cards),
        "to_close": len(close), "kept": len(keep),
        "closed": done, "failed": failed[:10],
        "close_reasons": dict(Counter(r["why"].split(",")[0] for r in close)),
        "keep_reasons": dict(Counter(r["why"].split(" at ")[0].split(",")[0]
                                     for r in keep)),
        "csv": str(path),
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
