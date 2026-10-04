"""Upwork GraphQL client — OAuth refresh OWNER-GATED on the cutover marker (§9).

Ownership lives in var/upwork-token-owner. While it reads 'v2' (pre-cutover) this
client is READ-ONLY: it never refreshes, never writes the file; on a 401 it raises
TransientAPIError so the runner backs off until v2's refresher cycles.

Once the marker is 'v3' (flipped by bin/upwork-token-cutover with Abhijeet's
sign-off), v3 OWNS the token and self-heals: it refreshes PROACTIVELY just before
expiry, and on a 401 it refreshes once and retries. refresh() still hard-refuses
unless the marker is 'v3', so v3 can never race v2's refresher.

Transport: stdlib urllib. Org context for dual-ACE via the
X-Upwork-API-TenantId header (personal vs agency).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

# Refresh proactively when the access token is within this many seconds of expiry
# (or already past it) — avoids eating a 401 on every cycle once v3 owns the token.
_REFRESH_MARGIN_S = 120

from core import config
from core import claude_gateway as gw
from modules.upwork import grant_guard as guard

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


class UpworkAuthError(UpworkAPIError):
    """The token endpoint refused the refresh. Not transient: retrying on the
    next tick will get the same answer until a human re-authorizes."""


REAUTH_COMMAND = "PYTHONPATH=. python3 bin/upwork-api-auth"
# what stops while this grant is dead, in the words the alert email uses
RAILS_ON_THIS_GRANT = "Upwork sync and the Upwork inbox"


def _grant_is_gone(exc_code: int, body: str) -> bool:
    return exc_code in (400, 401) and ("invalid_grant" in body or "revoked" in body
                                       or "expired" in body)


def _refuse_if_dead(refresh_token) -> None:
    """The circuit breaker (see grant_guard). After Upwork refused this refresh
    token the first time, sync presented it another 222 times over nine days.
    Now the second run says the same sentence without calling anyone."""
    doc = guard.dead(CREDS_PATH, refresh_token)
    if doc:
        guard.alert_once(CREDS_PATH, refresh_token)       # retries an unsent alert
        raise UpworkAuthError(guard.refusal_message(doc))


def _token_refusal(exc) -> str:
    """Turn a token-endpoint HTTPError into a sentence that says what to do.

    2026-09-25: this used to escape as the bare `<HTTPError 400: 'Bad
    Request'>` — the runs table, the log and the cockpit all said exactly that,
    fifteen times over, while Upwork's own body said "Refresh token is expired
    or revoked" and was thrown away with the exception. The MCP client in the
    same morning failed with an error that named its fix command, and that rail
    was back within the hour; this one stayed down all day. Same failure, same
    account — the only difference was whether the message said what to run."""
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:                                       # noqa: BLE001
        body = ""
    detail = body.strip()[:300] or f"HTTP {exc.code}"
    try:
        parsed = json.loads(body)
        detail = (f"{parsed.get('error', '')}: "
                  f"{parsed.get('error_description', '')}").strip(": ") or detail
    except (ValueError, AttributeError):
        pass
    exc.upwork_reason = detail                # for the breaker's marker
    exc.grant_gone = _grant_is_gone(exc.code, body)
    if exc.grant_gone:
        return (f"Upwork API refresh refused ({detail}) — the grant is gone;"
                f" re-authorize with `{REAUTH_COMMAND}`. upwork_sync and"
                f" upwork_inbox stay down until then.")
    return f"Upwork token endpoint HTTP {exc.code}: {detail}"


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

    def _ensure_fresh_token(self) -> None:
        """Proactive refresh: when v3 owns the token and it is at/near expiry,
        re-mint BEFORE the request so we don't take a 401 every cycle. No-op when
        v2 owns (pre-cutover read-only) or when no expiry is recorded."""
        if token_owner() != "v3":
            return
        try:
            creds = json.loads(CREDS_PATH.read_text())
        except OSError:
            return
        tokens = creds.get("tokens") or {}
        _refuse_if_dead(tokens.get("refresh_token"))   # no network while dead
        exp = tokens.get("expires_at")
        if exp is not None and time.time() >= float(exp) - _REFRESH_MARGIN_S:
            self.refresh()                       # owner-gated; updates self._token
        elif tokens.get("access_token") and tokens["access_token"] != self._token:
            # another process re-minted since this client was built: use theirs
            # rather than take a 401 on a token that has been rotated away
            self._token = tokens["access_token"]

    def graphql(self, query: str, variables: Optional[dict] = None,
                tolerate_errors: bool = False) -> dict:
        """tolerate_errors=True returns the FULL envelope {data, errors} —
        Upwork routinely returns partial data + errors (non-null bubbles on
        system stories, v2 quirk); callers that can use partial data opt in.

        When v3 owns the token this self-heals a 401: refresh once and retry the
        same request. Pre-cutover (v2 owns) a 401 raises TransientAPIError so the
        runner backs off until v2's refresher cycles — v3 never refreshes then."""
        self._ensure_fresh_token()
        data = None
        for attempt in (1, 2):
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
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    if attempt == 1 and token_owner() == "v3":
                        self.refresh()           # re-mint and retry once
                        continue
                    owner = token_owner()
                    raise gw.TransientAPIError(
                        "Upwork 401 — token expired; "
                        + ("refresh did not clear it, re-auth may be needed"
                           if owner == "v3" else
                           "v2 owns refresh (§9), retry after its refresher cycles")
                    ) from exc
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
        # ONE PROCESS AT A TIME, and the file is read again under the lock: if
        # another process re-minted while this one waited, its token is adopted
        # and Upwork is not called. Upwork rotates refresh tokens, so presenting
        # one twice is the replay that cancelled the MCP grant on 25 Sep.
        with guard.locked(CREDS_PATH):
            creds = json.loads(CREDS_PATH.read_text())
            tokens = creds.get("tokens") or {}
            rtoken = tokens.get("refresh_token")
            if not rtoken:
                raise UpworkAPIError("no refresh_token in credentials")
            _refuse_if_dead(rtoken)
            exp = tokens.get("expires_at")
            fresh = exp is None or time.time() < float(exp) - _REFRESH_MARGIN_S
            if (tokens.get("access_token") and tokens["access_token"] != self._token
                    and fresh):
                self._token = tokens["access_token"]
                guard.note(CREDS_PATH, "reused_fresh_token")
                return {"refreshed": False, "reused": True}
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
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                message = _token_refusal(exc)
                guard.note(CREDS_PATH, "refresh_refused", error=message[:160])
                if getattr(exc, "grant_gone", False):
                    guard.mark_dead(CREDS_PATH, rtoken, grant="API",
                                    reason=getattr(exc, "upwork_reason", "refused"),
                                    command=REAUTH_COMMAND, down=RAILS_ON_THIS_GRANT)
                    guard.alert_once(CREDS_PATH, rtoken)
                raise UpworkAuthError(message) from exc
            creds.setdefault("tokens", {})
            creds["tokens"]["access_token"] = data["access_token"]
            if "refresh_token" in data:
                creds["tokens"]["refresh_token"] = data["refresh_token"]
            if "expires_in" in data:
                creds["tokens"]["expires_at"] = time.time() + data["expires_in"]
            # replaced, never truncated: this file also holds the client secret
            guard.write_atomic(CREDS_PATH, json.dumps(creds, indent=2))
            self._token = creds["tokens"]["access_token"]
            guard.note(CREDS_PATH, "refreshed")
            return {"refreshed": True}
