from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol, TypedDict, cast
from uuid import uuid4

from langgraph.graph import StateGraph

from core.application.status_parsing import StatusParser
from core.domain.graph import EntityRef, FactEvent, JsonScalar, NodeKind
from core.domain.integrations import Issue, UserRef
from core.domain.llm import LlmRequest
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from core.domain.status import CheckIn, DeveloperStatus, StatusSource
from core.ports.chat import ChatProvider
from core.ports.issue_tracker import IssueTracker
from core.ports.llm import LlmProvider
from core.ports.repositories import StatusRepository, TimeSeriesRepository


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
    trace_id: str
    checkin: CheckIn


class StatusCollectorGraph(Protocol):
    async def ainvoke(self, input: StatusCollectorState) -> StatusCollectorState: ...


@dataclass(frozen=True, kw_only=True)
class NonResponseResult:
    nudge_message_id: str
    stale_status: DeveloperStatus
    inferred_status: DeveloperStatus | None


class StatusCollector:
    def __init__(
        self,
        *,
        issue_tracker: IssueTracker,
        chat_provider: ChatProvider,
        llm_provider: LlmProvider,
        status_repository: StatusRepository,
        model: str,
        time_series_repository: TimeSeriesRepository | None = None,
        parser: StatusParser | None = None,
    ) -> None:
        self._issue_tracker = issue_tracker
        self._chat_provider = chat_provider
        self._llm_provider = llm_provider
        self._status_repository = status_repository
        self._time_series_repository = time_series_repository
        self._model = model
        self._parser = parser or StatusParser(llm_provider, model)
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

    async def handle_reply(self, message: InboundMessage) -> DeveloperStatus:
        checkin = await self._status_repository.checkin_by_correlation(
            message.tenant_id,
            message.correlation_id,
        )
        if checkin is None:
            error = "inbound reply does not match a recorded check-in"
            raise ValueError(error)

        signals = await self._parser.parse_reply(
            tenant_id=message.tenant_id,
            developer_id=checkin.developer_id,
            raw_reply=message.text,
            correlation_id=message.correlation_id,
        )
        updated = CheckIn(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            correlation_id=checkin.correlation_id,
            asked_at=checkin.asked_at,
            replied_at=message.received_at,
            raw_reply=message.text,
            signals=signals,
        )
        await self._status_repository.record_checkin(updated)

        status = DeveloperStatus(
            tenant_id=checkin.tenant_id,
            developer_id=checkin.developer_id,
            as_of=message.received_at.date(),
            source=StatusSource.CONFIRMED,
            blockers=signals.blockers,
            summary=signals.progress_note,
        )
        await self._status_repository.record_developer_status(status)
        return status

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
        response = await self._llm_provider.complete(
            LlmRequest(
                tenant_id=tenant_id,
                prompt=prompt,
                model=self._model,
                correlation_id=correlation_id,
                metadata={
                    "service": "status_collector",
                    "purpose": "compose_nudge",
                    "developer_id": checkin.developer_id,
                    "nudge_number": 1,
                },
            )
        )
        text = response.text.strip() or "Could you share a quick status update when you can?"
        return await self._chat_provider.send_dm(
            ChatUserRef(
                tenant_id=tenant_id,
                external_id=chat_external_id or checkin.developer_id,
                display_name=developer_name,
            ),
            OutboundMessage(
                tenant_id=tenant_id,
                text=text,
                correlation_id=correlation_id,
                metadata={"purpose": "status_nudge", "nudge_number": 1},
            ),
        )

    async def record_non_response(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        as_of: date,
        developer_name: str | None = None,
    ) -> tuple[DeveloperStatus, DeveloperStatus | None]:
        inferred = await self.infer_fallback_status(
            tenant_id=tenant_id,
            developer_id=developer_id,
            as_of=as_of,
            developer_name=developer_name,
        )
        blockers = inferred.blockers if inferred and inferred.blockers else ("no confirmed reply",)
        summary = (
            f"No confirmed check-in after a nudge. Fallback context: {inferred.summary}"
            if inferred is not None
            else "No confirmed check-in after a nudge. Current status is unknown."
        )
        stale = DeveloperStatus(
            tenant_id=tenant_id,
            developer_id=developer_id,
            as_of=as_of,
            source=StatusSource.STALE,
            blockers=blockers,
            summary=summary,
        )
        await self._status_repository.record_developer_status(stale)
        return stale, inferred

    async def nudge_then_mark_stale(
        self,
        *,
        tenant_id: str,
        correlation_id: str,
        as_of: date,
        developer_name: str | None = None,
        chat_external_id: str | None = None,
    ) -> NonResponseResult:
        checkin = await self._status_repository.checkin_by_correlation(
            tenant_id,
            correlation_id,
        )
        if checkin is None:
            error = "cannot close non-response without a recorded check-in"
            raise ValueError(error)

        nudge_message_id = await self.send_nudge(
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            developer_name=developer_name,
            chat_external_id=chat_external_id,
        )
        stale, inferred = await self.record_non_response(
            tenant_id=tenant_id,
            developer_id=checkin.developer_id,
            as_of=as_of,
            developer_name=developer_name,
        )
        return NonResponseResult(
            nudge_message_id=nudge_message_id,
            stale_status=stale,
            inferred_status=inferred,
        )

    async def infer_fallback_status(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        as_of: date,
        developer_name: str | None = None,
    ) -> DeveloperStatus | None:
        latest = await self._status_repository.latest_developer_status(
            tenant_id,
            developer_id,
            as_of,
        )
        if latest is not None and latest.source is not StatusSource.UNKNOWN:
            return DeveloperStatus(
                tenant_id=tenant_id,
                developer_id=developer_id,
                as_of=as_of,
                source=StatusSource.INFERRED,
                blockers=latest.blockers,
                summary=(
                    f"Last known {latest.source.value} status on "
                    f"{latest.as_of.isoformat()}: {latest.summary}"
                ),
            )

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
            blockers=(),
            summary=context,
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
        lines = _issue_context_lines(issues)

        if self._time_series_repository is not None:
            facts = await self._recent_facts(tenant_id, developer_id, issues)
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
        response = await self._llm_provider.complete(
            LlmRequest(
                tenant_id=state["tenant_id"],
                prompt=prompt,
                model=self._model,
                correlation_id=state["correlation_id"],
                metadata={
                    "service": "status_collector",
                    "purpose": "compose_checkin",
                    "developer_id": state["developer_id"],
                },
            )
        )
        text = response.text.strip() or "Could you share progress, blockers, and any ETA changes?"
        return {"dm_text": text, "trace_id": response.trace_id}

    async def _send_dm_node(self, state: StatusCollectorState) -> StatusCollectorState:
        message_id = await self._chat_provider.send_dm(
            ChatUserRef(
                tenant_id=state["tenant_id"],
                external_id=state.get("chat_external_id", state["developer_id"]),
                display_name=state.get("developer_name"),
            ),
            OutboundMessage(
                tenant_id=state["tenant_id"],
                text=state["dm_text"],
                correlation_id=state["correlation_id"],
                metadata={"purpose": "status_checkin"},
            ),
        )
        return {"message_id": message_id}

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
        return {"checkin": checkin}

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
        for ref in refs:
            facts.extend(await self._time_series_repository.list_facts(tenant_id, ref))
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
