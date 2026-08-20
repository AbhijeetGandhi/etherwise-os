"""IMAP COLLECT rail (Jarvis sweep, inboxes 01/02) — fully offline.

A FakeIMAP stands in for imaplib.IMAP4_SSL (no network). Covers the guarantees
that matter for a rail reading a live mailbox: it is READ-ONLY by construction
(select(readonly=True), FETCH-with-PEEK only, never STORE/EXPUNGE/APPEND/COPY),
its UID cursor advances per inbox, it filters noise/self-noise with the SAME gate
as the Gmail API rail (mail_pipeline parity), and a missing app password skips the
inbox cleanly instead of crashing.
"""
from __future__ import annotations

import unittest

from modules.jarvis import imap_rail


def _hdr_bytes(m):
    return (f"From: {m['from']}\r\nSubject: {m['subject']}\r\n"
            f"Date: {m['date']}\r\nMessage-ID: {m['msgid']}\r\n\r\n").encode()


def _full_bytes(m):
    return (f"From: {m['from']}\r\nSubject: {m['subject']}\r\n"
            f"Date: {m['date']}\r\nMessage-ID: {m['msgid']}\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            f"{m['body']}").encode()


class FakeIMAP:
    """Minimal imaplib.IMAP4_SSL twin. Implements ONLY the read verbs the rail is
    allowed to use — any attempt to mutate (store/expunge/append/copy) would raise
    AttributeError, so read-only is enforced by the fake's very surface."""
    def __init__(self, msgs, uidvalidity=100):
        self.msgs = msgs                 # uid(int) -> message dict
        self.uidvalidity = uidvalidity
        self.calls = []                  # every method call, for assertions
        self.body_fetched = []           # uids whose BODY was pulled

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return ("OK", [str(len(self.msgs)).encode()])

    def status(self, mailbox, spec):
        self.calls.append(("status", mailbox, spec))
        return ("OK", [f"{mailbox} (UIDVALIDITY {self.uidvalidity})".encode()])

    def uid(self, command, *args):
        self.calls.append(("uid", command.upper(), *args))
        cmd = command.upper()
        if cmd == "SEARCH":
            return ("OK", [" ".join(str(u) for u in sorted(self.msgs)).encode()])
        if cmd == "FETCH":
            uid = int(args[0])
            spec = args[1]
            m = self.msgs[uid]
            if "HEADER" in spec:
                raw = _hdr_bytes(m)
            else:
                self.body_fetched.append(uid)
                raw = _full_bytes(m)
            meta = f"{uid} (UID {uid} {spec} {{{len(raw)}}})".encode()
            return ("OK", [(meta, raw), b")"])
        raise AssertionError(f"unexpected uid command {command!r}")

    def logout(self):
        self.calls.append(("logout",))
        return ("BYE", [b""])


INBOX = "abhijeetgandhi01@gmail.com"

MSGS = {
    1: {"from": "Chris Cave <chris@adelye.com>", "subject": "Cert update",
        "date": "Mon, 3 Aug 2026 10:00:00 +0000", "msgid": "<a1@adelye.com>",
        "body": "register nearly done"},
    2: {"from": "Google <no-reply@accounts.google.com>", "subject": "Security alert",
        "date": "Mon, 3 Aug 2026 11:00:00 +0000", "msgid": "<g2@google.com>",
        "body": "new sign-in"},
}


def _fake_factory(fake):
    def factory(host, inbox, password):
        return fake
    return factory


def _collect(fake, cursor=None, pw="secret", **kw):
    return imap_rail.collect(
        INBOX, cursor, max_items=60, first_fetch_days=3,
        conn_factory=_fake_factory(fake), pw_fn=lambda i: pw,
        now=imap_rail._parse_date("Mon, 3 Aug 2026 12:00:00 +0000"), **kw)


class ImapRailTests(unittest.TestCase):
    def test_read_only_by_construction(self):
        fake = FakeIMAP(MSGS)
        _collect(fake)
        # select was read-only
        selects = [c for c in fake.calls if c[0] == "select"]
        self.assertTrue(selects and all(c[2] is True for c in selects))
        # only SEARCH/FETCH uid verbs — never a mutation
        verbs = {c[1] for c in fake.calls if c[0] == "uid"}
        self.assertTrue(verbs.issubset({"SEARCH", "FETCH"}), verbs)
        # every FETCH uses BODY.PEEK (never marks \Seen on a live mailbox)
        fetch_specs = [c[3] for c in fake.calls if c[0] == "uid" and c[1] == "FETCH"]
        self.assertTrue(fetch_specs)
        self.assertTrue(all("PEEK" in s for s in fetch_specs), fetch_specs)

    def test_metadata_first_no_body_for_noise(self):
        fake = FakeIMAP(MSGS)
        events, _ = _collect(fake)
        by_state = {e["state"] for e in events}
        self.assertEqual(by_state, {"held", "excluded"})
        # noise (uid 2) classified from headers only — its body was never fetched
        self.assertEqual(fake.body_fetched, [1])
        excl = next(e for e in events if e["state"] == "excluded")
        self.assertTrue(excl["exclude_reason"].startswith("noise:"))
        self.assertIsNone(excl["text"])

    def test_self_noise_parity(self):
        msgs = {1: {"from": "Etherwise Bot <bot@etherwise.io>",
                    "subject": "Hot Upwork Leads",
                    "date": "Mon, 3 Aug 2026 10:00:00 +0000",
                    "msgid": "<s1@etherwise.io>", "body": "leads"}}
        fake = FakeIMAP(msgs)
        events, _ = _collect(fake)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["state"], "excluded")
        self.assertTrue(events[0]["exclude_reason"].startswith("self-noise"))
        self.assertEqual(fake.body_fetched, [])

    def test_native_id_and_permalink(self):
        fake = FakeIMAP(MSGS)
        events, _ = _collect(fake)
        held = next(e for e in events if e["state"] == "held")
        self.assertEqual(held["native_id"], "abhijeetgandhi01_gmail.com:a1@adelye.com")
        self.assertEqual(held["id"],
                         "evt:email:abhijeetgandhi01_gmail.com:a1@adelye.com")
        self.assertIn("rfc822msgid:a1%40adelye.com", held["permalink"])
        self.assertIn(f"authuser={INBOX}", held["permalink"])
        self.assertEqual(held["source"], "email")

    def test_per_inbox_uid_cursor(self):
        fake = FakeIMAP(MSGS)                         # uids 1,2 ; validity 100
        _, cursor = _collect(fake)
        self.assertEqual(cursor, "100:2")            # <uidvalidity>:<max uid>
        # first fetch is date-bounded (SINCE), not a UID range
        first = next(c for c in fake.calls if c[0] == "uid" and c[1] == "SEARCH")
        self.assertIn("SINCE", first[2:])

        # a new mail arrives; a second run with the cursor sees ONLY it
        fake2 = FakeIMAP({**MSGS, 3: {
            "from": "Bob <bob@realco.com>", "subject": "Quick question",
            "date": "Tue, 4 Aug 2026 09:00:00 +0000", "msgid": "<b3@realco.com>",
            "body": "help with Make?"}})
        events2, cursor2 = _collect(fake2, cursor="100:2")
        self.assertEqual([e["native_id"] for e in events2],
                         ["abhijeetgandhi01_gmail.com:b3@realco.com"])
        self.assertEqual(cursor2, "100:3")
        # second run searches a UID range above the cursor, not SINCE
        srch = next(c for c in fake2.calls if c[0] == "uid" and c[1] == "SEARCH")
        self.assertTrue(any("3:*" in str(a) for a in srch[2:]), srch)

    def test_uidvalidity_change_resets_to_since(self):
        # cursor says validity 100, but the mailbox now reports 999 -> re-fetch by
        # date (UID range would silently skip mail after a mailbox recreation)
        fake = FakeIMAP(MSGS, uidvalidity=999)
        events, cursor = _collect(fake, cursor="100:2")
        self.assertEqual(len(events), 2)                 # both re-seen, not skipped
        self.assertEqual(cursor, "999:2")
        srch = next(c for c in fake.calls if c[0] == "uid" and c[1] == "SEARCH")
        self.assertIn("SINCE", srch[2:])

    def test_missing_password_skips_cleanly(self):
        fake = FakeIMAP(MSGS)
        with self.assertRaises(imap_rail.InboxSkip) as cm:
            imap_rail.collect(INBOX, None, max_items=60,
                              conn_factory=_fake_factory(fake),
                              pw_fn=lambda i: None)
        self.assertIn(f"no app password for {INBOX}", str(cm.exception))
        # never even opened a connection
        self.assertEqual(fake.calls, [])

    def test_logout_always_called(self):
        fake = FakeIMAP(MSGS)
        _collect(fake)
        self.assertIn(("logout",), fake.calls)

    def test_connect_sets_a_bounded_timeout(self):
        """Every IMAP network call MUST be bounded — a rail that can block forever
        eventually hangs the whole sweep (a prior build wedged 24 min on a live
        connect). The real _connect must pass a positive, finite socket timeout to
        IMAP4_SSL; that timeout persists on the socket for every later SEARCH/FETCH.
        No socket is opened here: a stub IMAP4_SSL records the kwargs and returns."""
        seen = {}

        class _StubSSL:
            def __init__(self, host, timeout=None, **kw):
                seen["host"] = host
                seen["timeout"] = timeout

            def login(self, inbox, password):
                seen["logged_in"] = True

        orig = imap_rail.imaplib.IMAP4_SSL
        imap_rail.imaplib.IMAP4_SSL = _StubSSL
        try:
            imap_rail._connect("imap.example.com", INBOX, "secret")
        finally:
            imap_rail.imaplib.IMAP4_SSL = orig

        self.assertTrue(seen.get("logged_in"))
        self.assertIsNotNone(seen.get("timeout"), "IMAP4_SSL called without a timeout")
        self.assertGreater(seen["timeout"], 0)
        self.assertLess(seen["timeout"], 120)   # bounded, not an accidental huge value


class _Sock:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, t):
        self.timeouts.append(t)

    def gettimeout(self):
        return self.timeouts[-1] if self.timeouts else None


class ConnectTimeoutBudgetTests(unittest.TestCase):
    """LOGIN and the read phase get DIFFERENT budgets, for different reasons.

    2026-08-20: abhijeetgandhi02@gmail.com timed out in LOGIN — never in SEARCH
    or FETCH — at the flat 20s, roughly every other sweep since 2026-08-10. 12
    anomalies, `imap_inboxes` reading 1 instead of 2, and that inbox unread for
    eleven days while it held a client contract update. Measured after the fix:
    01 logs in in 1.7s, 02 takes 11.1s. Same host, same code — the number was
    simply too small for one mailbox.

    Raising the single flat timeout would have bought login its headroom and, in
    the same stroke, allowed a stalled SEARCH to hang the sweep for a minute.
    That is why these are two numbers and why the socket is tightened on the way
    in, not left where login needed it."""

    def _stub(self, fail_logins=0, sock=None):
        state = {"n": 0, "timeouts": []}

        class _StubSSL:
            def __init__(_s, host, timeout=None, **kw):
                state["timeouts"].append(timeout)
                _s.sock = sock if sock is not None else _Sock()

            def login(_s, inbox, password):
                state["n"] += 1
                if state["n"] <= fail_logins:
                    raise imap_rail.socket.timeout("The read operation timed out")

            def shutdown(_s):
                state["shutdown"] = state.get("shutdown", 0) + 1

        return _StubSSL, state

    def _run(self, stub, **kw):
        orig = imap_rail.imaplib.IMAP4_SSL
        imap_rail.imaplib.IMAP4_SSL = stub
        try:
            return imap_rail._connect("h", INBOX, "secret", **kw)
        finally:
            imap_rail.imaplib.IMAP4_SSL = orig

    def test_login_gets_the_larger_budget(self):
        stub, state = self._stub()
        self._run(stub, timeout=20, login_timeout=60)
        self.assertEqual(state["timeouts"], [60])

    def test_socket_is_tightened_for_the_read_phase(self):
        """The whole point: generosity is spent on login and then withdrawn, so
        a wedged SEARCH still cannot hang the sweep."""
        stub, _ = self._stub(sock=_Sock())
        client = self._run(stub, timeout=20, login_timeout=60)
        self.assertEqual(client.sock.gettimeout(), 20)

    def test_a_timed_out_login_is_retried(self):
        stub, state = self._stub(fail_logins=1)
        self._run(stub, timeout=20, login_timeout=60, attempts=2)
        self.assertEqual(state["n"], 2)

    def test_the_half_open_client_is_dropped_before_retrying(self):
        stub, state = self._stub(fail_logins=1)
        self._run(stub, timeout=20, login_timeout=60, attempts=2)
        self.assertEqual(state.get("shutdown"), 1)

    def test_it_still_gives_up_and_raises(self):
        """Bounded by construction: attempts * login_timeout, never unbounded,
        and the failure still reaches the sweep so the anomaly still fires."""
        stub, state = self._stub(fail_logins=99)
        with self.assertRaises(imap_rail.socket.timeout):
            self._run(stub, timeout=20, login_timeout=60, attempts=3)
        self.assertEqual(state["n"], 3)

    def test_defaults_are_sane(self):
        from core import config
        self.assertGreater(config.JARVIS_IMAP_LOGIN_TIMEOUT_SECONDS,
                           config.JARVIS_IMAP_TIMEOUT_SECONDS)
        self.assertLessEqual(config.JARVIS_IMAP_LOGIN_ATTEMPTS
                             * config.JARVIS_IMAP_LOGIN_TIMEOUT_SECONDS, 180)


if __name__ == "__main__":
    unittest.main()
