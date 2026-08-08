-- 022: events.direction — inbound vs outbound (Jarvis J2 Slack rail, 2026-08-09).
--
-- The email rails capture only inbound mail, so a NULL direction there is right.
-- The Slack rail captures BOTH sides of a client conversation: an inbound client
-- message AND our own outbound reply. Tagging our messages 'outbound' (rather than
-- excluding them) is the whole point of reading our own traffic — a rail that only
-- ever sees inbound cannot tell that we ALREADY replied, and would mint false
-- "owed a reply" cards. Nullable + additive: existing rows and the mail rails are
-- untouched (direction stays NULL for them).

ALTER TABLE events ADD COLUMN direction TEXT;   -- NULL | 'inbound' | 'outbound'

CREATE INDEX IF NOT EXISTS idx_events_direction ON events(client_id, direction)
    WHERE direction IS NOT NULL;
