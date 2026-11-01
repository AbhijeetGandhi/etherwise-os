# etherwise-v3-cockpit-audit-2026-10-31.md — the signal-layer audit and tonight's build list

**For:** the Claude Code build session, 2026-10-31 night · **Owner:** Abhijeet · **Written:** 2026-10-31 by the ops
chat after walking all 33 cockpit views and reading the live DB (30 Oct 18:30 IST). Visual version: the Cowork
artifact `cockpit-audit-2026-10-31`. Raw numbers: `outputs/audit/data-findings.md` in that session.

**Status: BUILT 2026-11-01 overnight** (the ops chat itself, commits `5eb2ca0` `beac02e` `e08c11f` `0a4acb0`
`cd71806` + batch F). Measured after: Today 45 → 14 cards · AM brief commitments 158 → 21 lines, grouped ·
Review 79 → 20 · anomalies 3,270 → 36 · 12 leads on day one · 31 launchd jobs (was 36). See BUILD_BRIEF.md for
the entry and the four things left for him. Items 3.4 (Activity merge) was NOT built — he kept both views.

Build conventions come from `CLAUDE.md` and `UI_BUILD_BRIEF.md`. Every law still holds: drafts only, nothing
sent, `—` ≠ 0, every figure drills, `bin/cockpit reload` after Python changes, tests green.

---

## 0. The diagnosis in one paragraph

The data is good; the signal layer is unfiltered. The AM/PM brief lists **158** overdue commitments while the
Today tab, same data, shows **4** (the email skips the two filters Today applies). "People waiting on you" shows
**17**, of which **14** are win-back drafts on Upwork threads dead 150–1,054 days. The Review badge says **79**,
of which **48** are payment notices, vendor sequences and Upwork notification emails, and **11** are leads the
queue has no bucket for. **3,139** anomalies are open, 2,280 "critical", and `bin/notify` has never run. In 30
days the system decayed 1,660 cards itself; Abhijeet marked 9 done. The fix is rules and merges, not features.

Rule for every change tonight: **a surface may ask for his attention only if he would act on it today.**
Everything else lives in a tab he visits.

---

## 1. Decisions already taken (his, 2026-10-31)

His answers, 2026-11-01 (the build follows these exactly):

1. **PM brief: keep, as a DELTA only** — 20:00 shows what changed since 11:00 (replies in, new leads, cards closed, rails broken); no repeated lists.
2. **Scan emails: unchanged** — the per-scan "Upwork scan: N scored, M hot" digests stay.
3. **Leads bucket: build tonight** — Review "Lead" action → `leads` table → Today "New leads" line → Pipeline › Leads; website-form mail auto-files as a lead.
4. **Teammates' commitments: keep in v3 as `who='team'`**, visible on the dossier / weekly report, never on Today or the brief. Abel/Jaap (client-side) → `who='client'`. No ClickUp writes.
5. **Merges approved:** Upwork › Health → gone · Knowledge 4 → 2 · Pipeline › Agents → folded into Flow. **Not approved:** Activity Health + Every system stay as two views (and the run log keeps its default).
6. **v2 leftovers: retire dashboard-server (:5050), fuse-cleanup, reconcile-airtable; keep backup-db.**
7. **Sachin Kadam: he should be logging** — the gap line stays; draft a nudge for Abhijeet to send.
8. **A/B test: send more** — Today gets a "Drafts ready to send" strip with the five best drafts of the day (Copy + Open on Upwork); the sample size stays 40 a side.

---

## 2. Tier 1 — stop the noise (≈2 h)

### 2.1 Follow-ups: win-back drafts are not owed replies
- **Where:** `modules/cockpit/data.py` `_act_now_queue`, the `_fu_rows` query (~L1908):
  `FROM drafts d JOIN threads t … WHERE d.sent_status = 'pending'`.
- **Fact:** pending drafts by kind = reactivation 17, followup 1, owed 1. The reactivation drafter
  (`modules/upwork/reactivation.py`, Mondays) writes `draft_kind='reactivation'` into the same table.
- **Fix:** `AND COALESCE(d.draft_kind,'') != 'reactivation'`. Same exclusion in `_upwork_needs_me` /
  `_drafts_waiting_sql` if the Upwork Inbox reads the same rows (it does — Rasheed K., 1,054 days, is its top card).
- **Expect:** Today "People waiting" 17 → 3 (NETFLY 7d, Rocky 4d, Celsius 35d); Upwork Inbox 17 → ~3.
  Win-back drafts keep their home on Pipeline › After the hire.
- **Test:** pin it — a pending reactivation draft must not produce a follow-up card.

### 2.2 The brief's commitments section
- **Where:** `modules/chief_of_staff/brief.py::build_brief` → `commitments.open_commitments(db, who="us")` →
  `overdue = [c for c in ours if c["due"] and c["due"][:10] < today]`; `render_html` prints one `OVERDUE:` line
  per item, unbounded, in both briefs. Footer prints "709 open commitments, 227 awaiting client".
- **Fix (rule B, what `rules.rule_commitments_due` already does):** exclude `review_state='stale'`; exclude
  `due < today - COS_COMMITMENT_STALE_DAYS` (30). Then **group by client**, newest due first, **top 5 + "N more on
  Today"**. Replace the footer with the count that passed the filter. Give the narrator the filtered list only.
- **Measured effect on today's 158:** A (stale flag) → 88; B (+30-day floor) → 30; C (+has client) → 27.
- **Growth curve for the record:** 140 (17 Oct) → 158 (30 Oct); it never went down.

### 2.3 Commitments that are not his
- 711 open who=us; only 68 name Abhijeet. First-word scan: Abel 9, Shruti 6, plus Ankita/Yogita/Jaap.
- **Fix:** in the extractor (`modules/chief_of_staff/commitments.py` or wherever `who` is set), a "we will"
  line whose subject is a named teammate → `who='team'`; a Hermon-side person (Abel, Jaap) → `who='client'`.
  Back-fill the open rows with one UPDATE (list the names in config, not in code).
- **Routing of `who='team'`:** per his answer in §1 (ClickUp task vs drop). Until decided, exclude from
  every surface.
- **One-off:** `PYTHONPATH=. python3 bin/cos-drain-commitments --stale-weeks 4 --apply` flags 256 more →
  599 of 711 off every surface. `--confirm-dead` only after he has seen the surviving list (the 30 are in the
  findings file §1).

### 2.4 Review intake: three rules + a one-off exclusion
- **Where:** the sweep's intent gate / proposer (`modules/jarvis/…` — the sweep already files
  `{marketing, relationship_event, account_notice}` intents; last sweep excluded 38 of 44).
- **Rule 1 — platform notices:** a payment / withdrawal / invite notice from a domain already on a client in
  `config/clients.yaml` (`info@app.globaltize.com`, `payments@app.globaltize.com`) = `account_notice` →
  auto-mapped, never queued. (15 of the 73.)
- **Rule 2 — Upwork notification mail:** "X sent you a message" emails about a room the GraphQL rail already
  holds (`upwork_inbox` / `upwork_pulse`) = excluded as duplicate signal. (5.)
- **Rule 3 — vendor list:** a config list of senders we buy from or who sell to us (Hero Marketing / josh@marketlikeahero.com,
  HubSpot marketing, siliconindia, myHQ zohodesk, JustCall, Shellkode, trooinbound, Aastha's proposal mails) = excluded. (21.)
- **Rule 4 — recordings with no client:** a Fathom/Loom source whose participants match no registry client and
  no prospect domain files as `internal`, not review. (7 "Impromptu Google Meet Meeting".)
- **One-off:** exclude the current 48 (state='excluded' is a ledger state; the producing query is state='queued').
- **Expect:** badge 79 → ~25 (14 real + 11 leads) and it stays low.

### 2.5 Escalations: one card per situation
- 15 open `escalate_client` nudges = ~5 situations (CacheSwipe/Hermon ×6, Shruti dashboard ×3, ZDH/Omar ×2,
  Mustafa milestone ×2, Rocky ×1) + 1 Upwork **invite** rendered as an escalation.
- **Fix:** the escalation proposer dedups on (client, `intent_hash`) before `write_nudge`; an existing open
  card for the same (client, intent) is refreshed, not re-minted. Invites route to the Upwork inbox, never to
  escalations. Heal: decay the 10 duplicates now.

### 2.6 Housekeeping cards the system can do itself
- 7 open `stale_dossier` cards ("Unprocessed sources: horizons-aba, amanda-thomas, mustafa-ahmed, rocky-choy,
  adelye, the-cradle-company, simran-bhola — run extraction"). kb-daily runs at 05:15; sources that land after
  it sit until tomorrow and get carded meanwhile.
- **Fix:** the 2-hourly `cos_sweep` runs the extraction step for clients with unprocessed sources (bounded, same
  budget line as kb_extract), and `rule_stale_dossier` stops minting cards for it. Run it once now to clear the 7.

### 2.7 Anomalies
- Open 3,139 (critical 2,280): `catch_up_gave_up` 1,488 (filed every 30 min for the same stuck source),
  `task_failure` 779, `pulse_failed` 749 (one evening of no DNS = 154 rows), rest 123. 2,680 older than 7 days.
  `bin/notify` has 0 runs — "critical" has reached nobody since August.
- **Fix:** (a) an anomaly self-resolves when the same `task_name`'s next run completes (`runner._finish_run`);
  (b) `catch_up_gave_up` dedups per (source, IST day); (c) `pulse_failed` network errors are `warn`;
  (d) bulk-resolve everything older than 7 days now (set `resolved_at`, audit_log row);
  (e) either schedule `bin/notify` (criticals → one email per hour max) or delete it and carry "N criticals in
  24h" on the brief's system line. Pick one — not both, not neither.

---

## 3. Tier 2 — one fact, one place (≈2 h)

### 3.1 Idle Upwork contracts
- Same fact on Today › Housekeeping ("19 Upwork contracts open with nothing on them"), Clients › Needs a look
  (12, "a live contract but nothing paid since …"), Review › Dismissed threads.
- **Keep:** Clients › Needs a look. Drop the Today system card (`rule_idle_contracts` or wherever kind='system'
  mints it) — or mint it monthly at most.

### 3.2 Upwork › Health → gone
- `sections/upwork.js` Health view duplicates Activity › Health and still renders "Offers rail — Not built yet ·
  Phase 0.3" (`data._UPWORK_NOT_BUILT`) although `upwork_pulse` has read offers since 2026-08-26.
- **Fix:** remove the Health subtab (keep the header's "every rail is fine" sentence linking to
  `#/activity?view=health`); delete the `_UPWORK_NOT_BUILT` row. Update `tests/test_upwork_tab.py`.

### 3.3 Knowledge: 4 views → 2
- Keep **Ask** and **Dossiers**. Fold **What came in** (4 stat cards) into Ask's freshness line and **Search
  facts** into the Ask box as a toggle (ask = model call, search = FTS, free). `sections/knowledge.js`.

### 3.4 Activity: Health + Every system → one table
- Health = 26 sources with Run now; Every system = 32 jobs with Run now. One table, a toggle "sources / all
  jobs". Run log default filter: hide `completed` rows of `upwork_pulse`, `catch_up`, `upwork_scan` skips
  (283 rows/day today, most of them the quarter-hour heartbeat). `sections/activity.js`.

### 3.5 Clients: drop the Airtable status merge
- `clients_airtable()` is merged by exact name; the Status column reads "—" on nearly every row; the fetch
  made the first load take ~10 s. Remove the merge from the page (keep the route if anything else uses it);
  status comes from the ledger (paying / quiet / ended). `sections/clients.js`, `data.clients`.

### 3.6 Pipeline tidy
- **Agents** view → fold into Flow (the chips already sit in Flow's footer). `sections/lifecycle.js`.
- Scanner "Why we skip": collapse the "(v2 rule)" legacy reasons into the current names (display map only;
  `_HOURLY_SKIPS` shows the pattern). `sections/pipeline.js`.
- Mustafa's unfunded milestone: Money › waiting on + at most one Today card (covered by 2.5).

---

## 4. Tier 3 — add (≈2 h)

### 4.1 Leads bucket (the real gap)
- Today ~11 queued events are prospects with nowhere to go — including **"New inquiry from Hannah Melotto
  (Melotto Group)"**, a website-form lead via `onboarding@resend.dev`, 3 days in a mapping queue — and
  pointjust.org/.co ×3, boostjet.co ×2, shiftember.org, david@dng.ai, plugotech ×2.
- **Build:** migration 046 `leads(id, name, company, email, domain, source, first_seen_at, event_id,
  status, note)`; Review gets a **Lead** action beside Assign / Ignore (`actions.queue_resolve` → `lead`);
  website-form senders (`onboarding@resend.dev` subject "New inquiry") auto-file as leads;
  Today gets a "New leads — N" line above LinkedIn; Pipeline gets a **Leads** view (status: new / contacted /
  call / proposal / client / no). Drafts only; he writes the reply. The growth context-pack slice reads it.

### 4.2 Today "At a glance" to the top, as bars
- Move the four figures out of the "More" expander to a strip under the health line: revenue MTD vs $35k,
  applied this week vs target, comments today of 10, buyer conversations of 5. `sections/today.js` `atAGlance`
  + `metricCard` with `bar`.

### 4.3 Scan: cost and mail
- `upwork_scan` = $40.69 of $58.39 (30 d) and 157 of 696 weekly runs fail on `PER_RUN_MAX_USD = 0.50`
  (160 failures / 14 d; a run that dies after scoring but before carding wastes the spend). Options: a
  per-task cap of $0.75 for `upwork_scan` (config, reviewed), or `MCP_MAX_GETS_PER_RUN` 25 → 15 so a round fits.
- Mail: per his §1 answer, the per-scan digest (4–14/day) becomes one 11:00 digest; ★ ICP / invite / offer
  alerts stay immediate. `scan_pipeline.py` digest send → a daily job (or fold into the AM brief).
- `upwork_funnel_topup` hourly: 136 empty + **36 failed** of 173 — find the exit-1 (likely the refresh lock
  path) and move to every 3 h. `upwork_conversation_codes` 2-hourly: 79 of 86 empty → 2×/day.

### 4.4 Retire
- `launchctl bootout gui/$(id -u)/io.etherwise.v3.june15-credit-validation` and `…shadow-diff`; move the plists
  to `launchd/retired/`; drop from `config.SOURCES` if listed. Per §1: the v2 `com.etherwise.dashboard-server`
  (:5050) and `com.etherwise.reconcile-airtable` (writes the same Airtable base `crm_mirror` writes hourly).
- `catch_up` every 30 min: keep, but see 2.7(b).
- Global `~/…/CLAUDE.md` (the hostloop one): the "Scheduled Tasks" table lists 7 Cowork tasks as live; all 11 are
  disabled, v3 launchd replaced them; rate is $51/hr not $32.50. Fix the doc.

---

## 5. Tab-by-tab verdicts (from the walk)

| Tab | Views | Verdict |
|---|---|---|
| Today | 1 | fix (2.1, 2.5, 2.6, 3.1) + add (4.2) |
| Pipeline | 7 | keep; Agents → Flow; collapse v2 skip names; **A/B test starving** (157 drafts/arm, 9+8 sent in 3 wks) |
| Upwork | 3 | fix Inbox (2.1); cut Health (3.2); Jobs is good |
| LinkedIn | 1 | keep |
| Money | 5 | keep (optional: Statements → an Import box inside Ledger) |
| Clients | 3 | keep; drop Airtable status column (3.5); Needs a look = the idle-contract home |
| Reports | 2 | keep; Sachin Kadam gap line per §1 |
| Knowledge | 4 | merge → 2 (3.3) |
| Activity | 4 | keep; merge Health + Every system (3.4) |
| Review | 3 | fix intake (2.4) + Leads (4.1) |

## 6. Acceptance for tonight

- Today ≤ ~15 cards: People waiting 3 · Needs a decision ~5 · Promised 4 · Housekeeping 0 · LinkedIn 4.
- AM brief commitments section ≤ 30 lines, grouped by client, top 5 shown, footer = the filtered count.
- Review badge ≤ 25 with zero payment notices, Upwork notification mails or vendor sequences in it; Hannah
  Melotto is a lead, not a queue item.
- Anomalies open < 100; next-run self-resolve pinned by a test.
- Upwork › Health gone; Knowledge 2 views; Activity one health table; Clients first load < 2 s.
- Suite green (the 5 known clock-dependent failures excepted), `bin/doctor` 0 FAIL, cockpit reloaded.
