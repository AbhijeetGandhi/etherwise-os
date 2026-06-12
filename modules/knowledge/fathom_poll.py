"""Fathom transcript poller (M3 prep — Day-5 stretch, research resolved).

Official public API: GET api.fathom.ai/external/v1/meetings with X-Api-Key
(validated live 2026-06-12). Pulls meetings created after the high-water-mark
cursor and drops each as knowledge-inbox/fathom/{date}__{recording_id}.json
for the M3 inbox pipeline (hash-dedup -> classify -> grade -> extract).

Shadow-irrelevant by design: writes only to the LOCAL knowledge-inbox/ (reads
of external APIs are always allowed in shadow). Cursor lives in v3
sync_cursors ('fathom_created_after'); dedupe on recording_id (existing file
wins). Pagination defensively follows next_cursor with a page cap — the
research note says page size defaults to 10 regardless of limit param.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from core import config, db, runner

API = "https://api.fathom.ai/external/v1/meetings"
CURSOR = "fathom_created_after"
PAGE_CAP = 40          # 400 meetings/run ceiling — backfill safety
TASK_NAME = "fathom_poll"


def api_key() -> str:
    env = config.CREDENTIALS_DIR / "etherwise-os.env"
    for line in env.read_text().splitlines():
        if line.startswith("FATHOM_API_KEY="):
            key = line.split("=", 1)[1].strip()
            if key:
                return key
    raise RuntimeError("FATHOM_API_KEY missing from credentials env")


def fetch_page(key: str, created_after, cursor=None) -> dict:
    params = {"include_transcript": "true", "include_summary": "true"}
    if created_after:
        params["created_after"] = created_after
    if cursor:
        params["cursor"] = cursor
    req = urllib.request.Request(
        f"{API}?{urllib.parse.urlencode(params)}",
        headers={"X-Api-Key": key})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def poll(ctx, _fetch=fetch_page) -> dict:
    dbp = ctx.db_path or config.DB_PATH
    with db.connect(dbp) as conn:
        row = conn.execute("SELECT value FROM sync_cursors WHERE name=?",
                           (CURSOR,)).fetchone()
    hwm = row["value"] if row else None

    inbox = config.KNOWLEDGE_INBOX / "fathom"
    inbox.mkdir(parents=True, exist_ok=True)
    key = api_key()

    saved, skipped, max_created = 0, 0, hwm
    cursor, pages = None, 0
    while pages < PAGE_CAP:
        pages += 1
        page = _fetch(key, hwm, cursor)
        items = page.get("items") or page.get("meetings") or []
        for m in items:
            rid = str(m.get("recording_id") or m.get("id") or "").strip()
            created = (m.get("created_at") or m.get("recording_start_time")
                       or "")
            if not rid:
                continue
            date = (created or "unknown")[:10]
            path = inbox / f"{date}__{rid}.json"
            if path.exists():
                skipped += 1
            else:
                path.write_text(json.dumps(m, indent=1, default=str))
                saved += 1
            if created and (max_created is None or created > max_created):
                max_created = created
        cursor = page.get("next_cursor")
        if not cursor or not items:
            break

    if max_created and max_created != hwm:
        with db.connect(dbp) as conn:
            conn.execute(
                "INSERT INTO sync_cursors (name, value, updated_at)"
                " VALUES (?,?,datetime('now')) ON CONFLICT(name) DO UPDATE"
                " SET value=excluded.value, updated_at=datetime('now')",
                (CURSOR, max_created))

    if saved == 0 and skipped == 0:
        raise runner.TaskSkip("no new Fathom meetings")
    return {"saved": saved, "deduped": skipped, "pages": pages,
            "cursor": max_created,
            "polled_at": datetime.now(timezone.utc).isoformat()}


def main() -> int:
    result = runner.run_task(TASK_NAME, poll, module="knowledge")
    print(json.dumps({"run_id": result.run_id, "status": result.status,
                      "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    sys.exit(main())
