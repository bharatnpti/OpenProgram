"""The 0030 scrub migration, run for real against SQLite.

Migrations are otherwise only exercised against Postgres in the Docker-backed
integration suite (see ``test_container_backed_phase0``, which runs this one
too). The scrub is a single UPDATE whose only Postgres-specific function is
``strpos``, so registering that function with PostgreSQL's semantics lets the
migration's own ``upgrade()`` run here, statement and all.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "infra/persistence/migrations/versions/0030_scrub_legacy_inferred_summaries.py"
)
_LEGACY = "No confirmed check-in after a nudge. Inferred from context: "
_NEUTRAL = "No confirmed check-in after a nudge. Inferred from recent activity."
# The shape of the stored prompt context, with a request note taken from a reply.
_DUMP = (
    "Developer: Noah\n"
    "Active issue PO-1: Payment intent API (blocked, days_since_update=3)\n"
    "Recent fact for developer/dev-1: source=cross_person_request, "
    "note=ask for the staging password before Friday"
)
_STALE_LEAD = "No confirmed check-in after a nudge. Last known inferred status on 2026-09-29: "
_KEPT_UPDATED_AT = "2026-09-30 08:00:00"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0030", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _strpos(string: str | None, substring: str | None) -> int | None:
    """PostgreSQL's ``strpos``: 1-based position, 0 when absent."""
    if string is None or substring is None:
        return None
    return string.find(substring) + 1


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[sa.Engine]:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'statuses.sqlite'}")

    @sa.event.listens_for(engine, "connect")
    def _register_strpos(dbapi_connection: sqlite3.Connection, _record: object) -> None:
        dbapi_connection.create_function("strpos", 2, _strpos, deterministic=True)

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                CREATE TABLE developer_statuses (
                    tenant_id TEXT NOT NULL,
                    developer_id TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    source TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, developer_id, as_of)
                )
                """
            )
        )
    yield engine
    engine.dispose()


_ROWS = {
    # key: (tenant, developer, as_of, source, stored summary, summary after the scrub)
    "legacy-inferred": (
        "demo",
        "dev-1",
        "2026-09-29",
        "inferred",
        f"{_LEGACY}{_DUMP}",
        _NEUTRAL,
    ),
    "stale-quoting-legacy": (
        "demo",
        "dev-1",
        "2026-09-30",
        "stale",
        f"{_STALE_LEAD}{_LEGACY}{_DUMP}",
        f"{_STALE_LEAD}{_NEUTRAL}",
    ),
    "confirmed-copy-other-tenant": (
        "acme",
        "dev-9",
        "2026-09-29",
        "confirmed",
        f"{_LEGACY}{_DUMP}",
        _NEUTRAL,
    ),
    "current-inferred": (
        "demo",
        "dev-2",
        "2026-09-29",
        "inferred",
        "No confirmed check-in after a nudge. Inferred from 1 active issue: PO-2 Refunds.",
        "No confirmed check-in after a nudge. Inferred from 1 active issue: PO-2 Refunds.",
    ),
    "confirmed": (
        "demo",
        "dev-3",
        "2026-09-29",
        "confirmed",
        "Shipped the refund flow.",
        "Shipped the refund flow.",
    ),
    "same-words-without-the-lead": (
        "demo",
        "dev-4",
        "2026-09-29",
        "confirmed",
        "Inferred from context: the API is done.",
        "Inferred from context: the API is done.",
    ),
}


def _seed(engine: sa.Engine) -> None:
    with engine.begin() as connection:
        for tenant_id, developer_id, as_of, source, summary, _ in _ROWS.values():
            connection.execute(
                sa.text(
                    "INSERT INTO developer_statuses VALUES "
                    "(:tenant_id, :developer_id, :as_of, :source, :summary, :updated_at)"
                ),
                {
                    "tenant_id": tenant_id,
                    "developer_id": developer_id,
                    "as_of": as_of,
                    "source": source,
                    "summary": summary,
                    "updated_at": _KEPT_UPDATED_AT,
                },
            )


def _run(engine: sa.Engine, step: str) -> None:
    migration = _load_migration()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            getattr(migration, step)()


def _stored(engine: sa.Engine) -> dict[tuple[str, str, str], tuple[str, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT tenant_id, developer_id, as_of, summary, updated_at FROM developer_statuses"
            )
        )
        return {(row[0], row[1], row[2]): (row[3], row[4]) for row in rows}


def test_scrub_rewrites_only_the_legacy_prompt_context(engine: sa.Engine) -> None:
    _seed(engine)

    _run(engine, "upgrade")

    stored = _stored(engine)
    for name, (tenant_id, developer_id, as_of, _, before, after) in _ROWS.items():
        summary, updated_at = stored[(tenant_id, developer_id, as_of)]
        assert summary == after, name
        # Rows without the legacy sentence are not written at all.
        assert (updated_at == _KEPT_UPDATED_AT) is (before == after), name
    for summary, _ in stored.values():
        assert "staging password" not in summary
        assert "Recent fact" not in summary


def test_scrub_is_idempotent_and_forward_only(engine: sa.Engine) -> None:
    _seed(engine)
    _run(engine, "upgrade")
    scrubbed = _stored(engine)

    _run(engine, "upgrade")
    assert _stored(engine) == scrubbed

    _run(engine, "downgrade")
    assert _stored(engine) == scrubbed


def test_scrub_targets_the_sentence_the_old_code_wrote() -> None:
    migration = _load_migration()

    assert migration.LEGACY_INFERRED_SENTENCE == _LEGACY
    assert migration.NEUTRAL_INFERRED_SUMMARY == _NEUTRAL
    assert migration.down_revision == "0029_backfill_developer_blockers"
    # No tenant or source filter: every stored row is checked.
    assert "tenant_id" not in migration.SCRUB_SQL
    assert "source" not in migration.SCRUB_SQL
