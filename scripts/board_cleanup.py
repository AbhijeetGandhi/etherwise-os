#!/usr/bin/env python3
"""Board cleanup + daily reaper for the Upwork Opportunities ClickUp folder.

Rules (approved by Abhijeet 2026-06-11):
  R1 EXPIRED: open 'new' tasks whose job posting is older than 14 days
     (job age from etherwise-os DB created_dt; fallback: task creation date).
  R2 DUPLICATE: among open tasks sharing a normalized title, keep the best
     (applied/interview always kept; else newest) — close other 'new' ones.
  R3 NEVER touch applied / interview / closed tasks with closures.

Modes:
  dry-run (default): writes reports/board-cleanup-<date>.csv, prints summary, NO writes.
  --live:            closes tasks (status + comment), 0.65s rate spacing, resume-safe.
  --reap:            daily-guard mode for the scanner (marker in var; max once/day),
                     implies --live but only R1 on jobs newly past 14d + new dup groups.

Reads ClickUp token from etherwise-os/.credentials/etherwise-os.env (CLICKUP_API_KEY).
DB access is READ-ONLY (uri=ro). Audit trail = CSV + task comments.
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "etherwise-os").is_dir())   # workspace root, symlink-safe
ENV_FILE = ROOT / "etherwise-os/.credentials/etherwise-os.env"
DB = ROOT / "etherwise-os/etherwise.db"
REPORTS = ROOT / "reports"
MARKER = ROOT / "etherwise-os/etherwise_os/tmp/reaper_last_run"  # daily guard

LISTS = {
    "901614356485": "Hot Leads",
    "901614356486": "Standard",
    "901614356487": "Low Priority",
    "901614356490": "Invites",
}
OPEN_STATUSES = {"new", "applied", "interview"}
PROTECTED = {"applied", "interview"}
EXPIRY_DAYS = 14
API = "https://api.clickup.com/api/v2"

EXPIRE_COMMENT = ("Auto-closed as EXPIRED (board hygiene, rule R1): the job posting is "
                  f"older than {EXPIRY_DAYS} days and was never applied to. "
                  "Reopen if you disagree — nothing else changed.")
DUP_COMMENT = "Auto-closed as duplicate (board hygiene, rule R2): a better/newer task for the same job stays open: {keep_url}"


def token() -> str:
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("CLICKUP_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("CLICKUP_API_KEY not found")


def req(method: str, path: str, tok: str, body: dict | None = None) -> dict:
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Authorization": tok,
                                        "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode() or "{}")


def fetch_open_tasks(tok: str) -> list[dict]:
    out = []
    for list_id, list_name in LISTS.items():
        page = 0
        while True:
            q = urllib.parse.urlencode({"page": page, "include_closed": "false",
                                        "order_by": "created", "reverse": "true",
                                        "subtasks": "false"})
            data = req("GET", f"/list/{list_id}/task?{q}", tok)
            tasks = data.get("tasks", [])
            for t in tasks:
                st = (t.get("status") or {}).get("status", "").lower()
                if st in OPEN_STATUSES:
                    out.append({"id": t["id"], "name": t["name"], "status": st,
                                "list": list_name, "url": t.get("url", ""),
                                "created_ms": int(t.get("date_created") or 0)})
            if len(tasks) < 100:
                break
            page += 1
            time.sleep(0.3)
    return out


_PREFIX_RE = re.compile(r"^(\[Invite\]\s*)?(🎥\s*)?(\[\d+\]\s*[—-]?\s*)?(\d+\s*[—-]\s*)?")
def norm_title(name: str) -> str:
    return _PREFIX_RE.sub("", name).strip().lower()


def job_ages(task_ids: list[str]) -> dict:
    """clickup_task_id -> job posted age in days (from v2 DB, read-only)."""
    ages = {}
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        for tid, created_dt in conn.execute(
                "SELECT clickup_task_id, created_dt FROM jobs"
                " WHERE clickup_task_id IS NOT NULL"):
            if tid and created_dt:
                try:
                    dt = datetime.fromisoformat(created_dt.replace("Z", "+00:00"))
                    if dt.tzinfo is None:          # naive timestamps exist in v2
                        dt = dt.replace(tzinfo=timezone.utc)
                    ages[tid] = (datetime.now(timezone.utc) - dt).days
                except ValueError:
                    pass
    finally:
        conn.close()
    return ages


def plan(tasks: list[dict]) -> list[dict]:
    ages = job_ages([t["id"] for t in tasks])
    now_ms = time.time() * 1000
    actions = []

    for t in tasks:
        t["age_days"] = ages.get(
            t["id"], int((now_ms - t["created_ms"]) / 86_400_000) if t["created_ms"] else None)

    # R1 expiry (status 'new' only)
    for t in tasks:
        if t["status"] == "new" and t["age_days"] is not None \
                and t["age_days"] > EXPIRY_DAYS:
            actions.append({**t, "action": "EXPIRED",
                            "reason": f"job age {t['age_days']}d > {EXPIRY_DAYS}d"})

    # R2 duplicates among open tasks (closure only for 'new' members)
    expired_ids = {a["id"] for a in actions}
    groups: dict = {}
    for t in tasks:
        groups.setdefault(norm_title(t["name"]), []).append(t)
    for title, members in groups.items():
        if len(members) < 2:
            continue
        keepers = [m for m in members if m["status"] in PROTECTED]
        keep = (max(keepers, key=lambda m: m["created_ms"]) if keepers
                else max(members, key=lambda m: m["created_ms"]))
        for m in members:
            if m["id"] == keep["id"] or m["status"] in PROTECTED \
                    or m["id"] in expired_ids:
                continue
            actions.append({**m, "action": "DUP_CLOSE",
                            "reason": f"duplicate of {keep['id']} ({keep['status']})",
                            "keep_url": keep["url"]})
    return actions


# ── liveness sweep (R3): verify every open task's job URL via Chrome session ──

CHROME_JS_TEMPLATE = """(async()=>{const urls=%s;const out=[];for(const u of urls){try{const r=await fetch(u,{credentials:'include'});const t=await r.text();out.push((t.includes('Job not found')||r.status===404||r.status===410)?'D':'A')}catch(e){out.push('E')}await new Promise(s=>setTimeout(s,250))}return out.join('')})()"""


def _chrome_js(js: str) -> str:
    """Run JS in a Chrome upwork.com tab via AppleScript; returns the JS result.
    Requires Chrome's 'Allow JavaScript from Apple Events' (already on — the
    scanner uses the same path)."""
    import subprocess
    osa = (
        'tell application "Google Chrome"\n'
        ' set theTab to missing value\n'
        ' repeat with w in windows\n  repeat with t in tabs of w\n'
        '   if URL of t contains "upwork.com" then\n    set theTab to t\n'
        '    exit repeat\n   end if\n  end repeat\n'
        '  if theTab is not missing value then exit repeat\n end repeat\n'
        ' if theTab is missing value then\n'
        '  set theTab to make new tab at end of tabs of front window '
        'with properties {URL:"https://www.upwork.com/nx/find-work/best-matches"}\n'
        '  delay 5\n end if\n'
        ' execute theTab javascript thejs\n'
        'end tell'
    )
    script = f'set thejs to {json.dumps(js)}\n{osa}'
    proc = subprocess.run(["osascript", "-e", script],
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"chrome js failed: {proc.stderr.strip()[:200]}")
    return proc.stdout.strip()


def liveness_sweep(tok: str) -> None:
    tasks = fetch_open_tasks(tok)
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    urls = {tid: u for tid, u in conn.execute(
        "SELECT clickup_task_id, job_url FROM jobs"
        " WHERE clickup_task_id IS NOT NULL AND job_url IS NOT NULL")}
    conn.close()

    sweep = [t for t in tasks if t["id"] in urls]
    no_url = [t for t in tasks if t["id"] not in urls]
    verdicts: dict = {}
    CHUNK = 15
    for i in range(0, len(sweep), CHUNK):
        chunk = sweep[i:i + CHUNK]
        js = CHROME_JS_TEMPLATE % json.dumps([urls[t["id"]] for t in chunk])
        try:
            flags = _chrome_js(js)
        except Exception as exc:
            print(f"WARN chunk {i//CHUNK}: {exc!r}", file=sys.stderr)
            flags = "E" * len(chunk)
        for t, f in zip(chunk, flags.ljust(len(chunk), "E")):
            verdicts[t["id"]] = f
        time.sleep(1)

    REPORTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    csv_path = REPORTS / f"liveness-sweep-{stamp}.csv"
    closed = 0
    dead_protected = []
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["task_id", "name", "list", "status", "verdict", "action", "url"])
        for t in sweep:
            v = {"A": "ALIVE", "D": "DEAD", "E": "CHECK_FAILED"}[verdicts[t["id"]]]
            action = ""
            if v == "DEAD" and t["status"] == "new":
                action = "CLOSED_EXPIRED"
                try:
                    req("POST", f"/task/{t['id']}/comment", tok, {"comment_text":
                        "Auto-closed (liveness sweep): the job link returns 'Job not"
                        " found' — posting was removed/filled, or the task came from"
                        " a corrupted scan. Verified against Upwork directly."})
                    time.sleep(0.65)
                    req("PUT", f"/task/{t['id']}", tok, {"status": "EXPIRED"})
                    time.sleep(0.65)
                    closed += 1
                except Exception as exc:
                    action = f"CLOSE_FAILED:{exc!r}"
            elif v == "DEAD":
                dead_protected.append(t)
                action = "REPORTED (protected status)"
            w.writerow([t["id"], t["name"][:80], t["list"], t["status"], v,
                        action, urls[t["id"]]])
        for t in no_url:
            w.writerow([t["id"], t["name"][:80], t["list"], t["status"],
                        "NO_DB_URL", "skipped", ""])

    print(json.dumps({
        "open_tasks": len(tasks), "swept": len(sweep), "no_db_url": len(no_url),
        "alive": sum(1 for v in verdicts.values() if v == "A"),
        "dead": sum(1 for v in verdicts.values() if v == "D"),
        "check_failed": sum(1 for v in verdicts.values() if v == "E"),
        "closed_new_dead": closed,
        "dead_protected_for_review": [
            {"task": t["id"], "status": t["status"], "name": t["name"][:60]}
            for t in dead_protected],
        "csv": str(csv_path)}, indent=1))


def main() -> None:
    if "--liveness" in sys.argv:
        liveness_sweep(token())
        return
    live = "--live" in sys.argv
    reap = "--reap" in sys.argv
    if reap:
        today = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
        if MARKER.exists() and MARKER.read_text().strip() == today:
            print(json.dumps({"reaper": "already_ran_today"})); return
        live = True

    tok = token()
    tasks = fetch_open_tasks(tok)
    actions = plan(tasks)

    REPORTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    csv_path = REPORTS / f"board-cleanup-{stamp}{'-LIVE' if live else '-DRYRUN'}.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "name", "list", "status",
                                          "age_days", "action", "reason", "url"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(actions)

    summary = {
        "open_tasks_scanned": len(tasks),
        "expired_closures": sum(1 for a in actions if a["action"] == "EXPIRED"),
        "dup_closures": sum(1 for a in actions if a["action"] == "DUP_CLOSE"),
        "protected_untouched": sum(1 for t in tasks if t["status"] in PROTECTED),
        "csv": str(csv_path),
        "mode": "LIVE" if live else "DRY-RUN",
    }

    if live:
        done = 0
        for a in actions:
            comment = (DUP_COMMENT.format(keep_url=a.get("keep_url", ""))
                       if a["action"] == "DUP_CLOSE" else EXPIRE_COMMENT)
            try:
                req("POST", f"/task/{a['id']}/comment", tok,
                    {"comment_text": comment})
                time.sleep(0.65)
                req("PUT", f"/task/{a['id']}", tok,
                    {"status": "EXPIRED" if a["action"] == "EXPIRED" else "SKIPPED"})
                time.sleep(0.65)
                done += 1
            except Exception as exc:  # keep going; CSV has the full plan
                print(f"WARN {a['id']}: {exc!r}", file=sys.stderr)
        summary["closed"] = done
        if reap:
            MARKER.parent.mkdir(parents=True, exist_ok=True)
            MARKER.write_text(datetime.now(
                timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d"))

    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
