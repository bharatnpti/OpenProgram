from __future__ import annotations

from typing import Protocol

from core.domain.escalation_matrix import EscalationMatrix


class EscalationMatrixRepository(Protocol):
    async def get(self, tenant_id: str, project_id: str) -> EscalationMatrix | None:
        """The matrix stored for the project, or for the tenant under ``TENANT_SCOPE``."""
        ...

    async def list(self, tenant_id: str) -> list[EscalationMatrix]: ...

    async def save(self, matrix: EscalationMatrix) -> None: ...

    async def delete(self, tenant_id: str, project_id: str) -> bool:
        """Remove a stored matrix. False when there was none."""
        ...
