from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol, TypedDict, cast
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from langgraph.graph import StateGraph
from opentelemetry import trace

from core.application.agents.tool_loop import ToolCallingAgent
from core.application.conversation_history import llm_messages_from_turns
from core.application.status_parsing import ClarificationEvaluator, StatusParser
from core.application.tools.conversation_history import MAX_HISTORY_LIMIT, ConversationHistoryTool
from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.cross_person import CrossPersonRequestResolution, CrossPersonRequestStatus
from core.domain.directory import DirectoryUser
from core.domain.graph import EntityRef, FactEvent, JsonScalar, NodeKind
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
)
from core.ports.chat import ChatProvider
from core.ports.directory import DirectoryUserRepository
from core.ports.issue_tracker import IssueTracker
from core.ports.llm import LlmProvider
from core.ports.repositories import ConversationRepository, StatusRepository, TimeSeriesRepository
from core.ports.tools import AgentTool

RECENT_FACT_LOOKBACK_DAYS = 30
RECENT_CONVERSATION_LOOKBACK = timedelta(hours=24)
RECENT_CONVERSATION_TURN_LIMIT = 20
_logger = structlog.get_logger(__name__)
COMPOSE_CHECKIN_SYSTEM_PROMPT = (
    "Compose a concise daily check-in DM. Use prior conversation turns as context, but do not "
    "quote private history unless it directly helps the ask."
)
COMPOSE_NUDGE_SYSTEM_PROMPT = (
    "Compose a concise follow-up DM for a pending status check-in. Use prior conversation turns "
    "as context and avoid assuming status is healthy without a reply."
)


class StatusCollectorState(TypedDict, total=False):
    tenant_id: str
    developer_id: str
    developer_name: str
    chat_external_id: str
    correlation_id: str
    asked_at: datetime
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
        parser: StatusParser | None = None,
        clarification_evaluator: ClarificationEvaluator | None = None,
        tool_agent: ToolCallingAgent | None = None,
        conversation_retention_days: int = 30,
        checkin_max_clarifications: int = 2,
        tenant_default_timezone: str = "UTC",
    ) -> None:
        self._issue_tracker = issue_tracker
        self._chat_provider = chat_provider
        self._llm_provider = llm_provider
        self._status_repository = status_repository
        self._time_series_repository = time_series_repository
        self._directory_repository = directory_repository
        self._conversation_repository = conversation_repository
        self._model = model
        self._tool_agent = tool_agent
        self._conversation_retention_days = conversation_retention_days
        self._checkin_max_clarifications = max(0, checkin_max_clarifications)
        self._tenant_default_timezone = tenant_default_timezone
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
    ) -> CheckIn:
        state = await self._compiled_graph.ainvoke(
            {
                "tenant_id": tenant_id,
                "developer_id": developer_id,
                "developer_name": developer_name or developer_id,
                "chat_external_id": chat_external_id or developer_id,
                "correlation_id": correlation_id or _new_correlation_id(),
                "asked_at": asked_at or datetime.now(tz=UTC),
            }
        )
        checkin = state.get("checkin")
        if not isinstance(checkin, CheckIn):
            message = "status collector graph did not record a check-in"
            raise RuntimeError(message)
        return checkin

    async def handle_reply(self, message: InboundMessage) -> ReplyOutcome:
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
            raw_reply=message.text,
        )
        trace.get_current_span().set_attribute("pulseops.raw_reply", message.text)

        if checkin.replied_at is not None:
            return ReplyOutcome(
                kind="processed",
                status=await self._confirmed_status_for_duplicate(checkin),
            )
        if await self._user_turn_exists(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            chat_message_id=message.message_id,
        ):
            return ReplyOutcome(kind="ignored")

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

        conversation_turns = await self._recent_conversation_turns(
            tenant_id=message.tenant_id,
            developer_id=checkin.developer_id,
            exclude_chat_message_id=message.message_id,
            reference_at=message.received_at,
        )
        history_tool = self._conversation_history_tool(
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
            tools=(history_tool,),
            prior_blockers=prior_blockers,
        )
        if not decision.is_status_update:
            await self._send_non_status_ack(checkin=checkin, message=message)
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
            return ReplyOutcome(kind="clarifying")

        signals = decision.signals or await self._parser.parse_reply(
            tenant_id=message.tenant_id,
            developer_id=checkin.developer_id,
            raw_reply=message.text,
            correlation_id=message.correlation_id,
            conversation_turns=conversation_turns,
            tools=(history_tool,),
            prior_blockers=prior_blockers,
        )
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
            return ReplyOutcome(kind="clarifying")
        status = await self._finalize_checkin_reply(
            checkin=checkin,
            replied_at=message.received_at,
            raw_reply=message.text,
            signals=signals,
            prior_blockers=prior_blockers,
        )
        return ReplyOutcome(
            kind="processed",
            status=status,
            cross_person_requests=person_resolution.resolutions,
        )

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

        existing_nudge = await self._status_repository.checkin_nudge_for(
            tenant_id,
            correlation_id,
            1,
        )
        if existing_nudge is not None:
            return existing_nudge.outbound_message_id or _pending_nudge_message_id(correlation_id)

        await self._status_repository.record_checkin_nudge(
            CheckInNudge(tenant_id=tenant_id, correlation_id=correlation_id, nudge_number=1)
        )

        context = await self.build_context(
            tenant_id=tenant_id,
            developer_id=checkin.developer_id,
            developer_name=developer_name,
        )
        prompt = (
            "Write one short, friendly follow-up asking for the pending status update. "
            "Do not imply the work is healthy just because there was no reply. "
            f"Developer: {developer_name or checkin.developer_id}. Context: {context}"
        )
        conversation_turns = await self._recent_conversation_turns(
            tenant_id=tenant_id,
            developer_id=checkin.developer_id,
            reference_at=checkin.asked_at,
        )
        history_tool = self._conversation_history_tool(
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
                    "nudge_number": 1,
                },
            ),
            tools=(history_tool,),
        )
        text = response.text.strip() or "Could you share a quick status update when you can?"
        message_id = await self._chat_provider.send_dm(
            ChatUserRef(
                tenant_id=tenant_id,
                external_id=chat_external_id or checkin.developer_id,
                display_name=developer_name,
            ),
            OutboundMessage(
                tenant_id=tenant_id,
                text=text,
                correlation_id=correlation_id,
                metadata={
                    "purpose": "status_nudge",
                    "nudge_number": 1,
                    "idempotency_key": f"nudge:{correlation_id}:1",
                },
            ),
        )
        sent_at = datetime.now(tz=UTC)
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
                nudge_number=1,
                sent_at=sent_at,
                outbound_message_id=message_id,
            )
        )
        return stored.outbound_message_id or message_id

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
            stale = DeveloperStatus(
                tenant_id=tenant_id,
                developer_id=developer_id,
                as_of=as_of,
                source=StatusSource.STALE,
                blockers=prior.blockers or ("no confirmed reply",),
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

    async def build_context(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        developer_name: str | None = None,
    ) -> str:
        issues = await self._issue_tracker.list_active_for(
            UserRef(tenant_id=tenant_id, external_id=developer_id)
        )
        prioritized_issues = _prioritize_issues(issues)
        lines = _issue_context_lines(prioritized_issues)

        if self._time_series_repository is not None:
            facts = await self._recent_facts(tenant_id, developer_id, prioritized_issues)
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
            "Write a concise, conversational direct message asking for today's work status. "
            "Ask for progress, blockers, and ETA changes. Return only the message text.\n\n"
            f"Developer: {state.get('developer_name', state['developer_id'])}\n"
            f"Context:\n{state['context']}"
        )
        history_tool = self._conversation_history_tool(
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
            tools=(history_tool,),
        )
        text = response.text.strip() or "Could you share progress, blockers, and any ETA changes?"
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
                metadata={"purpose": "status_checkin"},
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
        checkin = CheckIn(
            tenant_id=state["tenant_id"],
            developer_id=state["developer_id"],
            correlation_id=state["correlation_id"],
            asked_at=state.get("asked_at", datetime.now(tz=UTC)),
            replied_at=None,
            raw_reply=None,
            signals=None,
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

    async def _confirmed_status_for_duplicate(self, checkin: CheckIn) -> DeveloperStatus:
        status_as_of = await self._status_as_of_for_checkin(
            checkin,
            checkin.replied_at or checkin.asked_at,
        )
        latest = await self._status_repository.latest_developer_status(
            checkin.tenant_id,
            checkin.developer_id,
            status_as_of,
        )
        if latest is not None and latest.source is StatusSource.CONFIRMED:
            return latest
        signals = checkin.signals or CheckInSignals(progress_note="Duplicate confirmed reply.")
        return DeveloperStatus(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            as_of=status_as_of,
            source=StatusSource.CONFIRMED,
            blockers=signals.blockers,
            summary=signals.progress_note,
            eta_change_days=signals.eta_change_days,
        )

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
        prior_blockers: tuple[str, ...] = (),
    ) -> DeveloperStatus:
        final_signals = _signals_with_carried_blockers(
            signals,
            raw_reply=raw_reply,
            prior_blockers=prior_blockers,
        )
        updated = CheckIn(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=checkin.correlation_id,
            asked_at=checkin.asked_at,
            replied_at=replied_at,
            raw_reply=raw_reply,
            signals=final_signals,
        )
        recorded = await self._status_repository.record_checkin_reply_once(updated)
        if not recorded:
            duplicate = await self._status_repository.checkin_by_correlation(
                checkin.tenant_id,
                checkin.correlation_id,
            )
            return await self._confirmed_status_for_duplicate(duplicate or checkin)

        status = DeveloperStatus(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            as_of=await self._status_as_of_for_checkin(checkin, replied_at),
            source=StatusSource.CONFIRMED,
            blockers=final_signals.blockers,
            summary=final_signals.progress_note,
            eta_change_days=final_signals.eta_change_days,
        )
        await self._status_repository.record_developer_status(status)
        await self._status_repository.consume_checkin_correlation(
            checkin.tenant_id,
            checkin.correlation_id,
            replied_at,
        )
        await self._append_checkin_fact(updated, status)
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
        history_tool = self._conversation_history_tool(
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
            tools=(history_tool,),
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
            tools=(history_tool,),
            prior_blockers=prior_blockers,
        )
        return await self._finalize_checkin_reply(
            checkin=checkin,
            replied_at=user_turns[-1].observed_at,
            raw_reply=raw_reply,
            signals=_signals_with_note(
                signals,
                "Finalized from accumulated replies after clarification timeout.",
            ),
            prior_blockers=prior_blockers,
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
    ) -> None:
        if self._time_series_repository is None or checkin.replied_at is None:
            return
        signals = checkin.signals
        payload: dict[str, JsonScalar] = {
            "status_source": status.source.value,
            "blocker_count": len(status.blockers),
            "has_eta_change": signals.eta_change_days is not None if signals else False,
            "eta_change_days": signals.eta_change_days if signals else None,
            "raw_reply": checkin.raw_reply,
        }
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

    async def _prior_open_blockers(self, checkin: CheckIn, at: datetime) -> tuple[str, ...]:
        status = await self._status_repository.latest_developer_status(
            checkin.tenant_id,
            checkin.developer_id,
            await self._status_as_of_for_checkin(checkin, at),
        )
        return _open_blockers_from_status(status)

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
        since = datetime.now(tz=UTC) - timedelta(days=RECENT_FACT_LOOKBACK_DAYS)
        for ref in refs:
            facts.extend(await self._time_series_repository.list_facts(tenant_id, ref, since))
        return sorted(facts, key=lambda fact: fact.observed_at, reverse=True)[:5]

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


def _pending_nudge_message_id(correlation_id: str) -> str:
    return f"pending-nudge-{correlation_id}-1"


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
    return CheckInSignals(
        progress_note=f"{signals.progress_note} {note}",
        blockers=signals.blockers,
        eta_change_days=signals.eta_change_days,
        requests=signals.requests,
    )


def _signals_with_carried_blockers(
    signals: CheckInSignals,
    *,
    raw_reply: str,
    prior_blockers: tuple[str, ...],
) -> CheckInSignals:
    if not prior_blockers or signals.blockers or _explicitly_resolves_blockers(raw_reply):
        return signals
    return CheckInSignals(
        progress_note=(
            f"{signals.progress_note} Prior blockers carried forward until explicitly resolved: "
            f"{', '.join(prior_blockers)}."
        ),
        blockers=prior_blockers,
        eta_change_days=signals.eta_change_days,
        requests=signals.requests,
    )


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
    timezone_name = timezone or tenant_default_timezone
    try:
        return at.astimezone(ZoneInfo(timezone_name)).date()
    except ZoneInfoNotFoundError:
        _logger.warning("invalid_checkin_timezone", timezone=timezone_name)
        return at.astimezone(UTC).date()


def _issue_context_lines(issues: Iterable[Issue]) -> list[str]:
    return [
        f"Active issue {issue.key}: {issue.title} ({issue.state.value})"
        for issue in list(issues)[:5]
    ]


def _fact_context_lines(facts: Iterable[FactEvent]) -> list[str]:
    return [
        f"Recent fact for {fact.entity_ref.kind.value}/{fact.entity_ref.id}: "
        f"{_format_payload(fact.payload)}"
        for fact in facts
    ]


def _format_payload(payload: Mapping[str, JsonScalar]) -> str:
    pairs = [
        f"{key}={value}"
        for key, value in sorted(payload.items())
        if value is not None and isinstance(value, str | int | float | bool)
    ]
    return ", ".join(pairs[:4]) if pairs else "no scalar details"
