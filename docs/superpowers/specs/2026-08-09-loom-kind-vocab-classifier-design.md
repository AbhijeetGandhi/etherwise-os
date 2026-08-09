# Loom KIND + domain-vocabulary classifier — design

**Date:** 2026-08-09 · **Author:** night-shift build (Cowork task QUEUE-12) · **Status:** built vs fixtures, awaiting green + Abhijeet review of the ◆ decisions below.

## Problem — the measured negative result (do not repeat)
`jarvis-loom-bridge` attributes Looms by matching registry client names in the
title/transcript. Measured tonight: across the **426 still-unattributed Looms
with full transcripts, searching every registry id/name/alias against the
transcript body matched only 3 — and those 3 are false positives on
job-application Looms.** 423 of 426 never say any client name.

Cause: when Abhijeet records a Loom *for* a client he narrates the system to
them ("the automation does X") and never says their name. **Deterministic
name-matching has no signal in the transcript body.** Queueing 88 of these for
manual review is a chore, not a decision — also wrong.

## Goal
Attribute the ~426 residue Looms as far as a bounded LLM safely can, and clean
the non-client Looms out of the KB — so the KB becomes "systems we shipped, per
client" (the raw material for the productization scout), not "conversations we
had". Every model output stays cheap, bounded, idempotent, and — by default —
**non-authoritative over the KB**.

## Data
- `sources` table (`source_type='loom'`): 459 rows, **426 unmapped** (`client_id IS NULL`).
- Per-source transcript via `sources.raw_path` → `ingest.parse_loom` (already
  wired in `loom_bridge._read_loom`; the same 485 Looms as
  `../assets/loom-transcripts-all.json`, but read through the DB so heal/events/
  idempotency all key off `sources`).
- `events` ledger: currently **88 `queued`**, **17 `excluded`** loom events.
- Registry: 70 matchable clients, but only **names/aliases/emails** — **no domain
  vocabulary**. Only ~18 clients have dossiers.

## Architecture — a pass pipeline, ledger-only for the model

Per unmapped Loom, in strict precedence:

0. **Deterministic pass-0 ($0, KEPT):** `loom_bridge.title_match` /
   `transcript_match`. A unique registry name-match → **full auto-heal** exactly
   as today (this is precise — verified against exact registry tokens; it healed
   27 correctly). Most residue Looms won't match; that's expected.
1. **One Haiku call (KIND + attribution together):** returns
   `{kind, proposed_client_id, confidence, evidence}`.
   - `kind ∈ {client-work, upwork-proposal, internal-product, personal-noise}`.
   - The model is given the compact per-client **domain-vocabulary fingerprints**
     (not names) as candidates, and is **explicitly allowed/encouraged to answer
     `unknown` (null client)**.
2. **Route on the result — the model writes ONLY to the `events` ledger:**
   - `kind != client-work` → **exclude** `loom-kind:<kind>` (cleans KB junk).
   - `client-work` + `confidence ≥ HIGH` + valid client id → **provisional
     auto-map** (`state='mapped'`, `resolved_by='loom_classifier:high'`, full
     provenance: proposed id + confidence + evidence). **Sources / facts /
     dossiers are NOT touched.** This is the "attributed" set the scout reads.
   - `client-work` + `MED ≤ confidence < HIGH` → **queue** with the proposed
     client + evidence phrase (one-glance confirm, not a transcript read).
   - `client-work` + `confidence < MED` OR `unknown`/null → **exclude**
     `loom:unattributable` (not queued — a chore is not a decision).

Temporal correlation (the old pass 3) is **dropped** — it proposed the busiest
client (Horizons) for ~62 Looms and the vocab signal is strictly better.

### Why ledger-only (D1, the consequential decision)
`heal_source` doesn't just tag attribution — it promotes the Loom's facts into
the client's dossier (`facts.scope='client'`) and recompiles it. The dossier
compile pulls facts `WHERE client_id=? AND scope='client'`. So a wrong
high-confidence guess would inject a **false client-facing fact** — colliding
with two hard lines: *"model never authoritative; only deterministic registry
matches auto-heal"* and *"client-facing facts require KB citations"*.

Default here: **the LLM never writes to sources/facts/dossiers.** A high-conf
result is a provisional map in the events ledger — visible to the scout,
reversible, and it asserts no uncited fact. A future human/`--promote` step
(out of scope; mirrors `jarvis-extract-review --promote`) converts confirmed
provisional maps into real dossier heals. Config flag
`JARVIS_LOOM_HEAL_ON_HIGH=False` documents the one-line upgrade to the
task-as-written full auto-heal, if Abhijeet chooses to grant that authority.

## Domain-vocabulary fingerprints (D2)
`bin/jarvis-loom-fingerprints` runs one LLM pass per dossier → a compact record
`{client_id, name, domain, stack[], terms[], people[], systems[]}` →
**committed `assets/loom-fingerprints.json`** (reviewable, hand-editable,
re-runnable when dossiers grow). Classify-time loads this asset only (no per-run
dossier reading). Missing asset → a clean error telling Abhijeet to build it.
The named exemplars (Horizons=ABA/BCBA/session-cancellation/Fingertrack;
Adelye=course-registration/FORS-CPC/GoCanvas; Beglaubigt=notarisation/HubSpot/
Make→n8n) are hand-seeded so tests have deterministic fixtures.

## Clearing the 88 queued (D7)
`bin/jarvis-loom-classify --reclassify-queued` re-runs the classifier over the
existing `queued` loom events and re-routes each (exclude / provisional-map /
re-queue with a better evidence phrase), so the queue stops being a chore list.

## Cost discipline (D8)
Haiku (`MODELS["classify"]`); transcript truncated to the **first ~1,500 words**
(attribution signal is front-loaded); empty-skip before any call; per-run ceiling
`JARVIS_LOOM_CLASSIFY_MAX` + `--limit`; idempotent (a decided event gates re-work
BEFORE any transcript read or LLM spend). 426 items ≈ $1–2 — report the actual.

## Confidence bands (D5)
`JARVIS_LOOM_KIND_HIGH = 0.80` (provisional map) · `JARVIS_LOOM_KIND_MED = 0.50`
(queue) · below MED or unknown → exclude `loom:unattributable`.

## New / changed
- `modules/jarvis/loom_classifier.py` — kind+vocab classifier, routing,
  reclassify-queued, fingerprint loader (pure functions + injectable transport).
- `bin/jarvis-loom-classify` — `[--limit N] [--max N] [--dry-run]
  [--reclassify-queued]`.
- `bin/jarvis-loom-fingerprints` — `[--limit N] [--dry-run]` build/refresh asset.
- `assets/loom-fingerprints.json` — committed data (hand-seeded exemplars +
  builder output).
- `core/config.py` — `JARVIS_LOOM_KIND_HIGH/MED`, `JARVIS_LOOM_CLASSIFY_MAX`,
  `JARVIS_LOOM_TRANSCRIPT_WORDS`, `JARVIS_LOOM_HEAL_ON_HIGH`,
  `LOOM_FINGERPRINTS_PATH`.
- `modules/jarvis/events.py` — `set_auto_mapped(conn, eid, client_id, confidence,
  evidence, by)` (provisional map w/ full provenance; distinct from a
  deterministic heal, which also writes sources+facts).
- `tests/test_loom_classifier.py`.

No migration — reuses existing `events` columns.

## Tests
- KIND fixtures: a job-application Loom and a "Men's Jackets | REI" personal Loom
  must NOT become client-work (→ excluded).
- `unknown`/null client is respected (→ `loom:unattributable`, not a forced map).
- High→provisional-map, Med→queue, Low→exclude routing.
- Idempotency: a decided event is skipped before any transport call ($0 re-run).
- `--reclassify-queued` re-routes an existing queued event.
- Provisional map writes ONLY the events ledger — sources/facts untouched (guard).

## ◆ OPEN (Abhijeet — safe defaults taken, all reversible)
1. **Heal authority (D1):** default = provisional (ledger-only), facts gated.
   `JARVIS_LOOM_HEAL_ON_HIGH=True` upgrades high-conf to full dossier heal.
2. **Fingerprints (D2):** LLM build-step → committed reviewable asset. Alt:
   hand-curate in the registry, or deterministic TF-IDF extraction.
3. **Deterministic pass-0 (D3):** kept as $0 first pass. Alt: fully replace.
4. **Bands (D5):** 0.80 / 0.50. Tune after the first real batch.
