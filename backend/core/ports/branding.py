from __future__ import annotations

from typing import Protocol

from core.domain.branding import TenantLogo


class TenantLogoRepository(Protocol):
    """Each tenant's logo, keyed by tenant: a tenant has at most one."""

    async def get_logo(self, tenant_id: str) -> TenantLogo | None: ...

    async def save_logo(self, logo: TenantLogo) -> None:
        """Store the logo, replacing any logo the tenant already has."""
        ...

    async def delete_logo(self, tenant_id: str) -> bool:
        """Remove the tenant's logo. False when the tenant had none."""
        ...
