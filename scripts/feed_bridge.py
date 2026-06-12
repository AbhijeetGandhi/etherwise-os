#!/usr/bin/env python3
"""feed_bridge.py — deterministic Upwork feed extraction (scanner v4.14, P0).

Job data flows osascript → Python → SQLite without ever entering a model
context. This kills the fabrication class behind the June 10–11 incidents:
the LLM used to carry ids through its context and invented them under
pressure; a Python pipe cannot "reconstruct from memory".

Pipeline:
  1. osascript executes embedded JS in Chrome's ACTIVE TAB (the scanner prompt
     navigates first). The JS reads window.__NUXT__.state for job objects
     (exact ids/titles/timestamps) AND harvests literal <a href> links from
     the DOM keyed by the ~02<digits> token. hrefs are captured, NEVER
     reconstructed (upwork-job-recorder contract).
  2. Iron-Law validation per job: bare-numeric id, href on upwork.com
     containing ~02<id>, non-empty title.
  3. Batch sanity: intra-batch dedup; sequential-id run (the observed
     fabrication signature) aborts the whole batch with exit 2.
  4. Dedup vs jobs table (id = the only key), claim NEW rows status='New'
     (pre-score; the scoring step updates them — sync whitelist skips 'New').
  5. stdout: ONE line of JSON {feed, extracted, valid, new, known,
     claimed_ids, invalid, dry_run}. Diagnostics go to stderr.

Requires: Chrome → View → Developer → Allow JavaScript from Apple Events.

Usage:
  python3 feed_bridge.py my_feed                # extract + claim
  python3 feed_bridge.py best_matches --dry-run # no writes, full report
  python3 feed_bridge.py my_feed --dump-state   # diagnostic: NUXT shape
  (--db PATH and --json-file PATH exist for tests/debug.)
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import job_id_guard  # noqa: E402  (shared fabricated-id patterns)

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "etherwise-os").is_dir())   # workspace root, symlink-safe
DB = ROOT / "etherwise-os/etherwise.db"
FEEDS = ("most_recent", "best_matches", "my_feed")        # exact, lowercase
ID_RE = re.compile(r"^\d{15,25}$")
HOST = "https://www.upwork.com/jobs/"
OSASCRIPT_TIMEOUT = 45


class BridgeError(Exception):
    pass


class FabricationSuspected(BridgeError):
    """Batch ids look synthetic (consecutive run) — refuse everything."""


# ── JS payload (runs inside Chrome) ───────────────────────────────────────────
# DOM-tile harvest. Upwork removed window.__NUXT__ from the find-work pages
# (verified live 2026-06-11 — page is fully hydrated with zero NUXT globals),
# so the cards themselves are the data source: literal <a href>, anchor title,
# raw tile innerText. ALL parsing happens in Python where it's unit-tested;
# the JS stays dumb on purpose.
EXTRACT_JS = r"""
(function () {
  function grab(sel) {
    try { return Array.prototype.slice.call(document.querySelectorAll(sel)); }
    catch (e) { return []; }
  }
  var tiles = grab('section[data-ev-position]');
  if (tiles.length < 3) tiles = grab('div[data-test="job-tile-list"] > *');
  if (tiles.length < 3) tiles = grab('[data-test*="job-tile"]');
  var out = [];
  tiles.forEach(function (t) {
    var a = t.querySelector('a[href*="~02"]');
    if (!a) return;
    out.push({
      href: a.href,
      title: (a.textContent || '').trim(),
      text: (t.innerText || '').slice(0, 4000)
    });
  });
  return JSON.stringify({url: location.href, tile_count: tiles.length,
                         tiles: out});
})()
"""

DUMP_JS = r"""
(function () {
  var s = (window.__NUXT__ || {}).state || {};
  var keys = Object.keys(s).map(function (k) {
    var v = s[k];
    var kind = Array.isArray(v) ? ('array[' + v.length + ']') : typeof v;
    return k + ': ' + kind;
  });
  return JSON.stringify({url: location.href, has_nuxt: !!window.__NUXT__,
                         state_keys: keys}, null, 1);
})()
"""


def chrome_eval(js: str, tab_ref=None) -> str:
    """Execute JS via osascript; returns the JS result.

    tab_ref=None targets the ACTIVE tab (v4.14.x scanner-prompt behavior).
    tab_ref=(window_id, tab_index) targets a specific tab WITHOUT focusing
    it — the M1a shadow pipeline uses a dedicated parked tab and must never
    touch the user's active tab."""
    if tab_ref is None:
        target = "active tab of front window"
    else:
        window_id, tab_index = tab_ref
        target = f"tab {int(tab_index)} of window id {int(window_id)}"
    script = (f'tell application "Google Chrome" to execute {target}'
              ' javascript (item 1 of argv)')
    try:
        proc = subprocess.run(
            ["osascript", "-e", "on run argv", "-e", script, "-e", "end run",
             js],
            capture_output=True, text=True, timeout=OSASCRIPT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise BridgeError("osascript timed out — is Chrome responsive?")
    if proc.returncode != 0:
        err = proc.stderr.strip()
        hint = ""
        if "JavaScript through AppleScript" in err or "1743" in err \
                or "not allowed" in err.lower():
            hint = (" — enable Chrome menu: View > Developer >"
                    " Allow JavaScript from Apple Events, then retry")
        raise BridgeError(f"osascript failed: {err[:300]}{hint}")
    return proc.stdout.strip()


# ── tile parsing (pure, unit-tested against real card text) ──────────────────
_POSTED_RE = re.compile(
    r"Posted\s+(?:(\d+)\s+(minute|hour|day|week|month)s?\s+ago|(yesterday))",
    re.I)
_NOISE_PREFIXES = ("Job feedback", "Save job", "Posted ", "Proposals:",
                   "Skills")


def parse_posted_ago(text, now=None):
    """'Posted 10 hours ago' -> ISO 8601 UTC. Deterministic arithmetic —
    the recency signal must never be invented."""
    from datetime import timedelta
    m = _POSTED_RE.search(text or "")
    if not m:
        return None
    now = now or datetime.now(timezone.utc)
    if m.group(3):
        delta = timedelta(days=1)
    else:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = {"minute": timedelta(minutes=n), "hour": timedelta(hours=n),
                 "day": timedelta(days=n), "week": timedelta(weeks=n),
                 "month": timedelta(days=30 * n)}[unit]
    return (now - delta).isoformat()


def parse_budget_line(line):
    out = {"contract_type": None, "hourly_min": None, "hourly_max": None,
           "fixed_budget": None, "experience_level": None, "engagement": None,
           "engagement_duration": None}
    line = line or ""
    if re.search(r"fixed", line, re.I):
        out["contract_type"] = "Fixed"
    elif re.search(r"hourly", line, re.I):
        out["contract_type"] = "Hourly"
    m = re.search(r"Hourly:?\s*\$([\d,.]+)\s*[-–]\s*\$([\d,.]+)", line)
    if m:
        out["hourly_min"] = float(m.group(1).replace(",", ""))
        out["hourly_max"] = float(m.group(2).replace(",", ""))
    m = re.search(r"Budget:?\s*\$([\d,]+(?:\.\d+)?)", line)
    if m:
        out["fixed_budget"] = float(m.group(1).replace(",", ""))
    m = re.search(r"\b(Entry|Intermediate|Expert)\b", line)
    if m:
        out["experience_level"] = m.group(1)
    m = re.search(r"(\d+\+?\s*hrs?/week)", line)
    if m:
        out["engagement"] = m.group(1)
    m = re.search(r"Est\.?\s*Time:\s*([^,\n]+)", line)
    if m:
        out["engagement_duration"] = m.group(1).strip()
    return out


def parse_spent(text):
    m = re.search(r"\$([\d,.]+)\s*([KkMm])?\+?\s*spent", text or "")
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    return val * (1000.0 if unit == "k" else 1_000_000.0 if unit == "m"
                  else 1.0)


def parse_tile(tile, now=None):
    """Raw {href, title, text} from one feed card -> recorder-shaped job dict.
    Claim-critical fields (id, href, title, created_dt) are strict; the rest
    is best-effort enrichment per 'don't drop fields you already scraped'."""
    href = tile.get("href") or ""
    text = tile.get("text") or ""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    m = re.search(r"~02(\d{15,25})", href)
    job = {"id": m.group(1) if m else None, "href": href,
           "title": (tile.get("title") or "").strip(),
           "created_dt": parse_posted_ago(text, now=now)}

    budget_line = next((l for l in lines
                        if re.search(r"Fixed-price|Hourly", l)), "")
    job.update(parse_budget_line(budget_line))

    m = re.search(r"Proposals:\s*(\d+)", text)
    job["total_applicants"] = int(m.group(1)) if m else None  # tier low bound

    candidates = [l for l in lines if len(l) > 60
                  and not l.startswith(_NOISE_PREFIXES) and l != budget_line]
    job["description"] = max(candidates, key=len) if candidates else ""

    skills = []
    if "Skills" in lines:
        for l in lines[lines.index("Skills") + 1:]:
            if l == "Verified" or "spent" in l \
                    or l.startswith(("Payment verified", "Rating is")):
                break
            skills.append(l)
    job["skills"] = skills

    job["client_payment_verified"] = 1 if "Payment verified" in text else 0
    m = re.search(r"Rating is ([\d.]+) out of 5", text)
    job["client_rating"] = float(m.group(1)) if m else None
    job["client_total_spent"] = parse_spent(text)
    last = lines[-1] if lines else ""
    job["client_country"] = last if (last and len(last) <= 40 and not
                                     re.search(r"spent|Rating|verified|Skills"
                                               r"|ago", last, re.I)) else None
    return job


# ── validation (Iron Law) ─────────────────────────────────────────────────────

def validate_job(job: dict):
    """(ok, reason). Hard requirements only — optional fields stay optional."""
    jid = str(job.get("id") or "")
    if not ID_RE.fullmatch(jid):
        return False, f"id not bare-numeric 15-25 digits: {jid[:40]!r}"
    if job_id_guard.looks_fabricated(jid):
        return False, ("fabricated-pattern id (zeros/keyboard-walk) —"
                       " refusing to claim")
    href = str(job.get("href") or "")
    if not href:
        return False, "href missing (recorder forbids reconstruction)"
    if not href.startswith(HOST):
        return False, f"href not on {HOST}: {href[:60]!r}"
    if f"~02{jid}" not in href:
        return False, f"href/id mismatch (corruption signature): {href[:80]!r}"
    if not str(job.get("title") or "").strip():
        return False, "empty title"
    return True, ""


def batch_sanity(ids) -> None:
    """Abort on >=3 consecutive ids — the observed fabrication signature
    (sequential 2064750000000000001, ...002). Real feeds never do this."""
    nums = sorted(int(i) for i in ids)
    run = 1
    for a, b in zip(nums, nums[1:]):
        run = run + 1 if b - a == 1 else 1
        if run >= 3:
            raise FabricationSuspected(
                "3+ consecutive job ids in batch — refusing to claim"
                f" (around {b})")


def dedupe_batch(jobs):
    seen, out = set(), []
    for j in jobs:
        jid = str(j.get("id") or "")
        if jid in seen:
            continue
        seen.add(jid)
        out.append(j)
    return out


# ── claim (upwork-job-recorder contract) ──────────────────────────────────────

def known_ids(conn, ids, table: str = "jobs"):
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    return {r[0] for r in conn.execute(
        f"SELECT id FROM {table} WHERE id IN ({marks})", list(ids))}


def claim_new(conn, feed: str, job: dict, table: str = "jobs") -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        f"""INSERT INTO {table} (id, job_url, title, description, created_dt,
             fetched_at, feed_source, contract_type, hourly_min, hourly_max,
             fixed_budget, weekly_budget, total_applicants, experience_level,
             engagement, engagement_duration, skills_json, client_country,
             client_total_spent, client_hires, client_rating,
             client_payment_verified, applied, status, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'New',?)""",
        (job["id"], job["href"], job.get("title"),
         job.get("description") or "", job.get("created_dt"), now, feed,
         job.get("contract_type"), job.get("hourly_min"),
         job.get("hourly_max"), job.get("fixed_budget"),
         job.get("weekly_budget"), job.get("total_applicants"),
         job.get("experience_level"), job.get("engagement"),
         job.get("engagement_duration"),
         json.dumps(job.get("skills") or []), job.get("client_country"),
         job.get("client_total_spent"), job.get("client_hires"),
         job.get("client_rating"),
         1 if job.get("client_payment_verified") else 0, now))


def process(feed: str, jobs, db_path=DB, dry_run: bool = False,
            table: str = "jobs") -> dict:
    if feed not in FEEDS:
        sys.exit(f"feed must be one of {FEEDS} (exact, lowercase) — got"
                 f" {feed!r}")
    extracted = len(jobs)
    jobs = dedupe_batch(jobs)
    valid, invalid = [], 0
    for j in jobs:
        ok, reason = validate_job(j)
        if ok:
            valid.append(j)
        else:
            invalid += 1
            print(f"INVALID {str(j.get('id'))[:30]!r}: {reason}",
                  file=sys.stderr)
    batch_sanity([j["id"] for j in valid])

    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        existing = known_ids(conn, [j["id"] for j in valid], table=table)
        new_jobs = [j for j in valid if j["id"] not in existing]
        claimed = []
        for j in new_jobs:
            if not dry_run:
                try:
                    claim_new(conn, feed, j, table=table)
                except sqlite3.IntegrityError:
                    continue            # raced an overlapping run: it's known
            claimed.append(j["id"])
        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return {"feed": feed, "extracted": extracted, "valid": len(valid),
            "new": len(claimed), "known": len(existing),
            "claimed_ids": claimed, "invalid": invalid, "dry_run": dry_run}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("feed", choices=FEEDS)
    p.add_argument("--dry-run", action="store_true",
                   help="validate + dedup, claim nothing")
    p.add_argument("--db", default=str(DB))
    p.add_argument("--json-file", help="read extraction JSON from a file"
                                       " instead of Chrome (debug/tests)")
    p.add_argument("--dump-state", action="store_true",
                   help="print NUXT state shape from the active tab and exit")
    args = p.parse_args()

    if args.dump_state:
        print(chrome_eval(DUMP_JS))
        return 0

    if args.json_file:
        payload = json.loads(Path(args.json_file).read_text())
    else:
        raw = chrome_eval(EXTRACT_JS)
        if not raw:
            print("ERROR: empty result from Chrome (wrong tab?)",
                  file=sys.stderr)
            return 1
        payload = json.loads(raw)

    if "tiles" in payload:
        jobs = [parse_tile(t) for t in payload["tiles"]]
    else:
        jobs = payload.get("jobs") or []     # pre-normalized (tests/debug)
    print(f"tab: {payload.get('url')}  tiles: {payload.get('tile_count')}"
          f"  with_links: {len(jobs)}", file=sys.stderr)
    try:
        summary = process(args.feed, jobs,
                          db_path=args.db, dry_run=args.dry_run)
    except FabricationSuspected as exc:
        print(json.dumps({"feed": args.feed, "error": str(exc),
                          "claimed_ids": []}))
        return 2
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
