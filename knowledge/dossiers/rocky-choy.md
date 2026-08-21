# Rocky Choy — Dossier

_Compiled 2026-08-21 14:09 IST · 19 client facts (19 trusted) from 2 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- Cross-client example: Abhijeet built and has been maintaining for ~6 months a complex Make scenario for a coaching business that uses Calendly, Airtable (as CRM), and Slack notifications, routing bookings to different team members across multiple calendars. Volume as of ~June 10 was around 2,025 bookings. [CONFIRMED · fathom/153768286 @ 00:11:56]
- The client (Rocky/Richard) runs a photo company and is also a photographer. He uses Calendly as a booking system connected to Google Calendar via a Make scenario built from a template. [CONFIRMED · fathom/153768286 @ 00:03:59]
- The client's current Make scenario has a duplicate-booking bug: re-running the scenario creates double entries in Google Calendar, and edits in Calendly also result in duplicated events in Google Calendar. [CONFIRMED · fathom/153768286 @ 00:01:38]

## The numbers

- Abhijeet estimates the fix will take approximately 3–5 hours: ~2 hours to build and ~1–2 hours for testing edge cases. [CONFIRMED · fathom/153768286 @ 00:10:50]
- Abhijeet states he has built more than 300 automations using Make over the past two years. [CONFIRMED · fathom/153768286 @ 00:14:44]
- Abhijeet's hourly rate is $40/hour, putting the estimated project cost at approximately $200. [CONFIRMED · fathom/153768286 @ 00:11:19]
- Contract: Make.com Expert Needed for Calendly → Google Calendar Integration — status CLOSED. [CONFIRMED · CRM:contracts/43941050]
- Revenue to date (GROSS, billed): $309.00 across 2 revenue transactions (2026-07-03 → 2026-07-10). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $277.80 — gross $309.00 less $30.90 Upwork service fees, $0.30 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]

## People

- The client has a developer who is on holiday for one month, which is why he is seeking an external automation expert. [CONFIRMED · fathom/153768286 @ 00:02:19]
- The client has freelance photographers he manually forwards bookings to; he does not yet have an automated flow to distribute bookings to freelancers. [CONFIRMED · fathom/153768286 @ 00:03:59]

## Architecture & systems

- Abhijeet's approach: duplicate the client's existing Make scenario first, work on the copy, and request access to the Calendly booking link and Google Calendar account for testing. [CONFIRMED · fathom/153768286 @ 00:08:56]
- Calendly's unique event ID persists across all event state changes (created, rescheduled, deleted), making it a reliable identifier to look up and match events in Google Calendar. [CONFIRMED · fathom/153768286 @ 00:06:50]
- Key technical risk identified for the Calendly-to-Google Calendar integration: timezone mismatches across three systems (Calendly, Make, Google Calendar) when formatting datetime values. [CONFIRMED · fathom/153768286 @ 00:07:45]
- Proposed fix for the client's Make scenario: add routers for three Calendly event types (created, cancelled, rescheduled), then check if the event already exists in Google Calendar using the Calendly event ID stored as metadata. If it exists, update it; if not, create a new event. [CONFIRMED · fathom/153768286 @ 00:04:35]

## Stack & tools

- The client's current automation stack for this project is Make (formerly Integromat), Calendly, and Google Calendar only — no additional CRM or data storage is required. [CONFIRMED · fathom/153768286 @ 00:13:43]

## Commitments

- Abhijeet will test the scenario independently first, then conduct a joint test with the client before going live with real customers. [CONFIRMED · fathom/153768286 @ 00:10:05]
- The client intends to evaluate at least one other developer before making a hiring decision, and will inform Abhijeet of the outcome. [CONFIRMED · fathom/153768286 @ 00:15:49]

## Notable quotes

- Client on automation demand: 'It's a good business, automation, I think. It's a never-ending story.' [CONFIRMED · fathom/153768286 @ 00:15:22]

## Sources

- `crm/rocky-choy` · — · crm · CRM numbers — Rocky Choy
- `fathom/153768286` · 2026-06-10 · fathom · Impromptu Zoom Meeting
