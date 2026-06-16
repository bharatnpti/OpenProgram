from __future__ import annotations

from datetime import date

from opentelemetry import trace

from core.domain.graph import FactEvent, GraphTree
from core.ports.repositories import GraphRepository, TimeSeriesRepository

_tracer = trace.get_tracer("pulseops.application.graph_queries")


class GraphQueryService:
    def __init__(
        self,
        graph_repository: GraphRepository,
        time_series_repository: TimeSeriesRepository,
    ) -> None:
        self._graph_repository = graph_repository
        self._time_series_repository = time_series_repository

    async def program_tree(self, tenant_id: str, program_id: str, as_of: date) -> GraphTree:
        with _tracer.start_as_current_span("application.graph.program_tree"):
            return await self._graph_repository.get_program_tree(tenant_id, program_id, as_of)

    async def record_fact(self, fact: FactEvent) -> None:
        with _tracer.start_as_current_span("application.graph.record_fact"):
            await self._time_series_repository.append_fact(fact)
