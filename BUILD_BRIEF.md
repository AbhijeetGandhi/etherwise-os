# BUILD BRIEF — read this first, every session
**Updated:** 2026-06-10 (Day 1) · **Owner of this file:** updated at every checkpoint, never stale.

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

## Next actions (Day 2)
1. **Git first-aid then first push** (Cowork's mount can't delete files, so Day-1's commit attempt left locks): `cd ~/Desktop/Etherwise/etherwise-v3 && rm -f .git/index.lock .git/objects/*/tmp_obj_* && git add -A && git commit -m "Day 1: kernel scaffold" && git push -u origin main`. Lesson recorded: git commits + DB ops happen on the Mac only; Cowork authors files and designs.
2. `core/claude_gateway.py` — single LLM door: billing route (payg until June 15 → flip config to credit), 1h prompt caching, per-run ceilings, cost logging, structured outputs (never on Fable), sampling params stripped.
3. `core/runner.py` — run ledger, retries+backoff, model failover chains, single-instance locks, IST logging, shadow enforcement.
4. `core/guardrails.py` + `.claude/hooks/` — config SHA256 check, deny rules (terminal status, typecast, sends, deletes), audit log.
5. `bin/doctor` — creds modes, model-ID validity vs /v1/models, deprecation dates, plist integrity, cockpit auth.
6. Verify python3 version on the Mac; `rm var/etherwise.db*` (Day-1 leftover — SQLite WAL can't run through the Cowork FUSE mount, so the sandbox attempt left an empty artifact), then `PYTHONPATH=. python3 -m core.db`. Migration SQL itself is validated (ran clean on a real filesystem: 8 tables + 3 indexes, integrity ok).

## Scanner v5 — first run (2026-06-10, scan-20260610T152542) ✅
50 cards / 45 unique / 4 new → 2 Hot (incl. a 30 Hot+Loom) + 2 floor-skips · email sent · all self-checks green · dedup ledger held against an overlapping old-task run. **Prompt patched to v4.11 after the run:** correct My Feed URL (`/nx/find-work/my-feed`), prefer `window.__NUXT__.state` page-state JSON over DOM scraping (exact ids/hrefs/verbatim titles), n8n-in-tags does NOT trigger exclusion (decided 2026-06-10 — both best leads carried the tag), non-ASCII title re-verification, check-before-retry on ClickUp create errors.
**→ M1 Chrome prototype must inherit the `__NUXT__.state` extraction technique — it is dramatically more reliable than DOM scraping.**
DONE 2026-06-10: model pinned Sonnet 4.6 on v5 (UI) · old `upwork-job-scanner` DISABLED (rollback = re-enable; delete ~July 10 along with retired `upwork-job-scanner-v2`). The model-pin rot that motivated this rebuild is fixed on the one surface that keeps it.

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
