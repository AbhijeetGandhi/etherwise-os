# etherwise-v3-linkedin-brief.md — LinkedIn: the engagement tracker in the cockpit

**Owner:** Abhijeet · **Written:** 2026-10-28 · **Status:** decided, building.
Build conventions come from `etherwise-v3/CLAUDE.md` and `UI_BUILD_BRIEF.md`. This file is
the domain spec. Strategy context: `business/social/CONTEXT.md` (goal, gates, rules) and
`business/social/LOG.md` (decisions).

---

## 0. What this is for

LinkedIn is the second acquisition channel (bet B2). Its job is **suitable paying
clients**, not followers (founder decision, 9 Sep). From 22 Oct the work has been tracked
in the Airtable base **LinkedIn Engagement** (`applkzewYyLbRmcss`). The social sessions
keep it up to date by hand.

This brings that base into the cockpit, so the channel is measured the way Upwork is:
- a **LinkedIn tab** with the numbers;
- a **block on Today** listing only the things that need him;
- a **growth slice** in the context pack. That slice is gate ② for the Growth agent in
  `business/AGENT-ARCHITECTURE.md`: "a lead surface exists to advise from".

## 1. Decisions (Abhijeet, 2026-10-28)

| Question | Answer |
|---|---|
| Where it lives | **New LinkedIn tab + Today block** |
| How fresh | **Every 2h, 12 PM–4 AM IST** (plus a Refresh button) |
| Comment pace target | **10 comments per day** (supersedes 10/week of 11 Oct and the ~20/batch of 26 Oct) |
| Missing tracker fields | **Add both**: attribution on Messages, a weekly Profile snapshots table |

Fields added to the base the same day:
- **Messages:** `Buyer conversation` (checkbox, the thing the day-90 gate counts), `Became`
  (Lead / Proposal sent / Client), `Attribution` (LinkedIn-originated / -influenced), and
  `CRM name` (the name as written in `config/clients.yaml`).
- **New table `Profile snapshots`:** Week, Taken on, Followers, Connections, Profile viewers
  (90 days), Post impressions (7 days), Search appearances (7 days), SSI score, Notes.

## 2. Laws

1. **Read-only on Airtable.** The tracker is owned by the social sessions. v3 has one
   verb, GET. The sync goes through a read-only accessor with no create, update or delete
   methods, and a test pins that.
2. **Nothing is sent.** No route on the tab sends, posts, comments or DMs. Abhijeet
   publishes from his own account (CONTEXT §4).
3. **Airtable holds the state; Today only derives from it.** A Today item clears when the
   tracker changes, for example when a verdict moves from Comment to Posted. There are no
   card keys and no Done buttons, so none of the card-identity machinery applies
   (`nudges.followup_key_forms` is not touched).
4. **`—` means not measured. `0` means a real zero, and the page says why.** An empty
   Messages table is "no DMs sent yet". It is not a 0% reply rate.
5. **History is ours.** Airtable keeps only the latest value. The sync saves a daily
   snapshot of every row, so trends survive later edits.
6. **Schema drift is loud.** If a field the readers depend on disappears, the run files a
   `linkedin_schema_drift` anomaly. Source health then shows the rail as DEGRADED instead
   of the tab quietly showing zeros.

## 3. Data model (migration 045)

- `li_records(table_key, record_id, fields_json, created_time, first_seen_at, updated_at, gone_at)`
  - Primary key: `(table_key, record_id)`.
  - Generic on purpose. The social agent adds fields as the process evolves, and each new
    field must not need a migration. Readers pick fields by name.
  - A row Airtable stops returning gets `gone_at`; it is never deleted.
- `li_snapshots(day, table_key, record_id, fields_json)`
  - Primary key: `(day, table_key, record_id)`.
  - The last sync of an IST day wins.

Tables are configured by ID in `config.LINKEDIN_TABLES`: people, comments, messages,
candidates, posts, profile. Table IDs survive renames.

## 4. What the tab shows (in this order, most important first)

1. **Results.**
   - Buyer conversations against the day-90 target of 5. Day 0 is 9 Oct 2026, Aastha's
     first working session, so day 90 is 7 Jan 2027.
   - DMs and connection requests sent, and how many were replied to.
   - Calls booked, leads, proposals and clients, with originated and influenced kept
     separate.
2. **Pace.**
   - Comments today, and over the last 7 days, against 10/day.
   - A 14-day daily bar.
   - Posts this week against Aastha's scope of 3–4 a week.
3. **Commenting funnel.**
   - Posts found, then each verdict (Comment / Brainstorm / Revisit / Posted / Skipped),
     then comments posted, then authors who liked or replied.
   - Median hours between a post going up and our comment, and impressions on our comments.
4. **Relationships.**
   - People by stage: Watching → Engaged → Warm → Ready to DM → DM sent → In conversation
     → Call booked → Opportunity → Client.
   - Split by segment and by fit with who we sell to.
   - Who is due a touch, from the base's own `Next action` formula, which encodes Aastha's
     DM rule.
5. **Our posts.**
   - Each post's impressions at 24h and at 7 days, engagement rate, buyer comments,
     conversations started and followers gained.
6. **Profile.**
   - Followers, profile viewers and post impressions over time, from Profile snapshots.

Every figure drills to its rows (traceability contract), with a link to the post or profile
on LinkedIn.

## 5. Today block: "LinkedIn — needs you"

| Item | Source |
|---|---|
| Brainstorm posts waiting on his answer | Post candidates, Verdict = Brainstorm (shows `Question for Abhijeet`) |
| Drafts ready to post | Post candidates, Verdict = Comment |
| People ready for a DM / due a DM follow-up / in conversation | People, `Next action` |

All items are capped to the last 7 days by `Found on`; older ones are counted, not listed.
LinkedIn posts go stale within days. The block is wrapped so that a failed read never
breaks Today.

## 6. Schedule, health, rerun

- Job: `io.etherwise.v3.linkedin-sync` runs `python -m modules.linkedin.sync`.
  - It runs at :05 every two hours, 12:05 → 04:05 IST.
  - The overnight gap is 8h, so `max_age_hours = 10`.
- Source: `linkedin_sync`. Not critical; `backs: "linkedin"`; `catch_up: True`.
  - It is listed in `actions.RERUN_JOBS`, which is what the tab's Refresh button uses.
- Module flag: `SHADOW_MODE["linkedin"] = False`. There is nothing outward to shadow.

## 7. Acceptance

- Every count on the tab equals a count in Airtable at the same moment.
  - The 28 Oct baseline: 56 people, 8 comments, 0 messages, 29 candidates, 1 post.
- Restarting the Mac keeps the job (installed through `launchd/install.sh`).
- The full test suite passes; the cockpit is reloaded (`bin/cockpit reload`).
