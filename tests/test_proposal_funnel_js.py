"""modules/cockpit/static/js/propfunnel.js — the Funnel view's arithmetic.

Runs the real module under node (require()), so the assertions cover the code
the browser ships. Pins the funnel law (every stage a subset of the one above,
no step over 100%, a reply counts as an open), the split (groups add up to the
whole, "Not known" last), the connects rule (an outbid or closed boost costs
only the base) and the plain-word signal (Fisher exact, too-few guard).
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "modules" / "cockpit" / "static" / "js" / "propfunnel.js"


def _node():
    node = shutil.which("node")
    if node:
        return node
    for cand in ("/opt/homebrew/bin/node", "/usr/local/bin/node",
                 *glob.glob(str(Path.home() / ".nvm/versions/node/*/bin/node"))):
        if os.path.exists(cand):
            return cand
    return None


DRIVER = r"""
const F = require(process.argv[2]);
function row(o) { return Object.assign({ id: String(Math.random()), sent: "2026-09-10T10:00:00Z", k: "cold",
  op: 0, rep: 0, hired: 0, boost: 1, cx: 100, cb: 20, bo: null }, o); }
const rows = [];
for (let i = 0; i < 40; i++) rows.push(row({}));                                  // never opened
for (let i = 0; i < 12; i++) rows.push(row({ op: 1 }));                           // opened, no reply
for (let i = 0; i < 5; i++) rows.push(row({ op: 1, rep: 1 }));                    // answered
rows.push(row({ op: 0, rep: 1 }));                                                 // answered from the list
for (let i = 0; i < 2; i++) rows.push(row({ op: 1, rep: 1, hired: 1 }));          // hired
rows.push(row({ op: null, boost: 0, cx: null, cb: null }));                        // page gone, unknown cost
const st = F.stages(rows);
const groups = F.split(rows, "boost");
const unknownLast = F.split([row({ boost: null }), row({})], "boost");
const out = {
  n: st.map((s) => s.n),
  ofPrev: st.map((s) => s.ofPrev),
  monotone: st.every((s, i) => !i || s.n <= st[i - 1].n),
  nested: st.every((s, i) => !i || s.rows.every((r) => st[i - 1].rows.indexOf(r) >= 0)),
  splitSum: groups.reduce((a, g) => a + g.stages[0].n, 0),
  splitKeys: groups.map((g) => g.key),
  unknownLast: unknownLast.map((g) => g.key),
  rec: F.opensOnRecord(st[1].rows),
  spent: [F.connectsSpent(row({ bo: "outbid, bid returned" })), F.connectsSpent(row({ bo: "charged" })),
          F.connectsSpent(row({ boost: 0, cx: 20, cb: 20 })), F.connectsSpent(row({ cx: null, cb: null }))],
  money: F.money(rows, { price_per_connect: 0.15, won: {} }),
  fisher: F.fisher(8, 2, 1, 5),
  sigFew: F.signal(2, 3, 10, 60),
  sigClear: F.signal(30, 40, 35, 120),
  invite: F.stages([row({ k: "invite", rep: 1, hired: 1 }), row({ k: "invite", rep: 1 })], "invite").map((s) => s.n),
  period: F.periodBounds("4w", "2026-10-08"),
  ytd: F.periodBounds("ytd", "2026-10-08"),
  months: F.byMonth([row({ sent: "2026-08-02T00:00:00Z" }), row({ sent: "2026-09-02T00:00:00Z", op: 1 })])
    .map((m) => [m.month, m.sent, m.opened]),
};
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(_node(), "node is not installed on this machine")
class TestPropFunnelJS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        d = tempfile.mkdtemp(prefix="propfunnel-")
        cls.driver = Path(d) / "driver.js"
        cls.driver.write_text(DRIVER)
        res = subprocess.run([_node(), str(cls.driver), str(JS)], capture_output=True, text=True, timeout=30)
        if res.returncode:
            raise AssertionError(res.stderr)
        cls.out = json.loads(res.stdout)

    def test_the_funnel_nests_and_never_exceeds_100(self):
        o = self.out
        self.assertEqual(o["n"], [61, 20, 8, 2])
        self.assertTrue(o["monotone"])
        self.assertTrue(o["nested"])
        self.assertTrue(all(x is None or x <= 1 for x in o["ofPrev"]))

    def test_a_reply_counts_as_an_open_and_says_so(self):
        self.assertEqual(self.out["rec"], {"onRecord": 19, "answeredFromList": 1})

    def test_split_adds_up_and_unknown_goes_last(self):
        self.assertEqual(self.out["splitSum"], 61)
        self.assertEqual(self.out["splitKeys"], ["Boosted", "Not boosted"])
        self.assertEqual(self.out["unknownLast"], ["Boosted", "Not known"])

    def test_connects_rule(self):
        self.assertEqual(self.out["spent"], [20, 100, 20, None])
        m = self.out["money"]
        self.assertEqual(m["connectsUnknown"], 1)
        self.assertEqual(m["hires"], 2)
        self.assertEqual(m["revenueUnknown"], 2)       # no won entries: unknown, never $0

    def test_signal(self):
        self.assertAlmostEqual(self.out["fisher"], 0.0350, places=3)
        self.assertIsNone(self.out["sigFew"])
        self.assertEqual(self.out["sigClear"], "clear")

    def test_invitations_and_periods(self):
        self.assertEqual(self.out["invite"], [2, 1])
        self.assertEqual(self.out["period"]["lo"], "2026-09-11")
        self.assertEqual(self.out["ytd"]["lo"], "2026-01-01")
        self.assertEqual(self.out["months"], [["2026-08", 1, 0], ["2026-09", 1, 1]])
