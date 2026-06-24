-- 007_finance.sql — M5 correctness foundation (§3). Money becomes integer
-- minor-unit (USD cents / INR paise); float is banned anywhere near money.
-- NON-DESTRUCTIVE: the legacy REAL amount/amount_credited columns are kept
-- read-only through shadow and dropped only at cutover (after the independent
-- audit). bin/finance-migrate populates *_minor from them with a
-- Σ-before==Σ-after proof. Provenance + quarantine are added here too.

-- Money as signed integer minor units (the row's OWN currency: USD=cents,
-- INR=paise). Sign convention §3.10: credits +, debits −. NULL until migrated.
ALTER TABLE transactions ADD COLUMN amount_minor INTEGER;          -- signed
ALTER TABLE transactions ADD COLUMN amount_credited_minor INTEGER; -- signed
-- Provenance (§3.2). File imports (statements) set source_file+source_row;
-- API-sourced rows trace via source='upwork-api' + record_id (documented seam).
ALTER TABLE transactions ADD COLUMN source_file TEXT;
ALTER TABLE transactions ADD COLUMN source_row INTEGER;
-- FX (§3.3 #7) — pinned per-transaction, never re-fetched. Base = USD.
-- USD rows: amount_base_minor == amount_minor, fx_rate '1'. INR rows fill at
-- import. Decimal-as-TEXT for the rate (never a float).
ALTER TABLE transactions ADD COLUMN fx_rate TEXT;        -- Decimal string
ALTER TABLE transactions ADD COLUMN fx_date TEXT;
ALTER TABLE transactions ADD COLUMN amount_base_minor INTEGER;  -- USD cents

-- Quarantine (§3.4): reuse the canonical `quarantine` table (001_ops) — it
-- already carries (source, reason, payload_json, resolved_at, resolution), which
-- is exactly the "never drop a row, preserve + track resolution" contract.
-- Finance rows quarantine with source in (bank|wise|card|upwork|...).

-- Statement balances (§3 cash position; populated from Phase 1 imports). Money
-- as integer minor units; opening+Σcredits−Σdebits==closing is invariant #1.
CREATE TABLE account_balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL,            -- wise|idfc|card|upwork-...
    currency TEXT NOT NULL,
    opening_minor INTEGER NOT NULL,
    closing_minor INTEGER NOT NULL,
    statement_date TEXT NOT NULL,    -- as-of (period end), IST
    source_file TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(account, statement_date, source_file)
);
CREATE INDEX idx_account_balances ON account_balances(account, statement_date);
