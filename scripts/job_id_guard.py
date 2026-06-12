"""Fabricated-job-id pattern guard (shared; required in push + restoration
tooling per BUILD_BRIEF supervised-run-2 blockers, 2026-06-12).

Signatures observed across the June 10-12 incidents:
  zeros padding   2064750000000000001       (June-10 degraded runs)
  keyboard walk   2064823456789012345       (June-11 escalation, ~24 rows)
  walk + pad      ...1234567890666
  same-digit runs ...77777...

Real Upwork marketplaceJobPostingIds are effectively random digits — short
repeats (e.g. '444' in 2064484440376044477) are normal, and across ~3K rows
a handful of REAL ids carry a 5-digit run (~1.5e-3 per id), so the same-digit
threshold is >=6 (calibrated 2026-06-12: a 5-run-only rule flagged 4 rows
indistinguishable from expected false positives). Triggers: >=6 zeros, >=6
same digits, or an 8+-digit ascending walk. A flagged id is never
auto-deleted — it is refused tasks and surfaced for review (heuristics !=
verification; see the June-10 row-...117 lesson).
"""
from __future__ import annotations

import re

_ZEROS = re.compile(r"0{6,}")
_SAME_RUN = re.compile(r"(\d)\1{5,}")
_ASCENDING = "12345678901234567890"   # ascending walk, mod 10, doubled


def looks_fabricated(job_id) -> bool:
    s = str(job_id or "")
    if _ZEROS.search(s) or _SAME_RUN.search(s):
        return True
    for i in range(len(s) - 7):
        if s[i:i + 8] in _ASCENDING:
            return True
    return False
