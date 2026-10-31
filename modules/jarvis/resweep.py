"""Make the sender rules RETROACTIVE — the defect this module exists to close.

Abhijeet, 2026-08-19, looking at the Review tab: *"there are ton of emails that
are like marketing ones and should immediately be marked as ignore -- why are
those in my review tab?"*

He was right, and the answer was not that the rules were missing. The rules were
already written. `assets/sender-rules.json` holds 27 `bulk_subdomains`, and its
own note says they were "Added 2026-08-14 from the 58 rows sitting in `held`" --
the rules were DERIVED FROM THESE VERY EMAILS and then never applied to them,
because the only consumer of that file was the manual `bin/jarvis-triage-queue`
and nobody ran it again. `information@mailers.hdfcbank.bank.in` sat on the board
next to a rule naming `mailers.hdfcbank.bank.in` as the shape of bulk mail.

A rule that only ever runs forwards is half a rule. So:

  * `plan()` applies the CURRENT gate -- self-noise, transactional protection,
    the ingest noise list, then sender_rules -- to every row still `queued` or
    `held`. Same order the rail uses at ingest, so a row cannot get one answer
    on arrival and a different one a week later.
  * `run_if_rules_changed()` fingerprints every input that can change a verdict
    (both JSON assets, the code-side tuples in config/mail_pipeline, and the
    registry sha) and does nothing while that fingerprint is unchanged. Cheap
    enough to sit at the top of `jarvis_sweep`, which is where it sits -- so
    "the rules changed" and "the backlog was re-examined" are the same event and
    cannot come apart again. Editing the JSON is now sufficient; nothing has to
    be remembered.

THREE THINGS IT WILL NOT DO
---------------------------
1. It never re-opens a decision Abhijeet made. `events.is_human_decision()` is
   checked first on every row. A rule that overrules him has decided its
   evidence outranks his judgement, which is backwards.
2. It never auto-excludes a row that already carries `proposed_client_id` --
   a proposal is a decision awaiting confirmation, and the RECO x Etherwise
   invite (proposed reco-health, arriving via read.ai's bulk subdomain) is the
   standing proof of why.
3. It never writes `state='mapped'`. Attribution feeds extract, facts and
   dossiers; an exclusion feeds nothing and reverses with one UPDATE. So the
   strongest thing this is allowed to say about a client is a PROPOSAL, which
   is one click for him and zero client-facing writes if it is wrong.

Every write records the deciding phrase in `proposal_evidence`, so a wrong call
is found by reading rather than by re-running anything.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Optional

from core import config
from modules.jarvis import events, leads, mail_pipeline, mapping
from modules.jarvis import sender_rules as SR

FINGERPRINT_KEY = "jarvis_rules_fingerprint"
STATES = ("queued", "held")

# The proposer's confidence scale is 0-1. A transactional mention is
# deterministic (an exact registry alias inside the document's own subject), so
# it is high -- but it is still only "this name appears", which is why it lands
# as a proposal and not a map.
MENTION_CONFIDENCE = 0.9

_FROM_LINE = re.compile(r"^From:\s*(.+)$", re.MULTILINE)


# ── fingerprint ──────────────────────────────────────────────────────────────
def rules_fingerprint(*, rules: Optional[dict] = None,
                      learned: Optional[dict] = None,
                      reg=None) -> str:
    """A hash over EVERY input that can change a verdict.

    Not just `sender-rules.json`. `[etherwise ops]` went into
    `config.SELF_SUBJECT_PATTERNS` and `docusign.net` lives in
    `mail_pipeline._NOISE_SENDER_DOMAINS`; a fingerprint that only watched the
    JSON would have let exactly this bug recur through the other half of the
    gate. The registry sha is in here too -- a newly registered client is a
    reason to re-read the backlog, and the re-read costs no model call.
    """
    rules = SR.load_rules() if rules is None else rules
    learned = SR.load_learned() if learned is None else learned
    payload = {
        "v": 1,
        "rules": rules,
        "learned": learned,
        "self_senders": list(config.SELF_SENDERS),
        "self_sender_domains": list(config.SELF_SENDER_DOMAINS),
        "self_subject_patterns": list(config.SELF_SUBJECT_PATTERNS),
        "owner_identities": list(config.OWNER_IDENTITIES),
        "noise_sender_substr": list(mail_pipeline._NOISE_SENDER_SUBSTR),
        "noise_sender_domains": list(mail_pipeline._NOISE_SENDER_DOMAINS),
        "noise_subject_substr": list(mail_pipeline._NOISE_SUBJECT_SUBSTR),
        # the structural gate (2026-10-18) is a verdict input like any other
        "structural_shapes": list(mapping.STRUCTURAL_PATTERNS),
        "registry_sha": getattr(reg, "sha", None),
    }
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def read_fingerprint(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT value FROM sync_cursors WHERE name=?",
                       (FINGERPRINT_KEY,)).fetchone()
    return row["value"] if row else None


def write_fingerprint(conn: sqlite3.Connection, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_cursors (name, value, updated_at) VALUES"
        " (?,?,datetime('now')) ON CONFLICT(name) DO UPDATE SET"
        " value=excluded.value, updated_at=datetime('now')",
        (FINGERPRINT_KEY, value))


# ── one row's current verdict ────────────────────────────────────────────────
def from_header(row: dict) -> str:
    """The raw `From:` line if the stored body kept one, else the bare address.

    Worth the regex: the display name is often the only thing that identifies a
    client writing from a personal address ("Michael Gandia" -> cmf-publishing),
    and `self_noise_reason` matches the bot display name against the raw header,
    not the address.
    """
    m = _FROM_LINE.search(row.get("text") or "")
    return (m.group(1).strip() if m else (row.get("author") or "")).strip()


def display_name(row: dict) -> Optional[str]:
    m = re.search(r"^\s*([^<\n]{2,60})<", from_header(row))
    return m.group(1).strip() if m else None


def decide(row: dict, *, reg=None, rules: Optional[dict] = None,
           learned: Optional[dict] = None) -> dict:
    """{action, rule, reason, evidence, client_id} for one queued/held row.

    action is one of:
      skip     -- out of this module's authority (his decision, or a proposal)
      exclude  -- the current rules say noise, and say which phrase decided it
      propose  -- a client is named; queue it pre-attributed for one-click confirm
      keep     -- a genuine question; it stays exactly where it is
    """
    if events.is_human_decision(row.get("resolved_by")):
        return {"action": "skip", "rule": "human-decision",
                "reason": f"resolved by {row.get('resolved_by')} — a rule never"
                          f" re-opens Abhijeet's own call"}

    author = (row.get("author") or "").strip()
    if not author:
        # loom/fathom rows: participants live in the body, not the author slot.
        # The content classifiers own those; guessing here would be a second
        # opinion on someone else's data.
        return {"action": "skip", "rule": "no-sender",
                "reason": "no sender — the content classifiers own this row"}

    frm, subject = from_header(row), row.get("subject") or ""
    proposed = row.get("proposed_client_id")

    # 1. our own automation output — ours whatever shape it wears
    self_reason = mail_pipeline.self_noise_reason(frm, author, subject)
    if self_reason:
        phrase = self_reason.split(":", 2)[-1]
        return {"action": "exclude", "rule": self_reason, "reason": self_reason,
                "evidence": f"self-noise: matched '{phrase}' — Etherwise's own"
                            f" automation output, never a client message"}

    # 2. transactional protection, BEFORE anything that reads address shape
    prot = SR.transactional_reason(author, rules)

    # 2a. THE STRUCTURAL GATE, retroactively (2026-10-18). sweep.py's two
    # deterministic paths now file known-inert message shapes instead of
    # queueing them; the board already held ~50 rows they would have caught —
    # 42 Upwork "X sent you a message" notifications, calendar RSVPs, and the
    # same shapes echoed off client domains. Same rule, same reason string,
    # applied backwards: that is this module's whole job, and it is why the
    # cleanup is a re-sweep rather than a hand-written UPDATE nobody can audit.
    # Protection is honoured first, exactly as at the door.
    if not prot:
        dest, structural = mapping.route_structural(author, subject)
        if dest == "lead":
            # a website-form lead (2026-11-01): its own table, the event filed
            # under the lead id. Never a mapping question.
            return {"action": "lead", "rule": structural, "reason": "lead",
                    "evidence": "a website-form inquiry — a lead, not a client"
                                " to map"}
        if dest == "file":
            rule = (f"{structural}:{proposed}" if proposed else structural)
            return {"action": "exclude", "rule": rule, "reason": structural,
                    "client_id": proposed,
                    "evidence": "a notification that another message exists —"
                                " the thread rail already holds the message"
                                " itself, so there is nothing here to decide"}

    # 2b. …and OPERATIONAL protection, at the same point and for the same
    # reason. This has to be here as well as in mail_pipeline.classify: the
    # door and the retroactive path must give one answer, and step 3 below
    # calls noise_reason() DIRECTLY, so protection added only at the door would
    # be silently absent from every re-examination. The platform's own campaign
    # mail is still excluded, by the identical per-message rules.
    if not prot:
        op = SR.operational_verdict(author, subject, rules)
        alert = SR.ops_alert(author, subject, rules) if op else None
        if alert and not proposed:
            # off the mapping queue, onto the incident feed. Not a deletion:
            # cockpit.data.system reads these back by exclude_reason and groups
            # them by scenario. See sender_rules.ops_alert.
            return {"action": "exclude", "rule": f"{SR.OPS_ALERT}:{alert['domain']}",
                    "reason": alert["reason"],
                    "evidence": f"operational alert from {alert['domain']}"
                                f" about '{alert['scenario']}' — an incident,"
                                f" not an attribution question"}
        if op and op["verdict"] == SR.EXCLUDE and not proposed:
            return {"action": "exclude", "rule": op["rule"],
                    "reason": op["reason"],
                    "evidence": f"{op['rule']}: this platform's own campaign"
                                f" mail — the account is protected, this"
                                f" message is not"}
        if op:
            prot = op["reason"]

    # 3. the ingest noise gate (the other half of the rules — see fingerprint)
    if not prot:
        gate = mail_pipeline.noise_reason(author, subject)
        if gate and not proposed:
            phrase = gate.split(":", 2)[-1]
            return {"action": "exclude", "rule": f"ingest-gate:{gate}",
                    "reason": gate,
                    "evidence": f"ingest noise gate: matched '{phrase}' in the"
                                f" sender or subject"}

    # 4. the sender rules themselves
    v = SR.classify(author, reg=reg, rules=rules, learned=learned,
                    display_name=display_name(row), subject=subject,
                    text=row.get("text"))
    rule, reason, verdict = v["rule"], v["reason"], v["verdict"]

    if verdict == SR.EXCLUDE:
        # a proposal is a decision awaiting confirmation, never noise — with
        # ONE exception (2026-11-01): a NAMED category in sender-rules.json is a
        # human-curated list, not a shape rule, and it outranks a machine
        # proposal. Hero Marketing's cold sequence sat on the board five times
        # because the proposer had guessed "hero-marketing-agency" at 0.9 for a
        # vendor selling to us; a structural rule (a bulk subdomain) still may
        # not — the RECO x Etherwise invite is the standing proof of why.
        if proposed and not rule.startswith("category:"):
            return {"action": "skip", "rule": "has-proposal",
                    "reason": f"already proposed {proposed}; a rule may not"
                              f" overrule a pending decision"}
        return {"action": "exclude", "rule": rule, "reason": reason,
                "evidence": f"rule {rule}: {reason} (sender"
                            f" {SR.domain_of(author)})"}

    if verdict == SR.ASK:
        return {"action": "keep", "rule": rule, "reason": reason}

    # a client id. Propose only — never map (see the module docstring).
    if proposed == verdict:
        return {"action": "skip", "rule": rule,
                "reason": f"already proposed {verdict}"}
    return {"action": "propose", "rule": rule, "reason": reason,
            "client_id": verdict,
            "evidence": f"rule {rule}: {reason}"}


# ── plan + apply ─────────────────────────────────────────────────────────────
def rows(conn: sqlite3.Connection, states=STATES) -> list:
    qs = ",".join("?" * len(states))
    return [dict(r) for r in conn.execute(
        "SELECT id, source, state, author, subject, text, proposed_client_id,"
        f" resolved_by FROM events WHERE state IN ({qs}) ORDER BY ts",
        tuple(states))]


def plan(conn: sqlite3.Connection, *, reg=None, rules: Optional[dict] = None,
         learned: Optional[dict] = None, states=STATES) -> list:
    """[(row, decision)] for every row in `states`. Reads only."""
    rules = SR.load_rules() if rules is None else rules
    learned = SR.load_learned() if learned is None else learned
    return [(r, decide(r, reg=reg, rules=rules, learned=learned))
            for r in rows(conn, states)]


def apply(conn: sqlite3.Connection, decisions: list) -> dict:
    """Write the plan. Excludes and proposals only; never a map."""
    excluded = proposed = 0
    by_rule: dict = {}
    leads_made = 0
    for r, d in decisions:
        if d["action"] == "lead":
            full = conn.execute("SELECT * FROM events WHERE id=?", (r["id"],)).fetchone()
            if full:
                leads.from_event(conn, dict(full), note="website form",
                                 by=f"sender_rules:{d['rule']}")
                leads_made += 1
        elif d["action"] == "exclude":
            events.ignore(conn, r["id"], d["reason"],
                          by=f"sender_rules:{d['rule']}",
                          evidence=d.get("evidence"))
            excluded += 1
        elif d["action"] == "propose":
            events.set_queued(conn, r["id"], d["client_id"],
                              MENTION_CONFIDENCE, d.get("evidence"))
            proposed += 1
        else:
            continue
        by_rule[d["rule"]] = by_rule.get(d["rule"], 0) + 1
    return {"excluded": excluded, "proposed": proposed, "leads": leads_made,
            "by_rule": by_rule}


def run(conn: sqlite3.Connection, *, reg=None, rules: Optional[dict] = None,
        learned: Optional[dict] = None, states=STATES,
        dry_run: bool = False) -> dict:
    decisions = plan(conn, reg=reg, rules=rules, learned=learned, states=states)
    counts = {"examined": len(decisions)}
    for _r, d in decisions:
        counts[d["action"]] = counts.get(d["action"], 0) + 1
    if dry_run:
        counts["applied"] = False
        return {**counts, "decisions": decisions}
    written = apply(conn, decisions)
    return {**counts, **written, "applied": True, "decisions": decisions}


def run_if_rules_changed(conn: sqlite3.Connection, *, reg=None,
                         force: bool = False, log=None) -> dict:
    """The automatic path. No-op — one hash compare — while nothing changed.

    Called at the top of `jarvis_sweep`, so a rule edit is re-applied on the
    next sweep whether or not any new mail arrived. That coupling is the whole
    fix: there is no longer a step anyone can forget.
    """
    rules, learned = SR.load_rules(), SR.load_learned()
    fp = rules_fingerprint(rules=rules, learned=learned, reg=reg)
    seen = read_fingerprint(conn)
    if seen == fp and not force:
        return {"ran": False, "changed": False, "fingerprint": fp[:12],
                "reason": "rules unchanged since the last re-sweep"}

    out = run(conn, reg=reg, rules=rules, learned=learned)
    out.pop("decisions", None)
    write_fingerprint(conn, fp)
    changed = bool(out.get("excluded", 0) or out.get("proposed", 0))
    out.update({"ran": True, "changed": changed, "fingerprint": fp[:12],
                "first_run": seen is None})
    if log:
        log(f"resweep: rules fingerprint {(seen or 'none')[:12]} -> {fp[:12]};"
            f" examined {out['examined']}, excluded {out.get('excluded', 0)},"
            f" proposed {out.get('proposed', 0)},"
            f" kept {out.get('keep', 0)}, skipped {out.get('skip', 0)}")
    return out
