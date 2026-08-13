# CMF Publishing — Dossier

_Compiled 2026-08-13 13:36 IST · 9 client facts (9 trusted) from 2 sources._

> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED** are client-facing; _INFERRED_ is internal-only.

## The numbers

- Normal expected EPC for the TMS account is approximately $5; the trend chart is currently showing near-zero EPC for most days, indicating an attribution bug. [CONFIRMED · fathom/172525326 @ 00:05:48]
- Revenue to date: $1,273 across 21 earning transactions (2023-05-28 → 2023-09-24). [CONFIRMED · CRM:transactions]

## Architecture & systems

- Backend sales are currently excluded from the dashboard because ROAS values were missing for those records; a toggle button to include backend sales can be added if needed. [CONFIRMED · fathom/172525326 @ 00:03:00]
- Historical variant data for the Curve account shows 'X' instead of a real variant label (e.g. V5) because the old data has not been backfilled yet; backfilling may be done to make the charts look more sensible. [CONFIRMED · fathom/172525326 @ 00:06:31]
- The dashboard has a dropdown to filter by page type (landing page vs. order page) and identifies variants based on URL. For order pages, some variants are not yet tracked due to historical data gaps. [CONFIRMED · fathom/172525326 @ 00:01:51]
- The dashboard includes a tab showing how many users landed from each ad, and a trend chart intended to display EPC vs CPC metrics; EPC data is currently not being attributed correctly and is a work in progress. [CONFIRMED · fathom/172525326 @ 00:03:53]
- The funnel dashboard is hosted on Render and accessible via a '/control' route; the link will be shared with the team via Slack once data accuracy is verified. [CONFIRMED · fathom/172525326 @ 00:08:06]
- The funnel dashboard tracks landing page variants, pulling variant data from ClickFunnels for the TMS account (e.g. variant V5 for Curve), and displays metrics including how many users landed per variant. [CONFIRMED · fathom/172525326 @ 00:01:04]

## Commitments

- The team agreed to verify that all dashboard data and charts are accurate before sharing the funnel dashboard link with the broader team; Shruti will drop the update only once data is confirmed correct. [CONFIRMED · fathom/172525326 @ 00:08:20]

## Sources

- `crm/cmf-publishing` · — · crm · CRM numbers — CMF Publishing
- `fathom/172525326` · 2026-08-12 · fathom · Impromptu Google Meet Meeting
