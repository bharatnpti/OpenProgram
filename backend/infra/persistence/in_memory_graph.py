from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from math import sqrt

from core.domain.conversation import ConversationTurn
from core.domain.errors import GraphNotFound
from core.domain.graph import (
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    GraphNode,
    GraphTree,
    NodeKind,
    VectorMatch,
    normalize_vector,
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


@dataclass
class InMemoryGraphStore:
    _nodes: dict[tuple[str, str], GraphNode] = field(default_factory=dict)
    _edges: list[GraphEdge] = field(default_factory=list)
    _facts: list[FactEvent] = field(default_factory=list)
    _vectors: dict[tuple[str, str, str], tuple[float, ...]] = field(default_factory=dict)
    _checkins: list[CheckIn] = field(default_factory=list)
    _checkin_correlations: list[CheckInCorrelation] = field(default_factory=list)
    _checkin_preferences: dict[tuple[str, str], CheckInPreference] = field(default_factory=dict)
    _checkin_schedule_runs: dict[tuple[str, str, date], CheckInScheduleRun] = field(
        default_factory=dict
    )
    _checkin_nudges: dict[tuple[str, str, int], CheckInNudge] = field(default_factory=dict)
    _checkin_clarifications: dict[tuple[str, str, int], CheckInClarification] = field(
        default_factory=dict
    )
    _developer_statuses: dict[tuple[str, str, date], DeveloperStatus] = field(default_factory=dict)
    _node_statuses: dict[tuple[str, str, str, date], NodeStatus] = field(default_factory=dict)
    _sync_cursors: dict[tuple[str, str, str], SyncCursor] = field(default_factory=dict)
    _conversation_turns: list[ConversationTurn] = field(default_factory=list)

    async def upsert_node(self, node: GraphNode) -> None:
        self._nodes[(node.tenant_id, node.id)] = node

    async def add_edge(self, edge: GraphEdge) -> None:
        if edge not in self._edges:
            self._edges.append(edge)

    async def get_program_tree(self, tenant_id: str, program_id: str, as_of: date) -> GraphTree:
        root = self._nodes.get((tenant_id, program_id))
        if root is None:
            raise GraphNotFound(f"program {program_id} not found for tenant {tenant_id}")

        selected_edges: list[GraphEdge] = []
        selected_nodes: dict[str, GraphNode] = {root.id: root}
        queue: deque[str] = deque([root.id])

        while queue:
            current_id = queue.popleft()
            for edge in self._active_edges_from(tenant_id, current_id, as_of):
                target = self._nodes.get((tenant_id, edge.to_node_id))
                if target is None:
                    continue
                selected_edges.append(edge)
                if target.id not in selected_nodes:
                    selected_nodes[target.id] = target
                    queue.append(target.id)

        return GraphTree(
            root=root,
            nodes=tuple(selected_nodes.values()),
            edges=tuple(selected_edges),
        )

    async def active_developer_memberships(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> list[GraphEdge]:
        return [
            edge
            for edge in self._edges
            if edge.tenant_id == tenant_id
            and edge.kind in {EdgeKind.CONTAINS, EdgeKind.ASSIGNED_TO}
            and edge.is_active_on(as_of)
            and (edge.from_node_id == developer_id or edge.to_node_id == developer_id)
        ]

    async def append_fact(self, fact: FactEvent) -> None:
        identity = _fact_identity(fact)
        if any(_fact_identity(existing) == identity for existing in self._facts):
            return
        self._facts.append(fact)

    async def append_fact_once(self, fact: FactEvent) -> None:
        await self.append_fact(fact)

    async def list_facts(
        self,
        tenant_id: str,
        entity_ref: EntityRef,
        since: datetime | None = None,
    ) -> list[FactEvent]:
        return [
            fact
            for fact in self._facts
            if fact.tenant_id == tenant_id
            and fact.entity_ref == entity_ref
            and (since is None or fact.observed_at >= since)
        ]

    async def record_checkin(self, checkin: CheckIn) -> None:
        self._checkins = [
            existing
            for existing in self._checkins
            if not (
                existing.tenant_id == checkin.tenant_id
                and existing.correlation_id == checkin.correlation_id
            )
        ]
        self._checkins.append(checkin)

    async def checkin_by_correlation(self, tenant_id: str, correlation_id: str) -> CheckIn | None:
        for checkin in reversed(self._checkins):
            if checkin.tenant_id == tenant_id and checkin.correlation_id == correlation_id:
                return checkin
        return None

    async def record_checkin_correlation(self, correlation: CheckInCorrelation) -> None:
        self._checkin_correlations = [
            existing
            for existing in self._checkin_correlations
            if not (
                existing.tenant_id == correlation.tenant_id
                and existing.correlation_id == correlation.correlation_id
            )
        ]
        self._checkin_correlations.append(correlation)

    async def checkin_correlation_by_id(
        self, tenant_id: str, correlation_id: str
    ) -> CheckInCorrelation | None:
        for correlation in reversed(self._checkin_correlations):
            if correlation.tenant_id == tenant_id and correlation.correlation_id == correlation_id:
                return correlation
        return None

    async def latest_checkin_correlation_for_thread(
        self, tenant_id: str, chat_thread_ref: str, as_of: date
    ) -> CheckInCorrelation | None:
        matching = [
            correlation
            for correlation in self._checkin_correlations
            if correlation.tenant_id == tenant_id
            and correlation.chat_thread_ref == chat_thread_ref
            and correlation.asked_at.date() == as_of
        ]
        return max(matching, key=lambda correlation: correlation.asked_at) if matching else None

    async def latest_unconsumed_checkin_correlation_for_user(
        self, tenant_id: str, chat_user_ref: str, as_of: date
    ) -> CheckInCorrelation | None:
        matching = [
            correlation
            for correlation in self._checkin_correlations
            if correlation.tenant_id == tenant_id
            and correlation.chat_user_ref == chat_user_ref
            and correlation.consumed_at is None
            and correlation.asked_at.date() == as_of
        ]
        return max(matching, key=lambda correlation: correlation.asked_at) if matching else None

    async def consume_checkin_correlation(
        self, tenant_id: str, correlation_id: str, consumed_at: datetime
    ) -> None:
        correlation = await self.checkin_correlation_by_id(tenant_id, correlation_id)
        if correlation is None or correlation.consumed_at is not None:
            return
        await self.record_checkin_correlation(
            CheckInCorrelation(
                tenant_id=correlation.tenant_id,
                correlation_id=correlation.correlation_id,
                developer_id=correlation.developer_id,
                chat_user_ref=correlation.chat_user_ref,
                chat_thread_ref=correlation.chat_thread_ref,
                outbound_message_id=correlation.outbound_message_id,
                asked_at=correlation.asked_at,
                consumed_at=consumed_at,
            )
        )

    async def record_checkin_preference(self, preference: CheckInPreference) -> None:
        self._checkin_preferences[(preference.tenant_id, preference.developer_id)] = preference

    async def checkin_preference_for(
        self, tenant_id: str, developer_id: str
    ) -> CheckInPreference | None:
        return self._checkin_preferences.get((tenant_id, developer_id))

    async def record_checkin_schedule_run(self, run: CheckInScheduleRun) -> None:
        self._checkin_schedule_runs[(run.tenant_id, run.developer_id, run.checkin_date)] = run

    async def checkin_schedule_run(
        self, tenant_id: str, developer_id: str, checkin_date: date
    ) -> CheckInScheduleRun | None:
        return self._checkin_schedule_runs.get((tenant_id, developer_id, checkin_date))

    async def record_checkin_nudge(self, nudge: CheckInNudge) -> CheckInNudge:
        key = (nudge.tenant_id, nudge.correlation_id, nudge.nudge_number)
        existing = self._checkin_nudges.get(key)
        if existing is not None:
            if existing.outbound_message_id is not None:
                return existing
            updated = CheckInNudge(
                tenant_id=existing.tenant_id,
                correlation_id=existing.correlation_id,
                nudge_number=existing.nudge_number,
                sent_at=nudge.sent_at or existing.sent_at,
                outbound_message_id=nudge.outbound_message_id or existing.outbound_message_id,
            )
            self._checkin_nudges[key] = updated
            return updated
        self._checkin_nudges[key] = nudge
        return nudge

    async def checkin_nudge_for(
        self, tenant_id: str, correlation_id: str, nudge_number: int
    ) -> CheckInNudge | None:
        return self._checkin_nudges.get((tenant_id, correlation_id, nudge_number))

    async def record_checkin_clarification(
        self, clarification: CheckInClarification
    ) -> CheckInClarification:
        key = (
            clarification.tenant_id,
            clarification.correlation_id,
            clarification.clarification_number,
        )
        existing = self._checkin_clarifications.get(key)
        if existing is not None:
            if existing.outbound_message_id is not None:
                return existing
            updated = CheckInClarification(
                tenant_id=existing.tenant_id,
                correlation_id=existing.correlation_id,
                clarification_number=existing.clarification_number,
                question=existing.question or clarification.question,
                sent_at=clarification.sent_at or existing.sent_at,
                outbound_message_id=(
                    clarification.outbound_message_id or existing.outbound_message_id
                ),
            )
            self._checkin_clarifications[key] = updated
            return updated
        self._checkin_clarifications[key] = clarification
        return clarification

    async def checkin_clarification_count(self, tenant_id: str, correlation_id: str) -> int:
        return sum(
            1
            for existing_tenant_id, existing_correlation_id, _ in self._checkin_clarifications
            if existing_tenant_id == tenant_id and existing_correlation_id == correlation_id
        )

    async def record_developer_status(self, status: DeveloperStatus) -> None:
        self._developer_statuses[(status.tenant_id, status.developer_id, status.as_of)] = status

    async def latest_developer_status(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> DeveloperStatus | None:
        matching = [
            status
            for status in self._developer_statuses.values()
            if status.tenant_id == tenant_id
            and status.developer_id == developer_id
            and status.as_of <= as_of
        ]
        return max(matching, key=lambda status: status.as_of) if matching else None

    async def developers_without_checkin(self, tenant_id: str, as_of: date) -> list[str]:
        known_developer_ids = {
            node.id
            for (node_tenant_id, _), node in self._nodes.items()
            if node_tenant_id == tenant_id and node.kind is NodeKind.DEVELOPER
        }
        known_developer_ids.update(
            status.developer_id
            for status in self._developer_statuses.values()
            if status.tenant_id == tenant_id
        )
        known_developer_ids.update(
            checkin.developer_id for checkin in self._checkins if checkin.tenant_id == tenant_id
        )
        replied_developer_ids = {
            checkin.developer_id
            for checkin in self._checkins
            if checkin.tenant_id == tenant_id
            and checkin.replied_at is not None
            and checkin.replied_at.date() == as_of
        }
        return sorted(known_developer_ids - replied_developer_ids)

    async def record_node_status(self, status: NodeStatus) -> None:
        self._node_statuses[
            (
                status.entity_ref.tenant_id,
                status.entity_ref.kind.value,
                status.entity_ref.id,
                status.as_of,
            )
        ] = status

    async def latest_node_status(
        self, tenant_id: str, entity_ref: EntityRef, as_of: date
    ) -> NodeStatus | None:
        matching = [
            status
            for status in self._node_statuses.values()
            if status.entity_ref.tenant_id == tenant_id
            and status.entity_ref == entity_ref
            and status.as_of <= as_of
        ]
        return max(matching, key=lambda status: status.as_of) if matching else None

    async def list_node_statuses(self, tenant_id: str, as_of: date) -> list[NodeStatus]:
        latest_by_entity: dict[tuple[str, str], NodeStatus] = {}
        for status in self._node_statuses.values():
            if status.entity_ref.tenant_id != tenant_id or status.as_of > as_of:
                continue
            key = (status.entity_ref.kind.value, status.entity_ref.id)
            current = latest_by_entity.get(key)
            if current is None or current.as_of < status.as_of:
                latest_by_entity[key] = status
        return sorted(
            latest_by_entity.values(),
            key=lambda status: (status.entity_ref.kind.value, status.entity_ref.id),
        )

    async def get_cursor(self, tenant_id: str, connector: str, scope: str) -> SyncCursor:
        return self._sync_cursors.get((tenant_id, connector, scope), SyncCursor())

    async def record_cursor(
        self, tenant_id: str, connector: str, scope: str, cursor: SyncCursor
    ) -> None:
        self._sync_cursors[(tenant_id, connector, scope)] = cursor

    async def append_turn(self, turn: ConversationTurn) -> None:
        self._conversation_turns.append(turn)

    async def list_turns_for_day(
        self, tenant_id: str, developer_id: str, on: date
    ) -> list[ConversationTurn]:
        return sorted(
            (
                turn
                for turn in self._conversation_turns
                if turn.tenant_id == tenant_id
                and turn.developer_id == developer_id
                and turn.conversation_date == on
            ),
            key=_conversation_sort_key,
        )

    async def list_recent_turns(
        self,
        tenant_id: str,
        developer_id: str,
        limit: int,
        since: datetime | None = None,
    ) -> list[ConversationTurn]:
        if limit <= 0:
            return []
        matching = sorted(
            (
                turn
                for turn in self._conversation_turns
                if turn.tenant_id == tenant_id
                and turn.developer_id == developer_id
                and (since is None or turn.observed_at >= since)
            ),
            key=_conversation_sort_key,
            reverse=True,
        )
        return sorted(matching[:limit], key=_conversation_sort_key)

    async def user_turn_exists(
        self, tenant_id: str, developer_id: str, chat_message_id: str
    ) -> bool:
        return any(
            turn.tenant_id == tenant_id
            and turn.developer_id == developer_id
            and turn.chat_message_id == chat_message_id
            and turn.role.value == "user"
            for turn in self._conversation_turns
        )

    async def purge_turns_older_than(self, tenant_id: str, cutoff: datetime) -> int:
        retained = [
            turn
            for turn in self._conversation_turns
            if turn.tenant_id != tenant_id or turn.observed_at >= cutoff
        ]
        deleted_count = len(self._conversation_turns) - len(retained)
        self._conversation_turns = retained
        return deleted_count

    async def upsert_embedding(
        self, tenant_id: str, entity_ref: EntityRef, vector: Sequence[float]
    ) -> None:
        self._vectors[(tenant_id, entity_ref.kind.value, entity_ref.id)] = normalize_vector(vector)

    async def search(
        self, tenant_id: str, vector: Sequence[float], limit: int
    ) -> list[VectorMatch]:
        query = normalize_vector(vector)
        scored: list[VectorMatch] = []
        for (stored_tenant, kind, entity_id), stored_vector in self._vectors.items():
            if stored_tenant != tenant_id:
                continue
            score = _cosine(query, stored_vector)
            scored.append(
                VectorMatch(
                    entity_ref=EntityRef(tenant_id=tenant_id, kind=_node_kind(kind), id=entity_id),
                    score=score,
                )
            )
        return sorted(scored, key=lambda match: match.score, reverse=True)[:limit]

    def _active_edges_from(self, tenant_id: str, node_id: str, as_of: date) -> list[GraphEdge]:
        return [
            edge
            for edge in self._edges
            if edge.tenant_id == tenant_id
            and edge.from_node_id == node_id
            and edge.kind in {EdgeKind.CONTAINS, EdgeKind.ASSIGNED_TO}
            and edge.is_active_on(as_of)
        ]


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sqrt(sum(a * a for a in left))
    right_norm = sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _node_kind(value: str) -> NodeKind:
    return NodeKind(value)


def _fact_identity(fact: FactEvent) -> tuple[str, str, str, str, str, datetime]:
    return (
        fact.tenant_id,
        fact.source,
        fact.entity_ref.kind.value,
        fact.entity_ref.id,
        fact.correlation_id,
        fact.observed_at,
    )


def _conversation_sort_key(
    turn: ConversationTurn,
) -> tuple[datetime, str, str, str, str, str]:
    return (
        turn.observed_at,
        turn.conversation_id,
        turn.correlation_id or "",
        turn.chat_message_id or "",
        turn.role.value,
        turn.content,
    )
