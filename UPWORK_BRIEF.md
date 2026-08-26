# UPWORK_BRIEF.md — the Upwork system (boot file for Upwork sessions)

**Owner:** Abhijeet · **Created:** 2026-08-25 · **Status:** Phase 0.1 FLIPPED 2026-08-26 03:46 (`534283d`; rounds 04:15, 05:15, 06:15 clean) · Phase 1 BUILT, REDESIGNED, with Run-now buttons (`7279345`, `a10b421`, `7f7d710`) · the overnight list of 26 Aug DONE (0o, 0p, 0r, next actions 5) — his review pending

**Session protocol:** a dedicated Upwork Cowork chat boots from THIS file (say "upwork
session" or just work from an Upwork-dedicated chat — the workspace router points here).
Abhijeet's rule (2026-08-25 15:30): every question and decision goes to HIM in the chat
that is doing the work — nothing is deferred to "the ops chat"; that chat, if it runs, reads
BUILD_BRIEF like everyone else. Write outcomes back to this file at checkpoints; never let a
decision live only in chat. Build conventions still come from
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
  allowlist in code. The shadow poller (hourly at :40, `mcp_shadow_jobs`) ran 08-24 → 08-26
  03:40 and retired at the flip.
- **The cutover numbers that decided it (2026-08-25 19:20 IST, 48 h scorecard + the
  scoring pass, next actions 0d/0e; the flip itself is the next bullet):** coverage of scraper-hot 36/40 · latency +23 min
  (schedule offset) · drift median −1 (not the assumed −2; 65/82 within ±2; hot agreement
  60/63) · MCP-only survivors 157 scored, **94 hot (60 %)** ≈ 70–90 extra hot jobs/day
  against the scraper's ~30/day. The feed is good; the VOLUME is the decision: at today's
  bands the flip triples drafts + ClickUp cards (+~$2/day against the $4 soft cap) for a
  band (16–21) he applied to twice in six weeks. **Hot bar moved to 20 at 20:00
  (`ce45a21`)**. 0.1 built behind `config.JOB_FEED_PROVIDER` at 22:38 (`6b5543e`).
- **FLIPPED 2026-08-26 03:46 IST (`534283d`, his "let's flip it now anyways"):** the job
  feed is the Upwork API — `config.JOB_FEED_PROVIDER = "mcp"`, cut-off `MCP_CLAIM_SINCE` =
  the last Chrome scan (03:15 IST = `2026-08-25T21:45:00+00:00`); the shadow poller is
  booted out (plist in `launchd/retired/`, LaunchAgent removed, copy in
  `var/_retired_agents/`); the `scanner` SOURCES entry is now "Upwork job feed (MCP)", same
  task `upwork_scan`, same plist, the MCP round inside. First MCP round = the 04:15 scan
  (result under Next actions 1). Rollback = the one config line back to "chrome".
- **Chrome rails**: the scanner's Chrome path is RETIRED from the schedule (kept in
  `scan_pipeline._fetch_chrome` as the rollback); connects (`upwork_connects`, 05:35 — the
  only applied source) still Chrome until 0.2 decides its replacement. Corrected
  2026-08-25: the follow-up monitor in v3 is the GraphQL `upwork_inbox` job (10:15/19:15),
  not Chrome; no win-loss detector exists in v3 — both were Cowork desktop tasks,
  deprecated in May, and Airtable Communications has had no write since 2026-05-20.
- **Proposals sync frozen since 9 June** (scope_blocked vendorProposals) — local
  proposals table stale, win-rate wrong. Fix: `list_freelancer_proposals` (tasks #30–33).
  Also (found 2026-08-25, fixed same night — see Codebase findings 1): the hourly
  roomList pass was writing NULL telemetry into every proposal it touched.
- **The Upwork tab is live** (2026-08-25 night, `ec5de3d` + `7279345`, redesigned
  `a10b421`): digit 2 in the cockpit, `#/upwork` (`#/pipeline` aliases to it), four sub-tabs
  — **Inbox** (conversations as people, the row Today shares), **Jobs** (the flow, the job
  lists, connects), **Health** (rails in words), **Analysis** (the old charts). Reader
  `/api/upwork`, drills `/api/upwork/rows`. Spec: `docs/upwork/PHASE1_TAB_SPEC.md` (§11 =
  the shape as built). The Phase 2 digest slot is not drawn until Phase 2 exists.
- **A prospect's email now reaches the Upwork room** (2026-08-26 05:15, `9d2102b`,
  migration 029 `room_links`): a mail whose sender matches a room's counterparty by
  first name + surname initial (`modules/upwork/room_link.py`, no model call) is linked to
  the room, queued for Review instead of being excluded by the low-base-rate rule, shown
  on the Inbox row, and handed to the draft-writer. And a waiting draft older than the
  client's newest message (Upwork or linked mail) is redrafted at the next poll
  (`1c6e4d5`). Both born of the David Séjourné draft that said "I don't see the email".
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

### Phase 1 — The Upwork tab (visibility) — BUILT 2026-08-25 (`ec5de3d` reader, `7279345` section)
As built: `docs/upwork/PHASE1_TAB_SPEC.md` is the record (cohort funnel, closure rule, the
hot bar at `config.HOT_LEAD_THRESHOLD` = 20, "viewed" and "offer" drawn as unmeasured until
0.2/0.4 give them a signal). The outline below is the original intent, kept for the diff:
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
12. (19:00) **`shadow_poll` holds the DB write lock across its MCP `get` calls**
    (`feeds/shadow_poll.py:117-208`: the with-block opens before up to 25 network calls; the
    18:40 run held it 43 s against the 10 s busy timeout). The scoring pass's run 3 died on it
    (`database is locked`) and its `runs` row 7068 was stuck 'running' (marked failed by
    hand 19:41 with his OK). **FIXED `6b5543e`** (22:38): the round is `feeds/mcp_search.py`
    (network only); the poll is one short read → the round → one short write. The 22:40
    poll ran on it: completed, 40 s, lock held only for the write.
15. (22:41) **The MCP enforces ~12 `find_jobs` requests/min sustained** ("Rate limit
    exceeded … wait about 5 seconds", refilling continuously): a dry round right after the
    :40 poll lost its 14th query. A refused query is a silently under-scanned hour for that
    term. FIXED in the same evening (`MCP_CALL_INTERVAL_S` 5 s between calls + one retry
    after `MCP_RATE_LIMIT_WAIT_S`): a 40-call round now takes ~3.5 min of its hourly slot.
    Also why the poller must retire at the flip — two rounds an hour would share one budget.
13. (19:05) **Hot jobs that miss the ClickUp push are never re-pushed**: 20 hot jobs from the
    08-23 batch sit `status='Drafted'`, no ClickUp id; every other hot job since 08-18 was
    carded in its own run. The 14 open Loom-flag cards are exactly those 20 (the rule's
    `clickup_task_id IS NULL` predicate predates the live cutover). **FIXED `ce45a21`**
    (20:00): every scan also pushes rows scored within `config.CLICKUP_PUSH_RETRY_DAYS` that
    have no card; a failed create is a warn anomaly + metric, never a failed scan. Live at
    20:15: `clickup_pushed 28, clickup_retried 23, failed 0` — the 08-23 batch is carded.
14. (19:10) **The Sunday outcome email never sends in live mode**: `outcome_capture.capture()`
    is `if ctx.shadow: record_shadow_write … else: ctx.require_live(…)` and `require_live`
    is only a guard (`runner.py:104`) — no send call exists. Setting `form_url` (0a) made the
    links parse; nothing delivers them. **FIXED `ce45a21`** (20:00): the live branch sends
    via `notify.send_gmail`, a failed send fails the run (critical anomaly), one email per
    week. Still empty until 0.3: every run so far skipped on "no closed proposals" because
    `proposals.status` has no writer (finding 3). Doctor now says `upwork_outcomes STALE
    225h` — the plist is loaded; the 08-23 fire simply never happened (Mac asleep).

## Open decisions
- ~~Tab name: "Upwork" vs "Pipeline".~~ CLOSED 18:55 — "Upwork", and Pipeline becomes it (decision 10).
- Skip-review cadence: weekly card vs fold into Monday sales report.
- Whether ClickUp stays the action queue long-term or the tab absorbs it (decide AFTER the
  tab proves itself — not before).
- ~~Threshold VALUES~~ DECIDED 19:30 (decision 11): **hot bar 16 → 20, Loom stays 22**
  (`ce45a21`). The evidence that decided it, kept for the record:
  **(2026-08-25 19:25, live, since the rail began 07-13):** hard-rule
  survivors by band → applications: 22+ 732 → 33 · 20–21 124 → 0 · 18–19 130 → 1 · 16–17
  117 → 1 · 8–15 394 → 1. He applies at the Loom bar: 33 of 36 matched applications were 22+,
  and the 16–21 band (371 jobs, all drafted and carded) produced 2. MCP drift is −1 median, so
  the feed does not move the cuts by itself. 20 keeps 35 of 36 matched applications inside
  hot and cuts the drafted/carded volume ~40 % ahead of the flip.
- **◆ NEW (20:25) — what the Loom-flag CARD means.** Its rule fires on `loom_flag=1 AND
  clickup_task_id IS NULL` — since the live cutover that is only "the push missed it"
  (finding 13), so the 14 cards were an accident and the retry just cleared their cause.
  The honest condition (flagged, open, not applied, ≤7 d) is **151 jobs right now** — as
  cards that would flood the tab AND the AM/PM brief's top-N (ranked by money at stake).
  The tab shows that population as a COUNT tile with a drill (`needs_me.loom_flags`,
  `ec5de3d`). The card itself: (a) ICP-tier only (≈2/month, actionable), (b) retire the kind
  and keep the tile, or (c) all 151. His answer (22:50): "keep it as is for now". Observed
  20:30 the same evening: the sweep decayed all 14 cards (nudges 1151–1164) the moment the
  20:15 retry carded their jobs — so with the rule as is, the card stream is dead by
  construction (it can only fire between a draft and its push, or on a failed push); the
  tile (158 at 23:35) is the only place the Loom-worthy population shows now.
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
8. (15:30) **Ask here.** All questions to Abhijeet in the working chat; no "ops chat" hand-offs.
9. (15:30) **ClickUp list ids → config** (`CLICKUP_LISTS` incl. `icp`). **Band labels on the
   tab: numbers and names** ("Hot 16–21", from `config.score_band_label()`).
10. (18:55) **Phase 1 tab — the four design decisions** (spec: `docs/upwork/PHASE1_TAB_SPEC.md`):
    **Pipeline BECOMES the Upwork tab** (renamed, digit 2, `#/pipeline` aliased; the cockpit
    blocks on top, Pipeline's charts below the fold as "Analysis") · **funnel = job cohort**
    (jobs fetched in the period; later stages joined on the job id; conversion % honest by
    construction; wins whose job was never fetched on their own line) · **interviewed =
    `upwork_status IN ('Activated','Hired')`, won = a contract on the job** (rooms stay
    "client replied") · **connects spent = applications + boosts net of boost refunds**
    (badge its own line; purchases/renewals/awards out). Viewed and offer render "—" until
    0.2 / 0.5. Tab name: "Upwork" (the open item is closed by decision 1).
11. (19:30) **Hot bar 20** (Loom 22) · spec + §7 config diff approved, applied by the chat ·
    build order: Phase 1 steps 1–2 now, section + Pipeline rename after 0.2 · approvals:
    mark run 7068 failed, fix the Loom-flag rule in step 2 (→ became the ◆ above once the
    population was measured), build the outcome-email send path, the ClickUp push retry.
12. (2026-08-26 00:05, on three mockups) **The tab's shape**: build it like the mockups —
    **Inbox · Jobs · Health · Analysis** as sub-tabs inside Upwork · **Inbox opens first** ·
    conversation rows **first one open, the rest collapsed** · **the same rows on Today**
    (one component, one queue). Standing rule from his verdict: plain words on every
    screen — no statistics vocabulary; a comparison reads "usually N %".

## Next actions
0. DONE 2026-08-25 14:30 (Upwork chat, on Abhijeet's "do 1-4"): sync fix committed
   (`42c9eaf`, live-verified: proposals NULL created_dt 10 → 0, contracts NULL title 59 → 5);
   score-bands patch applied + committed (`39faf95`; test_score_bands 11, scan/doctor/
   guardrails 87, cockpit pipeline/today/system/query-guard classes 68 — all green on the
   Mac's files). Git lock/tmp debris the device VM could not delete was moved to
   `var/_to_delete/` (safe to `rm -rf`).
0a. DONE 2026-08-25 15:05 natively on the Mac (Abhijeet approved shell access; Terminal is
   click-only for agents, so the "Control your Mac" osascript `do shell script` was used):
   - `bin/cockpit reload` → new pid 36712; `/api/pipeline/facts` now serves `score_floor`
     from config.
   - snoozes: 14 → 0 (`audit_log` carries one `hand` row per thread, source of truth for the
     revert); `PRAGMA quick_check` ok. They re-bucket at the 19:15 poll.
   - outcome form: `https://airtable.com/appgE1QoEOXvbrUE4/pag9IZ4lgwmIZA47g/form` renders
     "Proposal Outcome" (checked in Chrome) → written to `rails/REGISTRY.md:23`;
     `outcome_capture.form_url()` parses it. ◆ watch the first Sunday email: the
     `prefill_Proposal` param must land the linked record on this page-style form.
   - on the Mac (3.9): test_score_bands + test_m1b + test_scan_pipeline 78 OK;
     `bin/doctor --offline` 60 PASS / 3 WARN (calendar reminders only) / 0 FAIL.
   - `var/_to_delete/` debris removed (his approval, 15:20).
0b. DONE 2026-08-25 15:50 (this chat, per his "ask me here" rule): BUILD_BRIEF entry written
   (top of file: the orientation, the sync bug, the bands, the invariant lift, the scorecard);
   ClickUp list ids folded into `config.CLICKUP_LISTS` incl. `icp` (`bf13fbd`; the form_url
   tests now use fixtures). Full suite on the Mac: **1,620 GREEN**.
0c. ANSWERED: ClickUp ids → config (done); band labels on the tab = numbers AND names
   (`config.score_band_label()`, never typed in JS).
0d. **Scorecard (15:40 IST, 48 h from the first shadow row):** coverage of scraper-hot
   29/33 (misses: 23, 23, 21 via best_matches; 24 via my_feed) · latency median +23 min
   (= the :15/:40 schedule offset) · MCP-only 363 jobs, 132 pass hard rules.
0e. **Scoring pass (Abhijeet: "run scoring pass", 18:31 IST):** `scripts/mcp_shadow_score.py`
   + migration 028 `mcp_shadow_scores` (`f83e0b8`) — the pass 027 reserved a NULL `score`
   for. Two sets, same scorer as the live scan: A. 144 MCP-only hard-rule survivors,
   B. 81 jobs both feeds scored (drift = mcp − scraper). Writes only the shadow tables;
   9 runner chunks of ≤25 (`upwork_shadow_score_<n>`); projected $1.83. Re-runnable, skips
   scored ids. `scripts/mcp_shadow_report.py` prints the two answers as section 4.
   **RESULT (finished 19:07 IST; 239 calls, $1.78, day's spend $3.09; run 3 of the first
   attempt died on finding 12 at 18:40, the re-run at 18:43 completed 7/7 chunks):**
   **A.** 157 MCP-only survivors scored → **94 hot (60 %)**, 37 Loom-band (24 %); bands 22+ 37
   · 16–21 57 · 12–15 40 · 8–11 23 · <8 0. The scraper's own survivors run 71 % hot over the
   same 14 days, so MCP-only jobs are a little cooler and there are a LOT of them (~120–150
   survivors/day, ~70–90 hot/day, vs the scraper's ~30 hot/day). **B.** drift on 82 shared
   jobs: median −1.0, mean −0.80, 65/82 within ±2 (47 within ±1; one −8, two −5 outliers);
   hot by scraper 63 · by MCP 62 · by both 60. Section 4 of `mcp_shadow_report.py` carries it.
   Reading: the feed is trustworthy (90 % scraper-hot coverage, ≤1 point of drift, 95 % hot
   agreement); the bands are the decision, not the feed (Open decisions → threshold values).
0f. DONE 19:20 (Abhijeet: "start with phase 1 in the meantime"): **Phase 1 tab DESIGN** —
   four decisions asked and answered (decision 10), spec written to
   `docs/upwork/PHASE1_TAB_SPEC.md` (reader payload, the eight stages with their SQL and the
   closure rule, drills + parity tests, the health strip with a proposed `SOURCES` diff §7,
   the shared card builder, connects definitions, build order). Findings 12–14 came out of
   writing it. Approved 19:30 with the §7 diff (decision 11).
0g. DONE 20:00 `ce45a21` — hot bar 20; `SOURCES` + `mcp_feed`/`upwork_inbox`/
   `upwork_outcomes`/`cos_sweep`, `RERUN_JOBS` ± ; push retry (finding 13); outcome email
   sends (finding 14); run 7068 failed. Suite 1,643 OK, doctor 62/0.
0i. DONE 22:38 `6b5543e` (+ pacing, same evening) — **Phase 0.1 built, not flipped.**
   `config.JOB_FEED_PROVIDER` ("chrome" today), `MCP_*` bounds moved to config,
   `MCP_CLAIM_SINCE` (his call: only posts created after the flip get claimed);
   `feeds/mcp_search.py` = the standing-query round, network-only, paced (finding 15);
   `scan_pipeline._fetch_chrome` / `_fetch_mcp` behind the switch with ONE shared tail
   (hard rules → score → draft → card + retry → digest); the MCP path claims through
   `feed_bridge.process` (Iron-Law validation, fabrication guards) with `feed_source`
   `mcp:<query>`; a dead session fails loud; a thin round is `scan_feed_degraded`. The
   poller now shares the round (finding 12 fixed). MCP shapes captured read-only through
   the chat's Upwork connector (his OK) into `tests/fixtures/mcp/`: **0.2 de-risked** — the
   proposal node is the GraphQL `vendorProposals` shape the adapter already parses and
   `marketplaceJobPosting.id` is the one job-id space; **no per-proposal "viewed" flag
   exists** (the `insights` block is job-level: `proposals_opened/total`), so the Phase 1
   "viewed" stage is dropped or re-defined at 0.2. Live dry round at 22:41 (no writes):
   109 surfaced, 60 already in `scored_jobs`, 46 older than the cut, 3 new. 20 provider
   tests + 6 fixture tests; suite 1,703 → see 0i'. 
0h. DONE 20:21 `ec5de3d` — **Phase 1 step 1 + the builder half of step 2**: `data.upwork()`
   + `upwork_rows()` on `/api/upwork` + `/api/upwork/rows` (cohort funnel with closure,
   contracts-started line, rails strip, connects, needs-me from the shared
   `_act_now_queue()` extracted out of `today()`), 37 tests, suite 1,680 OK, cockpit
   reloaded and serving it (live: week 24 Aug so far — fetched 223 → passed 86 → hot 47 →
   applied 2 → interviewing 1 → won 0; contracts started 1, outside the funnel). The
   Loom-flag RULE change is parked as the ◆ under Open decisions.
0j. DONE 23:20 `7279345` (his "let's build the UI then") — **Phase 1 step 4, the section.**
   `static/js/sections/upwork.js` (digit 2; `#/pipeline` and a remembered "pipeline"
   alias to it): Needs me now from the shared queue (replies + escalations before Loom
   flags, top 5 + expander; tiles: drafts waiting, Loom-worthy open, offers "—"), the cohort
   funnel with the week/month toggle (SIGNAL vs anecdote typesetting, unmeasured stages as
   a dashed band with the reason, hot/applied sub-lines, contracts-started line), the rails
   strip (source-health tones → Activity drill, re-run where the registry allows), connects
   as counts, the Phase 2 digest slot as an explicit not-built state, the old Pipeline
   charts behind an Analysis expander (`pipeline.renderAnalysis`; funnel-by-week and
   threads-by-status tables stay in the fold as history the cohort view does not show —
   a deliberate deviation from spec §2, noted there). `today.queueCard` is exported: one
   card component for both screens. +7 tests (44 in `test_upwork_tab.py`), suite **1,713
   OK** on the Mac, cockpit reloaded and serving the module. Rendered headless (Playwright)
   against the live snapshot: cards, both views, drills, the fold. **Phase 1 = built**;
   what is left of it is the ◆ list (Loom card meaning, a "viewed" signal at 0.2).
   He has to open `http://127.0.0.1:5051/#/upwork` himself — the chat's Chrome bridge
   refuses 127.0.0.1.
0l. DONE 2026-08-26 04:12 `7f7d710` (his 04:00 ask: "a manual trigger button for every
   system … run it instantly with a button click to get the latest") — **Run now on every
   system**: Inbox "Check Upwork now" (`upwork_inbox`), Jobs "Scan for new jobs now"
   (`upwork_scan`), the connects strip "Update connects" (`upwork_connects`), every
   re-runnable Health rail. The kick is the allowlisted `/api/rerun`; the button then polls
   the new `/api/upwork/run?task=` (`data.upwork_run_status`, RERUN_JOBS-allowlisted) every
   4 s until a NEW run has finished, says what happened in words and re-reads the tab; a
   run already in flight is waited for, never kickstarted over. 52 tests, suite 1,721 OK.
0m. DONE 2026-08-26 04:52 `1c6e4d5` (his 04:48: "Run now … is not pulling the latest —
   look at the current inbox item"): the poll HAD read Nabil's 20:16 message; what it had
   not done was write a reply to it, because `poll_inbox` skipped every room that already
   held a pending draft ("no churn") — a draft from the 19th sat under a message from the
   25th. Now `draft_predates(generated_at, newest_inbound)`: a pending draft older than
   the client's newest inbound message (Upwork, and since 0n a linked email) is
   superseded and the room redrafted; metric `drafts_refreshed`. Unparseable clocks keep
   the draft. +3 tests (test_m1b). Suite 1,724 OK.
0n. DONE 2026-08-26 05:15 `9d2102b` (his 04:58: "he did send me an email on
   abhijeetgandhi01@gmail.com — do you not have access to it?"; his answer to the three
   options: **link it to the conversation and use it**). David Séjourné's brief (24 Aug
   19:08, "DNG: Workflow to create // clustering", the Drive folder) had been read by the
   IMAP rail and EXCLUDED by the sweep (`imap-low-base-rate:no-client-signal` — a prospect
   is not a registry client); the draft-writer reads the room alone; so it wrote "I don't
   see the email". Built: migration 029 `room_links(event_id, thread_id, how,
   matched_name)`; `modules/upwork/room_link.py` — the deterministic match (Upwork shows
   "First L."; the mail's display name, RFC 2047 decoded and accent-folded, or its address
   local part, on first name + surname initial against inbound senders of rooms active
   within `config.UPWORK_ROOM_LINK_DAYS` = 30; the same person in two rooms → newest; two
   different people → no link); sweep step 3a links + queues (Review still sees the mail,
   the room named in the evidence; metric `room_linked`); `draft_followup` gets the linked
   mails (up to `UPWORK_ROOM_LINK_MAIL_CHARS` = 1500 of body each, "treat these as
   received"); the Inbox row shows the mail as their last word when it is newer, else
   "Also by email"; `room_link.backfill()` re-examines the excluded mail in the window
   (live: 216 examined, exactly 1 linked — David's). The wrong draft was superseded by
   hand; he had meanwhile answered David himself on Upwork (05:07), so the room is
   `healthy` and off the Inbox. Suite 1,733 OK, cockpit reloaded.
   ◆ The match is name-based; a client whose Upwork name and email name differ ("Bob" vs
   "Robert", a company address) will not link — the Review queue is the safety net.
0o. DONE 2026-08-26 05:53 (overnight item 3, his 05:20: "it says 0 won this month which is
   incorrect, we at least got 2 … I know we had more than 1 interview this week") — **the
   Jobs flow's last two tiles now count what happened.** Diagnosis on the live DB: the
   cohort funnel (decision 10) counted a win only when the CONTRACT carried the FETCHED
   job's id. Royan Stomberg / Globaltize: our proposal sat on job …411922 (fetched 13 Aug,
   score 28), the client hired us on 17 Aug on a fresh posting …535633 the feed never saw
   → contract "outside the funnel", the proposal now `Archived` → not even interviewed.
   Rocky Choy: a June client, back on 24 Aug (new room, straight to a contract) → outside.
   Interviews: the tile needed `proposals.upwork_status` Activated/Hired on a cohort job,
   so Jarrod Roback (24 Aug) counted and Rocky did not. **Decision taken (his to overturn):**
   (1) Interviewing = interviews that started in the period — an INTERVIEW room a client
   opened (reply / invitation / offer), one per person; Won = contracts that started in
   the period; each split *from this period's jobs · earlier jobs · repeat clients · came
   to us*, the names under the flow; the feed's own share stays as `cohort_n` with its own
   drill. (2) Attribution widened: a cohort job is interviewed when an INTERVIEW room sits
   on its proposal, won when the same client (`client_company` on proposal = contract)
   started a contract after the fetch; an earlier contract with the client = repeat.
   Result: month → Interviewing 5, Won 2 (Royan, from this month's jobs, hired on a new
   posting; Rocky, a repeat client); week → 2 and 1. **TCC / Brandi Jordan is July by the
   DB** (contract 44099615 started 15 Jul; the room holds that one contract id) — if a
   second TCC contract exists, the hourly ROOM_LIST sync cannot see it: `list_contracts`
   (MCP plan, next actions 5) is the fix. Spec addendum written. 56 tests in
   test_upwork_tab (+3: the rooms rule, the new-posting hire, the repeat client).
0p. DONE 2026-08-26 06:00 (overnight item a, his 05:20: "we told David that we will let him
   know immediately — that should become part of the Today tab because it's a task"; he
   believed it existed) — **promises typed on Upwork become tasks on Today.** Found: 660
   commitments in the table, every one from a Fathom call; the Upwork path (a `sources` row
   of type 'upwork' fed to the extractor) was never scheduled — 2 such rows, both from
   June. Built `modules/upwork/promises.py`, run by the inbox poll (10:15 / 19:15 / "Check
   Upwork now") after ingesting: every NEW outbound message since a cursor that passes a
   phrase gate ("I'll", "will send", "let you know", "get back to you", "by Friday" …;
   `config.UPWORK_PROMISE_PHRASES`, no model call for "thanks!") goes one at a time, with
   the two messages before it, to the SAME Sonnet extractor the Fathom path uses
   (`chief_of_staff.commitments` SYSTEM + SCHEMA); who='us' rows land in `commitments`
   with cite `upwork/<room> @ <message id>`, dedup on (room, message, text). A promise
   with no date is due `UPWORK_PROMISE_DAYS` (2) after it was made — the card says so.
   The card (`rules.rule_commitments_due`, new branch): "You promised David S.: read the
   brief and let him know" · due tomorrow · "Reply on Upwork — your next message in the
   room clears this by itself" · Open on Upwork · Done / Snooze; the detail names the
   room, the IST day, the cite. AUTO-CLEAR (law 5): a later outbound message in the same
   room marks the promise done at the next poll and the sweep clears the card. Bounds: a
   week's look-back on the first run, `UPWORK_PROMISE_MAX_PER_RUN` (12) extractor calls
   per poll, a failed extraction is retried next poll (the cursor stops before it), a
   failure is a metric on the poll (`promise_errors`), never a failed run. Config diff
   applied under his standing approval (three UPWORK_PROMISE_* lines). 12 tests
   (`tests/test_upwork_promises.py`). ◆ For him: the extractor is the design's Sonnet call
   (~$0.01 per promise-shaped message, ~3/day); a deterministic-only version would be
   free but blunter — say if the spend matters. ◆ The first poll after deploy reads the
   last 7 days of his outbound mail (64 messages) → up to 12 extractor calls, the rest at
   the next poll.
   **First live run (10:15, run 7188): 68 outbound messages scanned, 12 sent to the
   extractor (the per-run cap; 1 deferred — David's, it comes at the next poll), 9 promises
   found, 7 already kept (a later message of his in the room), 0 errors.** Read with his
   eyes: 4 real (ping Mustafa when a window is found; walk Amanda through the demo — OPEN
   since 20 Aug; send Carly the written breakdown — OPEN since 21 Aug; start Rocky's
   automation within 2 days), 3 weak ("stay in the call waiting for Carly" from a check-in,
   "assist Rocky" from "I can definitely assist", "available for a call at 10:30" from an
   offer of a time — all three already closed by a later message, so no card), and 2 from
   **2025** (Jonah Weber's room had its history re-pulled this week, so year-old messages
   were "new" by ingested_at). Fixed the same hour (`<p26>`): a message must be SAID inside
   the look-back window, not just stored inside it; the extractor is told what is not a
   commitment (a capability, an offered time, a check-in, thanks, work already done).
   ◆ The auto-clear rule ("your next message in the room = delivered") is right for
   reply-type promises and blunt for deliverable-type ones (Rocky's "done within 2 days"
   was closed by a later hello). If that bites, the refinement is to auto-clear only
   promises whose text is a reply (let you know / get back / ping / send), and leave the
   rest to Done — his call.
   Second run (10:26, run 7190, kicked through Run now): 42 scanned, 12 to the extractor,
   5 found (Jarrod's proposal due today; Dhanya's two zaps by Thursday / Friday; David's
   "review the brief and let him know" → due 28 Aug; one OTP promise already kept). After
   the sweep, **six promise cards on Today** with Open on Upwork (`99f2eb8`: a dated Upwork
   promise is carded by the Upwork branch too — named, with the room; its own date kept).
   Model spend for the day at 10:35: $1.08 of $4 over 107 calls.
0r. DONE 2026-08-26 06:29 (overnight item 1, his 05:20: "I like the way you built UI for the
   upwork tab or at least the direction you headed. I want UI of all the tabs improved now in
   the same way") — **the other six tabs, in the Upwork tab's style.** Mockups first
   (`var/patches/2026-08-26_mockups/` — eight pictures, his morning review), then built:
   **Today** — the one ranked list ("a lot of data laid out without structure") is now
   grouped by what each card asks of him: *People waiting on you* (the conversation rows),
   *Things you promised* (commitments in words — "promised 20 Aug on the call 'Impromptu
   Google Meet'", "promised 26 Aug on Upwork, to David S. · due tomorrow"), *Needs a
   decision*, *Housekeeping*; each group longest-wait first with a fold at five; the
   health box is one sentence ("5 systems are late — the names. Their numbers on this
   page may be behind." → Activity › Health); hot leads, the four figures and the brief
   sit under "More". **Money** — five views: *This month* (came in / went out / left in
   plain numbers with "by this day last month" and "a usual month ends at"; where it came
   from, payer by payer; what it cost; money you are waiting on), *Cash*, *History* (the
   month table, the chart, the whole picture, the running rate), *Ledger*, *Statements*;
   every figure still drills, the parity check still runs. **Activity** — *Today* (the
   day in words, run by run), *Health* (EVERY system in words with Run now — the Upwork
   Health table for the whole machine; `/api/system` now carries `rerun_jobs` and
   `task_words`), *Every system*, *Spend*. **Clients** — one row per client in words (what
   they pay, how you talk, what is owed, what the brain knows; the dossier opens under
   the row) under *Working with now* / *Everyone* (the audited table, unchanged) / *Needs
   a look*. **Knowledge** — *Ask* first, then *Dossiers*, *What came in*, *Search facts*.
   **Review** — a sentence on top (how many need a name, how old the oldest), *To decide* /
   *Decided* / *Dismissed*, and each item says "the system thinks: Hipp Health (9 in 10
   sure)" instead of a row of pills; the decisions themselves are untouched. One helper
   (`static/js/subtabs.js`) gives every section the same bar (remembered view, the hash
   kept honest). Tests: `tests/test_tabs_redesign.py` (8). Nothing that was on any page
   is gone — it moved under a view or a fold.
   ◆ FOR HIM (the choices made while he slept, each easy to flip): (1) Today's groups and
   their order — people, promises, decisions, housekeeping; (2) Money opens on *This month*
   with the range bar inside it; "All time" lives on *History*; (3) Clients' "working with
   now" = a live contract or money in 90 days, "needs a look" = owed / unattributed / a
   contract Upwork still calls live with no payment for four months; (4) the Review
   wording ("9 in 10 sure"); (5) Brandi Jordan shows "no dossier yet" on Clients because
   the ledger's payer name and the registry's "The Cradle Company" differ — the same
   linkage gap the roster panel already lists.
0k. DONE 2026-08-26 00:40 `a10b421` — **the tab REDESIGNED on his verdict** (23:50: "I don't
   like the UI at all … too complicated for me to understand anything … the part that I
   hate most is the client comms and how you represent it — it all should be intuitive").
   Three mockups sent, four answers (decision 12), then built: **Inbox · Jobs · Health ·
   Analysis** as sub-tabs of one page (`#/upwork?view=…`, remembered; Inbox opens first).
   Inbox = one **conversation row per person** (`static/js/conversation.js`, the SAME row
   Today renders): who they are and what they are to us, the last thing they said (their
   words, where, when — a mail subject with a Gmail link when the newest word is an
   email), the draft waiting (flagged when it predates their message), one state word
   with its days (reply owed / gone quiet / probably resolved), Open on Upwork / Copy
   draft / Done / Snooze; click → the last messages as bubbles + the full draft. Jobs =
   the flow as six plain numbers ("usually 44 %" in grey, an arrow only when the change is
   real), the contracts that started with where they came from, the job lists as an
   inline table, connects as a strip. Health = every rail in words with Run now. Copy
   rule pinned by a test: no "baseline / anecdote / cohort / n ≥ / pp" on the screen.
   Server side: `data._conversation()` (one clock for the three timestamp shapes),
   `upwork()` gains `inbox` + `summary`; the job rows carry client/budget/draft length.
   49 tests, suite **1,718 OK**, cockpit reloaded. The first cut's rails strip and
   funnel bands are gone; spec §1/§6 now describe the OLD layout — §11 records the new.
   His verdict (00:55): **"I like the UI better now"** — and "I don't see anything in the
   inbox yet": it was empty because he had cleared every conversation at 23:33, and the
   screen did not say so → `f670e5c`: the Inbox names what was cleared since yesterday
   (`inbox.cleared`, from nudge_state + confirmed closures) and that each comes back the
   moment the person writes again. Suite 1,719 OK.
1. **THE FLIP — DONE 2026-08-26 03:46 IST (`534283d`), on his "let's flip it now anyways so
   in the morning when I wake up I can check how it went".** Sequence as the runbook: the
   03:40 poll allowed to finish → poller booted out + LaunchAgent removed (03:44) → patch
   applied → 147 targeted tests + full suite 1,719 OK + doctor 0 FAIL → cockpit reloaded →
   committed. **First MCP round = run 7145, 04:15–04:20 (298 s):** `provider: mcp`, 15
   queries, 116 surfaced, **47 skipped as older than the cut** (the cut = the last Chrome
   scan, `2026-08-25T21:45:00+00:00` = 03:15 IST — nothing lost between the feeds, nothing
   stale claimed), **12 new claimed** (`mcp:gohighlevel` 3 · `mcp:claude` 3 ·
   `mcp:workflow automation` 3 · `mcp:vapi` 1 · `mcp:ai automation` 1 · `mcp:pipedrive` 1;
   smart_search and the other seven terms surfaced nothing new inside the hour), 9 killed by
   the hard rules (fixed_below_500 ×5, hourly_below_30 ×3, rate_unstated_low_client ×1),
   **3 scored → 3 hot (27, 26, 22)**, 2 drafts grounded + 1 BLOCKED by the validator
   (an unsourced "35"), **3 ClickUp cards pushed, 0 failed, digest sent, 0 anomalies**. The
   12 rows sit in `scored_jobs` with `feed_source mcp:*`; the Jobs tab counts them. The
   Chrome scan tab was not touched (rollback path). **Second round = run 7155, 05:15–05:19
   (247 s):** 14 queries, 117 surfaced, 66 already known, 42 older than the cut, **9 new
   claimed** (`mcp:claude` 6 · `mcp:gohighlevel` 3), 6 killed by the hard rules, **3 scored
   → 3 hot**, 2 drafts grounded + 1 ungrounded (kept, flagged), 3 ClickUp cards pushed,
   0 failed, digest sent, 0 anomalies. The 04:45 sync (run 7149) ran as always (proposals
   35 updated / 43 terminal-preserved, contracts 54, transactions 22, submitted_proposals
   still `scope_blocked`). Third round = run 7166, 06:15 (167 s): 6 claimed (claude 3, workflow automation 2,
   automated reports 1), 3 scored → 1 hot, 1 draft (ungrounded, flagged), 3 cards, digest
   sent, 0 anomalies. Three clean rounds = the flip holds; the 13:15 watch list in the
   runbook is the next check. Pre-flip preparation (03:40):
   `var/patches/2026-08-26_p11_THE_FLIP.patch` (checked clean against HEAD) + the runbook
   `var/patches/2026-08-26_FLIP_RUNBOOK.md` (8 steps, the 13:15 watch list, the one-line
   rollback). The patch = (a)+(b) below plus: the poller plist moved to `launchd/retired/`
   (install.sh only loads `launchd/*.plist`), the scanner SOURCES entry relabelled "Upwork
   job feed (MCP)", the MCP-poll row out of the Health table, the Chrome-path tests pinned to
   `JOB_FEED_PROVIDER="chrome"` and the provider tests to `MCP_CLAIM_SINCE=None`. Claim cut
   proposed = 12:15 IST 26 Aug (`2026-08-26T06:45:00+00:00`) — confirm with him at apply.
   (a) `config.JOB_FEED_PROVIDER = "mcp"` and `MCP_CLAIM_SINCE = "<now, ISO UTC>"`;
   (b) `launchctl bootout gui/$(id -u) launchd/io.etherwise.v3.upwork-mcp-shadow.plist`
   (the poller retires — one MCP budget, one round an hour) and drop `mcp_feed` from
   `SOURCES` + `mcp_shadow_poll` from `RERUN_JOBS`; the `scanner` entry keeps `upwork_scan`
   (same task name, same plist, MCP inside) — its `why` gets the word "MCP";
   (c) commit, then watch the next :15 run: `provider: mcp` in its metrics, `feed_source`
   `mcp:*` on the new rows, no `scan_feed_degraded`, cards + digest as before;
   (d) leave the Chrome scan tab where it is — `_fetch_chrome` stays in the file as the
   rollback path (`JOB_FEED_PROVIDER = "chrome"`, no other change).
   Then 0.2 → 0.3 → 0.4 → 0.5. (All in this chat now.)
2. Phase 1: BUILT (0g/0h/0j). Left inside it: the ◆ items only — the Loom-flag card
   meaning (his "keep as is for now"), the "viewed" stage once 0.2 shows what signal
   exists, the offers tile once 0.4 lands.
3. (first Monday after 0.2) First Loop 2 skip review — even manual, to shape it.
4. (when he says go) Phase 3 applicant pull for the LinkedIn-writer role.
5. **The MCP use cases (overnight item 2, 06:03) — plan written: `docs/upwork/MCP_USE_CASES.md`.**
   The short answer to his example: yes — `get_freelancer_financials transactions` per org
   (the agency is a second `org_uid`, not the ACE that fails "Authorization failed" on every
   hourly sync) replaces the CSV import for daily use; the CSV stays as the yearly audit
   (its running-balance tie-out has no API twin; the weekly `summary` becomes the continuous
   tie-out as a doctor check). Live picture that decided it: the GraphQL sync has inserted
   no personal transaction since 24 June and no agency row since 3 June; all 72 of August's
   Upwork rows are his CSV imports. Order: **A** transactions + summary → **B** connects
   (`connects_balance`, the last Chrome rail retires) → **C** proposals (0.2) → **D**
   contracts + milestones + end reasons (0.3; also the "second TCC contract?" answer) →
   **E** offers (0.5) → **G** the one-call dashboard heartbeat → **F** messages (0.4) →
   **H** timesheets (hours worked, not yet invoiced — new on Money) → the small ones (rate
   insights as a scoring input, saved jobs, the profile's real rate). ~150 reads a day at the
   proposed cadences. **Eight real-account calls wait on his OK, one at a time** (listed in
   §12 of the plan: `list_accounts`, transactions on each org, summary, connects_balance,
   contracts search on the agency org, dashboard check, list_rooms). **Built without any
   new call (06:55, `<p23>`): `modules/upwork/feeds/mcp_financials.py`** — the request side
   to the tool reference (transactions / summary / connects_balance per `org_uid`, dated
   windows, `limit` 200, truncation reported never swallowed), `orgs()` (TALENT = personal,
   AGENCY = agency), the row mapping as ONE function per kind tolerant of both key
   spellings (`transaction_row`, `connects_event` — the latter uses the Chrome rail's own
   kind rule and id hash so the two sources agree), and `run()` that fetches everything then
   writes once through the audited `upsert_transaction` (canonical `upwork:<recordId>` — an
   MCP row UPDATES a CSV row, never twins it; new rows tagged `Upwork-MCP`) and
   `connects_history`. `config.MCP_FINANCIALS_LOOKBACK_DAYS = 14`. NOT scheduled (no plist,
   no SOURCES entry, not in RERUN_JOBS — a test pins that). 9 tests. The fixtures from his
   three OKs (§12 items 2, 3, 5) pin the mapping; then the plist and the SOURCES entry.

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
  bind config; `tests/test_score_bands.py` greps them.
- 2026-08-25 14:30 · Abhijeet: "do 1-4 from your end" → the chat applied and committed the
  patch (`39faf95`) and committed the sync fix + map + brief (`42c9eaf`) via the device shell;
  the DB write (snoozes) and `bin/cockpit reload` stay his (Mac-only by the standing caution
  / launchctl); form link left for a one-click verification.
- 2026-08-25 18:31–19:25 · "run scoring pass" → migration 028 + `scripts/mcp_shadow_score.py`
  (`f83e0b8`), pass run twice (lock collision, finding 12), results in 0e. "start with phase 1
  in the meantime" → the four tab decisions (decision 10) and `docs/upwork/PHASE1_TAB_SPEC.md`.
  Findings 12–14 recorded. Threshold evidence tabled under Open decisions; the hot bar and
  the flip volume are one decision, his.
- 2026-08-25 19:30–20:25 · "any questions or get started?" → four answers (decision 11) →
  `ce45a21` (hot bar 20, rails registered, push retry, outcome email sends) and `ec5de3d`
  (the Upwork reader, drills, shared card builder). The Loom-flag rule change he approved
  was measured before shipping (151 jobs → cards) and turned into a question instead.
- 2026-08-25 20:40–22:50 · "what's next" → 0.1 first, one read-only MCP capture, only
  post-flip posts at the flip, Loom card "keep as is for now". → `6b5543e` the provider
  path (not flipped) + fixtures; the rate limit found by the dry round and paced the same
  evening. The flip is a checklist awaiting his go (Next actions 1).
- 2026-08-25 23:00–23:25 · "I will be there at the 12:15 scan so we can flip it after, so
  let's build the UI then" → the flip is scheduled for 2026-08-26 after the 12:15 scan with
  him present; the Phase 1 section built and committed tonight (`7279345`, 0j). Pipeline is
  now the Upwork tab; its charts live in the Analysis fold.
- 2026-08-25 23:50 – 08-26 00:40 · "I don't like the UI at all … too complicated … the
  part I hate most is the client comms" → three mockups, four answers (decision 12), the tab
  rebuilt as Inbox / Jobs / Health / Analysis with the conversation row shared with Today
  (`a10b421`, 0k). Suite 1,718 OK. Cockpit reloaded; his eyes are the acceptance test.
- 2026-08-26 03:45–04:20 · "let's flip it now anyways" → THE FLIP at 03:46 (`534283d`),
  first MCP round 04:15 clean (Next actions 1). "add a manual trigger button for every
  system" → Run now everywhere (`7f7d710`, 0l). Both watched to completion in the chat.
- 2026-08-26 04:48–05:15 · "not pulling the latest" → a stale pending draft is redrafted
  (`1c6e4d5`, 0m). "he did send me an email … do you not have access to it" → three
  options asked, his pick "link it to the conversation and use it" → `room_links`
  (`9d2102b`, 0n): a deterministic name match, never a model guess; ambiguity links
  nothing; Review keeps seeing every linked mail. Second MCP round (05:15) clean.
- 2026-08-26 05:20 · He goes to bed with an overnight list (this chat works alone; every
  design choice below is recorded as a note for him, not assumed silently): (a) a promise
  made in an Upwork message ("I'll take a look and let you know") must become a task on
  Today; (1) every other tab redesigned in the Upwork tab's style; (2) the Upwork MCP tools
  surveyed for use cases — transactions first, to replace the CSV import; (3) the Jobs
  numbers "0 won this month / 1 interviewing" are wrong (TCC/Brandi Jordan, Globaltize/
  Royan won; more than one interview this week).
