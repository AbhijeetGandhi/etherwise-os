#!/usr/bin/env python3
"""Write down the self-heal both readers already compute.

    PYTHONPATH=. python3 scripts/resolve_anomalies.py            # dry run
    PYTHONPATH=. python3 scripts/resolve_anomalies.py --apply
    PYTHONPATH=. python3 scripts/resolve_anomalies.py --apply --kind task_failure

WHAT THIS IS NOT. It is not a resolution engine, and `anomalies.resolved_at`
being unset was never making the cockpit lie. Both consumers already close an
incident honestly without it:

  * modules/cockpit/data.py::system — an anomaly is RECOVERED once its task has
    a completed run POSTDATING the anomaly's last occurrence ("computed
    honestly, not read off resolved_at (nothing sets it)");
  * core/source_health.py — a degrade anomaly is bound to the task's LATEST
    run_id, so a healthy run stops matching and the source is no longer
    degraded.

So the display was right. What was wrong is narrower and worth fixing anyway:
the stored column disagreed with the computed truth, permanently and only in
one direction. 106 rows sat unresolved on 2026-08-20 — 66 of them 'critical',
the oldest from 17 June — including 30 `catch_up_gave_up` and 22
`task_failure` rows from an Upwork logout that had ALREADY recovered. Anything
reading the table directly (a query, a future feature, a person) reads a
two-month backlog of critical incidents that are not happening.

This applies EXACTLY the cockpit's rule and nothing else — no new policy, no
judgement, no closing anything whose task has not demonstrably run clean since.
An anomaly whose task is still failing is left open, which is the point.

Idempotent. Dry run by default.
"""
from __future__ import annotations

import sys
from pathlib import Path

APPLY = "--apply" in sys.argv
KIND = None
if "--kind" in sys.argv:
    KIND = sys.argv[sys.argv.index("--kind") + 1]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import config, db                                   # noqa: E402

# The cockpit's rule, verbatim: a completed run of the SAME task strictly after
# this anomaly was detected. detected_at is UTC and runs.completed_at carries an
# IST offset, so they are compared as epochs via julianday — never as strings.
SELF_HEALED = """
SELECT a.id, a.task_name, a.kind, a.severity, a.detected_at,
       (SELECT MIN(r.completed_at) FROM runs r
         WHERE r.task_name = a.task_name AND r.status = 'completed'
           AND julianday(r.completed_at) > julianday(a.detected_at)) AS healed_at
  FROM anomalies a
 WHERE a.resolved_at IS NULL
   {kind_filter}
 ORDER BY a.id
"""


def main() -> int:
    dbp = config.DB_PATH
    q = SELF_HEALED.format(kind_filter="AND a.kind = ?" if KIND else "")
    args = (KIND,) if KIND else ()
    with db.connect(dbp) as conn:
        rows = [dict(r) for r in conn.execute(q, args)]

    healed = [r for r in rows if r["healed_at"]]
    still_open = [r for r in rows if not r["healed_at"]]

    by_kind: dict = {}
    for r in healed:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1

    print(f"{len(rows)} unresolved anomal(ies)"
          + (f" of kind {KIND!r}" if KIND else ""))
    print(f"  {len(healed)} self-healed (task completed after the last fire)")
    print(f"  {len(still_open)} STILL OPEN — left alone\n")

    for kind, n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        print(f"  resolve  {n:>4}  {kind}")
    if still_open:
        print()
        seen = set()
        for r in still_open:
            k = (r["task_name"], r["kind"])
            if k in seen:
                continue
            seen.add(k)
            print(f"  OPEN     [{r['severity']}] {r['task_name']} / {r['kind']}"
                  f"  since {r['detected_at']}")

    if not healed:
        print("\nnothing to resolve.")
        return 0
    if not APPLY:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    with db.connect(dbp) as conn:
        for r in healed:
            # stamped with WHEN IT RECOVERED, not with now(): the record should
            # say when the system got better, not when someone got round to
            # writing it down.
            conn.execute("UPDATE anomalies SET resolved_at=? WHERE id=?",
                         (r["healed_at"], r["id"]))
    print(f"\nresolved {len(healed)} anomal(ies), each stamped with the time its"
          " task actually recovered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
