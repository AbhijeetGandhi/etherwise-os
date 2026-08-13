# Vitaliy Kahanets — Dossier

_Compiled 2026-08-13 17:22 IST · 15 client facts (15 trusted) from 2 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- Agency has previously built outbound and inbound voice-agent systems for real estate clients: outbound agents call thousands of leads per day, log call summaries; inbound agents handle after-hours calls. Outbound has shown low success (people hang up on AI salespeople); inbound shows strong success. [CONFIRMED · fathom/117799510 @ 00:00:26 – 'the system that I built for them was creating an outreach system where… voice agents are reaching out to thousands of leads every day… for the inbound… we are seeing really good success']
- Client operates in wholesale real estate and uses GoHighLevel as their CRM. [CONFIRMED · fathom/117799510 @ 00:03:25 – 'the purpose of this call, you mentioned you are a pro in GoHighLevel']
- Client wants to build an AI system that automatically reviews a call after an acquisition agent finishes it, pulling in all prior conversation history, texts, previous call summaries, and notes from GoHighLevel, then outputs a lead evaluation (hot/nurture/dead), lead score, and feedback on the acquisition agent's performance. [CONFIRMED · fathom/117799510 @ 00:04:08 – 'I want to have in place a Yai who going to review the call right away after Acquisition Guy finished the call']
- Client's longer-term vision is an AI lead manager that calls prospects first to gather information, then humans set the maximum allowable offer, and then an AI senior acquisition agent calls back to close the deal. [CONFIRMED · fathom/117799510 @ 00:06:00 – 'AI lead manager who gonna call prospect first, identify all the information… AI gonna call back as acquisition senior and close the deal']

## The numbers

- Agency builder states they have built over 2,000 automations across different platforms. [CONFIRMED · fathom/117799510 @ 00:16:00 – 'I've built over 2,000 autobuses within a different platform']
- Contract: AI Automations — status ACTIVE. [CONFIRMED · CRM:contracts/42784694]

## People

- Client's name is Vitaliy Kahanets; agency contact on the call is Abhijeet Gandhi. [CONFIRMED · fathom/117799510 @ 00:00:00]

## Architecture & systems

- AI will work from call transcripts only; it will not process raw audio files. Agency position is that ~80% of conversational context (sentiment, satisfaction) is captured in text alone, making tonality analysis from audio unnecessary for this use case. [CONFIRMED · fathom/117799510 @ 00:13:31 – '80% of the context is just covered through text… It's very rare that a person is saying something which is positive and negative at the same time, which can only be defined through [tonality]']
- Agency's standard pre-implementation step: produce a workflow/architecture diagram showing which steps live in GoHighLevel vs. Make.com and what the updated GHL record looks like, get client sign-off, then implement — to avoid mid-build plan changes. [CONFIRMED · fathom/117799510 @ 00:11:09 – 'I'll give you a small architecture diagram or the workflow diagram… once you give me a go ahead, then I go and implement… we only spend like an hour before implementing anything']
- Proposed workflow: when a call ends in GoHighLevel a transcript is auto-generated; a workflow pulls all contact messages, existing notes, and calendar/meeting history; feeds everything to an AI model; AI outputs lead evaluation and acquisition-agent coaching feedback; results are written back into GoHighLevel fields. [CONFIRMED · fathom/117799510 @ 00:09:44 – 'whenever a call happens, there is a transcript that is being auto-generated… pull in all the information about the kernel session messages, about the existing nodes']

## Stack & tools

- Agency prefers Make.com over GoHighLevel native workflows for AI steps because the same OpenAI integration that takes 10 minutes in Make takes ~30 minutes in GoHighLevel (GoHighLevel requires manual HTTP requests with no direct OpenAI integration). [CONFIRMED · fathom/117799510 @ 00:16:40 – 'what takes me in MAKE 10 minutes would take me 30 minutes in GoHighLevel for every single step']
- Client currently uses Zapier (not Make.com) for automations such as receiving leads and connecting with spreadsheets, but not for AI workflows. [CONFIRMED · fathom/117799510 @ 00:16:59 – 'we using Zapier… receiving delete, kind of connection with the spreadsheets']
- Knowledge base for the AI will be a vector store created inside OpenAI (OpenAI Playground), populated with transcripts (PDF or text files) from YouTube videos or other sources the client supplies; no separate third-party vector DB platform is needed. [CONFIRMED · fathom/117799510 @ 00:14:46 – 'OpenAI has its own… vector store inside the OpenAI itself… you upload the files, like whether it's a PDF, whether it's a transcript']
- Proposed tech stack for the AI call-review system: GoHighLevel automations for primary workflow triggers, Make.com for steps requiring flexible AI model calls (e.g. OpenAI, Claude, Gemini), and OpenAI as the default AI model for information extraction and summarisation. [CONFIRMED · fathom/117799510 @ 00:10:23 – 'a combination of… go-high-level automotions, but then sometimes we do need a make.com in between… if we are seeing OpenAI is not giving us as good results, so maybe you want to use Claude, maybe you want to use Gemini']

## Other

- Agency practice for inbound voice agents: disclose at the start of the call that the caller is speaking to an AI assistant ('hey, this is AI assistant from XYZ office'), rather than attempting to hide the AI identity, which improves caller acceptance. [CONFIRMED · fathom/117799510 @ 00:02:33 – 'at the start of the call, we'll say, hey, this is AI assistant from XYZ office']

## Sources

- `crm/vitaliy-kahanets` · — · crm · CRM numbers — Vitaliy Kahanets
- `fathom/117799510` · 2026-01-28 · fathom · Impromptu Zoom Meeting
