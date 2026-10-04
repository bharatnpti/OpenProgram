from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import date, datetime
from typing import Protocol

from core.domain.blockers import DeveloperBlocker
from core.domain.brief import BriefKind, NarrativeBrief
from core.domain.conversation import ConversationTurn
from core.domain.cross_person import CrossPersonRequest, CrossPersonRequestStatus
from core.domain.dead_letter import DeadLetter
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
from core.domain.identity import IdentityLink
from core.domain.inbound import InboundChatEvent
from core.domain.integrations import SyncCursor, SyncCursorRecord
from core.domain.rollup import NodeStatus
from core.domain.status import (
    CheckIn,
    CheckInClarification,
    CheckInCorrelation,
    CheckInNudge,
    CheckInPreference,
    CheckInScheduleRun,
    CheckInSignals,
    DeveloperStatus,
)
from core.domain.writeback import WriteBackAudit


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

    async def pods_containing_developer(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> list[GraphNode]: ...

    async def pods_for_task(self, tenant_id: str, task_id: str, as_of: date) -> list[GraphNode]: ...


class TimeSeriesRepository(Protocol):
    async def append_fact(self, fact: FactEvent) -> None: ...

    async def append_fact_once(self, fact: FactEvent) -> None: ...

    async def list_facts(
        self,
        tenant_id: str,
        entity_ref: EntityRef,
        since: datetime | None = None,
    ) -> list[FactEvent]: ...

    async def list_recent_facts(
        self,
        tenant_id: str,
        since: datetime | None = None,
        sources: Sequence[str] | None = None,
        limit: int = 100,
    ) -> list[FactEvent]: ...


class CrossPersonRequestRepository(Protocol):
    async def create(self, request: CrossPersonRequest) -> CrossPersonRequest: ...

    async def get(self, tenant_id: str, request_id: str) -> CrossPersonRequest | None: ...

    async def update_status(
        self,
        tenant_id: str,
        request_id: str,
        status: CrossPersonRequestStatus,
        updated_at: datetime,
        *,
        from_statuses: Sequence[CrossPersonRequestStatus] | None = None,
    ) -> CrossPersonRequest | None:
        """Move a request to ``status``: one conditional update, a transition.

        Only a request not in ``status`` already, and with ``from_statuses``
        in one of them, changes. Returns the changed request, or None when
        nothing changed: of two writers moving the same request to the same
        status (two merge passes, a reply racing a merge) only one gets it
        back, and only that one tells anybody.
        """
        ...

    async def record_notification(
        self,
        tenant_id: str,
        request_id: str,
        *,
        notify_message_id: str,
        notify_correlation_id: str,
        updated_at: datetime,
    ) -> CrossPersonRequest | None: ...

    async def assign_counterpart(
        self,
        tenant_id: str,
        request_id: str,
        *,
        counterpart_id: str,
        counterpart_display_name: str | None,
        counterpart_email: str | None,
        updated_at: datetime,
    ) -> CrossPersonRequest | None:
        """Give a needs_resolution request its counterpart and open it.

        One conditional update: only a request still in needs_resolution
        changes, so a redelivered answer cannot reassign or reopen one.
        Returns the opened request, or None when nothing changed.
        """
        ...

    async def refresh(
        self,
        tenant_id: str,
        request_id: str,
        *,
        task_ref: EntityRef | None,
        updated_at: datetime,
    ) -> CrossPersonRequest | None:
        """A repeated ask: mark a still-open request as asked again.

        Only an open or acknowledged request changes: ``updated_at`` moves
        forward and the issue link is filled where the request has none. Its
        status, note and notification stay as they are, so nobody is told
        twice. Returns the request, or None when it is closed or missing.
        """
        ...

    async def claim_notification_attempt(
        self,
        tenant_id: str,
        request_id: str,
        *,
        expected_attempts: int,
        attempted_at: datetime,
        next_attempt_at: datetime | None,
    ) -> CrossPersonRequest | None:
        """Count one counterpart DM attempt, atomically, before it is sent.

        Succeeds only while the request is open, has a counterpart, has no
        notification recorded and still has ``expected_attempts`` attempts, so
        of two senders that read the same row only one can claim the attempt.
        Returns the claimed request, or None when the claim lost.
        """
        ...

    async def list_notification_retries_due(
        self,
        tenant_id: str,
        *,
        due_at: datetime,
        max_attempts: int,
        limit: int,
    ) -> list[CrossPersonRequest]:
        """Open requests whose counterpart DM failed and whose next attempt is due."""
        ...

    async def list_for_counterpart(
        self,
        tenant_id: str,
        counterpart_id: str,
        statuses: Sequence[CrossPersonRequestStatus] | None = None,
    ) -> list[CrossPersonRequest]: ...

    async def list_for_requester(
        self,
        tenant_id: str,
        requester_id: str,
        statuses: Sequence[CrossPersonRequestStatus] | None = None,
    ) -> list[CrossPersonRequest]: ...

    async def list_open(self, tenant_id: str) -> list[CrossPersonRequest]: ...

    async def get_by_notify_correlation(
        self,
        tenant_id: str,
        notify_correlation_id: str,
    ) -> CrossPersonRequest | None: ...

    async def get_by_notify_message_id(
        self,
        tenant_id: str,
        message_id: str,
    ) -> CrossPersonRequest | None: ...


class StatusRepository(Protocol):
    async def record_checkin(self, checkin: CheckIn) -> None: ...

    async def record_checkin_reply_once(self, checkin: CheckIn) -> bool: ...

    async def record_open_checkin_signals(
        self, tenant_id: str, correlation_id: str, signals: CheckInSignals
    ) -> bool:
        """Keep what an open check-in's replies said so far; touch nothing else.

        Only the signals of a check-in that is not finalized (``replied_at`` is
        unset) change; ``replied_at`` and ``raw_reply`` stay as they are. Returns
        False and writes nothing once the check-in is finalized, or when there
        is no such check-in.
        """
        ...

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

    async def checkin_schedule_run_for_correlation(
        self, tenant_id: str, correlation_id: str
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

    async def record_developer_blockers(
        self, tenant_id: str, blockers: Sequence[DeveloperBlocker]
    ) -> None: ...

    async def record_developer_status_with_blockers(
        self, status: DeveloperStatus, blockers: Sequence[DeveloperBlocker]
    ) -> None: ...

    async def resolve_developer_blockers(
        self,
        tenant_id: str,
        developer_id: str,
        blockers: Sequence[DeveloperBlocker],
        *,
        status_as_of: date,
        revise_status: Callable[
            [DeveloperStatus, Sequence[DeveloperBlocker]], DeveloperStatus | None
        ],
    ) -> tuple[DeveloperBlocker, ...]:
        """Resolve open blockers of one person, each at most once.

        ``blockers`` are rows as read, carrying their resolution
        (``resolved_on``, ``resolved_reason``, ``last_seen_on``). Each row
        changes in one conditional update, only while it is unresolved and
        unchanged since it was read (``updated_at``): of two passes resolving
        the same blocker only one changes it, and one the person restated in
        between stays open. In the same transaction the person's latest
        status as of ``status_as_of`` is read under a row lock and replaced
        by ``revise_status(status, changed)``, unless that returns None, so
        the status projection stays in step. Returns the rows changed here.
        """
        ...

    async def open_blockers(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> list[DeveloperBlocker]: ...

    async def open_blockers_for_developers(
        self, tenant_id: str, developer_ids: Sequence[str], as_of: date
    ) -> list[DeveloperBlocker]: ...

    async def blockers_for_work_item(
        self, tenant_id: str, work_item_id: str, as_of: date
    ) -> list[DeveloperBlocker]: ...

    async def has_blocker_rows(self, tenant_id: str, developer_id: str) -> bool: ...

    async def latest_developer_status(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> DeveloperStatus | None: ...

    async def developers_without_checkin(self, tenant_id: str, as_of: date) -> list[str]: ...

    async def purge_checkin_raw_replies_older_than(
        self, tenant_id: str, cutoff: datetime
    ) -> int: ...


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


class InboundChatEventRepository(Protocol):
    """Durable buffer for inbound chat events (fast-ack + coalesced processing).

    Raw DM content lives in these rows; keep it out of logs, traces, persona
    views, and public APIs, and purge it on the conversation retention path.
    """

    async def append(self, event: InboundChatEvent) -> bool: ...

    async def list_unprocessed_for_conversation(
        self, tenant_id: str, conversation_key: str
    ) -> list[InboundChatEvent]: ...

    async def mark_processed(
        self, tenant_id: str, event_ids: Sequence[str], processed_at: datetime
    ) -> None: ...

    async def list_stuck(self, tenant_id: str, older_than: datetime) -> list[InboundChatEvent]: ...

    async def purge_processed_older_than(self, tenant_id: str, cutoff: datetime) -> int: ...


class RollupRepository(Protocol):
    async def record_node_status(self, status: NodeStatus) -> None: ...

    def exclusive_day(self, tenant_id: str, as_of: date) -> AbstractAsyncContextManager[None]:
        """Hold the tenant's rollup of ``as_of`` alone while it is read and recorded.

        Two rollups of one day at once (the hourly one and a refresh after a
        resolution, N27) each read the tree and the statuses and then write row
        by row, so the slower one could leave rows from what it read earlier
        over the newer ones. Held around the read and the writes, the second
        waits, then reads what the first stored and what changed since.
        """
        ...

    async def latest_node_status(
        self, tenant_id: str, entity_ref: EntityRef, as_of: date
    ) -> NodeStatus | None: ...

    async def list_node_statuses(self, tenant_id: str, as_of: date) -> list[NodeStatus]: ...

    async def node_status_history(
        self, tenant_id: str, entity_ref: EntityRef, start: date, end: date
    ) -> list[NodeStatus]: ...


class NarrativeBriefRepository(Protocol):
    """Persist scheduled narrative briefs for later dashboard retrieval.

    Briefs are descriptive rollups only -- never raw check-in/DM/reply content.
    """

    async def record_brief(self, brief: NarrativeBrief) -> None: ...

    async def latest_briefs(
        self,
        tenant_id: str,
        kind: BriefKind | None = None,
        limit: int = 20,
    ) -> list[NarrativeBrief]: ...


class DeadLetterRepository(Protocol):
    """Durable record of workflow events whose retries were exhausted.

    Rows carry only identifiers and diagnostics -- never raw DM/reply content.
    """

    async def record_dead_letter(self, dl: DeadLetter) -> None: ...

    async def list_open_dead_letters(
        self, tenant_id: str, limit: int = 100
    ) -> list[DeadLetter]: ...

    async def get_dead_letter(self, tenant_id: str, id: str) -> DeadLetter | None: ...

    async def mark_dead_letter_rearmed(
        self, tenant_id: str, id: str, rearmed_at: datetime
    ) -> DeadLetter | None: ...

    async def count_open_dead_letters(self, tenant_id: str) -> int: ...


class SyncCursorRepository(Protocol):
    async def get_cursor(self, tenant_id: str, connector: str, scope: str) -> SyncCursor: ...

    async def record_cursor(
        self, tenant_id: str, connector: str, scope: str, cursor: SyncCursor
    ) -> None: ...

    async def list_cursors(self, tenant_id: str) -> list[SyncCursorRecord]:
        """Every stored cursor for a tenant, ordered by connector then scope."""
        ...


class IdentityLinkRepository(Protocol):
    """Persist provider-neutral identity links keyed by tenant and developer."""

    async def get_identity_link(self, tenant_id: str, developer_id: str) -> IdentityLink | None: ...

    async def upsert_identity_link(self, link: IdentityLink) -> None: ...

    async def list_identity_links(self, tenant_id: str) -> list[IdentityLink]: ...


class WriteBackConfigRepository(Protocol):
    """Persist the tenant-level system gate override for issue-tracker write-back.

    Returns ``None`` when no override is stored so callers can fall back to the
    static settings default. This is the admin-controlled system gate.
    """

    async def get_writeback_enabled(self, tenant_id: str) -> bool | None: ...

    async def set_writeback_enabled(self, tenant_id: str, enabled: bool) -> None: ...


class WriteBackAuditRepository(Protocol):
    """Append-only audit log of gated issue-tracker writes."""

    async def record(self, audit: WriteBackAudit) -> None: ...

    async def list_for_issue(self, tenant_id: str, issue_key: str) -> list[WriteBackAudit]: ...

    async def list_writeback_by_correlation(
        self, tenant_id: str, correlation_id: str
    ) -> list[WriteBackAudit]: ...

    async def find_existing(
        self,
        tenant_id: str,
        issue_key: str,
        target_state: str,
        correlation_id: str,
    ) -> WriteBackAudit | None: ...

    async def get_writeback_audit(self, tenant_id: str, audit_id: str) -> WriteBackAudit | None: ...

    async def count_applied_writebacks(
        self, tenant_id: str, since: datetime | None = None
    ) -> int: ...

    async def list_applied_writebacks(
        self, tenant_id: str, limit: int, since: datetime | None = None
    ) -> list[WriteBackAudit]: ...


class VectorStore(Protocol):
    async def upsert_embedding(
        self, tenant_id: str, entity_ref: EntityRef, vector: Sequence[float]
    ) -> None: ...

    async def search(
        self, tenant_id: str, vector: Sequence[float], limit: int
    ) -> list[VectorMatch]: ...
