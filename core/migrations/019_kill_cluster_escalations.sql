-- 019: kill the WORD-COUNT / cluster escalation cards that 018's digit-only GLOB
-- missed. These are Opus-correlation rollups ("Five commitments due tomorrow",
-- "Seven sales follow-ups owed", "Two hot Loom leads unrecorded while five
-- follow-ups sit") — cluster/count cards, which J1.5 makes render-time groupings
-- over the atomic set, never stored cards. The sweep now filters these at write
-- time; this clears the ones already stored. Atomic per-commitment / per-client
-- escalations (e.g. "ManyChat/dashboard commitment due Fri…") are preserved.

DELETE FROM nudges WHERE state IN ('open','snoozed') AND (
    kind = 'escalate_commitment_cluster' OR kind = 'escalate_pipeline'
    OR title GLOB '[0-9]* *'
    OR title GLOB 'One *' OR title GLOB 'Two *' OR title GLOB 'Three *'
    OR title GLOB 'Four *' OR title GLOB 'Five *' OR title GLOB 'Six *'
    OR title GLOB 'Seven *' OR title GLOB 'Eight *' OR title GLOB 'Nine *'
    OR title GLOB 'Ten *' OR title GLOB 'Several *' OR title GLOB 'Multiple *');
