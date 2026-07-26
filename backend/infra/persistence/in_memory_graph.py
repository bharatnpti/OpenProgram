from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from uuid import uuid4

from core.domain.brief import BriefKind, NarrativeBrief
from core.domain.conversation import ConversationTurn
from core.domain.cross_person import CrossPersonRequest, CrossPersonRequestStatus
from core.domain.dead_letter import DeadLetter, DeadLetterStatus
from core.domain.directory import DirectoryUser
from core.domain.errors import GraphNotFound
from core.domain.graph import (
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    GraphNode,
    GraphTree,
    NodeKind,
)
from core.domain.identity import IdentityLink
from core.domain.inbound import InboundChatEvent
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
from core.domain.writeback import WriteBackAudit
from core.ports.directory import DirectoryUserRepository


@dataclass
class InMemoryGraphStore:
    _nodes: dict[tuple[str, str], GraphNode] = field(default_factory=dict)
    _edges: list[GraphEdge] = field(default_factory=list)
    _facts: list[FactEvent] = field(default_factory=list)
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
    _directory_users: dict[tuple[str, str], DirectoryUser] = field(default_factory=dict)
    _identity_links: dict[tuple[str, str], IdentityLink] = field(default_factory=dict)
    _writeback_config: dict[str, bool] = field(default_factory=dict)
    _writeback_audit: dict[str, WriteBackAudit] = field(default_factory=dict)
    _conversation_turns: list[ConversationTurn] = field(default_factory=list)
    _cross_person_requests: dict[tuple[str, str], CrossPersonRequest] = field(default_factory=dict)
    _inbound_chat_events: list[InboundChatEvent] = field(default_factory=list)
    _narrative_briefs: list[NarrativeBrief] = field(default_factory=list)
    _dead_letters: dict[tuple[str, str], DeadLetter] = field(default_factory=dict)
    _checkin_reply_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def list_nodes(self, tenant_id: str, kind: NodeKind | None = None) -> list[GraphNode]:
        return sorted(
            (
                node
                for (node_tenant_id, _), node in self._nodes.items()
                if node_tenant_id == tenant_id and (kind is None or node.kind is kind)
            ),
            key=lambda node: (node.kind.value, node.name, node.id),
        )

    async def get_node(self, tenant_id: str, id: str) -> GraphNode | None:
        return self._nodes.get((tenant_id, id))

    async def upsert_node(self, node: GraphNode) -> None:
        self._nodes[(node.tenant_id, node.id)] = node

    async def delete_node(self, tenant_id: str, id: str) -> None:
        self._nodes.pop((tenant_id, id), None)
        self._edges = [
            edge
            for edge in self._edges
            if not (
                edge.tenant_id == tenant_id and (edge.from_node_id == id or edge.to_node_id == id)
            )
        ]

    async def add_edge(self, edge: GraphEdge) -> None:
        if edge not in self._edges:
            self._edges.append(edge)

    async def list_edges(
        self,
        tenant_id: str,
        from_node_id: str | None = None,
        to_node_id: str | None = None,
        kind: EdgeKind | None = None,
    ) -> list[GraphEdge]:
        return sorted(
            (
                edge
                for edge in self._edges
                if edge.tenant_id == tenant_id
                and (from_node_id is None or edge.from_node_id == from_node_id)
                and (to_node_id is None or edge.to_node_id == to_node_id)
                and (kind is None or edge.kind is kind)
            ),
            key=lambda edge: (
                edge.from_node_id,
                edge.to_node_id,
                edge.kind.value,
                edge.valid_from or date.min,
                edge.valid_to or date.max,
                tuple((key, repr(value)) for key, value in sorted(edge.metadata.items())),
            ),
        )

    async def remove_edge(self, edge: GraphEdge) -> None:
        self._edges = [existing for existing in self._edges if existing != edge]

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

    async def list_recent_facts(
        self,
        tenant_id: str,
        since: datetime | None = None,
        sources: Sequence[str] | None = None,
        limit: int = 100,
    ) -> list[FactEvent]:
        if limit <= 0:
            return []
        source_filter = set(sources) if sources is not None else None
        matching = [
            fact
            for fact in self._facts
            if fact.tenant_id == tenant_id
            and (since is None or fact.observed_at >= since)
            and (source_filter is None or fact.source in source_filter)
        ]
        return sorted(
            matching,
            key=lambda fact: (fact.observed_at, fact.ingested_at, fact.correlation_id),
            reverse=True,
        )[:limit]

    async def create(self, request: CrossPersonRequest) -> CrossPersonRequest:
        key = (request.tenant_id, request.id)
        existing = self._cross_person_requests.get(key)
        if existing is not None:
            return existing
        self._cross_person_requests[key] = request
        return request

    async def get(self, tenant_id: str, request_id: str) -> CrossPersonRequest | None:
        return self._cross_person_requests.get((tenant_id, request_id))

    async def update_status(
        self,
        tenant_id: str,
        request_id: str,
        status: CrossPersonRequestStatus,
        updated_at: datetime,
    ) -> CrossPersonRequest | None:
        existing = await self.get(tenant_id, request_id)
        if existing is None:
            return None
        updated = CrossPersonRequest(
            tenant_id=existing.tenant_id,
            id=existing.id,
            requester_id=existing.requester_id,
            requester_chat_ref=existing.requester_chat_ref,
            counterpart_id=existing.counterpart_id,
            kind=existing.kind,
            note=existing.note,
            source_correlation_id=existing.source_correlation_id,
            status=status,
            created_at=existing.created_at,
            updated_at=updated_at,
            task_ref=existing.task_ref,
            raw_name=existing.raw_name,
            email=existing.email,
            counterpart_display_name=existing.counterpart_display_name,
            counterpart_email=existing.counterpart_email,
            notify_message_id=existing.notify_message_id,
            notify_correlation_id=existing.notify_correlation_id,
        )
        self._cross_person_requests[(tenant_id, request_id)] = updated
        return updated

    async def record_notification(
        self,
        tenant_id: str,
        request_id: str,
        *,
        notify_message_id: str,
        notify_correlation_id: str,
        updated_at: datetime,
    ) -> CrossPersonRequest | None:
        existing = await self.get(tenant_id, request_id)
        if existing is None:
            return None
        updated = CrossPersonRequest(
            tenant_id=existing.tenant_id,
            id=existing.id,
            requester_id=existing.requester_id,
            requester_chat_ref=existing.requester_chat_ref,
            counterpart_id=existing.counterpart_id,
            kind=existing.kind,
            note=existing.note,
            source_correlation_id=existing.source_correlation_id,
            status=existing.status,
            created_at=existing.created_at,
            updated_at=updated_at,
            task_ref=existing.task_ref,
            raw_name=existing.raw_name,
            email=existing.email,
            counterpart_display_name=existing.counterpart_display_name,
            counterpart_email=existing.counterpart_email,
            notify_message_id=notify_message_id,
            notify_correlation_id=notify_correlation_id,
        )
        self._cross_person_requests[(tenant_id, request_id)] = updated
        return updated

    async def list_for_counterpart(
        self,
        tenant_id: str,
        counterpart_id: str,
        statuses: Sequence[CrossPersonRequestStatus] | None = None,
    ) -> list[CrossPersonRequest]:
        status_filter = set(statuses) if statuses is not None else None
        return _sort_cross_person_requests(
            request
            for request in self._cross_person_requests.values()
            if request.tenant_id == tenant_id
            and request.counterpart_id == counterpart_id
            and (status_filter is None or request.status in status_filter)
        )

    async def list_for_requester(
        self,
        tenant_id: str,
        requester_id: str,
        statuses: Sequence[CrossPersonRequestStatus] | None = None,
    ) -> list[CrossPersonRequest]:
        status_filter = set(statuses) if statuses is not None else None
        return _sort_cross_person_requests(
            request
            for request in self._cross_person_requests.values()
            if request.tenant_id == tenant_id
            and request.requester_id == requester_id
            and (status_filter is None or request.status in status_filter)
        )

    async def list_open(self, tenant_id: str) -> list[CrossPersonRequest]:
        return _sort_cross_person_requests(
            request
            for request in self._cross_person_requests.values()
            if request.tenant_id == tenant_id
            and request.status
            in {
                CrossPersonRequestStatus.OPEN,
                CrossPersonRequestStatus.ACKNOWLEDGED,
                CrossPersonRequestStatus.NEEDS_RESOLUTION,
            }
        )

    async def get_by_notify_correlation(
        self,
        tenant_id: str,
        notify_correlation_id: str,
    ) -> CrossPersonRequest | None:
        for request in self._cross_person_requests.values():
            if (
                request.tenant_id == tenant_id
                and request.notify_correlation_id == notify_correlation_id
            ):
                return request
        return None

    async def get_by_notify_message_id(
        self,
        tenant_id: str,
        message_id: str,
    ) -> CrossPersonRequest | None:
        for request in self._cross_person_requests.values():
            if request.tenant_id == tenant_id and request.notify_message_id == message_id:
                return request
        return None

    async def record_checkin(self, checkin: CheckIn) -> None:
        stored = _checkin_with_last_accessed_at(checkin)
        self._checkins = [
            existing
            for existing in self._checkins
            if not (
                existing.tenant_id == stored.tenant_id
                and existing.correlation_id == stored.correlation_id
            )
        ]
        self._checkins.append(stored)

    async def record_checkin_reply_once(self, checkin: CheckIn) -> bool:
        async with self._checkin_reply_lock:
            existing = await self.checkin_by_correlation(checkin.tenant_id, checkin.correlation_id)
            if existing is not None and existing.replied_at is not None:
                return False
            await self.record_checkin(checkin)
            return True

    async def checkin_by_correlation(self, tenant_id: str, correlation_id: str) -> CheckIn | None:
        for index in range(len(self._checkins) - 1, -1, -1):
            checkin = self._checkins[index]
            if checkin.tenant_id == tenant_id and checkin.correlation_id == correlation_id:
                if checkin.raw_reply is not None:
                    checkin = replace(checkin, last_accessed_at=datetime.now(tz=UTC))
                    self._checkins[index] = checkin
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

    async def unconsumed_checkin_correlations_for_thread(
        self, tenant_id: str, chat_thread_ref: str, as_of: date
    ) -> list[CheckInCorrelation]:
        matching = [
            correlation
            for correlation in self._checkin_correlations
            if correlation.tenant_id == tenant_id
            and correlation.chat_thread_ref == chat_thread_ref
            and correlation.consumed_at is None
            and correlation.asked_at.date() == as_of
        ]
        return sorted(matching, key=lambda correlation: correlation.asked_at, reverse=True)

    async def latest_unconsumed_checkin_correlation_for_user(
        self, tenant_id: str, chat_user_ref: str, as_of: date
    ) -> CheckInCorrelation | None:
        matching = await self.unconsumed_checkin_correlations_for_user(
            tenant_id,
            chat_user_ref,
            as_of,
        )
        return matching[0] if matching else None

    async def unconsumed_checkin_correlations_for_user(
        self, tenant_id: str, chat_user_ref: str, as_of: date
    ) -> list[CheckInCorrelation]:
        matching = [
            correlation
            for correlation in self._checkin_correlations
            if correlation.tenant_id == tenant_id
            and correlation.chat_user_ref == chat_user_ref
            and correlation.consumed_at is None
            and correlation.asked_at.date() == as_of
        ]
        return sorted(matching, key=lambda correlation: correlation.asked_at, reverse=True)

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

    async def list_checkin_preferences(self, tenant_id: str) -> list[CheckInPreference]:
        return sorted(
            (
                preference
                for (preference_tenant_id, _), preference in self._checkin_preferences.items()
                if preference_tenant_id == tenant_id
            ),
            key=lambda preference: preference.developer_id,
        )

    async def delete_checkin_preference(self, tenant_id: str, developer_id: str) -> None:
        self._checkin_preferences.pop((tenant_id, developer_id), None)

    async def get_identity_link(self, tenant_id: str, developer_id: str) -> IdentityLink | None:
        return self._identity_links.get((tenant_id, developer_id))

    async def upsert_identity_link(self, link: IdentityLink) -> None:
        self._identity_links[(link.tenant_id, link.developer_id)] = link

    async def list_identity_links(self, tenant_id: str) -> list[IdentityLink]:
        return sorted(
            (
                link
                for (link_tenant_id, _), link in self._identity_links.items()
                if link_tenant_id == tenant_id
            ),
            key=lambda link: link.developer_id,
        )

    async def get_writeback_enabled(self, tenant_id: str) -> bool | None:
        return self._writeback_config.get(tenant_id)

    async def set_writeback_enabled(self, tenant_id: str, enabled: bool) -> None:
        self._writeback_config[tenant_id] = enabled

    async def record(self, audit: WriteBackAudit) -> None:
        self._writeback_audit.setdefault(audit.id, audit)

    async def list_for_issue(self, tenant_id: str, issue_key: str) -> list[WriteBackAudit]:
        return sorted(
            (
                audit
                for audit in self._writeback_audit.values()
                if audit.tenant_id == tenant_id and audit.issue_key == issue_key
            ),
            key=lambda audit: audit.created_at,
        )

    async def find_existing(
        self,
        tenant_id: str,
        issue_key: str,
        target_state: str,
        correlation_id: str,
    ) -> WriteBackAudit | None:
        matches = [
            audit
            for audit in self._writeback_audit.values()
            if audit.tenant_id == tenant_id
            and audit.issue_key == issue_key
            and audit.target_state == target_state
            and audit.correlation_id == correlation_id
        ]
        if not matches:
            return None
        return min(matches, key=lambda audit: audit.created_at)

    async def record_checkin_schedule_run(self, run: CheckInScheduleRun) -> None:
        self._checkin_schedule_runs[(run.tenant_id, run.developer_id, run.checkin_date)] = run

    async def checkin_schedule_run(
        self, tenant_id: str, developer_id: str, checkin_date: date
    ) -> CheckInScheduleRun | None:
        return self._checkin_schedule_runs.get((tenant_id, developer_id, checkin_date))

    async def checkin_schedule_run_for_correlation(
        self, tenant_id: str, correlation_id: str
    ) -> CheckInScheduleRun | None:
        for run in self._checkin_schedule_runs.values():
            if run.tenant_id == tenant_id and run.correlation_id == correlation_id:
                return run
        return None

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
            and (
                checkin.checkin_date == as_of
                if checkin.checkin_date is not None
                else checkin.replied_at.date() == as_of
            )
        }
        return sorted(known_developer_ids - replied_developer_ids)

    async def purge_checkin_raw_replies_older_than(self, tenant_id: str, cutoff: datetime) -> int:
        cleared = 0
        retained: list[CheckIn] = []
        for checkin in self._checkins:
            last_accessed_at = _checkin_last_accessed_at(checkin)
            if (
                checkin.tenant_id == tenant_id
                and checkin.raw_reply is not None
                and last_accessed_at < cutoff
            ):
                retained.append(replace(checkin, raw_reply=None))
                cleared += 1
                continue
            retained.append(checkin)
        self._checkins = retained
        return cleared

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

    async def node_status_history(
        self, tenant_id: str, entity_ref: EntityRef, start: date, end: date
    ) -> list[NodeStatus]:
        matching = [
            status
            for status in self._node_statuses.values()
            if status.entity_ref.tenant_id == tenant_id
            and status.entity_ref == entity_ref
            and start <= status.as_of <= end
        ]
        return sorted(matching, key=lambda status: status.as_of)

    async def record_brief(self, brief: NarrativeBrief) -> None:
        self._narrative_briefs = [
            existing
            for existing in self._narrative_briefs
            if not (
                existing.tenant_id == brief.tenant_id
                and existing.kind == brief.kind
                and existing.scope_id == brief.scope_id
                and existing.generated_at == brief.generated_at
            )
        ]
        self._narrative_briefs.append(brief)

    async def latest_briefs(
        self,
        tenant_id: str,
        kind: BriefKind | None = None,
        limit: int = 20,
    ) -> list[NarrativeBrief]:
        if limit <= 0:
            return []
        matching = [
            brief
            for brief in self._narrative_briefs
            if brief.tenant_id == tenant_id and (kind is None or brief.kind == kind)
        ]
        matching.sort(key=lambda brief: brief.generated_at, reverse=True)
        return matching[:limit]

    async def record_dead_letter(self, dl: DeadLetter) -> None:
        self._dead_letters[(dl.tenant_id, dl.id)] = dl

    async def list_open_dead_letters(self, tenant_id: str, limit: int = 100) -> list[DeadLetter]:
        if limit <= 0:
            return []
        matching = [
            dl
            for dl in self._dead_letters.values()
            if dl.tenant_id == tenant_id and dl.status == DeadLetterStatus.OPEN
        ]
        matching.sort(key=lambda dl: dl.dead_lettered_at, reverse=True)
        return matching[:limit]

    async def get_dead_letter(self, tenant_id: str, id: str) -> DeadLetter | None:
        return self._dead_letters.get((tenant_id, id))

    async def mark_dead_letter_rearmed(
        self, tenant_id: str, id: str, rearmed_at: datetime
    ) -> DeadLetter | None:
        existing = self._dead_letters.get((tenant_id, id))
        if existing is None:
            return None
        updated = replace(existing, status=DeadLetterStatus.REARMED, rearmed_at=rearmed_at)
        self._dead_letters[(tenant_id, id)] = updated
        return updated

    async def count_open_dead_letters(self, tenant_id: str) -> int:
        return sum(
            1
            for dl in self._dead_letters.values()
            if dl.tenant_id == tenant_id and dl.status == DeadLetterStatus.OPEN
        )

    async def get_cursor(self, tenant_id: str, connector: str, scope: str) -> SyncCursor:
        return self._sync_cursors.get((tenant_id, connector, scope), SyncCursor())

    async def record_cursor(
        self, tenant_id: str, connector: str, scope: str, cursor: SyncCursor
    ) -> None:
        self._sync_cursors[(tenant_id, connector, scope)] = cursor

    async def append_turn(self, turn: ConversationTurn) -> None:
        self._conversation_turns.append(
            turn
            if turn.last_accessed_at is not None
            else replace(turn, last_accessed_at=turn.observed_at)
        )

    async def list_turns_for_day(
        self, tenant_id: str, developer_id: str, on: date
    ) -> list[ConversationTurn]:
        matched = sorted(
            (
                turn
                for turn in self._conversation_turns
                if turn.tenant_id == tenant_id
                and turn.developer_id == developer_id
                and turn.conversation_date == on
            ),
            key=_conversation_sort_key,
        )
        self._touch_conversation_turns(matched)
        return matched

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
        matched = sorted(matching[:limit], key=_conversation_sort_key)
        self._touch_conversation_turns(matched)
        return matched

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
            if turn.tenant_id != tenant_id or _turn_last_accessed_at(turn) >= cutoff
        ]
        deleted_count = len(self._conversation_turns) - len(retained)
        self._conversation_turns = retained
        return deleted_count

    def _touch_conversation_turns(self, turns: Sequence[ConversationTurn]) -> None:
        if not turns:
            return
        touched_at = datetime.now(tz=UTC)
        turn_keys = {_conversation_identity(turn) for turn in turns}
        self._conversation_turns = [
            replace(turn, last_accessed_at=touched_at)
            if _conversation_identity(turn) in turn_keys
            else turn
            for turn in self._conversation_turns
        ]

    async def append(self, event: InboundChatEvent) -> bool:
        for existing in self._inbound_chat_events:
            if (
                existing.tenant_id == event.tenant_id
                and existing.provider == event.provider
                and existing.event_id == event.event_id
            ):
                return False
        self._inbound_chat_events.append(
            event if event.id is not None else replace(event, id=uuid4().hex)
        )
        return True

    async def list_unprocessed_for_conversation(
        self, tenant_id: str, conversation_key: str
    ) -> list[InboundChatEvent]:
        return sorted(
            (
                event
                for event in self._inbound_chat_events
                if event.tenant_id == tenant_id
                and event.conversation_key == conversation_key
                and event.processed_at is None
            ),
            key=lambda event: (event.received_at, event.message_ref),
        )

    async def mark_processed(
        self, tenant_id: str, event_ids: Sequence[str], processed_at: datetime
    ) -> None:
        ids = set(event_ids)
        self._inbound_chat_events = [
            replace(event, processed_at=processed_at)
            if event.tenant_id == tenant_id and event.id in ids and event.processed_at is None
            else event
            for event in self._inbound_chat_events
        ]

    async def list_stuck(self, tenant_id: str, older_than: datetime) -> list[InboundChatEvent]:
        return sorted(
            (
                event
                for event in self._inbound_chat_events
                if event.tenant_id == tenant_id
                and event.processed_at is None
                and event.received_at < older_than
            ),
            key=lambda event: (event.received_at, event.message_ref),
        )

    async def purge_processed_older_than(self, tenant_id: str, cutoff: datetime) -> int:
        retained = [
            event
            for event in self._inbound_chat_events
            if event.tenant_id != tenant_id
            or event.processed_at is None
            or event.processed_at >= cutoff
        ]
        deleted_count = len(self._inbound_chat_events) - len(retained)
        self._inbound_chat_events = retained
        return deleted_count

    async def upsert_users(self, users: Sequence[DirectoryUser]) -> None:
        for user in users:
            self._directory_users[(user.tenant_id, user.external_id)] = user

    async def search_directory_users(
        self,
        tenant_id: str,
        query: str = "",
        limit: int = 25,
        offset: int = 0,
    ) -> list[DirectoryUser]:
        query_value = query.strip().lower()
        filtered = sorted(
            (
                user
                for (user_tenant, _), user in self._directory_users.items()
                if user_tenant == tenant_id
                and user.is_active
                and (
                    not query_value
                    or query_value in user.display_name.lower()
                    or (user.email is not None and query_value in user.email.lower())
                    or (user.handle is not None and query_value in user.handle.lower())
                    or query_value in user.external_id.lower()
                )
            ),
            key=lambda user: (user.display_name.lower(), user.external_id),
        )
        return filtered[offset : offset + limit]

    async def count_directory_users(self, tenant_id: str, query: str = "") -> int:
        query_value = query.strip().lower()
        return sum(
            1
            for (user_tenant, _), user in self._directory_users.items()
            if user_tenant == tenant_id
            and user.is_active
            and (
                not query_value
                or query_value in user.display_name.lower()
                or (user.email is not None and query_value in user.email.lower())
                or (user.handle is not None and query_value in user.handle.lower())
                or query_value in user.external_id.lower()
            )
        )

    async def get_directory_user(self, tenant_id: str, external_id: str) -> DirectoryUser | None:
        return self._directory_users.get((tenant_id, external_id))

    async def deactivate_missing_directory_users(
        self, tenant_id: str, seen_external_ids: Sequence[str]
    ) -> int:
        seen = set(seen_external_ids)
        count = 0
        for key, user in list(self._directory_users.items()):
            if key[0] != tenant_id:
                continue
            if user.external_id in seen or not user.is_active:
                continue
            self._directory_users[key] = DirectoryUser(
                tenant_id=user.tenant_id,
                external_id=user.external_id,
                display_name=user.display_name,
                email=user.email,
                handle=user.handle,
                avatar_url=user.avatar_url,
                title=user.title,
                is_active=False,
                source=user.source,
                synced_at=user.synced_at,
                metadata=dict(user.metadata),
            )
            count += 1
        return count

    def _active_edges_from(self, tenant_id: str, node_id: str, as_of: date) -> list[GraphEdge]:
        return [
            edge
            for edge in self._edges
            if edge.tenant_id == tenant_id
            and edge.from_node_id == node_id
            and edge.kind in {EdgeKind.CONTAINS, EdgeKind.ASSIGNED_TO}
            and edge.is_active_on(as_of)
        ]


@dataclass
class InMemoryDirectoryUserRepository(DirectoryUserRepository):
    store: InMemoryGraphStore

    async def upsert_users(self, users: Sequence[DirectoryUser]) -> None:
        await self.store.upsert_users(users)

    async def search(
        self,
        tenant_id: str,
        query: str = "",
        limit: int = 25,
        offset: int = 0,
    ) -> list[DirectoryUser]:
        return await self.store.search_directory_users(
            tenant_id, query=query, limit=limit, offset=offset
        )

    async def count(self, tenant_id: str, query: str = "") -> int:
        return await self.store.count_directory_users(tenant_id, query=query)

    async def get(self, tenant_id: str, external_id: str) -> DirectoryUser | None:
        return await self.store.get_directory_user(tenant_id, external_id)

    async def deactivate_missing(self, tenant_id: str, seen_external_ids: Sequence[str]) -> int:
        return await self.store.deactivate_missing_directory_users(tenant_id, seen_external_ids)


def _fact_identity(fact: FactEvent) -> tuple[str, str, str, str, str, datetime]:
    return (
        fact.tenant_id,
        fact.source,
        fact.entity_ref.kind.value,
        fact.entity_ref.id,
        fact.correlation_id,
        fact.observed_at,
    )


def _sort_cross_person_requests(
    requests: Iterable[CrossPersonRequest],
) -> list[CrossPersonRequest]:
    return sorted(
        requests,
        key=lambda request: (request.created_at, request.updated_at, request.id),
        reverse=True,
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


def _conversation_identity(
    turn: ConversationTurn,
) -> tuple[str, str, str, date, str, str | None, str | None, datetime]:
    return (
        turn.tenant_id,
        turn.developer_id,
        turn.conversation_id,
        turn.conversation_date,
        turn.role.value,
        turn.correlation_id,
        turn.chat_message_id,
        turn.observed_at,
    )


def _turn_last_accessed_at(turn: ConversationTurn) -> datetime:
    return turn.last_accessed_at or turn.observed_at


def _checkin_with_last_accessed_at(checkin: CheckIn) -> CheckIn:
    if checkin.last_accessed_at is not None:
        return checkin
    return replace(checkin, last_accessed_at=_checkin_last_accessed_at(checkin))


def _checkin_last_accessed_at(checkin: CheckIn) -> datetime:
    return checkin.last_accessed_at or checkin.replied_at or checkin.asked_at
