from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from core.domain.directory import DirectoryUser


@dataclass(frozen=True)
class FakeDirectoryProvider:
    users: tuple[DirectoryUser, ...] = (
        DirectoryUser(
            tenant_id="demo",
            external_id="U1001",
            display_name="Asha Rao",
            email="asha@example.com",
            handle="asha",
            avatar_url=None,
            title="Engineering Manager",
            source="fake",
            synced_at=datetime(2026, 1, 1, tzinfo=UTC),
            metadata={"location": "remote"},
        ),
        DirectoryUser(
            tenant_id="demo",
            external_id="U1002",
            display_name="Liam Chen",
            email="liam@example.com",
            handle="liam",
            avatar_url=None,
            title="Platform Engineer",
            source="fake",
            synced_at=datetime(2026, 1, 1, tzinfo=UTC),
            metadata={"location": "remote"},
        ),
        DirectoryUser(
            tenant_id="demo",
            external_id="U1003",
            display_name="Mina Patel",
            email="mina@example.com",
            handle="mina",
            avatar_url=None,
            title="Product Owner",
            source="fake",
            synced_at=datetime(2026, 1, 1, tzinfo=UTC),
            metadata={"location": "hybrid"},
        ),
    )

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
