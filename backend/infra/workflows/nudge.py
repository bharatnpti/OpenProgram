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
    reply_wait_seconds: int = 0
    final_reply_wait_seconds: int = 0


@dataclass(frozen=True, kw_only=True)
class NudgeResult:
    tenant_id: str
    developer_id: str
    correlation_id: str
    status: str
    nudge_message_id: str | None = None
    terminal_source: str | None = None


@activity.defn
async def send_checkin_nudge_activity(payload: NudgeInput) -> NudgeResult:
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

        nudge = await repository.checkin_nudge_for(payload.tenant_id, payload.correlation_id, 1)
        if nudge is not None:
            return NudgeResult(
                tenant_id=payload.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=payload.correlation_id,
                status="already_nudged",
                nudge_message_id=nudge.outbound_message_id
                or _pending_nudge_message_id(payload.correlation_id),
            )

        nudge_message_id = await registry.status_collector().send_nudge(
            tenant_id=payload.tenant_id,
            correlation_id=payload.correlation_id,
            developer_name=payload.developer_name,
            chat_external_id=payload.chat_external_id,
        )
        return NudgeResult(
            tenant_id=payload.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=payload.correlation_id,
            status="nudged",
            nudge_message_id=nudge_message_id,
        )
    finally:
        await registry.close()


@activity.defn
async def close_checkin_non_response_activity(payload: NudgeInput) -> NudgeResult:
    registry = _service_registry()
    try:
        repository = registry.status_repository()
        checkin = await repository.checkin_by_correlation(
            payload.tenant_id,
            payload.correlation_id,
        )
        if checkin is None:
            raise ValueError("cannot close non-response without a recorded check-in")
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
        if (
            existing_status is not None
            and existing_status.as_of == as_of
            and existing_status.source
            in {StatusSource.INFERRED, StatusSource.STALE, StatusSource.UNKNOWN}
        ):
            return NudgeResult(
                tenant_id=payload.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=payload.correlation_id,
                status="already_closed",
                terminal_source=existing_status.source.value,
            )

        terminal_status = await registry.status_collector().record_non_response(
            tenant_id=payload.tenant_id,
            developer_id=checkin.developer_id,
            as_of=as_of,
            developer_name=payload.developer_name,
        )
        return NudgeResult(
            tenant_id=payload.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=payload.correlation_id,
            status="closed",
            terminal_source=terminal_status.source.value,
        )
    finally:
        await registry.close()


@workflow.defn
class NudgeWorkflow:
    @workflow.run
    async def run(self, payload: NudgeInput) -> NudgeResult:
        if payload.reply_wait_seconds > 0:
            await workflow.sleep(timedelta(seconds=payload.reply_wait_seconds))
        nudge_result = await workflow.execute_activity(
            send_checkin_nudge_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )
        if nudge_result.status == "already_replied":
            return nudge_result
        if payload.final_reply_wait_seconds > 0:
            await workflow.sleep(timedelta(seconds=payload.final_reply_wait_seconds))
        close_result = await workflow.execute_activity(
            close_checkin_non_response_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
        )
        if close_result.nudge_message_id is None:
            return NudgeResult(
                tenant_id=close_result.tenant_id,
                developer_id=close_result.developer_id,
                correlation_id=close_result.correlation_id,
                status=close_result.status,
                nudge_message_id=nudge_result.nudge_message_id,
                terminal_source=close_result.terminal_source,
            )
        return close_result


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())


def _pending_nudge_message_id(correlation_id: str) -> str:
    return f"pending-nudge-{correlation_id}-1"
