-- 011 finance: statement WINDOWS on account_balances. Bank exports can OVERLAP
-- (a fresh download re-covers months already imported — 2026-07-07 cockpit
-- upload incident: old idfc statement ..2026-06-25, new export 2026-04-02..
-- 2026-06-30). #1 attributed flow by source_file — but canonical-key-guarded
-- inserts keep FIRST-import attribution, so an overlapping statement saw only
-- its genuinely-new rows and went falsely RED (-73 paise); #2 compared
-- closing(Jun-25) to opening(Apr-2). Flow must be attributed by ACCOUNT+WINDOW.
-- period_start/period_end = the statement's own coverage; NULL = pre-011 row
-- (checks fall back to source_file semantics).
ALTER TABLE account_balances ADD COLUMN period_start TEXT;
ALTER TABLE account_balances ADD COLUMN period_end TEXT;
-- backfill the two known idfc file statements (windows read from the files)
UPDATE account_balances SET period_start='2025-06-27', period_end='2026-06-25'
 WHERE source_file='idfc-60606200234.xlsx';
UPDATE account_balances SET period_start='2026-04-02', period_end='2026-06-30'
 WHERE source_file LIKE 'IDFCFIRSTBankstatement%';
