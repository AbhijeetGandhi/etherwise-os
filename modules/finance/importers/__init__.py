"""Finance importers — rebuilt on the kernel, v2 parser logic ported VERBATIM as
the tested spec (formats, dedup keys, FX rules are hard-won). Per source:
parse -> canonical-key dedup -> FX (Decimal, pinned) -> quarantine the rest ->
statement-integrity reconcile BEFORE commit -> append + move to _processed.
Money is integer minor units only (modules.finance.money); float is banned."""
