"""Pipeline orchestration: drive a source payload end-to-end
(ingest → extract → index) and bulk-process the Fathom asset zip for pilots.

Live Sonnet extraction happens here (via extract.py → gateway). The full
357-call backfill stays behind its Phase-5 cost gate; process_zip caps per
client so pilot runs are bounded.
"""
from __future__ import annotations

import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import List, Optional

from core import config
from modules.knowledge import ingest, extract, index


def process_payload(payload: dict, db_path, client_map, inbox_dir: Path,
                    source_type: str = "fathom") -> dict:
    """Write raw to the (gitignored) inbox, then ingest → extract → index."""
    parsed = ingest.parse_fathom(payload) if source_type == "fathom" else None
    rid = (parsed["source_ref"].split("/")[-1] if parsed else "x")
    occurred = (parsed["occurred_dt"] or "")[:10] if parsed else "unknown"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    path = inbox_dir / f"{occurred}__{rid}.json"
    path.write_text(json.dumps(payload))

    res = ingest.ingest_file(path, db_path=db_path, client_map=client_map,
                             source_type=source_type)
    if res["status"] != "ingested":
        return res
    try:
        facts = extract.extract_facts(res, res["text"], db_path=db_path)
        res["facts"] = index.index_facts(db_path, res["source_id"],
                                         res["client_id"], facts)
    except Exception as e:                  # one bad call must not abort batch
        res["facts"] = {"inserted": 0, "rejected": 0, "error": str(e)[:200]}
    return res


def process_zip(zip_path, db_path, client_map, pilots: set,
                max_per_client: int = 8, inbox_dir: Optional[Path] = None,
                source_type: str = "fathom") -> List[dict]:
    """Bulk-process the densest recent calls per pilot client from the zip.
    Filters to pilot clients and caps per client (cost control)."""
    inbox_dir = inbox_dir or (config.KNOWLEDGE_INBOX / "fathom")
    z = zipfile.ZipFile(zip_path)
    # parse + classify all, keep pilot calls, newest first
    candidates = []
    for name in z.namelist():
        if not name.endswith(".json"):
            continue
        try:
            payload = json.loads(z.read(name))
        except Exception:
            continue
        parsed = ingest.parse_fathom(payload)
        cid, _, _ = ingest.classify(parsed["participants"], parsed["title"],
                                    client_map)
        if cid in pilots:
            candidates.append(((parsed["occurred_dt"] or ""), cid, payload))
    candidates.sort(key=lambda x: x[0], reverse=True)   # newest first

    per = Counter()
    results = []
    for _dt, cid, payload in candidates:
        if per[cid] >= max_per_client:
            continue
        per[cid] += 1
        results.append(process_payload(payload, db_path, client_map,
                                       inbox_dir, source_type))
    return results
