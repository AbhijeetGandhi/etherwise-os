# Renutech Ltd — Dossier

_Compiled 2026-08-13 05:02 IST · 11 client facts (11 trusted) from 2 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## Overview

- The client's workflow involves inputting city/location names in bulk (e.g. Anaheim, Los Angeles) so the scraper generates SEO keyword data for each location sequentially; a prior SOD referenced a cities file filtered by population range of 40,000–50,000. [CONFIRMED · fathom/64678208 @ 00:13:04 – 'on the original SOD, Arisha was referencing a file that said 40,000 to 50,000, for example, which was the population range for that set of cities']

## The numbers

- Contract: Fixing / Developing Jupyter Notebook Scaper Application — status ACTIVE. [CONFIRMED · CRM:contracts/40100550]
- Revenue to date: $609 across 8 earning transactions (2025-03-30 → 2025-10-12). [CONFIRMED · CRM:transactions]

## People

- The two people on the call are Stephen Whitehouse (client) and Abhijeet Gandhi (agency/developer). A person named Arisha is also associated with the project (referenced the population-range cities file in the original SOD). [CONFIRMED · fathom/64678208 @ 00:13:04 – 'Arisha was referencing a file']

## Architecture & systems

- A second (bulk) script is designed to read from the stem keyword output file and iterate through each entry one by one, automatically updating the SEO.xlsx master sheet and creating ancillary files for each location — replacing the need to manually input locations one at a time. [CONFIRMED · fathom/64678208 @ 00:20:10 – 'the other script will run off of this stem keyword file. So then it will just pick up and then keep going through each one']
- A stem keyword generation script reads from 'sample_keywords.xlsx', calls the OpenAI API to generate stem keywords, and outputs a file (e.g. sample_keywords_with_stem.xlsx) that serves as input for the next bulk-processing script. [CONFIRMED · fathom/64678208 @ 00:14:54 – 'sample keywords dot xlss is where these names are stored and then this file will run and generate all the stem keywords']
- The master SEO.xlsx sheet contains keyword phrases and CPC data; ancillary files (e.g. Google My Business profile insight files) are also generated as separate .xlsx files alongside it. [CONFIRMED · fathom/64678208 @ 00:10:14 – 'this is our master sheet… we've got our CPC in here as well' and 00:21:21 – 'it will simultaneously create all the ancillary files']
- The project folder on the client's desktop is named 'SEO scraper 2025' and contains at minimum: an SEO keyword updated notebook, an SEO.xlsx master sheet, a stem keyword generation notebook, and a sample keywords .xlsx input file. [CONFIRMED · fathom/64678208 @ 00:03:13 – 'SEO scraper 2025' and 00:14:05 – 'we actually had two more files. One is stem keyword generation']

## Stack & tools

- The SEO keyword scraper tool runs inside a Jupyter Notebook (also compatible with JupyterLab), launched from the terminal, and outputs results to a local Excel (.xlsx) file on the client's Mac desktop rather than to a browser-based output. [CONFIRMED · fathom/64678208 @ 00:01:27 – 'we're just going to have everything go into the local file on Excel']
- The project uses the Python 'openai' library (pip install openai), which was not yet installed on the client's machine at the time of the call and had to be installed during the session. [CONFIRMED · fathom/64678208 @ 00:17:00 – 'pip install openai… it's just that library was not installed']

## Commitments

- Abhijeet committed to spending 15–20 minutes locating the bulk-processing script configuration, recording a Loom walkthrough, and scheduling a follow-up call the next day if needed. Stephen committed to writing an SOP as he works through the process. [CONFIRMED · fathom/64678208 @ 00:21:07 – 'I just need like 15-20 minutes to find where it is, and then I will record the Loom' and 00:21:26 – 'I'll literally write out the SOP as I do it']

## Sources

- `crm/renutech-ltd` · — · crm · CRM numbers — Renutech Ltd
- `fathom/64678208` · 2025-05-26 · fathom · Abhi <> Stephen
