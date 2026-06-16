from __future__ import annotations

from dataclasses import dataclass

from core.domain.workflows import ScheduleBootstrapResult


@dataclass(frozen=True)
class FakeWorkflowScheduler:
    schedule_id: str

    async def ensure_heartbeat_schedule(self) -> ScheduleBootstrapResult:
        return ScheduleBootstrapResult(schedule_id=self.schedule_id, status="ready")


class FakeWorkflowWorker:
    async def run(self) -> None:
        return None


class FakeWorkflowReadinessProbe:
    async def check(self) -> bool:
        return True
