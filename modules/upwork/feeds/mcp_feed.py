"""mcp.upwork.com -> the job dict the scorer already eats.

PURE. No network in this module. It takes the two payloads the MCP returns and
produces the exact dict `scripts/feed_bridge.parse_job` produces, so
`hard_rule_skip` and `score_job` run unchanged. Transport lives elsewhere;
keeping it out means the mapping is unit-testable against captured payloads,
which is the only way to earn back the SOURCING INVARIANT (see feeds/__init__).

WHY TWO PAYLOADS. Neither MCP call is sufficient alone, measured 2026-08-24:

    find_jobs action=search   proposal_count, skills[], client.verification_status,
                              client.country, created_date        ...but a TRUNCATED
                              description, no hourly min/max, NO client spend
    find_jobs action=get      full description, hourlyBudgetMin/Max,
                              fixedPriceContractTerms.amount,
                              client_record.spend_total            ...but NO
                              proposal count and country only sometimes

So a job needs one `search` row (cheap, ten per page) plus one `get` (per job).
`get` wins every field both carry, because search's description is a snippet
and its client block is thinner.

2026-11-10, WHAT A SEARCH ROW CARRIES NOW (probed live): the posted hourly
range in `budget` ("$19.00–$40.00/hr" on smart_search, "15.00–20.00/hr" on
search; no budget at all when the client posted none), the client's
total_spent as text, and — on `search` with include_full_details — the whole
untruncated description. It matters because from 6 Nov Upwork refuses `get`
once the day's allowance of job opens is spent ("jobs are being opened too
quickly"): every get from about 10:15 IST to the 05:30 IST reset. This module
read the rate from `get` alone, so for 19 hours a day every hourly job looked
RATE-UNSTATED, and a $15–20/hr job from a proven client was scored 26 and
carded (job 2119895503208409490 — he caught it). The row is now read for what
it says, `get` still wins when it answers, and `rate_source` / `post_source`
record which one spoke (the scan fails closed on rate_source 'unread').

WHAT IS DELIBERATELY DIFFERENT FROM THE SCRAPER — read before treating a diff
as a bug:

  * total_applicants. feed_bridge reads Upwork's proposal TIER and keeps the
    LOW BOUND ("20 to 50" -> 20). The MCP returns the exact count. Competition
    scores will move, and the direction of the move is 'more accurate'.
  * client_total_spent. The SSR payload frequently omits it (four of thirteen
    in the 2026-08-24 sample carried None, including a score-32 job). The MCP
    omits `spend_total` only when the client genuinely has none. Where the
    scraper said None and the MCP says a number, the MCP is right.
  * description. The scraper gets the feed tile's copy; `get` returns the full
    posting. Longer, and occasionally scores differently for that reason.

EXTRA FIELDS the scraper could never see are carried on the dict but are NOT
fed to the scorer yet (`scoring.job_input` pins its own key list, so adding
them here cannot change a score by accident). One change at a time: prove the
swap first, then decide whether bid stats and liveness earn a place in the
matrix.
"""
from __future__ import annotations

import re
from typing import Optional

HOST = "https://www.upwork.com/jobs/"


def _slug(title: str) -> str:
    """feed_bridge builds the href from a slugified title + ciphertext; mirror
    it so a row from either provider has the same shape of link."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (title or "")).strip("-")
    return s[:100] or "job"


# Money as Upwork writes it for people: "$65,990.74", "$1.2K", "$100K+".
_MONEY = re.compile(r"^\s*\$?\s*(\d[\d,]*(?:\.\d+)?)\s*([KkMm])?\s*\+?\s*$")


def _num(v) -> Optional[float]:
    """A number from a number, a numeric string, or a money string.

    Since 2026-09-28 the MCP `get` sends client_record.spend_total as text
    ("$65,990.74"), and float() refused it, so every MCP job from then on was
    stored with client_total_spent = None. Found 2026-10-14 when he asked why
    a job from a client with 33 hires and $66K spent scored 16: the scorer read
    "no spend recorded", the $100K+ bonus had not fired once in 16 days (169
    times in the 13 before), the rate-unstated rule could not see a proven
    spender, and the ICP spend gate could never pass."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    m = _MONEY.match(str(v))
    if not m:
        return None
    unit = {"k": 1e3, "m": 1e6}.get((m.group(2) or "").lower(), 1.0)
    return float(m.group(1).replace(",", "")) * unit


# THE POSTED RATE ON A SEARCH ROW (2026-11-10). Shapes seen live: smart_search
# "$19.00–$40.00/hr", search "15.00–20.00/hr"; Upwork's own tool notes name
# "up to B/hr" (no min) and "A+/hr" (no max). Any dash, "to", optional "$".
_AMT = r"\$?\s*(\d[\d,]*(?:\.\d+)?)"
_PER_HOUR = r"\s*/\s*(?:hr|hour)\s*$"
_RANGE_RE = re.compile(rf"^\s*{_AMT}\s*(?:[-\u2010-\u2015]|to)\s*{_AMT}{_PER_HOUR}", re.I)
_UP_TO_RE = re.compile(rf"^\s*up\s*to\s*{_AMT}{_PER_HOUR}", re.I)
_FROM_RE = re.compile(rf"^\s*{_AMT}\s*\+{_PER_HOUR}", re.I)
_ONE_RE = re.compile(rf"^\s*{_AMT}{_PER_HOUR}", re.I)


def _amt(text: str) -> Optional[float]:
    v = float(text.replace(",", ""))
    return v or None                      # a $0 end is no end


def search_rate(search_row: Optional[dict]) -> tuple:
    """What a search row says about an HOURLY job's posted rate:
      ('range', lo, hi)      a posted range; lo None for "up to B/hr", hi None
                             for "A+/hr"
      ('none', None, None)   no budget on the row: the client posted no range
      ('unread', None, None) a budget is there but is not a rate this code can
                             read — the old "0.0", or a shape Upwork has not
                             shown us yet. The scan fails closed on it: a rate
                             we could not see is never taken for a blank one."""
    b = (search_row or {}).get("budget")
    if b is None or (isinstance(b, str) and not b.strip()):
        return ("none", None, None)
    s = str(b)
    for rx, shape in ((_RANGE_RE, "lo-hi"), (_UP_TO_RE, "hi"),
                      (_FROM_RE, "lo"), (_ONE_RE, "one")):
        m = rx.match(s)
        if not m:
            continue
        if shape == "lo-hi":
            lo, hi = _amt(m.group(1)), _amt(m.group(2))
        elif shape == "hi":
            lo, hi = None, _amt(m.group(1))
        elif shape == "lo":
            lo, hi = _amt(m.group(1)), None
        else:
            lo = hi = _amt(m.group(1))
        return ("range", lo, hi) if (lo or hi) else ("none", None, None)
    return ("unread", None, None)


def _posting(get_row: dict) -> dict:
    return ((get_row or {}).get("data") or {}).get("marketplaceJobPosting") or {}


def _country(get_row: dict, search_row: dict) -> Optional[str]:
    """`get` carries the richer country object but drops it for some clients
    (a US posting in the sample had city+state and no country at all), so
    search is the fallback rather than the other way round."""
    cc = (_posting(get_row).get("clientCompanyPublic") or {}).get("country")
    if isinstance(cc, dict) and cc.get("name"):
        return cc["name"]
    if isinstance(cc, str) and cc.strip():
        return cc.strip()
    return ((search_row or {}).get("client") or {}).get("country") or None


def normalize(search_row: Optional[dict] = None,
              get_row: Optional[dict] = None) -> dict:
    """One MCP job -> the parse_job dict. Either payload may be absent; the
    result degrades field-by-field rather than raising, because a partial row
    that the hard rules can still judge is worth more than an exception."""
    search_row = search_row or {}
    get_row = get_row or {}
    post = _posting(get_row)
    content = post.get("content") or {}
    terms = post.get("contractTerms") or {}
    client_rec = get_row.get("client_record") or {}
    s_client = search_row.get("client") or {}

    jid = str(post.get("id") or search_row.get("id") or "").strip() or None
    title = (content.get("title") or search_row.get("title") or "").strip()
    out: dict = {
        "id": jid,
        "href": f"{HOST}{_slug(title)}_~02{jid}/" if jid else "",
        "title": title,
        "created_dt": (search_row.get("created_date")
                       or search_row.get("published_date") or None),
    }

    # ── contract shape. `get` is authoritative: search reports only a
    # hourly_budget_type string and a `budget` that is "0.0" on every hourly
    # job, which would read as a $0 fixed price and trip the $500 floor.
    ctype = (terms.get("contractType") or "").upper()
    hourly = terms.get("hourlyContractTerms") or {}
    fixed = terms.get("fixedPriceContractTerms") or {}
    if not ctype:
        ctype = "HOURLY" if str(search_row.get("job_type", "")).lower() == "hourly" \
            else ("FIXED" if search_row.get("job_type") else "")
    if ctype == "HOURLY":
        out["contract_type"] = "Hourly"
        lo = _num(hourly.get("hourlyBudgetMin")) or None
        hi = _num(hourly.get("hourlyBudgetMax")) or None
        state, s_lo, s_hi = search_rate(search_row)
        if lo or hi:
            source = "get"
        elif state == "range":            # get absent or silent: the row's range
            lo, hi, source = s_lo, s_hi, "search"
        elif terms.get("contractType"):   # the get read the post: no range posted
            source = "get"
        elif state == "none":             # the row says no range was posted
            source = "search"
        else:                             # nobody we could read said
            source = "unread"
        out["hourly_min"], out["hourly_max"] = lo, hi
        out["fixed_budget"] = None
    else:
        out["contract_type"] = "Fixed"
        amt = _num((fixed.get("amount") or {}).get("rawValue")
                   if isinstance(fixed.get("amount"), dict) else fixed.get("amount"))
        source = "get" if amt is not None else None
        if amt is None:
            b = search_row.get("budget")
            amt = _num(b)
            source = ("search" if amt is not None
                      else "none" if b is None or not str(b).strip() else "unread")
        out["fixed_budget"] = amt or None
        out["hourly_min"] = out["hourly_max"] = None
    out["rate_source"] = source           # get | search | none | unread

    out["experience_level"] = (terms.get("experienceLevel")
                               or search_row.get("experience_level") or None)
    # engagement: search's human string ("Less than 30 hrs/week") matches what
    # the scraper stored; get's enum (PART_TIME) does not, so search wins here.
    out["engagement"] = (search_row.get("engagement")
                         or (hourly.get("engagementType") or None))
    out["engagement_duration"] = search_row.get("duration") or None
    out["weekly_budget"] = None          # not exposed by either MCP payload

    # ── competition. EXACT, not the scraper's tier low-bound. See module docs.
    pc = search_row.get("proposal_count")
    out["total_applicants"] = int(pc) if isinstance(pc, int) else None

    # get's post, else search's include_full_details text, else the snippet
    desc = (content.get("description") or search_row.get("description")
            or search_row.get("description_snippet") or "")
    out["post_source"] = ("get" if content.get("description") else
                          "search" if search_row.get("description") else
                          "snippet" if search_row.get("description_snippet") else None)
    # Upwork wraps posting copy in an untrusted marker. Strip the tags, keep the
    # text: it is DATA for the scorer, and the marker is Upwork telling us so.
    out["description"] = re.sub(
        r"</?untrusted_participant_content>", "", desc).strip()

    out["skills"] = [s for s in (search_row.get("skills") or []) if s]
    # ...AND skills_json. The live pipeline scores DB rows (SELECT * FROM
    # scored_jobs), and scoring.job_input reads `skills_json`, not `skills`.
    # Emitting only `skills` scored every MCP job with the DOUBLE-WEIGHTED
    # skill_fit signal blanked — measured 2026-08-24 as a systematic -3/-4
    # residual after the applicant-count variable was isolated. Same failure
    # shape as 2026-07-13 ("stored [\"\",\"\",\"\"] and blanked the
    # double-weighted skill_fit signal"), one seam further down.
    import json as _json
    out["skills_json"] = _json.dumps(out["skills"])

    # ── client block
    out["client_country"] = _country(get_row, search_row)
    spent = client_rec.get("spend_total")
    if spent is None:                     # search rows carry it as text now
        spent = s_client.get("total_spent")
    out["client_total_spent"] = _num(spent) if spent is not None else None
    # client_hires == contracts_total, NOT jobs_with_hires.
    # Caught by scripts/mcp_feed_compare.py on 2026-08-24: for the whale client
    # on job 2091503480754476388 the scraper stored 327 and `jobs_with_hires`
    # is 215, while `contracts_total` is exactly 327. The two count different
    # things — a job can hire several freelancers, so contracts >= jobs — and
    # the scraper's SSR field `client.totalHires` is the contract count. Using
    # the wrong one under-reports every established client by a third, which
    # feeds Client Quality and the $100K/whale signals.
    hires = client_rec.get("contracts_total")
    if hires is None:
        hires = s_client.get("total_hires")
    out["client_hires"] = int(hires) if isinstance(hires, (int, float)) else None
    fb = client_rec.get("feedback_score") or s_client.get("rating")
    out["client_rating"] = round(float(fb), 2) if isinstance(fb, (int, float)) and fb else None
    out["client_payment_verified"] = 1 if str(
        s_client.get("verification_status", "")).upper() == "VERIFIED" else 0

    out["feed_source"] = search_row.get("_feed_source") or "mcp"

    # ── carried, NOT scored (yet). scoring.job_input pins its own key list, so
    # these cannot leak into a score without a deliberate change there.
    stats = ((post.get("activityStat") or {}).get("applicationsBidStats") or {})
    activity = ((post.get("activityStat") or {}).get("jobActivity") or {})
    out["mcp_extra"] = {
        "connects_cost": get_row.get("connects_cost"),
        "connects_balance": get_row.get("connects_balance"),
        "can_apply": get_row.get("can_apply"),
        "avg_rate_bid": _num((stats.get("avgRateBid") or {}).get("rawValue")),
        "min_rate_bid": _num((stats.get("minRateBid") or {}).get("rawValue")),
        "max_rate_bid": _num((stats.get("maxRateBid") or {}).get("rawValue")),
        "invites_sent": activity.get("invitesSent"),
        "total_hired": activity.get("totalHired"),
        "total_interviewed": activity.get("totalInvitedToInterview"),
        "preferred_qualifications": get_row.get("preferred_qualifications"),
        "screening_questions": [
            re.sub(r"</?untrusted_participant_content>", "", q).strip()
            for q in (get_row.get("screening_questions") or [])],
        "client_contracts_active": client_rec.get("contracts_active"),
        "client_hours_total": client_rec.get("hours_total"),
        "search_budget": search_row.get("budget"),
    }
    return out
