#!/usr/bin/env python3
"""
render_email.py — Render an HTML email template by substituting placeholders.

Usage:
    python3 render_email.py <template-path> <data-json-path>

Reads template HTML and JSON data, substitutes:
  - Simple placeholders: {{KEY}} → value
  - Conditional blocks: <!-- IF:NAME -->...<!-- END:NAME -->
    Block kept if data["SHOW_NAME"] is truthy, removed otherwise

Output: prints rendered HTML to stdout.

Example template fragment:
    <p>Today is {{DATE}}</p>
    <!-- IF:HOT_LEADS -->
    <h2>🔥 {{HOT_LEAD_COUNT}} Hot Leads</h2>
    {{HOT_LEADS_HTML}}
    <!-- END:HOT_LEADS -->

Example data JSON:
    {
        "DATE": "May 18, 2026",
        "HOT_LEAD_COUNT": 3,
        "HOT_LEADS_HTML": "<div>...</div><div>...</div>",
        "SHOW_HOT_LEADS": true
    }
"""

import json
import re
import sys
from pathlib import Path


def render(template: str, data: dict) -> str:
    # 1. Process conditional blocks first (so placeholders inside disabled blocks aren't substituted needlessly)
    def replace_conditional(match):
        name = match.group(1)
        content = match.group(2)
        return content if data.get(f"SHOW_{name}", False) else ""

    template = re.sub(
        r"<!--\s*IF:(\w+)\s*-->(.*?)<!--\s*END:\1\s*-->",
        replace_conditional,
        template,
        flags=re.DOTALL,
    )

    # 2. Substitute simple {{KEY}} placeholders
    def replace_placeholder(match):
        key = match.group(1).strip()
        if key in data:
            return str(data[key])
        # leave unresolved placeholders visible to help debugging
        return f"{{{{ {key} — NOT FOUND }}}}"

    template = re.sub(r"\{\{\s*([\w_]+)\s*\}\}", replace_placeholder, template)

    return template


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("Usage: render_email.py <template-path> <data-json-path>\n")
        sys.exit(1)

    template_path = Path(sys.argv[1])
    data_path = Path(sys.argv[2])

    if not template_path.exists():
        sys.stderr.write(f"ERROR: template not found: {template_path}\n")
        sys.exit(1)
    if not data_path.exists():
        sys.stderr.write(f"ERROR: data file not found: {data_path}\n")
        sys.exit(1)

    template = template_path.read_text()
    data = json.loads(data_path.read_text())

    rendered = render(template, data)
    sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
