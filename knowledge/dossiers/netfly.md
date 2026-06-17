# NETFLY — Dossier

_Compiled 2026-06-18 02:40 IST · 11 client facts (11 trusted) from 2 sources · 5 agency/other-client facts routed to proof-points._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- Agent-reported call outcomes in GHL are unreliable — agents forget to tag, tag wrong, or skip it when the call didn't convert, resulting in an untrustworthy outcome column. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "the agent-reported call outcomes in GHL aren't reliable. Agents forget to tag, tag wrong, or skip it when the call didn't convert."]
- Client (NetFly) uses GoHighLevel as CRM, Kixie as dialer, Zapier/webhooks for data routing, Speed.ai (or similar) for call transcription, and Google Sheets for reporting and logging. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ CURRENT STACK section of job description]
- NetFly already has a working Kixie → GHL integration where call activity is synced into GHL and call data is appended into the Notes section of each contact. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ Current Setup (Already Working) section of job description]
- NetFly's sign rate for law firm clients is calculated off connections only, not total leads routed, which understates performance and makes reporting to clients harder. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "NetFly's sign rate for law firm clients is calculated off connections only, not total leads routed."]

## The numbers

- Revenue to date: $2,761 across 5 earning transactions (2026-05-03 → 2026-05-31). [CONFIRMED · CRM:transactions]

## People

- Client-side contacts are Haleema U. (primary contact who initiated the hiring) and Sean (introduced on the post-call recap, likely a team member or stakeholder). [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "Hi Haleema, and welcome Sean." — 2026-04-22T22:40:00]

## Architecture & systems

- Proposed 4-phase build: Phase 1 – audit Kixie→GHL flow and restructure data from Notes into custom fields; Phase 2 – build Speed.ai transcription pipeline with contact matching and dual-push to GHL and Sheets; Phase 3 – design centralized Google Sheet schema with dedup and error handling via Zapier; Phase 4 – testing, edge case handling, documentation. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ A3 answer in initial proposal and recap message dated 2026-04-22T22:40:00]
- Proposed solution: Kixie CI Summary Webhook fires after every call containing AI summary, sentiment score, conversation strength, and keyword/phrase matches; payload is run through GPT for structured outcome classification (qualified, not qualified, wrong number, callback, signed) and pushed into GHL as a system-generated field alongside the agent-reported field. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "Kixie fires a CI Summary Webhook after every call that includes an AI summary, sentiment score, conversation strength, and keyword/phrase matches."]
- The system-generated GPT outcome classification field in GHL will sit alongside the agent-reported field rather than replacing it, enabling comparison over weeks to identify mis-tagging patterns and where sign rate is leaking. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "push the result into GHL as a system-generated field, sitting alongside the agent-reported field rather than replacing it"]

## Timeline

- Initial outreach and proposal exchange occurred on 2026-04-22; project acceptance and follow-up call scheduling occurred on 2026-04-23. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ Timestamps 2026-04-22T20:29:40 and 2026-04-23T22:52:47]

## Commitments

- Haleema confirmed moving forward with Abhijeet on the project and invited him to a follow-up call at 12 PM PST on 2026-04-24 to walk through details and grant system access. [CONFIRMED · upwork/room_6fd04d54fde0021b6cb46b320a16bf21 @ "We'd love to move forward with you on this project... Will you be available for a call tomorrow at 12 PM Pacific?" — 2026-04-23T22:52:47]

## Sources

- `upwork/room_6fd04d54fde0021b6cb46b320a16bf21` · — · upwork · Upwork thread room_6fd04d54fde0021b6cb46b320a16bf21
- `crm/netfly` · — · crm · CRM numbers — NETFLY
