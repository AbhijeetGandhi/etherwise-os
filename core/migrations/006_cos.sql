-- 006_cos.sql — Chief of Staff (M4) domain: the judgment layer's state.
-- nudges = the prioritized "here's what matters" queue every module writes into;
-- commitments = promises tracked + nagged until done; notes = Quick Capture
-- inbox; sweep_state = empty-skip bookkeeping (candidate-set hash). Shadow:
-- everything local until chief_of_staff cutover; briefs render as draft files,
-- never auto-sent. The M2 proto nudge_state is retired in favour of nudges.

-- The nudge queue. dedup_key holds ONE active (open|snoozed) nudge; re-firing a
-- rule refreshes that row (never duplicates). done/dismissed/decayed are
-- terminal history and may coexist with a future re-fire.
CREATE TABLE nudges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,              -- emitter: sales|knowledge|system|sweep|capture|commitment
    dept TEXT NOT NULL,               -- bucket for brief grouping: sales|knowledge|system
    kind TEXT NOT NULL,               -- followup|hotlead_no_loom|commitment_due|deadline|lost_pattern|won_no_outcome|stale_dossier|system|escalate_client|capture
    title TEXT NOT NULL,              -- what matters (one line)
    action TEXT,                      -- what to do about it
    due TEXT,                         -- ISO date/datetime, nullable
    urgency REAL NOT NULL DEFAULT 0,  -- computed score; ranks the queue
    money_at_stake REAL,              -- stake multiplier input (nullable)
    score INTEGER,                    -- job/lead score stake input (nullable)
    dedup_key TEXT NOT NULL,          -- followup:<thread_id> | commitment:<id> | payroll:2026-06
    state TEXT NOT NULL DEFAULT 'open'
        CHECK (state IN ('open','snoozed','done','dismissed','decayed')),
    snooze_until TEXT,                -- ISO (snoozed only)
    note TEXT,                        -- founder note from cockpit/terminal action
    payload_json TEXT,                -- optional structured context for brief/Opus
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    refreshed_at TEXT NOT NULL DEFAULT (datetime('now')),  -- last re-fire
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    decayed_at TEXT
);
-- one ACTIVE nudge per dedup_key (open or snoozed); history can repeat.
CREATE UNIQUE INDEX idx_nudges_active_dedup ON nudges(dedup_key)
    WHERE state IN ('open','snoozed');
CREATE INDEX idx_nudges_state ON nudges(state);
CREATE INDEX idx_nudges_dept ON nudges(dept);

-- Promises tracked until done. who='us' nags in every brief; who='client' is
-- surfaced as awaiting-context (no nag). source_cite is MANDATORY (provenance).
CREATE TABLE commitments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    who TEXT NOT NULL,                -- us|client|<name>
    client_id TEXT,                  -- linked client when known
    text TEXT NOT NULL,              -- the promise
    due TEXT,                        -- ISO, nullable
    source_cite TEXT NOT NULL,       -- fathom/<id> @ 00:12 | upwork/<thread> | note/<id>
    dedup_key TEXT NOT NULL,         -- commitment:<hash> — idempotent extraction
    state TEXT NOT NULL DEFAULT 'open'
        CHECK (state IN ('open','done','dropped')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX idx_commitments_dedup ON commitments(dedup_key);
CREATE INDEX idx_commitments_state ON commitments(state);

-- Quick Capture inbox. The next sweep classifies new notes (Sonnet) into a
-- nudge/commitment/kb-note/idea; nothing is ever lost.
CREATE TABLE notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    source TEXT NOT NULL,            -- terminal|cockpit|email
    classified TEXT,                 -- nudge|commitment|kb_note|idea (null until swept)
    classified_ref TEXT,             -- id of the row it became
    state TEXT NOT NULL DEFAULT 'new'
        CHECK (state IN ('new','classified')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_notes_state ON notes(state);

-- Empty-skip bookkeeping (singleton). The sweep hashes its deterministic
-- candidate set; if the hash is unchanged AND no nudge crossed a due/overdue
-- threshold, it TaskSkips with zero Opus spend.
CREATE TABLE sweep_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    candidate_hash TEXT,
    last_swept_at TEXT,
    last_opus_at TEXT,
    last_metrics_json TEXT
);
