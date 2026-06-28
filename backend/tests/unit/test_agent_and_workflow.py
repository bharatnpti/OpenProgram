from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import cast

import pytest

from config.settings import Settings
from core.application.agents.status_agent import StatusAgentNode
from core.application.conversation_history import llm_messages_from_turns
from core.application.status_collector import StatusCollector
from core.application.sync_services import SyncRunResult
from core.domain.conversation import ConversationRole, ConversationTurn
from core.domain.integrations import SyncCursor
from core.domain.status import CheckIn, CheckInScheduleRun, DeveloperStatus, StatusSource
from core.domain.workflows import (
    CheckinFanoutInput,
    CheckinScheduleConfig,
    ConversationPurgeInput,
    ConversationPurgeScheduleConfig,
    DeveloperCheckinDispatch,
    HeartbeatInput,
    ScheduleBootstrapResult,
    SyncDispatchInput,
    SyncScheduleConfig,
    record_heartbeat,
)
from infra.adapters.workflows import dbos as dbos_workflows
from infra.adapters.workflows import temporal as temporal_workflows
from infra.adapters.workflows.fake import FakeWorkflowScheduler
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.workflows import (
    checkin_fanout,
    conversation_purge,
    daily_checkin,
    jira_sync,
    nudge,
    schedule,
    worker,
)
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker, FakeLlmProvider


async def test_status_agent_calls_llm_provider() -> None:
    provider = FakeLlmProvider()
    node = StatusAgentNode(provider, model="test-model")
    result = await node(
        {
            "tenant_id": "demo",
            "developer_name": "Asha",
            "context": "API shell is complete; graph tests are blocked.",
            "correlation_id": "corr-1",
        }
    )
    assert result["trace_id"] == "trace-fake"
    assert result["summary"].startswith("summary:")
    request = provider.requests[0]
    assert request.system is not None
    assert request.metadata["purpose"] == "summarize_status"
    assert [(message.role, message.content) for message in request.messages] == [
        (
            "user",
            "Developer: Asha\nContext:\nAPI shell is complete; graph tests are blocked.",
        )
    ]


async def test_status_agent_langgraph_wrapper_calls_llm_provider() -> None:
    node = StatusAgentNode(FakeLlmProvider(), model="test-model")
    result = await node.graph().ainvoke(
        {
            "tenant_id": "demo",
            "developer_name": "Asha",
            "context": "Graph wrapper smoke.",
            "correlation_id": "corr-graph",
        }
    )
    assert result["trace_id"] == "trace-fake"
    assert result["summary"].startswith("summary:")


def test_conversation_history_maps_turn_roles_to_llm_messages() -> None:
    observed_at = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    turns = (
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-1",
            conversation_id="corr-1",
            conversation_date=date(2026, 1, 10),
            role=ConversationRole.SYSTEM,
            content="System context.",
            correlation_id="corr-1",
            chat_message_id="msg-system",
            observed_at=observed_at,
        ),
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-1",
            conversation_id="corr-1",
            conversation_date=date(2026, 1, 10),
            role=ConversationRole.AGENT,
            content="Any blockers?",
            correlation_id="corr-1",
            chat_message_id="msg-agent",
            observed_at=observed_at,
        ),
        ConversationTurn(
            tenant_id="demo",
            developer_id="dev-1",
            conversation_id="corr-1",
            conversation_date=date(2026, 1, 10),
            role=ConversationRole.USER,
            content="No blockers.",
            correlation_id="corr-1",
            chat_message_id="msg-user",
            observed_at=observed_at,
        ),
    )

    messages = llm_messages_from_turns(turns)

    assert [(message.role, message.content) for message in messages] == [
        ("system", "System context."),
        ("assistant", "Any blockers?"),
        ("user", "No blockers."),
    ]


def test_heartbeat_logic_is_retry_safe_shape() -> None:
    recorded_at = datetime(2026, 1, 1, tzinfo=UTC)
    result = record_heartbeat(
        HeartbeatInput(tenant_id="demo", heartbeat_id="heartbeat-1"),
        recorded_at=recorded_at,
    )
    assert result.tenant_id == "demo"
    assert result.heartbeat_id == "heartbeat-1"
    assert result.status == "ok"
    assert result.recorded_at == recorded_at.isoformat()


async def test_fake_workflow_scheduler_returns_deterministic_result() -> None:
    scheduler = FakeWorkflowScheduler(schedule_id="heartbeat-test")

    result = await scheduler.ensure_heartbeat_schedule()
    fanout = await scheduler.ensure_checkin_fanout_schedule(
        CheckinScheduleConfig(schedule_id="checkin-fanout", tenant_id="demo", cron="0 9 * * *")
    )
    purge = await scheduler.ensure_conversation_purge_schedule(
        ConversationPurgeScheduleConfig(
            schedule_id="conversation-purge",
            tenant_id="demo",
            retention_days=30,
            cron="0 3 * * *",
        )
    )
    sync_results = await scheduler.ensure_sync_schedules(
        [
            SyncScheduleConfig(
                schedule_id="jira-sync",
                tenant_id="demo",
                connector="issue",
                scope="project:PO",
                payload={"project_key": "PO"},
                cron="0 * * * *",
            )
        ]
    )
    checkin_workflow_id = await scheduler.dispatch_developer_checkin(
        DeveloperCheckinDispatch(
            tenant_id="demo",
            developer_id="dev-1",
            checkin_date="2026-01-10",
        )
    )
    sync_workflow_id = await scheduler.dispatch_sync(
        SyncDispatchInput(
            tenant_id="demo",
            connector="vcs",
            scope="repo:oneai/program-manager",
            payload={"repo_name": "oneai/program-manager"},
        )
    )

    assert result.schedule_id == "heartbeat-test"
    assert result.status == "ready"
    assert fanout.schedule_id == "checkin-fanout"
    assert fanout.status == "ready"
    assert purge.schedule_id == "conversation-purge"
    assert purge.status == "ready"
    assert [(item.schedule_id, item.status) for item in sync_results] == [("jira-sync", "ready")]
    assert checkin_workflow_id == "fake-checkin-demo-dev-1-2026-01-10"
    assert sync_workflow_id == "fake-sync-vcs-repo-oneai-program-manager"


async def test_checkin_fanout_dispatches_developers_without_checkin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryGraphStore()
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-1",
            as_of=date(2026, 1, 9),
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="yesterday",
        )
    )
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-2",
            as_of=date(2026, 1, 9),
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="yesterday",
        )
    )
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-2",
            correlation_id="corr-dev-2",
            asked_at=datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
            replied_at=datetime(2026, 1, 10, 9, 5, tzinfo=UTC),
            raw_reply=None,
            signals=None,
        )
    )
    registry = _FanoutRegistry(store)
    monkeypatch.setattr(checkin_fanout, "_service_registry", lambda: registry)

    result = await checkin_fanout.dispatch_checkins_for_tenant_activity(
        CheckinFanoutInput(tenant_id="demo", checkin_date="2026-01-10")
    )

    assert result.dispatched == 1
    assert result.workflow_ids == ["dispatch-dev-1-2026-01-10"]
    assert registry.scheduler.inputs == [
        DeveloperCheckinDispatch(
            tenant_id="demo",
            developer_id="dev-1",
            checkin_date="2026-01-10",
        )
    ]
    assert registry.closed is True


def test_schedule_configs_ignore_calendar_read_sync_targets() -> None:
    settings_factory = cast(Callable[..., Settings], Settings)
    settings = settings_factory(
        _env_file=None,
        secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        jira_sync_projects=("PO", "ENG:program-platform", "API:pod-runtime:board-1"),
        github_sync_repos=("oneai/program-manager",),
        calendar_sync_user_ids=("dev-1",),
        calendar_sync_window_days=2,
    )

    checkin_config = schedule.checkin_fanout_config(settings)
    purge_config = schedule.conversation_purge_config(settings)
    sync_configs = schedule.sync_schedule_configs(settings)

    assert checkin_config.schedule_id == "pulseops-checkin-fanout"
    assert purge_config == ConversationPurgeScheduleConfig(
        schedule_id="pulseops-conversation-purge",
        tenant_id="demo",
        retention_days=30,
        cron="0 3 * * *",
    )
    assert [(config.connector, config.scope, config.payload) for config in sync_configs] == [
        ("issue", "project:PO", {"project_key": "PO"}),
        (
            "issue",
            "project:ENG",
            {"project_key": "ENG", "container_id": "program-platform"},
        ),
        (
            "issue",
            "project:API",
            {"project_key": "API", "container_id": "pod-runtime", "board_id": "board-1"},
        ),
        ("vcs", "repo:oneai/program-manager", {"repo_name": "oneai/program-manager"}),
        ("directory", "directory", {}),
    ]


def test_schedule_configs_include_only_directory_sync_when_targets_are_empty() -> None:
    settings_factory = cast(Callable[..., Settings], Settings)
    settings = settings_factory(
        _env_file=None,
        secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        jira_sync_projects=(),
        github_sync_repos=(),
        calendar_sync_user_ids=(),
    )

    sync_configs = schedule.sync_schedule_configs(settings)

    assert [(config.connector, config.scope, config.payload) for config in sync_configs] == [
        ("directory", "directory", {})
    ]


async def test_ensure_workflow_schedules_bootstraps_all_configured_schedules() -> None:
    settings_factory = cast(Callable[..., Settings], Settings)
    settings = settings_factory(
        _env_file=None,
        secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        heartbeat_schedule_id="heartbeat-test",
        jira_sync_projects=("PO",),
        github_sync_repos=("oneai/program-manager",),
        calendar_sync_user_ids=("dev-1",),
    )
    registry = _ScheduleBootstrapRegistry(settings)

    results = await schedule.ensure_workflow_schedules(registry)

    assert registry.scheduler.heartbeat_calls == 1
    assert registry.scheduler.checkin_configs == [schedule.checkin_fanout_config(settings)]
    assert registry.scheduler.purge_configs == [schedule.conversation_purge_config(settings)]
    assert [(config.connector, config.scope) for config in registry.scheduler.sync_configs] == [
        ("issue", "project:PO"),
        ("vcs", "repo:oneai/program-manager"),
        ("directory", "directory"),
    ]
    assert [result.schedule_id for result in results] == [
        "heartbeat-test",
        settings.checkin_fanout_schedule_id,
        settings.conversation_purge_schedule_id,
        *(config.schedule_id for config in registry.scheduler.sync_configs),
    ]


async def test_worker_bootstraps_schedules_before_running_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_factory = cast(Callable[..., Settings], Settings)
    settings = settings_factory(
        _env_file=None,
        secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
    )
    events: list[str] = []
    registry = _WorkerStartupRegistry(settings, events)

    async def ensure_schedules(value: _WorkerStartupRegistry) -> list[ScheduleBootstrapResult]:
        assert value is registry
        events.append("ensure")
        return [ScheduleBootstrapResult(schedule_id="heartbeat-test", status="ready")]

    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "ServiceRegistry", lambda value: registry)
    monkeypatch.setattr(worker, "ensure_workflow_schedules", ensure_schedules)

    await worker.main()

    assert events == ["ensure", "worker", "run", "close"]


def test_temporal_nudge_child_uses_abandon_parent_close_policy() -> None:
    source = temporal_workflows.DailyCheckinWorkflow.run.__code__.co_names
    assert "ParentClosePolicy" in source


async def test_temporal_connect_retries_until_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    from temporalio.client import Client

    attempts: list[str] = []
    expected_client = object()

    async def connect(target: str) -> object:
        attempts.append(target)
        if len(attempts) < 3:
            raise RuntimeError("Temporal is starting")
        return expected_client

    monkeypatch.setattr(Client, "connect", connect)

    client = await temporal_workflows._connect_temporal(
        "temporal:7233",
        attempts=3,
        delay_seconds=0,
    )

    assert client is expected_client
    assert attempts == ["temporal:7233", "temporal:7233", "temporal:7233"]


async def test_temporal_connect_does_not_retry_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from temporalio.client import Client

    attempts: list[str] = []

    async def connect(target: str) -> object:
        attempts.append(target)
        raise ValueError("bad worker configuration")

    monkeypatch.setattr(Client, "connect", connect)

    with pytest.raises(ValueError, match="bad worker configuration"):
        await temporal_workflows._connect_temporal(
            "temporal:7233",
            attempts=3,
            delay_seconds=0,
        )

    assert attempts == ["temporal:7233"]


async def test_dbos_readiness_launches_once_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class Connection:
        async def execute(self, query: str) -> None:
            calls.append(("execute", query))

        async def close(self) -> None:
            calls.append(("close", None))

    async def connect(url: str) -> Connection:
        calls.append(("connect", url))
        return Connection()

    def configure(config: dbos_workflows.DbosRuntimeConfig) -> None:
        calls.append(("configure", config))

    def launch() -> None:
        calls.append(("launch", None))

    def destroy() -> None:
        calls.append(("destroy", None))

    monkeypatch.setattr("infra.adapters.workflows.dbos.psycopg.AsyncConnection.connect", connect)
    monkeypatch.setattr(dbos_workflows, "configure_dbos_runtime", configure)
    monkeypatch.setattr("infra.adapters.workflows.dbos.DBOS.launch", launch)
    monkeypatch.setattr(dbos_workflows, "destroy_dbos_runtime", destroy)

    probe = dbos_workflows.DbosWorkflowReadinessProbe(
        app_name="pulseops-test",
        system_database_url="postgresql://pulseops:pulseops@localhost:5432/pulseops",
    )

    assert await probe.check() is True
    assert await probe.check() is True
    assert calls == [
        ("connect", "postgresql://pulseops:pulseops@localhost:5432/pulseops"),
        ("execute", "SELECT 1"),
        ("close", None),
        (
            "configure",
            dbos_workflows.DbosRuntimeConfig(
                app_name="pulseops-test",
                system_database_url="postgresql://pulseops:pulseops@localhost:5432/pulseops",
            ),
        ),
        ("launch", None),
    ]


async def test_jira_sync_activity_returns_json_native_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _StubRegistry()
    monkeypatch.setattr(jira_sync, "_service_registry", lambda: registry)

    result = await jira_sync.sync_jira_project_activity(
        jira_sync.JiraSyncInput(
            tenant_id="demo",
            project_key="PO",
            board_id="board-1",
            observed_at="2026-01-10T09:00:00+00:00",
        )
    )

    assert result.connector == "issue"
    assert result.scope == "project:PO"
    assert result.items_synced == 1
    assert result.cursor_updated_at == "2026-01-10T09:00:00+00:00"
    assert result.cursor_metadata == {"last_item_count": 1}
    assert registry._service.board_id == "board-1"
    assert registry.closed is True


async def test_conversation_purge_activity_deletes_older_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryGraphStore()
    await store.append_turn(
        _conversation_turn("old", observed_at=datetime(2026, 1, 9, 23, 59, tzinfo=UTC))
    )
    await store.append_turn(
        _conversation_turn(
            "other-tenant-old",
            tenant_id="other",
            observed_at=datetime(2026, 1, 9, 23, 59, tzinfo=UTC),
        )
    )
    await store.append_turn(
        _conversation_turn("kept", observed_at=datetime(2026, 1, 10, 0, 0, tzinfo=UTC))
    )
    registry = _ConversationPurgeRegistry(store)
    monkeypatch.setattr(conversation_purge, "_service_registry", lambda: registry)

    result = await conversation_purge.purge_conversation_turns_activity(
        ConversationPurgeInput(
            tenant_id="demo",
            retention_days=30,
            now="2026-02-09T00:00:00+00:00",
        )
    )

    assert result.cutoff == "2026-01-10T00:00:00+00:00"
    assert result.deleted_count == 1
    assert [turn.content for turn in await store.list_recent_turns("demo", "dev-1", limit=10)] == [
        "kept"
    ]
    assert [turn.content for turn in await store.list_recent_turns("other", "dev-1", limit=10)] == [
        "other-tenant-old"
    ]
    assert registry.closed is True


async def test_daily_checkin_activity_is_idempotent_for_existing_correlation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked_at = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    registry = _ExistingCheckinRegistry(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            asked_at=asked_at,
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    monkeypatch.setattr(daily_checkin, "_service_registry", lambda: registry)

    result = await daily_checkin.start_daily_checkin_activity(
        daily_checkin.DailyCheckinInput(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-1",
            checkin_date="2026-01-09",
        )
    )

    assert result.already_recorded is True
    assert result.asked_at == asked_at.isoformat()
    assert result.reply_wait_seconds == 14400
    assert result.final_reply_wait_seconds == 28800
    assert registry.collector_called is False
    assert registry.closed is True


async def test_daily_checkin_activity_skips_weekends_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _DailyCheckinRegistry()
    monkeypatch.setattr(daily_checkin, "_service_registry", lambda: registry)
    payload = daily_checkin.DailyCheckinInput(
        tenant_id="demo",
        developer_id="dev-1",
        correlation_id="corr-weekend",
        checkin_date="2026-01-10",
    )

    first = await daily_checkin.start_daily_checkin_activity(payload)
    second = await daily_checkin.start_daily_checkin_activity(payload)

    assert first.status == "skipped_weekend"
    assert first.skipped_reason == "check-in preference excludes this weekday"
    assert first.reply_wait_seconds == 14400
    assert first.final_reply_wait_seconds == 28800
    assert second.status == "skipped_weekend"
    assert second.already_recorded is True
    assert len(registry.repository.schedule_runs) == 1
    assert registry.collector_called is False


async def test_daily_checkin_activity_sends_without_calendar_availability_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _DailyCheckinRegistry(collector=_RecordingStatusCollector())
    monkeypatch.setattr(daily_checkin, "_service_registry", lambda: registry)

    result = await daily_checkin.start_daily_checkin_activity(
        daily_checkin.DailyCheckinInput(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-pto",
            checkin_date="2026-01-12",
        )
    )

    assert result.status == "sent"
    assert result.skipped_reason is None
    assert registry.collector_called is True
    assert (
        registry.repository.schedule_runs[
            ("demo", "dev-1", date.fromisoformat("2026-01-12"))
        ].status
        == "sent"
    )


async def test_nudge_activities_send_once_then_close_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryGraphStore()
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-1",
            correlation_id="corr-nudge",
            asked_at=datetime(2026, 1, 12, 9, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    chat = FakeChatProvider()
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=chat,
        llm_provider=FakeLlmProvider(),
        status_repository=store,
        conversation_repository=store,
        model="test-model",
    )
    registry = _NudgeRegistry(store, collector)
    monkeypatch.setattr(nudge, "_service_registry", lambda: registry)
    payload = nudge.NudgeInput(
        tenant_id="demo",
        correlation_id="corr-nudge",
        as_of="2026-01-12",
        chat_external_id="U123",
    )

    first = await nudge.send_checkin_nudge_activity(payload)
    second = await nudge.send_checkin_nudge_activity(payload)
    closed = await nudge.close_checkin_non_response_activity(payload)

    assert payload.reply_wait_seconds == 14400
    assert payload.final_reply_wait_seconds == 28800
    assert first.status == "nudged"
    assert second.status == "already_nudged"
    assert first.nudge_message_id == "msg-U123-1"
    assert second.nudge_message_id == "msg-U123-1"
    assert len(chat.sent) == 1
    assert closed.status == "closed"
    assert closed.terminal_source == "unknown"


class _StubIssueSyncService:
    board_id: str | None = None

    async def sync_project(
        self,
        *,
        tenant_id: str,
        project_key: str,
        container_id: str | None = None,
        board_id: str | None = None,
        observed_at: datetime | None = None,
    ) -> SyncRunResult:
        self.board_id = board_id
        return SyncRunResult(
            connector="issue",
            scope=f"project:{project_key}",
            items_synced=1,
            cursor=SyncCursor(
                value="cursor-1",
                updated_at=observed_at,
                metadata={"last_item_count": 1},
            ),
        )


class _RecordingWorkflowScheduler:
    def __init__(self, heartbeat_schedule_id: str) -> None:
        self.heartbeat_schedule_id = heartbeat_schedule_id
        self.heartbeat_calls = 0
        self.checkin_configs: list[CheckinScheduleConfig] = []
        self.purge_configs: list[ConversationPurgeScheduleConfig] = []
        self.sync_configs: list[SyncScheduleConfig] = []

    async def ensure_heartbeat_schedule(self) -> ScheduleBootstrapResult:
        self.heartbeat_calls += 1
        return ScheduleBootstrapResult(schedule_id=self.heartbeat_schedule_id, status="ready")

    async def ensure_checkin_fanout_schedule(
        self, config: CheckinScheduleConfig
    ) -> ScheduleBootstrapResult:
        self.checkin_configs.append(config)
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="ready")

    async def ensure_conversation_purge_schedule(
        self, config: ConversationPurgeScheduleConfig
    ) -> ScheduleBootstrapResult:
        self.purge_configs.append(config)
        return ScheduleBootstrapResult(schedule_id=config.schedule_id, status="ready")

    async def ensure_sync_schedules(
        self, configs: Sequence[SyncScheduleConfig]
    ) -> list[ScheduleBootstrapResult]:
        self.sync_configs.extend(configs)
        return [
            ScheduleBootstrapResult(schedule_id=config.schedule_id, status="ready")
            for config in configs
        ]


class _ScheduleBootstrapRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.scheduler = _RecordingWorkflowScheduler(settings.resolved_heartbeat_schedule_id)

    def workflow_scheduler(self) -> _RecordingWorkflowScheduler:
        return self.scheduler


class _WorkerStartupRegistry:
    def __init__(self, settings: Settings, events: list[str]) -> None:
        self.settings = settings
        self.events = events

    def workflow_worker(self) -> _OneShotWorkflowWorker:
        self.events.append("worker")
        return _OneShotWorkflowWorker(self.events)

    async def close(self) -> None:
        self.events.append("close")


class _OneShotWorkflowWorker:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def run(self) -> None:
        self.events.append("run")


class _StubRegistry:
    def __init__(self) -> None:
        self.closed = False
        self._service = _StubIssueSyncService()

    def issue_read_sync_service(self) -> _StubIssueSyncService:
        return self._service

    async def close(self) -> None:
        self.closed = True


class _FanoutScheduler:
    def __init__(self) -> None:
        self.inputs: list[DeveloperCheckinDispatch] = []

    async def dispatch_developer_checkin(self, input: DeveloperCheckinDispatch) -> str:
        self.inputs.append(input)
        return f"dispatch-{input.developer_id}-{input.checkin_date}"


class _FanoutRegistry:
    def __init__(self, store: InMemoryGraphStore) -> None:
        self.closed = False
        self.scheduler = _FanoutScheduler()
        self._store = store

    def status_repository(self) -> InMemoryGraphStore:
        return self._store

    def workflow_scheduler(self) -> _FanoutScheduler:
        return self.scheduler

    async def close(self) -> None:
        self.closed = True


class _ExistingCheckinRepository:
    def __init__(self, checkin: CheckIn) -> None:
        self._checkin = checkin
        self.schedule_runs: list[CheckInScheduleRun] = []

    async def checkin_by_correlation(self, tenant_id: str, correlation_id: str) -> CheckIn | None:
        if self._checkin.tenant_id == tenant_id and self._checkin.correlation_id == correlation_id:
            return self._checkin
        return None

    async def checkin_preference_for(self, tenant_id: str, developer_id: str) -> None:
        return None

    async def checkin_schedule_run(
        self, tenant_id: str, developer_id: str, checkin_date: date
    ) -> None:
        return None

    async def record_checkin_schedule_run(self, run: CheckInScheduleRun) -> None:
        self.schedule_runs.append(run)


class _DailyCheckinRepository:
    def __init__(self) -> None:
        self.schedule_runs: dict[tuple[str, str, date], CheckInScheduleRun] = {}

    async def checkin_by_correlation(self, tenant_id: str, correlation_id: str) -> None:
        return None

    async def checkin_preference_for(self, tenant_id: str, developer_id: str) -> None:
        return None

    async def checkin_schedule_run(
        self, tenant_id: str, developer_id: str, checkin_date: date
    ) -> CheckInScheduleRun | None:
        return self.schedule_runs.get((tenant_id, developer_id, checkin_date))

    async def record_checkin_schedule_run(self, run: CheckInScheduleRun) -> None:
        self.schedule_runs[(run.tenant_id, run.developer_id, run.checkin_date)] = run


class _ExplodingStatusCollector:
    async def start_checkin(self, **kwargs: object) -> CheckIn:
        raise AssertionError("start_checkin should not be called for an existing correlation")


class _RecordingStatusCollector:
    async def start_checkin(self, **kwargs: object) -> CheckIn:
        asked_at = kwargs["asked_at"]
        assert isinstance(asked_at, datetime)
        return CheckIn(
            tenant_id=cast(str, kwargs["tenant_id"]),
            developer_id=cast(str, kwargs["developer_id"]),
            correlation_id=cast(str, kwargs["correlation_id"]),
            asked_at=asked_at,
            replied_at=None,
            raw_reply=None,
            signals=None,
        )


class _ExistingCheckinRegistry:
    def __init__(self, checkin: CheckIn) -> None:
        self.closed = False
        self.collector_called = False
        self.settings = _WorkflowSettings()
        self._repository = _ExistingCheckinRepository(checkin)
        self._collector = _ExplodingStatusCollector()

    def status_repository(self) -> _ExistingCheckinRepository:
        return self._repository

    def status_collector(self) -> _ExplodingStatusCollector:
        self.collector_called = True
        return self._collector

    async def close(self) -> None:
        self.closed = True


class _DailyCheckinRegistry:
    def __init__(self, *, collector: object | None = None) -> None:
        self.closed = False
        self.collector_called = False
        self.settings = _WorkflowSettings()
        self.repository = _DailyCheckinRepository()
        self._collector = collector or _ExplodingStatusCollector()

    def status_repository(self) -> _DailyCheckinRepository:
        return self.repository

    def status_collector(self) -> object:
        self.collector_called = True
        return self._collector

    async def close(self) -> None:
        self.closed = True


class _NudgeRegistry:
    def __init__(self, store: InMemoryGraphStore, collector: StatusCollector) -> None:
        self.closed = False
        self._store = store
        self._collector = collector

    def status_repository(self) -> InMemoryGraphStore:
        return self._store

    def status_collector(self) -> StatusCollector:
        return self._collector

    async def close(self) -> None:
        self.closed = True


def _conversation_turn(
    content: str,
    *,
    observed_at: datetime,
    tenant_id: str = "demo",
) -> ConversationTurn:
    return ConversationTurn(
        tenant_id=tenant_id,
        developer_id="dev-1",
        conversation_id="dev-1-2026-01-10",
        conversation_date=observed_at.date(),
        role=ConversationRole.USER,
        content=content,
        correlation_id=None,
        chat_message_id=None,
        observed_at=observed_at,
    )


class _ConversationPurgeRegistry:
    def __init__(self, store: InMemoryGraphStore) -> None:
        self.closed = False
        self._store = store

    def conversation_repository(self) -> InMemoryGraphStore:
        return self._store

    async def close(self) -> None:
        self.closed = True


class _WorkflowSettings:
    tenant_default_timezone = "UTC"
    checkin_reply_wait_seconds = 14400
    checkin_final_reply_wait_seconds = 28800
