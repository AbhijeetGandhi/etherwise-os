-- 021: EXTRACT stage (Jarvis J2 §4.3 stage 3) — SHADOW-FIRST staging.
--
-- The READER (Sonnet, no tools) turns MAPPED events into candidate knowledge.
-- NOTHING lands in live facts/commitments/cards here: every row goes to a
-- staging table keyed by the sweep run, reviewed via bin/jarvis-extract-review,
-- and promoted only on --promote after Abhijeet reviews the first real batch (◆).
-- Provenance is code-enforced AND schema-enforced: citation/source_cite/evidence
-- are NOT NULL — an uncited claim is dropped before it can reach staging.

-- Idempotency marker: the READER stamps each event it has processed, so a re-run
-- never re-extracts (and empty-skip is a cheap COUNT). Extraction is done at
-- STAGING time; promotion is a separate, later, human-gated step.
ALTER TABLE events ADD COLUMN extracted_at TEXT;
CREATE INDEX IF NOT EXISTS idx_events_extracted ON events(state, extracted_at);

-- Facts candidates. access_scope carries the founder|team wall (see below).
-- origin distinguishes a real fact from a mint-bar-rejected commitment demoted
-- to a fact (architecture: "otherwise it is a fact, not a commitment").
CREATE TABLE IF NOT EXISTS extract_staging_facts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        INTEGER,
  client_id     TEXT NOT NULL,
  event_id      TEXT,                     -- source event (evt:email:<id>)
  category      TEXT,                     -- kb category (overview|people|numbers|...)
  fact_text     TEXT NOT NULL,
  citation      TEXT NOT NULL,            -- "<permalink> @ <anchor>" — MANDATORY
  confidence    TEXT NOT NULL
                CHECK (confidence IN ('CONFIRMED','CROSS-VERIFIED','INFERRED')),
  access_scope  TEXT NOT NULL
                CHECK (access_scope IN ('founder','team')),
  origin        TEXT NOT NULL DEFAULT 'fact',   -- fact | demoted_commitment
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  promoted_at   TEXT,                     -- set by --promote (idempotency guard)
  promoted_ref  INTEGER                   -- facts.id once promoted
);
CREATE INDEX IF NOT EXISTS idx_estg_facts_run ON extract_staging_facts(run_id);
CREATE INDEX IF NOT EXISTS idx_estg_facts_promo ON extract_staging_facts(promoted_at);

-- Commitment candidates that PASSED the mint-bar. dedup_key matches the live
-- commitments convention so promotion dedups against anything already there.
CREATE TABLE IF NOT EXISTS extract_staging_commitments (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        INTEGER,
  client_id     TEXT NOT NULL,
  event_id      TEXT,
  who           TEXT NOT NULL,            -- us | client
  text          TEXT NOT NULL,
  due           TEXT,                     -- ISO date or NULL
  ask           INTEGER NOT NULL DEFAULT 0,  -- explicit-ask signal (mint-bar input)
  source_cite   TEXT NOT NULL,            -- "<permalink> @ <anchor>" — MANDATORY
  dedup_key     TEXT NOT NULL,            -- commitment:<hash>
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  promoted_at   TEXT,
  promoted_ref  INTEGER                   -- commitments.id once promoted
);
CREATE INDEX IF NOT EXISTS idx_estg_comm_run ON extract_staging_commitments(run_id);
CREATE INDEX IF NOT EXISTS idx_estg_comm_promo ON extract_staging_commitments(promoted_at);
CREATE INDEX IF NOT EXISTS idx_estg_comm_dedup ON extract_staging_commitments(dedup_key);

-- Closure signals: an email that may resolve a card raised elsewhere (Upwork /
-- Fathom). STAGED only tonight — feeding them into the live cross-source closure
-- pass (matching intent -> open card_id) is a deferred, gated step. evidence is
-- MANDATORY (a closure without a cite is not actionable).
CREATE TABLE IF NOT EXISTS extract_staging_closures (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        INTEGER,
  client_id     TEXT NOT NULL,
  event_id      TEXT,
  intent        TEXT NOT NULL,            -- the ask this email appears to resolve
  evidence      TEXT NOT NULL,            -- "<permalink> @ <anchor>" — MANDATORY
  confidence    REAL NOT NULL,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  applied_at    TEXT,                     -- reserved (closure wiring deferred)
  applied_card_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_estg_clo_run ON extract_staging_closures(run_id);

-- The founder/team ACCESS wall on live facts (architecture §4.4). DISTINCT from
-- facts.scope (client|agency|cross-client artifact routing): this is the data-
-- layer wall the future team chatbot (J5) is filtered on. Default 'team'; the
-- extract promoter sets 'founder' by the hard config rule (money/pricing/HR).
ALTER TABLE facts ADD COLUMN access_scope TEXT NOT NULL DEFAULT 'team'
  CHECK (access_scope IN ('founder','team'));
