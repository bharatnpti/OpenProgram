from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CHECKED_PACKAGES = (ROOT / "backend/core", ROOT / "backend/api")
FORBIDDEN_IMPORTS = (
    "from infra.adapters",
    "import infra.adapters",
    "from temporalio",
    "import temporalio",
    "from psycopg",
    "import psycopg",
    "from redis",
    "import redis",
    "from httpx",
    "import httpx",
    "from openai",
    "import openai",
    "from google.generativeai",
    "import google.generativeai",
)
FORBIDDEN_PROVIDER_TERMS = (
    "Slack",
    "slack",
    "Temporal",
    "temporal",
    "LiteLlm",
    "litellm",
    "Langfuse",
    "langfuse",
    "Psycopg",
    "psycopg",
)


def test_core_and_api_do_not_import_provider_sdks_or_adapters() -> None:
    violations: list[str] = []
    for path in _python_files():
        content = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_IMPORTS:
            if forbidden in content:
                violations.append(f"{path.relative_to(ROOT)} imports {forbidden}")
    assert violations == []


def test_core_and_api_do_not_name_concrete_providers() -> None:
    violations: list[str] = []
    for path in _python_files():
        content = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_PROVIDER_TERMS:
            if forbidden in content:
                violations.append(f"{path.relative_to(ROOT)} contains {forbidden}")
    assert violations == []


def _python_files() -> list[Path]:
    return [
        path
        for package in CHECKED_PACKAGES
        for path in package.rglob("*.py")
        if path.name != "__init__.py"
    ]
