# The sales engine: Upwork, from the job to the paying client and back

*Design and state, 9 Oct 2026 (overnight build). Owner: Abhijeet. Built in commits `548588b` and `7810efc`.*

## What he asked for

> "Now Upwork is our sales engine ... starting with the scanner, then people we apply to, then the people who opened our proposal, then the people who replied, then the people who actually hired us and then how much they are paying ... each of these metrics can then reflect back to the scanner ... and the proposal writer ... why only five people hired us and what are the reasons that 25 people did not move ahead ... reactivation of the client ... different agents doing different things but at the end there is this cohesive knowledge base." (9 Oct, before bed)

His answers the same night: a **new top-level Pipeline tab** that the Funnel and the scanner charts fold into, nothing parallel; an **AI reading of conversations up to $25** for the first pass, **Globaltize never** (their contract, section 6.4); **win-back drafts, at most five a week**, he sends them; **no fixed connects cap** (300 to 400 is the ceiling per bid until bigger clients come in; the $150 a week is tracked, not enforced).

## The stages

| Stage | What counts | Where it comes from |
|---|---|---|
| Found | a job the scanner scored hot (19+; 28+ is the top band) | `scored_jobs` |
| Applied | a cold proposal sent (invitations accepted are counted beside) | `proposal_outcomes` |
| Opened | Upwork records the open, or the client wrote back | `proposal_views`, `proposal_outcomes.replied` |
| Wrote back | the client opened a conversation | `proposal_outcomes.room_id` |
| Talking | messages both ways, a call, an offer | `messages`, `offers` |
| Hired | Upwork marks the proposal hired, or the client in its conversation first paid after it | `proposal_outcomes.hired` + the ledger |
| Paying now | paid in the last 45 days | the ledger, every rail, one name per client (the registry) |
| Stopped | no payment for 45+ days | the ledger |
| Came back | paid again after a gap of 60+ days | the ledger |

Every figure on the tab opens the rows behind it.

## The agents

Each agent is one or more scheduled jobs. They all read and write one knowledge base (the stores in the last section), which is what makes the whole thing cohesive: the Writer reads what the Tracker and the Conversation reader stored; the Win-back agent reads what the Account keeper and the reader stored; the Analyst reads everything.

| Agent | Does | Jobs | State on 9 Oct |
|---|---|---|---|
| Scout | reads every new job, drops rule-breakers, scores the rest | `upwork_scan` | blocked: Upwork sign-in expired 8 Oct 16:15 |
| Writer | drafts a proposal per hot job; every other draft carries the running test's version (T1) | `upwork_scan` | blocked: sign-in, and AI credit |
| Tracker | keeps proposals, opens, replies, offers and contracts current | `upwork_sync`, `upwork_pulse`, `upwork_inbox` | blocked: Upwork API sign-in expired 8 Oct 16:45 |
| Conversation reader | reads each conversation once (again when it moves): how far it got, why it stopped, the next step | `upwork_conversation_codes` (new) | built, not switched on (AI credit) |
| Account keeper | records every payment and names the client | `upwork_financials`, `finance_*` | Upwork ledger blocked by the sign-in; Wise, Stripe, mail fine |
| Win-back | ranks past clients worth a check-in, drafts up to five a week | `upwork_reactivation` (new) | ranking live on the tab; drafter built, not switched on |
| Analyst | calls the tests, writes the reading, sends the progress email | `upwork_progress` (new), `upwork_outcomes` | progress email built, not switched on |

## The tab (Pipeline)

- **Flow:** the period's numbers from hot job to hire, then four "where they drop, and what we know about why" cards (hot job → proposal, sent → opened, opened → wrote back, wrote back → hired), what happens after the hire, the running test, and the agents. Anything stopping the machine is at the top with its fix.
- **Scanner:** the scanner's own charts (moved from Upwork › Analysis).
- **Proposals:** sent → opened → wrote back → hired, broken down by any dimension (moved from Upwork › Funnel).
- **Conversations:** every proposal the client answered: messages each way, who wrote last, a call, what Upwork says the client did, and the reader's note.
- **After the hire:** who is paying now, who stopped, who came back; the win-back list with every point explained, and a copy button on each draft.
- **Tests:** version against version, the verdict in words, the log.
- **Agents:** each agent's jobs, last runs, what it made this week, what blocks it; the knowledge base's stores and how fresh they are.

The Upwork tab keeps Inbox, Jobs and Health. An old link to its Funnel or Analysis lands on the Pipeline tab.

## Rules the build follows

- Read-only on Upwork; drafts only; nothing is sent to a client by the system.
- The reader cuts passwords, keys, tokens, emails and phone numbers out of the text before it leaves the machine, and never reads Globaltize (by the conversation's client, the room, the proposal's client, or the room name).
- Money on the tab and in the email is shown only on a GREEN finance doctor.
- Every number says where it came from; "—" means not measured, never zero.

## What the numbers already say about the scanner (2026 cold proposals)

| Split | Wrote back | Hired |
|---|---|---|
| Scanner score 28+ (43 sent) | 16% | 4 |
| Scanner score 19–27 (55 sent) | 5% | 1 |
| Job title names his tool (130) | 19% | 8 |
| No tool in the title (97) | 5% | 0 |
| Job had over 100 proposals (25) | 4% | 0 |
| Job had 51–100 proposals (49) | 20% | 7 |
| Applied after the first day (31) | 3% | 0 |

The scanner flags 430 to 830 hot jobs a month; he applies to 6 to 30 of them (1–4%). A proposal for the scanner (his decision, config): make "hot" the top band only (28+), name-the-tool as a strong plus, drop jobs past 100 proposals or older than 24 hours. That would cut the hot list to roughly 60 to 200 a month, close to the 80 to 100 a month T1 plans to send.

## What is next

1. **His three fixes:** the two Upwork sign-ins (`bin/upwork-mcp-auth`, `bin/upwork-api-auth`) and AI credit (console.anthropic.com, Billing).
2. **The pull** (`scripts/upwork_pull_all.py`): contracts with no date cap, the messages of the 97 conversations not stored, the proposals since 8 Oct and a fresh read of the last 60 days. Then the proposal-page opens in Chrome with him awake.
3. **Switch on** the reader, the win-back drafter and the progress email (`docs/upwork/proposed-config-2026-10-09.md`).
4. **The scanner calibration** above, as a config proposal.
5. **Turn the pull into scheduled jobs** (the daily funnel refresh, the views rail) so the tab never goes stale.
