from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta

from core.domain.workflows import DeveloperCheckinDispatch, SyncDispatchInput, SyncScheduleConfig
from infra.workflows.calendar_sync import CalendarSyncInput
from infra.workflows.daily_checkin import DailyCheckinInput
from infra.workflows.git_sync import GitSyncInput
from infra.workflows.jira_sync import JiraSyncInput

type SyncWorkflowInput = JiraSyncInput | GitSyncInput | CalendarSyncInput


def daily_checkin_input(payload: DeveloperCheckinDispatch) -> DailyCheckinInput:
    return DailyCheckinInput(
        tenant_id=payload.tenant_id,
        developer_id=payload.developer_id,
        developer_name=payload.developer_name,
        chat_external_id=payload.chat_external_id,
        checkin_date=payload.checkin_date,
    )


def sync_dispatch_for_schedule(
    config: SyncScheduleConfig,
    scheduled_at: datetime,
) -> SyncDispatchInput:
    payload = dict(config.payload)
    payload.setdefault("observed_at", scheduled_at.isoformat())
    if _connector(config.connector) == "calendar":
        start = _optional_date(payload.get("start")) or scheduled_at.date()
        window_days = _positive_int(payload.get("window_days"), default=1)
        payload["start"] = start.isoformat()
        end = _optional_date(payload.get("end")) or start + timedelta(days=window_days)
        payload["end"] = end.isoformat()
    return SyncDispatchInput(
        tenant_id=config.tenant_id,
        connector=config.connector,
        scope=config.scope,
        payload=payload,
    )


def sync_workflow_input(input: SyncDispatchInput) -> SyncWorkflowInput:
    connector = _connector(input.connector)
    if connector == "issue":
        return JiraSyncInput(
            tenant_id=input.tenant_id,
            project_key=_required_str(input.payload, "project_key"),
            container_id=_optional_str(input.payload, "container_id"),
            observed_at=_optional_str(input.payload, "observed_at"),
        )
    if connector == "vcs":
        return GitSyncInput(
            tenant_id=input.tenant_id,
            repo_name=_required_str(input.payload, "repo_name"),
            observed_at=_optional_str(input.payload, "observed_at"),
        )
    if connector == "calendar":
        return CalendarSyncInput(
            tenant_id=input.tenant_id,
            user_id=_required_str(input.payload, "user_id"),
            start=_required_str(input.payload, "start"),
            end=_required_str(input.payload, "end"),
            display_name=_optional_str(input.payload, "display_name"),
            observed_at=_optional_str(input.payload, "observed_at"),
        )
    raise ValueError(f"unsupported sync connector: {input.connector}")


def sync_workflow_name(input: SyncDispatchInput) -> str:
    connector = _connector(input.connector)
    if connector == "issue":
        return "jira"
    if connector == "vcs":
        return "git"
    if connector == "calendar":
        return "calendar"
    raise ValueError(f"unsupported sync connector: {input.connector}")


def safe_workflow_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return normalized.strip("-") or "workflow"


def _connector(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"issue", "jira"}:
        return "issue"
    if normalized in {"vcs", "git", "github"}:
        return "vcs"
    if normalized in {"calendar", "google_calendar"}:
        return "calendar"
    return normalized


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError(f"sync payload field {key} must be a non-empty string")


def _optional_str(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _positive_int(value: object, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    raise ValueError("calendar sync window days must be positive")


def _optional_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return date.fromisoformat(value)
