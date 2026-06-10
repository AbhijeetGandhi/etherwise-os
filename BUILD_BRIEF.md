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

## Next actions (Day 3, per build sequence)
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
