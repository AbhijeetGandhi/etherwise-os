"""Self-audit checks (architecture §4 doctor). bin/doctor renders these.

Each check_* returns a list of Check(status, name, detail) with status in
PASS | WARN | FAIL | SKIP. Deterministic checks take injectable inputs so
they're unit-testable; only check_models_api touches the network.

Secret hygiene rule: scan findings name the file and line, NEVER the matched
text — the doctor must not become the leak.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

from core import config, db
from core import guardrails


@dataclass
class Check:
    status: str   # PASS | WARN | FAIL | SKIP
    name: str
    detail: str = ""


_SECRET_PATTERNS = (
    ("anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")),
    ("airtable PAT", re.compile(r"\bpat[A-Za-z0-9]{14,}\.[A-Za-z0-9]+")),
    ("clickup key", re.compile(r"\bpk_\d+_[A-Za-z0-9]+")),
    ("inline password", re.compile(r"(?i)password\s*[=:]\s*['\"][^'\"]{8,}")),
)

_EXPECTED_TABLES = {"runs", "claude_usage", "audit_log", "anomalies",
                    "sync_cursors", "shadow_ledger", "quarantine",
                    "schema_migrations"}


def check_python() -> list:
    checks = []
    v = sys.version_info
    if (v.major, v.minor) >= (3, 9):
        checks.append(Check("PASS", "python", f"{v.major}.{v.minor}.{v.micro}"))
    else:
        checks.append(Check("FAIL", "python",
                            f"{v.major}.{v.minor} < 3.9 minimum"))
    try:
        import anthropic
        checks.append(Check("PASS", "anthropic sdk", anthropic.__version__))
    except ImportError:
        checks.append(Check("FAIL", "anthropic sdk",
                            "not importable — payg route is dead"
                            " (pip3 install anthropic)"))
    return checks


def check_config_drift(root: Optional[Path] = None) -> list:
    root = root or config.V3_ROOT
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "show", "HEAD:core/config.py"],
            capture_output=True, timeout=10)
        if head.returncode != 0:
            return [Check("WARN", "config drift",
                          "could not read HEAD:core/config.py")]
        import hashlib
        if hashlib.sha256(head.stdout).hexdigest() == config.config_sha256():
            return [Check("PASS", "config drift", "config.py matches git HEAD")]
        return [Check("WARN", "config drift",
                      "core/config.py differs from git HEAD — commit the"
                      " reviewed change (config edits arrive only as commits)")]
    except Exception as exc:
        return [Check("WARN", "config drift", f"check failed: {exc!r}")]


def check_credentials(creds_dir: Optional[Path] = None) -> list:
    creds_dir = creds_dir or config.CREDENTIALS_DIR
    if not creds_dir.is_dir():
        return [Check("FAIL", "credentials dir", f"{creds_dir} missing")]
    checks = []
    if creds_dir.stat().st_mode & 0o077:
        checks.append(Check("WARN", "credentials dir",
                            "group/other access bits set — chmod 700"))
    else:
        checks.append(Check("PASS", "credentials dir", str(creds_dir)))

    env_file = creds_dir / "etherwise-os.env"
    if not env_file.is_file():
        checks.append(Check("FAIL", "etherwise-os.env", "missing"))
    else:
        if env_file.stat().st_mode & 0o077:
            checks.append(Check("WARN", "etherwise-os.env",
                                "readable beyond owner — chmod 600"))
        has_key = any(
            line.startswith("ANTHROPIC_API_KEY=") and line.split("=", 1)[1].strip()
            for line in env_file.read_text().splitlines())
        checks.append(Check("PASS" if has_key else "FAIL", "anthropic api key",
                            "present" if has_key
                            else "ANTHROPIC_API_KEY missing/empty in env file"))

    for fname, why in (("upwork-api.json", "M1 reads it read-only"),
                       ("gmail-app-password.txt", "notify.py will need it")):
        p = creds_dir / fname
        if p.is_file():
            mode_warn = p.stat().st_mode & 0o077
            checks.append(Check("WARN" if mode_warn else "PASS", fname,
                                "readable beyond owner — chmod 600"
                                if mode_warn else "present"))
        else:
            checks.append(Check("WARN", fname, f"missing — {why}"))
    return checks


def _tracked_files(root: Path) -> list:
    out = subprocess.run(["git", "-C", str(root), "ls-files"],
                         capture_output=True, text=True, timeout=15)
    return [root / line for line in out.stdout.splitlines() if line]


def check_secret_scan(files: Optional[Iterable[Path]] = None,
                      root: Optional[Path] = None) -> list:
    root = root or config.V3_ROOT
    if files is None:
        files = _tracked_files(root)
    findings = []
    for path in files:
        try:
            if path.stat().st_size > 1_000_000:
                continue
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, pattern in _SECRET_PATTERNS:
                if pattern.search(line):
                    try:
                        shown = path.relative_to(root)
                    except ValueError:
                        shown = path.name
                    findings.append(Check(
                        "FAIL", "secret scan",
                        f"{shown}:{lineno} looks like {name}"
                        " — never commit secrets"))
    return findings or [Check("PASS", "secret scan",
                              "no plaintext secrets in tracked files")]


def check_db(db_path: Optional[Path] = None) -> list:
    db_path = db_path or config.DB_PATH
    if not Path(db_path).is_file():
        return [Check("FAIL", "database",
                      f"{db_path} missing — run PYTHONPATH=. python3 -m core.db")]
    checks = []
    with db.connect(db_path) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        checks.append(Check("PASS" if integrity == "ok" else "FAIL",
                            "db integrity", integrity))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        checks.append(Check("PASS" if mode == "wal" else "WARN",
                            "db journal mode", mode))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = _EXPECTED_TABLES - tables
        checks.append(Check("FAIL" if missing else "PASS", "db schema",
                            f"missing tables: {sorted(missing)}" if missing
                            else f"{len(_EXPECTED_TABLES)} ops tables present"))
        n = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    # A committed .sql that was never applied would let a rail throw a confusing
    # 'no such table' on its first scheduled run (payment-mail hit this — 023 was
    # committed, the live DB stopped at 022). Fail loud with the pending list.
    pending = db.pending_migrations(db_path)
    checks.append(Check("FAIL" if pending else "PASS", "migrations",
                        f"{n} applied, {len(pending)} PENDING: {pending}"
                        " — run PYTHONPATH=. python3 -m core.db" if pending
                        else f"{n} applied, none pending"))
    return checks


def check_pricing_coverage(models: Optional[dict] = None,
                           fallbacks: Optional[dict] = None,
                           pricing: Optional[dict] = None,
                           structured_unsupported=None) -> list:
    models = config.MODELS if models is None else models
    fallbacks = config.MODEL_FALLBACKS if fallbacks is None else fallbacks
    pricing = config.PRICING if pricing is None else pricing
    if structured_unsupported is None:
        structured_unsupported = config.STRUCTURED_OUTPUT_UNSUPPORTED
    checks = []
    for key, model in models.items():
        if model not in pricing:
            checks.append(Check("FAIL", "pricing coverage",
                                f"MODELS[{key!r}]={model} has no PRICING"
                                " — gateway cost math would crash"))
    for primary, chain in fallbacks.items():
        for target in chain:
            if target not in pricing:
                checks.append(Check("FAIL", "pricing coverage",
                                    f"fallback {target} has no PRICING"))
            if target in structured_unsupported:
                checks.append(Check(
                    "FAIL", "failover sanity",
                    f"fallback {target} lacks structured outputs — schema"
                    " calls would break mid-failover"))
    return checks or [Check("PASS", "pricing coverage",
                            f"{len(models)} models + fallbacks all priced;"
                            " failover chain schema-safe")]


def check_guardrails_selftest() -> list:
    cases = [
        ("mcp__claude_ai_Airtable__create_records_for_table", {}, "deny"),
        ("mcp__claude_ai_ClickUp__clickup_create_task", {}, "deny"),
        ("Write", {"file_path": ".claude/ALLOW_CORE_WRITES"}, "deny"),
        ("Bash", {"command": "rm -rf ~/Documents/x"}, "deny"),
        ("Bash", {"command": "curl https://api.upwork.com/graphql -d"
                             " 'mutation sendMessage'"}, "deny"),
        ("Read", {"file_path": "core/config.py"}, "allow"),
    ]
    mismatches = []
    for tool, tool_input, expected in cases:
        got = guardrails.evaluate_pretooluse(
            tool, tool_input, shadow_map={"upwork": True},
            core_writes_allowed=True, v3_root=config.V3_ROOT).action
        if got != expected:
            mismatches.append(f"{tool}: expected {expected}, got {got}")
    if mismatches:
        return [Check("FAIL", "guardrails self-test", "; ".join(mismatches))]
    return [Check("PASS", "guardrails self-test",
                  f"{len(cases)}/{len(cases)} canned rules hold")]


def check_hooks(root: Optional[Path] = None) -> list:
    root = root or config.V3_ROOT
    checks = []
    settings = root / ".claude/settings.json"
    try:
        hooks_cfg = json.loads(settings.read_text()).get("hooks", {})
        wired = sorted(hooks_cfg)
        ok = {"PreToolUse", "PostToolUse", "SessionStart"} <= set(wired)
        checks.append(Check("PASS" if ok else "FAIL", "hooks wiring",
                            f"settings.json events: {wired}"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(Check("FAIL", "hooks wiring",
                            f"settings.json unreadable: {exc}"))
    for shim in ("pretooluse.py", "posttooluse.py", "sessionstart.py"):
        p = root / ".claude/hooks" / shim
        checks.append(Check("PASS" if p.is_file() else "FAIL",
                            f"hook {shim}",
                            "present" if p.is_file() else "missing"))
    marker = root / guardrails.MARKER_REL
    checks.append(Check("PASS", "write-guard mode",
                        "build-phase marker ACTIVE (core writes allowed)"
                        if marker.exists() else
                        "operational lockdown (core writes denied)"))
    return checks


def check_calendar(today: Optional[str] = None) -> list:
    today_d = date.fromisoformat(today) if today \
        else date.fromtimestamp(__import__("time").time())
    items = [(d, note) for d, note in config.CALENDAR_WATCH]
    items += [(d, f"{model}: {note}")
              for model, (d, note) in config.DEPRECATION_WATCH.items()]
    checks = []
    for d, note in sorted(items):
        delta = (date.fromisoformat(d) - today_d).days
        if delta < 0:
            checks.append(Check("WARN", "calendar",
                                f"{d} OVERDUE by {-delta}d: {note}"))
        elif delta <= 7:
            checks.append(Check("WARN", "calendar",
                                f"{d} in {delta}d: {note}"))
        else:
            checks.append(Check("PASS", "calendar",
                                f"{d} in {delta}d: {note}"))
    return checks


def check_models_api(offline: bool = False) -> list:
    if offline:
        return [Check("SKIP", "model ids vs /v1/models", "--offline")]
    model_ids = sorted(set(config.MODELS.values())
                       | {m for chain in config.MODEL_FALLBACKS.values()
                          for m in chain})
    try:
        import anthropic
        from core.claude_gateway import _load_api_key
        client = anthropic.Anthropic(api_key=_load_api_key(),
                                     timeout=20, max_retries=0)
        checks = []
        for mid in model_ids:
            try:
                client.models.retrieve(mid)
                checks.append(Check("PASS", "model id", mid))
            except anthropic.NotFoundError:
                checks.append(Check("FAIL", "model id",
                                    f"{mid} not found on /v1/models —"
                                    " pinned snapshot gone?"))
            except anthropic.AuthenticationError:
                return [Check("WARN", "model ids vs /v1/models",
                              "auth failed — check ANTHROPIC_API_KEY")]
        return checks
    except Exception as exc:
        return [Check("WARN", "model ids vs /v1/models",
                      f"unreachable: {exc!r}")]


def check_plists(root: Optional[Path] = None) -> list:
    root = root or config.V3_ROOT
    plists = sorted((root / "launchd").glob("*.plist"))
    if not plists:
        return [Check("SKIP", "launchd plists", "none yet (arrive with M1)")]
    checks = []
    for p in plists:
        lint = subprocess.run(["plutil", "-lint", str(p)],
                              capture_output=True, text=True)
        checks.append(Check("PASS" if lint.returncode == 0 else "FAIL",
                            f"plist {p.name}", lint.stdout.strip()))
    return checks


def check_source_freshness(db_path: Optional[Path] = None,
                           now=None) -> list:
    """Every number-backing source must be fresh AND un-degraded, or the numbers
    it feeds are lying. A critical source that is stale (stopped producing) or
    degraded (ran thin) is a WARN — amber, not red: the data is suspect, not
    corrupt, and a human should look before trusting the affected KPI."""
    from core import source_health as sh
    db_path = db_path or config.DB_PATH
    if not Path(db_path).is_file():
        return [Check("SKIP", "source freshness", "no database yet")]
    try:
        with db.connect(db_path) as conn:
            health = sh.source_health(conn, now=now)
    except Exception as exc:  # noqa: BLE001 — a health read must never crash doctor
        return [Check("WARN", "source freshness", f"check failed: {exc!r}")]

    checks = []
    for s in health["sources"]:
        if s["ok"]:
            checks.append(Check("PASS", f"source {s['key']}",
                                f"fresh ({s['age_hours']}h ago)"))
            continue
        status = s["status"]
        if status == "down":
            msg = "has NEVER produced a successful run"
        elif status == "stale":
            msg = (f"STALE — last success {s['age_hours']}h ago"
                   f" (max {s['max_age_hours']}h); {s['why']}")
        else:  # degraded
            msg = f"DEGRADED this run — {s['why']}"
        # A BROKEN RAIL IS NEVER 'PASS'. This line used to read
        #     Check("WARN" if s["critical"] else "PASS", ...)
        # on the reasoning that only critical sources should escalate the
        # verdict and the rest merely inform. But the doctor has exactly four
        # words and PASS is not one of the informational ones, so the output
        # read, literally:
        #     [PASS] source jarvis_imap_02  DEGRADED this run — ... a dead rail
        #                                   means that inbox goes unseen
        # for eleven days while that inbox went unread (2026-08-20). A health
        # check that prints PASS beside the word DEGRADED is worse than no
        # health check: it is scanned for red, and there was none to find.
        # WARN is still amber and still does not set the exit code (bin/doctor
        # exits non-zero on FAIL alone), so nothing that consumed the old
        # verdict changes — only what a human reading it is told.
        # Criticality moves into the MESSAGE, where it belongs.
        checks.append(Check("WARN", f"source {s['key']}",
                            msg if s["critical"] else f"{msg} [non-critical]"))
    return checks


def check_upwork_mcp_twins(db_path: Optional[Path] = None, inbox: Optional[Path] = None,
                           now=None) -> list:
    """The CSV import's retirement criterion (Upwork chat 2026-08-26, use case
    A of docs/upwork/MCP_USE_CASES.md): every Upwork row in the CSV exports on
    disk that the MCP ledger rail could have seen has a twin in upwork_mcp_rows
    (migration 030) carrying the same signed amount. "Could have seen" = record
    id at or above the smallest id the rail read among rows invoiced on or
    after its earliest window floor (sync_cursors upwork_financials_floor) —
    ids grow with the invoice date, and the MCP's date filter is loose on the
    old side, so the floor is drawn by date first, then by id (the first live
    run flagged a 10 Aug connects purchase the 12 Aug window never held).
    Reported for the last two COMPLETED weeks (Mon-Sun by the CSV's payment
    date) plus the running one; 2/2 clean is the signal for the Money import
    card to go audit-only. WARN, never FAIL: the rail is young and this is a
    decision aid, not a ledger invariant (those are finance-doctor's)."""
    import datetime as _dt
    db_path = db_path or config.DB_PATH
    if not Path(db_path).is_file():
        return [Check("SKIP", "upwork mcp twins", "no database yet")]
    try:
        with db.connect(db_path) as conn:
            have = conn.execute("SELECT name FROM sqlite_master WHERE type='table'"
                                " AND name='upwork_mcp_rows'").fetchone()
            if not have:
                return [Check("SKIP", "upwork mcp twins", "migration 030 not applied yet")]
            seen = {r["record_id"]: dict(r) for r in conn.execute(
                "SELECT record_id, amount_minor, outcome, invoice_dt, first_seen_at"
                " FROM upwork_mcp_rows")}
            floor_row = conn.execute("SELECT value FROM sync_cursors WHERE name="
                                     "'upwork_financials_floor'").fetchone()
            floor_day = floor_row["value"] if floor_row else None
    except Exception as exc:  # noqa: BLE001 — a decision aid must never crash doctor
        return [Check("WARN", "upwork mcp twins", f"check failed: {exc!r}")]
    if not seen:
        return [Check("SKIP", "upwork mcp twins", "the MCP ledger rail has not read a row yet")]
    inbox = Path(inbox or (config.V3_ROOT / "finance-inbox" / "upwork"))
    if not inbox.is_dir():
        return [Check("SKIP", "upwork mcp twins", f"no CSV exports at {inbox}")]
    try:
        from modules.finance import ledger as _ledger
        by_ref, _recon = _ledger.load_fullhistory(inbox)
    except Exception as exc:  # noqa: BLE001
        return [Check("WARN", "upwork mcp twins", f"could not read the CSV exports: {exc!r}")]
    ids = [int(r) for r in seen if str(r).isdigit()]
    if not ids:
        return [Check("SKIP", "upwork mcp twins", "no numeric record ids read yet")]
    if not floor_day:           # a rail from before the cursor: its first sight minus the window
        first = min(r["first_seen_at"] for r in seen.values())[:10]
        floor_day = (_dt.date.fromisoformat(first) - _dt.timedelta(days=14)).isoformat()
    in_view = [int(r) for r, row in seen.items()
               if str(r).isdigit() and (row.get("invoice_dt") or "")[:10] >= floor_day]
    floor_id = min(in_view) if in_view else min(ids)
    if isinstance(now, _dt.datetime):
        today = now.date()
    elif isinstance(now, _dt.date):
        today = now
    else:                       # the IST calendar day — the CSV's dates are his days
        today = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5, minutes=30))).date()
    this_monday = today - _dt.timedelta(days=today.weekday())
    weeks = [(this_monday - _dt.timedelta(days=14), this_monday - _dt.timedelta(days=8)),
             (this_monday - _dt.timedelta(days=7), this_monday - _dt.timedelta(days=1)),
             (this_monday, today)]
    since = weeks[0][0].isoformat()
    covered, older, missing, mismatch = 0, 0, [], []
    week_bad = [False, False, False]
    for ref, row in by_ref.items():
        day = row.get("date") or ""
        if not day or day < since or day > today.isoformat():
            continue
        if not str(ref).isdigit() or int(ref) < floor_id:
            older += 1
            continue
        covered += 1
        wk = next((i for i, (a, b) in enumerate(weeks) if a.isoformat() <= day <= b.isoformat()), 2)
        twin = seen.get(str(ref))
        if twin is None:
            missing.append(ref)
            week_bad[wk] = True
        elif twin["amount_minor"] is not None and twin["amount_minor"] != row.get("amount_minor"):
            mismatch.append(f"{ref} csv {row.get('amount_minor')} mcp {twin['amount_minor']}")
            week_bad[wk] = True
    clean = sum(1 for i in (0, 1) if not week_bad[i])
    # a completed payment-week counts as IN VIEW only when every invoice it
    # could hold was invoiced on or after the rail's floor (hourly pay lags
    # the invoice by ~10 days; 14 is the margin) — otherwise a young rail
    # would read 2/2 on its first morning
    in_view_weeks = sum(1 for i in (0, 1)
                        if (weeks[i][0] - _dt.timedelta(days=14)).isoformat() >= floor_day)
    ready_on = (_dt.date.fromisoformat(floor_day) + _dt.timedelta(days=28))
    ready_on = ready_on + _dt.timedelta(days=(7 - ready_on.weekday()) % 7)   # the Monday after
    older_note = f"; {older} older CSV rows predate the rail" if older else ""
    if missing or mismatch:
        parts = []
        if missing:
            parts.append(f"{len(missing)} of {covered} CSV rows since {since} have no MCP twin"
                         f" ({', '.join(missing[:6])}{'…' if len(missing) > 6 else ''})")
        if mismatch:
            parts.append(f"{len(mismatch)} amount mismatch(es): {'; '.join(mismatch[:4])}")
        return [Check("WARN", "upwork mcp twins",
                      "; ".join(parts) + f"; clean completed weeks {clean}/2{older_note}")]
    if clean == 2 and in_view_weeks == 2:
        verdict = "the CSV import can go audit-only"
    elif in_view_weeks < 2:
        verdict = (f"the rail has read invoices since {floor_day}, {in_view_weeks} of the two"
                   f" completed weeks fully in view — keep the CSV import until {ready_on}")
    else:
        verdict = "keep the CSV import until two completed weeks are clean"
    return [Check("PASS", "upwork mcp twins",
                  f"{covered} CSV rows since {since} all have MCP twins with matching amounts;"
                  f" clean completed weeks {clean}/2 — {verdict}{older_note}")]


def check_upwork_weekly_tieout(db_path: Optional[Path] = None) -> list:
    """Upwork's own weekly summary (upwork_mcp_summaries, migration 031: per
    org and completed week, billed / fee / tax by invoice date) against what
    the MCP rail read for the same days (upwork_mcp_rows), exact in minor
    units — use case A step 3, the continuous replacement for the CSV's
    running-balance check (Upwork chat 2026-08-26). One line per stored
    period. A week that starts before the rail's earliest window floor is
    SKIPPED (the rail could not have read all of it); a week Upwork reports
    no earnings data for is SKIPPED with its note. WARN on a gap, never FAIL
    — a human reads which client and which of billed / fee / tax is off."""
    from modules.upwork.feeds import mcp_financials as mf
    db_path = db_path or config.DB_PATH
    if not Path(db_path).is_file():
        return [Check("SKIP", "upwork weekly tie-out", "no database yet")]
    try:
        with db.connect(db_path) as conn:
            have = conn.execute("SELECT name FROM sqlite_master WHERE type='table'"
                                " AND name='upwork_mcp_summaries'").fetchone()
            if not have:
                return [Check("SKIP", "upwork weekly tie-out", "migration 031 not applied yet")]
            floor_row = conn.execute("SELECT value FROM sync_cursors WHERE name=?",
                                     (mf.FLOOR_CURSOR,)).fetchone()
            floor_day = floor_row["value"] if floor_row else None
            periods = [dict(r) for r in conn.execute(
                "SELECT profile, period_start, period_end, billed_minor, fee_minor, tax_minor,"
                " earnings_available, payload_json FROM upwork_mcp_summaries"
                " ORDER BY period_start, profile")]
            if not periods:
                return [Check("SKIP", "upwork weekly tie-out", "no weekly summary stored yet")]
            checks = []
            for p in periods:
                name = f"upwork tie-out {p['profile']} {p['period_start']}"
                if not p["earnings_available"]:
                    note = ""
                    try:
                        note = (json.loads(p["payload_json"] or "{}").get("note") or "")[:120]
                    except ValueError:
                        pass
                    checks.append(Check("SKIP", name, f"Upwork reported no earnings data: {note}"))
                    continue
                if floor_day and p["period_start"] < floor_day:
                    checks.append(Check("SKIP", name,
                                        f"starts before the rail's floor {floor_day} — not all in view"))
                    continue
                got = mf.rows_summary(conn, p["profile"], p["period_start"], p["period_end"])
                gaps = [f"{k} summary {p[k + '_minor']} rail {got[k]}" for k in ("billed", "fee", "tax")
                        if (p[k + "_minor"] or 0) != got[k]]
                if gaps:
                    per_client = []
                    try:
                        want = {c.get("client"): c for c in
                                (json.loads(p["payload_json"] or "{}").get("by_client") or [])}
                    except ValueError:
                        want = {}
                    for client in sorted(set(want) | set(got["by_client"])):
                        w = want.get(client) or {}
                        g = got["by_client"].get(client) or {"billed": 0, "fee": 0, "tax": 0}
                        for k in ("billed", "fee", "tax"):
                            wm = mf._minor(w.get(k)) or 0
                            if wm != g[k]:
                                per_client.append(f"{client} {k} {wm} vs {g[k]}")
                    checks.append(Check("WARN", name,
                                        f"{p['period_start']}..{p['period_end']}: " + "; ".join(gaps)
                                        + (f" — {'; '.join(per_client[:6])}" if per_client else "")))
                else:
                    checks.append(Check("PASS", name,
                                        f"{p['period_start']}..{p['period_end']}: billed {p['billed_minor']},"
                                        f" fee {p['fee_minor']}, tax {p['tax_minor']} — the rail read"
                                        f" every row Upwork adds up"))
            return checks
    except Exception as exc:  # noqa: BLE001 — a decision aid must never crash doctor
        return [Check("WARN", "upwork weekly tie-out", f"check failed: {exc!r}")]


def check_cockpit() -> list:
    """Cockpit auth/bind hygiene (M2): token present, token file owner-only,
    server bound to loopback. No network."""
    checks = []
    tok = config.cockpit_token()
    checks.append(Check("PASS" if tok else "WARN", "cockpit token",
                        "present" if tok
                        else "not minted yet (server mints on first start)"))
    tf = config.COCKPIT_TOKEN_FILE
    if Path(tf).is_file() and Path(tf).stat().st_mode & 0o077:
        checks.append(Check("WARN", "cockpit token file",
                            f"{tf} readable beyond owner — chmod 600"))
    elif Path(tf).is_file():
        checks.append(Check("PASS", "cockpit token file", "mode 600"))
    host = getattr(config, "COCKPIT_HOST", "127.0.0.1")
    checks.append(Check("PASS" if host == "127.0.0.1" else "FAIL",
                        "cockpit bind",
                        f"{host}:{config.COCKPIT_PORT}"
                        + (" (loopback)" if host == "127.0.0.1"
                           else " — NOT loopback, exposed!")))
    return checks


ALL_CHECKS = (
    ("python + sdk", check_python),
    ("config", check_config_drift),
    ("credentials", check_credentials),
    ("secret scan", check_secret_scan),
    ("database", check_db),
    ("pricing", check_pricing_coverage),
    ("guardrails", check_guardrails_selftest),
    ("hooks", check_hooks),
    ("calendar", check_calendar),
    ("source freshness", check_source_freshness),
    ("upwork ledger", check_upwork_mcp_twins),
    ("upwork tie-out", check_upwork_weekly_tieout),
    ("models api", check_models_api),
    ("plists", check_plists),
    ("cockpit", check_cockpit),
)
