"""Detector functions — pure logic for each scanner capability (Phase 1 Task 1.12a).

Each detector takes a project root path (and sometimes a pre-walked file list)
and returns its slice of the `ProjectProfile`. The `ProjectScanner` in
`scanner.py` orchestrates them.

Keeping detectors in their own module makes them unit-testable in isolation
without having to spin up the whole scanner.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import tomllib
from pathlib import Path

from tailtest.core.scan.profile import (
    AIConfidence,
    AISurface,
    DetectedFramework,
    DetectedInfrastructure,
    DetectedPlanFile,
    DetectedRunner,
    DirectoryClassification,
    EntryPoint,
    InfrastructureKind,
    PlanFileKind,
)

logger = logging.getLogger(__name__)


# --- Ignore / walk helpers ------------------------------------------------

# Directories we never recurse into. Covers build output, venvs, caches, VCS,
# common node/python/rust/go build dirs. Kept as a frozenset for O(1) lookup.
IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tailtest",
        ".venv",
        "venv",
        "env",
        ".env",
        "node_modules",
        "dist",
        "build",
        "target",
        "out",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".pyright",
        ".tox",
        ".nox",
        ".next",
        ".nuxt",
        ".svelte-kit",
        ".cache",
        ".parcel-cache",
        "coverage",
        "htmlcov",
        ".coverage",
        ".idea",
        ".vscode",
        "vendor",  # PHP / Go
        "Pods",  # Swift/ObjC
    }
)

# Max files we walk before bailing out to a partial scan. Keeps the shallow
# scan under 5 seconds on real-world monorepos per ADR 0010.
MAX_FILES_WALKED = 10_000


def walk_project(root: Path) -> tuple[list[Path], bool]:
    """Walk the project tree, skipping ignored dirs.

    Returns (files, hit_ceiling) — the second flag is True if the scan
    stopped early at MAX_FILES_WALKED.
    """
    files: list[Path] = []
    stack: list[Path] = [root]
    hit_ceiling = False

    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (OSError, PermissionError):
            continue

        for entry in entries:
            if entry.is_symlink():
                continue  # Skip symlinks to avoid cycles
            if entry.name in IGNORED_DIRS:
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                files.append(entry)
                if len(files) >= MAX_FILES_WALKED:
                    hit_ceiling = True
                    return files, hit_ceiling
    return files, hit_ceiling


# --- Language detection ---------------------------------------------------

# Map file extension → language name. Kept small and high-signal; anything
# not in this map is ignored for primary-language determination.
LANGUAGE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".rb": "ruby",
    ".php": "php",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".cs": "csharp",
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".h": "c-header",
    ".hpp": "cpp-header",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
}


def detect_languages(files: list[Path]) -> tuple[dict[str, int], str | None]:
    """Count files per language and pick a primary."""
    counts: dict[str, int] = {}
    for f in files:
        lang = LANGUAGE_EXTENSIONS.get(f.suffix.lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1

    if not counts:
        return counts, None

    primary = max(counts.items(), key=lambda kv: kv[1])[0]
    return counts, primary


# --- Framework detection --------------------------------------------------

# Map dependency name → (framework name, category). Only the high-signal
# frameworks we care about for vibe-coded AI projects. Phase 2+ can expand.
PYTHON_FRAMEWORK_SIGNATURES: dict[str, tuple[str, str]] = {
    "anthropic": ("anthropic-sdk", "agent"),
    "openai": ("openai-sdk", "agent"),
    "langchain": ("langchain", "agent"),
    "langchain-core": ("langchain", "agent"),
    "langgraph": ("langgraph", "agent"),
    "crewai": ("crewai", "agent"),
    "llama-index": ("llamaindex", "agent"),
    "llama_index": ("llamaindex", "agent"),
    "instructor": ("instructor", "agent"),
    "pydantic-ai": ("pydantic-ai", "agent"),
    "claude-agent-sdk": ("claude-agent-sdk", "agent"),
    "litellm": ("litellm", "agent"),
    "fastapi": ("fastapi", "web"),
    "django": ("django", "web"),
    "flask": ("flask", "web"),
    "starlette": ("starlette", "web"),
    "pytest": ("pytest", "test"),
    "numpy": ("numpy", "ml"),
    "pandas": ("pandas", "ml"),
    "torch": ("pytorch", "ml"),
    "tensorflow": ("tensorflow", "ml"),
}

JS_FRAMEWORK_SIGNATURES: dict[str, tuple[str, str]] = {
    "@anthropic-ai/sdk": ("anthropic-sdk", "agent"),
    "openai": ("openai-sdk", "agent"),
    "ai": ("vercel-ai-sdk", "agent"),
    "@langchain/core": ("langchain", "agent"),
    "langchain": ("langchain", "agent"),
    "@mariozechner/pi-ai": ("pi-ai", "agent"),
    "@mariozechner/pi-coding-agent": ("pi-coding-agent", "agent"),
    "next": ("nextjs", "web"),
    "react": ("react", "web"),
    "vue": ("vue", "web"),
    "svelte": ("svelte", "web"),
    "@sveltejs/kit": ("sveltekit", "web"),
    "express": ("express", "web"),
    "@nestjs/core": ("nestjs", "web"),
    "fastify": ("fastify", "web"),
    "vitest": ("vitest", "test"),
    "jest": ("jest", "test"),
    "@playwright/test": ("playwright", "test"),
}


RUST_FRAMEWORK_SIGNATURES: dict[str, tuple[str, str]] = {
    "async-openai": ("async-openai", "agent"),
    "anthropic": ("anthropic-rs", "agent"),
    "llm": ("llm", "agent"),
    "rig-core": ("rig", "agent"),
    "openai": ("openai-rs", "agent"),
    "langchain-rust": ("langchain-rust", "agent"),
}


def detect_frameworks(root: Path) -> list[DetectedFramework]:
    """Parse manifest files and return the matched frameworks."""
    results: list[DetectedFramework] = []
    results.extend(_detect_python_frameworks(root))
    results.extend(_detect_js_frameworks(root))
    results.extend(_detect_rust_frameworks(root))
    return results


def _detect_python_frameworks(root: Path) -> list[DetectedFramework]:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return []
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.debug("Failed to parse pyproject.toml: %s", exc)
        return []

    # Collect dependency names from PEP 621 and Poetry tables.
    dep_strings: list[str] = []
    project_section = data.get("project") or {}
    raw_deps = project_section.get("dependencies") or []
    if isinstance(raw_deps, list):
        dep_strings.extend(str(d) for d in raw_deps)
    optional_deps = project_section.get("optional-dependencies") or {}
    if isinstance(optional_deps, dict):
        for bucket in optional_deps.values():
            if isinstance(bucket, list):
                dep_strings.extend(str(d) for d in bucket)
    poetry_deps = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    if isinstance(poetry_deps, dict):
        dep_strings.extend(poetry_deps.keys())

    # Normalize each dependency string to a bare package name.
    names = {
        re.split(r"[\s<>=!~\[]", s, maxsplit=1)[0].strip().lower().replace("_", "-")
        for s in dep_strings
        if s and not s.startswith("#")
    }

    results: list[DetectedFramework] = []
    seen: set[str] = set()
    for dep in names:
        # Try both the normalized and underscore forms against the signatures.
        for key in (dep, dep.replace("-", "_")):
            if key in PYTHON_FRAMEWORK_SIGNATURES:
                framework, category = PYTHON_FRAMEWORK_SIGNATURES[key]
                if framework in seen:
                    continue
                seen.add(framework)
                results.append(
                    DetectedFramework(
                        name=framework,
                        confidence=AIConfidence.HIGH,
                        source="pyproject.toml",
                        category=category,
                    )
                )
                break
    return results


def _detect_js_frameworks(root: Path) -> list[DetectedFramework]:
    pkg = root / "package.json"
    if not pkg.exists():
        return []
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Failed to parse package.json: %s", exc)
        return []

    deps: dict[str, str] = {}
    for field in ("dependencies", "devDependencies", "peerDependencies"):
        value = data.get(field) or {}
        if isinstance(value, dict):
            deps.update(value)

    results: list[DetectedFramework] = []
    seen: set[str] = set()
    for dep_name in deps:
        if dep_name in JS_FRAMEWORK_SIGNATURES:
            framework, category = JS_FRAMEWORK_SIGNATURES[dep_name]
            if framework in seen:
                continue
            seen.add(framework)
            results.append(
                DetectedFramework(
                    name=framework,
                    confidence=AIConfidence.HIGH,
                    source="package.json",
                    category=category,
                )
            )
    return results


# Keywords in workspace member crate names that signal an AI agent project.
# Matched case-insensitively against the crate name (not path component).
_RUST_AI_CRATE_NAME_SIGNALS: frozenset[str] = frozenset(
    {"claude", "anthropic", "openai", "llm", "gpt", "gemini", "copilot", "ai-agent"}
)


def _collect_cargo_tomls(root: Path) -> list[Path]:
    """Return all Cargo.toml files reachable from ``root``.

    Handles both explicit member paths (``crates/foo``) and glob patterns
    (``crates/*``) in the workspace ``members`` list.
    """
    cargo_tomls: list[Path] = []
    root_toml = root / "Cargo.toml"
    if not root_toml.exists():
        return cargo_tomls

    cargo_tomls.append(root_toml)
    try:
        data = tomllib.loads(root_toml.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return cargo_tomls

    members = (data.get("workspace") or {}).get("members") or []
    if not isinstance(members, list):
        return cargo_tomls

    for member in members:
        member_str = str(member)
        if "*" in member_str or "?" in member_str:
            # Glob pattern: expand it relative to root.
            for matched in root.glob(member_str):
                candidate = matched / "Cargo.toml"
                if candidate.exists() and candidate not in cargo_tomls:
                    cargo_tomls.append(candidate)
        else:
            candidate = root / member_str / "Cargo.toml"
            if candidate.exists() and candidate not in cargo_tomls:
                cargo_tomls.append(candidate)

    return cargo_tomls


def _detect_rust_frameworks(root: Path) -> list[DetectedFramework]:
    """Scan workspace and crate Cargo.toml files for AI-signal dependencies.

    Two detection strategies:

    1. Dependency names: scan ``[dependencies]`` and ``[dev-dependencies]``
       for known AI framework crate names (``RUST_FRAMEWORK_SIGNATURES``).

    2. Workspace member crate names: if a workspace member crate is named
       using an AI keyword (e.g. ``rusty-claude-cli``, ``mock-anthropic-service``),
       emit an ``agent`` framework signal. This catches projects like claw-code
       that implement their own Anthropic API client rather than importing one.
    """
    cargo_tomls = _collect_cargo_tomls(root)

    results: list[DetectedFramework] = []
    seen: set[str] = set()
    for toml_path in cargo_tomls:
        try:
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            logger.debug("Failed to parse %s: %s", toml_path, exc)
            continue

        # Strategy 1: dependency-name matching.
        dep_names: set[str] = set()
        for section in ("dependencies", "dev-dependencies"):
            section_data = data.get(section) or {}
            if isinstance(section_data, dict):
                dep_names.update(section_data.keys())

        for dep_name in dep_names:
            key = dep_name.lower().replace("_", "-")
            if key in RUST_FRAMEWORK_SIGNATURES:
                framework, category = RUST_FRAMEWORK_SIGNATURES[key]
                if framework in seen:
                    continue
                seen.add(framework)
                results.append(
                    DetectedFramework(
                        name=framework,
                        confidence=AIConfidence.HIGH,
                        source=str(toml_path.name),
                        category=category,
                    )
                )

        # Strategy 2: AI keyword in the crate's own package name.
        pkg_name = (data.get("package") or {}).get("name", "")
        if isinstance(pkg_name, str) and pkg_name:
            norm_name = pkg_name.lower().replace("_", "-")
            for signal in _RUST_AI_CRATE_NAME_SIGNALS:
                if signal in norm_name:
                    framework_key = f"crate:{pkg_name}"
                    if framework_key not in seen:
                        seen.add(framework_key)
                        results.append(
                            DetectedFramework(
                                name=pkg_name,
                                confidence=AIConfidence.MEDIUM,
                                source=str(toml_path.relative_to(root)
                                           if toml_path.is_relative_to(root)
                                           else toml_path.name),
                                category="agent",
                            )
                        )
                    break  # one signal per crate is enough

    return results


# --- Runner detection -----------------------------------------------------


def detect_runners(root: Path, languages: dict[str, int]) -> list[DetectedRunner]:
    """Deduce which test runners this project has configured."""
    runners: list[DetectedRunner] = []

    # Python → pytest
    if "python" in languages:
        pyproject = root / "pyproject.toml"
        pytest_ini = root / "pytest.ini"
        tests_dir = root / "tests"
        if pyproject.exists():
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
                if ((data.get("tool") or {}).get("pytest") or {}).get("ini_options"):
                    runners.append(
                        DetectedRunner(
                            name="pytest",
                            language="python",
                            config_file=pyproject,
                            tests_dir=tests_dir if tests_dir.is_dir() else None,
                        )
                    )
                    # Fall through — we've added pytest, don't add it twice
            except (OSError, tomllib.TOMLDecodeError):
                pass
        if pytest_ini.exists() and not any(r.name == "pytest" for r in runners):
            runners.append(
                DetectedRunner(
                    name="pytest",
                    language="python",
                    config_file=pytest_ini,
                    tests_dir=tests_dir if tests_dir.is_dir() else None,
                )
            )
        # No config file but there IS a tests/ directory — still likely pytest
        if (
            not any(r.name == "pytest" for r in runners)
            and tests_dir.is_dir()
            and any(tests_dir.rglob("test_*.py"))
        ):
            runners.append(
                DetectedRunner(
                    name="pytest",
                    language="python",
                    config_file=None,
                    tests_dir=tests_dir,
                )
            )

    # Rust → cargo test
    if "rust" in languages:
        cargo_toml = root / "Cargo.toml"
        if cargo_toml.exists():
            import shutil as _shutil

            cargo_available = _shutil.which("cargo") is not None
            runners.append(
                DetectedRunner(
                    name="cargo",
                    language="rust",
                    config_file=cargo_toml if cargo_available else None,
                )
            )

    # TS/JS → jest or vitest
    if "typescript" in languages or "javascript" in languages:
        pkg = root / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            all_deps: dict[str, str] = {}
            for field in ("dependencies", "devDependencies"):
                value = data.get(field) or {}
                if isinstance(value, dict):
                    all_deps.update(value)

            has_vitest = (
                "vitest" in all_deps
                or (root / "vitest.config.ts").exists()
                or (root / "vitest.config.js").exists()
            )
            has_jest = (
                "jest" in all_deps
                or (root / "jest.config.js").exists()
                or (root / "jest.config.ts").exists()
            )
            # Prefer vitest if both are present (modern default)
            if has_vitest:
                runners.append(
                    DetectedRunner(
                        name="vitest",
                        language="typescript" if "typescript" in languages else "javascript",
                        config_file=pkg,
                    )
                )
            elif has_jest:
                runners.append(
                    DetectedRunner(
                        name="jest",
                        language="typescript" if "typescript" in languages else "javascript",
                        config_file=pkg,
                    )
                )
            elif not any(r.language in ("typescript", "javascript") for r in runners):
                # Detect node:test built-in runner via package.json test script
                scripts = data.get("scripts") or {}
                test_script = scripts.get("test") or ""
                if "node" in test_script and "--test" in test_script:
                    runners.append(
                        DetectedRunner(
                            name="node-test",
                            language="typescript" if "typescript" in languages else "javascript",
                            config_file=pkg,
                        )
                    )

    return runners


# --- Infrastructure detection ---------------------------------------------


def detect_infrastructure(root: Path) -> list[DetectedInfrastructure]:
    """Look for Dockerfile, docker-compose, k8s, terraform, CI configs."""
    results: list[DetectedInfrastructure] = []

    checks: list[tuple[InfrastructureKind, list[Path]]] = [
        (InfrastructureKind.DOCKER, [root / "Dockerfile"]),
        (
            InfrastructureKind.DOCKER_COMPOSE,
            [
                root / "docker-compose.yml",
                root / "docker-compose.yaml",
                root / "compose.yaml",
                root / "compose.yml",
            ],
        ),
    ]
    for kind, paths in checks:
        for path in paths:
            if path.exists():
                results.append(DetectedInfrastructure(kind=kind, file=path))
                break

    # Kubernetes: look for a k8s/ or kubernetes/ directory
    for dirname in ("k8s", "kubernetes"):
        p = root / dirname
        if p.is_dir():
            results.append(DetectedInfrastructure(kind=InfrastructureKind.KUBERNETES, file=p))

    # Terraform: any *.tf file at root or in terraform/ or infra/
    for candidate in (root, root / "terraform", root / "infra"):
        if candidate.is_dir():
            tf_files = list(candidate.glob("*.tf"))
            if tf_files:
                results.append(
                    DetectedInfrastructure(kind=InfrastructureKind.TERRAFORM, file=tf_files[0])
                )
                break

    # CI: .github/workflows/*.yml, .gitlab-ci.yml, .circleci/config.yml, Jenkinsfile
    ci_candidates = [
        root / ".github" / "workflows",
        root / ".gitlab-ci.yml",
        root / ".circleci" / "config.yml",
        root / "Jenkinsfile",
    ]
    for candidate in ci_candidates:
        if candidate.exists():
            if candidate.is_dir():
                workflows = list(candidate.glob("*.yml")) + list(candidate.glob("*.yaml"))
                if workflows:
                    results.append(
                        DetectedInfrastructure(kind=InfrastructureKind.CI, file=workflows[0])
                    )
                    break
            else:
                results.append(DetectedInfrastructure(kind=InfrastructureKind.CI, file=candidate))
                break

    # Env config: .env* files at root
    env_files = sorted(root.glob(".env*"))
    if env_files:
        results.append(
            DetectedInfrastructure(
                kind=InfrastructureKind.ENV_CONFIG,
                file=env_files[0],
            )
        )

    return results


# --- Plan file detection --------------------------------------------------

# Filename (lowercase for comparison) → PlanFileKind. Looked up against
# directory entries at the project root and under docs/.
PLAN_FILE_NAMES: dict[str, PlanFileKind] = {
    "claude.md": PlanFileKind.CLAUDE_CODE_INSTRUCTIONS,
    "agents.md": PlanFileKind.AGENT_INVENTORY,
    "roadmap.md": PlanFileKind.ROADMAP,
    "readme.md": PlanFileKind.README,
    "readme.rst": PlanFileKind.README,
    "plan.md": PlanFileKind.PROJECT_PLAN,
    ".cursorrules": PlanFileKind.CURSOR_RULES,
}


def detect_plan_files(root: Path) -> list[DetectedPlanFile]:
    """Find CLAUDE.md, AGENTS.md, README, ROADMAP, .cursor/, .claude/, etc."""
    results: list[DetectedPlanFile] = []

    # Files at root
    try:
        for entry in root.iterdir():
            if entry.is_file():
                kind = PLAN_FILE_NAMES.get(entry.name.lower())
                if kind is not None:
                    results.append(DetectedPlanFile(path=entry, kind=kind))
    except OSError:
        pass

    # docs/ subdirectory
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for name in ("plan.md", "PLAN.md"):
            p = docs_dir / name
            if p.exists():
                results.append(DetectedPlanFile(path=p, kind=PlanFileKind.PROJECT_PLAN))

    # Dot-directory conventions
    if (root / ".cursor").is_dir():
        results.append(DetectedPlanFile(path=root / ".cursor", kind=PlanFileKind.CURSOR_RULES))
    if (root / ".claude").is_dir():
        results.append(DetectedPlanFile(path=root / ".claude", kind=PlanFileKind.CLAUDE_CONFIG))

    return results


# --- Directory classification ---------------------------------------------

SOURCE_DIR_NAMES = {"src", "lib", "app", "pkg", "apps", "packages"}
TEST_DIR_NAMES = {"tests", "test", "__tests__", "spec", "specs"}
DOCS_DIR_NAMES = {"docs", "doc", "site", "documentation"}
EXAMPLES_DIR_NAMES = {"examples", "example", "samples", "sample"}
GENERATED_DIR_NAMES = {"dist", "build", "target", "out", ".next", ".nuxt", "htmlcov"}


def classify_directories(root: Path) -> DirectoryClassification:
    """Classify top-level directories into source/tests/docs/etc. buckets."""
    result = DirectoryClassification()
    try:
        entries = list(root.iterdir())
    except OSError:
        return result

    for entry in entries:
        if not entry.is_dir():
            continue
        name = entry.name.lower()
        # Semantic classifications first — `dist`, `build`, etc. are
        # both "generated" and "ignored by walker", but generated wins
        # for display because it's the more informative label.
        if name in SOURCE_DIR_NAMES:
            result.source.append(entry)
        elif name in TEST_DIR_NAMES:
            result.tests.append(entry)
        elif name in DOCS_DIR_NAMES:
            result.docs.append(entry)
        elif name in EXAMPLES_DIR_NAMES:
            result.examples.append(entry)
        elif name in GENERATED_DIR_NAMES:
            result.generated.append(entry)
        elif entry.name in IGNORED_DIRS:
            result.ignored.append(entry)
    return result


# --- AI surface detection -------------------------------------------------

# Frameworks whose presence strongly suggests an agent (multi-turn, tool use).
_AGENT_FRAMEWORK_NAMES: frozenset[str] = frozenset(
    {
        "langchain",
        "langgraph",
        "crewai",
        "llamaindex",
        "pydantic-ai",
        "claude-agent-sdk",
        "vercel-ai-sdk",
        "litellm",
        "pi-ai",
        "pi-coding-agent",
    }
)

# Frameworks that are SDKs you could use for either utility or agent work.
_SDK_FRAMEWORK_NAMES: frozenset[str] = frozenset(
    {
        "anthropic-sdk",
        "openai-sdk",
        "instructor",
    }
)

# Import / import-from patterns across Python and JS/TS
_AI_IMPORT_PATTERNS = [
    re.compile(r"(?:from|import)\s+anthropic(?:\s|\b|\.)"),
    re.compile(r"(?:from|import)\s+openai(?:\s|\b|\.)"),
    re.compile(r"(?:from|import)\s+langchain(?:\s|\b|\.)"),
    re.compile(r"(?:from|import)\s+langgraph(?:\s|\b|\.)"),
    re.compile(r"(?:from|import)\s+crewai(?:\s|\b|\.)"),
    re.compile(r"(?:from|import)\s+litellm(?:\s|\b|\.)"),
    re.compile(r"from\s+['\"]@anthropic-ai/sdk['\"]"),
    re.compile(r"from\s+['\"]openai['\"]"),
    re.compile(r"from\s+['\"]@langchain"),
    re.compile(r"from\s+['\"]ai['\"]"),  # Vercel AI SDK
    re.compile(r'from\s+[\'"]@mariozechner/'),  # pi-ai family
]

_SYSTEM_PROMPT_PATTERNS = [
    re.compile(r'"""\s*You are\s', re.IGNORECASE),
    re.compile(r"'''\s*You are\s", re.IGNORECASE),
    re.compile(r'system_prompt\s*[:=]\s*["\']', re.IGNORECASE),
    re.compile(r'SYSTEM_PROMPT\s*[:=]\s*["\']'),
]

# Weak signal: subprocess calls that reference the claude CLI binary.
# Matches patterns like subprocess.run(["claude", ...]),
# asyncio.create_subprocess_exec("claude", ...), etc.
# This is intentionally broad -- a false positive here is low cost.
_SUBPROCESS_CLAUDE_PATTERNS = [
    re.compile(r'subprocess\.[a-z_]+\s*\([^)]*["\']claude["\']', re.IGNORECASE),
    re.compile(r'create_subprocess_exec\s*\(\s*["\']claude["\']', re.IGNORECASE),
    re.compile(r'Popen\s*\(\s*\[["\']claude["\']', re.IGNORECASE),
]


def detect_ai_surface(
    root: Path,
    files: list[Path],
    frameworks: list[DetectedFramework],
) -> tuple[AISurface, AIConfidence, list[str]]:
    """Determine ai_surface (none / utility / agent / framework) + confidence.

    Uses three signal sources:
    1. Detected frameworks (strongest signal — manifest-declared deps)
    2. Import lines in source files (medium signal — only inspects source dirs)
    3. System-prompt string literals (weak signal — cosmetic but telling)
    """
    signals: list[str] = []

    # Signal 1: strong agent frameworks -- matched by name against the known
    # set OR by the framework's own category="agent" declaration. The second
    # path catches dynamically-detected Rust crate names (e.g. claw-code's
    # "rusty-claude-cli" and "mock-anthropic-service") that cannot be in the
    # static name set because they are project-specific.
    agent_frameworks: set[str] = {
        f.name
        for f in frameworks
        if f.name in _AGENT_FRAMEWORK_NAMES or f.category == "agent"
    }
    if agent_frameworks:
        signals.extend(f"framework:{name}" for name in sorted(agent_frameworks))

    # Signal 2: SDK frameworks (could be utility or agent)
    sdk_frameworks: set[str] = {
        f.name
        for f in frameworks
        if f.name in _SDK_FRAMEWORK_NAMES or f.category == "sdk"
    }
    if sdk_frameworks:
        signals.extend(f"sdk:{name}" for name in sorted(sdk_frameworks))

    # Signal 3: import lines in a bounded sample of source files
    source_files = [f for f in files if f.suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs"}]
    # Limit: inspect at most 100 source files to stay under the scan budget.
    source_files = source_files[:100]
    import_hits: set[str] = set()
    system_prompt_hits = 0
    for f in source_files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in _AI_IMPORT_PATTERNS:
            if pattern.search(content):
                import_hits.add(f.name)
                break
        for pattern in _SYSTEM_PROMPT_PATTERNS:
            if pattern.search(content):
                system_prompt_hits += 1
                break

    if import_hits:
        signals.append(f"imports_in:{len(import_hits)}_files")
    if system_prompt_hits > 0:
        signals.append(f"system_prompts:{system_prompt_hits}")

    # Weak signal: subprocess calls invoking the claude CLI binary.
    # Only checked when we have no strong import-based evidence already.
    subprocess_claude_hits = 0
    if not import_hits and not agent_frameworks and not sdk_frameworks:
        for f in source_files:
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for pattern in _SUBPROCESS_CLAUDE_PATTERNS:
                if pattern.search(content):
                    subprocess_claude_hits += 1
                    break
        if subprocess_claude_hits > 0:
            signals.append(f"subprocess_claude:{subprocess_claude_hits}_files")

    # Verdict logic:
    if agent_frameworks:
        return AISurface.AGENT, AIConfidence.HIGH, signals
    if sdk_frameworks and (import_hits or system_prompt_hits >= 2):
        return AISurface.AGENT, AIConfidence.MEDIUM, signals
    if sdk_frameworks and import_hits:
        return AISurface.UTILITY, AIConfidence.HIGH, signals
    if sdk_frameworks:
        return AISurface.UTILITY, AIConfidence.MEDIUM, signals
    if import_hits:
        # Imports without a declared dep -- unusual but possible (installed globally?)
        return AISurface.UTILITY, AIConfidence.LOW, signals
    if subprocess_claude_hits > 0:
        # Weak signal: project shells out to the claude CLI without importing an SDK.
        # Could be a vibe-coded orchestrator or a wrapper script. Low confidence.
        return AISurface.UTILITY, AIConfidence.LOW, signals
    return AISurface.NONE, AIConfidence.HIGH, signals


# --- Vibe-coded heuristic -------------------------------------------------


def compute_likely_vibe_coded(
    plan_files: list[DetectedPlanFile],
) -> tuple[bool, list[str]]:
    """Cheap filesystem check per the 2026-04-09 vibe-coded-repos survey.

    Signals: presence of CLAUDE.md, AGENTS.md, .claude/ dir, or .cursor dir.
    Any single signal is enough to flag. Signals are recorded for
    traceability.
    """
    strong_kinds = {
        PlanFileKind.CLAUDE_CODE_INSTRUCTIONS,
        PlanFileKind.AGENT_INVENTORY,
        PlanFileKind.CLAUDE_CONFIG,
        PlanFileKind.CURSOR_RULES,
    }
    signals = [f"{p.kind.value}@{p.path.name}" for p in plan_files if p.kind in strong_kinds]
    return (len(signals) > 0, signals)


# --- Content hash ---------------------------------------------------------

# Files whose content changes indicate a structural project shift. Used for
# cache invalidation: a shallow scan is valid as long as none of these files
# changed since the profile was written.
_STRUCTURAL_INDICATOR_FILES = [
    "pyproject.toml",
    "requirements.txt",
    "poetry.lock",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "Gemfile",
    "Gemfile.lock",
    "composer.json",
    "composer.lock",
    "Dockerfile",
    "docker-compose.yml",
    "CLAUDE.md",
    "AGENTS.md",
]


def compute_content_hash(root: Path) -> str:
    """Compute a deterministic hash of structural indicator files.

    Used for cache invalidation. Missing files are represented as empty bytes
    in the hash so that adding a new manifest invalidates the cache cleanly.
    """
    h = hashlib.sha256()
    for name in sorted(_STRUCTURAL_INDICATOR_FILES):
        path = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\x00")
        if path.exists() and path.is_file():
            with contextlib.suppress(OSError):
                h.update(path.read_bytes())
        h.update(b"\x01")
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Phase 6 Task 6.3 -- agent entry-point detection
# ---------------------------------------------------------------------------

# Python patterns (text-based, no AST to keep the scanner fast and dependency-free)
_PY_ENTRY_PATTERNS: list[tuple[re.Pattern[str], str, str | None]] = [
    # @agent_test decorator immediately before a function
    (re.compile(r"@agent_test\s+def\s+(\w+)\s*\("), "high", None),
    # top-level main() in a file under agents/
    (re.compile(r"^def\s+(main)\s*\(", re.MULTILINE), "medium", None),
    # invoke() method on a class
    (re.compile(r"def\s+(invoke)\s*\(\s*self"), "medium", None),
    # run_agent() or run() at top level
    (re.compile(r"^def\s+(run(?:_agent)?)\s*\(", re.MULTILINE), "low", None),
]
# Anthropic/OpenAI client construction anywhere in the file
_PY_CLIENT_PATTERN = re.compile(
    r"(?:Anthropic|AsyncAnthropic|OpenAI|AsyncOpenAI)\s*\(", re.MULTILINE
)

# TypeScript/JavaScript patterns
_TS_ENTRY_PATTERNS: list[tuple[re.Pattern[str], str, str | None]] = [
    # export default function ...
    (re.compile(r"export\s+default\s+(?:async\s+)?function\s+(\w+)"), "high", None),
    # Vercel AI SDK: streamText / generateText
    (re.compile(r"(?:streamText|generateText|createAI)\s*\("), "high", "vercel-ai-sdk"),
    # UserMessage or string → Promise<...>
    (re.compile(r"async\s+function\s+(\w+)\s*\(\s*\w+\s*:\s*(?:string|UserMessage)"), "medium", None),
    # export async function named run/invoke/handle
    (re.compile(r"export\s+(?:async\s+)?function\s+(run|invoke|handle)\s*\("), "medium", None),
]

# Rust patterns
_RS_ENTRY_PATTERNS: list[tuple[re.Pattern[str], str, str | None]] = [
    # async fn that uses async_openai or anthropic crate
    (re.compile(r"pub\s+async\s+fn\s+(\w+)\s*\("), "high", None),
    (re.compile(r"async\s+fn\s+(main|run|invoke|handle)\s*\("), "medium", None),
]
_RS_CLIENT_PATTERN = re.compile(r"async_openai|anthropic_sdk|anthropic::", re.MULTILINE)

_IGNORED_FOR_EP = frozenset({"node_modules", ".git", "__pycache__", "target", ".tailtest"})


def detect_entry_points(
    root: Path,
    files: list[Path],
    primary_language: str | None,
    *,
    config_path: Path | None = None,
) -> list[EntryPoint]:
    """Detect agent entry points in the project.

    Config-declared entry points (from ``.tailtest/config.yaml`` or
    ``config_path``) take precedence over auto-detected ones.

    Args:
        root: Project root directory.
        files: Pre-walked file list from the scanner.
        primary_language: Primary language string from the scanner (e.g. "python").
        config_path: Path to the tailtest config YAML. Defaults to
            ``<root>/.tailtest/config.yaml``.

    Returns:
        A list of ``EntryPoint`` objects, deduplicated by (file, function).
    """
    cfg = config_path or root / ".tailtest" / "config.yaml"
    declared = _load_declared_entry_points(cfg, root)
    if declared:
        return declared

    return _auto_detect_entry_points(root, files, primary_language)


def _load_declared_entry_points(config_path: Path, root: Path) -> list[EntryPoint]:
    """Load entry points from .tailtest/config.yaml agent.entry_points section."""
    if not config_path.exists():
        return []
    try:
        import yaml  # type: ignore[import-untyped]

        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    declared = (raw or {}).get("agent", {}).get("entry_points", [])
    if not declared:
        return []

    results: list[EntryPoint] = []
    for ep in declared:
        if not isinstance(ep, dict):
            continue
        file_path = ep.get("file")
        function = ep.get("function", "")
        if not file_path:
            continue
        full_path = root / file_path
        language = _language_from_path(Path(file_path))
        results.append(
            EntryPoint(
                file=full_path,
                function=function,
                language=language,
                confidence="high",  # config-declared = operator certainty
                framework=ep.get("framework"),
            )
        )
    return results


def _auto_detect_entry_points(
    root: Path,
    files: list[Path],
    primary_language: str | None,
) -> list[EntryPoint]:
    results: list[EntryPoint] = []
    seen: set[tuple[Path, str]] = set()

    for path in files:
        if any(part in _IGNORED_FOR_EP for part in path.parts):
            continue
        suffix = path.suffix.lower()
        lang = _language_from_suffix(suffix)
        if not lang:
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        eps = _detect_py(path, text, root) if lang == "python" else []
        if lang in {"typescript", "javascript"}:
            eps = _detect_ts(path, text, root)
        elif lang == "rust":
            eps = _detect_rs(path, text, root)

        for ep in eps:
            key = (ep.file, ep.function)
            if key not in seen:
                seen.add(key)
                results.append(ep)

    return results


def _detect_py(path: Path, text: str, root: Path) -> list[EntryPoint]:
    results: list[EntryPoint] = []
    is_agents_dir = "agents" in path.parts or path.stem.startswith("agent")
    has_client = bool(_PY_CLIENT_PATTERN.search(text))

    for pattern, raw_confidence, framework in _PY_ENTRY_PATTERNS:
        for m in pattern.finditer(text):
            func = m.group(1) if m.lastindex else "main"
            # Boost confidence if file is in agents/ or imports client
            confidence = raw_confidence
            if raw_confidence == "low" and (is_agents_dir or has_client):
                confidence = "medium"
            if raw_confidence == "medium" and has_client:
                confidence = "high"
            results.append(
                EntryPoint(
                    file=path,
                    function=func,
                    language="python",
                    confidence=confidence,
                    framework=framework,
                )
            )
    return results


def _detect_ts(path: Path, text: str, root: Path) -> list[EntryPoint]:
    results: list[EntryPoint] = []
    is_agents_dir = "agents" in path.parts

    for pattern, raw_confidence, framework in _TS_ENTRY_PATTERNS:
        for m in pattern.finditer(text):
            func = m.group(1) if m.lastindex and m.lastindex >= 1 else "default"
            try:
                func = m.group(1)
            except IndexError:
                func = "default"
            confidence = raw_confidence
            if raw_confidence == "medium" and is_agents_dir:
                confidence = "high"
            results.append(
                EntryPoint(
                    file=path,
                    function=func,
                    language="typescript",
                    confidence=confidence,
                    framework=framework,
                )
            )
    return results


def _detect_rs(path: Path, text: str, root: Path) -> list[EntryPoint]:
    results: list[EntryPoint] = []
    has_client = bool(_RS_CLIENT_PATTERN.search(text))
    if not has_client:
        return results  # only flag Rust files that use an LLM client

    for pattern, raw_confidence, framework in _RS_ENTRY_PATTERNS:
        for m in pattern.finditer(text):
            func = m.group(1) if m.lastindex else "run"
            results.append(
                EntryPoint(
                    file=path,
                    function=func,
                    language="rust",
                    confidence=raw_confidence,
                    framework=framework,
                )
            )
    return results


def _language_from_path(path: Path) -> str:
    return _language_from_suffix(path.suffix.lower()) or "unknown"


def _language_from_suffix(suffix: str) -> str | None:
    return {
        ".py": "python",
        ".pyi": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".rs": "rust",
    }.get(suffix)
