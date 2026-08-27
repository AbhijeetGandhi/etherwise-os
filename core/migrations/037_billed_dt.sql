-- What we BILLED, beside what we RECEIVED.
--
-- Abhijeet, 2026-08-27: "I wanna see both. One is how much money we billed to
-- the clients in a month, and then actual receivables to the account in that
-- month as well. For example, this month we billed for like twenty five
-- thousand dollars, but in account we received like twenty two thousand,
-- which could be because three thousand is gonna come in next month."
--
-- The ledger has only ever had ONE date: `creation_dt`, which for Upwork CSV
-- rows is the PAYOUT date. So "revenue this month" has always meant cash
-- received, and the billed view did not exist. Re-dating those rows would have
-- destroyed the cash view to build the billed one — measured on 2026-08-27,
-- July would have moved by $1,264.41 — so instead both dates live side by side
-- and the gap between them is the answer to "where is the rest of it".
--
--   creation_dt  when the money reached the account   -> RECEIVED
--   billed_dt    when the client was invoiced for it  -> BILLED
--   pnl_month    the manual accrual override, untouched, still wins
--
-- Filled from `upwork_mcp_rows.invoice_dt` (Upwork's own invoice date, which
-- the MCP carries and the CSV does not) and by the ledger rail from now on.
-- NULL means we do not know the invoice date — every reader falls back to
-- creation_dt, so an unknown never invents a gap.

ALTER TABLE transactions ADD COLUMN billed_dt TEXT;
CREATE INDEX idx_transactions_billed ON transactions(billed_dt);

UPDATE transactions
   SET billed_dt = (SELECT r.invoice_dt FROM upwork_mcp_rows r
                     WHERE r.record_id = transactions.record_id)
 WHERE billed_dt IS NULL
   AND EXISTS (SELECT 1 FROM upwork_mcp_rows r2
                WHERE r2.record_id = transactions.record_id
                  AND r2.invoice_dt IS NOT NULL);
