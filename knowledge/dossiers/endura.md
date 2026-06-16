# Endura Solutions — Dossier

_Compiled 2026-06-17 03:39 IST · 56 client facts (56 trusted) from 9 sources · 73 agency/other-client facts routed to proof-points._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- A follow-up email automation system for a client named 'Sharon Chan' is described as essentially complete; the remaining step is connecting Andrew's email account via make.com. [CONFIRMED · fathom/55259235 @ 00:06:23 / 'It's pretty much done' / 00:06:34 / 'the entire system is in place']
- Andrew mentioned an upcoming email and SMS marketing project, with a client meeting on Monday. [CONFIRMED · fathom/56272654 @ 00:16:33 – 00:16:42]
- Brad's planned automation stack includes: a voice agent with missed-call forwarding, SMS marketing, and email automation. [CONFIRMED · fathom/56778897 @ 00:21:19]
- Carbon Group is a current client that is an accounting firm; the agency is building an AI-powered email automation system for them. [CONFIRMED · fathom/55505679 @ 00:08:04]
- Carbon Group is an existing client being onboarded; Andrew Wang has a call with them the following day to finalize connections. [CONFIRMED · fathom/56009113 @ 00:08:13]
- The agency is working on an AI agent project for a client referred to as 'Carbon Group', involving a knowledge base and categorized AI agent responses. [CONFIRMED · fathom/55259235 @ 00:02:54 / 'carbon group have just uploaded more documents']
- The client project in focus is called 'Better Loans', which involves automations built across Pipedrive and Land Capital systems. [CONFIRMED · fathom/57309940 @ "the two projects i guess the first priority is better loans"]

## The numbers

- The Carbon Group email automation Make scenario contains 115 modules to handle four email categories with a separate OpenAI module for each. [CONFIRMED · fathom/56009113 @ 00:10:00]

## People

- Andrew Wang is the client/account manager; Abhijeet Gandhi and Jay Pokharna are on the agency side. [CONFIRMED · fathom/56778897 @ 00:00:10]
- Andrew Wang plans to call Josh the following day to make him complete the account reauthorization, and is also waiting for a reply from Nathan. [CONFIRMED · fathom/56527447 @ 00:05:19 – 00:05:43]
- Brad is a client (electrician/tradie) Andrew is onboarding; Brad has his phone number printed on his work vehicle, making a number change undesirable. [CONFIRMED · fathom/56778897 @ 00:17:21]
- Clients named Eagle and Josh (associated with 'Better Loans' and 'Carbon Group') have not yet sent required information; Andrew is waiting on them to proceed. [CONFIRMED · fathom/56527447 @ 00:04:03]

## Architecture & systems

- A 'better' (likely 'Bettermind' or similar LMS) integration is in progress: the agency obtained a successful HTTP 200 response from the test API when sending data from a third platform (referred to as 'five drive') but cannot verify the data is being written into the actual account because only the owner holds the production API key. [CONFIRMED · fathom/55015029 @ good news is the test API is working like we were able to get the 200 response now...we need the actual production API key to test that the information is actually being transferred from five drive to]
- A pending automation item for Better Loans is the creation of templated emails triggered by loan stage changes within Land Capital (not Pipedrive). [CONFIRMED · fathom/57309940 @ "he wants templates to be automated so that he can plug and fill certain details when I learn is at a different stage... this is on land, it's not on pipe drive"]
- A proposed enhancement for a client (context suggests the Carbon Group or a related email-automation client) is to store conversation history in Airtable keyed by sender email address, then use AI to retrieve that history as context when generating new email draft responses. [CONFIRMED · fathom/56272654 @ 00:02:58 – 00:04:32]
- An email-watching automation was rebuilt from scratch: it monitors for new incoming emails, stores them in Airtable, and auto-drafts a reply using AI. [CONFIRMED · fathom/55015029 @ I just first like walk you through the entire flow, most of the things are similar as it was before like that watching thing like we watch for the complete new emails, we store them to air table]
- Andrew Wang created his own FAQ document for Carbon Group (a large Q&A document covering topics like SMSF funds) to serve as the AI knowledge base after the client failed to provide materials. [CONFIRMED · fathom/55505679 @ 00:11:00]
- Automations for Better Loans include phone call and SMS workflows originating from Pipedrive, with data passed through to Land Capital. [CONFIRMED · fathom/57309940 @ "Pipe drive, yeah, you've done the good work, is doing those SMS and like transferring... started from pipe drive to land"]
- Brad's current software has no API; his current workflow involves sending a link (e.g. 'update your contact details') manually, and the agency plans to have the voice agent send that link instead. [CONFIRMED · fathom/56778897 @ 00:19:41]
- Carbon Group wants the email automation to run at three specific times per day: approximately 7-something AM, midday, and 3 PM Australian Eastern Standard Time. [CONFIRMED · fathom/56009113 @ 00:12:28]
- Carbon Group's automation setup includes data transfer to Airtable, the client's email ID selected in the automation, and an FAQ document plus a separate knowledge base document uploaded as knowledge base for the AI model. [CONFIRMED · fathom/55505679 @ 00:03:21]
- For Brad, the voice agent will use a missed-call forwarding approach: Brad keeps his existing number; if he doesn't answer, the call is auto-forwarded to a separate number where the AI agent picks up. The caller experiences it as one continuous call with a brief forwarding message. [CONFIRMED · fathom/56778897 @ 00:18:00]
- The AI email agent for Carbon Group is NOT set up as a continuously self-learning/fine-tuning model; the decision was made to build it once with a static knowledge base due to cost and complexity, with the option to add fine-tuning later if the client provides resources. [CONFIRMED · fathom/55505679 @ 00:04:28]
- The Carbon Group automation is currently connected to Andrew Wang's email account; tomorrow he will reconnect the output modules to Carbon Group's own Google Workspace email account. [CONFIRMED · fathom/56009113 @ 00:11:24]
- The Carbon Group build is an automated email draft-reply system: it watches incoming emails, categorises them into four categories, and uses a separate OpenAI model per category to generate draft replies, outputting to Airtable. [CONFIRMED · fathom/56009113 @ 00:09:53]
- The Land Capital API integration is currently using a test/sandbox environment; the production API credentials (API key, client ID, secret ID) have not yet been obtained, so the live Pipedrive-to-Land Capital data push cannot be fully verified. [CONFIRMED · fathom/57309940 @ "the API we have is of the test scenario test progress and the actual thing we have is of production Both of them doesn't match So even if we try to like push the data from five to land capital, it won't go on the actual thing"]
- The agency implemented an email forwarding workaround for Better Loans: since direct forwarding is not possible in their tool, a new draft email is created, the original HTML content and attachments are copied in, and it is sent to the desired recipient. The original email can be moved to trash. [CONFIRMED · fathom/57309940 @ "we are not forward it, but we can see it is a draft. Just copy all the exact things, like exact HTML content from that. So it will look exactly same"]
- The client's existing email automation (pre-rebuild) was built on Google Sheets; that Google Sheets-based automation was left in place because replicating its structure in Airtable required more information and was deemed too difficult. [CONFIRMED · fathom/55015029 @ your automation is still in the Google Sheets. It was kind of difficult to get into the one we wanted, because the structure that you had in Google Sheets was kind of difficult to, you know, replicate the air table]
- The current system does not track emails the owner sends manually (outside of AI-generated drafts); there is no record of the initial email in the database for those cases, so follow-up automation cannot be applied to them without a separate flow. [CONFIRMED · fathom/56527447 @ 00:00:44 – 00:02:00]
- The email automation is scheduled to run once per day at 7:45 a.m. Andrew's local time, per the client's requirement. [CONFIRMED · fathom/55015029 @ at 7.45 a.m. my time is when we launch it, so that's when she wants the automation run per day]
- The email automation system tracks AI-drafted emails via a conversation ID stored in a database sheet; when a draft is created by AI for an inbound email, that conversation ID is logged so the system can track whether the owner subsequently sends that draft. [CONFIRMED · fathom/56527447 @ 00:00:33 – 00:02:00]
- The same phone number will be used for both SMS marketing and as the destination number the missed calls are forwarded to for the voice agent, to avoid Brad managing multiple numbers. [CONFIRMED · fathom/56778897 @ 00:18:51]
- Three automations were built for Better Loans, documented in a combined SOP Google Doc shared via WhatsApp. [CONFIRMED · fathom/57309940 @ "there's a google doc which is the sop for all three automations that we built"]
- Two separate Airtable tables were created: one stores only drafted replies (so they can be monitored for whether a reply was sent and how long they have been waiting), and a second log table stores all email activity (received, drafted, replied) for debugging purposes. [CONFIRMED · fathom/55015029 @ created two different tables were the one where we only store the drafts we created...and then it's another table where we store all the emails like we received we draft or like we receive reply so it's kind of like a lock table]

## Stack & tools

- Better Loans uses Twilio for SMS; a phone number was purchased and added to the client's Twilio account, which was then connected to the Make (formerly Integromat) scenarios. [CONFIRMED · fathom/57309940 @ "I put it on Twilio. It's a better loan... I just connected that number. So, like, now it is switched to that"]
- The Carbon Group email automation is built in Make, uses OpenAI for draft generation, and stores data in Airtable. [CONFIRMED · fathom/56009113 @ 00:09:35]
- The agency uses Make (scenario-based automation tool) as part of its delivery stack for Better Loans. [CONFIRMED · fathom/57309940 @ "I just connected that number... this scenarios where we used to leave"]
- The follow-up email automation for Sharon Chan is built in make.com and spans multiple automations (referenced as automations 121–125 or similar numbered sequences). [CONFIRMED · fathom/55259235 @ 00:07:44 / 'Yes, the folder could follow up...which which number so all one two five guess or one two four']

## Timeline

- A client onboarding call for Better Loans is scheduled for 1:30 PM Australian Eastern Standard Time the following day, with at least one agency team member (Jay or Abhijeet) planned to join. [CONFIRMED · fathom/57309940 @ "One 30 p.m. my time, so Australian Eastern Standard Time... I'm going to have to get you guys to, one of you guys to join the call"]
- The Airtable client-history project is targeted for the following week, contingent on client approval and willingness to pay. [CONFIRMED · fathom/56272654 @ 00:04:54 – 00:05:05]
- The current week was described as onboarding-focused; the following week is also onboarding plus new clients, with more substantive project updates expected then. [CONFIRMED · fathom/56778897 @ 00:23:28]

## Commitments

- Abhijeet Gandhi committed to doing a final review of the Carbon Group build before Andrew Wang's onboarding call the next day. [CONFIRMED · fathom/56009113 @ 00:16:43]
- Abhijeet committed to sending Andrew a list of required knowledge base categories and data needs after the call so Andrew can supply relevant documents. [CONFIRMED · fathom/55259235 @ 00:05:57 / 'I'll share the, you know, these are things we need along with, you know, the lead to B']
- Abhijeet committed to sending Andrew the pricing for Brad's email automation + SMS marketing by the same evening (Monday). [CONFIRMED · fathom/56527447 @ 00:07:25 – 00:07:34]
- Andrew committed to sharing new client/project details asynchronously (via WhatsApp or a shared doc/call recording) rather than exclusively on daily calls, to reduce call frequency. [CONFIRMED · fathom/56778897 @ 00:25:00]
- Andrew plans to obtain the production API key, client ID, and secret ID for Land Capital from the Better Loans client during or before the onboarding call. [CONFIRMED · fathom/57309940 @ "do you want me to get the K from him? Yeah, the production one... just need all those details for the product"]
- Decision made to keep reauthorising the personal Gmail connection weekly for the current client (as she is a friend), but going forward the agency will require new clients to use Google Workspace accounts ($10/month). [CONFIRMED · fathom/55015029 @ for this one because she's a friend as well. I'll just reauthorize it once a week]
- Jay committed to merging two SOP documents into one detailed, final onboarding guide for Better Loans and delivering it before the evening of the call day. [CONFIRMED · fathom/57309940 @ "I will just merge both of them and create a final copy... I will need it before tonight"]
- Jay sent Andrew a document detailing the JSON payload structure and API response for the LMS integration, requesting Andrew pass it to 'Josh' (the platform owner) to confirm all fields are correct before proceeding. [CONFIRMED · fathom/55015029 @ the document I send you I have like put all the information there like what exactly Jason I use and what information I put in all the better...just pass it on with like Josh and he will just go through every parameter]
- The team is committed to fine-tuning/training the Carbon Group AI model today using the uploaded docs, then sharing sample Q&A outputs with Andrew for review next week. [CONFIRMED · fathom/55505679 @ 00:12:07]

## Other

- A project described as the 'Reddit OnlyFans' project is mentioned as incoming; Andrew states he will confirm whether it will proceed. [CONFIRMED · fathom/55259235 @ 00:09:20 / 'The reddit only fans one that's that's incoming. I'll let you know if that's going to be in project']
- A prospect or client named Brad (runs an electrical company) wants email automation plus SMS marketing set up; pricing had not yet been provided to Andrew as of this call. [CONFIRMED · fathom/56527447 @ 00:06:12 – 00:07:28]
- Andrew Wang requested a new flow to handle follow-up automation for emails the owner sends manually, not just AI-drafted ones; Abhijeet confirmed it is not impossible but needs investigation before committing. [CONFIRMED · fathom/56527447 @ 00:02:00 – 00:03:13]
- Josh was sent a Loom tutorial on how to reauthorize/connect his account; as of the call he had read the message at 1 p.m. that day but had not completed the reauthorization, blocking invoice issuance. [CONFIRMED · fathom/56527447 @ 00:04:09 – 00:05:19]
- Nathan (associated with 'Breath House') was the client for an email marketing automation, distinct from Brad's SMS + email request. [CONFIRMED · fathom/56527447 @ 00:07:04 – 00:07:20]
- The end-user ('G' / 'her') is using a personal Gmail account, not Google Workspace; personal Gmail accounts require reauthorisation in Make.com every 7 days. [CONFIRMED · fathom/55015029 @ G is on a personal Gmail account...all personal accounts require seven days react reauthorize into the account]

## Sources

- `crm/endura` · — · crm · CRM numbers — Endura Solutions
- `fathom/55015029` · 2025-04-02 · fathom · Automation Daily Meeting
- `fathom/55259235` · 2025-04-03 · fathom · Automation Daily Meeting
- `fathom/55505679` · 2025-04-04 · fathom · Automation Daily Meeting
- `fathom/56009113` · 2025-04-08 · fathom · Automation Daily Meeting
- `fathom/56272654` · 2025-04-09 · fathom · Automation Daily Meeting
- `fathom/56527447` · 2025-04-10 · fathom · Automation Daily Meeting
- `fathom/56778897` · 2025-04-11 · fathom · Automation Daily Meeting
- `fathom/57309940` · 2025-04-15 · fathom · Abhi
