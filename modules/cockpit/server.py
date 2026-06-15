"""Cockpit server — stdlib ThreadingHTTPServer, localhost only, token auth.

NO model calls in this process (architecture: cockpit is a view+action plane).
The web process only reads v3 SQLite (read-only) and, later, kicks launchd
jobs / files rail intents. Drafts-only is sacred: there is no send route on
any channel.

Run: python3 -m modules.cockpit.server  (the KeepAlive plist does this).
Tests use make_server(host, port, db_path, token) on an ephemeral port.
"""
from __future__ import annotations

import json
import os
import secrets
import stat
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional

from core import config
from modules.cockpit import actions, data

STATIC_DIR = Path(__file__).resolve().parent / "static"
_CT = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
       ".css": "text/css", ".json": "application/json"}

# GET /api/* → reader(db_path) -> JSON-able dict. Extended per phase.
GET_ROUTES: dict = {
    "/api/today": data.today,
    "/api/system": data.system,
    "/api/money": data.money,
}
# POST /api/* routes. Wake (launchd kick) arrives in Phase 5.
POST_ROUTES: dict = {
    "/api/nudge": actions.nudge,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "EtherwiseCockpit/1.0"

    def log_message(self, *args):  # quiet; launchd log stays clean
        pass

    # ── helpers ───────────────────────────────────────────────────────────
    def _authed(self) -> bool:
        token = self.server.token
        return bool(token) and \
            self.headers.get("X-Cockpit-Token") == token

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # localhost single-user tool; lock down embedding/sniffing
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "/index.html") \
            else path[len("/static/"):] if path.startswith("/static/") \
            else path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR not in target.parents or not target.is_file():
            self._json(404, {"error": "not found"})
            return
        self._send(200, target.read_bytes(),
                   _CT.get(target.suffix, "application/octet-stream"))

    # ── verbs ─────────────────────────────────────────────────────────────
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/index.html" or path.startswith("/static/"):
            self._static(path)
            return
        if path.startswith("/api/"):
            if not self._authed():
                self._json(401, {"error": "unauthorized"})
                return
            fn: Optional[Callable] = GET_ROUTES.get(path)
            if fn is None:
                self._json(404, {"error": "unknown endpoint"})
                return
            try:
                self._json(200, fn(self.server.db_path))
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": repr(exc)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/"):
            self._json(404, {"error": "not found"})
            return
        if not self._authed():
            self._json(401, {"error": "unauthorized"})
            return
        fn = POST_ROUTES.get(path)
        if fn is None:
            self._json(404, {"error": "unknown endpoint"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
            self._json(200, fn(self.server.db_path, body))
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": repr(exc)})


def make_server(host: str, port: int, db_path, token: str) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.db_path = db_path
    httpd.token = token
    return httpd


def _ensure_token() -> str:
    tok = config.cockpit_token()
    if tok:
        return tok
    path = config.COCKPIT_TOKEN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    path.write_text(tok + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 600
    return tok


def main() -> int:
    config.ensure_dirs()
    token = _ensure_token()
    httpd = make_server(config.COCKPIT_HOST, config.COCKPIT_PORT,
                        config.DB_PATH, token)
    print(f"cockpit on http://{config.COCKPIT_HOST}:{config.COCKPIT_PORT}"
          f" (token in {config.COCKPIT_TOKEN_FILE})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
