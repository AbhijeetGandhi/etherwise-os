-- 001_ops.sql — operations domain (kernel).
-- Sales/CRM/KB/CoS domains land in 002+ after their design sessions (clean v3
-- names per decision: proposals, threads, messages, scored_jobs — mapped from
-- v2's vendor_proposals/rooms/stories/jobs by bin/import-v2).

-- Every scheduled invocation of every task.
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',         -- running|completed|failed|skipped_empty
    shadow INTEGER NOT NULL DEFAULT 1,              -- was this run in shadow mode?
    metrics_json TEXT,
    anomalies_json TEXT,
    error TEXT,
    duration_ms INTEGER,
    config_sha256 TEXT                              -- integrity stamp (guardrails)
);
CREATE INDEX idx_runs_task_time ON runs(task_name, started_at DESC);

-- Every Claude call (carried from v2, extended with routing info).
CREATE TABLE claude_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    called_at TEXT NOT NULL,
    ist_date TEXT NOT NULL,
    task_name TEXT NOT NULL,
    purpose TEXT,
    model TEXT NOT NULL,
    billing_route TEXT NOT NULL,                    -- credit|payg
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    total_cost_usd REAL DEFAULT 0,
    duration_ms INTEGER,
    tool_calls INTEGER DEFAULT 0,
    stop_reason TEXT,
    error TEXT
);
CREATE INDEX idx_usage_date ON claude_usage(ist_date);

-- Field-level change history + conflicts (sync engine + hooks both write here).
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL DEFAULT (datetime('now')),
    actor TEXT NOT NULL,                            -- task name | 'agent:<name>' | 'human'
    entity TEXT NOT NULL,
    entity_id TEXT,
    field TEXT,
    old_value TEXT,
    new_value TEXT,
    source TEXT,                                    -- airtable|sqlite|upwork|clickup|hook
    note TEXT
);

-- Tripwires: volume bands, stale cursors, spend deviation (pre-flight: alert, not silence).
CREATE TABLE anomalies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    task_name TEXT NOT NULL,
    kind TEXT NOT NULL,                             -- zero_volume|stale_cursor|spend_spike|parity_drift|schema_drift
    detail_json TEXT,
    severity TEXT NOT NULL DEFAULT 'warn',          -- warn|critical (critical ⇒ interrupt email)
    resolved_at TEXT
);

-- Per-task incremental sync positions.
CREATE TABLE sync_cursors (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Shadow mode: intended external writes, recorded instead of executed (pre-flight §9).
CREATE TABLE shadow_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL DEFAULT (datetime('now')),
    run_id INTEGER REFERENCES runs(id),
    task_name TEXT NOT NULL,
    target TEXT NOT NULL,                           -- airtable|clickup|email|upwork_draft
    operation TEXT NOT NULL,                        -- create|update|send
    entity TEXT,
    entity_key TEXT,                                -- canonical key for diffing vs v2 actuals
    payload_json TEXT NOT NULL,
    diff_status TEXT                                -- pending|match|mismatch|v3_only|v2_only
);
CREATE INDEX idx_shadow_diff ON shadow_ledger(task_name, diff_status);

-- Nothing is silently dropped (data-accuracy plan §4): unparseable/ambiguous records wait here.
CREATE TABLE quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL DEFAULT (datetime('now')),
    source TEXT NOT NULL,                           -- import-v2|fathom|loom|bank|wise|card|upwork
    reason TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT                                 -- imported|discarded|manual
);
