-- 018: Today card engine v2 (Jarvis J1.5, architecture §4.3 stage 4 + §4.5).
--
-- The nudges table IS the atomic card store. Evolve it: a canonical card
-- identity (card:<client>:<intent_hash>) and a cross-source CLOSURE overlay so a
-- Fathom call that resolves an Upwork ask closes the card. Closure lives as
-- columns on the persistent card (not a state flip), so it survives the sweep's
-- roll-forward re-mint and is applied at RENDER by card key.
--
--   closure_state = NULL             open, not yet evaluated
--                 | possibly_resolved  medium confidence -> confirm chip
--                 | auto_closed        high confidence   -> hidden from Today
--                 | confirmed          human confirmed    -> hidden
--                 | rejected           human kept it open -> render normal, no re-chip

ALTER TABLE nudges ADD COLUMN client TEXT;
ALTER TABLE nudges ADD COLUMN intent_hash TEXT;
ALTER TABLE nudges ADD COLUMN closure_state TEXT;
ALTER TABLE nudges ADD COLUMN closure_evidence TEXT;
ALTER TABLE nudges ADD COLUMN closure_confidence REAL;
ALTER TABLE nudges ADD COLUMN closure_source TEXT;

-- Kill stored cluster/count cards — they become render-time groupings over the
-- atomic set ("5 commitments due" is computed in the Today view, never stored).
-- The count-titled generators (lost_pattern, stale_dossier) + any legacy
-- leading-count title. Open/snoozed only; done/dismissed history is preserved;
-- they re-mint atomic with the count in the payload on the next sweep.
DELETE FROM nudges WHERE state IN ('open','snoozed') AND (
    kind IN ('lost_pattern','stale_dossier')
    OR title GLOB '[0-9]* commitment*' OR title GLOB '[0-9]* follow*'
    OR title GLOB '[0-9]* owed*' OR title GLOB '[0-9]* due*'
    OR title GLOB '[0-9]* loss*' OR title GLOB '[0-9]* unprocessed*');
