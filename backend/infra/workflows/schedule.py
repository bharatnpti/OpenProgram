from __future__ import annotations

import asyncio

from config.settings import Settings, get_settings
from core.domain.workflows import (
    CheckinScheduleConfig,
    ConversationPurgeScheduleConfig,
    SyncScheduleConfig,
)
from infra.registry import ServiceRegistry
from infra.workflows.dispatch import safe_workflow_id


async def main() -> None:
    settings = get_settings()
    registry = ServiceRegistry(settings)
    try:
        scheduler = registry.workflow_scheduler()
        results = [
            await scheduler.ensure_heartbeat_schedule(),
            await scheduler.ensure_checkin_fanout_schedule(checkin_fanout_config(settings)),
            await scheduler.ensure_conversation_purge_schedule(conversation_purge_config(settings)),
            *(await scheduler.ensure_sync_schedules(sync_schedule_configs(settings))),
        ]
        for result in results:
            print(f"workflow schedule {result.status}: {result.schedule_id}")
    finally:
        await registry.close()


def checkin_fanout_config(settings: Settings) -> CheckinScheduleConfig:
    return CheckinScheduleConfig(
        schedule_id=settings.checkin_fanout_schedule_id,
        tenant_id=settings.tenant_id,
        cron=settings.checkin_fanout_cron,
    )


def conversation_purge_config(settings: Settings) -> ConversationPurgeScheduleConfig:
    return ConversationPurgeScheduleConfig(
        schedule_id=settings.conversation_purge_schedule_id,
        tenant_id=settings.tenant_id,
        retention_days=settings.conversation_retention_days,
        cron=settings.conversation_purge_cron,
    )


def sync_schedule_configs(settings: Settings) -> tuple[SyncScheduleConfig, ...]:
    configs: list[SyncScheduleConfig] = []
    for entry in settings.jira_sync_projects:
        project_key, container_id, board_id = _jira_project_target(entry)
        scope = f"project:{project_key}"
        payload: dict[str, str | int | float | bool | None] = {"project_key": project_key}
        if container_id is not None:
            payload["container_id"] = container_id
        if board_id is not None:
            payload["board_id"] = board_id
        configs.append(
            SyncScheduleConfig(
                schedule_id=safe_workflow_id(
                    f"pulseops-sync-issue-{scope}-{container_id or ''}-{board_id or ''}"
                ),
                tenant_id=settings.tenant_id,
                connector="issue",
                scope=scope,
                payload=payload,
                cron=settings.jira_sync_cron,
            )
        )
    for repo_name in settings.github_sync_repos:
        scope = f"repo:{repo_name}"
        configs.append(
            SyncScheduleConfig(
                schedule_id=safe_workflow_id(f"pulseops-sync-vcs-{scope}"),
                tenant_id=settings.tenant_id,
                connector="vcs",
                scope=scope,
                payload={"repo_name": repo_name},
                cron=settings.github_sync_cron,
            )
        )
    for user_id in settings.calendar_sync_user_ids:
        scope = f"user:{user_id}"
        configs.append(
            SyncScheduleConfig(
                schedule_id=safe_workflow_id(f"pulseops-sync-calendar-{scope}"),
                tenant_id=settings.tenant_id,
                connector="calendar",
                scope=scope,
                payload={
                    "user_id": user_id,
                    "window_days": settings.calendar_sync_window_days,
                },
                cron=settings.calendar_sync_cron,
            )
        )
    return tuple(configs)


def _jira_project_target(entry: str) -> tuple[str, str | None, str | None]:
    parts = entry.split(":")
    project_key = parts[0]
    container_id = parts[1] if len(parts) >= 2 else None
    board_id = parts[2] if len(parts) >= 3 else None
    return project_key, container_id, board_id


if __name__ == "__main__":
    asyncio.run(main())
