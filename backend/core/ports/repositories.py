from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol

from core.domain.conversation import ConversationTurn
from core.domain.graph import (
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    GraphNode,
    GraphTree,
    NodeKind,
    VectorMatch,
)
from core.domain.integrations import SyncCursor
from core.domain.rollup import NodeStatus
from core.domain.status import (
    CheckIn,
    CheckInClarification,
    CheckInCorrelation,
    CheckInNudge,
    CheckInPreference,
    CheckInScheduleRun,
    DeveloperStatus,
)


class GraphRepository(Protocol):
    async def list_nodes(self, tenant_id: str, kind: NodeKind | None = None) -> list[GraphNode]: ...

    async def get_node(self, tenant_id: str, id: str) -> GraphNode | None: ...

    async def upsert_node(self, node: GraphNode) -> None: ...

    async def delete_node(self, tenant_id: str, id: str) -> None: ...

    async def add_edge(self, edge: GraphEdge) -> None: ...

    async def list_edges(
        self,
        tenant_id: str,
        from_node_id: str | None = None,
        to_node_id: str | None = None,
        kind: EdgeKind | None = None,
    ) -> list[GraphEdge]: ...

    async def remove_edge(self, edge: GraphEdge) -> None: ...

    async def get_program_tree(self, tenant_id: str, program_id: str, as_of: date) -> GraphTree: ...

    async def active_developer_memberships(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> list[GraphEdge]: ...


class TimeSeriesRepository(Protocol):
    async def append_fact(self, fact: FactEvent) -> None: ...

    async def append_fact_once(self, fact: FactEvent) -> None: ...

    async def list_facts(
        self,
        tenant_id: str,
        entity_ref: EntityRef,
        since: datetime | None = None,
    ) -> list[FactEvent]: ...


class StatusRepository(Protocol):
    async def record_checkin(self, checkin: CheckIn) -> None: ...

    async def checkin_by_correlation(
        self, tenant_id: str, correlation_id: str
    ) -> CheckIn | None: ...

    async def record_checkin_correlation(self, correlation: CheckInCorrelation) -> None: ...

    async def checkin_correlation_by_id(
        self, tenant_id: str, correlation_id: str
    ) -> CheckInCorrelation | None: ...

    async def latest_checkin_correlation_for_thread(
        self, tenant_id: str, chat_thread_ref: str, as_of: date
    ) -> CheckInCorrelation | None: ...

    async def unconsumed_checkin_correlations_for_thread(
        self, tenant_id: str, chat_thread_ref: str, as_of: date
    ) -> list[CheckInCorrelation]: ...

    async def latest_unconsumed_checkin_correlation_for_user(
        self, tenant_id: str, chat_user_ref: str, as_of: date
    ) -> CheckInCorrelation | None: ...

    async def unconsumed_checkin_correlations_for_user(
        self, tenant_id: str, chat_user_ref: str, as_of: date
    ) -> list[CheckInCorrelation]: ...

    async def consume_checkin_correlation(
        self, tenant_id: str, correlation_id: str, consumed_at: datetime
    ) -> None: ...

    async def record_checkin_preference(self, preference: CheckInPreference) -> None: ...

    async def checkin_preference_for(
        self, tenant_id: str, developer_id: str
    ) -> CheckInPreference | None: ...

    async def list_checkin_preferences(self, tenant_id: str) -> list[CheckInPreference]: ...

    async def delete_checkin_preference(self, tenant_id: str, developer_id: str) -> None: ...

    async def record_checkin_schedule_run(self, run: CheckInScheduleRun) -> None: ...

    async def checkin_schedule_run(
        self, tenant_id: str, developer_id: str, checkin_date: date
    ) -> CheckInScheduleRun | None: ...

    async def record_checkin_nudge(self, nudge: CheckInNudge) -> CheckInNudge: ...

    async def checkin_nudge_for(
        self, tenant_id: str, correlation_id: str, nudge_number: int
    ) -> CheckInNudge | None: ...

    async def record_checkin_clarification(
        self, clarification: CheckInClarification
    ) -> CheckInClarification: ...

    async def checkin_clarification_count(self, tenant_id: str, correlation_id: str) -> int: ...

    async def record_developer_status(self, status: DeveloperStatus) -> None: ...

    async def latest_developer_status(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> DeveloperStatus | None: ...

    async def developers_without_checkin(self, tenant_id: str, as_of: date) -> list[str]: ...


class ConversationRepository(Protocol):
    async def append_turn(self, turn: ConversationTurn) -> None: ...

    async def list_turns_for_day(
        self, tenant_id: str, developer_id: str, on: date
    ) -> list[ConversationTurn]: ...

    async def list_recent_turns(
        self,
        tenant_id: str,
        developer_id: str,
        limit: int,
        since: datetime | None = None,
    ) -> list[ConversationTurn]: ...

    async def user_turn_exists(
        self, tenant_id: str, developer_id: str, chat_message_id: str
    ) -> bool: ...

    async def purge_turns_older_than(self, tenant_id: str, cutoff: datetime) -> int: ...


class RollupRepository(Protocol):
    async def record_node_status(self, status: NodeStatus) -> None: ...

    async def latest_node_status(
        self, tenant_id: str, entity_ref: EntityRef, as_of: date
    ) -> NodeStatus | None: ...

    async def list_node_statuses(self, tenant_id: str, as_of: date) -> list[NodeStatus]: ...


class SyncCursorRepository(Protocol):
    async def get_cursor(self, tenant_id: str, connector: str, scope: str) -> SyncCursor: ...

    async def record_cursor(
        self, tenant_id: str, connector: str, scope: str, cursor: SyncCursor
    ) -> None: ...


class VectorStore(Protocol):
    async def upsert_embedding(
        self, tenant_id: str, entity_ref: EntityRef, vector: Sequence[float]
    ) -> None: ...

    async def search(
        self, tenant_id: str, vector: Sequence[float], limit: int
    ) -> list[VectorMatch]: ...
