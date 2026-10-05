from __future__ import annotations

from dataclasses import dataclass, field

from core.domain.escalation_matrix import EscalationMatrix


@dataclass
class InMemoryEscalationMatrixRepository:
    _matrices: dict[tuple[str, str], EscalationMatrix] = field(default_factory=dict)

    async def get(self, tenant_id: str, project_id: str) -> EscalationMatrix | None:
        return self._matrices.get((tenant_id, project_id))

    async def list(self, tenant_id: str) -> list[EscalationMatrix]:
        return [
            matrix for (tenant, _project), matrix in self._matrices.items() if tenant == tenant_id
        ]

    async def save(self, matrix: EscalationMatrix) -> None:
        self._matrices[(matrix.tenant_id, matrix.project_id)] = matrix

    async def delete(self, tenant_id: str, project_id: str) -> bool:
        return self._matrices.pop((tenant_id, project_id), None) is not None
