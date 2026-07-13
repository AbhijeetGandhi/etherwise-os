-- 014: persist the ICP / Prime tier on scored_jobs.
--
-- icp.classify() is deterministic over columns we already store (title,
-- description, skills_json, contract_type, hourly_*, fixed_budget,
-- engagement_duration, client_total_spent, client_hires) plus the score and its
-- breakdown — so the tier was being computed at scan time and thrown away.
-- Persisting it makes lead quality a first-class, chartable, drillable history
-- instead of a number that only ever existed inside one run's metrics.
--
-- Values: 'icp' | 'icp_near' | NULL (NULL = not prime, or never classified).
-- Backfill: bin/icp-backfill (recomputes every scored row from stored fields).

ALTER TABLE scored_jobs ADD COLUMN icp_tier TEXT;

CREATE INDEX IF NOT EXISTS idx_scored_jobs_icp_tier
    ON scored_jobs(icp_tier) WHERE icp_tier IS NOT NULL;
