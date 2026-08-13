# Mustafa Ahmed — Dossier

_Compiled 2026-08-14 00:15 IST · 77 client facts (76 trusted) from 6 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- Client (Mustafa Ahmed) runs a medical/aesthetic practice and wants to automate tracking of leads, consultations, bookings, revenue, and expenses, as well as implement AI tools including ManyChat for DMs and a before-and-after photo enhancement tool. [CONFIRMED · fathom/158862212 @ 00:00:59]
- The agency is working on a data infrastructure and dashboard project for a medical/bariatric practice client (Mustafa Ahmed), pulling leads, procedures, and revenue data into Airtable and building a reporting dashboard on top of it. [CONFIRMED · fathom/169787214 @ 00:00:48]

## The numbers

- Agency runs more than 300 automations within their own Make.com organization, demonstrating platform scale capability. [CONFIRMED · fathom/159260924 @ 00:04:54 – 'we have organization where we are running like more than 300 automations. Like we have built 300 automations. There is no issue with it.']
- Contract: AI automation architect(Airtable + make.com + Gohigh level + Evoto — status ACTIVE. [CONFIRMED · CRM:contracts/43986743]
- In July, approximately $68k was collected with an additional $106k in payments still in process, totalling roughly $174k in procedures billed for that month. [CONFIRMED · fathom/169787214 @ 00:07:18]
- In May 2026 (as shown on the dashboard), total procedure value recorded was approximately $178k, with payments collected over subsequent months. [CONFIRMED · fathom/169787214 @ 00:05:39]
- Over the last 90 days, total cash collected shows approximately $390,000 (client's words: '390 that we collected in the last three months'). [CONFIRMED · fathom/169787214 @ 00:04:57]
- Over the last two months, the client collected $56,000 in cash payments from 16 patients (cash only, credit card payments not yet captured). [CONFIRMED · fathom/169787214 @ 00:04:47]
- Project is structured as three milestone phases at $1,500 each, totalling $4,500. [CONFIRMED · fathom/158862212 @ 00:13:22 — 'three milestone of $1,500 each, which was like three weekly payments of $1,500 and three phases']
- Revenue to date (GROSS, billed): $1,500.00 across 1 revenue transaction (2026-07-30 → 2026-07-30). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $1,348.50 — gross $1,500.00 less $150.00 Upwork service fees, $1.50 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]

## People

- Client contact is Mustafa Ahmed; agency contact (contractor) is Abhijeet Gandhi. [CONFIRMED · fathom/158862212 @ 00:00:03]
- Client contact is Mustafa Ahmed; agency contact on the call is Abhijeet Gandhi, who also has a teammate being granted access. [CONFIRMED · fathom/170225462 @ 00:00:08]
- MarketMade is the client's external website and marketing company; they manage the website and Google Ads and were historically responsible for sending leads into Prosper. [CONFIRMED · fathom/163315168 @ 00:05:40 – 00:05:55]
- Serena is a client-side team member who manages all consulting operations and has knowledge of how leads flow into Prosper and Recura. [CONFIRMED · fathom/163315168 @ She runs all the consulting. He wants to know how leads come in into Prosper and into Recur.]

## Architecture & systems

- Agency approach: keep client's existing workflow unchanged initially; integrate tools to read data, build dashboards, and establish metrics before layering in AI automation. [CONFIRMED · fathom/158862212 @ 00:01:41 — 'we want to keep your current workflow as is…we are just preparing some dashboards, some tracking']
- Agency uses a shared agency email address for receiving platform invites from clients (Make.com, Airtable, etc.) rather than individual developer accounts. [CONFIRMED · fathom/159260924 @ 00:02:15 – 'I will share one of the email IDs with you that we use at our agency, where you can send all the invites to.']
- Agency's approach favors no-code/low-code platforms like Make.com over building custom platforms, citing time savings and existing integration capabilities. [CONFIRMED · fathom/159260924 @ 00:05:04 – 'When he says like a custom platform, that's going to be a totally different build... while make.com already does that, for example. So, you know, we say a lot of time there.']
- Before Recura, all leads came from a website/marketing company (MarketMade) which sent them directly to Prosper. [CONFIRMED · fathom/163315168 @ 00:05:18 – 00:05:40]
- Goal is to make Airtable the single source of truth for all leads, including attribution data (which ad source drove each lead to the website/form). [CONFIRMED · fathom/163315168 @ we want to get like the airtight people to become a one source of truth... we still need to know from which source they came to website]
- Lead sources into Prosper include: (1) website form submissions, (2) Google Ads (via Recura and/or MarketMade), and (3) Unbounce landing pages (likely Facebook ads, expected to go away next month). Recura only receives Google Ads leads, not Unbounce leads. [CONFIRMED · fathom/163315168 @ 00:09:18 – 00:09:53]
- Recura does not currently offer API access; it was recently acquired by another company whose integration priorities have changed, blocking a Recura-Prosper sync. [CONFIRMED · fathom/163315168 @ we cannot set up that they are not able to set up the API access... They got acquired by some other company, and now they mentioned that their priority is to set up things for them]
- The Airtable Prosper leads table captures: first name, source channel, lead status, created date, last name, email, phone number, opted-in services, and notes. [CONFIRMED · fathom/163315168 @ 00:01:02]
- The Procedures table records completed procedures from Prosper, categorized by type (terminal, surgical, free), linked to the corresponding lead and revenue record. [CONFIRMED · fathom/169787214 @ 00:03:09]
- The Prosper Leads table captures all inbound leads including source channel (e.g. Google Ads, website contact form, website service form) and procedure interest. [CONFIRMED · fathom/169787214 @ 00:01:04]
- The Revenue table tracks payments linked to leads, showing procedure, revenue amount, total amount, amount due, and payment status (e.g. outstanding, partial payment). [CONFIRMED · fathom/169787214 @ 00:02:36]
- The agency is setting up ManyChat automation for the client to handle Instagram comments and direct messages, with AI to answer queries and capture lead contact information (name, email, phone). [CONFIRMED · fathom/169787214 @ 00:10:37]
- The client's Instagram account has two-factor authentication enabled via an authenticator app (not email or SMS), requiring a backup code or authenticator OTP for the agency to log in. [CONFIRMED · fathom/169787214 @ 00:14:19]
- Three new Airtable tables were created for the client: Prosper Leads, Revenue, and Procedures. [CONFIRMED · fathom/169787214 @ 00:00:59]
- When a new lead submits a website form, Recura immediately calls or texts the patient automatically; Prosper also sends an automated text within ~2 minutes of lead creation — both use different phone numbers, so a patient may receive messages from two different numbers. [CONFIRMED · fathom/163315168 @ 00:11:30 – 00:12:07]

## Stack & tools

- Agency uses OpenRouter as a unified AI API gateway, allowing switching between OpenAI (ChatGPT), Anthropic Claude, and Google Gemini through a single API key without needing to regenerate keys per model. [CONFIRMED · fathom/159260924 @ 00:08:19 – 'The reason we use OpenRouter is that it allows you to use, if you want to use OpenAI, if you want to use Anthropik Cloud, it would allow you to just do it through one interface.']
- Client (Mustafa Ahmed) is being set up on Make.com as the primary automation platform for the project. [CONFIRMED · fathom/159260924 @ 00:00:44]
- Client is being set up with an AI photo editor tool called Evoto ('the best AI photo editor'); agency to explore whether Evoto has an API for automating before-and-after photo processing. [CONFIRMED · fathom/159260924 @ 00:05:23 – 'It's called Evoto the best ai photo editor... explore the options on how we can integrate it... if they have api or anything that we can use to automate the before and after photos']
- Client is using Airtable and was advised to start on the $20/month plan; the $45 plan is not yet needed because AI-related operations are handled via Make.com. [CONFIRMED · fathom/159260924 @ we don't need too many air credits in there because we are using Meg.com for the, for the air related stuff. So we are fine with, you know, the credits that we have in $20]
- Client recently downloaded Evoto, an app to clean up before-and-after photos; current workflow requires manually pulling photos from Prosper and uploading them into Evoto. [CONFIRMED · fathom/163315168 @ 00:12:14 – 00:12:28]
- Client uses Google (Gmail/Google Workspace) and has a Google My Business account; all new tool accounts will be created under a dedicated Google account to keep everything under the client's domain. [CONFIRMED · fathom/158862212 @ 00:05:06 / 00:05:29]
- Client uses Make (make.com) as the automation/integration platform connecting Prosper to Airtable. [CONFIRMED · fathom/163315168 @ I'll try to share my screen and just quickly show you like, you know, the setup in make and Airtable.]
- Client uses ManyChat as part of the project stack. [CONFIRMED · fathom/159260924 @ 00:06:00 – 'I'm going to go to make.com, Airtable.com, ManyChat']
- Client uses QuickBooks and is approximately three months behind on updates; agency was invited to access QuickBooks to help address this. [CONFIRMED · fathom/159260924 @ 00:07:43 – 'We need to update our QuickBooks. We're like three months behind.']
- Client uses Recura for AI-driven lead outreach (automated calls and chat); Recura records and stores conversation playback between patients and its AI. [CONFIRMED · fathom/163315168 @ I can listen to the recordings. I can listen to the conversation... I can hear the playback, the conversation with Rekora and the patient.]
- Client uses Recura, described as a brand-new AI tool that cannot connect to external systems; agency may need to work around this with manual exports or build an AI bot for it. [CONFIRMED · fathom/159260924 @ 00:06:30 – 'Recura say that they're a brand new AI. They cannot connect to anything.']
- Client uses a CRM/tool called 'Prosper'; Abhijeet needs access to Prosper to begin work. [CONFIRMED · fathom/158862212 @ 00:04:15]
- Client uses a platform called Prosper, which contains invoice/payment data (member names, amounts paid, dates paid). [CONFIRMED · fathom/170225462 @ 00:01:21 – 'you can go on to prosper and look at it']
- Client wants to integrate a before-and-after photo enhancement AI tool (name uncertain — possibly 'Evolution AI' or 'Evolve AI'); Abhijeet will research options and suggest alternatives if needed. [CONFIRMED · fathom/158862212 @ 00:03:36 / 00:13:14]
- Client's CRM is Prosper. Agency needs to be granted access via a shared agency email. [CONFIRMED · fathom/159260924 @ 00:03:38 – 'That's my CRM. I need to email him.']
- Client's marketing channels are Google Ads and Instagram boosting (promotional posts with special pricing); Facebook ads are being discontinued next month. [CONFIRMED · fathom/163315168 @ 00:14:06 – 00:14:23]
- Core tool stack planned for this client: Make.com (automation), Airtable (database and dashboards), ManyChat (DM automation), and QuickBooks (financial integration). [CONFIRMED · fathom/158862212 @ 00:02:39 / 00:13:46]
- ManyChat integration is in progress for this client. [CONFIRMED · fathom/170225462 @ 00:01:33 – 'we are already making a progress on the ManyChat integration']
- Primary data source for the client's revenue and leads pipeline is Prosper (practice management platform). QuickBooks was evaluated but dropped due to poor data quality. [CONFIRMED · fathom/169787214 @ 00:09:00]
- Prosper and Airtable are now connected; all leads coming into Prosper are also synced to Airtable. [CONFIRMED · fathom/163315168 @ the good news is the Prosper and the Airtable is now connected. All the leads that come to the Prosper are now coming to the Airtable as well.]
- Prosper has a well-documented API that allows pulling images programmatically; Evoto does not appear to have a public API, but the agency will contact Evoto support to confirm. [CONFIRMED · fathom/163315168 @ the problem is not the Prosper, like we can obviously pull the images from the Prosper because Prosper has a really good API access. But the problem is with Evoto, I don't see if they have like an API documentation yet]
- QuickBooks has not been actively maintained for the last six months and reconciliation is being done from January; it was removed as a data source from the dashboard because of inaccurate data. [CONFIRMED · fathom/169787214 @ 00:08:19]
- The AI for the ManyChat/Instagram setup will be given a knowledge base built from the client's website content and a price range document the client agreed to provide. [CONFIRMED · fathom/169787214 @ 00:11:50]
- The agency referenced a prior AI Q&A setup done for 'Reqira' (or 'Quora') as a reference point for structuring the ManyChat AI conversation flow for the client. [_INFERRED_ · fathom/169787214 @ 00:11:03]

## Timeline

- Historical data backfilled into Airtable covers the past 90 days from the point of build. [CONFIRMED · fathom/169787214 @ 00:03:03]
- Initial phase (first 1–2 weeks) focuses on data integration and dashboard setup without AI; AI features (e.g. automated follow-ups, ManyChat) are introduced in subsequent phases. [CONFIRMED · fathom/158862212 @ 00:02:59 / 00:04:06]

## Commitments

- A follow-up meeting between the agency and client was agreed upon for later in the same week to address Prosper data gaps. [CONFIRMED · fathom/169787214 @ 00:09:19]
- Abhijeet committed to re-sharing the full three-phase milestone document (including the Evolve AI tool) for client review before starting the Upwork contract. [CONFIRMED · fathom/158862212 @ 00:12:20 / 00:13:29]
- Abhijeet will set up all tool accounts (Make.com, Airtable, ManyChat, etc.) using a dedicated automation email account to be created by the client's IT contact; client will then add payment card to each tool. [CONFIRMED · fathom/158862212 @ 00:06:33 / 00:07:05]
- Agency (Abhijeet) committed to having ManyChat working and dashboard numbers correct by Friday end of week, contingent on receiving all required data from client. [CONFIRMED · fathom/170225462 @ 00:01:52 – 'the goal is to, you know, by Friday, we have ManyChat working']
- Agency committed to re-examining Prosper for any missing payment data; if not found, will schedule a meeting with the Prosper team to investigate. [CONFIRMED · fathom/169787214 @ 00:10:00]
- Agency committed to sending client documentation on how to set up OpenRouter and generate an API key. [CONFIRMED · fathom/159260924 @ 00:09:28 – 'I'll send you the documentation on how to create new API key or how to get started with open router.']
- Agency committed to sending the client a detailed weekly plan tomorrow, outlining tasks for the week and expected deliverables by end of week. [CONFIRMED · fathom/159260924 @ 00:09:41 – 'tomorrow I'm again going to send you a proper detailed list of what we are doing this week. And you know, what is the available you can expect at the end of this week.']
- Agency will contact Evoto support to inquire about API access for programmatic image upload/retrieval. [CONFIRMED · fathom/163315168 @ I'll just communicate with their support once to see if it is something like, you know, they can give us the access to.]
- Client (Mustafa Ahmed) committed to printing and scanning a list of all patients operated on in June and July with money collected, to send to the agency for reconciliation. [CONFIRMED · fathom/169787214 @ 00:09:44]
- Client agreed to send the agency a range of procedure prices to be added to the AI knowledge base for the ManyChat Instagram bot. [CONFIRMED · fathom/169787214 @ 00:12:05]
- Client committed to finding and sharing where credit card payment records are stored (not currently in Prosper or QuickBooks). [CONFIRMED · fathom/169787214 @ 00:04:38]
- Client committed to granting agency access to six platforms: Make.com, Airtable, ManyChat, OpenRouter (API key), Prosper CRM, Recura, and QuickBooks. [CONFIRMED · fathom/159260924 @ 00:10:34 – 'Make.com, AirTable, ManyChat, Open Router... Pro[sper]... Recora... QuickBooks']
- Client expects the agency to train them (the client's team) on the ManyChat/dashboard solution after delivery. [CONFIRMED · fathom/170225462 @ 00:02:02 – 'you need to train us']
- Client needs to create an OpenRouter account, generate one API key, add $5–$10 in credits for testing, and send the API key to the agency. [CONFIRMED · fathom/159260924 @ 00:09:00 – 'I will show you the documentation on how to create that and for now you can just add $5, $10 worth of credits in there. That's enough for us to go through the test.']
- Client will email Abhijeet the correct name of the before-and-after photo AI tool once they locate it. [CONFIRMED · fathom/158862212 @ 00:13:14]
- Client's IT contact ('a guy who helps me') will create the dedicated Google Workspace user/email for automations and share credentials with Abhijeet. [CONFIRMED · fathom/158862212 @ 00:06:28]
- Mustafa (client) committed to emailing June and July invoices to the agency team today. [CONFIRMED · fathom/170225462 @ 00:01:13 – 'I'm going to email you the invoices for people for June and July']
- Mustafa (client) committed to introducing Abhijeet (agency) to the marketing person (MarketMade contact) via email introduction so Abhijeet can discuss lead source attribution. [CONFIRMED · fathom/163315168 @ I will send him and you email at the same time so I can introduce you to him.]
- Mustafa (client) committed to sending the pricing list (price ranges) to the agency today. [CONFIRMED · fathom/170225462 @ 00:01:44 – 'I will have it sent out to you today too']

## Other

- A separate operations/office-organisation consultant had advised the client to fix business processes before implementing AI, estimating a 1–2 year remediation timeline; client decided to proceed with AI implementation in parallel. [CONFIRMED · fathom/158862212 @ 00:00:22 / 00:00:42]
- If needed data is not found in Prosper, agency will contact Prosper's support team to resolve data questions. [CONFIRMED · fathom/170225462 @ 00:02:25 – 'If we don't find the data we need, I'll message the support team']

## Sources

- `crm/mustafa-ahmed` · — · crm · CRM numbers — Mustafa Ahmed
- `fathom/158862212` · 2026-06-28 · fathom · Impromptu Zoom Meeting
- `fathom/159260924` · 2026-06-29 · fathom · Impromptu Zoom Meeting
- `fathom/163315168` · 2026-07-13 · fathom · Impromptu Zoom Meeting
- `fathom/169787214` · 2026-08-03 · fathom · Impromptu Zoom Meeting
- `fathom/170225462` · 2026-08-04 · fathom · Impromptu Zoom Meeting
