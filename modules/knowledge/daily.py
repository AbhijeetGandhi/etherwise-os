"""Daily brain update (launchd, June-14 TCC pattern). One run_task that:
  1. polls Fathom (live API) -> knowledge-inbox/fathom/
  2. polls Gmail IF connected (token present) -> knowledge-inbox/email/
  3. ingests new inbox drops (dedup by content hash)
  4. extracts pending fathom+email sources, BUDGET-AWARE (stops cleanly at the
     per-run cap), plus up to KB_DAILY_LOOM_PER_RUN attributed Looms a run
     (2026-11-01 — the backlog used to wait for a human and card Today)
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

# INGEST scans these inbox dirs each run (dedup by content hash makes an already-
# ingested drop a no-op). Loom is INGESTED so a freshly-fetched recording becomes a
# searchable `sources` row immediately — no silent ingest-level backlog (the fetcher
# also ingests as it goes; this is the belt-and-suspenders re-scan).
DAILY_INGEST_TYPES = ("fathom", "email", "loom")
# EXTRACT: fathom+email every run, plus ATTRIBUTED looms (2026-11-01). A new
# loom has NO client_id until the gated classify->heal chain
# (jarvis-loom-classify) attributes it, so an unattributed loom is never
# extracted here (it would only mint unattributed noise facts). Once attributed
# it used to wait for a human to run
#   bin/kb-backfill extract loom --attributed --model classify --limit N
# — and 72 attributed looms had waited since August, minting seven
# "Unprocessed sources: <client>" cards on Today for a job the system could do
# itself (the cockpit audit). The drip now takes up to KB_DAILY_LOOM_PER_RUN of
# them a run, with the Haiku 'classify' pin kb-backfill used, so the backlog
# drains in about a week and never comes back.
DAILY_SOURCE_TYPES = ("fathom", "email")   # the every-run extract set
LOOM_PER_RUN = int(getattr(config, "KB_DAILY_LOOM_PER_RUN", 12))
LOOM_MODEL_KEY = "classify"                # the Haiku pin; dense walkthroughs
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
    for stype in DAILY_INGEST_TYPES:
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
        looms = [dict(r) for r in conn.execute(
            "SELECT id, source_type, source_ref, client_id, raw_path FROM"
            " sources WHERE status='ingested' AND source_type='loom'"
            " AND client_id IS NOT NULL ORDER BY client_id, occurred_dt LIMIT ?",
            (LOOM_PER_RUN,))]
        loom_backlog = conn.execute(
            "SELECT COUNT(*) FROM sources WHERE status='ingested'"
            " AND source_type='loom' AND client_id IS NOT NULL").fetchone()[0]
    done = facts = looms_done = failed = 0
    touched = set()
    for s in rows + looms:
        is_loom = s["source_type"] == "loom"
        try:
            payload = json.loads(Path(s["raw_path"]).read_text())
            parsed = ingest._PARSERS[s["source_type"]](payload)
            src = {"source_ref": s["source_ref"], "client_id": s["client_id"]}
            kw = {}
            if is_loom:
                # the Loom share URL deep-links each fact to the recording, and
                # a dense walkthrough needs the per-run token ceiling or the
                # JSON truncates on the richest ones (kb-backfill's lessons)
                src["cite_ref"] = parsed.get("url")
                kw = {"model_key": LOOM_MODEL_KEY,
                      "max_tokens": config.PER_RUN_MAX_OUTPUT_TOKENS}
            out = extract.extract_facts(src, parsed["text"], db_path=db_path, **kw)
            res = index.index_facts(db_path, s["id"], s["client_id"], out)
            done += 1
            looms_done += 1 if is_loom else 0
            facts += res["inserted"]
            if s["client_id"]:
                touched.add(s["client_id"])
        except ClaudeBudgetExceeded:        # hit the cap — stop cleanly
            return {"extracted": done, "facts": facts, "touched": list(touched),
                    "stopped": "budget", "pending": len(rows) + len(looms) - done,
                    "looms": looms_done, "loom_backlog": loom_backlog - looms_done}
        except Exception as exc:            # noqa: BLE001 — one bad source
            # (truncated JSON, unreadable raw file) stays status='ingested' and
            # is retried next run; it must not take the rest of the drip down
            failed += 1
            continue
    return {"extracted": done, "facts": facts, "touched": list(touched),
            "pending": 0, "failed": failed,
            "looms": looms_done, "loom_backlog": max(0, loom_backlog - looms_done)}


def _stamp() -> str:
    """The compile timestamp, read WHEN THE COMPILE HAPPENS.

    run_daily used to compute one `now_iso` at the START of the run and hand
    that same string to the compile at the END. Extraction in between is model
    work and can run for many minutes, so `dossier_index.last_compiled_at` was
    routinely EARLIER than facts written by the very run that compiled it — a
    dossier stamped before the facts it contains. Stamping per client, after
    that client's CRM facts land, makes the field mean what it says."""
    return config.now_ist_str() if hasattr(config, "now_ist_str") else \
        __import__("datetime").datetime.now(config.TZ).strftime(
            "%Y-%m-%d %H:%M IST")


def _compile(db_path) -> dict:
    out = {}
    for c in clients.load_client_map(db_path):
        crm.ingest_crm(db_path, c["client_id"], c["name"], c["identifiers"],
                       c.get("ledger_labels"))
        out[c["client_id"]] = dossier.compile_dossier(
            db_path, c["client_id"], c["name"], _stamp())["facts"]
    pp = proof_points.compile_proof_points(db_path, _stamp())
    return {"dossiers": out, "proof_points": pp}


def run_daily(ctx) -> dict:
    dbp = ctx.db_path or config.DB_PATH
    result = {
        "fathom": _poll_fathom(ctx),
        "email": _poll_email(ctx),
        "ingest": _ingest_inbox(dbp),
        # attribute newly-ingested Upwork threads to registry clients. This has
        # to run on a schedule, not just once: every new thread lands with
        # client_id NULL, and an unattributed thread belonging to an active
        # client renders on Today as a cold lead (the Brandi Jordan / Cradle
        # Company symptom) AND splits out of that client's single card.
        # Idempotent, never overwrites an approved linkage.
        "link_threads": clients.link_threads(dbp),
        "extract": _extract_pending(dbp),
    }
    result["compile"] = _compile(dbp)
    return result


def main() -> int:
    res = runner.run_task(TASK_NAME, run_daily, module="knowledge")
    print(json.dumps({"run_id": res.run_id, "status": res.status,
                      "metrics": res.metrics}))
    return 0 if res.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
