from __future__ import annotations

from datetime import date

from core.domain.integrations import (
    BuildResult,
    CalendarEvent,
    Issue,
    IssueState,
    PullRequest,
    UserRef,
)
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from core.ports.calendar import CalendarProvider
from core.ports.chat import ChatProvider
from core.ports.ci import CiProvider
from core.ports.issue_tracker import IssueTracker
from core.ports.vcs import VcsProvider


async def assert_chat_contract(provider: ChatProvider) -> None:
    user = ChatUserRef(tenant_id="demo", external_id="U123", display_name="Asha")
    thread_id = await provider.open_thread(user)
    message_id = await provider.send_dm(
        user,
        OutboundMessage(tenant_id="demo", text="status?", correlation_id="corr-1"),
    )
    assert thread_id
    assert message_id
    reply = await provider.fetch_reply(thread_id)
    assert reply is None or isinstance(reply, InboundMessage)


async def assert_issue_tracker_contract(provider: IssueTracker) -> None:
    user = UserRef(tenant_id="demo", external_id="U123")
    issue = await provider.get_issue("demo", "PO-1")
    assert isinstance(issue, Issue)
    assert await provider.list_active_for(user)
    await provider.transition("demo", "PO-1", IssueState.DONE.value)
    await provider.add_comment("demo", "PO-1", "done")


async def assert_vcs_contract(provider: VcsProvider) -> None:
    user = UserRef(tenant_id="demo", external_id="U123")
    pull_requests = await provider.list_pull_requests("demo", "repo")
    assert all(isinstance(pull_request, PullRequest) for pull_request in pull_requests)
    assert await provider.list_pull_requests_for(user)


async def assert_ci_contract(provider: CiProvider) -> None:
    build = await provider.latest_build("demo", "build-1")
    assert isinstance(build, BuildResult)
    assert await provider.list_recent_failures("demo", "repo")


async def assert_calendar_contract(provider: CalendarProvider) -> None:
    user = UserRef(tenant_id="demo", external_id="U123")
    events = await provider.list_events(user, date(2026, 1, 1), date(2026, 1, 31))
    assert all(isinstance(event, CalendarEvent) for event in events)
