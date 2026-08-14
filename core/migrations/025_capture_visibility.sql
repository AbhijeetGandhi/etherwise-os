-- 025: a captured note can never disappear.
--
-- WHAT WENT WRONG (2026-08-14). Abhijeet: "whenever I typed anything in the
-- capture box, like things I wanted to add to today, it didn't get added."
-- Two separate defects wearing one symptom:
--
--   1. LATENCY. `capture.classify_new` ran only inside the 2-hourly cos_sweep,
--      so a note was invisible for up to two hours. Note #3 was typed at
--      14:49 IST and its card was minted at 16:30 — 1h41m later. He typed,
--      got a toast, Today did not change, and the only available reading was
--      "it didn't save".
--   2. SILENT NON-ACTIONABLE. Note #2 ("evolve exteriors everything sorted
--      out") classified as `kb_note` and produced no row anywhere but this
--      table, which no screen renders. Possibly the right verdict — it is a
--      status note, not a to-do — but nothing said so, and something he typed
--      IN ORDER NOT TO FORGET became unreachable.
--
-- The rule these columns exist to enforce: a note is either visible as a card,
-- or visible as a note with the verdict that stopped it becoming one. There is
-- no third state, and "sitting in a table nobody reads" is not a state.
--
-- claimed_at   a LEASE, not a state. /api/note now spawns a classifier child
--              immediately AND the sweep still classifies as the backstop, so
--              two processes can reach for the same row. The claim is what
--              stops them paying twice, and it EXPIRES (see
--              config.CAPTURE_CLAIM_TTL_MINUTES) so a child killed mid-flight
--              releases the note instead of stranding it forever. State stays
--              'new' throughout: a claimed note is still an unclassified note,
--              and anything that crashes must leave it exactly that.
-- attempts     how many times the classifier has been tried. Bounds the spend
--              on a note that cannot be classified, and is itself the thing
--              displayed ("tried 3 times") instead of silence.
-- last_error   WHY the last attempt failed, in his words not in a log file.
-- resolved_at  he closed the loop on a note that produced no card, and
-- resolved_by  which way: 'card' (he disagreed with the verdict and made one)
--              or 'filed' (he agreed; keep it as a note). NULL means the note
--              is still waiting for him and Today still shows it.

ALTER TABLE notes ADD COLUMN claimed_at  TEXT;
ALTER TABLE notes ADD COLUMN attempts    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE notes ADD COLUMN last_error  TEXT;
ALTER TABLE notes ADD COLUMN resolved_at TEXT;
ALTER TABLE notes ADD COLUMN resolved_by TEXT;

-- the Today read: "which notes are still waiting on me?"
CREATE INDEX IF NOT EXISTS idx_notes_unresolved ON notes(resolved_at, state);
