from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from core.domain.release_readiness import (
    Finding,
    ReadinessAction,
    ReadinessRun,
    ReadinessSettings,
    ReleaseCriterion,
    ScopeRef,
    Suggestion,
    SuggestionStatus,
)


@dataclass
class InMemoryReleaseReadinessRepository:
    _settings: dict[str, ReadinessSettings] = field(default_factory=dict)
    _criteria: dict[tuple[str, str], ReleaseCriterion] = field(default_factory=dict)
    _findings: dict[tuple[str, str], Finding] = field(default_factory=dict)
    _suggestions: dict[tuple[str, str], Suggestion] = field(default_factory=dict)
    _actions: list[ReadinessAction] = field(default_factory=list)
    _runs: dict[tuple[str, str], ReadinessRun] = field(default_factory=dict)

    async def get_settings(self, tenant_id: str) -> ReadinessSettings | None:
        return self._settings.get(tenant_id)

    async def save_settings(self, settings: ReadinessSettings) -> None:
        self._settings[settings.tenant_id] = settings

    async def list_criteria(
        self, tenant_id: str, *, include_deleted: bool = False
    ) -> list[ReleaseCriterion]:
        return sorted(
            (
                item
                for (tenant, _), item in self._criteria.items()
                if tenant == tenant_id and (include_deleted or item.deleted_at is None)
            ),
            key=lambda item: item.name.casefold(),
        )

    async def save_criterion(self, criterion: ReleaseCriterion) -> None:
        self._criteria[(criterion.tenant_id, criterion.criterion_id)] = criterion

    async def list_findings(self, tenant_id: str, scopes: Sequence[ScopeRef]) -> list[Finding]:
        wanted = {scope.key for scope in scopes}
        return [
            item
            for (tenant, _), item in self._findings.items()
            if tenant == tenant_id and item.scope.key in wanted
        ]

    async def get_finding(self, tenant_id: str, finding_id: str) -> Finding | None:
        return self._findings.get((tenant_id, finding_id))

    async def save_finding(self, finding: Finding) -> None:
        for (tenant, finding_id), existing in self._findings.items():
            if (
                tenant == finding.tenant_id
                and finding_id != finding.finding_id
                and existing.criterion_id == finding.criterion_id
                and existing.scope == finding.scope
            ):
                raise ValueError("one finding per criterion and scope")
        self._findings[(finding.tenant_id, finding.finding_id)] = finding

    async def list_suggestions(
        self, tenant_id: str, finding_ids: Sequence[str]
    ) -> list[Suggestion]:
        wanted = set(finding_ids)
        return [
            item
            for (tenant, _), item in self._suggestions.items()
            if tenant == tenant_id and item.finding_id in wanted
        ]

    async def get_suggestion(self, tenant_id: str, suggestion_id: str) -> Suggestion | None:
        return self._suggestions.get((tenant_id, suggestion_id))

    async def save_suggestion(self, suggestion: Suggestion) -> None:
        for (tenant, suggestion_id), existing in self._suggestions.items():
            if (
                tenant == suggestion.tenant_id
                and suggestion_id != suggestion.suggestion_id
                and existing.finding_id == suggestion.finding_id
            ):
                raise ValueError("one draft per finding")
        self._suggestions[(suggestion.tenant_id, suggestion.suggestion_id)] = suggestion

    async def claim_suggestion(
        self, tenant_id: str, suggestion_id: str, *, version: int, at: datetime
    ) -> bool:
        current = self._suggestions.get((tenant_id, suggestion_id))
        if (
            current is None
            or current.status is not SuggestionStatus.OPEN
            or current.version != version
        ):
            return False
        self._suggestions[(tenant_id, suggestion_id)] = replace(
            current, status=SuggestionStatus.CREATING, updated_at=at
        )
        return True

    async def append_action(self, action: ReadinessAction) -> None:
        self._actions.append(action)

    async def list_actions(self, tenant_id: str, finding_id: str) -> list[ReadinessAction]:
        return sorted(
            (
                item
                for item in self._actions
                if item.tenant_id == tenant_id and item.finding_id == finding_id
            ),
            key=lambda item: item.at,
        )

    async def claim_run(self, run: ReadinessRun) -> bool:
        if any(
            tenant == run.tenant_id and item.slot == run.slot
            for (tenant, _), item in self._runs.items()
        ):
            return False
        self._runs[(run.tenant_id, run.run_id)] = run
        return True

    async def finish_run(self, run: ReadinessRun) -> None:
        self._runs[(run.tenant_id, run.run_id)] = run

    async def last_run(self, tenant_id: str) -> ReadinessRun | None:
        finished = [
            item
            for (tenant, _), item in self._runs.items()
            if tenant == tenant_id and item.finished_at is not None
        ]
        return max(finished, key=lambda item: item.started_at, default=None)

    # Test helpers.

    @property
    def actions(self) -> list[ReadinessAction]:
        return list(self._actions)
