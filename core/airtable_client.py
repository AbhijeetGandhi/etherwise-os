"""Stdlib Airtable REST client (the cockpit's cloud-data seam seed).

Reads/writes the Airtable REST API directly with the PAT from the shared
credentials env (independent of the MCP connector — works headless). Reads
power the cockpit Clients section now; the write methods fill
core/sync/engine.execute_push's `airtable` slot for M4 (Nudges/Commitments)
and sync mirroring. Typecast always; <=10 records/batch (Airtable's limit).

No new dependency — urllib only, with a curl fallback for the sandbox DNS
quirk that hits urllib (same pattern as the cleanup tooling).
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, List, Optional

from core import config

API_ROOT = "https://api.airtable.com/v0"
CREDENTIALS_ENV_FILE = "etherwise-os.env"
BATCH = 10


class AirtableError(Exception):
    pass


def api_key() -> str:
    env = config.CREDENTIALS_DIR / CREDENTIALS_ENV_FILE
    try:
        for line in env.read_text().splitlines():
            if line.startswith("AIRTABLE_API_KEY="):
                k = line.split("=", 1)[1].strip()
                if k:
                    return k
    except OSError as exc:
        raise AirtableError(f"cannot read credentials: {env}") from exc
    raise AirtableError(f"AIRTABLE_API_KEY not found in {env}")


def _request(method: str, url: str, key: str, payload=None) -> dict:
    """The one HTTP boundary (mocked in tests). urllib, curl fallback."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()[:300] if exc.fp else ""
        raise AirtableError(f"Airtable HTTP {exc.code}: {body}") from exc
    except OSError:
        # sandbox DNS quirk hits urllib only — fall back to curl
        args = ["curl", "-sS", "-X", method, url,
                "-H", f"Authorization: Bearer {key}",
                "-H", "Content-Type: application/json"]
        if data is not None:
            args += ["-d", data.decode()]
        out = subprocess.run(args, capture_output=True, text=True, timeout=40)
        if out.returncode != 0:
            raise AirtableError(f"curl failed: {out.stderr[:200]}")
        return json.loads(out.stdout or "{}")


class AirtableClient:
    def __init__(self, api_key: Optional[str] = None):
        self.key = api_key or api_key_default()

    def _url(self, base: str, table: str) -> str:
        return f"{API_ROOT}/{base}/{urllib.parse.quote(table)}"

    def list_records(self, base: str, table: str, *,
                     fields: Optional[List[str]] = None,
                     formula: Optional[str] = None,
                     max_records: Optional[int] = None,
                     page_size: int = 100) -> List[dict]:
        records: List[dict] = []
        offset = None
        while True:
            params: list = [("pageSize", str(min(page_size, 100)))]
            for f in (fields or []):
                params.append(("fields[]", f))
            if formula:
                params.append(("filterByFormula", formula))
            if offset:
                params.append(("offset", offset))
            url = self._url(base, table) + "?" + urllib.parse.urlencode(params)
            resp = _request("GET", url, self.key)
            records.extend(resp.get("records", []))
            if max_records and len(records) >= max_records:
                return records[:max_records]
            offset = resp.get("offset")
            if not offset:
                return records

    def create_records(self, base: str, table: str, records: List[dict]) -> int:
        done = 0
        for i in range(0, len(records), BATCH):
            chunk = records[i:i + BATCH]
            resp = _request("POST", self._url(base, table), self.key,
                            payload={"records": chunk, "typecast": True})
            done += len(resp.get("records", chunk))
        return done

    def update_records(self, base: str, table: str, records: List[dict]) -> int:
        done = 0
        for i in range(0, len(records), BATCH):
            chunk = records[i:i + BATCH]
            resp = _request("PATCH", self._url(base, table), self.key,
                            payload={"records": chunk, "typecast": True})
            done += len(resp.get("records", chunk))
        return done

    def engine_writer(self, base: str, table: str) -> Callable:
        """Adapter for core/sync/engine.execute_push(airtable=...): it calls
        airtable(method, path, payload); base+table are bound here."""
        def writer(method: str, path: str, payload: dict) -> dict:
            return _request(method, self._url(base, table), self.key,
                            payload=payload)
        return writer


def api_key_default() -> str:
    return api_key()
