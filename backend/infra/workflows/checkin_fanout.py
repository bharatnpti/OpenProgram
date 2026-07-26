from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from core.domain.workflows import (
    CheckinFanoutInput,
    CheckinFanoutResult,
    CheckinReconcileDispatchPlan,
    CheckinReconcileInput,
    CheckinReconcileResult,
    CheckinReconcileStatus,
    DeveloperCheckinDispatch,
)
from core.ports.workflows import WorkflowScheduler

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


async def dispatch_checkins_for_tenant_activity(
    payload: CheckinFanoutInput,
) -> CheckinFanoutResult:
    registry = _service_registry()
    try:
        dispatches = await _developer_checkin_dispatches(registry, payload)
        workflow_ids = await _dispatch_developer_checkins_concurrently(
            registry.workflow_scheduler(),
            dispatches,
            registry.settings.checkin_fanout_concurrency,
        )
        return CheckinFanoutResult(
            tenant_id=payload.tenant_id,
            checkin_date=payload.checkin_date,
            dispatched=len(workflow_ids),
            workflow_ids=workflow_ids,
        )
    finally:
        await registry.close()


async def _dispatch_developer_checkins_concurrently(
    scheduler: WorkflowScheduler,
    dispatches: list[DeveloperCheckinDispatch],
    concurrency: int,
) -> list[str]:
    """Dispatch child check-ins concurrently, bounded to protect Slack rate
    limits. asyncio.gather preserves dispatch order in the returned ids."""
    if not dispatches:
        return []
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def dispatch_one(dispatch: DeveloperCheckinDispatch) -> str:
        async with semaphore:
            return await scheduler.dispatch_developer_checkin(dispatch)

    return list(await asyncio.gather(*(dispatch_one(dispatch) for dispatch in dispatches)))


async def developer_checkin_dispatches_for_tenant_activity(
    payload: CheckinFanoutInput,
) -> list[DeveloperCheckinDispatch]:
    registry = _service_registry()
    try:
        return await _developer_checkin_dispatches(registry, payload)
    finally:
        await registry.close()


async def prepare_checkin_reconcile_dispatches_for_tenant_activity(
    payload: CheckinReconcileInput,
) -> CheckinReconcileDispatchPlan:
    registry = _service_registry()
    try:
        return await _checkin_reconcile_dispatch_plan(registry, payload)
    finally:
        await registry.close()


async def reconcile_checkins_for_tenant_activity(
    payload: CheckinReconcileInput,
) -> CheckinReconcileResult:
    registry = _service_registry()
    try:
        plan = await _checkin_reconcile_dispatch_plan(registry, payload)
        if plan.result.status != "dispatched":
            return plan.result
        workflow_ids = await _dispatch_developer_checkins_concurrently(
            registry.workflow_scheduler(),
            plan.dispatches,
            registry.settings.checkin_fanout_concurrency,
        )
        if not workflow_ids:
            return replace(plan.result, status="no_missing", dispatched=0, workflow_ids=[])
        return replace(plan.result, dispatched=len(workflow_ids), workflow_ids=workflow_ids)
    finally:
        await registry.close()


async def _developer_checkin_dispatches(
    registry: ServiceRegistry,
    payload: CheckinFanoutInput,
) -> list[DeveloperCheckinDispatch]:
    checkin_date = date.fromisoformat(payload.checkin_date)
    developers = await registry.status_repository().developers_without_checkin(
        payload.tenant_id,
        checkin_date,
    )
    return [
        DeveloperCheckinDispatch(
            tenant_id=payload.tenant_id,
            developer_id=developer_id,
            checkin_date=payload.checkin_date,
        )
        for developer_id in developers
    ]


async def _checkin_reconcile_dispatch_plan(
    registry: ServiceRegistry,
    payload: CheckinReconcileInput,
) -> CheckinReconcileDispatchPlan:
    observed_at = _observed_at(payload.observed_at)
    local_observed_at = observed_at.astimezone(_timezone(payload.timezone))
    checkin_date = local_observed_at.date()
    cutoff = _local_time(payload.after_local_time)
    if local_observed_at.time() < cutoff:
        return CheckinReconcileDispatchPlan(
            result=CheckinReconcileResult(
                tenant_id=payload.tenant_id,
                checkin_date=checkin_date.isoformat(),
                status="skipped_early",
                dispatched=0,
                workflow_ids=[],
                skipped_reason="before reconcile cutoff",
            ),
            dispatches=[],
        )

    repository = registry.status_repository()
    developers = await repository.developers_without_checkin(payload.tenant_id, checkin_date)
    dispatches: list[DeveloperCheckinDispatch] = []
    for developer_id in developers:
        existing_run = await repository.checkin_schedule_run(
            payload.tenant_id,
            developer_id,
            checkin_date,
        )
        if existing_run is not None:
            continue
        dispatches.append(
            DeveloperCheckinDispatch(
                tenant_id=payload.tenant_id,
                developer_id=developer_id,
                checkin_date=checkin_date.isoformat(),
            )
        )
    status: CheckinReconcileStatus = "dispatched" if dispatches else "no_missing"
    return CheckinReconcileDispatchPlan(
        result=CheckinReconcileResult(
            tenant_id=payload.tenant_id,
            checkin_date=checkin_date.isoformat(),
            status=status,
            dispatched=len(dispatches),
            workflow_ids=[],
        ),
        dispatches=dispatches,
    )


def _observed_at(value: str | None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _local_time(value: str) -> time:
    parsed = time.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise ValueError("check-in reconcile cutoff must be a local time")
    return parsed


def _timezone(value: str) -> ZoneInfo:
    return ZoneInfo(value)


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
