from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from core.domain.directory import DirectoryUser


@dataclass(frozen=True)
class MockSlackDirectoryProvider:
    async def fetch_users(self, tenant_id: str) -> list[DirectoryUser]:
        synced_at = datetime(2026, 1, 1, tzinfo=UTC)
        return [
            DirectoryUser(
                tenant_id=tenant_id,
                external_id="U1001",
                display_name="Asha Rao",
                email="asha@example.com",
                handle="asha",
                title="Engineering Manager",
                source="mock_slack",
                synced_at=synced_at,
                metadata={"location": "remote", "slack_id": "U1001"},
            ),
            DirectoryUser(
                tenant_id=tenant_id,
                external_id="U1002",
                display_name="Liam Chen",
                email="liam@example.com",
                handle="liam",
                title="Platform Engineer",
                source="mock_slack",
                synced_at=synced_at,
                metadata={"location": "remote", "slack_id": "U1002"},
            ),
            DirectoryUser(
                tenant_id=tenant_id,
                external_id="U1003",
                display_name="Mina Patel",
                email="mina@example.com",
                handle="mina",
                title="Product Owner",
                source="mock_slack",
                synced_at=synced_at,
                metadata={"location": "hybrid", "slack_id": "U1003"},
            ),
        ]
