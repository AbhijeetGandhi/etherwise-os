-- 015: attach anomalies to the run that raised them.
--
-- Source-health degradation ('the scanner returned a thin feed this run') must
-- reflect the CURRENT state, not all of history. Correlating by timestamp is
-- unsafe: runs.started_at is IST with an offset while anomalies.detected_at is
-- UTC space-separated, so string compares mix zones. A run_id link is exact.
--
-- It is also self-healing: a healthy run raises no new anomaly, so the previous
-- degrade anomaly (bound to an OLDER run_id) stops matching 'the latest run of
-- this task'. No resolver, no cron sweep — health clears the moment the source
-- recovers. Historical rows keep run_id = NULL and are simply ignored.

ALTER TABLE anomalies ADD COLUMN run_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_anomalies_run ON anomalies(run_id, kind)
    WHERE run_id IS NOT NULL;
