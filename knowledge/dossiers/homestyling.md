# Homestyling — Dossier

_Compiled 2026-08-14 00:15 IST · 74 client facts (72 trusted) from 7 sources · 62 agency/other-client facts routed to proof-points._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- Client operates two different businesses: Homestyling.se and Inredit [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'we have two different businesses, like the Homestylingse, Styling and Inredit']
- The client (referred to via homestyling.se) runs two businesses: a home styling/staging services business and a courses/interior design certification business. [CONFIRMED · fathom/141038392 @ 00:07:32 - 'They have a home styling, staging services, courses, such two.']
- The client is running Facebook/Meta ads, likely targeting Swedish-speaking users (Airtable fields are in Swedish), and the goal is to close the feedback loop so Meta knows which leads actually purchased, improving ad targeting. [CONFIRMED · fathom/150793211 @ 00:02:11]
- The client's automation involves three main scenarios: (1) incorporation/registration (general name), (2) qualified lead, and (3) checkout completed. [CONFIRMED · fathom/151806332 @ 00:00:15]
- The project involves companies named 'The Styling Group' and 'Home Styling'; The Styling Group creates a customer record (named 'AB') in Fortnox and invoices JV as a fixed customer. [CONFIRMED · fathom/142391273 @ 00:17:49 – 00:18:00]

## The numbers

- A test run of the automation took 23 seconds to complete. [CONFIRMED · fathom/142391273 @ 00:45:17]
- A test transaction value of 23681 was observed and confirmed as correct during a test run. [CONFIRMED · fathom/142391273 @ 00:44:06]
- Client receives more than hundreds of leads every week [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'we have more than hundreds of leads coming every, every week']
- Contract: Airtable + Make Automation Specialist / Technical Coordinator (Ongoing Role) — status ACTIVE. [CONFIRMED · CRM:contracts/43475182]
- Revenue to date (GROSS, billed): $2,755.00 across 10 revenue transactions (2026-04-26 → 2026-08-05). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $2,480.94 — gross $2,755.00 less $271.50 Upwork service fees, $2.56 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]
- The Meta lead conversation logging scenario runs more than 5,000 times per month. [CONFIRMED · fathom/141038392 @ 00:14:01 - 'Log Conversation, Meta, Create Lead, 5000 plus Runs a Month']
- The team reported sending 11 emails the previous night (last night) during testing of the automation. [CONFIRMED · fathom/142391273 @ 00:47:43]

## People

- Abhijeet Gandhi is the agency-side lead managing this client engagement. Jay Pokharna is a team member also involved in reviewing the client setup. [CONFIRMED · fathom/141038392 @ 00:00:05 - speakers identified as Jay Pokharna and Abhijeet Gandhi throughout the call]
- The call participants are Abhijeet Gandhi and Jay Pokharna. [CONFIRMED · fathom/142391273 @ 00:01:02]

## Architecture & systems

- A filter needs to be added to the Make InReader scenario so that HomeStyling leads do not go through the standard InReader flow — HomeStyling should be excluded via a condition/filter. [CONFIRMED · fathom/151806332 @ 00:22:13]
- An automation is being built to trigger when a lead's status changes to 'Homestyling KOPT' (homestyling purchased) in Airtable, which will then fire a Meta Conversions API (CAPI) event to send purchase conversion data back to Meta/Facebook. [CONFIRMED · fathom/150793211 @ 00:01:33]
- Each HubSpot deal created on qualify lead includes a booking link field that contains the product ID, used to match deals in the checkout completed scenario. [CONFIRMED · fathom/151806332 @ 00:04:49]
- Going forward, whenever a qualify lead event arrives, it will always include a case ID and a client company ID. The case ID is also the incorporation ID. [CONFIRMED · fathom/151806332 @ 00:09:00]
- Make.com Scenario 1-of-9 polls the Airtable Intake base for new Meta Lead records every 5 minutes, creates the lead record, sends an SMS via Brevo linking to the Alma chatbot, and queues a welcome email via an Airtable automation. [CONFIRMED · fathom/141038392 @ 00:22:38 - 'make scenario one of nine polls every five minutes, intake base, meta lead request create, SMS trigger']
- Provider company ID represents partnership IDs (e.g. 'konto', 'whole'), not the end client. It is used to identify which partner sent the deal, not for matching logic. It will be written to HubSpot for future use. [CONFIRMED · fathom/151806332 @ 00:18:00]
- Some products only have company ID and no case ID (e.g. when the company already exists and only a small service is needed). The system must handle cases where either or both IDs may be empty. [CONFIRMED · fathom/151806332 @ 00:19:01]
- The HomeStyling SMS scenario constructs a URL that includes form name, lead ID, email, and phone fields. A URL formatting issue was identified where appended field values were not appearing correctly in the generated URL. [CONFIRMED · fathom/151806332 @ 00:29:41]
- The Stripe/payment checkout integration maps: customer email, product name (service description), amount (parsed and multiplied by 100). [CONFIRMED · fathom/142391273 @ 00:43:32]
- The Zapier setup includes two Zaps corresponding to two clients; the team noted these likely do not need changes but flagged them for review. [CONFIRMED · fathom/142391273 @ 00:04:12]
- The automation sends an email when an invoice is created; there is a CC requirement on the email send step. [CONFIRMED · fathom/142391273 @ 00:34:49]
- The automation workflow includes the following steps: Search for project, found project, validate person number, create customer, create invoice, download invoice, upload invoice (to Google Drive and/or Airtable), get invoice share link, and send email when invoice is created. [CONFIRMED · fathom/142391273 @ 00:19:02]
- The checkout completed scenario's matching logic must be updated to search by case ID and company ID instead of booking link / product ID. However, the booking link search must be kept temporarily for already-created deals that do not yet have case ID or company ID populated. [CONFIRMED · fathom/151806332 @ 00:13:00]
- The client has a HomeStyling-related SMS and email sending scenario in Make. This scenario is condition-based (not form-based) and sends SMS directly from Make. [CONFIRMED · fathom/151806332 @ 00:20:00]
- The client has an Airtable automation (separate from Make.com) that triggers when a record matches in the Meta Leads table and sends a welcome email to the new lead. [CONFIRMED · fathom/141038392 @ 00:24:01 - 'last, welcome email, triggers on record matches in meta leads, main base... this is editable automation that he has given']
- The client instructed the team NOT to upload the invoice to Drive (editable download not required) and NOT to apply tax reduction. [CONFIRMED · fathom/142391273 @ 00:19:02]
- The client's Airtable main base contains 58 tables shared across projects. [CONFIRMED · fathom/141038392 @ 00:08:22 - 'shared infrastructure, table, main base, 58 tables']
- The client's ClickUp workspace is being set up with separate folders per client, with lead priority logic: after 3 unanswered calls the lead moves to medium priority, and after a further 3 unanswered calls it moves to low priority. [CONFIRMED · fathom/141038392 @ 00:02:00 - 'तीन बार call होने के बाद इत automatically moves to medium priority, और थीन बार all call हुआ, और फिर भी कुछ नहीं है, तो it automatically moves to low priority']
- The client's backend platform sends webhook events whenever any product purchase process is initiated on their platform. The webhook fires very frequently — every minute. [CONFIRMED · fathom/151806332 @ 00:02:02]
- The client's existing Make.com setup has quality issues: automation scenarios are not consistently named in English, there are unused Calendly integration modules, redundant 'set variables' modules used for logging, and some automations appear to serve no active purpose. [CONFIRMED · fathom/141038392 @ 00:13:00 - 'naming, rename या फिर error handler बगेरे, पर बाकी overall setup क्या पूरा ही हग क्या है, Calendly Booking पर है अभी बहुत issues... it's never getting used']
- The client's home styling business has a 9-step process for project management that runs through Make.com scenarios, covering stages from new project creation through to invoice generation and Google Drive folder creation (with 7 subfolders auto-created per project). [CONFIRMED · fathom/141038392 @ 00:19:02 - 'SRIG is a process to 9 step according to Homestead Make Scenario' and 00:20:19 - 'create seven subfolders']
- The email template was changed the previous night (HTML edits), requiring all variable mappings to be re-mapped in the automation; Google Sheet column arrangement was also changed by the client, causing mapping issues. [CONFIRMED · fathom/142391273 @ 00:39:00]
- The existing matching logic uses booking link / product ID (e.g. incorporation ID, business address ID) to match deals. This logic is being replaced with case ID and client company ID matching. [CONFIRMED · fathom/151806332 @ 00:09:00]
- The home styling business has nine active Make.com scenarios. Inbound lead sources include Facebook Lead Ads, Alma chatbot captures, and direct sign-up for the courses business; and phone, email, Mäklare (broker), referral, and direct inquiries for the home styling business. [CONFIRMED · fathom/141038392 @ 00:07:38 - 'inbound leads are Facebook lead forms, Alma chatbot captures, and direct sign-up... phone email to sales, Maclar broker, referral, and direct inquiries']
- There are two types of invoices handled: FNF (Full and Final Settlement) and Factoring. If the invoice is FNF, the flow just downloads and uploads it without sending an email again. If it is not FNF (Factoring), an invoice email is also sent. [CONFIRMED · fathom/142391273 @ 00:27:01]
- There is a date-calculation issue in the automation: the logic should compute '30 days minus days elapsed since a given date' (e.g., 30 minus 16 = 14 days remaining), but it is returning minus 1 instead of the correct value; the calculation section was changed four to five times without resolution. [CONFIRMED · fathom/142391273 @ 00:49:05]
- Two new HubSpot deal fields need to be created: case ID and company ID (provider company ID field also needs to be added). Total of four new field values to be added to HubSpot deals. [CONFIRMED · fathom/151806332 @ 00:17:06]
- When a 'qualify lead' webhook event fires, a Make (formerly Integromat) scenario creates a deal in HubSpot. This is the most important event for the client. [CONFIRMED · fathom/151806332 @ 00:03:00]

## Stack & tools

- Client uses Airtable as the database for lead management [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'Airtable is getting updated, with whatever the message that we send, the reply that we got from the customer']
- Client uses Brevo to send immediate messages to new leads [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'this client uses Brevo to send out the message']
- Client uses Meta Ads heavily with multiple Meta Lead forms running simultaneously [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'This is one of the clients that I've worked with and they use meta ads heavily']
- Client uses OpenAI assistant for generating lead responses based on business knowledge [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'we use the OpenAI, we have this assistant created, which knows all about the business and knows how to send the response']
- Client uses Talk.to as a unified messaging platform to connect email, SMS, and WhatsApp [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'Talk.to is a platform, basically, that we use, for example, this is the platform that we use to connect, you know, the different sources, whether it is email, SMS']
- Fortnox is being used as the invoicing/accounting platform in the automation being built. [CONFIRMED · fathom/142391273 @ 00:02:38]
- The agency uses Claude (AI / 'Brave') for generating Mermaid diagram documentation of client automation flows, outputting markdown (.md) files with Mermaid code. [CONFIRMED · fathom/141038392 @ 00:09:00 - 'mermaid code रहता है, तो you can copy paste here... md file में, तो mermaid language होता है']
- The agency uses Fathom for call recording and transcription. [CONFIRMED · fathom/141038392 @ 00:03:25 - 'So we're just talking about Fathom' and source reference 'fathom/141038392']
- The automation is being built in Make.com using the Facebook Conversions API 'Send Event' module, connected to the client's existing Airtable and Meta Ads account. [CONFIRMED · fathom/150793211 @ 00:02:33]
- The client project involves integrations with DHL, Assembly API, ClickUp, Fortnox, Go High Level, Google Sheets, SMDI, Zapier, and Airtable. [CONFIRMED · fathom/142391273 @ 00:02:19 – 00:05:00]
- The client uses Airtable as their database, with at least three separate bases: a Messages and Leads base, an Intake base, and a Projects base. [CONFIRMED · fathom/141038392 @ 00:18:01 - 'three bases: meta leads base, messages base, projects base... Intake base is a separate base']
- The client uses Brevo (formerly Sendinblue) for sending SMS messages to new meta leads. [CONFIRMED · fathom/141038392 @ 00:18:01 - 'बेज़ रहा है using Brevo API for SMS delivery, update meta lead table']
- The client uses Fortnox for invoicing in their home styling business, but invoicing for the courses business has not yet been automated. [CONFIRMED · fathom/141038392 @ 00:05:00 - 'fortnox वगेरे, सब चीज़े, use करके, मतलब invoicing वगेरे, तक सब setup किया हुआ है, पर जो यह दूसरा courses का business है, इसमें अभी तक invoicing वगेरे का कुछ automate नहीं हुआ है']
- The client uses GHL (GoHighLevel) with separate pipelines created for each client. [CONFIRMED · fathom/141038392 @ 00:02:00 - 'हर एक client के लिए यह separate GHL pipeline बनाते हैं']
- The client uses Klarna, Swish, and other payment methods which are currently being processed manually rather than automated into Fortnox. [CONFIRMED · fathom/141038392 @ 00:08:13 - 'payment, abhijeetg, klarna, skrivs, which were getting these all manually, not it, inmate, Fort Knox, Tom's plant, second, which we have to do']
- The client uses Make.com (formerly Integromat) as their primary automation platform, with multiple active scenarios for the home styling business. [CONFIRMED · fathom/141038392 @ 00:08:26 - 'make.com, home, slack, BA, google driver, project folders, google calendar per staff']
- The client uses Slack, Google Drive (for project folders), Google Calendar (for staff scheduling), and Alma chatbot (Talk.to chat widget) as part of their shared infrastructure. [CONFIRMED · fathom/141038392 @ 00:08:26 - 'make.com, home, slack, BA, google driver, project folders, google calendar per staff']
- The client uses Zapier, with separate folders created per client inside their Zapier account. [CONFIRMED · fathom/141038392 @ 00:02:00 - 'हर एक client के लिए यह separate Zapier का folder बनाते हैं']
- The client's automation stack includes Make (formerly Integromat), HubSpot, Brevo (for email/SMS sending), and InReader. [CONFIRMED · fathom/151806332 @ 00:26:00]
- The client's product catalogue includes: notarization, incorporation, translation, business address, registration, holding, GBR incorporation, and tax registration. [CONFIRMED · fathom/151806332 @ 00:02:19]
- The payment/checkout integration involves specifying a payment method via API call when creating a checkout session; a specific payment method needs to be removed or disabled from the checkout UI per client instruction. [CONFIRMED · fathom/142391273 @ 00:05:35]
- The automation is built in Make (formerly Integromat), referencing routers, routes, modules and conditions visible during the session. [_INFERRED_ · fathom/142391273 @ 00:22:00 – 00:24:00]

## Timeline

- Client handles over 100 leads within 3 hours on a typical day (June 20) [CONFIRMED · https://www.loom.com/share/6703f8e402264ab796597e321cc754c4 @ 'on the June 20, which is like just three hours ago, there are like more than you know a hundred leads already']

## Commitments

- Abhijeet (agency) will notify the client (mish) once the Make.com / Meta CAPI setup is complete; agency already has access to the client's Meta Ads platform. [CONFIRMED · fathom/150793211 @ 00:05:53]
- Abhijeet committed to: (1) creating the four new HubSpot deal fields, (2) mapping case ID and company ID in the qualify lead scenario, (3) updating checkout completed matching logic, (4) sending a sample SMS and email output to Jay for review before enabling the HomeStyling scenarios. [CONFIRMED · fathom/151806332 @ 00:32:00]
- After the current session, the team plans to review the HubSpot (intake) flow and an onboarding call is mentioned as a next step. [CONFIRMED · fathom/151806332 @ 00:32:00]
- The agency plans to conduct a full audit of the client's existing Make.com scenarios, document the logic, and create a proper plan with time estimates before starting rebuild work. [CONFIRMED · fathom/141038392 @ 00:03:31 - 'will analyze all proper plan... will you proper plan, time will we take']
- The agency plans to involve team member Yogita on Zapier-related tasks for this client, as she has good knowledge of Zapier. [CONFIRMED · fathom/141038392 @ 00:06:00 - 'yogita को भी लेंगे थोड़ा... Zapier का तो उसको दे सकते हैं, उसको काफी अच्छा idea है']
- The agency plans to rename all existing Make.com automation scenarios to proper English names to make management easier. [CONFIRMED · fathom/141038392 @ 00:08:42 - 'can we change those to English proper... automations यह करेंगे, नाम तोड़े change होंगे, इंग्लिश में होंगे, तो अपने को manage करने easy पड़ेगा']

## Other

- During live setup a technical error occurred when connecting the Facebook Conversions API module in Make.com; Abhijeet planned to retry after the call. [CONFIRMED · fathom/150793211 @ 00:05:32]
- The client's business appears to be based in Stockholm, Sweden, and operates a homestyling service sold via Meta ads. [_INFERRED_ · fathom/150793211 @ 00:04:25]

## Sources

- `crm/homestyling` · — · crm · CRM numbers — Homestyling
- `fathom/141038392` · 2026-04-24 · fathom · Impromptu Google Meet Meeting
- `fathom/142391273` · 2026-04-30 · fathom · Impromptu Google Meet Meeting
- `fathom/150793211` · 2026-05-29 · fathom · Impromptu Google Meet Meeting
- `fathom/151806332` · 2026-06-03 · fathom · Impromptu Google Meet Meeting
- `loom/2558f580bf3846eebb9095db4217c70b` · 2026-06-10 · loom · Senior Automation Engineer, Make, GoHighLevel, OpenAI
- `loom/6703f8e402264ab796597e321cc754c4` · 2026-06-19 · loom · Meta Ads Lead Response Automation, Built and Proven
