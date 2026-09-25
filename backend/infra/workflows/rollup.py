from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from core.application.blocker_resolution import BlockerResolutionService
from core.application.rollup_service import RollupService
from core.domain.errors import GraphNotFound
from core.domain.graph import GraphNode, NodeKind
from core.ports.repositories import RollupRepository

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry

_WEEKDAYS = frozenset({0, 1, 2, 3, 4})


@dataclass(frozen=True, kw_only=True)
class RollupInput:
    tenant_id: str
    as_of: str | None = None
    backfill_days: int | None = None


@dataclass(frozen=True, kw_only=True)
class RollupWorkflowResult:
    tenant_id: str
    days_recorded: int
    nodes_recorded: int


async def run_rollup_activity(payload: RollupInput) -> RollupWorkflowResult:
    """Compute and store node rollups for today, and for any missed weekday.

    Until this existed, the only thing that persisted ``node_statuses`` was a
    lazy compute-and-record inside the *read* paths in ``persona_views``. That
    made stored history a function of who happened to open which screen -- a day
    nobody looked at simply had no rollup -- and let any caller write a row for
    whatever ``as_of`` it asked about. Owning the write here lets those reads be
    reads.
    """
    registry = _service_registry()
    try:
        graph = registry.graph_repository()
        rollups = registry.rollup_repository()
        service = RollupService(
            registry.status_repository(),
            rollups,
            BlockerResolutionService(graph, registry.status_repository()),
        )
        today = _as_of(payload.as_of)
        backfill_days = (
            payload.backfill_days
            if payload.backfill_days is not None
            else registry.settings.rollup_backfill_days
        )
        days_recorded = 0
        nodes_recorded = 0
        for program in await graph.list_nodes(payload.tenant_id, NodeKind.PROGRAM):
            for day in await _days_to_record(rollups, program, today, backfill_days):
                try:
                    tree = await graph.get_program_tree(payload.tenant_id, program.id, day)
                except GraphNotFound:
                    continue
                statuses = await service.compute_and_record(tree, day)
                days_recorded += 1
                nodes_recorded += len(statuses)
        return RollupWorkflowResult(
            tenant_id=payload.tenant_id,
            days_recorded=days_recorded,
            nodes_recorded=nodes_recorded,
        )
    finally:
        await registry.close()


async def _days_to_record(
    rollups: RollupRepository,
    program: GraphNode,
    today: date,
    backfill_days: int,
) -> list[date]:
    """Today, plus any weekday in the window that never got a rollup.

    Today is always recomputed, because check-ins land throughout it. The
    programme root stands in for "this day was rolled up": it is the one node
    every rollup produces.
    """
    start = today - timedelta(days=max(0, backfill_days))
    recorded = {
        status.as_of
        for status in await rollups.node_status_history(
            program.tenant_id, program.ref, start, today
        )
    }
    days = [today]
    days.extend(
        day
        for day in _days_between(start, today)
        if day not in recorded and day.weekday() in _WEEKDAYS
    )
    return sorted(set(days))


def _days_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days)]


def _as_of(value: str | None) -> date:
    if value is None:
        return datetime.now(tz=UTC).date()
    try:
        return date.fromisoformat(value)
    except ValueError:
        return datetime.now(tz=UTC).date()


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
