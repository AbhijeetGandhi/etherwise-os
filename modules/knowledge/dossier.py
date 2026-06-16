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
                   sources: List[dict], now_iso: str) -> str:
    """Pure render. facts: rows with category/fact_text/citation/confidence.
    sources: rows with source_type/source_ref/title/occurred_dt/density."""
    trusted = [f for f in facts if f["confidence"] in kb.TRUSTED_TAGS]
    lines = [
        f"# {client_name} — Dossier",
        "",
        f"_Compiled {now_iso} · {len(facts)} facts "
        f"({len(trusted)} trusted) from {len(sources)} sources._",
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
                    dossier_dir: Optional[Path] = None) -> dict:
    """Query the client's facts + sources, render, write file, update index."""
    dossier_dir = dossier_dir or (config.KNOWLEDGE_DIR / "dossiers")
    dossier_dir.mkdir(parents=True, exist_ok=True)
    with db.connect(db_path) as conn:
        facts = [dict(r) for r in conn.execute(
            "SELECT category, fact_text, citation, confidence FROM facts"
            " WHERE client_id=? ORDER BY id", (client_id,))]
        sources = [dict(r) for r in conn.execute(
            "SELECT source_type, source_ref, title, occurred_dt, density FROM"
            " sources WHERE client_id=? ORDER BY occurred_dt", (client_id,))]

    md = render_dossier(client_name, client_id, facts, sources, now_iso)
    path = dossier_dir / f"{_slug(client_name, client_id)}.md"
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
    return {"client_id": client_id, "path": str(path),
            "facts": len(facts), "sources": len(sources)}
