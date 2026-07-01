from __future__ import annotations

import asyncio

from config.settings import Settings, get_settings
from core.domain.workflows import (
    CheckinScheduleConfig,
    ConversationPurgeScheduleConfig,
    ScheduleBootstrapResult,
    SyncScheduleConfig,
)
from infra.registry import ServiceRegistry
from infra.workflows.dispatch import safe_workflow_id


async def main() -> None:
    settings = get_settings()
    registry = ServiceRegistry(settings)
    try:
        results = await ensure_workflow_schedules(registry, settings)
        for result in results:
            print(f"workflow schedule {result.status}: {result.schedule_id}")
    finally:
        await registry.close()


async def ensure_workflow_schedules(
    registry: ServiceRegistry,
    settings: Settings | None = None,
) -> list[ScheduleBootstrapResult]:
    settings = settings or registry.settings
    scheduler = registry.workflow_scheduler()
    return [
        await scheduler.ensure_heartbeat_schedule(),
        await scheduler.ensure_checkin_fanout_schedule(checkin_fanout_config(settings)),
        await scheduler.ensure_conversation_purge_schedule(conversation_purge_config(settings)),
        *(await scheduler.ensure_sync_schedules(sync_schedule_configs(settings))),
    ]


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
    return (
        SyncScheduleConfig(
            schedule_id="pulseops-runtime-issue-sync",
            tenant_id=settings.tenant_id,
            connector="runtime",
            scope="issue",
            payload={"connector": "issue"},
            cron=settings.jira_sync_cron,
        ),
        SyncScheduleConfig(
            schedule_id="pulseops-runtime-vcs-sync",
            tenant_id=settings.tenant_id,
            connector="runtime",
            scope="vcs",
            payload={"connector": "vcs"},
            cron=settings.github_sync_cron,
        ),
        SyncScheduleConfig(
            schedule_id=settings.directory_sync_schedule_id,
            tenant_id=settings.tenant_id,
            connector="directory",
            scope="directory",
            payload={},
            cron=settings.directory_sync_cron,
        ),
        SyncScheduleConfig(
            schedule_id="pulseops-runtime-risk-assessment",
            tenant_id=settings.tenant_id,
            connector="risk",
            scope="assessment",
            payload={},
            cron=settings.risk_assessment_cron,
        ),
    )


def legacy_sync_schedule_configs(settings: Settings) -> tuple[SyncScheduleConfig, ...]:
    configs: list[SyncScheduleConfig] = []
    for project_key, container_id, board_id in (
        _jira_project_target(entry) for entry in settings.jira_sync_projects
    ):
        scope = f"project:{project_key}"
        configs.append(
            SyncScheduleConfig(
                schedule_id=safe_workflow_id(
                    f"pulseops-sync-issue-{scope}-{container_id or ''}-{board_id or ''}"
                ),
                tenant_id=settings.tenant_id,
                connector="issue",
                scope=scope,
                payload=_jira_payload(project_key, container_id, board_id),
                cron=settings.jira_sync_cron,
            )
        )
    configs.extend(
        SyncScheduleConfig(
            schedule_id=safe_workflow_id(f"pulseops-sync-vcs-repo:{repo_name}"),
            tenant_id=settings.tenant_id,
            connector="vcs",
            scope=f"repo:{repo_name}",
            payload={"repo_name": repo_name},
            cron=settings.github_sync_cron,
        )
        for repo_name in settings.github_sync_repos
    )
    return tuple(configs)


def _jira_project_target(entry: str) -> tuple[str, str | None, str | None]:
    parts = entry.split(":")
    project_key = parts[0]
    container_id = parts[1] if len(parts) >= 2 else None
    board_id = parts[2] if len(parts) >= 3 else None
    return project_key, container_id, board_id


def _jira_payload(
    project_key: str,
    container_id: str | None,
    board_id: str | None,
) -> dict[str, str | int | float | bool | None]:
    payload: dict[str, str | int | float | bool | None] = {"project_key": project_key}
    if container_id is not None:
        payload["container_id"] = container_id
    if board_id is not None:
        payload["board_id"] = board_id
    return payload


if __name__ == "__main__":
    asyncio.run(main())
