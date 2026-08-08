# Jarvis EXTRACT stage — design (2026-08-08)

Sweep stage 3 of the locked architecture (`business/etherwise-jarvis-architecture-2026-08.md`
§4.3). Today the sweep COLLECTs and MAPs; nothing turns mapped events into knowledge or action.
This closes that — SHADOW-FIRST (staging only), source-agnostic (Gmail today; Slack/Teams later).

## The pipeline (per sweep, per client-bundle of newly-mapped events)

```
mapped events (state='mapped', extracted_at IS NULL)
   └─ group by client_id  (per-CLIENT bundle, not per-event — cost §4)
        └─ empty-skip: no unextracted bundle ⇒ TaskSkip, ZERO LLM calls
        └─ per-run ceiling: at most JARVIS_EXTRACT_MAX_CLIENTS bundles/run
             └─ READER call (Sonnet, NO TOOLS, structured output, 1h cache)
                  content = UNTRUSTED DATA (mapper's guard + injection canary)
                  returns facts[] · commitments[] · closures[]
             └─ CODE post-processing (never model discretion):
                  · provenance law: event_ref ∈ bundle permalinks else DROP+count
                  · scope HARD RULE: founder iff money/pricing/invoice/salary/
                    contract-value/HR term hit; else team
                  · mint-bar: commitment minted iff (client|owner) AND (due|ask);
                    else it DEMOTES to a fact (origin='demoted_commitment')
             └─ write to STAGING tables (run_id-keyed) — never live facts/cards
        └─ mark those events extracted_at (idempotent — re-run never re-extracts)
```

## Injection boundary (§4.4/§4.9 — trifecta broken)
READER sees untrusted email text, has NO tools, returns JSON only. A hostile email can at worst
produce a wrong *staged* candidate that Abhijeet reviews; it can never call a tool, write live, or
send. Hardened system prompt (UNTRUSTED DATA / NEVER follow) + a hallucinated `event_ref` or
`client_id` is refused (validated against the bundle, mirror of the mapper).

## Shadow-first (non-negotiable)
Everything lands in `extract_staging_facts` / `_commitments` / `_closures` with the run id.
`bin/jarvis-extract-review` prints a readable diff ("would add N facts to client X, N commitments,
N closures — with citations"); `--promote` applies facts+commitments to the live tables AFTER
Abhijeet's review. Promotion is idempotent (staging rows stamp `promoted_at`). NOT wired into the
scheduled sweep — staging only.

## Provenance law
A fact or commitment without a citation link is INVALID — dropped before staging, counted in
`dropped_uncited`. citation = `<permalink> @ <anchor-quote>`.

## Scope (founder|team) — the §4.4 wall, code-enforced
Distinct axis from `facts.scope` (client|agency|cross-client artifact routing). New
`facts.access_scope` (founder|team) carries the data-layer wall the future team chatbot filters on.
Set by `EXTRACT_FOUNDER_SCOPE_TERMS` (config), never the model.

## Cost discipline
Per-client bundles · 1h prompt cache (gateway) · empty-skip before any call · per-run ceiling
(`JARVIS_EXTRACT_MAX_CLIENTS`, default 8) · gateway budget guards. `cost_usd` reported per run.

## Deferred (flagged for Abhijeet's ◆)
- closure_signals are STAGED only; wiring them into the live cross-source closure pass
  (matching a signal to an open card_id is itself a model pass) is deferred — the architecture
  says don't wire promotion into the scheduled sweep tonight.
- `MODELS["extract"]` pins the current Sonnet (`claude-sonnet-4-6`); the "Sonnet 5" of §5 has no
  verified id/pricing yet — swap the one config line when it does (calendar item).

## Tests (all in tests/test_jarvis_extract.py)
injection canary · mint-bar (no due/ask ⇒ fact) + owner branch · scope rule (money/HR ⇒ founder) ·
promotion idempotent · empty-skip = 0 LLM · provenance drop · per-run ceiling · per-client bundling.
