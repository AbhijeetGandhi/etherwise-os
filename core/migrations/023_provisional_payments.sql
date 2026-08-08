-- 023_provisional_payments.sql — the finance payment-mail rail's PROVISIONAL store.
--
-- Payment events read (read-only) from Wise/Stripe notification emails in inbox
-- 01. These are REPORTED money, not ledger truth: they exist so the cockpit can
-- say "reported by Wise email, awaiting statement" and so a stale CSV import
-- announces itself instead of rendering a confidently-wrong monthly total.
--
-- DELIBERATELY SEPARATE from `transactions`. doctor / pnl / cash / the P&L
-- NEVER read this table, so a provisional row CANNOT enter the real ledger and
-- CANNOT double-count. The authoritative CSV/statement import stays the only
-- writer of `transactions`; reconcile only flips a provisional row to
-- 'confirmed' once its real ledger twin lands. Additive / non-destructive.
CREATE TABLE IF NOT EXISTS provisional_payments (
    canonical_key    TEXT PRIMARY KEY,   -- wise-email:<msgid> | stripe-email:<msgid> (unique, immutable)
    provider         TEXT NOT NULL,       -- wise | stripe
    payer            TEXT,                -- raw payer name from the notification
    client_id        TEXT,                -- attributed client, or NULL -> mapping queue (never guessed)
    amount_minor     INTEGER NOT NULL,    -- signed minor units, incoming positive, original currency
    currency         TEXT NOT NULL,
    paid_date        TEXT,                -- date the email reports (YYYY-MM-DD)
    provider_ref     TEXT,                -- provider's own id if the email carries one
    native_id        TEXT NOT NULL,       -- RFC822 Message-ID (provenance)
    permalink        TEXT,                -- one-click-to-source deep link
    raw_ref          TEXT,                -- imap/<inbox>/<uid>
    state            TEXT NOT NULL DEFAULT 'provisional',  -- provisional | confirmed | superseded
    matched_canonical TEXT,              -- the ledger canonical_key it reconciled to (when confirmed)
    seen_at          TEXT NOT NULL DEFAULT (datetime('now')),
    confirmed_at     TEXT,
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_provisional_state ON provisional_payments(state);
CREATE INDEX IF NOT EXISTS idx_provisional_client ON provisional_payments(client_id);
CREATE INDEX IF NOT EXISTS idx_provisional_paid ON provisional_payments(paid_date);
