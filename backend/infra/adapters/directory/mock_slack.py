from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from core.domain.directory import DirectoryUser


@dataclass(frozen=True)
class MockSlackDirectoryProvider:
    """A fixed roster standing in for a real chat-workspace directory.

    Kept deliberately larger than a single pod so the local demo tenant can
    populate manager, product-owner, and executive views instead of only the
    one-developer case.
    """

    async def fetch_users(self, tenant_id: str) -> list[DirectoryUser]:
        synced_at = datetime(2026, 1, 1, tzinfo=UTC)
        return [
            DirectoryUser(
                tenant_id=tenant_id,
                external_id="U1001",
                display_name="Asha Rao",
                email="asha.rao@example.com",
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
                email="liam.chen@example.com",
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
                email="mina.patel@example.com",
                handle="mina",
                title="Product Owner",
                source="mock_slack",
                synced_at=synced_at,
                metadata={"location": "hybrid", "slack_id": "U1003"},
            ),
            DirectoryUser(
                tenant_id=tenant_id,
                external_id="U1004",
                display_name="Noah Weber",
                email="noah.weber@example.com",
                handle="noah",
                title="Senior Backend Engineer",
                source="mock_slack",
                synced_at=synced_at,
                metadata={"location": "onsite", "slack_id": "U1004"},
            ),
            DirectoryUser(
                tenant_id=tenant_id,
                external_id="U1005",
                display_name="Zoe Almeida",
                email="zoe.almeida@example.com",
                handle="zoe",
                title="Frontend Engineer",
                source="mock_slack",
                synced_at=synced_at,
                metadata={"location": "remote", "slack_id": "U1005"},
            ),
            DirectoryUser(
                tenant_id=tenant_id,
                external_id="U1006",
                display_name="Ira Novak",
                email="ira.novak@example.com",
                handle="ira",
                title="Scrum Master",
                source="mock_slack",
                synced_at=synced_at,
                metadata={"location": "hybrid", "slack_id": "U1006"},
            ),
            DirectoryUser(
                tenant_id=tenant_id,
                external_id="U1007",
                display_name="Kai Thompson",
                email="kai.thompson@example.com",
                handle="kai",
                title="Backend Engineer",
                source="mock_slack",
                synced_at=synced_at,
                metadata={"location": "remote", "slack_id": "U1007"},
            ),
            DirectoryUser(
                tenant_id=tenant_id,
                external_id="U1008",
                display_name="Omar Haddad",
                email="omar.haddad@example.com",
                handle="omar",
                title="SRE",
                source="mock_slack",
                synced_at=synced_at,
                metadata={"location": "onsite", "slack_id": "U1008"},
            ),
            DirectoryUser(
                tenant_id=tenant_id,
                external_id="U1009",
                display_name="Sofia Bergmann",
                email="sofia.bergmann@example.com",
                handle="sofia",
                title="QA Engineer",
                source="mock_slack",
                synced_at=synced_at,
                metadata={"location": "hybrid", "slack_id": "U1009"},
            ),
            DirectoryUser(
                tenant_id=tenant_id,
                external_id="U1010",
                display_name="Raj Iyer",
                email="raj.iyer@example.com",
                handle="raj",
                title="Data Engineer",
                source="mock_slack",
                synced_at=synced_at,
                metadata={"location": "remote", "slack_id": "U1010"},
            ),
            DirectoryUser(
                tenant_id=tenant_id,
                external_id="U1011",
                display_name="Elena Fischer",
                email="elena.fischer@example.com",
                handle="elena",
                title="Director of Engineering",
                source="mock_slack",
                synced_at=synced_at,
                metadata={"location": "onsite", "slack_id": "U1011"},
            ),
            DirectoryUser(
                tenant_id=tenant_id,
                external_id="U1012",
                display_name="Tom Okafor",
                email="tom.okafor@example.com",
                handle="tom",
                title="Frontend Engineer",
                source="mock_slack",
                synced_at=synced_at,
                metadata={"location": "remote", "slack_id": "U1012"},
            ),
            DirectoryUser(
                tenant_id=tenant_id,
                external_id="U1013",
                display_name="Hana Kobayashi",
                email="hana.kobayashi@example.com",
                handle="hana",
                title="Product Owner",
                source="mock_slack",
                synced_at=synced_at,
                metadata={"location": "hybrid", "slack_id": "U1013"},
            ),
            DirectoryUser(
                tenant_id=tenant_id,
                external_id="U1014",
                display_name="Ben Sorensen",
                email="ben.sorensen@example.com",
                handle="ben",
                title="Scrum Master",
                source="mock_slack",
                synced_at=synced_at,
                metadata={"location": "remote", "slack_id": "U1014"},
            ),
        ]
