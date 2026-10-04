"""Transport for mcp.upwork.com — OAuth 2.1 public client + JSON-RPC session.

WHY THIS EXISTS. The MCP feed's DATA was proven sufficient against live
payloads (see mcp_feed.py and 92146a3) before any transport was built, on
purpose: proving data first meant this file is written knowing it will be
kept. It lets launchd jobs call the official MCP server the same way an agent
does, which is what the 24-48h shadow poll — the definitive discovery test —
needs.

HOW THE CREDENTIALS CAME TO BE (2026-08-24). Upwork's OAuth discovery document
lists `registration_endpoint: https://www.upwork.com/register`, which is the
human signup page — a dead end that nearly forced CIMD hosting. But the
CONVENTIONAL path `https://mcp.upwork.com/register` is a real RFC 7591 dynamic
registration endpoint and returns 201 with a client. So:

  .credentials/upwork-mcp-registration.json   the registered client (public,
                                              token_endpoint_auth_method none,
                                              redirect http://127.0.0.1:8976/cb)
  .credentials/upwork-mcp.json                the token set bin/upwork-mcp-auth
                                              mints; this module refreshes it

Both live in the V2 TREE's .credentials directory — same cross-repo convention
as the cockpit token, documented in UI_BUILD_BRIEF §0.10 — and are chmod 600,
git-ignored, and never logged.

WHAT WAS RULED OUT FIRST: reusing the existing GraphQL app's token. Probed
2026-08-24: mcp.upwork.com answers it with 403 (authenticated, wrong
audience/scopes) — the two credential paths are disjoint by design, matching
the API & MCP Terms §6.2.

SESSION SHAPE, measured against the live server rather than assumed:
  * every response may be `application/json` OR `text/event-stream`; the SSE
    body carries JSON-RPC messages in `data:` lines and the LAST one with a
    result/error for our id is the answer;
  * `initialize` returns an `mcp-session-id` header that must be echoed on
    every later call, and the `notifications/initialized` notification must be
    sent before tools are called;
  * upwork.com's edge 403s urllib's default UA (same Cloudflare quirk
    upwork_client.py works around), so the browser-ish UA is not decoration.

READ-ONLY BY POLICY HERE: `call()` refuses tool names outside an allowlist of
read tools. The MCP exposes writes (submit proposals, send messages); the
shadow poller must be INCAPABLE of them, not merely told not to. Drafts-only
is a §0 law, and the allowlist is how a transport obeys it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from core import config
from modules.upwork import grant_guard as guard

MCP_URL = "https://mcp.upwork.com/mcp"
AUTHORIZE_URL = "https://www.upwork.com/ab/account-security/oauth2/authorize"
TOKEN_URL = "https://www.upwork.com/api/v3/oauth2/token"
REDIRECT_URI = "http://127.0.0.1:8976/cb"
PROTOCOL_VERSION = "2025-06-18"

REG_PATH = config.CREDENTIALS_DIR / "upwork-mcp-registration.json"
TOKENS_PATH = config.CREDENTIALS_DIR / "upwork-mcp.json"

_REFRESH_MARGIN_S = 120
REAUTH_COMMAND = "PYTHONPATH=. python3 bin/upwork-mcp-auth"
# what stops while this grant is dead, in the words the alert email uses
RAILS_ON_THIS_GRANT = "The job scanner, the Upwork ledger, the heartbeat and the hiring side"
_UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
                   " AppleWebKit/537.36 (KHTML, like Gecko)"
                   " Chrome/131.0.0.0 Safari/537.36 EtherwiseOS/1.0"),
    "Accept-Language": "en-US,en;q=0.9",
}

# The only tools this transport will invoke. Everything else — including every
# write the server exposes — is refused in code. Extend deliberately, never
# implicitly.
READ_TOOLS = frozenset({
    "list_accounts", "find_jobs", "get_job_posting", "get_freelancer_dashboard",
    "get_freelancer_financials", "list_freelancer_proposals", "list_contracts",
    "list_offers", "get_profile", "get_account", "get_rate_insights",
    "find_saved_jobs", "get_tool_help", "get_messages",
    # the hiring side (Phase 3, 2026-08-27) — reads on HIS OWN postings
    "list_client_proposals", "list_client_invitations", "get_client_dashboard",
})


class MCPAuthError(Exception):
    pass


class MCPCallError(Exception):
    pass


# Why a session could not start, in the words the runbook needs. The scan's
# failure text used to say "repair the OAuth token (bin/upwork-mcp-auth)" for
# EVERY start failure; during the 26 Aug 12:15-13:15 IST DNS outage
# (gaierror "nodename nor servname provided") that sent a human to fix a token
# that was fine. A network failure self-heals and needs nobody; an auth
# failure needs the auth script; anything else needs the log.
START_FAILURE_ADVICE = {
    "network": "the Mac could not reach mcp.upwork.com (DNS/network) —"
               " nothing to repair, the next run self-heals",
    "auth": "the OAuth token was refused — run bin/upwork-mcp-auth",
    "unknown": "cause unclear — read the log before touching the token",
}


def start_failure_kind(exc: BaseException) -> str:
    """'network' | 'auth' | 'unknown' for an exception raised while a session
    was starting (initialize / list_accounts). Walks the cause chain: urllib
    wraps the socket error, our MCPCallError wraps an HTTP 401/403."""
    import re as _re
    import socket
    import urllib.error as _ue
    seen, e = [], exc
    while e is not None and len(seen) < 8 and e not in seen:
        seen.append(e)
        e = e.__cause__ or e.__context__
    for e in seen:
        if isinstance(e, MCPAuthError):
            return "auth"
        if isinstance(e, MCPCallError) and _re.search(r"\bmcp (401|403)\b", str(e)):
            return "auth"
        if isinstance(e, _ue.HTTPError):
            return "auth" if e.code in (401, 403) else "unknown"
        if isinstance(e, (socket.gaierror, socket.timeout, TimeoutError,
                          ConnectionError, _ue.URLError)):
            return "network"
    return "unknown"


# ── credential files ─────────────────────────────────────────────────────────

def client_id() -> str:
    try:
        return json.loads(REG_PATH.read_text())["client_id"]
    except (OSError, ValueError, KeyError) as exc:
        raise MCPAuthError(
            f"no registered client at {REG_PATH} — re-run the RFC7591"
            f" registration (see module docstring)") from exc


def _read_tokens() -> dict:
    try:
        return json.loads(TOKENS_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _write_tokens(doc: dict) -> None:
    # replaced, never truncated: a reader could catch the old write_text empty
    guard.write_atomic(TOKENS_PATH, json.dumps(doc, indent=2) + "\n")


def _expired(tok: dict) -> bool:
    exp = tok.get("expires_at")
    return exp is not None and time.time() >= float(exp) - _REFRESH_MARGIN_S


def _refuse_if_dead(tok: dict) -> None:
    """The circuit breaker. Once Upwork has refused THIS refresh token, no rail
    presents it again — see grant_guard for why replaying it is worse than
    useless. An alert that could not be sent at the time is retried here, by a
    run that makes no call to Upwork."""
    doc = guard.dead(TOKENS_PATH, tok.get("refresh_token"))
    if doc:
        guard.alert_once(TOKENS_PATH, tok.get("refresh_token"))
        raise MCPAuthError(guard.refusal_message(doc))


def _upwork_reason(exc: BaseException) -> str:
    """Upwork's own sentence out of 'token endpoint 400: {json}'."""
    text = str(exc)
    try:
        body = json.loads(text[text.index("{"):])
        return (f"{body.get('error', '')}: {body.get('error_description', '')}"
                .strip(": ") or text)[:300]
    except ValueError:
        return text[:300]


# ── OAuth 2.1 (PKCE, public client) ──────────────────────────────────────────

def make_pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(challenge: str, state: str) -> str:
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id(),
        "redirect_uri": REDIRECT_URI, "code_challenge": challenge,
        "code_challenge_method": "S256", "state": state})


def _token_request(form: dict, keep_refresh: Optional[str] = None) -> dict:
    req = urllib.request.Request(
        TOKEN_URL, data=urllib.parse.urlencode(form).encode(), method="POST",
        headers={**_UA_HEADERS,
                 "Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            doc = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # the body names the OAuth error; the token itself is never in it
        raise MCPAuthError(f"token endpoint {e.code}:"
                           f" {e.read().decode()[:300]}") from e
    doc["expires_at"] = time.time() + float(doc.get("expires_in") or 0)
    if keep_refresh and not doc.get("refresh_token"):
        # a server that did not rotate must not cost us the token we have
        doc["refresh_token"] = keep_refresh
    _write_tokens(doc)
    return doc


def exchange_code(code: str, verifier: str) -> dict:
    with guard.locked(TOKENS_PATH):
        doc = _token_request({
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": REDIRECT_URI, "client_id": client_id(),
            "code_verifier": verifier})
        guard.clear(TOKENS_PATH)             # a fresh grant reopens the circuit
        guard.note(TOKENS_PATH, "authorized")
        return doc


def refresh(stale_access: Optional[str] = None) -> dict:
    """Re-mint the token set — ONE PROCESS AT A TIME.

    `stale_access` is the access token the caller already holds and found
    wanting (expired, or refused by the server). Under the lock the file is
    read again: if it now holds a DIFFERENT, unexpired access token, another
    process refreshed while this one waited, and that token is returned with no
    call to Upwork at all. Presenting the refresh token a second time is the
    replay that cancelled the grant on 25 Sep (see grant_guard)."""
    with guard.locked(TOKENS_PATH):
        tok = _read_tokens()
        if not tok.get("refresh_token"):
            raise MCPAuthError("no refresh_token — run bin/upwork-mcp-auth once")
        _refuse_if_dead(tok)
        if (stale_access is not None and tok.get("access_token")
                and tok["access_token"] != stale_access and not _expired(tok)):
            guard.note(TOKENS_PATH, "reused_fresh_token")
            return tok
        try:
            doc = _token_request({
                "grant_type": "refresh_token",
                "refresh_token": tok["refresh_token"],
                "client_id": client_id()}, keep_refresh=tok["refresh_token"])
        except MCPAuthError as exc:
            guard.note(TOKENS_PATH, "refresh_refused", error=str(exc)[:160])
            if "invalid_grant" in str(exc):
                guard.mark_dead(TOKENS_PATH, tok["refresh_token"], grant="MCP",
                                reason=_upwork_reason(exc), command=REAUTH_COMMAND,
                                down=RAILS_ON_THIS_GRANT)
                guard.alert_once(TOKENS_PATH, tok["refresh_token"])
            raise
        guard.note(TOKENS_PATH, "refreshed")
        return doc


def access_token() -> str:
    tok = _read_tokens()
    if not tok.get("access_token"):
        raise MCPAuthError("no MCP tokens yet — run bin/upwork-mcp-auth once")
    _refuse_if_dead(tok)                     # no network at all while it is dead
    if _expired(tok):
        tok = refresh(stale_access=tok["access_token"])
    return tok["access_token"]


# ── JSON-RPC over streamable HTTP ────────────────────────────────────────────

def _parse_body(raw: bytes, content_type: str, want_id) -> Optional[dict]:
    """The server answers in plain JSON or SSE, its choice per response. In the
    SSE case each `data:` line is a JSON-RPC message; the answer is the LAST
    one addressed to our id (progress notifications may precede it)."""
    if "text/event-stream" in (content_type or ""):
        answer = None
        for line in raw.decode("utf-8", "replace").splitlines():
            if not line.startswith("data:"):
                continue
            try:
                msg = json.loads(line[5:].strip())
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == want_id:
                answer = msg
        return answer
    try:
        msg = json.loads(raw.decode())
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


class MCPSession:
    """One initialized session against mcp.upwork.com. Cheap to create; the
    shadow poller makes one per run."""

    def __init__(self, timeout: int = 60):
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self._next_id = 0

    def _post(self, payload: dict, *, retried: bool = False):
        token = access_token()
        headers = {**_UA_HEADERS,
                   "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL_VERSION,
                   "Authorization": f"Bearer {token}"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(),
                                     method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                sid = r.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                return r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            # one refresh+retry on an auth failure, then give up loudly. 403 is
            # included because that is what the live server returns for a token
            # it does not accept (measured — the GraphQL token got 403, not 401).
            if e.code in (401, 403) and not retried:
                # says WHICH token was refused, so that if another process has
                # already replaced it this one reuses theirs instead of
                # presenting the refresh token a second time
                refresh(stale_access=token)
                return self._post(payload, retried=True)
            raise MCPCallError(f"mcp {e.code}: {e.read().decode()[:200]}") from e

    def _rpc(self, method: str, params: Optional[dict] = None) -> dict:
        self._next_id += 1
        rid = self._next_id
        payload = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            payload["params"] = params
        raw, ctype = self._post(payload)
        msg = _parse_body(raw, ctype, rid)
        if msg is None:
            raise MCPCallError(f"{method}: unparseable response"
                               f" (content-type {ctype!r})")
        if msg.get("error"):
            raise MCPCallError(f"{method}: {json.dumps(msg['error'])[:300]}")
        return msg.get("result") or {}

    def _notify(self, method: str) -> None:
        raw, _ = self._post({"jsonrpc": "2.0", "method": method})
        del raw

    def initialize(self) -> dict:
        res = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "etherwise-shadow-poller",
                           "version": "1.0"}})
        self._notify("notifications/initialized")
        return res

    def tools_list(self) -> list:
        return (self._rpc("tools/list") or {}).get("tools") or []

    def call(self, tool: str, arguments: dict) -> dict:
        """tools/call, READ TOOLS ONLY — the allowlist is the drafts-only law
        expressed as code. Returns the tool's parsed JSON payload.

        Callers use the BARE verb ('find_jobs'); the wire name is namespaced
        ('upwork__find_jobs') and applied here, so the allowlist and the
        transport can never disagree about which name the law applies to.
        Verified live 2026-08-24: bare names get 400 'invalid resource name'."""
        base = tool.split("__")[-1]
        if base not in READ_TOOLS:
            raise MCPCallError(f"refusing non-read tool {tool!r} — this"
                               f" transport is read-only by policy")
        wire = tool if "__" in tool else f"upwork__{base}"
        res = self._rpc("tools/call", {"name": wire, "arguments": arguments})
        if res.get("isError"):
            raise MCPCallError(f"{tool}: {json.dumps(res)[:300]}")
        sc = res.get("structuredContent")
        if sc is not None:
            return sc
        for item in res.get("content") or []:
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except ValueError:
                    return {"text": item["text"]}
        return {}
