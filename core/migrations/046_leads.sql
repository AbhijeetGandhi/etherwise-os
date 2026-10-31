-- 046: leads — the bucket the Review queue never had (2026-11-01, the cockpit
-- audit; his call: "build it tonight").
--
-- The mapping queue knows one verb: "map this to an existing client". A
-- prospect who writes in has no client to map to, so the row sat as `queued`
-- until someone dismissed it. Measured on 30 Oct: eleven of the 73 queued
-- events were prospects — pointjust, boostjet, shiftember, dng.ai, plugotech,
-- and "New inquiry from Hannah Melotto (Melotto Group)", a WEBSITE-FORM lead
-- three days in a queue built for a different question.
--
-- ONE ROW PER LEAD, keyed on the event that brought it (event_id UNIQUE where
-- set) so a re-sweep cannot mint it twice. `status` is the only thing he edits
-- (new -> contacted -> call -> proposal -> client | no); everything else is
-- what arrived. Nothing here sends: a lead gets a reply from him, on the
-- channel it came in on.
CREATE TABLE IF NOT EXISTS leads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT,                      -- the person, as best we can read it
    company        TEXT,
    email          TEXT,
    domain         TEXT,                      -- sender domain, for dedup + the registry later
    source         TEXT NOT NULL,             -- website_form | email | upwork | linkedin | manual
    event_id       TEXT,                      -- events.id it came from (nullable: manual)
    subject        TEXT,
    snippet        TEXT,                      -- first lines of what they wrote
    permalink      TEXT,
    status         TEXT NOT NULL DEFAULT 'new'
                   CHECK (status IN ('new','contacted','call','proposal','client','no')),
    note           TEXT,
    first_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_event ON leads(event_id) WHERE event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status, first_seen_at);
CREATE INDEX IF NOT EXISTS idx_leads_domain ON leads(domain);
