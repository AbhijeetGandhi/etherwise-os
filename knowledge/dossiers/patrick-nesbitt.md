# Patrick Nesbitt — Dossier

_Compiled 2026-11-01 05:18 IST · 28 client facts (28 trusted) from 2 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- One of the key goals of the lead gen system is to convert physical/in-person prospects ('prospecting 2.0 in the physical sense') into digital leads that can be reached via promotions and automated campaigns. [CONFIRMED · fathom/48927603 @ 00:10:00 - 'one of the main opportunities we have is capturing more information and communicating leads that are already in the prospecting 2.0 in the physical sense and making them also digital leads.']
- The agency is called Tortoisepark AI (also referred to as AutosPods/WaterPark AI in transcript). They describe themselves as doing 'heavy duty bespoke AI and automation implementations' across industries and geographies. [CONFIRMED · fathom/48927603 @ 00:03:01 - 'We formed the company a couple of years ago and essentially what we do is heavy duty bespoke AI and automation implementations.']
- The new client project is a lead generation system for the Posadas hotel group, aimed at automating and scaling lead capture and nurturing across hotel properties, referred to internally as the 'blood lead gen project' or lead gen phase 2. [CONFIRMED · fathom/48927603 @ 00:05:22 - 'I've been handling Diego's project called QA QA Dashboard. And this is the new project that we have been looking at, which is blood lead gen project.']
- The timeshare/hotel industry is characterized by highly manual processes according to the client-side contact Rafa. [CONFIRMED · fathom/48927603 @ 00:07:11 - 'this industry is very manual. is overwhelmed about how mobile is for work here.']

## The numbers

- Contract: AI & AUTOMATION SPECIALIST | Make.com | Airtable | API's | Attention to Detail — status CLOSED. [CONFIRMED · CRM:contracts/39523290]
- Contract: AI & Automation Consulting — status CLOSED. [CONFIRMED · CRM:contracts/40248263]
- Revenue to date (GROSS, billed): $1,976.32 across 10 revenue transactions (2025-01-26 → 2025-04-12). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $1,585.82 — gross $1,976.32 less $183.33 refunded, $179.29 Upwork service fees, $27.88 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]
- The Posadas hotel brand has a total of 196 hotels that could ultimately be impacted by the automation rollout. [CONFIRMED · fathom/48927603 @ 00:09:09 - 'The hotel has the whole brand, it has 196 hotels we can impact.']
- The client (Posadas hotel group) currently has approximately 15–16 hotels in scope for the lead gen project, producing leads at a consistent rate. [CONFIRMED · fathom/48927603 @ 00:08:54 - 'right now we're working with around, I think, 15, 16 hotels. they're producing, let's say, leads at a pretty consistent rate.']

## People

- Abhijeet Gandhi is the AI automation engineer at the agency. He has 3 years background as a machine learning engineer before moving into automation tools. [CONFIRMED · fathom/48927603 @ 00:06:04 - 'I'm Abhijit. I'm from India and I'm the AI automation engineer. I was working as a machine learning engineer for the past three years.']
- Diego Azcarraga is the client-side project lead / relationship owner connecting the agency with Rafa and the Posadas hotel group. [CONFIRMED · fathom/48927603 @ 00:02:00 - Diego introduces the agency team to Rafa as 'the team behind a lot of the things we've been working on.']
- Ishan Vats is COO and project manager at the agency. He has 6+ years in corporates, ran his own project management consultancy, and has ~4 years background in hospitality/hotel industry. [CONFIRMED · fathom/48927603 @ 00:05:02 - 'I'm working in the position of COO and project managers.' and 00:07:39 - 'I've worked with the hotel industry for almost four years.']
- Patrick Nesbitt is the founder of the agency, based in Cape Town, with a background in finance before pivoting to AI and automation. [CONFIRMED · fathom/48927603 @ 00:04:07 - 'I am in Cape Town, and yeah, I'm a finance person by trade, but just got all consumed by AI and automation, jump ship and start a tortoise park.']
- Rafael (Rafa) is the client-side contact in charge of the network marketing sales team for the timeshare/hotel company (Posadas). [CONFIRMED · fathom/48927603 @ 00:06:52 - 'Well, I'm Rafa and I'm in charge of the network marketing sales team.']

## Architecture & systems

- A physical in-meeting-room tool called 'Raminta Sala Adventas' (or similar) is planned to capture lead information during physical sales meetings and communicate it to the team in real time, built on Airtable. [CONFIRMED · fathom/48927603 @ 00:10:47 - 'what we called...Raminta Sala Adventas, which is a tool we can utilize in the physical meeting rooms to capture more information...we are planning to build it out on Airtable.']
- Airtable lead records will capture: name, email, phone, number of kids, occupation, preferences, special requests, check-in/check-out dates, sales funnel stage, lead source, notes, interest level, and the sales representative who added the lead. [CONFIRMED · fathom/48927603 @ 00:14:56 - 'the basic details like name, email, phone, each number of kids. What is their occupation?' through 00:17:00]
- Airtable will have role-based access views: general managers can see all sales data across all salespeople; individual sales representatives will have a restricted view limited to their own entries. [CONFIRMED · fathom/48927603 @ 00:19:00 - 'general manager will be able to see the entries for all the sales people however at the left you see sales representative one view so sale...']
- Airtable will serve as the main centralized lead database. Each hotel property will have its own Airtable base/view, and all hotel data will feed into one centralized database to track recurring customers and full lead history. [CONFIRMED · fathom/48927603 @ 00:14:22 - 'this is going to be the main database for all the data regarding the leads' and 00:18:14 - 'all the 20 hotels that will use a table all the data will go to centralized database.']
- An automation will automatically remove a lead from a hotel-specific Airtable view once their check-out date has passed, while retaining the record in the centralized database. [CONFIRMED · fathom/48927603 @ 00:15:33 - 'if the check-out date is passed, this lead is automatically removed from this base so that the people who are looking at it only has the view of the leads that are in the hotel or the good check-out dates are not passed.']
- ManyChat is identified as the primary WhatsApp chatbot and automation tool, chosen because WhatsApp is a dominant communication channel in Mexico where the client operates. [CONFIRMED · fathom/48927603 @ 00:12:25 - 'ManyChat obviously serves as a chatbot and automation service for WhatsApp. That's very popular in Mexico.']

## Stack & tools

- GoHighLevel will be used as the digital marketing CRM to centralize leads from all hotel digital channels and trigger automated workflows including ManyChat, email, and social media outreach. [CONFIRMED · fathom/48927603 @ 00:11:41 - 'digital marketing CRM that will be built on go high level...every client that arrives can go into this type of automatic process, using many tools, like many chat, like email, well, and even social media.']
- The lead gen system for Posadas is planned to be built on three core tools: Airtable (lead database), GoHighLevel (digital marketing CRM), and ManyChat (WhatsApp chatbot and automation). [CONFIRMED · fathom/48927603 @ 00:11:06 - 'we are planning to build it out on Airtable.' / 00:11:41 - 'digital marketing CRM that will be built on go high level.' / 00:12:25 - 'The third one...is ManyChat.']

## Other

- Rafa raised the use case of recurring hotel guests (e.g. a guest who has visited 5 times before), requiring the system to identify and flag returning customers rather than treating them as new leads. [CONFIRMED · fathom/48927603 @ 00:17:14 - 'We have some clients that are equivalent so for example you have John Smith in the first place...maybe he has been in the hotel five times before.']
- The agency has a pre-existing project with Diego's company 'Quack' involving a QA Dashboard, message automation, and a unified CRM — used as a reference example in this meeting. [CONFIRMED · fathom/48927603 @ 00:01:01 - 'things we've done with the Quack site saying like this is what we are doing and how we can use made a chat.' and 00:05:22 - 'I've been handling Diego's project called QA QA Dashboard.']
- The agency has worked with clients in the US in logistics, real estate, Switzerland in commercial property, and Mexico across multiple industries. [CONFIRMED · fathom/48927603 @ 00:03:19 - 'we've worked with clients in the states and logistics to handle in real estate, to Switzerland and commercial property, to Mexico.']
- The agency offers AI voice calling agent implementations as one of its capability types. [CONFIRMED · fathom/48927603 @ 00:03:47 - 'Sometimes they want an AI voice calling agent.']
- The lead gen system is seen as having long-term SaaS product potential according to Ishan Vats. [CONFIRMED · fathom/48927603 @ 00:05:44 - 'It has got a lot of potential that we see as a SaaS product in long term.']

## Sources

- `crm/patrick-nesbitt` · — · crm · CRM numbers — Patrick Nesbitt
- `fathom/48927603` · 2025-02-25 · fathom · Impromptu Google Meet Meeting
