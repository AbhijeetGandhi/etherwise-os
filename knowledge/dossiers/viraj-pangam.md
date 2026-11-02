# Viraj Pangam — Dossier

_Compiled 2026-11-02 05:16 IST · 21 client facts (21 trusted) from 2 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- A future automation goal (trade side) is an app that takes a company name as input, builds context on how that company should view the India market, and outputs a uniform market research report; this effort is planned roughly four months out. [CONFIRMED · fathom/106559638 @ 00:15:15 / 'we give the name of the company and it...develops its own context...gives out a uniform report in a particular format']
- Mayflower Consulting represents different geographies and regions across North America, Western Europe, and the Middle East. [CONFIRMED · fathom/106559638 @ 00:00:35]
- Mayflower Consulting's FDI work involves finding Indian companies ready to expand into the US (manufacturing, R&D, or larger office presence), engaging them, and highlighting the State of New York's value propositions. [CONFIRMED · fathom/106559638 @ 00:01:08]
- The agency (Etherwise) is registered in Pune and operates as a fully remote team with no physical office. [CONFIRMED · fathom/106559638 @ 00:13:38 / 'it's registered in Pune but we don't have like a physical office or something we are a completely remote team']
- The agency's engagement model is to start with one or two automations to build trust and understanding, then progressively automate broader operational processes (e.g., entire interview/hiring workflows) to minimize human intervention. [CONFIRMED · fathom/106559638 @ 00:12:45 / 'we start with creating one or two automations to build that trust...And then we take over']
- The primary automation need is an FDI signal-detection system: identifying Indian companies with expansion signals (revenue size, existing US sales presence) without manually visiting five or more databases. [CONFIRMED · fathom/106559638 @ 00:01:08 / 'we have to find companies from India who are ready to expand into the US']

## The numbers

- Contract: 30 minute consultation — status CLOSED. [CONFIRMED · CRM:contracts/42344926]
- Since using GPT for market research reports, the time to produce a report has dropped from seven days to two days internally. [CONFIRMED · fathom/106559638 @ 00:14:56 / 'We earlier used to take seven days. Now it just takes two.']

## People

- Abhijeet Gandhi is the agency contact; he studied computer science (diploma from Ailanagar, bachelor's from MIT Pune – Dehu Road campus), then worked as a machine learning engineer at a startup for approximately three years before starting his AI and automation agency (Etherwise) over two years ago. [CONFIRMED · fathom/106559638 @ 00:11:32 / 'I did my bachelors from MIT which is in R...the Dehu Fatal one' and 'started my own AI and automation agency. It's been more than two years now']
- Viraj Pangam works at Mayflower Consulting and personally handles the State of New York account, representing them in India for trade and FDI promotion. [CONFIRMED · fathom/106559638 @ 00:00:19 / 'I work at a company called Mayflower Consulting' and 'I personally work for the state of New York']

## Architecture & systems

- Crunchbase is acknowledged as best suited for tech startups and VC-funded companies, which is generally not Mayflower's target segment; their targets are manufacturing and pharmaceutical companies. [CONFIRMED · fathom/106559638 @ 00:05:04 / 'Crunchbase is good at targeting tech startups and VC funded companies. That is not generally what we look for']
- For LinkedIn monitoring, tools such as Phantom Buster or AP5 (pre-built bots) are planned rather than custom-built scrapers, to stay within LinkedIn's rate limits (e.g., no more than ~100 profile scrapes per day). [CONFIRMED · fathom/106559638 @ 00:08:10 / 'we use like the tool that was mentioned, either the Phantom Buster or the AP5']
- Sector-specific journals (e.g., BioSpectrum, Indian Energy Journal) have RSS feeds but no direct API access, requiring a separate pipeline approach such as a scraper bot. [CONFIRMED · fathom/106559638 @ 00:05:38 / 'many of these journals have RSS feeds, but they don't have direct API access']
- The client envisions a Google Sheets-based system where clicking 'Run' triggers multiple APIs, RSS feeds, Google Alerts, and a GPT wrapper to segment results into columns/parameters, delivering a weekly output of the top 5–10 relevant signals. [CONFIRMED · fathom/106559638 @ 00:04:21 / 'I have a Google sheet...I click run...utilizes a bunch of APIs, RSS feeds, Google alerts, and tops it up with the GPT wrapper']
- Three data pipelines are planned: (1) Crunchbase API for tech/VC-funded companies, (2) RSS feeds from sector-specific journals (e.g., BioSpectrum, Indian Energy Journal) for manufacturing/pharma signals, and (3) LinkedIn press monitoring for operational expansion signals. [CONFIRMED · fathom/106559638 @ 00:05:02 / 'these could be the three pipelines']

## Stack & tools

- Feedly is mentioned as a potential aggregator to handle RSS feeds from multiple platforms as part of the journal monitoring pipeline. [CONFIRMED · fathom/106559638 @ 00:07:19 / 'the Feedly is there who provides its own RSS for many different platforms']
- OpenAI (GPT) or another LLM will be used as a validator/classifier on top of the raw signal data to categorize and segment results. [CONFIRMED · fathom/106559638 @ 00:10:11 / 'putting OpenAI or any validator in front of it, it's an easy task']
- The agency has shifted from custom Python scripting to no-code/low-code automation tools as its primary delivery method, reflecting current market trends. [CONFIRMED · fathom/106559638 @ 00:12:19 / 'now, nowadays, the no code tools, local automation tools are just the trend...We build no code automations']

## Timeline

- The client plans to pilot the FDI automation model with the State of New York first, then expand to other FDI clients depending on results, and automate the trade/market-research side approximately four months later. [CONFIRMED · fathom/106559638 @ 00:14:01 / 'piloting this model with my client, which is the state of New York' and 'that's maybe four months down the line']

## Commitments

- Viraj Pangam expressed preference for a long-term formal contractual arrangement outside Upwork, not just a one-off Upwork project. [CONFIRMED · fathom/106559638 @ 00:11:09 / 'we would be happy with having a long term arrangement outside Upwork in a formal sort of a contractual basis']

## Notable quotes

- Abhijeet Gandhi on automation philosophy: 'There is a reason why the manual process that you do works, right? Because it's a working system. So let's just try to automate the exact same steps through automation — let's not rethink the wheel which is already working.' [CONFIRMED · fathom/106559638 @ 00:10:49]

## Sources

- `crm/viraj-pangam` · — · crm · CRM numbers — Viraj Pangam
- `fathom/106559638` · 2025-12-05 · fathom · Impromptu Zoom Meeting
