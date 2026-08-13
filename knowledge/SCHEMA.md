# Knowledge Base — Fact Protocol & Anti-Hallucination Rules (v3)

Ported from the v2 `knowledge-base/SCHEMA.md` (2026-06-16). In v3 these rules
are **enforced in code** (`modules/knowledge/kb.py` + the `004_kb.sql` schema),
not just convention. Storage: markdown dossiers/wiki in git + SQLite `kb`
domain + FTS; raw transcripts gitignored.

## Rule 1 — Source citation REQUIRED
Every persisted fact carries a citation: the source reference + a locator
(timestamp or short quote anchor), e.g. `fathom/2026-06-10__<rid>.json @ 00:12:34`.
A fact without a citation is **rejected** — `kb.validate_fact()` raises and the
DB enforces `citation NOT NULL`. No uncited fact survives.

## Rule 2 — Confidence tag REQUIRED (one of three)
- `CONFIRMED` — Abhijeet/the client directly stated it in a source.
- `CROSS-VERIFIED` — appears in 2+ independent sources.
- `INFERRED` — reasonable conclusion, not directly stated.

Enforced by `confidence CHECK IN (...)` in the schema. **The proposal-writer
may only use CONFIRMED / CROSS-VERIFIED facts.**

## Rule 3 — No fabrication
If it isn't in the sources, say so. A shorter true answer beats a longer
plausible one. The extractor is instructed to emit only what the transcript
supports, tagged honestly.

## Rule 4 — Knowledge ≠ behavior
Facts (what was built, tools, numbers) live in `kb`/dossiers. Skill behavior
(how to write) stays in the skills. The proposal-writer reads facts at runtime.

## Rule 5 — Quality-first (density grading)
Grade each source 0..1 for information density; deep-extract when
(active client) OR (capability/architecture call) OR (long/dense transcript);
light-index otherwise. For high-volume clients, prefer the densest sources.

## Rule 6 — Lint & maintenance (weekly)
Sweep for: uncited claims (must not exist), contradictions, superseded facts,
orphan citations (source removed), stale dossiers.

## Rule 7 — Money is stated GROSS **and** NET, never one alone (2026-08-13)
Abhijeet: *"I want dossier showing both gross and net so prepare it that way."*
`modules/knowledge/crm.py` emits two separately-labelled, separately-cited facts
for every client that has earnings:

| Fact | Citation | Means |
|---|---|---|
| `Revenue to date (GROSS, billed)` | `CRM:transactions[pnl_bucket=revenue]` | what the client was billed — the same figure the cockpit Money/Clients tab shows |
| `Revenue to date (NET, received after Upwork fees and tax)` | `CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]` | what actually landed |

Both are summed by `modules.finance.pnl.compute()` — the same function
`bin/finance-pnl` and the Money tab use — over the same `amount_base_minor`
integers, so a dossier cannot disagree with the Money tab. **Net is never
gross × a rate**: the Upwork fee is a real ledger row and it is not always 10%
(it runs 10%–30% across the roster), GST is 18% of that fee, WHT ~0.1% of
earnings, and refunds are their own bucket.

Anyone quoting a client's revenue must say WHICH. Deriving one from the other
with a percentage is a fabrication under Rule 3.

## Fact categories
`overview` · `people` · `numbers` · `timeline` · `architecture` · `stack` ·
`commitment` · `quote` · `other`

## Dossier structure (one markdown brain per client, `knowledge/dossiers/`)
Overview/stack/status (links the canonical `clients` row) · Key people ·
Timeline/history · The numbers (from CRM) · Open threads & commitments
(feeds M4) · Source log (per touchpoint: 2-line summary + raw link + citations)
· Architecture/solutions notes · Quotes (proof-points + voice).
