from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CHECKED_PACKAGES = (ROOT / "backend/core", ROOT / "backend/api")
APPLICATION_PACKAGE = ROOT / "backend/core/application"
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
    "from dbos",
    "import dbos",
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
    "DBOS",
    "dbos",
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


def test_only_writeback_service_calls_issue_tracker_write_back() -> None:
    # Phase 2 unlock: issue-tracker write-back is permitted only through the
    # gated, audited, reversible WriteBackService. Every other application module
    # must stay read-only.
    sanctioned = APPLICATION_PACKAGE / "writeback_service.py"
    violations: list[str] = []
    sanctioned_uses_write_back = False
    for path in APPLICATION_PACKAGE.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        for forbidden in (".transition(", ".add_comment("):
            if forbidden not in content:
                continue
            if path == sanctioned:
                sanctioned_uses_write_back = True
            else:
                violations.append(f"{path.relative_to(ROOT)} calls {forbidden}")
    assert violations == []
    assert sanctioned_uses_write_back


def _python_files() -> list[Path]:
    return [
        path
        for package in CHECKED_PACKAGES
        for path in package.rglob("*.py")
        if path.name != "__init__.py"
    ]
