# Simyon Pinsky — Dossier

_Compiled 2026-08-18 19:12 IST · 33 client facts (33 trusted) from 4 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- The client (Simyon Pinsky) is running an outbound calling and SMS campaign to invite buyers to attend a trade show at the end of March, requiring contacts to be reached and results recorded within approximately 2–3 weeks. [CONFIRMED · fathom/122372377 @ 00:00:51 – 'we actually have, like, two weeks to finalize this phase, the trade show is already in... at the end of March']

## The numbers

- Currently using free version of proxies which results in slow loading [CONFIRMED · https://www.loom.com/share/dbbb5a276559428f9b2e430def2d6a44 @ 0:01:50 'we are using the free version right now']
- Make.com will require an estimated 10,000–15,000 operations per day for this campaign, costing approximately $10/day on Make.com. [CONFIRMED · fathom/122372377 @ 00:10:21 – 'somewhere around 10 to 15,000 operations we'll need on a daily basis. So it's like $10 per day on make.com']
- Revenue to date (GROSS, billed): $1,324.75 across 2 revenue transactions (2024-12-17 → 2025-04-23). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $1,324.75 — the same as gross; the ledger carries no fee, tax or refund rows for this client. [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]
- The agency has 3 warmed-up email inboxes under phonicflow.ai, each capped at 100 emails/day, for a total sending capacity of ~300 emails/day. These inboxes have already sent approximately 5,000 emails collectively. [CONFIRMED · fathom/122372377 @ 00:16:15 – 'we have already sent like, you know, probably 5,000 of emails through three... we can send out 100 a day on these ones']
- The outbound call list consists of approximately 5,000 phone numbers. [CONFIRMED · fathom/122372377 @ 00:02:07 – 'it probably will run over, I would say maybe 5,000 whole numbers, less or less']
- VAPI is limited to 10 concurrent calls. With 10 calls every 3 minutes (including post-call processing wait), the system can make approximately 200 calls per hour, or roughly 1,500–1,600 calls per 8-hour day. [CONFIRMED · fathom/122372377 @ 00:02:32 – 'with Wappy, we can only call ten people simultaneously... 10 calls every three minutes... 200 calls per hour... over eight hours... 1600 calls a day']

## Architecture & systems

- Agency capability: AI outbound calling with VAPI where, if the person answers, the AI speaks a brief qualifying line (e.g. 'is this the right time to speak?') and then transfers the live call to a human coordinator. [CONFIRMED · fathom/122372377 @ 00:05:55 – 'usually there is one or two lines that AI says... is it the right time to speak with you and I can connect you with the coordinator... And then if the person says yes, then we transfer it to the human']
- Agency guideline: do not send more than 100 emails per day from a single warmed-up inbox; sending 1,000+ emails per day requires 9–10 separate warmed-up inboxes. [CONFIRMED · fathom/122372377 @ 00:14:25 – 'the limit is that you don't send more than a hundred a day from a single inbox... if you want to send out a thousand emails... you need nine to ten inboxes that are warmed up']
- The 5,000-number list will be split into batches of ~1,500. Cadence: Monday call batch 1, Tuesday follow-up batch 1; Wednesday call batch 2, Thursday follow-up batch 2; Friday call batch 3, Saturday follow-up batch 3. [CONFIRMED · fathom/122372377 @ 00:08:34 – 'Monday and then Tuesday follow-up. So we have three batches a week, Monday, Tuesday, then Wednesday, Thursday, then Friday']
- The campaign workflow: upload an Excel/CSV list → AI places outbound calls via VAPI → if no answer, send an SMS follow-up → next day, call again → if still no answer, send another SMS and stop. Results (call status tags, transcript, recording URL, and call summary) are written back to the Excel file. [CONFIRMED · fathom/122372377 @ 00:01:36 – 'Excel, we upload. It calls with a script... then we send an SMS... call me back... If nothing, try again the next day, send us a SMS, leave it alone']
- The client decided NOT to use GoHighLevel for this campaign, preferring a simpler Excel-based output given the tight 2–3 week timeline. [CONFIRMED · fathom/122372377 @ 00:07:39 – 'Something simple, no go high levels and all those stuff']
- The client's Instantly email sequence has 3 steps. A potential issue was identified with email formatting (gray background in preview) which may affect deliverability or open rates. [CONFIRMED · fathom/122372377 @ 00:18:25 – 'This is the sequence. There is three steps'; 00:18:42 – 'Maybe this is the problem... there's like this kind of a gray background']
- The output Excel will include, per call: call status/tags from VAPI (e.g. no answer, wrong number), a call summary, transcript, and recording URL. [CONFIRMED · fathom/122372377 @ 00:07:20 – 'every call that happens will put the transcript and the latest recording into the Excel... the summary as well... no answer or if the call happened, then it would be a bit longer summary']
- The script runs on both local development and AWS environments [CONFIRMED · https://www.loom.com/share/8492c62d703b41d88a97b9158e9b3607 @ if you're shifting, like if you run it on local, and then now if you want to run it on AWS]

## Stack & tools

- GHL (GoHighLevel) API tokens have a 24-hour expiry and require a refresh token to generate new API keys [CONFIRMED · https://www.loom.com/share/8492c62d703b41d88a97b9158e9b3607 @ token has an expiry date, expiry of 24 hours, and we need to have the previous refresh token to, you know, generate the new API key]
- Proxies integrated for both Thumbtack and White Pages scrapers [CONFIRMED · https://www.loom.com/share/dbbb5a276559428f9b2e430def2d6a44 @ 0:01:30 'I have added those with both the for both the thumbtack and the white page scraper']
- Proxy can be disabled by removing proxy string at line 180 for Thumbtack scraper [CONFIRMED · https://www.loom.com/share/dbbb5a276559428f9b2e430def2d6a44 @ 0:03:00 'on line 180, you can just remove this thing' (Thumbtack)]
- Proxy can be disabled by removing proxy string at line 203 for White Pages scraper [CONFIRMED · https://www.loom.com/share/dbbb5a276559428f9b2e430def2d6a44 @ 0:03:30 'for the white pages online 203, you can see there is a proxy called proxy string']
- SMS Notifier script added for sending messages [CONFIRMED · https://www.loom.com/share/dbbb5a276559428f9b2e430def2d6a44 @ 0:00:20 'I've added one script called SMS Notifier']
- SMS messaging is implemented using Twilio [CONFIRMED · https://www.loom.com/share/dbbb5a276559428f9b2e430def2d6a44 @ 0:00:30 'sending the message using Twilio']
- The agency uses the domain phonicflow.ai (positioned around appointments, leads, and support) as its sending domain for warmed-up email outreach campaigns. [CONFIRMED · fathom/122372377 @ 00:15:35 – 'It's a different, it's actually a phonicflow.ai... appointments, leads, and support']
- The automation will be built in Make.com. The client has a pre-existing Make.com account that was previously used for this project and will be reused to avoid re-setup. [CONFIRMED · fathom/122372377 @ 00:09:24 – 'all I would need is like the make.com access, and then the WAPI API key'; 00:11:08 – 'Because the previous one already worked, so we don't need to set up all stuff']
- The client (Simyon) has an Instantly account and set up 5 warmed-up email inboxes via Instantly (purchased the day before the call), currently limited to about 30–40 emails each per day. He ran a campaign sending 110 emails the prior day with 15 reported opens. [CONFIRMED · fathom/122372377 @ 00:15:03 – 'I literally made an account yesterday, there's like a domain with five warmed up, you know, instantly you can buy warmed up ones... limited to about 30, 40 per each'; 00:18:10 – '110 emails, 15 claimed to be opened']
- The client already has a VAPI account and will provide an API key to the agency for the outbound calling system. [CONFIRMED · fathom/122372377 @ 00:01:39 – 'We have a VAPI account, which we use. I can just create and give you a key']
- The script automatically requests a new API key on each run to prevent token expiration failures [CONFIRMED · https://www.loom.com/share/8492c62d703b41d88a97b9158e9b3607 @ whenever we run this script, we actually, you know, uh, request a new API key so that, you know, it never fails]

## Commitments

- Abhijeet committed to completing the outbound calling system build by Monday, potentially over the weekend, starting with a test run of 10 calls. [CONFIRMED · fathom/122372377 @ 00:11:39 – 'We'll get it done over the weekend as well... I'll just try to get it done by Monday'; 00:11:35 – 'We'll try with the 10 and then we'll see how it goes']
- Code will be pushed to GitHub repository instead of transferring via email [CONFIRMED · https://www.loom.com/share/dbbb5a276559428f9b2e430def2d6a44 @ 0:04:30 'this code is pushed so that we don't need to do transfer it through through email anymore']
- When deploying from local to AWS, ENV file credentials must be manually copied and updated to prevent the script from breaking [CONFIRMED · https://www.loom.com/share/8492c62d703b41d88a97b9158e9b3607 @ if you're shifting from local to AWS, you just need to make sure that you copy the contents of ENV file from your local and paste it into the, uh, ENV file of the AWS server]

## Other

- Configuration file (config.ini) contains Twilio phone numbers and auth tokens [CONFIRMED · https://www.loom.com/share/dbbb5a276559428f9b2e430def2d6a44 @ 0:01:00 'config.ini also includes the information, like Twilio phone numbers and the auth tokens']
- The script has not yet been committed to GitHub [CONFIRMED · https://www.loom.com/share/8492c62d703b41d88a97b9158e9b3607 @ but now, since this is not in, not on GitHub yet]
- Thumbtack scraper has a region error that prevents it from opening [CONFIRMED · https://www.loom.com/share/dbbb5a276559428f9b2e430def2d6a44 @ 0:02:30 'the thumbtack scraper still didn't open for me' and 'it was still giving the same region error']

## Sources

- `crm/simyon-pinsky` · — · crm · CRM numbers — Simyon Pinsky
- `loom/dbbb5a276559428f9b2e430def2d6a44` · 2024-10-29 · loom · Update on Recent Changes 📲
- `loom/8492c62d703b41d88a97b9158e9b3607` · 2024-12-03 · loom · Understanding API Key Generation for Script Running
- `fathom/122372377` · 2026-02-13 · fathom · Impromptu Google Meet Meeting
