"""Dossier compiler: gather a client's cited facts + source log from the kb and
render a deterministic markdown dossier (knowledge/dossiers/<client>.md), then
update dossier_index. render_dossier is pure (deterministic given inputs) so it
can be unit-tested; compile_dossier does the DB IO + file write.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from core import config, db
from modules.knowledge import kb

# section order + human titles (drives the dossier layout)
_SECTIONS = [
    ("overview", "Overview"),
    ("numbers", "The numbers"),
    ("people", "People"),
    ("architecture", "Architecture & systems"),
    ("stack", "Stack & tools"),
    ("timeline", "Timeline"),
    ("commitment", "Commitments"),
    ("quote", "Notable quotes"),
    ("other", "Other"),
]


def _slug(name: str, client_id: str) -> str:
    base = (client_id or name or "client").lower()
    return "".join(c if c.isalnum() else "-" for c in base).strip("-")


def render_dossier(client_name: str, client_id: str, facts: List[dict],
                   sources: List[dict], now_iso: str, routed: int = 0) -> str:
    """Pure render. facts: rows with category/fact_text/citation/confidence
    (client-scoped only). sources: source_type/source_ref/title/occurred_dt.
    routed: count of agency/cross-client facts sent to proof-points instead."""
    trusted = [f for f in facts if f["confidence"] in kb.TRUSTED_TAGS]
    routed_note = (f" · {routed} agency/other-client facts routed to "
                   "proof-points" if routed else "")
    lines = [
        f"# {client_name} — Dossier",
        "",
        f"_Compiled {now_iso} · {len(facts)} client facts "
        f"({len(trusted)} trusted) from {len(sources)} sources{routed_note}._",
        "",
        "> Every line is cited. Confidence: **CONFIRMED** / **CROSS-VERIFIED**"
        " are client-facing; _INFERRED_ is internal-only.",
        "",
    ]
    by_cat = {}
    for f in facts:
        by_cat.setdefault(f["category"] or "other", []).append(f)

    for cat, title in _SECTIONS:
        rows = by_cat.get(cat)
        if not rows:
            continue
        lines.append(f"## {title}")
        lines.append("")
        for f in sorted(rows, key=lambda x: (x["confidence"] != "CONFIRMED",
                                             x["fact_text"])):
            tag = (f["confidence"] if f["confidence"] in kb.TRUSTED_TAGS
                   else f"_{f['confidence']}_")
            lines.append(f"- {f['fact_text']} "
                         f"[{tag} · {f['citation']}]")
        lines.append("")

    lines.append("## Sources")
    lines.append("")
    for s in sorted(sources, key=lambda x: (x.get("occurred_dt") or "")):
        when = (s.get("occurred_dt") or "")[:10] or "—"
        title = s.get("title") or s.get("source_ref")
        lines.append(f"- `{s['source_ref']}` · {when} · {s['source_type']}"
                     f" · {title}")
    lines.append("")
    return "\n".join(lines)


def compile_dossier(db_path, client_id: str, client_name: str, now_iso: str,
                    dossier_dir: Optional[Path] = None,
                    force_empty: bool = False) -> dict:
    """Query the client's facts + sources, render, write file, update index.

    GUARD (force_empty=False): never clobber a populated dossier with an empty
    render when the READ ITSELF is suspect. The 2026-06-18 02:07 incident was a
    compile run against the wrong/empty db, which emptied real dossiers; the
    signature of that failure is that the whole kb reads as empty, not that one
    client does. So the guard fires when this client renders 0 facts AND the
    facts table is empty (db mid-write, locked, wrong file, migration pending).

    A populated kb in which THIS client has 0 facts is a REAL emptying and is
    written through. That happens whenever a source is re-attributed away from
    a client (jarvis fathom/loom heal, a mapping-queue correction): the facts
    leave, and the losing dossier must stop claiming them. Before 2026-08-13
    the guard swallowed that case and froze the index forever — `optimize-is`
    reported 17 facts for four days after its only Fathom call was unmapped,
    and those 17 were double-counted against the Knowledge cards. A caller who
    wants the old unconditional behaviour still passes force_empty=True."""
    dossier_dir = dossier_dir or (config.KNOWLEDGE_DIR / "dossiers")
    dossier_dir.mkdir(parents=True, exist_ok=True)
    with db.connect(db_path) as conn:
        # client dossier carries ONLY client-scoped facts; agency/cross-client
        # facts are routed to the proof-points wiki (proof_points.py)
        facts = [dict(r) for r in conn.execute(
            "SELECT category, fact_text, citation, confidence FROM facts"
            " WHERE client_id=? AND scope='client' ORDER BY id", (client_id,))]
        routed = conn.execute(
            "SELECT COUNT(*) c FROM facts WHERE client_id=? AND scope!='client'",
            (client_id,)).fetchone()["c"]
        sources = [dict(r) for r in conn.execute(
            "SELECT source_type, source_ref, title, occurred_dt, density FROM"
            " sources WHERE client_id=? ORDER BY occurred_dt", (client_id,))]
        prev = conn.execute("SELECT fact_count FROM dossier_index WHERE"
                            " client_id=?", (client_id,)).fetchone()
        # is the READ trustworthy? An empty facts table is the signature of the
        # failure this guard exists for (wrong db / mid-write / un-migrated).
        kb_populated = bool(conn.execute(
            "SELECT EXISTS(SELECT 1 FROM facts)").fetchone()[0])
    prev_count = prev["fact_count"] if prev else 0
    path = dossier_dir / f"{_slug(client_name, client_id)}.md"

    # anti-clobber: an empty render must never overwrite a dossier that is
    # already populated — either per the index (this db) OR per the existing
    # FILE (guards a compile run against the wrong/transient db, which is how
    # the 2026-06-18 incident clobbered the real dir from a temp-db compile) —
    # UNLESS the kb is demonstrably populated, in which case this client really
    # does have nothing left and saying otherwise is the bug.
    file_populated = path.exists() and "\n- " in path.read_text()
    if (not facts and not force_empty and not kb_populated
            and (prev_count > 0 or file_populated)):
        return {"client_id": client_id, "status": "skipped_empty",
                "facts": 0, "prev_facts": prev_count,
                "reason": "refused to overwrite populated dossier with 0 facts"
                          " — the facts table read as EMPTY, so the db, not the"
                          " client, is what changed"}

    md = render_dossier(client_name, client_id, facts, sources, now_iso,
                        routed=routed)
    path.write_text(md)

    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO dossier_index (client_id, client_name, dossier_path,"
            " fact_count, source_count, last_compiled_at) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(client_id) DO UPDATE SET client_name=excluded.client_name,"
            " dossier_path=excluded.dossier_path, fact_count=excluded.fact_count,"
            " source_count=excluded.source_count,"
            " last_compiled_at=excluded.last_compiled_at",
            (client_id, client_name, str(path), len(facts), len(sources), now_iso))
    return {"client_id": client_id, "path": str(path), "status": "compiled",
            "facts": len(facts), "sources": len(sources),
            "prev_facts": prev_count}
