-- 020: commitment backlog hygiene (J1.5 follow-up). A 400+ open backlog — mostly
-- old and unattributed — distorts urgency and would feed cards + closure chips
-- forever at the 8/sweep drip. review_state flags a commitment as 'stale' (old,
-- not upcoming, no recent activity) so it leaves the card + closure stream and
-- lands on a "stale — confirm dead?" review list. NON-destructive + reversible:
-- clear review_state to restore, or confirm-dead to state='dropped'.

ALTER TABLE commitments ADD COLUMN review_state TEXT;   -- NULL | stale
CREATE INDEX IF NOT EXISTS idx_commitments_review ON commitments(review_state)
  WHERE review_state IS NOT NULL;
