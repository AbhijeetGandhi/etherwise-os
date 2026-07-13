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
MONTHLY_SOFT_LIMIT_USD = 80.00                    # month-to-date WARN (Abhijeet's ~$100/mo target, 2026-07-01); non-blocking anomaly, not a hard cap
PER_RUN_MAX_USD = 0.50                            # single invocation kill-switch
PER_RUN_MAX_TOOL_CALLS = 40                       # NemoClaw lesson: die in-run, not at daily cap
PER_RUN_MAX_OUTPUT_TOKENS = 8_000
# Budget-contention guard (2026-06-22): the revenue-engine Upwork pipeline gets
# the full daily cap; all other tasks (backfills, CoS, KB) are blocked one
# reserve early, so a heavy backfill can't starve the scan again (June-18).
BUDGET_RESERVE_USD = 1.00
BUDGET_PRIORITY_PREFIXES = ("upwork_", "scan_jobs", "poll_inbox")

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
    # FLIPPED LIVE 2026-07-06 (scanner cutover — Abhijeet's go after parity
    # verify; Cowork upwork-job-scanner stands down in the same window).
    # Live surface: scan->ClickUp cards + hot-lead digest email. poll_inbox /
    # outcome_capture emails remain UNWIRED no-ops (v2 morning-briefing still
    # covers them). Rollback = True + re-enable the Cowork scanner.
    "upwork": False,
    "cockpit": False,          # cockpit is local-only; nothing to shadow
    "knowledge": True,
    "chief_of_staff": True,
    "finance": True,
    # CRM Airtable mirror gates (read by modules.crm.mirror). DECOUPLED from
    # 'upwork' (2026-07-02) so the mirror can go live WITHOUT lighting up the
    # scanner's ClickUp path (which stays shadowed under 'upwork' until the
    # separate scanner cutover). 'crm' -> Proposals+Contracts; 'crm_transactions'
    # -> Transactions (also decoupled from 'finance': the mirror is a strict
    # one-way copy-out — never reads Airtable back, never moves money — so the CRM
    # copy goes live while the finance ledger/doctor/reports stay shadowed).
    # FLIPPED LIVE 2026-07-03 (Abhijeet's go, shadow run 1027 verified clean).
    # Rollback = set both back to True.
    "crm": False,
    "crm_transactions": False,
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
# ── ICP / Prime (Abhijeet's ideal-client profile, 2026-07-13) ────────────────
# A TOP tier above 'hot' — 16+ had become a rubber stamp (81% of scored jobs).
# Deterministic, code-side (see modules/upwork/icp.py).
ICP_HOURLY_USD = 51.0            # $51/hr must be in the budget range
ICP_FIXED_USD = 5000.0           # fixed-price equivalent
ICP_CLIENT_SPEND_USD = 50000.0   # proven spender
ICP_SPEND_PER_HIRE_USD = 2000.0  # readiness-to-pay proxy (avg-rate enrichment pending)

# ── Chief of Staff (M4) — nudge scoring + decay (design spec §6) ──────────────
# urgency = COS_URGENCY_BASE[kind] * recency_decay(age) * stake_mult(money|score)
# Tunable here ONLY (single source of truth). Higher base = ranks higher.
COS_URGENCY_BASE = {
    "commitment_due":  100,   # an open promise; overdue escalates further
    "hotlead_no_loom":  80,   # high-score lead with no custom Loom yet
    "deadline":         70,   # dated event within window (payroll/renewal — M5+)
    "owed_reply":       60,   # owed Upwork reply past the anti-spam window
    "escalate_client":  55,   # Opus cross-signal: quiet client + overdue commitment
    "lost_pattern":     50,   # 3+ losses in one category/tool -> scoring nudge
    "won_no_outcome":   40,   # won proposal, outcome reason not captured
    "system":           35,   # job failed / spend near cap / model deprecation
    "stale_dossier":    20,   # dossier not refreshed in a while
    "capture":          30,   # a captured note awaiting classification/action
}
COS_RECENCY_HALFLIFE_DAYS = 7.0   # urgency decays by half every 7 days of age
# decay TTL in days per kind: None = never (until done); 0 = clears past its due
COS_DECAY_TTL_DAYS = {
    "owed_reply":       7,
    "hotlead_no_loom":  3,
    "deadline":         0,        # auto-clears once past `due`
    "commitment_due":   None,     # never decays until the commitment is done
    "lost_pattern":     14,
    "won_no_outcome":   14,
    "stale_dossier":    30,
    "system":           7,
    "escalate_client":  7,
    "capture":          14,
}
COS_BRIEF_TOPN = 3                 # brief leads with the top-N nudges by urgency
COS_BRIEFS_DIR = V3_ROOT / "var" / "briefs"   # shadow draft artifacts (no send)
# Sweep rule thresholds (Abhijeet, 2026-06-22)
COS_FOLLOWUP_IDLE_DAYS = 1         # owed_reply: nudge once a thread sits idle >= N days
COS_LOST_STATUSES = ("Lost", "Expired", "Withdrawn")  # what counts as a loss
COS_LOST_PATTERN_MIN = 3           # 3+ losses in one category -> scoring nudge
COS_COMMITMENT_DUE_DAYS = 2        # commitment within N days (or overdue) escalates
COS_SPEND_WARN_FRACTION = 0.8      # system nudge when today's spend >= 80% of cap
COS_SYSTEM_FAIL_WINDOW_H = 24      # failed runs within N hours surface as a nudge
COS_HOTLEAD_RECENT_DAYS = 7        # only nudge Looms for leads scored in last N days
COS_WON_OUTCOME_RECENT_DAYS = 30   # only chase outcome reasons for recent wins
COS_SWEEP_OPUS_MAXITEMS = 30       # cap nudges sent to Opus (top by urgency) + a
                                   # by-kind summary of the rest -> bounded cost
COS_QUEUE_GROUP_MIN = 5            # cockpit queue collapses a kind into ONE
                                   # expandable group once it exceeds N items

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
# Category-priority (whale) gate exemptions (v2.3 policy — these mirror the
# scoring prompt and are now CODE-ENFORCED: any one lifts the gate. The 2026-06-22
# shadow audit found the model applies these numeric thresholds unreliably,
# capping 9 leads with raw 16-31 to 15 -> lead loss. Code now overrides.
GATE_HOURLY_EXEMPT_USD = 45.0
GATE_FIXED_EXEMPT_USD = 3000.0
GATE_WHALE_SPEND_USD = 50000.0

# ── Email ────────────────────────────────────────────────────────────────────
NOTIFY_EMAIL = "contact@etherwise.io"
SMTP_FROM = "contact@etherwise.io"

# ── Cockpit (M2) ──────────────────────────────────────────────────────────────
COCKPIT_HOST = "127.0.0.1"      # localhost only; never 0.0.0.0
COCKPIT_PORT = 5051            # v2 dashboard holds :5050 (live)
COCKPIT_TOKEN_FILE = CREDENTIALS_DIR / "cockpit-token.txt"


def cockpit_token() -> str | None:
    try:
        return COCKPIT_TOKEN_FILE.read_text().strip() or None
    except OSError:
        return None


# ── Finance targets (cockpit Money) ───────────────────────────────────────────
# year-end goal $40K, floor $35K (2026-06); may become a monthly ramp at M5.
MONTHLY_REVENUE_TARGET_USD = 35000.0

# ── Integrity ────────────────────────────────────────────────────────────────
def config_sha256() -> str:
    """Hash of this file's bytes — verified by guardrails at task start."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def ensure_dirs() -> None:
    for d in (VAR_DIR, LOG_DIR, BACKUP_DIR, LOCK_DIR, KNOWLEDGE_INBOX / "fathom", KNOWLEDGE_INBOX / "loom"):
        d.mkdir(parents=True, exist_ok=True)
