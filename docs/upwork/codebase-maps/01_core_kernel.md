---

# Etherwise OS v3 — `core/` Kernel Map (read-only snapshot the `etherwise-v3/` repo, 2026-08-24)

Guardrail state: `.claude/ALLOW_CORE_WRITES` **exists** (build-phase marker, mode `r--r--r--`) ⇒ agent writes to `core/`, `.claude/`, `scripts/`, `rails/REGISTRY.md` are currently ALLOWED by the hook; the marker file itself is always write-denied (`core/guardrails.py:119-121`). Manual: `CLAUDE.md` (conventions at lines 17-27).

---

## 1. Kernel contracts

### 1.1 Task definition and execution — `core/runner.py`

There is **no decorator and no central task registry**. A task is a plain callable `fn(ctx) -> Optional[dict]` handed to:

```python
run_task(task_name: str, fn, module: str, db_path=None, stagger=False, _sleep=time.sleep) -> RunResult   # runner.py:218-289
```

Every scheduled entrypoint follows one pattern (`TASK_NAME` module constant + `main()` that calls `run_task` and returns an exit code): `modules/upwork/sync_state.py:28,468` (`"upwork_sync"`, module `"upwork"`), `scan_pipeline.py:37,384` (`"upwork_scan"`), `connects_history.py:39,257` (`"upwork_connects"`), `poll_inbox.py:28,376` (`"upwork_inbox"`), `outcome_capture.py:23,108` (`"upwork_outcomes"`), `feeds/shadow_poll.py:37,219` (`"mcp_shadow_poll"`, module **`"system"`**, not `"upwork"`), `bin/crm-mirror:28`, `bin/notify:20` (module `"kernel"`), `bin/catch-up:47,289`.

**Exceptions** (runner.py):
- `TaskSkip` (49) — clean empty skip → `runs.status='skipped_empty'`, `metrics_json={"skip_reason": str(exc)}` (271-276). Not an anomaly.
- `ShadowViolation` (53) — raised by `ctx.require_live()` when module is shadowed.
- `MigrationsPending(RuntimeError)` (57-61) — raised at startup if `db.pending_migrations()` is non-empty (252-258); run is marked failed + critical anomaly, then re-raised. Not retryable.
- `_RETRYABLE = (gw.TransientAPIError, ConnectionError, TimeoutError)` (66) — the whole task is re-invoked with backoff `RUNNER_BACKOFF_BASE_SECONDS * 2**(attempt-1)` (281) up to `RUNNER_MAX_ATTEMPTS` (278); so **tasks must be idempotent**. Any other exception fails immediately (285-287).

**`RunResult`** (70-74): `run_id`, `status ∈ {completed, failed, skipped_empty, skipped_locked}`, `metrics`, `error`.

**`RunContext`** (77-120) — what `fn` receives:
- `task_name, run_id, module, shadow: bool, db_path` (79-83)
- `ctx.log(msg)` (85) → `_log` (125-130): appends `[HH:MM:SS IST] <task> | <msg>` to `config.LOG_DIR / 'YYYY-MM-DD.log'` (IST daily file).
- `ctx.claude(**kw)` (88-91) → `claude_call` with `task_name`/`db_path` defaulted.
- `ctx.record_shadow_write(target, operation, entity, entity_key, payload)` (93-102) → `INSERT INTO shadow_ledger (... diff_status='pending')` bound to `run_id`.
- `ctx.require_live(action)` (104-108) → `ShadowViolation` if `ctx.shadow`.
- `ctx.anomaly(kind, severity, detail: dict)` (110-120) → `INSERT INTO anomalies (task_name, kind, detail_json, severity, run_id)` **without failing the run** (`severity='critical'` reaches the interrupt email; `'warn'` stays in cockpit/briefs). This is how a rail declares itself DEGRADED (see §4.3).

**`run_task` control flow** (218-289):
1. `stagger=True` → `_sleep(_stagger_seconds(task_name))` (226); `_stagger_seconds` = `int(sha256(task_name),16) % RUNNER_STAGGER_MAX_SECONDS` (159-163).
2. `shadow = config.SHADOW_MODE.get(module, True)` (229) — **unknown module ⇒ shadow ON**.
3. `_acquire_lock` (135-149): `flock(LOCK_EX|LOCK_NB)` on `config.LOCK_DIR/<task_name>.lock`, writes `pid isoformat`. If held: a `runs` row with `status='skipped_locked'`, `duration_ms=0` is written (236-239) and `RunResult(run_id,"skipped_locked")` returned — `fn` never runs.
4. `_insert_run` (168-176): `INSERT INTO runs (task_name, started_at, status='running', shadow, config_sha256)` — `started_at` is IST isoformat(seconds), `config_sha256 = config.config_sha256()`.
5. `gw.reset_run()` (243) zeroes the per-run spend accumulator.
6. Migration guard (252-258).
7. Attempt loop (260-287); on success `_finish_run(... "completed", metrics or {})` (179-189: `UPDATE runs SET status, completed_at (IST iso), duration_ms, metrics_json, error`).
8. `finally: _release_lock` (288-289).

**Failure routing** `_fail` (292-297) → `_finish_run(status='failed', error=repr(exc))` + `_route_failure` (202-213): `INSERT INTO anomalies (kind='task_failure', severity='critical', detail_json={run_id, error[:1000], ist_date}, run_id)`. `core/notify.py` drains those into the interrupt email.

`last_success(task_name, db_path)` (192-199): `completed_at` of the latest `status='completed'` run (catch-up helper).

**`claude_call(task_name, model_key, db_path=None, _sleep, **gw_kwargs)`** (302-332): resolves `config.MODELS[model_key]`, builds chain `[None] + config.MODEL_FALLBACKS[primary]` (311), tries each model up to `RUNNER_MAX_ATTEMPTS` on `TransientAPIError` with the same backoff; `ClaudeBudgetExceeded` re-raised immediately (321-322); any other `GatewayError` is not caught ⇒ propagates on first occurrence.

### 1.2 `runs` row semantics

| column | written by |
|---|---|
| `task_name, started_at, status='running', shadow (0/1), config_sha256` | `_insert_run` (168-176) |
| `status ∈ completed/failed/skipped_empty/skipped_locked, completed_at, duration_ms, metrics_json, error` | `_finish_run` (179-189) |
| `anomalies_json` | **never written by the runner** (column exists at `001_ops.sql:15`) |

`001_ops.sql:12` comment lists only `running|completed|failed|skipped_empty`; `skipped_locked` is runner-only (runner.py:236-239). Timestamps in `runs` are **IST with offset** (`+05:30`); most other tables default to `datetime('now')` = **naive UTC** — `source_health._parse` (source_health.py:51-66) documents this split.

### 1.3 How launchd invokes tasks

- Plists live in `launchd/*.plist` (11 in this snapshot: catch-up, cockpit, cos-sweep, crm-mirror, shadow-diff, upwork-connects, upwork-inbox, upwork-mcp-shadow, upwork-outcomes, upwork-scan, upwork-sync). Two invocation shapes:
  - module form: `ProgramArguments = [Framework Python 3.9, "-m", "modules.upwork.sync_state"]` (`io.etherwise.v3.upwork-sync.plist:9-14`), `StartCalendarInterval Minute=45`.
  - bin form: `[Framework Python 3.9, "/…/etherwise-v3/bin/upwork-mcp-shadow"]` (`io.etherwise.v3.upwork-mcp-shadow.plist:21-25`), `Minute=40`.
  - Both: `EnvironmentVariables PYTHONPATH=<repo>`, `StandardOut/ErrorPath=~/Library/Logs/Etherwise-v3/<name>.log`, `RunAtLoad=false`, **no WorkingDirectory** (plist comment at upwork-sync.plist:6-8: required or launchd exits 78).
- `bin/*` scripts are `#!/usr/bin/env python3`, `sys.path.insert(0, repo)`, then `raise SystemExit(<module>.main())` (`bin/upwork-mcp-shadow:12-19`).
- Install: `bash launchd/install.sh` copies to `~/Library/LaunchAgents` + `launchctl bootstrap gui/<uid>` (install.sh:46-85) and then **verifies every `config.SOURCES[*]['launchd_label']` is loaded** (install.sh:90-120, exits 1 if any missing).
- Missed windows (Mac asleep) are re-kicked by `bin/catch-up` using the SOURCES registry (config.py:894-914).

### 1.4 `core/db.py`

- `connect(db_path=None) -> sqlite3.Connection` (18-25): `config.ensure_dirs()`; `sqlite3.connect(db_path or config.DB_PATH, timeout=10)`; `row_factory = sqlite3.Row`; `PRAGMA journal_mode=WAL`; `PRAGMA foreign_keys=ON`; `PRAGMA busy_timeout=10000`. Returns a raw connection — `with connect() as conn:` uses sqlite3's context-manager semantics (**commit on clean exit, rollback on exception, connection NOT closed**). Python's default implicit transactions apply (BEGIN before DML; no explicit isolation set). House rule (db.py:3-6, CLAUDE.md:21): tight with-blocks, Claude calls outside them, canonical-key lookup before insert.
- `applied_migrations(conn)` (28-33): creates `schema_migrations(version TEXT PK, applied_at)` if missing.
- `pending_migrations(db_path)` (36-44): on-disk `*.sql` stems (lexical) minus applied.
- `migrate(db_path)` (47-59): for each pending file in lexical order `conn.executescript(text)` then `INSERT INTO schema_migrations`. **No per-migration transaction wrap**: `executescript` commits any pending transaction first and runs statements sequentially, so a script that fails mid-way leaves partial DDL applied and the version unrecorded. Later migrations therefore use `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` (010+, 014+, 017+); new migrations must be additive and re-runnable.
- Apply: `PYTHONPATH=. python3 -m core.db` (62-63).

---

## 2. `core/config.py` map (1099 lines)

### 2.1 Section index

| lines | section | constants |
|---|---|---|
| 18-37 | Paths | `V3_ROOT`19 `WORKSPACE_ROOT`20 `V2_ROOT`21 `CONFIG_DIR`23 `CLIENTS_REGISTRY`24 `VAR_DIR`25 `DB_PATH`26 `LOG_DIR`27 `BACKUP_DIR`28 `LOCK_DIR`29 `KNOWLEDGE_DIR`30 `KNOWLEDGE_INBOX`31 `CREDENTIALS_DIR`36 (env `ETHERWISE_CREDENTIALS`, default `V2_ROOT/.credentials`) `V2_DB_PATH`37 |
| 39-55 | Models | `MODELS`42-49 `MODEL_FALLBACKS`50-53 `FORBIDDEN_SAMPLING_PARAMS`54 `STRUCTURED_OUTPUT_UNSUPPORTED`55 |
| 57-61 | Billing | `BILLING_DEFAULT_ROUTE`60 (env `ETHERWISE_BILLING`, default `"payg"`) `BILLING_FALLBACK_TO_PAYG`61 |
| 63-91 | Budget ceilings | see 2.2 |
| 93-105 | Claude API | `PRICING`97-102 `CACHE_TTL`103 `CLAUDE_TIMEOUT_SECONDS`104 |
| 107-110 | Runner | `RUNNER_MAX_ATTEMPTS`108 `RUNNER_BACKOFF_BASE_SECONDS`109 `RUNNER_STAGGER_MAX_SECONDS`110 |
| 112-123 | Deprecation/calendar | `DEPRECATION_WATCH`113-117 `CALENDAR_WATCH`118-123 |
| 125-157 | Shadow mode | `SHADOW_MODE`129-156 `SCHEDULE_OFFSET_MIN`157 |
| 159-167 | Time & cadence | `TZ`160 `WORK_START`161 `WORK_END`162 `BRIEF_TIMES`163 `SWEEP_INTERVAL_HOURS`164 `INTERRUPT_POLICY`165 `HOT_LEAD_THRESHOLD`166 `LOOM_FLAG_THRESHOLD`167 |
| 169-211 | Proposal evidence + founder intent + Today screen | `EVIDENCE_*`173-185 `APPLICATIONS_PAUSED_SINCE`199 `STALE_INTENT_*`203-204 `TODAY_*`205-207 `REPLY_LAG_DAYS`211 |
| 213-291 | Client revenue attribution | `CLIENT_CANONICAL`224-287 `CLIENT_PASSTHROUGH_PAYERS`291 |
| 292-298 | ICP / Prime | `ICP_*` |
| 300-307 | Rate-unstated gate | `RATE_UNSTATED_*` |
| 309-345 | Stripe | `STRIPE_DEFAULT_CLIENT`345 |
| 347-393 | Finance payment-mail rail | `PAYMENT_MAIL_*`352-372 `PAYER_ALIASES`378-393 |
| 395-411 | Jarvis sweep | `JARVIS_SWEEP_*`399-401 `JARVIS_RESWEEP_ANOMALY_THRESHOLD`409 `JARVIS_LOOM_MAX_PROPOSALS`410 |
| 412-448 | Loom classifier | `JARVIS_LOOM_*`417-423 `LOOM_FINGERPRINTS_PATH`448 |
| 449-459 | Loom fetch | `LOOM_*`455-458 |
| 460-509 | IMAP rail | `JARVIS_IMAP_*`468-509 `GMAIL_APP_PASSWORDS_FILE`497 |
| 510-536 | Slack rail | `JARVIS_SLACK_*`518-536 |
| 537-638 | Internal people | `PAYROLL_FILE`572 `INTERNAL_PEOPLE_EXTRA`574-586 `_payroll_people()`589-607 `PAYROLL_READ_ERROR`610 `INTERNAL_PEOPLE`612 `JARVIS_SLACK_SELF_AUTHORS`619 `is_internal_person()`622-638 |
| 639-658 | EXTRACT stage | `JARVIS_EXTRACT_*`643-646 `EXTRACT_FOUNDER_SCOPE_TERMS`650-658 |
| 660-706 | Self-noise | `SELF_SENDERS`664-667 `SELF_SENDER_DOMAINS`668 `OWNER_IDENTITIES`675-682 `SELF_SUBJECT_PATTERNS`683-706 |
| 708-892 | **Source-health registry** | `SOURCES`726-892 |
| 894-914 | Catch-up | `CATCH_UP_COOLDOWN_MIN`901 `CATCH_UP_STAGGER_SEC`902 `CATCH_UP_MAX_CONSECUTIVE_FAILURES`908 `LAUNCH_AGENTS_DIR`914 |
| 916-1014 | Chief of Staff / cards / capture | `COS_*`919-1014 `CAPTURE_*`989-993 |
| 1016-1040 | Airtable | `AIRTABLE_BASE`1017 `AT`1018-1033 `AIRTABLE_TYPECAST`1034 `AIRTABLE_BATCH_SIZE`1035 `AT_BASE_PAYROLL/HR/FINANCES`1038-1040 |
| 1042-1049 | ClickUp | `CLICKUP_FOLDER_UPWORK`1043 `CLICKUP_LISTS`1044-1049 |
| 1051-1068 | Upwork | `UPWORK_USER_ID`1052 `UPWORK_ORGS`1053-1057 `ACE_IDS`1058 `PROFILE_RATE_USD`1059 `HARD_FLOOR_*`1060-1061 `GATE_*`1066-1068 |
| 1070-1072 | Email | `NOTIFY_EMAIL`1071 `SMTP_FROM`1072 |
| 1074-1084 | Cockpit | `COCKPIT_HOST`1075 `COCKPIT_PORT`1076 `COCKPIT_TOKEN_FILE`1077 `cockpit_token()`1080-1084 |
| 1087-1089 | Finance targets | `MONTHLY_REVENUE_TARGET_USD`1089 |
| 1091-1099 | Integrity | `config_sha256()`1092-1094 `ensure_dirs()`1097-1099 |

### 2.2 Verbatim constants relevant to an Upwork tab / Upwork sync tasks

**Upwork identity + scoring gates (1051-1068)**
```
UPWORK_USER_ID = "1647672547786633216"                                   # 1052
UPWORK_ORGS = {"personal": "1647672547786633217", "client": "1715797263477194752", "agency": "1910033523997712724"}   # 1053-1057
ACE_IDS = {"personal": "70275816", "agency": "121356955"}               # 1058
PROFILE_RATE_USD = 32.50                                                 # 1059
HARD_FLOOR_HOURLY = 30.0        # v4.9: NO exceptions                    # 1060
HARD_FLOOR_FIXED = 500.0        # v4.9: NO exceptions                    # 1061
GATE_HOURLY_EXEMPT_USD = 45.0                                            # 1066
GATE_FIXED_EXEMPT_USD = 3000.0                                           # 1067
GATE_WHALE_SPEND_USD = 50000.0                                           # 1068
```
**Thresholds (159-167, 292-307)**
```
HOT_LEAD_THRESHOLD = 16                                                  # 166
LOOM_FLAG_THRESHOLD = 22                                                 # 167
INTERRUPT_POLICY = ("hot_lead", "system_failure")                        # 165
ICP_HOURLY_USD = 51.0 ; ICP_FIXED_USD = 5000.0 ; ICP_CLIENT_SPEND_USD = 50000.0 ; ICP_SPEND_PER_HIRE_USD = 2000.0   # 295-298
RATE_UNSTATED_MIN_CLIENT_SPEND_USD = 5000.0 ; RATE_UNSTATED_MIN_HIRES = 1   # 306-307
```
Score bands used by the eval/cockpit: `>=22`, `16-21`, `12-15`, `8-11`, `<8` (`core/evals/scoring_eval.py:174-185`); gated jobs cap at `min(raw_total, 15)` (scoring_eval.py:95-96).

**Proposal evidence / Today (169-211)**
```
EVIDENCE_MIN_GRADE = 2 ; EVIDENCE_MIN_FACTS = 3 ; EVIDENCE_MAX_FACTS = 8 ; EVIDENCE_PER_TOOL = 3        # 173-176
EVIDENCE_MEASURED_MAX_AGE_DAYS = 365 ; EVIDENCE_MAX_AGE_DAYS = 1095 ; EVIDENCE_FREE_INTEGER_MAX = 12   # 180-185
APPLICATIONS_PAUSED_SINCE = None            # 199 (None = applications ACTIVE, resumed 2026-08-07, 10/week)
STALE_INTENT_WINDOW_DAYS = 14 ; STALE_INTENT_MIN_EVENTS = 2                                             # 203-204
TODAY_BACKLOG_TOPN = 8 ; TODAY_FRESH_LEAD_DAYS = 10 ; TODAY_QUEUE_TOPN = 12 ; REPLY_LAG_DAYS = 3        # 205-211
```
**SHADOW_MODE per module (129-156)**
```
"upwork": False           # LIVE since 2026-07-06 (scan->ClickUp cards + hot-lead digest email)
"cockpit": False          # local-only
"knowledge": True
"chief_of_staff": True
"finance": True
"crm": False              # LIVE 2026-07-03 (Proposals+Contracts mirror)
"crm_transactions": False # LIVE 2026-07-03
"system": False           # bin/catch-up; zero external writes
SCHEDULE_OFFSET_MIN = 15  # 157
```
**Schedules / cadences** — config holds only informational times; real cadence is in plists. `WORK_START = time(12,0)` `WORK_END = time(4,0)` `BRIEF_TIMES = (time(11,0), time(20,0))` `SWEEP_INTERVAL_HOURS = 2` (161-164); `JARVIS_SWEEP_TIMES = ("11:00","19:00","00:00")` (399); `JARVIS_SLACK_TIMES = ("11:00","19:00")` (518). Upwork rails per SOURCES comments: `upwork_sync` hourly at :45 (760; plist Minute=45), `crm_mirror` hourly at :50 (766), scanner max_age 3h (737; plist Minute=15 per mcp-shadow plist comment), connects daily (728), mcp_shadow_poll hourly at :40 (plist only).

**Budget (63-91)**
```
DAILY_SOFT_LIMIT_USD = 4.00 ; DAILY_HARD_LIMIT_USD = 8.00 ; MONTHLY_SOFT_LIMIT_USD = 80.00   # 81-83
PER_RUN_MAX_USD = 0.50 ; PER_RUN_MAX_TOOL_CALLS = 40 ; PER_RUN_MAX_OUTPUT_TOKENS = 8_000     # 84-86
BUDGET_RESERVE_USD = 1.00 ; BUDGET_PRIORITY_PREFIXES = ("upwork_", "scan_jobs", "poll_inbox") # 90-91
```
(Only task names starting with those prefixes get the full daily cap — `claude_gateway.py:302-305`. `mcp_shadow_poll` does not qualify; it makes no model calls today.)

**Models / runner (42-55, 108-110)**: `MODELS = {"scoring": "claude-sonnet-4-6", "classify": "claude-haiku-4-5-20251001", "drafting": "claude-sonnet-4-6", "extract": "claude-sonnet-4-6", "reasoning": "claude-opus-4-8", "architect": "claude-fable-5"}`; `MODEL_FALLBACKS = {"claude-opus-4-8": ["claude-sonnet-4-6"], "claude-sonnet-4-6": ["claude-haiku-4-5-20251001"]}`; `RUNNER_MAX_ATTEMPTS = 3`, `RUNNER_BACKOFF_BASE_SECONDS = 30`, `RUNNER_STAGGER_MAX_SECONDS = 120`; `CACHE_TTL = "1h"`, `CLAUDE_TIMEOUT_SECONDS = 120`.

**Connects**: no dedicated constants. Only the SOURCES entry `connects_rail` (727-735): `task_name "upwork_connects"`, `max_age_hours 26`, `degrade_kinds ("connects_empty_window",)`, `critical True`, `backs "applied"`, `launchd_label "io.etherwise.v3.upwork-connects"`, `catch_up True`.

**ClickUp (1042-1049)**: `CLICKUP_FOLDER_UPWORK = "90169179275"`; `CLICKUP_LISTS = {"hot": "901614356485", "standard": "901614356486", "low": "901614356487", "invites": "901614356490"}`.

**Airtable (1016-1040)**: `AIRTABLE_BASE = "appgE1QoEOXvbrUE4"`; `AT["proposals"]="tbloRHzMBbryPr090"`, `AT["contracts"]="tblkvAaajHAJxftKF"`, `AT["transactions"]="tblW515kregBO9WCE"`, `AT["clients"]="tbl9hhPhjmLyiLgj4"`, `AT["people"]="tbly8rJyCZ3z0k1p7"`, `AT["communications"]="tblCwxCS6fJMhFiUC"`, `AT["active_clients"]="tblOJIsZ8tojD9F3l"`, `AT["leads_pipeline"]="tblBJQFU5kANIMJGZ"`, `AT["weekly_metrics"]="tbllt8szMWsXViw9t"`, `AT["testimonials"]="tblV85CBqWB9lvRfx"`, `AT["roi_snapshots"]="tblF7PmsokukIUJOy"`, `AT["employees"]="tblPBSY9dZCtNfiYy"`; `AT["nudges"|"commitments"|"run_health"|"eval_results"] = None` (1032, unbuilt); `AIRTABLE_TYPECAST = True`; `AIRTABLE_BATCH_SIZE = 10`.

**Notify/email (1070-1072)**: `NOTIFY_EMAIL = "contact@etherwise.io"`, `SMTP_FROM = "contact@etherwise.io"`.

**Cockpit (1074-1084)**: `COCKPIT_HOST = "127.0.0.1"`, `COCKPIT_PORT = 5051` (v2 holds 5050), `COCKPIT_TOKEN_FILE = CREDENTIALS_DIR / "cockpit-token.txt"`, `cockpit_token()`. **No tab list in config** — tabs are the `GET_ROUTES` dict (`modules/cockpit/server.py:33-70`: `/api/today`, `/api/pipeline`, `/api/clients`, `/api/knowledge`, `/api/system`, `/api/money`, `/api/nudges`, `/api/queue`, `/api/review/*`, `/api/activity*`, drill-downs) + `POST_ROUTES` (`server.py:141-160`) + `modules/cockpit/static/index.html`.

**Feed / provider flags**: `scored_jobs.feed_source` values are documented only in `002_sales_crm.sql:19` (`most_recent|best_matches|my_feed|firehose|curated|previousClients`). **There are zero MCP constants in config.py** (grep for `MCP`/`mcp` returns nothing). The MCP shadow poll's bounds are hardcoded in `modules/upwork/feeds/shadow_poll.py:39-47` (`TERM_QUERIES`, `SMART_PAGES = 2`, `PER_QUERY_LIMIT = 10`, `MAX_GETS_PER_RUN = 25`) and its cadence only in the plist comment — an existing deviation from CLAUDE.md:19 ("ceilings, schedules: core/config.py ONLY"). Do not copy that precedent; put new MCP bounds in config.

**Watch / stale items (not TODOs, but time-sensitive)**: `DEPRECATION_WATCH` Haiku review date `2026-08-15` (114) and both `CALENDAR_WATCH` dates (119, 122) are **past today's date** ⇒ `check_calendar` emits WARN. `MODELS["extract"]` comment (46): swap to "Sonnet 5" when an id exists (~Sept 1). The only "UNVERIFIED" text (314) says the flag is retired (CONFIRMED 2026-08-14). `PAYROLL_FILE` (572) is outside the repo (`WORKSPACE_ROOT/Payroll/employees.json`); in this snapshot it is missing ⇒ `PAYROLL_READ_ERROR` set and `INTERNAL_PEOPLE` = extras only.

---

## 3. Schema (migrations 001–027)

`NN` = NOT NULL, `now` = `DEFAULT (datetime('now'))` (naive UTC). PK = primary key.

### 3.1 Ops (kernel)

| table | migration | columns | indexes | purpose |
|---|---|---|---|---|
| `runs` | 001:7-19 | `id INTEGER PK AUTOINCREMENT`, `task_name TEXT NN`, `started_at TEXT NN`, `completed_at TEXT`, `status TEXT NN DEFAULT 'running'`, `shadow INTEGER NN DEFAULT 1`, `metrics_json TEXT`, `anomalies_json TEXT`, `error TEXT`, `duration_ms INTEGER`, `config_sha256 TEXT` | `idx_runs_task_time(task_name, started_at DESC)` 001:20 | one row per task invocation |
| `claude_usage` | 001:23-40; `run_id INTEGER REFERENCES runs(id)` 002:11 | `id`, `called_at TEXT NN`, `ist_date TEXT NN`, `task_name NN`, `purpose`, `model NN`, `billing_route NN`, `input_tokens/output_tokens/cache_creation_tokens/cache_read_tokens INTEGER DEFAULT 0`, `total_cost_usd REAL DEFAULT 0`, `duration_ms`, `tool_calls INTEGER DEFAULT 0`, `stop_reason`, `error`, `run_id` | `idx_usage_date(ist_date)` | every Claude call incl. failures (gateway never fills `run_id`) |
| `audit_log` | 001:44-55 | `id`, `at NN now`, `actor NN`, `entity NN`, `entity_id`, `field`, `old_value`, `new_value`, `source`, `note` | — | field-level change history; hooks write `source='hook'` |
| `anomalies` | 001:58-66; `run_id INTEGER` 015:13 | `id`, `detected_at NN now`, `task_name NN`, `kind NN`, `detail_json`, `severity NN DEFAULT 'warn'`, `resolved_at`, `run_id` | `idx_anomalies_run(run_id, kind) WHERE run_id IS NOT NULL` 015:15 | tripwires; `critical` ⇒ interrupt email; degrade kinds bound to `run_id` |
| `sync_cursors` | 001:69-73 | `name TEXT PK`, `value TEXT NN`, `updated_at NN now` | — | per-task incremental positions (upsert pattern at notify.py:94-98) |
| `shadow_ledger` | 001:76-88 | `id`, `at NN now`, `run_id REFERENCES runs(id)`, `task_name NN`, `target NN`, `operation NN`, `entity`, `entity_key`, `payload_json NN`, `diff_status` | `idx_shadow_diff(task_name, diff_status)` | intended external writes recorded instead of executed |
| `quarantine` | 001:91-99 | `id`, `at NN now`, `source NN`, `reason NN`, `payload_json NN`, `resolved_at`, `resolution` | — | nothing silently dropped |
| `schema_migrations` | db.py:29-32 | `version TEXT PK`, `applied_at NN now` | — | applied versions |
| `events` | 017:15-37; `extracted_at` 021:13; `direction` 022:11 | `id TEXT PK` (`evt:<source>:<native_id>`), `source NN`, `native_id NN`, `ts`, `author`, `subject`, `client_id`, `state NN CHECK IN ('mapped','queued','held','excluded')`, `text`, `permalink`, `raw_ref`, `sweep_run_id`, `proposed_client_id`, `proposal_confidence REAL`, `proposal_evidence`, `exclude_reason`, `resolved_by`, `resolved_at`, `first_seen_at NN now`, `updated_at NN now`, `extracted_at`, `direction` | `idx_events_state`, `idx_events_client`, `idx_events_run(sweep_run_id)`, `idx_events_proposed` partial 017:39-43; `idx_events_extracted(state, extracted_at)` 021:14; `idx_events_direction(client_id, direction)` partial 022:13 | Jarvis normalized ingestion ledger (email/slack/upwork/fathom) |

### 3.2 Upwork pipeline

| table | migration | columns | indexes | purpose |
|---|---|---|---|---|
| `scored_jobs` | 002:13-36; `icp_tier TEXT` 014:13; `draft_evidence_json TEXT` 026:23 | `id TEXT PK` (bare numeric marketplaceJobPostingId), `title`, `description`, `created_dt`, `fetched_at`, `feed_source`, `contract_type`, `hourly_min/hourly_max/fixed_budget/weekly_budget REAL`, `total_applicants INTEGER`, `experience_level`, `engagement`, `engagement_duration`, `category`, `subcategory`, `skills_json`, `client_company`, `client_country`, `client_total_spent REAL`, `client_hires INTEGER`, `client_rating REAL`, `client_payment_verified INTEGER`, `applied INTEGER DEFAULT 0`, `score INTEGER`, `score_breakdown_json`, `hard_rule_skip`, `draft_proposal`, `draft_word_count`, `clickup_task_id`, `clickup_task_url`, `airtable_record_id`, `loom_flag INTEGER DEFAULT 0`, `status` (`New|Skipped|Scored|Drafted|ClickUp Created|Repost|Fetched|Phantom`), `score_override INTEGER`, `manual_note`, `job_url`, `first_scored_at`, `updated_at NN now`, `icp_tier` (`icp|icp_near|NULL`), `draft_evidence_json` | `idx_scored_jobs_status`, `idx_scored_jobs_fetched(fetched_at DESC)` 002:37-38; `idx_scored_jobs_icp_tier` partial 014:15 | scanned + scored jobs (v2 `jobs`) |
| `proposals` | 002:40-55 | `id TEXT PK`, `status` (terminal `Won|Lost|Expired|Withdrawn|Skipped` — NEVER flipped, 002:42), `upwork_status`, `status_reason`, `charge_rate REAL`, `charge_currency`, `cover_letter`, `marketplace_job_id`, `job_title`, `client_company`, `client_country`, `client_total_spent REAL`, `created_dt`, `modified_dt`, `thread_id`, `airtable_record_id`, `first_seen_at NN now`, `updated_at NN now` | — | submitted proposals (v2 `vendor_proposals`) |
| `contracts` | 002:57-70 | `id TEXT PK`, `title`, `upwork_status`, `airtable_status`, `contract_type`, `start_dt`, `end_dt`, `weekly_hours_limit INTEGER`, `hourly_rate REAL`, `weekly_charge REAL`, `paused INTEGER DEFAULT 0`, `job_id`, `job_title`, `freelancer_id`, `client_company`, `profile`, `thread_id`, `airtable_record_id`, `first_seen_at`, `updated_at` | — | Upwork contracts |
| `offers` | 002:72-80 | `id TEXT PK`, `title`, `state`, `type`, `job_id`, `client_id`, `client_name`, `message_to_contractor`, `thread_id`, `first_seen_at`, `updated_at` | — | offers |
| `threads` | 002:104-116; `client_id TEXT` 005:13 | `id TEXT PK`, `room_name`, `topic`, `room_type`, `contract_id`, `proposal_id`, `offer_ids_json`, `num_unread INTEGER`, `num_unread_mentions INTEGER`, `last_visited_dt`, `latest_message_id`, `latest_message_dt`, `awaiting_reply_from`, `bucket`, `tier`, `snooze_until DATE`, `first_seen_at`, `updated_at`, `client_id` | `idx_threads_client(client_id)` 005:14 | Upwork rooms; `client_id` set by `clients.link_threads`, never overwritten once non-null (registry.py:389) |
| `messages` | 002:118-128 | `id TEXT PK`, `thread_id TEXT NN REFERENCES threads(id)`, `message`, `created_dt`, `sender_user_id`, `sender_user_name`, `sender_org_id`, `sender_org_name`, `direction`, `has_attachment INTEGER DEFAULT 0`, `attachments_json`, `ingested_at NN now` | `idx_messages_thread(thread_id, created_dt)` | room messages (v2 `stories`); FK enforced (`foreign_keys=ON`) |
| `drafts` | 002:130-139 | `id INTEGER PK AUTOINCREMENT`, `thread_id`, `job_id`, `draft_kind NN`, `tier`, `generated_at NN now`, `body`, `word_count`, `rationale`, `sent_status DEFAULT 'pending'` | — | drafted replies/proposals (never sent) |
| `connects_history` | 013:9-20 | `id TEXT PK` (md5(date\|delta\|label\|job\|occ)[:16]), `event_date TEXT NN` (YYYY-MM-DD), `kind NN` (`applied|boost|boost_refund|refund|purchase|renewal|badge|other`), `label NN`, `job_id`, `job_title`, `connects_delta INTEGER NN`, `first_seen_at NN now` | `idx_connects_history_date(event_date)`, `idx_connects_history_kind(kind, event_date)` | applied-truth rail from daily Chrome scrape |
| `mcp_shadow_jobs` | 027:15-31 | `id TEXT PK`, `title`, `normalized_json NN`, `mcp_extra_json`, `found_by NN`, `also_found_by`, `hard_rule_skip`, `score INTEGER` (NULL until deliberate pass), `in_scored_jobs INTEGER NN DEFAULT 0`, `got_detail INTEGER NN DEFAULT 0`, `created_dt`, `first_seen_at NN`, `last_seen_at NN` | `idx_mcp_shadow_first_seen(first_seen_at)` | MCP feed shadow (Test B); deliberately isolated from `scored_jobs` |

### 3.3 CRM

| table | migration | columns | purpose |
|---|---|---|---|
| `clients` | 002:141-151 | `id INTEGER PK AUTOINCREMENT`, `name TEXT UNIQUE NN`, `folder_name`, `status`, `upwork_person_names_json`, `airtable_record_id`, `total_spent/total_hours/total_paid REAL`, `first_seen_at`, `updated_at` | legacy CRM client rows (identity spine is `config/clients.yaml`, not this table) |
| `people` | 002:153-163 | `id`, `name NN`, `role`, `client_id INTEGER REFERENCES clients(id)`, `emails_json`, `airtable_record_id`, `notes`, `first_seen_at`, `updated_at` | kernel stub |
| `communications` | 002:165-177 | `id`, `message_id TEXT UNIQUE NN`, `date`, `channel`, `direction`, `sender_name`, `sender_identifier`, `body_preview`, `body_full`, `thread_url`, `client_id REFERENCES clients(id)`, `related_proposal_id`, `related_contract_id`, `has_attachment`, `snooze_until DATE`, `airtable_record_id`, `first_seen_at` | mirrored communications |

### 3.4 Knowledge

| table | migration | columns | indexes | purpose |
|---|---|---|---|---|
| `sources` | 004:7-23 | `id`, `source_type NN` (`fathom|upwork|crm|email|loom|slack|clickup`), `source_ref NN`, `content_hash TEXT NN UNIQUE`, `client_id`, `client_name`, `content_type`, `density REAL`, `title`, `occurred_dt`, `raw_path`, `status NN DEFAULT 'ingested'`, `ingested_at NN now` | `idx_sources_client`, `idx_sources_status` | provenance per touchpoint |
| `facts` | 004:27-39; `scope` 005:7-8; `access_scope` 021:82-83 | `id`, `source_id INTEGER NN REFERENCES sources(id)`, `client_id`, `category`, `fact_text NN`, `citation NN`, `confidence NN CHECK IN ('CONFIRMED','CROSS-VERIFIED','INFERRED')`, `created_at`, `scope NN DEFAULT 'client' CHECK IN ('client','agency','cross-client')`, `access_scope NN DEFAULT 'team' CHECK IN ('founder','team')` | `idx_facts_client`, `idx_facts_source` | cited facts (citation mandatory) |
| `dossier_index` | 004:42-49 | `client_id TEXT PK`, `client_name`, `dossier_path`, `fact_count`, `source_count`, `last_compiled_at` | — | dossier registry |
| `facts_fts` / `source_fts` | 004:52-65 | fts5 (`facts_fts` content-synced by triggers `facts_ai/ad/au`) | — | search |
| `extract_staging_facts` | 021:19-37 | `id`, `run_id`, `client_id NN`, `event_id`, `category`, `fact_text NN`, `citation NN`, `confidence NN CHECK`, `access_scope NN CHECK`, `origin NN DEFAULT 'fact'`, `created_at`, `promoted_at`, `promoted_ref` | `idx_estg_facts_run`, `idx_estg_facts_promo` | shadow-first EXTRACT staging |
| `extract_staging_commitments` | 021:41-58 | `id`, `run_id`, `client_id NN`, `event_id`, `who NN`, `text NN`, `due`, `ask INTEGER NN DEFAULT 0`, `source_cite NN`, `dedup_key NN`, `created_at`, `promoted_at`, `promoted_ref` | `idx_estg_comm_run/promo/dedup` | staged commitments |
| `extract_staging_closures` | 021:64-76 | `id`, `run_id`, `client_id NN`, `event_id`, `intent NN`, `evidence NN`, `confidence REAL NN`, `created_at`, `applied_at`, `applied_card_id` | `idx_estg_clo_run` | staged closure signals |

### 3.5 Cards & Chief of Staff

| table | migration | columns | indexes | purpose |
|---|---|---|---|---|
| `nudge_state` | 003:5-13 | `item_key TEXT PK` (`followup:<thread_id>`, `hotlead:<job_id>`, `nudge:<ref>`), `state NN` (`done|snoozed|dismissed`), `snooze_until`, `note`, `source`, `updated_at NN now` | `idx_nudge_state(state)` | cockpit proto-nudge decisions (M2); still consulted via `card_subjects` |
| `nudges` | 006:11-37; 018:15-20 adds `client`, `intent_hash`, `closure_state`, `closure_evidence`, `closure_confidence REAL`, `closure_source` | `id`, `source NN`, `dept NN`, `kind NN`, `title NN`, `action`, `due`, `urgency REAL NN DEFAULT 0`, `money_at_stake REAL`, `score INTEGER`, `dedup_key NN`, `state NN DEFAULT 'open' CHECK IN ('open','snoozed','done','dismissed','decayed')`, `snooze_until`, `note`, `payload_json`, `created_at`, `refreshed_at`, `updated_at`, `decayed_at` | `idx_nudges_active_dedup UNIQUE(dedup_key) WHERE state IN ('open','snoozed')` 006:34-35, `idx_nudges_state`, `idx_nudges_dept` | THE card store; 018:27-31 and 019:9-15 are data DELETEs of cluster cards |
| `commitments` | 006:41-55; `review_state` 020:8 | `id`, `who NN`, `client_id`, `text NN`, `due`, `source_cite NN`, `dedup_key NN`, `state NN DEFAULT 'open' CHECK IN ('open','done','dropped')`, `created_at`, `updated_at`, `review_state` (NULL\|stale) | `idx_commitments_dedup UNIQUE(dedup_key)`, `idx_commitments_state`, `idx_commitments_review` partial | promises |
| `notes` | 006:59-69; 025:39-43 adds `claimed_at`, `attempts INTEGER NN DEFAULT 0`, `last_error`, `resolved_at`, `resolved_by` | `id`, `text NN`, `source NN`, `classified`, `classified_ref`, `state NN DEFAULT 'new' CHECK IN ('new','classified')`, `created_at` | `idx_notes_state`, `idx_notes_unresolved(resolved_at, state)` 025:46 | Quick Capture inbox |
| `sweep_state` | 006:74-80 | `id INTEGER PK CHECK (id = 1)`, `candidate_hash`, `last_swept_at`, `last_opus_at`, `last_metrics_json` | — | singleton empty-skip bookkeeping |
| `card_subjects` | 024:26-34 | `card_key NN`, `subject_kind NN` ('thread'), `subject_id NN` (threads.id), `first_seen_at NN now`; `PRIMARY KEY (card_key, subject_kind, subject_id)` | `idx_card_subjects_subject(subject_kind, subject_id)` | durable card identity across re-keying |

### 3.6 Finance (names + one line)

- `transactions` (002:82-102; +007:10-21 `amount_minor`, `amount_credited_minor`, `source_file`, `source_row`, `fx_rate TEXT`, `fx_date`, `amount_base_minor`; +008 `category`; +009 `pnl_bucket`; +016 `pnl_month`) — the ledger; `record_id TEXT PK`, `UNIQUE idx_transactions_canonical_key(canonical_key)` (002:101); Upwork rows carry `assignment_company`, `source`, `profile`.
- `account_balances` (007:30-41; +011 `period_start/period_end` with data backfills 011:13-16, 012:8-12) — statement opening/closing per account window.
- `payroll_runs` / `payroll_lines` (010:5-34) — prep-only payroll, never disbursed.
- `provisional_payments` (023:13-33; `canonical_key TEXT PK`, `provider`, `payer`, `client_id`, `amount_minor NN`, `currency NN`, `paid_date`, `provider_ref`, `native_id NN`, `permalink`, `raw_ref`, `state NN DEFAULT 'provisional'`, `matched_canonical`, `seen_at`, `confirmed_at`, `updated_at`; indexes state/client/paid) — email-reported money, isolated from `transactions`.

---

## 4. Guardrails, doctor, source health

### 4.1 `core/guardrails.py` — `evaluate_pretooluse(tool_name, tool_input, *, shadow_map=None, core_writes_allowed=None, v3_root=None) -> Decision` (210-225)

Dispatch: `Write/Edit/MultiEdit/NotebookEdit` (`_FILE_WRITE_TOOLS` 38) → `_evaluate_file_write`; `Bash` → `_evaluate_bash`; `mcp__*` → `_evaluate_mcp`; everything else ALLOW (225). `core_writes_allowed` defaults to `(v3_root / ".claude/ALLOW_CORE_WRITES").exists()` (216-217). `shadow_map` defaults to `config.SHADOW_MODE`.

- **File writes** (104-129): path resolved relative to `v3_root` (absolute paths outside the repo → ALLOW, 114). `MARKER_REL` itself → always DENY (119-121). `rel.parts[0] in ("core", ".claude", "scripts")` (41) or `rel_str in ("rails/REGISTRY.md",)` (42) → DENY unless `core_writes_allowed` (122-128). `modules/`, `tests/`, `bin/`, docs → ALLOW.
- **MCP** (78-99): method = last `__` segment, `clickup_` prefix stripped (70-75). Starts with any of `("list","get","search","read","download","ping","find","filter","resolve","whoami","check")` (44-45) → ALLOW. Contains `"delete"` → DENY always (84-86). Airtable `create_records`/`update_records` without `typecast: True` → DENY always (88-93). Then **if ANY value in `shadow_map` is True → DENY** (95-98). Live config has `knowledge/chief_of_staff/finance = True` ⇒ **all agent-driven MCP mutations (Airtable/ClickUp/Gmail/Upwork MCP) are denied in agent sessions today**; only runtime code paths under a live module perform external writes.
- **Bash** (170-205): credential reads (`/.credentials` or `.credentials/` in command AND `_CRED_READER_RE` 48-50 matches) → DENY; commands mentioning `etherwise-os` with SQL mutation / `rm|mv|chmod|chown|truncate` / a redirect into it → DENY (182-188); `rm`/`rmdir` targets must be under `var/`, `/tmp/`, `/private/tmp/`, `<v3>/var`, or `.git` lock/tmp_obj debris (134-167; `~`, `$`, backtick → DENY); SQL mutation on any `.db` other than `var/etherwise.db`/`config.DB_PATH` → DENY (194-198); `upwork.com` + any of `("mutation","sendMessage","submitProposal","applyTo","-X POST","--request POST")` (51-52) → DENY (200-203).
- **`audit_write(db_path, hook_event, tool_name, tool_input, session_id=None, note=None)`** (228-243): `INSERT INTO audit_log (actor='agent:<sid[:12]>', entity=tool_name, field=hook_event, new_value=json[:2000], source='hook', note[:500])`; swallows every exception.
- Hook wiring: `.claude/settings.json` → `PreToolUse: .claude/hooks/pretooluse.py`, `PostToolUse: posttooluse.py`, `SessionStart: sessionstart.py` (each with `PYTHONPATH=$CLAUDE_PROJECT_DIR`).

### 4.2 `core/doctor_checks.py` — `ALL_CHECKS` (400-414), each returns `list[Check(status∈PASS|WARN|FAIL|SKIP, name, detail)]` (25-29)

| registered name | function | asserts |
|---|---|---|
| python + sdk | `check_python` 44-59 | Python ≥3.9; `import anthropic` |
| config | `check_config_drift` 62-78 | `sha256(git show HEAD:core/config.py) == config.config_sha256()` else WARN |
| credentials | `check_credentials` 81-116 | creds dir exists + mode 700; `etherwise-os.env` present, mode 600, has `ANTHROPIC_API_KEY=`; `upwork-api.json`, `gmail-app-password.txt` present + 600 (WARN if missing) |
| secret scan | `check_secret_scan` 125-150 | no `_SECRET_PATTERNS` (32-37: anthropic key, Airtable PAT, ClickUp key, inline password) in tracked files ≤1MB; names file:line, never the match |
| database | `check_db` 153-181 | file exists; `PRAGMA integrity_check=='ok'`; journal_mode `wal`; `_EXPECTED_TABLES` (39-41) present; `db.pending_migrations()` empty (FAIL otherwise) |
| pricing | `check_pricing_coverage` 184-211 | every `MODELS` value + fallback in `PRICING`; no fallback in `STRUCTURED_OUTPUT_UNSUPPORTED` |
| guardrails | `check_guardrails_selftest` 214-234 | six canned `evaluate_pretooluse` cases hold (shadow_map `{"upwork": True}`, marker allowed) |
| hooks | `check_hooks` 237-260 | settings.json wires `PreToolUse/PostToolUse/SessionStart`; three shim files exist; reports marker mode (always PASS) |
| calendar | `check_calendar` 263-281 | each `CALENDAR_WATCH` + `DEPRECATION_WATCH` date: WARN if overdue or ≤7d |
| source freshness | `check_source_freshness` 327-374 | for every `source_health` item: PASS if ok, else WARN (`down`/`stale`/`degraded`, `[non-critical]` suffix for non-critical) — never FAIL |
| models api | `check_models_api` 284-310 | each model id resolves on `/v1/models` (network; `--offline` → SKIP) |
| plists | `check_plists` 313-324 | `plutil -lint` each `launchd/*.plist` |
| cockpit | `check_cockpit` 377-397 | token minted; token file mode 600; `COCKPIT_HOST == "127.0.0.1"` else FAIL |

**Registering a new check** = write `check_<name>() -> list[Check]` with injectable inputs, and append `("<label>", check_<name>)` to `ALL_CHECKS` (400-414). `bin/doctor` iterates that tuple; exit code is non-zero on FAIL only (doctor_checks.py:368-370 comment; test_doctor.py:252-256). There is no `check_migrations_or_launchd` for a task that is missing from SOURCES — registering in SOURCES is the only way a rail gets doctor coverage.

### 4.3 `core/source_health.py` — per-rail freshness + degradation

- Rails are **named by `config.SOURCES[*]['key']`** (config.py:726-892), each declaring `label`, `task_name` (the `runs.task_name` it writes), `max_age_hours`, `degrade_kinds`, `critical`, `backs`, optional `empty_ok`, `freshness_sql`, `launchd_label`, `catch_up`, `why`. Current keys: `connects_rail`, `scanner`, `wise_mail`, `upwork_sync`, `crm_mirror`, `finance_upwork_weekly`, `finance_stripe`, `jarvis_sweep`, `jarvis_imap_01`, `jarvis_imap_02`, `jarvis_slack`, `finance_ledger`, `finance_payment_mail`, `loom_fetch`. `backs` values in use: `applied`, `pipeline`, `money`, `crm`, `today` (no `upwork`).
- `source_status(conn, source, now) -> dict` (81-159): freshness = `completed_at` of latest run with `status IN SUCCESS_STATUSES` (`("completed",)` 40) or `SUCCESS_STATUSES_EMPTY_OK` (`("completed","skipped_empty")` 41) when `empty_ok`; or `freshness_sql` for manual sources. `status`: `down` (never) > `stale` (age > max_age) > `degraded` (unresolved anomaly with `kind IN degrade_kinds` AND `run_id == MAX(runs.id) for task`, 110-129 — self-healing) > `ok`. Returns keys: `key, label, critical, backs, why, task_name, max_age_hours, manual, last_success, age_hours, stale, degraded, degrade_detail, hours_overdue, days_overdue, escalation (0-3), status, ok`.
- `source_health(conn, now=None, sources=None) -> {"sources": [...], "worst", "ok" (no critical unhealthy), "critical_unhealthy": [keys]}` (162-177).
- Helpers for a health strip: `by_backs(health, backs)` first match (180-186); `worst_for(health, backs)` sickest feeder (262-271); `report(health)` → `{"headline","ok","lines","worst_escalation","count","unhealthy"}` (250-259); `headline` (199-216) leads `DEAD`/`STALE`/`DEGRADED`; `source_line` (224-247). Escalation thresholds `ESCALATE_PERSISTENT_HOURS = 24`, `ESCALATE_DEAD_HOURS = 72` (47-48).
- The cockpit already consumes it: `modules/cockpit/data.py:343`, `1337-1339` (`by_backs(health,"applied")`, `by_backs(health,"pipeline")`), `2196` (`report`), `2320-2327` (`worst_for(health,"money")`). For an Upwork strip, filter `health["sources"]` by `key in {"connects_rail","scanner","upwork_sync"}` (plus the MCP rail once registered) — `by_backs` will not do it since `upwork_sync` backs `money`.

---

## 5. Registry, notify, gateway, sync, Airtable client, eval

**`core/registry.py`** — read-only loader for `config/clients.yaml` via a strict stdlib YAML subset (`loads` 178-188, `dumps` 239-242; tabs/flow collections raise `RegistryError` 73). `norm()` (246-275) = NFKD accent-strip + whitespace-collapse + lower. `Registry` (307-506) validates on load (`_validate_and_index` 326-427): unique ids; emails unique across clients, bare shared-platform domains (`SHARED_SENDER_DOMAINS` 296-304 incl. `email.upwork.com`, `upwork.com`) dropped with a warning; name/aliases unique and never an internal person (`config.is_internal_person`); `upwork_rooms` unique; `slack.team_id` unique; `billing.ledger_label` unique. Per-client fields: `id, name, aliases, emails, slack{workspace,url,team_id,channels}, teams, upwork_rooms, fathom_names, clickup, billing{rail,canonical,ledger_label}, scope_notes, kind∈("client","prospect","former") (286), no_match`. Matchers: `match_email` (430), `match_upwork_room` (439), `match_slack_team` (442), `match_name` (463, exact normalised), `match_ledger_label` (466), `canonical` (474), `ids` (480), `matchable_ids` (483, excludes `no_match`), `kind` (488), `is_active` (493, False for `former`/unknown), `client_ids` (500, economics roster: excludes prospects, keeps former). `load(path=None, force=False)` (514-540) is mtime-cached under a lock; `loads_registry(text)` (543) for tests; `invalidate` (561).

**`core/notify.py`** — one Gmail SMTP-SSL send (`send_gmail` 43-54, creds from `CREDENTIALS_DIR/gmail-app-password.txt` keys `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `DEFAULT_FROM_NAME`; To = `config.NOTIFY_EMAIL`). `drain_critical_anomalies(db_path=None, send=None)` (63-100): selects `anomalies WHERE id > cursor AND (severity='critical' OR kind IN EMAIL_WARN_KINDS)` (`("budget_monthly_soft",)` 30), sends ONE HTML email `INTERRUPT: N anomalies — <last kind>`, then upserts `sync_cursors['notify_drained_anomaly_id']` (94-98) only after a successful send. Entry `bin/notify` (task `notify_drain`, module `kernel`).

**`core/claude_gateway.py`** — `call(task_name, model_key, user_content, system=None, schema=None, purpose=None, max_output_tokens=None, route=None, db_path=None, extra=None, model_override=None) -> GatewayResult(text, parsed, model, route, usage, cost_usd, stop_reason, duration_ms, request_id)` (342-427). Resolves `config.MODELS[model_key]` (raw ids forbidden; `model_override` reserved for runner failover), requires `PRICING`, refuses schema on `STRUCTURED_OUTPUT_UNSUPPORTED`, runs `_check_ceilings` (295-337: daily hard cap with `BUDGET_RESERVE_USD` for non-priority tasks → `ClaudeBudgetExceeded(scope='daily_hard')` + anomaly `budget_hard_cap`/`budget_reserve_guard`; soft cap anomaly `budget_soft_cap`; monthly anomaly `budget_monthly_soft`; per-run cap via module accumulator `_run_spent_usd` 83 → `scope='per_run'`), routes `credit` (`claude -p` subprocess, `build_credit_cmd` 119-138) with fallback to `payg` (anthropic SDK, `_payg_transport` 188-206, `max_retries=0`), and logs every call incl. failures to `claude_usage` (`_log_usage` 277-292; **`run_id` never set**). "Caching" = 1h `cache_control` on the system block (`build_payg_request` 105-109); there is no response cache. Costs via `compute_cost` (141-146) unless the CLI reports `total_cost_usd`. Anomaly dedupe `_record_anomaly_once` keys on `kind + detail.ist_date` (263-274). SDK errors mapped by class name to `TransientAPIError` (175-185).

**`core/sync/engine.py`** — pure Airtable mirror planner: `FieldSpec(at_field, owner∈system|human)` (35), `Operation(kind create|update, record_id, key, fields)` (41). `eligible_jobs` (49-56) = `scored_jobs` with `status IN SYNC_STATUS_WHITELIST` (`("Skipped","Scored","Drafted","ClickUp Created")` 32) minus `scripts/job_id_guard.looks_fabricated`. `plan_push(local_rows, at_records_by_id, ownership, at_key_field, key_col="id", fallback_match=None)` (67-108): match by `airtable_record_id`, then key field, then fallback (adopted rows get the key stamped); only changed system-owned fields are sent. `plan_pull` (111-137): human-owned fields, Airtable wins, emits `audit_log` kits. `execute_push(ops, *, shadow, record_shadow=None, airtable=None, entity)` (155-181): shadow ⇒ `record_shadow(target="airtable", ...)` per op (i.e. `ctx.record_shadow_write`); live ⇒ `airtable("POST"|"PATCH", "", payload)` in batches of 10 with `typecast: True`. `core/airtable_client.py` provides `AirtableClient.engine_writer(base, table)` (120-126) as that `airtable` callable, plus `list_records` (78-100, paginated, `filterByFormula`), `create_records`/`update_records` (102-118, typecast, batch 10); key from `AIRTABLE_API_KEY=` in `CREDENTIALS_DIR/etherwise-os.env` (32-42); `_request` (45-68) is urllib with curl fallback.

**`core/evals/scoring_eval.py`** — report-only Haiku-vs-Sonnet eval over historical `scored_jobs` joined to `proposals` outcomes (`sample_jobs` 102-122), strict `SCHEMA` (35-65) and the v2.3 matrix `SYSTEM` prompt (67-99), chunked into `runner.run_task(f"{task_name}_{n}", ..., module="kernel")` runs of ≤25 calls (167-170); writes `reports/scoring-eval-<date>.md`. Useful as the canonical statement of the scoring bands and the whale gate.

---

## 6. Test conventions

- Runner: **stdlib `unittest`**, no pytest config, no `tests/__init__.py`. Whole suite: `PYTHONPATH=. python3 -m unittest discover tests` (BUILD_BRIEF.md:576); one module: `PYTHONPATH=. /usr/bin/python3 -m unittest tests.test_<x>` (BUILD_BRIEF.md:216). Files `tests/test_<area>.py`, classes `Test<Area>(unittest.TestCase)` or `<Area>Case`, methods `test_<behaviour_in_plain_words>` with docstrings citing the incident date that motivated them.
- DB: a real file in a temp dir, migrated with the real migrations: `self.tmp = Path(tempfile.mkdtemp(prefix="…-")); self.db_path = self.tmp / "test.db"; db.migrate(self.db_path)` in `setUp`, `shutil.rmtree` in `tearDown` (test_runner.py:22-44, test_guardrails.py:254-260, test_source_health.py:44-51). Every code path takes `db_path` explicitly. Rows inserted with raw SQL through `db.connect(self.db_path)`; runs rows faked exactly as `_finish_run` writes them (test_source_health.py:35-41, 381-387).
- Config patching: `mock.patch.object(config, "LOG_DIR", …)`, `mock.patch.object(config, "LOCK_DIR", …)`, `mock.patch.dict(config.SHADOW_MODE, {"upwork": True})` (test_runner.py:30-38); `mock.patch.object(dc.config, "COCKPIT_HOST", "0.0.0.0")` (test_doctor.py:181); `mock.patch.object(config, "PAYROLL_FILE", …)` (test_registry.py:194).
- Boundaries mocked, not the DB: `mock.patch.object(gw, "call", side_effect=…)` (test_runner.py:259), `_sleep=lambda s: None` injection (58-60), `now=`/`sources=` injection into `source_health` (test_source_health.py:53-55), injectable doctor inputs (`files=`, `creds_dir=`, `db_path=`, `today=`, `models=`…), `send=` callable for notify, `mock.patch("core.source_health.source_health", return_value=…)` (test_doctor.py:218).
- Live-config coherence tests exist and will bite a config change: `test_live_config_is_coherent` (test_doctor.py:44-47), `RegistryCase.test_every_declared_source_is_well_formed` / `test_money_backing_sources_are_registered` (test_source_health.py:292-309), **`EmptyOkCase.test_registry_opt_ins_are_exactly_the_clean_skip_rails`** (424-434: the `empty_ok` set must equal `{"jarvis_sweep","jarvis_imap_01","jarvis_imap_02","loom_fetch"}` — adding an `empty_ok` source requires editing this test), `test_the_roster_is_DERIVED_from_payroll…` (test_registry.py:169-186, reads `config.PAYROLL_FILE` — fails in any checkout without `../Payroll/employees.json`), `TestHooksCheck.test_real_repo_wiring_passes`.
- Registry tests build registries from inline YAML strings via `registry.load(p, force=True)` (test_registry.py:80-84) or `loads_registry`.

---

## 7. Gotchas for a new Upwork task / tab

- **Every task runs through `runner.run_task(TASK_NAME, fn, module=…)`** (runner.py:218); the `runs` row, lock, migration guard, retries, and failure anomaly are all in there. Return a metrics dict; raise `runner.TaskSkip` for "nothing new" — but remember `skipped_empty` does **not** count as freshness unless the SOURCES entry sets `empty_ok` (source_health.py:40-41).
- **Pick the module deliberately.** `module="upwork"` is LIVE (`SHADOW_MODE["upwork"] = False`, config.py:135) ⇒ `ctx.shadow` is False and external writes are not ledgered. The existing MCP shadow poll runs as `module="system"` (shadow_poll.py:219) and relies on `mcp_client.py` refusing write tools in code (mcp_client.py:41,79). An unknown module string defaults to shadow ON (runner.py:229).
- **Task naming drives budget**: only names starting with `upwork_`/`scan_jobs`/`poll_inbox` get the full daily cap (config.py:91, claude_gateway.py:302-305). `mcp_shadow_poll` does not. Use the `upwork_` prefix for a revenue-engine sync.
- **Register the rail in `config.SOURCES`** (726-892) with `key`, `task_name`, `max_age_hours`, `degrade_kinds`, `critical`, `backs`, `launchd_label`, `catch_up`; otherwise it has no doctor/cockpit health, `bin/catch-up` never restarts it, and `launchd/install.sh:90-120` will not verify it. `mcp_shadow_poll` is currently **not** in SOURCES. Adding it means editing `core/config.py` (protected path — marker present now; propose a diff otherwise).
- **Degradation signalling** = `ctx.anomaly(kind, "warn"|"critical", detail)` (runner.py:110-120) with `kind` listed in that source's `degrade_kinds`; it is bound to `run_id`, so a clean later run self-heals (source_health.py:110-129). Kind strings are free text — keep them `<task>_<condition>` like `scan_feed_degraded`, `connects_empty_window`.
- **IST**: `config.TZ` (160); timestamp with `datetime.now(config.TZ).isoformat(timespec="seconds")` (runner.py:186, 230). `runs` timestamps carry `+05:30`; SQLite defaults are naive UTC — never string-compare across the two (015_anomaly_run_id.sql:4-6; source_health.py:51-66).
- **Idempotency pattern**: canonical id as TEXT PK + `SELECT`-by-id-before-`INSERT` (017_events.sql:5, 023:14 `canonical_key PK`, 027 `id TEXT PK` with `first_seen_at/last_seen_at`); cursors via `sync_cursors` upsert `ON CONFLICT(name) DO UPDATE` (notify.py:94-98). Unparseable input → `quarantine` row, never dropped (001:90-99).
- **Terminal statuses never flip** is a data rule stated at `002_sales_crm.sql:42` and CLAUDE.md:27; it is enforced in module code (e.g. `modules/upwork/sync_state.py`), not by a DB constraint — a new sync must not `UPDATE proposals SET status` for `Won|Lost|Expired|Withdrawn|Skipped` rows. `threads.client_id` likewise is never overwritten once non-null (registry.py:389).
- **Shadow data stays out of the live tables**: `mcp_shadow_jobs` is its own table by design (027:7-10); do not write MCP rows into `scored_jobs`/`proposals` until the cutover decision. `SYNC_STATUS_WHITELIST` (engine.py:32) plus `job_id_guard.looks_fabricated` gate anything mirrored to Airtable.
- **No hardcoded IDs/ceilings/schedules** (CLAUDE.md:19) — the MCP poll's `MAX_GETS_PER_RUN`/`SMART_PAGES` in `shadow_poll.py:44-47` are an existing violation, not a precedent.
- **Claude calls**: only via `ctx.claude(model_key=…)` / `runner.claude_call` / `gw.call` with a `MODELS` key; never import `anthropic` (claude_gateway.py:190 is the sole import); never pass `temperature/top_p/top_k` (stripped at 114-115 anyway). Calls go **outside** `with db.connect()` blocks. `claude_usage.run_id` is never populated — join spend by `task_name`+`ist_date`.
- **Cockpit seam**: a tab = a reader in `modules/cockpit/data.py` registered in `GET_ROUTES` (`server.py:33-70`), actions in `POST_ROUTES` (`server.py:141-160`), UI in `static/index.html` + `static/js`. Server binds `COCKPIT_HOST:COCKPIT_PORT` (127.0.0.1:5051) and `check_cockpit` FAILs on any other host. Health for the tab: `source_health.source_health(conn)` then filter by source `key`; there is no `backs="upwork"`.
- **Agent-session MCP writes are denied** while any module is shadowed (guardrails.py:95-98 — `knowledge/chief_of_staff/finance` are True), so an Upwork MCP sync must run as scheduled code, not as an agent tool call; Upwork drafts-only is additionally enforced for Bash (200-203) and by `mcp_client.py`'s allowlist.
- **Migrations**: next file is `028_*.sql`; additive, `IF NOT EXISTS`, no destructive DDL (migrate has no per-file transaction, db.py:47-59). After adding one, every task's startup guard (runner.py:252-258) and `check_db` (doctor_checks.py:176-180) fail loud until `PYTHONPATH=. python3 -m core.db` is run on the live DB.
- **launchd**: add `launchd/io.etherwise.v3.<name>.plist` (copy the shape at `io.etherwise.v3.upwork-mcp-shadow.plist:21-38`: Framework Python 3.9, `PYTHONPATH`, logs under `~/Library/Logs/Etherwise-v3/`, `RunAtLoad false`, no `WorkingDirectory`), then `bash launchd/install.sh`. Seven labels referenced by SOURCES (`finance-wise-mail`, `finance-upwork-weekly`, `finance-stripe`, `jarvis-sweep`, `jarvis-slack`, `finance-payment-mail`, `kb-loom-fetch`) have **no plist in this snapshot's `launchd/`** — ambiguous whether they live elsewhere on the Mac; `bin/doctor` `check_plists` only lints what is in the repo dir.
- **Python 3.9 floor** (doctor_checks.py:47; BUILD_BRIEF.md:576): no `match`, no `tomllib`, no `X | Y` runtime unions without `from __future__ import annotations` (every core file has it).
- **Snapshot environment caveats**: `CREDENTIALS_DIR`, `PAYROLL_FILE`, `V2_DB_PATH` resolve to siblings of the repo root and are absent here; `config` still imports (degrades via `PAYROLL_READ_ERROR`), but `test_registry.test_the_roster_is_DERIVED_from_payroll…` and anything touching credentials will fail in this checkout.
