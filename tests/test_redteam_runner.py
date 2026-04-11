"""Phase 6 Task 6.2 -- red-team runner tests.

Covers:
- applicable() gate (depth x ai_surface x ai_checks_enabled)
- _parse_verdicts() -- JSON extraction from raw claude output
- _judge_category() -- subprocess mocking, verdict parsing, finding creation
- run() -- no claude binary, no entry point, findings from mocked categories
- Rate-limiting to MAX_FINDINGS_PER_RUN
- HTML report generation
- HTML report skipped when no findings
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tailtest.core.config import DepthMode
from tailtest.core.config.schema import Config
from tailtest.core.findings.schema import FindingKind, Severity
from tailtest.core.scan.profile import AISurface, ProjectProfile
from tailtest.security.redteam.runner import (
    MAX_FINDINGS_PER_RUN,
    RedTeamRunner,
    _empty_batch,
    _parse_verdicts,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def agent_profile() -> ProjectProfile:
    """A project profile that looks like an AI agent."""
    return ProjectProfile(
        root=Path("/fake/project"),
        primary_language="python",
        ai_surface=AISurface.AGENT,
    )


@pytest.fixture()
def paranoid_config() -> Config:
    return Config(depth=DepthMode.PARANOID, ai_checks_enabled=True)


@pytest.fixture()
def runner() -> RedTeamRunner:
    return RedTeamRunner(timeout=5)


@pytest.fixture()
def project_with_agent(tmp_path: Path) -> Path:
    """A tmp project directory with a simple agent.py file."""
    agent_file = tmp_path / "agent.py"
    agent_file.write_text(
        "def run(user_input: str) -> str:\n"
        "    # Direct pass-through — no sanitization\n"
        "    return call_llm(system_prompt, user_input)\n"
    )
    return tmp_path


# ---------------------------------------------------------------------------
# applicable() gate
# ---------------------------------------------------------------------------


def test_applicable_true_for_paranoid_agent(
    runner: RedTeamRunner,
    agent_profile: ProjectProfile,
    paranoid_config: Config,
) -> None:
    assert runner.applicable(agent_profile, paranoid_config) is True


def test_applicable_false_for_thorough(
    runner: RedTeamRunner, agent_profile: ProjectProfile
) -> None:
    config = Config(depth=DepthMode.THOROUGH, ai_checks_enabled=True)
    assert runner.applicable(agent_profile, config) is False


def test_applicable_false_for_standard(
    runner: RedTeamRunner, agent_profile: ProjectProfile
) -> None:
    config = Config(depth=DepthMode.STANDARD)
    assert runner.applicable(agent_profile, config) is False


def test_applicable_false_for_non_agent_profile(
    runner: RedTeamRunner, paranoid_config: Config
) -> None:
    profile = ProjectProfile(
        root=Path("/fake"),
        primary_language="python",
        ai_surface=AISurface.NONE,
    )
    assert runner.applicable(profile, paranoid_config) is False


def test_applicable_false_when_ai_checks_disabled(
    runner: RedTeamRunner, agent_profile: ProjectProfile
) -> None:
    config = Config(depth=DepthMode.PARANOID, ai_checks_enabled=False)
    assert runner.applicable(agent_profile, config) is False


def test_applicable_true_when_ai_checks_none(
    runner: RedTeamRunner, agent_profile: ProjectProfile
) -> None:
    # ai_checks_enabled=None means "not explicitly disabled"
    config = Config(depth=DepthMode.PARANOID, ai_checks_enabled=None)
    assert runner.applicable(agent_profile, config) is True


# ---------------------------------------------------------------------------
# _parse_verdicts()
# ---------------------------------------------------------------------------


def test_parse_verdicts_valid_array() -> None:
    raw = json.dumps(
        [{"id": "pi_001", "vulnerable": True, "confidence": "high", "reasoning": "yes"}]
    )
    verdicts = _parse_verdicts(raw)
    assert len(verdicts) == 1
    assert verdicts[0]["id"] == "pi_001"
    assert verdicts[0]["vulnerable"] is True


def test_parse_verdicts_strips_markdown_fences() -> None:
    raw = "```json\n" + json.dumps([{"id": "x", "vulnerable": False}]) + "\n```"
    verdicts = _parse_verdicts(raw)
    assert len(verdicts) == 1


def test_parse_verdicts_empty_on_garbage() -> None:
    verdicts = _parse_verdicts("this is not json at all")
    assert verdicts == []


def test_parse_verdicts_empty_on_empty_string() -> None:
    assert _parse_verdicts("") == []


def test_parse_verdicts_extracts_array_from_preamble() -> None:
    raw = "Here are the results:\n" + json.dumps(
        [{"id": "a", "vulnerable": True, "reasoning": "r"}]
    )
    verdicts = _parse_verdicts(raw)
    assert len(verdicts) == 1


def test_parse_verdicts_returns_only_dicts() -> None:
    raw = json.dumps([{"id": "ok", "vulnerable": True}, "not_a_dict", 42])
    verdicts = _parse_verdicts(raw)
    assert all(isinstance(v, dict) for v in verdicts)


# ---------------------------------------------------------------------------
# _judge_category() -- subprocess mocked
# ---------------------------------------------------------------------------


def _fake_verdict(attack_id: str, vulnerable: bool) -> dict:
    return {
        "id": attack_id,
        "vulnerable": vulnerable,
        "confidence": "high",
        "reasoning": "test reasoning",
    }


def _make_proc(stdout: str) -> MagicMock:
    proc = MagicMock()
    # communicate() need not be async -- asyncio.wait_for is mocked separately
    proc.communicate = MagicMock(return_value=(stdout.encode(), b""))
    proc.returncode = 0
    return proc


@pytest.mark.asyncio
async def test_judge_category_returns_findings_for_vulnerable(
    runner: RedTeamRunner,
) -> None:
    from tailtest.security.redteam.loader import load_attacks

    pi_attacks = [a for a in load_attacks() if a.category == "prompt_injection"]
    assert pi_attacks

    verdict_attack = pi_attacks[0]
    raw = json.dumps([_fake_verdict(verdict_attack.id, vulnerable=True)])

    with (
        patch("asyncio.create_subprocess_exec", return_value=_make_proc(raw)),
        patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(raw.encode(), b"")),
    ):
        findings = await runner._judge_category(
            "prompt_injection", pi_attacks, "def run(x): return llm(x)"
        )

    assert len(findings) == 1
    assert findings[0].kind == FindingKind.REDTEAM
    assert findings[0].severity in {Severity.HIGH, Severity.CRITICAL}


@pytest.mark.asyncio
async def test_judge_category_returns_empty_when_not_vulnerable(
    runner: RedTeamRunner,
) -> None:
    from tailtest.security.redteam.loader import load_attacks

    pi_attacks = [a for a in load_attacks() if a.category == "prompt_injection"]
    raw = json.dumps([_fake_verdict(a.id, vulnerable=False) for a in pi_attacks])

    with (
        patch("asyncio.create_subprocess_exec", return_value=_make_proc(raw)),
        patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(raw.encode(), b"")),
    ):
        findings = await runner._judge_category(
            "prompt_injection", pi_attacks, "def run(x): return sanitize_then_llm(x)"
        )

    assert findings == []


@pytest.mark.asyncio
async def test_judge_category_timeout_returns_empty(runner: RedTeamRunner) -> None:
    from tailtest.security.redteam.loader import load_attacks

    pi_attacks = [a for a in load_attacks() if a.category == "prompt_injection"][:1]
    with (
        patch("asyncio.create_subprocess_exec", return_value=_make_proc("[]")),
        patch("asyncio.wait_for", side_effect=TimeoutError()),
    ):
        findings = await runner._judge_category("prompt_injection", pi_attacks, "code")

    assert findings == []


@pytest.mark.asyncio
async def test_judge_category_os_error_returns_empty(runner: RedTeamRunner) -> None:
    from tailtest.security.redteam.loader import load_attacks

    pi_attacks = [a for a in load_attacks() if a.category == "prompt_injection"][:1]
    with patch("asyncio.create_subprocess_exec", side_effect=OSError("no binary")):
        findings = await runner._judge_category("prompt_injection", pi_attacks, "code")

    assert findings == []


@pytest.mark.asyncio
async def test_judge_category_unknown_attack_id_skipped(runner: RedTeamRunner) -> None:
    from tailtest.security.redteam.loader import load_attacks

    pi_attacks = [a for a in load_attacks() if a.category == "prompt_injection"][:1]
    # Verdict references an unknown attack id
    raw = json.dumps([{"id": "does_not_exist", "vulnerable": True, "reasoning": "x"}])

    with (
        patch("asyncio.create_subprocess_exec", return_value=_make_proc(raw)),
        patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(raw.encode(), b"")),
    ):
        findings = await runner._judge_category("prompt_injection", pi_attacks, "code")

    assert findings == []


# ---------------------------------------------------------------------------
# run() -- high-level
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_returns_empty_when_no_claude(
    runner: RedTeamRunner,
    agent_profile: ProjectProfile,
    paranoid_config: Config,
    tmp_path: Path,
) -> None:
    with patch("shutil.which", return_value=None):
        batch = await runner.run(agent_profile, paranoid_config, tmp_path)

    assert batch.findings == []
    assert "not found" in batch.summary_line


@pytest.mark.asyncio
async def test_run_returns_empty_when_no_entry_point(
    runner: RedTeamRunner,
    agent_profile: ProjectProfile,
    paranoid_config: Config,
    tmp_path: Path,
) -> None:
    # tmp_path has no agent files
    with patch("shutil.which", return_value="/usr/bin/claude"):
        batch = await runner.run(agent_profile, paranoid_config, tmp_path)

    assert batch.findings == []
    assert "No agent entry point" in batch.summary_line


@pytest.mark.asyncio
async def test_run_rate_limits_to_max_findings(
    runner: RedTeamRunner,
    agent_profile: ProjectProfile,
    paranoid_config: Config,
    project_with_agent: Path,
) -> None:
    from tailtest.security.redteam.loader import load_attacks

    # Make every attack vulnerable: return one finding per category (8 total)
    # Rate-limit should cap at MAX_FINDINGS_PER_RUN = 5
    all_attacks = load_attacks()

    # Build 8 findings (one per category) and patch _judge_category
    from tailtest.core.findings.schema import Finding, FindingKind, Severity

    def make_finding(cat: str) -> Finding:
        return Finding.create(
            kind=FindingKind.REDTEAM,
            file=Path("agent.py"),
            line=0,
            rule_id=f"redteam/{cat}/test",
            message=f"test {cat}",
            severity=Severity.HIGH,
            run_id="redteam",
        )

    eight_findings = [make_finding(c) for c in list({a.category for a in all_attacks})]
    assert len(eight_findings) == 8  # one per category

    async def fake_judge_category(category, attacks, code_context):
        # return 1 finding per category
        return [make_finding(category)] if attacks else []

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch.object(runner, "_judge_category", side_effect=fake_judge_category),
    ):
        batch = await runner.run(agent_profile, paranoid_config, project_with_agent)

    assert len(batch.findings) <= MAX_FINDINGS_PER_RUN


@pytest.mark.asyncio
async def test_run_writes_html_report(
    runner: RedTeamRunner,
    agent_profile: ProjectProfile,
    paranoid_config: Config,
    project_with_agent: Path,
) -> None:
    from tailtest.core.findings.schema import Finding, FindingKind, Severity

    def make_finding(cat: str) -> Finding:
        return Finding.create(
            kind=FindingKind.REDTEAM,
            file=Path("agent.py"),
            line=0,
            rule_id=f"redteam/{cat}/test",
            message=f"vuln in {cat}",
            severity=Severity.HIGH,
            run_id="redteam",
        )

    async def fake_judge_category(category, attacks, code_context):
        return [make_finding(category)] if attacks else []

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch.object(runner, "_judge_category", side_effect=fake_judge_category),
    ):
        await runner.run(agent_profile, paranoid_config, project_with_agent)

    reports_dir = project_with_agent / ".tailtest" / "reports"
    html_files = list(reports_dir.glob("redteam-*.html"))
    assert html_files, "Expected HTML report to be written"
    html = html_files[0].read_text()
    assert "tailtest red-team report" in html


@pytest.mark.asyncio
async def test_run_no_html_report_when_no_findings(
    runner: RedTeamRunner,
    agent_profile: ProjectProfile,
    paranoid_config: Config,
    project_with_agent: Path,
) -> None:
    async def fake_judge_category(category, attacks, code_context):
        return []  # nothing vulnerable

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch.object(runner, "_judge_category", side_effect=fake_judge_category),
    ):
        batch = await runner.run(agent_profile, paranoid_config, project_with_agent)

    assert batch.findings == []
    reports_dir = project_with_agent / ".tailtest" / "reports"
    assert not list(reports_dir.glob("redteam-*.html"))


# ---------------------------------------------------------------------------
# _read_agent_code()
# ---------------------------------------------------------------------------


def test_read_agent_code_finds_agent_py(
    runner: RedTeamRunner,
    project_with_agent: Path,
    agent_profile: ProjectProfile,
) -> None:
    code = runner._read_agent_code(agent_profile, project_with_agent)
    assert "user_input" in code


def test_read_agent_code_returns_empty_for_empty_project(
    runner: RedTeamRunner,
    agent_profile: ProjectProfile,
    tmp_path: Path,
) -> None:
    code = runner._read_agent_code(agent_profile, tmp_path)
    assert code == ""


def test_read_agent_code_uses_config_override(
    runner: RedTeamRunner,
    agent_profile: ProjectProfile,
    tmp_path: Path,
) -> None:
    # Create a custom agent file declared in config
    custom_agent = tmp_path / "src" / "my_bot.py"
    custom_agent.parent.mkdir()
    custom_agent.write_text("def handle(msg): return llm(msg)\n")

    config_dir = tmp_path / ".tailtest"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "agent:\n  entry_points:\n    - file: src/my_bot.py\n"
    )

    code = runner._read_agent_code(agent_profile, tmp_path)
    assert "handle" in code


# ---------------------------------------------------------------------------
# _empty_batch helper
# ---------------------------------------------------------------------------


def test_empty_batch_has_no_findings() -> None:
    batch = _empty_batch("some reason")
    assert batch.findings == []
    assert "some reason" in batch.summary_line
