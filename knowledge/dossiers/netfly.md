# NETFLY — Dossier

_Compiled 2026-06-22 05:15 IST · 37 client facts (37 trusted) from 8 sources · 7 agency/other-client facts routed to proof-points._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- Agent-reported call outcomes in GHL are unreliable — agents forget to tag, tag wrong, or skip it when the call didn't convert, resulting in an untrustworthy outcome column. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "the agent-reported call outcomes in GHL aren't reliable. Agents forget to tag, tag wrong, or skip it when the call didn't convert."]
- Client (NetFly) uses GoHighLevel as CRM, Kixie as dialer, Zapier/webhooks for data routing, Speed.ai (or similar) for call transcription, and Google Sheets for reporting and logging. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ CURRENT STACK section of job description]
- NetFly already has a working Kixie → GHL integration where call activity is synced into GHL and call data is appended into the Notes section of each contact. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ Current Setup (Already Working) section of job description]
- NetFly's sign rate for law firm clients is calculated off connections only, not total leads routed, which understates performance and makes reporting to clients harder. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "NetFly's sign rate for law firm clients is calculated off connections only, not total leads routed."]
- The client (Haleema and Jovanna's agency) drives leads for multiple clients per US state and wants to consolidate campaigns into a shared state-level lead pool, then distribute leads to individual clients using a percentage-based (weighted round-robin) routing system. [CONFIRMED · fathom/155692198 @ 00:01:09]
- The problem the client is solving: multiple campaigns in the same state cannibalize each other's performance, and newer campaigns underperform due to lack of history, making it hard to deliver promised lead volumes to new clients. [CONFIRMED · fathom/155692198 @ 00:00:27]

## The numbers

- Revenue to date: $2,761 across 5 earning transactions (2026-05-03 → 2026-05-31). [CONFIRMED · CRM:transactions]
- The target metric is capturing data for all 600 calls happening per day in Kixie into Airtable. [CONFIRMED · fathom/153250099 @ 00:12:19 / 'if there are 600 calls happening a day in Kixi, we have data of all the 600 in Airtable']
- Total of eight clients have been onboarded into the Airtable base and are functioning. [CONFIRMED · fathom/153250099 @ Last Wednesday, we managed to set up all the clients into the Airtable base...total eight of the clients are now onboarded in the Airtable and are functioning]

## People

- Client-side contacts are Haleema U. (primary contact who initiated the hiring) and Sean (introduced on the post-call recap, likely a team member or stakeholder). [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "Hi Haleema, and welcome Sean." — 2026-04-22T22:40:00]
- Clients not yet onboarded into production (no Google sheet received) include Gibson Hill, Campion Law, and Casino Law Firm. [CONFIRMED · fathom/153250099 @ 00:13:07 / 'the law firms, the Gibson Hill, Campion Law, Casino Law Firm, like these are the ones we haven't put in the production yet']
- Key contacts on the client side: Haleema Umair (operations/project lead) and Jovanna/Joy (marketing lead responsible for deciding which landing pages to use). [CONFIRMED · fathom/155692198 @ 00:01:59]

## Architecture & systems

- A config table (Airtable base) is used to manage client onboarding; each client has a separate Airtable base identified by a base ID and a table ID, plus power list IDs. [CONFIRMED · fathom/153250099 @ 00:05:59]
- AI coaching output includes: overall disposition (e.g. signed/incomplete), per-element compliance scoring, agent feedback on what was done well and what could be improved. [CONFIRMED · fathom/153250099 @ 00:11:39 / 'there is the small feedback to the agent as well, where it says like what things did good and then what are the things that could have been improved']
- Adding inbound call tracking via GoHighLevel CRM is being earmarked as a future enhancement to the current outbound-only Kixie system. [CONFIRMED · fathom/153250099 @ 00:02:22 / 'We're thinking about adding inbound calls to what we do...into the high level CRM rather than going out by a kickseat']
- Calls from power list IDs without a corresponding Google sheet of contacts go to a 'not found' sheet rather than a client Airtable base. [CONFIRMED · fathom/153250099 @ 00:13:12 / 'those going to the not found sheet']
- Clients deactivated in the config table include: Abdallah, Casino, Campione, Gibson, Auto Injury, and James (law firm); the test client row is for testing only. [CONFIRMED · fathom/153250099 @ 00:15:20 / 'Abdallah, Cassino, Capione, Gibson' and surrounding context]
- Contact data is imported from Google Sheets provided by the client into individual Airtable bases for each client. [CONFIRMED · fathom/153250099 @ from the Google sheet and the data is already coming in for the calls going in with the statuses]
- Deactivated/old clients in the config table will be given a 'deactivated' status field rather than being deleted, so the system ignores their power list IDs but retains the data. [CONFIRMED · fathom/153250099 @ 00:14:53 / 'created this status field that would just set as deactivate it, and we won't consider it']
- Onboarding a new client requires a one-time setup: adding a row to the config table with base ID, table ID, and power list ID(s) (comma-separated), then importing the Google sheet contacts. [CONFIRMED · fathom/153250099 @ 00:15:53 / 'this is a part of one time setup']
- Proposed 4-phase build: Phase 1 – audit Kixie→GHL flow and restructure data from Notes into custom fields; Phase 2 – build Speed.ai transcription pipeline with contact matching and dual-push to GHL and Sheets; Phase 3 – design centralized Google Sheet schema with dedup and error handling via Zapier; Phase 4 – testing, edge case handling, documentation. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ A3 answer in initial proposal and recap message dated 2026-04-22T22:40:00]
- Proposed solution: Kixie CI Summary Webhook fires after every call containing AI summary, sentiment score, conversation strength, and keyword/phrase matches; payload is run through GPT for structured outcome classification (qualified, not qualified, wrong number, callback, signed) and pushed into GHL as a system-generated field alongside the agent-reported field. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "Kixie fires a CI Summary Webhook after every call that includes an AI summary, sentiment score, conversation strength, and keyword/phrase matches."]
- The agreed routing direction is a Zapier-based sequential weighted round-robin (e.g. 50/25/25 split expressed as a 2:1:1 pattern), using Zapier Storage or Airtable to maintain state (counter) so each incoming lead is instantly assigned to the correct client without waiting for a batch. [CONFIRMED · fathom/155692198 @ 00:15:54]
- The current system captures outbound calls via an end-call webhook from Kixi (Kixie), populating call status, AI-determined disposition, AI summary, recording URL, and full transcript into Airtable. [CONFIRMED · fathom/153250099 @ 00:04:25 / 'the end call web book is giving us']
- The proposed architecture collects all leads from multiple landing pages into a central pool (Airtable or Zapier Storage), then routes leads to individual clients based on configurable percentage splits defined per state. [CONFIRMED · fathom/155692198 @ 00:06:28]
- The system must allow the agency to change client percentage allocations flexibly on a month-by-month basis without rebuilding the automation (e.g. shifting from 30% to 45% for a given client in a given state). [CONFIRMED · fathom/155692198 @ 00:05:34]
- The system-generated GPT outcome classification field in GHL will sit alongside the agent-reported field rather than replacing it, enabling comparison over weeks to identify mis-tagging patterns and where sign rate is leaking. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "push the result into GHL as a system-generated field, sitting alongside the agent-reported field rather than replacing it"]
- There is a separate Airtable table called 'Open AI Agent Output' that stores AI coaching notes and call analysis (distinct from the main calls table). [CONFIRMED · fathom/153250099 @ 00:09:44 / 'There is like completely separate table for all the coaching that happens']
- Two lead-routing algorithm approaches were discussed: (1) percentage-based batched routing (hourly or daily), which requires knowing the total lead volume before distributing; (2) instantaneous sequential round-robin based on a pre-defined ratio (e.g. 2:1:1), using a counter/data-store in Zapier to track how many leads each client still needs in the current cycle. [CONFIRMED · fathom/155692198 @ 00:07:28]

## Stack & tools

- Airtable is already in use as a storage and config layer for the prior project build; a config record exists in Airtable for every client, and the agency proposes extending this config to include percentage splits per state. [CONFIRMED · fathom/155692198 @ 00:02:22]
- Landing pages used are Insta Pages, connected via Zapier. [CONFIRMED · fathom/155692198 @ 00:14:33]
- The existing workflow is built in Zapier; the client explicitly stated they want the new lead routing solution to stay within Zapier because all current setups are already there. [CONFIRMED · fathom/155692198 @ 00:14:24]

## Timeline

- Initial outreach and proposal exchange occurred on 2026-04-22; project acceptance and follow-up call scheduling occurred on 2026-04-23. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ Timestamps 2026-04-22T20:29:40 and 2026-04-23T22:52:47]

## Commitments

- Haleema confirmed moving forward with Abhijeet on the project and invited him to a follow-up call at 12 PM PST on 2026-04-24 to walk through details and grant system access. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "We'd love to move forward with you on this project... Will you be available for a call tomorrow at 12 PM Pacific?" — 2026-04-23T22:52:47]
- The client confirmed that instantaneous lead routing is required; any hourly or daily batching lag is unacceptable because lead freshness is critical and they want to contact leads immediately upon receipt. [CONFIRMED · fathom/155692198 @ 00:15:37]

## Other

- James (a law firm client) uses a methodology too different from the other clients and will not have the call-tracking/AI-coaching system set up for him. [CONFIRMED · fathom/153250099 @ 00:14:07 / 'The methodology is too different...I would say we don't need this for him']
- Kixie agents do not always add dispositions after calls, particularly on manual dials, so the AI-determined disposition fills that gap. [CONFIRMED · fathom/153250099 @ 00:07:54 / 'Agents don't always add the dispositions...If it's a manual dial, they often don't disposition']

## Sources

- `upwork/room_6fd04d54fde0021b6cb46b320a16bf21` · — · upwork · Upwork thread room_6fd04d54fde0021b6cb46b320a16bf21
- `crm/netfly` · — · crm · CRM numbers — NETFLY
- `fathom/141013120` · 2026-04-24 · fathom · Impromptu Zoom Meeting
- `fathom/142323375` · 2026-04-29 · fathom · Impromptu Zoom Meeting
- `fathom/144628294` · 2026-05-07 · fathom · Impromptu Zoom Meeting
- `fathom/147869825` · 2026-05-19 · fathom · Impromptu Zoom Meeting
- `fathom/153250099` · 2026-06-08 · fathom · Impromptu Zoom Meeting
- `fathom/155692198` · 2026-06-16 · fathom · Impromptu Zoom Meeting
