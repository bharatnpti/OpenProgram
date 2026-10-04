from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from functools import partial
from typing import Literal, Protocol, TypedDict, cast
from uuid import uuid4

import structlog
from langgraph.graph import StateGraph
from opentelemetry import trace

from core.application.agents.tool_loop import ToolCallingAgent
from core.application.blocker_lifecycle import (
    BlockerLifecycleService,
    reconciliation_with_updates,
)
from core.application.checkin_drift import (
    COMMIT_FACT_SOURCE,
    DEVELOPER_ROLE,
    NON_CODE_REVIEW_ROLES,
    code_work_keys,
    eta_stated_fact,
    in_review_claim_keys,
    issue_eta,
    keys_asked_for_merge_request,
    keys_without_open_merge_request,
    member_roles,
    review_without_merge_request_fact,
    review_without_merge_request_question,
)
from core.application.checkin_signals import merge_checkin_signals
from core.application.conversation_history import llm_messages_from_turns
from core.application.counterparts import (
    MemberContact,
    MemberDirectory,
    member_named_in_answer,
    members_for_mention,
    name_words,
)
from core.application.merge_request_links import MERGE_REQUEST_FACT_SOURCE
from core.application.risk_service import RISK_FACT_SOURCE
from core.application.status_parsing import (
    ClarificationDecision,
    ClarificationEvaluator,
    StatusParser,
)
from core.application.status_summaries import (
    NO_REPLY_BLOCKER,
    NON_STATUS_REPLY_SUMMARY,
    UNKNOWN_SUMMARY,
    basis_status,
    inferred_summary,
    names_one_basis,
    stale_summary,
    with_no_active_work,
)
from core.application.tools.conversation_history import MAX_HISTORY_LIMIT, ConversationHistoryTool
from core.application.tools.git_activity import GitActivityTool
from core.application.tools.issue_tracker import IssueTrackerTool
from core.application.writeback_service import (
    CHECKIN_CLOSED_SOURCE,
    CONSENT_REPLY_SOURCE,
    OPEN_MR_SOURCE,
    OpenMergeRequestHold,
    WriteBackService,
    canonical_target_state,
    interpret_consent_answer,
    target_state_label,
)
from core.domain.blockers import (
    BlockerReconciliation,
    BlockerSource,
    DeveloperBlocker,
    ReconcileMode,
)
from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestResolution,
    CrossPersonRequestStatus,
)
from core.domain.errors import ProviderUnavailable
from core.domain.escalation import EscalationTarget
from core.domain.graph import EdgeKind, EntityRef, FactEvent, GraphNode, JsonScalar, NodeKind
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.llm import LlmRequest, LlmResponse
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from core.domain.status import (
    CheckIn,
    CheckInClarification,
    CheckInCorrelation,
    CheckInNudge,
    CheckInSignals,
    CrossPersonMention,
    DeveloperStatus,
    IssueClaim,
    StatusSource,
    local_date,
    resolve_timezone,
)
from core.domain.writeback import WriteBackAudit, WriteBackStatus, WriteBackTarget
from core.ports.chat import ChatProvider
from core.ports.directory import DirectoryUserRepository
from core.ports.issue_tracker import IssueTracker
from core.ports.llm import LlmProvider
from core.ports.repositories import (
    ConversationRepository,
    CrossPersonRequestRepository,
    GraphRepository,
    IdentityLinkRepository,
    StatusRepository,
    TimeSeriesRepository,
)
from core.ports.tools import AgentTool

# Default lookback for recent facts fed into check-in context; overridable via
# Settings (recent_fact_lookback_days) through the StatusCollector constructor.
RECENT_FACT_LOOKBACK_DAYS = 30
RECENT_CONVERSATION_LOOKBACK = timedelta(hours=24)
RECENT_CONVERSATION_TURN_LIMIT = 20
# Merge request facts read to find the request an "in review" claim names (as
# the write-back's open merge request gate reads them).
_MERGE_REQUEST_FACT_SCAN_LIMIT = 5000
_logger = structlog.get_logger(__name__)
COMPOSE_CHECKIN_SYSTEM_PROMPT = (
    "Compose a concise daily check-in DM. Use prior conversation turns as context, but do not "
    "quote private history unless it directly helps the ask. Return one plain chat DM with no "
    "labels, preamble, quoted prompt text, or markdown table."
)
COMPOSE_NUDGE_SYSTEM_PROMPT = (
    "Compose a concise follow-up DM for a pending status check-in. Use prior conversation turns "
    "as context and avoid assuming status is healthy without a reply. Return one plain chat DM "
    "with no labels, preamble, quoted prompt text, or markdown table."
)
# Default outbound DM safety cap (prompt-echo + length guard); overridable via
# Settings (outbound_dm_max_chars) through the StatusCollector constructor.
OUTBOUND_DM_MAX_CHARS = 320
# The one ack a check-in gets for replies that carry no status (N22).
NON_STATUS_ACK_TEXT = "Thanks. I'll keep the check-in open for your status update."
_PROMPT_ECHO_MARKERS = (
    "mock status summary:",
    "return only the message text",
    "write a concise",
    "write one short",
    "asking for today's work status",
    "ask for progress, blockers, and eta changes",
)


class StatusCollectorState(TypedDict, total=False):
    tenant_id: str
    developer_id: str
    developer_name: str
    chat_external_id: str
    correlation_id: str
    asked_at: datetime
    checkin_date: date | None
    context: str
    dm_text: str
    message_id: str
    chat_thread_ref: str
    chat_user_ref: str
    trace_id: str
    checkin: CheckIn


class StatusCollectorGraph(Protocol):
    async def ainvoke(self, input: StatusCollectorState) -> StatusCollectorState: ...


@dataclass(frozen=True, kw_only=True)
class ReplyOutcome:
    kind: Literal["processed", "clarifying", "ignored", "acknowledged"]
    status: DeveloperStatus | None = None
    cross_person_requests: tuple[CrossPersonRequestResolution, ...] = ()


@dataclass(kw_only=True)
class _RequestLedger:
    """One check-in's cross-person requests while a reply is handled.

    ``recorded`` are the requests already stored for this check-in; this reply
    adds ``resolutions``: new requests, and answers that settle a recorded
    needs_resolution one. A request is stored the turn it is stated, so a
    later turn that re-extracts it without its person, or drops it, cannot
    lose it.
    """

    recorded: tuple[CrossPersonRequest, ...] = ()
    resolutions: list[CrossPersonRequestResolution] = field(default_factory=list)

    def outcome(self) -> tuple[CrossPersonRequestResolution, ...]:
        return tuple(self.resolutions)

    def unsettled(self) -> list[CrossPersonRequest]:
        """Recorded requests still waiting for their person after this reply."""
        settled = {resolution.request_id for resolution in self.resolutions}
        return [
            request
            for request in self.recorded
            if request.status is CrossPersonRequestStatus.NEEDS_RESOLUTION
            and request.id not in settled
        ]

    def unsettled_for(self, mention: CrossPersonMention) -> CrossPersonRequest | None:
        key = _request_key(mention.kind, mention.raw_name)
        return next(
            (
                request
                for request in self.unsettled()
                if _request_key(request.kind.value, request.raw_name or "") == key
            ),
            None,
        )

    def knows(self, mention: CrossPersonMention, counterpart_id: str | None) -> bool:
        """Whether this check-in already holds the request, stored or added by this reply."""
        key = _request_key(mention.kind, mention.raw_name)
        held = [
            (request.kind.value, request.raw_name or "", request.counterpart_id)
            for request in self.recorded
        ] + [
            (resolution.mention.kind, resolution.mention.raw_name, resolution.counterpart_id)
            for resolution in self.resolutions
        ]
        return any(
            _request_key(kind, raw_name) == key
            or (counterpart_id is not None and kind == mention.kind and held_id == counterpart_id)
            for kind, raw_name, held_id in held
        )


class StatusCollector:
    def __init__(
        self,
        *,
        issue_tracker: IssueTracker,
        chat_provider: ChatProvider,
        llm_provider: LlmProvider,
        status_repository: StatusRepository,
        conversation_repository: ConversationRepository,
        model: str,
        time_series_repository: TimeSeriesRepository | None = None,
        directory_repository: DirectoryUserRepository | None = None,
        identity_link_repository: IdentityLinkRepository | None = None,
        write_back_service: WriteBackService | None = None,
        graph_repository: GraphRepository | None = None,
        cross_person_repository: CrossPersonRequestRepository | None = None,
        parser: StatusParser | None = None,
        clarification_evaluator: ClarificationEvaluator | None = None,
        tool_agent: ToolCallingAgent | None = None,
        conversation_retention_days: int = 30,
        checkin_max_clarifications: int = 2,
        checkin_ack_enabled: bool = True,
        tenant_default_timezone: str = "UTC",
        outbound_dm_max_chars: int = OUTBOUND_DM_MAX_CHARS,
        recent_fact_lookback_days: int = RECENT_FACT_LOOKBACK_DAYS,
    ) -> None:
        self._issue_tracker = issue_tracker
        self._chat_provider = chat_provider
        self._llm_provider = llm_provider
        self._status_repository = status_repository
        self._time_series_repository = time_series_repository
        self._directory_repository = directory_repository
        self._identity_link_repository = identity_link_repository
        # A request can only name a member: the directory is a lookup table for
        # adding members, never a pool of counterparts to DM.
        self._members = MemberDirectory(
            graph_repository=graph_repository,
            identity_link_repository=identity_link_repository,
            directory_repository=directory_repository,
        )
        # Read only, to see which requests a check-in already holds; the
        # requests themselves are written by CrossPersonRequestService.
        self._cross_person_repository = cross_person_repository
        self._write_back_service = write_back_service
        # Without a graph repository the attribution machinery degrades
        # cleanly: no pods resolve, so no attribution question is ever asked.
        self._graph_repository = graph_repository
        self._blockers = BlockerLifecycleService(status_repository, graph_repository)
        self._conversation_repository = conversation_repository
        self._model = model
        self._tool_agent = tool_agent
        self._conversation_retention_days = conversation_retention_days
        self._checkin_max_clarifications = max(0, checkin_max_clarifications)
        self._checkin_ack_enabled = checkin_ack_enabled
        self._tenant_default_timezone = tenant_default_timezone
        self._outbound_dm_max_chars = outbound_dm_max_chars
        self._recent_fact_lookback_days = recent_fact_lookback_days
        self._parser = parser or StatusParser(llm_provider, model, tool_agent=tool_agent)
        self._clarification_evaluator = clarification_evaluator or ClarificationEvaluator(
            llm_provider,
            model,
            tool_agent=tool_agent,
        )
        self._compiled_graph = self._compile_graph()

    def graph(self) -> StatusCollectorGraph:
        return self._compiled_graph

    async def start_checkin(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        developer_name: str | None = None,
        chat_external_id: str | None = None,
        correlation_id: str | None = None,
        asked_at: datetime | None = None,
        checkin_date: date | None = None,
    ) -> CheckIn:
        state = await self._compiled_graph.ainvoke(
            {
                "tenant_id": tenant_id,
                "developer_id": developer_id,
                "developer_name": developer_name or developer_id,
                "chat_external_id": chat_external_id or developer_id,
                "correlation_id": correlation_id or _new_correlation_id(),
                "asked_at": asked_at or datetime.now(tz=UTC),
                "checkin_date": checkin_date,
            }
        )
        checkin = state.get("checkin")
        if not isinstance(checkin, CheckIn):
            message = "status collector graph did not record a check-in"
            raise RuntimeError(message)
        return checkin

    async def handle_reply(
        self,
        message: InboundMessage,
        *,
        allow_reprocess: bool = False,
    ) -> ReplyOutcome:
        checkin = await self._status_repository.checkin_by_correlation(
            message.tenant_id,
            message.correlation_id,
        )
        if checkin is None:
            error = "inbound reply does not match a recorded check-in"
            raise ValueError(error)

        _logger.info(
            "status_reply_received",
            tenant_id=message.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=message.correlation_id,
            message_id=message.message_id,
            reply_length=len(message.text),
        )
        span = trace.get_current_span()
        span.set_attribute("openprogram.reply_length", len(message.text))

        handled = await self._reply_handled_before_reading(
            checkin, message, allow_reprocess=allow_reprocess
        )
        if handled is not None:
            return handled
        requests = await self._request_ledger(checkin)
        await self._settle_waiting_requests(checkin, requests, message.text)
        # What this person's earlier messages in this check-in said (stored as
        # each one was read); None for the first message. This message is read
        # on its own and then merged in, so a short answer to a follow-up never
        # drops what the first reply said (N8).
        earlier = checkin.signals

        conversation_turns = await self._recent_conversation_turns(
            tenant_id=message.tenant_id,
            developer_id=checkin.developer_id,
            exclude_chat_message_id=message.message_id,
            reference_at=message.received_at,
        )
        tools = self._agent_tools(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            reference_at=message.received_at,
        )
        prior_blockers = await self._prior_open_blockers(checkin, message.received_at)
        decision = await self._clarification_evaluator.evaluate(
            tenant_id=message.tenant_id,
            developer_id=checkin.developer_id,
            raw_reply=message.text,
            correlation_id=message.correlation_id,
            conversation_turns=conversation_turns,
            tools=tools,
            prior_blockers=prior_blockers,
            tracker_write_back=await self._tracker_write_back_open(checkin),
        )
        decision = await self._without_tracker_update_question(checkin, decision, earlier=earlier)
        decision = self._without_answered_follow_up(checkin, decision, earlier=earlier)
        if not decision.is_status_update:
            await self._send_non_status_ack(checkin=checkin, message=message)
            span.set_attribute("openprogram.reply_classification", "non_status")
            span.set_attribute("openprogram.has_blocker", False)
            return ReplyOutcome(kind="acknowledged", cross_person_requests=requests.outcome())

        clarification_count = await self._status_repository.checkin_clarification_count(
            checkin.tenant_id,
            checkin.correlation_id,
        )
        insufficient_outcome = await self._maybe_insufficient_reply_clarification(
            checkin=checkin,
            message=message,
            decision=decision,
            earlier=earlier,
            prior_blockers=prior_blockers,
            clarification_count=clarification_count,
        )
        if insufficient_outcome is not None:
            await self._hold_cross_person_requests(
                checkin=checkin,
                signals=decision.signals,
                requests=requests,
                clarification_count=clarification_count,
            )
            span.set_attribute("openprogram.reply_classification", "clarifying")
            span.set_attribute(
                "openprogram.has_blocker",
                bool(decision.signals and decision.signals.blockers),
            )
            return replace(insufficient_outcome, cross_person_requests=requests.outcome())

        signals = decision.signals or await self._parser.parse_reply(
            tenant_id=message.tenant_id,
            developer_id=checkin.developer_id,
            raw_reply=message.text,
            correlation_id=message.correlation_id,
            conversation_turns=conversation_turns,
            tools=tools,
            prior_blockers=prior_blockers,
        )
        # Blockers stay this message's own: the lifecycle carries the earlier
        # messages' blockers (persisted with their partial status) and resolves
        # them only when a message says so.
        reconciliation = await self._reconcile_reply_blockers(
            checkin=checkin,
            at=message.received_at,
            prior=prior_blockers,
            signals=signals,
            raw_reply=message.text,
        )
        merged = merge_checkin_signals(earlier, signals)
        required_details_outcome = await self._maybe_required_details_clarification(
            checkin=checkin,
            message=message,
            signals=merged,
            reconciliation=reconciliation,
            clarification_count=clarification_count,
        )
        if required_details_outcome is not None:
            await self._hold_cross_person_requests(
                checkin=checkin,
                signals=signals,
                requests=requests,
                clarification_count=clarification_count,
            )
            span.set_attribute("openprogram.reply_classification", "clarifying")
            span.set_attribute(
                "openprogram.has_blocker",
                bool(required_details_outcome.status and required_details_outcome.status.blockers),
            )
            return replace(required_details_outcome, cross_person_requests=requests.outcome())
        final_signals = merged
        if not decision.sufficient:
            final_signals = _signals_with_note(
                merged,
                "Clarification cap reached before all details were confirmed.",
            )
        person_question = await self._resolve_cross_person_requests(
            checkin=checkin,
            signals=signals,
            requests=requests,
            clarification_count=clarification_count,
            ask=True,
        )
        if person_question is not None:
            # Same reasoning as the clarification branch above: hold the reply's
            # own signals rather than letting the day read as no reply at all.
            await self._hold_open_checkin_signals(checkin, merged)
            person_partial_status = await self._record_partial_checkin_status(
                checkin=checkin,
                as_of_at=message.received_at,
                signals=final_signals,
                reconciliation=reconciliation,
            )
            await self._send_clarification(
                checkin=checkin,
                message=message,
                question=person_question,
                clarification_number=clarification_count + 1,
            )
            span.set_attribute("openprogram.reply_classification", "needs_person_resolution")
            span.set_attribute("openprogram.has_blocker", bool(final_signals.blockers))
            return ReplyOutcome(
                kind="clarifying",
                status=person_partial_status,
                cross_person_requests=requests.outcome(),
            )
        late_outcome, reconciliation = await self._maybe_late_clarification(
            checkin=checkin,
            message=message,
            signals=final_signals,
            open_signals=merged,
            reconciliation=reconciliation,
            clarification_count=clarification_count,
        )
        if late_outcome is not None:
            span.set_attribute("openprogram.reply_classification", "clarifying")
            span.set_attribute(
                "openprogram.has_blocker",
                bool(late_outcome.status and late_outcome.status.blockers),
            )
            return replace(late_outcome, cross_person_requests=requests.outcome())
        consent_question = await self._maybe_ask_write_back_consent(
            checkin=checkin,
            message=message,
            signals=final_signals,
            open_signals=merged,
            reconciliation=reconciliation,
        )
        if consent_question is not None:
            span.set_attribute("openprogram.reply_classification", "awaiting_consent")
            return replace(consent_question, cross_person_requests=requests.outcome())
        status = await self._finalize_checkin_reply(
            checkin=checkin,
            replied_at=message.received_at,
            raw_reply=(
                message.text
                if earlier is None
                else _checkin_messages_text(checkin, conversation_turns, message)
            ),
            signals=final_signals,
            reconciliation=reconciliation,
        )
        span.set_attribute("openprogram.reply_classification", "status_update")
        span.set_attribute("openprogram.has_blocker", bool(status.blockers))
        return ReplyOutcome(
            kind="processed",
            status=status,
            cross_person_requests=requests.outcome(),
        )

    async def _reply_handled_before_reading(
        self,
        checkin: CheckIn,
        message: InboundMessage,
        *,
        allow_reprocess: bool,
    ) -> ReplyOutcome | None:
        """What needs no reading of the reply as status, or ``None`` to read it.

        A finalized check-in (a late consent answer, else a duplicate), a
        redelivered message, or an answer to the open write-back question (G1).
        """
        if checkin.replied_at is not None:
            return await self._handle_already_replied(checkin=checkin, message=message)
        if await self._register_reply_turn(checkin, message, allow_reprocess=allow_reprocess):
            return ReplyOutcome(kind="ignored")
        return await self._maybe_answer_write_back_consent(checkin=checkin, message=message)

    async def _register_reply_turn(
        self,
        checkin: CheckIn,
        message: InboundMessage,
        *,
        allow_reprocess: bool,
    ) -> bool:
        """Record the USER turn; True means a duplicate delivery to ignore.

        Legacy single-delivery dedup: a redelivered message id on an open
        check-in is ignored. The durable drain passes ``allow_reprocess=True``
        so a retry after a transient failure can finish classify/finalize
        instead of leaving the reply permanently "ignored" (R3).
        """
        turn_already_recorded = await self._user_turn_exists(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            chat_message_id=message.message_id,
        )
        if turn_already_recorded:
            return not allow_reprocess
        await self._record_conversation_turn(
            ConversationTurn(
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                conversation_id=checkin.correlation_id,
                conversation_date=await self._local_date_for_developer(
                    checkin.tenant_id,
                    checkin.developer_id,
                    message.received_at,
                ),
                role=ConversationRole.USER,
                content=message.text,
                correlation_id=message.correlation_id,
                chat_message_id=message.message_id,
                observed_at=message.received_at,
            )
        )
        return False

    async def _reconcile_reply_blockers(
        self,
        *,
        checkin: CheckIn,
        at: datetime,
        prior: tuple[DeveloperBlocker, ...],
        signals: CheckInSignals,
        raw_reply: str,
    ) -> BlockerReconciliation:
        return await self._blockers.reconcile(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            as_of=await self._status_as_of_for_checkin(checkin, at),
            prior=prior,
            signals=signals,
            mode=_reconcile_mode_for_reply(signals, raw_reply),
            source=BlockerSource.CHECKIN,
            source_correlation_id=checkin.correlation_id,
        )

    async def _tracker_write_back_open(self, checkin: CheckIn) -> bool:
        """Whether OpenProgram writes this person's own issue updates without asking."""
        service = self._write_back_service
        if service is None:
            return False
        try:
            return await service.standing_consent_open(checkin.tenant_id, checkin.developer_id)
        except Exception:  # pragma: no cover - defensive; the hint is best-effort
            return False

    async def _without_tracker_update_question(
        self,
        checkin: CheckIn,
        decision: ClarificationDecision,
        *,
        earlier: CheckInSignals | None = None,
    ) -> ClarificationDecision:
        """Drop a follow-up asking the person to update the tracker we are about to update.

        With standing consent, a claim on the person's own issue is written to
        the tracker when the check-in is recorded. A question that asks them to
        do that themselves ("Can you update the Jira ticket to Done?") is then
        wrong, so the reply counts as sufficient and the recorded claim is
        written instead. The same holds for a ``done`` the write-back holds back
        because the issue's merge request is still open: moving it themselves
        would make the tracker wrong, and the ack says why it was left as it is.
        Every other question is kept: one naming an issue the write-back will
        not touch, and any question that asks for something else. The claims
        are the whole check-in's (``earlier`` messages merged with this one),
        the same set the write-back gets when the check-in is recorded.
        """
        service = self._write_back_service
        if (
            service is None
            or decision.sufficient
            or decision.question is None
            or decision.signals is None
        ):
            return decision
        claims = merge_checkin_signals(earlier, decision.signals).issue_updates
        if not claims or not asks_person_to_update_tracker(decision.question):
            return decision
        try:
            dry_run = await service.dry_run(
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                claims=claims,
            )
        except Exception:  # pragma: no cover - defensive; keep the question
            return decision
        handled = dry_run.written | set(dry_run.held_for_open_mr)
        named = set(_ISSUE_KEY_IN_TEXT.findall(decision.question))
        if not handled or not named <= handled:
            return decision
        _logger.info(
            "tracker_update_question_dropped",
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=checkin.correlation_id,
            issue_keys=sorted(handled),
        )
        return replace(decision, sufficient=True, question=None)

    def _without_answered_follow_up(
        self,
        checkin: CheckIn,
        decision: ClarificationDecision,
        *,
        earlier: CheckInSignals | None,
    ) -> ClarificationDecision:
        """Drop a model-drafted follow-up that asks for what an earlier message gave (N29).

        The evaluator reads the latest message on its own, so in R4 it asked
        Mina for the ETA she gave in her first message, Sofia whether she had
        blockers after "No blockers.", and Asha for progress after "agenda and
        numbers drafted". A question that only asks for the ETA, blockers or
        progress is dropped when the merged signals of the check-in's
        ``earlier`` messages (N8) already answer every one of them. What this
        message itself says stays the evaluator's call, and a question about
        anything else (the tracker, a merge request, a person) is kept.
        """
        if (
            earlier is None
            or decision.sufficient
            or decision.question is None
            or not decision.is_status_update
            or not _follow_up_already_answered(decision.question, earlier)
        ):
            return decision
        _logger.info(
            "follow_up_already_answered",
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=checkin.correlation_id,
        )
        return replace(decision, sufficient=True, question=None)

    async def _maybe_insufficient_reply_clarification(
        self,
        *,
        checkin: CheckIn,
        message: InboundMessage,
        decision: ClarificationDecision,
        earlier: CheckInSignals | None,
        prior_blockers: tuple[DeveloperBlocker, ...],
        clarification_count: int,
    ) -> ReplyOutcome | None:
        """Ask for what the reply left out, keeping hold of what it did say.

        A reply that earns a clarification is still a reply, so whatever it
        already told us is recorded as a partial status. Leaving it unrecorded
        read the day as silence: the developer's own screen still said "no reply
        yet", a blocker they had just restated kept its old last-seen date, the
        reported ETA change was dropped, and an end-of-day reconcile could
        finalize the day as unknown -- counting them as a non-replier in every
        rollup above them. ``replied_at`` stays unset, so the clarification loop
        and its timeout finalizer still own the turn. What it said is merged with
        the ``earlier`` messages of the check-in and kept on the open check-in,
        so the answer to this question is merged with it in turn.
        """
        if (
            decision.sufficient
            or decision.question is None
            or clarification_count >= self._checkin_max_clarifications
        ):
            return None
        partial_status: DeveloperStatus | None = None
        merged: CheckInSignals | None = earlier
        if decision.signals is not None:
            merged = merge_checkin_signals(earlier, decision.signals)
            await self._hold_open_checkin_signals(checkin, merged)
            partial_status = await self._record_partial_checkin_status(
                checkin=checkin,
                as_of_at=message.received_at,
                signals=merged,
                reconciliation=await self._reconcile_reply_blockers(
                    checkin=checkin,
                    at=message.received_at,
                    prior=prior_blockers,
                    signals=decision.signals,
                    raw_reply=message.text,
                ),
            )
        await self._send_clarification(
            checkin=checkin,
            message=message,
            question=_question_naming_issue(
                decision.question, await self._follow_up_subject(checkin, merged)
            ),
            clarification_number=clarification_count + 1,
        )
        return ReplyOutcome(kind="clarifying", status=partial_status)

    async def _maybe_required_details_clarification(
        self,
        *,
        checkin: CheckIn,
        message: InboundMessage,
        signals: CheckInSignals,
        reconciliation: BlockerReconciliation,
        clarification_count: int,
    ) -> ReplyOutcome | None:
        required_check_signals = _signals_with_open_blockers(signals, reconciliation)
        missing_required = _missing_required_status_details(required_check_signals)
        if not missing_required or clarification_count >= self._checkin_max_clarifications:
            return None
        await self._hold_open_checkin_signals(checkin, signals)
        partial_status = await self._record_partial_checkin_status(
            checkin=checkin,
            as_of_at=message.received_at,
            signals=required_check_signals,
            reconciliation=reconciliation,
        )
        await self._send_clarification(
            checkin=checkin,
            message=message,
            question=_missing_required_status_question(
                missing_required, await self._follow_up_subject(checkin, signals)
            ),
            clarification_number=clarification_count + 1,
        )
        return ReplyOutcome(kind="clarifying", status=partial_status)

    async def _review_without_merge_request_question(
        self,
        *,
        checkin: CheckIn,
        signals: CheckInSignals,
        clarification_count: int,
        reference_at: datetime,
    ) -> str | None:
        """Ask once for the merge request of an issue said to be in review (R1-10, SC6).

        Omar said IDP-6 was "up for review" when only a branch existed, and the
        check-in accepted it. When the check-in says an issue is in review or
        ready for review and no open merge request names it, one short question
        asks whether it is opened. It is a follow-up like any other, within the
        same limit, and is never asked twice for an issue in one check-in; if
        the check-in ends without the request, the finalize records the drift.
        """
        if clarification_count >= self._checkin_max_clarifications:
            return None
        missing = await self._in_review_without_open_merge_request(
            checkin.tenant_id, checkin.developer_id, signals.issue_updates
        )
        if not missing:
            return None
        turns = await self._conversation_turns_for_correlation(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=checkin.correlation_id,
            reference_at=reference_at,
        )
        asked = keys_asked_for_merge_request(
            turn.content for turn in turns if turn.role is ConversationRole.AGENT
        )
        keys = [key for key in missing if key not in asked]
        if not keys:
            return None
        return review_without_merge_request_question(keys)

    async def _in_review_without_open_merge_request(
        self, tenant_id: str, developer_id: str, claims: Sequence[IssueClaim]
    ) -> tuple[str, ...]:
        """The issues claimed in review, as code work, that no open merge request names.

        Read from the synced merge request facts with the shared matcher. A
        tenant with no merge request synced at all has nothing to compare with,
        so nothing is reported. Only code work expects a merge request (N26):
        Mina, a product owner, had CHK-10 "in review" for its acceptance
        criteria, and was asked for a merge request that could not exist.
        """
        keys = in_review_claim_keys(claims)
        if not keys or self._time_series_repository is None:
            return ()
        facts = await self._time_series_repository.list_recent_facts(
            tenant_id,
            sources=(MERGE_REQUEST_FACT_SOURCE,),
            limit=_MERGE_REQUEST_FACT_SCAN_LIMIT,
        )
        if not facts:
            return ()
        missing = keys_without_open_merge_request(keys, facts)
        if not missing:
            return ()
        return await self._code_work_keys(tenant_id, developer_id, missing, facts)

    async def _code_work_keys(
        self,
        tenant_id: str,
        developer_id: str,
        keys: Sequence[str],
        merge_request_facts: Sequence[FactEvent],
    ) -> tuple[str, ...]:
        """Those of ``keys`` whose review is code work for this person (``code_work_keys``)."""
        node = (
            await self._graph_repository.get_node(tenant_id, developer_id)
            if self._graph_repository is not None
            else None
        )
        roles = member_roles(node.metadata if node is not None else None)
        commits: Sequence[FactEvent] = ()
        project_keys: dict[str, frozenset[str]] = {}
        # Only a role that is neither decides by the activity: read it just then.
        decided = DEVELOPER_ROLE in roles or bool(roles & NON_CODE_REVIEW_ROLES)
        if not decided and self._time_series_repository is not None:
            commits = await self._time_series_repository.list_recent_facts(
                tenant_id, sources=(COMMIT_FACT_SOURCE,), limit=_MERGE_REQUEST_FACT_SCAN_LIMIT
            )
            project_keys = await self._project_task_keys(tenant_id)
        return code_work_keys(
            keys,
            developer_id=developer_id,
            roles=roles,
            merge_request_facts=merge_request_facts,
            commit_facts=commits,
            project_task_keys=lambda key: project_keys.get(key, frozenset()),
        )

    async def _project_task_keys(self, tenant_id: str) -> dict[str, frozenset[str]]:
        """Each task key with the keys of its project's tasks (directly or under a sprint)."""
        if self._graph_repository is None:
            return {}
        nodes = {node.id: node for node in await self._graph_repository.list_nodes(tenant_id)}
        edges = await self._graph_repository.list_edges(tenant_id, kind=EdgeKind.CONTAINS)
        parent_of = {
            edge.to_node_id: edge.from_node_id
            for edge in edges
            if (child := nodes.get(edge.to_node_id)) is not None
            and child.kind in {NodeKind.TASK, NodeKind.SPRINT}
        }
        by_project: dict[str, set[str]] = {}
        for task_id, parent in parent_of.items():
            if nodes[task_id].kind is not NodeKind.TASK:
                continue
            project = parent
            parent_node = nodes.get(parent)
            if parent_node is not None and parent_node.kind is NodeKind.SPRINT:
                project = parent_of.get(parent, parent)
            by_project.setdefault(project, set()).add(task_id)
        return {
            task_id: frozenset(task_ids) for task_ids in by_project.values() for task_id in task_ids
        }

    async def _record_issue_etas(self, checkin: CheckIn, status: DeveloperStatus) -> None:
        """Keep the ETA each claim states for its issue, to compare across people (N23).

        Ira's summary said CHK-4 by Friday while its owner Liam said Tuesday,
        and nothing noticed. Each finalized check-in records the day it gives
        per issue; the drift read flags an issue whose ETAs disagree. This
        person's own status and ETA are left exactly as recorded.
        """
        if (
            self._time_series_repository is None
            or checkin.replied_at is None
            or checkin.signals is None
        ):
            return
        etas = {
            claim.issue_key: eta
            for claim in checkin.signals.issue_updates
            if claim.issue_key and (eta := issue_eta(claim, status.as_of)) is not None
        }
        if not etas:
            return
        name = await self._developer_display_name(checkin.tenant_id, checkin.developer_id)
        for key, eta in etas.items():
            await self._time_series_repository.append_fact_once(
                eta_stated_fact(
                    tenant_id=checkin.tenant_id,
                    issue_key=key,
                    eta=eta,
                    developer_id=checkin.developer_id,
                    developer_name=name,
                    as_of=status.as_of,
                    observed_at=checkin.replied_at,
                    correlation_id=checkin.correlation_id,
                )
            )

    async def _record_review_without_merge_request(
        self, checkin: CheckIn, status: DeveloperStatus
    ) -> None:
        """The drift "says in review, no MR" for a check-in that ends without the request."""
        if (
            self._time_series_repository is None
            or checkin.replied_at is None
            or checkin.signals is None
        ):
            return
        missing = await self._in_review_without_open_merge_request(
            checkin.tenant_id, checkin.developer_id, checkin.signals.issue_updates
        )
        if not missing:
            return
        name = await self._developer_display_name(checkin.tenant_id, checkin.developer_id)
        for key in missing:
            await self._time_series_repository.append_fact_once(
                review_without_merge_request_fact(
                    tenant_id=checkin.tenant_id,
                    issue_key=key,
                    developer_id=checkin.developer_id,
                    developer_name=name,
                    as_of=status.as_of,
                    status_source=status.source,
                    observed_at=checkin.replied_at,
                    correlation_id=checkin.correlation_id,
                )
            )
        _logger.info(
            "checkin_review_without_merge_request",
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=checkin.correlation_id,
            issue_keys=list(missing),
        )

    async def _follow_up_subject(
        self, checkin: CheckIn, signals: CheckInSignals | None
    ) -> _FollowUpSubject | None:
        """The issues a follow-up is about, so the question names them (R1-9).

        The check-in's own claims come first: the issues still under way, else
        any not done. With no claim, the person's issues under way in the
        tracker. Titles come from the tracker; when it cannot be read the
        question names the keys alone. None when no issue is known at all.
        """
        try:
            assignee = await self._resolve_issue_tracker_assignee_id(
                checkin.tenant_id, checkin.developer_id
            )
            listed = await self._issue_tracker.list_active_for(
                UserRef(tenant_id=checkin.tenant_id, external_id=assignee)
            )
        except Exception:  # best effort: the question still goes out, naming keys only
            listed = []
        keys = _follow_up_issue_keys(
            signals.issue_updates if signals is not None else (), _prioritize_issues(listed)
        )
        if not keys:
            return None
        titles = {issue.key: issue.title for issue in listed}
        return _FollowUpSubject(keys=keys, text=_issue_subject_text(keys, titles))

    async def _maybe_late_clarification(
        self,
        *,
        checkin: CheckIn,
        message: InboundMessage,
        signals: CheckInSignals,
        open_signals: CheckInSignals,
        reconciliation: BlockerReconciliation,
        clarification_count: int,
    ) -> tuple[ReplyOutcome | None, BlockerReconciliation]:
        """The follow-ups asked once the reply's details and requests are settled.

        First the merge request of an issue said to be in review (R1-10), then
        which pod or work item a blocker is on. ``signals`` is what would be
        recorded now, ``open_signals`` what the open check-in keeps meanwhile.
        """
        review_question = await self._review_without_merge_request_question(
            checkin=checkin,
            signals=open_signals,
            clarification_count=clarification_count,
            reference_at=message.received_at,
        )
        if review_question is None:
            return await self._maybe_attribution_clarification(
                checkin=checkin,
                message=message,
                signals=signals,
                open_signals=open_signals,
                reconciliation=reconciliation,
                clarification_count=clarification_count,
            )
        await self._hold_open_checkin_signals(checkin, open_signals)
        partial_status = await self._record_partial_checkin_status(
            checkin=checkin,
            as_of_at=message.received_at,
            signals=signals,
            reconciliation=reconciliation,
        )
        await self._send_clarification(
            checkin=checkin,
            message=message,
            question=review_question,
            clarification_number=clarification_count + 1,
        )
        return ReplyOutcome(kind="clarifying", status=partial_status), reconciliation

    async def _maybe_attribution_clarification(
        self,
        *,
        checkin: CheckIn,
        message: InboundMessage,
        signals: CheckInSignals,
        open_signals: CheckInSignals,
        reconciliation: BlockerReconciliation,
        clarification_count: int,
    ) -> tuple[ReplyOutcome | None, BlockerReconciliation]:
        """Ask (once per blocker, ever) which pod/work item an open blocker belongs to.

        Only multi-pod developers are asked, only within the clarification
        budget, and required-detail/person clarifications always outrank this
        one. A single-pod developer's unattributed blockers auto-attribute.
        ``open_signals`` (the check-in's merged signals without any closing
        note) is what the open check-in keeps while the question is out.
        """
        candidates = tuple(
            blocker
            for blocker in self._blockers.unattributed(reconciliation.open_after)
            if blocker.attribution_asked_at is None
        )
        if not candidates:
            return None, reconciliation
        pods = await self._blockers.pods_for_developer(
            checkin.tenant_id,
            checkin.developer_id,
            await self._status_as_of_for_checkin(checkin, message.received_at),
        )
        if len(pods) == 1:
            auto_attributed = tuple(replace(blocker, pod_id=pods[0].id) for blocker in candidates)
            return None, reconciliation_with_updates(reconciliation, auto_attributed)
        if len(pods) < 2 or clarification_count >= self._checkin_max_clarifications:
            return None, reconciliation
        asked_at = datetime.now(tz=UTC)
        stamped = tuple(replace(blocker, attribution_asked_at=asked_at) for blocker in candidates)
        updated = reconciliation_with_updates(reconciliation, stamped)
        await self._hold_open_checkin_signals(checkin, open_signals)
        partial_status = await self._record_partial_checkin_status(
            checkin=checkin,
            as_of_at=message.received_at,
            signals=_signals_with_open_blockers(signals, updated),
            reconciliation=updated,
        )
        await self._send_clarification(
            checkin=checkin,
            message=message,
            question=_attribution_question(candidates, pods),
            clarification_number=clarification_count + 1,
        )
        return ReplyOutcome(kind="clarifying", status=partial_status), updated

    async def resolve_reply_correlation(self, message: InboundMessage) -> str | None:
        correlation = await self._status_repository.checkin_correlation_by_id(
            message.tenant_id,
            message.correlation_id,
        )
        if correlation is not None:
            return correlation.correlation_id

        open_thread_matches = await self._unconsumed_thread_local_matches(message)
        resolved = _single_correlation_or_log_ambiguous(
            message,
            open_thread_matches,
            chat_thread_ref=message.thread_id,
        )
        if resolved is not None or open_thread_matches:
            return resolved

        thread_correlation = await self._latest_thread_local_match(message)
        if thread_correlation is not None:
            return thread_correlation.correlation_id

        user_matches = await self._unconsumed_user_local_matches(message)
        return _single_correlation_or_log_ambiguous(message, user_matches)

    async def _unconsumed_thread_local_matches(
        self,
        message: InboundMessage,
    ) -> list[CheckInCorrelation]:
        correlations: list[CheckInCorrelation] = []
        for candidate_date in _candidate_correlation_dates(
            message.received_at,
            self._tenant_default_timezone,
        ):
            correlations.extend(
                await self._status_repository.unconsumed_checkin_correlations_for_thread(
                    message.tenant_id,
                    message.thread_id,
                    candidate_date,
                )
            )
        return await self._local_date_matches(
            _dedupe_correlations(correlations),
            message.received_at,
        )

    async def _latest_thread_local_match(
        self,
        message: InboundMessage,
    ) -> CheckInCorrelation | None:
        correlations: list[CheckInCorrelation] = []
        for candidate_date in _candidate_correlation_dates(
            message.received_at,
            self._tenant_default_timezone,
        ):
            candidate = await self._status_repository.latest_checkin_correlation_for_thread(
                message.tenant_id,
                message.thread_id,
                candidate_date,
            )
            if candidate is not None:
                correlations.append(candidate)
        return await self._latest_local_date_match(correlations, message.received_at)

    async def _unconsumed_user_local_matches(
        self,
        message: InboundMessage,
    ) -> list[CheckInCorrelation]:
        user_correlations: list[CheckInCorrelation] = []
        for candidate_date in _candidate_correlation_dates(
            message.received_at,
            self._tenant_default_timezone,
        ):
            user_correlations.extend(
                await self._status_repository.unconsumed_checkin_correlations_for_user(
                    message.tenant_id,
                    message.user.external_id,
                    candidate_date,
                )
            )
        return await self._local_date_matches(
            _dedupe_correlations(user_correlations),
            message.received_at,
        )

    async def send_nudge(
        self,
        *,
        tenant_id: str,
        correlation_id: str,
        developer_name: str | None = None,
        chat_external_id: str | None = None,
        nudge_number: int = 1,
        target: EscalationTarget = EscalationTarget.DEVELOPER,
        recipient_chat_external_id: str | None = None,
        recipient_display_name: str | None = None,
    ) -> str:
        checkin = await self._status_repository.checkin_by_correlation(
            tenant_id,
            correlation_id,
        )
        if checkin is None:
            error = "cannot nudge without a recorded check-in"
            raise ValueError(error)
        if checkin.replied_at is not None:
            error = "cannot nudge a check-in that already has a reply"
            raise ValueError(error)
        if target is not EscalationTarget.DEVELOPER and not recipient_chat_external_id:
            error = "escalation to a human target requires a recipient chat id"
            raise ValueError(error)

        existing_nudge = await self._status_repository.checkin_nudge_for(
            tenant_id,
            correlation_id,
            nudge_number,
        )
        if existing_nudge is not None:
            return existing_nudge.outbound_message_id or _pending_nudge_message_id(
                correlation_id, nudge_number
            )

        await self._status_repository.record_checkin_nudge(
            CheckInNudge(
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                nudge_number=nudge_number,
            )
        )

        if target is EscalationTarget.DEVELOPER:
            text = await self._compose_developer_nudge_text(
                tenant_id=tenant_id,
                checkin=checkin,
                developer_name=developer_name,
                nudge_number=nudge_number,
                correlation_id=correlation_id,
            )
            recipient = ChatUserRef(
                tenant_id=tenant_id,
                external_id=chat_external_id or checkin.developer_id,
                display_name=developer_name,
            )
            purpose = "status_nudge"
        else:
            # Escalation notices to a human are non-response facts only -- they must
            # never carry the developer's raw check-in/reply content.
            text = _compose_escalation_notice(
                target=target,
                developer_name=await self._escalation_subject_name(
                    tenant_id=tenant_id,
                    developer_id=checkin.developer_id,
                    developer_name=developer_name,
                    chat_external_id=chat_external_id,
                ),
                max_chars=self._outbound_dm_max_chars,
            )
            recipient = ChatUserRef(
                tenant_id=tenant_id,
                external_id=recipient_chat_external_id or "",
                display_name=recipient_display_name,
            )
            purpose = "status_escalation"

        message_id = await self._chat_provider.send_dm(
            recipient,
            OutboundMessage(
                tenant_id=tenant_id,
                text=text,
                correlation_id=correlation_id,
                metadata={
                    "purpose": purpose,
                    "nudge_number": nudge_number,
                    "escalation_target": target.value,
                    "idempotency_key": f"nudge:{correlation_id}:{nudge_number}",
                },
            ),
        )
        sent_at = datetime.now(tz=UTC)
        if target is EscalationTarget.DEVELOPER:
            # Only the developer's own DM belongs in their conversation history.
            await self._record_conversation_turn(
                ConversationTurn(
                    tenant_id=tenant_id,
                    developer_id=checkin.developer_id,
                    conversation_id=correlation_id,
                    conversation_date=await self._local_date_for_developer(
                        tenant_id,
                        checkin.developer_id,
                        sent_at,
                    ),
                    role=ConversationRole.AGENT,
                    content=text,
                    correlation_id=correlation_id,
                    chat_message_id=message_id,
                    observed_at=sent_at,
                )
            )
        stored = await self._status_repository.record_checkin_nudge(
            CheckInNudge(
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                nudge_number=nudge_number,
                sent_at=sent_at,
                outbound_message_id=message_id,
            )
        )
        return stored.outbound_message_id or message_id

    async def _compose_developer_nudge_text(
        self,
        *,
        tenant_id: str,
        checkin: CheckIn,
        developer_name: str | None,
        nudge_number: int,
        correlation_id: str,
    ) -> str:
        context = await self.build_context(
            tenant_id=tenant_id,
            developer_id=checkin.developer_id,
            developer_name=developer_name,
        )
        prompt = (
            "Write one short, friendly follow-up asking for the pending status update. "
            "Do not imply the work is healthy just because there was no reply. "
            "Reference a specific pending, blocked, or stale issue and any carried-forward "
            "blocker from context when useful, while staying concise. "
            f"Developer: {developer_name or checkin.developer_id}. Context: {context}"
        )
        conversation_turns = await self._recent_conversation_turns(
            tenant_id=tenant_id,
            developer_id=checkin.developer_id,
            reference_at=checkin.asked_at,
        )
        tools = self._agent_tools(
            tenant_id=tenant_id,
            developer_id=checkin.developer_id,
            reference_at=checkin.asked_at,
        )
        response = await self._complete_llm(
            LlmRequest(
                tenant_id=tenant_id,
                prompt=prompt,
                model=self._model,
                correlation_id=correlation_id,
                system=COMPOSE_NUDGE_SYSTEM_PROMPT,
                messages=llm_messages_from_turns(conversation_turns),
                metadata={
                    "service": "status_collector",
                    "purpose": "compose_nudge",
                    "developer_id": checkin.developer_id,
                    "nudge_number": nudge_number,
                },
            ),
            tools=tools,
        )
        return _safe_outbound_checkin_text(
            response.text,
            developer_id=checkin.developer_id,
            developer_name=developer_name,
            context=context,
            purpose="nudge",
            max_chars=self._outbound_dm_max_chars,
        )

    async def _escalation_subject_name(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        developer_name: str | None,
        chat_external_id: str | None,
    ) -> str | None:
        """The display name an escalation notice gives the person it is about.

        Scheduled check-ins hand the ladder no name, so the member record is
        looked up. A supplied "name" that is only one of the person's ids counts
        as no name. ``None`` when no name is known; never a raw id.
        """
        raw_ids = {developer_id.strip(), (chat_external_id or "").strip()} - {""}
        supplied = (developer_name or "").strip()
        if supplied and supplied not in raw_ids:
            return supplied
        return await self._member_display_name(tenant_id, developer_id, raw_ids=raw_ids)

    async def _member_display_name(
        self, tenant_id: str, developer_id: str, *, raw_ids: set[str]
    ) -> str | None:
        """The member's name from their graph member record, else the chat directory."""
        names: list[str] = []
        if self._graph_repository is not None:
            node = await self._graph_repository.get_node(tenant_id, developer_id)
            if node is not None and node.kind is NodeKind.DEVELOPER:
                names.append(node.name)
        if self._directory_repository is not None:
            user = await self._directory_repository.get(tenant_id, developer_id)
            if user is not None:
                names.append(user.display_name)
        for name in names:
            cleaned = (name or "").strip()
            if cleaned and cleaned not in raw_ids:
                return cleaned
        return None

    async def has_reply_on_record(self, checkin: CheckIn) -> bool:
        """Whether the person has answered this check-in at all.

        ``replied_at`` is set only when a reply is finalized. A reply that drew
        a clarification leaves it unset until the clarification is answered or
        the close-out finalizes the accumulated reply, yet the person did reply.
        Every reply handled here is first recorded as a user turn on the
        check-in's correlation (``_register_reply_turn``) -- the same turns the
        timeout finalizer accumulates -- so one of those is the record.
        """
        if checkin.replied_at is not None:
            return True
        turns = await self._conversation_repository.list_recent_turns(
            checkin.tenant_id,
            checkin.developer_id,
            limit=MAX_HISTORY_LIMIT,
            since=checkin.asked_at,
        )
        return any(
            turn.role is ConversationRole.USER and turn.correlation_id == checkin.correlation_id
            for turn in turns
        )

    async def record_non_response(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        as_of: date,
        developer_name: str | None = None,
        correlation_id: str | None = None,
    ) -> DeveloperStatus:
        if correlation_id is not None:
            finalized = await self._finalize_accumulated_reply_on_timeout(
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                as_of=as_of,
            )
            if finalized is not None:
                return finalized

        inferred, no_active_work = await self._fallback_inference(
            tenant_id=tenant_id,
            developer_id=developer_id,
            as_of=as_of,
        )
        if inferred is not None:
            await self._status_repository.record_developer_status(inferred)
            return inferred

        prior = await self._status_repository.latest_developer_status(
            tenant_id,
            developer_id,
            as_of,
        )
        if prior is not None and prior.source is not StatusSource.UNKNOWN:
            # No lifecycle operations: open blocker rows simply stay open and
            # keep aging — the compat strings mirror the true open set.
            open_rows = await self._blockers.open_blockers(
                tenant_id,
                developer_id,
                as_of,
                legacy_status=prior,
            )
            stale = DeveloperStatus(
                tenant_id=tenant_id,
                developer_id=developer_id,
                as_of=as_of,
                source=StatusSource.STALE,
                blockers=tuple(blocker.description for blocker in open_rows)
                or ("no confirmed reply",),
                summary=with_no_active_work(
                    await self._stale_summary(prior), no_active_work=no_active_work
                ),
            )
            await self._status_repository.record_developer_status(stale)
            return stale

        unknown = DeveloperStatus(
            tenant_id=tenant_id,
            developer_id=developer_id,
            as_of=as_of,
            source=StatusSource.UNKNOWN,
            blockers=("no confirmed reply",),
            summary=with_no_active_work(UNKNOWN_SUMMARY, no_active_work=no_active_work),
        )
        await self._status_repository.record_developer_status(unknown)
        return unknown

    async def _stale_summary(self, prior: DeveloperStatus) -> str:
        """Name the last status that said something, once, however long ago.

        A stale prior already names it unless it was stored in the old nested
        wording; nothing has been said since, so it is reused as is. Otherwise
        the status is found by following earlier rows back.
        """
        if prior.source is StatusSource.STALE and names_one_basis(prior.summary):
            return prior.summary
        return stale_summary(await basis_status(self._status_repository, prior))

    async def infer_fallback_status(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        as_of: date,
        developer_name: str | None = None,
    ) -> DeveloperStatus | None:
        inferred, _ = await self._fallback_inference(
            tenant_id=tenant_id, developer_id=developer_id, as_of=as_of
        )
        return inferred

    async def _fallback_inference(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        as_of: date,
    ) -> tuple[DeveloperStatus | None, bool]:
        """The inferred status for a silent person, and whether they have no active work.

        Inferred from the same issues and facts the check-in DM is composed
        from, but not summarised as that prompt context: the summary is shown to
        the developer, their scrum master and "owner says" on Signals, and the
        context prints fact payloads, which can quote a reply.

        Only work under way is evidence: an issue In Progress (the trackers read
        In Review as that) or Blocked. When the tracker lists the person's issues
        and none is under way, nothing is inferred and the second value is True,
        so the unknown or stale status says there was no active work to infer
        from (N4: Hana's only issue was To Do). An empty tracker result is not
        that: it can be an unmapped assignee, and recent facts still count.
        """
        reference_at = datetime.now(tz=UTC)
        issues, facts = await self._context_inputs(tenant_id, developer_id)
        active = [issue for issue in issues if issue.state in _ACTIVE_ISSUE_STATES]
        if issues and not active:
            return None, True
        if not active and not facts:
            return None, False
        return (
            DeveloperStatus(
                tenant_id=tenant_id,
                developer_id=developer_id,
                as_of=as_of,
                source=StatusSource.INFERRED,
                blockers=("no confirmed reply",),
                summary=_inferred_summary(active, facts, reference_at),
            ),
            False,
        )

    async def _resolve_issue_tracker_assignee_id(self, tenant_id: str, developer_id: str) -> str:
        """Resolve the external id the issue tracker indexes assignments by.

        Jira indexes issues by ``accountId`` rather than the chat-provider id
        used as the canonical ``developer_id``. When an identity link maps the
        developer to a ``jira_account_id`` we query by that. A link that only
        knows the ``jira_email`` is resolved through the tracker once and the
        account id is stored on the link, so it is looked up a single time.
        Otherwise we fall back to the canonical id so unmapped developers keep
        prior behaviour (a real tracker then finds nothing for them).
        """
        if self._identity_link_repository is None:
            return developer_id
        link = await self._identity_link_repository.get_identity_link(tenant_id, developer_id)
        if link is not None and link.jira_account_id:
            return link.jira_account_id
        if link is not None and link.jira_email:
            try:
                found = await self._issue_tracker.find_user_by_email(tenant_id, link.jira_email)
            except ProviderUnavailable:
                found = None
            if found is not None:
                await self._identity_link_repository.upsert_identity_link(
                    replace(link, jira_account_id=found.external_id)
                )
                return found.external_id
        return developer_id

    async def build_context(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        developer_name: str | None = None,
        include_status: bool = True,
    ) -> str:
        reference_at = datetime.now(tz=UTC)
        prioritized_issues, facts = await self._context_inputs(tenant_id, developer_id)

        lines: list[str] = []
        if include_status:
            latest_status = await self._status_repository.latest_developer_status(
                tenant_id,
                developer_id,
                _local_date(reference_at, None, self._tenant_default_timezone),
            )
            lines.extend(_status_context_lines(latest_status))
        lines.extend(
            _issue_context_lines(prioritized_issues, facts=facts, reference_at=reference_at)
        )
        lines.extend(_fact_context_lines(facts))

        if not lines:
            return _NO_CONTEXT
        heading = f"Developer: {developer_name or developer_id}"
        return "\n".join((heading, *lines))

    async def _context_inputs(
        self, tenant_id: str, developer_id: str
    ) -> tuple[list[Issue], list[FactEvent]]:
        """The developer's active issues, most pressing first, and their recent facts."""
        assignee_external_id = await self._resolve_issue_tracker_assignee_id(
            tenant_id, developer_id
        )
        issues = await self._issue_tracker.list_active_for(
            UserRef(tenant_id=tenant_id, external_id=assignee_external_id)
        )
        prioritized_issues = _prioritize_issues(issues)
        facts: list[FactEvent] = []
        if self._time_series_repository is not None:
            facts = await self._recent_facts(tenant_id, developer_id, prioritized_issues)
        return prioritized_issues, facts

    async def _build_context_node(self, state: StatusCollectorState) -> StatusCollectorState:
        return {
            "context": await self.build_context(
                tenant_id=state["tenant_id"],
                developer_id=state["developer_id"],
                developer_name=state.get("developer_name"),
            )
        }

    async def _compose_dm_node(self, state: StatusCollectorState) -> StatusCollectorState:
        prompt = (
            "Write one concise, conversational chat direct message asking for today's work "
            "status. Ask for progress, blockers, and ETA changes. Reference the specific "
            "pending, blocked, or stale issue(s) and any carried-forward blocker from context "
            "when useful, while staying within the character cap. Return only the message text.\n\n"
            f"Developer: {state.get('developer_name', state['developer_id'])}\n"
            f"Context:\n{state['context']}"
        )
        tools = self._agent_tools(
            tenant_id=state["tenant_id"],
            developer_id=state["developer_id"],
            reference_at=state.get("asked_at"),
        )
        response = await self._complete_llm(
            LlmRequest(
                tenant_id=state["tenant_id"],
                prompt=prompt,
                model=self._model,
                correlation_id=state["correlation_id"],
                system=COMPOSE_CHECKIN_SYSTEM_PROMPT,
                messages=llm_messages_from_turns(
                    await self._recent_conversation_turns(
                        tenant_id=state["tenant_id"],
                        developer_id=state["developer_id"],
                        reference_at=state.get("asked_at"),
                    )
                ),
                metadata={
                    "service": "status_collector",
                    "purpose": "compose_checkin",
                    "developer_id": state["developer_id"],
                },
            ),
            tools=tools,
        )
        text = _safe_outbound_checkin_text(
            response.text,
            developer_id=state["developer_id"],
            developer_name=state.get("developer_name"),
            context=state["context"],
            purpose="checkin",
            max_chars=self._outbound_dm_max_chars,
        )
        return {"dm_text": text, "trace_id": response.trace_id}

    async def _send_dm_node(self, state: StatusCollectorState) -> StatusCollectorState:
        user = ChatUserRef(
            tenant_id=state["tenant_id"],
            external_id=state.get("chat_external_id", state["developer_id"]),
            display_name=state.get("developer_name"),
        )
        chat_thread_ref = await self._chat_provider.open_thread(user)
        message_id = await self._chat_provider.send_dm(
            user,
            OutboundMessage(
                tenant_id=state["tenant_id"],
                text=state["dm_text"],
                correlation_id=state["correlation_id"],
                metadata={
                    "purpose": "status_checkin",
                    # Keyed so a durable-step retry between send and record_checkin
                    # returns the first ts instead of posting a second DM (C3).
                    "idempotency_key": f"checkin:{state['correlation_id']}",
                },
            ),
        )
        observed_at = state.get("asked_at", datetime.now(tz=UTC))
        await self._record_conversation_turn(
            ConversationTurn(
                tenant_id=state["tenant_id"],
                developer_id=state["developer_id"],
                conversation_id=state["correlation_id"],
                conversation_date=await self._local_date_for_developer(
                    state["tenant_id"],
                    state["developer_id"],
                    observed_at,
                ),
                role=ConversationRole.AGENT,
                content=state["dm_text"],
                correlation_id=state["correlation_id"],
                chat_message_id=message_id,
                observed_at=observed_at,
            )
        )
        return {
            "message_id": message_id,
            "chat_thread_ref": chat_thread_ref,
            "chat_user_ref": user.external_id,
        }

    async def _record_checkin_node(self, state: StatusCollectorState) -> StatusCollectorState:
        asked_at = state.get("asked_at", datetime.now(tz=UTC))
        checkin = CheckIn(
            tenant_id=state["tenant_id"],
            developer_id=state["developer_id"],
            correlation_id=state["correlation_id"],
            asked_at=asked_at,
            replied_at=None,
            raw_reply=None,
            signals=None,
            last_accessed_at=asked_at,
            checkin_date=state.get("checkin_date"),
        )
        await self._status_repository.record_checkin(checkin)
        await self._status_repository.record_checkin_correlation(
            CheckInCorrelation(
                tenant_id=checkin.tenant_id,
                correlation_id=checkin.correlation_id,
                developer_id=checkin.developer_id,
                chat_user_ref=state["chat_user_ref"],
                chat_thread_ref=state["chat_thread_ref"],
                outbound_message_id=state["message_id"],
                asked_at=checkin.asked_at,
            )
        )
        return {"checkin": checkin}

    async def _recover_finalized_status(self, checkin: CheckIn) -> DeveloperStatus:
        """Return (or reconstruct) the status for an already-finalized check-in.

        Fast path: a CONFIRMED/PARTIAL status already exists. Otherwise the
        process died between ``record_checkin_reply_once`` and the status
        write, so rebuild from the persisted ``checkin.signals`` (which
        round-trip blocker reports and resolved ids) and persist idempotently —
        blocker upserts converge by natural key.
        """
        status_as_of = await self._status_as_of_for_checkin(
            checkin,
            checkin.replied_at or checkin.asked_at,
        )
        latest = await self._status_repository.latest_developer_status(
            checkin.tenant_id,
            checkin.developer_id,
            status_as_of,
        )
        if latest is not None and latest.source in {StatusSource.CONFIRMED, StatusSource.PARTIAL}:
            return latest
        signals = checkin.signals or CheckInSignals(progress_note="Duplicate confirmed reply.")
        prior = await self._prior_open_blockers(checkin, checkin.replied_at or checkin.asked_at)
        reconciliation = await self._reconcile_reply_blockers(
            checkin=checkin,
            at=checkin.replied_at or checkin.asked_at,
            prior=prior,
            signals=signals,
            raw_reply=checkin.raw_reply or "",
        )
        final_signals = _signals_with_open_blockers(signals, reconciliation)
        missing_required = _missing_required_status_details(final_signals)
        status = DeveloperStatus(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            as_of=status_as_of,
            source=_status_source_for_signals(final_signals),
            blockers=final_signals.blockers,
            summary=_summary_with_missing_required_details(
                final_signals.progress_note, missing_required
            ),
            eta_change_days=final_signals.eta_change_days,
        )
        await self._blockers.persist_with_status(status, reconciliation)
        await self._append_checkin_fact(checkin, status, reconciliation=reconciliation)
        return status

    async def _send_clarification(
        self,
        *,
        checkin: CheckIn,
        message: InboundMessage,
        question: str,
        clarification_number: int,
    ) -> str:
        claimed = await self._status_repository.record_checkin_clarification(
            CheckInClarification(
                tenant_id=checkin.tenant_id,
                correlation_id=checkin.correlation_id,
                clarification_number=clarification_number,
                question=question,
            )
        )
        if claimed.outbound_message_id is not None:
            return claimed.outbound_message_id

        message_id = await self._chat_provider.send_dm(
            message.user,
            OutboundMessage(
                tenant_id=checkin.tenant_id,
                text=claimed.question,
                correlation_id=checkin.correlation_id,
                metadata={
                    "purpose": "status_clarification",
                    "clarification_number": clarification_number,
                    "idempotency_key": (
                        f"clarification:{checkin.correlation_id}:{clarification_number}"
                    ),
                },
            ),
        )
        sent_at = datetime.now(tz=UTC)
        stored = await self._status_repository.record_checkin_clarification(
            CheckInClarification(
                tenant_id=checkin.tenant_id,
                correlation_id=checkin.correlation_id,
                clarification_number=clarification_number,
                question=claimed.question,
                sent_at=sent_at,
                outbound_message_id=message_id,
            )
        )
        await self._record_conversation_turn(
            ConversationTurn(
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                conversation_id=checkin.correlation_id,
                conversation_date=await self._local_date_for_developer(
                    checkin.tenant_id,
                    checkin.developer_id,
                    sent_at,
                ),
                role=ConversationRole.AGENT,
                content=claimed.question,
                correlation_id=checkin.correlation_id,
                chat_message_id=stored.outbound_message_id or message_id,
                observed_at=sent_at,
            )
        )
        return stored.outbound_message_id or message_id

    async def _send_non_status_ack(
        self,
        *,
        checkin: CheckIn,
        message: InboundMessage,
    ) -> str | None:
        """Say once per check-in that a reply carried no status (N22).

        A second non-status message on the same check-in gets no second ack:
        the first one still holds, and the close-out records no status for the
        day without telling the person anything that contradicts it.
        """
        text = NON_STATUS_ACK_TEXT
        earlier_turns = await self._conversation_turns_for_correlation(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=checkin.correlation_id,
            reference_at=message.received_at,
        )
        if any(_is_non_status_ack(turn) for turn in earlier_turns):
            return None
        message_id = await self._chat_provider.send_dm(
            message.user,
            OutboundMessage(
                tenant_id=checkin.tenant_id,
                text=text,
                correlation_id=checkin.correlation_id,
                metadata={
                    "purpose": "status_non_status_ack",
                    "idempotency_key": (
                        f"non-status-ack:{checkin.correlation_id}:{message.message_id}"
                    ),
                },
            ),
        )
        sent_at = datetime.now(tz=UTC)
        await self._record_conversation_turn(
            ConversationTurn(
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                conversation_id=checkin.correlation_id,
                conversation_date=await self._local_date_for_developer(
                    checkin.tenant_id,
                    checkin.developer_id,
                    sent_at,
                ),
                role=ConversationRole.AGENT,
                content=text,
                correlation_id=checkin.correlation_id,
                chat_message_id=message_id,
                observed_at=sent_at,
            )
        )
        return message_id

    async def _request_ledger(self, checkin: CheckIn) -> _RequestLedger:
        if self._cross_person_repository is None:
            return _RequestLedger()
        raised = await self._cross_person_repository.list_for_requester(
            checkin.tenant_id,
            checkin.developer_id,
        )
        return _RequestLedger(
            recorded=tuple(
                request
                for request in raised
                if request.source_correlation_id == checkin.correlation_id
            )
        )

    async def _settle_waiting_requests(
        self,
        checkin: CheckIn,
        requests: _RequestLedger,
        answer: str,
    ) -> None:
        """Let a reply answer "who did you mean?" before anything else reads it.

        The answer may be all the reply says (an address, a mention) and the
        model need not read it as a status update, nor repeat the request it
        settles, so it is matched against each waiting request's members here.
        With several requests waiting, an answer only settles one whose
        candidates include the member it names.
        """
        waiting = requests.unsettled()
        if not waiting:
            return
        members = await self._members.active_members(checkin.tenant_id)
        for request in waiting:
            mention = _mention_of(request)
            candidates = members_for_mention(mention, members)
            member = member_named_in_answer(answer, mention, candidates, members)
            if member is None or (len(waiting) > 1 and candidates and member not in candidates):
                continue
            requests.resolutions.append(_open_resolution(mention, member, request_id=request.id))

    async def _hold_cross_person_requests(
        self,
        *,
        checkin: CheckIn,
        signals: CheckInSignals | None,
        requests: _RequestLedger,
        clarification_count: int,
    ) -> None:
        """Record this reply's requests while another question goes out first.

        Without this, a request stated in a reply that is asked for its ETA
        lived only in the model's reading of that one reply, and was lost when
        the next reply was read on its own.
        """
        if signals is None:
            return
        await self._resolve_cross_person_requests(
            checkin=checkin,
            signals=signals,
            requests=requests,
            clarification_count=clarification_count,
            ask=False,
        )

    async def _resolve_cross_person_requests(
        self,
        *,
        checkin: CheckIn,
        signals: CheckInSignals,
        requests: _RequestLedger,
        clarification_count: int,
        ask: bool,
    ) -> str | None:
        """Add this reply's requests to the ledger; return a "who?" question when one is due.

        Each request is recorded the turn it is stated: one naming a single
        member opens (and its person is told), any other waits as
        needs_resolution. A request the check-in already holds is not added
        again, and a mention that now names one member settles the waiting
        request instead. With ``ask`` and clarification budget left, the
        question is about the first request still waiting for its person.
        """
        if not signals.requests and not (ask and requests.unsettled()):
            return None
        members = await self._members.active_members(checkin.tenant_id)
        for mention in signals.requests:
            matches = members_for_mention(mention, members)
            waiting = requests.unsettled_for(mention)
            if waiting is not None:
                if len(matches) == 1:
                    requests.resolutions.append(
                        _open_resolution(_mention_of(waiting), matches[0], request_id=waiting.id)
                    )
                continue
            if requests.knows(mention, matches[0].chat_id if len(matches) == 1 else None):
                continue
            if len(matches) == 1:
                requests.resolutions.append(_open_resolution(mention, matches[0]))
                continue
            requests.resolutions.append(
                CrossPersonRequestResolution(
                    mention=mention,
                    status=CrossPersonRequestStatus.NEEDS_RESOLUTION,
                )
            )
        if not ask or clarification_count >= self._checkin_max_clarifications:
            return None
        waiting_mentions = [_mention_of(request) for request in requests.unsettled()] + [
            resolution.mention
            for resolution in requests.resolutions
            if resolution.status is CrossPersonRequestStatus.NEEDS_RESOLUTION
        ]
        if not waiting_mentions:
            return None
        first = waiting_mentions[0]
        return _person_clarification_question(first, members_for_mention(first, members))

    async def _finalize_checkin_reply(
        self,
        *,
        checkin: CheckIn,
        replied_at: datetime,
        raw_reply: str,
        signals: CheckInSignals,
        reconciliation: BlockerReconciliation,
        closing: bool = False,
    ) -> DeveloperStatus:
        """Record the check-in's reply, status and facts, write back, and ack once.

        ``closing`` is the close-out: a write-back question still open is closed
        with the check-in (its proposals expire) and no new one is asked.
        """
        final_signals = _signals_with_open_blockers(signals, reconciliation)
        updated = CheckIn(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=checkin.correlation_id,
            asked_at=checkin.asked_at,
            replied_at=replied_at,
            raw_reply=raw_reply,
            signals=final_signals,
            checkin_date=checkin.checkin_date,
        )
        recorded = await self._status_repository.record_checkin_reply_once(updated)
        if not recorded:
            duplicate = await self._status_repository.checkin_by_correlation(
                checkin.tenant_id,
                checkin.correlation_id,
            )
            return await self._recover_finalized_status(duplicate or checkin)

        missing_required = _missing_required_status_details(final_signals)
        status = DeveloperStatus(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            as_of=await self._status_as_of_for_checkin(checkin, replied_at),
            source=_status_source_for_signals(final_signals),
            blockers=final_signals.blockers,
            summary=_summary_with_missing_required_details(
                final_signals.progress_note,
                missing_required,
            ),
            eta_change_days=final_signals.eta_change_days,
        )
        await self._blockers.persist_with_status(status, reconciliation)
        await self._status_repository.consume_checkin_correlation(
            checkin.tenant_id,
            checkin.correlation_id,
            replied_at,
        )
        await self._append_checkin_fact(updated, status, reconciliation=reconciliation)
        await self._append_blocker_resolved_facts(updated, status, reconciliation)
        await self._record_review_without_merge_request(updated, status)
        await self._record_issue_etas(updated, status)
        written = await self._maybe_write_back(updated, final_signals, closing=closing)
        # Send exactly one "Got it" ack per accepted reply. Gated on the
        # record_checkin_reply_once success above, so a durable retry or a
        # duplicate delivery (which returns early) never double-acks (C3).
        await self._send_checkin_ack(checkin=updated, signals=final_signals, applied=written)
        return status

    async def _send_checkin_ack(
        self,
        *,
        checkin: CheckIn,
        signals: CheckInSignals,
        applied: Sequence[WriteBackAudit] = (),
    ) -> None:
        """DM the developer a short receipt once their reply is finalized.

        The ack goes to the developer themselves, so a concise recorded-summary
        (state + first blocker) is fine; we never echo the raw reply text. A
        low-confidence parse adds a correction hint so the developer can fix a
        misread; a confident parse gets the plain ack. Best-effort: a send
        failure must never lose the already-recorded check-in.
        """
        if not self._checkin_ack_enabled:
            return
        correlation = await self._status_repository.checkin_correlation_by_id(
            checkin.tenant_id,
            checkin.correlation_id,
        )
        if correlation is None:
            return
        recipient = ChatUserRef(
            tenant_id=checkin.tenant_id,
            external_id=correlation.chat_user_ref,
        )
        text = _compose_checkin_ack_text(
            signals=signals,
            max_chars=self._outbound_dm_max_chars,
            applied=applied,
            held=await self._open_merge_request_holds(checkin, applied),
        )
        try:
            await self._chat_provider.send_dm(
                recipient,
                OutboundMessage(
                    tenant_id=checkin.tenant_id,
                    text=text,
                    correlation_id=checkin.correlation_id,
                    metadata={
                        "purpose": "status_ack",
                        # Keyed per correlation so a provider-level send-once still
                        # collapses any retry to a single ack DM.
                        "idempotency_key": f"checkin-ack:{checkin.correlation_id}",
                    },
                ),
            )
        except Exception:  # pragma: no cover - defensive; ack is best-effort
            _logger.warning(
                "checkin_ack_failed",
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=checkin.correlation_id,
            )

    async def _open_merge_request_holds(
        self, checkin: CheckIn, rows: Sequence[WriteBackAudit]
    ) -> list[OpenMergeRequestHold]:
        """The open merge requests behind each held-back ``done``, for the ack to name."""
        service = self._write_back_service
        if service is None or not any(row.source == OPEN_MR_SOURCE for row in rows):
            return []
        try:
            return await service.open_merge_request_holds(checkin.tenant_id, rows)
        except Exception:  # pragma: no cover - defensive; the ack is best-effort
            return []

    async def _maybe_write_back(
        self, checkin: CheckIn, signals: CheckInSignals, *, closing: bool = False
    ) -> list[WriteBackAudit]:
        """Apply gated write-back for a finalized check-in's issue claims.

        The default-deny gates and audit live in ``WriteBackService``; here we
        only forward the claims. A write-back failure must never lose a recorded
        check-in, so any error is logged (without raw reply content) and swallowed.
        On the close-out (``closing``) every proposal still waiting for a yes/no
        expires with the check-in and no consent question is sent. Returns what
        the check-in's write-back did, one row per issue (applied, held for an
        open merge request, declined by the person, expired), for the ack to name.
        """
        service = self._write_back_service
        if service is None or (not signals.issue_updates and not closing):
            return []
        try:
            results = (
                await service.apply_from_checkin(
                    tenant_id=checkin.tenant_id,
                    developer_id=checkin.developer_id,
                    correlation_id=checkin.correlation_id,
                    claims=signals.issue_updates,
                    reported_on=checkin.checkin_date,
                )
                if signals.issue_updates
                else []
            )
            if closing:
                await service.expire_pending_proposals(
                    tenant_id=checkin.tenant_id,
                    developer_id=checkin.developer_id,
                    correlation_id=checkin.correlation_id,
                )
            outcomes = await service.reply_outcomes(checkin.tenant_id, checkin.correlation_id)
        except Exception:  # pragma: no cover - defensive; write-back is best-effort
            _logger.warning(
                "writeback_failed",
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=checkin.correlation_id,
            )
            return []
        if results:
            _logger.info(
                "writeback_recorded",
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=checkin.correlation_id,
                outcomes=[
                    {
                        "issue_key": audit.issue_key,
                        "status": audit.status.value,
                        "source": audit.source,
                    }
                    for audit in results
                ],
            )
        proposed = [audit for audit in results if audit.status is WriteBackStatus.PROPOSED]
        if proposed and not closing:
            await self._send_consent_prompt(checkin=checkin, proposals=proposed)
        return outcomes

    async def _send_consent_prompt(
        self,
        *,
        checkin: CheckIn,
        proposals: list[WriteBackAudit],
    ) -> None:
        """Ask the developer to confirm a proposed write-back in the DM (yes/no).

        Privacy-safe by construction: the prompt names only the issue key and the
        target state, never the developer's note or any raw reply content. Sent to
        the persisted correlation's chat_user_ref. Best-effort -- a send failure
        must never lose the recorded ``proposed`` audit rows, which remain pending
        until answered.
        """
        correlation = await self._status_repository.checkin_correlation_by_id(
            checkin.tenant_id,
            checkin.correlation_id,
        )
        if correlation is None:
            return
        recipient = ChatUserRef(
            tenant_id=checkin.tenant_id,
            external_id=correlation.chat_user_ref,
        )
        text = _compose_consent_prompt_text(proposals, max_chars=self._outbound_dm_max_chars)
        try:
            await self._chat_provider.send_dm(
                recipient,
                OutboundMessage(
                    tenant_id=checkin.tenant_id,
                    text=text,
                    correlation_id=checkin.correlation_id,
                    metadata={
                        "purpose": "writeback_consent_prompt",
                        # Per set of issues: a follow-up that asks only about the
                        # issues an answer left open is a message of its own.
                        "idempotency_key": (
                            f"writeback-consent:{checkin.correlation_id}:"
                            + "+".join(sorted({p.issue_key for p in proposals}))
                        ),
                    },
                ),
            )
        except Exception:  # pragma: no cover - defensive; prompt is best-effort
            _logger.warning(
                "writeback_consent_prompt_failed",
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=checkin.correlation_id,
            )

    async def _maybe_ask_write_back_consent(
        self,
        *,
        checkin: CheckIn,
        message: InboundMessage,
        signals: CheckInSignals,
        open_signals: CheckInSignals,
        reconciliation: BlockerReconciliation,
    ) -> ReplyOutcome | None:
        """Ask an ``always_ask`` person to confirm tracker updates, keeping the check-in open.

        G1: the yes/no question used to be followed a second later by "your
        update is recorded", the check-in closed, and the person's answer then
        reached nothing. Now the check-in stays open while a proposal waits:
        the proposals are recorded (nothing is written), the check-in's signals
        are held, the status is recorded as finalizing would record it, and the
        question is the last message -- no "recorded" until it is answered or the
        check-in closes. ``None`` when nothing needs asking (another consent, no
        proposal); the check-in is then finalized as before.
        """
        service = self._write_back_service
        if service is None or not signals.issue_updates:
            return None
        try:
            if not await service.asks_before_writing(checkin.tenant_id, checkin.developer_id):
                return None
            await service.apply_from_checkin(
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=checkin.correlation_id,
                claims=signals.issue_updates,
                reported_on=checkin.checkin_date,
            )
            pending = await service.list_pending_proposals(
                checkin.tenant_id, checkin.correlation_id
            )
        except Exception:  # pragma: no cover - defensive; the check-in still records
            _logger.warning(
                "writeback_consent_question_failed",
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=checkin.correlation_id,
            )
            return None
        if not pending:
            return None
        await self._hold_open_checkin_signals(checkin, open_signals)
        status = await self._record_status_awaiting_consent(
            checkin=checkin,
            as_of_at=message.received_at,
            signals=signals,
            reconciliation=reconciliation,
        )
        await self._send_consent_prompt(checkin=checkin, proposals=pending)
        return ReplyOutcome(kind="clarifying", status=status)

    async def _record_status_awaiting_consent(
        self,
        *,
        checkin: CheckIn,
        as_of_at: datetime,
        signals: CheckInSignals,
        reconciliation: BlockerReconciliation,
    ) -> DeveloperStatus:
        # The status is what the person said; only the tracker write waits for
        # them, so it is recorded as finalizing would record it, not as partial.
        final_signals = _signals_with_open_blockers(signals, reconciliation)
        status = DeveloperStatus(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            as_of=await self._status_as_of_for_checkin(checkin, as_of_at),
            source=_status_source_for_signals(final_signals),
            blockers=final_signals.blockers,
            summary=_summary_with_missing_required_details(
                final_signals.progress_note,
                _missing_required_status_details(final_signals),
            ),
            eta_change_days=final_signals.eta_change_days,
        )
        await self._blockers.persist_with_status(status, reconciliation)
        return status

    async def _maybe_answer_write_back_consent(
        self,
        *,
        checkin: CheckIn,
        message: InboundMessage,
    ) -> ReplyOutcome | None:
        """Apply a yes/no to the open write-back question, then finalize the check-in.

        Only while the check-in is open with proposals waiting, and only for a
        reply that reads as an answer (``interpret_consent_answer``: per issue,
        "yes for CHK-3, leave CHK-4"). Every write goes through
        ``WriteBackService.resolve_consent_reply``, so the owner check, the open
        merge request hold and the canonical target all still apply. Issues the
        answer leaves open are asked about again; once none is left the check-in
        is finalized from its held signals, and the ack says what was done. Any
        other reply returns ``None`` and is read as status, as before.
        """
        service = self._write_back_service
        held = checkin.signals
        if service is None or held is None:
            return None
        try:
            pending = await service.list_pending_proposals(
                checkin.tenant_id, checkin.correlation_id
            )
            if not pending or not interpret_consent_answer(
                message.text, [proposal.issue_key for proposal in pending]
            ):
                return None
            results = await service.resolve_consent_reply(
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=checkin.correlation_id,
                reply_text=message.text,
                claims=held.issue_updates,
                reported_on=checkin.checkin_date,
            )
            remaining = await service.list_pending_proposals(
                checkin.tenant_id, checkin.correlation_id
            )
        except Exception:  # pragma: no cover - defensive; read the reply as status
            _logger.warning(
                "writeback_consent_resolution_failed",
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=checkin.correlation_id,
            )
            return None
        _logger.info(
            "writeback_consent_resolved",
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=checkin.correlation_id,
            outcomes=[
                {"issue_key": audit.issue_key, "status": audit.status.value} for audit in results
            ],
        )
        if remaining and results:
            await self._send_consent_prompt(checkin=checkin, proposals=remaining)
            return ReplyOutcome(kind="clarifying")
        turns = await self._recent_conversation_turns(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            exclude_chat_message_id=message.message_id,
            reference_at=message.received_at,
        )
        raw_reply = _checkin_status_text(checkin, turns) or held.progress_note
        reconciliation = await self._reconcile_reply_blockers(
            checkin=checkin,
            at=message.received_at,
            prior=await self._prior_open_blockers(checkin, message.received_at),
            signals=held,
            raw_reply=raw_reply,
        )
        status = await self._finalize_checkin_reply(
            checkin=checkin,
            replied_at=message.received_at,
            raw_reply=raw_reply,
            signals=held,
            reconciliation=reconciliation,
            # A clear answer the gates no longer let through closes the question too.
            closing=bool(remaining),
        )
        return ReplyOutcome(kind="processed", status=status)

    async def _handle_already_replied(
        self,
        *,
        checkin: CheckIn,
        message: InboundMessage,
    ) -> ReplyOutcome:
        """Route a reply on a finalized check-in: a consent yes/no, else duplicate.

        A pending write-back proposal turns a later yes/no into a consent
        resolution; anything else is the existing duplicate-reply behaviour.
        """
        consent_outcome = await self._maybe_resolve_consent(checkin=checkin, message=message)
        if consent_outcome is not None:
            return consent_outcome
        return ReplyOutcome(
            kind="processed",
            status=await self._recover_finalized_status(checkin),
        )

    async def _maybe_resolve_consent(
        self,
        *,
        checkin: CheckIn,
        message: InboundMessage,
    ) -> ReplyOutcome | None:
        """Treat a reply on an already-finalized check-in as a yes/no consent answer.

        Returns an ``acknowledged`` outcome only when a pending write-back proposal
        exists and the reply resolves it (apply/decline) via ``WriteBackService``;
        otherwise ``None`` so the caller falls through to the normal duplicate-reply
        path. The three gates and audit live in ``WriteBackService``; a resolution
        failure must never disturb the recorded check-in, so errors are swallowed.
        """
        service = self._write_back_service
        if service is None:
            return None
        try:
            results = await service.resolve_consent_reply(
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=checkin.correlation_id,
                reply_text=message.text,
                claims=checkin.signals.issue_updates if checkin.signals is not None else (),
                reported_on=checkin.checkin_date,
            )
        except Exception:  # pragma: no cover - defensive; resolution is best-effort
            _logger.warning(
                "writeback_consent_resolution_failed",
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=checkin.correlation_id,
            )
            return None
        if not results:
            return None
        _logger.info(
            "writeback_consent_resolved",
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=checkin.correlation_id,
            outcomes=[
                {"issue_key": audit.issue_key, "status": audit.status.value} for audit in results
            ],
        )
        return ReplyOutcome(kind="acknowledged")

    async def _record_partial_checkin_status(
        self,
        *,
        checkin: CheckIn,
        as_of_at: datetime,
        signals: CheckInSignals,
        reconciliation: BlockerReconciliation,
    ) -> DeveloperStatus:
        missing_required = _missing_required_status_details(signals)
        status = DeveloperStatus(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            as_of=await self._status_as_of_for_checkin(checkin, as_of_at),
            source=StatusSource.PARTIAL,
            blockers=signals.blockers,
            summary=_summary_with_missing_required_details(
                signals.progress_note,
                missing_required,
            ),
            eta_change_days=signals.eta_change_days,
        )
        # Atomic with the blocker rows so a clarify-then-timeout never orphans
        # blockers minted in a clarifying turn.
        await self._blockers.persist_with_status(status, reconciliation)
        return status

    async def _hold_open_checkin_signals(self, checkin: CheckIn, signals: CheckInSignals) -> None:
        """Keep what the check-in's messages said so far on the still-open check-in.

        The next message of the check-in (the answer to a follow-up) is merged
        with these, and so is the close-out of an unanswered follow-up. Only an
        open check-in is written: ``replied_at`` and ``raw_reply`` stay unset,
        and a check-in finalized meanwhile is left alone.
        """
        await self._status_repository.record_open_checkin_signals(
            checkin.tenant_id,
            checkin.correlation_id,
            signals,
        )

    async def _finalize_accumulated_reply_on_timeout(
        self,
        *,
        tenant_id: str,
        correlation_id: str,
        as_of: date,
    ) -> DeveloperStatus | None:
        checkin = await self._status_repository.checkin_by_correlation(tenant_id, correlation_id)
        if checkin is None or checkin.replied_at is not None:
            return None

        turns = await self._correlation_turns_for_timeout(
            tenant_id=tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=correlation_id,
            reference_at=datetime.combine(as_of, datetime.max.time(), tzinfo=UTC),
        )
        user_turns = [turn for turn in turns if turn.role is ConversationRole.USER]
        if not user_turns:
            return None
        clarified = (
            await self._status_repository.checkin_clarification_count(tenant_id, correlation_id) > 0
        )
        if not clarified and checkin.signals is None and _answered_as_non_status(turns):
            # Every message was read as carrying no status when it arrived, and
            # the person was told so. Reading them again now can only disagree
            # with that ack (N22: "no updates today" closed as partial).
            return await self._record_non_status_reply_day(checkin, as_of)

        raw_reply = "\n".join(turn.content for turn in user_turns)
        prior_blockers = await self._prior_open_blockers(checkin, user_turns[-1].observed_at)
        tools = self._agent_tools(
            tenant_id=tenant_id,
            developer_id=checkin.developer_id,
            reference_at=user_turns[-1].observed_at,
        )
        decision = await self._clarification_evaluator.evaluate(
            tenant_id=tenant_id,
            developer_id=checkin.developer_id,
            raw_reply=raw_reply,
            correlation_id=correlation_id,
            conversation_turns=turns,
            tools=tools,
            prior_blockers=prior_blockers,
        )
        if not decision.is_status_update:
            if checkin.signals is None:
                return await self._record_non_status_reply_day(checkin, as_of)
            return None

        signals = decision.signals or await self._parser.parse_reply(
            tenant_id=tenant_id,
            developer_id=checkin.developer_id,
            raw_reply=raw_reply,
            correlation_id=correlation_id,
            conversation_turns=turns,
            tools=tools,
            prior_blockers=prior_blockers,
        )
        # The timeout path never asks the attribution question; unattributed
        # blockers finalize unattributed (visible in every pod, flagged).
        reconciliation = await self._reconcile_reply_blockers(
            checkin=checkin,
            at=user_turns[-1].observed_at,
            prior=prior_blockers,
            signals=signals,
            raw_reply=raw_reply,
        )
        if checkin.signals is not None:
            # What each message said as it was read stays, so a claim or answer
            # this reading of all the messages leaves out is not lost; it still
            # words the summary, and wins for whatever it does mention.
            signals = replace(
                merge_checkin_signals(checkin.signals, signals),
                progress_note=signals.progress_note,
            )
        return await self._finalize_checkin_reply(
            checkin=checkin,
            replied_at=user_turns[-1].observed_at,
            raw_reply=raw_reply,
            signals=_signals_with_note(
                signals,
                (
                    "Finalized from accumulated replies after clarification timeout."
                    if clarified
                    else "Finalized from accumulated replies at check-in close."
                ),
            ),
            reconciliation=reconciliation,
            closing=True,
        )

    async def _record_non_status_reply_day(self, checkin: CheckIn, as_of: date) -> DeveloperStatus:
        """Close a check-in whose replies carried no status: unknown for the day.

        The person answered, so the summary says so instead of the non-response
        wording, and nothing is inferred over what they said. It is never green
        and never a partial status built from a re-reading of the replies.
        Open blocker rows stay open, as for a stale day.
        """
        prior = await self._status_repository.latest_developer_status(
            checkin.tenant_id, checkin.developer_id, as_of
        )
        open_rows = await self._blockers.open_blockers(
            checkin.tenant_id,
            checkin.developer_id,
            as_of,
            legacy_status=(
                prior if prior is not None and prior.source is not StatusSource.UNKNOWN else None
            ),
        )
        status = DeveloperStatus(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            as_of=as_of,
            source=StatusSource.UNKNOWN,
            blockers=tuple(blocker.description for blocker in open_rows) or (NO_REPLY_BLOCKER,),
            summary=NON_STATUS_REPLY_SUMMARY,
        )
        await self._status_repository.record_developer_status(status)
        _logger.info(
            "checkin_closed_without_status",
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=checkin.correlation_id,
        )
        return status

    async def _complete_llm(
        self,
        request: LlmRequest,
        *,
        tools: Iterable[AgentTool] = (),
    ) -> LlmResponse:
        if self._tool_agent is not None:
            return await self._tool_agent.run(request, tools)
        return await self._llm_provider.complete(request)

    async def _append_checkin_fact(
        self,
        checkin: CheckIn,
        status: DeveloperStatus,
        reconciliation: BlockerReconciliation | None = None,
    ) -> None:
        if self._time_series_repository is None or checkin.replied_at is None:
            return
        signals = checkin.signals
        payload: dict[str, JsonScalar] = {
            "status_source": status.source.value,
            "blocker_count": len(status.blockers),
            "has_eta_change": signals.eta_change_days is not None if signals else False,
            "eta_change_days": signals.eta_change_days if signals else None,
            # Readable name for the activity feed, which would otherwise render
            # the raw developer id. Falls back to the id when unknown.
            "developer_name": await self._developer_display_name(
                checkin.tenant_id, checkin.developer_id
            ),
        }
        if reconciliation is not None:
            payload["new_blocker_count"] = len(reconciliation.minted)
            payload["resolved_blocker_count"] = len(reconciliation.resolved)
            payload["unattributed_blocker_count"] = len(
                self._blockers.unattributed(reconciliation.open_after)
            )
        await self._time_series_repository.append_fact_once(
            FactEvent(
                tenant_id=checkin.tenant_id,
                source="checkin",
                entity_ref=EntityRef(
                    tenant_id=checkin.tenant_id,
                    kind=NodeKind.DEVELOPER,
                    id=checkin.developer_id,
                ),
                payload=payload,
                observed_at=checkin.replied_at,
                correlation_id=checkin.correlation_id,
            )
        )

    async def _developer_display_name(self, tenant_id: str, developer_id: str) -> str:
        """Best-effort display name for a developer id.

        Tries the chat directory, where the developer id doubles as the chat
        external id in single-workspace tenants, then the graph node, and
        finally returns the id unchanged.
        """
        if self._directory_repository is not None:
            user = await self._directory_repository.get(tenant_id, developer_id)
            if user is not None and user.display_name:
                return user.display_name
        if self._graph_repository is not None:
            node = await self._graph_repository.get_node(tenant_id, developer_id)
            if node is not None and node.name:
                return node.name
        return developer_id

    async def _append_blocker_resolved_facts(
        self,
        checkin: CheckIn,
        status: DeveloperStatus,
        reconciliation: BlockerReconciliation,
    ) -> None:
        """One convergent fact per blocker resolved by this reply (no free text)."""
        if self._time_series_repository is None or checkin.replied_at is None:
            return
        for blocker in reconciliation.resolved:
            await self._time_series_repository.append_fact_once(
                FactEvent(
                    tenant_id=checkin.tenant_id,
                    source="checkin",
                    entity_ref=EntityRef(
                        tenant_id=checkin.tenant_id,
                        kind=NodeKind.DEVELOPER,
                        id=checkin.developer_id,
                    ),
                    payload={
                        "event": "blocker_resolved",
                        "blocker_id": blocker.blocker_id,
                        "blocker_age_days": max((status.as_of - blocker.first_seen_on).days, 0),
                        "attributed": blocker.is_attributed,
                    },
                    observed_at=checkin.replied_at,
                    correlation_id=(
                        f"{checkin.correlation_id}:blocker-resolved:{blocker.blocker_id}"
                    ),
                )
            )

    async def _record_conversation_turn(self, turn: ConversationTurn) -> None:
        await self._conversation_repository.append_turn(turn)

    async def _user_turn_exists(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        chat_message_id: str,
    ) -> bool:
        return await self._conversation_repository.user_turn_exists(
            tenant_id,
            developer_id,
            chat_message_id,
        )

    def _conversation_history_tool(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        reference_at: datetime | None,
    ) -> ConversationHistoryTool:
        return ConversationHistoryTool(
            tenant_id=tenant_id,
            developer_id=developer_id,
            repository=self._conversation_repository,
            retention_days=self._conversation_retention_days,
            reference_at=reference_at,
        )

    def _issue_tracker_tool(
        self,
        *,
        tenant_id: str,
        developer_id: str,
    ) -> IssueTrackerTool:
        return IssueTrackerTool(
            tenant_id=tenant_id,
            developer_id=developer_id,
            issue_tracker=self._issue_tracker,
            resolve_assignee_id=partial(
                self._resolve_issue_tracker_assignee_id, tenant_id, developer_id
            ),
        )

    def _git_activity_tool(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        reference_at: datetime | None,
    ) -> GitActivityTool | None:
        if self._time_series_repository is None:
            return None
        return GitActivityTool(
            tenant_id=tenant_id,
            developer_id=developer_id,
            repository=self._time_series_repository,
            reference_at=reference_at,
        )

    def _agent_tools(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        reference_at: datetime | None,
    ) -> tuple[AgentTool, ...]:
        tools: list[AgentTool] = [
            self._conversation_history_tool(
                tenant_id=tenant_id,
                developer_id=developer_id,
                reference_at=reference_at,
            ),
            self._issue_tracker_tool(tenant_id=tenant_id, developer_id=developer_id),
        ]
        git_tool = self._git_activity_tool(
            tenant_id=tenant_id,
            developer_id=developer_id,
            reference_at=reference_at,
        )
        if git_tool is not None:
            tools.append(git_tool)
        return tuple(tools)

    async def _local_date_for_developer(
        self,
        tenant_id: str,
        developer_id: str,
        at: datetime,
    ) -> date:
        preference = await self._status_repository.checkin_preference_for(tenant_id, developer_id)
        return _local_date(
            at, preference.timezone if preference else None, self._tenant_default_timezone
        )

    async def _status_as_of_for_checkin(self, checkin: CheckIn, at: datetime) -> date:
        schedule_run = await self._status_repository.checkin_schedule_run_for_correlation(
            checkin.tenant_id,
            checkin.correlation_id,
        )
        if schedule_run is not None:
            return schedule_run.checkin_date
        return await self._local_date_for_developer(
            checkin.tenant_id,
            checkin.developer_id,
            at,
        )

    async def _local_date_matches(
        self,
        correlations: Iterable[CheckInCorrelation],
        received_at: datetime,
    ) -> list[CheckInCorrelation]:
        matches: list[CheckInCorrelation] = []
        for correlation in correlations:
            preference = await self._status_repository.checkin_preference_for(
                correlation.tenant_id,
                correlation.developer_id,
            )
            timezone = preference.timezone if preference else None
            asked_date = _local_date(correlation.asked_at, timezone, self._tenant_default_timezone)
            reply_date = _local_date(received_at, timezone, self._tenant_default_timezone)
            if correlation.asked_at <= received_at and asked_date == reply_date:
                matches.append(correlation)
        return sorted(matches, key=lambda correlation: correlation.asked_at, reverse=True)

    async def _latest_local_date_match(
        self,
        correlations: Iterable[CheckInCorrelation],
        received_at: datetime,
    ) -> CheckInCorrelation | None:
        matches = await self._local_date_matches(_dedupe_correlations(correlations), received_at)
        return matches[0] if matches else None

    async def _prior_open_blockers(
        self, checkin: CheckIn, at: datetime
    ) -> tuple[DeveloperBlocker, ...]:
        as_of = await self._status_as_of_for_checkin(checkin, at)
        status = await self._status_repository.latest_developer_status(
            checkin.tenant_id,
            checkin.developer_id,
            as_of,
        )
        if status is not None and status.source is StatusSource.UNKNOWN:
            # Matches the legacy rule: UNKNOWN statuses never seed carry-forward.
            status = None
        return await self._blockers.open_blockers(
            checkin.tenant_id,
            checkin.developer_id,
            as_of,
            legacy_status=status,
        )

    async def _recent_conversation_turns(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        exclude_chat_message_id: str | None = None,
        reference_at: datetime | None = None,
    ) -> list[ConversationTurn]:
        reference_time = reference_at or datetime.now(tz=UTC)
        turns = await self._conversation_repository.list_recent_turns(
            tenant_id,
            developer_id,
            limit=RECENT_CONVERSATION_TURN_LIMIT,
            since=reference_time - RECENT_CONVERSATION_LOOKBACK,
        )
        if exclude_chat_message_id is None:
            return turns
        return [turn for turn in turns if turn.chat_message_id != exclude_chat_message_id]

    async def _conversation_turns_for_correlation(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        correlation_id: str,
        reference_at: datetime,
    ) -> list[ConversationTurn]:
        turns = await self._conversation_repository.list_recent_turns(
            tenant_id,
            developer_id,
            limit=RECENT_CONVERSATION_TURN_LIMIT,
            since=reference_at - RECENT_CONVERSATION_LOOKBACK,
        )
        return [turn for turn in turns if turn.correlation_id == correlation_id]

    async def _correlation_turns_for_timeout(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        correlation_id: str,
        reference_at: datetime,
    ) -> list[ConversationTurn]:
        turns = await self._conversation_repository.list_recent_turns(
            tenant_id,
            developer_id,
            limit=MAX_HISTORY_LIMIT,
            since=reference_at - timedelta(days=self._conversation_retention_days),
        )
        return [turn for turn in turns if turn.correlation_id == correlation_id]

    async def _recent_facts(
        self,
        tenant_id: str,
        developer_id: str,
        issues: Iterable[Issue],
    ) -> list[FactEvent]:
        if self._time_series_repository is None:
            return []
        refs = [
            EntityRef(tenant_id=tenant_id, kind=NodeKind.DEVELOPER, id=developer_id),
            *(EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id=issue.key) for issue in issues),
        ]
        facts: list[FactEvent] = []
        since = datetime.now(tz=UTC) - timedelta(days=self._recent_fact_lookback_days)
        for ref in refs:
            facts.extend(await self._time_series_repository.list_facts(tenant_id, ref, since))
        return sorted(facts, key=lambda fact: fact.observed_at, reverse=True)[:10]

    def _compile_graph(self) -> StatusCollectorGraph:
        graph = StateGraph(StatusCollectorState)
        graph.add_node("build_context", self._build_context_node)
        graph.add_node("compose_dm", self._compose_dm_node)
        graph.add_node("send_dm", self._send_dm_node)
        graph.add_node("record_checkin", self._record_checkin_node)
        graph.set_entry_point("build_context")
        graph.add_edge("build_context", "compose_dm")
        graph.add_edge("compose_dm", "send_dm")
        graph.add_edge("send_dm", "record_checkin")
        graph.set_finish_point("record_checkin")
        return cast(StatusCollectorGraph, graph.compile())


_NO_CONTEXT = "No active issues or recent facts were available."

_ISSUE_KEY_IN_TEXT = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]+-\d+(?![A-Za-z0-9])")
_SENTENCE_END = re.compile(r"[.?!\n]+")
_ADDRESSES_PERSON = re.compile(r"\b(?:you|your|please|could|can|would)\b", re.IGNORECASE)
_TRACKER_UPDATE_VERB = re.compile(
    r"\b(?:update|move|mark|close|transition|set|change|resolve)\b", re.IGNORECASE
)
_TRACKER_NOUN = re.compile(r"\b(?:jira|ticket|tracker|board)\b", re.IGNORECASE)


def asks_person_to_update_tracker(question: str) -> bool:
    """Whether a follow-up asks the person to change the issue tracker themselves.

    One sentence must address the person, name an update verb and name the
    tracker: "Can you update the Jira ticket to Done?". A statement about the
    tracker ("IDP-5 is still marked In Progress in Jira.") is not an ask.
    """
    return any(
        _ADDRESSES_PERSON.search(sentence)
        and _TRACKER_UPDATE_VERB.search(sentence)
        and _TRACKER_NOUN.search(sentence)
        for sentence in _SENTENCE_END.split(question)
    )


def _new_correlation_id() -> str:
    return f"checkin-{uuid4().hex}"


def _pending_nudge_message_id(correlation_id: str, nudge_number: int = 1) -> str:
    return f"pending-nudge-{correlation_id}-{nudge_number}"


_ESCALATION_ROLE_LABELS: dict[EscalationTarget, str] = {
    EscalationTarget.SCRUM_MASTER: "scrum master",
    EscalationTarget.MANAGER: "manager",
}


# Who an escalation notice is about when no name is known: never a raw chat id.
_ESCALATION_SUBJECT_FALLBACK = "a team member"


def _compose_escalation_notice(
    *,
    target: EscalationTarget,
    developer_name: str | None,
    max_chars: int = OUTBOUND_DM_MAX_CHARS,
) -> str:
    """A privacy-safe non-response escalation notice (no raw reply content).

    ``developer_name`` is a display name, or ``None`` when none is known; the
    notice then says "a team member" rather than showing an id.
    """
    role_label = _ESCALATION_ROLE_LABELS.get(target, "escalation contact")
    subject = (developer_name or "").strip() or _ESCALATION_SUBJECT_FALLBACK
    notice = (
        f"Heads up: {subject} hasn't completed today's check-in yet. "
        f"You're notified as the {role_label} so you can follow up if needed."
    )
    if len(notice) > max_chars:
        return notice[: max(0, max_chars - 1)].rstrip() + "…"
    return notice


def _compose_consent_prompt_text(
    proposals: list[WriteBackAudit],
    *,
    max_chars: int = OUTBOUND_DM_MAX_CHARS,
) -> str:
    """A privacy-safe write-back consent prompt (issue key + target state only).

    Never echoes the developer's note or any raw reply content -- it references
    the concrete diff (which issue, to which state) and asks for a yes/no.
    """
    if len(proposals) == 1:
        proposal = proposals[0]
        prompt = (
            f"Want me to update {proposal.issue_key} to "
            f"“{target_state_label(proposal.target_state)}” "
            f"in the issue tracker? Reply yes or no."
        )
    else:
        diffs = ", ".join(
            f"{p.issue_key} → {target_state_label(p.target_state)}" for p in proposals
        )
        prompt = f"Want me to apply these issue-tracker updates: {diffs}? Reply yes or no."
        per_issue = (
            f"{prompt[: -len('Reply yes or no.')]}Reply yes or no, or per issue "
            f"(e.g. “yes for {proposals[0].issue_key}, no for {proposals[1].issue_key}”)."
        )
        if len(per_issue) <= max_chars:
            prompt = per_issue
    if len(prompt) > max_chars:
        return prompt[: max(0, max_chars - 1)].rstrip() + "…"
    return prompt


_CHECKIN_ACK_PLAIN = "Got it \U0001f44d Thanks — your update is recorded."
_CHECKIN_ACK_LOW_CONFIDENCE_FALLBACK = (
    "Got it \U0001f44d Recorded your update — reply 'fix' if I read it wrong."
)


def _compose_checkin_ack_text(
    *,
    signals: CheckInSignals,
    max_chars: int = OUTBOUND_DM_MAX_CHARS,
    applied: Sequence[WriteBackAudit] = (),
    held: Sequence[OpenMergeRequestHold] = (),
) -> str:
    """Deterministic "Got it" ack for a finalized check-in reply.

    A confident parse gets the plain ack. A low-confidence parse restates the
    recorded status (state + first blocker, never the raw reply) and invites a
    correction. Tracker updates OpenProgram just applied are named (issue key
    and state only), so the person knows they need not make them, and so is a
    ``done`` held back by an open merge request (issue key, its state and the
    request), so they know why the tracker did not move. Every branch stays
    within ``max_chars`` with a safe fallback.
    """
    if signals.parser_confident:
        text = _CHECKIN_ACK_PLAIN
    else:
        recorded = _recorded_status_phrase(signals)
        text = f"Got it \U0001f44d I recorded this as {recorded} — reply 'fix' if that's wrong."
        if len(text) > max_chars:
            return _cap_outbound_dm_text(_CHECKIN_ACK_LOW_CONFIDENCE_FALLBACK, max_chars)
    for note in (
        _applied_write_back_note(applied),
        *_open_merge_request_notes(held),
        _left_as_is_note(applied),
    ):
        if note is not None and len(f"{text} {note}") <= max_chars:
            text = f"{text} {note}"
    return _cap_outbound_dm_text(text, max_chars)


def open_merge_request_note(hold: OpenMergeRequestHold) -> str:
    """Why a ``done`` claim did not move the tracker: its merge request is still open.

    "INS-2 still has an open merge request (insights-pipeline !1), so I left it
    In Progress in the issue tracker; it can move to Done once that merges."
    """
    many = len(hold.merge_requests) > 1
    noun = "open merge requests" if many else "an open merge request"
    named = f" ({', '.join(hold.merge_requests)})" if hold.merge_requests else ""
    state = target_state_label(hold.current_state) if hold.current_state else "as it is"
    merges = "those merge" if many else "that merges"
    return (
        f"{hold.issue_key} still has {noun}{named}, so I left it {state} in the issue "
        f"tracker; it can move to Done once {merges}."
    )


def _open_merge_request_notes(held: Sequence[OpenMergeRequestHold]) -> list[str]:
    return [open_merge_request_note(hold) for hold in held]


def _applied_write_back_note(applied: Sequence[WriteBackAudit]) -> str | None:
    updates = [
        f"{audit.issue_key} to {target_state_label(audit.after_state or audit.target_state)}"
        for audit in applied
        if audit.status is WriteBackStatus.APPLIED
    ]
    if not updates:
        return None
    return f"I updated {_joined(updates)} in the issue tracker."


def _left_as_is_note(rows: Sequence[WriteBackAudit]) -> str | None:
    """Issues the person said no to, or never answered about before the check-in closed."""
    declined = [
        row.issue_key
        for row in rows
        if row.status is WriteBackStatus.DECLINED and row.source == CONSENT_REPLY_SOURCE
    ]
    expired = [
        row.issue_key
        for row in rows
        if row.status is WriteBackStatus.EXPIRED and row.source == CHECKIN_CLOSED_SOURCE
    ]
    notes = []
    if declined:
        notes.append(f"I left {_joined(declined)} as is in the issue tracker, as you asked.")
    if expired:
        notes.append(
            f"The check-in closed before you answered about {_joined(expired)}, "
            "so I left the issue tracker as it is."
        )
    return " ".join(notes) or None


def _recorded_status_phrase(signals: CheckInSignals) -> str:
    if signals.blockers:
        return f"in progress with a blocker on {_truncate_subject(signals.blockers[0])}"
    return "in progress with no blockers"


def _cap_outbound_dm_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return text[:max_chars]
    return f"{text[: max_chars - 1].rstrip()}…"


def _safe_outbound_checkin_text(
    generated_text: str,
    *,
    developer_id: str,
    developer_name: str | None,
    context: str,
    purpose: Literal["checkin", "nudge"],
    max_chars: int = OUTBOUND_DM_MAX_CHARS,
) -> str:
    text = _normalize_outbound_dm_text(generated_text)
    if not text or len(text) > max_chars or _looks_like_prompt_echo(text):
        return _fallback_outbound_checkin_text(
            developer_id=developer_id,
            developer_name=developer_name,
            context=context,
            purpose=purpose,
        )
    return text


def _normalize_outbound_dm_text(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    normalized = " ".join(lines).strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
        normalized = normalized[1:-1].strip()
    return normalized


def _looks_like_prompt_echo(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    if any(marker in normalized for marker in _PROMPT_ECHO_MARKERS):
        return True
    label_hits = sum(1 for label in ("developer:", "context:", "prompt:") if label in normalized)
    return label_hits >= 2


def _fallback_outbound_checkin_text(
    *,
    developer_id: str,
    developer_name: str | None,
    context: str,
    purpose: Literal["checkin", "nudge"],
) -> str:
    greeting = f"Hi {_display_name_for_dm(developer_name, developer_id)}, "
    subject = _primary_context_subject(context)
    if purpose == "nudge":
        if subject:
            return (
                f"{greeting}quick follow-up on {subject}: please share progress, blockers, "
                "and any ETA changes when you can."
            )
        return (
            f"{greeting}quick follow-up on today's status check: please share progress, blockers, "
            "and any ETA changes when you can."
        )
    if subject:
        return (
            f"{greeting}quick status on {subject}: what moved today, any blockers, "
            "and any ETA changes?"
        )
    return f"{greeting}quick status check: what moved today, any blockers, and any ETA changes?"


def _display_name_for_dm(developer_name: str | None, developer_id: str) -> str:
    display_name = (developer_name or "").strip()
    if display_name:
        return display_name
    if developer_id.strip():
        return developer_id.strip()
    return "there"


def _primary_context_subject(context: str) -> str | None:
    if not context or context == _NO_CONTEXT:
        return None
    for line in context.splitlines():
        stripped = line.strip()
        if not stripped.startswith("Active issue "):
            continue
        subject = stripped.removeprefix("Active issue ").rsplit(" (", 1)[0].strip()
        return _truncate_subject(subject) if subject else None
    return None


def _truncate_subject(subject: str) -> str:
    if len(subject) <= 96:
        return subject
    return f"{subject[:93].rstrip()}..."


def _single_correlation_or_log_ambiguous(
    message: InboundMessage,
    matches: list[CheckInCorrelation],
    *,
    chat_thread_ref: str | None = None,
) -> str | None:
    if len(matches) == 1:
        return matches[0].correlation_id
    if len(matches) > 1:
        values: dict[str, object] = {
            "tenant_id": message.tenant_id,
            "chat_user_ref": message.user.external_id,
            "message_id": message.message_id,
            "correlation_ids": [correlation.correlation_id for correlation in matches],
        }
        if chat_thread_ref is not None:
            values["chat_thread_ref"] = chat_thread_ref
        _logger.info("reply_correlation_ambiguous", **values)
    return None


def _is_non_status_ack(turn: ConversationTurn) -> bool:
    return turn.role is ConversationRole.AGENT and turn.content == NON_STATUS_ACK_TEXT


def _answered_as_non_status(turns: Iterable[ConversationTurn]) -> bool:
    """Whether the person was told that a reply of this check-in carried no status."""
    return any(_is_non_status_ack(turn) for turn in turns)


def _signals_with_note(signals: CheckInSignals, note: str) -> CheckInSignals:
    # replace() preserves every other field (issue_updates, parser_confident, ...).
    return replace(signals, progress_note=f"{signals.progress_note} {note}")


def _checkin_messages_text(
    checkin: CheckIn,
    other_turns: Iterable[ConversationTurn],
    message: InboundMessage,
) -> str:
    """The person's messages in this check-in, oldest first, as one raw reply.

    The same shape the clarification-timeout finalizer stores. ``other_turns``
    are the recent turns without ``message`` itself.
    """
    messages = [
        (turn.observed_at, turn.content)
        for turn in other_turns
        if turn.role is ConversationRole.USER and turn.correlation_id == checkin.correlation_id
    ]
    messages.append((message.received_at, message.text))
    return "\n".join(text for _, text in sorted(messages, key=lambda item: item[0]))


def _checkin_status_text(checkin: CheckIn, other_turns: Iterable[ConversationTurn]) -> str:
    """The person's earlier messages in this check-in, oldest first (no consent answer)."""
    return "\n".join(
        turn.content
        for turn in sorted(other_turns, key=lambda item: item.observed_at)
        if turn.role is ConversationRole.USER and turn.correlation_id == checkin.correlation_id
    )


def _reconcile_mode_for_reply(signals: CheckInSignals, raw_reply: str) -> ReconcileMode:
    """Heuristic all-resolved fallback applies only to unstructured replies.

    When the model reported structured blockers or resolved ids, those are the
    single source of resolution truth; the legacy text heuristic then never
    fires (it kept "no blockers now" replies working before structured output).
    """
    if signals.blocker_reports or signals.resolved_blocker_ids:
        return ReconcileMode.CHECKIN
    if _explicitly_resolves_blockers(raw_reply):
        return ReconcileMode.RESOLVE_ALL
    return ReconcileMode.CHECKIN


def _signals_with_open_blockers(
    signals: CheckInSignals,
    reconciliation: BlockerReconciliation,
) -> CheckInSignals:
    """Derive the day's compat blocker strings from the true open set.

    Unlike the legacy carry-forward, a reply naming a NEW blocker no longer
    silently drops the old open one from the day's status — the strings are
    the union of what is actually open after reconciliation.
    """
    open_descriptions = tuple(blocker.description for blocker in reconciliation.open_after)
    note = signals.progress_note
    if reconciliation.carried:
        carried_text = ", ".join(blocker.description for blocker in reconciliation.carried)
        note = f"{note} Prior blockers carried forward until explicitly resolved: {carried_text}."
    if open_descriptions == signals.blockers and note == signals.progress_note:
        return signals
    # replace() preserves every other field (issue_updates, parser_confident, ...).
    return replace(signals, progress_note=note, blockers=open_descriptions)


def _attribution_question(
    candidates: tuple[DeveloperBlocker, ...],
    pods: tuple[GraphNode, ...],
) -> str:
    pod_names = ", ".join(pod.name for pod in pods)
    if len(candidates) == 1:
        description = candidates[0].description
        return (
            f"Quick check so this lands on the right board: which pod or work item is "
            f'"{description}" blocking? Your pods: {pod_names}. '
            "Reply with a pod name or an issue key."
        )
    numbered = " ".join(
        f"{index}) {blocker.description}" for index, blocker in enumerate(candidates, start=1)
    )
    return (
        f"Which pod or work item does each blocker belong to? {numbered}. "
        f'Your pods: {pod_names}. Reply like "1: {pods[0].name}" or "1: PROJ-123".'
    )


def _missing_required_status_details(
    signals: CheckInSignals,
) -> tuple[Literal["blockers", "eta"], ...]:
    missing: list[Literal["blockers", "eta"]] = []
    if not _blockers_answered(signals):
        missing.append("blockers")
    if not _eta_answered(signals):
        missing.append("eta")
    return tuple(missing)


def _blockers_answered(signals: CheckInSignals) -> bool:
    return signals.blockers_answered or bool(signals.blockers)


# What a model-drafted follow-up asks for, read from its words (N29).
_ASKS_FOR_ETA = re.compile(
    r"\b(?:etas?|deadline|timeline|target date|how long|when (?:will|do|can|should) you)\b",
    re.IGNORECASE,
)
_ASKS_FOR_BLOCKERS = re.compile(
    r"\b(?:blockers?|blocked|blocking|impediments?|stuck)\b", re.IGNORECASE
)
_ASKS_FOR_PROGRESS = re.compile(r"\b(?:progress|concrete|accomplished)\b", re.IGNORECASE)
# A question about anything else is kept: a contradiction with the tracker or
# Git, a merge request, who or which.
_ASKS_ABOUT_SOMETHING_ELSE = re.compile(
    r"\b(?:jira|tracker|ticket|merge request|mr|pull request|pr|branch|commit\w*|merged|"
    r"done|closed|who|which)\b",
    re.IGNORECASE,
)


def _follow_up_already_answered(question: str, earlier: CheckInSignals) -> bool:
    """Whether every detail ``question`` asks for is in the ``earlier`` messages.

    ETA and blockers count once any earlier message answered them (the merged
    signals say so). Progress counts as given when an earlier message described
    each issue the question names (any issue, if it names none).
    """
    if _ASKS_ABOUT_SOMETHING_ELSE.search(question):
        return False
    answered: list[bool] = []
    if _ASKS_FOR_ETA.search(question):
        answered.append(_eta_answered(earlier))
    if _ASKS_FOR_BLOCKERS.search(question):
        answered.append(_blockers_answered(earlier))
    if _ASKS_FOR_PROGRESS.search(question):
        answered.append(_progress_given(question, earlier))
    return bool(answered) and all(answered)


def _progress_given(question: str, earlier: CheckInSignals) -> bool:
    described = {
        claim.issue_key
        for claim in earlier.issue_updates
        if claim.issue_key and (claim.claimed_state or claim.note)
    }
    named = set(_ISSUE_KEY_IN_TEXT.findall(question))
    return bool(described) and named <= described


def _eta_answered(signals: CheckInSignals) -> bool:
    return signals.eta_answered or signals.eta_change_days is not None


def _missing_required_status_question(
    missing: tuple[Literal["blockers", "eta"], ...],
    subject: _FollowUpSubject | None = None,
) -> str:
    if subject is None:
        if missing == ("blockers", "eta"):
            return "Thanks. Any blockers on this work, and what is your ETA to finish it?"
        if missing == ("blockers",):
            return "Thanks. Any blockers on this work?"
        return "Thanks. What is your ETA to finish it?"
    pronoun = "it" if len(subject.keys) == 1 else "them"
    if missing == ("blockers", "eta"):
        return f"Thanks. Any blockers on {subject.text}, and what is your ETA to finish {pronoun}?"
    if missing == ("blockers",):
        return f"Thanks. Any blockers on {subject.text}?"
    return f"Thanks. What is your ETA to finish {subject.text}?"


@dataclass(frozen=True)
class _FollowUpSubject:
    """The issues a follow-up question is about: their keys, and how it names them."""

    keys: tuple[str, ...]
    text: str


# A follow-up names at most this many issues, with titles only for one or two.
_FOLLOW_UP_ISSUE_LIMIT = 3
_FOLLOW_UP_TITLE_CHARS = 40
_THANKS_LEAD = "Thanks. "


def _follow_up_issue_keys(
    claims: Iterable[IssueClaim], tracker_issues: Iterable[Issue]
) -> tuple[str, ...]:
    claimed = [claim for claim in claims if claim.issue_key]
    states = {claim.issue_key: canonical_target_state(claim.claimed_state) for claim in claimed}
    not_done = [
        claim.issue_key
        for claim in claimed
        if not claim.claimed_done and states[claim.issue_key] is not WriteBackTarget.DONE
    ]
    under_way = [key for key in not_done if states[key] is not WriteBackTarget.TODO]
    keys = (
        under_way
        or not_done
        or [issue.key for issue in tracker_issues if issue.state in _ACTIVE_ISSUE_STATES]
    )
    return tuple(dict.fromkeys(keys))


def _issue_subject_text(keys: Sequence[str], titles: Mapping[str, str]) -> str:
    """``CHK-4 (Step-up flow)``; ``CHK-4, CHK-9 and 2 more`` once there are many."""
    shown = list(keys[:_FOLLOW_UP_ISSUE_LIMIT])
    if len(keys) <= 2:
        shown = [
            f"{key} ({_short_title(titles[key])})" if titles.get(key) else key for key in shown
        ]
    if len(keys) > len(shown):
        shown.append(f"{len(keys) - len(shown)} more")
    return _joined(shown)


def _short_title(title: str) -> str:
    title = " ".join(title.split())
    if len(title) <= _FOLLOW_UP_TITLE_CHARS:
        return title
    return f"{title[: _FOLLOW_UP_TITLE_CHARS - 3].rstrip()}..."


def _question_naming_issue(question: str, subject: _FollowUpSubject | None) -> str:
    """A model-drafted follow-up that names no issue key gets the issues it is about.

    "What is your ETA to finish it?" did not say which issue (R1-9); it becomes
    "About CHK-4 (Step-up flow): What is your ETA to finish it?". A question
    that already names a key is left as drafted.
    """
    if subject is None or _ISSUE_KEY_IN_TEXT.search(question):
        return question
    lead = _THANKS_LEAD if question.startswith(_THANKS_LEAD) else ""
    return f"{lead}About {subject.text}: {question.removeprefix(lead)}"


def _status_source_for_signals(signals: CheckInSignals) -> StatusSource:
    # A low-confidence parse (unparseable model output) must never roll up green.
    if not signals.parser_confident or _missing_required_status_details(signals):
        return StatusSource.PARTIAL
    return StatusSource.CONFIRMED


def _summary_with_missing_required_details(
    summary: str,
    missing: tuple[Literal["blockers", "eta"], ...],
) -> str:
    note = _missing_required_status_note(missing)
    if note is None or note in summary:
        return summary
    return f"{summary} {note}"


def _missing_required_status_note(
    missing: tuple[Literal["blockers", "eta"], ...],
) -> str | None:
    if missing == ("blockers", "eta"):
        return "Blocker status and ETA were not provided."
    if missing == ("blockers",):
        return "Blocker status was not provided."
    if missing == ("eta",):
        return "ETA was not provided."
    return None


def _open_resolution(
    mention: CrossPersonMention,
    member: MemberContact,
    *,
    request_id: str | None = None,
) -> CrossPersonRequestResolution:
    return CrossPersonRequestResolution(
        mention=mention,
        status=CrossPersonRequestStatus.OPEN,
        counterpart_id=member.chat_id,
        counterpart_display_name=member.name,
        counterpart_email=member.email,
        request_id=request_id,
    )


def _mention_of(request: CrossPersonRequest) -> CrossPersonMention:
    return CrossPersonMention(
        raw_name=request.raw_name or "",
        kind=request.kind.value,
        note=request.note,
        email=request.email,
    )


def _request_key(kind: str, raw_name: str) -> tuple[str, tuple[str, ...]]:
    """Two mentions of the same ask of the same name are one request."""
    return (kind, name_words(raw_name))


def _person_clarification_question(
    mention: CrossPersonMention,
    matches: Iterable[MemberContact],
) -> str:
    candidates = tuple(matches)[:5]
    if not candidates:
        return (
            f"I could not find {mention.raw_name} on the team. "
            "Reply with the person's name or email."
        )
    options = " or ".join(_person_option(member) for member in candidates)
    return f"Did you mean {options}? Reply with the name or email."


def _person_option(member: MemberContact) -> str:
    if member.email:
        return f"{member.name} ({member.email})"
    return member.name


def _explicitly_resolves_blockers(raw_reply: str) -> bool:
    normalized = raw_reply.lower()
    negated_resolution_phrases = (
        "not resolved",
        "not yet resolved",
        "isn't resolved",
        "is not resolved",
        "wasn't resolved",
        "was not resolved",
        "not cleared",
        "isn't cleared",
        "is not cleared",
        "not unblocked",
        "still blocked",
        "still blocking",
        "still unresolved",
        "remains blocked",
        "unresolved",
    )
    if any(phrase in normalized for phrase in negated_resolution_phrases):
        return False
    resolution_phrases = (
        "no blocker",
        "no blockers",
        "not blocked",
        "unblocked",
        "resolved",
        "cleared",
        "blocker is gone",
        "blockers are gone",
        "nothing blocking",
    )
    return any(phrase in normalized for phrase in resolution_phrases)


def _open_blockers_from_status(status: DeveloperStatus | None) -> tuple[str, ...]:
    if status is None or status.source is StatusSource.UNKNOWN:
        return ()
    blockers = []
    for blocker in status.blockers:
        clean = blocker.strip()
        if clean and clean.lower() != "no confirmed reply" and clean not in blockers:
            blockers.append(clean)
    return tuple(blockers)


def _prioritize_issues(issues: Iterable[Issue]) -> list[Issue]:
    return sorted(
        issues,
        key=lambda issue: (
            issue.state is not IssueState.BLOCKED,
            not _is_priority_issue(issue),
            -(issue.updated_at.timestamp() if issue.updated_at is not None else 0.0),
            issue.key,
        ),
    )


def _is_priority_issue(issue: Issue) -> bool:
    metadata = issue.metadata
    if _truthy(metadata.get("critical_path")):
        return True
    priority = metadata.get("priority")
    if isinstance(priority, str) and priority.strip().lower() in {
        "blocker",
        "critical",
        "highest",
        "high",
        "p0",
        "p1",
    }:
        return True
    labels = metadata.get("labels")
    label_values: tuple[str, ...]
    if isinstance(labels, str):
        label_values = (labels,)
    elif isinstance(labels, list | tuple | set):
        label_values = tuple(label for label in labels if isinstance(label, str))
    else:
        label_values = ()
    priority_labels = {
        "blocker",
        "critical",
        "critical-path",
        "critical_path",
        "highest",
        "high-priority",
        "p0",
        "p1",
    }
    return any(label.strip().lower() in priority_labels for label in label_values)


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    if isinstance(value, int | float):
        return value > 0
    return False


def _candidate_correlation_dates(
    received_at: datetime, tenant_default_timezone: str
) -> tuple[date, ...]:
    utc_date = received_at.date()
    dates = {
        utc_date - timedelta(days=1),
        utc_date,
        utc_date + timedelta(days=1),
        _local_date(received_at, None, tenant_default_timezone),
    }
    return tuple(sorted(dates))


def _dedupe_correlations(correlations: Iterable[CheckInCorrelation]) -> list[CheckInCorrelation]:
    deduped: dict[str, CheckInCorrelation] = {}
    for correlation in correlations:
        current = deduped.get(correlation.correlation_id)
        if current is None or current.asked_at < correlation.asked_at:
            deduped[correlation.correlation_id] = correlation
    return list(deduped.values())


def _local_date(at: datetime, timezone: str | None, tenant_default_timezone: str) -> date:
    return local_date(at, resolve_timezone(timezone, tenant_default_timezone))


def _status_context_lines(status: DeveloperStatus | None) -> list[str]:
    if status is None or status.source is StatusSource.UNKNOWN:
        return []
    lines: list[str] = []
    blockers = _open_blockers_from_status(status)
    if blockers:
        lines.append(
            f"Yesterday unresolved blockers from {status.as_of.isoformat()}: {'; '.join(blockers)}"
        )
    if status.source in {StatusSource.CONFIRMED, StatusSource.PARTIAL, StatusSource.STALE}:
        lines.append(
            f"Last {status.source.value} status from {status.as_of.isoformat()}: "
            f"{_truncate_subject(status.summary)}"
        )
    return lines


def _issue_context_lines(
    issues: Iterable[Issue],
    *,
    facts: Iterable[FactEvent] = (),
    reference_at: datetime | None = None,
) -> list[str]:
    reference_time = reference_at or datetime.now(tz=UTC)
    fact_tuple = tuple(facts)
    return [_issue_context_line(issue, fact_tuple, reference_time) for issue in list(issues)[:8]]


def _issue_context_line(
    issue: Issue,
    facts: tuple[FactEvent, ...],
    reference_at: datetime,
) -> str:
    details = [issue.state.value]
    days_since_update = _days_since(issue.updated_at, reference_at)
    if days_since_update is not None:
        details.append(f"days_since_update={days_since_update}")
    if issue.state is IssueState.BLOCKED:
        details.append("blocked=true")
    if _has_recent_git_activity(issue.key, facts):
        details.append("recent_git_activity=true")
    if days_since_update is not None and days_since_update >= 7:
        details.append("stale=true")
    return f"Active issue {issue.key}: {issue.title} ({', '.join(details)})"


def _fact_context_lines(facts: Iterable[FactEvent]) -> list[str]:
    lines: list[str] = []
    for fact in facts:
        prefix = (
            "Recent Git activity"
            if fact.source in {"vcs_commit", "vcs_pull_request"}
            else "Recent fact"
        )
        lines.append(
            f"{prefix} for {fact.entity_ref.kind.value}/{fact.entity_ref.id}: "
            f"source={fact.source}, {_format_payload(fact.payload)}"
        )
    return lines


_INFERRED_ISSUE_LIMIT = 3
# Work under way: what a silent person's status can be inferred from. The
# trackers read In Review as In Progress.
_ACTIVE_ISSUE_STATES = frozenset({IssueState.IN_PROGRESS, IssueState.BLOCKED})


def _inferred_summary(
    issues: list[Issue],
    facts: Iterable[FactEvent],
    reference_at: datetime,
) -> str:
    """A non-response status summary a person can read.

    Names what it was inferred from: the most pressing active issues, the last
    check-in reply, items with risks flagged, and a count of recent Git
    activity. It reads only keyed payload fields and never repeats free text:
    a fact can carry words taken from a reply, and this summary is returned by
    persona APIs.

    An empty issue list is not reported as "no active issues": the tracker can
    come back empty for an unmapped assignee while the developer has work.
    """
    facts = sorted(facts, key=lambda fact: fact.observed_at, reverse=True)
    basis: list[str] = []
    if issues:
        named = [
            _inferred_issue_label(issue, reference_at) for issue in issues[:_INFERRED_ISSUE_LIMIT]
        ]
        if len(issues) > len(named):
            named.append(f"{len(issues) - len(named)} more")
        noun = "active issue" if len(issues) == 1 else "active issues"
        basis.append(f"{len(issues)} {noun}: {_joined(named)}")
    last_checkin = next((fact for fact in facts if fact.source == "checkin"), None)
    if last_checkin is not None:
        replied_on = last_checkin.observed_at.date()
        reply = f"last check-in reply on {replied_on:%b} {replied_on.day}"
        blocker_count = last_checkin.payload.get("blocker_count")
        if isinstance(blocker_count, int) and not isinstance(blocker_count, bool) and blocker_count:
            reply += f", with {_counted(((blocker_count, 'blocker', 'blockers'),))}"
        basis.append(reply)
    risky = list(
        dict.fromkeys(
            entity_id
            for fact in facts
            if fact.source == RISK_FACT_SOURCE
            and isinstance(entity_id := fact.payload.get("entity_id"), str)
            and entity_id
        )
    )
    if risky:
        basis.append(f"risks flagged on {_joined(risky)}")
    sources = [fact.source for fact in facts]
    git_activity = _counted(
        (
            (sources.count("vcs_commit"), "commit", "commits"),
            (sources.count("vcs_pull_request"), "pull request", "pull requests"),
        )
    )
    if git_activity:
        basis.append(f"recent Git activity: {git_activity}")

    return inferred_summary("; ".join(basis) or "recent signals")


def _inferred_issue_label(issue: Issue, reference_at: datetime) -> str:
    label = f"{issue.key} {_truncate_subject(issue.title)}"
    if issue.state is IssueState.BLOCKED:
        return f"{label} (blocked)"
    days_since_update = _days_since(issue.updated_at, reference_at)
    if days_since_update is not None and days_since_update >= 7:
        return f"{label} (no update for {days_since_update} days)"
    return label


def _counted(counts: Iterable[tuple[int, str, str]]) -> str:
    return _joined(
        [
            f"{count} {singular if count == 1 else plural}"
            for count, singular, plural in counts
            if count
        ]
    )


def _joined(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _days_since(value: datetime | None, reference_at: datetime) -> int | None:
    if value is None:
        return None
    return max(0, (reference_at - value).days)


def _has_recent_git_activity(issue_key: str, facts: Iterable[FactEvent]) -> bool:
    pattern = _issue_key_pattern(issue_key)
    for fact in facts:
        if fact.source not in {"vcs_commit", "vcs_pull_request"}:
            continue
        if fact.entity_ref.id.casefold() == issue_key.casefold():
            return True
        if any(isinstance(value, str) and pattern.search(value) for value in fact.payload.values()):
            return True
    return False


def _issue_key_pattern(issue_key: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(issue_key)}(?![A-Za-z0-9])", re.IGNORECASE)


def _format_payload(payload: Mapping[str, JsonScalar]) -> str:
    pairs = [
        f"{key}={value}"
        for key, value in sorted(payload.items())
        if value is not None and isinstance(value, str | int | float | bool)
    ]
    return ", ".join(pairs[:4]) if pairs else "no scalar details"
