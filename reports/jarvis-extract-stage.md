# Jarvis EXTRACT stage — build report (2026-08-08, night shift)

Closes sweep stage 3 (`business/etherwise-jarvis-architecture-2026-08.md` §4.3): the sweep now has
a path from MAPPED events → candidate knowledge + action, SHADOW-FIRST (staging only).

## What landed
- **`core/migrations/021_extract_staging.sql`** — `extract_staging_facts` / `_commitments` /
  `_closures` (run-id keyed, citation/source_cite/evidence NOT NULL), `events.extracted_at`
  idempotency marker, and `facts.access_scope` (founder|team — the §4.4 access wall, distinct from
  the existing `facts.scope` artifact axis). Applied to the live DB (non-destructive, additive).
- **`modules/jarvis/extract.py`** — the READER: Sonnet (`MODELS["extract"]`), **NO tools**,
  structured output, content as UNTRUSTED DATA (mapper's injection guard reused). Per-client
  bundle. All model output is validated by CODE, never trusted:
  - **provenance law:** each item's `event_ref` must be a REAL permalink from the bundle + an
    anchor quote, else DROP + count (`dropped_uncited`). Kills injected/hallucinated citations.
  - **scope hard rule:** `founder` iff `EXTRACT_FOUNDER_SCOPE_TERMS` (money/pricing/invoice/salary/
    contract-value/HR) hit; else `team`. Never model discretion.
  - **mint-bar** (project memory `commitment-extraction-bar`): a commitment is minted only with
    (client|owner) AND (due|ask); otherwise it DEMOTES to a fact (`origin='demoted_commitment'`).
- **`modules/jarvis/extract_staging.py`** — staging DAL: bundling (mapped + unextracted, per-client
  caps), inserts, review reads, and **idempotent promotion** of facts+commitments into the live
  tables (`promoted_at` guard + commitments `dedup_key` UNIQUE). Closures are staged only.
- **`bin/jarvis-extract`** — runs the staging-only extract (module=system: zero external writes).
- **`bin/jarvis-extract-review`** — readable diff ("would add N facts to client X, N commitments,
  N closures" with a citation on every line + measured $); `--promote` applies after review.
- **`tests/test_jarvis_extract.py`** — 11 tests: injection canary, mint-bar (+ owner branch), scope
  rule, promotion idempotency, empty-skip=0 LLM, provenance drop, per-run ceiling, per-client
  bundling, staging-only, rerun-skips-extracted.
- **`core/config.py`** — `MODELS["extract"]`, `JARVIS_EXTRACT_*` ceilings, `EXTRACT_FOUNDER_SCOPE_TERMS`.

## Cost discipline (as specified)
Per-client bundles (not per-event) · 1h prompt cache (gateway) · empty-skip before any call ·
per-run ceiling `JARVIS_EXTRACT_MAX_CLIENTS=8` · gateway budget guards · `cost_usd` in run metrics.

## Verification status — ACTION NEEDED (honest)
This night-shift session's permission mode gated **all `python3` execution** behind interactive
approval that did not grant autonomously (only the documented `-m core.db` migration passed). So:
- The 11-test suite is **written and statically traced against the implementation, but NOT yet
  executed.** I am not claiming it green.
- The **dry run + measured cost** the spec asks for could not be produced (it needs a live Sonnet
  call, also gated).

**One-time approvals to finish (run on the Mac):**
```
PYTHONPATH=. /usr/bin/python3 -m unittest tests.test_jarvis_extract   # suite green?
PYTHONPATH=. /usr/bin/python3 bin/jarvis-extract                      # dry run -> staging (real $)
PYTHONPATH=. /usr/bin/python3 bin/jarvis-extract-review --run-id <id> # counts + citations + cost
# then, after reviewing the diff:
PYTHONPATH=. /usr/bin/python3 bin/jarvis-extract-review --run-id <id> --promote
```

## Open decisions for Abhijeet (◆) — flagged, not assumed
1. **`MODELS["extract"]` pins the current Sonnet (`claude-sonnet-4-6`).** Architecture §5 names
   "Sonnet 5" ($2/$10→$3/$15) but no verified id/pricing exists yet — swap the one config line at
   the Sept-1 step. One-line change.
2. **Closure promotion is deferred (staging only).** A closure_signal is {intent, evidence, conf};
   wiring it to a specific open `card_id` is itself a model pass, and the spec says don't wire
   promotion into the scheduled sweep tonight. `--promote` covers facts+commitments; closures wait
   for the closure-wiring decision.
3. **`facts.access_scope` (founder|team) is a NEW axis** beside `facts.scope`. Promoted email facts
   default artifact scope='client'; the founder/team wall comes from the hard rule. Confirm the two-
   axis model before the team chatbot (J5) filters on it.
4. **Promotion is human-gated at the ◆.** Not scheduled. After you review the first real batch,
   we can decide whether promotion (facts+commitments) becomes automatic.
