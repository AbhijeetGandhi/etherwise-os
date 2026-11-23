-- 049: every recording the OTHER recorders showed us, and what became of it
-- (Abhijeet, 2026-11-24: "the way you have the fathom recording being watched —
-- use this fireflies api key, all my globaltize calls are recorded here, and
-- then use my clickup api key to see all recording that go there now").
--
-- Fathom is no longer the only thing recording his calls: Fireflies records the
-- Globaltize ones (their workspace), ClickUp's AI Notetaker the rest, and
-- Fathom is still running beside both — so the same call can arrive three
-- times. This table is the one list of what the two new recorders have shown
-- us (modules/knowledge/call_recorders.py), keyed on the recorder's own id:
--   saved    normalised into the call pipeline (knowledge-inbox/fathom/…json)
--   twin     another recorder's copy of this call is already a source — skipped
--   waiting  listed, but no transcript yet (re-read on the next run)
--   skipped  never produced a transcript, or nothing was said on it
-- A final state is never fetched again, so a daily run costs one list call per
-- recorder plus one read per NEW recording.
CREATE TABLE IF NOT EXISTS call_recordings (
    recorder      TEXT NOT NULL,           -- fireflies | clickup
    native_id     TEXT NOT NULL,           -- transcript id / notetaker doc id
    title         TEXT,
    started_at    TEXT,                    -- UTC ISO; ClickUp's is an estimate
    ended_at      TEXT,
    state         TEXT NOT NULL,           -- saved | twin | waiting | skipped
    twin_of       TEXT,                    -- recording_id of the copy already held
    file          TEXT,                    -- knowledge-inbox path when saved
    note          TEXT,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    checked_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (recorder, native_id)
);
CREATE INDEX IF NOT EXISTS idx_call_recordings_state ON call_recordings (state, ended_at);
