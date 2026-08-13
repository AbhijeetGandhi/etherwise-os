# Brendan Jansen — Dossier

_Compiled 2026-08-14 00:15 IST · 23 client facts (23 trusted) from 2 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## The numbers

- After cost optimization, the scraping automation was reduced to approximately $0.50 per job scraped (yielding ~20 contacts). [CONFIRMED · fathom/98594850 @ 00:17:05]
- An early version of the contact-scraping automation cost $1 per 20 contacts scraped (i.e. ~$25 per 100 jobs / 2,000 contacts). [CONFIRMED · fathom/98594850 @ 00:16:36]
- Contract: AI Cold Outreach agent — status CLOSED. [CONFIRMED · CRM:contracts/41233897]
- Contract: VAPI AI agent — status CLOSED. [CONFIRMED · CRM:contracts/40856821]
- Revenue to date (GROSS, billed): $150.00 across 1 revenue transaction (2025-09-29 → 2025-09-29). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $120.00 — gross $150.00 less $30.00 Upwork service fees. [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]
- Sending 6,000 emails with personalized videos was estimated to cost approximately 2,000 euros at the evaluated Kling API pricing. [CONFIRMED · fathom/98594850 @ 00:05:22]
- Switching from VideoGen API to the Kling direct API was estimated to take approximately 3–4 hours of rebuild work. [CONFIRMED · fathom/98594850 @ 00:02:52]
- The client's target email volume by end of year is 6,000–7,000 emails. [CONFIRMED · fathom/98594850 @ 00:15:32]

## People

- Abhijeet Gandhi is the agency-side lead on this project; a team member named Mikey also worked on the automation build. [CONFIRMED · fathom/98594850 @ 00:16:54]
- Brendan Jansen op de Haar is the client; he flagged that project delays are causing him to miss his year-end targets. [CONFIRMED · fathom/98594850 @ 00:12:08]

## Architecture & systems

- The automation system includes at least two distinct automations: (1) scraping contacts from job listings, and (2) a video generation/email outreach pipeline. [CONFIRMED · fathom/98594850 @ 00:12:43]

## Stack & tools

- Kling 2.5 has its own direct API that could be used as a fallback if VideoGen API remains unavailable, producing equivalent results. [CONFIRMED · fathom/98594850 @ the Kling actually has their own API, so like, you know, we don't need to use, like, you know, VideoGen API to use this model]
- The client's automation pipeline uses VideoGen API as a unified platform to access AI video generation models including Kling and Sora. [CONFIRMED · fathom/98594850 @ we are using like Kling 2.5 model to generate these videos, right? And obviously what videos in API, what this platform is doing is like, you know, you just provide us work unified platform to access different AI models]
- The current video generation model in use is Kling 2.5, accessed via the VideoGen API. [CONFIRMED · fathom/98594850 @ 00:01:25]

## Commitments

- Abhijeet committed to proactively communicating blockers, delays, and difficulties to the client rather than staying silent when behind schedule. [CONFIRMED · fathom/98594850 @ 00:18:46]
- Client expressed interest in expanding the engagement beyond email outreach into machine learning / AI work in future phases. [CONFIRMED · fathom/98594850 @ 00:15:49]
- Decision made to immediately begin sending outreach emails without video (no video attached) while waiting up to 48 hours for VideoGen API to recover, after which the team will decide on a permanent alternative. [CONFIRMED · fathom/98594850 @ 00:08:18]
- Payment will be handled via Wise invoice rather than through Upwork, as the Upwork contract has already lapsed due to inactivity. [CONFIRMED · fathom/98594850 @ 00:18:00]

## Notable quotes

- Client stated: 'If this works, I think we can sell it to more people.' [CONFIRMED · fathom/98594850 @ 00:15:16]
- Client stated: 'The year is ending, and I'm probably not going to hit my target anymore. So it has real consequences.' [CONFIRMED · fathom/98594850 @ 00:12:08]

## Other

- Abhijeet acknowledged the team has limited experience specifically in lead generation automation, which contributed to underestimating complexity and delays. [CONFIRMED · fathom/98594850 @ 00:13:23]
- VideoGen appears to be operated by a single developer based in France, running from a personal Gmail account, raising concerns about reliability. [CONFIRMED · fathom/98594850 @ 00:00:27]

## Sources

- `crm/brendan-jansen` · — · crm · CRM numbers — Brendan Jansen
- `fathom/98594850` · 2025-11-03 · fathom · Impromptu Google Meet Meeting
