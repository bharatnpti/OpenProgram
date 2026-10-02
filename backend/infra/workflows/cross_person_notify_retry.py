"""Retry counterpart DMs that failed to send, from a recurring schedule.

Workflow definitions import this module, so it must stay light at load time:
the service (and the logging it brings) is reached only through the registry,
inside the activity.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.domain.workflows import CrossPersonNotifyRetryInput, CrossPersonNotifyRetryResult

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


async def retry_cross_person_notifications_activity(
    payload: CrossPersonNotifyRetryInput,
) -> CrossPersonNotifyRetryResult:
    """One bounded, idempotent retry pass; safe to run again or concurrently.

    Each request is claimed before its DM is sent, so a pass that is retried
    after a crash, or one that overlaps another, never sends a DM twice.
    """
    registry = _service_registry()
    try:
        settings = registry.settings
        if not (settings.cross_person_notify_retry_enabled and settings.cross_person_auto_notify):
            # A schedule registered earlier outlives a flag switched off since.
            return CrossPersonNotifyRetryResult(tenant_id=payload.tenant_id, status="disabled")
        summary = await registry.cross_person_request_service().retry_failed_notifications(
            payload.tenant_id,
            now=_reference_time(payload.now),
        )
        return CrossPersonNotifyRetryResult(
            tenant_id=payload.tenant_id,
            status="ran",
            due=summary.due,
            sent=summary.sent,
            failed=summary.failed,
            skipped=summary.skipped,
            given_up=summary.given_up,
        )
    finally:
        await registry.close()


def _reference_time(value: str | None) -> datetime:
    """The scheduled time, but never earlier than the wall clock.

    A pass that the runtime retries some minutes after it was scheduled must
    not stamp its attempts in the past, or the next one would come due early.
    """
    now = datetime.now(tz=UTC)
    if value is None:
        return now
    parsed = datetime.fromisoformat(value)
    scheduled = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return max(scheduled, now)


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
