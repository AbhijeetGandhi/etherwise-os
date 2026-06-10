# Etherwise OS v3

The operating system for Abhijeet Gandhi's agency (Etherwise): lead acquisition, CRM, knowledge, finance, and a proactive chief-of-staff — SQLite-canonical, Airtable-mirrored, Claude-native, rails for execution, agents for judgment.

**Status: kernel scaffold (Day 1, 2026-06-10). v2 (`../etherwise-os/`) is the live system until per-module cutover.**

Start here: `BUILD_BRIEF.md` → `../business/etherwise-v3-core-architecture.md`.

```
core/        kernel: config · db+migrations · claude_gateway · runner · guardrails · sync · evals
modules/     upwork · cockpit · knowledge · chief_of_staff · finance
rails/       REGISTRY.md + deterministic executors (watchers)
.claude/     agents (subagents) · skills · hooks
knowledge/   wiki + client dossiers (git-tracked, cited)
launchd/     schedules
var/         db · logs · backups (gitignored)
```

Run migrations: `PYTHONPATH=. python3 -m core.db`
