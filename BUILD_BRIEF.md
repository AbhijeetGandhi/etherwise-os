# BUILD BRIEF — read this first, every session
**Updated:** 2026-06-11 (Day 2 complete) · **Owner of this file:** updated at every checkpoint, never stale.

## What's happening
Complete rebuild of Etherwise OS ("v3") per the four planning docs in `../business/`:
1. `etherwise-v3-core-architecture.md` — THE spec: 23 locked decisions, kernel, rails, planes, build sequence, pre-flight protocol
2. `etherwise-v3-master-build-catalog.md` — every capability (P0/P1/P2), 8 departments
3. `claude-platform-research-2026-06.md` — platform facts (models, billing, surfaces)
4. `claw-architecture-lessons.md` — adopted patterns / rejected anti-patterns

**v2 (`../etherwise-os/`) stays live and untouched until per-module cutover.** Shadow mode rules in architecture §9 — v3 makes NO external writes until a module's cutover flag flips.

## Process rules (Abhijeet's explicit instruction)
- **He is a core part of development. Ask questions liberally — there is no question too small.** Quality over speed, always. When in doubt: ask, don't assume. 100 questions for one thing is fine.
- Design sessions happen in Cowork (better Q&A tooling); build runs in Claude Code on this repo.
- Every module: design session → build → 7-day shadow → parity review with him → cutover.

## Day 1 status (this scaffold)
DONE: repo tree · git init (remote: https://github.com/AbhijeetGandhi/etherwise-os) · `core/config.py` (all IDs, pinned models, billing routing, ceilings, shadow map) · `core/db.py` + `001_ops.sql` (ops domain) · `rails/REGISTRY.md` seed · `SOUL.md` · root `CLAUDE.md` router · Cowork scanner task recreation (model pin fix).
DECIDED TODAY: remote = AbhijeetGandhi/etherwise-os (private) · system python3 · scanner on Sonnet 4.6 · clean v3 schema names (with documented v2→v3 mapping in import).

## Day 2 status (kernel built — 2026-06-11)
DONE: git first-aid + first push (remote switched to SSH — keychain had no HTTPS token) · python3 verified 3.9.6 (stdlib code must stay 3.9-compatible: no match/tomllib; the Agent SDK python pkg needs 3.10+, so the **credit route runs via `claude -p` subprocess**, CLI 2.1.170 — flags verified: --json-schema, --max-budget-usd, --output-format json, --tools "") · migrations applied clean on the Mac (8 tables, WAL, integrity ok) · **core/claude_gateway.py** (both billing routes + credit→payg fallback, ANTHROPIC_API_KEY scrubbed from credit env, 1h caching, structured outputs via output_config never-on-Fable, sampling params stripped, per-run + daily ceilings with anomaly rows, every call incl. failures logged to claude_usage, SDK errors mapped to a gateway taxonomy so nothing else imports anthropic) · **core/runner.py** (run ledger, retry+backoff, failover chains via gateway model_override, flock locks, deterministic stagger, IST daily logs, ctx.record_shadow_write/require_live, failure→critical anomaly) · **core/guardrails.py + .claude/hooks/ + settings.json** (typecast, shadow external-write denies, delete allowlist, v2 read-only, credentials-echo deny, Upwork drafts-only, protected-path denies, PostToolUse audit, SessionStart config-integrity check) · **bin/doctor** (live run: 24 PASS / 0 FAIL; all four pinned model IDs validated against /v1/models with the real key) · **tests/: 106 unit tests green** (`PYTHONPATH=. python3 -m unittest discover tests`).

DECIDED TODAY (approved in-session): tests live in `tests/` on stdlib unittest · kernel gateway = single-shot judgment calls only (tool-enabled headless runs designed at M4) · failure routing = critical `anomalies` rows for now, **notify.py comes Day 3 and drains them into interrupt emails** · core/.claude write-protection via build-phase marker `.claude/ALLOW_CORE_WRITES` — **delete it at M1 cutover to arm the lockdown** (marker itself is never agent-writable) · config.py gained PRICING / CACHE_TTL / CLAUDE_TIMEOUT_SECONDS / runner constants / LOCK_DIR / DEPRECATION_WATCH / CALENDAR_WATCH (three reviewed diffs).

NOTE: hooks snapshot at session start — active from the NEXT Claude Code session in this repo onward.

## Day 3 status (2026-06-11 evening — PARTIAL, parity sign-off PENDING)
DONE: **P0 extraction bridge v4.14** — scripts/feed_bridge.py (DOM-tile harvest; Upwork removed window.__NUXT__ from find-work pages ~June 11, verified live — tiles are the data source now) + clickup_push.py (deterministic tasks from DB rows, v2-code title style, check-before-retry) + run_log.py (honest ledger, hardcoded task_name) · 31 unit tests green · **live-tested on real Chrome**: 3 feeds, 50 cards, 0 invalid extractions, 48 known agreed with production dedup, 2 genuinely-new correctly identified in dry-run · prompt draft at `upwork/scanner-prompt-v4.14.md` · **re-enable criteria status: bridge live ✓ · run_log.py ledger ✓ · 2 externally-verified clean runs = pending Cowork install (Abhijeet)** · Chrome AppleScript-JS permission ENABLED (menu toggle wasn't persisting; set via Local State pref surgery + real restart, backups in /tmp — board_cleanup --liveness is unblocked too) · **June-15 scheduled**: launchd one-shot `io.etherwise.v3.june15-credit-validation` (12:10 IST) → bin/validate-credit-route (asserts route_used=='credit' with payg fallback DISABLED) → emails outcome · guardrails false-positive fixed live (v2 read-only rule denied benign 2>/dev/null redirects; now only redirects INTO v2 deny; 108 v3 tests) · hooks CONFIRMED live (PreToolUse denied real commands; PostToolUse audit rows flowing) · **002_sales_crm migration** applied (scored_jobs/proposals/threads/messages/contracts/offers/transactions/drafts/clients/people/communications + claude_usage.run_id) · **bin/import-v2 RUN: 19/19 parity checks PASS** — money sums to the cent, terminal statuses exact; report at `../reports/parity-report-2026-06-11.md` + junk sweep CSV.

**◆ GATE: parity report awaits Abhijeet's sign-off — nothing consumes the imported data until then.** Audit notes for him: Airtable #ERROR records found = 49, not the known 8 (all quarantined w/ payloads; clustered Apr 5/Apr 22/May 1 timestamps) · legacy Submitted-no-outcome = 0, not 44 (appear to have been outcomed in v2 era) · 1,874 unlinked Airtable Proposals records listed FYI (jobs-mirror writeback gaps; sync-engine session reconciles) · junk-window sweep list = 29 records, deletion needs his approval.

## INCIDENT 2026-06-11 (afternoon): unlogged bulk Phantom-marking — RESTORED
At 09:44 UTC (15:14 IST) an **unlogged ad-hoc SQL write** bulk-marked **3,014 jobs rows status='Phantom'** (94% of the table, incl. April GraphQL-era rows). No runs row; SQL-side timestamps. **Likeliest actor (post-hoc): the midday escalation session's fabrication cleanup — an UPDATE meant for ~24 fabricated rows ran unscoped.** Found during import recon ~14:00 UTC. Blast contained: sync's whitelist made Phantoms invisible (Airtable went stale, not corrupt); dedup unaffected (id-based). **Restoration (authorized, 14:1x UTC):** forensic snapshot `backups/etherwise-2026-06-11_1410-FORENSIC-pre-restoration.db` · sync-airtable + reconcile paused · verified the burst touched ONLY the status column (column-diff vs backup) · 2,854 rows restored verbatim from the 03:00 backup · 160 post-backup rows evidence-derived (task/skip/draft/score) · 19 scanner-marked Phantoms (11:55/12:46) untouched · fabricated rows stayed Phantom (no evidence columns — restoration correct under the cleanup-actor theory) · **Phantom 3,033 → 68 · assertions pass · integrity ok** · honest runs row `incident-restore-phantom-statuses` · jobs resumed + verification sync kicked. **CLOSED 2026-06-11 ~20:30 IST: sync run 2169 completed with ZERO errors — proposals_from_jobs created 84 + updated 1,318 Airtable records (the un-staling, at expected magnitude), proposals_from_vp updated 73; runs 2170/2171 completed normally; tripwire reads exactly 68 Phantom (writer has not refired).** **Tripwire:** `SELECT COUNT(*) FROM jobs WHERE status='Phantom'` >75 means the writer refired. **Lesson for the registry: ad-hoc production SQL needs the same discipline as rails — scoped WHERE verified on a dry SELECT first, runs-ledger row always.**

## Next actions (Day 3 remainder — blocked on parity sign-off where marked)
1. `bin/import-v2` + **parity report** ◆ (sign-off gate): map v2 jobs/proposals/contracts/transactions/rooms/stories/offers/usage → clean v3 names; quarantine dirty rows (8 Airtable error records, 44 pre-v2 Submitted proposals, and the **53 status='Phantom' rows from the 2026-06-10 incident — must NOT import as real jobs**); counts + spot-sums + terminal-status preservation; baseline metrics snapshot.
2. Sales/CRM DDL (002 migration) to receive the import — also add `claude_usage.run_id` linkage while the schema is young.
3. Sync-engine core (`core/sync/`): field-ownership maps, typecast always, ≤10/batch, recordId-first dedup, conflict→audit_log — and a status whitelist that excludes Phantom (v2's sync_airtable failure on 2026-06-10 19:55Z is the cautionary tale; verify v2's next runs stayed clean).
4. Scoring eval ◆: 50 historical scored jobs, Haiku vs Sonnet → decision #4 model tiers.
5. `core/notify.py`: render+send via gmail app password, drain critical anomalies → interrupt email, everything else batches to briefs.
6. Open tuning item: credit-route context shape (`claude -p` may auto-load CLAUDE.md from parent dirs; tools disabled, cwd=var/) — validate when ETHERWISE_BILLING flips on June 15.

## Scanner v5 — first run (2026-06-10, scan-20260610T152542) ✅
50 cards / 45 unique / 4 new → 2 Hot (incl. a 30 Hot+Loom) + 2 floor-skips · email sent · all self-checks green · dedup ledger held against an overlapping old-task run. **Prompt patched to v4.11 after the run:** correct My Feed URL (`/nx/find-work/my-feed`), prefer `window.__NUXT__.state` page-state JSON over DOM scraping (exact ids/hrefs/verbatim titles), n8n-in-tags does NOT trigger exclusion (decided 2026-06-10 — both best leads carried the tag), non-ASCII title re-verification, check-before-retry on ClickUp create errors.
**→ M1 Chrome prototype must inherit the `__NUXT__.state` extraction technique — it is dramatically more reliable than DOM scraping.**
DONE 2026-06-10: model pinned Sonnet 4.6 on v5 (UI) · old `upwork-job-scanner` DISABLED (rollback = re-enable; delete ~July 10 along with retired `upwork-job-scanner-v2`). The model-pin rot that motivated this rebuild is fixed on the one surface that keeps it.

## INCIDENT 2026-06-10 (night): scanner phantom-ID corruption — resolved
**Symptoms (Abhijeet):** duplicate ClickUp tasks + "Job not found" links.
**Root cause (verified empirically, two false leads on the way):** NOT uid-vs-ciphertext (live-feed census: uid == ciphertext digits, 30/30). The 17:45Z and 18:54Z runs (v4.11-era, one in-flight during the patch) **corrupted job IDs in their extraction/transfer path** — stored ids matched no real job. Corrupt ids defeated id-dedup, so already-known jobs were re-claimed as "new" (one run: 36 "new", mostly phantoms of known jobs) → duplicate tasks; and `~02<corrupt-id>` URLs 404'd → dead links. Same root, both symptoms.
**Cleanup done:** 56 rows marked status='Phantom' (3 later verified-real and restored — incl. row ...117 wrongly caught by my slug heuristic, lesson: heuristics ≠ verification) · 30 ClickUp tasks closed w/ comments (3 dups-of-originals, 27 corrupt) · 4 latest-run tasks verified live and kept (86d3a89zz/yk + ybb/xrf rows restored) · post-patch runs verified clean ("already_known 23, new 1").
**Prompt now v4.13:** Iron Law of Data Integrity — single-evaluation extraction (no clipboard/reassembly for ids), ciphertext-only URLs, post-claim cross-check vs extraction snapshot, per-run liveness probe (1 random URL must load), repost guard, dup-title self-check.
**Loose ends:** (1) job ~022064484440376044477 'No-Code/Low-Code Agency Partner' — REAL, LIVE, score 29, sits in DB w/o task (pre-incident quirk) — Abhijeet may want to apply manually. (2) sync_airtable run 19:55Z failed (likely collided with cleanup writes) — verify next runs; Phantom rows must NOT sync to Airtable (check sync's status filter). (3) 53 Phantom rows retained for audit; scanner ignores them.
**For M1 (must-inherit):** `__NUXT__.state` extraction with ciphertext; ids/urls never cross a lossy boundary; cross-check + liveness as code, not prompt; this incident is the case study for why scanning belongs in the deterministic data plane — a Python extractor cannot "reconstruct from memory."

## Day-2 independent verification (Cowork design-side audit, 2026-06-11 ~05:00 IST) — PASSED
Method: spec-conformance code read (gateway/runner/guardrails/hooks/doctor vs architecture + 23 decisions + claw lessons) + independent re-run (tests 106 OK; doctor 25 PASS · 1 WARN · 0 FAIL · 2 SKIP, four pinned model IDs validated against live /v1/models) + production probes.
**Four audit notes (none blocking):**
1. Credit-route CLI flags (`--no-session-persistence`, `--tools`, `--max-budget-usd`, `--json-schema`) are UNVERIFIED against the installed CLI until the June-15 live validation — failure mode is safe (CreditRouteError → payg fallback) but watch for silent always-payg; the Jun-15 validation task must assert route_used='credit' actually happens.
2. Guardrails file-write check: relative-path writes outside the repo (e.g. `../etherwise-os/x`) fall through to ALLOW (positioned as the permission system's concern) — Bash mutations to v2 ARE denied; acceptable, documented here.
3. `.claude/ALLOW_CORE_WRITES` is COMMITTED — a fresh clone arrives unlocked. At M1 cutover: delete it AND add to .gitignore so restores arrive locked.
4. Per-run USD cap is checked pre-call → a single call can overshoot once (bounded by max_tokens; fine).
**Production findings:** scanner v4.13 Iron Law verified live — 20:37Z run: crosscheck_failures=0, liveness probe PASS on a real URL; runs at 21:46Z + 22:47Z produced 53 corrupt extractions that the machinery SELF-QUARANTINED (Phantom, zero tasks, zero pollution). Extraction flakiness persists per-run (LLM-as-plumbing — the v3 thesis), but now fails safe. sync_airtable recovered after the 19:55Z one-off. **Total Phantom now 106** (53 incident + 53 self-quarantined).
**NEW Day-3 item:** ~30 junk Airtable Proposals records were created from corrupt jobs rows during the incident window (18:04Z + 18:59Z sync runs, e.g. recGrOiSeeeImJDff) — sweep list + Abhijeet-approved deletion (Airtable trash keeps 7-day recovery), alongside the already-queued sync status whitelist + import-v2 Phantom quarantine.

## Board hygiene + extraction bridge (2026-06-11 morning, approved by Abhijeet)
**Findings:** 329 open tasks on the Upwork board; 160 pointed at jobs 15–64 days old (median 50d — nothing ever reaped un-applied tasks, systemic gap since April); 21 duplicate-title groups (mostly applied/interview, protected); my_feed coverage was FAKE until the 2026-06-10 URL fix (bare /nx/find-work/ redirected to best-matches — weeks of "my_feed" data are actually best-matches; treat per-feed historical analytics accordingly). Post-fix coverage verified real: my_feed contributes ~5 unique claims/day.
**Shipped:** `scripts/board_cleanup.py` — R1 expire >14d 'new' tasks · R2 dup-close keep-best (applied/interview never auto-closed) · dry-run CSV gate → approved → live run (161 closures) · `--reap` mode = daily self-guarded reaper, now invoked from the scanner prompt (Abhijeet chose in-scanner over launchd). M1 absorbs this.
**Manual queue:** ~20 dup groups with all-protected members (e.g. Spa job ×3 in interview) listed for Abhijeet's manual merge.

## ESCALATION 2026-06-11 (midday): scanner PAUSED — fabrication reached the board
A degraded v4.13 run fabricated ~24 COMPLETE jobs (generic template titles, keyboard-walk ids like ~022064823456789012345) AND fabricated its own guard reports ("crosscheck: 0", "liveness: PASSED") — **prompt-based guards can be hallucinated as executed; only externally-run verification counts.** Abhijeet approved: v5 task DISABLED until the bridge ships + 2 externally-verified clean runs.
**External liveness sweep executed (Chrome session-fetch, 141 open tasks verified):** 79 alive · 65 dead — ALL 65 were scanner-created 'new' tasks (zero dead in applied/interview — the human pipeline is fully alive) · 65 closed as EXPIRED w/ comments · verdicts CSV in reports/. 98 old tasks (mostly April-era applied) have no DB URL — untouched, tracked via proposals lifecycle.
**New tooling:** `board_cleanup.py --liveness` (AppleScript-JS path — BLOCKED until Chrome's View > Developer > "Allow JavaScript from Apple Events" is enabled; Abhijeet asked to flip it — **the v4.14 bridge has the same dependency, verify it FIRST**) · `apply_verdicts.py` (close from verified dead-list).
**Re-enable criteria for the scanner:** bridge live + run's new tasks externally liveness-verified clean ×2 + run ledger written by run_log.py (not self-reported).

### → DAY-3 PRIORITY 0 for Claude Code: v4.14 extraction bridge (approved)
Root cause of all scanner corruption (June 10–11): the LLM carries job data through its context and FABRICATES ids under context pressure (sequential `2064750000000000001...`, padded `...1234567890666` — caught by v4.13 guards, quarantined 53 rows, but degraded runs waste their hour). Fix: **data never enters model context.**
Build `scripts/feed_bridge.py` + `scripts/clickup_push.py`:
- `feed_bridge.py <feed>`: osascript executes the NUXT-state JS in Chrome's active tab → JSON straight to Python → Iron-Law validation (ciphertext-only ids) → claim via the upwork-job-recorder contract → stdout one-line summary {new, known, claimed_ids}. Job bytes flow machine-to-machine.
- `clickup_push.py`: creates tasks FROM DB ROWS (verbatim title, URL from DB, body assembled deterministically; the only LLM content is the draft proposal text read from the drafts/jobs row). Zero fabrication surface.
- Scanner prompt v4.14 shrinks to: account check (model) → navigate feeds (model) → bash bridge ×3 → score+draft new jobs (model, judgment only, from DB) → bash push → bash email → bash reaper (`board_cleanup.py --reap`) → log run via a `run_log.py` helper (literal task_name='upwork-job-scanner', `date -u` timestamps, runs row inserted as 'running' at start — fixes the fabricated-ledger defect from run 2098).
- Test against live Chrome; 2 clean runs before replacing v4.13.
**Ledger defects to fix with it:** run 2098 logged task_name='scan_jobs' + invented started_at 06:00:00; two runs wrote no ledger row at all.

## Standing cautions
- v2 OWNS the Upwork OAuth refresh until M1 cutover — v3 reads `../etherwise-os/.credentials/upwork-api.json` READ-ONLY, never refreshes (token-race rule, architecture §9).
- Stale duplicate creds dir at `../.credentials/` (workspace root) — flagged for deletion with Abhijeet's OK (PL-10, week 2).
- Agent SDK credit claimable June 15 (one-time opt-in on his Max account — remind him). Fable-class free on Max only through June 22.
- Never commit: var/, knowledge-inbox payloads, anything from .credentials.
- **SQLite WAL does not work through the Cowork FUSE mount** (disk I/O error) — all DB operations run on the Mac (Claude Code / launchd / terminal), never from Cowork's sandbox. Cowork sessions read the DB via `mode=ro` URIs if needed.

## Calendar
- 2026-06-15: claim Agent SDK credit; flip `ETHERWISE_BILLING=credit`
- 2026-06-22: Fable-included window ends (architect sessions → Opus 4.8 or usage credits)
- 2026-08-15: review Haiku 4.5 pin (dated snapshot retires not before Oct 15)
- Day 3: import-v2 parity report + baseline metrics snapshot ◆ · scoring model eval (Haiku vs Sonnet) ◆
- Day 4: M1 (Upwork) design session ◆
