# Sunil Paul — Dossier

_Compiled 2026-08-13 17:22 IST · 107 client facts (107 trusted) from 8 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- The client (Tenni Theurer) is building a 'gratitude chain' system where users are messaged via SMS, confirm readiness, then receive an outbound voice call via VAPI to collect gratitude messages and referral contacts (names and phone numbers), which are stored in Google Sheets. [CONFIRMED · fathom/61317495 @ 00:00:00 – 00:02:40]
- The client is building a voice-agent platform with multiple agent types including: a Gratitude Chain (daily recurring calls), a Whisper Game (a telephone-style game with a start and end), and an AI Quiz. The immediate focus is on the Gratitude Chain. [CONFIRMED · fathom/63675421 @ 00:13:19 – 'the gratitude change is on a daily basis. The whisper game is just a game of telephone… AI quiz. That's also an idea']
- The client's project is called the 'Gratitude Chain' — an AI voice agent system that contacts people via SMS, collects a moment of gratitude via a voice call, and asks them to add new participants to the chain. [CONFIRMED · fathom/62084384 @ 00:04:20]
- The clients (Tenni and Sunil) are building VAPI-based voice agents and have been doing so themselves but progressing slowly; they are now seeking experienced outside help to move faster. [CONFIRMED · fathom/60769759 @ 00:00:27]
- The clients have already built a recruiting voice agent using VAPI as their first use case, and plan to build 5–10 additional voice agents targeting various use cases with viral feedback loops built in. [CONFIRMED · fathom/60769759 @ 00:10:50]
- The clients plan to build a summer internship program, potentially for 1–10 people, combining mentorship in entrepreneurship with hands-on experience in AI/automation work. [CONFIRMED · fathom/60769759 @ 00:12:45]
- The product being built is a 'gratitude chain' app: a voice agent calls users daily to record gratitude messages, and users can invite others who then hear the gratitude shared about them. [CONFIRMED · fathom/64481221 @ we're trying to create patterns or rituals...every day you're doing a gratitude...you might share a gratitude with somebody and then invite them to the gratitude chain and then they get to hear what you were grateful for]
- The client's project is a 'gratitude chain' automation: contacts are SMS'd, those who reply 'yes' receive an AI voice call from an agent named Hannah, the agent collects a gratitude message and referral contacts, and new contacts are added to the sheet for future outreach. [CROSS-VERIFIED · fathom/61813233 @ 00:08:19 – 00:20:09]

## The numbers

- Abhijeet can commit approximately 10–12 hours per week; the client's near-term ceiling is around 20 hours per week but 10 hours per week is preferred initially. [CONFIRMED · fathom/60769759 @ 00:04:18]
- Abhijeet estimates 4–5 hours to set up the entire current scope of the gratitude chain workflow. [CONFIRMED · fathom/61317495 @ 00:14:16 – 00:14:22]
- Current platform costs are effectively zero as the project is operating on free credits. [CONFIRMED · fathom/62084384 @ 00:06:21]
- Make.com free plan limits active scenarios to 2 at a time; the client needs to upgrade to a Core (paid) plan to keep all scenarios active simultaneously. [CONFIRMED · fathom/61813233 @ 00:01:00 – 00:01:18]
- Revenue to date (GROSS, billed): $845.67 across 3 revenue transactions (2025-05-11 → 2025-06-01). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $760.26 — gross $845.67 less $84.57 Upwork service fees, $0.84 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]
- The client has approximately 10 use cases they want to build out using VAPI, Make.com, and related tools. [CONFIRMED · fathom/61317495 @ 00:15:41 – 00:15:49]

## People

- Tenni Theurer and Sunil are co-founders working together on voice agent projects. They have also worked together for three years at a prior startup called Springfree EV, a fintech company focused on EV financing. [CONFIRMED · fathom/60769759 @ 00:11:21]
- Tenni Theurer and Sunil met with contractor Abhijeet Gandhi (based in Pune, India) to discuss engagement; Abhijeet had previously spoken with Sunil before this call. [CONFIRMED · fathom/60769759 @ 00:00:03]
- The client's name is Tenni Theurer. The agency contact/contractor is Abhijeet Gandhi (referred to as 'Abhi'). [CONFIRMED · fathom/63675421 @ 00:00:00]

## Architecture & systems

- A 'reset' scenario was added that runs daily at 12:00 to reset all participant statuses (except opted-out) to empty, so that the daily gratitude reminder call cycle restarts each day. [CONFIRMED · fathom/62084384 @ 00:10:00]
- A Twilio webhook is set up to send inbound SMS data to a Make.com scenario, which then processes the reply (e.g., 'yes' or 'stop') and either opts the user out in Google Sheets or triggers an outbound VAPI call. [CONFIRMED · fathom/61317495 @ 00:00:00 – 00:01:28]
- A bug was identified where the automation was prepending a country code (+1) when searching for phone numbers, but the Excel sheet did not include country codes, causing the 'YES' reply lookup to fail. [CONFIRMED · fathom/62084384 @ 00:01:03]
- A known VAPI issue is that agents often fail to end calls autonomously. Two workarounds are: (1) setting a max call duration buffer based on test call averages, and (2) prompt engineering to explicitly instruct the agent to terminate the call. [CONFIRMED · fathom/60769759 @ 00:05:42]
- A real-time SMS is sent during the voice call via a VAPI tool called 'send SMS gratitude'; the tool is triggered in the prompt after the user shares their gratitude message, then a Make.com webhook scenario fires the SMS. Phone number is passed automatically by VAPI so no extra parameters are needed. [CONFIRMED · fathom/65139964 @ 00:11:30]
- Abhijeet built an appointment-booking VAPI agent integrated with Google Calendar; it initially took two days to build but he can now complete a similar setup in a couple of hours. [CONFIRMED · fathom/60769759 @ 00:09:35]
- Abhijeet fixed the country code bug: the automation now checks if a country code is already present before adding one, so it works whether or not the Excel sheet includes the country code. [CONFIRMED · fathom/62084384 @ 00:03:01]
- Abhijeet suggested a cleaner future architecture: record the streak value in a Google Sheet or database (e.g. Airtable), and have that update trigger a separate Make scenario to send the milestone SMS, rather than embedding streak logic inside the log-calls scenario. [CONFIRMED · fathom/65139964 @ 00:05:00]
- Airtable is being introduced as a data layer ('we have the A table now as well'), and Abhijeet planned to start working on the new architecture including linking of records that evening. [CONFIRMED · fathom/62084384 @ 00:07:02]
- An iterator module is used to handle the array of invited phone numbers returned by the OpenAI extraction step, allowing the automation to process one or more invites per call. [CONFIRMED · fathom/64481221 @ Iterator is for like, you know, obviously the phone numbers, there is going to be the array]
- At the end of each call, an OpenAI model processes the call transcript to extract both the gratitude message shared by the user and any invited phone numbers/names, which are then written back to the spreadsheet. [CONFIRMED · fathom/64481221 @ we are asking it to not only give us what invited phone numbers are, but also give us what is the gratitude message that the user shared]
- ChatGPT (GPT model in Make) is used to phrase/rephrase previous gratitude messages before passing them to the voice agent as context for the call, in both the 'Launch Voice Agent' and 'Daily Calls' scenarios. [CONFIRMED · fathom/65139964 @ 00:08:20]
- During the VAPI voice call, a VAPI 'tool' (function call type) is configured to send collected data (names and phone numbers as arrays) to a Make.com webhook only after the user has finished providing all referral information, to limit the number of webhook calls. [CONFIRMED · fathom/61317495 @ 00:03:09 – 00:05:30]
- For managing multiple use cases, the agency's proposed approach is to create a Google Sheet per use case tracking workflow status (built, in progress, etc.), assistant IDs, Make.com scenario links, and other metadata. [CONFIRMED · fathom/61317495 @ 00:16:50 – 00:17:07]
- For outbound calls, VAPI requires a Make.com API call specifying both the assistant ID and the phone number ID; the Twilio phone number cannot be used for outbound directly within VAPI's UI — Make.com is required as the intermediary to initiate outbound calls. [CONFIRMED · fathom/61317495 @ 00:10:00 – 00:12:40]
- Google Sheets is structured with the following tabs acting as tables: Invites (people being invited), Routing (maps users/agents), Users, Sessions (every call logged), Transcripts, Agents (agent table with agent codes), and a campaigns/future features tab. [CONFIRMED · fathom/63675421 @ 00:02:19 – 00:05:06]
- Gratitude messages are stored in a dedicated 'Gratitude History' tab in the Excel/spreadsheet, keyed by phone number; multiple messages per user are appended to a single cell with timestamps rather than stored as separate rows. [CONFIRMED · fathom/64481221 @ we now updated the Excel sheet in a way that I just created one gratitude history kind of a tab where we are storing like the phone number]
- Gratitude storage is handled post-call via the end-of-call report flow (not via a real-time VAPI tool call) to avoid the ~2-second pause that tool calls introduce during a live call. [CONFIRMED · fathom/64481221 @ one of the things that happens with tools is...there is a slight two second pause...this information is okay, even if you get it at the end of the call]
- If a user replies 'stop' to the SMS, their status is marked as opted out in Google Sheets. If they reply 'yes', an outbound call is placed immediately (no delay) via VAPI's API, triggered from Make.com. [CONFIRMED · fathom/61317495 @ 00:01:18 – 00:01:56]
- Make.com queues incoming webhook requests even when a scenario is deactivated; upon reactivation, the scenario processes all queued (incomplete) executions. [CONFIRMED · fathom/61813233 @ 00:13:43 – 00:14:11]
- Personalization is passed to VAPI calls via 'assistant overrides' dynamic variables (double curly braces in the system prompt). The planned next step is to add the inviter's name as a dynamic variable so the agent can say who invited the callee. [CONFIRMED · fathom/63675421 @ 00:16:05 – 'we just need to add more variables there with the information like the inviter's name… you have to have those exact variable names in the happy prompt with the double curly braces']
- Real-time VAPI tool calls are reserved for actions needed during the call (e.g., sending an SMS/link to a user, fetching live appointment slots); non-real-time data like gratitude storage is handled post-call. [CONFIRMED · fathom/64481221 @ the tools we mainly use, like you want to send the SMS to the user in the call itself...this information is okay, even if you get it at the end of the call]
- Reminder message for day 1 invite: 'Quick reminder from the Global Guest, ready to share one grateful, reply to start.' Reminder message for day 3 invite (second reminder): 'Still time to add your voice, keep the chain rolling.' [CONFIRMED · fathom/65139964 @ 00:11:03]
- SMS messages are sent using the Twilio module within Make.com (not VAPI's built-in send-text tool), as the VAPI send-text tool is only usable for sending SMS during an active call. [CONFIRMED · fathom/61317495 @ 00:13:18 – 00:13:47]
- Scenarios triggered by webhooks are on-demand (no schedule); scenarios triggered by polling modules (e.g., Google Sheets row watch) use a clock/interval schedule. The trigger type is determined by the starting module selected. [CONFIRMED · fathom/61813233 @ 00:09:22 – 00:10:50]
- Session state (user perspective: e.g. 'awaiting reply', 'active session') and last action (system perspective: e.g. 'sent invite') are tracked as separate fields in the Routing tab of Google Sheets. [CONFIRMED · fathom/63675421 @ 00:11:56 – 'the last action is sort of what did the system do… the session state is more from a user's perspective']
- Session/user statuses used in Google Sheet include: 'sent SMS invite', 'waiting for reply', 'completed session', 'no answer'. Users with 'completed session' or 'no answer' are treated as existing/returning users for daily calls. [CONFIRMED · fathom/65139964 @ 00:07:40]
- Streak milestone SMS is sent at the end of a call within the 'log calls' Make scenario, immediately after the call ends (within ~50 seconds), if the user has shared a gratitude message that day and had previous consecutive-day messages forming a streak of 3, 5, or 7/10 days. [CONFIRMED · fathom/65139964 @ 00:02:49]
- The 'Invite Reminder' Make scenario runs daily at 10 a.m., checks the invite sheet for users with status 'invited' and session status 'waiting for reply', calculates the time difference since invitation was sent, and sends different reminder SMS messages based on whether it is day 1 or day 3 since the invite. [CONFIRMED · fathom/65139964 @ 00:10:05]
- The SMS-reply-handling scenario uses a Router with two filters: one branch checks if the reply body is 'yes' (updates sheet and triggers Vapi call via HTTP module), the other checks if the body is 'stop' (marks user as opted out). [CONFIRMED · fathom/61813233 @ 00:14:44 – 00:15:00]
- The agent dynamically announces who invited the user at the start of each call (e.g., 'XYZ person invited you to this'), using the inviter variable injected into the prompt. [CONFIRMED · fathom/64481221 @ 00:01:00]
- The call-trigger flow is: user receives an SMS invite → user replies with an agent code (e.g. 'AI quiz' or 'whisper') → Twilio sends the reply to a Make webhook → Make looks up the phone number and session state in the Routing Google Sheet tab → Make verifies the agent code matches → VAPI call is placed to the user. [CONFIRMED · fathom/63675421 @ 00:14:18 – 'what triggers the call is the SMS reply, so when Twilio gets the reply, that's this web call']
- The current database design uses Excel sheets as a lightweight prototype; the team acknowledged they will move to a properly architected database when transitioning to production. [CONFIRMED · fathom/64481221 @ once we start moving on more on the production side, we can obviously spend more time on, like, you know, architecting every database and the entity]
- The gratitude chain automation is split into multiple independent Make.com scenarios: (1) Send First SMS – scheduled trigger, runs at regular intervals (currently every 15 min); (2) Watch for SMS reply – webhook/on-demand; (3) Store Moment of Gratitude – webhook triggered by Vapi tool call; (4) Add New Contacts – webhook triggered by Vapi tool call; (5) Follow-up Call scenario – triggered when a call ends via Vapi webhook. [CONFIRMED · fathom/61813233 @ 00:07:56 – 00:18:13]
- The gratitude history stored per user (all messages + dates in one cell) is structured to enable future LLM-based analysis, such as extracting themes or surfacing a random past gratitude to share back with the user. [CONFIRMED · fathom/64481221 @ you can feed the entire gratitude history of one person into an LLM and say, pull out all the themes, pull something out, or pick something random to share back with the user]
- The system is organized in Make into three core scenarios: (1) inviting users, (2) launching agents, and (3) logging calls. [CONFIRMED · fathom/63675421 @ 00:05:26 – 'everything is kind of boiled down to three different scenarios for inviting users, launching agents and logging those calls']
- The team discussed creating reusable Make.com scenario templates for common building blocks (e.g., pick up phone number and send SMS) to be reused across multiple VAPI agents, planned as a week-three project. [CONFIRMED · fathom/62084384 @ 00:09:03]
- The team plans to version scenario folders in Make.com using naming conventions such as 'prod' for production and 'dev v0.0.2' for the development version to avoid conflicts. [CONFIRMED · fathom/62084384 @ 00:07:31]
- The voice agent uses VAPI with dynamic variables injected via double curly brace syntax in the prompt; variables are passed through 'assistant override' and 'variable values' fields during the VAPI call. [CONFIRMED · fathom/64481221 @ the way we fill in the dynamic information into the agent is there is this syntax that what for the keyword you want to have, you just put double curly braces around it]
- The voice agent's first message for existing/returning users is static (name-substituted), not ChatGPT-generated. First-time users receive a different first message referencing who invited them. Abhijeet noted the existing-user greeting may need ChatGPT too to avoid incorrectly implying a streak. [CONFIRMED · fathom/65139964 @ 00:09:03]
- The workflow flow is: (1) Send SMS to participant, (2) Wait for 'YES' reply, (3) Trigger outbound VAPI voice call, (4) Collect gratitude and new referral names/numbers, (5) Save transcript and recording. [CONFIRMED · fathom/62084384 @ 00:03:51]
- There are two paths that trigger the voice agent: (1) 'Launch Voice Agent' — triggered when a user sends a keyword (e.g. 'gratitude') via Twilio SMS webhook; (2) 'Daily Calls to Active Users' — a scheduled scenario that runs every day at 10 a.m. to call active users. [CONFIRMED · fathom/65139964 @ 00:06:42]
- Two Vapi tools are configured on the gratitude assistant: (1) 'Gratitude Message Story' – collects the user's gratitude message or opt-out status and posts to a Make.com webhook; (2) 'Invite to Gratitude Chain' – collects contact details of new people to add and posts to a Make.com webhook. [CONFIRMED · fathom/61813233 @ 00:16:41 – 00:18:04]
- Two dynamic variables are currently injected into the VAPI agent prompt: the inviter's name and the user's gratitude message history. [CONFIRMED · fathom/64481221 @ we have two now that is the name for the whoever the invited and then the one for the gratitude messages as well]
- VAPI tools are configured with a description field that instructs the agent on when to trigger the external function call (e.g., 'whenever the user has given us all the information about the new users'). The Make.com webhook URL is pasted into the VAPI tool's server URL field. [CONFIRMED · fathom/61317495 @ 00:04:41 – 00:05:30]
- VAPI uses a 'tools' feature to allow voice agents to call third-party APIs and services such as Make.com, Zapier, or any REST API; this is the standard integration mechanism Abhijeet uses. [CONFIRMED · fathom/60769759 @ 00:09:03]
- VAPI's native Twilio phone number integration works for inbound calls (customer calls the VAPI number), but outbound calls must be initiated via VAPI's API (called from Make.com), passing customer phone number, assistant ID, and phone number ID. [CONFIRMED · fathom/61317495 @ 00:12:00 – 00:13:06]
- Vapi assistant overrides are used to dynamically inject personalized values (e.g., the caller's name) into the prompt at call time, passed via the HTTP request from Make.com. [CONFIRMED · fathom/61813233 @ 00:16:01 – 00:16:31]
- Within the 'Store Moment of Gratitude' Make.com scenario, execution flow is: webhook trigger → extract function arguments (gratitude message, opt-out flag) → search Google Sheet row by phone number → update row with gratitude data; a second route handles opt-out=true by marking the user as opted out. [CONFIRMED · fathom/61813233 @ 00:18:13 – 00:19:56]

## Stack & tools

- Abhijeet has experience with Make.com, Zapier, and n8n (no-code automation tools) prior to working with VAPI, and uses these for integrations such as writing to Google Sheets or Supabase. [CONFIRMED · fathom/60769759 @ 00:10:00]
- Client's project uses VAPI for voice agents, Make (formerly Integromat) for automation/orchestration, Twilio for SMS, and Google Sheets as the primary data store (chosen over Airtable and Supabase for simplicity during early stage). [CONFIRMED · fathom/63675421 @ 00:08:17 – 'rather than trying to hook up the Supabase or Airtable, maybe for now we'll just use the Google Sheets']
- Google Sheets (referred to interchangeably as 'Excel sheet') is used as the data store, holding at minimum: contact name, phone number, last SMS sent date, and status fields. [CONFIRMED · fathom/61813233 @ 00:01:00 – 00:07:43]
- Sunil spent approximately 4–5 hours attempting to connect VAPI output to Google Sheets or Supabase without success. [CONFIRMED · fathom/60769759 @ 00:08:05]
- Tenni has been trying to set up VAPI's native Twilio integration for over a week and is stuck due to no visible error messages and an unresolved configuration issue on the Twilio side. [CONFIRMED · fathom/60769759 @ 00:07:26]
- The agency uses Make.com as its primary automation/orchestration platform, with scenarios that can be copy-pasted and adapted for different clients or agents. [CONFIRMED · fathom/62084384 @ 00:08:32]
- The automation is built in Make.com (formerly Integromat), using multiple scenarios organized in a project folder dedicated to the client's gratitude chain project. [CONFIRMED · fathom/61813233 @ 00:01:00]
- The automation is built in Make.com (make scenarios) with WAPI for voice agent triggering. [CONFIRMED · fathom/65139964 @ 00:02:11]
- The automation stack for this client's project uses Make.com as the core orchestration layer, Twilio for SMS messaging, VAPI for outbound voice calls, and Google Sheets as the primary data store. [CONFIRMED · fathom/61317495 @ 00:00:00 – 00:13:47]
- The automation stack for this project includes VAPI (voice AI), Make.com (automation), Twilio (SMS/telephony), OpenAI (transcript parsing), and Excel/Google Sheets (data storage). [CONFIRMED · fathom/64481221 @ 00:07:28]
- The automation uses Make.com (scenarios) for orchestration, Twilio for SMS, VAPI for the AI voice agent, and an Excel sheet as the data store for phone numbers and statuses. [CONFIRMED · fathom/62084384 @ 00:08:32]
- The clients are currently manually copying and pasting transcripts into custom GPTs for processing because the automated pipeline is not yet configured. [CONFIRMED · fathom/60769759 @ 00:08:15]
- The clients are using VAPI for voice agents, Twilio for phone number integration, Supabase and/or Google Sheets as data outputs, custom GPTs for transcript processing, and are looking to use Make.com for automation (they do not yet have a Make account as of this call). [CONFIRMED · fathom/60769759 @ 00:07:50]
- The clients prefer Make.com over Zapier for orchestration, though Tenni has prior familiarity with Zapier. They have not yet deeply used Make. [CONFIRMED · fathom/60769759 @ 00:08:42]
- The system uses Twilio for SMS messaging; Twilio account was suspended due to insufficient balance and was recharged during the call. [CONFIRMED · fathom/65139964 @ 00:00:30]
- Twilio is used for SMS sending in this client's automation, but the client's Twilio account does not yet have SMS verified; SMS is temporarily running through the agency's Twilio account. [CONFIRMED · fathom/61813233 @ 00:00:20 – 00:00:42]
- User/session data is stored in Google Sheets; Google Apps Script would be required to trigger Make scenarios directly from a Google Sheet row change, unlike Airtable which supports native watch/trigger events. [CONFIRMED · fathom/65139964 @ 00:05:39]
- Vapi (api.vapi.ai) is used for the AI voice calling layer; the HTTP module in Make.com posts to the Vapi create-call endpoint, passing assistant ID, phone number ID, assistant overrides, and customer details. [CONFIRMED · fathom/61813233 @ 00:15:00 – 00:16:31]

## Timeline

- As of this call, Sunil is on a road trip for the next four to five weeks; the clients want to resolve technical configuration issues during this period. [CONFIRMED · fathom/60769759 @ 00:12:15]
- The contract was paused during Abhijeet's hospitalization and is being reopened at this call; the team is resuming work on the v2 scope. [CONFIRMED · fathom/63675421 @ 00:01:42 – 'I'll reopen the project so that we can kind of go through it']
- The second scope (next week after the current build) includes Supabase API integration and a Calendly or Google Calendar integration for scheduling. [CONFIRMED · fathom/61317495 @ 00:15:06 – 00:15:41]

## Commitments

- Abhijeet committed to having the full gratitude chain workflow set up and ready to test by the Friday call. [CONFIRMED · fathom/61317495 @ 00:14:23 – 00:14:33]
- Abhijeet committed to starting work on the new architecture and additional features (linking of records) that same evening (the night of the call). [CONFIRMED · fathom/62084384 @ 00:06:55]
- Abhijeet offered to receive brief descriptions of all 10 use cases from the client and produce workflow diagrams for each, showing where VAPI, Make.com, and other tools will be used. [CONFIRMED · fathom/61317495 @ 00:16:19 – 00:16:55]
- Abhijeet proposed starting solo for the first one to two weeks, then potentially involving teammates if workload requires, while always remaining the single point of contact. [CONFIRMED · fathom/60769759 @ 00:02:09]
- Abhijeet will be invited to the clients' VAPI and Make.com accounts before Wednesday's call so he can review existing agents and identify priority issues in advance. [CONFIRMED · fathom/60769759 @ 00:14:24]
- Agreed next steps: (1) look up inviter data from the Invites sheet during the call-trigger flow and pass it to VAPI as a dynamic variable (inviter's name), (2) keep Google Sheets as the persistence layer for now, (3) focus on the Gratitude Chain use case for deeper interaction and user testing. [CONFIRMED · fathom/63675421 @ 00:15:32 – 00:13:45]
- Engagement is planned to formally start on Wednesday; the clients plan to use hourly contracts via Upwork. [CONFIRMED · fathom/60769759 @ 00:03:05]
- The client (Tenni) wants to move to a new VAPI agent after the current scope is completed, preferring to have several agents being tested in parallel rather than over-developing one. [CONFIRMED · fathom/62084384 @ 00:09:12]
- Twilio SMS verification for the client's own account is expected to be completed by Monday following the call. [CONFIRMED · fathom/61813233 @ 00:00:42 – 00:00:47]

## Notable quotes

- "Right after the call within 50 seconds, if they get the message, those are the highest chances that they would look at it as well." — Abhijeet Gandhi, explaining why streak milestone SMS is sent immediately at end of call. [CONFIRMED · fathom/65139964 @ 00:04:34]
- Tenni: 'I don't want to overdevelop on any one agent, but I'd rather have a few that we have that we're testing.' [CONFIRMED · fathom/62084384 @ 00:09:25]
- The AI voice agent introduced itself as: 'Hello, this is Hannah, an AI agent helping build something amazing. Someone you know asked us to reach out. We're building the world's largest gratitude chain. Would you like to help us?' [CONFIRMED · fathom/61813233 @ 00:11:14 – 00:11:27]

## Other

- A 'set multi-variables' tool is used in Make.com to manually assign variable values, enabling fast debugging of automations without running the full trigger flow. [CONFIRMED · fathom/64481221 @ This is like a set multi-variables tool. So where you can just add variables and then assign values...helps you debug the automation pretty quickly]
- Abhijeet Gandhi (contractor) was hospitalized for approximately 5 days with a fever reaching 104°F; the contract was paused during this period. [CONFIRMED · fathom/63675421 @ 00:00:00 – 'i just paused the contract and things like that']
- Abhijeet flagged that status update logic in the Google Sheet is a known fragile point that could cause calls not to go out when the system goes live; the team agreed to monitor it. [CONFIRMED · fathom/65139964 @ 00:12:40]
- During the demo, the daily-calls scenario triggered two calls unexpectedly; the root cause appeared to be an unsaved change to the scenario filter (status filter had reverted), resolved by re-setting to 'completed session' and saving. [CONFIRMED · fathom/65139964 @ 00:17:06]
- Tenni built the current Make + VAPI + Google Sheets scaffolding herself, without prior experience in VAPI, noting it took roughly three times longer than it would have for an experienced developer. [CONFIRMED · fathom/63675421 @ 00:16:47 – 'it probably took me three times longer to build this out than you would have']
- The voice agent persona is named 'Hannah' from 'the Global Gratitude Chain', which describes itself as building a worldwide collection of gratitude moments to boost happiness and well-being. [CONFIRMED · fathom/65139964 @ 00:17:43]
- Twilio SMS messages received on Abhijeet's test number were arriving from an unexpected phone number (575757), causing the webhook not to trigger when replying; a dummy setup was created to work around this for testing. [CONFIRMED · fathom/64481221 @ The message I received is from like 575757 phone number...created like a dummy setup for myself with the phone number and the body]

## Sources

- `crm/sunil-paul` · — · crm · CRM numbers — Sunil Paul
- `fathom/60769759` · 2025-05-05 · fathom · Tenni Theurer
- `fathom/61317495` · 2025-05-07 · fathom · Project Kickoff
- `fathom/61813233` · 2025-05-09 · fathom · Project Check in
- `fathom/62084384` · 2025-05-12 · fathom · Abhi/Tenni Quick Check in
- `fathom/63675421` · 2025-05-20 · fathom · Tenni
- `fathom/64481221` · 2025-05-23 · fathom · Tenni Demo Progress
- `fathom/65139964` · 2025-05-28 · fathom · Tenni
