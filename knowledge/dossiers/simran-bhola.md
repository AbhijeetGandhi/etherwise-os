# Simran Bhola — Dossier

_Compiled 2026-08-14 00:15 IST · 20 client facts (20 trusted) from 2 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- The project involves a web-based tool where users record their voice saying 'I use SaaS' and receive an AI-generated score with a report categorized into low, medium, or high bands. [CONFIRMED · fathom/55718345 @ 00:01:28]

## The numbers

- Approximately 6 audio sample files (2 per band: low, medium, high) of people saying 'I use SaaS' are needed to begin building and testing the AI analysis. [CONFIRMED · fathom/55718345 @ 00:07:13]
- Revenue to date (GROSS, billed): $10.00 across 1 revenue transaction (2025-04-18 → 2025-04-18). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $8.99 — gross $10.00 less $1.00 Upwork service fees, $0.01 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]

## People

- Abhijeet Gandhi stated the agency continuously hires to match workload and has bench strength to bring more people onto projects as needed; project team is built based on effort and timeline scoped per project. [CONFIRMED · fathom/55718345 @ 00:11:23]
- Simran Bhola is the client-side project manager/contact; Abhijeet Gandhi is the agency lead building the automation. A frontend developer on the client side is involved but was unavailable during the call. [CONFIRMED · fathom/55718345 @ 00:07:41]

## Architecture & systems

- An error/fallback screen is displayed if the user is not holding the mic correctly or there is a mic issue. [CONFIRMED · fathom/55718345 @ 00:02:22]
- Based on the score band, a discount code is generated for the user (e.g., a score of 32 in the low band qualifies for a 20% discount), which the retailer then applies manually as there is no integrated retailer system. [CONFIRMED · fathom/55718345 @ 00:09:37]
- Chemist codes map to individual retailer outlets; the CRM will track metrics per outlet such as how many people tried the tool, scores achieved, and discounts offered. [CONFIRMED · fathom/55718345 @ 00:09:06]
- Data fields to be passed from frontend to CRM include: name, phone number, OTP verified status (yes/no), chemist code, gender, and age. [CONFIRMED · fathom/55718345 @ 00:08:40]
- Phone number OTP verification is handled via MSG 9.1, and chemist codes (each representing one retailer outlet) are mapped at the backend during CRM integration. [CONFIRMED · fathom/55718345 @ 00:00:00]
- The form/UI is custom coded; when a user clicks 'start recording', instructions are shown, an animation plays, the user says 'I use SaaS' for as long as possible, and results are returned as a score on a bar/threshold scale. [CONFIRMED · fathom/55718345 @ 00:01:18]
- The key integration challenge identified is the data transfer format between frontend and backend: the frontend developer needs to provide input JSON schema (audio file/data) and expected output JSON schema (score and any other fields) to Abhijeet. [CONFIRMED · fathom/55718345 @ 00:03:31]
- Three different result screens/reports are displayed depending on which band the score falls in: low, medium, or high. [CONFIRMED · fathom/55718345 @ 00:02:02]

## Stack & tools

- The backend automation will be built using Make.com (webhook triggered by the frontend audio recording) and OpenAI — initially using the Whisper model to transcribe the audio and then analyze it with AI. [CONFIRMED · fathom/55718345 @ 00:02:48]

## Timeline

- The project is broken into two milestones: Milestone 1 is the audio analysis and scoring automation; Milestone 2 involves CRM integration (blocked until client's CRM is ready). [CONFIRMED · fathom/55718345 @ 00:12:33]

## Commitments

- Abhijeet Gandhi committed to delivering an initial automation setup for audio analysis and producing results, demoed via a short video, by end of day of the call. [CONFIRMED · fathom/55718345 @ 00:12:41]
- Agency's own Make.com account and OpenAI API key will be used temporarily until the client sets up their own accounts (estimated 1–2 days for corporate approvals). [CONFIRMED · fathom/55718345 @ 00:06:16]
- Simran Bhola indicated there is additional future project work available and asked about the agency's capacity to run parallel projects. [CONFIRMED · fathom/55718345 @ 00:10:46]
- Simran Bhola will share with Abhijeet after the call: Make.com account details, OpenAI key, audio file samples (approx. 2 per band, 6 total), and frontend developer's questions/requirements on input/output JSON format. [CONFIRMED · fathom/55718345 @ 00:05:13]

## Sources

- `crm/simran-bhola` · — · crm · CRM numbers — Simran Bhola
- `fathom/55718345` · 2025-04-06 · fathom · Impromptu Google Meet Meeting
