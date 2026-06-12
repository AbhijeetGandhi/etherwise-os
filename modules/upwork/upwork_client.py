"""Upwork GraphQL client — READ-ONLY token until M1b cutover (§9).

v2 owns the OAuth refresh. This client reads upwork-api.json read-only; on
401/expiry it raises TransientAPIError so the runner backs off and retries
AFTER v2's refresher has cycled — it never refreshes, never writes the file.

Refresh capability exists ONLY for post-cutover: refresh() hard-refuses
unless var/upwork-token-owner contains 'v3' (flipped exclusively by
bin/upwork-token-cutover with Abhijeet's sign-off).

Transport: stdlib urllib. Org context for dual-ACE via the
X-Upwork-API-TenantId header (personal vs agency).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from core import config
from core import claude_gateway as gw

GRAPHQL_URL = "https://api.upwork.com/graphql"
CREDS_PATH = config.CREDENTIALS_DIR / "upwork-api.json"
OWNER_MARKER = config.VAR_DIR / "upwork-token-owner"

# Cloudflare 403s the default urllib UA on api.upwork.com (v2 quirk, ported).
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 EtherwiseOS/1.0"
    ),
    "Accept": "application/json, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
}


class UpworkAPIError(Exception):
    pass


def token_owner() -> str:
    try:
        return OWNER_MARKER.read_text().strip() or "v2"
    except OSError:
        return "v2"


def read_access_token() -> str:
    creds = json.loads(CREDS_PATH.read_text())
    token = (creds.get("tokens") or {}).get("access_token")
    if not token:
        raise UpworkAPIError("no access_token in upwork-api.json")
    return token


class UpworkClient:
    def __init__(self, tenant_id: Optional[str] = None, timeout: int = 60):
        self.tenant_id = tenant_id
        self.timeout = timeout
        self._token = read_access_token()

    def graphql(self, query: str, variables: Optional[dict] = None,
                tolerate_errors: bool = False) -> dict:
        """tolerate_errors=True returns the FULL envelope {data, errors} —
        Upwork routinely returns partial data + errors (non-null bubbles on
        system stories, v2 quirk); callers that can use partial data opt in."""
        headers = dict(_BROWSER_HEADERS)
        headers["Authorization"] = f"Bearer {self._token}"
        headers["Content-Type"] = "application/json"
        if self.tenant_id:
            headers["X-Upwork-API-TenantId"] = str(self.tenant_id)
        body = json.dumps({"query": query,
                           "variables": variables or {}}).encode()
        req = urllib.request.Request(GRAPHQL_URL, data=body, method="POST",
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise gw.TransientAPIError(
                    "Upwork 401 — token expired; v2 owns refresh (§9),"
                    " retry after its refresher cycles") from exc
            if exc.code in (429, 500, 502, 503, 504):
                raise gw.TransientAPIError(
                    f"Upwork HTTP {exc.code}") from exc
            raise UpworkAPIError(f"Upwork HTTP {exc.code}") from exc
        except OSError as exc:
            raise gw.TransientAPIError(f"network: {exc!r}") from exc
        if tolerate_errors:
            return data
        if data.get("errors"):
            msg = "; ".join(str(e.get("message")) for e in data["errors"])[:300]
            if "auth" in msg.lower() or "permission" in msg.lower():
                raise gw.TransientAPIError(f"Upwork auth error: {msg}")
            raise UpworkAPIError(f"GraphQL errors: {msg}")
        return data.get("data") or {}

    def refresh(self) -> dict:
        """Post-cutover only — HARD-GATED on the ownership marker so v3 can
        never race v2's refresher (§9). Port of v2's _refresh_token."""
        if token_owner() != "v3":
            raise UpworkAPIError(
                "refresh refused: token owner is v2 (§9). Ownership flips"
                " only via bin/upwork-token-cutover at M1b cutover.")
        creds = json.loads(CREDS_PATH.read_text())
        rtoken = (creds.get("tokens") or {}).get("refresh_token")
        if not rtoken:
            raise UpworkAPIError("no refresh_token in credentials")
        form = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": rtoken,
        }).encode()
        req = urllib.request.Request(
            "https://www.upwork.com/api/v3/oauth2/token", data=form,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        creds["tokens"]["access_token"] = data["access_token"]
        if "refresh_token" in data:
            creds["tokens"]["refresh_token"] = data["refresh_token"]
        if "expires_in" in data:
            import time as _time
            creds["tokens"]["expires_at"] = _time.time() + data["expires_in"]
        CREDS_PATH.write_text(json.dumps(creds, indent=2))
        self._token = creds["tokens"]["access_token"]
        return {"refreshed": True}
