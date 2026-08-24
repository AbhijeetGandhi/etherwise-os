"""Job SOURCING providers — one seam, several transports.

Everything downstream of sourcing (hard_rule_skip, score_job, the v2.3 matrix,
the exemplars, the category gate, drafting, the ClickUp push) is deliberately
untouched by anything in this package. A provider's ONLY job is to return the
job dict `scripts/feed_bridge.parse_job` returns, field for field, so the
scorer cannot tell which transport produced it.

That constraint is the whole design. The 2026-05-22 attempt to move sourcing to
the API failed and was reverted ("[RETIRED] API-only scanner — Can't see Most
Recent / Best Matches / My Feed via Upwork API"), and v3 wrote the conclusion
into scan_pipeline as a permanent SOURCING INVARIANT. Reversing a decision of
that weight needs a provider that is provably identical at the seam, not a
rewrite that is merely plausible — hence a pure `normalize()` per provider,
unit-tested against real captured payloads, and a comparison harness that
scores the same jobs both ways before anything is switched.

Providers:
    browser   the live Chrome/feed_bridge path (scan_pipeline owns it today)
    mcp       mcp.upwork.com — see mcp_feed.py
"""
from __future__ import annotations

BROWSER = "browser"
MCP = "mcp"
PROVIDERS = (BROWSER, MCP)
