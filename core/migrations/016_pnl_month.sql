-- 016: effective-month (accrual) override for the monthly P&L.
--
-- A salary paid on Jul 2 that was FOR June must land in JUNE's revenue/expense/
-- profit split, not July's (Abhijeet, 2026-08-03). The payment DATE stays as it
-- is (cash + statement integrity depend on it); pnl_month, when set to a
-- 'YYYY-MM', overrides which month the monthly P&L attributes the row to. NULL
-- means 'use the transaction date' — the normal case.
--
-- Only the monthly SPLIT changes; the all-time P&L, the bucket, and the cash
-- reconciliation are untouched, so finance-doctor is unaffected.

ALTER TABLE transactions ADD COLUMN pnl_month TEXT;
