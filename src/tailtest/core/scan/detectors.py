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


def detect_frameworks(root: Path) -> list[DetectedFramework]:
    """Parse manifest files and return the matched frameworks."""
    results: list[DetectedFramework] = []
    results.extend(_detect_python_frameworks(root))
    results.extend(_detect_js_frameworks(root))
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
    re.compile(r"from\s+['\"]@anthropic-ai/sdk['\"]"),
    re.compile(r"from\s+['\"]openai['\"]"),
    re.compile(r"from\s+['\"]@langchain"),
    re.compile(r"from\s+['\"]ai['\"]"),  # Vercel AI SDK
]

_SYSTEM_PROMPT_PATTERNS = [
    re.compile(r'"""\s*You are\s', re.IGNORECASE),
    re.compile(r"'''\s*You are\s", re.IGNORECASE),
    re.compile(r'system_prompt\s*[:=]\s*["\']', re.IGNORECASE),
    re.compile(r'SYSTEM_PROMPT\s*[:=]\s*["\']'),
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
    framework_names = {f.name for f in frameworks}

    # Signal 1: strong agent frameworks
    agent_frameworks = framework_names & _AGENT_FRAMEWORK_NAMES
    if agent_frameworks:
        signals.extend(f"framework:{name}" for name in sorted(agent_frameworks))

    # Signal 2: SDK frameworks (could be utility or agent)
    sdk_frameworks = framework_names & _SDK_FRAMEWORK_NAMES
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
        # Imports without a declared dep — unusual but possible (installed globally?)
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
