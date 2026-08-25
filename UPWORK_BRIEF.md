# UPWORK_BRIEF.md — the Upwork system (boot file for Upwork sessions)

**Owner:** Abhijeet · **Created:** 2026-08-25 · **Status:** PLANNED, Phase 0 in motion

**Session protocol:** a dedicated Upwork Cowork chat boots from THIS file (say "upwork
session" or just work from an Upwork-dedicated chat — the workspace router points here).
The general ops chat stays the overview. Write outcomes back to this file at checkpoints;
never let a decision live only in chat. Build conventions still come from
`etherwise-v3/CLAUDE.md`; this brief is the DOMAIN state, not a second manual.

## Why this exists

Upwork is currently the ONLY working client-acquisition channel. It gets platform-grade
treatment — full visibility, closed learning loops, one clean surface — WITHOUT becoming a
second platform. Decision (2026-08-25, Abhijeet): one **Upwork tab** in the v3 cockpit, on
the same DB / doctor / drill machinery. No separate platform (the v1 `dashboard/` relic and
the v2→v3 rebuild are both testimony to what parallel systems cost). No revenue/P&L on the
tab — money lives on Money; connects appear as COUNTS here, their dollars stay on Money.

## Current state (2026-08-25)

- **MCP transport live** (`modules/upwork/feeds/mcp_client.py`) — OAuth 2.1, read-only
  allowlist in code. Shadow poller hourly at :40 (`mcp_shadow_jobs`, 197+ jobs).
- **Cutover pending**: midday scorecard (`scripts/mcp_shadow_report.py`) on ~18h of
  US-day data → if scraper-hot coverage holds, build the provider-flag path in
  scan_pipeline, recalibrate 16/20/22 thresholds (MCP data scores ~2 lower on truer
  applicant counts), flip in the evening. Scraper plist = one-command rollback.
- **Chrome rails still live** (each retires at its module's cutover, per §5.2):
  scanner (`upwork_scan`, :15) and connects (`upwork_connects`, 05:35 — the only applied
  source). Corrected 2026-08-25: the follow-up monitor in v3 is the GraphQL `upwork_inbox`
  job (10:15/19:15), not Chrome; no win-loss detector exists in v3 — both were Cowork
  desktop tasks, deprecated in May, and Airtable Communications has had no write since
  2026-05-20. Recorded as NOT running.
- **Proposals sync frozen since 9 June** (scope_blocked vendorProposals) — local
  proposals table stale, win-rate wrong. Fix: `list_freelancer_proposals` (tasks #30–33).
  Also (found 2026-08-25, fixed same night — see Codebase findings 1): the hourly
  roomList pass was writing NULL telemetry into every proposal it touched.
- **Codebase map** (2026-08-25): `docs/upwork/CODEBASE_MAP.md` + `docs/upwork/codebase-maps/`
  — rails, tables as they really are, verified findings, the tab recipe, the card seam,
  Phase 0–3 readiness. Read it before touching code in this chat.
- **Hiring**: LinkedIn-writer position posted from the client org; applicants waiting;
  nothing systematized yet.
- **Skills**: upwork-proposal-writer (LIVE, scanner reads it), lead-scorer,
  upwork-profile-optimizer. The proposal writer is the artifact Loop 1 finetunes.

## The laws

1. **Drafts only.** Never send a message, submit a proposal, respond to an offer, or spend
   money. `confirm_draft`, `send_message`, `boost_profile`, connects purchases stay banned
   in code. Writes get enabled ONE tool at a time, draft-semantics only, per Abhijeet's go.
2. **§5.2 — no mixed provenance.** MCP data and scraped data never combine. When a module
   cuts over to MCP, its Chrome twin RETIRES; they never co-feed one store.
3. **§4.1 — bounded standing queries.** First pages, capped gets, hourly cadence. One
   freelancer's lead flow, never corpus enumeration.
4. **§5.9 — hiring side is presentation, not scoring.** No agent scores/rankings of
   applicants for hiring decisions. Summarize uniformly; Abhijeet judges.
5. **UI law (learned from the Today tab, 2026-08-25):** default view ≤ one screen; counts
   before lists; lists behind drills. Every card carries an OBSERVED auto-clear condition
   (a reply arrived, a status changed) — needing manual Done is a bug report against the
   card's clear condition, not a user duty. (Origin: Dhanya Sangoli's card survived his
   reply. That class of failure is banned here from day one.)
6. **Small-N honesty.** ~10 proposals/month means most "patterns" are noise. Every learning
   report separates SIGNAL (view-rate class metrics, n≥30) from ANECDOTE (hire-level
   patterns), and says which is which. No confident recommendations off three data points.
7. **No number without a drill** (cockpit traceability law, inherited).

## The build — four phases, each shippable alone

### Phase 0 — Foundations (this week; partially agreed already)
| # | What | Replaces | Tools |
|---|------|----------|-------|
| 0.1 | Job feed cutover behind provider flag + threshold recalibration | Chrome scanner | `find_jobs` (search + smart_search + get) |
| 0.2 | Proposals sync + backfill 29 + silent-zero guard (tasks #30–33) | frozen GraphQL sync | `list_freelancer_proposals` |
| 0.3 | Win/loss detection from proposal/contract/offer statuses | Chrome win-loss detector | `list_freelancer_proposals`, `list_contracts`, `list_offers` |
| 0.4 | Message-thread sync — real timestamps, reply detection | Chrome follow-up monitor | `get_messages` |
| 0.5 | Offer alerts (new offer → card + email within the hour) | nothing (email luck) | `list_offers` |

Phase 0 output is the DATA SPINE. Every later phase reads it; nothing later is built until
its spine row exists. 0.4's reply detection is also the auto-clear mechanism law #5 needs.

### Phase 1 — The Upwork tab (visibility)
Top-to-bottom, one screen by default:
1. **Needs me now** (≤5 cards): offers awaiting response · replies owed / stale threads ·
   drafts ready for review · 🎥 custom-Loom flags. Auto-clear per law #5.
2. **Funnel** (week + month): fetched → passed rules → hot (≥16) → applied → viewed →
   interviewed → offer → won. Conversion % between stages, delta vs trailing 8-week
   average. Every stage drills to the jobs behind it.
3. **System health strip**: per-rail last-run + result — MCP poll, proposals sync,
   messages sync, offers sync, ClickUp push (and scanner until 0.1 retires it). The
   "are all MCPs working" answer, one glance, drills to runs.
4. **Connects** (counts only): spent this week/month, per applied job, per interview.
   Dollars stay on Money.
5. **Learning digest slot**: the current week's Loop cards (Phase 2) render here — one
   digest, not five reports.

### Phase 2 — The learning loops (the system that gets smarter)
- **Loop 1 · Proposal outcomes → writing.** Join every proposal's text + structure
  (opening style, length, Loom y/n, rate stated y/n, question asked, personalization
  depth, category) to its outcome chain (viewed → replied → interview → hire) from 0.2/0.4.
  Leading indicators map to proposal parts: VIEW rate judges the opening, REPLY rate the
  hook, INTERVIEW rate the substance. Monthly: a proposal-writing review with concrete
  edits to `skills/upwork-proposal-writer/` — versioned, so before/after is measurable.
  This is the finetuning: the skill is the model; its prompt is what trains.
- **Loop 2 · Skip review (weekly, WITH Abhijeet).** Jobs scored ≥16 that he skipped, with
  his Outcome Reasons → short discussion → reasons coded into scoring (new hard rule /
  weight change / threshold move). Every change lands in a scoring changelog with the
  evidence that caused it. Includes threshold governance: e.g. "16–18 band: 0 applications
  in 6 weeks, 40 connects burned — raise hot bar to 18?" Proposed with numbers, decided by
  him.
- **Loop 3 · Rejection mining.** For losses with threads: extract stated/implied reasons
  (rate, timezone, chose specialist, portfolio gap) from 0.4 messages. Quarterly rollup →
  feeds Loop 4 and Loop 1.
- **Loop 4 · Rate & profile counsel.** `get_rate_insights` per category + win-rate by rate
  band + Loop 3 reasons → rate recommendation (the flat "+10% every 6 months" becomes
  evidence-based). Monthly `get_profile` audit through upwork-profile-optimizer → suggested
  edits; `update_profile` applies only what he approves (write-gated).

### Phase 3 — Hiring side (live need: LinkedIn-writer applicants)
- Pull the posting + applicants: `list_client_proposals`, `list_client_invitations`,
  `get_client_dashboard` on the client org (`list_accounts` already shows it on this token).
- **Applicant review flow per law #4**: uniform one-pager per applicant (their words,
  rate, history, portfolio links — summarized, never scored/ranked); Abhijeet shortlists;
  message/interview DRAFTS prepared; `manage_offers`/`respond_to_offer` only ever fired by
  his explicit per-action go.
- Renders as a separate card group on the same Upwork tab ("Hiring").

## Write-tool ladder (each rung needs Abhijeet's explicit enable)
1. `update_draft` + attachment tools — proposals drafted INSIDE Upwork; he reviews and
   presses submit there. The capstone; only after Phase 0 has earned trust.
2. `update_profile` — approved profile/rate edits (Loop 4 output).
3. `manage_proposals` — bulk-withdraw dead proposals. Hygiene, low priority.
4. Never: `confirm_draft`, `send_message` auto-send, `boost_profile`, milestone
   submission, anything that spends or commits.

## Known bugs feeding this design
- Today-tab card survived a replied thread (Dhanya Sangoli, 2026-08-25). Owned by the OPS
  chat (general cockpit card identity), but its fix — reply-detection freshness — is the
  same mechanism as law #5 here.

## Codebase findings (2026-08-25, Upwork chat — each verified in code AND on a read-only copy of the live DB)
Full detail with file:line in `docs/upwork/CODEBASE_MAP.md` §3. Numbers are LIVE at 02:24 IST.
1. **`upwork_sync` pass 1 wrote NULLs, hourly, since cutover.** `sync()` handed the raw
   ROOM_LIST `vendorProposal` node to `upsert_proposal`, which reads the flat shape → all 9
   v3-era proposals had NULL `created_dt`/`job_title`/`charge_rate`/`modified_dt`, and the
   hourly UPDATE re-NULLed 26 imported rows' title/rate and 72/74 `modified_dt`. Same nesting
   bug on contracts: 59/59 `job_title` NULL. **FIXED 03:10 IST** (`sync_state.py`: pass 1
   normalised via `_vp_node_to_proposal`, both UPDATEs fill-or-keep, `created_dt`/
   `marketplace_job_id` backfilled; `tests/test_m1b.py` 41 → 49). Uncommitted on the Mac;
   next :45 run heals the rows. ◆ Run `PYTHONPATH=. python3 -m unittest tests.test_m1b` on
   the Mac (3.9) and commit. `contracts.hourly_rate` stays NULL (ROOM_LIST selects none).
2. **`offers` is never written by v3** (ROOM_LIST selects `offerIds`; `sync()` reads
   `offers`; `OFFER_BY_ID` never called). 30 rows, last touched 2026-06-11. 0.5 has no
   substrate; the table has no Upwork timestamp column.
3. **`proposals.status` has no writer in v3.** Won/Lost/Expired are the May import; the 9
   v3-era rows have NULL status while `upwork_status` (Hired 26 / Activated 25 / Archived 31
   / Declined 1) is known. Win rate on the cockpit is a May snapshot. First terminal write is
   permanent → the 0.3 mapping must be right the first time.
4. **The Dhanya card, from live rows:** one counterparty card over 3 rooms; a `followup`
   draft (#2655, room_999337…) pending since 08-03 keeps the card alive; his 08-21 reply in
   room_1f5d0e… is in the DB as outbound but `evidence.answered_since` is used nowhere except
   the chip, which excludes Upwork (`data.py:2043`), and `rule_owed_replies` drops only on
   idle < 1 day (`rules.py:95-97`); every non-client follow-up card is labelled "reply owed"
   regardless of bucket (`data.py:2020`); nothing observes Upwork between 19:15 and 10:15 and
   `upwork_inbox` is not in `SOURCES`. Law #5 = 0.4 + those four seams.
5. **Provider flag is a docstring** (`feeds/__init__.py:24-26`, nothing imports it);
   `scan_pipeline.scan()` is Chrome-only. 0.1 is a build, not a flip.
6. **Thresholds are duplicated**: 8/12/16/22 hardcoded in `clickup_push.py`,
   `scan_pipeline.py`, `cockpit/data.py`, `mcp_shadow_report.py`, `scoring_eval.py` besides
   config. **No "20" exists anywhere.** LIVE: 74% of non-skipped jobs (30d) score ≥16, 49%
   ≥22; 240 hot jobs in 7 days.
7. **Retiring the scanner is three edits**: plist + `SOURCES["scanner"]` + catch-up, else
   `bin/catch-up` kickstarts Chrome back (§5.2 broken by the catch-up rail); keep the module
   (`connects_history.py` imports its tab helpers); seed/date-filter the 311 shadow rows or
   the first MCP claim scores+drafts+cards them all in one run.
8. **Invisible rails**: `upwork_inbox`, `upwork_outcomes`, `mcp_shadow_poll` not in
   `config.SOURCES` — no health, no catch-up; the Sunday 08-23 outcome run silently did not
   happen. The health strip cannot show them until registered (config = proposed diff).
9. **Chrome is contended**: scan 19 failed / 172 runs in 7 days (12 logged-out, 6 osascript
   timeouts), connects 2/10 ("landed app.slack.com"). Belongs in the cutover scorecard.
10. **Not Chrome, not in v3**: "follow-up monitor (2x daily)" is the GraphQL `upwork_inbox`;
    "win-loss detector (1pm)" does not exist in v3 (both were Cowork desktop tasks marked
    deprecated in `etherwise-os/CLAUDE.md:467-468`). `upwork_connects` (05:35, Chrome) is
    the only applied source and is not in the list above. ◆ confirm what is really running.
11. **Doc drift**: `CLAUDE.md:15` + BUILD_BRIEF standing caution say v2 owns the OAuth token
    (`var/upwork-token-owner` = v3 since 06-30); `PROFILE_RATE_USD = 32.50` and every
    `skills/` file still quote $32.50 vs the $50/$51.50 master reference; §5.2/§5.9 exist
    only in this brief (§4.1 is Upwork's own terms clause).

## Open decisions
- Tab name: "Upwork" vs "Pipeline".
- Skip-review cadence: weekly card vs fold into Monday sales report.
- Whether ClickUp stays the action queue long-term or the tab absorbs it (decide AFTER the
  tab proves itself — not before).
- Threshold VALUES (after single-sourcing; decided from the MCP scorecard, not guessed).
- Outcome Reason vocabulary: 40 Airtable choices with duplicates (map §9) → one canonical
  set before Loop 2 codes anything.
- `rails/REGISTRY.md` `form_url`: the Outcome Submissions table + form exist in Airtable
  (one Done record, 2026-06-14) — paste the `shr…` link and the Sunday rail goes live.

## Decisions taken 2026-08-25 (night, Upwork chat)
1. **Thresholds**: single-source first — the four cuts (8/12/16/22) become ONE config tuple
   read by clickup_push, scan_pipeline, cockpit/data.py, mcp_shadow_report and
   scoring_eval; new values only after the scorecard measures the MCP drift. "20" was never
   a band; if a fifth band is wanted it is designed into the same change.
2. **Sourcing invariant LIFTED for MCP `find_jobs`** (smart_search + bounded term queries,
   §4.1-compliant), conditional on the cutover scorecard. Recorded here and to be pasted into
   BUILD_BRIEF by the ops chat:
   > 2026-08-25 · SOURCING INVARIANT (M1 lock 06-12) LIFTED for Upwork MCP `find_jobs`
   > (smart_search = best_matches analogue + ≤5 bounded term queries, first page, hourly).
   > Not the GraphQL firehose. Flip gated on `scripts/mcp_shadow_report.py` coverage of the
   > scraper-hot set; Chrome scanner retires at flip (plist + SOURCES + catch-up). — Abhijeet
3. **Chrome rails**: only `upwork_scan` and `upwork_connects` exist; monitor/detector are
   not running (see Current state). `upwork_connects`' replacement is decided with 0.2.
4. **0.3 status map** (first terminal write is permanent, so conservative): Hired/Accepted →
   Won · Declined → Lost · Withdrawn → Withdrawn · Activated → Interview (non-terminal) ·
   Pending/Offered → no status · **Archived → NOT written** until a contract/offer check
   distinguishes Lost from Expired.
5. **Cards**: one engine, two views — Today keeps the ranked act-now queue (cap 12) with
   Upwork cards in it; the Upwork tab's "Needs me now" lists every Upwork card. One Done.
6. **Snoozes**: expire the v2-era `threads.snooze_until` relics (14 rows: 11 × 2099-12-31,
   3 × 2026-05-26 already lapsed — list in chat), keep honouring the column, and give the
   tab a snooze action in Phase 1 with an observed expiry. The UPDATE runs on the Mac, not
   from Cowork (standing caution): `UPDATE threads SET snooze_until=NULL,
   updated_at=datetime('now') WHERE snooze_until IS NOT NULL;` — all 14 have no message
   since 2026-05-29, so they re-bucket to hard-skip, nothing resurfaces.
7. **Map location**: `docs/upwork/`; findings mirrored in this brief; finding 1 fixed here.

## Next actions
0. (Abhijeet, now) `PYTHONPATH=. python3 -m unittest tests.test_m1b` on the Mac, commit the
   sync fix; run the snooze UPDATE (decision 6); paste the BUILD_BRIEF line (decision 2);
   paste the outcome form `shr…` link into `rails/REGISTRY.md`.
0b. (Abhijeet) Threshold single-sourcing is READY as a proposed diff (decision 1, 04:20 IST):
   `var/proposed-diffs/2026-08-25-score-bands-single-source.{md,patch}` — 7 files, values
   unchanged, 11 new tests, `git apply --check` clean against the working tree. Apply per
   its §C (ends with `bin/cockpit reload`). Two ◆ questions in its §E (ClickUp list-id dedup;
   show band numbers on the tab or names only).
1. (ops chat, today) Cutover scorecard → provider path → flip. Then 0.2 → 0.3 → 0.4 → 0.5.
2. (upwork chat, after 0.2) Phase 1 tab skeleton: health strip + funnel from real tables.
3. (upwork chat, first Monday after 0.2) First Loop 2 skip review — even manual, to shape it.
4. (upwork chat, when he says go) Phase 3 applicant pull for the LinkedIn-writer role.

## Decision log
- 2026-08-25 · Tab in cockpit, NOT a separate platform. No revenue on the tab. Dedicated
  Upwork chat boots from this brief; ops chat keeps the overview. Learning loops 1–4 and
  laws 1–7 agreed in principle (this brief is their spec).
- 2026-08-25 (night) · Full codebase orientation before any change; map lives in
  `docs/upwork/`. Finding 1 (sync NULL-writes) fixed in THIS chat by Abhijeet's call, not the
  ops chat. Findings written here rather than BUILD_BRIEF (ops chat owns that file — it
  should mirror finding 1 as a checkpoint line).
- 2026-08-25 (night) · Seven decisions above (thresholds single-sourced, sourcing invariant
  lifted for MCP find_jobs, Chrome-rail inventory corrected, 0.3 status map, cards in both
  views, snooze relics expired, map location). Still open: tab name, skip-review cadence,
  ClickUp's future, threshold values, Outcome Reason canon, form_url.
- 2026-08-25 04:20 · Decision 1 built as a proposed diff (config is protected): `SCORE_FLOOR`,
  `SCORE_STANDARD`, `SCORE_BANDS`, `score_band()`, `score_band_label()` in config; five readers
  bind config; `tests/test_score_bands.py` greps them. Awaiting Abhijeet's apply.
