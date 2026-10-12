from __future__ import annotations

from dataclasses import dataclass, field, replace

from core.domain.baseline import BaselineScopeKind, PlanningBaseline


@dataclass
class InMemoryPlanningBaselineRepository:
    _baselines: list[PlanningBaseline] = field(default_factory=list)

    async def add(self, baseline: PlanningBaseline) -> PlanningBaseline:
        self._baselines = [
            replace(kept, superseded_at=baseline.set_at)
            if kept.tenant_id == baseline.tenant_id
            and kept.scope_kind is baseline.scope_kind
            and kept.scope_id == baseline.scope_id
            and kept.superseded_at is None
            else kept
            for kept in self._baselines
        ]
        stored = replace(baseline, superseded_at=None)
        self._baselines.append(stored)
        return stored

    async def list_for_scope(
        self, tenant_id: str, scope_kind: BaselineScopeKind, scope_id: str
    ) -> list[PlanningBaseline]:
        return sorted(
            (
                baseline
                for baseline in self._baselines
                if baseline.tenant_id == tenant_id
                and baseline.scope_kind is scope_kind
                and baseline.scope_id == scope_id
            ),
            key=lambda baseline: baseline.set_at,
        )

    async def list_for_project(self, tenant_id: str, project_id: str) -> list[PlanningBaseline]:
        return sorted(
            (
                baseline
                for baseline in self._baselines
                if baseline.tenant_id == tenant_id and baseline.project_id == project_id
            ),
            key=lambda baseline: baseline.set_at,
        )
