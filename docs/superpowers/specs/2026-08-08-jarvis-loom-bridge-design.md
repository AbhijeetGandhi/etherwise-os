# jarvis-loom-bridge — design

**Date:** 2026-08-08 · **Author:** build session (Cowork night-shift delegation) · **Mirrors:** `bin/jarvis-fathom-bridge` + `modules/jarvis/fathom_bridge.py`

## Goal
Attribute the 453 unattributed `source_type='loom'` rows (vs 6 already attributed) to
clients through the registry, exactly as the Fathom bridge drained Fathom. Looms are
Abhijeet's own one-way recordings explaining systems he BUILT for clients — the richest
"what we actually deliver" evidence in the KB. Deterministic-first, `$0` where possible,
idempotent, re-runnable, NEVER guesses.

## The key difference from Fathom
Fathom calls have **participants** (attendee emails) — the external-participant email is
the signal that separates a real client call from an internal/owner-only one. **Looms have
NO participants** (`parse_loom` returns `participants: []`). So the internal-vs-client split
cannot use participants; it has to come from title + transcript content.

### Landmine that shapes the design
Of the 6 already-attributed Looms, one is titled *"Showcasing My Automation Engineering
Project"* → `horizons-aba` (a real client delivery demo). A generic self-pitch-sounding
title was a real client recording. **Therefore deterministic keyword exclusion of
"internal/owner-only" recordings is unsafe** — it would drop real client evidence.

## Signals, in strict precedence (deterministic first, exactly like Fathom)
1. **Title registry match** (`$0`) — a client name / alias / `fathom_names` token appears in
   the Loom title → **auto-heal**.
2. **Transcript registry strong-hit** (`$0`) — a client name / alias / email-domain-root
   appears in the transcript body with a STRONG hit → **auto-heal**. Strong hit =
   `(total needle mentions ≥ 2)` OR `(≥1 mention of a DISTINCTIVE needle)`. Distinctive =
   multi-word alias OR alnum length ≥ 6 (so "hermon"/"adelye"/"Adélye Ltd." qualify; a bare
   4-char token or a passing tool-name mention does not). If two+ clients strong-hit the same
   transcript → ambiguous → NOT healed (falls through to queue).
3. **Temporal correlation** (`$0`, PROPOSAL ONLY, never auto-map) — a Loom recorded within
   ±2 days of exactly one attributed client (attributed Fathom `sources.occurred_dt` +
   Upwork `threads.latest_message_dt` / `messages.created_dt` via `threads.client_id`) →
   **queue** with that proposed client + temporal evidence.
4. **Residue** (no deterministic signal), capped LLM proposer — see below.

## Residue: the capped Haiku proposer (the only thing that can EXCLUDE)
For rows no deterministic pass placed, up to a per-run cap (`config.JARVIS_LOOM_MAX_PROPOSALS`
= 25, `--proposer-cap N` override), call ONE Haiku (`classify` model) proposer per row through
`ctx.claude` (reader call, no tools, strict JSON, transcript passed as UNTRUSTED DATA — same
injection boundary as `modules/jarvis/mapping.py`). Schema:
`{proposed_client_id|null, is_internal (bool), confidence, evidence}`.
- `is_internal == true` (and no client) → **exclude** the event, reason `internal:<evidence>`
  (product demo / personal test / generic pitch with no specific client).
- valid `proposed_client_id` → **queue** as a proposal (human one-click assign; the LLM is
  NEVER authoritative — no auto-heal from a model).
- `null` and not internal → **queue** with no proposal (unknown client, needs a human).
- A hallucinated id not in the candidate set is dropped to null (never trusted).

Rows beyond the proposer cap this run get **no event** and stay unmapped → the next run
re-evaluates them (`$0` deterministic passes are stable) and LLMs them as slots free up. So
the 453 drain across runs without ever re-spending on a row that already got a decision.

## Auto-heal (decision 2, documented for reversal)
**Only deterministic registry matches auto-heal** (signals 1 & 2) — reusing
`fathom_bridge.heal_source` verbatim (the spec: "the same heal must fire as Fathom's"):
reattribute the source + its facts, set `content_type='client-call'` (matches the existing 6
attributed Looms), recompile that client's dossier. Idempotent; only touches unmapped rows.
Temporal (3) and LLM (4) NEVER heal — they queue.

## Idempotency
For each unmapped Loom source, compute `eid = events.canonical_id("loom", <video_id>)` and
**skip if that event already exists** BEFORE any transcript read or LLM spend. A healed row
has `sources.client_id` set so it leaves the unmapped scan. A queued/excluded row has an
event → skipped on re-run. Nothing is processed twice; no LLM call is ever repeated.

## `--recheck`
Re-evaluate `state='queued'` Loom events after the registry learns new aliases/domains
(mirrors `fathom_bridge.recheck_queued`): re-run title + transcript deterministic match; a
now-match **heals** + `events.set_mapped`; otherwise leave queued. (Excluded rows are left
alone — un-excluding is a human/registry decision, not automatic.)

## Bounds / guardrails
- Deterministic passes are `$0`. LLM only on residue, ≤ `--proposer-cap` (default 25) per run.
- `--limit N` (default 50) newest-first scan cap, like Fathom.
- Empty scan → `runner.TaskSkip` → `skipped_empty` (clean, spends nothing).
- No external writes (this is DB + local dossier files only; the KB module is not gated by
  `SHADOW_MODE` for internal writes — same as the Fathom bridge).

## Report (printed by the bin + returned metrics)
`scanned / auto_healed / queued / excluded / held(over-cap) / skipped_existing`, plus top
clients by newly-attributed Loom count and total facts re-attributed.

## Two decisions taken WITHOUT Abhijeet (he was unavailable — reverse if wrong)
1. **Exclusion is LLM-decided only** (deterministic never excludes) — forced by the
   "Showcasing My…" landmine.
2. **Only deterministic registry matches auto-heal**; temporal + LLM only queue.

## Files
- `modules/jarvis/loom_bridge.py` — the logic (mirrors `fathom_bridge.py`).
- `bin/jarvis-loom-bridge` — CLI (mirrors `bin/jarvis-fathom-bridge`; adds `--proposer-cap`).
- `core/config.py` — `JARVIS_LOOM_MAX_PROPOSALS = 25` (ceilings live in config only).
- `tests/test_loom_bridge.py` — unit tests (deterministic passes, strong-hit rule, idempotency,
  LLM exclude/queue with a stub `ctx.claude`, recheck, empty-skip).
