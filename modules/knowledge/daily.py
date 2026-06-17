"""Daily brain update (launchd, June-14 TCC pattern). One run_task that:
  1. polls Fathom (live API) -> knowledge-inbox/fathom/
  2. polls Gmail IF connected (token present) -> knowledge-inbox/email/
  3. ingests new inbox drops (dedup by content hash)
  4. extracts pending fathom+email sources, BUDGET-AWARE (stops cleanly at the
     per-run cap; Loom backlog is excluded — its bulk extraction is a separate
     gated decision, not a daily drip)
  5. recompiles dossiers + proof-points for the roster

Incremental by design: after the one-time backfill clears the history, daily
volume is a few calls and fits the normal $0.50/run cap. Run:
  python3 -m modules.knowledge.daily
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from core import config, db, runner
from core.claude_gateway import ClaudeBudgetExceeded
from modules.knowledge import (fathom_poll, email_poll, ingest, extract, index,
                               clients, crm, dossier, proof_points)

DAILY_SOURCE_TYPES = ("fathom", "email")   # Loom backlog excluded from the drip
TASK_NAME = "kb_daily"


def _poll_fathom(ctx) -> dict:
    try:
        return fathom_poll.poll(ctx)
    except runner.TaskSkip:
        return {"saved": 0, "deduped": 0}
    except Exception as e:                  # a poll outage must not block extract
        return {"error": str(e)[:200]}


def _poll_email(ctx) -> dict:
    if not email_poll.TOKEN_FILE.exists():
        return {"skipped": "gmail not connected"}
    try:
        return email_poll.poll(ctx)
    except runner.TaskSkip:
        return {"saved": 0}
    except Exception as e:
        return {"error": str(e)[:200]}


def _ingest_inbox(db_path) -> dict:
    cmap = clients.load_client_map(db_path)
    counts = {"ingested": 0, "duplicate": 0}
    for stype in DAILY_SOURCE_TYPES:
        d = config.KNOWLEDGE_INBOX / stype
        if not d.exists():
            continue
        for path in sorted(d.glob("*.json")):
            r = ingest.ingest_file(path, db_path=db_path, client_map=cmap,
                                   source_type=stype)
            counts[r["status"]] = counts.get(r["status"], 0) + 1
    return counts


def _extract_pending(db_path) -> dict:
    qs = ",".join("?" * len(DAILY_SOURCE_TYPES))
    with db.connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT id, source_type, source_ref, client_id, raw_path FROM"
            f" sources WHERE status='ingested' AND source_type IN ({qs})"
            f" ORDER BY occurred_dt", DAILY_SOURCE_TYPES)]
    done = facts = 0
    touched = set()
    for s in rows:
        try:
            payload = json.loads(Path(s["raw_path"]).read_text())
            parsed = ingest._PARSERS[s["source_type"]](payload)
            out = extract.extract_facts(
                {"source_ref": s["source_ref"], "client_id": s["client_id"]},
                parsed["text"], db_path=db_path)
            res = index.index_facts(db_path, s["id"], s["client_id"], out)
            done += 1
            facts += res["inserted"]
            if s["client_id"]:
                touched.add(s["client_id"])
        except ClaudeBudgetExceeded:        # hit the cap — stop cleanly
            return {"extracted": done, "facts": facts, "touched": list(touched),
                    "stopped": "budget", "pending": len(rows) - done}
    return {"extracted": done, "facts": facts, "touched": list(touched),
            "pending": 0}


def _compile(db_path, now_iso: str) -> dict:
    out = {}
    for c in clients.load_client_map(db_path):
        crm.ingest_crm(db_path, c["client_id"], c["name"], c["identifiers"])
        out[c["client_id"]] = dossier.compile_dossier(
            db_path, c["client_id"], c["name"], now_iso)["facts"]
    pp = proof_points.compile_proof_points(db_path, now_iso)
    return {"dossiers": out, "proof_points": pp}


def run_daily(ctx) -> dict:
    dbp = ctx.db_path or config.DB_PATH
    now_iso = config.now_ist_str() if hasattr(config, "now_ist_str") else \
        __import__("datetime").datetime.now(config.TZ).strftime("%Y-%m-%d %H:%M IST")
    result = {
        "fathom": _poll_fathom(ctx),
        "email": _poll_email(ctx),
        "ingest": _ingest_inbox(dbp),
        "extract": _extract_pending(dbp),
    }
    result["compile"] = _compile(dbp, now_iso)
    return result


def main() -> int:
    res = runner.run_task(TASK_NAME, run_daily, module="knowledge")
    print(json.dumps({"run_id": res.run_id, "status": res.status,
                      "metrics": res.metrics}))
    return 0 if res.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
