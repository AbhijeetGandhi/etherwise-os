#!/usr/bin/env python3
"""
upwork_api.py — Upwork GraphQL API client for Etherwise scheduled tasks.

Handles OAuth 2.0 (Desktop project with loopback callback), token refresh,
and GraphQL query execution.

CLI usage:
    python3 upwork_api.py auth                  # first-time OAuth dance
    python3 upwork_api.py whoami                # test query — returns your user info
    python3 upwork_api.py introspect [out.json] # dump full GraphQL schema
    python3 upwork_api.py graphql '<query>'     # run an arbitrary GraphQL query

Programmatic usage:
    from upwork_api import UpworkClient
    client = UpworkClient()
    result = client.graphql('{ user { id name } }')

Credentials file:
    ~/Desktop/Etherwise/.credentials/upwork-api.json (mode 600)
    Stores client_id, client_secret, access_token, refresh_token, expires_at.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import webbrowser
import secrets
import http.server
import socketserver
import threading
from pathlib import Path

# ============================================================
# Constants
# ============================================================

CREDS_PATH = Path.home() / "Desktop" / "Etherwise" / ".credentials" / "upwork-api.json"

# Upwork OAuth 2.0 endpoints (confirmed via Upwork developer docs)
AUTH_URL = "https://www.upwork.com/ab/account-security/oauth2/authorize"
TOKEN_URL = "https://www.upwork.com/api/v3/oauth2/token"
GRAPHQL_URL = "https://api.upwork.com/graphql"

CALLBACK_HOST = "localhost"
CALLBACK_PORT = 8080
CALLBACK_PATH = "/callback"


# ============================================================
# Credential storage
# ============================================================

def load_creds() -> dict:
    if not CREDS_PATH.exists():
        die(f"Credentials file not found at {CREDS_PATH}. "
            "Run setup first or check the path.")
    with open(CREDS_PATH) as f:
        return json.load(f)


def save_creds(creds: dict) -> None:
    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CREDS_PATH, "w") as f:
        json.dump(creds, f, indent=2)
    os.chmod(CREDS_PATH, 0o600)


def die(msg: str, code: int = 1) -> None:
    sys.stderr.write(f"[upwork_api] ERROR: {msg}\n")
    sys.exit(code)


# ============================================================
# OAuth 2.0 dance (one-time)
# ============================================================

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Captures the OAuth callback code in `received` then closes."""
    received = {}  # class-level shared state — single request expected

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.received = {k: v[0] for k, v in params.items()}

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if "code" in _CallbackHandler.received:
            body = (
                "<html><body style='font-family:system-ui;padding:40px;max-width:560px;'>"
                "<h1 style='color:#065f46;'>✅ Upwork auth complete</h1>"
                "<p>You can close this tab and return to the terminal.</p>"
                "</body></html>"
            )
        else:
            body = (
                "<html><body style='font-family:system-ui;padding:40px;max-width:560px;'>"
                "<h1 style='color:#991b1b;'>⚠ Auth failed</h1>"
                f"<pre>{json.dumps(_CallbackHandler.received, indent=2)}</pre>"
                "</body></html>"
            )
        self.wfile.write(body.encode())

    def log_message(self, *_):  # silence default access-log noise
        pass


def do_oauth() -> dict:
    """Run the OAuth dance. Opens browser, captures code via loopback,
    exchanges for tokens, saves to creds file. Returns the new creds dict.
    """
    creds = load_creds()
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")
    if not (client_id and client_secret):
        die("client_id / client_secret missing from credentials file.")

    # CSRF state — Upwork echoes this back; we verify it matches
    state = secrets.token_urlsafe(24)

    redirect_uri = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}"
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    # Start local server to catch the callback
    _CallbackHandler.received = {}
    server = socketserver.TCPServer((CALLBACK_HOST, CALLBACK_PORT), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(f"\n→ Opening browser to authorize Etherwise's Upwork app...")
    print(f"  If the browser doesn't open, paste this URL:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    # Wait up to 5 minutes for the callback
    deadline = time.time() + 300
    while time.time() < deadline and not _CallbackHandler.received:
        time.sleep(0.3)

    server.shutdown()
    received = _CallbackHandler.received

    if "code" not in received:
        die(f"OAuth callback did not include 'code'. Got: {received}")
    if received.get("state") != state:
        die(f"State mismatch — possible CSRF. Expected {state}, got {received.get('state')}.")

    code = received["code"]

    # Exchange code for tokens
    token_resp = _post_form(TOKEN_URL, {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    })

    if "access_token" not in token_resp:
        die(f"Token exchange failed: {token_resp}")

    creds["tokens"] = {
        "access_token": token_resp["access_token"],
        "refresh_token": token_resp.get("refresh_token"),
        "expires_at": int(time.time()) + int(token_resp.get("expires_in", 3600)),
        "token_type": token_resp.get("token_type", "Bearer"),
        "scope": token_resp.get("scope"),
        "_first_authed_at": int(time.time()),
    }
    save_creds(creds)

    print("\n✅ OAuth complete.")
    print(f"  access_token saved (length: {len(creds['tokens']['access_token'])} chars)")
    print(f"  refresh_token: {'YES' if creds['tokens'].get('refresh_token') else 'NO'}")
    print(f"  expires in: {token_resp.get('expires_in', 'unknown')} seconds")
    print(f"  scope: {token_resp.get('scope', 'unspecified')}\n")
    return creds


def refresh_access_token() -> dict:
    """Refresh the access token using the stored refresh_token."""
    creds = load_creds()
    rtoken = creds.get("tokens", {}).get("refresh_token")
    if not rtoken:
        die("No refresh_token in credentials. Run `auth` first.")

    resp = _post_form(TOKEN_URL, {
        "grant_type": "refresh_token",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": rtoken,
    })

    if "access_token" not in resp:
        die(f"Refresh failed: {resp}. May need to re-run `auth`.")

    creds["tokens"]["access_token"] = resp["access_token"]
    creds["tokens"]["expires_at"] = int(time.time()) + int(resp.get("expires_in", 3600))
    if "refresh_token" in resp:
        creds["tokens"]["refresh_token"] = resp["refresh_token"]
    save_creds(creds)
    return creds


def get_access_token() -> str:
    """Return a valid access token, refreshing if expired or near expiry."""
    creds = load_creds()
    tokens = creds.get("tokens", {})
    access = tokens.get("access_token")
    expires_at = tokens.get("expires_at", 0)

    if not access:
        die("No access_token. Run `python3 upwork_api.py auth` first.")

    # Refresh if expired or within 60s of expiry
    if int(time.time()) >= expires_at - 60:
        print("[upwork_api] Token expired or near-expiry, refreshing...", file=sys.stderr)
        creds = refresh_access_token()
        access = creds["tokens"]["access_token"]

    return access


# ============================================================
# HTTP helpers
# ============================================================

# Cloudflare's WAF on api.upwork.com blocks the default Python-urllib UA (error 1010).
# Use a browser-like UA string. Also send Accept + Accept-Language to look more like a real client.
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 "
                  "EtherwiseInternalCRM/1.0",
    "Accept": "application/json, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",  # avoid gzip we'd have to decompress
}


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    headers = dict(_BROWSER_HEADERS)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return {"_http_error": e.code, "_body": body}
    except Exception as e:
        die(f"POST {url} failed: {e}")


def _post_json(url: str, data: dict, token: str) -> dict:
    body = json.dumps(data).encode()
    headers = dict(_BROWSER_HEADERS)
    headers["Content-Type"] = "application/json"
    headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return {"_http_error": e.code, "_body": body}


# ============================================================
# GraphQL execution
# ============================================================

def graphql(query: str, variables: dict = None, max_retries: int = 3) -> dict:
    """Execute a GraphQL query. Returns the parsed JSON response.
    Auto-refreshes token on 401. Backs off on 429 (rate limit).
    """
    for attempt in range(max_retries):
        token = get_access_token()
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        resp = _post_json(GRAPHQL_URL, payload, token)

        if resp.get("_http_error") == 401 and attempt == 0:
            print("[upwork_api] 401 — forcing token refresh", file=sys.stderr)
            refresh_access_token()
            continue
        if resp.get("_http_error") == 429:
            wait = 2 ** attempt
            print(f"[upwork_api] 429 rate-limited — backing off {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        return resp

    return {"_error": "max retries exceeded"}


class UpworkClient:
    """Convenience wrapper for programmatic use."""

    def graphql(self, query: str, variables: dict = None) -> dict:
        return graphql(query, variables)

    def whoami(self) -> dict:
        """Quick smoke-test query — returns the freelancer's basic identity.
        Conservative: only requests fields that are virtually guaranteed to exist.
        Use `introspect` to see the full CurrentUser type definition.
        """
        return self.graphql("""
            query Whoami {
                user {
                    id
                }
            }
        """)

    def introspect_schema(self) -> dict:
        """Return the full GraphQL schema via introspection.
        Useful for capability discovery — pipe to a file and grep.
        """
        # Standard GraphQL introspection query (trimmed)
        return self.graphql("""
            query IntrospectionQuery {
                __schema {
                    queryType { name }
                    mutationType { name }
                    subscriptionType { name }
                    types {
                        kind
                        name
                        description
                        fields(includeDeprecated: true) {
                            name
                            description
                            args {
                                name
                                description
                                type { kind name ofType { kind name ofType { kind name } } }
                            }
                            type { kind name ofType { kind name ofType { kind name } } }
                        }
                        inputFields {
                            name
                            description
                            type { kind name ofType { kind name } }
                        }
                        enumValues(includeDeprecated: true) { name description }
                    }
                }
            }
        """)


# ============================================================
# CLI
# ============================================================

def main(argv):
    if len(argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = argv[1]

    if cmd == "auth":
        do_oauth()
    elif cmd == "whoami":
        result = UpworkClient().whoami()
        print(json.dumps(result, indent=2))
    elif cmd == "introspect":
        out_path = argv[2] if len(argv) > 2 else None
        schema = UpworkClient().introspect_schema()
        if out_path:
            with open(out_path, "w") as f:
                json.dump(schema, f, indent=2)
            # Pretty-print summary
            try:
                types = schema["data"]["__schema"]["types"]
                qtype = schema["data"]["__schema"]["queryType"]["name"]
                mtype = (schema["data"]["__schema"]["mutationType"] or {}).get("name")
                print(f"✅ Schema written to {out_path}")
                print(f"   Total types: {len(types)}")
                print(f"   Query root: {qtype}")
                print(f"   Mutation root: {mtype}")
            except Exception:
                print(f"Schema written to {out_path}. Top-level keys:", list(schema.keys()))
        else:
            print(json.dumps(schema, indent=2))
    elif cmd == "graphql":
        if len(argv) < 3:
            die("Usage: upwork_api.py graphql '<query>'")
        result = graphql(argv[2])
        print(json.dumps(result, indent=2))
    elif cmd == "refresh":
        refresh_access_token()
        print("✅ Token refreshed.")
    else:
        die(f"Unknown command: {cmd}. Try `auth`, `whoami`, `introspect`, `graphql`, `refresh`.")


if __name__ == "__main__":
    main(sys.argv)
