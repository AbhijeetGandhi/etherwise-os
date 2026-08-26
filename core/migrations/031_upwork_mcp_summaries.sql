-- 031: upwork_mcp_summaries — Upwork's own weekly financial summary, one row
-- per (org, period), as the MCP returns it (Upwork chat 2026-08-26, use case A
-- step 3: the continuous tie-out that replaces the CSV's running-balance
-- check; Abhijeet OK'd the capture at 17:55 — tests/fixtures/mcp/
-- financials_summary_personal.json).
--
-- The summary is per-client billed / fee / tax / earned over a period, by
-- INVOICE date — the same rows `transactions` returns, added up by Upwork.
-- The daily rail (upwork_financials) stores every completed Mon–Sun week it
-- has not stored yet; bin/doctor's "upwork weekly tie-out" adds up what the
-- rail read (upwork_mcp_rows) for the same period and compares, exact in
-- minor units. A gap means a row the rail never saw, or a kind the summary
-- counts that the ledger does not — either way a human reads it.
CREATE TABLE IF NOT EXISTS upwork_mcp_summaries (
    profile            TEXT NOT NULL,                 -- Personal | Agency
    period_start       TEXT NOT NULL,                 -- YYYY-MM-DD (inclusive)
    period_end         TEXT NOT NULL,                 -- YYYY-MM-DD (inclusive)
    billed_minor       INTEGER,                       -- totals.billed, USD cents
    fee_minor          INTEGER,                       -- totals.fee (negative)
    tax_minor          INTEGER,                       -- totals.tax (negative)
    earned_minor       INTEGER,                       -- totals.earned
    currency           TEXT,
    earnings_available INTEGER NOT NULL DEFAULT 1,    -- 0 = Upwork said no data; the note is in payload_json
    payload_json       TEXT,                          -- the answer verbatim (by_client, totals, note)
    fetched_at         TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (profile, period_start)
);
