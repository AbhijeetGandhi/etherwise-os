-- 008 finance: per-row economic category for the P&L bucket + transfer-integrity
-- (§3.10). The IDFC importer classifies each statement row (internal_sweep /
-- transfer_in / cc_settlement / owner_draw / salary / expense / income); Wise
-- classifies income / transfer_out / fee. This column carries that classification
-- onto the ledger row so the transfers bucket (#7) and Phase-3 P&L can exclude
-- the right rows without re-deriving from description. NULL = unclassified
-- (legacy / Upwork rows that use type/accounting_subtype instead).
ALTER TABLE transactions ADD COLUMN category TEXT;
