from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from core.domain.status import (
    CheckIn,
    CheckInPreference,
    CheckInScheduleRun,
    resolve_timezone,
)
from core.ports.repositories import StatusRepository
from infra.workflows.nudge import NudgeInput

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
    checkin_date: str | None = None


@dataclass(frozen=True, kw_only=True)
class DailyCheckinResult:
    tenant_id: str
    developer_id: str
    correlation_id: str
    asked_at: str
    already_recorded: bool
    status: str = "sent"
    skipped_reason: str | None = None
    nudge_workflow_id: str | None = None
    reply_wait_seconds: int = 14400
    final_reply_wait_seconds: int = 28800


async def start_daily_checkin_activity(payload: DailyCheckinInput) -> DailyCheckinResult:
    registry = _service_registry()
    try:
        repository = registry.status_repository()
        settings = registry.settings
        preference = await repository.checkin_preference_for(
            payload.tenant_id,
            payload.developer_id,
        )
        preference = preference or CheckInPreference(
            tenant_id=payload.tenant_id,
            developer_id=payload.developer_id,
            reply_wait_seconds=settings.checkin_reply_wait_seconds,
            final_reply_wait_seconds=settings.checkin_final_reply_wait_seconds,
        )
        checkin_date = _checkin_date(payload, datetime.now(tz=UTC))
        scheduled_at = _scheduled_at(
            checkin_date,
            preference,
            preference.timezone or settings.tenant_default_timezone,
        )

        existing_run = await repository.checkin_schedule_run(
            payload.tenant_id,
            payload.developer_id,
            checkin_date,
        )
        if existing_run is not None:
            existing_checkin = await repository.checkin_by_correlation(
                payload.tenant_id,
                existing_run.correlation_id,
            )
            if existing_checkin is not None:
                return _checkin_result(
                    existing_checkin,
                    already_recorded=True,
                    status=existing_run.status,
                    reply_wait_seconds=preference.reply_wait_seconds,
                    final_reply_wait_seconds=preference.final_reply_wait_seconds,
                )
            return _run_result(
                existing_run,
                already_recorded=True,
                reply_wait_seconds=preference.reply_wait_seconds,
                final_reply_wait_seconds=preference.final_reply_wait_seconds,
            )

        correlation_id = payload.correlation_id or f"checkin-{payload.developer_id}-{checkin_date}"
        if checkin_date.weekday() not in set(preference.weekdays):
            run = await _record_schedule_run(
                repository,
                tenant_id=payload.tenant_id,
                developer_id=payload.developer_id,
                checkin_date=checkin_date,
                correlation_id=correlation_id,
                status="skipped_weekend",
                scheduled_at=scheduled_at,
                reason="check-in preference excludes this weekday",
            )
            return _run_result(
                run,
                already_recorded=False,
                reply_wait_seconds=preference.reply_wait_seconds,
                final_reply_wait_seconds=preference.final_reply_wait_seconds,
            )

        timezone = preference.timezone or settings.tenant_default_timezone
        scheduled_at = _scheduled_at(checkin_date, preference, timezone)

        existing = None
        if payload.correlation_id is not None:
            existing = await repository.checkin_by_correlation(
                payload.tenant_id,
                payload.correlation_id,
            )
        if existing is not None:
            await _record_schedule_run(
                repository,
                tenant_id=payload.tenant_id,
                developer_id=payload.developer_id,
                checkin_date=checkin_date,
                correlation_id=existing.correlation_id,
                status="sent",
                scheduled_at=scheduled_at,
                reason="check-in already existed for correlation",
            )
            return _checkin_result(
                existing,
                already_recorded=True,
                status="sent",
                reply_wait_seconds=preference.reply_wait_seconds,
                final_reply_wait_seconds=preference.final_reply_wait_seconds,
            )

        checkin = await registry.status_collector().start_checkin(
            tenant_id=payload.tenant_id,
            developer_id=payload.developer_id,
            developer_name=payload.developer_name,
            chat_external_id=payload.chat_external_id,
            correlation_id=correlation_id,
            asked_at=_optional_datetime(payload.asked_at) or scheduled_at,
            checkin_date=checkin_date,
        )
        await _record_schedule_run(
            repository,
            tenant_id=payload.tenant_id,
            developer_id=payload.developer_id,
            checkin_date=checkin_date,
            correlation_id=checkin.correlation_id,
            status="sent",
            scheduled_at=scheduled_at,
            reason=None,
        )
        return _checkin_result(
            checkin,
            already_recorded=False,
            status="sent",
            reply_wait_seconds=preference.reply_wait_seconds,
            final_reply_wait_seconds=preference.final_reply_wait_seconds,
        )
    finally:
        await registry.close()


def prepare_daily_checkin_payload(
    payload: DailyCheckinInput,
    *,
    workflow_id: str,
    now: datetime,
) -> DailyCheckinInput:
    scheduled = payload
    if scheduled.correlation_id is None:
        scheduled = replace(
            scheduled,
            correlation_id=f"checkin-{workflow_id}",
        )
    if scheduled.asked_at is None:
        scheduled = replace(scheduled, asked_at=now.isoformat())
    if scheduled.checkin_date is None:
        scheduled = replace(scheduled, checkin_date=now.date().isoformat())
    return scheduled


def nudge_input_for_daily_checkin_result(
    result: DailyCheckinResult,
    scheduled: DailyCheckinInput,
    *,
    now: datetime,
) -> NudgeInput:
    return NudgeInput(
        tenant_id=result.tenant_id,
        correlation_id=result.correlation_id,
        as_of=scheduled.checkin_date or now.date().isoformat(),
        developer_name=scheduled.developer_name,
        chat_external_id=scheduled.chat_external_id,
        reply_wait_seconds=result.reply_wait_seconds,
        final_reply_wait_seconds=result.final_reply_wait_seconds,
    )


def _checkin_result(
    checkin: CheckIn,
    *,
    already_recorded: bool,
    status: str,
    reply_wait_seconds: int,
    final_reply_wait_seconds: int,
) -> DailyCheckinResult:
    return DailyCheckinResult(
        tenant_id=checkin.tenant_id,
        developer_id=checkin.developer_id,
        correlation_id=checkin.correlation_id,
        asked_at=checkin.asked_at.isoformat(),
        already_recorded=already_recorded,
        status=status,
        reply_wait_seconds=reply_wait_seconds,
        final_reply_wait_seconds=final_reply_wait_seconds,
    )


def _run_result(
    run: CheckInScheduleRun,
    *,
    already_recorded: bool,
    reply_wait_seconds: int,
    final_reply_wait_seconds: int,
) -> DailyCheckinResult:
    return DailyCheckinResult(
        tenant_id=run.tenant_id,
        developer_id=run.developer_id,
        correlation_id=run.correlation_id,
        asked_at=run.scheduled_at.isoformat(),
        already_recorded=already_recorded,
        status=run.status,
        skipped_reason=run.reason,
        reply_wait_seconds=reply_wait_seconds,
        final_reply_wait_seconds=final_reply_wait_seconds,
    )


async def _record_schedule_run(
    repository: StatusRepository,
    *,
    tenant_id: str,
    developer_id: str,
    checkin_date: date,
    correlation_id: str,
    status: str,
    scheduled_at: datetime,
    reason: str | None,
) -> CheckInScheduleRun:
    run = CheckInScheduleRun(
        tenant_id=tenant_id,
        developer_id=developer_id,
        checkin_date=checkin_date,
        correlation_id=correlation_id,
        status=status,
        scheduled_at=scheduled_at,
        reason=reason,
    )
    await repository.record_checkin_schedule_run(run)
    return run


def _checkin_date(payload: DailyCheckinInput, fallback: datetime) -> date:
    if payload.checkin_date is not None:
        return date.fromisoformat(payload.checkin_date)
    asked_at = _optional_datetime(payload.asked_at)
    return (asked_at or fallback).date()


def _scheduled_at(checkin_date: date, preference: CheckInPreference, timezone: str) -> datetime:
    zone = resolve_timezone(timezone, "UTC")
    local_dt = datetime.combine(checkin_date, preference.local_time, tzinfo=zone)
    # Spring-forward gap: the wall-clock time does not exist that day, so a
    # round-trip through UTC yields a different wall time. Advance to the first
    # valid instant after the transition instead of silently keeping fold=0.
    normalized = local_dt.astimezone(UTC).astimezone(zone)
    if normalized.time() != preference.local_time:
        local_dt = normalized
    return local_dt.astimezone(UTC)


def _optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
