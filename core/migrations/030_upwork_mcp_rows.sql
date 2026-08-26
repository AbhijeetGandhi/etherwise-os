-- 030: upwork_mcp_rows — every transaction the MCP financials rail has read,
-- one row per Upwork record (Upwork chat, 2026-08-26, use case A of
-- docs/upwork/MCP_USE_CASES.md: "retire the CSV import").
--
-- WHY a table of its own. The ledger (`transactions`) keys Upwork money on
-- `upwork:<recordId>` whichever path brought it — the CSV import, the old
-- GraphQL sync, now the MCP — and an upsert on an existing row leaves no trace
-- of WHO saw it. So "did the MCP see every row the CSV has?" cannot be
-- answered from the ledger in either direction: once the MCP inserts first,
-- the CSV import finds the row present and moves on; once the CSV inserted
-- first, the MCP's read is an invisible status update. This table is the MCP
-- side of that tie-out: what it read, when, with the signed amount it would
-- have stored, and what became of the row (inserted / updated an existing row
-- / quarantined when the MCP gave no amount).
-- bin/doctor's "upwork mcp twins" check reads the CSV exports on disk against
-- it; two clean weeks is the criterion for the CSV import going audit-only.
--
-- Read-only on Upwork; nothing here is money — the ledger stays the ledger.
CREATE TABLE IF NOT EXISTS upwork_mcp_rows (
    record_id          TEXT PRIMARY KEY,
    profile            TEXT NOT NULL,                 -- personal | agency (the org it was read from)
    upwork_type        TEXT,                          -- APInvoice | APAdjustment | ARInvoice | ARPayment | APPayment
    accounting_subtype TEXT,                          -- Hourly | Service Fee | WHT | Membership Fee | Payment | Withdrawal ...
    amount_minor       INTEGER,                       -- signed, the ledger's convention; NULL = the MCP gave no amount
    currency           TEXT,
    invoice_dt         TEXT,                          -- transactionCreationDate: the INVOICE date (the CSV carries the payment date)
    status             TEXT,
    company            TEXT,                          -- assignmentCompanyName
    outcome            TEXT NOT NULL,                 -- inserted | updated | quarantined
    first_seen_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at       TEXT NOT NULL DEFAULT (datetime('now')),
    seen_runs          INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_upwork_mcp_rows_seen ON upwork_mcp_rows(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_upwork_mcp_rows_invoice ON upwork_mcp_rows(invoice_dt);
