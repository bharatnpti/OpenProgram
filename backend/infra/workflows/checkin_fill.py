"""The day's fill from Jira, on a schedule of its own.

It sends nothing, so it never waits on the check-in send: with the send off
nobody was asked, and everyone due gets a day that says "Not asked today.";
with it on, those the send did not reach get "Not reached in chat.". Its
schedule (every 15 minutes by default, every day of the week) exists while
the deployment's switch (``checkin_fill_enabled``) and the tenant's switch
("Fill the day from Jira for people who were not asked") are both on: the
worker decides at start (``ensure_workflow_schedules``) and saving the
tenant's switch adds or removes it. Each run checks both switches again, so a
schedule that outlives either sends nothing and fills nobody. The fan-out and
reconcile schedules are not touched.

Imports stay light: the registry, and with it logging and providers, is only
reached inside an activity, so the workflow sandbox can import this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.domain.workflows import ScheduleBootstrapResult, SyncScheduleConfig

if TYPE_CHECKING:
    from config.settings import Settings
    from core.ports.workflows import WorkflowScheduler
    from infra.registry import ServiceRegistry

#: The sync connector the fill's schedule dispatches.
CHECKIN_FILL_CONNECTOR = "checkin_fill"


@dataclass(frozen=True, kw_only=True)
class CheckinFillInput:
    tenant_id: str
    observed_at: str | None = None


@dataclass(frozen=True, kw_only=True)
class CheckinFillResult:
    tenant_id: str
    #: disabled (deployment switch), off (tenant switch) or ok.
    status: str
    filled: int


async def run_checkin_fill_activity(payload: CheckinFillInput) -> CheckinFillResult:
    registry = _service_registry()
    try:
        if not registry.settings.checkin_fill_enabled:
            return CheckinFillResult(tenant_id=payload.tenant_id, status="disabled", filled=0)
        setting = await registry.fill_day_settings_service().effective(payload.tenant_id)
        if not setting.enabled:
            return CheckinFillResult(tenant_id=payload.tenant_id, status="off", filled=0)
        filled = await registry.fill_day_pass().run(payload.tenant_id, _timestamp(payload))
        return CheckinFillResult(tenant_id=payload.tenant_id, status="ok", filled=len(filled))
    finally:
        await registry.close()


def checkin_fill_schedule_config(settings: Settings) -> SyncScheduleConfig:
    return SyncScheduleConfig(
        schedule_id=settings.checkin_fill_schedule_id,
        tenant_id=settings.tenant_id,
        connector=CHECKIN_FILL_CONNECTOR,
        scope="tenant",
        payload={},
        cron=settings.checkin_fill_cron,
    )


async def apply_checkin_fill_schedule(
    scheduler: WorkflowScheduler, settings: Settings, *, tenant_on: bool
) -> ScheduleBootstrapResult:
    """Add the fill's schedule while both switches are on; remove it otherwise."""
    if settings.checkin_fill_enabled and tenant_on:
        configured = await scheduler.ensure_sync_schedules([checkin_fill_schedule_config(settings)])
        return configured[0]
    return await scheduler.remove_schedule(settings.checkin_fill_schedule_id)


def _timestamp(payload: CheckinFillInput) -> datetime:
    if payload.observed_at is None:
        return datetime.now(tz=UTC)
    parsed = datetime.fromisoformat(payload.observed_at)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
