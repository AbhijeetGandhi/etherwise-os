-- 004_kb.sql — Knowledge (M3) domain: cited facts + per-client dossiers + FTS.
-- Decision #7: markdown in git for dossiers/wiki; SQLite kb domain + FTS here.
-- Citations are MANDATORY and code-enforced; the schema is defense-in-depth
-- (citation NOT NULL + confidence CHECK). No uncited fact persists.

-- Provenance: one row per ingested touchpoint (call/thread/email/...).
CREATE TABLE sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,          -- fathom|upwork|crm|email|loom|slack|clickup
    source_ref TEXT NOT NULL,           -- recording_id | thread_id | file | ...
    content_hash TEXT NOT NULL UNIQUE,  -- sha256(normalized content) — dedup key
    client_id TEXT,                     -- canonical client key, or NULL = unmatched
    client_name TEXT,
    content_type TEXT,                  -- client-call|sales|capability|internal|unknown
    density REAL,                       -- 0..1 information density (grade)
    title TEXT,
    occurred_dt TEXT,                   -- when the touchpoint happened (ISO)
    raw_path TEXT,                      -- gitignored disk path to the raw payload
    status TEXT NOT NULL DEFAULT 'ingested',  -- ingested|extracted|skipped|quarantined
    ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_sources_client ON sources(client_id);
CREATE INDEX idx_sources_status ON sources(status);

-- Cited facts. citation + confidence are NOT NULL / CHECK-constrained — the
-- DB itself refuses an uncited or untagged fact (SCHEMA.md, code-enforced).
CREATE TABLE facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    client_id TEXT,
    category TEXT,                      -- overview|people|numbers|timeline|architecture|quote|commitment|stack
    fact_text TEXT NOT NULL,
    citation TEXT NOT NULL,             -- "<source_ref> @ <locator>" — MANDATORY
    confidence TEXT NOT NULL
        CHECK (confidence IN ('CONFIRMED', 'CROSS-VERIFIED', 'INFERRED')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_facts_client ON facts(client_id);
CREATE INDEX idx_facts_source ON facts(source_id);

-- Per-client dossier registry (the markdown brain lives in knowledge/dossiers/).
CREATE TABLE dossier_index (
    client_id TEXT PRIMARY KEY,
    client_name TEXT,
    dossier_path TEXT,
    fact_count INTEGER DEFAULT 0,
    source_count INTEGER DEFAULT 0,
    last_compiled_at TEXT
);

-- FTS over facts (search the extracted, cited knowledge).
CREATE VIRTUAL TABLE facts_fts USING fts5(fact_text, content='facts', content_rowid='id');
CREATE TRIGGER facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, fact_text) VALUES (new.id, new.fact_text);
END;
CREATE TRIGGER facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, fact_text) VALUES ('delete', old.id, old.fact_text);
END;
CREATE TRIGGER facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, fact_text) VALUES ('delete', old.id, old.fact_text);
    INSERT INTO facts_fts(rowid, fact_text) VALUES (new.id, new.fact_text);
END;

-- FTS over raw transcript/source text (search hits the source material too).
CREATE VIRTUAL TABLE source_fts USING fts5(text, source_id UNINDEXED);
