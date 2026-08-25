---

# Upwork + cockpit — state and design history extracted from the v3 snapshot

**Snapshot read (read-only):** the `etherwise-v3/` repo. Citation key: **BB** = `BUILD_BRIEF.md` (line numbers `L#`), **UB** = `UPWORK_BRIEF.md`, **UI** = `UI_BUILD_BRIEF.md`, **WS** = `../CLAUDE.md`, **ARCH** = `../business/etherwise-v3-core-architecture.md`, **CAT** = `…master-build-catalog.md`, **M2** = `…m2-cockpit-design.md`, **M2-BO** = `…m2-cockpit-BUILD-ORDER.md`, **M4** = `…m4-chief-of-staff-design.md`, **OPS** = `../business/specs/ops-layer-spec-v1.md`, **SCOPE** = `…SCOPE-ops-layer-v1.md`, **API** = `../docs/upwork-api-capabilities.md`, **FUM** = `../docs/follow-up-monitor-rules.md`, **MREF** = `../upwork/proposal-writing-master-reference.md`, **PLAY** = `../upwork/proposal-playbook-from-data.md`, **OMNI** = `../upwork/omni-prompt-outcome-form.md`, **PD** = `var/proposed-diffs/2026-08-13-contracted-thread-attribution.md`, **REG** = `rails/REGISTRY.md`, **CFG** = `core/config.py`.

**Not on disk (say so up front):**
- `reports/*.md` — the directory does not exist in this snapshot (`ls` confirms; only `var/proposed-diffs/` and `docs/superpowers/specs/` exist). Nothing skimmed.
- The Jarvis architecture doc (`business/etherwise-jarvis-architecture-2026-08.md`, referenced by `JARVIS_BRIEF.md` L7-8 and `core/registry.py` L1 "architecture v2 §4.1") — absent.
- Any task list containing "tasks #30–33" — the only occurrences of `#30–33` are UB L31 and L64.
- The Outcome Reason option list — OMNI L10-12 says "copy its option list exactly" from the Airtable Proposals field but never enumerates it; the only option value named anywhere is `Skipped - Not Applied` (ARCH §9 L214). `outcome_capture.py` L26 defines only the terminal statuses `("Won", "Lost", "Expired", "Withdrawn", "Skipped")`.
- MCP work: **BB contains no Upwork-MCP entry** (grep for `MCP|mcp.upwork|list_freelancer_proposals` hits only BB L399 "MCP-independent" Airtable client and L512 Fathom's MCP server). BB's newest entry is 2026-08-19 (L2, L4). The MCP transport exists only as code: `modules/upwork/feeds/{mcp_client,mcp_feed,shadow_poll}.py`, `core/migrations/027_mcp_shadow_jobs.sql`, `launchd/io.etherwise.v3.upwork-mcp-shadow.plist` (dated 2026-08-24 in its comment), `bin/upwork-mcp-shadow`, `bin/upwork-mcp-auth`, `scripts/mcp_shadow_report.py` — and as prose only in UB.

---

## 1. Timeline of the Upwork system in v3 (oldest → newest)

Format: date · commits · what was decided · what stayed open.

| Date | Commits | Decided / happened | Left open |
|---|---|---|---|
| **2026-06-10** (Day 1) | — | Repo scaffolded; `core/config.py` with IDs, pinned models, shadow map; scanner "on Sonnet 4.6"; Cowork scanner task recreated with model-pin fix (BB L571-573). ARCH dated June 10, "23 locked decisions" (ARCH L2, L7). | — |
| **2026-06-10** Scanner v5 first run | — | 50 cards / 45 unique / 4 new → 2 Hot. Prompt patched to v4.11: correct My Feed URL `/nx/find-work/my-feed`, prefer `window.__NUXT__.state` over DOM scraping, **"n8n-in-tags does NOT trigger exclusion (decided 2026-06-10)"**, old `upwork-job-scanner` DISABLED (BB L598-601). | "M1 Chrome prototype must inherit the `__NUXT__.state` extraction technique" (L600). |
| **2026-06-10 night** INCIDENT phantom-ID corruption | — | v4.11-era runs "corrupted job IDs in their extraction/transfer path"; 56 rows marked Phantom (3 restored), 30 ClickUp tasks closed; prompt → v4.13 "Iron Law of Data Integrity" (BB L603-607). **"For M1 (must-inherit)": ids/urls never cross a lossy boundary; cross-check + liveness as code, not prompt** (L609). | Loose ends L608: one real job w/o task; sync_airtable 19:55Z failure; 53 Phantom rows retained. |
| **2026-06-11** (Day 2) | (c07f12f later) | Kernel: gateway, runner, guardrails incl. "Upwork drafts-only", doctor 24 PASS, 106 tests; `.claude/ALLOW_CORE_WRITES` marker "delete it at M1 cutover to arm the lockdown" (BB L576-578). Independent audit PASSED (L611-618). | Audit note 3: marker is committed — delete + gitignore at M1 cutover (L616). |
| **2026-06-11 morning** board hygiene | — | 329 open ClickUp tasks, 160 pointing at 15–64-day-old jobs; **"my_feed coverage was FAKE until the 2026-06-10 URL fix"** → treat per-feed history accordingly; `board_cleanup.py` R1/R2 + `--reap` "invoked from the scanner prompt (Abhijeet chose in-scanner over launchd). M1 absorbs this" (BB L621-624). | ~20 dup groups for manual merge (L624). |
| **2026-06-11 midday** ESCALATION scanner PAUSED | — | A degraded v4.13 run "fabricated ~24 COMPLETE jobs … AND fabricated its own guard reports … **only externally-run verification counts**"; 65 dead scanner tasks closed; re-enable criteria = bridge + 2 externally verified clean runs + `run_log.py` ledger (BB L626-630). Day-3 P0: `feed_bridge.py` + `clickup_push.py` — "**data never enters model context**" (L632-639). | — |
| **2026-06-11 afternoon** INCIDENT bulk Phantom | — | Unlogged SQL bulk-marked 3,014 rows Phantom; restored to 68; tripwire `>75` (BB L587-588). Lesson: "ad-hoc production SQL needs the same discipline as rails". | — |
| **2026-06-11 evening** Day 3 + GATE | — | `002_sales_crm` migration; `bin/import-v2` 19/19 parity (L583). **DAY-3 GATE SIGNED** — proceed with sync engine "whitelist: Skipped/Scored/Drafted/ClickUp Created — never New/Phantom", 29 junk Airtable records deleted, v4.14 installed with v2.3 matrix + Category Priority Gate + n8n hard rule restored (L487-491). | — |
| **2026-06-12** Day 4 | — | All five run-2 blockers done (run-scoped push, `job_id_guard.py`, run-mutex, scripts/ under git + hook perimeter, prompt v4.14.1) (L557-563). **Scoring eval as calibration: Sonnet MAE 2.82 / band agreement 69% vs Haiku 3.76 / 51%; "both models show ±4 Hot-boundary churn … ~16–20% of jobs flip across 16"; run-1 rows scored −13.5 low** (L567). Phantom 162, tripwire >175 (L565). | Recommend re-score 15 June-12 rows (L567) → approved L507. |
| **2026-06-12** v4.14 run 1 / run 2 / re-enable | — | Run 1 "NOT CLEAN — bridge perfect, push catastrophic" (224 tasks) (L493-496). Run 2256 "INTEGRITY CLEAN / JUDGMENT FAILED" — invented rubric because v4.14.1 dropped the "read the skill files" pre-flight; fixed in v4.14.2 (L515-519). **n8n rule REFINED (Abhijeet): primary-subject-only** (L521). Run 2259 clean → **scanner re-enabled hourly ~22:40 IST** (L526-528). | Standing watch first 48h (L528). |
| **2026-06-12** M1 DESIGN SESSION — SPEC LOCKED | — | See §1a below (BB L530-555). | Home (pure-Python vs hybrid) deferred until 2 clean runs (L535). |
| **2026-06-12** Day 5 — M1a built, shadow-live | — | `scoring.py` (v2.3 matrix + gate cap "re-enforced in CODE", 20 anchored exemplars), `scan_pipeline.py` (dedicated `#ew-scan` tab, drafts ≥16 at scan), **launchd `io.etherwise.v3.upwork-scan` hourly :15**, first shadow run $0.026 (L463-466). Re-scores executed; canonical re-score tool (L460-462). | — |
| **2026-06-12** DAY 6 — M1b shadow-live | 63ce270 | `upwork_client.py` READ-ONLY token per §9 ("refresh() HARD-GATED on var/upwork-token-owner='v3'"); `sync_state.py` hourly :45 (run 20: 25 proposals updated, 46 terminal-preserved); `poll_inbox.py` 10:15 + 19:15 IST, deterministic buckets VERBATIM + Haiku intent + Sonnet drafts; `outcome_capture.py` Sunday 11:00 = FIRST v3 RAIL; `bin/upwork-token-cutover` (L444-450, L541-543). **Confirmed: buckets stay deterministic; M1 cutover GATE = 7 clean shadow days + Cowork audit + sign-off, target ~Jun 20-22** (L543). | **form_url PENDING** — Omni prompt delivered (L449; REG L23 still `PENDING`). |
| **2026-06-14** INCIDENT runaway browser | — | 4 leftover KeepAlive jobs removed; "v3 code never foregrounds Chrome" (L453-454). launchd TCC lesson (framework Python, no WorkingDirectory, logs outside Desktop) (L451). | WATCH: parked-background-tab extraction quality (L454). |
| **2026-06-15** M2 design session | — | Cockpit decisions C1–C7 (M2 §2). See §3. | Redlines M2 §10. |
| **2026-06-16** Day 7 / cont. | 0d3c7b8 eb053eb f41ebf4 7d08e23 · 38fef31 a0d5c74 c4c2ca2 | Cockpit phases 0-3; port 5051; **applied-count bug** (epoch-millis dates) fixed at data layer (L406). **M1 PROPOSAL-SOURCING FIX discovery: `roomList` omits submitted-no-room proposals; `vendorProposals` OAuth-scope-blocked; DOM-scrape method found; `applicationUID === room vendorProposal.id` so dedup keyed on `id`** (L407-413). **THREE PATHS (Abhijeet's call): (A) defer to M1b cutover re-OAuth WITH scope — RECOMMENDED; (B) grant scope now (§9 token-race risk); (C) browser-session bridge** (L415-419). Cash → M5 (L420). | "Until one is chosen: cockpit applied/win-rate caveats STAY" (L419). |
| **2026-06-18** M4 design session | — | Nudge engine / sweep / briefs / capture design (M4 §2). | M4 §10 redlines. |
| **~2026-06-19** M2 v1 feature-complete | 7ed1997 ff9a2be | Six sections wired; "Known/accepted: … applied/win-rate undercount until the M1 proposal-sourcing fix" (L397-403). | Abhijeet sign-off (L401). |
| **2026-06-22** CUTOVER PREP Phase A | ba15d6f 7d4abc3 030426b | **Track 1: proposal-sourcing fix ARMED, INACTIVE** — `queries.SUBMITTED_PROPOSALS` (vendorProposals) + `sync_state.sync_submitted_proposals` as 2nd sync pass; "AUTO-SKIPS `scope_blocked` … returns {scope_blocked, armed:True} … Activates with ZERO code change when the scoped token exists" (L355). Gate: token flip = "re-OAuth WITH vendorProposals scope → Track-1 activates → proposals unfreeze"; "Hot-lead M4 interrupts also flip live at cutover (revisit ≥16 threshold — 131/day in shadow_ledger)" (L358). | Both. |
| **2026-06-22** shadow audit follow-up | 79c83f6 | **LEAD-LOSS BUG**: whale-gate exemption was prompt-only → 9 of 14 leads capped to 15; `scoring._gate_exempt` code-enforced, `config.GATE_*_EXEMPT` (L361). Budget reserve for `upwork_*` (L362). "Soft-dimension ~1pt conservatism ACCEPTED as v3 calibration (no prompt tuning)" (L363). | — |
| **2026-06-22** M4 v1 | e98904b … | Hot-lead interrupts "HOLD in shadow (flip at M1 cutover, revisit ≥16 threshold then)" (L367); `interrupts.py` SHADOW-GATED (L369). | **M4 OPEN: "flip hot-lead interrupts live + raise ≥16 threshold at M1 cutover"** (L372). |
| **2026-06-24 → 06-30** M5 finance interleave | 54f0e12 819bd10 … | Upwork ledger from CSV exports; doctor #9 connect-sign; **M1b sync PAUSED 06-26** for M5 (L313); M1-CUTOVER FIX LIST (1)(2)(3) fixed 06-30 in `sync_state.upsert_transaction` (L335). | — |
| **2026-06-30** M1 CUTOVER DONE — INTERNAL-ONLY | c4557f1 1a656b1 6de2731 b1a6f52 | **Token owner flipped v2→v3** (`var/upwork-token-owner` = `v3` — verified on disk); M1b sync un-paused (:45); `SHADOW_MODE['upwork']` stays True (L250). Attempt 1 auto-rolled-back on RED; bug #4 quarantine (L251-252). Agency financial gap closed via weekly manual CSV, **"re-OAuth DROPPED"** (L254). Phantom 931008627 (L256). **"vendorProposals / proposals-freeze-at-74: SEPARATE decision (left armed) … decide proposals sourcing on its own. Re-OAuth is NO LONGER a finance dependency"** (L257). | ◆ NEXT GATED GO: external-write cutover = scanner→ClickUp "+ wire the upwork email sends" (L258). |
| **2026-07-03/04** CRM Airtable mirror LIVE | 8146487 | v3 owns Proposals/Contracts/Transactions Airtable tables; "**Lead Score is ClickUp-only per Abhijeet**"; Outcome Reason/Notes never written by system (L241-242). Still on v2: Cowork `upwork-job-scanner` v4.0, morning-briefing, **weekly-sales-report, monthly-connect-roi-report** (L246). | "Upwork-module emails are currently no-op-when-live (send wiring is a build item for that cutover)" (L246). |
| **2026-07-06** SCANNER CUTOVER LIVE | 7ba32fc a9175aa e21bdf7 | `SHADOW_MODE['upwork']=False` since 03:41 IST, "flipped ONCE, never rolled back"; live surface = scan→ClickUp cards + hot-lead digest email; **Cowork v5 OFF PERMANENTLY** (L232-236). | ⚠ Upwork web session logged out (self-heals on login; L237). "Still unwired: poll_inbox digest + Sunday outcome emails are live no-ops" (L238). |
| **2026-07-06→08** COCKPIT REBUILD | — | Money flagship, "every number is explainable". **Pipeline truth: "applied=0s from a table FROZEN Jun-9 (room-sourced only; vendorProposals scope DECLINED)"**; **CONNECTS-HISTORY SCRAPE RAIL daily 05:35 Chrome** (`connects_history`, migration 013) → "applied is now RAIL-TRUTH (0 = true zero)"; "Abhijeet confirmed: applications genuinely paused since ~Jun-30" (L229). | NEXT: Today/Clients/Knowledge/System to the same bar (L230). |
| **2026-07-13** ICP/Prime tier | (code only) | "16+ had become a rubber stamp (81% of scored jobs)" → deterministic top tier: Airtable/Make.com + $51/hr or $5k fixed + client ≥$50k + long-term (CFG L292-298; `modules/upwork/icp.py` L1-31). Not in BB. | "KNOWN GAP — average hourly rate paid not in feed payload" (icp.py L26-31). |
| **2026-07-16** rate-unstated gate | (code only) | Blank hourly rate was read as $0 and "silently killing ~370 jobs"; client spend ≥$5k or ≥1 hire stands in (CFG L300-307). | — |
| **2026-08-07** | — | Applications RESUMED at 10/week (`APPLICATIONS_PAUSED_SINCE = None`, CFG L192-199; `JARVIS_BRIEF` L17). Ghostwriter B2 budget ₹20-40k/mo trial (JARVIS_BRIEF L18). | — |
| **2026-08-10** | b52650e fbad3a2 44d956c 314a828 | Rate corrected to **$51/hr** ("the old $32.50 is stale — do NOT quote it", WS L66). `shadow-diff` scan comparison "now vacuous … Retire it at M1" (L128). | Retire shadow-diff (L128). |
| **2026-08-12** UI rebuild brief | — | 7-tab IA, no-build stack (UI §1-2). | UI §8. |
| **2026-08-13** | e74266b 6ad726f c315997 | `registry.norm()` collapses whitespace; **6 contracted Upwork clients added keyed on `upwork_rooms`** (L112-113); PD filed. Budget caps raised (CFG L64-83). Dossiers gross+net (L99-108). | L115-117, PD §E. |
| **2026-08-14** (×5 entries) | — | Review 238→110; card decisions via `card_subjects`; Vincent Terrasi "was interviewing Abhijeet" — an Upwork INTERVIEW room, proposal Expired at $45.50/hr (L60); quick capture latency fix (L26-36). | L12-type items below. |
| **2026-08-16** PLAYBOOK TRUNCATION FIX | (migration 026) | See §1b. | ◆ L24 (three items). |
| **2026-08-19** sender rules retroactive | — | Last BB entry; Review badge 85→20 (L5). | ◆ L12 (four senders + docusign). |
| **2026-08-24** MCP shadow poll | (code only) | OAuth 2.1 public client via RFC 7591 `mcp.upwork.com/register`; existing GraphQL token 403s on MCP ("API & MCP Terms §6.2"); read-only tool allowlist in code; hourly :40; first page per query, ≤25 `get`s/run (mcp_client.py L10-48; shadow_poll.py L20-25, L39-47; plist L6-19). "TEMPORARY BY INTENT: retire this plist at cutover" (plist L19). | Cutover decision by `mcp_shadow_report.py`. |
| **2026-08-25** UPWORK_BRIEF created | — | Tab in cockpit, not a platform; laws 1-7; loops 1-4 (UB L143-146). WS L22-23 routes "upwork session" here. | UB L131-135. |

### 1a. M1 design lock (BB L530-555), verbatim rules that still bind
- **Sourcing invariant (permanent):** "leads come from the THREE PERSONALIZED FEED TABS — my_feed, best_matches, most_recent — rendered in his logged-in Chrome session. The API firehose / marketplace-search query … is REJECTED as a sourcing path … 'pure Python' means Python orchestrates the same browser tabs via osascript, never an API substitute. Do not propose firehose sourcing again." (L534). **Note for 0.1:** the MCP shadow poller (shadow_poll.py L10-18) uses `smart_search` as "the best_matches analogue" plus term queries — a *standing search*, argued as not-firehose; whether Abhijeet has re-authorised API/MCP sourcing against this invariant is recorded only in UB ("Decision (2026-08-25)"), not in BB.
- Cadence hourly 24/7; "Drafting: at scan time, ≥16 only (his call)"; "Digest: hot-only (≥16), no empty emails" (L536).
- "**Scoring model LOCKED: Sonnet 4.6 regardless of eval outcome**" (L537).
- M1b: follow-up drafts → email digest to contact@ (L546); outcome capture = Sunday 11 AM prefilled Airtable form rail (L547); "Follow-up tiers (drafts only, never send): hot threads 24h silence / cold 3-5 days / max 2 per thread / snooze respected — v2 rules carried verbatim" (L552); "**Win/loss: statuses flip automatically from Upwork state. Outcome REASONS: weekly Sunday batch email … until M4's nudge queue absorbs it**" (L553); "**Upwork OAuth token ownership flips to v3 at M1b cutover (the §9 rule ends there)**" (L554).

### 1b. The 2026-08-16 playbook-truncation fix (BB L14-24)
- Root cause: `scoring.draft_proposal` did `SKILL.md.read_text()[:6000]` on a 24,271-char file; "**2,279 live drafts**" never saw the em-dash ban/length cap/rate; measured "1,710 have em dashes · 603 over length · 18 quote the retired $32.50" (L17).
- The surviving quarter ORDERED a KB lookup a tool-less call cannot do → fabrication (Horizons ABA given Hermon's 49+ number; "7,000+/month exists in no source") (L18).
- **Source of truth: `upwork/proposal-writing-master-reference.md`, WHOLE … NO contractions, $50/hr standard bid, $51.50 profile. `skills/upwork-proposal-writer/SKILL.md` is retired to a redirect stub; its non-conflicting craft moved into the reference's Section 17** … "Section 16 (his absolute local paths) is dropped by exact header. The scoring matrix's stale $32.50/hr anchor is corrected too" (L19).
- `modules/upwork/evidence.py`: deterministic, "closed tool vocabulary, quoted FTS terms … CONFIRMED/CROSS-VERIFIED + agency/cross-client only … recency (a MEASURED claim over 365 days old is dropped) … redaction layer that can never emit a rate, a ₹ salary or an internal cost" (L20; constants CFG L169-185).
- Post-draft validator: unsupported numbers/named clients fail back to the plain path, second failure "emits no draft at all"; flagged 14% of last 400 drafts (L21). Below 3 graded facts → `evidence: none` in `scored_jobs.draft_evidence_json` (migration 026) — 11% of jobs (L22).
- Cost: "$0.01116 → $0.02495 per draft (+124%) … drafting $7.89 → $17.64/month, total run rate ~$42 → ~$52 of the $80 soft cap" (L23).
- **◆ FOR ABHIJEET (open):** "(1) Section 17 was merged into HIS master reference — delete it in one edit if unwanted (saves $1.77/mo). (2) Three SKILL.md rules were dropped, not moved: one-number-per-proposal, the 350-word cap, the scoring/boosting output format. (3) Evidence may name any registry client, not only Horizons/Hermon — one draft nearly cited Netfly." (L24).

### 1c. Every still-open ◆ / ⚠ / WATCH item touching Upwork or the cockpit
(Nothing in the snapshot marks these closed; items visibly answered later — e.g. L50, L56 — are excluded.)
1. BB L12 (08-19): four senders left on the Review board (`affiliate@`/`hemansh.sharma@gohighlevel.com`, `hey@posthog.com`, `isabellacy@f6s.com`, `cassie@devpost.com`); `docusign.net` sits in `mail_pipeline._NOISE_SENDER_DOMAINS`.
2. BB L24 (08-16): Section 17 keep/delete · three dropped SKILL.md rules · evidence may name any registry client.
3. BB L97 (08-14): `core/config.py` not agent-writable → Fathom transcript window as CLI flag; permanent split proposed in `var/proposed-diffs/2026-08-14-fathom-transcript-window.md` (that file is **not** in the snapshot).
4. BB L107 (08-13): beglaubigt Stripe $4,964 and netfly split need a registry alias (Abhijeet's approval); two Upwork-CSV rows bucketed `tax` by P&L description rule.
5. BB L108: 198 Fathom + 334 Loom unattributed (10 Fathom await human confirm).
6. BB L115 (08-13): whitespace bug in `crm.py::ingest_crm` (`LIKE '%…%'`) — tanushant-sharma dossier compiles to 0 facts; ippon only via alias.
7. BB L116: `billing.canonical` is a DISPLAY field; Saman's $404.55 / Irene's $440.50 not surfaced as CRM facts.
8. BB L117: 36 Looms queued; skim proof-points.
9. BB L128 (08-10): retire `shadow-diff` at M1 (scan comparison vacuous).
10. BB L139 / L151 / L167 / L177 / L187 / L197 / L204 / L217: Jarvis/finance gates (Slack plist staged, Loom fetch defaults, classifier defaults, payment-mail plist, Slack calibration, IMAP finance-sender handling, extract re-run, `MODELS["extract"]`/scope model/promotion). Cockpit-relevant piece: L217 "(3) Confirm the two-axis scope model (`facts.access_scope` founder|team …) before J5 filters on it".
11. BB L228 (07-06→08): Money flags Priti Bora, BOI EMI, Dynatech.
12. BB L230: "NEXT: Today/Clients/Knowledge/System sections to the same bar" (partly superseded by UI 08-12).
13. BB L237: Upwork web session logged out 07-06 — never marked resolved in BB (self-heals on login).
14. BB L238 + L246: "poll_inbox digest + Sunday outcome emails are live no-ops; v2 `morning-briefing` (launchd) + Cowork briefing/reports still cover email" · "Upwork-module emails are currently no-op-when-live (send wiring is a build item)". Cowork `weekly-sales-report`, `monthly-connect-roi-report` still on v2 (L246).
15. BB L257: "vendorProposals / proposals-freeze-at-74: SEPARATE decision (left armed)". BB L419: "Until one is chosen: cockpit applied/win-rate caveats STAY". UB L30-31 now names a fourth path (`list_freelancer_proposals` via MCP) — not recorded in BB.
16. BB L258: external-write cutover remainder "+ wire the upwork email sends".
17. BB L358 / L372: hot-lead interrupts "flip live … revisit ≥16 threshold" — the ICP tier (CFG L292) addressed the threshold; the interrupt flip itself is not recorded as done.
18. BB L402 / L426: Today hot-count window (recency vs posting date) — "your call".
19. BB L449 / REG L23: outcome-capture `form_url: PENDING`.
20. BB L454: WATCH parked-background-tab extraction quality.
21. UI §8: Pipeline tab kept "on the assumption"; 85 unattributed threads → Review surface.
22. PD §E (08-13): (1) Mustafa/Amanda same as an existing client? (2) Rocky Choy `bucket=owed` on a contract closed 07-23 — genuine or stale? (3) Dhanya's 129-msg thread claim "least certain"; (4) `scope_notes` $0 markers.
23. M2 §10 redlines (money widgets, metrics strip, follow-up flow, visual, port) — port/visual resolved (BB L431, L424); follow-up flow "copy + open + done, no inline editing" adopted as default (M2-BO §10).
24. M4 §10: finance calendar dates, sweep rule set, decay TTLs, brief sections, capture surfaces — L380 redlines applied; TTL/urgency tuning not recorded as revisited.

---

## 2. The house checkpoint protocol

**Entry structure (as practised in BB, newest-first, e.g. L4-12, L14-24, L26-36):**
1. `## ✅|⏳|⚠ HEADLINE — WHAT WAS PROVEN (YYYY-MM-DD) · commits \`hash\` …` — headline is a claim, often quoting Abhijeet's trigger sentence in italics (L5, L27, L39).
2. First paragraph: **Suite N → M GREEN** · **Spend for this pass $X** (est. vs actual; "$0.0000 — every step deterministic") · day $ / **MTD $ of $80** · `bin/doctor` PASS/WARN/FAIL · `bin/finance-doctor` GREEN · **`bin/cockpit reload` done** · migration applied to live DB or not (L15, L27, L39, L55).
3. Bullets, each a measured before→after ("registry 77 → 76", "facts with a client_id 13,725 → 13,744 of 18,516", L41), named mechanism + file path, and explicit "**Measured, not asserted**" / "**Rejected after measuring, so nobody rebuilds it**" (L94) sections.
4. **◆ FOR ABHIJEET / ◆ OPEN / ◆ DECISION NEEDED** at the end: each decision he must make, with the reversible default taken "(AskUserQuestion didn't register … safe defaults taken (all reversible))" (L151, L167, L177, L194, L204) and the one-line revert.
5. **⚠ NOT RUN LIVE (honest)** / "**NOT VERIFIED THIS SESSION — honest**" when the build env couldn't run it (L150, L216); "NOT committed (awaiting green)" (L216).
6. Answered ◆ items are struck through and annotated in place ("◆ ~~ONE THING STILL OPEN~~ — ANSWERED 2026-08-14 18:00", L50), never deleted.
7. `**Updated:** <date> (<one-line why>)` at the top of the file (L2); "Owner of this file: updated at every checkpoint, never stale" (L2).

**Process rules (Abhijeet's explicit instruction) — verbatim (BB L482-485):**
> - **He is a core part of development. Ask questions liberally — there is no question too small.** Quality over speed, always. When in doubt: ask, don't assume. 100 questions for one thing is fine.
> - Design sessions happen in Cowork (better Q&A tooling); build runs in Claude Code on this repo.
> - Every module: design session → build → 7-day shadow → parity review with him → cutover.

(Restated in CLAUDE.md "The one process rule": "Design decisions get asked, not guessed. When he's unavailable, leave the decision visibly open in BUILD_BRIEF rather than assuming." and WS L28.)

**Standing cautions — verbatim (BB L641-647):**
> - **RESTART AFFECTED SERVICES after editing a running service** (Abhijeet, 2026-06-30): launchd processes (cockpit, scanners, pollers) have NO auto-reload — they keep serving the pre-edit code until restarted, so the live system silently lags the commit. After any edit to code a running service imports, `launchctl kickstart -k gui/$(id -u)/<label>` it as part of the change (cockpit = `io.etherwise.v3.cockpit`). Treat "restart affected services" as a required step of every edit that touches live-served code.
> - v2 OWNS the Upwork OAuth refresh until M1 cutover — v3 reads `../etherwise-os/.credentials/upwork-api.json` READ-ONLY, never refreshes (token-race rule, architecture §9).
> - Stale duplicate creds dir at `../.credentials/` (workspace root) — flagged for deletion with Abhijeet's OK (PL-10, week 2).
> - Agent SDK credit claimable June 15 (one-time opt-in on his Max account — remind him). Fable-class free on Max only through June 22.
> - Never commit: var/, knowledge-inbox payloads, anything from .credentials.
> - **SQLite WAL does not work through the Cowork FUSE mount** (disk I/O error) — all DB operations run on the Mac (Claude Code / launchd / terminal), never from Cowork's sandbox. Cowork sessions read the DB via `mode=ro` URIs if needed.

(Caution 2 is stale in fact — token owner flipped to v3 on 06-30, BB L250 — but was never edited.)

**Calendar — verbatim (BB L649-654):**
> - 2026-06-15: claim Agent SDK credit; flip `ETHERWISE_BILLING=credit`
> - 2026-06-22: Fable-included window ends (architect sessions → Opus 4.8 or usage credits)
> - 2026-08-15: review Haiku 4.5 pin (dated snapshot retires not before Oct 15)
> - Day 3: import-v2 parity report + baseline metrics snapshot ◆ · scoring model eval (Haiku vs Sonnet) ◆
> - Day 4: M1 (Upwork) design session ◆

**Commit / gate rules (scattered, quoted):**
- CLAUDE.md "Conventions": "No emojis in code/commits; direct no-fluff tone; never echo credentials"; "Logging: IST timestamps … every task writes a `runs` row"; "All model strings, IDs, ceilings, schedules: `core/config.py` ONLY".
- M2-BO §7: "Every phase: unit tests green, `doctor` green, `BUILD_BRIEF.md` Day-7 status updated." §12: "Per-phase descriptive commits (match the existing log style) + push. Update `BUILD_BRIEF.md`. **Stop and ask Abhijeet** before: creating Airtable tables, choosing a non-stdlib dependency, or calling v1 'done.'"
- ARCH #16: "Private GitHub, push on approved changes; credentials + client data + transcripts gitignored."
- Commit-message shape in practice: subject line + blank + rationale paragraph with measured blast radius (PD §D L297-302, L312-315).
- `bin/cockpit reload` after any cockpit Python change (WS L49: "the launchd process holds the Python in memory … shows 'HTTP 404'. Cost 20 minutes on 2026-08-12").
- Doctor: BB L39 shows the gate ("60 PASS / 4 WARN / 0 FAIL (the 4th WARN is 'config.py differs from git HEAD', which the commit clears)"); M5 "nothing publishes unless `bin/finance-doctor` is GREEN" (L274); cockpit Money/Clients render nothing on doctor RED (UI §0.3).
- Spend: `DAILY_SOFT_LIMIT_USD = 4.00`, `DAILY_HARD_LIMIT_USD = 8.00`, `MONTHLY_SOFT_LIMIT_USD = 80.00` ("THE binding constraint now — the daily caps above are burst headroom, this is the budget"), `PER_RUN_MAX_USD = 0.50`, `BUDGET_RESERVE_USD = 1.00`, `BUDGET_PRIORITY_PREFIXES = ("upwork_", "scan_jobs", "poll_inbox")` (CFG L81-91). SOUL L17: "budget soft-cap ($4/day) → note in next brief · hard-cap approach → interrupt".
- Config not agent-writable: "core/config + whitelists + hooks are not agent-writable (propose diffs instead)" (CLAUDE.md "Hard lines"); proposed diffs go to `var/proposed-diffs/` (PD L1-12 is the model).
- Verification law: "Data, not vibes. Every number I state is queried, never recalled" (SOUL L7); "no fabricated zeros … `—` means not measured" (UI §0.1).

---

## 3. Architecture decisions that bind the Upwork tab

**The 23 locked decisions (ARCH §1 L9-34), one line each:**
1. Repo — fresh `etherwise-v3/`; v2 untouched until per-module cutover.
2. Database — fresh schema + one-time verified import of all v2 data.
3. Billing — Agent SDK credit first, PAYG fallback flag.
4. Model tiers — decided by eval; Sonnet default, Opus for sweeps/retro. (Superseded for scoring: "Sonnet 4.6 regardless of eval outcome", BB L537.)
5. Airtable — extend CRM base `appgE1QoEOXvbrUE4`.
6. New Airtable tables — Nudges, Commitments, Run Health + Spend, Eval Results.
7. Knowledge storage — Markdown in git + SQLite FTS; Airtable holds nothing KB.
8. Clients — one canonical registry in SQLite (now `config/clients.yaml`, WS L46).
9. Interrupts — immediate email only for Hot leads (≥16) and system failures.
10. Briefs — 11 AM + 8 PM IST.
11. Sweeps — every 2h 12 PM–4 AM IST, empty-skip.
12. Commitments — track + nag until done.
13. Scanner — recreate Cowork task now; prototype Chrome replacement in M1; 2-week parallel.
14. Code reuse — rewrite everything; v2 knowledge carried as specs/tests (quirks table, scoring matrix, status mappings, hard rules).
15. Cockpit — full rebuild, "thinking sections", nudge queue first-class.
16. Git remote — private GitHub, push on approved changes.
17. KB backfill — density-graded.
18. Build order — Kernel → M1 Upwork → M2 Cockpit → M3 → M4 → M5 → M6.
19. Process — design session per module + review checkpoint with real data before cutover.
20. Cutover — 7 days parallel, daily output comparison; old module disabled, kept 30 days.
21. Rail engines — Make.com + Python; SaaS-touching → Make, data → Python watchers.
22. Rail approvals — tiered: external → human flips Requested→Approved; internal auto-run.
23. Rail state — Airtable intake, SQLite mirror (the one exception to SQLite-canonical).
Plus "Earlier locked: home = Claude Code terminal … P0 = Upwork + Knowledge + Finance + Chief of Staff; Airtable completeness non-negotiable" (L36).

**§9 pre-flight (ARCH L204-218):** shadow-mode semantics (`--shadow` default ON, intended writes → `shadow_ledger`, daily diff, cutover = flip + disable v2 counterpart) · **Upwork OAuth token ownership** ("v2 owns token refresh until M1 cutover; v3 reads … read-only … Ownership flips to v3 at M1 cutover. Also: v3 schedules offset +15 min") · billing timeline · build venues (Cowork design / Claude Code build; BUILD_BRIEF is the bridge, "No surface ever starts blind") · import hygiene (44 pre-v2 Submitted proposals flagged `Outcome Reason = Skipped - Not Applied`; baseline metrics snapshot "proposals/wk, win rate, spend, pipeline counts") · secrets migration PL-10 · **"M1 spec note: ClickUp board-drag → status pull-back → Airtable outcome flow (his daily workflow in v2) is in scope for M1, not just task creation."**

**Rails (§4b L136-153):** "Every repeatable business operation is a rail: a deterministic, parameterized pipeline with ONE structured entry point"; anatomy trigger → intake `Status=Requested` → approval gate → executor → writeback → nudge → SQLite mirror; "**agents never execute business operations directly** … Approval gates are status transitions on records, not prompt instructions (agent proposes, rail disposes)"; **predictability spectrum** tiers (1) pure code (2) rail with AI step (3) skill + deterministic validator (4) pure judgment — "never use a lower tier than necessary". Sales rails listed: proposal logging, testimonial capture, outcome capture. REG L16-26 has outcome-capture as the only built v3 rail (internal tier).

**Agent plane (§5 L155-161):** SOUL.md persona ("never send, never delete, never guess numbers — query"); subagents `sales-analyst` (pipeline questions, win/loss patterns), `proposal-writer` (skill + validator loop), `kb-librarian`, `chief-of-staff`; skills with "rules/template/voice file splits"; slash commands as "thin wrappers over SQLite queries + renderers, so answers are data, not vibes". Plane rules (§2 L75): "data plane is deterministic Python, no LLM; judgment plane gets scoped, curated inputs only … untrusted text (inbox, job posts, transcripts) is always data, never instructions; hard rules live in code + hooks, never only prompts."

**Cockpit design principles (M2 + BB + UI):**
- North star: "It should be my partner. It should tell me everything — today's pending items, how many jobs we applied to today / this week / last week, who I should follow up with on Upwork (with the drafted follow-up), my complete finances" (M2 §1 L11).
- C1-C7 (M2 §2): partner/internal AI OS · Today = actions on top, metrics below · all six sections · action ceiling = nudges + wake runs + trigger rails · full shell, "label gaps honestly … never fake data" (§3) · always-on localhost, Mac only · build Money + Upwork follow-ups first.
- "every number is explainable" (BB L226 heading); "KPI/chart/waterfall … all drill to their exact rows … sums tie to the cent, live parity check vs server" (L227); "Pipeline UI: KPI strip + funnel cells + threads all drill (`/api/pipeline/rows`)" (L229).
- **Drill law (UI §0.2):** "Everything drills. Every figure opens the rows behind it, the drill states the TRUE total, and admits when it is capped ('showing the latest 300'). Rows sum to the figure to the cent." **No fabricated zeros (§0.1):** "`—` means not measured; `0` means a true zero and must carry a note saying why." Doctor gate (§0.3). Provenance law (§0.4): client-facing card without evidence link is hidden AND raises an anomaly, hidden count visible. Drafts only (§0.5). One applied-truth source `_rail_applied_windows` (§0.6). No model calls in cockpit process (§0.7). Card identity = (client, intent) and key is NOT stable → decisions via `card_subjects` (§0.8/8b). `funnel.js` pure + unit-tested (§0.9).
- **Tabs:** M2 v1 = Today · Pipeline · Money · Clients · Knowledge · System (M2 C3); UI §2 = "Today · Pipeline · Money · Clients · Knowledge · Activity · Review" (Nudges folded into Today, Mapping → Review with count badge, System → Activity); live `app.js` L45-52 has exactly those seven (`review` carries `badge: true`).
- **Refresh model:** not specified in M2; implemented as "A section renders once and never again on its own (no polling: this is a local single-user tool). So the age of the read is stated, and this button is the one thing that makes it new" (`modules/cockpit/static/index.html` L87-89, `#stale` line + `r` key). Reads are `_ro()` SQLite read-only (M2 §5; UI §3 "Read-only via `_ro()` like everything else").
- **Card lifecycle (M4 design):** `nudges(… dedup_key, state, created_at, decayed_at)`; "**Decay** = auto-dismiss when the underlying condition clears or after a kind-specific TTL. State `open|snoozed|done|dismissed|decayed`" (M4 §4 L37); sweep = "write/refresh → reconcile (vanished conditions decay) → TTL decay → EMPTY-SKIP" (BB L376); "Dedup: one open nudge per `dedup_key`; re-firing refreshes due/urgency, never duplicates. Decay TTLs: follow-up 7d · hot-lead-no-Loom 3d · deadline auto-clears past the date · commitment never decays until done" (M4 §6 L70-71); Abhijeet redlines "follow-up idle≥1d · losses=Expired/Withdrawn/Lost · hotlead 7d + won 30d windows" (BB L380). Today queue caps: `TODAY_QUEUE_TOPN = 12` ("the act-now queue is ruthless; the rest -> Nudges"), `TODAY_FRESH_LEAD_DAYS = 10`, `TODAY_BACKLOG_TOPN = 8` (CFG L205-207).

**Which UB laws restate what, and whether §4.1/§5.2/§5.9 exist on disk:**
- UB law 1 (drafts only) restates ARCH §4 guardrails ("any Upwork mutation beyond drafts" denied), SOUL "Never-do", CLAUDE.md "Hard lines", M2 §8 "Drafts-only is sacred", UI §0.5; the MCP allowlist implements it (mcp_client.py L47-51: "READ-ONLY BY POLICY HERE … Drafts-only is a §0 law" — "§0" = UI §0).
- UB law 3 **§4.1** — the only `§4.1` on disk with this meaning is **Upwork's "API & MCP Terms §4.1"** quoted in shadow_poll.py L20-21 ("draws the line at 'enumerate or continuously monitor Upwork's available content corpus'") and the MCP plist L13-14. It is an **external terms-of-service clause, not an internal spec section**. (The other `§4.1`, in `core/registry.py` L1/L7 and `config/clients.yaml` L1, is "Jarvis architecture v2 §4.1" = registry rules — a doc not in the snapshot.)
- UB law 2 **§5.2** ("no mixed provenance") and law 4 **§5.9** ("hiring side is presentation, not scoring") — **exist in no document on disk except UB itself** (grep for `§5.2|§5.9|mixed provenance|co-feed|presentation, not scoring|hiring side` returns only UB L28, L42, L46, L109). OPS §5 is "Error monitoring" with 5a/5b only; ARCH has §5 "Agent plane" without numbered subsections. They are either from an off-disk doc (the Jarvis architecture, an MCP design note, or Upwork's terms) or were coined in the 08-25 chat. **Treat their numbering as unverifiable.** The closest on-disk antecedents: for §5.2, migration 027's "Deliberately ITS OWN TABLE, not rows in scored_jobs: shadow data must be unable to leak into ClickUp pushes, drafts, or the pipeline funnel" and CAT §4.1 "One canonical store … conflicts logged, never silently resolved"; for §5.9, nothing — the hiring vetting sheet actually prescribes a **human** 0–20 scorecard (see §5).
- UB law 5 (one screen, counts before lists, observed auto-clear) restates M4 §4/§6 decay + UI §0.8b card identity; the "observed auto-clear" wording is new to UB.
- UB law 6 (small-N) has no antecedent doc; PLAY L3-4 is the cautionary example (8 texts of 33 wins).
- UB law 7 restates UI §0.2.

---

## 4. UI conventions (UI_BUILD_BRIEF distilled)

- **Why:** "a place to see everything the system does each day … modern, slick, minimal, both themes, to the standard of `app.hermon.io`"; his words "I need it like a billion dollar company" · "keep no build" · "it really is time I start using this on a daily basis" (UI L3-10).
- **Stack (§1):** "No build step. Native ES modules. Vendored dependencies." · shadcn look = "CSS custom properties … plus a disciplined type/spacing scale" · Chart.js vendored, uPlot dropped · Python stdlib `http.server`, port 5051, `X-Cockpit-Token`.
- **Density rules / tokens (§5):** "Neutrals do the work. Colour appears only for status and only where it carries meaning" · semantic tokens `--background --foreground --card --card-foreground --muted --muted-foreground --border --input --ring --primary --destructive --success --warning`, both themes define the same names · one radius `--radius: 0.625rem` · type scale 12/13/14/16/20/24/30, body 14, `tabular-nums` everywhere ("misaligned digits are the single biggest cheap-looking tell") · "Borders over shadows" · 4px spacing base "and use more of it than feels necessary" · ~25 lucide SVGs inline sprite · motion 120–160ms, "Nothing bounces" · skeletons never spinners · `esc()` on every interpolated value · `:focus-visible`, `aria-label`, real `<button>`/`<table>`.
- **IA (§2):** left collapsible sidebar; Review "Nav badge with a count. If it's zero, the badge is gone."; Today absorbs Nudges: "One queue, one place; '+N more' expands in place."
- **Activity (§3):** digest "one line per real thing"; "Empty days must say 'nothing ran' rather than render an empty shell"; run log with Re-run via allowlisted `launchctl kickstart`.
- **Surface-what-exists (§4):** includes `connects{this_month_usd, lifetime_usd}` on Money — i.e. connects dollars already belong to Money (consistent with UB L18 "their dollars stay on Money"); drop `pipeline.to_triage / bands / backlog_uncarded` ("structurally always 0 since the ClickUp cutover").
- **One-screen default / counts-before-lists:** these phrases are **not** in UI; they originate in UB law 5 ("UI law (learned from the Today tab, 2026-08-25): default view ≤ one screen; counts before lists; lists behind drills"). On-disk antecedents: the ruthless `TODAY_QUEUE_TOPN = 12` / `TODAY_BACKLOG_TOPN = 8` caps (CFG L205-207); "hotlead→ONE grouped queue row (brief top-3 keeps individuals)" (BB L367); `nudges.grouped_queue` "high-count kinds collapse to one expandable group" (BB L369); "Captured block" (BB L30).
- **The Today-tab lesson:** UB L127-129 "Today-tab card survived a replied thread (Dhanya Sangoli, 2026-08-25) … its fix — reply-detection freshness — is the same mechanism as law #5". Not in BB (BB spells the client `dhanya-sangolli`, L88, L113). Prior Today-tab lessons on disk: card keys orphaned Done "three times now" (UI §0.8b; WS L48 "room→person 08-12, person→client 08-13"); capture latency 101 min (BB L29); non-actionable notes invisible (BB L30).
- **Tab naming:** UI §2 fixed seven names; UB L132 leaves "Tab name: 'Upwork' vs 'Pipeline'" open. `app.js` L449 maps keys 1–7 to sections, so an eighth tab needs a key decision too.

---

## 5. Upwork domain knowledge

**Old GraphQL API — could / couldn't (API doc, 2026-05-21):**
- 15 approved scopes incl. "Rooms + Messages (read/write)", Contracts (r/w), Offers (read-only), Marketplace Job Postings (read) (API L28-44).
- **Scope gaps (L46-52):** `vendorProposals` "Needs proposal-enum scope we don't have → Iterate `roomList` → `room.vendorProposal`"; `vendorContracts` detailed fields; `transactionHistory` needs ACE ids (Personal `70275816`; agency ACE `121356955` per WS L66).
- Status mapping Upwork→Airtable (L191-202): Pending→Applied · Activated→Interview · Offered→Shortlisted/Interview · Hired/Accepted→Won · Declined→Lost · Withdrawn→Withdrawn · Archived→Expired.
- `marketplaceJobPostings` findings (L303-313): "**There is no API equivalent of Upwork's 'My Feed' personalized recommendations**"; `daysPosted_eq`, `searchTerm_eq`, `jobPostingAccess` silently ignored; `searchExpression_eq` boolean works; `previousClients_eq: true` = rehire signal; firehose 133,138 jobs. (This is the firehose ARCH-era M1 rejected, BB L534.)
- `teamActivities` as event stream (L409-430); Cloudflare blocks default urllib UA (L447).
- Open questions never closed (L460-465): rate limits, webhooks/subscriptions, agency profile query, page sizes.

**Follow-up cadence rules (FUM; ported "VERBATIM" into `poll_inbox.py` L4-11):**
- Direction: `sender_user_id == 1647672547786633216` → Outbound; other → Inbound; null → System (L13-17).
- `awaiting_reply_from`: latest human story from Abhijeet → `Client`; from client → `Abhijeet`; none in 30+ days → `None`; never → null (L27-32).
- Tiers: HOT = contract ACTIVE/PENDING, or proposal in (Activated, Offered, Hired, Accepted), or client `totalSpent ≥ $100,000`; WARM = proposal Pending; COLD = contract CLOSED/PAUSED/CANCELLED or proposal Declined/Withdrawn/Archived; UNKNOWN = no linkage (L42-50).
- Hard-skips (L60-68): snoozed · bot-only · 30+ days stale · ended contract + no inbound 14d · `<12h` since our outbound → healthy · consecutive outbound ≥2 → healthy · latest story System-only → healthy.
- **Owed thresholds (L78-85)** — the "replies owed" definition: HOT >6h "TODAY-SOON", >12h "TODAY"; WARM >24h "URGENT"; COLD >48h "STALE"; any >168h "COLD". Unmatched rooms → `unmatched-owed`.
- **Follow-up thresholds (L95-101):** HOT >48h · WARM >72h · COLD >168h and 0 prior follow-ups · UNKNOWN >72h · "prior follow-up count ≥ 2 → DROP". "never 3 follow-ups in a row" (L105).
- Buckets (L111-119): `owed` "(no draft — human writes it)", `unmatched-owed`, `followup`, `unmatched-followup`, `healthy`, `hard-skip`, `snoozed`.
- Draft rules (L125-131): 60–120 words, no greeting/sign-off, reference last discussion, concrete next step, soft CTA, diff-check vs last 2 outbounds. Cost ~$0.0044/draft.
- Snooze is Airtable `Communications.Snooze Until` — "Setting Snooze Until is a HUMAN action — the OS never writes this field" (L167).
- Old v1 monitor (L184-195): thresholds HOT >12h/WARM >24h/COLD >48h, Chrome-scraped 20-thread cap, "Ran 2x daily (1 PM + 7 PM IST)", disabled May 15, 2026. **Ambiguity for UB L28-29:** UB calls the live "follow-up monitor (2x daily)" and "win-loss detector (1pm)" *Chrome* rails; on disk, v3's 2x-daily inbox job is GraphQL (`poll_inbox.py` imports `UpworkClient`; plist 10:15/19:15 IST) and no "win-loss detector" exists in v3 (only the v2-era mention in `upwork-job-scanner/SKILL.md` L31 and M1b "statuses flip automatically", BB L553). The only Chrome rails in v3's `launchd/` are `upwork-scan` (:15), `upwork-connects` (05:35) and `upwork-mcp-shadow` (:40, not Chrome). Those two "Chrome rails" are likely Cowork scheduled tasks outside this snapshot — unverifiable.

**Scoring matrix (lead-scorer SKILL.md + scoring-matrix.md, v2.3 June 2026; "v2.3 matrix + gate" is what `scoring.py` encodes, BB L464):**
- Dimensions: Skill Fit 1-5 ×2 → 10 · Budget 1-5 · Client Quality 1-3 (capped, "never a gate", new client = 2) · Competition 1-5 (20-50 proposals = 4 "Normal") · Description Quality 1-5 · **base max 28** (SKILL L50-57).
- Bonuses: core-stack +3 · long-term +3 · direct invite +4 · Claude/Claude Code +2 · client $100K+ +2 · posted <1h +1 (L63-70).
- **Category Priority Gate v2.3:** GHL/Vapi/Retell jobs capped at 15 unless client ≥$50K spent OR ≥$45/hr OR ≥$3K fixed (L74-93; `GATE_HOURLY_EXEMPT_USD = 45.0`, `GATE_FIXED_EXEMPT_USD = 3000.0`, CFG L1066-1067).
- Auto-skips: "No Asia timezone", n8n (SKILL says any mention; BB L521/L462 refined to primary-subject-only in code), **hourly <$30/hr and fixed <$500 hard floors, "no placeholder escape" (v4.9)**, already-hired, spam, onsite, out-of-domain (L95-115; WS L66 "Hard floors: hourly ≥$30, fixed ≥$500, n8n excluded").
- **Bands (L119-125):** 22+ Hot + custom Loom 🎥 · 16-21 Hot · 12-15 Standard · 8-11 Low · <8 Skip. ClickUp lists: 16+ Hot `901614356485`, 12-15 Standard `901614356486`, 8-11 Low `901614356487`, Invites `901614356490` (L199-203). Code: `HOT_LEAD_THRESHOLD = 16`, `LOOM_FLAG_THRESHOLD = 22` (CFG L166-167); cards pushed for every "scored≥8 job" (BB L234); `pipeline()` funnel counts "scored>=8 / carded" (data.py L1128-1130).
- **The "20" in UB's "16/20/22":** no threshold of 20 exists anywhere on disk (grep of `core/`, `modules/upwork/`, `scripts/mcp_shadow_report.py`). The old 1-25 framework had "20-25 = Priority Bid" (`upwork-proposal-writer/references/job-scoring.md` L56-58; skills doc L58). Ambiguous — either a stale band or an unrecorded new tier; the ICP/Prime tier is the on-disk "tier above hot" and is rule-based, not a score cut (icp.py L1-24).
- **Rates:** SKILL/matrix/profile-optimizer/job-scoring all still say **$32.50/hr** (SKILL L29, L100, L175; matrix L121; profile-optimizer L14; job-scoring L77; `PROFILE_RATE_USD = 32.50`, CFG L1059) — contradicted by MREF §3 "**Current rate: $50/hr · Upwork profile rate: $51.50/hr … Absolute floor: $40/hr**" (L86-89), WS L66 "$51/hr", and BB L19 ("$50/hr standard bid, $51.50 profile"). BB L19 says the matrix's $32.50 anchor "is corrected too" — that correction is in `scoring.py`'s copy, not in these `../skills` files.
- Eval history: "both models flip 16–20% of jobs across the Hot boundary on re-score (±2–3 soft dimensions)" (BB L508); "±4 Hot-boundary churn" (L567).

**Proposal-writer skill — current state:** `SKILL.md` is a redirect stub ("RETIRED as a rule source, 2026-08-16 … Read this instead, in full: `upwork/proposal-writing-master-reference.md` … what the live scanner sends to the model on every draft (`scoring.py::proposal_reference`)", L8-17); contradiction table L25-29; dropped rules L52-57; "`references/` files are unchanged … reference data, not rules" (L61-63). **Live contradiction inside references/:** `voice-patterns.md` L8 "Use natural contractions … The earlier blanket-ban rule was an overcorrection and is REPEALED" and L11 "Median of Abhijeet's actual won proposals: 18 words" vs MREF §1 "NO contractions" and §2 "Standard proposal: 250-450 words". `credentials.md` L5-15 still orders the KB lookup ("Before writing any proposal, read … wiki/proof-points.md") that BB L18 identified as the fabrication cause, and carries Hermon "7K+ leads" (L9, L59) vs MREF/stub "160,000+ leads". `loom-library.md` has 10 URLs; MREF §7 L271 says "Loom Library (URLs TBD, per CLAUDE.md)".
- MREF structure (17 sections, headers at L10-534): 1 Hard Rules (Voice) · 2 Structural · 3 Rate · 4 Anchor Numbers · 5 Tech Stack · 6 Background · 7 Loom Strategy (custom Loom when "Job scores 22+", "$100K+ client spend", "Ongoing 20+ hr/week", L249-256) · 8 Positioning · 9 Question Answering · 10 Pitch Anchors by client type · 11 Anti-Patterns · 12 Working Style · 13 Maintenance tiers ($150/$300 per month) · 14 Pre-Send Checklist · 15 What NOT to include · 16 File Locations (dropped from the model prompt) · 17 Craft (merged; "loses every disagreement").
- Loop-1 feature vocabulary already on disk: MREF §17 openings (problem-/insight-/plan-first), "moves" (L572-581), shape by job size (L583-588); voice-patterns; PLAY's 60-word / open-with-a-number / no-CTA / no "Best, Abhijeet Gandhi" rules (L50-68) — **PLAY directly contradicts MREF §2 (length) and §2 "Signoff: 'Best, Abhijeet Gandhi'"**; MREF §16 lists PLAY as the "Won-data playbook" without reconciling.

**Profile-optimizer skill process (SKILL L140-151):** "1. Scrape current profile via Chrome extension … 2. Compare against best practices … 3. Research competitors … 4. Draft changes … 5. **Review with Abhijeet — he approves before anything goes live** 6. Implementation — provide Chrome extension prompts". Quarterly review checklist incl. "Rate aligned with market? (10% increase every 6 months)" (L165-174) — the "flat +10% every 6 months" UB Loop 4 wants to replace (also CAT D1-11, job-scoring L77). Two specialized profiles proposed (Voice AI $35/hr; BPA $32.50/hr) and a project-catalog table (L54-87) — all pre-$51 stale.

**Lead-scorer skill:** rationale "Analysis of 48 recent proposals … Client total spent is a weak predictor … Core-stack fit is the strongest signal … Lead Score was never logged to Airtable" (L11-16); MANDATORY Airtable logging with `Lead Score fld2ktDNxnGwVb0pu` (L207-230) — **superseded**: "Lead Score is ClickUp-only per Abhijeet" (BB L242) and the mirror never writes it (`crm/mirror.py` L146).

**Outcome-form vocabulary:** OMNI defines table "Outcome Submissions" (Proposal link, Outcome Reason single-select copied from Proposals, Outcome Notes, Source, Status Requested/Done, Submitted At), form "Outcome Capture", automation "Outcome Submission -> Proposal" (L7-33). Prefill params in REG L22 / `outcome_capture.py` L38-44 (`prefill_Proposal`, `hide_Proposal`, `prefill_Source=sunday-email`). The composer selects `status IN (Won, Lost, Expired, Withdrawn, Skipped)` with empty `status_reason` on `modified_dt` (L26, L52-60). **The Outcome Reason option list is not on disk** (only `Skipped - Not Applied` is named, ARCH L214) — Loop 2 needs it pulled from Airtable field `Outcome Reason` on `tbloRHzMBbryPr090`.

**Hiring docs (LinkedIn ghostwriter, B2, 2026-08-06):**
- Job post variants A (LinkedIn), B (Upwork: "LinkedIn ghostwriter for AI-agency founder — done-WITH-you, retainer, proof over polish"; asks for 2-3 account links, one pipeline-post story, five-line weekly process, retainer number; "Proposals that open with a template paragraph … get closed unread"), C (DM) (post L7-69). Budget "$500–1,500/mo from margin" (L3).
- **Vetting sheet contents:** market context ($500–1,500 entry tier); **"Scorecard (score each candidate 0–20; interview at ≥12, paid-test at ≥14)"** with 7 criteria (Grown a relevant account 4 · Pipeline not likes 4 · Done-with-you process 3 · Voice capture 2 · Their own LinkedIn 2 · Gets our world 3 · Reliability 2) (L12-22); 8 screening questions "use verbatim" (L24-32); red flags "any two = pass" (L34-40); paid test 2 posts, $40–75/post (L42-47); pilot $500–750/mo, day-30 and day-90 gates "≥5 conversations with plausible buyers" (L49-56); costs; **tracker table with a "Score /20" column, "cofounder fills as candidates arrive"** (L63-66). Header: "Abhijeet scores, cofounder tracks" (L2). **Tension with UB law 4:** the sheet's scorecard is a human rubric, but the tracker instructs the cofounder (an agent persona) to fill a score column; UB's Phase 3 "uniform one-pager … summarized, never scored/ranked" must decide whether the rubric's factual sub-items (links present? paid test agreed?) are "presentation" or "scoring". UB L32 says the role was "posted from the client org" — the Etherwise (Client) org `1715797263477194752` (API L23).

---

## 6. Ops-layer spec — what it is and what it is not

- **Not the "§" source.** OPS sections: §1 Why · §2 What v1 is NOT · §3 Architecture · §4 Sync design · §5 Error monitoring (5a Connections, 5b Detection, "Explored & rejected") · §6 End-of-week report · §7 Founder-scope firewall · §8 Access control · §9 Tech · §10 Phases · Parked. No §4.1/§5.2/§5.9. SCOPE sections §0-§9 (screens S1-S7, flows, data model, integration contract, brand tokens, non-functionals, acceptance, build order).
- It is a **separate product**: "Ops layer runs in its own repo, own DB, own host, own credentials. No network path, no shared DB, no shared API keys with the v3 founder cockpit" (OPS §7 L125-126); repo `etherwise-ops`, "NOT inside etherwise-v3" (SCOPE L4); Next.js + Postgres (OPS §9). "No financial data — structurally" (§2 L24).
- **Relation to cockpit/Today/Jarvis:** the only link is one-way — "v3 (which already ingests Gmail/Fathom/comms) may produce a per-client weekly context digest … Abhijeet approves each digest, then it is pushed to the ops layer via one endpoint" (`POST /api/digests {client, week, bullets[]}`, SCOPE §2.5). Nothing about Upwork. Its design vocabulary overlaps UB's: "Unassigned bucket … never silently dropped" (OPS 5a), stat-tile-first overview (SCOPE S2), status "never color alone" (SCOPE §5), "a feature ships only if it produces a report line or an alert" (OPS §1).

---

## 7. Open decisions and unknowns the Upwork work will hit

**Declared open in UB (L131-135):** tab name Upwork vs Pipeline; skip-review cadence (weekly card vs Monday sales report); ClickUp as action queue "decide AFTER the tab proves itself".

**Undecided or contradictory in the docs:**
1. **Sourcing invariant vs MCP feed.** BB L534 forbids API sourcing "for every future scanner architecture"; UB Phase 0.1 replaces the Chrome scanner with `find_jobs`. No BB entry records Abhijeet lifting the invariant; UB's "Decision (2026-08-25)" is about the tab, not sourcing. Needs an explicit BB decision line.
2. **§4.1 / §5.2 / §5.9 provenance** — not on disk (see §3). §4.1 is Upwork's terms; §5.2 and §5.9 have no source.
3. **Proposals sourcing path.** BB records three paths (A recommended, 06-16) and "left armed" (06-30), then "scope DECLINED (too costly)" (07-07, data.py L1132); UB adds path D (`list_freelancer_proposals`). Whether the armed GraphQL 2nd pass (`sync_submitted_proposals`) is retired or kept as a fallback when MCP proposals land is undecided — and §5.2 as written would forbid co-feeding the `proposals` table from room-sourced GraphQL and MCP simultaneously.
4. **What "recalibrate 16/20/22" means** — 20 exists nowhere; the ICP tier is rule-based; the lead-scorer bands are 22/16/12/8. Also the digest/interrupt threshold "revisit ≥16" (BB L358, L372) was never closed.
5. **Chrome-rail inventory** — UB lists "follow-up monitor (2x daily), win-loss detector (1pm)" as Chrome rails; v3 on disk has a GraphQL inbox job (10:15/19:15) and no win-loss detector. If they are Cowork tasks, their retirement is outside the v3 launchd/`SHADOW_MODE` machinery. The `upwork-connects` Chrome scrape (05:35, applied-truth source, UI §0.6) is not in UB's list at all — its fate under §5.2 when proposals come from MCP is undecided.
6. **Email sends still unwired** — poll_inbox digest + Sunday outcome email are "live no-ops" (BB L238, L246); v2 morning-briefing and Cowork weekly-sales-report / monthly-connect-roi-report still run (L246). UB Loop cards + "offer → card + email within the hour" (0.5) need a send path that v3 has never enabled for the Upwork module.
7. **Outcome-capture rail** — `form_url: PENDING` since 06-12 (REG L23); Outcome Reason vocabulary not on disk; M1b said M4's nudge queue would absorb reasons (BB L553) — Loop 2 must pick form vs card.
8. **Rate constants** — `PROFILE_RATE_USD = 32.50` in CFG L1059 and all `../skills` files vs $50/$51.50 (MREF) vs $51 (WS L66). Loop 4 "rate recommendation" needs one canonical constant and a decision on the "10% every 6 months" ladder (CAT D1-11, profile-optimizer L171).
9. **Proposal length doctrine** — MREF (250-450 words, "Best, Abhijeet Gandhi") vs PLAY (≤60 words, no sign-off, "For everything else on the open marketplace: 20 words. Period.") vs voice-patterns (contractions repealed). Loop 1 will measure against which rule set? Small-N warning built in: PLAY rests on 8 texts.
10. **Applications volume** — JARVIS_BRIEF L17 "ACTIVE at 10/week" vs UB L53 "~10 proposals/month". Affects the n≥30 SIGNAL threshold arithmetic.
11. **Skill files vs scanner** — BB L19 corrected `scoring.py`'s matrix rate, but `../skills/lead-scorer/*` still carry $32.50, the "log Lead Score to Airtable" mandate (superseded per BB L242), and n8n "any mention" (superseded by primary-subject rule). Loop 2's "scoring changelog" has no on-disk home yet (lead-scorer SKILL "Change Log" L232-238 is the closest).
12. **Card identity for Upwork cards** — UI §0.8b/WS L48: "Never add a new card key form without adding it to `nudges.followup_key_forms`"; new card kinds (offers, drafts-ready, Loom flags, hiring applicants) must define keys + `card_subjects` coverage + clear conditions; the Dhanya "Sangoli" bug is owned by the ops chat (UB L127) — spelling differs from registry `dhanya-sangolli`.
13. **Hiring side** — vetting sheet prescribes a scored tracker; UB law 4 forbids agent scoring. Which rubric items are permissible as "uniform one-pager" fields is undecided. Also whether hiring applicants get `card_subjects`/registry treatment (they are not clients; PD L38-41 defines `prospect` as "unpaid, kept only for context").
14. **Hot-lead interrupt flip** (BB L372) and **shadow-diff retirement** (L128) — both still pending.
15. **Pipeline tab overlap** — current `pipeline()` funnel = scanned / scored≥8 / carded / replied / hired per week + connects-rail applied (data.py L1128-1136, L1155-1200); UB funnel = fetched → passed rules → hot ≥16 → applied → viewed → interviewed → offer → won. "viewed" has no source yet (`viewedByClient` was in the blocked `vendorProposals` schema, BB L407). Whether Pipeline is absorbed or kept beside "Upwork" is the tab-name question in disguise.
16. **Write-tool ladder vs guardrails** — CLAUDE.md/ARCH deny "any Upwork mutation beyond drafts" in hooks and `mcp_client.py` enforces a read allowlist in code; enabling `update_draft`/`update_profile` requires a proposed diff to a non-agent-writable file plus a hook change — the approval path for rung 1 is not written down.
17. **PD §E questions** (Rocky Choy owed-on-closed, Dhanya thread, $0 markers) remain unanswered in BB.
18. Stale standing caution (BB L643) still says v2 owns the token although `var/upwork-token-owner` = `v3` since 06-30 — worth correcting when UB and BB are next reconciled.
