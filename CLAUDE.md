# Etherwise OS v3 — Agent Manual

**Read order, every session: `BUILD_BRIEF.md` (current state + next actions) → this file → `SOUL.md` (persona + hard lines).**

Owner: Abhijeet Gandhi (contact@etherwise.io) · Pune, IST, works 12 PM–4 AM · Mac mini, this repo: `~/Desktop/Etherwise/etherwise-v3/`

## The one process rule
**Abhijeet is a core part of development. Ask him questions liberally — quality over speed, no question too small, no assumption where a question would do.** Design decisions get asked, not guessed. When he's unavailable, leave the decision visibly open in BUILD_BRIEF rather than assuming.

## What this is
Complete rebuild of the agency OS. Spec: `../business/etherwise-v3-core-architecture.md` (23 locked decisions — read it before changing anything structural). Capability list: `../business/etherwise-v3-master-build-catalog.md`.

**v2 (`../etherwise-os/`) is LIVE in parallel. Do not touch it. Shadow rules:**
- v3 makes ZERO external writes (Airtable/ClickUp/email/Upwork) while a module's `SHADOW_MODE` flag is True in `core/config.py` — intended writes go to `shadow_ledger`
- v2 owns Upwork OAuth refresh; v3 reads `../etherwise-os/.credentials/upwork-api.json` READ-ONLY, never refreshes

## Conventions
- stdlib-only Python (system python3); the Anthropic/Agent SDK is the one external dependency
- All model strings, IDs, ceilings, schedules: `core/config.py` ONLY — never hardcode elsewhere; never add sampling params (temperature/top_p/top_k)
- All Claude calls go through `core/claude_gateway.py` (when built) — never import anthropic directly in modules
- DB: `with connect() as conn:` tight transactions; Claude calls OUTSIDE the with-block; idempotent tasks; canonical-key lookup before insert; nothing silently dropped (use `quarantine`)
- Airtable: typecast always, ≤10/batch, field-ownership rules (system-wins vs human-wins per architecture)
- Logging: IST timestamps, daily files in `var/logs/`; every task writes a `runs` row
- No emojis in code/commits; direct no-fluff tone; never echo credentials

## Hard lines (also code-enforced — see SOUL.md)
Drafts only on Upwork, never send · terminal statuses never flip · no deletes outside allowlists · core/config + whitelists + hooks are not agent-writable (propose diffs instead) · client-facing facts require KB citations (CONFIRMED/CROSS-VERIFIED)

## Quick commands
```
PYTHONPATH=. python3 -m core.db          # apply migrations
PYTHONPATH=. python3 bin/doctor          # self-audit (Day 2+)
sqlite3 var/etherwise.db ".tables"
```
