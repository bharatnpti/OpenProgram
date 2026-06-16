from __future__ import annotations

from typing import Protocol

from core.domain.workflows import ScheduleBootstrapResult


class WorkflowScheduler(Protocol):
    async def ensure_heartbeat_schedule(self) -> ScheduleBootstrapResult: ...


class WorkflowWorker(Protocol):
    async def run(self) -> None: ...
