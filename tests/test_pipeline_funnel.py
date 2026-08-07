"""modules/cockpit/static/funnel.js — the conversion-funnel topology.

The law the UI states is "each band IS a subset of the one above". These tests
pin it as an INVARIANT: band counts are monotonic non-increasing and no
conversion step ever exceeds 100%. The 131% regression (2026-08-08) came from
ordering Hot before Carded when the scanner cards every scored>=FLOOR job, so
Carded (228) was rendered as a child of Hot (174). The fix nests by narrowing
the previous band's rows, so the invariant holds by construction.

Runs the real funnel.js under node (require()), so the assertions cover the
code the browser actually ships — not a Python re-implementation of it.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FUNNEL_JS = ROOT / "modules" / "cockpit" / "static" / "funnel.js"


def _node() -> str | None:
    node = shutil.which("node")
    if node:
        return node
    for cand in ("/opt/homebrew/bin/node", "/usr/local/bin/node",
                 *glob.glob(str(Path.home() / ".local/state/fnm_multishells/*/bin/node")),
                 *glob.glob(str(Path.home() / ".nvm/versions/node/*/bin/node"))):
        if os.path.exists(cand):
            return cand
    return None


# Reproduces the live shape: 480 scanned → 229 passed → 228 scored → 228 carded
# → of which 174 hot. Under the OLD ordering (Hot before Carded) the Carded step
# read 228/174 = 131%; under the new nesting it can only taper.
DRIVER = r"""
const F = require(process.argv[2]);
function rows(spec) {
  // spec: {killed, scoredNoCard, cardedNotHot, cardedHot, passedBelowFloor,
  //        hotUncarded, cardedBelowFloor}
  const out = [];
  for (let i = 0; i < spec.killed; i++) out.push({ k: "no_rate", s: 20, c: false });
  for (let i = 0; i < spec.passedBelowFloor; i++) out.push({ k: null, s: 4, c: false });
  for (let i = 0; i < spec.scoredNoCard; i++) out.push({ k: null, s: 10, c: false });
  for (let i = 0; i < spec.cardedNotHot; i++) out.push({ k: null, s: 12, c: true });
  for (let i = 0; i < spec.cardedHot; i++) out.push({ k: null, s: 24, c: true });
  // adversarial rows the invariant must survive:
  for (let i = 0; i < (spec.hotUncarded || 0); i++) out.push({ k: null, s: 24, c: false });
  for (let i = 0; i < (spec.cardedBelowFloor || 0); i++) out.push({ k: null, s: 3, c: true });
  return out;
}
const scenarios = {
  live_131_shape: rows({ killed: 251, passedBelowFloor: 1, scoredNoCard: 0,
                         cardedNotHot: 54, cardedHot: 174 }),
  hot_uncarded_shadow_era: rows({ killed: 10, passedBelowFloor: 2, scoredNoCard: 5,
                                  cardedNotHot: 8, cardedHot: 6, hotUncarded: 9 }),
  carded_below_floor_anomaly: rows({ killed: 3, passedBelowFloor: 0, scoredNoCard: 2,
                                     cardedNotHot: 4, cardedHot: 3, cardedBelowFloor: 5 }),
  empty: [],
};
const out = {};
for (const [name, rs] of Object.entries(scenarios)) {
  out[name] = F.computeFunnel(rs, { FLOOR: 8, HOT: 16 });
}
process.stdout.write(JSON.stringify(out));
"""


class FunnelTopologyCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = _node()
        if not node:
            raise unittest.SkipTest("node not found; funnel.js topology test needs it")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(DRIVER)
            cls.driver = f.name
        proc = subprocess.run([node, cls.driver, str(FUNNEL_JS)],
                              capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise AssertionError(f"driver failed: {proc.stderr}")
        cls.result = json.loads(proc.stdout)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "driver", None):
            os.unlink(cls.driver)

    def test_band_counts_are_monotonic_non_increasing(self):
        for name, fn in self.result.items():
            counts = [s["n"] for s in fn["steps"]]
            for a, b in zip(counts, counts[1:]):
                self.assertLessEqual(
                    b, a, f"{name}: band {b} exceeds its parent {a} — subset law"
                          f" broken (counts={counts})")

    def test_no_conversion_step_exceeds_100_percent(self):
        for name, fn in self.result.items():
            steps = fn["steps"]
            for prev, cur in zip(steps, steps[1:]):
                if prev["n"]:
                    pct = 100.0 * cur["n"] / prev["n"]
                    self.assertLessEqual(
                        pct, 100.0,
                        f"{name}: {cur['k']} is {pct:.0f}% of {prev['k']} (>100%)")

    def test_order_is_scanned_passed_scored_carded_hot(self):
        # Carded must sit ABOVE Hot (the fix): the scanner cards every scored job,
        # so Carded is a subset of Scored, and Hot is a subset of Carded.
        keys = [s["k"] for s in self.result["live_131_shape"]["steps"]]
        self.assertEqual(keys, ["scanned", "passed", "scored", "carded", "hot"])

    def test_live_shape_carded_exceeds_hot_without_breaking_the_step(self):
        # the exact 131% trigger: more carded (228) than hot (174), now legal
        by_k = {s["k"]: s["n"] for s in self.result["live_131_shape"]["steps"]}
        self.assertEqual(by_k["scored"], 228)
        self.assertEqual(by_k["carded"], 228)
        self.assertEqual(by_k["hot"], 174)
        self.assertLessEqual(by_k["hot"], by_k["carded"])   # no >100% step

    def test_hot_uncarded_rows_never_inflate_the_hot_band(self):
        # a hot job that was never carded (shadow era) is NOT in the carded-child
        # Hot band — else Hot could exceed Carded again.
        by_k = {s["k"]: s["n"] for s in
                self.result["hot_uncarded_shadow_era"]["steps"]}
        self.assertEqual(by_k["hot"], 6)    # only the 6 carded-hot, not the +9
        self.assertLessEqual(by_k["hot"], by_k["carded"])

    def test_carded_below_floor_never_exceeds_scored(self):
        # a carded row scored below the floor must not push Carded past Scored
        by_k = {s["k"]: s["n"] for s in
                self.result["carded_below_floor_anomaly"]["steps"]}
        self.assertLessEqual(by_k["carded"], by_k["scored"])


if __name__ == "__main__":
    unittest.main()
