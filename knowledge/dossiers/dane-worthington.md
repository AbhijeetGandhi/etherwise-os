# Dane Worthington — Dossier

_Compiled 2026-08-21 14:09 IST · 50 client facts (50 trusted) from 5 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- A voice agent system for virtual job interview coaching that validates employee CEO IDs against an Excel sheet and records call details. [CONFIRMED · https://www.loom.com/share/21ecfe69e06043718cc2ac54570eb396 @ 0:00-0:30 'in this video I'll just try to demonstrate the functioning of our voice agent']
- Client already has a WAPI summary prompt configured for internal use that exports call summaries to a Google Sheet column, covering engagement level, misunderstandings, and follow-up flags for staff. [CONFIRMED · fathom/101491448 @ 00:13:07]
- The client (Dane Worthington) is exploring a voice agent system targeting the U.S. prison/correctional system, which is a highly regulated environment where each prison has a telecom provider and calls must be whitelisted/registered with the FCC. [CONFIRMED · fathom/99116769 @ 00:01:04]

## The numbers

- A test call with CEO ID '11' was rejected as not found in the records. [CONFIRMED · https://www.loom.com/share/21ecfe69e06043718cc2ac54570eb396 @ 2:40 'The CEO was 11, which obviously it didn't found']
- A test call with CEO ID '20105' was successfully validated and proceeded to the interview coaching flow. [CONFIRMED · https://www.loom.com/share/21ecfe69e06043718cc2ac54570eb396 @ 5:20 'Thanks, I heard your CEO ID is 20105, is that correct? So, yeah, I get the call, but you can see, like, you know, it basically went on to discuss the, the next flow']
- Client estimates 2–5% of participant email addresses in their system have errors or typos. [CONFIRMED · fathom/101491448 @ 00:11:41]
- Client estimates they could have 5,000–6,000 participant records that would need to be in the COID-to-email mapping sheet. [CONFIRMED · fathom/101491448 @ 00:04:29]
- Coefficient has a 2,000-row export limit from Salesforce to Google Sheets, which is a blocker for the client since their participant count can exceed that number. [CONFIRMED · fathom/101491448 @ 00:02:31]
- Contract: AI Voice Enablement Training Program Development — status ACTIVE. [CONFIRMED · CRM:contracts/40535816]
- Contract: zapier consultation for vapi integration — status ACTIVE. [CONFIRMED · CRM:contracts/40732694]
- Revenue to date (GROSS, billed): $1,110.00 across 7 revenue transactions (2025-05-27 → 2025-12-16). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $997.88 — gross $1,110.00 less $111.00 Upwork service fees, $1.12 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]
- The client's PRD specifies MVP requirements including >90% connection rate and <5% dropped calls; Abhijeet assessed these are achievable with VAPI. [CONFIRMED · fathom/99116769 @ 00:09:43]
- VAPI's standard plan supports up to 10 concurrent calls; upgrading to the enterprise plan removes that limit and allows any number of simultaneous calls. [CONFIRMED · fathom/99116769 @ 00:04:00]

## People

- Staff members manage approximately 25 people on their caseload each and primarily work within Salesforce (not email) for notes, work history, and participant details. [CONFIRMED · fathom/101491448 @ 00:14:14]

## Architecture & systems

- A separate participant-facing email summary prompt will be created and plugged into the automation (using the call transcript via an OpenAI module), distinct from the existing internal staff summary prompt already exported to the Google Sheet. [CONFIRMED · fathom/101491448 @ 00:13:40]
- A voice agent system is composed of three distinct AI model layers: speech-to-text, a text-to-text LLM (deciding what to say next), and text-to-speech — all combined into one pipeline. [CONFIRMED · fathom/99116769 @ 00:02:06]
- Future wishlist item: use Salesforce API to automatically create a 'service entry' object in Salesforce after each call, tagged with the participant's COID, the call type (e.g. mock interview coach), and an AI-generated summary to help case managers support the participant. [CONFIRMED · fathom/101491448 @ 00:15:00]
- HIPAA compliance on VAPI automatically announces on-call recording disclosures, which makes it unsuitable for cold calling but viable for training programs and similar use cases. [CONFIRMED · fathom/99116769 @ 00:03:37]
- Ideal long-term architecture: query Salesforce API by COID at end-of-call to retrieve participant email dynamically, eliminating the need for manual Google Sheet updates. [CONFIRMED · fathom/101491448 @ 00:03:16]
- Make, Zapier, and similar workflow automation tools are not HIPAA compliant and therefore cannot be used to transfer sensitive call data; a custom server hosted on AWS or Google Cloud running proprietary scripts is required instead. [CONFIRMED · fathom/99116769 @ 00:07:47]
- Proposed near-term architecture: client maintains a Google Sheet tab with two columns (COID and participant email), manually updated weekly via CSV export from Salesforce; the automation queries this sheet post-call to look up the participant's email and send a follow-up email. [CONFIRMED · fathom/101491448 @ 00:05:13]
- The AI voice agent will read back the email address on file during the call and ask the participant to confirm it; if they say no, a flag will be written to the end-of-call report Google Sheet column so staff can manually correct it in Salesforce. [CONFIRMED · fathom/101491448 @ 00:10:00]
- The system captures and logs CEO ID, recording URL, call duration, call end reason, transcript, and caller phone number into the Excel sheet after each call. [CONFIRMED · https://www.loom.com/share/21ecfe69e06043718cc2ac54570eb396 @ 4:10 'the CEO ID mentioned, the recording URL, the call duration. Uh, what was the reason for call ended, the transcript and the phone number from which, you know, I made the call as well']
- The system uses an Excel sheet with CEO codes and phone numbers as the validation database. [CONFIRMED · https://www.loom.com/share/21ecfe69e06043718cc2ac54570eb396 @ 0:35 'this is our Excel sheet with the CEO codes that you shared']
- The voice agent validates the provided CEO ID against the Excel sheet using a filter; if no matching row is found, it returns a 'record not found' response and prompts the caller to repeat the ID. [CONFIRMED · https://www.loom.com/share/21ecfe69e06043718cc2ac54570eb396 @ 3:20-3:50 'it noticed that, you know, the CEO ID was not present in the sheet. Basically, it, you know, made a call to this automation where we go through our Excel sheet and see, like, you know, so you can see a filter with the CEO ID code... once the row number is not found, we send the response saying record not found']
- To send post-call emails, the client's Google account (a generic/training Gmail account rather than Dane Worthington's personal account) needs to be connected to Zapier/Make via OAuth; Abhijeet will record a Loom showing how to do this. [CONFIRMED · fathom/101491448 @ 00:09:00]

## Stack & tools

- Client uses Salesforce CRM as their primary system of record for participant data, including email addresses and unique participant IDs called COIDs. [CONFIRMED · fathom/101491448 @ 00:00:59]
- Client uses a tool called Coefficient to automatically sync Salesforce reports to Google Sheets, configurable to refresh every minute, hour, day, or week. [CONFIRMED · fathom/101491448 @ 00:01:11]
- End-of-call automations are built in Make (not Zapier) according to initial check, though Abhijeet noted it doesn't matter which platform is used for the Gmail send step. [CONFIRMED · fathom/101491448 @ 00:08:25]
- End-of-call reports from WAPI are sent to Google Sheets via Zapier webhooks [CONFIRMED · https://www.loom.com/share/a72a82465e5b4d33b7ea66d477a3a76a @ 0:00:15 'the information that we put into the Google Sheet is called as end of call report, which is something that WAPI provides']
- For HIPAA-compliant data storage outside VAPI, the agency considers Airtable (HIPAA-compliant, good UI) or Supabase (secure but less visual) as database options. [CONFIRMED · fathom/99116769 @ 00:07:00]
- OpenAI module is conditionally used in Zapier workflows when WAPI cannot provide required data (e.g., staff full names) [CONFIRMED · https://www.loom.com/share/a72a82465e5b4d33b7ea66d477a3a76a @ 0:03:15 'this open AM module is not required every time' and 'this particular agent needs the full name of the staff. This is something that WebP cannot provide']
- Phone numbers for VAPI would likely be provisioned via Twilio; VAPI supports importing numbers from multiple providers. FCC registration compatibility with Twilio for prison telecom whitelisting is uncertain and needs investigation. [CONFIRMED · fathom/99116769 @ 00:05:45]
- Two Make.com automations are configured: one for checking valid employee CEO IDs and one for storing call details. [CONFIRMED · https://www.loom.com/share/21ecfe69e06043718cc2ac54570eb396 @ 1:05 'we have two automations in make.com. One is for the valid employee for checking valid employee. And then another one is for like, you know, actually storing. The details of the calls']
- VAPI is being evaluated as the primary platform for the client's voice agent instead of building from scratch; it supports multiple languages, concurrent calls, and HIPAA-compliant call settings. [CONFIRMED · fathom/99116769 @ 00:02:52]
- WAPI summaries are noted as not highly accurate; the preferred fallback is an OpenAI module in the automation that takes the full transcript and generates the summary. [CONFIRMED · fathom/101491448 @ 00:12:55]
- WAPI webhook payload includes transcript, assistant name, recording URL, and duration among other call data [CONFIRMED · https://www.loom.com/share/a72a82465e5b4d33b7ea66d477a3a76a @ 0:02:45 'everything, from the transcript to everything we get here' and 0:05:30 'assistant name recording URL duration']
- WAPI webhook sends end-of-call report data when a call ends for an agent [CONFIRMED · https://www.loom.com/share/a72a82465e5b4d33b7ea66d477a3a76a @ 0:02:00 'whenever a call ends for this particular agent, it will send the information to this Zap']
- Webhook setup process: create a Zapier app, set trigger as webhook, use 'catch hook' to receive information, and copy the resulting URL to WAPI's server messages configuration [CONFIRMED · https://www.loom.com/share/a72a82465e5b4d33b7ea66d477a3a76a @ 0:00:45 'what we need to do is create a webbook URL' through 0:02:30 'put it in WAPI']
- When using OpenAI module, the call transcript is passed to extract specific fields like staff full name via a structured JSON prompt [CONFIRMED · https://www.loom.com/share/a72a82465e5b4d33b7ea66d477a3a76a @ 0:03:45 'we have given, like, you know, the call transcript to it' and 'you will be provided with the transcript of the call and your task is to face the following values']
- Zapier spreadsheeet rows map values from webhooks (assistant name, recording URL, duration) and/or OpenAI outputs (staff full name) to Google Sheet columns [CONFIRMED · https://www.loom.com/share/a72a82465e5b4d33b7ea66d477a3a76a @ 0:04:45 'in the spreadsheet row, you can see we just map out all these values' through 0:05:15 'for all the others, it is from the webhook only']

## Timeline

- The client plans to put the project out to bid in approximately the next couple of weeks from the call date. [CONFIRMED · fathom/99116769 @ 00:11:05]

## Commitments

- Abhijeet committed to investigating the Salesforce API for both reading (fetching email by COID) and writing (creating service entry objects), and will look at the documentation to determine feasibility. [CONFIRMED · fathom/101491448 @ 00:16:10]
- Dane committed to creating a new Google Sheet tab named 'IDs and emails' with columns for COID, participant email, and optionally a staff email column, and will populate it with an initial export. [CONFIRMED · fathom/101491448 @ 00:07:07]
- Dane committed to providing Abhijeet with a prompt for the participant-facing email summary to be plugged into the automation. [CONFIRMED · fathom/101491448 @ 00:13:40]
- The agency stated the voice agent validation and logging functionality is set up and ready; further customization/tweaking of the interview coaching flow can be done after this is confirmed. [CONFIRMED · https://www.loom.com/share/21ecfe69e06043718cc2ac54570eb396 @ 6:05 'I haven't, like, you know, tweaked a lot more into, like, you know, the process. Because, yeah, I think we, once this functionality is set up, then we can obviously, you know, uh, do that, uhm, and you can also do it on, on your end as required']

## Other

- AI voice agents have difficulty accurately capturing email addresses spoken aloud, especially with non-standard accents or letter spellings (e.g. double-E misheard), making spoken email capture unreliable. [CONFIRMED · fathom/101491448 @ 00:02:56]
- Abhijeet previously worked with a different client approximately 6–8 months prior attempting to build a custom voice agent system from scratch without VAPI. A team of 3 people including a senior AI engineer formerly at Google worked on it for 6–8 months and still did not reach VAPI's output quality before the project was cancelled. [CONFIRMED · fathom/99116769 @ 00:08:20]
- Prison calls are collect calls: the receiving party pays, and the call begins with an automated prompt asking if they accept the charges. The voice agent would need to programmatically accept this prompt and have a billing account tied to the receiving number. [CONFIRMED · fathom/99116769 @ 00:05:00]

## Sources

- `crm/dane-worthington` · — · crm · CRM numbers — Dane Worthington
- `loom/21ecfe69e06043718cc2ac54570eb396` · 2025-05-12 · loom · Videos | Library | Loom - 13 May 2025
- `loom/a72a82465e5b4d33b7ea66d477a3a76a` · 2025-06-18 · loom · Setting Up End-of-Call Reports with Google Sheets and Zapier
- `fathom/99116769` · 2025-11-04 · fathom · Impromptu Zoom Meeting
- `fathom/101491448` · 2025-11-13 · fathom · Impromptu Zoom Meeting
