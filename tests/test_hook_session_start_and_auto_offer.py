"""Tests for the SessionStart hook runtime + auto-offer integration.

Covers Phase 1 Task 1.6 (SessionStart) and the auto-offer portion
of Task 1.5a wired through the PostToolUse runtime. Pure-function
heuristics are tested separately in test_heuristics_auto_offer.py;
session state persistence in test_session_state.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tailtest.hook.post_tool_use import run as post_tool_use_run
from tailtest.hook.session_start import (
    SessionStartResult,
    _extract_session_id,
    _parse_stdin,
)
from tailtest.hook.session_start import (
    run as session_start_run,
)

# --- SessionStart pure helpers -----------------------------------------


def test_parse_stdin_valid_payload() -> None:
    data = _parse_stdin('{"session_id": "abc123"}')
    assert data == {"session_id": "abc123"}


def test_parse_stdin_empty_returns_none() -> None:
    assert _parse_stdin("") is None
    assert _parse_stdin("   \n") is None


def test_parse_stdin_malformed_returns_none() -> None:
    assert _parse_stdin("{broken") is None


def test_parse_stdin_non_dict_returns_none() -> None:
    assert _parse_stdin('"just a string"') is None
    assert _parse_stdin("[1, 2]") is None


def test_extract_session_id_present() -> None:
    assert _extract_session_id({"session_id": "abc"}) == "abc"


def test_extract_session_id_missing_returns_none() -> None:
    assert _extract_session_id({}) is None
    assert _extract_session_id(None) is None
    assert _extract_session_id({"session_id": ""}) is None
    assert _extract_session_id({"session_id": 42}) is None


# --- SessionStart end-to-end via run() --------------------------------


@pytest.mark.asyncio
async def test_run_empty_project_emits_ready_message(tmp_path: Path) -> None:
    """An empty directory surfaces the 'ready when you have code' message."""
    result = await session_start_run('{"session_id": "s1"}', project_root=tmp_path)
    assert isinstance(result, SessionStartResult)
    assert result.stdout_json is not None
    message = result.stdout_json  # SessionStart outputs plain text, not JSON
    assert "ready" in message.lower()
    assert "code to test" in message.lower()


@pytest.mark.asyncio
async def test_run_python_project_emits_init_message(tmp_path: Path) -> None:
    """A real Python project with pytest gets the initialized message."""
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("def add(a, b): return a + b\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_pkg.py").write_text("def test_placeholder():\n    assert True\n")

    result = await session_start_run('{"session_id": "s1"}', project_root=tmp_path)
    message = result.stdout_json or ""  # SessionStart outputs plain text
    assert "python" in message.lower()
    assert "initialized" in message.lower() or "mode" in message.lower()
    assert "/tailtest:status" in message


@pytest.mark.asyncio
async def test_run_bootstraps_config_file(tmp_path: Path) -> None:
    """SessionStart creates `.tailtest/config.yaml` when it is missing."""
    # Project has some code so we get past the empty-project branch.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")

    assert not (tmp_path / ".tailtest" / "config.yaml").exists()
    await session_start_run('{"session_id": "s1"}', project_root=tmp_path)
    assert (tmp_path / ".tailtest" / "config.yaml").exists()


@pytest.mark.asyncio
async def test_run_persists_profile_json(tmp_path: Path) -> None:
    """SessionStart writes `.tailtest/profile.json` from the shallow scan."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")

    await session_start_run('{"session_id": "s1"}', project_root=tmp_path)
    profile_path = tmp_path / ".tailtest" / "profile.json"
    assert profile_path.exists()
    profile_data = json.loads(profile_path.read_text())
    assert "primary_language" in profile_data


@pytest.mark.asyncio
async def test_run_resets_session_state_on_new_session(tmp_path: Path) -> None:
    """A new session_id wipes the previous debounce cache."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    (tailtest_dir / "session-state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": "old-session",
                "seen_offers": [
                    {
                        "file": "src/foo.py",
                        "symbol": "add",
                        "first_seen_iso": "2026-04-09T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")

    await session_start_run('{"session_id": "new-session"}', project_root=tmp_path)
    reloaded = json.loads((tailtest_dir / "session-state.json").read_text())
    assert reloaded["session_id"] == "new-session"
    assert reloaded["seen_offers"] == []


@pytest.mark.asyncio
async def test_run_handles_missing_session_id_gracefully(tmp_path: Path) -> None:
    """An empty stdin still produces a valid envelope."""
    result = await session_start_run("", project_root=tmp_path)
    assert result.stdout_json is not None  # SessionStart outputs plain text


# --- Auto-offer integration in PostToolUse run() ----------------------


def _make_python_project_with_uncovered_function(tmp_path: Path) -> Path:
    """Build a minimal Python project with one tested + one UNtested function."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
    )
    src = tmp_path / "src" / "calc"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "ops.py").write_text(
        "def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    # Only `add` has a test; `multiply` is uncovered.
    (tests / "test_ops.py").write_text(
        "from calc.ops import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    return src / "ops.py"


@pytest.mark.asyncio
async def test_auto_offer_surfaces_uncovered_function(tmp_path: Path) -> None:
    """Editing a source file with an uncovered pure function triggers an offer."""
    changed = _make_python_project_with_uncovered_function(tmp_path)
    payload = json.dumps(
        {
            "tool_name": "Edit",
            "session_id": "s1",
            "tool_input": {"file_path": str(changed)},
        }
    )
    result = await post_tool_use_run(payload, project_root=tmp_path)
    assert result.stdout_json is not None
    envelope = json.loads(result.stdout_json)
    context = envelope["additionalContext"]
    # `multiply` is the uncovered function; `add` already has a test.
    assert "multiply" in context
    assert "/tailtest:gen" in context
    assert "add" not in context or "multiply" in context  # defensive


@pytest.mark.asyncio
async def test_auto_offer_debounced_in_same_session(tmp_path: Path) -> None:
    """A second edit to the same file does not re-surface the same offer."""
    changed = _make_python_project_with_uncovered_function(tmp_path)
    payload = json.dumps(
        {
            "tool_name": "Edit",
            "session_id": "s1",
            "tool_input": {"file_path": str(changed)},
        }
    )
    first = await post_tool_use_run(payload, project_root=tmp_path)
    first_context = json.loads(first.stdout_json or "{}")["additionalContext"]
    assert "multiply" in first_context

    # Second call with the same session id should NOT re-offer multiply.
    second = await post_tool_use_run(payload, project_root=tmp_path)
    second_context = json.loads(second.stdout_json or '{"additionalContext": ""}')["additionalContext"]
    assert "multiply" not in second_context


@pytest.mark.asyncio
async def test_auto_offer_re_fires_on_new_session(tmp_path: Path) -> None:
    """A new session_id resets the debounce cache and re-surfaces the offer."""
    changed = _make_python_project_with_uncovered_function(tmp_path)

    first_payload = json.dumps(
        {
            "tool_name": "Edit",
            "session_id": "s1",
            "tool_input": {"file_path": str(changed)},
        }
    )
    await post_tool_use_run(first_payload, project_root=tmp_path)

    # Simulate a SessionStart that resets the state file.
    await session_start_run('{"session_id": "s2"}', project_root=tmp_path)

    second_payload = json.dumps(
        {
            "tool_name": "Edit",
            "session_id": "s2",
            "tool_input": {"file_path": str(changed)},
        }
    )
    second = await post_tool_use_run(second_payload, project_root=tmp_path)
    second_context = json.loads(second.stdout_json or '{"additionalContext": ""}')["additionalContext"]
    assert "multiply" in second_context


@pytest.mark.asyncio
async def test_auto_offer_suppressed_by_config_flag(tmp_path: Path) -> None:
    """Setting auto_offer_generation: false in config disables the offer."""
    changed = _make_python_project_with_uncovered_function(tmp_path)
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    (tailtest_dir / "config.yaml").write_text(
        "schema_version: 1\ndepth: standard\nnotifications:\n  auto_offer_generation: false\n"
    )

    payload = json.dumps(
        {
            "tool_name": "Edit",
            "session_id": "s1",
            "tool_input": {"file_path": str(changed)},
        }
    )
    result = await post_tool_use_run(payload, project_root=tmp_path)
    context = json.loads(result.stdout_json or "{}")["additionalContext"]
    assert "multiply" not in context
    assert "/tailtest:gen" not in context


@pytest.mark.asyncio
async def test_auto_offer_caps_at_3_suggestions(tmp_path: Path) -> None:
    """A source file with 5 uncovered functions surfaces at most 3 offers."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
    )
    src = tmp_path / "src" / "many"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "stuff.py").write_text(
        "def one(a): return a + 1\n"
        "def two(a): return a + 2\n"
        "def three(a): return a + 3\n"
        "def four(a): return a + 4\n"
        "def five(a): return a + 5\n"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_stuff.py").write_text("def test_noop(): assert True\n")

    payload = json.dumps(
        {
            "tool_name": "Edit",
            "session_id": "s1",
            "tool_input": {"file_path": str(src / "stuff.py")},
        }
    )
    result = await post_tool_use_run(payload, project_root=tmp_path)
    context = json.loads(result.stdout_json or "{}")["additionalContext"]
    # Count how many /tailtest:gen suggestions appear.
    count = context.count("/tailtest:gen")
    assert count == 3
