from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

from core.domain.hygiene import (
    HygieneAction,
    HygieneFinding,
    HygieneRule,
    HygieneRun,
    HygieneSettings,
    HygieneState,
)
from core.domain.release_readiness import ScopeRef


@dataclass
class InMemoryHygieneRepository:
    _settings: dict[str, HygieneSettings] = field(default_factory=dict)
    _rules: dict[tuple[str, str], HygieneRule] = field(default_factory=dict)
    _findings: dict[tuple[str, str], HygieneFinding] = field(default_factory=dict)
    _actions: list[HygieneAction] = field(default_factory=list)
    _runs: dict[tuple[str, str], HygieneRun] = field(default_factory=dict)

    async def get_settings(self, tenant_id: str) -> HygieneSettings | None:
        return self._settings.get(tenant_id)

    async def save_settings(self, settings: HygieneSettings) -> None:
        self._settings[settings.tenant_id] = settings

    async def list_rules(
        self, tenant_id: str, *, include_deleted: bool = False
    ) -> list[HygieneRule]:
        return sorted(
            (
                item
                for (tenant, _), item in self._rules.items()
                if tenant == tenant_id and (include_deleted or item.deleted_at is None)
            ),
            key=lambda item: item.name.casefold(),
        )

    async def save_rule(self, rule: HygieneRule) -> None:
        self._rules[(rule.tenant_id, rule.rule_id)] = rule

    async def list_findings(
        self,
        tenant_id: str,
        scopes: Sequence[ScopeRef] | None = None,
        *,
        states: Collection[HygieneState] | None = None,
    ) -> list[HygieneFinding]:
        wanted = {scope.key for scope in scopes} if scopes is not None else None
        return [
            item
            for (tenant, _), item in self._findings.items()
            if tenant == tenant_id
            and (wanted is None or item.scope.key in wanted)
            and (states is None or item.state in states)
        ]

    async def get_finding(self, tenant_id: str, finding_id: str) -> HygieneFinding | None:
        return self._findings.get((tenant_id, finding_id))

    async def save_finding(self, finding: HygieneFinding) -> None:
        if finding.state is HygieneState.OPEN:
            for (tenant, finding_id), existing in self._findings.items():
                if (
                    tenant == finding.tenant_id
                    and finding_id != finding.finding_id
                    and existing.state is HygieneState.OPEN
                    and existing.rule_id == finding.rule_id
                    and existing.issue_key == finding.issue_key
                    and existing.scope == finding.scope
                ):
                    raise ValueError("one open finding per rule, issue and scope")
        self._findings[(finding.tenant_id, finding.finding_id)] = finding

    async def append_action(self, action: HygieneAction) -> None:
        self._actions.append(action)

    async def list_actions(self, tenant_id: str, finding_id: str) -> list[HygieneAction]:
        return sorted(
            (
                item
                for item in self._actions
                if item.tenant_id == tenant_id and item.finding_id == finding_id
            ),
            key=lambda item: item.at,
        )

    async def claim_run(self, run: HygieneRun) -> bool:
        if any(
            tenant == run.tenant_id and item.slot == run.slot
            for (tenant, _), item in self._runs.items()
        ):
            return False
        self._runs[(run.tenant_id, run.run_id)] = run
        return True

    async def finish_run(self, run: HygieneRun) -> None:
        self._runs[(run.tenant_id, run.run_id)] = run

    async def last_run(self, tenant_id: str) -> HygieneRun | None:
        finished = [
            item
            for (tenant, _), item in self._runs.items()
            if tenant == tenant_id and item.finished_at is not None
        ]
        return max(finished, key=lambda item: item.started_at, default=None)

    # Test helpers.

    @property
    def actions(self) -> list[HygieneAction]:
        return list(self._actions)
