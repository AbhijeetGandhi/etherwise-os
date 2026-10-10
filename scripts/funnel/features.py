"""Text and context features for every proposal in var/prop/dataset.json -> var/prop/features.json.
Pure functions of the saved data: no network, no ledger. Stdlib only (the Mac's Python 3.9)."""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("var/prop/dataset.json")
OUT = Path("var/prop/features.json")

CONTRACTION = re.compile(r"\b\w+(?:n['’]t|['’]ve|['’]m|['’]re|['’]ll|['’]d)\b|\b(?:it|that|there|here|what|let|he|she|who)['’]s\b", re.I)
URL = re.compile(r"https?://\S+|\b[\w-]+\.(?:com|io|co|ai|app|dev)\b(?:/\S*)?", re.I)
NUM = re.compile(r"(?<![\w$])\$?\d[\d,]*(?:\.\d+)?\+?%?(?:k|K)?")
WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
INVITE = re.compile(r"thank(?:s| you)[^.\n]{0,40}\b(?:invit\w*|reaching out|considering me|shortlist\w*)|\bfor the invit", re.I)
GREETING = re.compile(r"^\s*(?:[A-Z][A-Za-z ]{0,24}[.!]?\s*\n\s*|[^\w\s]{1,3}\s*\n\s*)?(hi|hey|hello|dear|greetings|good (?:morning|afternoon|evening))\b"
                      r"(?:[ \t]+(?:there|team|all|[A-Z][a-z]+)(?=[ \t]*[,.!\]\n]))?[ \t]*[,.!\]]?\s*", re.I)
STOP = set("""a an the and or but if then so of to in on for with at by from as is are was were be been being it its this that
these those i you he she we they me my your our their them us do does did have has had will would can could should may might must
not no yes about into over under up down out off than too very just also more most much many any some each every other such own
same both few all how what when where which who whom why there here am im ive youre hi hey hello thanks thank best regards
get got make made need needs needed want wants like looking look work works working job project help use using used one two three
new via per etc within across between through while during after before again only really""".split())
ANCHORS = {
    "hermon": r"\bhermon\b|49\+? (?:airtable )?bases|160,?000|20,?000\+? contracts",
    "horizons": r"\bhorizons?\b|19\+? months|197\+?|300\+ make",
    "optimize": r"\boptimize\b",
    "etherwise": r"\betherwise\b",
    "top_rated": r"top rated|100% job success|100% jss",
    "make_3000": r"3,?000\+?",
    "vapi_130": r"130\+|200\+ (?:ai )?voice",
    "ml_background": r"machine learning engineer|ml engineer",
    "claude_code": r"claude code",
}
FILLER = re.compile(r"genuinely excited|i would love to|thrilled to|passionate about|excited about|great fit|perfect fit|"
                    r"caught my (?:eye|attention)|stood out to me|i am confident|look forward to|looking forward", re.I)
SELF_OPEN = re.compile(r"^(?:i|i['’]m|i['’]ve|my|we|our)\b", re.I)
CLIENT_OPEN = re.compile(r"^(?:you|your|you['’]re)\b", re.I)


OPEN_RULES = [  # first match wins, on the first ~240 characters after the greeting and any code word
    ("invite thanks", r"thank(?:s| you)[^.]{0,50}\b(?:invit|reaching out|offer|referral|opportunity to apply)"),
    ("loom first", r"\brecorded (?:a|an|this)\b[^.]{0,40}\bloom\b|^[^.]{0,80}\bloom\b"),
    ("self intro", r"^i(?:['’]m| am) abhijeet|^my name is|^this is abhijeet"),
    ("their problem / insight first", r"^(?:the )?(?:real|hard|hardest|tricky|trickiest|core|biggest|interesting|key|main|most important)\b[^.]{0,40}\b(?:part|challenge|risk|lever|piece|thing|decision|issue|problem|layer)\b"
        r"|\b(?:isn['’]?t|is not|is rarely|usually is not)\b[^.]{0,90}\bit['’]?s\b|^the (?:part|line|fact|pattern|shape|framing|distinction|relational|stack)\b"
        r"|^one thing worth|^your system does not|^replacing |^managing |^maintaining |^connecting |^setting up |^cleaning up |^automating |^building |^streamlining "
        r"|\btend to hit\b|\bhave specific\b|^zero automation|^i would not touch|^i don['’]t see|usual culprits|^your (?:workflow|operations|team|setup|stack)\b"),
    ("fit claim / excitement", r"\bexactly\b|\bstrong (?:match|fit)\b|\bgreat fit\b|\bdirect match\b|\bwheelhouse\b|caught my (?:eye|attention)|\bexcit|i(?:['’]d| would) love"
        r"|saw your (?:job )?post|came across your|read (?:through )?your post|your (?:job )?post\b|\bthis (?:role|one|project|looks|sounds|is a)\b|very (?:close|aligned|familiar)|closely aligned|identical to"),
    ("self / credentials first", r"^(?:i|we|my)\b|^i['’]|^over the (?:last|past)|^most of my|^for [A-Z][\w.]+,? i\b|^for (?:a|an|one|my) [^.]{3,90}\bi (?:have|built|designed)\b|^\d"
        r"|^one automation i am|^with (?:over|more than)"),
]


def opener_family(after_greeting):
    """What the first lines DO. Works on the letter after "Hi," and after a leading code word the client
    asked for (one short line, e.g. "PURPLE", or an emoji)."""
    t = re.sub(r"\s+", " ", after_greeting or "").strip()
    t = re.sub(r"^(?:[A-Z][A-Za-z ]{0,24}|[^\w\s]{1,3})[.!]?\s+(?=(?:[Hh]i|[Hh]ey|[Hh]ello)\b)", "", t)   # "PURPLE Hi, ..."
    t = re.sub(r"^(?:hi|hey|hello|dear)\b(?:\s+(?:there|team|all|[A-Z][a-z]+)(?=\s*[,.!\]]))?\s*[,.!\]]?\s*", "", t, flags=re.I)
    head = t[:240]
    for name, pat in OPEN_RULES:
        if re.search(pat, head, re.I):
            return name
    return "other"


def words(t):
    return WORD.findall(t or "")


def content_words(t):
    return [w.lower().strip("'’-") for w in words(t) if len(w) > 2 and w.lower() not in STOP]


def sentences(t):
    t = re.sub(r"\s+", " ", t or "").strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", t)
    return [p.strip() for p in parts if p.strip()]


def shingles(t, n=5):
    w = [x.lower() for x in words(t)]
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def parse_dt(s):
    if not s:
        return None
    s = str(s).strip()
    try:
        if s.isdigit():
            return datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc)
        s = s.replace("Z", "+00:00")
        s = re.sub(r"\+0000$", "+00:00", s)
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def text_features(cl):
    cl = cl or ""
    body = cl.strip()
    g = GREETING.match(body)
    greeting = (g.group(1).lower() if g else None)
    after = body[g.end():].lstrip() if g else body
    sents = sentences(after)
    first = sents[0] if sents else ""
    w = words(body)
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    f = {
        "words": len(w), "chars": len(body), "paragraphs": len(paras), "sentences": len(sentences(body)),
        "greeting": greeting,
        "opener_family": opener_family(body) if body else None,
        "first_sentence": first[:260],
        "first_words": len(words(first)),
        "open_self": bool(SELF_OPEN.match(first)), "open_client": bool(CLIENT_OPEN.match(first)),
        "open_number": bool(re.match(r"^\$?\d", first)),
        "invite_phrase": bool(INVITE.search(body[:260])),
        "em_dashes": body.count("—") + body.count(" – "),
        "contractions": len(CONTRACTION.findall(body)),
        "numbers": len([m for m in NUM.findall(body) if re.search(r"\d", m)]),
        "urls": len(URL.findall(body)), "loom": bool(re.search(r"loom\.com|\bloom\b", body, re.I)),
        "questions": body.count("?"),
        "list_lines": len(re.findall(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", body)),
        "rate_mention": bool(re.search(r"\$\s?\d+\s?(?:/|per)\s?(?:hr|hour)|rate:", body, re.I)),
        "signoff_best": bool(re.search(r"\bbest,?\s*\n?\s*abhijeet", body, re.I)),
        "filler": len(FILLER.findall(body)),
        "i_count": len(re.findall(r"\bI\b", body)), "you_count": len(re.findall(r"\byou(?:r|rs)?\b", body, re.I)),
        "call_cta": bool(re.search(r"\bcall\b|\bchat\b|\bmeet\b|walk (?:you )?through", body, re.I)),
        "anchors": sorted(k for k, pat in ANCHORS.items() if re.search(pat, body, re.I)),
    }
    f["i_you_ratio"] = round(f["i_count"] / max(1, f["you_count"]), 2)
    return f


def main():
    data = json.loads(DATA.read_text())
    sh = {r["id"]: shingles(r.get("cover_letter") or "") for r in data}
    # how many OTHER letters share each 5-word shingle -> boilerplate share
    freq = {}
    for s in sh.values():
        for x in s:
            freq[x] = freq.get(x, 0) + 1
    out = []
    for r in data:
        cl = r.get("cover_letter") or ""
        f = text_features(cl)
        s = sh[r["id"]]
        f["boilerplate_share"] = round(sum(1 for x in s if freq[x] >= 4) / len(s), 3) if s else None
        # nearest other letter
        best, best_id = 0.0, None
        if s:
            for oid, o in sh.items():
                if oid == r["id"] or not o:
                    continue
                inter = len(s & o)
                if inter:
                    j = inter / len(s | o)
                    if j > best:
                        best, best_id = j, oid
        f["nearest_jaccard"] = round(best, 3)
        f["nearest_id"] = best_id
        job = r.get("job") or {}
        jtext = " ".join(x for x in (r.get("job_title") or "", job.get("description") or "") if x)
        jw = set(content_words(jtext))
        cw = content_words(cl)
        f["job_text_words"] = len(words(jtext))
        f["echo_share"] = round(sum(1 for x in cw if x in jw) / len(cw), 3) if cw and jw else None
        head = content_words(cl[:350])
        f["echo_head"] = round(sum(1 for x in head if x in jw) / len(head), 3) if head and jw else None
        tw = set(content_words(r.get("job_title") or ""))
        f["title_echo_head"] = sorted(set(head) & tw)
        draft = job.get("draft_proposal") or ""
        if draft and s:
            d = shingles(draft)
            f["draft_jaccard"] = round(len(s & d) / len(s | d), 3) if d else None
            f["draft_words"] = len(words(draft))
        else:
            f["draft_jaccard"], f["draft_words"] = None, (len(words(draft)) if draft else None)
        created, posted = parse_dt(r.get("created")), parse_dt(job.get("created_dt"))
        f["hours_to_apply"] = round((created - posted).total_seconds() / 3600, 2) if created and posted else None
        f["qa_n"] = len(r.get("qa") or [])
        f["qa_words"] = sum(len(words(x.get("a") or "")) for x in (r.get("qa") or []))
        out.append({"id": r["id"], **f})
    OUT.write_text(json.dumps(out))
    print("features:", len(out), "| with text:", sum(1 for x in out if x["words"] > 0))


if __name__ == "__main__":
    main()
