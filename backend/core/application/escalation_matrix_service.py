"""Each project's escalation matrix: set by an admin, inherited from the tenant's.

A project uses its own matrix when it has one, else the tenant's, else the
default. Saving checks that the project and every member it names exist, so a
report never escalates to someone OpenProgram cannot name.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

from core.domain.errors import GraphNotFound
from core.domain.escalation_matrix import (
    TENANT_SCOPE,
    ContactSource,
    EscalationMatrix,
    EscalationMatrixError,
    default_matrix,
    validated_matrix,
)
from core.domain.graph import NodeKind
from core.ports.escalation_matrix import EscalationMatrixRepository
from core.ports.repositories import GraphRepository


class MatrixSource(StrEnum):
    #: The project's own matrix.
    PROJECT = "project"
    #: The tenant's matrix, which projects without one use.
    TENANT = "tenant"
    #: Nothing stored: the default.
    DEFAULT = "default"


@dataclass(frozen=True, kw_only=True)
class MatrixView:
    matrix: EscalationMatrix
    source: MatrixSource


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class EscalationMatrixService:
    def __init__(
        self,
        *,
        repository: EscalationMatrixRepository,
        graph_repository: GraphRepository,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._matrices = repository
        self._graph = graph_repository
        self._clock = clock

    async def matrix_for(self, tenant_id: str, project_id: str) -> MatrixView:
        """The matrix a project's asks escalate by."""
        if project_id != TENANT_SCOPE:
            own = await self._matrices.get(tenant_id, project_id)
            if own is not None:
                return MatrixView(matrix=own, source=MatrixSource.PROJECT)
        tenant = await self._matrices.get(tenant_id, TENANT_SCOPE)
        if tenant is not None:
            return MatrixView(
                matrix=replace(tenant, project_id=project_id),
                source=MatrixSource.TENANT,
            )
        return MatrixView(matrix=default_matrix(tenant_id, project_id), source=MatrixSource.DEFAULT)

    async def stored(self, tenant_id: str) -> list[EscalationMatrix]:
        return sorted(await self._matrices.list(tenant_id), key=lambda item: item.project_id)

    async def save(self, matrix: EscalationMatrix, *, actor: str) -> EscalationMatrix:
        checked = validated_matrix(matrix)
        if checked.project_id != TENANT_SCOPE:
            project = await self._graph.get_node(checked.tenant_id, checked.project_id)
            if project is None or project.kind is not NodeKind.PROJECT:
                raise GraphNotFound(f"No project {checked.project_id!r}.")
        await self._check_members(checked)
        saved = replace(checked, updated_at=self._clock(), updated_by=actor)
        await self._matrices.save(saved)
        return saved

    async def remove(self, tenant_id: str, project_id: str) -> bool:
        return await self._matrices.delete(tenant_id, project_id)

    async def _check_members(self, matrix: EscalationMatrix) -> None:
        named = {
            level.member_id
            for level in matrix.levels
            if level.source is ContactSource.MEMBER and level.member_id
        }
        if matrix.decision_owner_id:
            named.add(matrix.decision_owner_id)
        if not named:
            return
        members = {
            node.id for node in await self._graph.list_nodes(matrix.tenant_id, NodeKind.DEVELOPER)
        }
        unknown = sorted(named - members)
        if unknown:
            raise EscalationMatrixError(
                "No member " + ", ".join(repr(member) for member in unknown) + "."
            )
