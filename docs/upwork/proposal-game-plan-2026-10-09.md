# The proposal game: plan for your approval

*Prepared 9 Oct 2026 for Abhijeet. Status: his four answers are in (section 0). Built overnight 9 Oct: T1 in the draft writer (`0c42823`), the Pipeline tab with the tests view, the progress email (`d8d9e51`, `db5ec45`; the email is not switched on yet). The whole engine: `docs/upwork/sales-engine.md`. 12 Oct: version A now opens with you, as your own letters do (it had no instruction, and most A drafts opened with the client); version B is unchanged apart from "do not judge or praise their plan". No test letter had been sent.*

---

## 0. Your answers (9 Oct)

| Question | Answer | What it means |
|---|---|---|
| First test | **Volume + openings** | T1 below |
| Weekly connects budget | **No fixed cap** (9 Oct, night): "300 to 400 is like my last range ... until we hit some good clients with more revenue" | Each bid is judged on the job; the progress email shows connects spent against $150 a week as a guide, not a limit. The bid calculator (T2) will advise per job. |
| (the first answer, superseded the same night) | $150 a week | About 10 proposals a week at the 2026 average bid; T1 was sized at 18–20 a week with boosts of 60 extra connects or less to fit it. |
| Progress email | **Mon, Wed, Fri and Sun** | Mon/Wed/Fri at 8:30 IST, and the week on Sunday at 10:30 (built 9 Oct, `modules/upwork/progress_email.py`; not switched on yet). The plan was to fold Sunday's into the outcome email; as built it is its own email, because the outcome email skips any Sunday with nothing to ask. Folding it in is a change to that email, so it is your call. |
| Test length | **About 40 per side + 7 days**, at most 6 weeks | At 18–20 a week, T1's openings result is due about mid-November |

---

## In short

- **The data is nearly complete.** Four gaps are worth closing before the first test:
  - contracts that closed before Nov 2023;
  - every proposal and every client open after 8 Oct;
  - what each job looked like at the moment you applied (how crowded it was, whether the client was active);
  - a link between the scanner and the proposals you actually sent.

  All four can be pulled read-only. None needs anything from you except a yes.
- **"Apply more" is probably right, but only if the extra proposals go to the right jobs.** In 2026 you sent about four times as many cold proposals as in 2025 and won more clients (8 against 6). But each proposal converted about a third as often: 3.5 hires per 100 sent, against 10 per 100. The 2026 data shows where it leaked: crowded jobs, late applications and lower scanner scores all converted far worse. The scanner already finds about 10 top-band jobs a week, and those convert best. So "more" should mean every top-band job first, then the best of the rest.
- **The game: change one thing at a time.**
  - Every other proposal gets the new version, so both versions meet the same weeks and the same kinds of jobs.
  - We decide once each side has about 40 proposals and a week has passed for opens and replies to arrive. At 25 proposals a week that is about three weeks of applying, plus the week of waiting.
  - At your current volume, a one-week test is mostly luck. The tracker will say so on screen instead of pretending.
- **First test, from Friday 9 Oct:**
  - raise the volume to about 25 a week, aimed at the top band and applied within 24 hours;
  - at the same time, alternate the letter's opening lines (the part clients see in their list) between your current style and a "their problem first" style.
- **The 600-connect question gets a calculator, not a rule.** For each job it shows what the boost costs, what similar bids got you before, and what this client is likely worth. You decide.

---

## 1. Is the data complete and accurate?

### What we have today

| Data | Covers | Size | How it was read |
|---|---|---|---|
| Every proposal you sent, all 8 statuses | 16 Apr 2023 → 7 Oct 2026 | 595: 486 cold, 106 invitations, 3 direct offers | Upwork connection, read-only, every status list paged to the end |
| When each client first opened it | snapshot of 8 Oct | 589 pages read (208 opened, 381 not), 6 pages gone | the date on each proposal's own page, read in your Chrome |
| Replies and hires | same | 183 conversations; 51 hires marked by Upwork + 2 found since (Globaltize, Optimize IS) | proposal records, rooms, contracts, ledger |
| Contracts | 13 Nov 2023 → 26 Aug 2026 | 65 (53 personal, 12 agency) | daily contracts read |
| Money | May 2023 → today | every Upwork, Wise, Stripe and bank payment | reconciles with the Clients tab to the cent |
| Scanner | 20 Mar 2026 → today | 18,906 jobs scored | hourly scan |
| Connects | every proposal's bid and boost result; every purchase | 26,805 connects on cold proposals | proposals + ledger |
| Invitations you did not take | Jan 2025 → Oct 2026 | 22 months | your archived-interviews list |
| Conversations | Oct 2024 → today | 3,586 messages | hourly inbox read |

The proposal list is complete as far as Upwork will show it. The research read every status list to its last page. The earliest proposal (16 Apr 2023) sits just before your first Upwork payment (28 May 2023), which suggests the account's history starts there.

### Gaps, and how to close them

| # | Gap | Why it matters | Fix (all read-only) |
|---|---|---|---|
| A1 | Contracts closed before Nov 2023 are missing (CMF Publishing's start, InfoMagic, Remotual, inner.garden, AI Agency…) | 8 hires from 2023–25 show "no payment found"; we can't tell how some big clients started | one-time read of every closed contract, no date limit, which links each contract's job to its proposal |
| A2 | Nothing after 8 Oct is in the funnel | the page stops at 7 Oct | daily refresh of all 8 status lists into the funnel, with letter facts and job tags |
| A3 | We only know how crowded a job was **after it closed** | the strongest signal (a crowded job with a quiet client) has to be known **before** you apply | for each new proposal, record the job at that moment: number of proposals, interviewing, invites sent, the client's last activity, the top boost |
| A4 | Opens are a single snapshot from 8 Oct | recent proposals can still be opened | daily Chrome read of every open proposal's page until it closes |
| A5 | The scanner doesn't know what you applied to (14 of 18,906 rows marked) | we can't measure which scores convert, week by week | link scanner rows to proposals by job id (121 of the 233 proposals since March already match) |
| A6 | Your profile's history isn't kept | a profile test needs dated versions | weekly snapshot of title, overview, rate and Job Success Score |
| A7 | No check against Upwork's own numbers | "100% accurate" needs an outside check | monthly comparison with Upwork's own counts (the proposals page's tab totals, and the stats page if it shows them); differences listed, never smoothed |

### Accuracy notes from the audit

- **September looks broken but isn't.** Only 1 of 16 cold proposals was opened. Those jobs drew 90–350 proposals and their clients opened almost nobody: 0 of 184 on one job, 7 of 285 on another. It's a lesson about which jobs to pick, not a data error. Fix A3 would have shown it before you spent the connects.
- **Hires** now include clients won on another job or off Upwork.
- **Revenue** follows the client across Upwork, the agency, Wise, Stripe and the bank. It is reconciled with the Clients tab.
- **Old hires:** 8 hires from 2023–25 still have no payment under any name. A1 may recover some of them.

---

## 2. What the data says about "just apply more"

**Cold proposals by year:**

| Year | Sent | Opened | Wrote back (of opened) | Hired | Hires per 100 sent |
|---|---|---|---|---|---|
| 2023 | 120 | 34% | 54% | 7 | 5.8 |
| 2024 | 80 | 39% | 48% | 5 | 6.2 |
| 2025 | 59 | 36% | 62% | 6 | 10.2 |
| 2026 (to 7 Oct) | 227 | 30% | 45% | 8 | 3.5 |

You're right that the funnel's shape barely moves between periods. Roughly a third get opened, about half of those write back, and a quarter to a third of those hire. The 2026 row, though, shows the part to watch: more volume did bring more clients, but each extra proposal converted worse.

Between March and August 2026 you won 1–2 cold clients every month, whether you sent 6 proposals (July) or 59 (May). That's too few hires to prove anything either way, but it's a warning that volume alone may add fewer clients than you'd expect.

**What separated the proposals that worked (2026 cold):**

| Signal | Better | Worse |
|---|---|---|
| Scanner band | 28+: 43 sent → 13 opened → 7 wrote back → **4 hired** | 19–27: 55 sent → 13 opened → 3 wrote back → **1 hired** |
| How crowded the job was (final count) | 20–49 proposals: 38% opened | 100+ proposals: 15% opened, 1 reply in 26 |
| How fast you applied | within 24 hours: about 32% opened | after 24 hours: 19% opened, 1 reply in 31 |

**Supply isn't the limit.** In September and October the scanner scored about 60–110 jobs a week at 20 or more, and 5–11 a week in the top band (28+). You sent 4–15 proposals a week.

**The money supports spending more.**
- In the last six months, a cold hire cost about $410 in connects.
- 2026's cold hires have paid $22,741 so far. Over the last six months that's about $7.90 back for every $1 of connects.
- Even if the extra proposals convert half as well, they still pay for themselves several times over.

**My read:** volume is the biggest lever, if it's aimed. Apply to every top-band job, apply within 24 hours, and skip jobs already crowded with a quiet client. The first test measures exactly this.

---

## 3. The 600-connect question

What the data says about boosts so far (cold proposals, Apr–Oct 2026):

| Boost (connects above the base) | Sent | Opened | Wrote back |
|---|---|---|---|
| none | 35 | 14% | 3 |
| 1–30 | 23 | 43% | 3 |
| 31–60 | 26 | 35% | 6 |
| 61–100 | 50 | 36% | 8 |
| over 100 | 68 | 26% | 5 |

The base cost averaged 21 connects, and the whole bid 107.

Two things stand out. Not boosting at all hurts: 14% opened. A small or medium boost did as well as a big one. That isn't a rule yet: these are small groups, and big boosts tend to happen on crowded jobs, so the crowd may be what hurts, not the boost. But it's why T1 can stay within $150 a week.

**Proposed: a bid calculator in the draft view.** For each job it shows:

- the top boost right now, and what yours would cost in dollars;
- what proposals like this got you before: same boost level, same crowd size, same scanner band;
- what this client is likely worth, from their spend, hires and the rates they've paid;
- the result in one line: *"600 connects = $90. Worth it if your chance of being hired is above 2%. Proposals like this have won about X% so far."*

**Worked example.** A client who has spent $50k and hires often is likely worth $3–5k a year to you. A $90 boost pays for itself if it raises your chance of being hired by 2–3 points. On the jobs where you skipped a 600-connect top bid, the calculator might well have said yes.

Test 2 checks whether following the calculator does better than your current habits.

---

## 4. How the game works

**Each test is one card:**

- **Hypothesis:** what we think will happen and why, quoting the data.
- **The change:** exactly what differs. For a letter test, both versions are written out.
- **Assignment:** every other proposal, alternating, so both versions meet the same weeks and jobs. The drafting system picks the version and tags the draft. After you send, the system checks the sent letter, so a heavily edited draft is flagged, not miscounted.
- **The deciding number:**
  - *opened* for anything the client sees in the list: the first lines, your profile title, your rate, the boost;
  - *wrote back, of opened* for anything inside the letter;
  - *hires per 100 sent* and *revenue* for volume and job choice. These are slow, so they decide only over longer runs.
- **Size and end:** at least about 40 proposals per side, and 7 days after the last one so opens and replies can arrive. At most 6 weeks. We stop early only if one side is clearly broken, for example no opens in its first 15.
- **Verdict, in plain words:** *clearly better*, *a lean*, *no difference*, or *too early*.
- **Decision:** keep it, go back, or try a variation. Each decision is written into the log with what we learned and what to try next.

**How sure can a test be?** If one version really is better, these are the chances the numbers show it:

| Proposals per side | 30% vs 45% opened | 30% vs 40% opened | 13% vs 26% wrote back |
|---|---|---|---|
| 10 | 67% | 67% | 85% |
| 20 | 83% | 74% | 77% |
| 40 | 91% | 82% | 92% |
| 80 | 98% | 90% | 98% |

So a one-week test at 10–20 proposals a side can only catch a dramatic change. About 40 a side is where the result starts to mean something.

**One test at a time.** The exception is a volume test, which can run alongside an A/B test because both sides get the extra volume.

**The weekly rhythm (Friday):**
1. Look at the tracker.
2. Read the verdict.
3. Decide: keep it, go back, or try a variation.
4. Start the next test.

The Friday email carries the decision prompt.

### Starting queue (for your approval)

| # | Test | Change | Deciding number | Earliest call |
|---|---|---|---|---|
| T1 | **Aimed volume + opening lines** | 18–20 cold a week within $150: every 28+ job first, then the best 19–27, applied within 24h, boosts of 60 extra connects or less. Openings alternate: your current style vs "their problem first" | **wrote back, of sent** (as built: the opening is read before and after the open, so the reply is what it moves); hires and revenue per 100 sent and per $ compared with Apr–Sep (volume) | about mid-November (openings); the volume read continues after that |
| T2 | **Bid calculator** | on alternating proposals, bid what the calculator says; on the others, your usual bid | wrote back per $ of connects | after T1 |
| T3 | **Crowd filter** | skip jobs already past ~50 proposals with a client who hasn't been active in the last day, unless top band (needs A3) | opened | after T1 |
| T4 | **Profile title** | a sharper title, before and after (one profile, so it can't alternate) | opened | later |
| T5 | **Loom on top-band jobs** | a short Loom on alternating 28+ jobs | wrote back, of opened | later |

---

## 5. Build checklist, in order

**Phase A — complete, current data** (about 2 days; all read-only on Upwork)
- [ ] A1 One-time read of every closed contract, linking contracts to proposals by job. Re-check the 8 "no payment found" hires.
- [ ] A2 Daily proposal refresh (all 8 statuses) into the funnel, with letter facts and job tags.
- [ ] A3 Job snapshot at the moment you apply: proposals, interviewing, invites, the client's last activity, the top boost.
- [ ] A4 Daily Chrome read of open proposals' pages (open date, crowd) until each one closes.
- [ ] A5 Link scanner rows to proposals, so score bands are measured every week.
- [ ] A6 Weekly profile snapshot.
- [ ] A7 Monthly accuracy check against Upwork's own counts.

**Phase B — the game** (about 3 days)
- [ ] B1 Store tests and their arms; tag each proposal with the arm it was drafted under.
- [ ] B2 Drafting: the system picks the arm (alternating), writes that version, tags the draft, and checks the sent letter after the fact.
- [ ] B3 Upwork tab › **Tests**:
  - the live test's scoreboard per side (sent, opened, wrote back, hired, connects, $);
  - the verdict in plain words and the days left;
  - the queue of next tests;
  - the log of every past test with its decision and lesson.
- [ ] B4 The end and verdict rules above, computed, never typed.

**Phase C — reports** (about 1 day)
- [ ] C1 Email on Mon / Wed / Fri at 8:30 IST, and on Sunday inside the existing outcome email:
  - each proposal sent since the last email and where it stands;
  - the live test's scoreboard;
  - connects spent against the $150 week;
  - the best open jobs to apply to now, top band first, with links;
  - replies waiting for you.
- [ ] C2 Friday: a written weekly reading (AI) saved to the Funnel's insights, plus the test decision prompt.

**Phase D — decision support** (about 2 days)
- [ ] D1 Bid calculator in the draft view.
- [ ] D2 Scanner feedback: each month, proposed rule changes with their numbers (for example the crowd filter, or the hours-to-apply cut-off). You decide. Config changes stay proposed diffs.

**Phase E — run**
- [ ] T1 starts Friday 9 Oct by hand (you apply as agreed; openings alternate). The tracker picks it up when B lands, because the two openings can be told apart in the sent letters.

Notes:
- Task schedules, budgets and scanner thresholds live in the config. Each one comes to you as a proposed change.
- Upwork stays read-only. Proposals stay drafts, which you send.

---

## 6. What this changes from earlier decisions

- **Experiments:** on 8 Oct you chose "detect after the fact only". This plan makes tests planned and tagged when the draft is written. Detecting after the fact stays, as the check.
- **The weekly runner** becomes the Friday reading and test review.
- **The end-of-week email** becomes Mon / Wed / Fri at 8:30 IST, plus a section in the existing Sunday outcome email.

## 7. Questions for you

All four are answered (section 0). Still open:

- Your go on the build order (section 5).
- Your OK on T1's boost limit (60 extra connects or less), which keeps 18–20 proposals a week inside $150.
- Both openings for T1. I'll draft them on Friday's first top-band job for your OK before you send.

## 8. Honest limits

- **Hires are rare.** At 1–2 a month, conclusions about hires take months, so tests decide on opens and replies, and hires and revenue confirm over time.
- **Upwork changes mid-test** (search ranking, connects prices, boost rules) can move the numbers. We'll log them on the test card when we notice them.
- **Drafts are only drafts.** If you change a version heavily before sending, that proposal is flagged and left out of the comparison rather than counted on the wrong side.
