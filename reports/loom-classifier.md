# Loom KIND + domain-vocabulary classifier — build report

**Date:** 2026-08-09 (night shift) · **Task:** Cowork QUEUE-12 · **Status:** built,
**full suite 903 GREEN**, NOT run live, NOT yet acting on the 426 (gated on your go).

## Why the old approach was replaced (your measured negative result)
Name-matching healed 27/453 and queued 88. You proved the ceiling: across the 426
unmapped Looms with transcripts, every registry id/name/alias searched against the
transcript body matched only **3 — all false positives**. When you record a Loom
*for* a client you narrate the system to them and never say their name.
Deterministic name-matching has no signal in the residue, and queueing 88 is a
chore, not a decision.

## What was built
A KIND-first, domain-vocabulary LLM classifier. One Haiku call per residue Loom
returns `{kind, proposed_client_id, confidence, evidence}`:

1. **KIND** ∈ `client-work | upwork-proposal | internal-product | personal-noise`.
   Only `client-work` proceeds; the rest are excluded with their kind as the
   reason (cleans job-application / product-demo / personal junk out of the KB).
2. **Attribution by DOMAIN VOCABULARY, not names.** The model gets a compact
   per-client fingerprint (domain, stack, terms, people, systems) and matches the
   Loom's vocabulary to one client — or answers **`unknown`** (encouraged).
3. **Routing** (bands in config): high (≥0.80) → provisional map · medium
   (0.50–0.80) → queue with the proposed client + evidence phrase (one-glance
   confirm) · low / unknown / non-client → excluded (`loom:unattributable` or
   `loom-kind:<kind>`).

### The safe default that preserves the hard line
`heal_source` promotes a Loom's facts into the client dossier (`scope='client'`)
and the dossier compile surfaces facts `WHERE scope='client'` — so a wrong
high-confidence guess would inject a **false client-facing fact**, against the
"model never authoritative" / "facts require citations" hard lines.

**Default here: the LLM writes ONLY the events ledger — never sources/facts/
dossiers.** A high-confidence result is a reversible provisional map
(`state='mapped'`, `resolved_by='loom_classifier:high'`, full provenance) that the
productization scout reads; it asserts no uncited fact. Deterministic registry
name-matches (pass-0, kept) still do the full heal because they are precise.
`JARVIS_LOOM_HEAL_ON_HIGH=False` is the one-line switch to grant high-confidence
inferences full dossier-heal authority — **your call** (see ◆ below).

## Files
- `modules/jarvis/loom_classifier.py` — kind+vocab classifier, pure `route()`,
  ledger writers, `reclassify_queued`, fingerprint loader + builder.
- `bin/jarvis-loom-classify` — `[--limit N] [--max N] [--dry-run]
  [--reclassify-queued]`.
- `bin/jarvis-loom-fingerprints` — build/refresh the fingerprint asset.
- `assets/loom-fingerprints.json` — **hand-seeded 12 clients** from the dossier
  overviews (Horizons, Adelye, Beglaubigt, Endura, NetFly, Cradle, Medical
  Kitchen, HIPP Health, College Life, Hermon, Renutech, Hero). Reviewable +
  editable. Builder writes `source='llm'`; seeds are `source='seed'`.
- `core/config.py` — `JARVIS_LOOM_KIND_HIGH/MED`, `JARVIS_LOOM_CLASSIFY_MAX`,
  `JARVIS_LOOM_TRANSCRIPT_WORDS`, `JARVIS_LOOM_HEAL_ON_HIGH`, `LOOM_FINGERPRINTS_PATH`.
- `modules/jarvis/events.py` — `set_auto_mapped()` (provisional map, full
  provenance, distinct from a deterministic heal).
- `tests/test_loom_classifier.py` — 26 tests. No migration (reuses `events` cols).

## Tests — 26, all green (full suite 903)
Pure bands; `classify_loom` normalization (hallucinated id → null, bad kind →
unattributable, non-client forces null client, confidence clamp, failure → None);
**KIND gate** (a job-application Loom and a "Men's Jackets | REI" personal Loom
must NOT become client-work); **`unknown` respected** (not forced to a map);
high→provisional-map / med→queue / low→exclude; **the ledger-only guarantee**
(a provisional map leaves `sources.client_id` and `facts.client_id` untouched,
`facts_reattributed=0`); deterministic pass-0 still full-heals (and spends $0);
`JARVIS_LOOM_HEAL_ON_HIGH=True` promotes to a full heal; idempotency (a decided
event gates re-work, 0 LLM calls on re-run); per-run ceiling; `--dry-run` spends
but writes nothing; `--reclassify-queued` re-routes the existing 88; empty-skip.

## NOT done here (gated on your go — spends real money + writes the live ledger)
The live run was NOT executed: it spends Haiku (~$1–2 for 426) and writes event
rows the running sweep + cockpit read. Left for your greenlight. Recommended
sequence on the Mac:

```
# 1. (optional) build richer fingerprints for all 17 dossiers, then eyeball the asset
PYTHONPATH=. /usr/bin/python3 bin/jarvis-loom-fingerprints            # ~$0.20
#    review assets/loom-fingerprints.json, edit any vocab

# 2. DRY-RUN the classifier — kind distribution + per-client counts + cost, 0 writes
PYTHONPATH=. /usr/bin/python3 bin/jarvis-loom-classify --dry-run

# 3. APPLY once the dry-run looks right (idempotent; re-runnable; bounded)
PYTHONPATH=. /usr/bin/python3 bin/jarvis-loom-classify

# 4. drain the existing 88 queued Looms through the same pass
PYTHONPATH=. /usr/bin/python3 bin/jarvis-loom-classify --reclassify-queued
```

The kind distribution, per-client attribution counts, remaining-unknown count,
and actual cost get reported by those runs — I could not measure them offline.

## ◆ OPEN (your call — safe defaults taken, all reversible)
1. **Heal authority.** Default = ledger-only provisional (facts gated). Flip
   `JARVIS_LOOM_HEAL_ON_HIGH=True` to let a high-confidence inference do the full
   dossier heal, as your task literally described. I defaulted safe because it
   reverses the "model never authoritative" hard line and can inject a wrong
   client-facing fact.
2. **Fingerprints.** 12 hand-seeded from dossier overviews; run
   `bin/jarvis-loom-fingerprints` to LLM-build all 17 + refresh as dossiers grow.
3. **Deterministic pass-0 kept** as a $0 precise first pass (your 27 heals stay).
   "Replace" was applied to the residue approach, where you measured the failure.
4. **Bands 0.80 / 0.50** — tune after the first real dry-run.
5. **Temporal correlation dropped** — it proposed the busiest client (Horizons)
   for ~62 Looms; vocab is strictly better signal. (Closes that standing open
   decision from the loom-bridge block.)
