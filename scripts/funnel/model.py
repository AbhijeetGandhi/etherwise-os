"""Shared derivations for the proposal research: one flat row per proposal with its kind (cold /
invite), what happened (replied, hired), what the job did (client interviewed / hired anyone), and
the text features. Pure: reads var/prop/dataset.json, features.json and raw/job/*.json."""
import glob
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

INVITE = re.compile(r"thank(?:s| you)[^.\n]{0,40}\b(?:invit\w*|reaching out|considering me|shortlist\w*)|\bfor the invit|"
                    r"\bthanks? for (?:the )?(?:offer|referral)", re.I)


ARRANGED = re.compile(r"as per our (?:discussion|conversation|call|chat)|\bas discussed\b|agreed with everything|"
                      r"thank(?:s| you)[^.]{0,25}\boffer\b", re.I)


def clean_letter(t):
    t = (t or "").strip()
    if t.startswith("{coverLetter="):
        t = t[len("{coverLetter="):]
        t = re.sub(r",\s*(?:questions|answers|attachments|projectPlan)=.*$", "", t, flags=re.S)
        t = t.rstrip("}").strip()
    t = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2", t)
    return t


def pdt(s):
    """An instant from any of the shapes on file: ...Z, +0000, +00:00, epoch millis, 'YYYY-MM-DD HH:MM:SS'."""
    if not s:
        return None
    s = str(s).strip()
    try:
        if s.isdigit():
            return datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc)
        s = s.replace("Z", "+00:00")
        s = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", s)
        if " " in s and "T" not in s:
            s = s.replace(" ", "T", 1)
        s = re.sub(r"\.(\d{1,6})\d*", lambda mo: "." + mo.group(1).ljust(6, "0"), s)
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def num(v):
    try:
        return float(str(v).replace("$", "").replace(",", "")) if v not in (None, "") else None
    except ValueError:
        return None


def load():
    data = json.loads(Path("var/prop/dataset.json").read_text())
    feats = {x["id"]: x for x in json.loads(Path("var/prop/features.json").read_text())}
    jobs = {}
    for f in glob.glob("var/prop/raw/job/*.json"):
        try:
            jobs[Path(f).stem] = json.loads(Path(f).read_text())
        except ValueError:
            pass
    rooms = {}
    for f in glob.glob("var/prop/raw/room/*.json"):
        try:
            rooms[Path(f).stem] = json.loads(Path(f).read_text())
        except ValueError:
            pass
    # the proposal's own `get` answer names its room whenever one exists (checked against get_room and
    # against every ledger room, 8 Oct 2026) - the complete "the client opened a conversation" flag
    room_ids = {}
    for f in glob.glob("var/prop/raw/get/*.json"):
        try:
            room_ids[Path(f).stem] = json.loads(Path(f).read_text()).get("room_id")
        except ValueError:
            pass
    rows = []
    for r in data:
        x = feats.get(r["id"]) or {}
        m = r.get("msgs") or {}
        cl = clean_letter(r.get("cover_letter"))
        status = r.get("status") or (r.get("ledger") or {}).get("upwork_status")
        label = r.get("status_label") or (r.get("ledger") or {}).get("upwork_status_label") or ""
        led = r.get("ledger") or {}
        # "Activated" (Upwork's label: "Invitation accepted") is NOT only invitations: it is every proposal
        # in a live interview - an invitation he accepted, AND a cold proposal the client then wrote back to.
        # What tells them apart is the room's birth: an invitation's room is born in the same second as the
        # proposal; a cold proposal's room is born when the client writes (minutes to weeks later).
        rr = rooms.get(r["id"])
        pr = ((rr or {}).get("data") or {}).get("proposalRoom") or {}
        room_ledger = bool(r.get("thread_id"))
        room = bool(room_ids.get(r["id"])) or room_ledger or bool(pr)
        room_created = pr.get("createdAtDateTime") or (m.get("first_dt") if room_ledger else None)
        t0, t1 = pdt(r.get("created")), pdt(room_created)
        lag = (t1 - t0).total_seconds() if (t0 and t1) else None
        invite_text = bool(INVITE.search(cl[:300]))
        born_together = lag is not None and lag <= 120
        client_first = bool(m.get("first_dir") == "inbound" and m.get("first_dt") and r.get("created")
                            and m["first_dt"][:16] <= r["created"][:16])
        invite = invite_text or born_together or client_first
        kind_sure = invite_text or (not room) or (lag is not None)
        room_upwork = bool(pr)
        room_checked = True
        hired = status == "Hired" or led.get("status") == "Won" or bool(r.get("contracts"))
        replied = room or hired or status in ("Offered", "Activated")
        jr = jobs.get(r.get("job_id") or "") or {}
        post = ((jr.get("data") or {}).get("marketplaceJobPosting") or {})
        act = ((post.get("activityStat") or {}).get("jobActivity") or {})
        bids = ((post.get("activityStat") or {}).get("applicationsBidStats") or {})
        wf = post.get("workFlowState") or {}
        crec = jr.get("client_record") or {}
        sj = r.get("job") or {}
        ins = r.get("insights") or {}
        row = {
            "id": r["id"], "created": r.get("created") or "", "month": (r.get("created") or "")[:7],
            "year": (r.get("created") or "")[:4],
            "status": status, "label": label, "reason": r.get("reason"), "lists": r.get("lists"),
            # "arranged": a proposal filed to take work already agreed in chat ("Thank you for the offer.",
            # "as per our discussion", two words) - never a competition, so never counted as a cold proposal
            "kind": "invite" if invite else ("arranged" if (len(cl.split()) <= 15 or ARRANGED.search(cl[:220])) else "cold"),
            "room": room, "room_ledger": room_ledger, "room_upwork": room_upwork, "room_checked": room_checked,
            "room_created": room_created, "reply_lag_h": (round(lag / 3600, 2) if lag is not None else None),
            "kind_sure": bool(kind_sure), "invite_text": invite_text, "born_together": born_together,
            "room_last_side": pr.get("last_message_side"), "room_awaiting": pr.get("awaiting_reply_from"),
            "replied": bool(replied), "hired": bool(hired),
            "n_inbound": m.get("n_inbound"), "first_dir": m.get("first_dir"),
            "boosted": r.get("boosted"), "bid": r.get("connects_bid"), "rate": num(r.get("rate")),
            "title": r.get("job_title") or "", "job_id": r.get("job_id"), "url": r.get("job_url"),
            "letter": cl, "qa": r.get("qa") or [], "got_detail": r.get("got_detail"),
            "ins_total": ins.get("proposals_total"), "ins_opened": ins.get("proposals_opened"),
            "ins_unopened": ins.get("proposals_unopened"), "ins_short": ins.get("shortlisted"),
            "ins_msg": ins.get("messaged"), "ins_ok": bool(ins),
            "job_have": bool(post), "job_state": wf.get("status"), "job_close": wf.get("closeResult"),
            "job_interviewed": act.get("totalInvitedToInterview"), "job_hired": act.get("totalHired"),
            "job_invites": act.get("invitesSent"), "job_offered": act.get("totalOffered"),
            "avg_bid": num((bids.get("avgRateBid") or {}).get("rawValue")),
            "client_hire_rate": crec.get("hire_rate_percent"), "client_spend": num(crec.get("spend_total")),
            "client_jobs": crec.get("jobs_posted"), "client_contracts": crec.get("contracts_total"),
            "connects_cost": jr.get("connects_cost"),
            "sj_score": sj.get("score"), "sj_hmin": sj.get("hourly_min"), "sj_hmax": sj.get("hourly_max"),
            "sj_fixed": sj.get("fixed_budget"), "sj_applicants": sj.get("total_applicants"),
            "sj_spent": sj.get("client_total_spent"), "sj_type": sj.get("contract_type"),
        }
        ct = post.get("contractTerms") or {}
        hct = ct.get("hourlyContractTerms") or {}
        cls = post.get("classification") or {}
        desc = re.sub(r"</?untrusted_participant_content>", "", ((post.get("content") or {}).get("description") or "")).strip()
        row.update({
            "j_type": ct.get("contractType") or (sj.get("contract_type") or None),
            "j_exp": ct.get("experienceLevel"),
            "j_hmin": hct.get("hourlyBudgetMin") if hct.get("hourlyBudgetMin") is not None else sj.get("hourly_min"),
            "j_hmax": hct.get("hourlyBudgetMax") if hct.get("hourlyBudgetMax") is not None else sj.get("hourly_max"),
            "j_fixed": num(((ct.get("fixedPriceContractTerms") or {}).get("amount") or {}).get("rawValue")) or sj.get("fixed_budget"),
            "j_engagement": hct.get("engagementType"),
            "j_persons": ct.get("personsToHire"),
            "j_cat": ((cls.get("category") or {}).get("preferredLabel")),
            "j_subcat": ((cls.get("subCategory") or {}).get("preferredLabel")),
            "j_country": (((post.get("clientCompanyPublic") or {}).get("country") or {}).get("name")) or sj.get("client_country"),
            "j_desc": desc or (sj.get("description") or ""),
            "j_min_bid": num((bids.get("minRateBid") or {}).get("rawValue")),
            "j_max_bid": num((bids.get("maxRateBid") or {}).get("rawValue")),
            "client_feedback": crec.get("feedback_score"), "client_jobs_hired": crec.get("jobs_with_hires"),
            "client_open_jobs": crec.get("open_jobs"),
            "client_hours": crec.get("hours_total"), "client_feedback_n": crec.get("feedback_count"),
            "j_questions": [re.sub(r"</?untrusted_participant_content>", "", q or "").strip() for q in (jr.get("screening_questions") or [])],
            "j_unanswered_invites": act.get("totalUnansweredInvites"),
            "j_skills": sj.get("skills_json"),
            "sj_created": sj.get("created_dt"),
        })
        for k in ("words", "greeting", "first_sentence", "open_self", "open_client", "open_number", "em_dashes",
                  "contractions", "numbers", "urls", "loom", "questions", "list_lines", "rate_mention",
                  "signoff_best", "filler", "i_you_ratio", "call_cta", "anchors", "boilerplate_share",
                  "nearest_jaccard", "echo_share", "echo_head", "title_echo_head", "draft_jaccard",
                  "hours_to_apply", "qa_n", "qa_words", "paragraphs", "first_words", "opener_family"):
            row[k] = x.get(k)
        rows.append(row)
    _specificity(rows)
    _boosts(rows)
    return rows


STOP = set("""a an the and or but if then so of to in on for with at by from as is are was were be been being it its this that
these those i you he she we they me my your our their them us do does did have has had will would can could should may might must
not no yes about into over under up down out off than too very just also more most much many any some each every other such own
same both few all how what when where which who whom why there here am im ive youre hi hey hello thanks thank best regards
get got make made need needs needed want wants like looking look work works working job project help use using used one two three
new via per etc within across between through while during after before again only really please able well good great experience
experienced expert someone who's whose including include includes required requirements skills skill strong ability must should
build building built create creating set setup existing current currently based time team company business role position
hire hiring freelancer contractor candidate ideal apply applying proposal upwork hours hour week weeks month months long term
ongoing start asap soon budget rate fixed hourly""".split())
_W = re.compile(r"[A-Za-z][A-Za-z0-9+.#'’\-]*")


def _cw(t):
    out = []
    for w in _W.findall(t or ""):
        w = w.lower().strip("'’-.")
        if len(w) > 2 and w not in STOP:
            out.append(w)
    return out


def opening(letter, chars=260):
    """The letter as the client's list shows it: after the greeting and any code word, first `chars`."""
    t = re.sub(r"\s+", " ", letter or "").strip()
    t = re.sub(r"^(?:[A-Z][A-Za-z ]{0,24}|[^\w\s]{1,3})[.!]?\s+(?=(?:[Hh]i|[Hh]ey|[Hh]ello)\b)", "", t)
    t = re.sub(r"^(?:hi|hey|hello|dear)\b(?:\s+(?:there|team|all|[A-Z][a-z]+)(?=\s*[,.!\]]))?\s*[,.!\]]?\s*", "", t, flags=re.I)
    return t[:chars]


def _specificity(rows):
    """How much of the opening is made of words that are particular to THIS job post: words of the post
    (title + description) that fewer than 4% of all job posts on file contain. A tool name every job
    shares (Airtable, Make) does not count; the client's own nouns do."""
    docs = {}
    for r in rows:
        if r.get("j_desc") and r.get("job_id") and r["job_id"] not in docs:
            docs[r["job_id"]] = set(_cw((r.get("title") or "") + " " + r["j_desc"]))
    n = len(docs) or 1
    df = {}
    for s in docs.values():
        for w in s:
            df[w] = df.get(w, 0) + 1
    cut = max(3, int(0.04 * n))
    for r in rows:
        s = docs.get(r.get("job_id"))
        if not s:
            r.update({"spec_head": None, "spec_head_words": None, "spec_all": None, "echo_head2": None, "j_words": None})
            continue
        head = _cw(opening(r["letter"]))
        allw = _cw(r["letter"])
        rare = {w for w in s if df.get(w, 0) <= cut}
        hits = sorted({w for w in head if w in rare})
        r["spec_head"] = len(hits)
        r["spec_head_words"] = hits[:12]
        r["spec_all"] = round(100.0 * len({w for w in allw if w in rare}) / max(1, len(allw)), 2)
        r["echo_head2"] = round(sum(1 for w in head if w in s) / len(head), 3) if head else None
        r["j_words"] = len((r.get("j_desc") or "").split())


def _boosts(rows):
    """What the Connects billing says about each boost since July 2026 (var/prop/boost.json, built natively)."""
    f = Path("var/prop/boost.json")
    by = {}
    if f.exists():
        for b in json.loads(f.read_text()):
            if b.get("proposal_id"):
                by[b["proposal_id"]] = b
    for r in rows:
        b = by.get(r["id"]) or {}
        r["boost_outcome"] = b.get("outcome")
        r["boost_touch"] = b.get("touch")
        r["boost_bid"] = b.get("bid")
        r["posted"] = b.get("posted")


def wilson(k, n, z=1.96):
    if not n:
        return (None, None, None)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (p, max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def rate(k, n):
    p, lo, hi = wilson(k, n)
    return f"{k}/{n}" + (f" = {p:.0%} ({lo:.0%}-{hi:.0%})" if n else "")


def fisher(a, b, c, d):
    """Two-sided Fisher exact p for [[a,b],[c,d]]."""
    n1, n2, k = a + b, c + d, a + c
    tot = n1 + n2

    def pr(x):
        return math.comb(n1, x) * math.comb(n2, k - x) / math.comb(tot, k)
    p0 = pr(a)
    return min(1.0, sum(pr(x) for x in range(max(0, k - n2), min(k, n1) + 1) if pr(x) <= p0 * (1 + 1e-9)))
