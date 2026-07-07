-- 012: wise balance-row corrections (2026-07-07 confidence audit). The wise
-- importer wrote ONE balance row per RUN from mixed-file rows: the 2026-07-03
-- statement row carried the OLD file's source_file and no window, so #1 passed
-- only because the Wise USD account hovers at $0 (coincidental, not
-- structural). Backfill both rows' windows (the exports' claimed ranges from
-- the Wise filenames) and point the 07-03 row at its actual file. The importer
-- now writes per-file rows with windows; these UPDATEs are no-ops on fresh DBs.
UPDATE account_balances SET period_start='2023-09-19', period_end='2026-06-26'
 WHERE account='wise' AND statement_date='2026-06-19' AND period_start IS NULL;
UPDATE account_balances SET period_start='2026-06-01', period_end='2026-07-06',
 source_file='statement_68202295_USD_2026-06-01_2026-07-06.csv'
 WHERE account='wise' AND statement_date='2026-07-03' AND period_start IS NULL;
