from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from core.domain.integrations import (
    BuildResult,
    CalendarEvent,
    Issue,
    IssueState,
    PullRequest,
    UserRef,
)
from core.domain.llm import LlmRequest, LlmResponse, TokenUsage
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage


@dataclass
class FakeChatProvider:
    sent: list[OutboundMessage] = field(default_factory=list)
    replies: dict[str, InboundMessage] = field(default_factory=dict)

    async def send_dm(self, user: ChatUserRef, message: OutboundMessage) -> str:
        self.sent.append(message)
        return f"msg-{user.external_id}-{len(self.sent)}"

    async def open_thread(self, user: ChatUserRef) -> str:
        return f"thread-{user.external_id}"

    async def fetch_reply(self, thread_id: str) -> InboundMessage | None:
        return self.replies.get(thread_id)


@dataclass
class FakeIssueTracker:
    issues: dict[str, Issue] = field(default_factory=dict)

    async def get_issue(self, tenant_id: str, key: str) -> Issue:
        return self.issues[key]

    async def list_active_for(self, assignee: UserRef) -> list[Issue]:
        return [issue for issue in self.issues.values() if issue.assignee == assignee]

    async def transition(self, tenant_id: str, key: str, to_state: str) -> None:
        issue = self.issues[key]
        self.issues[key] = Issue(
            tenant_id=issue.tenant_id,
            key=issue.key,
            title=issue.title,
            state=IssueState(to_state),
            assignee=issue.assignee,
            metadata=issue.metadata,
        )

    async def add_comment(self, tenant_id: str, key: str, body: str) -> None:
        issue = self.issues[key]
        self.issues[key] = Issue(
            tenant_id=issue.tenant_id,
            key=issue.key,
            title=issue.title,
            state=issue.state,
            assignee=issue.assignee,
            metadata={**issue.metadata, "last_comment": body},
        )


@dataclass
class FakeVcsProvider:
    pull_requests: list[PullRequest] = field(default_factory=list)

    async def list_pull_requests(self, tenant_id: str, repo: str) -> list[PullRequest]:
        return [
            pull_request
            for pull_request in self.pull_requests
            if pull_request.tenant_id == tenant_id
        ]

    async def list_pull_requests_for(self, author: UserRef) -> list[PullRequest]:
        return [
            pull_request for pull_request in self.pull_requests if pull_request.author == author
        ]


@dataclass
class FakeCiProvider:
    builds: list[BuildResult] = field(default_factory=list)

    async def latest_build(self, tenant_id: str, pipeline_id: str) -> BuildResult | None:
        matching = [
            build
            for build in self.builds
            if build.tenant_id == tenant_id and build.id == pipeline_id
        ]
        return matching[-1] if matching else None

    async def list_recent_failures(self, tenant_id: str, repo: str) -> list[BuildResult]:
        return [
            build
            for build in self.builds
            if build.tenant_id == tenant_id and build.status == "failed"
        ]


@dataclass
class FakeCalendarProvider:
    events: list[CalendarEvent] = field(default_factory=list)

    async def list_events(self, user: UserRef, start: date, end: date) -> list[CalendarEvent]:
        return [
            event
            for event in self.events
            if event.user == user and event.starts_on >= start and event.ends_on <= end
        ]


class FakeLlmProvider:
    async def complete(self, request: LlmRequest) -> LlmResponse:
        return LlmResponse(
            tenant_id=request.tenant_id,
            text=f"summary: {request.prompt[:24]}",
            model=request.model,
            usage=TokenUsage(
                prompt_tokens=5,
                completion_tokens=7,
                total_tokens=12,
                cost_usd=0.0,
                latency_ms=1.0,
            ),
            trace_id="trace-fake",
            metadata={"correlation_id": request.correlation_id},
        )
