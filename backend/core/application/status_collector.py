from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
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
from core.application.conversation_history import llm_messages_from_turns
from core.application.status_parsing import ClarificationEvaluator, StatusParser
from core.application.tools.conversation_history import MAX_HISTORY_LIMIT, ConversationHistoryTool
from core.application.tools.git_activity import GitActivityTool
from core.application.tools.issue_tracker import IssueTrackerTool
from core.application.writeback_service import WriteBackService
from core.domain.blockers import (
    BlockerReconciliation,
    BlockerSource,
    DeveloperBlocker,
    ReconcileMode,
)
from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.cross_person import CrossPersonRequestResolution, CrossPersonRequestStatus
from core.domain.directory import DirectoryUser
from core.domain.escalation import EscalationTarget
from core.domain.graph import EntityRef, FactEvent, GraphNode, JsonScalar, NodeKind
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
    StatusSource,
    local_date,
    resolve_timezone,
)
from core.domain.writeback import WriteBackAudit, WriteBackStatus
from core.ports.chat import ChatProvider
from core.ports.directory import DirectoryUserRepository
from core.ports.issue_tracker import IssueTracker
from core.ports.llm import LlmProvider
from core.ports.repositories import (
    ConversationRepository,
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


@dataclass(frozen=True, kw_only=True)
class _CrossPersonResolutionResult:
    resolutions: tuple[CrossPersonRequestResolution, ...]
    clarification_question: str | None = None


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
        self._write_back_service = write_back_service
        # Without a graph repository the attribution machinery degrades
        # cleanly: no pods resolve, so no attribution question is ever asked.
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

        if checkin.replied_at is not None:
            return await self._handle_already_replied(checkin=checkin, message=message)
        if await self._register_reply_turn(checkin, message, allow_reprocess=allow_reprocess):
            return ReplyOutcome(kind="ignored")

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
        )
        if not decision.is_status_update:
            await self._send_non_status_ack(checkin=checkin, message=message)
            span.set_attribute("openprogram.reply_classification", "non_status")
            span.set_attribute("openprogram.has_blocker", False)
            return ReplyOutcome(kind="acknowledged")

        clarification_count = await self._status_repository.checkin_clarification_count(
            checkin.tenant_id,
            checkin.correlation_id,
        )
        if (
            not decision.sufficient
            and decision.question is not None
            and clarification_count < self._checkin_max_clarifications
        ):
            await self._send_clarification(
                checkin=checkin,
                message=message,
                question=decision.question,
                clarification_number=clarification_count + 1,
            )
            span.set_attribute("openprogram.reply_classification", "clarifying")
            span.set_attribute(
                "openprogram.has_blocker",
                bool(decision.signals and decision.signals.blockers),
            )
            return ReplyOutcome(kind="clarifying")

        signals = decision.signals or await self._parser.parse_reply(
            tenant_id=message.tenant_id,
            developer_id=checkin.developer_id,
            raw_reply=message.text,
            correlation_id=message.correlation_id,
            conversation_turns=conversation_turns,
            tools=tools,
            prior_blockers=prior_blockers,
        )
        reconciliation = await self._reconcile_reply_blockers(
            checkin=checkin,
            at=message.received_at,
            prior=prior_blockers,
            signals=signals,
            raw_reply=message.text,
        )
        required_details_outcome = await self._maybe_required_details_clarification(
            checkin=checkin,
            message=message,
            signals=signals,
            reconciliation=reconciliation,
            clarification_count=clarification_count,
        )
        if required_details_outcome is not None:
            span.set_attribute("openprogram.reply_classification", "clarifying")
            span.set_attribute(
                "openprogram.has_blocker",
                bool(required_details_outcome.status and required_details_outcome.status.blockers),
            )
            return required_details_outcome
        if not decision.sufficient:
            signals = _signals_with_note(
                signals,
                "Clarification cap reached before all details were confirmed.",
            )
        person_resolution = await self._resolve_cross_person_requests(
            checkin=checkin,
            message=message,
            signals=signals,
            clarification_count=clarification_count,
        )
        if person_resolution.clarification_question is not None:
            await self._send_clarification(
                checkin=checkin,
                message=message,
                question=person_resolution.clarification_question,
                clarification_number=clarification_count + 1,
            )
            span.set_attribute("openprogram.reply_classification", "needs_person_resolution")
            span.set_attribute("openprogram.has_blocker", bool(signals.blockers))
            return ReplyOutcome(kind="clarifying")
        attribution_outcome, reconciliation = await self._maybe_attribution_clarification(
            checkin=checkin,
            message=message,
            signals=signals,
            reconciliation=reconciliation,
            clarification_count=clarification_count,
        )
        if attribution_outcome is not None:
            span.set_attribute("openprogram.reply_classification", "clarifying")
            span.set_attribute("openprogram.has_blocker", True)
            return attribution_outcome
        status = await self._finalize_checkin_reply(
            checkin=checkin,
            replied_at=message.received_at,
            raw_reply=message.text,
            signals=signals,
            reconciliation=reconciliation,
        )
        span.set_attribute("openprogram.reply_classification", "status_update")
        span.set_attribute("openprogram.has_blocker", bool(status.blockers))
        return ReplyOutcome(
            kind="processed",
            status=status,
            cross_person_requests=person_resolution.resolutions,
        )

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
        partial_status = await self._record_partial_checkin_status(
            checkin=checkin,
            as_of_at=message.received_at,
            signals=required_check_signals,
            reconciliation=reconciliation,
        )
        await self._send_clarification(
            checkin=checkin,
            message=message,
            question=_missing_required_status_question(missing_required),
            clarification_number=clarification_count + 1,
        )
        return ReplyOutcome(kind="clarifying", status=partial_status)

    async def _maybe_attribution_clarification(
        self,
        *,
        checkin: CheckIn,
        message: InboundMessage,
        signals: CheckInSignals,
        reconciliation: BlockerReconciliation,
        clarification_count: int,
    ) -> tuple[ReplyOutcome | None, BlockerReconciliation]:
        """Ask (once per blocker, ever) which pod/work item an open blocker belongs to.

        Only multi-pod developers are asked, only within the clarification
        budget, and required-detail/person clarifications always outrank this
        one. A single-pod developer's unattributed blockers auto-attribute.
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
                developer_name=developer_name or checkin.developer_id,
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

        inferred = await self.infer_fallback_status(
            tenant_id=tenant_id,
            developer_id=developer_id,
            as_of=as_of,
            developer_name=developer_name,
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
                summary=(
                    "No confirmed check-in after a nudge. "
                    f"Last known {prior.source.value} status on {prior.as_of.isoformat()}: "
                    f"{prior.summary}"
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
            summary="No confirmed check-in after a nudge. Current status is unknown.",
        )
        await self._status_repository.record_developer_status(unknown)
        return unknown

    async def infer_fallback_status(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        as_of: date,
        developer_name: str | None = None,
    ) -> DeveloperStatus | None:
        context = await self.build_context(
            tenant_id=tenant_id,
            developer_id=developer_id,
            developer_name=developer_name,
            include_status=False,
        )
        if context == _NO_CONTEXT:
            return None
        return DeveloperStatus(
            tenant_id=tenant_id,
            developer_id=developer_id,
            as_of=as_of,
            source=StatusSource.INFERRED,
            blockers=("no confirmed reply",),
            summary=f"No confirmed check-in after a nudge. Inferred from context: {context}",
        )

    async def _resolve_issue_tracker_assignee_id(self, tenant_id: str, developer_id: str) -> str:
        """Resolve the external id the issue tracker indexes assignments by.

        Jira indexes issues by ``accountId`` rather than the chat-provider id
        used as the canonical ``developer_id``. When an identity link maps the
        developer to a ``jira_account_id`` we query by that; otherwise we fall
        back to the canonical id so unmapped developers keep prior behaviour.
        """
        if self._identity_link_repository is None:
            return developer_id
        link = await self._identity_link_repository.get_identity_link(tenant_id, developer_id)
        if link is not None and link.jira_account_id:
            return link.jira_account_id
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
    ) -> str:
        text = "Thanks. I'll keep the check-in open for your status update."
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

    async def _resolve_cross_person_requests(
        self,
        *,
        checkin: CheckIn,
        message: InboundMessage,
        signals: CheckInSignals,
        clarification_count: int,
    ) -> _CrossPersonResolutionResult:
        if not signals.requests:
            return _CrossPersonResolutionResult(resolutions=())
        resolutions: list[CrossPersonRequestResolution] = []
        for mention in signals.requests:
            matches = await self._directory_matches_for_mention(checkin.tenant_id, mention)
            if len(matches) == 1:
                user = matches[0]
                resolutions.append(
                    CrossPersonRequestResolution(
                        mention=mention,
                        status=CrossPersonRequestStatus.OPEN,
                        counterpart_id=user.external_id,
                        counterpart_display_name=user.display_name,
                        counterpart_email=user.email,
                    )
                )
                continue
            if clarification_count < self._checkin_max_clarifications:
                return _CrossPersonResolutionResult(
                    resolutions=(),
                    clarification_question=_person_clarification_question(mention, matches),
                )
            resolutions.append(
                CrossPersonRequestResolution(
                    mention=mention,
                    status=CrossPersonRequestStatus.NEEDS_RESOLUTION,
                )
            )
        return _CrossPersonResolutionResult(resolutions=tuple(resolutions))

    async def _directory_matches_for_mention(
        self,
        tenant_id: str,
        mention: CrossPersonMention,
    ) -> list[DirectoryUser]:
        if self._directory_repository is None:
            return []
        query = (mention.email or mention.raw_name).strip()
        if not query:
            return []
        matches = await self._directory_repository.search(tenant_id, query, limit=10)
        exact_email = mention.email or (query if "@" in query else None)
        if exact_email is not None:
            normalized = exact_email.casefold()
            exact_matches = [
                user
                for user in matches
                if user.email is not None and user.email.casefold() == normalized
            ]
            if exact_matches:
                return exact_matches
        return matches

    async def _finalize_checkin_reply(
        self,
        *,
        checkin: CheckIn,
        replied_at: datetime,
        raw_reply: str,
        signals: CheckInSignals,
        reconciliation: BlockerReconciliation,
    ) -> DeveloperStatus:
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
        await self._maybe_write_back(updated, final_signals)
        # Send exactly one "Got it" ack per accepted reply. Gated on the
        # record_checkin_reply_once success above, so a durable retry or a
        # duplicate delivery (which returns early) never double-acks (C3).
        await self._send_checkin_ack(checkin=updated, signals=final_signals)
        return status

    async def _send_checkin_ack(
        self,
        *,
        checkin: CheckIn,
        signals: CheckInSignals,
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

    async def _maybe_write_back(self, checkin: CheckIn, signals: CheckInSignals) -> None:
        """Apply gated write-back for a finalized check-in's issue claims.

        The three default-deny gates and audit live in ``WriteBackService``; here we
        only forward the claims. A write-back failure must never lose a recorded
        check-in, so any error is logged (without raw reply content) and swallowed.
        """
        service = self._write_back_service
        if service is None or not signals.issue_updates:
            return
        try:
            results = await service.apply_from_checkin(
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=checkin.correlation_id,
                claims=signals.issue_updates,
            )
        except Exception:  # pragma: no cover - defensive; write-back is best-effort
            _logger.warning(
                "writeback_failed",
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=checkin.correlation_id,
            )
            return
        if results:
            _logger.info(
                "writeback_recorded",
                tenant_id=checkin.tenant_id,
                developer_id=checkin.developer_id,
                correlation_id=checkin.correlation_id,
                outcomes=[
                    {"issue_key": audit.issue_key, "status": audit.status.value}
                    for audit in results
                ],
            )
        proposed = [audit for audit in results if audit.status is WriteBackStatus.PROPOSED]
        if proposed:
            await self._send_consent_prompt(checkin=checkin, proposals=proposed)

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
                        "idempotency_key": f"writeback-consent:{checkin.correlation_id}",
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
        return await self._finalize_checkin_reply(
            checkin=checkin,
            replied_at=user_turns[-1].observed_at,
            raw_reply=raw_reply,
            signals=_signals_with_note(
                signals,
                "Finalized from accumulated replies after clarification timeout.",
            ),
            reconciliation=reconciliation,
        )

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


def _new_correlation_id() -> str:
    return f"checkin-{uuid4().hex}"


def _pending_nudge_message_id(correlation_id: str, nudge_number: int = 1) -> str:
    return f"pending-nudge-{correlation_id}-{nudge_number}"


_ESCALATION_ROLE_LABELS: dict[EscalationTarget, str] = {
    EscalationTarget.SCRUM_MASTER: "scrum master",
    EscalationTarget.MANAGER: "manager",
}


def _compose_escalation_notice(
    *,
    target: EscalationTarget,
    developer_name: str,
    max_chars: int = OUTBOUND_DM_MAX_CHARS,
) -> str:
    """A privacy-safe non-response escalation notice (no raw reply content)."""
    role_label = _ESCALATION_ROLE_LABELS.get(target, "escalation contact")
    notice = (
        f"Heads up: {developer_name} hasn't completed today's check-in yet. "
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
            f"Want me to update {proposal.issue_key} to “{proposal.target_state}” "
            f"in the issue tracker? Reply yes or no."
        )
    else:
        diffs = ", ".join(f"{p.issue_key} → {p.target_state}" for p in proposals)
        prompt = f"Want me to apply these issue-tracker updates: {diffs}? Reply yes or no."
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
) -> str:
    """Deterministic "Got it" ack for a finalized check-in reply.

    A confident parse gets the plain ack. A low-confidence parse restates the
    recorded status (state + first blocker, never the raw reply) and invites a
    correction. Every branch stays within ``max_chars`` with a safe fallback.
    """
    if signals.parser_confident:
        return _cap_outbound_dm_text(_CHECKIN_ACK_PLAIN, max_chars)
    recorded = _recorded_status_phrase(signals)
    text = f"Got it \U0001f44d I recorded this as {recorded} — reply 'fix' if that's wrong."
    if len(text) > max_chars:
        return _cap_outbound_dm_text(_CHECKIN_ACK_LOW_CONFIDENCE_FALLBACK, max_chars)
    return text


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


def _signals_with_note(signals: CheckInSignals, note: str) -> CheckInSignals:
    # replace() preserves every other field (issue_updates, parser_confident, ...).
    return replace(signals, progress_note=f"{signals.progress_note} {note}")


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


def _eta_answered(signals: CheckInSignals) -> bool:
    return signals.eta_answered or signals.eta_change_days is not None


def _missing_required_status_question(
    missing: tuple[Literal["blockers", "eta"], ...],
) -> str:
    if missing == ("blockers", "eta"):
        return "Thanks. Any blockers on this work, and what is your ETA to finish it?"
    if missing == ("blockers",):
        return "Thanks. Any blockers on this work?"
    return "Thanks. What is your ETA to finish it?"


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


def _person_clarification_question(
    mention: CrossPersonMention,
    matches: Iterable[DirectoryUser],
) -> str:
    candidates = tuple(matches)[:5]
    if not candidates:
        return (
            f"I could not find {mention.raw_name} in the directory. "
            "Reply with the person's name or email."
        )
    options = " or ".join(_person_option(user) for user in candidates)
    return f"Did you mean {options}? Reply with the name or email."


def _person_option(user: DirectoryUser) -> str:
    if user.email:
        return f"{user.display_name} ({user.email})"
    return user.display_name


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
