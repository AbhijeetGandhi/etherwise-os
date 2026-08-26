-- 032: which MCP list a proposal was last seen in, and Upwork's plain-English
-- label for its status (use case C, first live round 26 Aug 18:30-18:38).
--
-- The round showed that `list_freelancer_proposals list status=X` is NOT
-- "proposals whose status is X": the Hired list (231) held 42 proposals whose
-- own status says Hired, 29 that say Activated and ~129 that say Accepted —
-- the job was filled, by us or by someone else; the Declined list (186) held
-- 6 Declined and 175 Accepted; 154 'Accepted' rows date back to 2023 with no
-- room and no contract. So the list is the JOB-LEVEL outcome bucket and the
-- node's status is our proposal's own last state. Decision 4's map reads the
-- node status (conservative, correct as far as it goes); mapping the rest —
-- an Accepted proposal in the Hired list is a job someone else won — needs
-- both dimensions on the row, for him to read and decide. Additive.
ALTER TABLE proposals ADD COLUMN mcp_list_status TEXT;      -- Accepted|Offered|Hired|Activated|Declined|Withdrawn|Archived (the list it was last seen in)
ALTER TABLE proposals ADD COLUMN upwork_status_label TEXT;  -- the tool's status_label ("Submitted", ...)
ALTER TABLE proposals ADD COLUMN mcp_listed_at TEXT;        -- when the MCP last listed it
