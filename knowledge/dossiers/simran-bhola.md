# Simran Bhola — Dossier

_Compiled 2026-11-02 05:16 IST · 45 client facts (45 trusted) from 4 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- The client is a wellness D2C brand in India — an old corporate company moving digital. They sell cough-related products (cough syrups, tablets, etc.) and are currently building a Shopify website. [CONFIRMED · fathom/54780403 @ 00:00:25]
- The client wants to run a pilot QR code assessment initiative with approximately 200–500 retailer stores. [CONFIRMED · fathom/54780403 @ 00:01:00]
- The project involves a web-based tool where users record their voice saying 'I use SaaS' and receive an AI-generated score with a report categorized into low, medium, or high bands. [CONFIRMED · fathom/55718345 @ 00:01:28]

## The numbers

- Abhijeet estimates the CRM integration will take a couple of hours to build, and the AI audio analysis component a couple of hours; overall, one day is more than enough per task once the front end is ready. [CONFIRMED · fathom/54780403 @ 00:13:02]
- Approximately 6 audio sample files (2 per band: low, medium, high) of people saying 'I use SaaS' are needed to begin building and testing the AI analysis. [CONFIRMED · fathom/55718345 @ 00:07:13]
- Contract: Voice Agent Expert (Elevenlabs conversational AI) — status CLOSED. [CONFIRMED · CRM:contracts/40205226]
- Revenue to date (GROSS, billed): $10.00 across 1 revenue transaction (2025-04-18 → 2025-04-18). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $8.99 — gross $10.00 less $1.00 Upwork service fees, $0.01 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]
- The client has 1.8 lakh (180,000) retailer stores in India. [CONFIRMED · fathom/54780403 @ 00:01:00]

## People

- Abhijeet Gandhi started freelancing approximately one and a half years ago, then about a year ago founded his own AI and automation agency with a team of approximately five people. [CONFIRMED · fathom/54780403 @ 00:14:20]
- Abhijeet Gandhi stated the agency continuously hires to match workload and has bench strength to bring more people onto projects as needed; project team is built based on effort and timeline scoped per project. [CONFIRMED · fathom/55718345 @ 00:11:23]
- Simran Bhola is the client-side project manager/contact; Abhijeet Gandhi is the agency lead building the automation. A frontend developer on the client side is involved but was unavailable during the call. [CONFIRMED · fathom/55718345 @ 00:07:41]

## Architecture & systems

- A new manual scoring button was built; clicking it opens a pop-up where a user can submit a Firefly call recording link or upload the transcript file exported from the call recording page. [CONFIRMED · https://www.loom.com/share/43b897457b544489b24e6c7c882717db @ So that's the button, on clicking on it, you see this pop-up]
- An error/fallback screen is displayed if the user is not holding the mic correctly or there is a mic issue. [CONFIRMED · fathom/55718345 @ 00:02:22]
- Based on the score band, a discount code is generated for the user (e.g., a score of 32 in the low band qualifies for a 20% discount), which the retailer then applies manually as there is no integrated retailer system. [CONFIRMED · fathom/55718345 @ 00:09:37]
- CRM integration plan: form submissions (name, age, gender, chemist code) will flow into the CRM so the client can see real-time dashboards — e.g., how many users scanned per retailer code — on a weekly or monthly basis. [CONFIRMED · fathom/54780403 @ 00:04:32]
- Chemist codes map to individual retailer outlets; the CRM will track metrics per outlet such as how many people tried the tool, scores achieved, and discounts offered. [CONFIRMED · fathom/55718345 @ 00:09:06]
- Data fields to be passed from frontend to CRM include: name, phone number, OTP verified status (yes/no), chemist code, gender, and age. [CONFIRMED · fathom/55718345 @ 00:08:40]
- Each call scored through the manual button is added to a table where the score and full breakdown can be reviewed later, and the scored recording can be opened from the button. [CONFIRMED · https://www.loom.com/share/43b897457b544489b24e6c7c882717db @ whatever you score using this button, it also gets added to this table]
- Each retailer/chemist is assigned a unique chemist code. Territory sales managers inform retailers of their code. When a customer scans the QR code and fills out the form, they enter the chemist code so scans can be attributed to specific retailers. [CONFIRMED · fathom/54780403 @ 00:03:26]
- Phone number OTP verification is handled via MSG 9.1, and chemist codes (each representing one retailer outlet) are mapped at the backend during CRM integration. [CONFIRMED · fathom/55718345 @ 00:00:00]
- Project 1: QR codes placed at pilot retailer stores direct users to a lung-health assessment landing page. Users say 'Ayu Ses' (a phrase) for as long as possible; duration is analyzed by AI to score lung health as low, medium, or high. A discount code (10–15% depending on score) is then shown. [CONFIRMED · fathom/54780403 @ 00:02:01]
- Project 2: After checkout from a product landing page, customers can book a free consultation with one of three in-house lung health experts via Cal.com integration. A WhatsApp reminder (via Bik.ai) will be sent immediately upon booking and again one day / half a day before the session. [CONFIRMED · fathom/54780403 @ 00:10:00]
- The Mr. Miyagi bot is currently turned off in the scoring flow; once the flow moves to production it will be activated and will send messages to Slack. [CONFIRMED · https://www.loom.com/share/43b897457b544489b24e6c7c882717db @ Now currently I have kept the Mr. Miyagi bot off. Once it moves to production, it will be activated and will start sending the messages to Slack.]
- The form/UI is custom coded; when a user clicks 'start recording', instructions are shown, an animation plays, the user says 'I use SaaS' for as long as possible, and results are returned as a score on a bar/threshold scale. [CONFIRMED · fathom/55718345 @ 00:01:18]
- The key integration challenge identified is the data transfer format between frontend and backend: the frontend developer needs to provide input JSON schema (audio file/data) and expected output JSON schema (score and any other fields) to Abhijeet. [CONFIRMED · fathom/55718345 @ 00:03:31]
- The manual scoring flow was tested with a Firefly call recording link, and it returned a score of 19 out of 20 with a score breakdown for the call. [CONFIRMED · https://www.loom.com/share/43b897457b544489b24e6c7c882717db @ It gave it 19 out of 20. And for the breakdown of the score and the call.]
- Three different result screens/reports are displayed depending on which band the score falls in: low, medium, or high. [CONFIRMED · fathom/55718345 @ 00:02:02]

## Stack & tools

- Abhijeet plans to use Make.com for the CRM integration. If an existing form tool is used, Make.com will watch for form submissions; if a custom form is used, he will create a Make.com webhook endpoint and share it with the front-end developer. [CONFIRMED · fathom/54780403 @ 00:08:17]
- For the voice/audio assessment, AI will analyze the audio recording of the user saying 'Ayu Ses' and return a lung health score to be displayed in the UI. A threshold (e.g., 20 seconds = high lung health) will be set, possibly informed by querying GPT. [CONFIRMED · fathom/54780403 @ 00:09:39]
- The backend automation will be built using Make.com (webhook triggered by the frontend audio recording) and OpenAI — initially using the Whisper model to transcribe the audio and then analyze it with AI. [CONFIRMED · fathom/55718345 @ 00:02:48]
- The client is likely to use Bik.ai for WhatsApp messaging/reminders. [CONFIRMED · fathom/54780403 @ 00:11:41]
- The client will most likely use Zoho as their CRM; they currently have no CRM as this is a completely new business entity separate from their existing offline business. [CONFIRMED · fathom/54780403 @ 00:04:01]

## Timeline

- The project is broken into two milestones: Milestone 1 is the audio analysis and scoring automation; Milestone 2 involves CRM integration (blocked until client's CRM is ready). [CONFIRMED · fathom/55718345 @ 00:12:33]

## Commitments

- Abhijeet Gandhi committed to delivering an initial automation setup for audio analysis and producing results, demoed via a short video, by end of day of the call. [CONFIRMED · fathom/55718345 @ 00:12:41]
- Agency's own Make.com account and OpenAI API key will be used temporarily until the client sets up their own accounts (estimated 1–2 days for corporate approvals). [CONFIRMED · fathom/55718345 @ 00:06:16]
- Agreed next steps from Abhijeet: (1) share CRM tool suggestions with pros/cons, (2) share form tool options and customizability analysis, (3) send fixed-price quotes for both Project 1 (CRM integration + AI voice scoring) and Project 2 (Cal.com + WhatsApp integration). [CONFIRMED · fathom/54780403 @ 00:16:00]
- Both parties agreed on fixed-price (not hourly) pricing for these projects, given that scope is clearly defined. [CONFIRMED · fathom/54780403 @ 00:17:00]
- Simran Bhola indicated there is additional future project work available and asked about the agency's capacity to run parallel projects. [CONFIRMED · fathom/55718345 @ 00:10:46]
- Simran Bhola will share with Abhijeet after the call: Make.com account details, OpenAI key, audio file samples (approx. 2 per band, 6 total), and frontend developer's questions/requirements on input/output JSON format. [CONFIRMED · fathom/55718345 @ 00:05:13]
- Simran indicated the front-end designer received the full user-flow brief the day before this call; front-end development is expected to take at least one week before integration work can begin. [CONFIRMED · fathom/54780403 @ 00:16:32]
- Simran offered to commit to both projects simultaneously, suggesting a collective price across both could be discussed. [CONFIRMED · fathom/54780403 @ 00:18:24]
- The client was asked to send additional specific call links to be tested through the manual scoring flow. [CONFIRMED · https://www.loom.com/share/43b897457b544489b24e6c7c882717db @ if you want some other specific calls to be tested through this flow, you can send me the links]
- The developer planned further thorough testing of the manual scoring button the following day, then move the button/flow to production. [CONFIRMED · https://www.loom.com/share/43b897457b544489b24e6c7c882717db @ tomorrow after further thorough testing, I will move this flower. Or the button to production]

## Notable quotes

- Simran: 'I am looking for somebody who we see as reliable. I've seen your profile quite well myself, and I've read the client feedbacks as well, something that I really like and that's why I reached out to you.' [CONFIRMED · fathom/54780403 @ 00:13:33]

## Sources

- `crm/simran-bhola` · — · crm · CRM numbers — Simran Bhola
- `fathom/54780403` · 2025-04-01 · fathom · Impromptu Zoom Meeting
- `fathom/55718345` · 2025-04-06 · fathom · Impromptu Google Meet Meeting
- `loom/43b897457b544489b24e6c7c882717db` · 2026-10-06 · loom · Manual Scoring Button Workflow Demo
