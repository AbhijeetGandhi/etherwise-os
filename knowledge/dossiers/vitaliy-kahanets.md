# Vitaliy Kahanets — Dossier

_Compiled 2026-08-18 19:12 IST · 33 client facts (33 trusted) from 3 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- Agency has previously built outbound and inbound voice-agent systems for real estate clients: outbound agents call thousands of leads per day, log call summaries; inbound agents handle after-hours calls. Outbound has shown low success (people hang up on AI salespeople); inbound shows strong success. [CONFIRMED · fathom/117799510 @ 00:00:26 – 'the system that I built for them was creating an outreach system where… voice agents are reaching out to thousands of leads every day… for the inbound… we are seeing really good success']
- Client operates in wholesale real estate and uses GoHighLevel as their CRM. [CONFIRMED · fathom/117799510 @ 00:03:25 – 'the purpose of this call, you mentioned you are a pro in GoHighLevel']
- Client wants to build an AI system that automatically reviews a call after an acquisition agent finishes it, pulling in all prior conversation history, texts, previous call summaries, and notes from GoHighLevel, then outputs a lead evaluation (hot/nurture/dead), lead score, and feedback on the acquisition agent's performance. [CONFIRMED · fathom/117799510 @ 00:04:08 – 'I want to have in place a Yai who going to review the call right away after Acquisition Guy finished the call']
- Client's longer-term vision is an AI lead manager that calls prospects first to gather information, then humans set the maximum allowable offer, and then an AI senior acquisition agent calls back to close the deal. [CONFIRMED · fathom/117799510 @ 00:06:00 – 'AI lead manager who gonna call prospect first, identify all the information… AI gonna call back as acquisition senior and close the deal']
- The project involves building an AI-powered post-call automation for a real estate acquisition business: when a call ends in GoHighLevel, the system extracts structured data from the transcript and writes AI-generated notes, scores, and lead memory back to the CRM. [CONFIRMED · fathom/120464251 @ 00:03:28 – 'the automation that we're going to set up, the main trigger for that automation is always going to be like when the call ends']

## The numbers

- Agency builder states they have built over 2,000 automations across different platforms. [CONFIRMED · fathom/117799510 @ 00:16:00 – 'I've built over 2,000 autobuses within a different platform']
- Contract: AI Automations — status ACTIVE. [CONFIRMED · CRM:contracts/42784694]
- Revenue to date (GROSS, billed): $200.00 across 1 revenue transaction (2026-03-03 → 2026-03-03). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $360.00 — gross $200.00 less $40.00 Upwork service fees, $200.00 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]

## People

- Client's name is Vitaliy Kahanets; agency contact on the call is Abhijeet Gandhi. [CONFIRMED · fathom/117799510 @ 00:00:00]

## Architecture & systems

- A 'Lead Memory' custom field will be maintained and updated after every call, storing a running summary of what is known about the lead (motivation, situation, key details) to provide AI context for future calls. [CONFIRMED · fathom/120464251 @ 00:08:28 – 'lead memory… another custom field that we want to create, which we update every time… it's being updated after every call']
- AI Review notes section (right panel in GoHighLevel) will contain: call date, deal assessment (classification + justification + red flags + missing info), house/owner situation details extracted from the call, recommended next action (deep recommendation up to deal close, including copy-paste follow-up text messages), and rep coaching (what the rep did well, opportunities missed, one improvement suggestion). [CONFIRMED · fathom/120464251 @ 00:06:15 – 'AI review… classification… justification… red flags… recommended action… rep coaching… what they did well… opportunities that they missed… one improvement']
- AI will work from call transcripts only; it will not process raw audio files. Agency position is that ~80% of conversational context (sentiment, satisfaction) is captured in text alone, making tonality analysis from audio unnecessary for this use case. [CONFIRMED · fathom/117799510 @ 00:13:31 – '80% of the context is just covered through text… It's very rare that a person is saying something which is positive and negative at the same time, which can only be defined through [tonality]']
- Agency's standard pre-implementation step: produce a workflow/architecture diagram showing which steps live in GoHighLevel vs. Make.com and what the updated GHL record looks like, get client sign-off, then implement — to avoid mid-build plan changes. [CONFIRMED · fathom/117799510 @ 00:11:09 – 'I'll give you a small architecture diagram or the workflow diagram… once you give me a go ahead, then I go and implement… we only spend like an hour before implementing anything']
- Calls are recorded and full transcripts are stored in GoHighLevel, so team members can review recordings or full transcripts in addition to the AI-generated notes. [CONFIRMED · fathom/120464251 @ 00:08:11 – 'this call is going to be recorded anyway… we will have the full transcript as well. So if someone is just confused and want to look at the complete transcript or hear the recording, they can do that']
- Custom fields (left panel in GoHighLevel) planned: Lead Category (hot/nurture/dead lead), Motivation Score (0–10 AI rating of caller motivation), Acquisition Score (0–10 rep performance rating), Next Action (e.g. send offer, follow up, request docs), Follow-Up ETA (number of days). [CONFIRMED · fathom/120464251 @ 00:05:20 – 'lead category… motivation score… acquisition score… next action… follow up ETA']
- Proposed workflow: when a call ends in GoHighLevel a transcript is auto-generated; a workflow pulls all contact messages, existing notes, and calendar/meeting history; feeds everything to an AI model; AI outputs lead evaluation and acquisition-agent coaching feedback; results are written back into GoHighLevel fields. [CONFIRMED · fathom/117799510 @ 00:09:44 – 'whenever a call happens, there is a transcript that is being auto-generated… pull in all the information about the kernel session messages, about the existing nodes']
- The automation trigger is 'call ends' in GoHighLevel; every completed call will trigger the workflow regardless of which representative handled it. [CONFIRMED · fathom/120464251 @ 00:03:28 – 'every call will trigger. So it's not like when we are implementing, we need to do it for all the existing leads']
- The automation will use Make.com (not GoHighLevel's native AI step) to process data, because GoHighLevel's built-in ChatGPT integration is less flexible and offers fewer fine-tuning options than Make. [CONFIRMED · fathom/120464251 @ 00:11:54 – 'it would still be better that we use make.com… we fetch all the data from GoHighLevel and then send it to Make. In Make, there is the chat GPT agent that processes it and then writes back']
- Three separate, individual API calls to ChatGPT are planned rather than one combined call: (1) extract custom field data from transcript, (2) generate AI review/notes using transcript + lead memory, (3) update the lead memory by combining old memory with the latest conversation summary. [CONFIRMED · fathom/120464251 @ 00:09:08 – 'it's going to be an individual API call… one call… extract the custom feed information… next instance… get me the AI review… third one… this was the old AI memory, this was the latest conversation, now update the lead memory']

## Stack & tools

- Agency prefers ChatGPT (OpenAI) over Claude or Gemini for structured extraction tasks like this; Claude is noted as good but not the preferred choice for this use case. [CONFIRMED · fathom/120464251 @ 00:13:10 – 'usually for the tasks like what we are doing right now, we just prefer ChatGPT… we don't see using Claude for such things']
- Agency prefers Make.com over GoHighLevel native workflows for AI steps because the same OpenAI integration that takes 10 minutes in Make takes ~30 minutes in GoHighLevel (GoHighLevel requires manual HTTP requests with no direct OpenAI integration). [CONFIRMED · fathom/117799510 @ 00:16:40 – 'what takes me in MAKE 10 minutes would take me 30 minutes in GoHighLevel for every single step']
- Client currently uses Zapier (not Make.com) for automations such as receiving leads and connecting with spreadsheets, but not for AI workflows. [CONFIRMED · fathom/117799510 @ 00:16:59 – 'we using Zapier… receiving delete, kind of connection with the spreadsheets']
- Knowledge base for the AI will be a vector store created inside OpenAI (OpenAI Playground), populated with transcripts (PDF or text files) from YouTube videos or other sources the client supplies; no separate third-party vector DB platform is needed. [CONFIRMED · fathom/117799510 @ 00:14:46 – 'OpenAI has its own… vector store inside the OpenAI itself… you upload the files, like whether it's a PDF, whether it's a transcript']
- Proposed tech stack for the AI call-review system: GoHighLevel automations for primary workflow triggers, Make.com for steps requiring flexible AI model calls (e.g. OpenAI, Claude, Gemini), and OpenAI as the default AI model for information extraction and summarisation. [CONFIRMED · fathom/117799510 @ 00:10:23 – 'a combination of… go-high-level automotions, but then sometimes we do need a make.com in between… if we are seeing OpenAI is not giving us as good results, so maybe you want to use Claude, maybe you want to use Gemini']
- Tech stack for the automation: GoHighLevel (CRM/trigger), Make.com (automation middleware), OpenAI ChatGPT API (AI processing). [CONFIRMED · fathom/120464251 @ 00:12:04 – 'fetch all the data from GoHighLevel and then send it to Make. In Make, there is the chat GPT agent that processes it and then writes back to [GoHighLevel]']

## Commitments

- Abhijeet committed to completing the first implementation by the following Monday (working over the weekend) and will communicate proactively if there is a delay. [CONFIRMED · fathom/120464251 @ 00:02:28 – 'I'll try to get it done… by Monday, this time, because I work over the weekend. So probably, you know, get it implemented. And if not, I'll let you know']
- After initial implementation, additional custom fields or value options can be added easily based on client feedback/revision sessions. [CONFIRMED · fathom/120464251 @ 00:02:15 – 'after the first implementation, if you tell me… we want to add a couple of more fields… we can add that easily']
- Client needs to create a new Make.com account; Abhijeet will send an affiliate link post-call and will share an email address for the Make.com collaborator invite. [CONFIRMED · fathom/120464251 @ 00:12:12 – 'I'll just send you an affiliate link that I have with Make.com… I will send you the email ID to send the invite']
- Client needs to obtain an OpenAI API key (via OpenAI Platform/Playground, pay-per-use, not a subscription) so Make.com can call ChatGPT. [CONFIRMED · fathom/120464251 @ 00:12:33 – 'the last thing we'll need is going to be the OpenAI API key so that we can make API calls to chat GPT through Make']
- Client requested that the 'Recommended Action' output be a deep, multi-step recommendation up to deal close, not just a next-step label — including ready-to-send copy-paste text message drafts. [CONFIRMED · fathom/120464251 @ 00:10:31 – 'this recommendation action can be kind of like a much, much deeper… next step. And at the point to get the deal signed… send him text message, boom, boom, and text message, like copy paste']
- Client's goal is that acquisition reps do not take any notes during calls; they focus entirely on the conversation while AI automatically captures all deal and coaching information. [CONFIRMED · fathom/120464251 @ 00:04:07 – 'I don't want to have acquisition guys make any notes during the call. I want them to be focused on a call. And all those notes are supposed to come through the call']

## Other

- Agency practice for inbound voice agents: disclose at the start of the call that the caller is speaking to an AI assistant ('hey, this is AI assistant from XYZ office'), rather than attempting to hide the AI identity, which improves caller acceptance. [CONFIRMED · fathom/117799510 @ 00:02:33 – 'at the start of the call, we'll say, hey, this is AI assistant from XYZ office']

## Sources

- `crm/vitaliy-kahanets` · — · crm · CRM numbers — Vitaliy Kahanets
- `fathom/117799510` · 2026-01-28 · fathom · Impromptu Zoom Meeting
- `fathom/120464251` · 2026-02-06 · fathom · Impromptu Zoom Meeting
