"""The Jira-observed day: close-out v2, replies without a status, and the day's fill.

A silent day is inferred from what moved in the tracker and Git since the
person's last answer: Done and moved work counts, and no issue need be under
way. Someone the check-in never reached gets their day filled from the same
digest, once, on the reconcile tick. An inferred day is never green.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import cast

import pytest

from config.settings import Settings
from core.application.availability import Availability
from core.application.fill_day import FillDayPass, FillDaySettingsService
from core.application.observed_day_service import ObservedDayReader
from core.application.rollup_service import _developer_factors
from core.application.status_collector import StatusCollector
from core.application.status_parsing import StatusParser
from core.application.status_summaries import (
    NOT_ASKED_LEAD,
    NOT_REACHED_LEAD,
    inferred_basis,
    observed_evidence,
    stale_summary,
)
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    JsonScalar,
    NodeKind,
    Task,
)
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.observed_day import FillDaySetting
from core.domain.rollup import Rag
from core.domain.status import CheckIn, CheckInScheduleRun, DeveloperStatus, StatusSource
from infra.persistence.in_memory_fill_day import InMemoryFillDayRepository
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry
from infra.workflows import checkin_fill, schedule
from infra.workflows.checkin_fill import CheckinFillInput, apply_checkin_fill_schedule
from infra.workflows.dispatch import sync_dispatch_for_schedule, sync_workflow_input
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_agent_and_workflow import _RecordingWorkflowScheduler
from tests.unit.test_status_collector import SequenceLlmProvider

NOW = datetime.now(tz=UTC)
TODAY = NOW.date()


def _change(
    key: str,
    field: str,
    before: str | None,
    after: str | None,
    *,
    hours_ago: float = 0,
    at: datetime | None = None,
    author: str | None = "dev-kai",
) -> FactEvent:
    at = at or NOW - timedelta(hours=hours_ago)
    payload: dict[str, JsonScalar] = {"key": key, "field": field, "from": before, "to": after}
    if author:
        payload["author_member_id"] = author
    return FactEvent(
        tenant_id="demo",
        source="issue_change",
        entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.TASK, id=key),
        payload=payload,
        observed_at=at,
        correlation_id=f"issue_change:demo:{key}:{at.isoformat()}:{field}",
    )


async def _team(store: InMemoryGraphStore, *keys: str) -> None:
    await store.upsert_node(Developer(tenant_id="demo", id="dev-kai", name="Kai"))
    await store.upsert_node(Developer(tenant_id="demo", id="dev-ira", name="Ira"))
    for key in keys:
        await store.upsert_node(
            Task(tenant_id="demo", id=key, name=f"Made-up {key}", metadata={"key": key})
        )
        await store.add_edge(
            GraphEdge(
                tenant_id="demo",
                from_node_id="dev-kai",
                to_node_id=key,
                kind=EdgeKind.ASSIGNED_TO,
            )
        )


def _collector(store: InMemoryGraphStore, *issues: Issue) -> StatusCollector:
    return StatusCollector(
        issue_tracker=FakeIssueTracker(issues={issue.key: issue for issue in issues}),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(texts=[]),
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        graph_repository=store,
        model="test-model",
        parser=StatusParser(SequenceLlmProvider(texts=[]), model="test-model"),
        issue_tracker_name="Jira",
        vcs_name="GitLab",
        observed_day_reader=ObservedDayReader(
            graph_repository=store, time_series_repository=store, status_repository=store
        ),
    )


async def test_a_silent_member_is_inferred_from_what_moved_and_stays_amber() -> None:
    store = InMemoryGraphStore()
    await _team(store, "CHK-4", "IDP-3")
    await store.append_fact(_change("CHK-4", "status", "In Progress", "In Review", hours_ago=3))
    await store.append_fact(
        _change("IDP-3", "due_date", "2026-10-10", "2026-10-14", hours_ago=2, author="dev-ira")
    )
    await store.append_fact(
        FactEvent(
            tenant_id="demo",
            source="vcs_pull_request",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-kai"),
            payload={
                "repo": "demo/storefront",
                "id": "12",
                "title": "made-up",
                "source_branch": "CHK-4-intent",
                "web_url": "https://git.example.test/demo/storefront/-/merge_requests/12",
                "opened_at": (NOW - timedelta(hours=1)).isoformat(),
            },
            observed_at=NOW - timedelta(hours=1),
            correlation_id="vcs:pull_request:12",
        )
    )

    status = await _collector(store).record_non_response(
        tenant_id="demo", developer_id="dev-kai", as_of=TODAY
    )

    assert status.source is StatusSource.INFERRED
    assert status.summary == (
        "No confirmed check-in after a nudge. Inferred from Jira and GitLab: "
        "CHK-4 moved to In Review (in development) and merge request !12 opened; "
        "IDP-3 due date moved Oct 10 → Oct 14."
    )
    assert status.blockers == ("no confirmed reply",)
    assert observed_evidence(status.summary) == (
        "CHK-4 moved to In Review (in development) and merge request !12 opened",
        "IDP-3 due date moved Oct 10 → Oct 14",
    )
    # Silence is never green: the rollup reads an inferred day amber.
    kai = Developer(tenant_id="demo", id="dev-kai", name="Kai")
    rag, factors = _developer_factors(kai, status, ())
    assert rag is Rag.AMBER
    # Its reason names the evidence, counted by task.
    assert [factor.description for factor in factors] == [
        "Inferred from Jira and GitLab: 2 tasks moved."
    ]


async def test_a_member_whose_only_issue_moved_to_done_is_inferred_not_no_active_work() -> None:
    store = InMemoryGraphStore()
    await _team(store, "CHK-9")
    await store.append_fact(_change("CHK-9", "status", "In Review", "Done", hours_ago=2))
    await store.append_fact(_change("CHK-9", "resolution", None, "Done", hours_ago=2))
    # The tracker's live list of open work is empty: before, "no active work".
    collector = _collector(store)

    status = await collector.record_non_response(
        tenant_id="demo", developer_id="dev-kai", as_of=TODAY
    )

    assert status.source is StatusSource.INFERRED
    assert status.summary == (
        "No confirmed check-in after a nudge. Inferred from Jira: "
        "CHK-9 moved to Done (in development → production)."
    )


async def test_nothing_moved_keeps_the_earlier_inference_from_open_work() -> None:
    store = InMemoryGraphStore()
    await _team(store, "IDP-6")
    # A label change is not movement; the open issue still is evidence, as before.
    await store.append_fact(_change("IDP-6", "labels", None, "x", hours_ago=1))
    issue = Issue(
        tenant_id="demo",
        key="IDP-6",
        title="Metadata refresh",
        state=IssueState.IN_PROGRESS,
        assignee=UserRef(tenant_id="demo", external_id="dev-kai"),
    )

    status = await _collector(store, issue).record_non_response(
        tenant_id="demo", developer_id="dev-kai", as_of=TODAY
    )

    assert status.source is StatusSource.INFERRED
    assert status.summary == (
        "No confirmed check-in after a nudge. Inferred from 1 active issue: IDP-6 Metadata refresh."
    )


async def test_a_change_before_the_last_answer_is_not_evidence() -> None:
    store = InMemoryGraphStore()
    await _team(store, "CHK-4")
    await store.append_fact(_change("CHK-4", "status", "In Progress", "In Review", hours_ago=3))
    await store.append_fact(
        FactEvent(
            tenant_id="demo",
            source="checkin",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="dev-kai"),
            payload={"status_source": "confirmed", "blocker_count": 0},
            observed_at=NOW - timedelta(hours=1),
            correlation_id="checkin:kai",
        )
    )

    status = await _collector(store).record_non_response(
        tenant_id="demo", developer_id="dev-kai", as_of=TODAY
    )

    assert "CHK-4 moved" not in status.summary


async def test_a_reply_without_a_status_gains_what_moved_in_jira() -> None:
    store = InMemoryGraphStore()
    await _team(store, "CHK-4")
    await store.append_fact(_change("CHK-4", "status", "In Progress", "In Review", hours_ago=3))
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-kai",
            correlation_id="corr-kai",
            asked_at=NOW - timedelta(hours=2),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    collector = _collector(store)
    await collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="dev-kai"),
            text="no updates today",
            thread_id="thread-kai",
            message_id="msg-1",
            correlation_id="corr-kai",
            received_at=NOW - timedelta(hours=1),
        )
    )

    closed = await collector.record_non_response(
        tenant_id="demo", developer_id="dev-kai", as_of=TODAY, correlation_id="corr-kai"
    )

    assert closed.source is StatusSource.INFERRED
    assert closed.summary == (
        "Replied without a status update. From Jira: CHK-4 moved to In Review (in development)."
    )
    assert inferred_basis(closed.summary) == "Jira: CHK-4 moved to In Review (in development)"


async def test_a_reply_without_a_status_and_nothing_moved_stays_unknown() -> None:
    store = InMemoryGraphStore()
    await _team(store, "CHK-4")
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id="dev-kai",
            correlation_id="corr-kai",
            asked_at=NOW - timedelta(hours=2),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    collector = _collector(store)
    await collector.handle_reply(
        InboundMessage(
            tenant_id="demo",
            user=ChatUserRef(tenant_id="demo", external_id="dev-kai"),
            text="thanks, nothing new from me",
            thread_id="thread-kai",
            message_id="msg-1",
            correlation_id="corr-kai",
            received_at=NOW - timedelta(hours=1),
        )
    )

    closed = await collector.record_non_response(
        tenant_id="demo", developer_id="dev-kai", as_of=TODAY, correlation_id="corr-kai"
    )

    assert closed.source is StatusSource.UNKNOWN
    assert closed.summary == "Replied without a status update. Current status is unknown."


# --- the fill pass ------------------------------------------------------------


class _Calendar:
    def __init__(self, away: set[str] | None = None) -> None:
        self.away = away or set()

    async def availability_for(
        self, user: UserRef, as_of: date, *, default_timezone: str = "UTC"
    ) -> Availability:
        return Availability(
            user=user,
            as_of=as_of,
            available=user.external_id not in self.away,
            timezone=default_timezone,
            blocking_events=(),
        )


def _fill_pass(
    store: InMemoryGraphStore,
    *,
    settings: FillDaySettingsService | None = None,
    sends_on: bool = False,
    away: set[str] | None = None,
) -> FillDayPass:
    collector = _collector(store)
    return FillDayPass(
        status_repository=store,
        graph_repository=store,
        filler=collector,
        reader=ObservedDayReader(
            graph_repository=store, time_series_repository=store, status_repository=store
        ),
        settings=settings or FillDaySettingsService(InMemoryFillDayRepository()),
        availability=_Calendar(away),  # type: ignore[arg-type]
        sends_on=sends_on,
    )


def _weekday() -> date:
    """The latest weekday up to today, so the default Mon-Fri check-in days hold it."""
    day = TODAY
    while day.weekday() > 4:
        day -= timedelta(days=1)
    return day


def _after_fill_time(day: date) -> datetime:
    # The default check-in is 09:30 UTC with a 4 h nudge wait: filled from 13:30.
    return datetime.combine(day, time(14, 0), tzinfo=UTC)


async def test_fill_day_fills_a_member_who_was_not_asked_once() -> None:
    store = InMemoryGraphStore()
    await _team(store, "CHK-4")
    day = _weekday()
    now = _after_fill_time(day)
    await store.append_fact(
        _change("CHK-4", "status", "In Progress", "In Review", at=now - timedelta(hours=2))
    )
    fill = _fill_pass(store)

    first = await fill.run("demo", now)
    second = await fill.run("demo", now + timedelta(minutes=15))

    kai = [item for item in first if item.developer_id == "dev-kai" and item.as_of == day]
    assert len(kai) == 1
    assert kai[0].as_of == day
    assert kai[0].status.source is StatusSource.INFERRED
    assert kai[0].status.summary == (
        "Not asked today. From Jira: CHK-4 moved to In Review (in development)."
    )
    # Ira had nothing that moved: her day is filled unknown, still not green.
    ira = [item for item in first if item.developer_id == "dev-ira" and item.as_of == day]
    assert [item.status.source for item in ira] == [StatusSource.UNKNOWN]
    assert ira[0].status.summary == f"{NOT_ASKED_LEAD} Current status is unknown."
    # Idempotent per member and day.
    assert [item for item in second if item.as_of == day] == []
    stored = await store.latest_developer_status("demo", "dev-kai", day)
    assert stored == kai[0].status


async def test_fill_day_says_not_reached_while_the_send_is_on() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    day = _weekday()

    filled = await _fill_pass(store, sends_on=True).run("demo", _after_fill_time(day))

    assert {item.status.summary for item in filled if item.as_of == day} == {
        f"{NOT_REACHED_LEAD} Current status is unknown."
    }


async def test_fill_day_leaves_alone_the_asked_the_answered_the_away_and_the_early() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    day = _weekday()
    # Kai was asked: his own check-in closes his day.
    await store.record_checkin_schedule_run(
        CheckInScheduleRun(
            tenant_id="demo",
            developer_id="dev-kai",
            checkin_date=day,
            correlation_id="checkin-kai",
            status="sent",
            scheduled_at=datetime.combine(day, time(9, 30), tzinfo=UTC),
        )
    )

    early = await _fill_pass(store).run("demo", datetime.combine(day, time(11, 0), tzinfo=UTC))
    away = await _fill_pass(store, away={"dev-ira"}).run("demo", _after_fill_time(day))

    assert [item for item in early if item.as_of == day] == []
    assert [item for item in away if item.as_of == day] == []


async def test_fill_day_keeps_a_status_given_that_day_and_names_an_earlier_one() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    day = _weekday()
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-kai",
            as_of=day,
            source=StatusSource.CONFIRMED,
            blockers=(),
            summary="Wired the intent.",
        )
    )
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-ira",
            as_of=day - timedelta(days=7),
            source=StatusSource.PARTIAL,
            blockers=(),
            summary="Started the spike.",
        )
    )

    filled = await _fill_pass(store).run("demo", _after_fill_time(day))

    assert {item.developer_id for item in filled if item.as_of == day} == {"dev-ira"}
    ira = next(item for item in filled if item.as_of == day)
    assert ira.status.source is StatusSource.STALE
    assert ira.status.summary.startswith(f"{NOT_ASKED_LEAD} Last known partial status on ")
    assert (await store.latest_developer_status("demo", "dev-kai", day)) is not None


async def test_fill_day_is_off_when_the_tenant_switches_it_off() -> None:
    store = InMemoryGraphStore()
    await _team(store)
    settings = FillDaySettingsService(InMemoryFillDayRepository())
    assert (await settings.read("demo"))[0].enabled is True
    await settings.set("demo", False, actor="U-admin")

    filled = await _fill_pass(store, settings=settings).run("demo", _after_fill_time(_weekday()))

    assert filled == ()
    setting, saved = await settings.read("demo")
    assert (setting.enabled, saved, setting.updated_by) == (False, True, "U-admin")


def test_the_rollup_reason_counts_moved_and_quiet_tasks_and_keeps_the_old_one_else() -> None:
    kai = Developer(tenant_id="demo", id="dev-kai", name="Kai")

    def reason(summary: str) -> str:
        status = DeveloperStatus(
            tenant_id="demo",
            developer_id="dev-kai",
            as_of=TODAY,
            source=StatusSource.INFERRED,
            blockers=("no confirmed reply",),
            summary=summary,
        )
        rag, factors = _developer_factors(kai, status, ())
        assert rag is Rag.AMBER
        return factors[0].description

    assert reason(
        "No confirmed check-in after a nudge. Inferred from Jira: CHK-4 moved to In Review "
        "(in development); CHK-7 resolved; no movement on CHK-9 for 8 days."
    ) == ("Inferred from Jira: 2 tasks moved, 1 without movement for 8 days.")
    assert reason(
        "Not asked today. From Jira: CHK-1 moved to Done (in development → production); "
        "2 more tasks moved; worked on IDP-2; no movement on CHK-18 for 20 days; "
        "no movement on CHK-3 for 7 days; 1 more task without movement."
    ) == ("Inferred from Jira: 3 tasks moved, 3 without movement for up to 20 days.")
    # The earlier rule's inference, and one with only other people's issues, keep the old words.
    assert reason(
        "No confirmed check-in after a nudge. Inferred from 1 active issue: IDP-6 Metadata refresh."
    ) == ("Status is inferred and needs confirmation.")
    assert reason("Replied without a status update. From Jira: worked on IDP-2.") == (
        "Status is inferred and needs confirmation."
    )


def test_stale_summary_takes_the_fill_lead() -> None:
    assert stale_summary(None, lead=NOT_ASKED_LEAD) == (
        "Not asked today. No confirmed or inferred status in the last 90 days."
    )


# --- the fill's own schedule: the send's switches never decide it --------


def _settings(**overrides: object) -> Settings:
    factory = cast(Callable[..., Settings], Settings)
    return factory(
        _env_file=None,
        secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        heartbeat_schedule_id="heartbeat-test",
        **overrides,
    )


class _BootRegistry:
    """What ensure_workflow_schedules reads, with the registry's own switch wiring."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.scheduler = _RecordingWorkflowScheduler(settings.resolved_heartbeat_schedule_id)
        self.repository = InMemoryFillDayRepository()

        async def schedule(tenant_id: str, enabled: bool) -> None:
            await apply_checkin_fill_schedule(self.scheduler, settings, tenant_on=enabled)

        self.fill_day = FillDaySettingsService(self.repository, on_change=schedule)

    def workflow_scheduler(self) -> _RecordingWorkflowScheduler:
        return self.scheduler

    def fill_day_settings_service(self) -> FillDaySettingsService:
        return self.fill_day


def _fill_configs(scheduler: _RecordingWorkflowScheduler) -> list[str]:
    return [c.schedule_id for c in scheduler.sync_configs if c.connector == "checkin_fill"]


async def test_send_off_fill_on_keeps_only_the_fill_schedule_and_fills_not_asked() -> None:
    settings = _settings(checkin_fanout_enabled=False)
    registry = _BootRegistry(settings)

    await schedule.ensure_workflow_schedules(registry)  # type: ignore[arg-type]

    # The send's off switch removes exactly what it removed before.
    assert settings.checkin_fanout_schedule_id in registry.scheduler.removed
    assert settings.checkin_reconcile_schedule_id in registry.scheduler.removed
    assert registry.scheduler.checkin_configs == []
    assert registry.scheduler.checkin_reconcile_configs == []
    assert _fill_configs(registry.scheduler) == [settings.checkin_fill_schedule_id]
    assert settings.checkin_fill_schedule_id not in registry.scheduler.removed
    fill = schedule.checkin_fill_schedule_config(settings)
    assert (fill.cron, fill.connector) == ("*/15 * * * *", "checkin_fill")

    # Nobody was asked: everyone due gets "Not asked today.", once, never green.
    store = InMemoryGraphStore()
    await _team(store, "CHK-4")
    day = _weekday()
    now = _after_fill_time(day)
    await store.append_fact(
        _change("CHK-4", "status", "In Progress", "In Review", at=now - timedelta(hours=2))
    )
    run = _fill_pass(store, sends_on=settings.checkin_fanout_enabled)
    first = [item for item in await run.run("demo", now) if item.as_of == day]
    again = [
        item for item in await run.run("demo", now + timedelta(minutes=15)) if item.as_of == day
    ]
    assert {item.developer_id: item.status.summary for item in first} == {
        "dev-kai": "Not asked today. From Jira: CHK-4 moved to In Review (in development).",
        "dev-ira": "Not asked today. Current status is unknown.",
    }
    assert all(item.status.source is not StatusSource.CONFIRMED for item in first)
    assert again == []


async def test_send_off_fill_off_schedules_neither_and_fills_nobody() -> None:
    settings = _settings(checkin_fanout_enabled=False)
    registry = _BootRegistry(settings)
    await registry.repository.save_fill_day(FillDaySetting(tenant_id="demo", enabled=False))

    await schedule.ensure_workflow_schedules(registry)  # type: ignore[arg-type]

    assert {
        settings.checkin_fanout_schedule_id,
        settings.checkin_reconcile_schedule_id,
        settings.checkin_fill_schedule_id,
    } <= set(registry.scheduler.removed)
    assert _fill_configs(registry.scheduler) == []
    store = InMemoryGraphStore()
    await _team(store)
    off = FillDaySettingsService(registry.repository)
    filled = await _fill_pass(store, settings=off).run("demo", _after_fill_time(_weekday()))
    assert filled == ()


async def test_send_on_fill_on_schedules_all_three_and_fills_not_reached() -> None:
    settings = _settings()
    registry = _BootRegistry(settings)

    await schedule.ensure_workflow_schedules(registry)  # type: ignore[arg-type]

    assert registry.scheduler.checkin_configs == [schedule.checkin_fanout_config(settings)]
    assert registry.scheduler.checkin_reconcile_configs == [
        schedule.checkin_reconcile_config(settings)
    ]
    assert _fill_configs(registry.scheduler) == [settings.checkin_fill_schedule_id]
    assert registry.scheduler.removed == []
    store = InMemoryGraphStore()
    await _team(store)
    day = _weekday()
    filled = await _fill_pass(store, sends_on=True).run("demo", _after_fill_time(day))
    assert {item.status.summary for item in filled if item.as_of == day} == {
        f"{NOT_REACHED_LEAD} Current status is unknown."
    }


async def test_the_deployment_switch_off_removes_the_fill_schedule_whatever_the_tenant() -> None:
    settings = _settings(checkin_fill_enabled=False)
    registry = _BootRegistry(settings)

    await schedule.ensure_workflow_schedules(registry)  # type: ignore[arg-type]

    assert _fill_configs(registry.scheduler) == []
    assert settings.checkin_fill_schedule_id in registry.scheduler.removed
    # The send's schedules are untouched by it.
    assert registry.scheduler.checkin_configs == [schedule.checkin_fanout_config(settings)]


async def test_toggling_the_switch_removes_and_adds_the_fill_schedule_only() -> None:
    settings = _settings(checkin_fanout_enabled=False)
    registry = _BootRegistry(settings)

    await registry.fill_day.set("demo", False, actor="U-admin")
    assert registry.scheduler.removed == [settings.checkin_fill_schedule_id]
    assert _fill_configs(registry.scheduler) == []

    await registry.fill_day.set("demo", True, actor="U-admin")
    assert _fill_configs(registry.scheduler) == [settings.checkin_fill_schedule_id]
    assert registry.scheduler.checkin_configs == []
    assert registry.scheduler.checkin_reconcile_configs == []
    assert registry.scheduler.removed == [settings.checkin_fill_schedule_id]


async def test_the_registry_wires_the_switch_to_the_fill_schedule() -> None:
    settings = _settings(runtime_mode="memory", tenant_id="demo", workflow_provider="fake")
    registry = ServiceRegistry(settings)
    recorder = _RecordingWorkflowScheduler(settings.resolved_heartbeat_schedule_id)
    registry.workflow_scheduler = lambda: recorder  # type: ignore[method-assign]

    await registry.fill_day_settings_service().set("demo", False, actor="U-admin")
    await registry.fill_day_settings_service().set("other-tenant", True, actor="U-admin")

    # Only the deployment's tenant has the schedule; another tenant's save leaves it.
    assert recorder.removed == [settings.checkin_fill_schedule_id]
    assert recorder.sync_configs == []


async def test_a_fill_tick_checks_both_switches_and_fills_through_the_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryGraphStore()
    await _team(store)
    repository = InMemoryFillDayRepository()

    class _Registry:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def fill_day_settings_service(self) -> FillDaySettingsService:
            return FillDaySettingsService(repository)

        def fill_day_pass(self) -> FillDayPass:
            return _fill_pass(store, settings=FillDaySettingsService(repository))

        async def close(self) -> None:
            return None

    day = _weekday()
    tick = CheckinFillInput(tenant_id="demo", observed_at=_after_fill_time(day).isoformat())
    for deployment_on, tenant_on, expected in (
        (False, True, "disabled"),
        (True, False, "off"),
        (True, True, "ok"),
    ):
        await repository.save_fill_day(FillDaySetting(tenant_id="demo", enabled=tenant_on))
        registry = _Registry(_settings(checkin_fill_enabled=deployment_on))
        monkeypatch.setattr(checkin_fill, "_service_registry", lambda r=registry: r)
        result = await checkin_fill.run_checkin_fill_activity(tick)
        assert result.status == expected
        assert (result.filled > 0) is (expected == "ok")

    dispatched = sync_workflow_input(
        sync_dispatch_for_schedule(
            schedule.checkin_fill_schedule_config(_settings()), _after_fill_time(day)
        )
    )
    assert dispatched == CheckinFillInput(
        tenant_id="demo", observed_at=_after_fill_time(day).isoformat()
    )
