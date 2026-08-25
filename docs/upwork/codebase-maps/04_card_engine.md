# Card engine / Chief-of-Staff — how cards are minted, identified, surfaced, actioned and cleared (snapshot 2026-08-24)

All paths under `etherwise-v3/`. Line numbers are from this snapshot.

## 0. The one-paragraph model

There are **two card producers and two decision stores**, reconciled at render time:

1. **`nudges` table** (M4 engine, `modules/chief_of_staff/nudges.py`) — written by `rules.py` via `sweep.py` every 2h, by Opus correlations, by Quick Capture. Has `state`, `dedup_key`, `payload_json.evidence_ts`, a closure overlay.
2. **Follow-up cards derived live from `drafts.sent_status='pending' JOIN threads`** in `modules/cockpit/data.py:1800-1884` — NOT stored as nudges; they exist as long as the draft is pending. `drafts.sent_status` is flipped only by `modules/upwork/poll_inbox.py` (10:15 / 19:15 IST).

Decisions live in **`nudge_state`** (cockpit overlay, `003_cockpit.sql`) AND **`nudges.state`** (engine); both are read and merged in `data.today()` (`data.py:1743-1756`), forwarded across re-keyings via **`card_subjects`** (`decisions.py`). "Auto-clear" for rule-owned kinds = the rule stops emitting the key → `sweep.py:313-321` decays it. For the drafts-derived follow-up card, "auto-clear" = poll_inbox re-bucketing the thread → `drafts.sent_status='stale'` (`poll_inbox.py:346-354`). **Nothing between polls observes an Upwork reply.**

---

## 1. Card model

### 1.1 Tables

| Table | Migration | Columns (meaning) |
|---|---|---|
| `nudges` | `core/migrations/006_cos.sql:11-37`; +`018_card_engine.sql:15-20` | `id`; `source` (emitter: `sales|knowledge|system|sweep|capture|commitment`, L13); `dept` (brief bucket `sales|knowledge|system`, L14); `kind` (canonical vocabulary, §1.2); `title`; `action`; `due` (ISO, nullable); `urgency` (stored at write; re-computed live at read); `money_at_stake`, `score` (stake inputs); `dedup_key` (identity, §1.3); `state` CHECK `open|snoozed|done|dismissed|decayed` (L23-24); `snooze_until`; `note`; `payload_json`; `created_at`, `refreshed_at` (last re-fire), `updated_at` (all written **IST-naive** by `nudges._iso()` L227-228 — NOT the SQLite UTC default; see `ts_key` L107-135); `decayed_at`. 018 adds `client`, `intent_hash` (`card:<client>:<intent_hash>` canonical identity, computed but never used as a lookup key — `write_nudge` L308 stores it, nothing selects on it), `closure_state` (`NULL|possibly_resolved|auto_closed|confirmed|rejected`, 018 L9-13), `closure_evidence`, `closure_confidence`, `closure_source`. Unique index **only on ACTIVE rows**: `idx_nudges_active_dedup ON nudges(dedup_key) WHERE state IN ('open','snoozed')` (006 L34-35) — done/dismissed/decayed history rows for the same key accumulate (Rocky Choy had 10, `nudges.py:348-350`). |
| `nudge_state` | `003_cockpit.sql:5-13` | `item_key` PK (`followup:<key>`, `nudge:<dedup_key>`, bare `<dedup_key>` since `actions.cos_nudge` L252-258, `review:thread:<id>` L622-629); `state` `done|snoozed|dismissed`; `snooze_until`; `note`; `source` (`cockpit`, `carry`, `engine-sync`); `updated_at` **SQLite UTC** (`datetime('now')`). 006 L6 says it is "retired" — it is not; it is the cockpit's write target for every follow-up card Done (`actions.apply_nudge` L417-425) and the write-through mirror of every nudge Done (L250-258). |
| `card_subjects` | `024_card_subjects.sql:26-34` | `card_key` (bare dedup_key), `subject_kind` (`'thread'` only today; column reserved for commitment/job-keyed cards, L23-24), `subject_id` (`threads.id`), `first_seen_at`. PK on all three; additive, never rewritten (`decisions.record` L93-108). |
| `commitments` | `006_cos.sql:41-55`; +`020_commitment_review.sql:8` | `who` (`us|client`), `client_id`, `text`, `due`, `source_cite` (mandatory provenance: `fathom/<id> @ quote`, `upwork/<thread>`, `note/<id>`), `dedup_key` (`commitment:<sha256[:16] of source_ref|text>` `commitments.py:60-63`; `commitment:note:<id>` from capture L187), `state` `open|done|dropped`, `review_state` `NULL|stale` (stale → not a card, `rules.py:249`). |
| `notes` | `006_cos.sql:59-69`; +`025_capture_visibility.sql:39-46` | `text`, `source` (`terminal|cockpit|email`), `classified` (`nudge|commitment|kb_note|idea`), `classified_ref`, `state` `new|classified`, `claimed_at` (lease), `attempts`, `last_error`, `resolved_at`, `resolved_by` (`card|filed`). |
| `sweep_state` | `006_cos.sql:74-80` | singleton: `candidate_hash`, `last_swept_at`, `last_opus_at`, `last_metrics_json` — empty-skip bookkeeping. |
| `threads` | `002_sales_crm.sql:104-116`; +`005:13` `client_id` | see §5. |
| `messages`, `drafts`, `offers` | `002:118-139`, `002:72-80` | see §5. |
| `events` | `017_events.sql:15-37`; +`022:11` `direction` | Jarvis rail ledger (email/slack/fathom/loom). `evidence.client_signals` reads `source, direction, ts, client_id` (L155-171). |

### 1.2 Kind vocabulary (every literal)

Canonical, derived from `config.COS_URGENCY_BASE` (`core/config.py:919-930`; `COS_CARD_KINDS = frozenset(COS_URGENCY_BASE)` L975):

| kind | base urgency | TTL (`COS_DECAY_TTL_DAYS` L949-960) | one-shot? (`COS_DONE_TERMINAL_KINDS` L939-940) | producer |
|---|---|---|---|---|
| `commitment_due` | 100 | None (never) | yes | `rules.rule_commitments_due` |
| `hotlead_no_loom` | 80 | 3 | yes | `rules.rule_hotlead_no_loom` |
| `deadline` | 70 | 0 (clears past `due`) | yes | **nobody** — reserved for the finance calendar (design §3), no producer in code |
| `owed_reply` | 60 | 7 | no (evidence-gated) | `rules.rule_owed_replies` |
| `escalate_client` | 55 | 7 | no | Opus correlations (`sweep.py:359-384`) + default for any unknown kind (`COS_KIND_DEFAULT` L984) |
| `lost_pattern` | 50 | 14 | no | `rules.rule_lost_pattern` |
| `won_no_outcome` | 40 | 14 | yes | `rules.rule_won_no_outcome` |
| `system` | 35 | 7 | no | `rules.rule_system` |
| `stale_dossier` | 20 | 30 | no | `rules.rule_stale_dossier` |
| `capture` | 30 | 14 | yes | `capture._apply` L179-185, `capture.resolve` L401-405 |

Non-canonical literals still in the code: `'followup'` (006 L15 comment; and the **render** kind of the drafts-derived Today entry, `data.py:2008` — never stored in `nudges`); `escalate_commitment_cluster`, `escalate_pipeline` (killed by `019_kill_cluster_escalations.sql`; `sweep._is_cluster_card` L28-30 still drops any kind ending `cluster` or `== 'escalate_pipeline'` at write time). Anything else Opus invents is canonicalised at the choke point `nudges.canonical_kind` L31-61 (whole-word containment, longest wins; else by `source` via `COS_KIND_DEFAULT_BY_SOURCE` L978-983; original kept as `payload.kind_raw`). Measured 2026-08-20: 44 of 54 stored kinds were inventions (`scripts/canonicalize_card_kinds.py:16-19`).

Rule-owned kinds reconciled by the sweep: `sweep.RULE_KINDS` L41-42 = `owed_reply, hotlead_no_loom, won_no_outcome, lost_pattern, stale_dossier, commitment_due, system`. Closure-pass kinds: `nudges.CLOSEABLE_KINDS` L28 = `commitment_due, owed_reply, won_no_outcome`. Client-facing (provenance law): `data._CLIENT_FACING_KINDS` L53-54 = `owed_reply, commitment_due, won_no_outcome, hotlead_no_loom`.

### 1.3 Identity — dedup_key derivation

| key form | producer | line |
|---|---|---|
| `followup:c:<client_id>` / `followup:p:<slug(room_name first comma-part)>` / `followup:<thread_id>` | `nudges.followup_key` | `nudges.py:141-165` (one card per COUNTERPARTY, not per room; attributed → client form) |
| `hotlead_no_loom:<scored_jobs.id>` | rules | L165 |
| `won_outcome:<proposal_id>` | rules | L187 |
| `lost_pattern:<category>` | rules | L209 |
| `stale_dossier:<client_id>` | rules | L231 |
| `commitment:<commitments.id>` | rules | L260 |
| `system:run:<runs.id>`, `system:spend:<YYYY-MM-DD>` | rules | L289, L302 |
| `note:<notes.id>` | capture | L184, L405 |
| `escalate_client:<client_id>` (asked for; free text in practice) | Opus | `sweep.OPUS_SYSTEM` L157-160; merged by `covers` containment `collapse_target` L97-114 |

Cockpit render identity `data._card_key` L57-67: `followup:*` and `nudge:followup:*` collapse to `card:thread:<rest>` so the drafts card and its `owed_reply` nudge are one card; everything else `card:<item_key>`.

**What `card_subjects` adds** (`decisions.py:1-59` rationale): the follow-up key is derived from MUTABLE attribution; each attribution improvement renamed the card and orphaned the Done (room→person 08-12, person→client 08-13 — Rocky Choy / Dhanya Sangolli "for the third time, after five Dones"). Fix: record the `threads.id` set a follow-up key stands for at mint (`nudges.write_nudge` L317 → `decisions.record`) and at decision (`actions._capture_subject` L382-398 → `decisions.capture` L113-128, union of caller ids + `cover()` + payload thread_ids). Readers: `decisions.aliases` L219-243 (used by `nudges._latest_actioned` L196-218 so a re-keyed re-mint sees the old Done), `decisions.forward_map` L204-216 (used by `data.today` L1773-1779 to re-file decisions under today's key). Backfill for pre-table decisions derives from `nudges.followup_key_forms` L168-187 (inverse of `followup_key`; cannot recover merges/renames — `cover()` L173-187). Namespace gate: only `followup:` keys forward (`is_followup_key` L81-82); `review:thread:<id>` never does.

**Identity bugs fixed, in order:** (1) room-keyed cards → counterparty key (`nudges.py:146-149`, migrated by `scripts/today_repair_migrate.py` step 2); (2) Done written only to `nudge_state` while brief/sweep read `nudges` → write-through both ways (`actions.py:426-437`, `250-258`; backfill `scripts/today_repair_engine_sync.py`); (3) recurring `owed_reply` re-minted every sweep after Done → evidence-gated suppression (`nudges.py:343-374`); (4) naive string compare of `'T'` vs `' '` timestamps and IST-naive vs UTC 5h30m window → `ts_key(naive=...)` L107-135; (5) re-keying orphaning Dones → `card_subjects` + `decisions.py` (and `scripts/today_carry_decisions.py` one-time carry preserving the ORIGINAL decision timestamp, L26-32); (6) invented kinds escaping every lifecycle table → `canonical_kind` at `write_nudge` (`scripts/canonicalize_card_kinds.py` catch-up); (7) escalation ladders → `covers` + containment merge + reconcile (`scripts/today_repair_escalations.py`); (8) `nudges.intent_hash`/title hygiene (no relative dates/context counts in stored titles, `nudges.py:63-99`; `bin/cos-migrate-cards`).

### 1.4 States and terminal semantics

- `nudges.state`: `open` → `snoozed` (needs `snooze_until`; `active_nudges` L494-495 wakes it when `snooze_until <= now`) → `done|dismissed` (human) / `decayed` (system: `clear()` L443-445 = condition vanished; `decay_expired()` L450-475 = TTL from `refreshed_at`, or past `due` for TTL 0). `set_state` L421-440 by `dedup_key` touches only ACTIVE rows (so `bin/nudges done <key>` on a card the engine has no active row for reports `0 updated`); by `nudge_id` it can set ANY state including `open` (re-activation is possible by id only).
- Terminal rows are history; a new active row for the same key may be inserted later. Whether it is: §3.
- `closure_state` overlay (never a state flip): `auto_closed|confirmed` → hidden at render (`data.py:2145-2147`), `possibly_resolved` → chip (L2148-2151), `rejected` → normal, no re-chip; survives the roll-forward re-mint because `write_nudge`'s refresh UPDATE (L322-327) does not touch closure columns — but a **new** row after decay has closure NULL again.
- `drafts.sent_status`: `pending` → `superseded` (new draft for the thread, `poll_inbox.py:331-333`) / `stale` (thread no longer in an actionable bucket, L346-354). Nothing ever writes `sent` — a draft he actually sends in Upwork is only detected via the next poll's bucket flip.
- `commitments.state open|done|dropped`; `review_state stale` removes it from cards without closing.

---

## 2. Rules → cards

### 2.1 `modules/chief_of_staff/rules.py` — every rule

| rule (line) | observes | reads | mints | condition / notes |
|---|---|---|---|---|
| `rule_owed_replies` (41-143) | pending Upwork draft + counterparty silent | `drafts d JOIN threads t` where `d.sent_status='pending' AND substr(t.latest_message_dt,1,10) <= now-COS_FOLLOWUP_IDLE_DAYS` (L64-71, **cheap prefilter on ANY-direction newest row, over-selects only**); then `evidence.counterparty(conn, thread_ids, client_id, thread_latest)` L80-82 and `evidence.engagement` L83-84 (`messages`, `events`, `sources`, `contracts`) | kind `owed_reply`, source/dept `sales`, key `followup_key(...)`, title `Follow up: <topic>` or `Client comms: <topic>` when a live contract exists (L111-121), `payload {idle_days, thread_id, thread_ids, client_id, engagement, evidence, evidence_ts=ev["ts"]}` L137-142 | Dropped when `age_days(ev["waiting_since"]) < COS_FOLLOWUP_IDLE_DAYS` (=1, `config:997`) L95-97. **Not dropped when `ev["answered_since"]` is True** — our own newer Upwork/Slack reply does not clear the card; only the pending draft going non-pending does. Grouped per counterparty; the newest thread's row is the head. |
| `rule_hotlead_no_loom` (146-168) | `scored_jobs.loom_flag=1 AND clickup_task_id IS NULL AND status IN ('Scored','Drafted') AND first_scored_at >= now-7d` | `scored_jobs` | `hotlead_no_loom:<id>`, stake `fixed_budget` or `hourly_max*160` | clears when tasked/skipped/aged out. Today hides this kind entirely (`data.py:2096-2097` "that IS the paused backlog"). |
| `rule_won_no_outcome` (171-188) | `proposals.status='Won'` with empty `status_reason`, modified within 30d | `proposals` | `won_outcome:<id>`, client `client_company` | clears when reason captured. |
| `rule_lost_pattern` (191-211) | ≥`COS_LOST_PATTERN_MIN`(3) proposals in `COS_LOST_STATUSES` per `scored_jobs.category` | `proposals JOIN scored_jobs` | `lost_pattern:<category>`, count only in payload | never self-clears in practice (losses do not un-happen); Done = 2h snooze (§3). |
| `rule_stale_dossier` (216-233) | `sources.status='ingested' AND client_id NOT NULL` grouped by client | `sources` | `stale_dossier:<client_id>` | clears when extraction runs. |
| `rule_commitments_due` (236-262) | `commitments` `state='open' AND who='us' AND due NOT NULL AND review_state!='stale' AND due within [now-30d, now+2d]` | `commitments` | `commitment_due`, source `commitment`, dept `knowledge`, key `commitment:<id>`, `due` set, payload `cite` | clears on done/dropped/stale/aged past 30d overdue. Undated commitments never card (capture.py:259-269 shows them under "Captured"). |
| `rule_system` (267-304) | failed `runs` in last 24h with no later completed/skipped_empty run of the same task (L278-284); today's `claude_usage` spend ≥ 0.8×`DAILY_HARD_LIMIT_USD` | `runs`, `claude_usage` | `system:run:<id>`, `system:spend:<date>` | clears when a later run succeeds / date rolls. |
| `ALL_RULES` L307-309, `gather` L312-317 | | | | flat list, deterministic order, no model calls. |

**Upwork-related coverage today:** only `owed_reply` (from poll_inbox drafts) and `hotlead_no_loom` (scanner). There is **no** rule for offers, interviews, proposal-viewed, stale-thread-no-draft, drafts-ready-for-review-as-such. `offers` table exists (`002:72-80`) but `sync_state.sync` L423 reads `room.get("offers")` while `ROOM_LIST` selects only `offerIds` (`queries.py:61`) — the v3 sync never populates `offers`; rows there are v2 imports.

**Which v2 follow-up-monitor rules (`../docs/follow-up-monitor-rules.md`) survived** — they survived into `poll_inbox.py`, not `rules.py` (rules.py *consumes* poll_inbox's drafts): Step 1 direction by `sender_user_id == UPWORK_USER_ID` → `poll_inbox.py:99-100` (lowercase `outbound|inbound|system`); Step 2 awaiting-reply from the latest human non-empty story, 30d stale → `compute_awaiting` L131-150; Step 3 tiers → `compute_tier` L112-128 (**the `$100K whale → HOT` row is gone**); Step 4 hard-skips 1-6 → `classify_thread` L185-204 (rule 7 "system-only latest → healthy" not separately coded; rule 4 uses latest-story age, not "no Inbound in 14d"); Step 5 owed thresholds (HOT>6h/12h, WARM>24h, COLD>48h) **dropped** — `awaiting=='Abhijeet'` is `owed` immediately L197-199; Step 6 follow-up thresholds HOT 48 / WARM 72 / COLD 168 / UNKNOWN 72 → L34-35, L205-211 (COLD's "0 prior follow-ups" replaced by the generic ≥2 cap); Step 8 drafts → `draft_followup` L234-251, now also for `owed` ("reply to the client's last message"), <120 words; snooze → `_snoozed` L153-156 reads `threads.snooze_until` but **nothing in v3 writes it** (the v2 Airtable `pull_snooze` phase was not ported) — snooze is dead; cadence hourly → **2x daily** (`launchd/io.etherwise.v3.upwork-inbox.plist:25-32`, 10:15 + 19:15; BUILD_BRIEF.md:448 notes the divergence). The old v1 monitor's "2x daily" cadence is what v3 actually runs.

### 2.2 `sweep.py` — cadence, ordering, reconcile, auto-clear

- Cadence: launchd `io.etherwise.v3.cos-sweep.plist:22-30` — 12:30, 14:30 … 02:30, 04:30 IST (9 slots, `StartCalendarInterval`, `RunAtLoad=false`; missed windows are NOT queued — `bin/catch-up` only kicks SOURCES-registered jobs and `cos_sweep` is not in `config.SOURCES` L726-892, so a slept-through sweep is simply lost). `config.SWEEP_INTERVAL_HOURS=2` L164 is documentation only.
- Order inside `run_sweep` (L270-406): (0) `capture.classify_new` + `commitments.extract_new` as backstops L292-297; (1) `interrupts.check_hot_leads` L302-306 (independent of empty-skip); (2) `rules.gather` L308; (3) **write/refresh every candidate** `nudges.write_nudge` L310-311; (4) **reconcile**: every ACTIVE (`open|snoozed`) row whose `kind IN RULE_KINDS` and whose `dedup_key` is not in this run's candidate set → `nudges.clear` → `decayed` L313-321 (`metrics.cleared`); (5) TTL decay L323; (6) closure pass L329-334 (metered Haiku, before empty-skip); (7) empty-skip: hash of `(kind|dedup_key|due)` L177-181 unchanged AND no due boundary crossed (`_due_trip` L188-204) → `TaskSkip` L346-351 with zero Opus; (8) Opus correlate (≤30 items, L244-265), ≤3 correlations, cluster filter, `covers` validation against live keys, containment merge (L359-384); (9) escalations whose covered cards are all gone → decayed L389-397.

**Does an absent rule result CLEAR a card?** Yes, for `RULE_KINDS` — **auto-clear is `sweep.py:313-321`**: the key not being re-emitted on this sweep is the observed condition. No human Done is needed. Trace for an owed reply after HE replies on Upwork:
1. poll_inbox 10:15/19:15 inserts his story as `direction='outbound'` (L92-107) → `compute_awaiting` = `'Client'` (L149-150) → `classify_thread`: `<12h` or below tier threshold → `'healthy'` (L201-211) → `threads.bucket='healthy'` (L308-311) → pending draft marked `stale` (L346-354).
2. Today's drafts-derived card disappears at the next page load (`data.py:1804` reads `sent_status='pending'`).
3. Next `cos_sweep`: `rule_owed_replies` finds no pending draft → key absent → `nudges.clear` → `decayed` (L320-321). Done.
Also: `rule_owed_replies`' SQL prefilter drops any thread whose `threads.latest_message_dt` (any direction, incl. system) is within the last calendar day (L69), so the nudge decays on ANY new row — while the Today drafts card does not.

**Where a card can SURVIVE its clear condition (the Dhanya Sangolli class)** — plausible mechanisms, ranked by likelihood given the brief's own diagnosis ("reply-detection freshness"):

1. **Nothing observes Upwork replies except `poll_inbox`, which runs twice a day and is unmonitored.** The drafts-derived follow-up card clears ONLY via `poll_inbox.py:346-354`; `rules.rule_owed_replies` ONLY via the same draft flip. `upwork_inbox` is absent from `config.SOURCES` (L726-892) → no freshness verdict in doctor/brief, never kicked by `bin/catch-up` (`overdue_sources` L165-177 only consults SOURCES), and its plist is the pre-`install.sh` kind that "ceased to exist" after the Aug-11 reboot for other jobs (`launchd/install.sh:8-15`). A reply at 19:20 IST is invisible until 10:15 next day even when healthy; a dead poll = the card lives forever with no alarm.
2. **`evidence.answered_since` is computed but never used to clear.** `rules.py:95-97` drops a candidate only on `idle < 1 day`; `data.py:2043` raises the "possibly resolved" chip only when `ours_channel != "upwork"`. So his own Upwork reply, even when the poll HAS captured it as an outbound message, does not clear the card until the bucket/draft path fires — and if the bucket path returned `'followup'` (≥48/72h since his reply, L205-211) a NEW follow-up draft is minted and the card returns wearing the label **"reply owed"** (`data.py:2020` labels every non-client follow-up card "reply owed" regardless of `bucket`; `today.js:421` defaults the same) and the title `Follow up:` (`rules.py:119`). To the founder that reads as "the system never saw my reply".
3. **Direction mis-tagging is permanent.** `ROOM_STORIES_WITH_SENDERS` can error on system stories; the fallback `ROOM_STORIES` (L285-292) carries no `user`, so every story from that fetch is inserted with `sender_user_id=NULL, direction='system'` (L97-100). `upsert_message` is insert-only (L94-96), so the row is never corrected. A real reply stored as `system` is invisible to `compute_awaiting` (`sender_user_id IS NOT NULL`, L134), to `_human_ever`, to `evidence.upwork_signals` (`lower(direction)='inbound'|'outbound'`, L121-129). `evidence.py:31-32` measured 21 threads whose latest row is `system`. If HIS reply landed that way, awaiting stays `'Abhijeet'` → `owed` → draft stays pending → card survives; if HER reply did, the card would not come back after a Done (evidence too old).
4. **`compute_awaiting` returning `None` skips the bucket update entirely** (`poll_inbox.py:304-305 continue`): a thread with no human rows (all-system, or attachment-only/empty-text replies — `TRIM(COALESCE(message,''))!=''` L134) keeps whatever `bucket` it already had, so a stale `owed` bucket and its pending draft persist indefinitely.
5. **Decision-store divergence** is handled now (`data.py:1719-1756` merges both, `actions.py` writes through both ways; `_is_actioned` L70-125 lets `evidence_ts` decide alone for evidence-bearing kinds). Residual risk: a Done pressed on the drafts card BEFORE the engine has minted its nudge → `engine_rows=0` (normal, L440-446); the later mint is gated by `_latest_actioned` (L370-374) only if `payload.evidence_ts` is present — it is for `owed_reply`.
6. **Cadence stacking:** poll (≤15h) + sweep (2h) + `TaskSkip` when the candidate hash is unchanged (a cleared draft changes the hash, so this one is fine).

Which of these hit Dhanya on 2026-08-25 is not determinable from the snapshot (no `var/etherwise.db`, no logs). The brief's wording ("survived his reply", "reply-detection freshness") points at 1–2.

---

## 3. Decisions & human overrides

- `modules/jarvis/events.py:25-38` `is_human_decision(resolved_by)` = `resolved_by` starts with `cockpit-` or `hand` (`HUMAN_RESOLVER_PREFIXES` L18). It governs the **events ledger** (mapping queue re-sweeps must never re-open a human verdict) — it is **not consulted by the card engine**. The card engine's "who decided" is implicit: a `nudge_state` row (`source='cockpit'|'carry'|'engine-sync'`) or `nudges.state IN ('done','dismissed','snoozed')`.
- How Done/Skip is recorded: follow-up card → `POST /api/nudge` (`today.js:618-621`) → `actions.apply_nudge` L401-449: upsert `nudge_state` (UTC `datetime('now')`) + `decisions.capture` + write-through `nudges.set_state(dedup_key)` (0 rows is normal). Nudge card → `POST /api/cos-nudge` (`today.js:614-617`) → `actions.cos_nudge` L225-261: `nudges.set_state` + mirror into `nudge_state` under the **bare** dedup_key + `_capture_subject`. Terminal `bin/nudges done|snooze|dismiss <key>` → `set_state` only (no overlay, no subject). Snooze default 3 days (`DEFAULT_SNOOZE_DAYS` L373). Chip → `POST /api/card/closure` → `set_closure` on the newest engine row, else `apply_nudge(...,'done')` (L701-736).
- **Can a Done block a legitimate re-mint?**
  - One-shot kinds (`COS_DONE_TERMINAL_KINDS`): **yes, forever** — `write_nudge` L335-342 returns `suppressed-actioned` if ANY done/dismissed row exists for the key. Keys are per-entity (`commitment:<id>`, `hotlead_no_loom:<job>`, `note:<id>`), so this is intended; a Done on `hotlead_no_loom:<job>` can never re-fire even if the Loom flag changes.
  - `owed_reply` (recurring with `evidence_ts`): suppressed while `ts_key(evidence_ts) <= ts_key(latest actioned.updated_at, naive="ist")` across aliases (L368-374); a newer counterparty message re-mints (`created`). Render side mirrors it (`_is_actioned` L120-122). A snooze is absolute until `snooze_until` (L117-119).
  - Recurring kinds WITHOUT `evidence_ts` (`system`, `stale_dossier`, `lost_pattern`, `escalate_client`): `write_nudge` inserts a fresh open row on the next sweep, and `_is_actioned` L123-124 un-hides it because `created_at > done`. **Done on these is effectively a ≤2h snooze**, by design (docstring L100-106).
- Carry/migration scripts' role: `scripts/today_repair_migrate.py` (08-12: room→person keys in `nudge_state`, roster-based `link_threads`, listed legacy escalations); `scripts/today_repair_engine_sync.py` (nudge_state → nudges backfill, skipping keys where `threads.latest_message_dt` is newer than the Done); `scripts/today_repair_cross_channel.py` (report + optional nudges → nudge_state repair); `scripts/today_carry_decisions.py` (general carry across re-keyings via `decisions.forward_map`, original timestamp preserved, refuses to close a row whose `evidence_ts` is newer; writes `card_subjects` via `decisions.backfill`; idempotent — BUILD_BRIEF.md:64 "0 carried, re-run identical"); `scripts/today_repair_escalations.py` (evidence-quoted closes of the pre-`covers` ladder); `scripts/canonicalize_card_kinds.py` (kind catch-up, active rows only, keeps `kind_raw`); `bin/cos-migrate-cards` (title hygiene + `client`/`intent_hash` backfill).

---

## 4. Nudges / interrupts / brief

- **Queue surfaces:** `nudges.active_nudges` L489-502 (open + woken snoozed, ranked by live urgency `compute_urgency` L262-271 = base × 0.5^(age/7d) × stake × 1.5 if overdue); `grouped_queue` L512-539 collapses a kind with >`COS_QUEUE_GROUP_MIN`(5) items → served as `GET /api/nudges` (`data.nudges` L398-407) and the Today "more" panel (`today.js:645-660`, filters keys already shown); terminal `bin/nudges`. `top_n` L505-509 (3) feeds the brief. No dept/source filter is exposed in the cockpit; `active_nudges(dept=)` exists.
- **Today** (`data.today` L1686-2282): queue = drafts follow-up entries (ranked as `owed_reply` if bucket owed and not a client, else as `capture`, L2001-2006) + all active nudges except `hotlead_no_loom` (L2096), superseded-by-escalation (L2087-2100), `owed_reply` already shown as a follow-up (L2104-2105); sorted by urgency, deduped by `_card_key`, closure overlay applied, provenance law (client-facing card without `thread_url|source|job_url|cite|permalink` in `detail` is hidden + `card_no_provenance` anomaly, L2156-2165), clusters counted (L2168-2184), cut at `TODAY_QUEUE_TOPN`=12 (`config:207`, L2188-2190).
- **Interrupts** (`interrupts.py:31-74`): new `scored_jobs` with `score >= HOT_LEAD_THRESHOLD` (16, `config:166`), recent 7d, status Scored/Drafted, id past cursor `cos_hotlead_interrupt_id` → email via `notify.send_gmail` — but `SHADOW_MODE["chief_of_staff"]=True` (`config:138`) so it only writes `shadow_ledger` (L56-62). `INTERRUPT_POLICY=("hot_lead","system_failure")` L165. Called from the sweep L302-306. The scanner's own hot-lead digest is what actually emails today (config L130-134).
- **Brief** (`brief.py`): AM/PM per docstring L8-9 and `config.BRIEF_TIMES=(11:00, 20:00)` L163. `build_brief` L79-108: top-3 nudges, `by_dept` counts, commitments overdue/due-soon (2d), movement (wins/losses/new hot leads over 18h AM / 9h PM, L45-62), system line, source health. `narrate` L113-137 (Opus, skipped when all quiet). `write_brief` L235-248 renders HTML to `var/briefs/<date>-<slot>.html` — **draft only, never sent**; cockpit replays the newest file via `latest_brief` L251-258. Entry `brief.main()` L261-271 (`cos_brief_am|pm` task). **No `cos-brief-*` plist exists in this snapshot's `launchd/`** (actions.py:46 refers to one; `RERUN_JOBS` deliberately excludes it) — whether the brief is scheduled cannot be verified here.
- **How Upwork items appear in the brief:** only as `owed_reply` nudges in the top-3 (`_nudge_li` L142-146) or in the `sales: N open` dept count; the drafts-derived follow-up cards are NOT read by the brief; pipeline movement line is proposals/scored_jobs counts. There is no offers/interviews section.

---

## 5. Upwork thread data quality

**`threads` row** (`002:104-116` + `005:13`): `id` (room id), `room_name` (counterparty display, comma-joined), `topic`, `room_type` (e.g. INTERVIEW), `contract_id`, `proposal_id`, `offer_ids_json` (**never written** — `upsert_thread` L70-89 omits it though `ROOM_LIST` selects `offerIds`), `num_unread`, `num_unread_mentions` (not written), `last_visited_dt` (**not written** — `lastVisitedDateTime` is selected L35 but dropped), `latest_message_id`, `latest_message_dt` (= roomList `latestStory.createdDateTime`, ANY direction incl. system), `awaiting_reply_from` (`Client|Abhijeet|None`), `bucket` (`owed|unmatched-owed|followup|unmatched-followup|healthy|hard-skip|snoozed`), `tier` (`HOT|WARM|COLD|UNKNOWN`), `snooze_until` (dead), `client_id` (set by `clients.link_threads` L140-182: registry `upwork_rooms` claim first, else counterparty name match; never overwritten), `first_seen_at`, `updated_at`.

**`messages` row** (`002:118-128`): `id` (story id), `thread_id`, `message`, `created_dt` (Upwork `createdDateTime`, UTC `...Z` — **real Upwork timestamp exists**), `sender_user_id`, `sender_user_name`, `sender_org_id`/`sender_org_name` (**not written by v3 poll** L101-106 — only v2-imported rows have them, which starves `link_threads`' org-name match L166-169), `direction` (`outbound|inbound|system` lowercase from v3; **`Inbound|Outbound` mixed case from the v2 import** — `evidence.py:27-33`; `evidence` lowercases, `poll_inbox._consecutive_outbound` L172 compares `== "outbound"` case-sensitively so imported `Outbound` rows never count toward the ≥2 cap), `has_attachment`/`attachments_json` (not written), `ingested_at`.

**`drafts`** (`002:130-139`): `thread_id`, `draft_kind` = the bucket at generation, `tier`, `generated_at`, `body`, `word_count`, `rationale` (intent JSON), `sent_status`.

**How "reply detected" is computed today:** exclusively in `poll_inbox.poll`: deep-fetch a room only when roomList `latestStory.createdDateTime > MAX(messages.created_dt)` (string compare, L272-279), 50 newest stories (L283), insert new ids only; then `compute_awaiting` = sender of the newest non-empty human story (L131-150) and `classify_thread`. Direction of the latest human message IS the reply signal; there is no "reply since X" event, no `replied_at` column, no per-thread cursor. Cadence 10:15 / 19:15 IST. The CoS side then reads `messages` via `evidence.upwork_signals` L87-134 (`MAX(created_dt)` per `lower(direction)`, oldest per-thread newest-inbound for `waiting_since`) and `threads.latest_message_dt` as the prefilter/fallback.

**Exact columns the existing rules read**, i.e. what Phase 0.4 (`get_messages`) must write for auto-clear to work unchanged:
- `messages.id` (idempotency, L94), `thread_id`, `message` (non-empty for a human story to count, L134), `created_dt` (real Upwork UTC ISO with `Z`; `ts_key` handles `Z`/offset/space), `sender_user_id` (`compute_awaiting` L133-150, `_human_ever` L159-162, `link_threads` L168), `sender_user_name`, `direction` lowercase `inbound|outbound|system` (evidence L121-129; poll L168-172).
- `threads.latest_message_dt` (rule prefilter L69, evidence fallback L112-118 when no message rows, `today_repair_engine_sync`), `latest_message_id`, plus `room_name`/`topic`/`contract_id`/`proposal_id`/`client_id` for keying and labels.
- Then either keep running the bucket classification + stale-draft clearing (`poll_inbox.py:299-354`) over the MCP-written rows, or make the rules observe replies directly (§6). Per law §5.2 the MCP sync cannot co-feed `messages` alongside the GraphQL poll — poll_inbox's mirror half must retire in the same cutover (only its draft/bucket half could survive, reading MCP rows).
- Bonus columns worth writing since the schema already has them: `sender_org_name` (attribution), `has_attachment` (so an attachment-only reply is not ignored), `threads.last_visited_dt` (a "did he read it" signal).

---

## 6. Recipe: adding an Upwork card kind (house style)

Example kinds: `offer_pending` (offer awaiting response) and `draft_ready` (draft ready for review). Steps, minimal:

1. **Register the kind — `core/config.py` (not agent-writable: propose a diff in `var/proposed-diffs/`, per CLAUDE.md).** Add to `COS_URGENCY_BASE` L919-930 (this alone makes it canonical, L975) AND `COS_DECAY_TTL_DAYS` L949-960 (`tests/test_card_kinds.py:80-86` fails otherwise); add to `COS_DONE_TERMINAL_KINDS` L939 only if Done must be permanent (for an offer it should not — the observed state is the authority). Optionally `COS_KIND_DEFAULT_BY_SOURCE["upwork"]`.
2. **Write the data spine first** (brief: "nothing later is built until its spine row exists"): `offers` needs a writer — `list_offers` → `offers(id, title, state, type, job_id, client_id, client_name, thread_id, updated_at)` (`002:72-80`; note `offers.client_id` is Upwork's numeric company id, not a registry slug — `test_card_identity.py:320-325`). For drafts-ready the substrate already exists (`drafts.sent_status='pending'`).
3. **Add the rule in `rules.py`** next to the Sales rules, pure producer returning `write_nudge` kwargs, e.g. `rule_offers_pending(db_path, now)`: `SELECT ... FROM offers WHERE state IN (<pending statuses>)` → `{"source":"upwork","dept":"sales","kind":"offer_pending","title":f"Offer awaiting response: {title}","client":client_name,"action":"Accept/decline in Upwork.","due":expiry?,"dedup_key":f"offer:{id}","money_at_stake":..., "payload":{"offer_id":id,"thread_id":thread_id,"job_url":...,"cite":f"upwork/offer/{id}"}}`. Append to `ALL_RULES` L307-309. **Add the kind to `sweep.RULE_KINDS` L41-42** — that is what turns "the offer is no longer pending" into an automatic `decayed` on the next sweep (L313-321). The clear condition is OBSERVED because the rule re-reads `offers.state` every sweep.
4. **Key derivation:** use a stable primary key (`offer:<id>`, `draft:<drafts.id>` or `followup_key(...)` for anything counterparty-scoped). Only `followup:` keys get `card_subjects` forwarding (`decisions.is_followup_key` L81-82); an `offer:<id>` key never re-keys so it needs none. If a draft card should share identity with the follow-up/owed_reply card for the same person, use `nudges.followup_key` so `data._card_key` L57-67 collapses them.
5. **Evidence for Done-suppression:** if the kind is recurring (not terminal), put `payload["evidence_ts"]` = the timestamp of the observed fact (offer `updated_at`, draft `generated_at`) so a Done sticks until the fact changes (`write_nudge` L368-374, `_is_actioned` L120-122). Without it, Done lasts one sweep.
6. **Render wiring in `modules/cockpit/data.py`:** add to `_CLIENT_FACING_KINDS` L53-54 (provenance law applies) and make sure `payload.job_url` or `payload.cite` is set (the generic nudge branch reads exactly those, L2121) or the card is hidden with a `card_no_provenance` anomaly; add a `_CLUSTER` label L2168-2172 (else counted as "other"). Today will then show it automatically in the ranked queue (L2095-2126) — there is **no per-tab/source filter on cards today**; for the Upwork tab either add a `data.upwork()` reader that filters `active_nudges` by `source='upwork'` (or dedup_key prefix) and decide whether Today also shows them (law #5 "one screen"), reusing `POST /api/cos-nudge` for Done/snooze/dismiss and `/api/card/closure` for chips.
7. **Draft-ready specifically:** today's drafts-derived follow-up card already IS "draft ready for review"; its clear condition is `drafts.sent_status` flipping (`poll_inbox.py:331-354`). To make reply-arrival the observed clear without waiting for the bucket pass: in `rule_owed_replies` drop the candidate when `ev["answered_since"] and ev["ours_channel"] == "upwork"` (after L95-97), and in `data.today` hide (or at least chip) the drafts card on the same condition (L2043 currently excludes `upwork`); mark the draft `superseded/stale` from the MCP sync when an outbound newer than `generated_at` is observed.
8. **Tests to add:** `tests/test_chief_of_staff.py::TestRules` pattern (seed rows → rule → assert `dedup_key`, `kind`, payload) L265-330; `TestSweep::test_vanished_condition_clears` pattern L678-689 for the auto-clear (seed → sweep → flip the fact → sweep → `state=='decayed'`); `TestDedup::test_done_sticks_when_nothing_has_changed` L56-69 with your `evidence_ts`; `tests/test_jarvis_cards.py:87-100` shape for provenance; `tests/test_card_kinds.py` covers the config pairing automatically. Assert `"evidence_ts" in inspect.getsource(rule)` as L120-125 does.

---

## 7. Tests

Harness: `unittest`, temp dir + `db.migrate(path)` (`core/db.py:47`) per test; fixtures are raw `INSERT`s; model calls injected (`_opus=`, `_classify=`, `_narrate=`, `_extract=`, `FakeCtx.claude`) or `mock.patch.object(runner,"claude_call")`; `tests/test_cockpit.py::seed_today` L420-450 seeds threads/drafts/scored_jobs for Today; `datetime(2026,6,22,tzinfo=config.TZ)` style pinned clocks; `registry.load(path, force=True)` for a scratch `clients.yaml`.

Counts (test functions / classes): `test_card_identity.py` 23/6 · `test_card_kinds.py` 20/3 · `test_chief_of_staff.py` 89/15 · `test_cos_closure.py` 10/3 · `test_jarvis_cards.py` 5/1 · `test_catch_up.py` 21/2 · `test_cockpit.py` 292/47 (Today/decision tests at L1544-1720 and `TestCrossChannelEvidence` L1723-1883) · `test_m1b.py` 41/12 (`TestClassification` L415-493 pins poll_inbox buckets).

Pinned behaviours a change must keep: refire refreshes never duplicates; `owed_reply` Done sticks until newer `evidence_ts` (incl. IST/UTC gap and `T` vs space); one-shot Done is terminal; no-evidence recurring kinds re-mint; TTLs (7/3/None/0); vanished condition → decayed; empty-skip → zero Opus; escalations merge by containment ≥2 covers and retire when covered cards vanish; snooze never inherited/cut short; one card per counterparty with all drafts reachable; follow-up + owed_reply collapse to one Today card; active client relabelled "client comms", never hidden; Slack reply → chip not hide; Upwork system row ≠ client reply; unattributed thread says "only Upwork was checked"; Done on either card form hides the other; re-keying (attribute/merge/rename/room rename) never resurrects a Done; `review:thread:` never forwards; stored kind canonical, `kind_raw` kept; provenance-less client card hidden + anomaly once/day; no `smtplib/send_gmail/.send(` in CoS modules (`TestNoAutoSend` L1214-1223).

---

## 8. Gotchas

- `upwork_inbox` is the only writer of `threads/messages/drafts` and is **not in `config.SOURCES`** → no health line, no catch-up, no brief warning when it dies; the whole reply-detection chain hangs off it.
- Two timestamp conventions in the same column name: `nudges.updated_at` IST-naive vs `nudge_state.updated_at` UTC — always go through `nudges.ts_key(naive=...)`; a raw compare re-opens the 5h30m bug.
- `threads.latest_message_dt` counts our own and system rows — usable only as an over-selecting prefilter (rules L64-71) or last-resort fallback.
- `messages.direction` mixed case from the v2 import; `poll_inbox._consecutive_outbound` is case-sensitive.
- Fallback story fetch stores real messages as `direction='system'`, permanently (insert-only upsert).
- `offers` is never fed by v3 (`ROOM_LIST` selects `offerIds`; `sync_state` reads `offers`); `threads.offer_ids_json`, `last_visited_dt`, `sender_org_*`, `has_attachment` selected/available but never written.
- Today labels every non-client follow-up card "reply owed" (`data.py:2020`, `today.js:421`) even when `bucket='followup'` (we wrote last) — the card claims the wrong direction.
- `rule_owed_replies` never uses `answered_since`; the chip excludes Upwork replies (`data.py:2043`).
- `hotlead_no_loom` cards are minted every sweep but never shown on Today (L2096) — they only reach `/api/nudges`/brief.
- `deadline` kind exists in every config table with no producer; `nudges.intent_hash`/`client` are stored but unused as identity.
- `COS_DONE_TERMINAL_KINDS` suppression is forever and alias-blind (L336-342 checks the exact key only).
- `write_nudge` refresh overwrites title/action/payload wholesale (L322-327) — an escalation merged into another loses its text; closure columns survive refresh but not a decay→re-mint.
- Sweep reconcile also decays **snoozed** rule cards whose condition vanished; if it recurs the snooze is gone.
- `canonical_kind` whole-word containment: a new kind named e.g. `system_offer` would silently become `system`; an unregistered kind from a `source` not in `COS_KIND_DEFAULT_BY_SOURCE` becomes `escalate_client`.
- `config.py` is not agent-writable; every new kind is a proposed diff first.
- `SHADOW_MODE["chief_of_staff"]=True`: interrupts and briefs never send; `upwork` is live but poll_inbox's digest is a no-op (`require_live` only raises in shadow, `runner.py:104-108`).
- `threads.snooze_until` has no writer in v3 (the v2 Airtable snooze pull was not ported).
- Snapshot gaps: no `var/etherwise.db`/logs; `launchd/` lacks jarvis/kb/finance/brief plists and `bin/` lacks `note-classify`, `kb-ask`, `brief` — statements about live scheduling of briefs are unverifiable here.
