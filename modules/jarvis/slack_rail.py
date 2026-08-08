"""Slack COLLECT rail (Jarvis J2 rail #1) — client workspaces via the browser.

Slack's web client renders BLANK to screenshots (a heavy SPA) but is fully
readable through the DOM / accessibility tree, so this rail extracts from the DOM
ONLY, never from pixels — the same failure class as the scanner's hidden-tab bug,
which we do not repeat. API / bot install is not available for client workspaces
(we are guests), so the browser is the only path; it is READ-ONLY and NEVER sends
a Slack message, ever.

Shape mirrors the other rails: a thin, injectable transport layer (navigate + a
DOM `eval`) feeds a PURE, unit-tested normalization layer. The transport is the
only thing that touches Chrome; every guarantee below is proven offline against a
scripted transport, exactly like the connects rail's fake_eval.

Deterministic mapping: a client workspace IS the client (registry slack.url /
slack.team_id), so every message maps straight to state='mapped' with the
workspace's client_id — no proposer, no queue. Our OWN messages are captured and
tagged direction='outbound' (not dropped): a rail that only sees inbound cannot
tell that we already replied, and would mint false "owed a reply" cards.

Robustness (learned from every browser rail): bounded per workspace (freshest ~40
conversations, --since window on messages, a poll timeout on every DOM read);
idempotent (canonical event id -> re-run is a no-op); a logged-out workspace FAILS
LOUD with the exact "log into <workspace> in Chrome" instruction and does NOT take
down the sibling workspaces; a signed-in-but-empty workspace DEGRADES (warn),
never crashes.

╔══════════════════════════════════════════════════════════════════════════════╗
║  DOM SELECTORS + JS PROBES live in ONE block below (SELECTORS / the two JS    ║
║  constants). Slack renders blank to screenshots, so these are calibrated      ║
║  against the LIVE accessibility tree with Abhijeet at the ◆ — a 10-minute     ║
║  job, not a hunt. Everything else is transport-agnostic and fixture-tested.   ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core import config, db, registry, runner            # noqa: E402
from modules.jarvis import events                         # noqa: E402

SOURCE = "slack"
TASK_NAME = "jarvis_slack"
POLL_EVERY_SECONDS = 2


class WorkspaceLoggedOut(Exception):
    """The workspace redirected to sign-in / is not showing the signed-in client.
    Carries the exact remediation ('log into <workspace> in Chrome'). Handled
    per-workspace: it degrades that workspace loudly, never the whole rail."""


# ╔═══ CALIBRATION BLOCK — the ONLY Slack-DOM-coupled code in the rail ═════════╗
# Slack's data-qa hooks (web client, 2026). If Slack ships a layout change these
# are the lines to re-point; the JS below is built entirely from this dict so the
# live calibration is edit-here-only. Each probe returns a strict JSON contract
# (documented at each constant); the parse layer depends on the CONTRACT, not the
# selectors, so calibrating selectors never touches tested code.
SELECTORS = {
    # sidebar conversation rows (channels + DMs we are a member of)
    "sidebar_item": '[data-qa="channel_sidebar_name_button"]',
    "sidebar_row": '.p-channel_sidebar__channel',
    # a conversation's rendered message rows + their parts
    "message": '[data-qa="message_container"]',
    "message_sender": '[data-qa="message_sender_name"]',
    "message_text": '[data-qa="message-text"]',
    # signed-in marker (present only in the real client, not the sign-in page)
    "client_marker": '.p-client_container, [data-qa="workspace-list"]',
}

# CONVERSATIONS PROBE — contract:
#   {url, signed_in:bool, ready:bool, team_id:str|null,
#    conversations:[{id, name, kind:'channel'|'dm', last_activity_ts:str}]}
# last_activity_ts is epoch.micro; it drives freshest-first ordering + the cursor.
_EXTRACT_CONVERSATIONS_JS = """
(function(){
  var S = %s;
  var href = location.href;
  var signedIn = /app\\.slack\\.com\\/client\\//.test(href)
      && !/\\/(signin|sso|workspace-signin)/.test(href);
  var team = (href.match(/\\/client\\/(T[A-Z0-9]+)/) || [])[1] || null;
  var rows = [].slice.call(document.querySelectorAll(S.sidebar_row));
  var convs = rows.map(function(r){
    var btn = r.querySelector(S.sidebar_item) || r;
    var link = r.querySelector('a[href*="/client/"]') || btn;
    var href2 = link.getAttribute && link.getAttribute('href') || '';
    var id = (href2.match(/\\/(C[A-Z0-9]+|D[A-Z0-9]+|G[A-Z0-9]+)/) || [])[1]
             || r.getAttribute('data-qa-channel-sidebar-channel-id') || '';
    var kind = /^D/.test(id) ? 'dm' : 'channel';
    // last activity: Slack tags the row with a sortable ts on recency-ordered
    // sections; fall back to a data attribute. (calibration point)
    var ts = r.getAttribute('data-ts')
             || r.getAttribute('data-qa-last-activity-ts') || '';
    return {id:id, name:(btn.textContent||'').trim(), kind:kind,
            last_activity_ts: ts};
  }).filter(function(c){ return c.id; });
  return JSON.stringify({
    url: href, signed_in: signedIn,
    ready: signedIn ? !!document.querySelector(S.client_marker) : true,
    team_id: team, conversations: convs});
})()
"""

# MESSAGES PROBE — contract:
#   {url, signed_in:bool, ready:bool, team_id:str|null, conversation_id:str,
#    messages:[{ts:str, author:str, member_id:str|null, is_me:bool, text:str}]}
# ts is epoch.micro (Slack's message id). is_me marks OUR OWN messages so the
# rail can tag them outbound; the parse layer also falls back to the display name.
_EXTRACT_MESSAGES_JS = """
(function(){
  var S = %s;
  var href = location.href;
  var signedIn = /app\\.slack\\.com\\/client\\//.test(href)
      && !/\\/(signin|sso|workspace-signin)/.test(href);
  var team = (href.match(/\\/client\\/(T[A-Z0-9]+)/) || [])[1] || null;
  var conv = (href.match(/\\/(C[A-Z0-9]+|D[A-Z0-9]+|G[A-Z0-9]+)/) || [])[1] || '';
  var nodes = [].slice.call(document.querySelectorAll(S.message));
  var msgs = nodes.map(function(n){
    var ts = n.getAttribute('data-ts')
             || (n.id||'').replace(/[^0-9.]/g,'');
    var sender = n.querySelector(S.message_sender);
    var body = n.querySelector(S.message_text);
    var me = n.classList.contains('c-message--is-me')
             || n.getAttribute('data-qa-is-self') === 'true';
    return {ts: ts, author: sender ? sender.textContent.trim() : '',
            member_id: n.getAttribute('data-member-id') || null,
            is_me: !!me,
            text: body ? body.textContent.trim() : ''};
  }).filter(function(m){ return m.ts; });
  return JSON.stringify({
    url: href, signed_in: signedIn,
    ready: signedIn ? !!document.querySelector(S.message) : true,
    team_id: team, conversation_id: conv, messages: msgs});
})()
"""
# ╚═══ END CALIBRATION BLOCK ══════════════════════════════════════════════════╝


def _conversations_js() -> str:
    return _EXTRACT_CONVERSATIONS_JS % json.dumps(SELECTORS)


def _messages_js() -> str:
    return _EXTRACT_MESSAGES_JS % json.dumps(SELECTORS)


# ── normalization (pure, transport-agnostic) ──────────────────────────────────
def _subdomain(url: str) -> str:
    return (url or "").split(".", 1)[0]


def _fmt_ts(t: float) -> str:
    return f"{t:.6f}"


def _ts_to_iso(ts: str) -> Optional[str]:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def permalink(ws_url: str, conv_id: str, ts: str) -> str:
    """Slack's own archive deep link: p<ts-with-the-dot-removed> under the
    conversation. One click from a card to the exact message in the browser."""
    return f"https://{ws_url}/archives/{conv_id}/p{str(ts).replace('.', '')}"


def parse_message(m: dict, ws: dict, *, team_id: Optional[str], conv_id: str,
                  self_authors) -> dict:
    """One DOM message row -> one normalized, canonical-keyed event. Deterministic
    client_id (the workspace IS the client) => state 'mapped'. direction is
    'outbound' for our own messages (Slack's is_me cue, or our display name),
    else 'inbound'."""
    team = team_id or ws.get("team_id") or _subdomain(ws["url"])
    ts = str(m.get("ts"))
    author = (m.get("author") or "").strip()
    selves = {a.lower() for a in (self_authors or ())}
    outbound = bool(m.get("is_me")) or author.lower() in selves
    native_id = f"{team}:{conv_id}:{ts}"
    return {
        "id": events.canonical_id(SOURCE, native_id),
        "source": SOURCE, "native_id": native_id,
        "ts": _ts_to_iso(ts), "author": author or None, "subject": None,
        "client_id": ws["client_id"], "state": "mapped",
        "text": (m.get("text") or "").strip() or None,
        "permalink": permalink(ws["url"], conv_id, ts),
        "raw_ref": f"slack/{team}/{conv_id}",
        "direction": "outbound" if outbound else "inbound",
    }


# ── transport-agnostic collect (scripted in tests, live Chrome in prod) ───────
def _extract(eval_fn: Callable[[str], str], js: str, sleep: Callable[[float], None],
             timeout: float) -> dict:
    """Re-eval the DOM probe until it reports `ready` or the poll times out — a
    heavy SPA paints asynchronously, and a bounded poll is the whole point (a rail
    that can block forever eventually hangs the run). Returns the last payload."""
    waited, last = 0.0, {}
    while True:
        last = json.loads(eval_fn(js))
        if last.get("ready") or waited >= timeout:
            return last
        sleep(POLL_EVERY_SECONDS)
        waited += POLL_EVERY_SECONDS


def _workspace_url(ws: dict) -> str:
    return f"https://{ws['url']}/"


def _conversation_url(ws: dict, conv_id: str) -> str:
    # the archives path redirects into the app and loads that conversation,
    # so we never need the team id to navigate — only to key the event.
    return f"https://{ws['url']}/archives/{conv_id}"


def collect_workspace(ws: dict, cursor: Optional[str], *, since_days: float,
                      max_conversations: int, now: datetime,
                      eval_fn: Callable[[str], str],
                      navigate: Callable[[str], None],
                      sleep: Optional[Callable[[float], None]] = None,
                      self_authors=(),
                      poll_timeout: Optional[float] = None) -> tuple:
    """Collect one workspace's recent messages. Returns (events, new_cursor, stats).
    Raises WorkspaceLoggedOut if the workspace is not showing the signed-in client.

    Bounded: the freshest `max_conversations` are considered, ordered by activity
    so a truncation always keeps the newest (never a random slice); only messages
    inside the --since window AND newer than the cursor are emitted."""
    sleep = sleep or time.sleep
    timeout = poll_timeout if poll_timeout is not None \
        else config.JARVIS_SLACK_POLL_TIMEOUT_SECONDS

    navigate(_workspace_url(ws))
    conv_payload = _extract(eval_fn, _conversations_js(), sleep, timeout)
    if not conv_payload.get("signed_in"):
        raise WorkspaceLoggedOut(
            f"Slack workspace '{ws.get('workspace') or ws['client_id']}' is not"
            f" signed in — log into {ws['url']} in Chrome (the rail is read-only"
            " and cannot sign in for you).")

    team_id = conv_payload.get("team_id") or ws.get("team_id")
    convs = conv_payload.get("conversations") or []
    stats = {"signed_in": True, "conversations_seen": len(convs),
             "conversations_scanned": 0, "messages": 0, "skipped": 0}

    # freshest-first, then bound: a truncation keeps the newest, never a slice
    ordered = sorted(convs, key=lambda c: float(c.get("last_activity_ts") or 0),
                     reverse=True)[:max_conversations]

    floor = now.timestamp() - since_days * 86400
    cur = float(cursor) if cursor else None

    def _keep(ts) -> bool:
        try:
            t = float(ts)
        except (TypeError, ValueError):
            return False
        if t < floor:
            return False                 # outside the --since window
        if cur is not None and t <= cur:
            return False                 # already ingested (<= high-watermark)
        return True

    out: list[dict] = []
    max_ts = cur or 0.0
    for c in ordered:
        if not _keep(c.get("last_activity_ts")):
            continue                     # no new activity -> don't even open it
        navigate(_conversation_url(ws, c["id"]))
        msg_payload = _extract(eval_fn, _messages_js(), sleep, timeout)
        stats["conversations_scanned"] += 1
        for m in msg_payload.get("messages") or []:
            ts = m.get("ts")
            if not ts or not (m.get("text") or "").strip() or not m.get("author"):
                stats["skipped"] += 1    # system/structural row -> not an event
                continue
            if not _keep(ts):
                stats["skipped"] += 1
                continue
            out.append(parse_message(m, ws, team_id=team_id, conv_id=c["id"],
                                     self_authors=self_authors))
            max_ts = max(max_ts, float(ts))
            stats["messages"] += 1

    new_cursor = _fmt_ts(max_ts) if max_ts and (cur is None or max_ts > cur) \
        else cursor
    return out, new_cursor, stats


# ── per-workspace cursor (sync_cursors, like the IMAP rail) ───────────────────
def _cursor_name(ws: dict) -> str:
    return f"jarvis_slack_{ws['client_id']}"


def read_cursor(conn, ws: dict) -> Optional[str]:
    row = conn.execute("SELECT value FROM sync_cursors WHERE name=?",
                       (_cursor_name(ws),)).fetchone()
    return row["value"] if row else None


def write_cursor(conn, ws: dict, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_cursors (name, value, updated_at) VALUES"
        " (?,?,datetime('now')) ON CONFLICT(name) DO UPDATE SET"
        " value=excluded.value, updated_at=datetime('now')",
        (_cursor_name(ws), value))


# ── live browser wiring (the calibration surface; injected out in tests) ──────
def _live_browser():
    """Return (eval_fn, navigate, teardown) driving the scanner's parked Chrome
    tab. Slack, like the connects page, will not render in a hidden tab, so the
    tab is made visible for the run and restored on teardown (never brought to the
    front — no focus theft). Reuses the proven scan-tab rails."""
    from modules.upwork import scan_pipeline as sp
    from modules.upwork import connects_history as ch      # its _tab_visible helper
    sys.path.insert(0, str(config.V3_ROOT / "scripts"))
    import feed_bridge                                      # noqa

    tab = sp.ensure_scan_tab()
    vis = ch._tab_visible(sp, tab)
    vis.__enter__()

    def navigate(url: str) -> None:
        sp.navigate_tab(tab, url + sp.MARKER)               # keep the tab findable

    def eval_fn(js: str) -> str:
        return feed_bridge.chrome_eval(js, tab_ref=tab)

    def teardown() -> None:
        try:
            vis.__exit__(None, None, None)
        except Exception:
            pass

    return eval_fn, navigate, teardown


# ── the task: collect -> store (internal only; NEVER an external write) ───────
def run_rail(ctx, *, reg=None, eval_fn=None, navigate=None, sleep=None,
             since_days: Optional[float] = None, dry_run: bool = False,
             max_conversations: Optional[int] = None,
             now: Optional[datetime] = None) -> dict:
    reg = reg or registry.load()
    since_days = config.JARVIS_SLACK_SINCE_DAYS if since_days is None else since_days
    max_conv = max_conversations or config.JARVIS_SLACK_MAX_CONVERSATIONS
    sleep = sleep or time.sleep
    now = now or datetime.now(timezone.utc)
    self_authors = config.JARVIS_SLACK_SELF_AUTHORS
    workspaces = reg.slack_workspaces()
    dbp = ctx.db_path or config.DB_PATH

    teardown = None
    if eval_fn is None or navigate is None:
        eval_fn, navigate, teardown = _live_browser()

    all_events: list[dict] = []
    logged_out: list[str] = []
    empty: list[str] = []
    per_ws: dict = {}
    new_cursors: dict = {}
    try:
        with db.connect(dbp) as conn:
            cursors = {w["client_id"]: read_cursor(conn, w) for w in workspaces}
        for w in workspaces:
            label = w.get("workspace") or w["client_id"]
            try:
                evs, ncur, stats = collect_workspace(
                    w, cursors[w["client_id"]], since_days=since_days,
                    max_conversations=max_conv, now=now, eval_fn=eval_fn,
                    navigate=navigate, sleep=sleep, self_authors=self_authors)
            except WorkspaceLoggedOut as lo:
                logged_out.append(label)
                ctx.anomaly("jarvis_slack_logged_out", "critical",
                            {"workspace": label, "client_id": w["client_id"],
                             "url": w["url"],
                             "instruction": f"log into {w['url']} in Chrome"})
                ctx.log(f"slack {label}: LOGGED OUT — {lo}")
                continue
            except Exception as exc:                     # transient/layout: degrade
                ctx.anomaly("jarvis_slack_error", "warning",
                            {"workspace": label, "error": repr(exc)})
                ctx.log(f"slack {label} failed, skipping this workspace: {exc!r}")
                continue

            per_ws[w["client_id"]] = stats
            all_events.extend(evs)
            if ncur:
                new_cursors[w["client_id"]] = ncur
            if stats["signed_in"] and stats["conversations_seen"] == 0:
                empty.append(label)
                ctx.anomaly("jarvis_slack_empty_window", "warn",
                            {"workspace": label, "client_id": w["client_id"],
                             "note": "signed in but the workspace listed zero"
                                     " conversations — logged-in Slack always has"
                                     " channels, so this is a degrade signal"})
                ctx.log(f"slack {label}: signed in but ZERO conversations — degraded")
    finally:
        if teardown:
            teardown()

    stored = 0
    if not dry_run:
        with db.connect(dbp) as conn:
            for ev in all_events:
                if events.store_event(conn, ev, ctx.run_id) == "inserted":
                    stored += 1
            for w in workspaces:
                cid = w["client_id"]
                nc = new_cursors.get(cid)
                if nc and nc != cursors[cid]:
                    write_cursor(conn, w, nc)

    ctx.log(f"slack rail: {len(workspaces)} workspace(s) -> collected "
            f"{len(all_events)} (stored {stored}) | logged_out {len(logged_out)} "
            f"empty {len(empty)}"
            + (" | DRY-RUN (nothing stored)" if dry_run else ""))
    return {"workspaces": len(workspaces), "collected": len(all_events),
            "stored": stored, "logged_out": logged_out, "empty": empty,
            "per_workspace": per_ws}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Jarvis Slack rail (read-only browser)")
    ap.add_argument("--since", type=float, default=None,
                    help="days of recent activity to collect (default config)")
    ap.add_argument("--dry-run", action="store_true",
                    help="collect + report per workspace, store NOTHING")
    args = ap.parse_args(argv)

    result = runner.run_task(
        TASK_NAME,
        lambda ctx: run_rail(ctx, since_days=args.since, dry_run=args.dry_run),
        module="system")
    print(json.dumps({"run_id": result.run_id, "status": result.status,
                      "metrics": result.metrics}))
    return 0 if result.status in ("completed", "skipped_empty") else 1


if __name__ == "__main__":
    raise SystemExit(main())
