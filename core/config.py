"""Etherwise OS v3 — central configuration.

THE single source of truth for: model strings, billing routing, budget ceilings,
external-system IDs, schedules, and paths. No secrets in this file — secrets load
from the shared credentials dir (v2-owned until PL-10 migration).

Integrity: guardrails verify CONFIG_SHA256 of this file at task start (decision:
agent plane has no write path here; changes arrive only as reviewed git commits).
"""
from __future__ import annotations

import hashlib
import os
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Paths ────────────────────────────────────────────────────────────────────
V3_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = V3_ROOT.parent                      # ~/Desktop/Etherwise
V2_ROOT = WORKSPACE_ROOT / "etherwise-os"            # live v2 (until cutover)

VAR_DIR = V3_ROOT / "var"
DB_PATH = VAR_DIR / "etherwise.db"
LOG_DIR = VAR_DIR / "logs"
BACKUP_DIR = VAR_DIR / "backups"
LOCK_DIR = VAR_DIR / "locks"   # flock single-instance locks (runner)
KNOWLEDGE_DIR = V3_ROOT / "knowledge"
KNOWLEDGE_INBOX = V3_ROOT / "knowledge-inbox"

# Shared with v2 until PL-10 (secrets migration, week 2).
# v2 OWNS Upwork token refresh until M1 cutover — v3 reads upwork-api.json
# READ-ONLY and never refreshes (pre-flight §9, token-race rule).
CREDENTIALS_DIR = Path(os.environ.get("ETHERWISE_CREDENTIALS", V2_ROOT / ".credentials"))
V2_DB_PATH = V2_ROOT / "etherwise.db"                # import + shadow-diff source

# ── Models (pinned snapshots — see claude-platform-research-2026-06.md §1) ───
# Dateless IDs are FIXED since the 4.6 generation; Haiku pinned by date.
# Deprecation watch: Haiku 4.5 → review by 2026-08-15 (retires not before Oct 15).
MODELS = {
    "scoring":   "claude-sonnet-4-6",            # LOCKED (M1 design, 2026-06-12): Sonnet regardless of eval — revenue-engine quality over savings; eval is calibration-only
    "classify":  "claude-haiku-4-5-20251001",    # thread/commitment/note classification
    "drafting":  "claude-sonnet-4-6",            # proposals, follow-ups, briefs
    "reasoning": "claude-opus-4-8",              # CoS sweeps (on fire), weekly retro
    "architect": "claude-fable-5",               # interactive sessions only; never scheduled
}
MODEL_FALLBACKS = {                               # runner failover chains (OpenClaw pattern)
    "claude-opus-4-8": ["claude-sonnet-4-6"],
    "claude-sonnet-4-6": ["claude-haiku-4-5-20251001"],
}
FORBIDDEN_SAMPLING_PARAMS = ("temperature", "top_p", "top_k")   # 4.7+ reject these
STRUCTURED_OUTPUT_UNSUPPORTED = ("claude-fable-5", "claude-mythos-5")

# ── Billing (decision #3 + pre-flight timeline) ──────────────────────────────
# "credit"  = subscription-authenticated Agent SDK / claude -p  (claimable from 2026-06-15)
# "payg"    = ANTHROPIC_API_KEY on the Messages API (uncapped fallback)
BILLING_DEFAULT_ROUTE = os.environ.get("ETHERWISE_BILLING", "payg")  # flip to "credit" on June 15
BILLING_FALLBACK_TO_PAYG = True                   # on credit exhaustion / auth failure

# ── Budget ceilings ──────────────────────────────────────────────────────────
DAILY_SOFT_LIMIT_USD = 1.50                       # warning
DAILY_HARD_LIMIT_USD = 5.00                       # ClaudeBudgetExceeded
PER_RUN_MAX_USD = 0.50                            # single invocation kill-switch
PER_RUN_MAX_TOOL_CALLS = 40                       # NemoClaw lesson: die in-run, not at daily cap
PER_RUN_MAX_OUTPUT_TOKENS = 8_000

# ── Claude API (gateway) ─────────────────────────────────────────────────────
# $/MTok: (input, output, cache_read, cache_write_1h)
# Source: claude-platform-research-2026-06 §1 + claude-api skill (verified 2026-06).
# Cache write 1h = 2x input.
PRICING = {
    "claude-fable-5":            (10.0, 50.0, 1.00, 20.0),
    "claude-opus-4-8":           ( 5.0, 25.0, 0.50, 10.0),
    "claude-sonnet-4-6":         ( 3.0, 15.0, 0.30,  6.0),
    "claude-haiku-4-5-20251001": ( 1.0,  5.0, 0.10,  2.0),
}
CACHE_TTL = "1h"              # prompt-cache TTL (architecture §4)
CLAUDE_TIMEOUT_SECONDS = 120  # per-call HTTP timeout
# SDK auto-retries OFF in gateway — runner.py owns retry/backoff/failover.

# ── Runner (job wrapper) ─────────────────────────────────────────────────────
RUNNER_MAX_ATTEMPTS = 3           # per model in failover chain
RUNNER_BACKOFF_BASE_SECONDS = 30  # 30s -> 60s between retries
RUNNER_STAGGER_MAX_SECONDS = 120  # hash(task) % N sleep at top-of-hour

# ── Deprecation + platform calendar (doctor watches these) ───────────────────
DEPRECATION_WATCH = {
    "claude-haiku-4-5-20251001":
        ("2026-08-15", "dated snapshot retires not before 2026-10-15"
         " — review pin"),
}
CALENDAR_WATCH = (
    ("2026-06-15", "Claim Agent SDK credit (one-time opt-in, Max account);"
     " flip ETHERWISE_BILLING=credit"),
    ("2026-06-22", "Fable-included window ends — architect sessions move to"
     " Opus 4.8 or usage credits"),
)

# ── Shadow mode (pre-flight §9) ──────────────────────────────────────────────
# True ⇒ external writes (Airtable / ClickUp / email / Upwork drafts) are
# suppressed and recorded to shadow_ledger for diffing. Flipped OFF per-module
# at its cutover, via this map — an explicit, reviewed config change.
SHADOW_MODE = {
    "upwork": True,
    "cockpit": False,          # cockpit is local-only; nothing to shadow
    "knowledge": True,
    "chief_of_staff": True,
    "finance": True,
}
SCHEDULE_OFFSET_MIN = 15       # v3 jobs run +15min vs v2 counterparts during parallel

# ── Time & cadence (decisions #10, #11) ──────────────────────────────────────
TZ = ZoneInfo("Asia/Kolkata")
WORK_START = time(12, 0)       # 12 PM IST
WORK_END = time(4, 0)          # 4 AM IST (next day)
BRIEF_TIMES = (time(11, 0), time(20, 0))          # morning + evening
SWEEP_INTERVAL_HOURS = 2       # within work hours; empty-skip short-circuit
INTERRUPT_POLICY = ("hot_lead", "system_failure") # ONLY these email immediately (decision #9)
HOT_LEAD_THRESHOLD = 16
LOOM_FLAG_THRESHOLD = 22

# ── Airtable (decision #5: extend base appgE1QoEOXvbrUE4) ────────────────────
AIRTABLE_BASE = "appgE1QoEOXvbrUE4"
AT = {
    "proposals":      "tbloRHzMBbryPr090",
    "contracts":      "tblkvAaajHAJxftKF",
    "transactions":   "tblW515kregBO9WCE",
    "clients":        "tbl9hhPhjmLyiLgj4",
    "people":         "tbly8rJyCZ3z0k1p7",
    "communications": "tblCwxCS6fJMhFiUC",
    "active_clients": "tblOJIsZ8tojD9F3l",   # merges INTO clients (decision #8) — kept for import
    "leads_pipeline": "tblBJQFU5kANIMJGZ",
    "weekly_metrics": "tbllt8szMWsXViw9t",
    "testimonials":   "tblV85CBqWB9lvRfx",
    "roi_snapshots":  "tblF7PmsokukIUJOy",
    "employees":      "tblPBSY9dZCtNfiYy",
    # Created at module sessions (decision #6): nudges, commitments, run_health, eval_results
    "nudges": None, "commitments": None, "run_health": None, "eval_results": None,
}
AIRTABLE_TYPECAST = True       # always — hook-enforced
AIRTABLE_BATCH_SIZE = 10

# Other bases (read/secondary)
AT_BASE_PAYROLL = "appwEb67uNMlDRdDQ"
AT_BASE_HR = "appii28erPgIAhnHq"
AT_BASE_FINANCES = "appEe3oqHhC7ABAXU"

# ── ClickUp ──────────────────────────────────────────────────────────────────
CLICKUP_FOLDER_UPWORK = "90169179275"
CLICKUP_LISTS = {
    "hot": "901614356485",
    "standard": "901614356486",
    "low": "901614356487",
    "invites": "901614356490",
}

# ── Upwork ───────────────────────────────────────────────────────────────────
UPWORK_USER_ID = "1647672547786633216"
UPWORK_ORGS = {
    "personal": "1647672547786633217",
    "client":   "1715797263477194752",
    "agency":   "1910033523997712724",
}
ACE_IDS = {"personal": "70275816", "agency": "121356955"}
PROFILE_RATE_USD = 32.50
HARD_FLOOR_HOURLY = 30.0       # v4.9: NO exceptions
HARD_FLOOR_FIXED = 500.0       # v4.9: NO exceptions

# ── Email ────────────────────────────────────────────────────────────────────
NOTIFY_EMAIL = "contact@etherwise.io"
SMTP_FROM = "contact@etherwise.io"

# ── Integrity ────────────────────────────────────────────────────────────────
def config_sha256() -> str:
    """Hash of this file's bytes — verified by guardrails at task start."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def ensure_dirs() -> None:
    for d in (VAR_DIR, LOG_DIR, BACKUP_DIR, LOCK_DIR, KNOWLEDGE_INBOX / "fathom", KNOWLEDGE_INBOX / "loom"):
        d.mkdir(parents=True, exist_ok=True)
