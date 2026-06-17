from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from temporalio import activity, workflow

from core.domain.status import CheckIn

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


@dataclass(frozen=True, kw_only=True)
class DailyCheckinInput:
    tenant_id: str
    developer_id: str
    developer_name: str | None = None
    chat_external_id: str | None = None
    correlation_id: str | None = None
    asked_at: str | None = None


@dataclass(frozen=True, kw_only=True)
class DailyCheckinResult:
    tenant_id: str
    developer_id: str
    correlation_id: str
    asked_at: str
    already_recorded: bool


@activity.defn
async def start_daily_checkin_activity(payload: DailyCheckinInput) -> DailyCheckinResult:
    registry = _service_registry()
    try:
        existing = None
        if payload.correlation_id is not None:
            existing = await registry.status_repository().checkin_by_correlation(
                payload.tenant_id,
                payload.correlation_id,
            )
        if existing is not None:
            return _checkin_result(existing, already_recorded=True)

        checkin = await registry.status_collector().start_checkin(
            tenant_id=payload.tenant_id,
            developer_id=payload.developer_id,
            developer_name=payload.developer_name,
            chat_external_id=payload.chat_external_id,
            correlation_id=payload.correlation_id,
            asked_at=_optional_datetime(payload.asked_at),
        )
        return _checkin_result(checkin, already_recorded=False)
    finally:
        await registry.close()


@workflow.defn
class DailyCheckinWorkflow:
    @workflow.run
    async def run(self, payload: DailyCheckinInput) -> DailyCheckinResult:
        scheduled = payload
        if scheduled.correlation_id is None:
            scheduled = replace(
                scheduled,
                correlation_id=f"checkin-{workflow.info().workflow_id}",
            )
        if scheduled.asked_at is None:
            scheduled = replace(scheduled, asked_at=workflow.now().isoformat())
        return await workflow.execute_activity(
            start_daily_checkin_activity,
            scheduled,
            start_to_close_timeout=timedelta(minutes=5),
        )


def _checkin_result(checkin: CheckIn, *, already_recorded: bool) -> DailyCheckinResult:
    return DailyCheckinResult(
        tenant_id=checkin.tenant_id,
        developer_id=checkin.developer_id,
        correlation_id=checkin.correlation_id,
        asked_at=checkin.asked_at.isoformat(),
        already_recorded=already_recorded,
    )


def _optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
