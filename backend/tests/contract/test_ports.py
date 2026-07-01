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
from infra.adapters.chat.fake import FakeChatWebhookMapper
from tests.contract.contracts import (
    assert_calendar_contract,
    assert_chat_contract,
    assert_chat_webhook_mapper_contract,
    assert_ci_contract,
    assert_conversation_repository_contract,
    assert_directory_user_repository_contract,
    assert_issue_tracker_contract,
    assert_rollup_repository_contract,
    assert_status_repository_contract,
    assert_sync_cursor_repository_contract,
    assert_time_series_repository_contract,
    assert_vcs_contract,
)
from tests.contract.fakes import (
    FakeCalendarProvider,
    FakeChatProvider,
    FakeCiProvider,
    FakeConversationRepository,
    FakeDirectoryUserRepository,
    FakeIssueTracker,
    FakeRollupRepository,
    FakeStatusRepository,
    FakeSyncCursorRepository,
    FakeTimeSeriesRepository,
    FakeVcsProvider,
)


async def test_fake_chat_provider_satisfies_contract() -> None:
    await assert_chat_contract(FakeChatProvider())


def test_fake_chat_webhook_mapper_satisfies_contract() -> None:
    assert_chat_webhook_mapper_contract(
        FakeChatWebhookMapper(tenant_id="demo"),
        {"user_id": "U123", "text": "blocked", "message_id": "msg-1"},
    )


async def test_fake_issue_tracker_satisfies_contract() -> None:
    user = UserRef(tenant_id="demo", external_id="U123")
    provider = FakeIssueTracker(
        issues={
            "PO-1": Issue(
                tenant_id="demo",
                key="PO-1",
                title="Graph adapter",
                state=IssueState.IN_PROGRESS,
                assignee=user,
            )
        }
    )
    await assert_issue_tracker_contract(provider)


async def test_fake_vcs_provider_satisfies_contract() -> None:
    user = UserRef(tenant_id="demo", external_id="U123")
    provider = FakeVcsProvider(
        pull_requests=[
            PullRequest(
                tenant_id="demo",
                id="1",
                title="Add graph adapter",
                author=user,
                merged=False,
            )
        ]
    )
    await assert_vcs_contract(provider)


async def test_fake_status_repository_satisfies_contract() -> None:
    await assert_status_repository_contract(FakeStatusRepository())


async def test_fake_time_series_repository_satisfies_contract() -> None:
    await assert_time_series_repository_contract(FakeTimeSeriesRepository())


async def test_fake_rollup_repository_satisfies_contract() -> None:
    await assert_rollup_repository_contract(FakeRollupRepository())


async def test_fake_sync_cursor_repository_satisfies_contract() -> None:
    await assert_sync_cursor_repository_contract(FakeSyncCursorRepository())


async def test_fake_conversation_repository_satisfies_contract() -> None:
    await assert_conversation_repository_contract(FakeConversationRepository())


async def test_fake_ci_provider_satisfies_contract() -> None:
    provider = FakeCiProvider(
        builds=[
            BuildResult(tenant_id="demo", id="build-1", status="failed"),
        ]
    )
    await assert_ci_contract(provider)


async def test_fake_calendar_provider_satisfies_contract() -> None:
    user = UserRef(tenant_id="demo", external_id="U123")
    provider = FakeCalendarProvider(
        events=[
            CalendarEvent(
                tenant_id="demo",
                user=user,
                starts_on=date(2026, 1, 10),
                ends_on=date(2026, 1, 11),
                kind="pto",
            )
        ]
    )
    await assert_calendar_contract(provider)


async def test_fake_directory_user_repository_satisfies_contract() -> None:
    await assert_directory_user_repository_contract(FakeDirectoryUserRepository())
