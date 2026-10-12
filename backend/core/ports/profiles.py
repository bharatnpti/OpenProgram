from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from core.domain.profiles import PodMemberProfile, ProfileDefinition


class ProfileRepository(Protocol):
    """The tenant's catalog changes and each pod membership's dated profile rows."""

    async def list_catalog(self, tenant_id: str) -> list[ProfileDefinition]:
        """The tenant's own entries: changes to shipped ones and additions. None means defaults."""
        ...

    async def save_catalog_entry(
        self, tenant_id: str, entry: ProfileDefinition, *, updated_by: str
    ) -> None: ...

    async def list_member_profiles(
        self,
        tenant_id: str,
        *,
        pod_ids: Sequence[str] | None = None,
        member_id: str | None = None,
    ) -> list[PodMemberProfile]:
        """Every row, history included, for the pods (all when None) and the member (any)."""
        ...

    async def save_member_profile(self, row: PodMemberProfile) -> None:
        """Keep the row; a second change on the same day replaces that day's row."""
        ...
