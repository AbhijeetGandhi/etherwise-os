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
  scanner (:15), follow-up monitor (2x daily), win-loss detector (1pm).
- **Proposals sync frozen since 9 June** (scope_blocked vendorProposals) — local
  proposals table stale, win-rate wrong. Fix: `list_freelancer_proposals` (tasks #30–33).
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

## Open decisions
- Tab name: "Upwork" vs "Pipeline".
- Skip-review cadence: weekly card vs fold into Monday sales report.
- Whether ClickUp stays the action queue long-term or the tab absorbs it (decide AFTER the
  tab proves itself — not before).

## Next actions
1. (ops chat, today) Cutover scorecard → provider path → flip. Then 0.2 → 0.3 → 0.4 → 0.5.
2. (upwork chat, after 0.2) Phase 1 tab skeleton: health strip + funnel from real tables.
3. (upwork chat, first Monday after 0.2) First Loop 2 skip review — even manual, to shape it.
4. (upwork chat, when he says go) Phase 3 applicant pull for the LinkedIn-writer role.

## Decision log
- 2026-08-25 · Tab in cockpit, NOT a separate platform. No revenue on the tab. Dedicated
  Upwork chat boots from this brief; ops chat keeps the overview. Learning loops 1–4 and
  laws 1–7 agreed in principle (this brief is their spec).
