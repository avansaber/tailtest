"""Tests for Wave A (v3.15.0): SubagentStop hook, verifier agent, manifest polish.

Covers:
  - subagent_stop.py: drain path, paused guard, empty-pending guard, output format
  - agents/verifier.md: file exists, valid frontmatter
  - .claude-plugin/plugin.json: new required fields present, version bumped
"""

from __future__ import annotations

import json
import os
import sys
import io
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(pending: list[dict] | None = None, paused: bool = False) -> dict:
    return {
        "session_id": "test-session",
        "started_at": "2026-06-13T00:00:00Z",
        "project_root": "/tmp/project",
        "runners": {"python": {"command": "pytest", "args": ["-q"], "test_location": "tests/"}},
        "pending_files": pending if pending is not None else [],
        "touched_files": [],
        "fix_attempts": {},
        "deferred_failures": [],
        "paused": paused,
    }


def _run_subagent_stop(session: dict, project_root: str = "/tmp/project") -> tuple[str, int]:
    """Run subagent_stop.main() with the given session, return (stdout, exit_code)."""
    import subagent_stop

    event = json.dumps({"cwd": project_root})
    captured = io.StringIO()
    exit_code = 0

    with patch("subagent_stop.load_session", return_value=session):
        with patch("sys.stdin", io.StringIO(event)):
            with patch("sys.stdout", captured):
                try:
                    subagent_stop.main()
                except SystemExit as e:
                    exit_code = e.code if e.code is not None else 0

    return captured.getvalue(), exit_code


# ---------------------------------------------------------------------------
# SubagentStop: drain path
# ---------------------------------------------------------------------------


class TestSubagentStopDrain:
    def test_single_pending_file_emits_additionalcontext(self):
        session = _make_session(pending=[{"path": "src/billing.py", "language": "python", "status": "new-file"}])
        stdout, exit_code = _run_subagent_stop(session)
        assert exit_code == 0
        data = json.loads(stdout.strip())
        assert "hookSpecificOutput" in data
        assert "additionalContext" in data["hookSpecificOutput"]
        note = data["hookSpecificOutput"]["additionalContext"]
        assert "billing.py" in note
        assert "1 file" in note

    def test_multiple_pending_files_list_all_paths(self):
        pending = [
            {"path": "src/billing.py", "language": "python", "status": "new-file"},
            {"path": "src/proration.py", "language": "python", "status": "new-file"},
        ]
        session = _make_session(pending=pending)
        stdout, exit_code = _run_subagent_stop(session)
        assert exit_code == 0
        data = json.loads(stdout.strip())
        note = data["hookSpecificOutput"]["additionalContext"]
        assert "billing.py" in note
        assert "proration.py" in note
        assert "2 file" in note

    def test_drain_note_includes_session_read_instruction(self):
        pending = [{"path": "src/utils.py", "language": "python", "status": "new-file"}]
        session = _make_session(pending=pending)
        stdout, _ = _run_subagent_stop(session)
        data = json.loads(stdout.strip())
        note = data["hookSpecificOutput"]["additionalContext"]
        assert "session.json" in note

    def test_output_is_valid_json(self):
        pending = [{"path": "src/foo.py", "language": "python", "status": "new-file"}]
        session = _make_session(pending=pending)
        stdout, exit_code = _run_subagent_stop(session)
        assert exit_code == 0
        data = json.loads(stdout.strip())
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# SubagentStop: guard conditions
# ---------------------------------------------------------------------------


class TestSubagentStopGuards:
    def test_paused_session_exits_silently(self):
        session = _make_session(paused=True, pending=[{"path": "src/foo.py", "language": "python", "status": "new-file"}])
        stdout, exit_code = _run_subagent_stop(session)
        assert exit_code == 0
        assert stdout == ""

    def test_empty_pending_files_exits_silently(self):
        session = _make_session(pending=[])
        stdout, exit_code = _run_subagent_stop(session)
        assert exit_code == 0
        assert stdout == ""

    def test_missing_pending_key_exits_silently(self):
        session = _make_session()
        del session["pending_files"]
        stdout, exit_code = _run_subagent_stop(session)
        assert exit_code == 0
        assert stdout == ""

    def test_empty_stdin_handled_gracefully(self):
        import subagent_stop
        import io as _io

        session = _make_session(pending=[])
        captured = _io.StringIO()
        exit_code = 0
        with patch("subagent_stop.load_session", return_value=session):
            with patch("sys.stdin", _io.StringIO("")):
                with patch("sys.stdout", captured):
                    try:
                        subagent_stop.main()
                    except SystemExit as e:
                        exit_code = e.code if e.code is not None else 0
        assert exit_code == 0

    def test_malformed_stdin_handled_gracefully(self):
        import subagent_stop
        import io as _io

        session = _make_session(pending=[])
        captured = _io.StringIO()
        exit_code = 0
        with patch("subagent_stop.load_session", return_value=session):
            with patch("sys.stdin", _io.StringIO("not json")):
                with patch("sys.stdout", captured):
                    try:
                        subagent_stop.main()
                    except SystemExit as e:
                        exit_code = e.code if e.code is not None else 0
        assert exit_code == 0


# ---------------------------------------------------------------------------
# Verifier agent: file and frontmatter
# ---------------------------------------------------------------------------


class TestVerifierAgent:
    def test_verifier_md_exists(self):
        path = os.path.join(REPO_ROOT, "agents", "verifier.md")
        assert os.path.isfile(path), f"agents/verifier.md not found at {path}"

    def test_verifier_md_has_yaml_frontmatter(self):
        path = os.path.join(REPO_ROOT, "agents", "verifier.md")
        content = open(path).read()
        assert content.startswith("---"), "agents/verifier.md must begin with YAML frontmatter (---)"

    def test_verifier_md_declares_name(self):
        path = os.path.join(REPO_ROOT, "agents", "verifier.md")
        content = open(path).read()
        assert "name:" in content

    def test_verifier_md_declares_model_haiku(self):
        path = os.path.join(REPO_ROOT, "agents", "verifier.md")
        content = open(path).read()
        assert "model:" in content
        assert "haiku" in content

    def test_verifier_md_disallows_write_tools(self):
        path = os.path.join(REPO_ROOT, "agents", "verifier.md")
        content = open(path).read()
        assert "disallowedTools" in content
        assert "Write" in content
        assert "Edit" in content

    def test_verifier_md_is_read_only(self):
        path = os.path.join(REPO_ROOT, "agents", "verifier.md")
        content = open(path).read()
        assert "Never modify" in content or "read-only" in content.lower() or "Read-only" in content


# ---------------------------------------------------------------------------
# Plugin manifest: enriched fields
# ---------------------------------------------------------------------------


class TestPluginManifest:
    @pytest.fixture
    def plugin_json(self):
        path = os.path.join(REPO_ROOT, ".claude-plugin", "plugin.json")
        with open(path) as f:
            return json.load(f)

    def test_version_bumped_to_3_15_0(self, plugin_json):
        assert plugin_json["version"] == "3.15.0"

    def test_display_name_present(self, plugin_json):
        assert "displayName" in plugin_json
        assert plugin_json["displayName"]

    def test_description_updated(self, plugin_json):
        assert plugin_json["description"]
        assert len(plugin_json["description"]) > 30

    def test_keywords_include_tdd_and_adversarial(self, plugin_json):
        keywords = plugin_json.get("keywords", [])
        assert "tdd" in keywords
        assert "adversarial" in keywords

    def test_agents_reference_present(self, plugin_json):
        assert "agents" in plugin_json
        agents = plugin_json["agents"]
        assert agents

    def test_hooks_reference_present(self, plugin_json):
        assert "hooks" in plugin_json
        assert "hooks.json" in plugin_json["hooks"]

    def test_user_config_depth_present(self, plugin_json):
        assert "userConfig" in plugin_json
        uc = plugin_json["userConfig"]
        assert "depth" in uc
        depth = uc["depth"]
        assert depth.get("type") == "string"
        assert depth.get("default") == "standard"

    def test_marketplace_json_version_matches(self):
        mpath = os.path.join(REPO_ROOT, ".claude-plugin", "marketplace.json")
        with open(mpath) as f:
            marketplace = json.load(f)
        plugin_versions = [p["version"] for p in marketplace.get("plugins", [])]
        assert "3.15.0" in plugin_versions


# ---------------------------------------------------------------------------
# hooks.json: SubagentStop wired
# ---------------------------------------------------------------------------


class TestHooksJson:
    @pytest.fixture
    def hooks_json(self):
        path = os.path.join(REPO_ROOT, "hooks", "hooks.json")
        with open(path) as f:
            return json.load(f)

    def test_subagent_stop_present(self, hooks_json):
        hooks = hooks_json.get("hooks", {})
        assert "SubagentStop" in hooks, "SubagentStop event not found in hooks.json"

    def test_subagent_stop_references_correct_script(self, hooks_json):
        hooks = hooks_json.get("hooks", {})
        entries = hooks["SubagentStop"]
        commands = [h["command"] for entry in entries for h in entry.get("hooks", [])]
        assert any("subagent_stop.py" in cmd for cmd in commands)

    def test_subagent_stop_has_timeout(self, hooks_json):
        hooks = hooks_json.get("hooks", {})
        entries = hooks["SubagentStop"]
        timeouts = [h.get("timeout") for entry in entries for h in entry.get("hooks", [])]
        assert all(t is not None for t in timeouts)

    def test_existing_hooks_unchanged(self, hooks_json):
        hooks = hooks_json.get("hooks", {})
        assert "SessionStart" in hooks
        assert "SessionEnd" in hooks
        assert "PostToolUse" in hooks
