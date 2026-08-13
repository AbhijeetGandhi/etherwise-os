# Dane Worthington — Dossier

_Compiled 2026-08-13 17:22 IST · 16 client facts (16 trusted) from 2 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- The client (Dane Worthington) is exploring a voice agent system targeting the U.S. prison/correctional system, which is a highly regulated environment where each prison has a telecom provider and calls must be whitelisted/registered with the FCC. [CONFIRMED · fathom/99116769 @ 00:01:04]

## The numbers

- Contract: AI Voice Enablement Training Program Development — status ACTIVE. [CONFIRMED · CRM:contracts/40535816]
- Contract: zapier consultation for vapi integration — status ACTIVE. [CONFIRMED · CRM:contracts/40732694]
- Revenue to date (GROSS, billed): $1,110.00 across 7 revenue transactions (2025-05-27 → 2025-12-16). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $997.88 — gross $1,110.00 less $111.00 Upwork service fees, $1.12 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]
- The client's PRD specifies MVP requirements including >90% connection rate and <5% dropped calls; Abhijeet assessed these are achievable with VAPI. [CONFIRMED · fathom/99116769 @ 00:09:43]
- VAPI's standard plan supports up to 10 concurrent calls; upgrading to the enterprise plan removes that limit and allows any number of simultaneous calls. [CONFIRMED · fathom/99116769 @ 00:04:00]

## Architecture & systems

- A voice agent system is composed of three distinct AI model layers: speech-to-text, a text-to-text LLM (deciding what to say next), and text-to-speech — all combined into one pipeline. [CONFIRMED · fathom/99116769 @ 00:02:06]
- HIPAA compliance on VAPI automatically announces on-call recording disclosures, which makes it unsuitable for cold calling but viable for training programs and similar use cases. [CONFIRMED · fathom/99116769 @ 00:03:37]
- Make, Zapier, and similar workflow automation tools are not HIPAA compliant and therefore cannot be used to transfer sensitive call data; a custom server hosted on AWS or Google Cloud running proprietary scripts is required instead. [CONFIRMED · fathom/99116769 @ 00:07:47]

## Stack & tools

- For HIPAA-compliant data storage outside VAPI, the agency considers Airtable (HIPAA-compliant, good UI) or Supabase (secure but less visual) as database options. [CONFIRMED · fathom/99116769 @ 00:07:00]
- Phone numbers for VAPI would likely be provisioned via Twilio; VAPI supports importing numbers from multiple providers. FCC registration compatibility with Twilio for prison telecom whitelisting is uncertain and needs investigation. [CONFIRMED · fathom/99116769 @ 00:05:45]
- VAPI is being evaluated as the primary platform for the client's voice agent instead of building from scratch; it supports multiple languages, concurrent calls, and HIPAA-compliant call settings. [CONFIRMED · fathom/99116769 @ 00:02:52]

## Timeline

- The client plans to put the project out to bid in approximately the next couple of weeks from the call date. [CONFIRMED · fathom/99116769 @ 00:11:05]

## Other

- Abhijeet previously worked with a different client approximately 6–8 months prior attempting to build a custom voice agent system from scratch without VAPI. A team of 3 people including a senior AI engineer formerly at Google worked on it for 6–8 months and still did not reach VAPI's output quality before the project was cancelled. [CONFIRMED · fathom/99116769 @ 00:08:20]
- Prison calls are collect calls: the receiving party pays, and the call begins with an automated prompt asking if they accept the charges. The voice agent would need to programmatically accept this prompt and have a billing account tied to the receiving number. [CONFIRMED · fathom/99116769 @ 00:05:00]

## Sources

- `crm/dane-worthington` · — · crm · CRM numbers — Dane Worthington
- `fathom/99116769` · 2025-11-04 · fathom · Impromptu Zoom Meeting
