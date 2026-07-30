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
    # 'system' = local operational tasks (bin/catch-up). LIVE because it makes
    # ZERO external writes — it only restarts local launchd agents. Each job it
    # restarts still enforces its OWN module's shadow gate, so catch-up can
    # never be a way around the shadow protocol.
    "system": False,
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
# Founder INTENT, carried as config so the system respects it instead of reading
# neglect into deliberate choice: applications are paused since 2026-06-30. While
# set, the Today screen shows accumulated hot leads as a 'ready when you resume'
# backlog (ranked by ICP/score), never as an act-now queue that nags every
# morning. Set to None to resume — hot leads then re-enter the act-now queue.
APPLICATIONS_PAUSED_SINCE = "2026-06-30"
TODAY_BACKLOG_TOPN = 8            # how many ready-when-resumed leads to preview
TODAY_FRESH_LEAD_DAYS = 10       # a lead older than this has almost surely expired
TODAY_QUEUE_TOPN = 12            # the act-now queue is ruthless; the rest -> Nudges

# ── Client revenue attribution (2026-07-27) ──────────────────────────────────
# Revenue reaches us on two rails: Upwork (transactions.assignment_company is
# Upwork's OWN client label) and Wise (the payer is parseable from the
# 'Received money from X with reference ...' description). The same client can
# use both (Adelye pays via Upwork AND Wise), so a per-client total must merge
# them. CANONICAL merges names that DEMONSTRABLY refer to one entity; anything
# not listed keeps its raw ledger name (honest — never guess a merge). Keys are
# lowercased; the parser strips a trailing company suffix (Ltd/LLC/Inc/B.V.).
# Canonical name is the DOSSIER client name where one exists, so Clients and
# Knowledge reconcile. Keys are lowercased AND accent-stripped (data.py compares
# on the deaccented form), so 'Adélye' in the ledger matches the plain key here.
CLIENT_CANONICAL = {
    "adelye ltd": "Adelye", "adelye": "Adelye",         # -> dossier: adelye
    "horizons aba midco": "Horizons ABA",               # -> dossier: horizons-aba
    "horizons aba": "Horizons ABA",
    # same client across two eras: Upwork as 'Alexander Apfel' first, now Wise as
    # Horizons ABA (Abhijeet, 2026-07-30)
    "alexander apfel": "Horizons ABA",
    # Remotual pays through Deel (EOR); Dilip Malave is the person there
    # (Abhijeet, 2026-07-27) — three ledger names, one client.
    "deel": "Remotual",
    "dilip n malave": "Remotual",
    "dilip malave": "Remotual",
    "remotual": "Remotual",
    # Ioannis Pirigkos == Beglaubigt (Abhijeet, 2026-07-30): Upwork names the
    # person across two truncations; the company is Beglaubigt. Unifies the
    # Upwork-API 'Ioannis P' row with the CSV rows linked via contract title.
    "ioannis pirigkos, ioannis p": "Beglaubigt",
    "ioannis pirigkos": "Beglaubigt",
    "ioannis p": "Beglaubigt",
    # Ledger-identity merge (Abhijeet, 2026-07-30): same client across two names,
    # no dossier yet — canonical is the company name (not the person). This is an
    # identity fact, distinct from a dossier link.
    "mike gandia": "CMF Publishing",         # Upwork person == Wise 'CMF Publishing LLC'
    "cmf publishing llc": "CMF Publishing",
    "cmf publishing": "CMF Publishing",
    # NOT merged on purpose (Abhijeet, 2026-07-27): real client, NO dossier, and
    # we do not invent one — stays named-but-unlinked until a dossier exists:
    #   'Consulting Systems B.V.' -> Hermon/Abel  (no hermon dossier)
}
# Wise payers that are PAYMENT PROCESSORS / EORs whose end client is NOT yet
# supplied — shown under their own name with an 'attribution pending' flag until
# mapped. (Deel is resolved above -> Remotual, so it is no longer here.)
CLIENT_PASSTHROUGH_PAYERS = ()
# ── ICP / Prime (Abhijeet's ideal-client profile, 2026-07-13) ────────────────
# A TOP tier above 'hot' — 16+ had become a rubber stamp (81% of scored jobs).
# Deterministic, code-side (see modules/upwork/icp.py).
ICP_HOURLY_USD = 51.0            # $51/hr must be in the budget range
ICP_FIXED_USD = 5000.0           # fixed-price equivalent
ICP_CLIENT_SPEND_USD = 50000.0   # proven spender
ICP_SPEND_PER_HIRE_USD = 2000.0  # readiness-to-pay proxy (avg-rate enrichment pending)

# ── Rate-unstated hourly gate (2026-07-16, option B) ─────────────────────────
# Upwork ships a blank hourly rate as $0, and the $30 floor read $0 as 'cheap',
# silently killing ~370 jobs (2/3 of that skip bucket) that never stated a rate
# at all. Rate-unstated is NOT low-value — but we don't score all of them either.
# When the rate is absent, the CLIENT'S proven spend stands in for it: a real
# spender who left the rate blank is worth scoring; an unproven one is not.
RATE_UNSTATED_MIN_CLIENT_SPEND_USD = 5000.0   # proven-spender proxy for a missing rate
RATE_UNSTATED_MIN_HIRES = 1                    # ...or payment-verified with a hire history

# ── Source-health registry (2026-07-16) ──────────────────────────────────────
# Every number-backing source self-reports freshness + health so a source that
# silently degrades (a dead rail, a scanner extracting 2 tiles/feed) can never
# render stale/thin numbers as if they were current. core/source_health.py reads
# this; bin/doctor and the cockpit both consume the verdict. Adding a source is
# ONE entry here, not new code.
#   task_name     — the runs.task_name it writes (freshness = age of last success)
#   max_age_hours — cadence + grace; older than this => STALE
#   degrade_kinds — anomaly kinds that mean 'ran but produced thin/bad output';
#                   an unresolved one on the LATEST run => DEGRADED (self-healing)
#   critical      — a stale/degraded critical source turns doctor amber
#   backs         — the cockpit figure it feeds (so that KPI can show 'not current')
#   launchd_label — the agent bin/catch-up may kickstart when this source is
#                   overdue. Omit it (or set catch_up False) and the source is
#                   REPORTED but never auto-restarted.
#   catch_up      — safe to re-run unattended after a missed window? False for
#                   anything with an outward side-effect that must not repeat
#                   (the brief emails), and for manually-fed sources.
SOURCES = (
    {"key": "connects_rail", "label": "Connects-history rail",
     "task_name": "upwork_connects", "max_age_hours": 26,
     # an empty window is degraded-not-failed: genuinely no activity looks
     # identical to a throttled endpoint, and failing would spam criticals
     "degrade_kinds": ("connects_empty_window",),
     "critical": True, "backs": "applied",
     "launchd_label": "io.etherwise.v3.upwork-connects", "catch_up": True,
     "why": "the applied-truth source; a stale rail makes 'applied' look like a"
            " live 0 when nobody is actually counting"},
    {"key": "scanner", "label": "Upwork scanner",
     "task_name": "upwork_scan", "max_age_hours": 3,
     "degrade_kinds": ("scan_feed_degraded",), "critical": True,
     "backs": "pipeline",
     "launchd_label": "io.etherwise.v3.upwork-scan", "catch_up": True,
     "why": "backs the funnel + carded board; a thin feed silently under-scans"
            " lead-gen"},
    # ── money-backing sources (2026-07-20) ───────────────────────────────────
    # A dead sync silently staling the Money tab is the same failure class as
    # the dead connects rail, so the Money numbers get the same guarantee.
    {"key": "upwork_sync", "label": "Upwork sync",
     "task_name": "upwork_sync", "max_age_hours": 3,     # hourly at :45
     "degrade_kinds": (), "critical": True, "backs": "money",
     "launchd_label": "io.etherwise.v3.upwork-sync", "catch_up": True,
     "why": "feeds proposals, contracts and Upwork transactions — the Money"
            " tab's revenue side and the pipeline's thread state"},
    {"key": "crm_mirror", "label": "CRM mirror (Airtable)",
     "task_name": "crm_mirror", "max_age_hours": 3,      # hourly at :50
     "degrade_kinds": (), "critical": True, "backs": "crm",
     "launchd_label": "io.etherwise.v3.crm-mirror", "catch_up": True,
     "why": "one-way copy-out to the Airtable CRM; when it stops, Airtable"
            " quietly drifts from the v3 truth"},
    {"key": "finance_upwork_weekly", "label": "Upwork finance pull (weekly)",
     "task_name": "finance_upwork_weekly", "max_age_hours": 192,  # 8d = 7 + grace
     "degrade_kinds": (), "critical": True, "backs": "money",
     "launchd_label": "io.etherwise.v3.finance-upwork-weekly", "catch_up": True,
     "why": "the weekly Upwork earnings/fee pull behind the P&L"},
    # Manually fed: statement imports have no scheduled run to age, but the DATA
    # still has an age — freshness_sql asks the ledger when it was last fed.
    {"key": "finance_ledger", "label": "Statement imports (bank/card/Wise)",
     "task_name": "finance_import", "max_age_hours": 1080,   # 45d: monthly + grace
     "degrade_kinds": (), "critical": True, "backs": "money",
     "freshness_sql": "SELECT MAX(first_seen_at) FROM transactions"
                      " WHERE source IN ('IDFC','Bank','Card','Wise')",
     "catch_up": False,          # a human has to drop the statement in
     "why": "the manual bank/card/Wise imports behind cash + operating expenses;"
            " an unfed ledger makes the P&L look complete when it is months"
            " behind"},
)

# Catch-up shim (bin/catch-up, 2026-07-20). macOS skips StartCalendarInterval
# windows that pass while the Mac is asleep/off — it does NOT queue them — so a
# machine that sleeps loses whole runs. Blanket RunAtLoad is the wrong cure: it
# fires EVERY job on every load (a thundering herd at boot, and a spurious run
# each time a plist is reloaded). Instead the shim asks the source registry what
# is ACTUALLY overdue and restarts only that — which turns the freshness
# mechanism into a self-healing one.
CATCH_UP_COOLDOWN_MIN = 20     # never re-kick a task that ran this recently
CATCH_UP_STAGGER_SEC = 5       # spread kickstarts; several rails share Chrome
# A job that fails every attempt is BROKEN, not merely missed — restarting it on
# a loop just spams critical anomalies and hides the real problem. After this
# many consecutive failures catch-up gives up and lets the brief carry it to a
# human. (Found the hard way: the connects rail fails whenever its shared Chrome
# tab is parked hidden, so it would have been re-kicked every 30 min forever.)
CATCH_UP_MAX_CONSECUTIVE_FAILURES = 3

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
# ONE-SHOT kinds: 'done'/'dismissed' is terminal — never resurrect on the next
# sweep. A commitment, once fulfilled, does not recur, and (unlike owed_reply,
# whose 'condition recurred' refire is intentional) its rule does not self-clear,
# so without this a done commitment was re-created every hour and flooded Today
# with months-old items (2026-07-30). Recurring kinds (owed_reply, system,
# stale_dossier, escalate_client) are deliberately absent — they SHOULD refire
# while their condition holds.
COS_DONE_TERMINAL_KINDS = {"commitment_due", "won_no_outcome",
                           "hotlead_no_loom", "deadline", "capture"}
# A commitment overdue by MORE than this stops nudging. Commitments are
# extracted with due dates RELATIVE to the call ('demo tomorrow' -> call+1), so
# an old call leaves a permanently-overdue item that nagged Today for months
# (2026-07-30: 54 of 79 dated commitments were >30d overdue). Past this window
# the stated deadline is meaningless — it stays in the commitments table for
# review but leaves the act-now queue. Today is for what's actionable now.
COS_COMMITMENT_STALE_DAYS = 30
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
