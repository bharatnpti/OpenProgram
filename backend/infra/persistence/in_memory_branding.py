from __future__ import annotations

from dataclasses import dataclass, field

from core.domain.branding import TenantLogo


@dataclass
class InMemoryTenantLogoRepository:
    """Tenant logos for memory mode: one per tenant, gone when the process stops."""

    _logos: dict[str, TenantLogo] = field(default_factory=dict)

    async def get_logo(self, tenant_id: str) -> TenantLogo | None:
        return self._logos.get(tenant_id)

    async def save_logo(self, logo: TenantLogo) -> None:
        self._logos[logo.tenant_id] = logo

    async def delete_logo(self, tenant_id: str) -> bool:
        return self._logos.pop(tenant_id, None) is not None
