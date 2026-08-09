"""Slack COLLECT rail (Jarvis J2 rail #1) — fully offline.

Slack's web client renders BLANK to screenshots but is fully readable via the
DOM/accessibility tree, so this rail extracts from the DOM only (never pixels).
No live browser here: a scripted eval/navigate pair stands in for Chrome, exactly
as the connects rail's fake_eval does. The tests pin the guarantees that matter:

- normalization: a DOM message row becomes ONE canonical-keyed event with a
  clickable permalink and the deterministic client_id of its workspace;
- direction: our own messages are captured (not dropped) and tagged outbound, so
  the follow-up logic can tell we already replied;
- scope: conversations are ordered most-recent-first and bounded, messages are
  windowed by --since, and the per-workspace cursor advances;
- robustness: a logged-out workspace fails loud with the exact login instruction
  and does NOT take down the other workspaces; a signed-in-but-empty workspace
  degrades (warn), never crashes.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from core import config, db, registry
from modules.jarvis import events, slack_rail


# ── fixtures ──────────────────────────────────────────────────────────────────
NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)


def _ts(days_ago: float) -> str:
    """An epoch.micro Slack ts `days_ago` before NOW."""
    return f"{NOW.timestamp() - days_ago * 86400:.6f}"


WS = {"client_id": "the-cradle-company", "url": "the-cradle-company.slack.com",
      "team_id": "T01FJJNP2F6", "workspace": "The Cradle Company"}


def _conv(cid, name, kind, last_days_ago):
    return {"id": cid, "name": name, "kind": kind,
            "last_activity_ts": _ts(last_days_ago)}


def _msg(ts, author, text, is_me=False, member_id="U9"):
    return {"ts": ts, "author": author, "member_id": member_id,
            "is_me": is_me, "text": text}


def _conv_payload(convs, signed_in=True, ready=True, team_id="T01FJJNP2F6",
                  url="https://app.slack.com/client/T01FJJNP2F6",
                  signin_page=None):
    # signin_page mirrors the DOM probe: True only on a REAL Slack sign-in page.
    # A not-signed-in payload that is not a sign-in page means the page never
    # loaded, which is a different fault with a different remedy.
    if signin_page is None:
        signin_page = not signed_in
    return {"url": url, "signed_in": signed_in, "ready": ready,
            "signin_page": signin_page,
            "team_id": team_id, "conversations": list(convs)}


def _msgs_payload(conv_id, msgs, signed_in=True, ready=True,
                  team_id="T01FJJNP2F6"):
    return {"url": f"https://app.slack.com/client/{team_id}/{conv_id}",
            "signed_in": signed_in, "ready": ready, "team_id": team_id,
            "conversation_id": conv_id, "messages": list(msgs)}


class FakeChrome:
    """Scripted stand-in for the Chrome tab. navigate() records the URL it is
    pointed at; eval() returns the conversations payload while parked at the
    workspace root, or the messages payload for whichever conversation was last
    opened. It NEVER opens a socket or shells out to osascript."""

    def __init__(self, conv_payload, msgs_by_conv):
        self.conv_payload = conv_payload
        self.msgs_by_conv = msgs_by_conv        # conv_id -> payload
        self.current = None
        self.navigations = []

    def navigate(self, url):
        self.navigations.append(url)
        self.current = url

    def eval(self, js):
        cur = self.current or ""
        for cid, payload in self.msgs_by_conv.items():
            if cur.endswith("/" + cid):          # exact conversation match
                return json.dumps(payload)
        return json.dumps(self.conv_payload)


def _collect(chrome, cursor=None, *, since_days=3, max_conversations=40,
             self_authors=("abhijeet gandhi",), **kw):
    return slack_rail.collect_workspace(
        WS, cursor, since_days=since_days, max_conversations=max_conversations,
        now=NOW, eval_fn=chrome.eval, navigate=chrome.navigate,
        sleep=lambda s: None, self_authors=self_authors, **kw)


# ── parse / normalization ─────────────────────────────────────────────────────
class ParseTests(unittest.TestCase):
    def test_message_becomes_a_normalized_client_event(self):
        m = _msg(_ts(0.5), "Jane Doe", "When can we ship?")
        ev = slack_rail.parse_message(m, WS, team_id="T01FJJNP2F6",
                                      conv_id="C123", self_authors=())
        self.assertEqual(ev["source"], "slack")
        self.assertEqual(ev["native_id"], "T01FJJNP2F6:C123:" + m["ts"])
        self.assertEqual(ev["id"], "evt:slack:T01FJJNP2F6:C123:" + m["ts"])
        self.assertEqual(ev["client_id"], "the-cradle-company")
        self.assertEqual(ev["state"], "mapped")     # workspace IS the client
        self.assertEqual(ev["author"], "Jane Doe")
        self.assertEqual(ev["text"], "When can we ship?")
        self.assertEqual(ev["direction"], "inbound")

    def test_permalink_is_the_archives_deep_link(self):
        m = _msg("1691057400.000100", "Jane Doe", "hi")
        ev = slack_rail.parse_message(m, WS, team_id="T01FJJNP2F6",
                                      conv_id="C123", self_authors=())
        self.assertEqual(
            ev["permalink"],
            "https://the-cradle-company.slack.com/archives/C123/p1691057400000100")

    def test_our_own_message_is_captured_and_tagged_outbound(self):
        # via Slack's own is_me DOM cue
        m = _msg(_ts(0.1), "Abhijeet Gandhi", "Shipping Friday", is_me=True)
        ev = slack_rail.parse_message(m, WS, team_id="T01FJJNP2F6",
                                      conv_id="C123", self_authors=())
        self.assertEqual(ev["direction"], "outbound")
        self.assertIsNotNone(ev["text"])            # captured, NOT excluded

    def test_outbound_falls_back_to_the_self_author_name(self):
        # DOM did not expose is_me, but the display name is ours
        m = _msg(_ts(0.1), "Abhijeet Gandhi", "on it")
        ev = slack_rail.parse_message(m, WS, team_id="T01FJJNP2F6", conv_id="C1",
                                      self_authors=("abhijeet gandhi",))
        self.assertEqual(ev["direction"], "outbound")

    def test_team_id_falls_back_to_the_registry_when_dom_omits_it(self):
        m = _msg(_ts(0.1), "Jane", "hi")
        ev = slack_rail.parse_message(m, WS, team_id=None, conv_id="C1",
                                      self_authors=())
        self.assertTrue(ev["native_id"].startswith("T01FJJNP2F6:C1:"))


# ── collect flow (scripted Chrome) ────────────────────────────────────────────
class CollectTests(unittest.TestCase):
    def test_orders_by_recent_activity_and_bounds_the_count(self):
        convs = [_conv(f"C{i}", f"chan{i}", "channel", last_days_ago=i * 0.1)
                 for i in range(50)]                 # 50 conversations, C0 freshest
        msgs = {c["id"]: _msgs_payload(c["id"], [_msg(c["last_activity_ts"],
                "Jane", "hi")]) for c in convs}
        chrome = FakeChrome(_conv_payload(convs), msgs)
        evs, _cur, stats = _collect(chrome, max_conversations=40, since_days=999)
        # only the 40 freshest conversations were opened, never a random slice
        opened = [u for u in chrome.navigations if "/client/" in u]
        self.assertEqual(len(opened), 40)
        self.assertIn("/C0", opened[0])              # freshest first
        self.assertTrue(all("/C4" not in u           # C40..C49 dropped
                            for u in opened if u.endswith(("C40", "C49"))))
        self.assertEqual(stats["conversations_scanned"], 40)

    def test_windows_messages_by_since(self):
        conv = _conv("C1", "proj", "channel", last_days_ago=0.1)
        msgs = {"C1": _msgs_payload("C1", [
            _msg(_ts(0.5), "Jane", "recent"),        # inside 3d window
            _msg(_ts(10), "Jane", "old"),            # outside -> dropped
        ])}
        chrome = FakeChrome(_conv_payload([conv]), msgs)
        evs, _cur, _stats = _collect(chrome, since_days=3)
        texts = [e["text"] for e in evs]
        self.assertIn("recent", texts)
        self.assertNotIn("old", texts)

    def test_collects_both_channels_and_dms(self):
        convs = [_conv("C1", "proj", "channel", 0.2),
                 _conv("D1", "Jane Doe", "dm", 0.1)]
        msgs = {"C1": _msgs_payload("C1", [_msg(_ts(0.2), "Jane", "chan msg")]),
                "D1": _msgs_payload("D1", [_msg(_ts(0.1), "Jane", "dm msg")])}
        chrome = FakeChrome(_conv_payload(convs), msgs)
        evs, _cur, _stats = _collect(chrome)
        self.assertEqual({e["text"] for e in evs}, {"chan msg", "dm msg"})

    def test_cursor_advances_to_the_max_ts_and_second_run_sees_only_new(self):
        conv = _conv("C1", "proj", "channel", 0.1)
        first = {"C1": _msgs_payload("C1", [_msg(_ts(1), "Jane", "one")])}
        chrome = FakeChrome(_conv_payload([conv]), first)
        evs, cursor, _ = _collect(chrome, since_days=5)
        self.assertEqual(len(evs), 1)
        self.assertEqual(cursor, _ts(1))             # high-watermark = max ts seen

        conv2 = _conv("C1", "proj", "channel", 0.05)
        second = {"C1": _msgs_payload("C1", [
            _msg(_ts(1), "Jane", "one"),             # already seen (<= cursor)
            _msg(_ts(0.2), "Jane", "two")])}         # new
        chrome2 = FakeChrome(_conv_payload([conv2]), second)
        evs2, cursor2, _ = _collect(chrome2, cursor=cursor, since_days=5)
        self.assertEqual([e["text"] for e in evs2], ["two"])
        self.assertEqual(cursor2, _ts(0.2))

    def test_idle_conversation_is_not_reopened(self):
        # a conversation whose last activity predates the cursor has nothing new,
        # so the rail must not spend a navigation opening it
        conv = _conv("C1", "proj", "channel", last_days_ago=5)
        chrome = FakeChrome(_conv_payload([conv]),
                            {"C1": _msgs_payload("C1", [_msg(_ts(5), "J", "x")])})
        evs, _cur, stats = _collect(chrome, cursor=_ts(2), since_days=10)
        self.assertEqual(evs, [])
        self.assertEqual([u for u in chrome.navigations if "/client/" in u], [])
        self.assertEqual(stats["conversations_scanned"], 0)

    def test_logged_out_workspace_fails_loud_with_login_instruction(self):
        chrome = FakeChrome(
            _conv_payload([], signed_in=False, signin_page=True,
                          url="https://the-cradle-company.slack.com/signin"), {})
        with self.assertRaises(slack_rail.WorkspaceLoggedOut) as cm:
            _collect(chrome)
        self.assertIn("The Cradle Company", str(cm.exception))
        self.assertIn("log into", str(cm.exception).lower())
        self.assertIn("Chrome", str(cm.exception))

    def test_page_that_never_loads_is_reported_as_a_load_failure_not_a_logout(self):
        # Regression, 2026-08-10: an unloaded tab (chrome-error://) reports
        # signed_in=False, and the rail called all four workspaces "logged out"
        # in under a second while Chrome was demonstrably signed in. A load
        # failure and a dead session need different human actions, so the
        # message must not send Abhijeet hunting a session problem.
        chrome = FakeChrome(
            _conv_payload([], signed_in=False, signin_page=False,
                          url="chrome-error://chromewebdata/"), {})
        with self.assertRaises(slack_rail.WorkspaceLoggedOut) as cm:
            _collect(chrome)
        msg = str(cm.exception)
        self.assertIn("The Cradle Company", msg)
        self.assertIn("LOAD failure", msg)
        self.assertIn("chrome-error", msg)
        self.assertNotIn("log into", msg.lower())

    def test_conversation_without_an_activity_ts_is_still_opened(self):
        # Regression, 2026-08-10: Slack's virtual-list sidebar rows carry NO
        # activity timestamp, so treating a missing ts as "no new activity"
        # skipped every conversation in every workspace — silent total data
        # loss. Unknown must mean "open it and let the --since window decide".
        conv = _conv("C1", "proj", "channel", last_days_ago=5)
        conv["last_activity_ts"] = ""                     # what Slack actually returns
        chrome = FakeChrome(_conv_payload([conv]),
                            {"C1": _msgs_payload("C1", [_msg(_ts(1), "J", "hi")])})
        evs, _cur, stats = _collect(chrome, since_days=10)
        self.assertEqual([e["text"] for e in evs], ["hi"])
        self.assertEqual(stats["conversations_scanned"], 1)
        self.assertEqual(stats["no_activity_ts"], 1)

    def test_conversations_are_opened_via_the_team_scoped_client_url(self):
        # Regression, 2026-08-10 (live): the rail navigated to
        # https://<ws>.slack.com/archives/<CONV>, whose docstring claimed it
        # redirects into the client. It does not — it parks on a "Redirecting…"
        # shell (39s+ observed, never resolved), so every conversation read back
        # zero messages. The team-scoped client URL loads in ~6s.
        self.assertEqual(
            slack_rail._conversation_url(WS, "C123", "T01FJJNP2F6"),
            "https://app.slack.com/client/T01FJJNP2F6/C123")
        # no team id in the registry -> archives is the only thing left to try
        self.assertEqual(
            slack_rail._conversation_url({"url": "x.slack.com"}, "C123"),
            "https://x.slack.com/archives/C123")
        # the PERMALINK is unaffected — archives is still the shareable form
        self.assertTrue(slack_rail.permalink(WS["url"], "C123", "1.2")
                        .startswith("https://the-cradle-company.slack.com/archives/"))

    def test_stale_workspace_page_is_refused_not_attributed(self):
        # Regression, 2026-08-10 (live): navigating A -> B leaves A's fully
        # painted DOM in the tab, and A answers ready:true instantly. The rail
        # accepted it, so ALL FOUR workspaces returned Beglaubigt's 14
        # conversations and one client's messages were about to be filed under
        # another. 'ready' must mean "the page I asked for", not "a page".
        other = _conv_payload([_conv("C9", "someone-elses", "channel", 1)],
                              team_id="T_OTHER_WORKSPACE")
        chrome = FakeChrome(other, {})
        with self.assertRaises(slack_rail.WrongPageLoaded) as cm:
            _collect(chrome)                       # ws under test is T01FJJNP2F6
        msg = str(cm.exception)
        self.assertIn("T_OTHER_WORKSPACE", msg)
        self.assertIn("T01FJJNP2F6", msg)
        self.assertIn("Refusing", msg)

    def test_stale_conversation_page_is_skipped_not_misfiled(self):
        # Same trap one level down: the previous conversation is still painted
        # while the next loads. Skipping loses one conversation (recoverable);
        # accepting it writes the wrong permalink onto real messages (not).
        convs = [_conv("C1", "first", "channel", 1), _conv("C2", "second", "channel", 1)]
        for c in convs:
            c["last_activity_ts"] = ""
        chrome = FakeChrome(
            _conv_payload(convs),
            # both navigations answer with C1's DOM
            {"C1": _msgs_payload("C1", [_msg(_ts(1), "J", "from one")]),
             "C2": _msgs_payload("C1", [_msg(_ts(1), "J", "from one")])})
        evs, _cur, stats = _collect(chrome, since_days=10)
        self.assertEqual([e["text"] for e in evs], ["from one"])
        self.assertEqual(stats["stale_conversation"], 1)
        self.assertTrue(all("/C1/" in e["permalink"] for e in evs))

    def test_signed_in_but_empty_workspace_reports_zero_not_a_crash(self):
        chrome = FakeChrome(_conv_payload([]), {})
        evs, _cur, stats = _collect(chrome)
        self.assertEqual(evs, [])
        self.assertEqual(stats["conversations_seen"], 0)
        self.assertTrue(stats["signed_in"])


# ── run_rail integration (temp DB + registry + scripted Chrome) ───────────────
REG_YAML = """\
clients:
  -
    id: the-cradle-company
    name: The Cradle Company
    slack:
      workspace: The Cradle Company
      url: the-cradle-company.slack.com
      team_id: T01FJJNP2F6
    billing:
      canonical: The Cradle Company
  -
    id: beglaubigt
    name: Beglaubigt
    slack:
      workspace: Beglaubigt.de
      url: beglaubigt.slack.com
    billing:
      canonical: Beglaubigt
"""


class RunRailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="slack-rail-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)
        (self.tmp / "clients.yaml").write_text(REG_YAML)
        self.reg = registry.load(self.tmp / "clients.yaml", force=True)
        self.ctx = mock.Mock(db_path=self.db_path, task_name="jarvis_slack",
                             run_id=1)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        registry.invalidate()

    def _browser_for(self, per_ws):
        """per_ws: {workspace_subdomain: (conv_payload, msgs_by_conv)}. Returns
        an (eval_fn, navigate) pair routing to whichever workspace's scripted
        Chrome was last navigated to.

        Two URL shapes must route, because the rail uses both: the workspace
        root (<sub>.slack.com) and the team-scoped conversation deep link
        (app.slack.com/client/<TEAM>/<CONV>), which contains no subdomain at
        all. Team ids are read off each fixture's own conversations payload, so
        the harness cannot drift from the fixtures."""
        browsers = {sub: FakeChrome(cp, mp) for sub, (cp, mp) in per_ws.items()}
        team_of = {sub: (cp.get("team_id") or "") for sub, (cp, _mp) in per_ws.items()}
        state = {"chrome": None}

        def navigate(url):
            for sub, chrome in browsers.items():
                team = team_of.get(sub)
                if sub in url or (team and f"/client/{team}/" in url):
                    state["chrome"] = chrome
                    chrome.navigate(url)
                    return
            # A deep link whose team matches nothing stays in the workspace we
            # are already inside — same as a real in-app route change.
            if "/client/" in url and state["chrome"] is not None:
                state["chrome"].navigate(url)
                return

        def eval_fn(js):
            return state["chrome"].eval(js)

        return eval_fn, navigate

    def test_stores_mapped_events_with_client_and_direction(self):
        cp = _conv_payload([_conv("C1", "proj", "channel", 0.2)])
        mp = {"C1": _msgs_payload("C1", [
            _msg(_ts(0.2), "Jane Doe", "when can we ship?"),
            _msg(_ts(0.1), "Abhijeet Gandhi", "Friday", is_me=True)])}
        eval_fn, navigate = self._browser_for(
            {"the-cradle-company.slack.com": (cp, mp)})
        out = slack_rail.run_rail(self.ctx, reg=self.reg, eval_fn=eval_fn,
                                  navigate=navigate, sleep=lambda s: None, now=NOW)
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT client_id, state, direction, text FROM events"
                " WHERE source='slack' ORDER BY ts").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["client_id"] == "the-cradle-company"
                            and r["state"] == "mapped" for r in rows))
        self.assertEqual({r["direction"] for r in rows}, {"inbound", "outbound"})
        self.assertEqual(out["stored"], 2)

    def test_a_logged_out_workspace_degrades_and_others_continue(self):
        cradle = (_conv_payload([], signed_in=False), {})   # logged out
        beg_cp = _conv_payload([_conv("C9", "beg", "channel", 0.1)],
                               team_id="T02BEG")
        beg_mp = {"C9": _msgs_payload("C9", [_msg(_ts(0.1), "Ioannis", "hi")],
                                      team_id="T02BEG")}
        eval_fn, navigate = self._browser_for(
            {"the-cradle-company.slack.com": cradle,
             "beglaubigt.slack.com": (beg_cp, beg_mp)})
        out = slack_rail.run_rail(self.ctx, reg=self.reg, eval_fn=eval_fn,
                                  navigate=navigate, sleep=lambda s: None, now=NOW)
        # the sibling workspace still produced its event
        with db.connect(self.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM events WHERE"
                             " client_id='beglaubigt'").fetchone()[0]
        self.assertEqual(n, 1)
        # the dead workspace raised a CRITICAL anomaly carrying the instruction
        kinds = [c.args[0] for c in self.ctx.anomaly.call_args_list]
        self.assertIn("jarvis_slack_logged_out", kinds)
        crit = next(c for c in self.ctx.anomaly.call_args_list
                    if c.args[0] == "jarvis_slack_logged_out")
        self.assertEqual(crit.args[1], "critical")
        self.assertEqual(out["logged_out"], ["The Cradle Company"])

    def test_empty_workspace_files_a_warn_not_a_crash(self):
        eval_fn, navigate = self._browser_for(
            {"the-cradle-company.slack.com": (_conv_payload([]), {}),
             "beglaubigt.slack.com": (_conv_payload([], team_id="T02BEG"), {})})
        out = slack_rail.run_rail(self.ctx, reg=self.reg, eval_fn=eval_fn,
                                  navigate=navigate, sleep=lambda s: None, now=NOW)
        kinds = [c.args[0] for c in self.ctx.anomaly.call_args_list]
        self.assertIn("jarvis_slack_empty_window", kinds)
        warn = next(c for c in self.ctx.anomaly.call_args_list
                    if c.args[0] == "jarvis_slack_empty_window")
        self.assertEqual(warn.args[1], "warn")
        self.assertEqual(out["stored"], 0)

    def test_dry_run_stores_nothing(self):
        cp = _conv_payload([_conv("C1", "proj", "channel", 0.2)])
        mp = {"C1": _msgs_payload("C1", [_msg(_ts(0.2), "Jane", "hi")])}
        eval_fn, navigate = self._browser_for(
            {"the-cradle-company.slack.com": (cp, mp)})
        out = slack_rail.run_rail(self.ctx, reg=self.reg, eval_fn=eval_fn,
                                  navigate=navigate, sleep=lambda s: None,
                                  dry_run=True, now=NOW)
        with db.connect(self.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(n, 0)
        self.assertEqual(out["collected"], 1)     # collected but not stored

    def test_events_are_idempotent_across_runs(self):
        cp = _conv_payload([_conv("C1", "proj", "channel", 0.2)])
        mp = {"C1": _msgs_payload("C1", [_msg(_ts(0.2), "Jane", "hi")])}
        spec = {"the-cradle-company.slack.com": (cp, mp)}
        # reset the cursor each run so the SAME message is re-collected: the
        # event store, not the cursor, is what must make a re-run a no-op
        for _ in range(2):
            with db.connect(self.db_path) as conn:
                conn.execute("DELETE FROM sync_cursors")
            eval_fn, navigate = self._browser_for(spec)
            slack_rail.run_rail(self.ctx, reg=self.reg, eval_fn=eval_fn,
                                navigate=navigate, sleep=lambda s: None, now=NOW)
        with db.connect(self.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(n, 1)


# ── registry: slack workspace indexing + the seeded five ──────────────────────
class RegistrySlackTests(unittest.TestCase):
    def test_matches_a_slack_team_to_its_client(self):
        reg = registry.loads_registry(REG_YAML)
        self.assertEqual(reg.match_slack_team("T01FJJNP2F6"), "the-cradle-company")

    def test_slack_workspaces_lists_only_url_bearing_clients(self):
        reg = registry.loads_registry(REG_YAML)
        wss = {w["client_id"]: w for w in reg.slack_workspaces()}
        self.assertEqual(set(wss), {"the-cradle-company", "beglaubigt"})
        self.assertEqual(wss["the-cradle-company"]["team_id"], "T01FJJNP2F6")
        self.assertIsNone(wss["beglaubigt"]["team_id"])

    def test_duplicate_slack_team_is_a_load_error(self):
        dup = REG_YAML + """\
  -
    id: impostor
    name: Impostor
    slack:
      workspace: X
      url: impostor.slack.com
      team_id: T01FJJNP2F6
"""
        with self.assertRaises(registry.RegistryError):
            registry.loads_registry(dup)

    def test_live_registry_has_the_seeded_workspaces(self):
        reg = registry.load()
        wss = {w["client_id"]: w for w in reg.slack_workspaces()}
        # the four client workspaces from the recon (internal Etherwise excluded)
        self.assertEqual(wss["the-cradle-company"]["team_id"], "T01FJJNP2F6")
        self.assertEqual(wss["optimize-is"]["url"], "numbersdriven.slack.com")
        self.assertEqual(wss["beglaubigt"]["url"], "beglaubigt.slack.com")
        self.assertEqual(wss["cmf-publishing"]["url"], "cmfirm.slack.com")


# ── events.store_event persists direction ─────────────────────────────────────
class DirectionColumnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="slack-dir-"))
        self.db_path = self.tmp / "v3.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_store_event_writes_the_direction(self):
        ev = {"id": "evt:slack:T:C:1.0", "source": "slack", "native_id": "T:C:1.0",
              "ts": "2026-08-03T00:00:00+00:00", "author": "Jane", "subject": None,
              "client_id": "the-cradle-company", "state": "mapped",
              "text": "hi", "permalink": "https://x/archives/C/p10",
              "raw_ref": "slack/T/C", "direction": "outbound"}
        with db.connect(self.db_path) as conn:
            events.store_event(conn, ev, 1)
            row = conn.execute("SELECT direction FROM events WHERE id=?",
                               (ev["id"],)).fetchone()
        self.assertEqual(row["direction"], "outbound")


# ── source-health registration ────────────────────────────────────────────────
class SourceHealthTests(unittest.TestCase):
    def test_slack_rail_is_a_registered_source_with_degrade_kinds(self):
        src = {s["key"]: s for s in config.SOURCES}.get("jarvis_slack")
        self.assertIsNotNone(src, "jarvis_slack must be in config.SOURCES")
        self.assertEqual(src["task_name"], "jarvis_slack")
        self.assertIn("jarvis_slack_empty_window", src["degrade_kinds"])
        self.assertIn("jarvis_slack_logged_out", src["degrade_kinds"])


if __name__ == "__main__":
    unittest.main()
