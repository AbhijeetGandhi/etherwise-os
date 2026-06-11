"""Unit tests for core/guardrails.py — pure decision logic, parameterized so
no test depends on the live SHADOW_MODE map or the real marker file.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from core import config, db
from core import guardrails as g

SHADOW_ON = {"upwork": True, "cockpit": False}
SHADOW_OFF = {"upwork": False, "cockpit": False}


def ev(tool_name, tool_input=None, shadow_map=SHADOW_ON,
       core_writes_allowed=False):
    return g.evaluate_pretooluse(tool_name, tool_input or {},
                                 shadow_map=shadow_map,
                                 core_writes_allowed=core_writes_allowed)


class TestAirtableRules(unittest.TestCase):
    CREATE = "mcp__claude_ai_Airtable__create_records_for_table"
    UPDATE = "mcp__claude_ai_Airtable__update_records_for_table"
    DELETE = "mcp__claude_ai_Airtable__delete_records_for_table"

    def test_write_without_typecast_denied_even_live(self):
        d = ev(self.CREATE, {"baseId": "app1", "records": []},
               shadow_map=SHADOW_OFF)
        self.assertEqual(d.action, "deny")
        self.assertIn("typecast", d.reason)

    def test_write_with_typecast_false_denied(self):
        d = ev(self.UPDATE, {"typecast": False}, shadow_map=SHADOW_OFF)
        self.assertEqual(d.action, "deny")

    def test_write_with_typecast_denied_while_shadowed(self):
        d = ev(self.CREATE, {"typecast": True}, shadow_map=SHADOW_ON)
        self.assertEqual(d.action, "deny")
        self.assertIn("shadow", d.reason)

    def test_write_with_typecast_allowed_when_all_live(self):
        d = ev(self.CREATE, {"typecast": True}, shadow_map=SHADOW_OFF)
        self.assertEqual(d.action, "allow")

    def test_delete_records_denied_always(self):
        d = ev(self.DELETE, {"typecast": True}, shadow_map=SHADOW_OFF)
        self.assertEqual(d.action, "deny")
        self.assertIn("delete", d.reason.lower())

    def test_reads_always_allowed(self):
        for tool in ("mcp__claude_ai_Airtable__list_records_for_table",
                     "mcp__claude_ai_Airtable__get_table_schema",
                     "mcp__claude_ai_Airtable__search_records",
                     "mcp__claude_ai_Airtable__ping"):
            self.assertEqual(ev(tool).action, "allow", tool)


class TestClickUpGmailRules(unittest.TestCase):
    def test_clickup_mutations_denied_in_shadow(self):
        for tool in ("mcp__claude_ai_ClickUp__clickup_create_task",
                     "mcp__claude_ai_ClickUp__clickup_update_task",
                     "mcp__claude_ai_ClickUp__clickup_move_task",
                     "mcp__claude_ai_ClickUp__clickup_send_chat_message"):
            self.assertEqual(ev(tool).action, "deny", tool)

    def test_clickup_reads_allowed(self):
        for tool in ("mcp__claude_ai_ClickUp__clickup_get_task",
                     "mcp__claude_ai_ClickUp__clickup_filter_tasks",
                     "mcp__claude_ai_ClickUp__clickup_search"):
            self.assertEqual(ev(tool).action, "allow", tool)

    def test_clickup_delete_denied_even_live(self):
        d = ev("mcp__claude_ai_ClickUp__clickup_delete_task",
               shadow_map=SHADOW_OFF)
        self.assertEqual(d.action, "deny")

    def test_gmail_draft_denied_in_shadow_allowed_live(self):
        self.assertEqual(ev("mcp__claude_ai_Gmail__create_draft").action,
                         "deny")
        self.assertEqual(
            ev("mcp__claude_ai_Gmail__create_draft",
               shadow_map=SHADOW_OFF).action, "allow")

    def test_gmail_reads_allowed(self):
        self.assertEqual(
            ev("mcp__claude_ai_Gmail__search_threads").action, "allow")


class TestProtectedPaths(unittest.TestCase):
    def edit(self, path, allowed=False):
        return ev("Edit", {"file_path": path}, core_writes_allowed=allowed)

    def test_core_denied_without_marker(self):
        for p in ("core/config.py", "core/runner.py",
                  str(config.V3_ROOT / "core/claude_gateway.py")):
            self.assertEqual(self.edit(p).action, "deny", p)

    def test_claude_dir_denied_without_marker(self):
        for p in (".claude/settings.json", ".claude/hooks/pretooluse.py"):
            self.assertEqual(self.edit(p).action, "deny", p)

    def test_registry_denied_without_marker(self):
        self.assertEqual(self.edit("rails/REGISTRY.md").action, "deny")

    def test_marker_unlocks_protected_paths(self):
        self.assertEqual(self.edit("core/config.py", allowed=True).action,
                         "allow")
        self.assertEqual(self.edit(".claude/settings.json",
                                   allowed=True).action, "allow")

    def test_marker_file_itself_always_denied(self):
        d = self.edit(".claude/ALLOW_CORE_WRITES", allowed=True)
        self.assertEqual(d.action, "deny")

    def test_module_and_doc_writes_allowed(self):
        for p in ("modules/upwork/sync.py", "BUILD_BRIEF.md",
                  "knowledge/wiki/foo.md", "tests/test_x.py"):
            self.assertEqual(self.edit(p).action, "allow", p)

    def test_write_tool_same_rules(self):
        d = ev("Write", {"file_path": "core/config.py"})
        self.assertEqual(d.action, "deny")


class TestBashRules(unittest.TestCase):
    def bash(self, cmd, **kw):
        return ev("Bash", {"command": cmd}, **kw)

    def test_rm_inside_var_allowed(self):
        self.assertEqual(self.bash("rm var/etherwise.db-journal").action,
                         "allow")
        self.assertEqual(
            self.bash(f"rm -f {config.VAR_DIR}/logs/old.log").action, "allow")

    def test_rm_tmp_allowed(self):
        self.assertEqual(self.bash("rm -rf /tmp/gw-test-x").action, "allow")
        self.assertEqual(self.bash("rm /private/tmp/foo").action, "allow")

    def test_rm_git_locks_allowed(self):
        self.assertEqual(
            self.bash("rm -f .git/index.lock .git/objects/02/tmp_obj_x").action,
            "allow")

    def test_rm_outside_allowlist_denied(self):
        self.assertEqual(self.bash("rm -rf ~/Documents/x").action, "deny")
        self.assertEqual(self.bash("rm core/config.py").action, "deny")
        self.assertEqual(self.bash("rm .git/config").action, "deny")

    def test_rm_with_unresolvable_target_denied(self):
        self.assertEqual(self.bash('rm -rf "$SOME_DIR"').action, "deny")

    def test_v2_mutations_denied(self):
        self.assertEqual(
            self.bash("sqlite3 ../etherwise-os/etherwise.db 'DELETE FROM x'")
            .action, "deny")
        self.assertEqual(
            self.bash("rm ../etherwise-os/some.py").action, "deny")

    def test_v2_reads_allowed(self):
        self.assertEqual(
            self.bash("sqlite3 ../etherwise-os/etherwise.db '.tables'").action,
            "allow")

    def test_v2_reads_with_benign_redirects_allowed(self):
        # 2>/dev/null and 2>&1 are not writes INTO v2 — regression: these were
        # false-positive denied the moment hooks went live (Day 3)
        self.assertEqual(
            self.bash("grep -rln pat /x/etherwise-os 2>/dev/null").action,
            "allow")
        self.assertEqual(
            self.bash('sqlite3 "file:/x/etherwise-os/etherwise.db?mode=ro"'
                      ' ".schema jobs" 2>&1 | head').action, "allow")

    def test_redirect_into_v2_denied(self):
        self.assertEqual(
            self.bash("echo hi > ../etherwise-os/notes.md").action, "deny")
        self.assertEqual(
            self.bash("cat x >> /Users/a/Etherwise/etherwise-os/log.txt")
            .action, "deny")

    def test_own_db_mutations_allowed(self):
        self.assertEqual(
            self.bash("sqlite3 var/etherwise.db 'DELETE FROM quarantine"
                      " WHERE id=3'").action, "allow")

    def test_other_db_mutations_denied(self):
        self.assertEqual(
            self.bash("sqlite3 /Users/abhijeet/other.db 'DROP TABLE x'")
            .action, "deny")

    def test_credentials_echo_denied(self):
        for cmd in ("cat ../etherwise-os/.credentials/etherwise-os.env",
                    "grep KEY ../etherwise-os/.credentials/upwork-api.json",
                    "cp ../etherwise-os/.credentials/upwork-api.json /tmp/"):
            self.assertEqual(self.bash(cmd).action, "deny", cmd)

    def test_credentials_listing_allowed(self):
        self.assertEqual(
            self.bash("ls -la ../etherwise-os/.credentials/").action, "allow")

    def test_upwork_mutation_denied(self):
        self.assertEqual(
            self.bash("curl -X POST https://api.upwork.com/graphql -d"
                      " '{\"query\": \"mutation sendMessage...\"}'").action,
            "deny")

    def test_upwork_read_allowed(self):
        self.assertEqual(
            self.bash("curl https://api.upwork.com/graphql -d"
                      " '{\"query\": \"query marketplaceJobPostings...\"}'")
            .action, "allow")

    def test_plain_commands_allowed(self):
        for cmd in ("ls -la", "git status", "python3 -m unittest",
                    "grep -r foo core/"):
            self.assertEqual(self.bash(cmd).action, "allow", cmd)


class TestDefaults(unittest.TestCase):
    def test_read_tools_allowed(self):
        for tool in ("Read", "Glob", "Grep", "WebFetch", "TodoWrite",
                     "AskUserQuestion"):
            self.assertEqual(ev(tool).action, "allow", tool)

    def test_unknown_tool_allowed(self):
        self.assertEqual(ev("SomeFutureTool").action, "allow")


class TestAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="guard-test-"))
        self.db_path = self.tmp / "test.db"
        db.migrate(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_audit_write_inserts_truncated_row(self):
        g.audit_write(self.db_path, "PostToolUse", "Bash",
                      {"command": "x" * 5000}, session_id="sess-1234567890",
                      note="ok")
        with db.connect(self.db_path) as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM audit_log")]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["entity"], "Bash")
        self.assertEqual(row["field"], "PostToolUse")
        self.assertEqual(row["source"], "hook")
        self.assertTrue(row["actor"].startswith("agent:"))
        self.assertLessEqual(len(row["new_value"]), 2000)
        self.assertEqual(row["note"], "ok")

    def test_audit_write_never_raises(self):
        # bogus db path must not blow up a session
        g.audit_write(self.tmp / "nope" / "missing.db", "PostToolUse",
                      "Read", {})


if __name__ == "__main__":
    unittest.main()
