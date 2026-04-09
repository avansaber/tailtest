"""Tests that every skill file under ``tailtest/skills/`` parses as valid.

Phase 1 Task 1.9 through 1.10 ship six namespaced skill files. Each
one is a markdown file with YAML frontmatter. This test walks the
directory, verifies the directory/filename contract, and asserts
the frontmatter has the required fields.

The tests are static: no Claude Code session, no plugin install, no
MCP invocation. They catch typos + forgotten fields + broken YAML
before a real session ever sees the skill.
"""

from __future__ import annotations

from pathlib import Path

import yaml

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# The six skills Phase 1 ships. Directory name, which maps to the
# invocation as `/tailtest:<name>` per the Claude Code plugin
# namespacing rule.
EXPECTED_SKILLS = {
    "status",
    "depth",
    "scan",
    "gen",
    "report",
    "setup",
    # Phase 2 Task 2.7 added the debt review skill.
    "debt",
}

# Skills that take a positional argument via $ARGUMENTS and therefore
# MUST declare an argument-hint in their frontmatter.
SKILLS_REQUIRING_ARGUMENT_HINT = {"depth", "gen"}


def _parse_frontmatter(skill_file: Path) -> dict:
    """Parse the YAML frontmatter from a SKILL.md file.

    Expects the file to start with ``---\\n``, contain the frontmatter,
    and close with another ``---\\n`` before the body.
    """
    text = skill_file.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise AssertionError(f"{skill_file} does not start with YAML frontmatter")
    # Find the closing --- marker.
    rest = text[len("---\n") :]
    end = rest.find("\n---")
    if end < 0:
        raise AssertionError(f"{skill_file} frontmatter is not closed with ---")
    yaml_text = rest[:end]
    parsed = yaml.safe_load(yaml_text)
    if not isinstance(parsed, dict):
        raise AssertionError(f"{skill_file} frontmatter did not parse as a mapping")
    return parsed


def test_skills_dir_exists() -> None:
    assert SKILLS_DIR.is_dir(), f"missing skills dir: {SKILLS_DIR}"


def test_every_expected_skill_has_a_directory() -> None:
    present = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}
    missing = EXPECTED_SKILLS - present
    assert not missing, f"missing skill dirs: {sorted(missing)}"


def test_no_stale_skill_directories() -> None:
    """Phase 0 placeholder `skills/tailtest/` must be gone. Only the six
    expected namespaced skills should be present.
    """
    present = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}
    extra = present - EXPECTED_SKILLS
    assert not extra, (
        f"unexpected skill dirs present (did the Phase 0 placeholder "
        f"get deleted properly?): {sorted(extra)}"
    )


def test_every_skill_has_a_skill_md_file() -> None:
    for name in EXPECTED_SKILLS:
        skill_file = SKILLS_DIR / name / "SKILL.md"
        assert skill_file.is_file(), f"missing SKILL.md at {skill_file}"


def test_every_skill_frontmatter_has_description() -> None:
    for name in EXPECTED_SKILLS:
        skill_file = SKILLS_DIR / name / "SKILL.md"
        parsed = _parse_frontmatter(skill_file)
        assert "description" in parsed, f"{skill_file} missing `description`"
        assert isinstance(parsed["description"], str), f"{skill_file} description must be a string"
        assert len(parsed["description"]) >= 20, (
            f"{skill_file} description is too short ({len(parsed['description'])} chars), "
            f"Claude Code uses it for skill discovery"
        )


def test_argument_taking_skills_declare_argument_hint() -> None:
    """Skills that accept $ARGUMENTS must declare an `argument-hint` so
    Claude Code can show a hint in the autocomplete UI.
    """
    for name in SKILLS_REQUIRING_ARGUMENT_HINT:
        skill_file = SKILLS_DIR / name / "SKILL.md"
        parsed = _parse_frontmatter(skill_file)
        assert "argument-hint" in parsed, (
            f"{skill_file} takes arguments but has no argument-hint in frontmatter"
        )
        assert isinstance(parsed["argument-hint"], str), (
            f"{skill_file} argument-hint must be a string"
        )


def test_no_skill_has_a_name_field() -> None:
    """Per the Claude Code plugin convention, the skill name is inferred
    from the directory name. A `name:` field in frontmatter is either
    ignored or (worse) creates ambiguity. Reject it at the test level.
    """
    for name in EXPECTED_SKILLS:
        skill_file = SKILLS_DIR / name / "SKILL.md"
        parsed = _parse_frontmatter(skill_file)
        assert "name" not in parsed, (
            f"{skill_file} has a `name` field in frontmatter. "
            "Skill names come from the directory name; remove the field."
        )


def test_every_skill_body_references_at_least_one_tailtest_concept() -> None:
    """Sanity check: every skill body should mention at least one real
    tailtest concept (MCP tool, config file, or sibling skill). This
    catches empty stubs that got committed by accident.
    """
    sentinel_substrings = (
        "MCP tool",
        "scan_project",
        "run_tests",
        "generate_tests",
        "get_baseline",
        "tailtest_status",
        ".tailtest/",
        "config.yaml",
        "profile.json",
        "latest.json",
        "/tailtest:",
    )
    for name in EXPECTED_SKILLS:
        skill_file = SKILLS_DIR / name / "SKILL.md"
        body = skill_file.read_text(encoding="utf-8")
        assert any(sentinel in body for sentinel in sentinel_substrings), (
            f"{skill_file} body mentions no tailtest concept, looks like an empty stub"
        )
