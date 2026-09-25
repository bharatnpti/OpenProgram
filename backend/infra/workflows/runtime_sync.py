from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from config.settings import Settings
from core.application.sync_targets import RuntimeSyncTargetResolver, RuntimeSyncTargets
from core.domain.workflows import SyncDispatchInput

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


@dataclass(frozen=True, kw_only=True)
class RuntimeSyncInput:
    tenant_id: str
    connector: str | None = None


@dataclass(frozen=True, kw_only=True)
class RuntimeSyncWorkflowResult:
    tenant_id: str
    connector: str | None
    dispatched: int
    workflow_ids: list[str]


@dataclass(frozen=True, kw_only=True)
class RuntimeSyncPlan:
    """Which syncs runtime config asks for, resolved but not yet dispatched."""

    connector: str | None
    dispatches: tuple[SyncDispatchInput, ...]


async def run_runtime_config_sync_activity(payload: RuntimeSyncInput) -> RuntimeSyncWorkflowResult:
    registry = _service_registry()
    try:
        plan = await _resolve_plan(registry, payload)
        scheduler = registry.workflow_scheduler()
        workflow_ids = [await scheduler.dispatch_sync(dispatch) for dispatch in plan.dispatches]
        return RuntimeSyncWorkflowResult(
            tenant_id=payload.tenant_id,
            connector=plan.connector,
            dispatched=len(workflow_ids),
            workflow_ids=workflow_ids,
        )
    finally:
        await registry.close()


async def resolve_runtime_sync_plan(payload: RuntimeSyncInput) -> RuntimeSyncPlan:
    """Resolve the targets without dispatching them.

    An orchestrator that cannot start child workflows from wherever it resolves
    targets calls this and fans out itself. A DBOS step is one such place, so
    the fan-out lives in the workflow that wraps the step.
    """
    registry = _service_registry()
    try:
        return await _resolve_plan(registry, payload)
    finally:
        await registry.close()


async def _resolve_plan(registry: ServiceRegistry, payload: RuntimeSyncInput) -> RuntimeSyncPlan:
    connector = _connector_filter(payload.connector)
    resolver = RuntimeSyncTargetResolver(registry.graph_repository())
    targets = await resolver.resolve(payload.tenant_id)
    return RuntimeSyncPlan(
        connector=connector,
        dispatches=_runtime_or_legacy_dispatches(
            registry.settings,
            payload.tenant_id,
            targets,
            connector,
        ),
    )


def _runtime_or_legacy_dispatches(
    settings: Settings,
    tenant_id: str,
    targets: RuntimeSyncTargets,
    connector: str | None,
) -> tuple[SyncDispatchInput, ...]:
    issue_dispatches = targets.issue_dispatches
    vcs_dispatches = targets.vcs_dispatches
    if connector == "issue":
        return issue_dispatches or _legacy_issue_dispatches(settings, tenant_id)
    if connector == "vcs":
        return vcs_dispatches or _legacy_vcs_dispatches(settings, tenant_id)
    return (issue_dispatches or _legacy_issue_dispatches(settings, tenant_id)) + (
        vcs_dispatches or _legacy_vcs_dispatches(settings, tenant_id)
    )


def _legacy_issue_dispatches(settings: Settings, tenant_id: str) -> tuple[SyncDispatchInput, ...]:
    dispatches: list[SyncDispatchInput] = []
    for entry in settings.jira_sync_projects:
        project_key, container_id, board_id = _jira_project_target(entry)
        payload: dict[str, str | int | float | bool | None] = {"project_key": project_key}
        if container_id is not None:
            payload["container_id"] = container_id
        if board_id is not None:
            payload["board_id"] = board_id
        dispatches.append(
            SyncDispatchInput(
                tenant_id=tenant_id,
                connector="issue",
                scope=f"project:{project_key}",
                payload=payload,
            )
        )
    return tuple(dispatches)


def _legacy_vcs_dispatches(settings: Settings, tenant_id: str) -> tuple[SyncDispatchInput, ...]:
    return tuple(
        SyncDispatchInput(
            tenant_id=tenant_id,
            connector="vcs",
            scope=f"repo:{repo_name}",
            payload={"repo_name": repo_name},
        )
        for repo_name in settings.github_sync_repos
    )


def _connector_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"issue", "jira"}:
        return "issue"
    if normalized in {"vcs", "git", "github", "gitlab"}:
        return "vcs"
    return None


def _jira_project_target(entry: str) -> tuple[str, str | None, str | None]:
    parts = entry.split(":")
    project_key = parts[0]
    container_id = parts[1] if len(parts) >= 2 else None
    board_id = parts[2] if len(parts) >= 3 else None
    return project_key, container_id, board_id


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
