# PHASE1_TAB_SPEC.md — the Upwork tab, designed before it is built

**Status:** BUILT — approved 2026-08-25 19:30 (with the §7 diff; hot bar → 20 the same minute); steps 1–3 `ce45a21` + `ec5de3d`, step 4 (the section, §2/§6) `7279345` at 23:20; open items are the ◆ list in §10 · **Written:** 2026-08-25 19:05 IST (Upwork chat) ·
**Builds on:** `UPWORK_BRIEF.md` (Phase 1, laws 1–7, decisions 5/6/9), `docs/upwork/CODEBASE_MAP.md`
§5–§6, `codebase-maps/03_cockpit.md`. Every number in §0 was read from the live DB (read-only) at
18:45 IST; every file:line was checked in the working tree at `39faf95`+.

The rule this document obeys (CLAUDE.md): design decisions get asked, not guessed. The four the
code forced were asked and answered in chat at 18:55 IST; they are §0.1. Everything else here is
either derived from those answers or marked ◆ open.

---

## 0. The decisions this spec is built on

### 0.1 Answered 2026-08-25 18:55 IST (Abhijeet, in the Upwork chat)

1. **Tab identity — Pipeline BECOMES Upwork.** The Pipeline tab is already 100 % Upwork data.
   It is renamed "Upwork" (keyboard digit 2, `#/pipeline` bookmarks aliased); the brief's cockpit
   blocks go on top; Pipeline's charts move below the fold as "Analysis". One channel, one tab,
   one funnel.
2. **Funnel model — job cohort.** A period's funnel is the set of jobs FETCHED in that period;
   every later stage counts jobs of that cohort that reached it, joined on the job id, whenever
   they reached it. Conversion % is honest by construction. The current period reads "so far".
   Wins whose job was never fetched are shown on their own line, never dropped.
3. **Stage signals — Activated/Hired → interviewed; contracts → won.** Interviewed =
   `proposals.upwork_status IN ('Activated','Hired')` on the job (Upwork's own "active" state);
   0.3 writes `status='Interview'` from the same signal, so nothing moves at 0.3. Won = a
   contract on the job (`contracts.job_id`), dated by `start_dt`. Rooms
   (`threads.room_type='INTERVIEW'`) remain "client replied", not interviews.
4. **Connects "spent" = applications + boosts, net of boost refunds.** Badge is its own line;
   purchases / renewals / awards are inflows and stay out of "spent". Per applied job = net ÷
   applications in the window; per interview = net ÷ proposals that reached Activated/Hired,
   small-N flagged.

### 0.2 Carried in from the brief

- Decision 5: one card engine, two views — Today keeps the ranked top-12 with Upwork cards in it;
  this tab lists EVERY Upwork card. One Done, honoured everywhere.
- Decision 6: a snooze action on the tab, with an expiry that is honoured (the v2 relics at
  2099-12-31 were the failure).
- Decision 9: band labels are numbers AND names, from `config.score_band_label()`, never typed
  in JS.
- Law 5: default view ≤ one screen; counts before lists; lists behind drills; every card carries
  an OBSERVED auto-clear condition. Law 6: SIGNAL (n ≥ 30) is typeset differently from ANECDOTE.
  Law 7: no number without a drill.

### 0.3 What the live DB says (18:45 IST, read-only copy)

| Fact | Value | Why it matters |
|---|---|---|
| Job ids are one id space | `scored_jobs.id`, `connects_history.job_id`, `proposals.marketplace_job_id`, `contracts.job_id`, `offers.job_id` are all the bare 19-digit `marketplaceJobPostingId` | the cohort join is real, no mapping table needed |
| Applied ↔ fetched | 40/40 rail applications (since 07-13) resolve to a `scored_jobs` row; 37 of the 40 scored ≥ 16 | applied ⊆ fetched holds today; applied ⊄ hot (3 of 40) |
| Wins outside the funnel | 6 contracts started since 06-30; 1 has its job in `scored_jobs`; 08-17 "Ai Automation Engineer" and 08-24 "Simplebookme…" match nothing (not fetched, not in the rail, no proposal row) | the "won outside the funnel" line is not a corner case, it is most recent wins |
| Interview signals | `upwork_status`: Activated 25 (3 v3-era), Hired 26 (1), Archived 31 (2), Declined 1 (1) · `status` (May import): Interview 27, Won 21, Expired 25, '' 10 · `threads.room_type`: INTERVIEW 118, ONE_ON_ONE 26 | decision 3 |
| Viewed / offer | no viewed column anywhere; `offers` has no v3 writer (30 rows, last write 06-11) | two "—" tiles until 0.2 / 0.5 |
| Connects since 07-13 | applied −793 (40 rows) · boost −7,264 (47 rows, all on applied jobs) · boost_refund +4,343 · badge −112 · inflows +3,478 (Added extra Connects 3,200, renewals 200, awards/earned 78) | decision 4 |
| Cards open now | `dept='sales'`: owed_reply 5, hotlead_no_loom 14, escalate_client 1 · pending drafts 11 (followup 7, owed 2, unmatched 2) | the Upwork filter is `dept='sales'` + follow-up cards |
| Loom cards | all 14 sit on jobs from the 08-23 batch of 20 hot jobs that were drafted but never carded (`status='Drafted'`, no ClickUp id); every other hot job since 08-18 is `ClickUp Created` in its own run | the push has no retry (◆ finding 3.13) |
| Rail health, 7 d | scan 145 ok / 19 failed · connects 8 / 2 · inbox 13 · sync 163 · shadow poll 29 + 1 `skipped_locked` · cos_sweep 20 + 42 `skipped_empty` | the strip has real variance to show |

---

## 1. The tab, top to bottom (default = one screen)

Numbers in the sketch are illustrative shapes, not measurements — the measured ones are in §0.3.

```
 UPWORK                                                  as of 25 Aug · r to refresh
 ┌ Needs me now ─────────────────────────────────────────────────────────────┐
 │ 5 replies owed · 14 Loom flags · 6 drafts waiting · offers: — (0.5)       │
 │ [card] [card] [card] [card] [card]                       show all 20 →   │
 └───────────────────────────────────────────────────────────────────────────┘
 ┌ Funnel · week ▾ (24 Aug, so far) ─────────────────────────────────────────┐
 │ fetched 312 → passed 201 → hot 148 → applied 9 → viewed — → interviewed 1 │
 │   → offer — → won 0        each bar: n · % of previous · Δ vs 8-wk        │
 │ contracts started this week: 1 — 0 from this cohort · 0 earlier · 1 outside│
 └───────────────────────────────────────────────────────────────────────────┘
 ┌ Rails ────────────────────────────────────────────────────────────────────┐
 │ ● scanner 18:17 ok · ● MCP poll 18:40 ok · ● proposals 17:45 ok · ○ messages│
 │ 10:15 ok · ○ offers not built (0.5) · ● applied 05:35 ok · ● outcome Sun   │
 └───────────────────────────────────────────────────────────────────────────┘
 ┌ Connects (counts) ────────────────────────────────────────────────────────┐
 │ spent this week 480 · this month 2,131 · per application 53 (n=9)         │
 │ per interview — (n=1, anecdote)                                            │
 └───────────────────────────────────────────────────────────────────────────┘
 ┌ This week's learning ─────────────────────────────────────────────────────┐
 │ nothing measured here yet — Loop cards arrive in Phase 2                   │
 └───────────────────────────────────────────────────────────────────────────┘
 ▸ Analysis (score distribution · why we skip · feed ROI · weekly throughput · lead quality)
```

Order is the brief's. Blocks 1–5 are the default screen; "Analysis" is the former Pipeline
page behind one `expander` (ui.js:543) — collapsed by default, remembered per browser in
`localStorage.ew_cockpit_upwork_analysis`.

---

## 2. Tab identity and the migration from Pipeline

| Item | Change | Where |
|---|---|---|
| Registry | `{ id: "upwork", label: "Upwork", icon: "trending-up", load: () => import("./sections/upwork.js") }` replaces the `pipeline` entry at the SAME position (digit 2) | `app.js:47` |
| Old links | `ALIASES.pipeline = "upwork"` — a `#/pipeline` bookmark opens the tab, never a blank screen | `app.js:57` |
| Icon | keep `trending-up` (exists in the sprite, `index.html`); ◆ a distinct Upwork glyph is a 20-line sprite addition if wanted | `index.html` |
| Last-section memory | `localStorage.ew_cockpit_last_section` may hold `pipeline`; `byId()` falls back to Today today — the alias map is applied to the stored value too (one line in `boot()`) | `app.js:460` |
| The old file | `sections/pipeline.js` keeps its charts and becomes the Analysis module: `export async function renderAnalysis(root, ctx)`. Its OWN funnel block, outcomes block, KPI strip, win-rate line and funnel-by-week table are REMOVED (they are the new tab's blocks now) — the file's header note already documents dropping dead blocks per brief §4; this is the same move. **As built (`7279345`):** `renderAnalysis` = `render(root, ctx, {analysisOnly: true})` — the heading, KPI strip, win-rate line, funnel and outcomes blocks are skipped; the **funnel-by-week and threads-by-status tables STAY in the fold** (deliberate deviation: they are history the cohort view does not show, and removing them would orphan the range bar they share) | `sections/pipeline.js` — funnel + outcomes blocks (~489–630); KPI strip, win-rate line, funnel-by-week and threads-by-status (~1078–1385) |
| Routes | `/api/pipeline`, `/api/pipeline/facts`, `/api/pipeline/rows` STAY (the Analysis charts read facts/rows; `test_cockpit.py` pins the `/api/pipeline` keys). `/api/upwork` + `/api/upwork/rows` are added | `server.py:33-70` |
| State keys | `ew_cockpit_pipeline_filters` (the Analysis range bar) is kept as-is; new keys are `ew_cockpit_upwork_view` (`week`|`month`) and `ew_cockpit_upwork_analysis` (`open`|`closed`) | section |
| Badge | none in Phase 1 — Today already carries the same cards; a second badge for the same work is noise. ◆ revisit if the tab becomes the primary screen | `app.js` |

Nothing about the Money, Clients, Knowledge, Activity or Review tabs changes.

---

## 3. The reader — `data.upwork(db_path=None, today=None)`

House rules it obeys (map §5.1): `_ro()` only, no model call, `today` POSITIONAL (the clock-slot
test), every figure equal to one `upwork_rows` query with the same predicate, band cuts bound
from `config` at call time (never a literal — `tests/test_score_bands.py` greps this file too).

### 3.1 Payload (JSON, keys pinned by `TestUpworkReader`)

```
{
  "as_of": "2026-08-25",
  "bands": {"hot": "Hot 16–21", "loom": "Loom 22+", "hot_cut": 16, "loom_cut": 22},   # from config
  "needs_me": {
    "cards":   [ …every Upwork card, ranked, same shape as today().queue… ],
    "total": 20, "shown_default": 5,
    "clusters": [{"label": "replies owed", "count": 5}, {"label": "Loom flags", "count": 14},
                 {"label": "escalations", "count": 1}],
    "snoozed": {"count": 2, "next_back": "2026-08-27"},
    "drafts_waiting": {"count": 6, "days": 10, "note": "hot, drafted, not applied, scored in the last 10 days"},
    "offers": {"count": null, "measured": false, "reason": "offers rail not built (Phase 0.5)"}
  },
  "funnel": {
    "week":  { …§3.3 block… },
    "month": { …§3.3 block… }
  },
  "rails": { "items": [ …§3.5 rows… ], "worst": "ok|degraded|stale|down", "not_built": ["offers"] },
  "connects": {
    "week":  { …§3.6 block… }, "month": { …§3.6 block… },
    "source": { …source_health record for connects_rail… }
  },
  "digest": {"built": false, "reason": "Phase 2 — Loop cards render here"}
}
```

Both funnel views and both connects windows are computed server-side in one call so the
week/month toggle is a client-side switch with no refetch (Pipeline's range bar works the same
way). Cost: ~12 queries over `scored_jobs` (~4.8k rows) + the queue builder; well under the
Today reader.

### 3.2 Periods

- **week** = ISO week, Monday–Sunday, exactly `pipeline()`'s `funnel_weeks` convention
  (`this_mon = d0 - timedelta(days=d0.weekday())`, `data.py:1141`). Key = the Monday, `YYYY-MM-DD`.
- **month** = calendar month, key `YYYY-MM`.
- The current period carries `"so_far": true` and its label says so.
- Baseline windows (§3.4): week view → the 8 completed weeks before the current one; month view →
  the 3 completed months before the current one.

### 3.3 The cohort funnel — stage by stage

Cohort `C(lo, hi)` = `SELECT id, score, hard_rule_skip FROM scored_jobs WHERE fetched_at LIKE '20%'
AND substr(fetched_at,1,10) BETWEEN :lo AND :hi` (the `LIKE '20%'` guard is Pipeline's, for the
malformed-timestamp rows). Every stage below is a predicate over a cohort job `j`.

| # | key | Label on the tab | Predicate (SQL fragment against `j`) | Measured today | Drill stage |
|---|---|---|---|---|---|
| 1 | `fetched` | Fetched | all of `C` | yes | `fetched` |
| 2 | `passed` | Passed the hard rules | `j.hard_rule_skip IS NULL` | yes | `passed` |
| 3 | `hot` | Hot ≥ 16 *(label from config)* | `j.hard_rule_skip IS NULL AND j.score >= :HOT` — sub-line "of which Loom 22+: n" (`>= :LOOM`) | yes | `hot` |
| 4 | `applied` | Applied | `EXISTS (SELECT 1 FROM connects_history h WHERE h.kind='applied' AND h.job_id = j.id)` **∪ closure** (see below) | yes — stamped with the connects-rail verdict | `applied` |
| 5 | `viewed` | Viewed by client | no column exists | **no** → "—". **Measured 22:35 (fixtures/mcp/proposal_get.json): the MCP proposal shape carries NO per-proposal viewed flag; its `insights` block is job-level (`proposals_opened` / `proposals_total` / `shortlisted` across all applicants).** So at 0.2 this stage is either dropped or re-defined as "client is opening proposals on this job" (job-level, from the get's insights) — Abhijeet's call at 0.2 | — |
| 6 | `interviewed` | Interviewing | `EXISTS (SELECT 1 FROM proposals p WHERE p.marketplace_job_id = j.id AND p.upwork_status IN ('Activated','Hired'))` ∪ closure | yes (sparse: 3 v3-era) | `interviewed` |
| 7 | `offer` | Offer received | `EXISTS (SELECT 1 FROM offers o WHERE o.job_id = j.id AND o.state <> 'DELETED')` ∪ closure | **no** → "—", reason "offers are never ingested (finding 3.2) — Phase 0.5" | — |
| 8 | `won` | Won (contract) | `EXISTS (SELECT 1 FROM contracts c WHERE c.job_id = j.id)` | yes | `won` |

**Closure rule (makes the outcome chain nested by construction).** A job at a later outcome
stage is counted at every earlier outcome stage: a contract implies an interview implies a
proposal was sent, even when the connects rail missed the application (Chrome contention,
finding 3.9 — e.g. the 08-24 Activated proposal has no rail row). So
`won ⊆ interviewed ⊆ applied ⊆ fetched` always holds and no step can read > 100 %. The
reader also returns how many rows each stage owes to closure (`"inferred": {"applied": 2}`);
the drill marks those rows `inferred: 1` with the stage that proved them. An inferred count
that keeps growing is a rail-health signal, and it is visible, not silent.

**Unmeasured stages are skipped, never inferred.** `viewed` and `offer` render as "—" with the
reason on the tile; the conversion chain runs applied → interviewed and interviewed → won
directly until their spine rows exist (0.2 / 0.5). The moment a stage has a writer, the same
predicate is switched on — no redesign.

**The hot split.** `applied` is a subset of `fetched`, not of `hot` (3 of 40 applications went
to non-hot jobs). The bar therefore shows `applied 40` with the split `37 hot · 3 below the hot
bar`, and the step "hot → applied" is computed as `applied ∧ hot / hot` ("of the hot jobs, how
many did you apply to") — the honest reading of what the scanner's hot band is for. The
non-hot applications are never dropped: they are in the `applied` drill with their score.

**Carded is a note, not a stage.** Since the live ClickUp cutover every hot job is carded in its
own run (verified 08-18 → 08-25: every hot row is `ClickUp Created` except the 08-23 batch of 20
that missed the push — finding 3.13). The hot bar's sub-line says "n carded · m missed the
push" with a drill; a stage that is structurally equal to its parent is a dead metric
(pipeline.js header, brief §4).

**Contracts context line** (the "won outside the funnel" requirement, decision 2). Under the
funnel, for the SAME period, dated by `contracts.start_dt`:
`started: T — a from this cohort · b from earlier cohorts (drill lists which week) · c outside
the funnel (job never fetched)`. `c` is `contracts WHERE substr(start_dt,1,10) BETWEEN lo AND
hi AND job_id NOT IN (SELECT id FROM scored_jobs)`. Today that line would read, for August:
"started: 2 — 0 · 0 · 2 outside the funnel". That is the fact the cohort funnel would otherwise
hide, and it is a business fact (invites / direct hires are converting where the scanner is
not looking), so it is printed every time, zero or not.

Per-stage payload:

```
{"k": "applied", "label": "Applied", "n": 40, "measured": true, "inferred": 2,
 "of_prev": {"k": "hot", "n_den": 148, "pct": 25.0},          # null when prev is unmeasured
 "baseline": {"pct": 18.4, "n_den": 1180, "periods": 8},      # pooled over the baseline window
 "delta_pp": 6.6, "signal": true,                              # n_den >= 30 → SIGNAL, else ANECDOTE
 "reason": null,                                                # text when measured == false
 "drill": {"stage": "applied", "week": "2026-08-24"}}
```

### 3.4 Baseline and delta (the "vs trailing 8-week average")

- `pct` for step a → b = `100 * n_b / n_a` over the period's cohort.
- `baseline.pct` = the POOLED ratio over the baseline window: `100 * Σ n_b / Σ n_a` across the 8
  completed weeks (3 completed months). Pooled, not the mean of eight ratios — with 6–14
  applications a week a mean-of-ratios lets one 1-for-1 week weigh as much as a 3-for-40 week.
- `delta_pp` = `pct − baseline.pct`, in percentage points, rendered "+6.6 pp" / "−2.1 pp".
- `signal` = `n_den >= 30` (law 6). A SIGNAL step gets the tone and the delta arrow; an
  ANECDOTE step is typeset muted with "n = 9" beside it and NO arrow — the delta is still in the
  payload, it just is not dressed as a trend. Today only fetched → passed → hot → applied are
  SIGNAL in a normal week; every later step is ANECDOTE for months (n ≥ 30 interviews needs
  ~4 months at the current 6–14 applications/week).
- Maturity: the current cohort is right-censored ("so far"); the baseline includes last week,
  which is also still maturing for the late stages. Stated on the tile hint ("late stages fill
  in over 2–6 weeks"); not corrected for — a correction would be a model, and this tab prints
  observations.

### 3.5 Rails (the health strip)

Source: `source_health.source_health(conn)["sources"]` (`core/source_health.py:162`), filtered
to the keys below, plus the latest `runs` row per task for the "result" text
(`_metrics_summary`, `data.py:3201`). One row per rail the brief names, in this order:

| Strip label | v3 task today | Cadence | `SOURCES` key | Re-run (`actions.RERUN_JOBS`) | Notes |
|---|---|---|---|---|---|
| Job feed (scanner) | `upwork_scan` | :15 hourly | `scanner` (exists, `config.py:787`) | yes | retires at 0.1 — the row disappears with the plist |
| MCP poll | `mcp_shadow_poll` | :40 hourly | **proposed** `mcp_feed` (§7) | added in the same change (the registry test demands it, §7) | shadow until the flip; `critical: False` until then |
| Proposals sync | `upwork_sync` (roomList pass) → 0.2 | :45 hourly | `upwork_sync` (exists, backs `money`) | yes | label changes to the MCP task at 0.2 |
| Messages sync | `upwork_inbox` → 0.4 | 10:15 / 19:15 | **proposed** `upwork_inbox` (§7) | yes | finding 3.8 — invisible today |
| Offers sync | — | — | — | — | rendered as `built: false`, "not built (Phase 0.5)" — visible so its absence cannot be mistaken for health |
| Applied (connects rail) | `upwork_connects` | 05:35 daily | `connects_rail` (exists, backs `applied`) | yes | its verdict is also stamped on the applied bar and on every connects figure |
| Outcome email | `upwork_outcomes` | Sun 11:00 | **proposed** `upwork_outcomes` (§7, `catch_up: False`) | no — leaves `RERUN_JOBS`; the deliberate `/api/rail outcome-capture` button stays | finding 3.8 (invisible; the 08-23 run silently did not happen) AND **finding 3.14, found writing this spec: in live mode `capture()` composes the email and never sends it** — `outcome_capture.py:95-100` is `if ctx.shadow: record_shadow_write … else: ctx.require_live(…)` and `require_live` is only a guard (`runner.py:104`); there is no send call. The form-link fix alone does not make a Sunday email arrive. The strip row says "composes, does not send" until the send path exists |
| ClickUp push | inside `upwork_scan` | with the scan | derived, no source entry | — | "n carded in the last scan · m hot jobs missed the push (7 d)" from `scored_jobs`; there is no push task and no push anomaly kind today (◆ 3.13) |
| Cards sweep | `cos_sweep` | every 2 h | **proposed** `cos_sweep` (§7, `empty_ok: True`) | yes | law 5 depends on it: a dead sweep never decays a card |

Row payload: `{key, label, built, task_name, status, last_success, age_hours, cadence,
result, run_id, day, rerun}`. Pill tones map 1:1 to `.pill.ok/.degraded/.stale/.down`
(`components.css`). Click → `ctx.navigate("activity", {day, run})` (the run's full detail on
Activity); the re-run button → `ctx.kick("/api/rerun", {task})` for rows whose task is in
`RERUN_JOBS` (`actions.py:52`). `worst` is the strip's one-word verdict for the heading.

### 3.6 Connects (counts only — dollars stay on Money)

Windows: `week` = last 7 days incl. today and `month` = calendar month to date, both from
`connects_history.event_date` (the page shows no time). Per window:

| Field | Definition |
|---|---|
| `applications` | `COUNT(*) WHERE kind='applied'` — the same rows `_rail_applied_between` counts (`data.py:192`), so this number can never differ from the Today tile or the funnel bar |
| `applied_cost` | `-SUM(connects_delta) WHERE kind='applied'` |
| `boost_cost` | `-SUM(connects_delta) WHERE kind='boost'` |
| `boost_refunds` | `SUM(connects_delta) WHERE kind='boost_refund'` |
| `spent_net` | `applied_cost + boost_cost − boost_refunds` — **the headline** (decision 4) |
| `badge` | `-SUM(connects_delta) WHERE kind='badge'` — its own small line |
| `inflows` | `SUM(connects_delta) WHERE kind IN ('other','renewal','refund')` — context line "+n bought / renewed / refunded", never inside "spent" |
| `per_application` | `spent_net / applications` (null when applications = 0, rendered "—") · `n = applications` |
| `interviews` | proposals with `upwork_status IN ('Activated','Hired')` AND `substr(created_dt,1,10)` in the window (proposals sent in the window that reached an interview — cohort-consistent with the funnel; the Activated transition itself has no timestamp) |
| `per_interview` | `spent_net / interviews` · `n = interviews` · ANECDOTE below 30 |

Every figure drills to the `connects_history` rows of the window (`upwork_rows(stage="connects",
frm/to, kind)`), faceted by `kind`, with `amount(row) = connects_delta` so the drill's own sum
must equal the tile (drill.js "header sum"). The block carries the `connects_rail` health record;
a stale rail greys every figure ("not current since <date>") — the July lesson, kept.

### 3.7 Learning digest slot

`{"built": false, "reason": …}` → `emptyState("nothing measured here yet — Loop cards arrive in
Phase 2")`. An explicit not-built state, not an absent block: a slot that only appears when it
has content is indistinguishable from a slot that stopped rendering.

---

## 4. Drills — `data.upwork_rows(db_path=None, *, stage=None, week=None, month=None, frm=None, to=None, kind=None, _today=None)`

Contract (map §5.1): every user param validated first with `_bad_filter` (200 + `{"error", rows:
[], count: 0, total: None}`), rows capped at 300, `total` = the UNCAPPED `COUNT(*)` of the same
predicate, `_today` underscored (unreachable from the URL). `week`/`month` are the funnel's period
keys (exactly one of them); `frm`/`to` are day windows for the connects drill; `kind` is
`connects_history.kind`.

| `stage` | Rows | Columns (drill.js `columns`) | Facets |
|---|---|---|---|
| `fetched` | cohort jobs | id, title, score, band, hard_rule_skip, feed_source, d (fetched), job_url, clickup_task_url | band, feed |
| `passed` | cohort ∧ `hard_rule_skip IS NULL` | same | band, feed |
| `hot` | cohort ∧ passed ∧ `score >= HOT` | same + carded | carded, loom |
| `hot_missed_push` | hot ∧ `clickup_task_id IS NULL AND status='Drafted'` | same | — |
| `applied` | cohort ∧ (rail row ∨ closure) | id, title, score, band, applied_on (rail `event_date` or null), inferred (stage that proved it), connects (rail delta), job_url | hot / not hot, inferred |
| `interviewed` | cohort ∧ (Activated/Hired ∨ closure) | id, title, score, proposal id, upwork_status, created_dt, thread_url (`ROOM_URL`), inferred | upwork_status |
| `won` | cohort ∧ contract | contract id, title, client_company, contract_type, start_dt, hourly_rate, job_url | contract_type |
| `contracts_started` | contracts with `start_dt` in the period, each tagged `cohort: this|earlier(<week>)|outside` | contract id, title, client_company, start_dt, cohort tag, job in scored_jobs (y/n) | cohort tag |
| `connects` | `connects_history` rows in `[frm, to]`, optional `kind` | event_date, kind, label, job_title, job_id, connects_delta | kind |
| `drafts_waiting` | delegates to `today_rows(which="fresh")`'s predicate — one helper, so Today and the tab agree | as Today | — |

Parity is the test: for every measured stage in `upwork()["funnel"]["week"]`, `n ==
upwork_rows(stage=k, week=key)["total"]`, and likewise for month. Unmeasured stages have no drill
(`drill: null`) and the reader refuses `stage="viewed"`/`"offer"` with the reason in the error
envelope — a drill that returns 0 rows for an unmeasured stage would be the fabricated zero.

Query-param registry (`server.py:97`): `stage`, `week`, `frm`, `to`, `month` already exist;
**`kind`** is the one new name (validated in the reader against the rail's kind vocabulary is
NOT done — it is an open-vocabulary equality filter, same reasoning as `feed`/`icp`).

---

## 5. Needs me now — the card seam

### 5.1 One builder, two views

`today()` currently assembles the queue inline (`data.py:1788-2197`: follow-ups from pending
drafts, decisions from both stores, forwarding across re-keys, active nudges, superseding,
dedup, closure overlay, provenance law, clusters). That body moves into
`_act_now_queue(conn, db_path, day) -> {"queue", "clusters", "hidden_no_provenance",
"auto_closed", "follow_ups"}` with ZERO behaviour change; `today()` calls it and keeps its own
view rules (drop `hotlead_no_loom` — "that IS the paused backlog", `data.py:2103` — then slice
`TODAY_QUEUE_TOPN`). `upwork()` calls the same builder and applies the tab's view rule instead:

```
is_upwork(e) = e["kind"] == "followup"            # every follow-up is an Upwork room (drafts ⋈ threads)
            or e.get("dept") == "sales"           # owed_reply, hotlead_no_loom, escalate_client, won_no_outcome, lost_pattern
```

(`dept` is carried onto nudge entries at `data.py:2132`; follow-up entries get `dept: "sales"`
added in the builder.) No cap: `cards` is the whole ranked Upwork set, `shown_default = 5` is a
render hint. The `hotlead_no_loom` skip therefore moves OUT of the builder and INTO `today()` —
Today still hides Loom flags, the tab shows them (the brief's 🎥 item).

Tests pin the seam: `today()["queue"]` equals the builder's output minus Loom flags, sliced;
`upwork()["needs_me"]["cards"]` equals the builder's output filtered by `is_upwork`; a Done or a
snooze posted through `/api/nudge` or `/api/cos-nudge` disappears from BOTH readers in the same
test.

### 5.2 The four groups the brief names

| Group | What it is on the tab | Source | Auto-clear condition OBSERVED today (law 5) | What 0.x adds |
|---|---|---|---|---|
| Offers awaiting response | count tile, "—" | none (finding 3.2) | — | 0.5: `offer:<id>` card, clears when `offers.state` leaves PENDING |
| Replies owed / stale threads | cards: follow-up (draft ready) + `owed_reply` + `escalate_client` | drafts ⋈ threads; `rule_owed_replies` (`rules.py:41`) | clears when the draft is sent (`sent_status <> 'pending'`) or the counterparty writes again (`evidence.counterparty` ts > Done) — observed only at 10:15/19:15 by `upwork_inbox` (finding 3.4) | 0.4: hourly reply detection — the Dhanya class of failure closes here |
| Drafts ready for review | count tile "n proposal drafts waiting" + drill (not cards — counts before lists) | `today_rows(which="fresh")`'s predicate: hot, `draft_proposal IS NOT NULL`, not in the rail's applied ids, scored within `TODAY_FRESH_LEAD_DAYS` | drops when applied (rail) or when the job ages out of the fresh window | 0.2: applied from proposals, not the Chrome rail |
| 🎥 Custom-Loom flags | a COUNT tile (`needs_me.loom_flags`: flagged, open, not applied per the rail, scored ≤ 7 d — with its ICP sub-count and a drill) PLUS whatever `hotlead_no_loom` cards the engine holds, shown here and hidden on Today | tile: `_loom_flags_sql()` in `data.py`; cards: `rule_hotlead_no_loom` (`rules.py:146`), still `loom_flag=1 AND clickup_task_id IS NULL AND status actionable AND scored within 7 d` | tile drops when applied or aged out; a card decays when carded or aged past 7 d | **Measured 2026-08-25 20:20 before changing the rule: the honest condition is 151 jobs in 7 days (1 ICP).** As cards that floods the tab and the AM/PM brief's top-N (ranked by money at stake), so the rule was NOT changed; the tile carries the truth. ◆ the card's meaning is Abhijeet's call: ICP-tier only (≈2/month) · retire the kind and keep the tile · all 151. ◆ nothing observes "a Loom was recorded" |

### 5.3 Actions on a card

Identical to Today's — the card component is `today.js`'s renderer exported (`renderCard(item,
ctx)`) so the two views cannot drift: Done / Snooze (1, 3, 7 days → `snooze_days`) / Dismiss via
`act()` (`today.js:610`), routed by kind to `/api/nudge` (follow-ups, `nudge_state` + write-through)
or `/api/cos-nudge` (engine nudges). The tab adds one line above the cards: "n snoozed · back on
<earliest snooze_until>" with a drill listing them — decision 6's "observed expiry" is the
expiry being honoured and visible; both readers already hide a snoozed card only while
`snooze_until > day` (`data.py:1750-1763`).

---

## 6. The section — `static/js/sections/upwork.js`

- `export const meta = { title: "Upwork", hint: "the channel, end to end" }`;
  `export async function render(root, ctx)`; one `ctx.api("/api/upwork")`; sections render once,
  `r` refreshes (app.js:443).
- Primitives only from `ui.js`: `heading`, `kpi` (with `unmeasured: true` for "—" — never a
  hand-built dash, ui.js:305), `pill`, `notice`, `emptyState`, `expander`, `table`, `fmt.*`,
  `dash()`; drills via `openDrill` with `total`, `time`, `facets`, `capNote` (drill.js:100-145).
- Funnel drawing: a small pure `drawFunnel(stages)` inside the section — bars sized by `n`,
  the step label "n · pct% of previous · Δ" and the ANECDOTE typesetting (`.muted` + "n = k").
  `funnel.js` (`computeFunnel`) is NOT reused: it derives bands by narrowing a row set in the
  browser; the cohort funnel is server-computed (joins across five tables). The 131 % lesson is
  kept by construction on the server (§3.3 closure) and pinned by the parity tests, not by JS.
- Week/month toggle: two buttons, state in `localStorage.ew_cockpit_upwork_view`, no refetch.
- Rails strip: one `pill` per rail, tone = status; `built: false` rows render as a hollow pill
  with "not built (0.5)".
- Analysis fold: `expander("Analysis", (body) => renderAnalysis(body, ctx))` importing
  `renderAnalysis` from `./pipeline.js` — loads on first open only.
- Tokens only (`tokens.css`); any new modifier goes in `components.css` (a `.anecdote` text
  tone is the one addition expected).

---

## 7. Proposed config diff (config is protected — Abhijeet applies)

`core/config.py`, append to `SOURCES` after the `scanner` entry (`config.py:793`):

```python
    {"key": "mcp_feed", "label": "Upwork MCP poll (find_jobs)",
     "task_name": "mcp_shadow_poll", "max_age_hours": 3,       # hourly at :40
     "degrade_kinds": (), "critical": False,                    # shadow until the 0.1 flip
     "backs": "upwork",
     "launchd_label": "io.etherwise.v3.upwork-mcp-shadow", "catch_up": True,
     "why": "the MCP job feed; becomes the funnel's only source at the 0.1 flip"},
    {"key": "upwork_inbox", "label": "Upwork inbox (messages, GraphQL)",
     "task_name": "upwork_inbox", "max_age_hours": 12,        # 10:15 + 19:15, 9h gap + grace
     "degrade_kinds": (), "critical": True, "backs": "upwork",
     "launchd_label": "io.etherwise.v3.upwork-inbox", "catch_up": True,
     "why": "reply detection for every owed-reply card; stale means cards outlive"
            " the replies that should have cleared them"},
    {"key": "upwork_outcomes", "label": "Sunday outcome email",
     "task_name": "upwork_outcomes", "max_age_hours": 192,    # weekly + a day of grace
     "degrade_kinds": (), "critical": False, "backs": "upwork",
     "launchd_label": "io.etherwise.v3.upwork-outcomes", "catch_up": False,   # an email must not repeat
     "why": "the Loop 2 outcome-reason capture; the 08-23 run silently did not happen"},
    {"key": "cos_sweep", "label": "Card sweep (chief of staff)",
     "task_name": "cos_sweep", "max_age_hours": 5,            # every 2h + grace
     "degrade_kinds": (), "critical": True, "backs": "today", "empty_ok": True,
     "launchd_label": "io.etherwise.v3.cos-sweep", "catch_up": True,
     "why": "decays every card whose condition is no longer observed; a dead sweep"
            " means nothing ever auto-clears"},
```

The four `launchd_label` values are the plist labels in `launchd/` (`upwork-mcp-shadow`,
`upwork-inbox`, `upwork-outcomes`, `cos-sweep` — checked in the working tree; `bin/doctor`
checks the pairing).

`tests/test_cockpit.py:3690 test_catch_up_safe_sources_are_all_rerunnable` binds the registry to
the re-run button both ways: a `catch_up: True` source MUST be in `actions.RERUN_JOBS`, a
`catch_up: False` one MUST NOT. So the same change (agent-writable, `actions.py:52`) adds
`"mcp_shadow_poll": "io.etherwise.v3.upwork-mcp-shadow"` and REMOVES `"upwork_outcomes"` from
`RERUN_JOBS` — the outcome rail keeps its deliberate `/api/rail` button (`RAIL_JOBS
"outcome-capture"`, `actions.py:35`, pinned by `test_rail_kicks_outcome_capture`). Effect beyond
the tab: `bin/doctor` and the brief's health headline start reporting these four; `bin/catch-up`
starts kickstarting three of them — which is why the outcome email is `catch_up: False` (an
email must not repeat, once it actually sends — finding 3.14).

---

## 8. Tests (written with the reader, before the section)

`tests/test_upwork_tab.py`:

- `TestUpworkReader` — migrate a temp DB, seed one synthetic cohort (fetched 12 · passed 8 · hot 5
  · applied 3 by rail + 1 by closure · interviewed 2 · won 1 · one contract outside the funnel
  · one 'Drafted' uncarded hot job); pin the payload keys; pin every stage `n`; pin `inferred`;
  pin the contracts context line (this/earlier/outside); pin "unmeasured" for viewed/offer with
  `n: None` (never 0); pooled baseline math on a two-week fixture; `signal` flips at 30; band
  labels come from `config.score_band_label` under a patched `HOT_LEAD_THRESHOLD`.
- `TestUpworkParity` — for each measured stage and both views, `n == upwork_rows(...)["total"]`;
  the connects tile equals the drill's `Σ connects_delta`; `drafts_waiting` equals
  `today_rows(which="fresh")["total"]`.
- `TestUpworkServerParams(CockpitServerCase)` — `/api/upwork` 200 with the pinned keys;
  `/api/upwork/rows` bad `stage`/`week`/`month`/`frm` → error envelope, `total: None`;
  `stage=viewed` → error with the reason; `?today=` and `?_today=` unreachable (the clock-slot
  test extends automatically because it iterates `GET_ROUTES`); `kind` present in
  `QUERY_PARAMS`.
- `TestUpworkCards` — the shared builder: Today = builder − Loom flags, sliced; tab = builder
  filtered; Done/snooze through both POST routes hides the card in both readers; a snoozed card
  returns on `snooze_until`.
- `TestUpworkRails` — with the four proposed sources present: a task that never ran → `down`;
  `built: false` rows carry no status; the derived ClickUp line counts the seeded missed-push job.
- `tests/test_score_bands.py::TestNoLiteralBands` — add `upwork()`/`upwork_rows()` to the grep list.
- `tests/test_cockpit.py` — the `/api/pipeline` key pins stay green (route untouched); the
  `SECTIONS`/`ALIASES` sanity test (if any) learns `upwork`.

Run on the Mac (3.9): the new file + `tests.test_cockpit` classes + `test_score_bands` +
`test_card_identity` + `test_card_kinds`; then the full suite before commit.

---

## 9. What waits on Phase 0 — and what does not

| Block | Reads real tables now | Waits on |
|---|---|---|
| Needs me now | yes (cards, drafts waiting) | offers tile: 0.5 · reply auto-clear latency: 0.4 |
| Funnel 1–4, 6, 8 | yes | — |
| Funnel 5 (viewed) | no | 0.2 — and only if the MCP proposal shape carries a viewed signal; otherwise the stage is dropped from the brief |
| Funnel 7 (offer) | no | 0.5 |
| Contracts context line | yes | — |
| Rails | yes for 7 of 9 rows | config diff (§7); offers row: 0.5; MCP row relabels at 0.1 |
| Connects | yes | `upwork_connects`' replacement is decided with 0.2 (decision 3) — the definitions above are rail-agnostic |
| Digest | placeholder | Phase 2 |

The brief's next action 2 says the skeleton is built after 0.2. Nothing in this design needs
0.2 to be CORRECT — the "—" tiles are designed for it — so the reader, the drills, the shared
queue builder and their tests can be built now with no UI risk, and the section + the Pipeline
rename land when Abhijeet says go. Recommended order:

1. DONE `ec5de3d` — reader + rows + tests (no UI)
2. DONE `ec5de3d` — `_act_now_queue` extraction + the Loom-flag view flag (Today's queue
   pinned equal to the builder's output minus Loom flags); the RULE change parked (§5.2 ◆)
3. DONE `ce45a21` — config diff (§7) applied by the chat on his approval; doctor green
4. DONE `7279345` (2026-08-25 23:20) — `upwork.js` + Pipeline → Upwork rename + Analysis fold;
   `bin/cockpit reload` done; rendered headless (Playwright against the live snapshot: cards,
   week/month, drills, `#/pipeline` → the tab, the fold) — the chat's Chrome bridge cannot open
   127.0.0.1, so the in-browser check is Abhijeet's; screenshot sent to the chat. Suite 1,713 OK.
5. DONE — UPWORK_BRIEF 0j + Next actions 2, BUILD_BRIEF entry, this status line.

Size: reader ~350 lines, rows ~150, builder extraction ~40 (moved, not written), section ~450,
pipeline.js −380, tests ~400. No migration. No model call anywhere on the tab.

---

## 10. Open (◆) — small, none blocks the build

1. Sprite: keep `trending-up` or add an Upwork glyph.
2. Nav badge for "needs me" — none in Phase 1 (§2); revisit when the tab is the daily screen.
3. `rule_hotlead_no_loom` — the honest predicate is 151 jobs/week (measured); the tab shows
   the count, the card needs a narrower meaning (ICP-only?) or retirement — see §5.2.
4. Finding 3.13 — hot jobs that miss the ClickUp push are never re-pushed (20 on 08-23). A retry
   in the next scan is a 10-line change in `scan_pipeline`; belongs to 0.1 or to a small fix now?
5. "A Loom was recorded" has no observer — the Loom-flag card cannot auto-clear on its real
   condition until one exists (the `loom_fetch` rail sees new recordings; matching one to a job
   is the missing piece).
6. Month-view baseline = 3 completed months (§3.2) — or the same 8 weeks re-cut?
7. Finding 3.14 (§3.5): the Sunday outcome email has no send path in live mode — is that a
   Phase 1 fix (one `notify.send_gmail` call behind the existing `INTERRUPT_POLICY` pattern) or
   Loop 2's first build item? Until then Loop 2 has no outcome-reason intake.

Findings surfaced while writing this spec, for the brief: **3.12** `shadow_poll` holds the DB
write lock across up to 25 MCP `get` calls (`feeds/shadow_poll.py:117-208`; 43 s at 18:40 vs
the 10 s busy timeout) — the scoring pass's run 3 died on it and its `runs` row 7068 is stuck
'running'; **3.13** hot jobs that miss the ClickUp push are never re-pushed (20 on 08-23);
**3.14** the outcome email never sends.
