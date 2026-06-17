from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from temporalio import activity, workflow

from core.domain.status import StatusSource

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


@dataclass(frozen=True, kw_only=True)
class NudgeInput:
    tenant_id: str
    correlation_id: str
    as_of: str
    developer_name: str | None = None
    chat_external_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class NudgeResult:
    tenant_id: str
    developer_id: str
    correlation_id: str
    status: str
    nudge_message_id: str | None = None
    stale_source: str | None = None
    inferred_source: str | None = None


@activity.defn
async def nudge_non_response_activity(payload: NudgeInput) -> NudgeResult:
    registry = _service_registry()
    try:
        repository = registry.status_repository()
        checkin = await repository.checkin_by_correlation(
            payload.tenant_id,
            payload.correlation_id,
        )
        if checkin is None:
            raise ValueError("cannot nudge without a recorded check-in")
        if checkin.replied_at is not None:
            return NudgeResult(
                tenant_id=payload.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=payload.correlation_id,
                status="already_replied",
            )

        as_of = date.fromisoformat(payload.as_of)
        existing_status = await repository.latest_developer_status(
            payload.tenant_id,
            checkin.developer_id,
            as_of,
        )
        if existing_status is not None and existing_status.source is StatusSource.STALE:
            return NudgeResult(
                tenant_id=payload.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=payload.correlation_id,
                status="already_marked_stale",
                stale_source=existing_status.source.value,
            )

        result = await registry.status_collector().nudge_then_mark_stale(
            tenant_id=payload.tenant_id,
            correlation_id=payload.correlation_id,
            as_of=as_of,
            developer_name=payload.developer_name,
            chat_external_id=payload.chat_external_id,
        )
        return NudgeResult(
            tenant_id=payload.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=payload.correlation_id,
            status="nudged",
            nudge_message_id=result.nudge_message_id,
            stale_source=result.stale_status.source.value,
            inferred_source=result.inferred_status.source.value
            if result.inferred_status is not None
            else None,
        )
    finally:
        await registry.close()


@workflow.defn
class NudgeWorkflow:
    @workflow.run
    async def run(self, payload: NudgeInput) -> NudgeResult:
        return await workflow.execute_activity(
            nudge_non_response_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
