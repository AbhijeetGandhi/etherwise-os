"""Cockpit server — stdlib ThreadingHTTPServer, localhost only, token auth.

NO model calls in this process (architecture: cockpit is a view+action plane).
The web process only reads v3 SQLite (read-only) and, later, kicks launchd
jobs / files rail intents. Drafts-only is sacred: there is no send route on
any channel.

Run: python3 -m modules.cockpit.server  (the KeepAlive plist does this).
Tests use make_server(host, port, db_path, token) on an ephemeral port.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import secrets
import stat
from email.utils import formatdate, parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from core import config
from modules.cockpit import actions, data, lifecycle, proposal_funnel

STATIC_DIR = Path(__file__).resolve().parent / "static"
_CT = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
       ".css": "text/css", ".json": "application/json"}

# GET /api/* → reader(db_path) -> JSON-able dict. Extended per phase.
GET_ROUTES: dict = {
    "/api/today": data.today,
    "/api/today/rows": data.today_rows,                  # backlog drill (traceability)
    "/api/pipeline": data.pipeline,
    "/api/clients": data.clients,
    "/api/clients/transactions": data.client_transactions,  # per-client drill
    # the Airtable roster (Status/Tags/Notes). Computed since M0 and never
    # routed, so no screen could show it; the Clients view merges it onto the
    # ledger rows by name. Reader is 60s-cached — Airtable is a network hop.
    "/api/clients/airtable": data.clients_airtable,
    "/api/knowledge": data.knowledge,
    "/api/system": data.system,
    "/api/money": data.money,
    "/api/money/transactions": data.money_transactions,  # drill-down (traceability)
    "/api/pipeline/rows": data.pipeline_rows,            # drill-down (traceability)
    "/api/pipeline/facts": data.pipeline_facts,          # chart source (one row/job)
    # the Upwork tab (Phase 1): the channel's cockpit + the one drill behind
    # every figure on it (?stage=...&week=|month=, ?frm=&to=&kind= for connects)
    "/api/upwork": data.upwork,
    "/api/upwork/rows": data.upwork_rows,
    # the "Run now" buttons poll this (?task=) after their /api/rerun kick
    "/api/upwork/run": data.upwork_run_status,
    # the Upwork tab's Funnel view (Phase 2, Loop 1): one row per proposal +
    # the money that belongs to them; every figure is computed in the browser
    # by propfunnel.js from these rows, so no drill route is needed
    "/api/upwork/funnel": proposal_funnel.funnel,
    # the Pipeline tab (2026-10-09, "Upwork is our sales engine"): what the
    # funnel does not carry — the scanner per day and its hot jobs, every
    # proposal conversation counted, the reader's notes, the tests, where each
    # client is now + the win-back list (doctor-gated), the agents and the
    # knowledge base. No params; the page reads /api/upwork/funnel beside it.
    "/api/lifecycle": lifecycle.lifecycle,
    # Hours (2026-10-19): ClickUp time, read-only, inside the Clients tab.
    # Every figure carries its as-of and drills to the entries behind it.
    "/api/hours": data.hours_overview,
    "/api/hours/client": data.hours_client,
    "/api/hours/gaps": data.hours_gaps,
    "/api/hours/report": data.hours_report,
    "/api/hours/rows": data.hours_rows,       # the drill (traceability)
    "/api/hours/eow": data.hours_eow,         # the weekly client report
    "/api/nudges": data.nudges,   # M4 Chief-of-Staff Nudge Queue
    # the capture box's poll target (?note_id=N): "is it a card yet, and if not
    # what did the classifier decide instead?" The classifying runs in a child
    # process; this is the read that lets the toast tell him the truth.
    "/api/note": data.note_status,
    "/api/queue": data.queue,     # Jarvis J1 mapping queue (unmapped events)
    # Review's other two backlogs. /threads is the unattributed Upwork rooms
    # (?show=open|dismissed|all); /counts is the nav badge's single source —
    # counts only, no rows, because the shell re-reads it every five minutes.
    "/api/review/threads": data.review_threads,
    "/api/review/counts": data.review_counts,
    # Activity: the daily digest (?day=YYYY-MM-DD) + one run's full detail
    # (?run_id=N). Both params are KEYWORD-ONLY on the readers — see below.
    "/api/activity": data.activity,
    "/api/activity/run": data.activity_run,
    # …and the rows behind two of the digest's figures. /sources is the answer
    # to "WHICH 5 Fathom transcripts, and how was each one attributed": one row
    # per source ingested that day, with its facts, cost, queue event and any
    # registry commit it caused. /facts is one row per fact created that day.
    "/api/activity/sources": data.activity_sources,
    "/api/activity/facts": data.activity_facts,
}
# ── the query-param registry (the guard) ─────────────────────────────────────
# A URL query param reaches a reader ONLY if its name appears here.
#
# Reading the signature was not enough on its own. It made URL-reachability an
# ACCIDENT of how a param happened to be declared: `today` is a TEST clock slot,
# positional (and so unreachable) on today()/pipeline()/money()/pipeline_facts()
# but keyword-only (and so reachable) on today_rows()/clients()/pipeline_rows().
# The same knob, safe on four routes and exposed on three. `?today=lol` 500'd
# one of them and stamped a FALSE `"as_of": "lol"` onto the Clients view — a
# provenance line the user reads as server truth.
#
# So the rule is opt-IN, not opt-out. A new keyword-only param is unreachable
# from a URL until someone writes its name below, which is exactly the moment
# they have to answer "is this user input, and what validates it?". Forgetting
# now fails SAFE (the param is ignored) instead of failing open (a raw query
# string handed to an internal slot). Naming a slot `_x` (clients_airtable's
# `_client`) still marks the intent at the READER; this registry enforces it at
# the ROUTER, and both are checked — the mistake has to be made twice now.
#
# VALIDATION of what is in here stays with the reader, in the reader's own error
# envelope (activity()'s `day`, pipeline_rows()' `stage`/`week`): a 200 carrying
# {"error": ...}, which the UI toasts in red. Never a bare empty list —
# UI_BUILD_BRIEF §0, a rejected filter must not look like "no data".
#
# tests/test_cockpit.py::TestQueryParamGuard keeps this in sync with the readers
# and fails the build if a routed reader grows a forwardable param.
QUERY_PARAMS: dict = {
    # /api/knowledge · /api/clients/transactions · /api/money/transactions
    "client": "client id or display name",
    "q": "free-text search",
    # /api/clients
    "sort": "revenue|recent",
    # /api/hours/* — `client` and `q` are already declared above; these are the
    # two this surface adds. `period` is a ClickUp list id, so the sprint being
    # looked at is explicit rather than inferred from a date the UI guessed.
    "period": "sprint list id (defaults to the client's current period)",
    "frm": "YYYY-MM-DD (hours gap window start)",
    "to": "YYYY-MM-DD (hours gap window end)",
    "fmt": "md — ask /api/hours/eow for the sendable markdown too",
    # /api/today/rows
    "which": "fresh|accumulated",
    # /api/pipeline/rows — the one drill behind every Pipeline figure
    "stage": "jobs|scanned|scored|carded|applied|replied|hired|triage|threads",
    "week": "funnel week — the Monday, YYYY-MM-DD",
    "status": "proposal status (threads stage)",
    "frm": "window start — YYYY-MM on /api/pipeline/rows, YYYY-MM-DD on /api/upwork/rows",
    "to": "window end — same shape as frm",
    "skip": "hard_rule_skip reason, or __none__/__any__",
    "feed": "feed_source",
    "icp": "icp_tier",
    "score_min": "integer, inclusive",
    "score_max": "integer, exclusive",
    "carded": "1|0",
    # /api/money/transactions (and the Upwork funnel's month cohort)
    "bucket": "pnl bucket, comma-list",
    "month": "YYYY-MM",
    # /api/money/transactions — the canonical client the Money page's "Where it
    # came from" list GROUPS by, so the drill filters on the same key it
    # grouped by. Not `q`: a payer's own name is often nowhere in their own row
    # (an Upwork hourly line names the freelancer, a Wise line the legal
    # entity), which is how four of September's ten payers drilled to nothing.
    # Matched in Python against the row's derived client, never interpolated
    # into SQL.
    "who": "canonical client name, exact",
    # /api/upwork/rows — the connects drill's kind (open vocabulary, SQL-bound:
    # the rail's kinds grow with the page; a whitelist would go stale)
    "kind": "connects_history.kind",
    # /api/upwork/run — a runs.task_name, refused unless in actions.RERUN_JOBS
    "task": "runs.task_name (allowlisted)",
    "source": "ledger source",
    # /api/activity · /api/activity/run · /api/activity/sources · …/facts
    "day": "IST calendar date, YYYY-MM-DD",
    "run_id": "runs.id",
    # sources.source_type — validated against _SOURCE_TYPE_RE in the reader,
    # not a whitelist: the vocabulary grows with the rails (fathom, loom, email,
    # slack, crm, upwork, clickup…) and a frozen list here would start refusing
    # real types. A syntactically-valid type that matched nothing comes back
    # with total 0 AND the day's real type counts, so "no data" is never
    # mistaken for "you asked for a type that does not exist".
    "source_type": "sources.source_type, e.g. fathom|loom|email|slack",
    # /api/review/threads — which side of the dismissal line to list. Validated
    # in the reader (a 200 carrying {"error": …}), never a silent empty list:
    # "you asked for a value I do not know" and "there is nothing here" are
    # opposite answers and this screen is where the difference costs most.
    "show": "open|dismissed|all",
    # /api/note — which captured note the box is polling. Validated in the
    # reader (integer or a 200 carrying {"error": …}).
    "note_id": "notes.id, integer",
}

POST_ROUTES: dict = {
    "/api/nudge": actions.nudge,
    "/api/queue/resolve": actions.queue_resolve,  # assign|create_alias|ignore
    "/api/queue/batch": actions.queue_batch,      # the same, many at once
    # the thread backlog's own write path: dismiss|restore|assign|create_client.
    # It is separate from queue/resolve because a thread is not an event and
    # approve.resolve cannot reach it (see modules/jarvis/approve.py header).
    "/api/review/thread": actions.review_thread,
    "/api/wake": actions.wake,    # allowlisted launchd kick
    "/api/rail": actions.rail,    # allowlisted rail trigger
    "/api/rerun": actions.rerun,  # allowlisted re-run of a task from the run log
    "/api/ask": actions.ask,      # kb-librarian proxy (model call out-of-process)
    "/api/note": actions.note,    # Quick Capture -> CoS notes inbox (+ classify now)
    # his answer to a note the classifier said was not actionable:
    # 'card' (make one anyway) | 'filed' (agreed, stop showing it)
    "/api/note/resolve": actions.note_resolve,
    "/api/cos-nudge": actions.cos_nudge,  # act on a real M4 nudge
    "/api/card/closure": actions.card_closure,  # J1.5 confirm/reject a chip
    "/api/finance/import": actions.finance_import,  # dry-run/commit via importer bins
}
# raw-body route (file bytes, not JSON): staged into finance-inbox, kind-allowlisted
UPLOAD_ROUTE = "/api/finance/upload"
# ceiling on a JSON POST body (the upload route has its own, larger one)
JSON_MAX_BYTES = 1024 * 1024
# how much of a refused body we will read and throw away so the 413 can reach
# the client as a message rather than as a broken pipe (see Handler._drain)
DRAIN_MAX_BYTES = 32 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "EtherwiseCockpit/1.0"

    def log_message(self, *args):  # quiet; launchd log stays clean
        pass

    # ── helpers ───────────────────────────────────────────────────────────
    def _authed(self) -> bool:
        token = self.server.token
        return bool(token) and \
            self.headers.get("X-Cockpit-Token") == token

    def _send(self, status: int, body: bytes, content_type: str,
              extra: Optional[dict] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # localhost single-user tool; lock down embedding/sniffing
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # Read and discard a body we are about to refuse, in bounded chunks.
    #
    # WHY REFUSING POLITELY MATTERS HERE: replying 413 and closing while the
    # client is still writing gives it a broken pipe, not a message — so the
    # capture box would show "connection error" for what is really "that paste
    # was too big, and your text is still in the box". The whole point of the
    # cap is that the refusal is READABLE. Bounded so draining cannot itself
    # become the unbounded read the cap exists to prevent; past DRAIN_MAX_BYTES
    # we stop and the connection closes, which for a body that size is fine.
    DRAIN_CHUNK = 65536

    def _drain(self, length: int) -> None:
        remaining = min(length, DRAIN_MAX_BYTES)
        while remaining > 0:
            chunk = self.rfile.read(min(self.DRAIN_CHUNK, remaining))
            if not chunk:
                return
            remaining -= len(chunk)

    def _json(self, status: int, obj) -> None:
        # API reads are live state. A JSON body must never be cached, and
        # "no cache headers at all" is not the same thing as "do not cache":
        # HTTP/1.0 lets a client heuristically reuse an un-validated response.
        self._send(status, json.dumps(obj).encode(),
                   "application/json", {"Cache-Control": "no-store"})

    # ── static caching ────────────────────────────────────────────────────
    # THE DEFECT THIS EXISTS FOR (2026-08-13): /static/* went out with no
    # Cache-Control, no ETag and no Last-Modified. Under HTTP/1.0 that leaves
    # freshness entirely to the browser's heuristic, and Chrome duly served a
    # 22 KB today.js out of memory cache while disk held 32 KB. A hash-only SPA
    # navigation never re-requests a module, so every fix shipped that day was
    # invisible in the tab that was open — which makes EVERY other fix
    # unverifiable, not just slow to appear.
    #
    # The contract now: `no-cache` (store it, but revalidate before every
    # reuse) plus a STRONG ETag over the exact bytes served, and Last-Modified
    # as the weak fallback. Unchanged file → 304 with no body; changed file →
    # a different ETag and the new bytes, on the very next request. The hash is
    # taken over the bytes themselves rather than (mtime, size): a same-second,
    # same-length edit is exactly the case that produced this bug, and a
    # validator that cannot see it would reintroduce it.

    @staticmethod
    def _etag_for(body: bytes) -> str:
        return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'

    def _if_none_match(self, etag: str) -> Optional[bool]:
        """True/False if the request carried If-None-Match, else None.

        RFC 9110 §13.1.2: a list, `*` matches anything, and the comparison is
        weak — `W/"x"` and `"x"` are the same entity for a GET.
        """
        raw = self.headers.get("If-None-Match")
        if not raw:
            return None
        if raw.strip() == "*":
            return True
        for cand in raw.split(","):
            c = cand.strip()
            if c.startswith("W/"):
                c = c[2:]
            if c == etag:
                return True
        return False

    def _not_modified_since(self, mtime: float) -> bool:
        raw = self.headers.get("If-Modified-Since")
        if not raw:
            return False
        try:
            since = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return False
        if since is None:
            return False
        # both sides to whole seconds — Last-Modified has no sub-second part
        return int(mtime) <= int(since.timestamp())

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "/index.html") \
            else path[len("/static/"):] if path.startswith("/static/") \
            else path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR not in target.parents or not target.is_file():
            self._json(404, {"error": "not found"})
            return
        try:
            body = target.read_bytes()
            mtime = target.stat().st_mtime
        except OSError:
            self._json(404, {"error": "not found"})
            return
        etag = self._etag_for(body)
        validators = {
            "Cache-Control": "no-cache",
            "ETag": etag,
            "Last-Modified": formatdate(mtime, usegmt=True),
        }
        inm = self._if_none_match(etag)
        fresh = inm if inm is not None else self._not_modified_since(mtime)
        if fresh:
            # 304 carries the validators and NO body (RFC 9110 §15.4.5), so it
            # is also the one response that must not claim a Content-Length.
            self.send_response(304)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in validators.items():
                self.send_header(k, v)
            self.end_headers()
            return
        self._send(200, body,
                   _CT.get(target.suffix, "application/octet-stream"),
                   validators)

    # ── verbs ─────────────────────────────────────────────────────────────
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
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
            # pass through only query params the reader actually declares
            # (keyword-only), so adding ?client=/?q= can't break other routes
            raw = parse_qs(urlparse(self.path).query)
            params = {k: v[0] for k, v in raw.items()}
            # Three conditions, all required (see QUERY_PARAMS above):
            #  1. KEYWORD_ONLY — db_path stays the fixed positional arg, so no
            #     ?db_path= query can redirect the read off the server's DB.
            #  2. in QUERY_PARAMS — registered as genuine user input. This is
            #     the guard: anything a reader declares but nobody registered
            #     (a clock slot, a test hook, a param added last week) is
            #     simply not reachable from a URL.
            #  3. no leading underscore — the reader-side marker for "a test
            #     fills this in, a URL never does" (clients_airtable's
            #     `_client` is an AirtableClient; forwarding a query string
            #     into it handed the reader a str where it wanted an object,
            #     and `?client=Adelye` 500'd that route).
            accepted = {p.name for p in inspect.signature(fn).parameters.values()
                        if p.kind == p.KEYWORD_ONLY
                        and p.name in QUERY_PARAMS
                        and not p.name.startswith("_")}
            kwargs = {k: v for k, v in params.items() if k in accepted}
            try:
                self._json(200, fn(self.server.db_path, **kwargs))
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
        if path == UPLOAD_ROUTE:                 # raw file bytes, not JSON
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            length = int(self.headers.get("Content-Length") or 0)
            if length > actions.UPLOAD_MAX_BYTES:
                self._json(413, {"ok": False, "error": "file too large (20MB cap)"})
                return
            data = self.rfile.read(length) if length else b""
            try:
                self._json(200, actions.finance_upload(
                    q.get("kind", ""), q.get("filename", ""), data))
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": repr(exc)})
            return
        fn = POST_ROUTES.get(path)
        if fn is None:
            self._json(404, {"error": "unknown endpoint"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        # A JSON body had NO ceiling: `rfile.read(Content-Length)` read whatever
        # it was told to. Quick Capture is a box he can paste into, so the first
        # realistic way to reach that was an accidental paste of a whole
        # transcript. Refused with a reason he can read (the UI leaves his text
        # in the box), rather than swallowed into memory. 1 MB is ~50x the
        # capture cap — generous for every other route, finite for this one.
        if length > JSON_MAX_BYTES:
            self._drain(length)                  # …then answer, see _drain
            self._json(413, {"error": "request body too large ({:,} bytes; the"
                                      " cap is {:,}). Nothing was saved."
                             .format(length, JSON_MAX_BYTES)})
            return
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
