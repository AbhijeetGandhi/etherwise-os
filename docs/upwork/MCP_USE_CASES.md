# The Upwork MCP tools — what each one can do for us, and in what order

**Written:** 2026-08-26 06:00 IST (the Upwork chat, overnight, on Abhijeet's 05:20: "look at all
the upwork other mcp tools that are available and let's start building a use case around it —
for example if we can access transactions data then the import feature that we have will no
longer be needed, it can completely be replaced with mcp").
**Method:** the tool reference (`get_tool_help`, documentation only — no account data was
read), the shapes already captured with his approval on 25 Aug (`tests/fixtures/mcp/`), and
the live DB read-only. Every real-account call this plan needs is listed at the end and waits
for his OK, one call at a time, as agreed.
**Transport:** `modules/upwork/feeds/mcp_client.py` — OAuth 2.1, and a READ_TOOLS allowlist in
code (`list_accounts, find_jobs, get_job_posting, get_freelancer_dashboard,
get_freelancer_financials, list_freelancer_proposals, list_contracts, list_offers, get_profile,
get_account, get_rate_insights, find_saved_jobs, get_tool_help, get_messages`). Everything
below is a read. Nothing here sends, submits, accepts, boosts or spends — law 1 stands.

## 0. Status — 2026-08-27 01:30 IST

Written at 06:00 on the 26th as a plan. **Every use case in it is now built** —
A, C, D and E/G/H on the 26th, B and F and the hiring side on the 27th. This
section is the only place to trust for state; the sections themselves are the
original plan, stamped where reality differed.

| | use case | state | rail |
|---|---|---|---|
| A | transactions + weekly summary | **BUILT** | `upwork_financials`, daily 06:05 (CSV kept until 2026-09-09 as the tie-out) |
| B | connects | **LIVE from 2026-08-27** — the MCP owns `connects_history` | **the last Chrome rail is retired**: `io.etherwise.v3.upwork-connects` moved to `launchd/retired/`, `upwork_connects` left RERUN_JOBS, and the `connects_rail` health line now watches `upwork_financials` |
| C | proposals (0.2) | **BUILT** | `mcp_proposals_pass`, hourly inside `upwork_sync`; win rate went from a frozen 46% to an honest 11.0% |
| D | contracts + milestones (0.3) | **BUILT** | `mcp_contracts_pass` daily; milestones in `mcp_inflight` |
| E | offers (0.5) | **BUILT** | `upwork_pulse`, :05/:20/:35/:50 |
| F | messages (0.4) | **BUILT 2026-08-27** | inside `upwork_pulse` — the dashboard's own room preview is the change detector, so it costs nothing; the full room list is paged once an IST hour |
| G | dashboard heartbeat | **BUILT** | `upwork_pulse` — the dashboard is read first and the detail reads only fire on a non-zero count |
| H | timesheets | **BUILT** | `mcp_inflight` inside `upwork_financials`; Money shows uninvoiced hours |
| — | the hiring side (Phase 3) | **BUILT 2026-08-27** | `upwork_hiring`, daily 07:05, migration 034 — his OWN postings, who applied, and what the market pays |

Three things the live reads corrected that no amount of reading `get_tool_help`
would have:

* transactions are dated by **invoice** date; the CSV carries the **payment**
  date, ~10 days later. The weekly tie-out is a per-client subset-sum over a
  28-day lag window, not a date-window sum.
* `list_offers` returns `hasMore: true` on an **empty** page. A pager that loops
  on `hasMore` never terminates (`tests/fixtures/mcp/offers_pending_empty.json`).
* `timesheets` takes **no cursor**, so the window is the paging unit: a 14-day
  window returned the oldest 10 of 24 entries and made `unbilled` read $0 on a
  week holding $565.83. `fetch_timesheets` now halves a short window down to a
  single day.
* on a story, `from_self` is TRUE on his own and **ABSENT** on inbound — never
  False — and Upwork injects SYSTEM stories stamped `from_self: true`, which
  counted as outbound would tell him he replied when he did not.
* on the hiring side, **shortlisted and read are ANNOTATIONS, not statuses**: a
  shortlisted applicant is still `status.status == "Created"`. Reading it off
  the status would report six people as nobody.

## 1. The short answer

Yes: **transactions can come from the MCP, and the CSV import can retire from daily use** —
kept only as the yearly audit (the running-balance tie-out the API cannot give). The same
tool also carries the connects history, which retires the **last Chrome rail** we still run
(`upwork_connects`, 05:35). Below that, four more tools each close a hole the briefs already
name (0.2 proposals, 0.3 win/loss, 0.4 messages, 0.5 offers), and two give the Money tab
things it never had (hours logged but not yet invoiced; milestone money funded but not
released).

## 2. What the transactions picture is today (live DB, read-only, 05:55 IST)

| source | profile | rows | first | last |
|---|---|---|---|---|
| `Upwork` (GraphQL sync, hourly) | Personal | 1,731 | 2023-05-28 | **2026-06-10** |
| `Upwork` (GraphQL sync, v3) | personal | 17 | 2026-06-12 | **2026-06-24** |
| `Upwork` (GraphQL sync) | Agency | 72 | 2025-07-16 | **2026-06-03** |
| `Upwork-CSV` (his imports) | Personal | 71 | 2026-06-29 | 2026-08-26 |
| `Upwork-CSV` (his imports) | Agency | 83 | 2025-07-16 | 2026-09-02 (a scheduled row) |

Reading: the hourly `upwork_sync` still *queries* transactions ("updated 22" every run) but has
not inserted a personal row since 24 June — every row it sees is already there under the same
`upwork:<recordId>` key because Abhijeet imported the CSV first — and the **agency accounting
entity fails on every run** (`transactions[agency] partial errors: Authorization failed`,
metric `errors_agency: 1`, hourly since the token cutover). So since the end of June the
ledger's Upwork side has been fed by hand: all 72 of August's Upwork rows are CSV. That is the
import feature he wants gone.

Why the CSV survived the API in the first place (from the importer's own docstring and doctor
#8): the GraphQL rows for AP/AR types carry **no amount** and quarantine; the CSV has every
amount and a running balance that ties out to the cent. Any replacement has to answer both:
amounts on every row, and a tie-out.

## 3. Use case A — transactions and the weekly summary (replaces the CSV import)

**Status:** **BUILT** — `modules/upwork/feeds/mcp_financials.py`, daily 06:05. Doctor #10 twins and the weekly tie-out both live. The GraphQL transactions pass is retired.

> **STATUS 2026-08-26 evening — BUILT AND SCHEDULED.** Captures 1, 2, 3 and 5 below were run
> at ~16:20 IST on his OK ("the three for transactions + connects") and pinned in
> `tests/fixtures/mcp/`; `modules/upwork/feeds/mcp_financials.py` reads both orgs daily at
> 06:05 IST (task `upwork_financials`, SOURCES key `upwork_financials`, migration 030
> `upwork_mcp_rows`, doctor check "upwork mcp twins"). What the captures settled:
> the agency answers (org `1910033523997712724`, role string **`FL_AGENCY`**); every row has
> an amount; the row IS the GraphQL `transactionHistoryRow` (recordId, type, accountingSubtype,
> description, transactionAmount{rawValue signed}, transactionCreationDate, paymentStatus on
> payments, assignmentCompanyName); `total_count` is honest and the `from_date` filter loose.
> Two things the plan did not know: the MCP dates rows by **invoice date** where the CSV carries
> the **payment date** (~10 days later for hourly) — MCP-inserted rows are invoice-dated (his
> accrual month), CSV-inserted rows keep their payment date, nothing is fabricated; and
> withdrawal legs were held the way the CSV importer held them — **decided 16:50: invoice
> dates stay; withdrawals go in as transfers** (the importer's hold is lifted too). **Step 3
> is built (17:55, his OK on call 4):** the rail stores Upwork's `summary` for every
> completed Mon–Sun week per org (`upwork_mcp_summaries`, migration 031) and bin/doctor's
> "upwork weekly tie-out" adds up what the rail read for the same days — billed / fee / tax
> per client, exact in minor units. Step 4 (the Money import card audit-only) waits for two
> clean weeks of the twins check. The GraphQL sync's transactions pass is retired (17:55).
> The connects half (use case B) runs in **shadow** inside the same rail until
> `MCP_CONNECTS_SINCE` is set.

**Tool:** `get_freelancer_financials` — actions `transactions`, `summary`, `connects_balance`,
`timesheets`; every action takes `org_uid` (from `list_accounts`), so the **agency is a second
org, not a failing ACE** — the one thing the GraphQL path cannot do today.

`transactions`: `from_date`/`to_date` (max 53 weeks per call), `ace_ids` optional (auto-
resolved from the org), `limit` up to 200 most recent, `total_count` says how many exist.
`summary`: per-client earned / billed / fee / tax and per-job billings for a period — "the
Weekly financial summary"; it reports INVOICED billings only (hours logged but not yet
invoiced are absent — that is what `timesheets` is for) and says `earnings_available: false`
with a note rather than an empty report.

**Build (`modules/upwork/feeds/mcp_financials.py`, task `upwork_financials`, launchd daily
06:05 + the hourly sync's slot once trusted):**

1. one `transactions` call per org (personal, agency), window = the last 14 days (the same
   look-back the GraphQL sync uses), `limit` 200; a `total_count` above the limit pages by
   narrowing the window (never silently drops the tail — a metric says when it had to);
2. rows into `transactions` through the existing `upsert_transaction` path with
   `source='Upwork-MCP'`, canonical key `upwork:<recordId>` — the SAME key the CSV and the
   GraphQL rows use, so nothing double-counts and a CSV row already present is an update, not
   a twin. Amounts to integer minor via `finance.money.to_minor` (Decimal), never float. A row
   without an amount quarantines exactly as today (never a fabricated 0);
3. one `summary` call per org per completed week → a new doctor check, **"Upwork ledger ties
   out to Upwork's own weekly summary"**: our sum of that week's earnings / fees per client vs
   the summary's `earned` / `fee`. This is the API's replacement for the CSV running-balance
   tie-out — weaker (weekly, per client) but continuous and automatic;
4. the Money tab's provisional overlay ("Upwork CSV import is behind") becomes "Upwork
   financials last read …" from `SOURCES`; the import card keeps the Upwork kinds but labels
   them **audit only**.

**Retire the CSV** after two consecutive weeks in which (a) every CSV row of the week already
existed as an MCP row, and (b) the tie-out passed. The CSV importer code stays (it is the
yearly audit and the recovery path).

**Unknowns that need one fixture each (his OK):** the field names on an MCP transaction row —
does it carry `recordId` (the canonical key), `accountingSubtype`, `fullyPaidDate`,
`paymentStatus` the way the GraphQL row does; whether AP/AR rows have amounts here; whether the
agency org answers at all through this token. Two calls: `transactions` on each org, August
window, limit 200. One `summary` call (personal, August) for the tie-out shape.

## 4. Use case B — connects (retires the last Chrome rail)

**Status:** **LIVE from 2026-08-27, and the last Chrome rail is retired.** Until then the rows were read and written to `upwork_mcp_rows`; `upwork_connects` (Chrome) owned the table until he named the day. Evidence at the flip: 9 twins and one "untwinned" row that was only that day's Availability Badge charge the Chrome rail (05:35) had not run for yet. Usage rows carry NO label: the kind is inferred from DEBIT/REFUND_CREDIT + the instant + `jobPost.jobId`.

> **STATUS 2026-08-26 evening — IN SHADOW inside `upwork_financials`.** Capture 5 answered the
> unknowns: the usage row has **no label** (`transactionType` DEBIT | REFUND_CREDIT, an
> instant, `amount.rawValue`, `jobPost{jobId '~02…', jobTitle}` or null); the event time is an
> **instant** and the Chrome rail's `event_date` is its **UTC day** (checked on the 23–24 Aug
> rows). The kind is read from the shape (badge = DEBIT −1 without a job; two debits on one
> job at one instant = the proposal and its boost; a refund on a job with a boost on record =
> boost_refund) and compared with the Chrome rail's rows every morning — the run's
> `connects.twins`/`untwinned` says whether the reading agrees. The day he sets
> `MCP_CONNECTS_SINCE` the MCP writes from that day on and the Chrome plist goes to
> `launchd/retired/` the same day (law 2). Paged (`hasNextPage`), 10 rows per read.

**Tool:** the same `get_freelancer_financials`, action `connects_balance` — "current balance
breakdown (free, paid, rollover) and recent connects transactions with job titles", `limit`
1–10. Also on `get_profile` (same action).

Today `upwork_connects` (Chrome, 05:35) scrapes the connects-history page into
`connects_history` (kind applied / boost / boost_refund / refund / purchase / renewal / badge /
other, `job_id` from the row's link, `job_title`, `connects_delta`) — the **only source of the
"applied" stage** on the Jobs flow and of the connects strip. It is the last Chrome rail
standing after the flip, and the one Phase 0.2 was told to "decide the replacement of".

**Build (`feeds/mcp_connects.py`, inside the hourly scan or its own :35 task):** one
`connects_balance` call, limit 10, hourly → the ten newest usage rows mapped onto
`connects_history` with the same kinds (dedup on date + delta + label + job, as today) plus a
`connects_balance` snapshot row for the strip. Ten an hour covers any day he has had: the
busiest day in the table is 17 events (13 Aug), the next 14 — and those are whole days, read
hourly. A metric says when a call came back with ten rows all new (the signal to read again
sooner). The Chrome rail retires the way the scanner did:
plist to `launchd/retired/`, `SOURCES["connects_rail"]` repointed, catch-up excluded. **Law 2
(no mixed provenance)** — the MCP rows carry `source='mcp'`; the day the switch flips, the
Chrome rail stops, never both.

**Unknown (one fixture):** the usage row's shape — is the job id present (it drives the
"applied" join to `scored_jobs`), and is the event time a date or an instant.

## 5. Use case C — proposals (Phase 0.2, de-risked on 25 Aug)

**Status:** **BUILT** — `mcp_proposals.py`, hourly. The status lists are JOB-level buckets, not the proposal's own status; only Upwork's own status field is allowed to disagree with a terminal verdict (`verdict_with_source`), which is what stopped the hourly false `terminal_disagreement`.

> **STATUS 2026-08-26 18:25 — BUILT** (`modules/upwork/feeds/mcp_proposals.py`, inside the
> hourly `upwork_sync`; his 17:55 "use case C next"). One `list` per status per round,
> newest-modified first to a stored cursor (7 calls a quiet hour; the first round pages the
> backlog); decision 4's map writes the verdicts, never flips a terminal one. The tool's
> `Accepted` = SUBMITTED. First live round on his word.

**Tool:** `list_freelancer_proposals` — `list` (status: Accepted = submitted, Offered, Hired,
Activated = invitation accepted, Pending, Declined, Withdrawn, Archived; cursor pagination),
`invitations` (received, with `jobPosting.id`), `get`, `get_room`.

The proposals table has been frozen since 9 June (`vendorProposals` scope-blocked on the
GraphQL token; only rooms refresh a proposal's status). The fixture shows the `list` node is
exactly the `vendorProposals` shape `sync_state._vp_node_to_proposal` already parses, and
`marketplaceJobPosting.id` is the one job-id space — so 0.2 is a paging loop over eight
statuses into the existing upsert. Two things it gives the tab immediately: **"Applied" from
Upwork itself** (a proposal with status Accepted and its `createdDateTime`) instead of the
connects page, and **invitations as a third source** for the Jobs flow's "came to us" line.
There is no per-proposal *viewed* flag (checked on 25 Aug) — the Viewed stage stays undrawn.

## 6. Use case D — contracts, milestones, end reasons (Phase 0.3, and the TCC question)

**Status:** **BUILT** — `mcp_contracts.py` (daily) and the milestone half of `mcp_inflight.py`. `list_contracts search` is refused by the agency org; the personal org lists agency contracts too. On a milestone, `state` is the authority — the wire reports `fundedAmount` == the amount even on a NotFunded one.

> **STATUS 2026-08-26 19:28 — contracts BUILT** (`modules/upwork/feeds/mcp_contracts.py`, once
> per IST day inside the hourly `upwork_sync`; his 19:18 "0.3 — contracts through the MCP").
> The agency org REFUSES `list_contracts search` ("you don't have access to this data"); the
> personal org lists every contract he holds, the agency's included — four pages, 34 active
> (fixture `contracts_search_active.json`). **The TCC question: ONE contract, 44099615, 15 Jul
> 2026.** ACTIVE + PAUSED in full, CLOSED narrowed to the last 45 days; new contracts inserted
> with what the search carries, existing ones keep the room pass's type / rate / job and gain
> the status; the agency's contracts are marked from the ledger's own evidence. Milestones and
> end reasons (`get`, one read per contract) are not read yet — ◆.

**Tools:** `list_contracts` — `search` (statuses ACTIVE / CLOSED / PAUSED, date filters, limit
10 + offset), `list` (by ids), `get` (full contract: `offer.milestones` with dueDateTime,
state, depositAmount, fundedAmount, paid, submissionCount), `get_original_proposal`,
`timesheets`, `end_reasons`; `list_milestones` — the milestone states of a fixed-price contract
with the submitted deliverables.

Today `contracts` is filled from ROOM_LIST — **one contract id per room**, which is why "a
second TCC contract this month" (his 05:20 message) is invisible if it exists: the Brandi
Jordan room points at contract 44099615 (started 15 Jul) and nothing else. A daily
`list_contracts search` per org (ACTIVE + the last 30 days of CLOSED) makes the contracts table
complete: every contract, its start, its client, its offer id — and `end_reasons` gives 0.3 its
Lost-vs-Ended evidence without a model guess. `get` on each ACTIVE fixed-price contract gives
the Money tab a line it has never had: **funded but not yet submitted / submitted and awaiting
approval** — money that exists but is not in any ledger row.

**Fixture (his OK):** `list_contracts search` on the agency org, statuses ACTIVE, limit 10 —
this is also the direct answer to "did TCC start a second contract this month".

## 7. Use case E — offers (Phase 0.5)

**Status:** **BUILT** — `mcp_pulse.py`, one page only, never paged on `hasMore`. Nothing was waiting on the first live read.

**Tool:** `list_offers` — `list` / `list_mine` (newest first, states for the freelancer role:
awaiting_your_acceptance, contract_started, awaiting_client_approval, expired, denied, draft,
withdrawn, canceled), `get`, `get_attachments` (the documents a client attached, links that
expire in ~15 min).

Offers are never ingested today (finding 2; the `offers` table's last write is 11 June). One
`list` call per org per hour → `offers` rows → a Today card and an email **within the hour of
an offer arriving** (the brief's 0.5), and the Jobs flow's "Offer received" tile stops reading
"—". The 25 Aug fixture also showed the two August contracts are *not* marketplace offers on
the personal org — so the agency org has to be polled too, or they are direct hires; the
fixture on the agency org settles it.

## 8. Use case F — messages (Phase 0.4)

**Status:** **BUILT 2026-08-27** — `mcp_messages.py`, inside the quarter-hour pulse.
`get_freelancer_dashboard`'s own answer already carries `data.messages.rooms[]`
(the newest rooms with their last-message instants), so noticing that a reply
landed costs NO extra call and only a room that moved is read; the full room
list is paged once an IST hour because the preview is short. The ids are the
SAME space the GraphQL rail writes (`room_`/`story_`), so an MCP row updates a
GraphQL row and never twins it. Live: the mirror went from nine hours behind
(newest story 08:18) to current.

**Tool:** `get_messages` — `list_rooms` (most-recently-active first, `unread_only`,
`room_type` ALL / GROUP / ONE_ON_ONE / INTERVIEW, cursor), `find_room` by context
(contract / offer / proposal), `list_messages` (newest first, cursor), `get_message`.

The inbox poll reads rooms through the GraphQL `roomList` today, twice a day plus "Check
Upwork now". `list_rooms unread_only=true` is the cheap "did anything change" call the poll
lacks (one call, no room fan-out), and `list_messages` carries real instants — the reply
detection law 5 wants ("a reply arrived → the card clears itself") without the deep-fetch per
room. The promise extractor (`modules/upwork/promises.py`, today) and the room links
(`room_link.py`) read the `messages` table, so they follow whichever transport fills it.

## 9. Use case G — the dashboard (one call for "what is new")

**Status:** **BUILT** — `mcp_pulse.py`, :05/:20/:35/:50. A quiet quarter-hour costs exactly one call.

**Tool:** `get_freelancer_dashboard` action `check` — invitations, offers, messages, matching
jobs, contract updates and the connects balance **in one call**, no params.

This is the natural heartbeat for the Health tab's "Check Upwork now" and for a 15-minute
poller that decides whether the expensive polls need to run at all: nothing new → skip (the
sweep's empty-skip pattern). It is also the cheapest possible early warning for an offer or an
invitation between the hourly polls.

## 10. Use case H — timesheets (Money: hours worked, not yet invoiced)

**Status:** **BUILT** — `mcp_inflight.py`. Money shows "$565.84 of hours worked since 24 Aug, not invoiced yet", per contract. It is an ESTIMATE to the cent (per-entry rounding), from his own entries — Upwork invoices the week on Sunday.

**Tool:** `get_freelancer_financials` action `timesheets` (also `list_contracts timesheets`) —
hours logged per contract for a window, `contract_ids` filter.

The weekly summary counts invoiced billings only; the hours logged this week sit nowhere in
v3 until Upwork invoices them on Monday. One call per week per org → a Money line "this week
so far: N h logged on hourly contracts ≈ $X at their rates, invoiced next Monday" — the gap
between worked and billed, which today only exists in his head.

## 11. Smaller uses

- `get_rate_insights` — the market's hourly range for a job (from its text) → one scoring
  input: our bid vs the going rate on every hot job, and a "this client's budget is under the
  market" flag the drafter can name. Read-only, no account data.
- `find_saved_jobs` — his own bookmarks as a shortlist source: a saved job the feed scored low
  is a disagreement worth a card; a saved job never applied to is a reminder.
- `get_profile` / `get_account` — the profile's `totalJobs` and rate on the Health tab, and
  the **$32.50 vs $50 rate drift** the briefs kept flagging. RESOLVED 2026-08-26: the
  profile reads $51.50/hr, `PROFILE_RATE_USD` is 51.50 and `BID_RATE_USD` 50.00, and the
  skills/ files were corrected. The profile's own description still claims "69 contracts
  / $100K+" — free text he wrote, not an Upwork metric; Upwork computes 124 jobs and
  $127,017.71. That is a Today card (`profile_stale_claim:2026-08`).
- `get_job_posting` / `list_client_proposals` / `get_client_dashboard` are the client role.
  **BUILT 2026-08-27** as `upwork_hiring` (daily 07:05, migration 034) on his "In — the
  applicant pull": his own postings with the whole funnel (the list call carries it), the
  people who applied to the open ones, and `get_rate_insights` for what the market pays.
  Live: the LinkedIn Ghostwriter post had 9 applicants, 4 never opened, 6 shortlisted,
  0 offered; the market is $15-35/hr and the bids ran $8-75.

## 12. Order, cost, and what needs his OK

**Order (each shippable alone):** A transactions + summary tie-out → B connects (retire the
Chrome rail) → C proposals (0.2) → D contracts + milestones (0.3 evidence, the TCC answer) →
E offers (0.5) → G dashboard heartbeat → F messages (0.4) → H timesheets → 11.

**Calls per day at the proposed cadences:** A 2 + 2/week · B 24 · C 8 (statuses) · D 2 + a
`get` per active fixed-price contract · E 2 · G 96 (every 15 min) · F 2–4 · H 2/week —
roughly 150 reads a day against a server that paced `find_jobs` at ~12/min; the hourly scan's
15 queries already run at 5 s spacing and finish inside four minutes, so this is not a rate
concern, and every call is one `runs` row with a health line.

**Real-account calls this plan needs before building, one at a time, on his OK
(the chat's `mcp__Upwork__*` connector reaches his real account; each result goes to
`tests/fixtures/mcp/` with free text truncated, as on 25 Aug):**

1. ~~`list_accounts`~~ — **DONE 26 Aug 16:20** → `tests/fixtures/mcp/accounts.json`.
2. ~~`get_freelancer_financials transactions`, personal~~ — **DONE** →
   `financials_transactions_personal.json` (12 of 32 rows kept).
3. ~~the same on the agency org~~ — **DONE** → `financials_transactions_agency.json` (9 of 27);
   the agency answers.
4. ~~`get_freelancer_financials summary`, personal, `2026-08-01`…`2026-08-26`~~ — **DONE
   26 Aug 17:57** → `financials_summary_personal.json` (verbatim: by_client billed / earned /
   fee / tax + jobs, totals, `earnings_available`; amounts are decimal strings, by invoice
   date).
5. ~~`get_freelancer_financials connects_balance`, `limit 10`~~ — **DONE** →
   `connects_balance.json` (verbatim; no labels, see §4).
6. ~~`list_contracts search`, agency org~~ — **REFUSED by the agency org 26 Aug 19:20**
   ("you don't have access to this data"); the PERSONAL org paged instead on his OK (4 reads,
   34 active) → `contracts_search_active.json`. The TCC answer: one contract.
7. `get_freelancer_dashboard check` — the heartbeat's shape.
8. `get_messages list_rooms`, `unread_only true`, `limit 10` — for 0.4, later.

What can be built with **no new call**: the `mcp_financials` and `mcp_connects` modules
against the documented parameters, with the row-mapping left as one function each that the
fixture then pins (the same way `feeds/mcp_search.py` was built before the flip); the doctor
check skeleton for the tie-out; the `SOURCES` entries and plists (not loaded). That is the
next step of this chat once the tab redesign mockups are out.
