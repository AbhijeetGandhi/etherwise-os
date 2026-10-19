# etherwise-v3-hours-brief.md — Hours: ClickUp time into the cockpit

**For:** a Claude Code session in `etherwise-v3`
**Owner:** Abhijeet · **Written:** 2026-10-19 · **Status: BUILT 2026-10-19.** Kept as the
record of what was decided and what the build found, including where this brief was wrong.

### What changed once it met real data

- **§2's biggest claim was wrong.** `task_location` is null and responses cap at 50 *in the
  ClickUp MCP wrapper*, not in the API. The raw v2 endpoint takes `space_id`, `list_id` and
  `include_location_names=true` and returns list/folder/space ids and names. The sync never
  needed to resolve task → space itself.
- **`assignee` must be passed explicitly** — omit it and the API silently returns only the
  authenticated user's entries. There is no `any` on the raw endpoint.
- **Month chunks lost an entry.** ClickUp wants an entry *inside* the window, so one
  starting 23:52 on 30 Sept and ending after midnight belonged to no chunk: Hermon Sprint 22
  came to 177.06h instead of 178.31h. Windows now overlap by a day at each edge.
- **Rate limits are real** — a full backfill is ~130 reads and died on 429. The client now
  paces itself and honours `Retry-After`.
- **Dating sprints from a config anchor failed.** Hermon's folder opens in December 2025, so
  a 2026 anchor put Sprint 22 in 2027 where it matched nothing and showed 0.0h. Periods are
  now dated from the entries logged in them — 28 were wrong and were re-dated.
- **The drill drifted 0.05h** against its headline on 179 entries, because it summed
  per-row 2dp values instead of integer milliseconds.

### The acceptance numbers, settled

| Period | Expected | Result |
|---|---|---|
| Hermon Sprint 22 | 178.31 h | **reproduces exactly** — 108.06 sprint folder + 70.25 Vibe Coding |
| Horizons 43 | 26.05 h | **reproduces exactly as of 7 Oct 04:31 IST**; it is 32.55h now because 6.50h was logged afterwards |
| Horizons 44 | 33.47 h | **dropped** (his call) — no window, list, intersection or creation-cutoff produces it |

The third number is why the whole design changed: **hours keep arriving after a period
closes**, so totals are always live and every figure carries an as-of stamp (his decision,
2026-10-19). The earlier idea that internal tasks were being excluded was wrong — he
confirmed all of it is billable.

Build conventions come from `etherwise-v3/CLAUDE.md` and `UI_BUILD_BRIEF.md`. This file is
the domain spec. Read both; this one does not repeat them.

---

## 0. What this is for

Abhijeet bills clients by the hour and the hours live in ClickUp. Today, working out what
to invoice means opening ClickUp, picking the right sprint list, and adding up by eye —
which is why a sprint's number is only ever approximately known, and why a carried-over
task can quietly be billed twice.

This builds a read-only sync and an **Hours** view inside the cockpit's Clients tab, with
four parts: an **overview**, **each client's current period**, the **team's logging gaps**,
and a **report builder** that produces what actually goes to a client.

It does not replace ClickUp. ClickUp stays where time is logged; the cockpit is where it is
read, reconciled and turned into a number you can defend.

---

## 1. Laws (non-negotiable)

1. **READ-ONLY ON CLICKUP.** No task writes, no status changes, no time-entry edits, no
   comments. The sync has one verb: GET. A write path must not exist in the module, so a
   future edit cannot quietly add one — mirror the pattern in
   `modules/upwork/feeds/mcp_client.py`, where the transport refuses non-read tools *in
   code* rather than by convention.
2. **NOTHING IS SENT.** The report builder renders a report. It does not email it, post it
   to ClickUp, or push it anywhere. Drafts-only is a §0 law of this codebase and it applies
   here with no exception.
3. **THE OPS PLATFORM IS NOT TOUCHED.** `repos/etherwise-ops` is a separate system on a
   separate host with a founder-scope firewall. This work does not read it, write it or
   import from it.
4. **HOURS COUNT BY THE DATE THEY WERE LOGGED, NOT BY WHICH SPRINT THE TASK SITS IN.**
   This is the whole reason the feature exists. A task carried from Horizons 43 into
   Horizons 44 still holds its old time entries; counting by the task's current list would
   bill those hours a second time. The period a time entry belongs to is decided by the
   entry's own timestamp and nothing else. See §3 for the exact field and how to settle it.
5. **A NUMBER THAT GOES TO A CLIENT MUST DRILL TO ITS ENTRIES.** Same traceability contract
   as Money: every figure on the page opens the exact time entries behind it, and they sum
   to the figure to the minute.
6. **NO FABRICATED HOURS.** If an entry has no resolvable client, it appears in an
   unattributed bucket with its reason. It is never silently dropped and never assigned to
   a plausible client.

---

## 2. What ClickUp actually gives you (verified 2026-10-19 — do not re-derive)

Workspace `9016735299`. Probed live via the ClickUp MCP; these are measurements, not
assumptions, and two of them change the design:

- **`task_location` comes back `null` on time entries.** The time-entries response tells you
  the task id and name and nothing about where that task lives. **The sync must resolve
  task → list → folder → space itself** and cache that mapping. This is the single biggest
  piece of work in the sync and the easiest to under-estimate.
- **Responses cap at 50 entries.** The probe returned exactly 50 with a workspace-wide
  14-day window, which is a page limit, not a total. **Paginate, and assert you did** — a
  silent truncation here under-bills a client, which is the worst failure this feature can
  have. A sync that returns exactly 50, 100 or 150 entries should be treated as suspicious
  and logged as such.
- **`billable` is `false` on every entry observed.** ClickUp's own billable flag is not in
  use here. Do not build anything on it. Billability is decided by the rules in §4, not by
  that field.
- **`duration_ms` is authoritative**; the `duration` string ("2h 45m") is display. Sum the
  integer milliseconds and convert once at the edge, the same discipline the ledger uses
  for money.
- **`assignee` must be passed explicitly.** Omitting it returns only the authenticated
  user's entries. Pass `any` (or explicit ids) or you will silently sync one person.
- **Credentials:** `CLICKUP_API_KEY` in the credentials env file, read exactly as
  `scripts/clickup_push.py:token()` does. Never logged, never echoed.

### Spaces in scope (his decision: the eight with Sprint Folders)

| Space | id | Sprint folder |
|---|---|---|
| Horizons ABA | 90164727594 | 90166142809 |
| Hermon | 90166003526 | 90168014133 |
| Adelye | 90164737880 | 90166276702 |
| Globaltize | 90167875609 | 901610974588 |
| TCC | 90167673606 | 901610674888 |
| homestyling | 90166841994 | 90169370191 |
| Discover Data | 90167745788 | 901610779598 |
| Netfly | 90166867133 | 90169411810 |

**Excluded:** `Template Space`, `Client Projects` (24 folders, no sprint structure),
`Client Tasks Board`, `Auto-Tasks`, `Abhijeet's Space`. Scope lives in config as a list of
space ids with a comment saying why each is in or out — not hard-coded in a reader.

### Periods come from the sprint list name

Sprint lists are named with their window: `Horizons 44 (10/5 - 10/18)`,
`Sprint 22 (9/28 - 10/11)`. Parse the window from the name. Two traps, both real in this
data:

- **The year is absent.** Infer it from the list's position in the folder and the tasks
  inside it; a naive parse puts October 2025 sprints in 2026.
- **Numbering has gaps and cadence changes.** Horizons skips 3, 33, 34, 37 and switched from
  weekly to fortnightly around Horizons 19. Never infer a window by counting forward from
  another sprint — read each name.

If a list name does not parse, that is a visible error on the page naming the list, not a
skipped period.

---

## 3. The sync

New module `modules/hours/` plus a migration (next free number is `041_`). Nightly launchd
job, following the estate's existing conventions (`runner.run_task`, flock, metrics,
source-health registration so a stale sync shows up on the system view like every other rail).

**Store entries, not totals.** One row per ClickUp time entry — entry id, task id, task
name, resolved list / folder / space, client id, user id, `duration_ms`, the entry
timestamp, and when we first saw it. Totals are computed at read time. Storing
pre-aggregated period totals would make law 4 unverifiable and re-syncs destructive.

**Entry id is the primary key**, so a re-sync is idempotent. An entry that changes duration
updates in place; an entry that disappears from ClickUp is marked gone rather than deleted,
because a number that was reported to a client must stay explicable afterwards.

**Which timestamp decides the period (law 4).** Use the entry's `start`. Settle it against
the acceptance numbers in §6 rather than by argument: if `start` does not reproduce them,
try the entry's created-at, and whichever reproduces all three is correct — **write down in
the module docstring which one won and that the numbers chose it.** Convert epoch
milliseconds to IST before taking the date; a UTC date boundary moves an evening entry into
the wrong day and, at a period edge, the wrong invoice.

**Client resolution:** space → client id, via `config/clients.yaml`. The registry is the
only roster in this codebase — do not introduce a second map. A space with no registry match
is a visible gap, not a guess.

---

## 4. Billing rules

**Hermon's `Vibe Coding Projects`** (folder `90169726549`, ~70h in the Sprint 22 window,
outside the sprint lists) — **billable, folded into Hermon's period total** (his decision,
2026-10-19). It is client work that simply is not sprint-tracked, so the period total must
include it or Hermon is under-billed. Keep the folder attribution on the stored rows even
though the headline folds it in: when he is asked what a period consisted of, "70 of those
hours were Vibe Coding Projects" has to be answerable without re-opening ClickUp.

**"On us" items.** A team member can mark work as not-to-be-billed with a reason. Each such
item shows **the team's stated reason**, and **he decides each period whether it is actually
billed** — the mark is a recommendation from the person who did the work, never an automatic
write-off. An "On us" item with no reason is itself a gap worth showing.

**How an item is marked is an open mechanic** (§8) — ClickUp tags are the obvious candidate
since entries carry a `tags` array, but confirm the team can realistically apply them before
depending on it. Whatever the mechanic, the reason must survive into the cockpit.

---

## 5. The four views, inside the Clients tab

His decision (2026-10-19): **Hours lives inside Clients**, not as its own tab. The reader is
new; the surface is a section on the existing page. Follow `modules/cockpit/data.py`
conventions exactly — a reader per view, routes registered in `server.py:GET_ROUTES`, every
query param declared in `QUERY_PARAMS` (opt-in: an undeclared param is unreachable, by
design), and a rejected filter returns a 200 with an `error` envelope, never an empty list
that reads as "no data".

1. **Overview** — all clients, current period, hours to date, versus the same point in the
   previous period. Unattributed bucket visible with its count.
2. **Each client's period** — the period's total, the sprint it maps to, the split by person
   and by task, "On us" items awaiting his ruling, and the drill to raw entries.
3. **Team logging gaps** — **a weekday on which a person logged nothing** (his decision).
   Not a threshold on thin days: the failure worth catching is a day that never got recorded
   at all. **Exclude Jaap Meijvogel (`jaap@hermon.io`, id 101070656) — he is client-side, not
   team.** The seven Etherwise members are Abhijeet, Jay, Yogita, Shruti, Ankita, Sachin,
   Tanmay. Weekends are not gaps. A person with no entries all period is one line, not ten.
4. **Report builder** — see §6.

---

## 6. The report builder

**Default: total billed hours only.** Nothing else. Most clients get a number.

**Breakdowns are enabled per client, as they ask for them** — Alex (Horizons ABA) first.
Per-client report settings live in config beside the client, so turning one on is a reviewed
change and not a UI toggle that silently alters what a client receives.

Before it renders, the builder states the period, the number, and what is excluded —
including any "On us" items and his ruling on each. A report he cannot explain line by line
is not shippable.

Renders to screen and to a file. **It does not send** (law 2).

---

## 7. Acceptance — reproduce these before calling it done

From live data. All three must match; one matching is a coincidence, and these numbers are
also what settles the timestamp question in §3.

| Period | List id | Expected |
|---|---|---|
| Horizons 44 (10/5 – 10/18) | `1301230000004662` | **33.47 h** |
| Horizons 43 (9/21 – 10/4) | `1301230000001775` | **26.05 h** |
| Hermon Sprint 22 (9/28 – 10/11) | `1301230000002369` | **178.31 h** |

Hermon Sprint 22 **includes** the ~70h of Vibe Coding Projects per §4 — if your total lands
near 108, you have excluded the folder and the fold-in is not wired.

Also required:

- **Carry-over cannot double-count.** A task with entries in both Horizons 43 and 44 windows
  contributes each entry to exactly one period. Pin it with a test using a real carried-over
  task, not a synthetic one.
- **Re-running the sync changes no total.** Idempotency, asserted.
- **Pagination proven** — a window with more than 50 entries returns all of them, and the
  test fails if the count is suspiciously round.
- **The drill sums to the headline** to the minute, for all three periods above.
- Unit tests in `tests/`, house style: the docstring says what broke or what would break,
  not what the function does.

---

## 8. Open mechanics for the builder to decide and write down

Not blocking — resolve during the build, record the decision in the module docstring:

- **How "On us" is marked** in ClickUp (tags are the likely answer — entries carry a `tags`
  array — but only if the team will actually use them).
- **Sync cadence** — nightly is the assumption; if period-close day needs fresher numbers,
  say so and make it explicit rather than silently frequent.
- **A sprint window that has not started, or a client with no sprint folder** — what the
  overview shows. "No current period" is a fine answer; a blank cell is not.

---

## 9. Ask Abhijeet if

- The acceptance numbers do not reproduce under **either** timestamp interpretation. Do not
  adjust the rule to fit the numbers — that inverts the test. Report the gap with the entries
  that explain it.
- A space in scope has no `config/clients.yaml` entry. The registry is his file; propose the
  diff, do not write it.
- Hours appear for a client he is not billing. That is a commercial question, not a
  rounding one.
