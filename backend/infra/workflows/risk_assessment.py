from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.application.risk_service import RiskService
from core.domain.graph import GraphNode, NodeKind
from core.domain.integrations import SyncCursor
from core.domain.risk import RiskProviderConfig

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry

_RISK_CURSOR_CONNECTOR = "risk"
_DEFAULT_RUN_TIME = time(18, 0)


@dataclass(frozen=True, kw_only=True)
class RiskAssessmentInput:
    tenant_id: str
    project_id: str | None = None
    observed_at: str | None = None


@dataclass(frozen=True, kw_only=True)
class RiskAssessmentWorkflowResult:
    tenant_id: str
    projects_evaluated: int
    projects_assessed: int
    newly_opened: int
    newly_cleared: int


async def run_risk_assessment_activity(
    payload: RiskAssessmentInput,
) -> RiskAssessmentWorkflowResult:
    """Per-project, end-of-day risk assessment.

    Runs on a polled schedule (see infra/workflows/schedule.py); each tick
    only assesses projects whose configured local end-of-day time has
    elapsed since their last run. No event-driven ingestion is involved --
    this rides the same polled-schedule model as the existing sync/fact
    pipeline.
    """
    registry = _service_registry()
    try:
        observed_at = _timestamp(payload.observed_at)
        projects = await _target_projects(registry, payload)
        risk_service = _risk_service(registry)
        assessed = 0
        newly_opened = 0
        newly_cleared = 0
        for project in projects:
            local_today, should_run = await _should_run(
                registry, payload.tenant_id, project, observed_at
            )
            if not should_run:
                continue
            delta = await risk_service.assess_and_persist_project(
                payload.tenant_id, project.id, local_today
            )
            await _record_run(registry, payload.tenant_id, project.id, local_today)
            assessed += 1
            newly_opened += len(delta.newly_opened)
            newly_cleared += len(delta.newly_cleared)
        return RiskAssessmentWorkflowResult(
            tenant_id=payload.tenant_id,
            projects_evaluated=len(projects),
            projects_assessed=assessed,
            newly_opened=newly_opened,
            newly_cleared=newly_cleared,
        )
    finally:
        await registry.close()


async def _target_projects(
    registry: ServiceRegistry,
    payload: RiskAssessmentInput,
) -> list[GraphNode]:
    graph_repository = registry.graph_repository()
    if payload.project_id is not None:
        node = await graph_repository.get_node(payload.tenant_id, payload.project_id)
        if node is None or node.kind is not NodeKind.PROJECT:
            return []
        return [node]
    return await graph_repository.list_nodes(payload.tenant_id, NodeKind.PROJECT)


async def _should_run(
    registry: ServiceRegistry,
    tenant_id: str,
    project: GraphNode,
    observed_at: datetime,
) -> tuple[date, bool]:
    run_time, timezone = _project_run_config(registry, project)
    zone = _zone(timezone)
    local_now = observed_at.astimezone(zone)
    local_today = local_now.date()
    if local_now.time() < run_time:
        return local_today, False
    cursor = await registry.sync_cursor_repository().get_cursor(
        tenant_id, _RISK_CURSOR_CONNECTOR, _cursor_scope(project.id)
    )
    last_run_date = _optional_date(cursor.value)
    if last_run_date is not None and last_run_date >= local_today:
        return local_today, False
    return local_today, True


async def _record_run(
    registry: ServiceRegistry,
    tenant_id: str,
    project_id: str,
    as_of: date,
) -> None:
    await registry.sync_cursor_repository().record_cursor(
        tenant_id,
        _RISK_CURSOR_CONNECTOR,
        _cursor_scope(project_id),
        SyncCursor(value=as_of.isoformat(), updated_at=datetime.now(tz=UTC)),
    )


def _risk_service(registry: ServiceRegistry) -> RiskService:
    settings = registry.settings
    return RiskService(
        graph_repository=registry.graph_repository(),
        time_series_repository=registry.time_series_repository(),
        status_repository=registry.status_repository(),
        provider_config=RiskProviderConfig(
            jira_base_url=settings.jira_base_url,
            github_base_url=settings.github_base_url,
            default_no_pr_days=settings.risk_default_no_pr_days,
            default_pr_age_days=settings.risk_default_pr_age_days,
            default_stale_days=settings.risk_default_stale_days,
        ),
    )


def _project_run_config(registry: ServiceRegistry, project: GraphNode) -> tuple[time, str]:
    local_time_value = _string_metadata(project, "risk_run_local_time")
    run_time = (
        _parse_time(local_time_value)
        or _parse_time(registry.settings.risk_run_default_local_time)
        or _DEFAULT_RUN_TIME
    )
    timezone = (
        _string_metadata(project, "risk_run_timezone") or registry.settings.tenant_default_timezone
    )
    return run_time, timezone


def _cursor_scope(project_id: str) -> str:
    return f"project:{project_id}"


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None
    try:
        hour_str, minute_str = value.split(":", 1)
        return time(int(hour_str), int(minute_str))
    except (ValueError, TypeError):
        return None


def _optional_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _string_metadata(node: GraphNode, key: str) -> str | None:
    value = node.metadata.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
