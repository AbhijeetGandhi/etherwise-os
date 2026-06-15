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
        checks.append(Check("PASS", "migrations", f"{n} applied"))
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
    ("models api", check_models_api),
    ("plists", check_plists),
    ("cockpit", check_cockpit),
)
