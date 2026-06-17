from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from core.domain.errors import ProviderUnavailable
from core.domain.graph import EntityRef, NodeKind
from core.domain.integrations import (
    BuildResult,
    CalendarEvent,
    Commit,
    Issue,
    IssueState,
    Project,
    PullRequest,
    Repo,
    Sprint,
    SyncCursor,
    UserRef,
)
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from core.domain.rollup import NodeStatus, Rag, RollupFactor
from core.domain.status import (
    CheckIn,
    CheckInCorrelation,
    CheckInNudge,
    CheckInPreference,
    CheckInScheduleRun,
    CheckInSignals,
    DeveloperStatus,
    StatusSource,
)
from core.ports.calendar import CalendarProvider
from core.ports.chat import ChatProvider, ChatWebhookMapper
from core.ports.ci import CiProvider
from core.ports.issue_tracker import IssueTracker
from core.ports.repositories import RollupRepository, StatusRepository, SyncCursorRepository
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


def assert_chat_webhook_mapper_contract(
    mapper: ChatWebhookMapper,
    payload: dict[str, object],
) -> None:
    message = mapper.map_webhook(payload, "corr-webhook")
    assert isinstance(message, InboundMessage)
    assert message.tenant_id == "demo"
    assert message.correlation_id == "corr-webhook"
    assert message.message_id
    assert message.thread_id


async def assert_issue_tracker_contract(provider: IssueTracker) -> None:
    user = UserRef(tenant_id="demo", external_id="U123")
    projects = await provider.list_projects("demo")
    assert all(isinstance(project, Project) for project in projects)
    updated_issues = await provider.list_issues_updated_since("demo", "PO", SyncCursor())
    assert all(isinstance(issue, Issue) for issue in updated_issues)
    sprints = await provider.list_sprints("demo", "board-1")
    assert all(isinstance(sprint, Sprint) for sprint in sprints)
    issue = await provider.get_issue("demo", "PO-1")
    assert isinstance(issue, Issue)
    assert await provider.list_active_for(user)
    with pytest.raises(ProviderUnavailable):
        await provider.transition("demo", "PO-1", IssueState.DONE.value)
    with pytest.raises(ProviderUnavailable):
        await provider.add_comment("demo", "PO-1", "done")


async def assert_vcs_contract(provider: VcsProvider) -> None:
    user = UserRef(tenant_id="demo", external_id="U123")
    repos = await provider.list_repos("demo")
    assert all(isinstance(repo, Repo) for repo in repos)
    commits = await provider.list_commits("demo", "repo", SyncCursor())
    assert all(isinstance(commit, Commit) for commit in commits)
    pull_requests = await provider.list_pull_requests("demo", "repo", SyncCursor())
    assert all(isinstance(pull_request, PullRequest) for pull_request in pull_requests)
    assert await provider.list_pull_requests_for(user)


async def assert_status_repository_contract(repository: StatusRepository) -> None:
    asked_at = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    replied_at = datetime(2026, 1, 10, 9, 5, tzinfo=UTC)
    await repository.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=asked_at,
            replied_at=replied_at,
            raw_reply="blocked on dependency",
            signals=CheckInSignals(
                progress_note="Implementing graph sync",
                blockers=("dependency",),
            ),
        )
    )
    checkin = await repository.checkin_by_correlation("demo", "corr-1")
    assert checkin is not None
    assert checkin.developer_id == "dev-1"
    assert checkin.raw_reply == "blocked on dependency"

    correlation = CheckInCorrelation(
        tenant_id="demo",
        developer_id="dev-1",
        correlation_id="corr-1",
        chat_user_ref="U123",
        chat_thread_ref="thread-1",
        outbound_message_id="msg-1",
        asked_at=asked_at,
    )
    await repository.record_checkin_correlation(correlation)
    assert await repository.checkin_correlation_by_id("demo", "corr-1") == correlation
    assert (
        await repository.latest_checkin_correlation_for_thread(
            "demo",
            "thread-1",
            date(2026, 1, 10),
        )
        == correlation
    )
    assert (
        await repository.latest_unconsumed_checkin_correlation_for_user(
            "demo",
            "U123",
            date(2026, 1, 10),
        )
        == correlation
    )
    await repository.consume_checkin_correlation("demo", "corr-1", replied_at)
    consumed = await repository.checkin_correlation_by_id("demo", "corr-1")
    assert consumed is not None
    assert consumed.consumed_at == replied_at

    preference = CheckInPreference(tenant_id="demo", developer_id="dev-1")
    await repository.record_checkin_preference(preference)
    assert await repository.checkin_preference_for("demo", "dev-1") == preference

    schedule_run = CheckInScheduleRun(
        tenant_id="demo",
        developer_id="dev-1",
        checkin_date=date(2026, 1, 10),
        correlation_id="corr-1",
        status="sent",
        scheduled_at=asked_at,
    )
    await repository.record_checkin_schedule_run(schedule_run)
    assert await repository.checkin_schedule_run("demo", "dev-1", date(2026, 1, 10)) == schedule_run

    claimed_nudge = await repository.record_checkin_nudge(
        CheckInNudge(tenant_id="demo", correlation_id="corr-1", nudge_number=1)
    )
    assert claimed_nudge.outbound_message_id is None
    sent_nudge = await repository.record_checkin_nudge(
        CheckInNudge(
            tenant_id="demo",
            correlation_id="corr-1",
            nudge_number=1,
            sent_at=replied_at,
            outbound_message_id="nudge-1",
        )
    )
    assert sent_nudge.outbound_message_id == "nudge-1"
    assert await repository.checkin_nudge_for("demo", "corr-1", 1) == sent_nudge

    status = DeveloperStatus(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=date(2026, 1, 10),
        source=StatusSource.CONFIRMED,
        blockers=("dependency",),
        summary="Implementing graph sync; blocked on dependency.",
    )
    await repository.record_developer_status(status)
    latest = await repository.latest_developer_status("demo", "dev-1", date(2026, 1, 10))
    assert latest == status
    missing = await repository.developers_without_checkin("demo", date(2026, 1, 10))
    assert all(isinstance(developer_id, str) for developer_id in missing)


async def assert_rollup_repository_contract(repository: RollupRepository) -> None:
    as_of = date(2026, 1, 10)
    entity_ref = EntityRef(tenant_id="demo", kind=NodeKind.POD, id="pod-1")
    source_ref = EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="PO-1")
    status = NodeStatus(
        entity_ref=entity_ref,
        rag=Rag.AMBER,
        source=StatusSource.CONFIRMED,
        factors=(
            RollupFactor(
                description="One blocker on a critical-path task",
                contributes=Rag.AMBER,
                source_ref=source_ref,
            ),
        ),
        as_of=as_of,
    )
    await repository.record_node_status(status)
    latest = await repository.latest_node_status("demo", entity_ref, as_of)
    assert latest == status
    statuses = await repository.list_node_statuses("demo", as_of)
    assert status in statuses


async def assert_sync_cursor_repository_contract(repository: SyncCursorRepository) -> None:
    missing = await repository.get_cursor("demo", "jira", "project:PO")
    assert missing == SyncCursor()

    updated_at = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    cursor = SyncCursor(
        value="cursor-1",
        updated_at=updated_at,
        metadata={"page": 2, "has_more": False},
    )
    await repository.record_cursor("demo", "jira", "project:PO", cursor)
    assert await repository.get_cursor("demo", "jira", "project:PO") == cursor


async def assert_ci_contract(provider: CiProvider) -> None:
    build = await provider.latest_build("demo", "build-1")
    assert isinstance(build, BuildResult)
    assert await provider.list_recent_failures("demo", "repo")


async def assert_calendar_contract(provider: CalendarProvider) -> None:
    user = UserRef(tenant_id="demo", external_id="U123")
    events = await provider.list_events(user, date(2026, 1, 1), date(2026, 1, 31))
    assert all(isinstance(event, CalendarEvent) for event in events)
