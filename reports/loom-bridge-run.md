# jarvis-loom-bridge — first live run (2026-08-08)

Mirrors `bin/jarvis-fathom-bridge` for the 453 unattributed `source_type='loom'`
rows (Abhijeet's own one-way recordings of systems he built for clients — the
richest "what we deliver" evidence in the KB).

## Run (`--limit 500 --queue-cap 40 --proposer-cap 25`)
| metric | value |
|---|---|
| scanned | 453 |
| **auto-healed (deterministic)** | **27** |
| queued (proposals) | 48 → 88 after a 2nd deterministic pass |
| excluded (LLM: internal) | 17 |
| held (over proposer cap → next runs) | 361 |
| Haiku proposer calls | 25 |
| facts re-attributed | 0 (see note) |

Loom sources now attributed: **33** (6 prior + 27 healed). 426 remain unmapped
(drip-drained across future runs; deterministic passes are $0, ≤25 Haiku/run).

### Top clients by newly-attributed Loom count
- Horizons ABA — 23 · College Life — 3 · VANTAGE — 1 (dossiers recompiled)

### Why facts_reattributed = 0
Loom bulk extraction has NOT run yet (`modules/knowledge/daily.py` excludes the
Loom backlog from the drip). Healing re-attributes the SOURCE, so future
extraction lands its facts on the right client. There are simply no Loom facts to
move yet — this is expected, not a miss. (Fathom's 152 re-attributions existed
because Fathom facts were already extracted.)

## Signal precedence (deterministic first, $0 where possible)
1. **Title registry match** → auto-heal. 0 fired (Loom titles are descriptive, not
   client-named).
2. **Transcript registry strong-hit** → auto-heal. All 27 heals. Strong = a
   DISTINCTIVE needle (multi-word, or alnum ≥6) once, or ≥2 mentions of a shorter
   one. Every heal fired on a curated registry token (`horizons`/`horizons aba`
   [clients.yaml fathom_names], `college life`, `vantage`, `adelye ltd.`).
3. **Temporal correlation** (±2d of exactly one attributed Fathom call / Upwork
   thread) → QUEUE a proposal, never a heal.
4. **Residue** → capped Haiku proposer: `is_internal`→exclude, valid id→queue,
   null→queue. The ONLY path that can EXCLUDE.

### LLM quality (25 calls)
- **17 excluded as internal** — all correct: founder self-intros ("My Journey in
  AI and Automation"), Upwork job pitches ("Excited to Apply…", "Showcasing My
  Expertise…"), generic product demos, admin-panel guides. No client evidence lost.
- **8 queued proposals** — sensible: ABA/BCBA/autism/insurance content → horizons-aba,
  "Simeon" direct address → simyon-pinsky, "Vidali" greeting → vantage.

## Bug caught + fixed before trusting any heal
First dry-run auto-healed **95 Looms to Endura** — false. Endura's email is
`endurasolutions.com.au`; the old domain-root logic took `parts[-2]` = **`com`**,
and `com` (Make.com / Monday.com / Cal.com) racked up mentions. Fix: the brand
label is the FIRST domain label (`endurasolutions`; also fixed `hipp.health`→`hipp`),
and any needle under 4 alnum chars is dropped. Post-fix: 27 precise heals, every
one verified against the exact matched registry token.

## ◆ OPEN DECISION FOR ABHIJEET — temporal is noisy (I did NOT change the spec)
80 of the 88 queued rows are **temporal**; **62 of those propose horizons-aba**.
Because Horizons dominates the Fathom calendar, almost every Loom falls within ±2
days of a Horizons call, so pure-temporal degenerates into "propose the busiest
client for everything nearby" — guess-quality, which rubs against "NEVER guess."
It is spec-faithful (signal #3, proposal-only, never heals, bounded per run) and
fully reviewable/reversible in the mapping queue, so I shipped it as specified
rather than silently deviating. **Recommendation (one-line change, your call):**
gate temporal on a weak content co-mention (the correlated client's name appears
≥1× in the transcript) so every queued proposal carries BOTH time AND content
evidence — OR lower temporal below the LLM so the LLM reads those transcripts
first. Left open in BUILD_BRIEF.

## Two decisions taken without you (unavailable) — reverse if wrong
1. **Exclusion is LLM-decided only** — deterministic never excludes. Forced by a
   landmine: "Showcasing My Automation Engineering Project" (a generic self-pitch
   title) is a REAL horizons-aba delivery demo; keyword exclusion would drop it.
2. **Only deterministic registry matches auto-heal**; temporal + LLM only queue
   (the model is never authoritative for attribution — architecture §4.1/§4.4).

## Idempotency / safety
- A healed row leaves the scan (`client_id` set); a queued/excluded row leaves an
  event that gates re-work BEFORE any transcript read or LLM spend. Verified: a $0
  re-run re-healed 0 and skipped all 65 decided rows. No row is ever re-proposed.
- Heals commit per-row before any LLM call; proposer failures are caught (held,
  never crash). On-demand only (no launchd — same as the Fathom bridge).
- `--recheck` re-heals queued rows after the registry learns a new alias/domain.
