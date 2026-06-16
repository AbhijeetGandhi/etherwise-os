"""Proof-points wiki compiler: the agency-level counterpart to the per-client
dossier. Aggregates the facts that DON'T belong in a client dossier —
scope='agency' (general method/positioning/capability) and scope='cross-client'
(another client's project mentioned as an example) — across ALL sources into
knowledge/proof-points.md. Cited, deterministic; this is where the Adelye-style
capability bleed lands instead of polluting the dossier.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from core import config, db
from modules.knowledge import kb


def render_proof_points(agency: List[dict], cross: List[dict],
                        now_iso: str) -> str:
    """Pure render of the two non-client scopes, grouped by category."""
    trusted = [f for f in agency + cross if f["confidence"] in kb.TRUSTED_TAGS]
    lines = [
        "# Etherwise — Proof Points & Capabilities",
        "",
        f"_Compiled {now_iso} · {len(agency)} agency + {len(cross)} "
        f"cross-client facts ({len(trusted)} trusted)._",
        "",
        "> Agency-level knowledge extracted from client calls but NOT specific "
        "to one client. Cited; use CONFIRMED / CROSS-VERIFIED for outreach.",
        "",
    ]
    for title, rows in (("Agency capabilities & method", agency),
                        ("Cross-client proof points", cross)):
        if not rows:
            continue
        lines.append(f"## {title}")
        lines.append("")
        by_cat: dict = {}
        for f in rows:
            by_cat.setdefault(f["category"] or "other", []).append(f)
        for cat in sorted(by_cat):
            lines.append(f"### {cat}")
            seen = set()
            for f in sorted(by_cat[cat], key=lambda x: x["fact_text"]):
                if f["fact_text"] in seen:        # dedup identical claims
                    continue
                seen.add(f["fact_text"])
                tag = (f["confidence"] if f["confidence"] in kb.TRUSTED_TAGS
                       else f"_{f['confidence']}_")
                lines.append(f"- {f['fact_text']} [{tag} · {f['citation']}]")
            lines.append("")
    return "\n".join(lines)


def compile_proof_points(db_path, now_iso: str,
                         out_dir: Optional[Path] = None) -> dict:
    """Gather all agency/cross-client facts and write the proof-points wiki."""
    out_dir = out_dir or config.KNOWLEDGE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    with db.connect(db_path) as conn:
        agency = [dict(r) for r in conn.execute(
            "SELECT category, fact_text, citation, confidence FROM facts"
            " WHERE scope='agency' ORDER BY category, id")]
        cross = [dict(r) for r in conn.execute(
            "SELECT category, fact_text, citation, confidence FROM facts"
            " WHERE scope='cross-client' ORDER BY category, id")]
    md = render_proof_points(agency, cross, now_iso)
    path = out_dir / "proof-points.md"
    path.write_text(md)
    return {"path": str(path), "agency": len(agency), "cross": len(cross)}
