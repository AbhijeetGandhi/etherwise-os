# Andrew Wang — Dossier

_Compiled 2026-08-13 17:22 IST · 38 client facts (38 trusted) from 3 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- 'Better Loans' is a second client project, described as a mortgage/car mortgage broker. The automation involves connecting systems, developing email and SMS templates, and transferring leads to a platform called 'Lend' (partners.lend.com.au). [CONFIRMED · fathom/53331796 @ 00:11:30 – 'better loans...like a mortgage broker like a car mortgage broker' and 00:18:00 – 'developing those templates for better loans and developing those like email SMS systems so that transferring those leads towards the land']
- A third prospective client is a building company with a Wednesday proposal meeting. They want: (1) email automation similar to Carbon Group (analyze Outlook emails and draft replies), (2) email categorization into folders, (3) email extraction to CSV for a newsletter/campaign via MailChimp, and (4) lead capture through GoHighLevel capturing calls, emails, and form fills, then nurturing leads over time. [CONFIRMED · fathom/53331796 @ 00:19:13 – 00:21:45 – 'they want to analyze emails from Outlook and reply...categorize emails...extract all of it into like a CSV...campaigns through MailChimp...lead capture tool...through high level']
- Andrew Wang mentioned a third potential client project, with a decision date the following Wednesday. [CONFIRMED · fathom/53091368 @ 00:13:51 – 'there is another third one on the line for you. Next Wednesday is when I will have that date']
- Andrew Wang runs an automation agency and is subcontracting automation work to Abhijeet Gandhi (and his team) via Upwork. [CONFIRMED · fathom/53091368 @ 00:00:40 – 'I'm running agency. So I'm gonna be subcontinent work out to you']
- The first client project is for Carbon Group (an accountant client), consisting of an Outlook email automation: auto-draft replies using OpenAI and route incoming emails into categorised Outlook folders. [CONFIRMED · fathom/53091368 @ 00:07:38 – 'this client is an accountant and he gets a lot of different emails and he wants it to… send important emails to a folder']
- The primary client project discussed is an email automation for 'Carbon Group' (referred to as an accountant/carbon group client) that reads incoming emails, analyzes the thread context, drafts a reply, and places the draft in the inbox/draft folder for the client to review and send. [CONFIRMED · fathom/53331796 @ 00:02:13 – 'looking for any messes that come in then identify the thread go through there...create a value response and put that in the draft folder']
- The second client project involves integrating Pipedrive (CRM) with Lend Capital (a loan-management platform) via their respective APIs, to automatically move qualified leads from Pipedrive into Lend Capital instead of doing it manually. [CONFIRMED · fathom/53091368 @ 00:11:14 – 'he's always like leads, new leads in Pipedrive… he wants that qualify to be removed to lend, but right now he's doing it manually']

## The numbers

- Abhijeet Gandhi estimated the Pipedrive/Lend Capital integration at 3–4 hours of work and quoted $100–$150 fixed price. [CONFIRMED · fathom/53091368 @ 00:11:39 – 'three to four hours for the complete automation. So it would be somewhere around $100 to $150']
- Abhijeet Gandhi quoted approximately $50 (fixed price) for the Carbon Group email folder-routing automation. [CONFIRMED · fathom/53091368 @ 00:09:27 – 'for this type of email automation, it could be as low as somewhere around $50']
- Contract: Make.com, GHL & API All-Rounder to build automations — status CLOSED. [CONFIRMED · CRM:contracts/40100566]
- Contract: Pipedrive backend work — status ACTIVE. [CONFIRMED · CRM:contracts/40496773]
- Revenue to date (GROSS, billed): $438.67 across 3 revenue transactions (2025-04-17 → 2025-10-12). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $394.36 — gross $438.67 less $43.87 Upwork service fees, $0.44 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]

## People

- Abhijeet Gandhi has a team he can involve depending on client needs; he prefers to be the single point of contact on calls for at least the first couple of months. [CONFIRMED · fathom/53091368 @ 00:03:51 – 'for the starting couple of months, I try to be there on every call with you']
- Abhijeet Gandhi has a team with expertise in marketing tools such as MailChimp and Instantly, while Abhijeet himself focuses on building personalized email automations and campaign setups. [CONFIRMED · fathom/53331796 @ 00:20:54 – 'I do have people who are expert in these tools...I'm mostly part of where I work into the creating the personalized emails...setting up campaigns and everything in those tools is done by the team']

## Architecture & systems

- Andrew Wang demonstrated a pre-built Make.com email-triage flow (built for a friend) that watches emails, saves thread IDs to Google Sheets, uses OpenAI to classify new vs existing customers, and creates drafted replies accordingly. [CONFIRMED · fathom/53091368 @ 00:06:17 – 'This is what I've built so far for one of my friends… we save the thread ID to Google Shades so that we can get open AI to track which one's a new customer']
- Carbon Group client is happy to have all drafted replies placed in the draft/draw folder rather than requiring any email movement or folder shifting, which simplifies the automation. [CONFIRMED · fathom/53331796 @ 00:00:57 – 'he's happy to have all the drafts in the inbox or like the draw folder. So he's fine with that. We don't need to move around anything with those.']
- Carbon Group client receives automated emails from a fixed sender email address; the email body contains company information. The desired automation is to look up that company in Xero, find the related contact email address, and forward the email to that address. [CONFIRMED · fathom/53331796 @ 00:04:00 – 'he gets it from like a company and it's the same email address every time...in the body of the email is some information about a company. He wants us to go find that company in zero and find the related email address to that company and populate it and so you can forward it to that email address.']
- Carbon Group client requires a CC email address filter so that a specific CC address is included in every outgoing email draft. [CONFIRMED · fathom/53331796 @ 00:03:00 – 'he wants everything to be CC'd as well so we need to create a filter that all like a CC email address that needs to be in every single email']
- The Carbon Group email automation requires: Outlook connection in Make.com, a list of existing folders, and criteria defining which email types go into which folders; OpenAI will determine folder routing based on email context. [CONFIRMED · fathom/53091368 @ 00:08:03 – 'I just need the outlook and the list of the folders… how do we decide which mail goes to which folder']
- The Xero forwarding feature was flagged as potentially out of scope because the original engagement was for an email automation (cheaper), not a full system integration; it will only be built if it is straightforward to implement. [CONFIRMED · fathom/53331796 @ 00:05:00 – 'we were supposed to do system integrations here. We were just meant to do an automation, which is a lot cheaper than a system integration...if it's easy to do it, then do it. If it's not easy, you can just leave it out.']
- The agency's email automation approach applies to all emails in the inbox by default; some clients later request refinements such as only replying to initial/first emails in a thread. [CONFIRMED · fathom/53331796 @ 00:02:32 – 'some of my clients are very nitpicky...they want to refine it...I only wanted to reply to the first email...right now we'll just do it so that it applies to all emails']

## Stack & tools

- Asana is being considered as a project and task tracking tool for the agency's client work; Andrew plans to invite Abhijeet via the support email so he can update projects and access documents there. Google Drive is being used in the interim. [CONFIRMED · fathom/53331796 @ 00:09:44 – 00:10:41 – 'I may be using that soon...soon I'll be inviting you...we may be switching to that. For the time being...I'll just operate everything to Google Drive']
- Client recordings and project files will be shared via a Google Drive folder (organized by client name) rather than WhatsApp, to prevent files from being lost in chat. [CONFIRMED · fathom/53331796 @ 00:08:43 – 'let's do...I'll create a shared drive folder in my org with your name where you get this, you know, like create folders for every class']
- SMS for the Better Loans project will be sent through Andrew Wang's Twilio account; Abhijeet will purchase the phone number and connect it within the automation using that Twilio account. [CONFIRMED · fathom/53331796 @ 00:16:51 – 'I want you to purchase it off my Twilio account...all the SMS will be sent through my Twilio account']
- The Carbon Group email automation will use Microsoft Outlook (connected via Make.com) and OpenAI to identify email context and assign emails to the correct folder. [CONFIRMED · fathom/53091368 @ 00:08:43 – 'we need to have their Outlook connection made in the make.com']
- The Pipedrive-to-Lend Capital automation will be built in Make.com using Pipedrive's native Make integration and Lend Capital's REST API (API documentation confirmed available). [CONFIRMED · fathom/53091368 @ 00:10:48 – 'pipe drive has the direct integration with make… the lender has a good documentation']
- The Xero integration for the Carbon Group forwarding feature may be implemented via Xero's API using a Make (or similar tool) API call module, subject to feasibility. [CONFIRMED · fathom/53331796 @ 00:06:00 – 'I don't know if you can use an API...yeah API probably have the API, make an API call module as well']
- The correct API endpoint for the Better Loans project is partners.lend.com.au, not the other lend.com.au subdomain; an incorrect API was initially identified due to confusion between the two similar-looking subdomains. [CONFIRMED · fathom/53331796 @ 00:12:00 – 'partners.lend.com.au, right? So that's the link that we discussed, which has the API' and 00:16:39 – 'essentially the wrong API, but yeah, now we fix that']

## Timeline

- Andrew Wang has a client meeting with Carbon Group on Monday to obtain Outlook access and gather folder-categorisation criteria. [CONFIRMED · fathom/53091368 @ 00:07:07 – 'on Monday, I will be speaking with a client on this email one… I will be getting access to his Outlook']

## Commitments

- Abhijeet Gandhi agreed to provide fixed-price estimates for both automations (Carbon Group email and Pipedrive/Lend Capital) on the same evening as the call. [CONFIRMED · fathom/53091368 @ 00:15:53 – 'I can give you the update on both tonight']
- Abhijeet is asked to provide pricing for the building company proposal by the next day so Andrew can prepare for the Wednesday meeting. [CONFIRMED · fathom/53331796 @ 00:18:59 – 'you can just get back to me on the price in the next couple of hours, or day or two, probably by tomorrow, because I need to prepare for it']
- Andrew Wang and Abhijeet Gandhi agreed to hold a daily 30–45 minute call to review progress, answer questions, and align on tasks, replacing ad-hoc message back-and-forth. [CONFIRMED · fathom/53331796 @ 00:07:43 – 'find a time that works for both of us...maybe have 30 or 45 minutes call each day']
- Andrew Wang expects automations to be delivered within approximately two weeks. [CONFIRMED · fathom/53091368 @ 00:03:06 – 'the average two days, two weeks. Are you able to do those things in two weeks time']
- Andrew Wang explicitly gave Abhijeet approval to ask him questions freely (spam him with questions) whenever he is awake, to ensure automations are built correctly. [CONFIRMED · fathom/53331796 @ 00:06:33 – 'I want you to spam me with questions...I'm giving you the approval to spam as much as you want']
- Andrew Wang insisted on fixed-price agreements rather than hourly billing to avoid time-padding, but is open to hourly negotiation if needed. [CONFIRMED · fathom/53091368 @ 00:13:02 – 'these are our fixed prices… give me some ranges and then I can also do a fixed price estimate for you as well']
- Andrew Wang will provide Abhijeet Gandhi with Make.com account access, full proposal documents, meeting recordings, and transcripts for each project. [CONFIRMED · fathom/53091368 @ 00:08:50 – 'I'll give you the make.com as well, you can use my make.com' and 00:04:04 – 'I have videos of the recordings of my myself with a client']

## Notable quotes

- Abhijeet Gandhi on quality: 'if I'm giving you the estimate and if I'm reducing my budget, I don't compromise on quality because as I said, I also want to work with the people who are willing to work long term' [CONFIRMED · fathom/53091368 @ 00:12:16]

## Sources

- `crm/andrew-wang` · — · crm · CRM numbers — Andrew Wang
- `fathom/53091368` · 2025-03-21 · fathom · Impromptu Zoom Meeting
- `fathom/53331796` · 2025-03-24 · fathom · Impromptu Zoom Meeting
