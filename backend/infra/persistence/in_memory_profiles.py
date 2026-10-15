from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date

from core.domain.profiles import PodMemberProfile, ProfileDefinition


@dataclass
class InMemoryProfileRepository:
    _catalog: dict[tuple[str, str], ProfileDefinition] = field(default_factory=dict)
    _rows: dict[tuple[str, str, str, date], PodMemberProfile] = field(default_factory=dict)

    async def list_catalog(self, tenant_id: str) -> list[ProfileDefinition]:
        return [entry for (tenant, _slug), entry in self._catalog.items() if tenant == tenant_id]

    async def save_catalog_entry(
        self, tenant_id: str, entry: ProfileDefinition, *, updated_by: str
    ) -> None:
        self._catalog[(tenant_id, entry.slug)] = replace(entry, customized=True)

    async def list_member_profiles(
        self,
        tenant_id: str,
        *,
        pod_ids: Sequence[str] | None = None,
        member_id: str | None = None,
    ) -> list[PodMemberProfile]:
        wanted = set(pod_ids) if pod_ids is not None else None
        return [
            row
            for (tenant, pod_id, member, _day), row in self._rows.items()
            if tenant == tenant_id
            and (wanted is None or pod_id in wanted)
            and (member_id is None or member == member_id)
        ]

    async def save_member_profile(self, row: PodMemberProfile) -> None:
        self._rows[(row.tenant_id, row.pod_id, row.member_id, row.valid_from)] = row
