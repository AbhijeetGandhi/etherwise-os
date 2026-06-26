-- 009 finance: persist the P&L bucket so the money picture is REPRODUCIBLE from
-- stored data + auditable by query (Abhijeet 2026-06-26). The stored `category`
-- (set per-source) and the computed pnl.bucket() disagreed (transfer +$28k vs
-- -$65k), so the P&L could not be re-derived from the DB. pnl_bucket is the ONE
-- authoritative bucket: bin/finance-bucketize writes pnl.bucket(row) to every
-- row; bin/finance-pnl aggregates from it; the doctor verifies it still matches
-- bucket() (reproducibility). NULL = not yet bucketized.
ALTER TABLE transactions ADD COLUMN pnl_bucket TEXT;
