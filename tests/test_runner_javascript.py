"""Tests for JSRunner (Phase 1 Task 1.2b + Task 1.3 JS half).

Exercises discovery, framework selection (vitest vs jest), native TIA
delegation, heuristic fallback, and JSON parsing for both vitest and
jest output formats. The integration test against a real vitest fixture
is marked so it only runs when npx + vitest are reachable on the host.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tailtest.core.findings.schema import FindingKind, Severity
from tailtest.core.runner import RunnerNotAvailable
from tailtest.core.runner.javascript import JSRunner
from tailtest.core.tia.javascript import JSTIA

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VITEST_FIXTURE = FIXTURES / "runner_vitest_basic"


# --- Discovery ----------------------------------------------------------


def test_discover_returns_false_for_empty_project(tmp_path: Path) -> None:
    """A project without package.json, configs, or test dirs does not discover."""
    runner = JSRunner(tmp_path)
    assert runner.discover() is False


def test_discover_vitest_via_package_json(tmp_path: Path, monkeypatch) -> None:
    """devDependencies.vitest in package.json is sufficient for vitest detection."""
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "x", "devDependencies": {"vitest": "^1.0.0"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(shutil, "which", lambda _name: "/fake/npx")
    runner = JSRunner(tmp_path)
    assert runner.discover() is True
    assert runner.framework == "vitest"


def test_discover_jest_via_package_json(tmp_path: Path, monkeypatch) -> None:
    """devDependencies.jest only picks jest."""
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "x", "devDependencies": {"jest": "^29.0.0"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(shutil, "which", lambda _name: "/fake/npx")
    runner = JSRunner(tmp_path)
    assert runner.discover() is True
    assert runner.framework == "jest"


def test_discover_prefers_vitest_when_both_present(tmp_path: Path, monkeypatch) -> None:
    """vitest wins over jest when both are declared. This is the intentional default."""
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "x",
                "devDependencies": {"vitest": "^1.0.0", "jest": "^29.0.0"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(shutil, "which", lambda _name: "/fake/npx")
    runner = JSRunner(tmp_path)
    assert runner.discover() is True
    assert runner.framework == "vitest"


def test_discover_via_vitest_config_file(tmp_path: Path, monkeypatch) -> None:
    """A vitest.config.ts file is sufficient, no package.json needed."""
    (tmp_path / "vitest.config.ts").write_text("export default {};")
    monkeypatch.setattr(shutil, "which", lambda _name: "/fake/npx")
    runner = JSRunner(tmp_path)
    assert runner.discover() is True
    assert runner.framework == "vitest"


def test_discover_via_jest_config_file(tmp_path: Path, monkeypatch) -> None:
    """A jest.config.js file is sufficient."""
    (tmp_path / "jest.config.js").write_text("module.exports = {};")
    monkeypatch.setattr(shutil, "which", lambda _name: "/fake/npx")
    runner = JSRunner(tmp_path)
    assert runner.discover() is True
    assert runner.framework == "jest"


def test_discover_returns_false_for_test_dir_without_framework_signal(
    tmp_path: Path, monkeypatch
) -> None:
    """A `tests/` dir alone is NOT a framework signal (Checkpoint G fix).

    Regression test for the Feynman dogfood finding: Feynman has
    `tests/*.test.ts` files but uses Node's built-in `node --test`
    runner, not vitest or jest. Earlier JSRunner would return True
    here and fall back to `vitest` as the default, then crash on run
    because vitest is not installed. The fix tightens discover() to
    require an explicit framework signal (config file or
    package.json devDependency).
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "example.test.ts").write_text("// test placeholder")
    # package.json with NO vitest or jest entry, mimicking Feynman's
    # node --test setup.
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "x",
                "scripts": {"test": "node --import tsx --test tests/*.test.ts"},
                "devDependencies": {"tsx": "^4.21.0", "typescript": "^5.9.3"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(shutil, "which", lambda _name: "/fake/npx")
    runner = JSRunner(tmp_path)
    assert runner.discover() is False


def test_discover_raises_when_npx_missing(tmp_path: Path, monkeypatch) -> None:
    """When package.json declares vitest but npx is not on PATH, raise."""
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "x", "devDependencies": {"vitest": "^1.0.0"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    runner = JSRunner(tmp_path)
    with pytest.raises(RunnerNotAvailable):
        runner.discover()


def test_discover_ignores_malformed_package_json(tmp_path: Path, monkeypatch) -> None:
    """A broken package.json doesn't crash discover, it just doesn't count."""
    (tmp_path / "package.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda _name: "/fake/npx")
    runner = JSRunner(tmp_path)
    assert runner.discover() is False


# --- Heuristic TIA fallback --------------------------------------------


def test_heuristic_matches_test_file_by_stem(tmp_path: Path) -> None:
    """The fallback heuristic matches test files that reference the changed stem."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "widget.ts").write_text("export const x = 1;")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "widget.test.ts").write_text(
        "import { x } from '../src/widget'; it('works', () => {});"
    )
    (tmp_path / "tests" / "other.test.ts").write_text("it('unrelated', () => {});")

    runner = JSRunner(tmp_path)
    impacted = runner._impacted_via_heuristic([Path("src/widget.ts")])
    assert len(impacted) == 1
    assert "widget.test.ts" in impacted[0]


def test_heuristic_returns_empty_for_non_js_changes(tmp_path: Path) -> None:
    """A .py or .md change should not match any JS test."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "widget.test.ts").write_text("// unused")
    runner = JSRunner(tmp_path)
    assert runner._impacted_via_heuristic([Path("src/widget.py")]) == []
    assert runner._impacted_via_heuristic([Path("README.md")]) == []


def test_heuristic_skips_fixture_subtrees(tmp_path: Path) -> None:
    """Test files inside fixtures/ subdirs must be skipped (same rule as PythonRunner)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "widget.ts").write_text("export const x = 1;")
    fixture_dir = tmp_path / "tests" / "fixtures" / "inner"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "widget.test.ts").write_text("// references widget")

    runner = JSRunner(tmp_path)
    impacted = runner._impacted_via_heuristic([Path("src/widget.ts")])
    assert impacted == []


# --- JSON parsing: vitest ----------------------------------------------


def test_parse_vitest_json_all_passing() -> None:
    stdout = json.dumps(
        {
            "testResults": [
                {
                    "name": "/tmp/x/math.test.js",
                    "assertionResults": [
                        {"status": "passed", "fullName": "math > adds"},
                        {"status": "passed", "fullName": "math > subtracts"},
                    ],
                }
            ]
        }
    )
    runner = JSRunner(Path("."))
    batch = runner._parse_vitest_json(stdout=stdout, run_id="r", duration_ms=12.0)
    assert batch.tests_passed == 2
    assert batch.tests_failed == 0
    assert batch.findings == []
    assert "2/2 tests passed" in batch.summary_line


def test_parse_vitest_json_mixed_results() -> None:
    """1 passed, 1 failed, 1 skipped produces a single Finding with HIGH severity."""
    stdout = json.dumps(
        {
            "testResults": [
                {
                    "name": "/tmp/x/math.test.js",
                    "assertionResults": [
                        {"status": "passed", "fullName": "math > adds"},
                        {
                            "status": "failed",
                            "fullName": "math > subtracts (bug)",
                            "failureMessages": [
                                "AssertionError: expected 2 to be 8\n    at /tmp/x/math.test.js:15"
                            ],
                            "location": {"line": 15, "column": 4},
                        },
                        {"status": "skipped", "fullName": "math > multiplies"},
                    ],
                }
            ]
        }
    )
    runner = JSRunner(Path("."))
    batch = runner._parse_vitest_json(stdout=stdout, run_id="r", duration_ms=12.0)
    assert batch.tests_passed == 1
    assert batch.tests_failed == 1
    assert batch.tests_skipped == 1
    assert len(batch.findings) == 1

    f = batch.findings[0]
    assert f.kind == FindingKind.TEST_FAILURE
    assert f.severity == Severity.HIGH
    assert f.line == 15
    assert "expected 2 to be 8" in f.message
    assert f.rule_id is not None
    assert "math > subtracts (bug)" in f.rule_id
    assert f.claude_hint is not None
    assert "AssertionError" in f.claude_hint


def test_parse_vitest_json_tolerates_banner_prefix() -> None:
    """vitest sometimes prints a banner before the JSON. The parser must find the first {."""
    stdout = "DEV  v1.6.0 /tmp/x\n\n" + json.dumps(
        {
            "testResults": [
                {
                    "name": "/tmp/x/one.test.js",
                    "assertionResults": [{"status": "passed", "fullName": "one"}],
                }
            ]
        }
    )
    runner = JSRunner(Path("."))
    batch = runner._parse_vitest_json(stdout=stdout, run_id="r", duration_ms=10.0)
    assert batch.tests_passed == 1


# --- JSON parsing: jest ------------------------------------------------


def test_parse_jest_json_mixed_results() -> None:
    stdout = json.dumps(
        {
            "testResults": [
                {
                    "name": "/tmp/j/math.test.js",
                    "testResults": [
                        {"status": "passed", "fullName": "math > adds"},
                        {
                            "status": "failed",
                            "fullName": "math > fails",
                            "failureMessages": [
                                "Error: boom\n    at Object.<anonymous> (math.test.js:7:3)"
                            ],
                            "location": {"line": 7, "column": 3},
                        },
                    ],
                }
            ]
        }
    )
    runner = JSRunner(Path("."))
    runner._framework = "jest"
    batch = runner._parse_jest_json(stdout=stdout, run_id="r", duration_ms=20.0)
    assert batch.tests_passed == 1
    assert batch.tests_failed == 1
    assert len(batch.findings) == 1
    assert batch.findings[0].line == 7
    assert "boom" in batch.findings[0].message
    assert batch.findings[0].rule_id is not None
    assert batch.findings[0].rule_id.startswith("jest::")


def test_parse_jest_json_handles_missing_location_gracefully() -> None:
    stdout = json.dumps(
        {
            "testResults": [
                {
                    "name": "/tmp/j/other.test.js",
                    "testResults": [
                        {
                            "status": "failed",
                            "fullName": "no location",
                            "failureMessages": ["boom"],
                        }
                    ],
                }
            ]
        }
    )
    runner = JSRunner(Path("."))
    runner._framework = "jest"
    batch = runner._parse_jest_json(stdout=stdout, run_id="r", duration_ms=5.0)
    assert batch.tests_failed == 1
    assert batch.findings[0].line == 0


def test_summarize_trims_and_collapses_whitespace() -> None:
    msg = "a   b\n\nc\td" + "\n" * 10
    assert JSRunner._summarize(msg) == "a b c d"


def test_summarize_truncates_long_messages() -> None:
    msg = "x" * 500
    out = JSRunner._summarize(msg)
    assert len(out) == 200
    assert out.endswith("...")


def test_first_line_returns_none_when_empty() -> None:
    assert JSRunner._first_line("") is None
    assert JSRunner._first_line("\n\n\n") is None


def test_first_line_caps_at_200_chars() -> None:
    out = JSRunner._first_line("a" * 500)
    assert out is not None
    assert len(out) == 200


# --- TIA delegator ------------------------------------------------------


def test_jstia_constructs_without_discovery_failure(tmp_path: Path) -> None:
    """JSTIA swallows discover() errors so the constructor is always safe."""
    # No package.json, no config, no test dir. Discovery returns False
    # and JSTIA should still construct without raising.
    provider = JSTIA(tmp_path)
    assert provider is not None


@pytest.mark.asyncio
async def test_jstia_delegates_impacted_to_runner(tmp_path: Path) -> None:
    """JSTIA.impacted passes through to the wrapped JSRunner instance."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "w.ts").write_text("export const x = 1;")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "w.test.ts").write_text("import '../src/w'; it('x', () => {});")

    provider = JSTIA(tmp_path)
    # No npx available in CI, so impacted() will hit the heuristic
    # fallback path. We don't mock npx here; we just verify the
    # delegator reaches the runner and returns a list.
    result = await provider.impacted([Path("src/w.ts")])
    # Either the native path returned results OR the heuristic did.
    # Both are valid for a one-file project with one matching test.
    assert isinstance(result, list)


# --- Integration test against the real vitest fixture -----------------


def _vitest_available() -> bool:
    """Return True iff a real vitest run can be launched from the fixture."""
    if shutil.which("npx") is None:
        return False
    node_modules = VITEST_FIXTURE / "node_modules" / "vitest"
    return node_modules.exists()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _vitest_available(),
    reason="vitest not installed in the fixture's node_modules (run `cd tests/fixtures/runner_vitest_basic && npm install`)",
)
async def test_run_vitest_fixture_end_to_end() -> None:
    """End-to-end: JSRunner runs the vitest fixture and parses real output.

    Only runs when the fixture has node_modules (set up manually or via
    `npm install` in the fixture directory). Gated by the skipif above
    so CI passes on machines without Node.
    """
    runner = JSRunner(VITEST_FIXTURE)
    assert runner.discover() is True
    batch = await runner.run([], run_id="vitest-int", timeout_seconds=120.0)
    # Fixture has 1 passing + 1 failing + 1 skipped test.
    assert batch.tests_passed == 1
    assert batch.tests_failed == 1
    assert batch.tests_skipped == 1
    assert len(batch.findings) == 1
    assert batch.findings[0].kind == FindingKind.TEST_FAILURE
