-- 005_kb_scope_linkage.sql — M3 refinement: fact scope + thread→client linkage.
-- scope routes facts to the right artifact: 'client' facts go in the client
-- dossier; 'agency' (general method/capability) and 'cross-client' (another
-- client's work referenced in this call) go to the proof-points wiki, NOT the
-- dossier — fixes the capability bleed (e.g. Adelye dossier carrying agency
-- positioning + another client's voice-agent project).
ALTER TABLE facts ADD COLUMN scope TEXT NOT NULL DEFAULT 'client'
    CHECK (scope IN ('client', 'agency', 'cross-client'));

-- Persisted thread→client linkage so message-heavy clients (e.g. NETFLY, which
-- has no Fathom calls) get their Upwork signal. Set by clients.link_threads
-- via counterparty org/user/room-name match against the roster identifiers.
ALTER TABLE threads ADD COLUMN client_id TEXT;
CREATE INDEX IF NOT EXISTS idx_threads_client ON threads(client_id);
