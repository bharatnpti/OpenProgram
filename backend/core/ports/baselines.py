from __future__ import annotations

from typing import Protocol

from core.domain.baseline import BaselineScopeKind, PlanningBaseline


class PlanningBaselineRepository(Protocol):
    """Every planning baseline, the replaced ones kept."""

    async def add(self, baseline: PlanningBaseline) -> PlanningBaseline:
        """Keep ``baseline`` as its scope's active one, marking the active one before it
        replaced at the new one's ``set_at``, in one transaction."""
        ...

    async def list_for_scope(
        self, tenant_id: str, scope_kind: BaselineScopeKind, scope_id: str
    ) -> list[PlanningBaseline]:
        """The scope's baselines, oldest first."""
        ...

    async def list_for_project(self, tenant_id: str, project_id: str) -> list[PlanningBaseline]:
        """The baselines of the project and of its releases, oldest first."""
        ...
