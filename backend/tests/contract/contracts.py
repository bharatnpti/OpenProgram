from __future__ import annotations

from datetime import UTC, date, datetime

from core.domain.brief import BriefKind, NarrativeBrief
from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.dead_letter import DeadLetter, DeadLetterStatus
from core.domain.directory import DirectoryUser
from core.domain.graph import EntityRef, FactEvent, NodeKind
from core.domain.identity import IdentityLink
from core.domain.inbound import InboundChatEvent
from core.domain.integrations import (
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
    CheckInClarification,
    CheckInCorrelation,
    CheckInNudge,
    CheckInPreference,
    CheckInScheduleRun,
    CheckInSignals,
    DeveloperStatus,
    StatusSource,
)
from core.domain.writeback import WriteBackAudit, WriteBackStatus
from core.ports.calendar import CalendarProvider
from core.ports.chat import ChatProvider, ChatWebhookMapper
from core.ports.directory import DirectoryUserRepository
from core.ports.issue_tracker import IssueTracker
from core.ports.repositories import (
    ConversationRepository,
    DeadLetterRepository,
    IdentityLinkRepository,
    InboundChatEventRepository,
    NarrativeBriefRepository,
    RollupRepository,
    StatusRepository,
    SyncCursorRepository,
    TimeSeriesRepository,
    WriteBackAuditRepository,
    WriteBackConfigRepository,
)
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
    # Write-back is now implemented behind the gated, audited WriteBackService, so
    # the port itself accepts writes. The closed-system-gate no-op is enforced and
    # covered in test_writeback_service.py, not at the raw port level.
    await provider.transition("demo", "PO-1", IssueState.DONE.value)
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
    assert checkin.last_accessed_at is not None

    await repository.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-idle",
            asked_at=asked_at,
            replied_at=replied_at,
            raw_reply="idle raw reply",
            signals=CheckInSignals(progress_note="idle"),
            last_accessed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    assert (
        await repository.purge_checkin_raw_replies_older_than(
            "demo",
            datetime(2026, 1, 2, tzinfo=UTC),
        )
        == 1
    )
    idle_checkin = await repository.checkin_by_correlation("demo", "corr-idle")
    assert idle_checkin is not None
    assert idle_checkin.raw_reply is None
    assert idle_checkin.signals == CheckInSignals(progress_note="idle")

    await repository.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-once",
            asked_at=asked_at,
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    assert await repository.record_checkin_reply_once(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-once",
            asked_at=asked_at,
            replied_at=replied_at,
            raw_reply="first final reply",
            signals=CheckInSignals(progress_note="first"),
        )
    )
    assert not await repository.record_checkin_reply_once(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-once",
            asked_at=asked_at,
            replied_at=replied_at,
            raw_reply="second final reply",
            signals=CheckInSignals(progress_note="second"),
        )
    )
    once_checkin = await repository.checkin_by_correlation("demo", "corr-once")
    assert once_checkin is not None
    assert once_checkin.raw_reply == "first final reply"

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
    assert await repository.unconsumed_checkin_correlations_for_thread(
        "demo",
        "thread-1",
        date(2026, 1, 10),
    ) == [correlation]
    assert (
        await repository.latest_unconsumed_checkin_correlation_for_user(
            "demo",
            "U123",
            date(2026, 1, 10),
        )
        == correlation
    )
    assert await repository.unconsumed_checkin_correlations_for_user(
        "demo",
        "U123",
        date(2026, 1, 10),
    ) == [correlation]
    await repository.consume_checkin_correlation("demo", "corr-1", replied_at)
    consumed = await repository.checkin_correlation_by_id("demo", "corr-1")
    assert consumed is not None
    assert consumed.consumed_at == replied_at
    assert (
        await repository.unconsumed_checkin_correlations_for_user(
            "demo",
            "U123",
            date(2026, 1, 10),
        )
        == []
    )
    assert (
        await repository.unconsumed_checkin_correlations_for_thread(
            "demo",
            "thread-1",
            date(2026, 1, 10),
        )
        == []
    )

    preference = CheckInPreference(tenant_id="demo", developer_id="dev-1")
    await repository.record_checkin_preference(preference)
    assert await repository.checkin_preference_for("demo", "dev-1") == preference
    assert await repository.list_checkin_preferences("demo") == [preference]
    await repository.delete_checkin_preference("demo", "dev-1")
    assert await repository.checkin_preference_for("demo", "dev-1") is None
    assert await repository.list_checkin_preferences("demo") == []

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
    assert await repository.checkin_schedule_run_for_correlation("demo", "corr-1") == schedule_run

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

    claimed_clarification = await repository.record_checkin_clarification(
        CheckInClarification(
            tenant_id="demo",
            correlation_id="corr-1",
            clarification_number=1,
            question="What is still missing?",
        )
    )
    assert claimed_clarification.outbound_message_id is None
    sent_clarification = await repository.record_checkin_clarification(
        CheckInClarification(
            tenant_id="demo",
            correlation_id="corr-1",
            clarification_number=1,
            question="Different question should not overwrite the claim.",
            sent_at=replied_at,
            outbound_message_id="clarify-1",
        )
    )
    assert sent_clarification.question == "What is still missing?"
    assert sent_clarification.outbound_message_id == "clarify-1"
    assert await repository.checkin_clarification_count("demo", "corr-1") == 1

    status = DeveloperStatus(
        tenant_id="demo",
        developer_id="dev-1",
        as_of=date(2026, 1, 10),
        source=StatusSource.CONFIRMED,
        blockers=("dependency",),
        summary="Implementing graph sync; blocked on dependency.",
        eta_change_days=1,
        developer_confirmed=True,
        confirmed_at=replied_at,
    )
    await repository.record_developer_status(status)
    latest = await repository.latest_developer_status("demo", "dev-1", date(2026, 1, 10))
    assert latest == status
    missing = await repository.developers_without_checkin("demo", date(2026, 1, 10))
    assert all(isinstance(developer_id, str) for developer_id in missing)


async def assert_identity_link_repository_contract(
    repository: IdentityLinkRepository,
) -> None:
    assert await repository.get_identity_link("demo", "dev-1") is None
    assert await repository.list_identity_links("demo") == []

    link = IdentityLink(
        tenant_id="demo",
        developer_id="dev-1",
        chat_user_id="U123",
        jira_account_id="acct-1",
        jira_email="dev1@example.com",
        vcs_username="dev1",
    )
    await repository.upsert_identity_link(link)
    assert await repository.get_identity_link("demo", "dev-1") == link
    assert await repository.list_identity_links("demo") == [link]

    remapped = IdentityLink(
        tenant_id="demo",
        developer_id="dev-1",
        jira_account_id="acct-2",
    )
    await repository.upsert_identity_link(remapped)
    assert await repository.get_identity_link("demo", "dev-1") == remapped
    assert await repository.list_identity_links("demo") == [remapped]


async def assert_writeback_config_repository_contract(
    repository: WriteBackConfigRepository,
) -> None:
    assert await repository.get_writeback_enabled("demo") is None
    await repository.set_writeback_enabled("demo", True)
    assert await repository.get_writeback_enabled("demo") is True
    await repository.set_writeback_enabled("demo", False)
    assert await repository.get_writeback_enabled("demo") is False


async def assert_writeback_audit_repository_contract(
    repository: WriteBackAuditRepository,
) -> None:
    assert await repository.list_for_issue("demo", "PO-1") == []
    assert await repository.find_existing("demo", "PO-1", "done", "corr-1") is None

    audit = WriteBackAudit(
        id="wb-1",
        tenant_id="demo",
        developer_id="dev-1",
        issue_key="PO-1",
        correlation_id="corr-1",
        status=WriteBackStatus.APPLIED,
        target_state="done",
        before_state="in_progress",
        after_state="done",
        comment="done via check-in",
        source="checkin",
        created_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
    )
    await repository.record(audit)
    assert await repository.list_for_issue("demo", "PO-1") == [audit]
    assert await repository.find_existing("demo", "PO-1", "done", "corr-1") == audit

    # Idempotent: re-recording the same id does not duplicate.
    await repository.record(audit)
    assert await repository.list_for_issue("demo", "PO-1") == [audit]

    # A different (target_state, correlation_id) tuple is not matched.
    assert await repository.find_existing("demo", "PO-1", "todo", "corr-1") is None
    assert await repository.find_existing("demo", "PO-1", "done", "corr-2") is None

    # Lookup by id is tenant-scoped.
    assert await repository.get_writeback_audit("demo", "wb-1") == audit
    assert await repository.get_writeback_audit("demo", "missing") is None
    assert await repository.get_writeback_audit("other", "wb-1") is None

    # Applied writes are counted and listed newest-first; other statuses excluded.
    proposed = WriteBackAudit(
        id="wb-2",
        tenant_id="demo",
        developer_id="dev-1",
        issue_key="PO-2",
        correlation_id="corr-2",
        status=WriteBackStatus.PROPOSED,
        target_state="done",
        before_state="in_progress",
        after_state="done",
        comment=None,
        source="checkin",
        created_at=datetime(2026, 1, 11, 9, 0, tzinfo=UTC),
    )
    applied_later = WriteBackAudit(
        id="wb-3",
        tenant_id="demo",
        developer_id="dev-1",
        issue_key="PO-3",
        correlation_id="corr-3",
        status=WriteBackStatus.APPLIED,
        target_state="done",
        before_state="in_progress",
        after_state="done",
        comment=None,
        source="checkin",
        created_at=datetime(2026, 1, 12, 9, 0, tzinfo=UTC),
    )
    await repository.record(proposed)
    await repository.record(applied_later)
    assert await repository.count_applied_writebacks("demo") == 2
    assert await repository.count_applied_writebacks("other") == 0
    assert await repository.count_applied_writebacks(
        "demo", datetime(2026, 1, 12, 0, 0, tzinfo=UTC)
    ) == 1
    recent = await repository.list_applied_writebacks("demo", 5)
    assert [entry.id for entry in recent] == ["wb-3", "wb-1"]
    assert await repository.list_applied_writebacks("demo", 1) == [applied_later]


async def assert_directory_user_repository_contract(repository: DirectoryUserRepository) -> None:
    users = [
        DirectoryUser(
            tenant_id="demo",
            external_id="U1001",
            display_name="Asha Rao",
            email="asha@example.com",
            handle="asha",
            avatar_url=None,
            title="Engineering Manager",
            source="slack",
        ),
        DirectoryUser(
            tenant_id="demo",
            external_id="U1002",
            display_name="Liam Chen",
            email="liam@example.com",
            handle="liam",
            avatar_url=None,
            title="Platform Engineer",
            source="slack",
        ),
        DirectoryUser(
            tenant_id="demo",
            external_id="U1003",
            display_name="Mina Patel",
            email="mina@example.com",
            handle="mina",
            avatar_url=None,
            title="Product Owner",
            source="slack",
        ),
    ]
    await repository.upsert_users(users)
    await repository.upsert_users(
        [
            DirectoryUser(
                tenant_id="demo",
                external_id="U1004",
                display_name="Zoya Khan",
                email="zoya@example.com",
                handle="zoya",
                avatar_url=None,
                title="Designer",
                source="slack",
            )
        ]
    )

    assert await repository.count("demo", "a") == 4
    assert [user.external_id for user in await repository.search("demo", "li", limit=10)] == [
        "U1002"
    ]
    assert await repository.get("demo", "U1001") is not None
    assert await repository.deactivate_missing("demo", ["U1001", "U1002"]) == 2
    assert [user.external_id for user in await repository.search("demo", "", limit=10)] == [
        "U1001",
        "U1002",
    ]


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


async def assert_narrative_brief_repository_contract(
    repository: NarrativeBriefRepository,
) -> None:
    assert await repository.latest_briefs("demo") == []
    daily = NarrativeBrief(
        tenant_id="demo",
        kind=BriefKind.DAILY_POD,
        scope_id="pod-1",
        title="Pod 1 daily",
        body="Descriptive rollup of pod-1 facts.",
        generated_at=datetime(2026, 1, 10, 17, 0, tzinfo=UTC),
        sources=("pod:pod-1",),
    )
    later = NarrativeBrief(
        tenant_id="demo",
        kind=BriefKind.EXEC,
        scope_id="",
        title="Exec brief",
        body="Portfolio-wide descriptive rollup.",
        generated_at=datetime(2026, 1, 11, 16, 0, tzinfo=UTC),
        sources=("program:root",),
    )
    await repository.record_brief(daily)
    await repository.record_brief(later)

    newest_first = await repository.latest_briefs("demo")
    assert [brief.generated_at for brief in newest_first] == [
        later.generated_at,
        daily.generated_at,
    ]
    assert await repository.latest_briefs("demo", BriefKind.DAILY_POD) == [daily]
    assert await repository.latest_briefs("demo", limit=1) == [later]
    assert await repository.latest_briefs("other") == []


async def assert_dead_letter_repository_contract(
    repository: DeadLetterRepository,
) -> None:
    assert await repository.list_open_dead_letters("demo") == []
    assert await repository.count_open_dead_letters("demo") == 0
    assert await repository.get_dead_letter("demo", "missing") is None

    first = DeadLetter(
        id="demo:conv-1:1",
        tenant_id="demo",
        kind="inbound_reply",
        conversation_key="demo:conv-1",
        event_ids=("evt-1", "evt-2"),
        reason="grace exceeded",
        attempts=3,
        first_seen_at=datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
        dead_lettered_at=datetime(2026, 1, 10, 13, 0, tzinfo=UTC),
    )
    later = DeadLetter(
        id="demo:conv-2:1",
        tenant_id="demo",
        kind="inbound_reply",
        conversation_key="demo:conv-2",
        event_ids=("evt-3",),
        reason="grace exceeded",
        attempts=1,
        first_seen_at=datetime(2026, 1, 11, 9, 0, tzinfo=UTC),
        dead_lettered_at=datetime(2026, 1, 11, 10, 0, tzinfo=UTC),
    )
    other_tenant = DeadLetter(
        id="other:conv-9:1",
        tenant_id="other",
        kind="inbound_reply",
        conversation_key="other:conv-9",
        event_ids=("evt-9",),
        reason="grace exceeded",
        attempts=1,
        first_seen_at=datetime(2026, 1, 11, 9, 0, tzinfo=UTC),
        dead_lettered_at=datetime(2026, 1, 11, 10, 0, tzinfo=UTC),
    )
    await repository.record_dead_letter(first)
    await repository.record_dead_letter(later)
    await repository.record_dead_letter(other_tenant)

    # record is idempotent by (tenant_id, id): re-recording upserts in place.
    await repository.record_dead_letter(first)

    newest_first = await repository.list_open_dead_letters("demo")
    assert [dl.id for dl in newest_first] == [later.id, first.id]
    assert await repository.count_open_dead_letters("demo") == 2
    assert await repository.list_open_dead_letters("demo", limit=1) == [later]
    assert await repository.list_open_dead_letters("other") == [other_tenant]

    fetched = await repository.get_dead_letter("demo", first.id)
    assert fetched is not None
    assert fetched.event_ids == ("evt-1", "evt-2")

    rearm_time = datetime(2026, 1, 12, 8, 0, tzinfo=UTC)
    rearmed = await repository.mark_dead_letter_rearmed("demo", first.id, rearm_time)
    assert rearmed is not None
    assert rearmed.status == DeadLetterStatus.REARMED
    assert rearmed.rearmed_at == rearm_time
    assert await repository.count_open_dead_letters("demo") == 1
    assert [dl.id for dl in await repository.list_open_dead_letters("demo")] == [later.id]
    assert await repository.mark_dead_letter_rearmed("demo", "missing", rearm_time) is None


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


async def assert_conversation_repository_contract(repository: ConversationRepository) -> None:
    first = ConversationTurn(
        tenant_id="demo",
        developer_id="dev-1",
        conversation_id="conv-1",
        conversation_date=date(2026, 1, 10),
        role=ConversationRole.AGENT,
        content="How is PO-1 going?",
        correlation_id="corr-1",
        chat_message_id="msg-1",
        observed_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
    )
    second = ConversationTurn(
        tenant_id="demo",
        developer_id="dev-1",
        conversation_id="conv-1",
        conversation_date=date(2026, 1, 10),
        role=ConversationRole.USER,
        content="Blocked on dependency.",
        correlation_id="corr-1",
        chat_message_id="msg-2",
        observed_at=datetime(2026, 1, 10, 9, 5, tzinfo=UTC),
    )
    previous = ConversationTurn(
        tenant_id="demo",
        developer_id="dev-1",
        conversation_id="conv-0",
        conversation_date=date(2026, 1, 9),
        role=ConversationRole.USER,
        content="Yesterday's context.",
        correlation_id=None,
        chat_message_id="msg-0",
        observed_at=datetime(2026, 1, 9, 17, 0, tzinfo=UTC),
    )
    other_developer = ConversationTurn(
        tenant_id="demo",
        developer_id="dev-2",
        conversation_id="conv-other",
        conversation_date=date(2026, 1, 10),
        role=ConversationRole.USER,
        content="Different developer.",
        correlation_id=None,
        chat_message_id="msg-other",
        observed_at=datetime(2026, 1, 10, 10, 0, tzinfo=UTC),
    )
    other_tenant_old = ConversationTurn(
        tenant_id="other",
        developer_id="dev-1",
        conversation_id="conv-other-tenant",
        conversation_date=date(2026, 1, 9),
        role=ConversationRole.USER,
        content="Other tenant old context.",
        correlation_id=None,
        chat_message_id="msg-other-tenant",
        observed_at=datetime(2026, 1, 9, 12, 0, tzinfo=UTC),
    )

    await repository.append_turn(second)
    await repository.append_turn(previous)
    await repository.append_turn(other_developer)
    await repository.append_turn(other_tenant_old)
    await repository.append_turn(first)

    day_turns = await repository.list_turns_for_day("demo", "dev-1", date(2026, 1, 10))
    assert [turn.content for turn in day_turns] == [first.content, second.content]
    assert all(turn.last_accessed_at is not None for turn in day_turns)

    recent = await repository.list_recent_turns("demo", "dev-1", limit=2)
    assert [turn.content for turn in recent] == [first.content, second.content]
    assert all(turn.last_accessed_at is not None for turn in recent)

    recent_since = await repository.list_recent_turns(
        "demo",
        "dev-1",
        limit=10,
        since=datetime(2026, 1, 10, 9, 1, tzinfo=UTC),
    )
    assert [turn.content for turn in recent_since] == [second.content]
    assert await repository.list_recent_turns("demo", "dev-1", limit=0) == []
    assert await repository.user_turn_exists("demo", "dev-1", "msg-2") is True
    assert await repository.user_turn_exists("demo", "dev-1", "msg-1") is False
    assert await repository.user_turn_exists("demo", "dev-2", "msg-2") is False
    assert await repository.user_turn_exists("other", "dev-1", "msg-2") is False

    purged = await repository.purge_turns_older_than(
        "demo",
        datetime(2026, 1, 10, 0, 0, tzinfo=UTC),
    )
    assert purged == 1
    assert await repository.list_turns_for_day("demo", "dev-1", date(2026, 1, 9)) == []
    assert [
        turn.content
        for turn in await repository.list_turns_for_day("other", "dev-1", date(2026, 1, 9))
    ] == [other_tenant_old.content]
    assert [
        turn.content
        for turn in await repository.list_turns_for_day("demo", "dev-2", date(2026, 1, 10))
    ] == [other_developer.content]


async def assert_inbound_chat_event_repository_contract(
    repository: InboundChatEventRepository,
) -> None:
    first = InboundChatEvent(
        tenant_id="demo",
        provider="slack",
        event_id="Ev-1",
        conversation_key="demo:thread-1",
        chat_user_ref="U123",
        message_ref="1700000000.000001",
        text="first message",
        correlation_id="corr-1",
        chat_thread_ref="thread-1",
        received_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
    )
    second = InboundChatEvent(
        tenant_id="demo",
        provider="slack",
        event_id="Ev-2",
        conversation_key="demo:thread-1",
        chat_user_ref="U123",
        message_ref="1700000000.000002",
        text="second message",
        correlation_id="corr-1",
        chat_thread_ref="thread-1",
        received_at=datetime(2026, 1, 10, 9, 0, 20, tzinfo=UTC),
    )
    other_tenant = InboundChatEvent(
        tenant_id="other",
        provider="slack",
        event_id="Ev-1",
        conversation_key="other:thread-1",
        chat_user_ref="U999",
        message_ref="1700000000.000003",
        text="other tenant",
        correlation_id="corr-x",
        chat_thread_ref="thread-1",
        received_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
    )

    assert await repository.append(first) is True
    assert await repository.append(second) is True
    # Same (tenant, provider, event_id) is a redelivery -> deduped.
    assert await repository.append(first) is False
    # Same event_id under a different tenant is a distinct row.
    assert await repository.append(other_tenant) is True

    unprocessed = await repository.list_unprocessed_for_conversation("demo", "demo:thread-1")
    assert [event.event_id for event in unprocessed] == ["Ev-1", "Ev-2"]
    assert all(event.id is not None for event in unprocessed)

    processed_at = datetime(2026, 1, 10, 9, 1, tzinfo=UTC)
    await repository.mark_processed(
        "demo",
        [event.id for event in unprocessed if event.id is not None],
        processed_at,
    )
    assert await repository.list_unprocessed_for_conversation("demo", "demo:thread-1") == []

    stuck_before = InboundChatEvent(
        tenant_id="demo",
        provider="slack",
        event_id="Ev-3",
        conversation_key="demo:thread-2",
        chat_user_ref="U456",
        message_ref="1700000000.000004",
        text="stuck message",
        correlation_id="corr-2",
        chat_thread_ref="thread-2",
        received_at=datetime(2026, 1, 10, 8, 0, tzinfo=UTC),
    )
    assert await repository.append(stuck_before) is True
    stuck = await repository.list_stuck("demo", datetime(2026, 1, 10, 8, 30, tzinfo=UTC))
    assert [event.event_id for event in stuck] == ["Ev-3"]

    purged = await repository.purge_processed_older_than(
        "demo",
        datetime(2026, 1, 10, 9, 30, tzinfo=UTC),
    )
    # Only the two processed rows are purged; the unprocessed stuck row survives.
    assert purged == 2
    remaining_stuck = await repository.list_stuck("demo", datetime(2026, 1, 10, 8, 30, tzinfo=UTC))
    assert [event.event_id for event in remaining_stuck] == ["Ev-3"]


async def assert_time_series_repository_contract(repository: TimeSeriesRepository) -> None:
    created_at = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    entity_ref = EntityRef(tenant_id="demo", kind=NodeKind.WORK_ITEM, id="wi-1")
    older_fact = FactEvent(
        tenant_id="demo",
        source="work_item",
        entity_ref=entity_ref,
        payload={"name": "Feature 1", "from_state": "proposed", "to_state": "in_progress"},
        observed_at=created_at,
        correlation_id="corr-1",
        ingested_at=created_at,
    )
    newer_fact = FactEvent(
        tenant_id="demo",
        source="work_item",
        entity_ref=entity_ref,
        payload={"name": "Feature 1", "from_state": "in_progress", "to_state": "done"},
        observed_at=created_at.replace(hour=10),
        correlation_id="corr-2",
        ingested_at=created_at.replace(hour=10),
    )
    other_source_fact = FactEvent(
        tenant_id="demo",
        source="vcs_commit",
        entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.REPO, id="repo-1"),
        payload={"repo": "repo-1", "sha": "abc1234", "message": "Update"},
        observed_at=created_at.replace(hour=11),
        correlation_id="corr-3",
        ingested_at=created_at.replace(hour=11),
    )
    other_tenant_fact = FactEvent(
        tenant_id="other",
        source="work_item",
        entity_ref=entity_ref,
        payload={"name": "Other tenant"},
        observed_at=created_at.replace(hour=12),
        correlation_id="corr-4",
        ingested_at=created_at.replace(hour=12),
    )

    await repository.append_fact(older_fact)
    await repository.append_fact_once(newer_fact)
    await repository.append_fact_once(other_source_fact)
    await repository.append_fact(other_tenant_fact)

    recent = await repository.list_recent_facts(
        "demo", since=created_at, sources=("work_item",), limit=10
    )
    assert recent == [newer_fact, older_fact]
    assert await repository.list_recent_facts("demo", sources=("vcs_commit",), limit=10) == [
        other_source_fact
    ]
    assert await repository.list_recent_facts("demo", limit=0) == []
    assert await repository.list_recent_facts("demo", sources=(), limit=10) == []


async def assert_calendar_contract(provider: CalendarProvider) -> None:
    user = UserRef(tenant_id="demo", external_id="U123")
    events = await provider.list_events(user, date(2026, 1, 1), date(2026, 1, 31))
    assert all(isinstance(event, CalendarEvent) for event in events)
