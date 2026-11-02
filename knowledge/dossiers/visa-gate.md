# Visa Gate GmbH — Dossier

_Compiled 2026-11-02 05:16 IST · 20 client facts (20 trusted) from 2 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- A previous bot attempt (built with Python/JSON, hard-coded selectors) failed because the AutoPay site uses a headless CMS and Google reCAPTCHA v2, which detected and blocked the bot. [CONFIRMED · fathom/64401809 @ 00:01:00]
- Visa Gate GmbH needs a web scraper/automation bot to purchase Austrian motorway vignettes (tolling system) on the AutoPay platform and return purchase data to the client, with orders flowing in via email and results logged to a CRM. [CONFIRMED · fathom/64401809 @ 00:03:06]

## The numbers

- Contract: AI-powered purchasing Bot — status CLOSED. [CONFIRMED · CRM:contracts/40655756]
- Contract: Postal forwarding service — status CLOSED. [CONFIRMED · CRM:contracts/40951616]
- Revenue to date (GROSS, billed): $1,013.75 across 6 revenue transactions (2025-06-11 → 2025-07-23). [CONFIRMED · CRM:transactions[pnl_bucket=revenue]]
- Revenue to date (NET, received after Upwork fees and tax): $899.87 — gross $1,013.75 less $112.76 Upwork service fees, $1.12 tax withheld (GST/WHT). [CONFIRMED · CRM:transactions[pnl_bucket=revenue+refund+platform_fee+tax]]
- The project budget was raised from approximately $600 to approximately $900 (roughly 50% increase) across three defined steps. [CONFIRMED · fathom/64401809 @ 00:01:00]

## People

- Abhijeet Gandhi (the automation developer/agency contact) primarily works with US and UK clients and operates in late-night hours accordingly; he also works on weekends. [CONFIRMED · fathom/64401809 @ 00:00:24]
- Primary contacts at Visa Gate GmbH are Raphael Mahir and Martin; Martin was absent from this call due to a conflicting meeting. [CONFIRMED · fathom/64401809 @ 00:00:36]

## Architecture & systems

- A queuing system is required to serialise concurrent orders; even with ~10 orders/day, simultaneous submissions are considered likely and would break the bot without queuing. [CONFIRMED · fathom/64401809 @ 00:02:21]
- IP rotation will be used continuously so that requests never originate from the same IP, avoiding detection and flagging by the Austrian Motorway Organisation. [CONFIRMED · fathom/64401809 @ 00:10:54]

## Stack & tools

- Abhijeet identified the need for rotating residential proxies (preferred provider: Oxylabs) and a CAPTCHA-solving service to bypass anti-bot blockers on the AutoPay site. [CONFIRMED · fathom/64401809 @ 00:03:53]
- The new automation will be built in Python using Selenium WebDriver to handle browser interactions and bypass anti-bot protections. [CONFIRMED · fathom/64401809 @ 00:04:51]

## Timeline

- Target delivery is two weeks with a one-week buffer (i.e. three weeks worst case); Visa Gate has an upcoming holiday deadline creating urgency. [CONFIRMED · fathom/64401809 @ 00:06:48]
- Visa Gate will record a Loom-style walkthrough video of the manual purchase process over the weekend and deliver it by Sunday; Abhijeet will start work once the video is received. [CONFIRMED · fathom/64401809 @ 00:06:10]

## Commitments

- Abhijeet will send Visa Gate a list of recommended proxy providers and CAPTCHA-solving services with pricing options after the call. [CONFIRMED · fathom/64401809 @ 00:11:35]
- Abhijeet will share the Fathom call notes/transcript link with Raphael so he can forward it to Martin. [CONFIRMED · fathom/64401809 @ 00:14:39]

## Other

- A previous developer from Tunisia built only a test bot that pinged the AutoPay API ~10 times per day; the Austrian Motorway Organisation detected the pings and alerted AutoPay, causing the project to be flagged. [CONFIRMED · fathom/64401809 @ 00:10:08]
- AutoPay sells Austrian motorway vignettes at the same price as the initiating organisation (Austrian Motorway Organisation); the target market operator is described as staffed by ex-McKinsey people who actively monitor for grey-market bulk-buying patterns such as multiple credit card or email usage. [CONFIRMED · fathom/64401809 @ 00:07:38]
- Visa Gate is considering using flexible/disposable email services (e.g. Addy.io) to avoid pattern detection by the platform. [CONFIRMED · fathom/64401809 @ 00:08:40]

## Sources

- `crm/visa-gate` · — · crm · CRM numbers — Visa Gate GmbH
- `fathom/64401809` · 2025-05-23 · fathom · Kick-off Call AI-BOT
