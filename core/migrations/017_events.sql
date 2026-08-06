-- 017: events — the sweep's normalized ingestion table (Jarvis J1 §4.2/§4.3).
--
-- Every rail (Gmail first; Slack/Teams/Upwork/Fathom in J2) normalizes each new
-- item into ONE events row, canonical-keyed `evt:<source>:<native_id>` so a
-- re-run is idempotent (SELECT-by-id-before-INSERT, the transactions discipline).
-- Nothing is silently dropped: an item is always in exactly one state.
--
--   state = mapped    deterministic registry match -> client_id set
--         | queued    unmatched, a Haiku proposer ran -> awaiting Abhijeet (the queue)
--         | held      unmatched, proposer not yet run (per-run cap) -> next sweep
--         | excluded  noise blocklist / not-a-client -> exclude_reason set
--
-- Per sweep run the counts MUST reconcile: collected = mapped+queued+held+excluded.

CREATE TABLE IF NOT EXISTS events (
  id            TEXT PRIMARY KEY,          -- evt:<source>:<native_id>
  source        TEXT NOT NULL,             -- email|slack|teams|upwork|fathom
  native_id     TEXT NOT NULL,
  ts            TEXT,                       -- occurred datetime (ISO)
  author        TEXT,                       -- sender/handle
  subject       TEXT,
  client_id     TEXT,                       -- NULL until mapped/resolved
  state         TEXT NOT NULL
                CHECK (state IN ('mapped','queued','held','excluded')),
  text          TEXT,                       -- body (NULL for metadata-only excluded)
  permalink     TEXT,                       -- clickable provenance (deep link)
  raw_ref       TEXT,                       -- inbox filepath / source ref
  sweep_run_id  INTEGER,                    -- the runs.id that collected it
  proposed_client_id  TEXT,                 -- Haiku proposal (never authoritative)
  proposal_confidence REAL,
  proposal_evidence   TEXT,
  exclude_reason      TEXT,
  resolved_by         TEXT,                 -- cockpit-approval|hand
  resolved_at         TEXT,
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_state ON events(state);
CREATE INDEX IF NOT EXISTS idx_events_client ON events(client_id);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(sweep_run_id);
CREATE INDEX IF NOT EXISTS idx_events_proposed ON events(proposed_client_id)
  WHERE proposed_client_id IS NOT NULL;
