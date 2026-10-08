"""Job-title tags the proposal funnel splits on (Upwork brief Phase 2, Loop 1).

Two facts read off the JOB TITLE alone, the way a client's job reaches us:

  tool_in_title(title)  1 when the title names a tool we sell. The single
                        strongest split in the 8 Oct research: once a client
                        opened the proposal, 63% of 2025-26 proposals to such
                        jobs got a reply against 19% to the rest.
  job_category(title)   which kind of job it is (first rule that matches).

Same rules as the research (var/prop/cloud/lib.py), with its two fixes of
8 Oct evening: "Go High Level" written with spaces counts as GoHighLevel, and
"Make" used as a verb ("... and Make Suggestions") does not count as Make.
"""
from __future__ import annotations

import re
from typing import Optional

TOOLS = re.compile(
    r"airtable|make\.com|\bmake\b(?!\s+suggestions)|integromat|zapier|\bn8n\b|gohigh\s*level|\bghl\b"
    r"|high\s*level|hubspot|monday\.com|clickup|\bvapi\b|pipedrive|salesforce|zoho|notion|jotform"
    r"|softr|retell", re.I)

CATEGORIES = (
    ("Airtable", r"airtable"),
    ("Make / Zapier / n8n", r"make\.com|\bmake\b(?!\s+suggestions)|integromat|zapier|\bn8n\b"),
    ("GoHighLevel / CRM tool", r"gohigh\s*level|\bghl\b|high\s*level|hubspot|salesforce|pipedrive|zoho"
                               r"|monday\.com|\bcrm\b|clickup|notion|jotform"),
    ("Voice AI", r"\bvapi\b|voice (?:ai|agent|bot)|retell|elevenlabs|bland"),
    ("AI agent / LLM", r"ai agent|agentic|\bllm|langchain|\brag\b|openai|chatgpt|claude|\bgpt\b|ai engineer"
                       r"|ai developer|ai automation|\bmcp\b|\bai\b"),
    ("Generic dev / SaaS", r"python|fastapi|django|react|next\.js|full.?stack|saas|developer|engineer|software"),
    ("Automation, tool not named", r"automation|automat|workflow|integration|systems|operations"),
)


def tool_in_title(title: Optional[str]) -> Optional[int]:
    if not (title or "").strip():
        return None
    return 1 if TOOLS.search(title) else 0


def job_category(title: Optional[str]) -> Optional[str]:
    t = (title or "").lower()
    if not t.strip():
        return None
    for name, pat in CATEGORIES:
        if re.search(pat, t):
            return name
    return "Other"
