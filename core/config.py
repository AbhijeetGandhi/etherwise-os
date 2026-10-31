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

CONFIG_DIR = V3_ROOT / "config"                      # git-versioned, human-readable
CLIENTS_REGISTRY = CONFIG_DIR / "clients.yaml"       # the identity spine (Jarvis J1)
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
    "extract":   "claude-sonnet-4-6",            # Jarvis EXTRACT reader (§4.3 stage 3). Pins the CURRENT Sonnet; the "Sonnet 5" of architecture §5 has no verified id/pricing yet — swap this one line when it does (calendar item, ~Sept 1)
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
# RAISED 2026-08-13 (soft 1.50->4.00, hard 5.00->8.00; Abhijeet's go). WHY, so
# nobody lowers these back "to be safe": the DAILY cap was binding while the
# MONTHLY one was nowhere near. Measured on 2026-08-13 — month-to-date spend
# $13.73 of the $80 monthly ceiling (17%), yet the $1.50 soft cap tripped on
# most days and the non-priority effective cap (hard - reserve = $4.00) actually
# bit on 2026-08-10 at $3.95. Observed 7-day spend $11.44 (~$1.63/day):
# upwork_scan $4.43 · kb_extract $2.67 · jarvis_loom_classify $2.08 · everything
# else $2.26. Because BUDGET_PRIORITY_PREFIXES gives the scanner the full cap,
# the scan spends the day's allowance first and kb_extract runs into the reserve
# guard with work still queued — that is exactly how kb_daily run #5160
# (2026-08-12) ended with `"stopped": "budget"` and sources still pending, which
# is why 382 sources (374 of them Loom, oldest 2024-06-13) sat unextracted for
# two years. The DAILY numbers are a BURST ceiling for catch-up work; the real
# sustained governor is MONTHLY_SOFT_LIMIT_USD (unchanged at $80 = ~$2.58/day
# averaged), so raising the daily lets a backlog drain in one day without
# licensing $8/day forever. If you want to spend less, lower the MONTHLY cap —
# lowering the daily one only starves whichever task the scanner runs ahead of.
DAILY_SOFT_LIMIT_USD = 4.00                       # warning
DAILY_HARD_LIMIT_USD = 8.00                       # ClaudeBudgetExceeded
MONTHLY_SOFT_LIMIT_USD = 80.00                    # month-to-date WARN (Abhijeet's ~$100/mo target, 2026-07-01); non-blocking anomaly, not a hard cap. THE binding constraint now — the daily caps above are burst headroom, this is the budget.
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
    # read-only on Airtable (modules/linkedin/sync.py has no write path), so
    # there is nothing outward to shadow (2026-10-28)
    "linkedin": False,
    "knowledge": True,
    "chief_of_staff": True,
    # THE AM/PM BRIEF EMAIL, decoupled from 'chief_of_staff' the same way 'crm'
    # was decoupled from 'finance' — and for a concrete reason, not tidiness:
    # `modules/chief_of_staff/interrupts.py` reads the module flag too, and its
    # own docstring says flipping it would double-ping hot leads, which the
    # upwork module already emails. So the brief gets its own gate and the
    # module stays shadowed.
    #
    # FLIPPED LIVE 2026-10-18 on Abhijeet's "wire a v3 brief in email". The
    # trigger: v2's `com.etherwise.morning-briefing` was stopped that morning
    # for serving 119-day-old threads off the v2 DB, and the scanner note above
    # ("v2 morning-briefing still covers them") stopped being true the moment
    # it did. v3 had been writing a correct brief to var/briefs/ daily since
    # the module was built, and nobody was reading it.
    # Rollback = True: the artifact keeps being written, the email stops.
    "cos_brief_email": False,
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
# HOT BAR 16 -> 20 (Abhijeet, 2026-08-25 19:30, Upwork chat — the reviewed
# recalibration commit the block below promised). Evidence, live since the
# connects rail began 07-13: hard-rule survivors by band -> his applications:
# 22+ 732 -> 33 · 20-21 124 -> 0 · 18-19 130 -> 1 · 16-17 117 -> 1 · 8-15 394 -> 1.
# The 16-19 band was drafted, carded and digested 247 times for two applications.
# MCP drift measured at -1 median (not the assumed -2), so the feed itself moves
# nothing; 20 keeps 35 of 36 matched applications inside 'hot' and cuts the
# drafted/carded volume ~40% ahead of the MCP flip (~70-90 hot/day at 16).
# LOOM stays 22: Loom-flag band = 22+, hot band = 20-21.
# 2026-09-08, SCORER v3.0 — THE NUMBERS BELOW ARE ON A NEW SCALE. The score is
# now skill_fit(1-5) x4 + budget + client_quality + core_stack/invite bonuses
# (max 35), because skill fit was the only component that predicted whether he
# bids (7.11 on the 50 he took vs 4.62 on the 1,956 he skipped) and the other
# four were flat or backwards. The cuts are re-derived from the same history,
# re-scored arithmetically: a bar of 16 yields ~49 hot cards a week and keeps 42
# of his 49 applications; the old bar of 20 on the old scale yielded ~143/wk.
# 2026-09-09 — RE-CUT AFTER THE HALVING BUG. The cuts below were calibrated on
# 2026-09-08 against scores whose skill-fit component was being halved in code
# (scoring._raw_fit); every score has since risen 4-8 points, so a bar of 16 now
# means something completely different. Re-derived the same way, against the 63
# proposals he submitted since 1 June: a bar of 23 yields ~87 hot cards a week
# and keeps 56 of them (89%), which is the same operating point 16 was CHOSEN
# for, expressed on the corrected scale. Not a new decision — a translation.
#   bar 23: 87/wk, 89% · bar 24: 73/wk, 81% · bar 25: 60/wk, 71%
# The knee is right here; tightening past 24 costs an application for every
# three cards it saves.
# 2026-09-23 — HIS CALL, AFTER READING A WEEK OF IT: 19. The note above ended
# "23 is a translation of a number calibrated on bad data; it deserves
# confirmation from real reading." It did not survive the reading. At 23 the
# Hot list ran 6.1 cards a day and 110 jobs in ten days sat in Low/Standard
# unread, among them "Softr Airtable Make.com Platform Build" (22),
# "GoHighLevel Automation & Integration Architect Expert" (22) and a $125,000
# fixed "Claude / AI Integration Specialist" (22). Against his own 80 scored
# proposals: bar 23 hides 9, bar 19 hides 2.
#   bar 19: 12.9/day, 98% · bar 20: 10.6/day, 96% · bar 23: 6.1/day, 89%
HOT_LEAD_THRESHOLD = 19
# Loom was hot+6 and stays hot+5: ~31 cards a week, the ones worth a video.
LOOM_FLAG_THRESHOLD = 28

# ── Score bands — ONE source (Upwork chat, 2026-08-25, decision 1) ───────────
# Until this block the four cuts lived as literals in clickup_push (list
# routing + priority), scan_pipeline (card floor + digest copy), cockpit/data.py
# (funnel, bands, drills), mcp_shadow_report (the cutover scorecard) and
# core/evals/scoring_eval — so "recalibrate 16" would have moved the drafts and
# the digest but not the cards or the funnel. Every reader now imports these;
# tests/test_score_bands.py greps the readers for stray literals.
# The four cuts were single-sourced first with their values unchanged; the hot
# bar moved 16 -> 20 in a separate reviewed commit once the MCP scorecard had
# measured the drift (see HOT_LEAD_THRESHOLD above). Semantics of a stored
# (post-gate) score:
#   score <  SCORE_FLOOR                     -> no card ("below band")
#   SCORE_FLOOR    <= score < SCORE_STANDARD -> Low list
#   SCORE_STANDARD <= score < HOT            -> Standard list
#   HOT            <= score < LOOM           -> Hot list, draft at scan, digest
#   LOOM           <= score                  -> Hot + custom-Loom flag, priority 1
# Re-cut for v3.0 (see HOT_LEAD_THRESHOLD). The floor rose most: 54% of the
# cards he was being shown sat BELOW the old hot bar and produced 1 application
# in 1,475 — the Low and Standard lists were the noise he was complaining about.
# 2026-09-23 — THE LADDER IS COLLAPSED, and that is the point. With the hot bar
# at the card floor there is no gap left for a Low or Standard card to land in:
# everything worth carding is worth him reading, and the two lists that used to
# absorb the difference stop receiving new cards. That is the decision, not a
# side effect — those lists produced ONE application in 1,475 cards, and a queue
# he does not read is worse than no queue, because it looks like coverage.
# `score_band` tests loom -> hot -> standard -> floor in that order, so 'low'
# and 'standard' simply become unreachable rather than needing special cases.
# The guard below is now NON-decreasing: equal cuts are a legitimate shape
# (a collapsed band), out-of-order cuts are still a bug.
SCORE_FLOOR = 19
SCORE_STANDARD = 19
SCORE_BANDS = (SCORE_FLOOR, SCORE_STANDARD, HOT_LEAD_THRESHOLD, LOOM_FLAG_THRESHOLD)
if list(SCORE_BANDS) != sorted(SCORE_BANDS):
    raise ValueError(f"SCORE_BANDS must be non-decreasing: {SCORE_BANDS}")


def score_band(score) -> str:
    """Band name for a stored score: 'loom' | 'hot' | 'standard' | 'low' |
    'below' | 'unscored'. The one place band arithmetic lives."""
    if score is None:
        return "unscored"
    if score >= LOOM_FLAG_THRESHOLD:
        return "loom"
    if score >= HOT_LEAD_THRESHOLD:
        return "hot"
    if score >= SCORE_STANDARD:
        return "standard"
    if score >= SCORE_FLOOR:
        return "low"
    return "below"


def score_band_label(name: str) -> str:
    """Human label for a band name, derived from the live cuts ('16-21',
    '22+', '<8'...) so no report or chart hardcodes a number."""
    def span(lo, hi):
        # a collapsed band has no scores in it and must not print a backwards
        # range like "19-18" — it says so instead.
        return f"{lo}-{hi - 1}" if hi > lo else "(retired)"
    labels = {
        "loom": f"{LOOM_FLAG_THRESHOLD}+",
        "hot": span(HOT_LEAD_THRESHOLD, LOOM_FLAG_THRESHOLD),
        "standard": span(SCORE_STANDARD, HOT_LEAD_THRESHOLD),
        "low": span(SCORE_FLOOR, SCORE_STANDARD),
        "below": f"<{SCORE_FLOOR}",
        "unscored": "?",
    }
    return labels[name]

# ── Proposal evidence (modules/upwork/evidence.py, 2026-08-16) ───────────────
# Deterministic KB retrieval for a draft. Every one of these is a gate, not a
# preference: the drafter used to be ORDERED to look facts up and, being a
# tool-less single-shot call, invented them instead.
EVIDENCE_MIN_GRADE = 2        # 0=tool inventory, 1=plain, 2=insight OR number, 3=both
EVIDENCE_MIN_FACTS = 3        # below this: no evidence block, plain path, `evidence: none`
EVIDENCE_MAX_FACTS = 8        # ~600 prompt tokens; more is dilution, not proof
EVIDENCE_PER_TOOL = 3         # stops one dense tool crowding out the rest of the stack
# A MEASURED claim decays into a wrong number: the same statement reads "80+
# automations" (2024-11) and "3,000+" (2026-04). Citing the old one is a 37x
# understatement in his own voice. An architectural constraint ages far better.
EVIDENCE_MEASURED_MAX_AGE_DAYS = 365
EVIDENCE_MAX_AGE_DAYS = 1095
# Post-draft validator: integers at or below this are enumeration ("three
# phases", "2 weeks", "1."), not claims. Anything above must trace to the
# reference, the supplied evidence, or the job post itself.
EVIDENCE_FREE_INTEGER_MAX = 12
# Founder INTENT, carried as config so the system respects it instead of reading
# neglect into deliberate choice. When set to a YYYY-MM-DD, the Today screen shows
# accumulated hot leads as a 'ready when you resume' backlog (ranked by ICP/score),
# never as an act-now queue that nags every morning. None = applications ACTIVE;
# hot leads re-enter the act-now queue.
#
# RESUMED 2026-08-07 (set to None): Abhijeet answered board-memo-001 Q1 = YES,
# resuming disciplined applications at 10/week — logged in business/STRATEGY.md
# decision log ("2026-08-07 · Q1 = YES — B1 ACTIVE"). The prior pause since
# 2026-06-30 is retired. This constant is a STATED INTENT: render code reconciles
# it against the connects rail (_pause_contradiction in modules/cockpit/data.py)
# so a stale value can never silently override observed applications — same law as
# no-fabricated-zeros and render-time-only counts.
APPLICATIONS_PAUSED_SINCE = None
# Intent-reconciliation thresholds: while a pause IS set, if the connects rail
# shows >= MIN_EVENTS 'applied' events after paused_since within WINDOW_DAYS, the
# tile surfaces a contradiction instead of 'paused' and a WARN anomaly is raised.
STALE_INTENT_WINDOW_DAYS = 14
STALE_INTENT_MIN_EVENTS = 2
TODAY_BACKLOG_TOPN = 8            # how many ready-when-resumed leads to preview
TODAY_FRESH_LEAD_DAYS = 10       # a lead older than this has almost surely expired
TODAY_QUEUE_TOPN = 12            # the act-now queue is ruthless; the rest -> Nudges
# When applications DID go out but no client has replied yet, replies lag by a
# few days — so a zero-reply window reads as "give it time", not "nothing to
# reply to". Typical proposal→reply lag; tune from observed thread timings.
REPLY_LAG_DAYS = 3

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
    "michaelxavierga": "CMF Publishing",     # IMPS bank remitter (Abhijeet, 2026-07-30)
    # Upwork masks this client as '(inner)'; it is one client — inner.garden
    # (contact Martin Butterfly), across contracts 37683592 & 37182507 (Abhijeet,
    # 2026-07-30). Requires removing '(inner)' from the _canon_client exclusion.
    "(inner)": "inner.garden",
    "cmf publishing llc": "CMF Publishing",
    "cmf publishing": "CMF Publishing",
    # Registry merge 2026-08-14 (Abhijeet: "service people rapheal and visa gate
    # all three are same"). `servicepeople` and `raphael-mahir` were retired into
    # `visa-gate`. These keys are what make the CLIENTS TAB agree with the
    # dossier: without them `_canon_client('Servicepeople')` still title-cases
    # its way to a row called 'Servicepeople' ($900.00, the 4 rows Upwork
    # labelled directly) while the two hourly rows reach the survivor through
    # the contract bridge and land on a SECOND row ($113.75) — one client, two
    # rows, which is the exact failure `_contract_client` exists to end. With
    # them both label groups collapse onto the dossier's own name and the tab
    # reads $1,013.75, to the cent.
    "servicepeople": "Visa Gate GmbH",
    "raphael mahir": "Visa Gate GmbH",
    "raphael mahir, servicepeople": "Visa Gate GmbH",
    "visa gate": "Visa Gate GmbH",          # _CO_SUFFIX strips the 'GmbH' first
    "visa gate gmbh": "Visa Gate GmbH",
    # MERGED 2026-08-24 — reversing the 2026-07-27 'NOT merged' decision, whose
    # stated premise ('no hermon dossier, we do not invent one') no longer
    # holds: hermon is a registry client with both Consulting-Systems spellings
    # as aliases and a compiled dossier. The trigger was Abhijeet catching the
    # symptom himself in this month's revenue: Abel paid two invoices on
    # 2026-08-10 and the Money tab filed one under a synthetic client named
    # 'Consulting Systems' while the other fell to the wise-mail bucket
    # ("that is abel that is hermon"). Both dotted and undotted forms are
    # keyed because Wise itself is inconsistent — the two payment emails,
    # same payer, same day, spelled it 'B.V.' and 'BV'. 'hermon.io' is what
    # Upwork's assignment_company carries, which was quietly a THIRD row for
    # the same client. 'consultancy international' is a DIFFERENT registry
    # client; no key here may collide with it.
    "consulting systems": "Hermon",
    "consulting systems b.v.": "Hermon",
    "consulting systems bv": "Hermon",
    "hermon.io": "Hermon",
    "hermon": "Hermon",
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
# A SCORE route into the ICP list too (Abhijeet, 2026-10-09: "28+ go into ICP
# and the rest go in hot leads" — the top of the pipeline uncrowded, and no job
# lost: 19-27 still lands in Hot). Why 28: in 2026 his cold proposals on 28+
# jobs were written back 16% of the time (4 hires in 43), on 19-27 jobs 5% (1
# in 55). It is the Loom bar, so a top-band card is ICP-listed and Loom-flagged.
ICP_SCORE_MIN = LOOM_FLAG_THRESHOLD

# ── Rate-unstated hourly gate (2026-07-16, option B) ─────────────────────────
# Upwork ships a blank hourly rate as $0, and the $30 floor read $0 as 'cheap',
# silently killing ~370 jobs (2/3 of that skip bucket) that never stated a rate
# at all. Rate-unstated is NOT low-value — but we don't score all of them either.
# When the rate is absent, the CLIENT'S proven spend stands in for it: a real
# spender who left the rate blank is worth scoring; an unproven one is not.
RATE_UNSTATED_MIN_CLIENT_SPEND_USD = 5000.0   # proven-spender proxy for a missing rate
RATE_UNSTATED_MIN_HIRES = 1                    # ...or payment-verified with a hire history

# Stripe currently has ONE paying client, so every Stripe income row is
# attributed here unless a Payments export names a different customer for that
# payment id (Abhijeet, 2026-07-30). Set to None once Stripe has >1 client and
# the Payments export is dropped alongside balance_history for real attribution.
#
# CONFIRMED 2026-08-14 — this was flagged UNVERIFIED on 2026-08-13 and the flag
# is now retired. Abhijeet: "optimize is real revenue, that is Optimize IS, a
# client that we have. This was their latest payout." So the 3 rows / $574.39
# (Jul 2 / 16 / 30 2026) on the Clients tab are REAL client revenue and this
# constant stays. What was true in the old caveat and is still true: no Payments
# export has ever been dropped in finance-inbox/stripe/, so the ATTRIBUTION is
# still carried by this constant rather than observed per-payment. That is
# acceptable while Stripe has one paying client and is exactly why the first
# line above says to set it to None when a second appears.
#
# THE 'Fee 0.00' WORRY IS RESOLVED, ARITHMETICALLY (2026-08-14). It was raised
# because a live Stripe CARD charge is never fee-free — but these are not card
# charges. balance_history holds five rows: three `payment` (Source `py_…`, a
# charge on a non-card payment method) and two `payout` (Source `po_…`), and a
# payout is a bank settlement, not a processed charge, so Fee 0.00 on it is
# structurally correct. On the three payments the zero is not merely reported,
# it is PROVEN by the ledger's own arithmetic: each settled payment is followed
# by a payout of the identical amount to the paisa (₹23,111.76 → ₹23,111.76 on
# Jul 2 → 5; ₹17,329.69 → ₹17,329.69 on Jul 16 → 19), which a deducted fee makes
# impossible, and `stripe_csv.reconcile()` closes Σpayments = Σpayouts + Σfees +
# balance exactly (55,015.53 = 40,441.45 + 0 + 14,574.08, the balance being the
# Jul-30 payment not yet settled at export time — "their latest payout"). The
# importer reads the Fee column rather than defaulting it, so a real fee would
# have shown. Measured: var/agent-scratch/four/04-money.
#
# Abhijeet's 2026-08-13 "there is no stripe client revenue at all" was about the
# EUR Beglaubigt.de receipts, a DIFFERENT Stripe account (EUR, zero-value,
# product testing — payment_mail_rail.ZERO_VALUE_STATE). That rule keys on the
# AMOUNT, never the provider, so it cannot swallow a payment like this one;
# tests/test_payment_mail_rail.py::test_a_real_stripe_payment_is_still_tracked_
# and_escalates pins it, using "Optimize IS" by name.
STRIPE_DEFAULT_CLIENT = "Optimize IS"

# ── Finance payment-mail rail (2026-08-09) ───────────────────────────────────
# Reads Wise/Stripe incoming-payment NOTIFICATIONS (read-only) from inbox 01 and
# emits PROVISIONAL payment events so a stale CSV import announces itself instead
# of rendering a confidently-wrong monthly total. Never writes `transactions`,
# never sends. See modules/finance/payment_mail_rail.py.
PAYMENT_MAIL_INBOX = "abhijeetgandhi01@gmail.com"
# Wise/Stripe payment notifications are ARCHIVED, not left in INBOX, so this rail
# reads All Mail (INBOX returns zero payment mail — the bug this rail was silently
# hitting). The name MUST stay quoted: imaplib does not quote a mailbox itself, so
# the space in "[Gmail]/All Mail" would make SELECT/EXAMINE a BAD parse error. The
# jarvis client-mail rail keeps its own INBOX (unread client mail); this is per-rail.
PAYMENT_MAIL_MAILBOX = '"[Gmail]/All Mail"'
# Sender allow-list per provider (bare domains or full addresses; matched on the
# address, its domain, or a parent domain). Only these are parsed by this rail.
PAYMENT_MAIL_SENDERS = {
    "wise": ("wise.com", "transferwise.com"),
    "stripe": ("stripe.com",),
}
PAYMENT_MAIL_FIRST_FETCH_DAYS = 60      # cold start window (the 60-day report needs it)
PAYMENT_MAIL_MAX_ITEMS = 400            # per-inbox per-run bound
PAYMENT_MAIL_MAX_SECONDS = 90           # wall-clock cap per collect: a slow All Mail
                                        # mailbox DEGRADES (partial, cursor advances)
                                        # instead of hanging the run (a 60-day fetch
                                        # wedged twice at 0% CPU before this guard)
PAYMENT_MAIL_RECONCILE_WINDOW_DAYS = 10  # unmatched-past-this -> surfaced loudly (weekly cadence + buffer)
PAYMENT_MAIL_AMOUNT_TOLERANCE_MINOR = 100  # $1.00 — provider fee/rounding slack when matching a ledger twin
# Billing entity (payer name on the notification) -> client_id (registry slug).
# Learnable/known billing identities; unknown payer -> mapping queue, never a
# guess. Keys are in payment_mail_rail._norm() space: lowercased, de-accented,
# punctuation -> spaces, collapsed. So "Consulting Systems B.V." -> the key
# "consulting systems b v"; "Beglaubigt.de" -> "beglaubigt de".
PAYER_ALIASES = {
    "consulting systems b v": "hermon",      # Hermon/Abel pays as Consulting Systems B.V.
    # ...and as 'Consulting Systems BV', no dots — norm 'consulting systems bv',
    # which is NOT 'consulting systems b v'. Wise used both spellings for the
    # same payer on the SAME DAY (2026-08-10, refs ...599882 dotted and
    # ...595443 undotted); the undotted invoice fell to the wise-mail bucket
    # and Abhijeet caught it in the month's revenue. One alias per spelling,
    # because the sender's inconsistency is not the ledger's problem.
    "consulting systems bv": "hermon",
    "consulting systems": "hermon",          # suffix-stripped form
    "adelye ltd": "adelye", "adelye": "adelye",
    "horizons aba midco llc": "horizons-aba",
    "horizons aba midco": "horizons-aba", "horizons aba": "horizons-aba",
    "cmf publishing llc": "cmf-publishing", "cmf publishing": "cmf-publishing",
    "beglaubigt": "beglaubigt", "beglaubigt de": "beglaubigt",
}

# ── Jarvis sweep (J1) — collect+map ceilings ─────────────────────────────────
# The 3x/day sweep (11:00 / 19:00 / 00:00 IST) reads contact@ read-only, maps
# each item to a client via the registry, and proposes the rest via Haiku. Hard
# per-run bounds so a bad day can never run away (§4.3 per-run ceilings):
JARVIS_SWEEP_TIMES = ("11:00", "19:00", "00:00")   # IST (informational; see plist)
JARVIS_SWEEP_MAX_EVENTS = 60        # collect at most N new items per sweep
# RAISED 15 -> 40 on 2026-10-18. 'held' means the proposer has not run yet, and
# at 15 calls x 3 sweeps the ceiling sat below the inflow, so the backlog only
# grew — 84 events were waiting, some for weeks, and they were being counted on
# the Review badge as though they were decisions he owed. 40 clears a 60-event
# collection inside two sweeps while staying a hard bound (Haiku, bounded
# prompt: the whole ceiling is cents per sweep). The backlog, not the cost, was
# the thing running away.
JARVIS_SWEEP_MAX_PROPOSALS = 40     # at most N Haiku proposer calls per sweep;
                                    # the rest stay 'held' for the next sweep
# A retroactive re-sweep (modules/jarvis/resweep.py) that clears more than this
# many rows off the Review board files a `warn` anomaly. Not a limit — it always
# applies every rule — but a rule edit is a decision Abhijeet did not watch
# happen, and he has to be able to disagree with it after the fact. Steady state
# is a handful of rows; the whole backlog moving at once is the case worth
# saying out loud.
JARVIS_RESWEEP_ANOMALY_THRESHOLD = 20
JARVIS_LOOM_MAX_PROPOSALS = 25      # loom bridge: at most N Haiku residue-proposer
                                    # calls per run (deterministic passes are $0)
# ── Loom KIND + domain-vocabulary classifier (2026-08-09, QUEUE-12) ──────────
# Replaces name-matching on the residue (measured: only 3/426 transcripts name a
# client). One Haiku call per Loom returns {kind, proposed_client_id, confidence,
# evidence}; the model writes ONLY the events ledger (never sources/facts/dossier)
# unless JARVIS_LOOM_HEAL_ON_HIGH is flipped on. Bands below route the result.
JARVIS_LOOM_KIND_HIGH = 0.80        # confidence >= HIGH -> provisional auto-map
JARVIS_LOOM_KIND_MED = 0.50         # MED <= confidence < HIGH -> queue w/ evidence;
                                    # below MED (or unknown) -> exclude unattributable
JARVIS_LOOM_CLASSIFY_MAX = 200      # per-run ceiling on Haiku classify calls
JARVIS_LOOM_TRANSCRIPT_WORDS = 1500 # transcript truncated to first N words (signal
                                    # is front-loaded; caps cost + injection surface)
JARVIS_LOOM_HEAL_ON_HIGH = True     # ABHIJEET'S CALL, MADE 2026-08-13 (was False).
# What it does: at CLASSIFY TIME, a >=HIGH-confidence client-work result stops
# being a ledger-only provisional map and additionally runs `heal_source` --
# sets sources.client_id/client_name and re-attributes that source's facts to
# the client, then recompiles the dossier. The event row is still written first
# and still carries resolved_by='loom_classifier:high', so the inference stays
# labelled machine-made and reversible; what changes is that it now reaches the
# KB instead of stopping at the ledger.
# WHY it was flipped: with it False the classifier produced NO KB value at all.
# Its high-confidence maps left sources.client_id NULL, and a Loom fact with a
# NULL client_id lands in proof-points and the search corpus but NEVER in the
# dossier of the client it is actually about. Loom walkthroughs are Abhijeet
# narrating the systems he shipped -- the richest "what we actually deliver"
# evidence in the business -- and all of it was landing everywhere except the
# one place a client conversation would use it.
# WHAT IT DOES NOT CHANGE: medium confidence still queues for a human, low /
# unknown / non-client kinds are still excluded, and every promoted fact still
# has to pass kb.validate_fact's citation requirement and the shared noise gate.
# The model still cannot invent a client id -- a hallucinated one is dropped to
# null against reg.matchable_ids() before routing.
# NOTE the flag only bites at classify time. Recordings classified BEFORE the
# flip are healed by the separate, LLM-free, idempotent backfill:
#   PYTHONPATH=. python3 bin/jarvis-loom-classify --heal-mapped
# (loom_classifier.heal_mapped -> the same heal_source). Run 2026-08-13 for the
# 34 maps this session's classify pass produced.
LOOM_FINGERPRINTS_PATH = V3_ROOT / "assets" / "loom-fingerprints.json"
# ── Loom FETCH rail (2026-08-10) — pull NEW recordings from loom.com ──────────
# The manual index export (assets/loom-video-index.json) froze 2026-04-08; with no
# fetcher the corpus stalled four months. This rail fetches the live GraphQL index
# (Abhijeet's logged-in session, via the scanner's Chrome substrate) and pulls
# transcripts for ids not already in `sources`. Read-only on Loom; local writes
# only. See modules/knowledge/loom_fetch.py. Session loss FAILS LOUD, never a 0.
LOOM_INDEX_LIMIT = 700               # my_videos(limit:) — comfortably over the ~591
LOOM_FETCH_MAX_PER_RUN = 50          # transcripts fetched per run (bound; --limit)
# Attributed looms kb_daily EXTRACTS per run (2026-11-01). The 72-deep backlog
# used to wait for a human to run kb-backfill and carded Today meanwhile; at
# this pace it drains in about a week on the Haiku pin, inside the run cap.
KB_DAILY_LOOM_PER_RUN = 12
LOOM_FETCH_POLL_TIMEOUT_SECONDS = 40 # per in-Chrome async fetch (index/share HTML)
LOOM_TRANSCRIPT_HTTP_TIMEOUT_SECONDS = 30  # per Python urllib GET of a signed CDN
                                     # transcript URL (public — no session/CORS)
# ── IMAP rail (2026-08-09) — the inboxes contact@'s OAuth app cannot reach ────
# The OAuth client is registered Internal, so it can only ever authorize
# etherwise.io accounts; the personal inboxes go over IMAP with per-account app
# passwords instead (no expiry, no Google verification). SAME classify gate +
# event shape as the Gmail API rail (mail_pipeline) — only the transport differs.
# contact@ stays on OAuth (untouched); abhijeet.gandhi@optimizeis.com gets NO
# IMAP rail (its Workspace disallows app passwords — covered later by the browser
# rail) and so is deliberately absent from this list.
JARVIS_IMAP_INBOXES = (
    "abhijeetgandhi01@gmail.com",
    "abhijeetgandhi02@gmail.com",
)
JARVIS_IMAP_HOST = "imap.gmail.com"
JARVIS_IMAP_FIRST_FETCH_DAYS = 3    # cursor-less first fetch is bounded to N days
# Socket timeout (seconds) on EVERY IMAP network call — connect AND each SEARCH /
# FETCH, because the timeout set at connect persists on the (SSL-wrapped) socket
# for subsequent reads. A rail that can block forever will eventually hang the
# sweep (a prior build wedged 24 min at 0% CPU on a live connect), so this is a
# hard requirement, not a nicety. Both the sweep rail and bin/jarvis-imap-check
# inherit it via imap_rail._connect.
JARVIS_IMAP_TIMEOUT_SECONDS = 20
# …but LOGIN gets its own, larger budget. How long Gmail takes to answer LOGIN
# is a property of the ACCOUNT, not of us: measured 2026-08-20,
# abhijeetgandhi02@gmail.com timed out in LOGIN — never in SEARCH or FETCH — at
# the flat 20s, roughly every other sweep since 2026-08-10 (12 anomalies,
# `imap_inboxes` reading 1 instead of 2, that inbox unread for eleven days),
# while 01 on the same host and the same 20s was fine. Raising the ONE number
# would have fixed login and simultaneously allowed a stalled read to hang the
# sweep for a minute, which is this rail's cardinal sin — so the budgets are
# split, and imap_rail._connect tightens the socket back to the read bound the
# moment login returns. Worst case is bounded at ATTEMPTS * LOGIN_TIMEOUT.
JARVIS_IMAP_LOGIN_TIMEOUT_SECONDS = 60
JARVIS_IMAP_LOGIN_ATTEMPTS = 2      # login is where the variance is; retry once
# Per-account app passwords: { "<email>": "<app password>" }, chmod 600,
# git-ignored, in CREDENTIALS_DIR (v2's .credentials). Read at runtime by
# imap_rail; NEVER logged, echoed, or placed in a prompt. A missing entry means
# that inbox is skipped with a clear reason, not a crash.
GMAIL_APP_PASSWORDS_FILE = CREDENTIALS_DIR / "gmail-app-passwords.json"
# Personal inboxes have a VERY LOW base rate of client mail (01 is card/finance
# alerts + newsletters, 02 is largely personal), so UNMATCHED mail from them must
# default to EXCLUDED, never queued — otherwise the mapping queue fills with
# hundreds of non-client items and becomes unusable (it jumped to 94 in one live
# sweep). Only a POSITIVE client signal queues one of these: a deterministic
# registry match / domain echo, or a proposer attribution naming a registry client
# at >= JARVIS_IMAP_QUEUE_MIN_CONFIDENCE. A null / low-confidence proposal on a
# low-base-rate inbox is EXCLUDED (reason 'imap-low-base-rate:no-client-signal'),
# not queued. contact@ (never in this set) keeps its original behaviour — a null
# proposal there still queues for human triage, because its base rate is high.
JARVIS_IMAP_LOW_BASE_RATE_INBOXES = JARVIS_IMAP_INBOXES
JARVIS_IMAP_QUEUE_MIN_CONFIDENCE = 0.50   # positive-signal bar for a low-base-rate proposal
# ── Slack rail (J2 rail #1, 2026-08-09) — client workspaces via the browser ───
# Slack's web client renders BLANK to screenshots but is fully readable via the
# DOM/accessibility tree, so this rail extracts from the DOM only, never pixels.
# API/bot install is NOT available for client workspaces (we are guests), so the
# browser is the only path; it is READ-ONLY and NEVER sends a Slack message.
# Registry-driven: one loop iteration per client with a slack.url (the internal
# Etherwise workspace is not a client, so it never appears — v1 skips it). Browser
# work is slow, so this runs 2x/day on its OWN launchd job, NOT the 3x/day sweep.
JARVIS_SLACK_TIMES = ("11:00", "19:00")     # IST (informational; see plist)
JARVIS_SLACK_SINCE_DAYS = 3                 # default --since window (recent only)
JARVIS_SLACK_MAX_CONVERSATIONS = 40         # per workspace, freshest-first, bounded
# Every network step (navigate + each DOM eval) is bounded so one wedged workspace
# can never hang the whole rail; the browser layer polls up to this many seconds
# for the SPA to paint before giving up on a conversation.
# Measured live 2026-08-10: the Slack client shell paints in ~1s but the
# virtualised sidebar needs ~9-11s, and a conversation's messages hydrate a few
# seconds after that. The poll now waits for real content (rows / message rows),
# so this ceiling is what a genuinely empty or wedged workspace costs before it
# degrades — keep it comfortably above the observed hydration time.
JARVIS_SLACK_POLL_TIMEOUT_SECONDS = 40
# Per-CONVERSATION poll. Shorter than the workspace poll because it is paid once
# PER CONVERSATION — 40s here turned a 4-workspace sweep into a 40-minute grind.
# But measured live 2026-08-10: message rows hydrate at ~11s, and 12s produced
# 0 messages on 38 of 38 conversations (scanned fine, just read too early). 22s
# is that measurement plus headroom; a conversation that paints fast still exits
# early, so only genuinely empty conversations pay the ceiling.
JARVIS_SLACK_CONV_POLL_TIMEOUT_SECONDS = 22
# ── OUR SIDE — the humans who ARE Etherwise (2026-08-14) ─────────────────────
# ONE list, because "is this person us?" is asked by four different consumers and
# each of them used to answer it from its own private constant:
#   * slack_rail.parse_message  — outbound vs inbound  (JARVIS_SLACK_SELF_AUTHORS)
#   * fathom_bridge._external_signal — is this call a CLIENT signal or internal?
#     (it only ever checked EMAILS, so a teammate who appears by NAME slipped past)
#   * registry._validate_and_index — one of us may never become a client identifier
#   * mail rails — SELF_SENDERS / SELF_SENDER_DOMAINS (address-based, unchanged)
#
# What a second list costs, measured: Alphons Jaimon is an Etherwise contractor.
# He appears on 12 Fathom calls; on exactly ONE of them (src 712) Fathom captured
# his address, alphons@etherwise.io, and that call was correctly read as internal.
# On the other 11 he is a bare transcript SPEAKER, so every name-blind check saw
# an unrecognised human on a call and treated him as external client signal. The
# roster miner then proposed him as an alias for TWO different clients at once.
#
# Matched EXACTLY on the normalised full name — never as a substring. "abhijeet"
# is in here as a bare token precisely because Slack renders him that way; a
# substring rule would let it claim "Abhijeet Sharma", a stranger.
#
# DERIVED FROM PAYROLL, NOT HAND-MAINTAINED (2026-08-14). The hand-kept tuple
# had drifted four names behind `../Payroll/employees.json` — E004 Sachin Kadam,
# E005 Ranjana Gandhi, E007 Ankita Mehta and E008 Tanmay Bora were all on
# payroll and none of them was "us" to this system. That is a defect with a
# known shape: an unrecognised human on a call is read as external CLIENT
# signal, and the roster miner then proposes the teammate as a client alias
# (Alphons Jaimon, twice, on two different clients). A new hire must not need a
# code change to stop looking like a client.
#
# So the roster is: every name in employees.json (whatever their status — a
# withdrawn or not-yet-started hire is still not a client) UNION the extras
# below, which are the people and display forms payroll cannot know about.
#
# ADD A CONTRACTOR OR A DISPLAY FORM to INTERNAL_PEOPLE_EXTRA; add an EMPLOYEE
# to Payroll/employees.json and this picks them up on the next import.
PAYROLL_FILE = WORKSPACE_ROOT / "Payroll" / "employees.json"

INTERNAL_PEOPLE_EXTRA = (
    "abhijeet gandhi",       # the founder — not on the payroll file
    "abhijeet",              # …and the bare token Slack renders him as
    "alphons jaimon",        # contractor (confirmed by Abhijeet 2026-08-13);
                             # alphons@etherwise.io appears on src 712
    # E008's transcript SPEAKER label is the bare given name. Fathom wrote
    # "Tanmay" (not "Tanmay Abhay Bora") on evt:fathom:170653696 — 41%
    # Devanagari, with Jay — and an exact-full-name check does not see it, so
    # the call read as "an unknown human was here" until Abhijeet said
    # otherwise. Deliberate, like "abhijeet": checked 2026-08-14 against every
    # registry identifier, no client claims the token.
    "tanmay",
)


def _payroll_people() -> tuple:
    """Every employee name in Payroll/employees.json, lowercased.

    Never raises and never returns a SHORTER roster than the extras: a missing
    or malformed payroll file must degrade to "we know about fewer people",
    not to an import-time crash in the module every other module imports.
    A read failure is recorded on `PAYROLL_READ_ERROR` so `bin/doctor` can say
    so out loud rather than letting the roster silently shrink.
    """
    global PAYROLL_READ_ERROR
    try:
        import json
        data = json.loads(PAYROLL_FILE.read_text())
        names = [str(e.get("name") or "").strip().lower()
                 for e in (data.get("employees") or [])]
        return tuple(n for n in names if n)
    except Exception as exc:                    # noqa: BLE001 — see docstring
        PAYROLL_READ_ERROR = f"{type(exc).__name__}: {exc}"
        return ()


PAYROLL_READ_ERROR = None                       # set by _payroll_people() below
# dict.fromkeys: de-duplicated, insertion-ordered, stable across runs.
INTERNAL_PEOPLE = tuple(dict.fromkeys(_payroll_people() + INTERNAL_PEOPLE_EXTRA))
# Our own display names inside CLIENT workspaces: a message from one of these is
# OURS -> tagged direction='outbound' (captured, never dropped) so the follow-up
# logic can tell we already replied. Matched case-insensitively on the full name.
# Slack's own is_me DOM cue wins when present; this is the name-based fallback.
# DERIVED, not a second list: until 2026-08-14 it held only Abhijeet's two names,
# so a teammate answering a client in Slack read as the CLIENT talking.
JARVIS_SLACK_SELF_AUTHORS = INTERNAL_PEOPLE


def is_internal_person(who: str) -> bool:
    """True if this participant string is one of US — by NAME (exact, normalised)
    or by ADDRESS (OWNER_IDENTITIES / an @etherwise.io address).

    The single answer to "is this person us?". `registry.norm` is imported lazily
    because registry imports config; calling it (rather than mirroring lower()/
    strip() here) is what stops this check from drifting away from the identity
    spine's own notion of sameness — norm() gained accent-folding and internal
    whitespace collapse in 2026-08 and a hand-rolled copy would still be stale.
    """
    from core import registry                     # lazy: registry imports config
    a = (who or "").strip().lower()
    if not a:
        return False
    if "@" in a:
        return a in OWNER_IDENTITIES or a.rsplit("@", 1)[-1] in SELF_SENDER_DOMAINS
    return registry.norm(a) in {registry.norm(p) for p in INTERNAL_PEOPLE}
# ── EXTRACT stage (J2 §4.3 stage 3) — the READER, staging-only ───────────────
# Per-CLIENT bundle (not per-event), so cost scales with active clients, not
# mail volume. Hard per-run ceiling so a busy day can never run away; overflow
# stays unextracted for the next sweep (like the proposer's 'held').
JARVIS_EXTRACT_MAX_CLIENTS = 8            # at most N client bundles extracted per run
JARVIS_EXTRACT_MAX_EVENTS_PER_CLIENT = 12  # cap events bundled per client (cost)
JARVIS_EXTRACT_TEXT_CAP = 4000           # chars of each event body sent to the reader
JARVIS_EXTRACT_MAX_OUTPUT_TOKENS = 4000  # reader output ceiling
# Founder-scope HARD RULE (architecture §4.4): a fact touching ANY of these is
# founder-only — NEVER model discretion. Everything else is team scope. Matched
# case-insensitively on word boundaries against fact_text. Add a term = one line.
EXTRACT_FOUNDER_SCOPE_TERMS = (
    "money", "price", "pricing", "priced", "cost", "costs", "budget",
    "invoice", "invoices", "invoiced", "payment", "payments", "paid",
    "salary", "salaries", "payroll", "wage", "wages", "compensation",
    "rate", "rates", "retainer", "deposit", "refund", "discount",
    "fee", "fees", "commission", "bonus", "equity", "revenue", "profit",
    "contract value", "contract worth", "quote", "quoted", "billing",
    "hr", "hiring", "hire", "hired", "offer letter", "raise", "severance",
)

# Self-noise: the rail NEVER ingests our OWN automation output (bot digests,
# scan alerts, reports). Excluded at the metadata stage BEFORE any body fetch or
# token is spent (reason 'self-noise'). Add a sender or subject here — one line —
# whenever a new system email appears; this is code-enforced, not prompt-hoped.
SELF_SENDERS = (
    "contact@etherwise.io",         # our primary SMTP identity (SMTP_FROM)
    "etherwise bot",                # the automation display name (From header)
)
SELF_SENDER_DOMAINS = ("etherwise.io",)   # any @etherwise.io sender is internal
# Owner/team identities — the humans who are US, not clients, BY ADDRESS. A
# call/email where the only outside party is an owner is internal, never a client
# signal (the Fathom bridge skips them; the email rail treats them as self-noise).
# Extend as team members' personal addresses surface.
# The NAME-side companion is INTERNAL_PEOPLE above; `is_internal_person()` asks
# both, and is what every consumer should call rather than reading either tuple.
OWNER_IDENTITIES = (
    "contact@etherwise.io",
    "abhijeetgandhi02@gmail.com",   # Abhijeet (personal)
    # Abhijeet's own account on a client's domain: mail FROM it is US, so it's
    # self-noise; other @optimizeis.com senders stay client signal (registry maps
    # the optimizeis.com domain to optimize-is; owner check runs FIRST) (2026-08-08)
    "abhijeet.gandhi@optimizeis.com",
)
SELF_SUBJECT_PATTERNS = (
    "upwork scan:", "hot upwork leads", "source-health", "source health",
    "weekly sales report", "monthly connect roi", "roi report",
    "finance-upwork-weekly", "upwork agency",
    # 2026-08-19. 69 mails subjected "[Etherwise Ops] <Error> — <workflow>" from
    # onboarding@resend.dev — Resend's default sender, so the ADDRESS proves
    # nothing and cannot be used: the same address also delivers the website
    # contact form ("New inquiry from Nick Esquivel (Globaltize)"), a real
    # inbound lead. The SUBJECT is the discriminator and it is exact: every one
    # of the 69 carries this prefix, the contact form carries none.
    #
    # WHY THEY ARE SELF-NOISE AND NOT AN INCIDENT FEED: every body cites
    # `http://localhost:3000/incidents/<uuid>` and names `Client: Test Client A
    # / Org: Northwind Retail`. They are test emissions from an incident-monitor
    # build running on Abhijeet's own laptop, not production alerts about a real
    # client. Routing them to `anomalies` would have filled the system's own
    # health surface with fabricated incidents — the exact sin the no-fabricated-
    # zeros rule exists to prevent. He had already reached the same verdict by
    # hand 16 times ('not a client', cockpit-approval) while 43 more piled up
    # behind those clicks, which is what made this the never-ask-twice failure.
    # If this monitor ever ships for real it will send from an Etherwise domain
    # and want its own rail; that is a decision for him, not for this tuple.
    "[etherwise ops]",
)

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
    # 2026-08-27: the LAST Chrome rail retired. connects_history is written by
    # the MCP ledger read (mcp_financials, daily 06:05) from MCP_CONNECTS_SINCE
    # on; io.etherwise.v3.upwork-connects moved to launchd/retired/. The health
    # line stays — the applied-truth still needs a freshness signal — it just
    # watches the task that actually fills the table now. An empty window is
    # degraded-not-failed: genuinely no activity looks identical to a throttled
    # endpoint, and failing would spam criticals.
    {"key": "connects_rail", "label": "Connects history (MCP, in the ledger rail)",
     "task_name": "upwork_financials", "max_age_hours": 30,      # daily + grace
     "degrade_kinds": ("connects_empty_window",),
     "critical": True, "backs": "applied",
     "launchd_label": "io.etherwise.v3.upwork-financials", "catch_up": True,
     "why": "the applied-truth source; a stale rail makes 'applied' look like a"
            " live 0 when nobody is actually counting"},
    {"key": "scanner", "label": "Upwork job feed (MCP)",
     "task_name": "upwork_scan", "max_age_hours": 3,
     "degrade_kinds": ("scan_feed_degraded",), "critical": True,
     "backs": "pipeline",
     "launchd_label": "io.etherwise.v3.upwork-scan", "catch_up": True,
     # FLIPPED 2026-08-26 (Phase 0.1): the same task, the same plist, the MCP
     # standing-query round inside instead of the Chrome scrape (scan_pipeline
     # dispatches on JOB_FEED_PROVIDER below). The shadow poller that proved
     # the feed retired the same minute — one MCP budget, one round an hour
     # until 2026-10-18, four since (UPWORK_SCAN_MINUTES). max_age stays 3h:
     # a quiet quarter hour ends skipped_empty, which is not a success here,
     # so a tighter bar would cry "late" over a slow night.
     "why": "backs the funnel + carded board; a thin MCP round is"
            " scan_feed_degraded and silently under-scans lead-gen"},
    # ── the Upwork rails that were invisible (Upwork chat, 2026-08-25) ────────
    # Finding 8 of the Upwork codebase map: upwork_inbox, upwork_outcomes and
    # the MCP poller had no entry here, so no health line, no catch-up, and the
    # 08-23 Sunday outcome run silently did not happen. The Upwork tab's rails
    # table (docs/upwork/PHASE1_TAB_SPEC.md §3.5/§7) reads these. NOTE the
    # registry test binds `catch_up` to actions.RERUN_JOBS both ways: a catch-up
    # source must be re-runnable from the cockpit, a non-catch-up one must not.
    # (The `mcp_feed` poller entry lived here 08-25 → 08-26; retired at the flip.)
    {"key": "upwork_inbox", "label": "Upwork inbox (messages, GraphQL)",
     "task_name": "upwork_inbox", "max_age_hours": 12,        # 10:15 + 19:15, 9h gap + grace
     "degrade_kinds": (), "critical": True, "backs": "upwork",
     "launchd_label": "io.etherwise.v3.upwork-inbox", "catch_up": True,
     "why": "reply detection for every owed-reply card; stale means cards outlive"
            " the replies that should have cleared them"},
    {"key": "upwork_outcomes", "label": "Sunday outcome email",
     "task_name": "upwork_outcomes", "max_age_hours": 192,    # weekly + a day of grace
     "degrade_kinds": (), "critical": False, "backs": "upwork", "empty_ok": True,
     # catch_up is SAFE despite this being an email: outcome_capture.already_sent
     # refuses a second send in the same ISO week, so a Sunday the Mac slept
     # through (19 Jul and 23 Aug both) is asked on the Monday instead of never.
     "launchd_label": "io.etherwise.v3.upwork-outcomes", "catch_up": True,
     "why": "the Loop 2 outcome-reason capture; a week with nothing to ask is a"
            " clean skip, a week that never ran is not"},
    # the sales engine's three new agents (2026-10-09, his "All three now";
    # docs/upwork/sales-engine.md). The progress email is catch-up safe for the
    # same reason the outcome email is: it refuses a second send the same day.
    {"key": "upwork_progress", "label": "Progress email (Mon/Wed/Fri + Sunday)",
     "task_name": "upwork_progress", "max_age_hours": 80,      # Fri 08:30 -> Mon 08:30 + grace
     "degrade_kinds": (), "critical": False, "backs": "pipeline", "empty_ok": True,
     "launchd_label": "io.etherwise.v3.upwork-progress", "catch_up": True,
     "why": "the game's scoreboard in his inbox (his cadence, 2026-10-09);"
            " stale means days of proposals nobody summed up"},
    {"key": "upwork_conversation_codes", "label": "Conversation reader (AI, every 2h)",
     "task_name": "upwork_conversation_codes", "max_age_hours": 6,
     "degrade_kinds": (), "critical": False, "backs": "pipeline", "empty_ok": True,
     "launchd_label": "io.etherwise.v3.upwork-conversation-codes", "catch_up": False,
     "why": "why conversations did or did not become hires; capped at $25 for"
            " the first pass and $0.50 a run; never reads Globaltize"},
    {"key": "upwork_funnel_refresh", "label": "Funnel refresh (nightly)",
     "task_name": "upwork_funnel_refresh", "max_age_hours": 30,   # daily 05:20 + grace
     "degrade_kinds": (), "critical": False, "backs": "pipeline",
     # catch-up safe: read-only on Upwork, every step resumable, the backfill
     # an upsert — a night the Mac slept through is read the next morning
     "launchd_label": "io.etherwise.v3.upwork-funnel-refresh", "catch_up": True,
     "why": "new proposals, their opens, replies and hires, and the tests'"
            " links; stale means the Pipeline and the running test stop counting"},
    # the hourly top-up (2026-10-13, his "Can we build this"): only proposals
    # the sync has stored and the funnel has not. Skips are its steady state
    # (most hours send nothing new). Catch-up safe: read-only on Upwork, the
    # backfill an upsert, and it steps aside while the nightly refresh runs.
    {"key": "upwork_funnel_topup", "label": "Funnel top-up (hourly)",
     "task_name": "upwork_funnel_topup", "max_age_hours": 6,
     "degrade_kinds": (), "critical": False, "backs": "pipeline", "empty_ok": True,
     "launchd_label": "io.etherwise.v3.upwork-funnel-topup", "catch_up": True,
     "why": "a proposal he sends reaches the funnel and the running test within"
            " the hour instead of the next morning"},
    {"key": "upwork_reactivation", "label": "Win-back drafts (Mondays)",
     "task_name": "upwork_reactivation", "max_age_hours": 192,
     "degrade_kinds": (), "critical": False, "backs": "pipeline", "empty_ok": True,
     "launchd_label": "io.etherwise.v3.upwork-reactivation", "catch_up": True,
     "why": "at most five check-in drafts a week for past clients; he sends them"},
    {"key": "cos_sweep", "label": "Card sweep (chief of staff)",
     "task_name": "cos_sweep", "max_age_hours": 10,           # every 2h 12:30-04:30; the overnight gap is 8h
     "degrade_kinds": (), "critical": True, "backs": "today", "empty_ok": True,
     "launchd_label": "io.etherwise.v3.cos-sweep", "catch_up": True,
     "why": "decays every card whose condition is no longer observed; a dead sweep"
            " means nothing ever auto-clears"},
    # ── money-backing sources (2026-07-20) ───────────────────────────────────
    # A dead sync silently staling the Money tab is the same failure class as
    # the dead connects rail, so the Money numbers get the same guarantee.
    # Wise mail is the SOLE source of Wise money since the manual CSV export was
    # retired (2026-08-10, Abhijeet's call). That removes the human cross-check
    # that used to catch a broken importer, so this rail carries the whole
    # burden: if it stops, the books freeze and nothing else would notice.
    # 30h deadline = daily 06:00 + grace for a missed wake.
    {"key": "wise_mail", "label": "Wise mail -> ledger (income + fee)",
     "task_name": "finance_wise_mail", "max_age_hours": 30,
     "degrade_kinds": ("wise_mail_empty_window",), "critical": True,
     "backs": "money",
     "launchd_label": "io.etherwise.v3.finance-wise-mail", "catch_up": True,
     "why": "the only source of Wise revenue and fees now that the CSV export"
            " is retired; stale means the P&L silently stops at the last email"
            " it managed to read"},
    {"key": "upwork_sync", "label": "Upwork sync",
     "task_name": "upwork_sync", "max_age_hours": 3,     # hourly at :45
     # the MCP proposals pass inside the sync failing is a degraded sync, not
     # a failed one: rooms and contracts still landed (2026-08-26)
     "degrade_kinds": ("proposals_pass_failed", "contracts_pass_failed"),
     "critical": True, "backs": "pipeline",
     "launchd_label": "io.etherwise.v3.upwork-sync", "catch_up": True,
     "why": "feeds rooms, contracts and — through the MCP since 26 Aug — every"
            " proposal's status: the pipeline's thread state, the win rate and"
            " the Sunday outcome email all read it"},
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
    # ── the MCP ledger read (2026-08-26, use case A) ──────────────────────────
    # Daily 06:05 IST: both orgs' transactions (personal + agency) into the
    # ledger under the CSV's own canonical key, and the connects usage rows in
    # shadow. Critical from day one: this is the rail the CSV import retires
    # into, so its silence must turn the doctor amber before a week of money
    # goes unread. degraded = a read that could not return every row.
    {"key": "upwork_pulse", "label": "Upwork heartbeat (offers, invitations, messages)",
     "task_name": "upwork_pulse", "max_age_hours": 2,      # every 15 min + grace
     "degrade_kinds": ("pulse_failed", "messages_pass_failed"),
     "critical": True, "backs": "pipeline",
     "launchd_label": "io.etherwise.v3.upwork-pulse", "catch_up": True,
     "why": "one read a quarter hour that says whether an offer or an invitation"
            " is waiting — the only thing that sees either; stale means an offer"
            " can sit unanswered for a day. It also carries the room sweep (0.4),"
            " so a reply lands in v3 within 15 minutes instead of at the next"
            " 10:15/19:15 inbox poll"},
    {"key": "upwork_hiring", "label": "Hiring side (his own postings)",
     "task_name": "upwork_hiring", "max_age_hours": 30,          # daily + grace
     "degrade_kinds": (), "critical": False, "backs": "upwork",
     "launchd_label": "io.etherwise.v3.upwork-hiring", "catch_up": True,
     "why": "the applicants waiting on HIM; stale means a shortlist sits and the"
            " hire quietly does not happen"},
    {"key": "upwork_financials", "label": "Upwork ledger (MCP, daily)",
     "task_name": "upwork_financials", "max_age_hours": 30,      # daily + grace
     "degrade_kinds": ("financials_truncated", "financials_no_agency", "inflight_failed"),
     "critical": True, "backs": "money",
     "launchd_label": "io.etherwise.v3.upwork-financials", "catch_up": True,
     "why": "reads both Upwork orgs' transactions every morning — the rail"
            " that replaces the weekly CSV import; stale means new Upwork"
            " earnings and fees are not reaching the P&L"},
    {"key": "finance_stripe", "label": "Stripe rail (daily)",
     "task_name": "finance_stripe", "max_age_hours": 26,      # daily at 06:20
     "degrade_kinds": ("finance_stripe_reconcile", "finance_stripe_doctor_red"),
     "critical": True, "backs": "money",
     "launchd_label": "io.etherwise.v3.finance-stripe", "catch_up": True,
     "why": "imports Stripe payments (revenue) + settlements; a stale/failed run"
            " means new Stripe income is not reaching the P&L"},
    {"key": "jarvis_sweep", "label": "Jarvis sweep (Gmail contact@)",
     "task_name": "jarvis_sweep", "max_age_hours": 16,   # 3x/day (max gap 11h)+grace
     "degrade_kinds": ("sweep_reconcile_mismatch",),
     # LIVE at the J1 checkpoint (Abhijeet, 2026-08-07): the sweep has run and is
     # the client-centric backbone, so a stale/degraded rail turns health amber.
     # Keep the launchd job loaded so freshness stays inside max_age_hours.
     "critical": True, "backs": "today",
     # a quiet inbox is a CLEAN skip ('no new mail since cursor'), not a death —
     # see SUCCESS_STATUSES_EMPTY_OK in core/source_health.py
     "empty_ok": True,
     "launchd_label": "io.etherwise.v3.jarvis-sweep", "catch_up": True,
     "why": "the client-centric sweep behind Today + the mapping queue; a stale"
            " sweep means new client mail is not being seen or routed"},
    # Per-IMAP-inbox health (2026-08-09). The IMAP rails ride inside jarvis_sweep,
    # so they SHARE its freshness (task_name=jarvis_sweep); each row exists only to
    # surface a per-inbox DEGRADE — a bad app password / IMAP failure raises
    # 'jarvis_imap_error_<stem>' on the latest sweep, so exactly the dead inbox
    # shows amber while contact@ + the sibling inbox stay green. Not critical: a
    # personal-inbox rail is supplementary signal, not a money/pipeline source.
    {"key": "jarvis_imap_01", "label": "Jarvis IMAP (abhijeetgandhi01@gmail.com)",
     "task_name": "jarvis_sweep", "max_age_hours": 16,
     "degrade_kinds": ("jarvis_imap_error_abhijeetgandhi01_gmail.com",),
     "critical": False, "backs": "today", "empty_ok": True,
     "launchd_label": "io.etherwise.v3.jarvis-sweep", "catch_up": True,
     "why": "read-only IMAP rail for the 01 inbox (card/finance + client mail the"
            " OAuth app cannot reach); a dead rail means that inbox goes unseen"},
    {"key": "jarvis_imap_02", "label": "Jarvis IMAP (abhijeetgandhi02@gmail.com)",
     "task_name": "jarvis_sweep", "max_age_hours": 16,
     "degrade_kinds": ("jarvis_imap_error_abhijeetgandhi02_gmail.com",),
     "critical": False, "backs": "today", "empty_ok": True,
     "launchd_label": "io.etherwise.v3.jarvis-sweep", "catch_up": True,
     "why": "read-only IMAP rail for the 02 inbox; a dead rail means that inbox"
            " goes unseen"},
    # Slack rail (2026-08-09) — client workspaces over the browser, 2x/day on its
    # OWN launchd job (browser work is slow, so it is NOT on the sweep). One SOURCE
    # for the whole rail: a logged-out or signed-in-but-empty workspace raises a
    # STATIC degrade kind (the team is in the anomaly detail), so exactly the same
    # self-healing model as the connects rail. Not critical: client-context signal,
    # not a money/pipeline source. Max gap is 19:00->11:00 = 16h, plus grace.
    {"key": "jarvis_slack", "label": "Jarvis Slack rail (client workspaces)",
     "task_name": "jarvis_slack", "max_age_hours": 20,
     "degrade_kinds": ("jarvis_slack_empty_window", "jarvis_slack_logged_out"),
     "critical": False, "backs": "today",
     "launchd_label": "io.etherwise.v3.jarvis-slack", "catch_up": True,
     "why": "reads client Slack workspaces (both sides of the thread) for the"
            " follow-up logic; a stale/logged-out rail means client Slack goes"
            " unseen and we cannot tell what we already replied to"},
    # LinkedIn (2026-10-28) — the engagement tracker, read-only from Airtable
    # every 2h 12:05-04:05 IST. The overnight gap is 8h, so 10h is late. Not
    # critical: a channel being built, not a money/pipeline source. Catch-up
    # safe: read-only, and the store is an upsert.
    {"key": "linkedin_sync", "label": "LinkedIn tracker (Airtable)",
     "task_name": "linkedin_sync", "max_age_hours": 10,
     "degrade_kinds": ("linkedin_schema_drift",),
     "critical": False, "backs": "linkedin",
     "launchd_label": "io.etherwise.v3.linkedin-sync", "catch_up": True,
     "why": "the LinkedIn tab and Today's LinkedIn block read it; stale means"
            " drafts, replies and DM-ready people stop appearing"},
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

    # Freshness is runs-based (did the rail RUN), NOT data-based: an inbox with no
    # new Wise email is legitimately empty and must not read as 'down'. The
    # email-ahead-of-ledger signal is the SEPARATE staleness() overlay in the
    # cockpit.
    # NO empty_ok: an empty inbox COMPLETES normally here (inserted: 0). This
    # rail's only TaskSkip is InboxSkip = 'no app password configured', which is
    # broken, not healthy, and must never count as a live run.
    # catch_up FLIPPED False->True (2026-08-13). The old comment argued its own
    # opposite — "a catch_up=True label that isn't loaded flags loudly" — and then
    # set False, which made the rail INVISIBLE to the shim: overdue_sources()
    # skips non-catch_up sources, so no catch_up_not_installed anomaly was ever
    # raised and the never-loaded plist went 97h stale in total silence while
    # jarvis_sweep and loom_fetch (both catch_up=True) were flagged within hours.
    # Safe to restart unattended: read-only IMAP, no sends, and the local write is
    # idempotent (upsert_provisional dedupes on canonical_key).
    {"key": "finance_payment_mail", "label": "Payment-mail rail (Wise/Stripe email)",
     "task_name": "finance_payment_mail", "max_age_hours": 26,
     "degrade_kinds": ("finance_payment_mail_unmatched",),
     "critical": False, "backs": "money",
     "launchd_label": "io.etherwise.v3.finance-payment-mail", "catch_up": True,
     "why": "reads Wise/Stripe payment NOTIFICATIONS read-only so a stale CSV"
            " import announces itself; a dead rail means the ledger can silently"
            " fall behind the money that actually arrived"},
    # Loom FETCH rail (2026-08-10) — weekly pull of new recordings from loom.com.
    # The whole POINT of registering it: a stalled fetcher (the exact four-month
    # freeze this rail fixes) surfaces as STALE in the brief instead of a silently
    # frozen KB. Weekly cadence => 8d deadline (7 + grace). A logged-out session is
    # a degrade signal between runs (its critical anomaly also fails the run). Not
    # critical (KB freshness, not money/pipeline); catch_up-safe (idempotent read).
    # empty_ok: a weekly rail's NORMAL healthy outcome is '0 new recordings',
    # which run_fetch records as a clean TaskSkip. Counting only 'completed'
    # would have pinned this source at 'down' forever even once it works —
    # the exact wrong-health-definition bug that let it sit unnoticed.
    # loom_fetch_empty_index: a VALID session returning an empty index is the
    # silent zero — the account has ~590 recordings, so 0 means the fetch broke,
    # not that the corpus is empty. It degrades instead of reporting 'up to date'.
    # loom_fetch_zero_ingest: the SECOND shape of the same lie (2026-08-13, run
    # 5391). The index was fine (597) and 34 new recordings were attempted, and
    # every single one failed — yet the run was `completed` and this source read
    # `ok`, because the empty-index guard only ever looked at the index. Attempted
    # > 0 with ingested == 0 is a degraded run whatever the index says.
    {"key": "loom_fetch", "label": "Loom fetch rail (new recordings)",
     "task_name": "loom_fetch", "max_age_hours": 192,   # 8d = weekly + grace
     "degrade_kinds": ("loom_fetch_logged_out", "loom_fetch_empty_index",
                       "loom_fetch_zero_ingest"),
     "critical": False, "backs": "today", "empty_ok": True,
     "launchd_label": "io.etherwise.v3.kb-loom-fetch", "catch_up": True,
     "why": "pulls new Loom walkthroughs (the richest 'what we deliver' evidence)"
            " into the KB; a stalled fetcher froze the corpus four months and must"
            " announce itself instead of silently going stale"},
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
# Where launchd agents are installed on the Mac. A label the shim is asked to
# restart but that is ABSENT from `launchctl list` was never bootstrapped — a
# one-time setup error, not a transient failure; catch-up points a human at the
# exact bootstrap command instead of retry-looping (2026-08-07: jarvis_sweep's
# plist existed but was never loaded, so the shim raised catch_up_failed 26x).
LAUNCH_AGENTS_DIR = str(Path.home() / "Library" / "LaunchAgents")

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
    # 2026-11-01 (the cockpit audit): these three were minted by rules with
    # their own names (rules.rule_upwork_waiting, rule_stalled_milestone) but
    # were NOT priced here, so canonical_kind filed every one of them as the
    # default — 'escalate_client'. Six Upwork invitations and an unfunded
    # milestone sat in Today's "Needs a decision" dressed as client escalations,
    # ranked and decayed like them. An invitation is time-boxed (Upwork expires
    # it), so it ranks high and decays fast; a milestone nobody funded is a
    # standing money fact and refires while it holds.
    "upwork_invitation": 75,
    "upwork_offer":      85,
    "stalled_milestone": 60,
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
# ── Whose promise is it? (2026-11-01, the cockpit audit) ─────────────────────
# The extractor files every "<name> will …" line on a call as who='us'. Of 711
# open "us" promises only 68 named Abhijeet; the rest were Ankita/Shruti/Yogita
# tasks (his team) and Abel/Jaap tasks (Hermon's side) — all nagging HIM. A
# line whose subject is one of these names is re-filed at insert time
# (commitments.classify_who): team names -> who='team' (kept, shown on the
# client's dossier, never on Today or the brief — his call); client-side names
# -> who='client' (awaiting them). First names, matched as whole words at the
# START of the promise or as "<name> will/to/should"; a promise ABOUT someone
# ("Send Shruti the brief") stays his. Keep these short and real.
COS_TEAM_NAMES = ("Ankita", "Shruti", "Yogita", "Sachin", "Jay", "Aastha")
COS_CLIENT_SIDE_NAMES = ("Abel", "Jaap")      # Hermon's side; add names as they recur
# Lines the AM brief shows under Commitments before "and N more on Today"
COS_BRIEF_COMMITMENT_LINES = 5
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
    "upwork_invitation": 3,       # Upwork itself closes an unanswered invite
    "upwork_offer":      3,
    "stalled_milestone": 14,      # refires from the pulse while unfunded
}
# THE CARD VOCABULARY. Every lifecycle table above is keyed by `kind`, and the
# sweep lets Opus fill `kind` in as free text (see sweep.OPUS_SCHEMA — that is
# deliberate: the model's own words are the signal `_is_cluster_card` reads).
# The bug is what happened NEXT: an invented kind matched no key in ANY of these
# tables, so a card wearing one silently escaped all of them at once —
# COS_URGENCY_BASE fell back to 25, COS_DECAY_TTL_DAYS to 14 days, and
# sweep.RULE_KINDS never reconciled it. Measured 2026-08-20: 44 of the 54 kinds
# ever stored were model inventions, and the live symptom was
# 'system_recurring' — one word off 'system' — leaving "upwork_scan pipeline
# down" on Today for 14 hours after the scan had recovered, with two weeks left
# to run. Kinds are therefore CANONICALISED at the storage choke point
# (nudges.canonical_kind, called by write_nudge); the model's original survives
# in payload.kind_raw, where it describes and controls nothing.
# Derived, never hand-listed: a kind that is not priced cannot be stored.
COS_CARD_KINDS = frozenset(COS_URGENCY_BASE)
# What an unrecognised kind becomes, by the PROVENANCE of the writer — a rail
# reporting a failure must not decay on a sales card's schedule.
COS_KIND_DEFAULT_BY_SOURCE = {
    "system":     "system",
    "capture":    "capture",
    "commitment": "commitment_due",
    "knowledge":  "stale_dossier",
}
COS_KIND_DEFAULT = "escalate_client"   # sweep correlations and anything else
# ── Quick Capture (2026-08-14) ───────────────────────────────────────────────
# A captured note is classified by a CHILD process spawned by /api/note, within
# seconds, and by the sweep as the backstop. These bound that path. See
# modules/chief_of_staff/capture.py for why each one exists.
CAPTURE_MAX_CHARS = 20000          # add_note REFUSES past this; never truncates
CAPTURE_CLASSIFY_CHARS = 4000      # …but only this much reaches the model
CAPTURE_CLAIM_TTL_MINUTES = 10     # a classifier lease this old is re-claimable
CAPTURE_MAX_ATTEMPTS = 3           # stop paying; start SHOWING him the failure
CAPTURE_CLASSIFY_TIMEOUT_S = 90    # the spawned child's own wall-clock ceiling
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
# Cross-source closure (J1.5): a Fathom call / thread update resolving an open
# ask. Metered per client per sweep; bounded + only cards not yet decided.
COS_CLOSURE_AUTO_CONF = 0.75       # >= this -> auto-close (linked to the call)
COS_CLOSURE_CHIP_CONF = 0.40       # >= this (and < auto) -> "confirm?" chip
COS_CLOSURE_MAX_CLIENTS = 8        # per-run cap on closure LLM calls
COS_CLOSURE_LOOKBACK_DAYS = 30     # only recent calls can close a card

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
# THE list ids (Upwork chat, 2026-08-25): scripts/clickup_push.py used to carry
# its own copy of these plus the ICP list that existed nowhere else. One dict,
# read by the script at call time; tests/test_score_bands.py pins the pairing.
CLICKUP_LISTS = {
    "icp": "901615852269",       # ICP / Prime — the top tier (2026-07-13)
    "hot": "901614356485",
    "standard": "901614356486",
    "low": "901614356487",
    "invites": "901614356490",
}
# Push retry window (finding 13, Upwork chat 2026-08-25): a scan that dies after
# scoring but before its push loop (08-23 23:09: ClaudeBudgetExceeded at the
# per-run cap, 20 hot jobs drafted, 0 carded) left its rows 'Drafted' with no
# card, and nothing ever came back for them — the push loop only ever saw the
# run's own verdicts. Every scan now also pushes uncarded rows scored within this
# many days; older ones are the shadow-era backlog (listings long expired) and
# stay where they are. Same horizon as the cockpit's board window.
CLICKUP_PUSH_RETRY_DAYS = 7

# ── Hours (2026-10-19) — ClickUp time into the cockpit ───────────────────────
# The eight Spaces that carry a Sprint Folder, which is the convention the
# period logic depends on (his scope decision, 2026-10-19). Excluded on
# purpose: `Template Space` (a template), `Client Projects` (24 folders, no
# sprint structure), `Client Tasks Board`, `Auto-Tasks`, `Abhijeet's Space`.
# Scope lives here and not in a reader so widening it is a reviewed change.
HOURS_SPACES = {
    "90164727594": "Horizons ABA",
    "90166003526": "Hermon",
    "90164737880": "Adelye",
    "90167875609": "Globaltize",
    "90167673606": "TCC",
    "90166841994": "homestyling",
    "90167745788": "Discover Data",
    "90166867133": "Netfly",
}
HOURS_SPRINT_FOLDERS = {
    "90164727594": "90166142809",   # Horizons ABA
    "90166003526": "90168014133",   # Hermon
    "90164737880": "90166276702",   # Adelye
    "90167875609": "901610974588",  # Globaltize
    "90167673606": "901610674888",  # TCC
    "90166841994": "90169370191",   # homestyling
    "90167745788": "901610779598",  # Discover Data
    "90166867133": "90169411810",   # Netfly
}
# Sprint list names carry no YEAR ("Horizons 21 (12/29 - 1/11)"), so the first
# list in each folder needs an anchor; everything after it is inferred from the
# month going backwards. Verify a space's oldest sprint before adding it here.
HOURS_DEFAULT_YEAR = 2026
HOURS_SPRINT_ANCHOR_YEAR = {
    "90164727594": 2025,            # Horizons 0 (7/27 - 8/2) is July 2025
}
# How far back the sync reads. Not "all time": the window is walked in
# month-sized chunks and every chunk is a request.
HOURS_SYNC_SINCE = "2025-07-01"
# A client whose space total should INCLUDE a folder outside the sprint folder.
# Hermon's Vibe Coding Projects is ~70h of a 178h sprint and is client work that
# simply is not sprint-tracked (his decision, 2026-10-19: billable, folded into
# the sprint total). The folder attribution is kept on every row so "70 of those
# hours were Vibe Coding" stays answerable without reopening ClickUp.
HOURS_FOLD_IN_FOLDERS = {
    "90169726549": "90166003526",   # Vibe Coding Projects -> Hermon
}
# Jaap Meijvogel is jaap@hermon.io — a CLIENT's person who happens to hold a
# seat in our workspace. Checking him for logging gaps would report a teammate
# who never logs time, every single period.
HOURS_GAP_EXCLUDE_USER_IDS = ("101070656",)
# Report breakdowns, per client, switched on as they ask for them. Default is
# TOTAL BILLED HOURS ONLY — most clients get a number. Keys map to the groupings
# in modules/hours/report.client_period: person | task | folder.
HOURS_REPORT_BREAKDOWNS = {
    "horizons-aba": ("task",),      # Alex asked first (2026-10-19)
}
# How many recent sprint lists to pull TASKS for. The EOW report covers the
# period being reported and the one before it; pulling tasks for all 44 of
# Horizons' sprints every night would be 44 paged reads for history that cannot
# change the current report.
HOURS_TASK_PERIODS = 3
# ClickUp status types that mean "finished". `type` is ClickUp's own
# classification, not the display name, so a client renaming "Complete" to
# "Shipped" does not silently empty the report's first section.
HOURS_DONE_TYPES = ("closed", "done")
# Statuses (lowercased display names) that mean the ball is with the CLIENT.
# These drive the report's "Needs your input" section — the one part a client
# reads for an action rather than for reassurance.
# Only statuses that EXPLICITLY name the client. "blocked" and "on hold" were in
# this list and came out of it on 2026-10-19: on Hermon's Sprint 22 report a
# task BLOCKED for our own reasons was printed under "Waiting on your review".
# Telling a client something waits on them when it does not is worse than
# leaving it in progress. Matched as substrings, so "NEEDS CLIENT REVIEW" hits.
HOURS_AWAITING_CLIENT = ("waiting on client", "awaiting client",
                         "awaiting approval", "needs approval",
                         "client review", "client approval")
# How many tasks changing status within the same MINUTE counts as a sprint
# tidy-up rather than delivery. Measured on Horizons 44 (2026-10-19): 40 of 43
# "completed" tasks shared one close timestamp. Listing those as forty things
# shipped is a claim a client checks once and then stops believing the report.
HOURS_BULK_CLOSE_MIN = 5

# ── LinkedIn (2026-10-28) — the engagement tracker into the cockpit ──────────
# Spec: etherwise-v3-linkedin-brief.md. The base is OWNED by the social
# sessions (business/social/); v3 only reads it (modules/linkedin/sync.py).
# Tables by ID, not name: an Airtable rename must not break the sync.
LINKEDIN_BASE = "applkzewYyLbRmcss"
LINKEDIN_TABLES = {
    "people": "tbl3TyfufD46ZZYWE",
    "comments": "tblWjENF13bcYNf8r",
    "messages": "tblnHZq9TvpQtUjsq",
    "candidates": "tble7hk9KHTSaG8id",
    "posts": "tbl5beYW61JtCnsxg",
    "profile": "tblMPcJyCPUaWlAl3",     # Profile snapshots, added 2026-10-28
}
# The fields the readers DEPEND on, per table. Absent on every record of a
# non-empty table = a rename or a deletion in Airtable, filed as the
# `linkedin_schema_drift` degrade so the tab cannot quietly read zeros. (An
# empty table proves nothing — Airtable omits empty cells — so it is skipped.)
LINKEDIN_REQUIRED_FIELDS = {
    "people": ("Name", "Stage", "Next action"),
    "comments": ("Commented at", "Person"),
    "candidates": ("Post", "Verdict", "Found on"),
    "posts": ("Post", "Posted at", "Impressions"),
}
# Targets, his decisions. Comments: 10 A DAY (2026-10-28, superseding the
# 10/week of 11 Oct). Posts: Aastha's written scope, 3-4 a week (7 Oct) —
# the pace bar uses the lower bound. Day-90 gate: five conversations with
# plausible buyers (CONTEXT §2), counted from her first working session.
LINKEDIN_COMMENTS_PER_DAY = 10
LINKEDIN_POSTS_PER_WEEK = 3
LINKEDIN_DAY0 = "2026-10-09"
LINKEDIN_DAY90_CONVERSATIONS = 5
# Today shows LinkedIn items found within this many days; older ones are
# counted, not listed — a LinkedIn post is cold within days.
LINKEDIN_TODAY_FRESH_DAYS = 7

# ── Job feed provider (Phase 0.1 cutover, Upwork chat 2026-08-25) ────────────
# THE switch. "chrome" = the three personalized feed tabs in his logged-in
# Chrome (scan_pipeline._fetch_chrome, the M1 sourcing invariant); "mcp" = the
# bounded standing queries against mcp.upwork.com (scan_pipeline._fetch_mcp;
# the invariant was LIFTED for MCP find_jobs on 2026-08-25, recorded in
# BUILD_BRIEF). Everything after the fetch — hard rules, scoring, drafts,
# cards, digest — is one shared tail, so the two providers can never diverge
# in what they do with a job, only in where the job came from. §5.2 of the
# Upwork brief: the providers never co-feed one store, so this is a switch,
# not a merge; the flip is this one line and so is the rollback.
# FLIPPED 2026-08-26 03:55 IST on Abhijeet's "let's flip it now" (UPWORK_BRIEF
# Next actions 1). Rollback: "chrome" — the Chrome path is still in scan_pipeline.
JOB_FEED_PROVIDER = "mcp"
# The standing queries (moved here from feeds/shadow_poll.py, where the shadow
# poller proved them): smart_search = Upwork's profile-skill matching (the
# best_matches analogue) + the term list mirroring his curated GraphQL
# expression plus the four gaps the 08-24 Test B found. n8n is deliberately
# absent — the n8n EXCLUSION is a hard rule, not a search. Terms §4.1 bounds:
# first pages only, one `get` per NEW job, a hard per-run ceiling on gets.
# Rebalanced 2026-09-08 on measured yield. Since the flip these terms fetched
# 3,948 jobs and produced TWO applications; the dead weight was concentrated:
#   claude 965 fetched -> 0 · gohighlevel 725 -> 0 · workflow automation 577 -> 0
#   automated reports 175 -> 0 · clay outreach 37 -> 0 · pipedrive 2 -> 0
# while the terms that name the platforms he actually bids on were starved
# (airtable 45 fetched, and an "airtable" job converts at 14.1%). "claude" is
# gone entirely: it was also a +2 scoring bonus, and across 182 hot cards
# carrying the word he applied to none. GoHighLevel stays — it is his own core
# stack and 2 of his applications carry it — but as one term, not a firehose.
MCP_TERM_QUERIES = (
    "airtable", "make.com", "zapier", "hubspot", "smartsuite",
    "airtable automation", "crm automation", "no code automation",
    "ai automation", "gohighlevel", "twilio", "vapi",
)
MCP_SMART_PAGES = 2            # smart_search page 1 + 2 (profile-matched)
MCP_PER_QUERY_LIMIT = 10       # server max per page anyway
MCP_MAX_GETS_PER_RUN = 25      # detail calls are the expensive half; bound them
# a search round that surfaces fewer rows than this is a broken feed, not a
# quiet market (the Chrome twin: MIN_HEALTHY_TILES per feed)
MCP_MIN_HEALTHY_SURFACED = 20
# Pacing (measured 2026-08-25 22:41): the server enforces ~12 find_jobs
# requests/min sustained, refilling continuously; a second round inside the
# minute after the :40 poll was refused with "Rate limit exceeded ... wait
# about 5 seconds". A refused query is a silently under-scanned hour, so every
# call waits this long after the previous one (a 40-call round takes ~3.5 min
# of an hourly slot) and a refused call is retried once after the advised wait.
MCP_CALL_INTERVAL_S = 5.0
MCP_RATE_LIMIT_WAIT_S = 6.0
# THE SCAN'S SLOTS, minutes past every hour. Hourly (:15) until 2026-10-18;
# then Abhijeet: "can we do scann for job posts more frequently like every 15
# or 30 minutes so that we can apply quickly", and, asked with the trade-offs
# in front of him (the hourly cadence was our own margin under the API & MCP
# Terms §4.1 line on "continuously monitor"; four times the searching moves
# toward it), he chose every 15 minutes for all 14 searches. Load: a round is
# ~17 find_jobs calls at the pace above (~1.5 min), so four an hour is ~70 of
# the ~720 an hour the tool allows; each job is still fetched and scored ONCE,
# so model spend does not move. Must match the StartCalendarInterval of
# launchd/io.etherwise.v3.upwork-scan.plist (tests/test_scan_schedule.py holds
# them together). Other readers of find_jobs keep out of these minutes
# (feeds/mcp_search.in_scan_window).
UPWORK_SCAN_MINUTES = (0, 15, 30, 45)
# THE CROWD CHECK (2026-10-23, his pick from the diagnosis: "Re-read each
# job's proposal count when the card is made and again before you apply.
# Flag 50+, and skip 150+ unless it's Make.com or Airtable."). Measured since
# 1 Aug on the jobs he applied to: under 100 proposals 7 of 32 were opened by
# the client, 100-249 1 of 8, 250+ 0 of 10; his Make.com and Airtable
# proposals were opened 6 of 22 while generic AI posts went 3 of 31 - and the
# generic ones collect 50-400 proposals in their first hours. Counts come
# from the search row when it has one, else one title search
# (feeds/mcp_search.proposal_count); modules/upwork/crowd.py decides.
CROWD_FLAG_AT = 50          # the card's name says "⚠ 50+ proposals"
CROWD_SKIP_AT = 150         # not carded, and an open card goes to `skipped`...
CROWD_EXEMPT_PATTERN = r"make\.com|integromat|airtable"   # ...unless title/skills
CROWD_RECHECK_HOURS = 48    # an open card's count is re-read for this long
CROWD_RECHECK_EVERY_MIN = 60
CROWD_RECHECK_PER_RUN = 8   # title searches per scan run (~40 s at the pace)
# At the flip, posts created BEFORE this instant are never claimed (Abhijeet,
# 2026-08-25 20:40: "only jobs posted after the flip"). The search pages at
# any moment hold ~100 jobs the scraper never saw; claiming them all would
# card ~60 stale listings over the first afternoon. ISO-8601 UTC, e.g.
# "2026-08-26T09:00:00+00:00"; None = no cut (the chrome provider ignores it).
# Set at the flip (03:55 IST, 26 Aug, on Abhijeet's "flip it now") to the
# moment of the LAST CHROME SCAN (03:15 IST): everything posted before it was
# the scraper's to find; everything after is the MCP round's — no gap, no
# stale backlog.
MCP_CLAIM_SINCE = "2026-08-25T21:45:00+00:00"
# Financials through the MCP (docs/upwork/MCP_USE_CASES.md, use cases A + B;
# modules/upwork/feeds/mcp_financials.py, task upwork_financials, daily 06:05
# IST). The look-back window of one transactions read per org — the GraphQL
# sync's 14 days; the MCP dates rows by INVOICE date and a week's hourly
# invoices are paid ~10 days later, so 14 days keeps every row in view across
# its payment. Scheduled 2026-08-26 evening on Abhijeet's "Finish use case A —
# retire the CSV import"; the fixtures under tests/fixtures/mcp/ pin the shapes.
MCP_FINANCIALS_LOOKBACK_DAYS = 14
# Upwork's own weekly summary (get_freelancer_financials summary; his OK on
# the capture 26 Aug 17:55): the rail stores every completed Mon-Sun week it
# has not stored yet, this many back, per org — the doctor's tie-out reads
# them. Two = the previous week plus one of grace for a missed morning.
MCP_SUMMARY_WEEKS = 2
# A week's invoices reach the `transactions` action days after the summary
# already counts them (the 17-23 Aug agency invoices were in the summary on
# 26 Aug and not in transactions); the tie-out reads a week only once it is
# this many days old. Upwork's review + payment cycle is ~10 days.
MCP_TIEOUT_SETTLE_DAYS = 10
# The connects usage rows the same rail reads (use case B). None = SHADOW: the
# rows are mapped and compared with the Chrome rail's (a twin = same UTC day,
# delta, job) and never written. Set a 'YYYY-MM-DD' and rows from that day on
# are written to connects_history — retire the Chrome rail (upwork_connects)
# the SAME day, never both (law 2: no mixed provenance on one day).
# 2026-08-27, on his word: the MCP owns connects from TODAY. Evidence at the
# flip: 9 twins and one "untwinned" row that was only today's Availability
# Badge charge the Chrome rail (05:35) had not run for yet — the two rails
# agreed on everything both had seen. The Chrome plist moved to
# launchd/retired/ in the same commit (install.sh globs launchd/*.plist, so a
# retired one cannot come back) and the SOURCES entry now points here.
MCP_CONNECTS_SINCE = "2026-08-27"
# Proposals through the MCP (use case C, Phase 0.2; Abhijeet 26 Aug 17:55 "use
# case C next"; modules/upwork/feeds/mcp_proposals.py, run by the hourly
# upwork_sync in place of the scope-blocked GraphQL vendorProposals pass).
# `list_freelancer_proposals list` filters ONE status per call and pages 10 at
# a time by modified time, so a round is one call per status below, newest
# first, stopping at the last modified instant the previous round stored
# (sync_cursors upwork_proposals_modified:<Status>). 'Pending' (processing)
# is transient and not read. The FIRST round has no cursor and pages back
# through history up to MCP_PROPOSALS_MAX_PAGES per status (the backlog since
# the 9 June freeze).
MCP_PROPOSAL_STATUSES = ("Accepted", "Offered", "Hired", "Activated", "Declined",
                         "Withdrawn", "Archived")
MCP_PROPOSALS_MAX_PAGES = 20
# The 0.3 status map (decision 4, 2026-08-25 — the first terminal write is
# permanent, so conservative). Upwork's status -> proposals.status. The tool's
# 'Accepted' means SUBMITTED (validated by Upwork), not accepted by the client;
# 'Activated' = an invitation accepted = an interview. Archived is NEVER
# written here: whether it is Lost or Expired needs contract/offer evidence
# (0.3). None = the row's status is left as it is.
MCP_PROPOSAL_STATUS_MAP = {
    "Hired": "Won", "Declined": "Lost", "Withdrawn": "Withdrawn",
    "Activated": "Interview",
    "Accepted": None, "Offered": None, "Archived": None, "Pending": None,
}
# The second half of the map (Abhijeet, 26 Aug 19:05: "Expired / Lost as
# proposed"), read when the proposal's own status writes nothing: the LIST a
# proposal sits in is the job-level bucket (the first live round showed the
# Hired list holding jobs someone else won, the Archived/Withdrawn lists the
# closed and gone jobs), so (list, own status) says what became of it:
# submitted and the job closed or went to someone else -> Expired (never a
# decision on us); an invitation accepted and the job closed or went to
# someone else -> Lost (we were in the room); "Archived (job closed)" ->
# Expired, or Lost when our row already says Interview (mcp_proposals turns
# an Expired into Lost on interview evidence). The Declined list without the
# status field saying so is still the client's decline -> Lost.
# Money in flight and the heartbeat (Abhijeet, 26 Aug 22:30: "money in flight
# + offers" and "the dashboard heartbeat"; modules/upwork/feeds/
# mcp_inflight.py inside the daily upwork_financials rail, and
# mcp_pulse.py every quarter hour).
#   TIMESHEET_DAYS      how far back the hours read goes each day. Upwork
#                       invoices a week on the Sunday it ends, so 14 keeps a
#                       whole unbilled week plus the one settling behind it.
#   MILESTONE_MAX_GETS  contract `get` reads per daily run (one read each):
#                       34 active contracts refresh over ~5 days.
#   CONTRACT_DETAIL_DAYS how stale a contract's milestones may get.
#   OFFER_PENDING_STATES the freelancer-side state that means "waiting on you"
#                       (the tool's client-side vocabulary differs).
MCP_TIMESHEET_DAYS = 14
MCP_MILESTONE_MAX_GETS = 8
MCP_CONTRACT_DETAIL_DAYS = 5
MCP_TIMESHEET_MAX_CALLS = 12   # window-halving budget: 14 days of entries
                               # cost 7 reads on 2026-08-26
MCP_OFFER_PENDING_STATES = ("awaiting_your_acceptance",)
# ── use case F: the message rail (0.4, 26 Aug) ───────────────────────────────
# `get_messages` inside the quarter-hour pulse. The room list is the change
# detector; a room whose newest story we do not hold is the only one whose
# messages get read.
MCP_ROOMS_PAGE = 10             # rooms per list_rooms call
MCP_ROOMS_MAX_PAGES = 3         # 30 rooms is well past his active set
MCP_MESSAGE_MAX_ROOMS = 5       # detail reads per heartbeat — a hard ceiling
MCP_MESSAGES_PAGE = 20
MCP_MESSAGES_MAX_PAGES = 3
# Upwork writes SYSTEM stories stamped with from_self=true. Counted as his own
# reply they would clear an owed-reply card he never answered, so any story
# whose text carries one of these markers is direction 'system'. Blunt on
# purpose: a missed outbound only leaves a card up longer, a false outbound
# tells him he replied when he did not.
MCP_MESSAGE_SYSTEM_MARKERS = ("System event:", "Sender's note:")
# A contract Upwork still calls ACTIVE with no message, no money and no logged
# hour for this long. Upwork never closes one on its own, so an abandoned
# engagement stays open for years and inflates every "committed work" reading —
# 17 of his 35 on 2026-08-27, the oldest quiet since 2023.
MCP_CONTRACT_IDLE_DAYS = 90
IDLE_CONTRACTS_CARD_MIN = 3     # below this it is not worth a card
# ── Phase 3: the hiring side (2026-08-27, his "In — the applicant pull") ─────
# His OWN postings on the CLIENT org and the people bidding on him. The list
# call already carries the whole funnel, so "how many applied" costs one read.
MCP_POSTINGS_PAGE = 10
MCP_POSTINGS_MAX_PAGES = 3          # 25 postings live; 30 is the whole history
MCP_APPLICANTS_PAGE = 10
MCP_APPLICANTS_MAX_PAGES = 3
MCP_APPLICANT_MAX_POSTINGS = 5      # detail reads per run, hard ceiling
# get_rate_insights REFUSES the freelancer org; the client org answers. The
# market does not move in a day, so a posting's range is re-read this rarely.
MCP_RATE_INSIGHT_DAYS = 30
MCP_RATE_MAX_READS = 3
# A posting with people waiting on him. 2026-08-27: the LinkedIn Ghostwriter
# post had 9 applicants, 4 never opened, 6 shortlisted and 0 offered.
HIRING_CARD_MIN_APPLICANTS = 1
# Contracts through the MCP (use case D, Phase 0.3; Abhijeet 26 Aug 19:18):
# `list_contracts search` on the PERSONAL org lists every contract he holds,
# the agency's included (the agency org refuses the search). Once a day
# inside the hourly sync (sync_cursors upwork_contracts_day): ACTIVE and
# PAUSED in full, CLOSED narrowed to contracts ended in the last
# MCP_CONTRACTS_CLOSED_DAYS. 34 active contracts = 4 pages of 10.
MCP_CONTRACT_STATUSES = ("ACTIVE", "PAUSED", "CLOSED")
MCP_CONTRACTS_CLOSED_DAYS = 45
MCP_CONTRACTS_MAX_PAGES = 20
MCP_PROPOSAL_LIST_MAP = {
    ("Archived", "Accepted"): "Expired", ("Withdrawn", "Accepted"): "Expired",
    ("Hired", "Accepted"): "Expired", ("Declined", "Accepted"): "Lost",
    ("Archived", "Activated"): "Lost", ("Withdrawn", "Activated"): "Lost",
    ("Hired", "Activated"): "Lost", ("Declined", "Activated"): "Lost",
    ("Archived", "Archived"): "Expired", ("Withdrawn", "Archived"): "Expired",
}

# ── Upwork ───────────────────────────────────────────────────────────────────
UPWORK_USER_ID = "1647672547786633216"
UPWORK_ORGS = {
    "personal": "1647672547786633217",
    "client":   "1715797263477194752",
    "agency":   "1910033523997712724",
}
ACE_IDS = {"personal": "70275816", "agency": "121356955"}
PROFILE_RATE_USD = 51.50       # read from the profile 2026-08-26; was 32.50
BID_RATE_USD = 50.00           # standard hourly bid (proposal-writing master reference)
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
# Upwork tab (Phase 1, docs/upwork/PHASE1_TAB_SPEC.md §3.2/§3.4, 2026-08-25):
# the cohort funnel's baseline is POOLED over the completed periods before the
# current one, and a conversion is typeset as SIGNAL only when its denominator
# reaches the small-N line (Upwork brief law 6) — below it the % is printed
# muted with its n and no trend arrow.
UPWORK_BASELINE_WEEKS = 8
UPWORK_BASELINE_MONTHS = 3
UPWORK_SIGNAL_MIN_N = 30
# Prospect mail ↔ Upwork room (2026-08-26, Abhijeet: "link it to the
# conversation and use it"): an email whose sender matches the counterparty of
# an Upwork room active within this many days is queued (never excluded as
# no-client-signal), linked in room_links, shown on the conversation row and
# handed to the draft-writer. The match is deterministic — Upwork shows clients
# as "First L.", so first name + surname initial against the mail's display
# name; an ambiguous match (two rooms, two people) links nothing.
UPWORK_ROOM_LINK_DAYS = 30
UPWORK_ROOM_LINK_MAIL_CHARS = 1500   # of a linked email's body the draft-writer sees
# Promises typed on Upwork become tasks on Today (modules/upwork/promises.py,
# Abhijeet 2026-08-26 05:20: "we told David we will let him know — that should
# be on Today because it's a task"). The phrase gate decides which outbound
# messages the extractor reads at all; an undated promise is due
# UPWORK_PROMISE_DAYS after it was made; the first run looks back
# UPWORK_PROMISE_LOOKBACK_DAYS, never the whole history.
UPWORK_PROMISE_PHRASES = (
    r"\b(?:i|we)\s*(?:'ll|will|shall|can|am going to|are going to)\b",
    r"\blet (?:you|him|her|them) know\b", r"\bget back to you\b",
    r"\bsend (?:you|it|them|over|across|the|a|an)\b", r"\bshare (?:it|the|a|an|with)\b",
    r"\b(?:by|before|within) (?:tomorrow|tonight|today|monday|tuesday|wednesday|thursday"
    r"|friday|saturday|sunday|eod|eow|end of|the end of|next|this|an? |\d)",
    r"\bkeep you (?:posted|updated)\b", r"\bfollow up\b", r"\bcircle back\b",
)
UPWORK_PROMISE_DAYS = 2              # an undated promise is due this many days later
UPWORK_PROMISE_LOOKBACK_DAYS = 7     # first run: only promises this recent
UPWORK_PROMISE_MAX_PER_RUN = 12      # extractor calls per poll; the rest wait for the next
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
