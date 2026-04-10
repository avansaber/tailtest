"""Import-based SCA: discover Python imports and check them against OSV."""
from __future__ import annotations

import ast
import os
from pathlib import Path


# Map common stdlib-aliased or ambiguous import names to their PyPI package names.
# Only include packages where the import name differs from the PyPI name.
_IMPORT_TO_PYPI: dict[str, str] = {
    # Web frameworks
    "flask": "Flask",
    "django": "Django",
    "fastapi": "fastapi",
    "starlette": "starlette",
    "aiohttp": "aiohttp",
    "tornado": "tornado",
    "bottle": "bottle",
    # Data
    "numpy": "numpy",
    "pandas": "pandas",
    "scipy": "scipy",
    "sklearn": "scikit-learn",
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "matplotlib": "matplotlib",
    # DB / ORM
    "sqlalchemy": "SQLAlchemy",
    "pymongo": "pymongo",
    "redis": "redis",
    "psycopg2": "psycopg2",
    "psycopg": "psycopg",
    "pymysql": "PyMySQL",
    "elasticsearch": "elasticsearch",
    # Auth / crypto
    "jwt": "PyJWT",
    "cryptography": "cryptography",
    "paramiko": "paramiko",
    "nacl": "PyNaCl",
    "bcrypt": "bcrypt",
    # HTTP / networking
    "requests": "requests",
    "httpx": "httpx",
    "urllib3": "urllib3",
    "certifi": "certifi",
    "bs4": "beautifulsoup4",
    "lxml": "lxml",
    # Cloud / infra
    "boto3": "boto3",
    "botocore": "botocore",
    "google": "google-cloud-core",
    "azure": "azure-core",
    # Serialization
    "yaml": "PyYAML",
    "toml": "toml",
    "tomllib": None,  # stdlib in 3.11+
    "msgpack": "msgpack",
    "orjson": "orjson",
    "pydantic": "pydantic",
    # Task queues
    "celery": "celery",
    "dramatiq": "dramatiq",
    "rq": "rq",
    # CLI / config
    "click": "click",
    "typer": "typer",
    "rich": "rich",
    "dotenv": "python-dotenv",
    # Testing
    "pytest": "pytest",
    "hypothesis": "hypothesis",
    # Other common
    "aiofiles": "aiofiles",
    "jinja2": "Jinja2",
    "markupsafe": "MarkupSafe",
    "packaging": "packaging",
    "setuptools": "setuptools",
    "pip": "pip",
    "tqdm": "tqdm",
    "arrow": "arrow",
    "pendulum": "pendulum",
    "loguru": "loguru",
    "structlog": "structlog",
    "sentry_sdk": "sentry-sdk",
    "openai": "openai",
    "anthropic": "anthropic",
    "langchain": "langchain",
    "tiktoken": "tiktoken",
}

# Standard library top-level names to skip
_STDLIB_TOP_LEVEL = frozenset({
    "abc", "ast", "asyncio", "base64", "binascii", "builtins", "calendar",
    "cgi", "cgitb", "chunk", "cmath", "cmd", "code", "codecs", "codeop",
    "colorsys", "compileall", "concurrent", "configparser", "contextlib",
    "contextvars", "copy", "copyreg", "csv", "ctypes", "curses", "dataclasses",
    "datetime", "dbm", "decimal", "difflib", "dis", "doctest", "email",
    "encodings", "enum", "errno", "faulthandler", "fcntl", "filecmp",
    "fileinput", "fnmatch", "fractions", "ftplib", "functools", "gc",
    "getopt", "getpass", "gettext", "glob", "grp", "gzip", "hashlib",
    "heapq", "hmac", "html", "http", "idlelib", "imaplib", "importlib",
    "inspect", "io", "ipaddress", "itertools", "json", "keyword", "lib2to3",
    "linecache", "locale", "logging", "lzma", "mailbox", "math", "mimetypes",
    "mmap", "modulefinder", "multiprocessing", "netrc", "nis", "nntplib",
    "numbers", "operator", "optparse", "os", "ossaudiodev", "pathlib",
    "pickle", "pickletools", "pipes", "pkgutil", "platform", "plistlib",
    "poplib", "posix", "posixpath", "pprint", "profile", "pstats", "pty",
    "pwd", "py_compile", "pyclbr", "pydoc", "queue", "quopri", "random",
    "re", "readline", "reprlib", "resource", "rlcompleter", "runpy",
    "sched", "secrets", "select", "selectors", "shelve", "shlex", "shutil",
    "signal", "site", "smtpd", "smtplib", "sndhdr", "socket", "socketserver",
    "spwd", "sqlite3", "sre_compile", "sre_constants", "sre_parse", "ssl",
    "stat", "statistics", "string", "stringprep", "struct", "subprocess",
    "sunau", "symtable", "sys", "sysconfig", "syslog", "tabnanny", "tarfile",
    "telnetlib", "tempfile", "termios", "test", "textwrap", "threading",
    "time", "timeit", "tkinter", "token", "tokenize", "tomllib", "trace",
    "traceback", "tracemalloc", "tty", "turtle", "turtledemo", "types",
    "typing", "unicodedata", "unittest", "urllib", "uu", "uuid", "venv",
    "warnings", "wave", "weakref", "webbrowser", "wsgiref", "xdrlib",
    "xml", "xmlrpc", "zipapp", "zipfile", "zipimport", "zlib", "zoneinfo",
    # typing extensions
    "typing_extensions", "_thread", "__future__",
})

_SKIP_DIRS = frozenset({
    ".git", ".tox", ".venv", "venv", "env", ".env",
    "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache",
    "dist", "build", ".build", "site-packages",
})


def discover_imports(root: str | Path) -> dict[str, str]:
    """Walk *root* and return ``{import_name: pypi_name}`` for third-party imports.

    Uses AST parsing to find ``import X`` and ``from X import ...`` statements.
    Skips stdlib names and directories that are not source code.
    Only returns packages that have a known PyPI mapping (skips unknown imports
    to avoid false positives).

    Returns a dict mapping the top-level import name -> canonical PyPI package name.
    """
    root = Path(root)
    found: dict[str, str] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in-place
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            filepath = Path(dirpath) / filename
            try:
                source = filepath.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source, filename=str(filepath))
            except SyntaxError:
                continue
            except OSError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_level = alias.name.split(".")[0]
                        _record(top_level, found)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        top_level = node.module.split(".")[0]
                        _record(top_level, found)

    return found


def _record(name: str, found: dict[str, str]) -> None:
    if not name or name in _STDLIB_TOP_LEVEL:
        return
    pypi = _IMPORT_TO_PYPI.get(name)
    if pypi is None:
        return  # Unknown package, skip to avoid false positives
    found[name] = pypi
