from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from core.application.directory_sync_service import DirectorySyncService
from core.domain.directory import DirectoryUser
from infra.adapters.directory.slack import SlackDirectoryProvider
from infra.persistence.postgres_directory import PostgresDirectoryUserRepository
from tests.contract.fakes import FakeDirectoryUserRepository


@dataclass
class _StaticDirectoryProvider:
    users: list[DirectoryUser]

    async def fetch_users(self, tenant_id: str) -> list[DirectoryUser]:
        return [
            DirectoryUser(
                tenant_id=tenant_id,
                external_id=user.external_id,
                display_name=user.display_name,
                email=user.email,
                handle=user.handle,
                avatar_url=user.avatar_url,
                title=user.title,
                is_active=user.is_active,
                source=user.source,
                synced_at=user.synced_at,
                metadata=dict(user.metadata),
            )
            for user in self.users
        ]


@dataclass
class _FakeSlackHttpClient:
    pages: list[dict[str, object]]
    calls: list[str | None]

    async def list_users(self, cursor: str | None = None) -> dict[str, object]:
        self.calls.append(cursor)
        if not self.pages:
            return {"ok": True, "members": [], "response_metadata": {"next_cursor": ""}}
        return self.pages.pop(0)


@dataclass
class _RecordingExecutor:
    calls: list[tuple[str, tuple[object, ...]]]
    fetch_calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)
    fetch_rows: list[dict[str, object]] = field(default_factory=list)

    async def execute(self, query: str, params: tuple[object, ...]) -> object:
        self.calls.append((query, params))
        return object()

    async def fetch(self, query: str, params: tuple[object, ...]) -> list[dict[str, object]]:
        self.fetch_calls.append((query, params))
        return self.fetch_rows

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_RecordingExecutor]:
        yield self


async def test_directory_sync_service_upserts_and_deactivates_missing() -> None:
    repository = FakeDirectoryUserRepository()
    await repository.upsert_users(
        [
            DirectoryUser(
                tenant_id="demo",
                external_id="U1003",
                display_name="Outdated User",
                email="old@example.com",
                handle="old",
                source="slack",
            )
        ]
    )
    service = DirectorySyncService(
        provider=_StaticDirectoryProvider(
            users=[
                DirectoryUser(
                    tenant_id="demo",
                    external_id="U1001",
                    display_name="Asha Rao",
                    email="asha@example.com",
                    handle="asha",
                    source="slack",
                    synced_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
                DirectoryUser(
                    tenant_id="demo",
                    external_id="U1002",
                    display_name="Liam Chen",
                    email="liam@example.com",
                    handle="liam",
                    source="slack",
                    synced_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            ]
        ),
        repository=repository,
    )

    result = await service.sync("demo")

    assert result.tenant_id == "demo"
    assert result.synced_count == 2
    assert result.deactivated_count == 1
    assert [user.external_id for user in await repository.search("demo", "", 10)] == [
        "U1001",
        "U1002",
    ]


async def test_slack_directory_provider_paginates_and_filters() -> None:
    client = _FakeSlackHttpClient(
        pages=[
            {
                "ok": True,
                "members": [
                    {
                        "id": "U1001",
                        "name": "asha",
                        "deleted": False,
                        "is_bot": False,
                        "profile": {
                            "display_name": "Asha Rao",
                            "email": "asha@example.com",
                            "title": "Engineering Manager",
                            "image_192": "https://example.com/asha.png",
                        },
                    },
                    {
                        "id": "B1002",
                        "name": "bot",
                        "deleted": False,
                        "is_bot": True,
                        "profile": {"display_name": "Bot"},
                    },
                ],
                "response_metadata": {"next_cursor": "cursor-2"},
            },
            {
                "ok": True,
                "members": [
                    {
                        "id": "U1002",
                        "name": "liam",
                        "deleted": False,
                        "is_bot": False,
                        "profile": {
                            "real_name": "Liam Chen",
                            "email": "liam@example.com",
                            "title": "Platform Engineer",
                        },
                    },
                    {
                        "id": "U1003",
                        "name": "removed",
                        "deleted": True,
                        "is_bot": False,
                        "profile": {"display_name": "Removed User"},
                    },
                ],
                "response_metadata": {"next_cursor": ""},
            },
        ],
        calls=[],
    )
    provider = SlackDirectoryProvider(http_client=client)

    users = await provider.fetch_users("demo")

    assert client.calls == [None, "cursor-2"]
    assert [user.external_id for user in users] == ["U1001", "U1002"]
    assert users[0].display_name == "Asha Rao"
    assert users[0].avatar_url == "https://example.com/asha.png"
    assert users[1].display_name == "Liam Chen"
    assert users[1].email == "liam@example.com"


async def test_postgres_directory_repository_batches_large_upserts() -> None:
    executor = _RecordingExecutor(calls=[])
    repository = PostgresDirectoryUserRepository(executor)
    users = [
        DirectoryUser(
            tenant_id="demo",
            external_id=f"U{index:04d}",
            display_name=f"User {index}",
            source="slack",
        )
        for index in range(1001)
    ]

    await repository.upsert_users(users)

    assert [len(params) for _, params in executor.calls] == [11000, 11]


async def test_postgres_directory_repository_uses_index_friendly_search_queries() -> None:
    executor = _RecordingExecutor(calls=[])
    repository = PostgresDirectoryUserRepository(executor)

    await repository.search("demo", query="", limit=25, offset=0)
    empty_query, empty_params = executor.fetch_calls[-1]
    assert "ILIKE" not in empty_query
    assert "ORDER BY display_name, external_id" in empty_query
    assert empty_params == ("demo", 25, 0)

    await repository.search("demo", query="asha", limit=25, offset=0)
    search_query, search_params = executor.fetch_calls[-1]
    assert "COALESCE" not in search_query
    assert "handle ILIKE" in search_query
    assert "external_id ILIKE" in search_query
    assert search_params == ("demo", "%asha%", "%asha%", "%asha%", "%asha%", 25, 0)
