"""Day reports: when they are due, what they say, who resolves what, and how they are sent."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time
from email.message import EmailMessage

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from api.dtos import ReportRunResponse
from api.main import create_app
from config.settings import Settings
from core.application.day_report_service import DayReportService, ReportNotFound
from core.domain.blockers import BlockerSource, DeveloperBlocker
from core.domain.connections import ConnectionValues
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestStatus,
)
from core.domain.delivery import DeliveryStage, RequirementsSnapshot
from core.domain.errors import GraphNotFound, ProviderUnavailable
from core.domain.escalation_matrix import (
    ContactSource,
    EscalationLevel,
    EscalationMatrix,
    NeedType,
    default_matrix,
)
from core.domain.forecast import (
    CommitmentScope,
    CommitmentScopeKind,
    DateChange,
    Release,
    ReleaseMatch,
    ReleaseMatchKind,
)
from core.domain.gates import GateItem, ItemSource, ItemStatus, QuestionStatus, TrackedQuestion
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    GraphEdge,
    NodeKind,
    Pod,
    Project,
    Task,
)
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from core.domain.reports import (
    DayReport,
    DayReportDefinition,
    DeliveryOutcome,
    DestinationKind,
    ReportDefinitionError,
    ReportDestination,
    ReportGroup,
    ReportRun,
    ReportSchedule,
    ReportSection,
    ReportTable,
    RunStatus,
    RunTrigger,
    due_date,
    progress_bar,
    render_text,
    validated_definition,
)
from core.domain.rollup import Rag
from infra.adapters.reports.render import email_html, slack_text, teams_payload
from infra.adapters.reports.sender import ConnectionReportSender
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.persistence.postgres_reports import PostgresDayReportRepository
from infra.registry import ServiceRegistry
from infra.workflows import delivery_reports

TENANT = "demo"
TODAY = date(2026, 10, 5)  # a Monday
NOW = datetime(2026, 10, 5, 16, 30, tzinfo=UTC)  # 18:30 in Berlin
BERLIN = "Europe/Berlin"
S = DeliveryStage


def _schedule(
    at: time = time(18, 0), weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
) -> ReportSchedule:
    return ReportSchedule(local_time=at, timezone=BERLIN, weekdays=weekdays)


def _definition(**overrides: object) -> DayReportDefinition:
    values: dict[str, object] = {
        "tenant_id": TENANT,
        "report_id": "rep-1",
        "name": "Checkout daily",
        "project_id": "checkout",
        "enabled": True,
        "schedule": _schedule(),
        "destinations": (ReportDestination(kind=DestinationKind.PERSON, target="dev-asha"),),
        "updated_at": NOW,
        "updated_by": "admin",
    }
    values.update(overrides)
    return DayReportDefinition(**values)  # type: ignore[arg-type]


# --- When a report is due ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 10, 5, 15, 59, tzinfo=UTC), None),  # 17:59 Berlin: not yet
        (datetime(2026, 10, 5, 16, 0, tzinfo=UTC), TODAY),  # 18:00 sharp
        (datetime(2026, 10, 5, 18, 59, tzinfo=UTC), TODAY),  # 20:59: still in the window
        (datetime(2026, 10, 5, 19, 0, tzinfo=UTC), None),  # 21:00: too late to send
        (datetime(2026, 10, 4, 16, 30, tzinfo=UTC), None),  # a Sunday
    ],
)
def test_a_report_is_due_from_its_local_time_for_three_hours_on_its_weekdays(
    now: datetime, expected: date | None
) -> None:
    assert due_date(_schedule(), now) == expected


def test_due_follows_the_reports_own_timezone_across_daylight_saving() -> None:
    # Berlin leaves summer time on 25 October 2026: 18:00 is 17:00 UTC after it.
    assert due_date(_schedule(), datetime(2026, 10, 26, 16, 30, tzinfo=UTC)) is None
    assert due_date(_schedule(), datetime(2026, 10, 26, 17, 0, tzinfo=UTC)) == date(2026, 10, 26)


def test_a_definition_is_tidied_and_its_destinations_deduplicated() -> None:
    definition = validated_definition(
        _definition(
            name="  Checkout   daily ",
            schedule=_schedule(time(18, 0, 31), (4, 0, 0)),
            destinations=(
                ReportDestination(kind=DestinationKind.EMAIL, target=" Team-DL@Example.com "),
                ReportDestination(kind=DestinationKind.EMAIL, target="team-dl@example.com"),
                ReportDestination(kind=DestinationKind.TEAMS, target="ignored"),
            ),
        )
    )

    assert definition.name == "Checkout daily"
    assert definition.schedule.weekdays == (0, 4)
    assert definition.schedule.local_time == time(18, 0)
    assert definition.destinations == (
        ReportDestination(kind=DestinationKind.EMAIL, target="team-dl@example.com"),
        ReportDestination(kind=DestinationKind.TEAMS, target=""),
    )


def test_a_definition_that_cannot_be_sent_names_every_problem() -> None:
    with pytest.raises(ReportDefinitionError) as caught:
        validated_definition(
            _definition(
                name=" ",
                schedule=ReportSchedule(local_time=time(18), timezone="Mars/Olympus", weekdays=()),
                destinations=(ReportDestination(kind=DestinationKind.EMAIL, target="not-mail"),),
            )
        )

    message = str(caught.value)
    for problem in (
        "A report needs a name",
        "pick at least one weekday",
        "'Mars/Olympus' is not a timezone",
        "'not-mail' is not an email address",
        "a report that is on needs somewhere to go",
    ):
        assert problem in message


def test_a_run_reports_partial_and_failed_sends() -> None:
    destination = ReportDestination(kind=DestinationKind.EMAIL, target="a@example.com")
    run = ReportRun(
        tenant_id=TENANT,
        run_id="r",
        report_id="rep-1",
        report_date=TODAY,
        trigger=RunTrigger.SCHEDULE,
        started_at=NOW,
    )
    ok = DeliveryOutcome(destination=destination, ok=True, detail="Emailed.")
    failed = DeliveryOutcome(destination=destination, ok=False, detail="Not sent.")

    assert run.status is RunStatus.SENDING
    assert (
        ReportRun(**{**run.__dict__, "finished_at": NOW, "outcomes": (ok,)}).status
        is RunStatus.SENT
    )
    assert (
        ReportRun(**{**run.__dict__, "finished_at": NOW, "outcomes": (ok, failed)}).status
        is RunStatus.PARTIAL
    )
    assert ReportRun(**{**run.__dict__, "finished_at": NOW, "outcomes": (failed,)}).status is (
        RunStatus.FAILED
    )


def test_the_progress_bar_reads_the_same_everywhere() -> None:
    assert progress_bar(64.0, width=10) == "██████░░░░"
    assert progress_bar(None, width=4) == "░░░░"
    assert progress_bar(140.0, width=4) == "████"


# --- What the report says ----------------------------------------------------------------


class _RegistryOnTheDay(ServiceRegistry):
    """The registry with the tenant's today pinned to the report's day.

    A day before today is read from stored snapshots rather than the live
    graph, so on the wall clock these reports would count nothing.
    """

    def _tenant_today(self) -> date:
        return TODAY


async def _seeded_registry() -> tuple[ServiceRegistry, InMemoryGraphStore]:
    store = InMemoryGraphStore()
    for node in (
        Project(tenant_id=TENANT, id="checkout", name="Checkout Revamp"),
        Pod(
            tenant_id=TENANT,
            id="pod-pay",
            name="Payments Pod",
            metadata={
                "escalation_sm_chat_external_id": "U-PRIYA",
                "escalation_sm_display_name": "Priya",
                "escalation_sm_member_id": "dev-priya",
                "escalation_manager_chat_external_id": "U-MARK",
                "escalation_manager_display_name": "Mark",
            },
        ),
        Pod(
            tenant_id=TENANT,
            id="pod-platform",
            name="Platform Pod",
            metadata={
                "escalation_sm_chat_external_id": "U-LENA",
                "escalation_sm_display_name": "Lena",
            },
        ),
        Developer(tenant_id=TENANT, id="dev-asha", name="Asha"),
        Developer(tenant_id=TENANT, id="dev-priya", name="Priya"),
        Developer(tenant_id=TENANT, id="dev-omar", name="Omar"),
        Developer(tenant_id=TENANT, id="dev-zoe", name="Zoe"),
        Pod(tenant_id=TENANT, id="pod-store", name="Storefront Pod"),
    ):
        await store.upsert_node(node)
    issues = {
        "CHK-1": ("In QA", "in_progress", "pod-pay"),
        "CHK-2": ("Done", "done", "pod-pay"),
        "CHK-3": ("In Progress", "in_progress", "pod-pay"),
        "PLT-9": ("In Progress", "in_progress", "pod-platform"),
        "STO-4": ("In Progress", "in_progress", "pod-store"),
    }
    for key, (status, state, parent) in issues.items():
        await store.upsert_node(
            Task(
                tenant_id=TENANT,
                id=key,
                name=f"{key} work",
                metadata={"key": key, "status": status, "state": state},
            )
        )
        if parent is not None:
            await store.add_edge(_edge(parent, key))
    for edge in (
        _edge("checkout", "pod-pay"),
        _edge("pod-pay", "dev-asha"),
        _edge("pod-pay", "dev-priya"),
        _edge("pod-platform", "dev-omar"),
        _edge("pod-store", "dev-zoe"),
        _assigned("dev-asha", "CHK-1"),
        _assigned("dev-asha", "CHK-3"),
        _assigned("dev-omar", "PLT-9"),
        _assigned("dev-zoe", "STO-4"),
    ):
        await store.add_edge(edge)
    await store.record_developer_blockers(
        TENANT,
        [
            # Waits on another team's issue: Omar owns PLT-9.
            _blocker("b-1", "dev-asha", "CHK-1 waits on Omar's API change in PLT-9", "CHK-1", 5),
            # Inside the team: the scrum master clears it.
            _blocker("b-2", "dev-asha", "Test environment is down", "CHK-3", 1),
            # Another team's person waiting on this project's issue.
            _blocker("b-3", "dev-zoe", "STO-4 needs CHK-3 merged first", "STO-4", 2),
        ],
    )
    asked_at = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
    await store.create(
        CrossPersonRequest(
            tenant_id=TENANT,
            id="req-1",
            requester_id="dev-asha",
            requester_chat_ref=None,
            counterpart_id="dev-omar",
            kind=CrossPersonRequestKind.REVIEW,
            note="private words from the chat",
            source_correlation_id="corr-1",
            status=CrossPersonRequestStatus.OPEN,
            created_at=asked_at,
            updated_at=asked_at,
            task_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-1"),
        )
    )
    settings = Settings(
        _env_file=None,
        secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
        runtime_mode="memory",
        tenant_default_timezone=BERLIN,
        auth_frontend_url="https://console.example.com",
    )
    return _RegistryOnTheDay(settings, graph_store=store), store


def _edge(parent: str, child: str) -> GraphEdge:
    return GraphEdge(
        tenant_id=TENANT, from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
    )


def _assigned(developer: str, task: str) -> GraphEdge:
    return GraphEdge(
        tenant_id=TENANT, from_node_id=developer, to_node_id=task, kind=EdgeKind.ASSIGNED_TO
    )


def _blocker(
    blocker_id: str, developer: str, description: str, work_item: str, age_days: int
) -> DeveloperBlocker:
    first_seen = date.fromordinal(TODAY.toordinal() - age_days)
    return DeveloperBlocker(
        tenant_id=TENANT,
        blocker_id=blocker_id,
        developer_id=developer,
        description=description,
        normalized_key=description.lower(),
        work_item_id=work_item,
        source=BlockerSource.CHECKIN,
        first_seen_on=first_seen,
        last_seen_on=TODAY,
    )


async def _build(registry: ServiceRegistry) -> DayReport:
    return await registry.day_report_builder().build(TENANT, "checkout", TODAY)


def _section(report: DayReport, title: str) -> ReportSection:
    return next(section for section in report.sections if section.title == title)


def _groups(section: ReportSection) -> dict[str, tuple[str, ...]]:
    return {group.heading: group.lines for group in section.groups}


async def test_the_report_reads_in_the_order_of_a_status_mail() -> None:
    registry, _store = await _seeded_registry()

    report = await _build(registry)

    assert [section.title for section in report.sections] == [
        "In short",
        "Where we stand",
        "Most important",
        "What we need, and from whom",
        "Open questions",
    ]


async def test_what_we_need_names_each_owner_with_the_kind_and_escalation() -> None:
    registry, _store = await _seeded_registry()

    report = await _build(registry)

    needs = _section(report, "What we need, and from whom")
    assert [group.heading for group in needs.groups] == ["Asha", "Omar", "Priya"]
    assert _groups(needs) == {
        "Asha": (
            "Fix: Zoe waits on CHK-3 (2 days). Escalated to Priya (Scrum master).",
            "Fix: CHK-1: no test case yet for engineering delivery (before business testing).",
        ),
        "Omar": (
            "Fix: CHK-1: CHK-1 waits on Omar's API change in PLT-9 (5 days; reported by Asha, "
            "waits on Platform Pod (PLT-9)). Escalated to Lena (Scrum master).",
            "Review: CHK-1: Asha asked for a review (2 days). Escalated to Lena (Scrum master).",
        ),
        "Priya": ("Fix: CHK-3: Test environment is down (1 day; reported by Asha).",),
    }
    # The request's own words never leave OpenProgram.
    assert "private words" not in render_text(report)


async def test_a_projects_own_matrix_decides_when_and_to_whom() -> None:
    registry, _store = await _seeded_registry()
    await registry.escalation_matrix_service().save(
        EscalationMatrix(
            tenant_id=TENANT,
            project_id="checkout",
            decision_owner_id=None,
            levels=(
                EscalationLevel(
                    label="Delivery lead",
                    source=ContactSource.MEMBER,
                    member_id="dev-omar",
                    after_days={NeedType.FIX: 1},
                ),
            ),
        ),
        actor="admin",
    )

    needs = _section(await _build(registry), "What we need, and from whom")

    lines = _groups(needs)
    assert (
        lines["Asha"][0] == "Fix: Zoe waits on CHK-3 (2 days). Escalated to Omar (Delivery lead)."
    )
    # Never escalated to the person it is already with, nor for a kind the level leaves out.
    assert not any("Escalated" in line for line in lines["Omar"])
    assert lines["Priya"] == (
        "Fix: CHK-3: Test environment is down (1 day; reported by Asha). "
        "Escalated to Omar (Delivery lead).",
    )


async def test_in_short_names_the_date_and_what_is_needed_most() -> None:
    registry, _store = await _seeded_registry()

    report = await _build(registry)

    assert report.title == "Checkout Revamp: day report, Mon 5 Oct 2026"
    assert report.headline == "4 fixes and 1 review needed, 3 escalated."
    assert report.rag is Rag.UNKNOWN
    assert report.percent_complete == pytest.approx(100 / 3)
    assert report.progress_line == "33% complete: 1 of 3 requirements in production."
    assert report.console_url == "https://console.example.com/delivery/project/checkout"
    assert _section(report, "In short").lines == (
        "Delivery: no delivery date set.",
        "Needed most: a fix from Omar on PLT-9 (5 days), a fix from Asha on CHK-3 (2 days) "
        "and a review from Omar on CHK-1 (2 days).",
    )


@pytest.mark.parametrize(
    ("environment", "console_url", "link"),
    [
        pytest.param("local", None, "http://localhost:5173/delivery/project/checkout", id="local"),
        pytest.param(
            "production",
            "https://openprogram.example.com",
            "https://openprogram.example.com/delivery/project/checkout",
            id="set",
        ),
        # A deployment that never set its address would link to a local one.
        pytest.param("production", None, None, id="deployed-without-an-address"),
    ],
)
async def test_the_report_links_the_console_only_where_its_readers_can_open_it(
    environment: str, console_url: str | None, link: str | None
) -> None:
    registry, store = await _seeded_registry()
    settings = registry.settings.model_copy(
        update={
            "environment": environment,
            "auth_frontend_url": "http://localhost:5173",
            "console_url": console_url,
        }
    )

    report = await _build(_RegistryOnTheDay(settings, graph_store=store))

    assert report.console_url == link
    assert ("Open in OpenProgram" in render_text(report)) is (link is not None)


async def test_where_we_stand_says_what_changed_and_why() -> None:
    registry, _store = await _seeded_registry()
    await registry.requirements_snapshot_repository().save(
        RequirementsSnapshot(
            tenant_id=TENANT,
            project_id="checkout",
            day=date(2026, 10, 2),
            stage_counts={**{stage: 0 for stage in S}, S.IN_DEVELOPMENT: 2},
            stage_points={stage: 0.0 for stage in S},
            has_points=False,
            excluded=0,
            unmapped_statuses=(),
            items={"CHK-1": S.IN_DEVELOPMENT, "CHK-2": S.IN_DEVELOPMENT},
            titles={"CHK-1": "CHK-1 work", "CHK-2": "CHK-2 work"},
            computed_at=NOW,
        )
    )
    scope = CommitmentScope(kind=CommitmentScopeKind.PROJECT, id="checkout", project_id="checkout")
    for when, target, note in (
        (datetime(2026, 9, 20, 9, 0, tzinfo=UTC), date(2026, 11, 14), ""),
        (datetime(2026, 10, 4, 9, 0, tzinfo=UTC), date(2026, 11, 21), "Vendor moved its test date"),
    ):
        await registry.commitment_repository().append(
            DateChange(
                tenant_id=TENANT,
                scope=scope,
                target_date=target,
                changed_at=when,
                changed_by="dev-priya",
                note=note,
            )
        )

    report = await _build(registry)

    stand = _groups(_section(report, "Where we stand"))
    assert stand["Progress"] == (
        "33% complete: 1 of 3 requirements in production (0% on Fri 2 Oct 2026).",
        "Raised 0 · Groomed 0 · In development 1 (-1) · In testing 1 (+1) · "
        "Business testing 0 · Production 1 (+1)",
    )
    assert stand["What changed since Fri 2 Oct 2026"] == (
        "CHK-1 CHK-1 work: In development → In testing",
        "CHK-2 CHK-2 work: In development → Production",
        "CHK-3 CHK-3 work: new, in In development",
        "Scope +1 requirement",
        "The delivery date moved from Sat 14 Nov 2026 to Sat 21 Nov 2026 "
        "(Priya: Vendor moved its test date).",
    )
    assert stand["Acceptance and tests"] == (
        "Business acceptance (before production): 0 of 3 passed, 3 with nothing confirmed; "
        "1 moved on without it.",
        "Engineering delivery (before business testing): 0 of 3 passed, 3 with nothing "
        "confirmed; 1 moved on without it.",
    )
    assert _section(report, "In short").lines[0].startswith("Delivery Sat 21 Nov 2026: ")
    assert _section(report, "Most important").lines == (
        "CHK-2 reached production without Business acceptance and Engineering delivery passing.",
    )


async def test_gate_sign_offs_and_questions_go_to_who_decides_and_who_was_asked() -> None:
    registry, store = await _seeded_registry()
    await store.upsert_node(
        Task(
            tenant_id=TENANT,
            id="CHK-1",
            name="CHK-1 work",
            metadata={"key": "CHK-1", "status": "UAT", "state": "in_progress"},
        )
    )
    await registry.escalation_matrix_service().save(
        replace(default_matrix(TENANT, "checkout"), decision_owner_id="dev-priya"), actor="admin"
    )
    _templates, items, questions, _scans = registry.gate_repositories()
    await items.save(
        GateItem(
            tenant_id=TENANT,
            item_id="g-1",
            issue_key="CHK-1",
            template_id="business-acceptance",
            kind="acceptance",
            text="A saved card pays without the CVC",
            status=ItemStatus.PENDING,
            source=ItemSource.DESCRIPTION,
            source_ref="description",
            created_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
            updated_at=datetime(2026, 10, 2, 9, 0, tzinfo=UTC),
            created_by="scan",
        )
    )
    await questions.save(
        TrackedQuestion(
            tenant_id=TENANT,
            question_id="q-1",
            issue_key="CHK-1",
            comment_ref="c-1",
            asked_by="acc-asha",
            asked_by_name="Asha",
            asked_to="dev-priya",
            asked_to_name="Priya",
            asked_at=datetime(2026, 9, 29, 9, 0, tzinfo=UTC),
            summary="Is 3-D Secure in scope?",
            status=QuestionStatus.NOT_YET,
            confirmed=True,
        )
    )

    report = await _build(registry)

    priya = _groups(_section(report, "What we need, and from whom"))["Priya"]
    assert "Decision: CHK-1: sign off 1 acceptance criterion (before production) (3 days)." in priya
    assert (
        'Answer: CHK-1: "Is 3-D Secure in scope?" (6 days; asked by Asha). '
        "Escalated to Mark (Manager)."
    ) in priya
    table = _section(report, "Open questions").table
    assert table is not None
    assert table.rows == (
        ("CHK-1", "Is 3-D Secure in scope?", "Priya", "Tue 29 Sep 2026", "Not yet (6 days)"),
    )
    assert "CHK-1 · What we asked: Is 3-D Secure in scope?" in render_text(report)


async def test_a_report_on_a_release_counts_only_the_releases_issues() -> None:
    registry, store = await _seeded_registry()
    await store.upsert_node(
        Task(
            tenant_id=TENANT,
            id="CHK-1",
            name="CHK-1 work",
            metadata={"key": "CHK-1", "status": "In QA", "state": "in_progress", "labels": "r1"},
        )
    )
    await registry.release_repository().save(
        Release(
            tenant_id=TENANT,
            release_id="rel-1",
            project_id="checkout",
            name="Checkout 1.0",
            match=ReleaseMatch(kind=ReleaseMatchKind.LABEL, value="r1"),
            updated_at=NOW,
            updated_by="admin",
        )
    )

    report = await registry.day_report_builder().build(
        TENANT, "checkout", TODAY, release_id="rel-1"
    )

    assert report.title == "Checkout Revamp, Checkout 1.0: day report, Mon 5 Oct 2026"
    assert report.progress_line == "0% complete: 0 of 1 requirements in production."
    needs = _groups(_section(report, "What we need, and from whom"))
    assert set(needs) == {"Asha", "Omar"}
    assert not any("CHK-3" in line for lines in needs.values() for line in lines)


async def test_a_release_of_another_project_is_refused() -> None:
    registry, store = await _seeded_registry()
    await store.upsert_node(Project(tenant_id=TENANT, id="other", name="Other"))
    await registry.release_repository().save(
        Release(
            tenant_id=TENANT,
            release_id="rel-x",
            project_id="other",
            name="Other 1.0",
            match=ReleaseMatch(kind=ReleaseMatchKind.LABEL, value="x"),
            updated_at=NOW,
            updated_by="admin",
        )
    )

    with pytest.raises(GraphNotFound):
        await registry.day_report_builder().build(TENANT, "checkout", TODAY, release_id="rel-x")


async def test_a_project_with_nothing_open_says_so() -> None:
    registry, store = await _seeded_registry()
    await store.upsert_node(Project(tenant_id=TENANT, id="quiet", name="Quiet"))

    report = await registry.day_report_builder().build(TENANT, "quiet", TODAY)

    assert report.headline == "Nothing is needed from anyone today."
    assert report.progress_line == "No requirements counted yet."
    text = render_text(report)
    assert "Nothing is needed from anyone today." in text
    assert "Nothing threatens the delivery date today." in text
    assert "No open questions." in text


async def test_only_a_project_gets_a_report() -> None:
    registry, _store = await _seeded_registry()

    with pytest.raises(GraphNotFound):
        await registry.day_report_builder().build(TENANT, "pod-pay", TODAY)


# --- Sending -------------------------------------------------------------------------------


@dataclass
class _RecordingSender:
    calls: list[tuple[ReportDestination, str]] = field(default_factory=list)

    async def deliver(
        self, tenant_id: str, destination: ReportDestination, report: DayReport
    ) -> DeliveryOutcome:
        self.calls.append((destination, report.title))
        return DeliveryOutcome(destination=destination, ok=True, detail="Sent.")


async def _service_with_sender(
    sender: _RecordingSender,
) -> tuple[DayReportService, ServiceRegistry]:
    registry, _store = await _seeded_registry()
    service = DayReportService(
        repository=registry.day_report_repository(),
        sender=sender,
        builder=registry.day_report_builder(),
        graph_repository=registry.graph_repository(),
        clock=lambda: NOW,
    )
    return service, registry


async def test_a_scheduled_report_goes_out_once_per_local_day() -> None:
    sender = _RecordingSender()
    service, registry = await _service_with_sender(sender)
    await service.save(_definition(report_id=""), actor="admin")

    first = await service.dispatch_due(TENANT, NOW)
    again = await service.dispatch_due(TENANT, datetime(2026, 10, 5, 17, 0, tzinfo=UTC))
    tomorrow = await service.dispatch_due(TENANT, datetime(2026, 10, 6, 16, 5, tzinfo=UTC))

    assert [run.report_date for run in first] == [TODAY]
    assert again == []
    assert [run.report_date for run in tomorrow] == [date(2026, 10, 6)]
    assert len(sender.calls) == 2
    assert first[0].status is RunStatus.SENT
    assert "Checkout Revamp: day report" in first[0].text


async def test_send_now_always_sends_and_does_not_use_up_the_scheduled_run() -> None:
    sender = _RecordingSender()
    service, _registry = await _service_with_sender(sender)
    saved = await service.save(_definition(report_id=""), actor="admin")

    manual = await service.send_now(TENANT, saved.report_id, actor="admin")
    scheduled = await service.dispatch_due(TENANT, NOW)
    runs = await service.runs(TENANT, saved.report_id)

    assert manual.trigger is RunTrigger.MANUAL and manual.actor == "admin"
    assert len(scheduled) == 1
    assert {run.trigger for run in runs} == {RunTrigger.MANUAL, RunTrigger.SCHEDULE}


async def test_the_days_note_opens_that_days_report_only() -> None:
    sender = _RecordingSender()
    service, _registry = await _service_with_sender(sender)
    saved = await service.save(_definition(report_id=""), actor="admin")

    note = await service.save_note(
        TENANT, saved.report_id, "  We need the vendor's answer\nby Thursday.  ", actor="dev-priya"
    )
    preview = await service.preview(TENANT, saved.report_id)
    await service.save_note(TENANT, saved.report_id, "", actor="dev-priya")
    after = await service.preview(TENANT, saved.report_id)

    assert note.report_date == TODAY and note.text == "We need the vendor's answer\nby Thursday."
    in_short = next(section for section in preview.report.sections if section.title == "In short")
    assert in_short.lines[0] == "Priya: We need the vendor's answer\nby Thursday."
    assert await service.note(TENANT, saved.report_id) is None
    assert "vendor" not in after.text
    with pytest.raises(ReportDefinitionError, match="at most 1000"):
        await service.save_note(TENANT, saved.report_id, "x" * 1001, actor="dev-priya")


async def test_a_report_may_cover_one_release_of_its_project() -> None:
    sender = _RecordingSender()
    service, registry = await _service_with_sender(sender)
    await registry.release_repository().save(
        Release(
            tenant_id=TENANT,
            release_id="rel-1",
            project_id="checkout",
            name="Checkout 1.0",
            match=ReleaseMatch(kind=ReleaseMatchKind.LABEL, value="r1"),
            updated_at=NOW,
            updated_by="admin",
        )
    )

    saved = await service.save(_definition(report_id="", release_id=" rel-1 "), actor="admin")
    preview = await service.preview(TENANT, saved.report_id)

    assert saved.release_id == "rel-1"
    assert preview.report.title.startswith("Checkout Revamp, Checkout 1.0: day report")
    with pytest.raises(GraphNotFound):
        await service.save(_definition(report_id="", release_id="nope"), actor="admin")


async def test_a_report_that_is_off_or_has_nowhere_to_go_is_not_sent() -> None:
    sender = _RecordingSender()
    service, _registry = await _service_with_sender(sender)
    await service.save(_definition(report_id="", enabled=False), actor="admin")

    assert await service.dispatch_due(TENANT, NOW) == []
    assert sender.calls == []


async def test_saving_needs_a_real_project_and_an_existing_report() -> None:
    service, _registry = await _service_with_sender(_RecordingSender())

    with pytest.raises(GraphNotFound):
        await service.save(_definition(report_id="", project_id="nope"), actor="a")
    with pytest.raises(ReportNotFound):
        await service.save(_definition(report_id="missing"), actor="a")


async def test_a_report_whose_project_was_removed_records_why_nothing_was_sent() -> None:
    sender = _RecordingSender()
    service, registry = await _service_with_sender(sender)
    saved = await service.save(_definition(report_id=""), actor="admin")
    await registry.graph_repository().delete_node(TENANT, "checkout")

    run = await service.send_now(TENANT, saved.report_id, actor="admin")

    assert run.status is RunStatus.FAILED
    assert run.outcomes[0].detail == "Not sent: the report's project or release no longer exists."
    assert sender.calls == []


# --- Each destination ---------------------------------------------------------------------


def _report() -> DayReport:
    return DayReport(
        title="Checkout Revamp: day report, Mon 5 Oct 2026",
        project_name="Checkout Revamp",
        report_date=TODAY,
        rag=Rag.AMBER,
        headline="1 blocker needs action.",
        percent_complete=50.0,
        progress_line="50% complete: 1 of 2 requirements in production.",
        sections=(
            ReportSection(title="In short", lines=("CHK-1: <script>x</script> & co.",)),
            ReportSection(
                title="What we need, and from whom",
                groups=(ReportGroup(heading="Omar", lines=("Fix: PLT-9 rate limits (5 days).",)),),
            ),
            ReportSection(
                title="Open questions",
                table=ReportTable(
                    columns=("Ticket", "What we asked", "Asked to"),
                    rows=(("CHK-1", "Is it in scope?", "Priya"),),
                ),
            ),
            ReportSection(title="Most important", empty_text="No open risk signals."),
        ),
        console_url="https://console.example.com/delivery/project/checkout",
    )


class _Connections:
    def __init__(self, **connectors: Mapping[str, str]) -> None:
        self._connectors = connectors

    async def resolve(self, tenant_id: str, connector: str) -> ConnectionValues | None:
        values = self._connectors.get(connector)
        return ConnectionValues(connector=connector, values=values) if values else None


@dataclass
class _Chat:
    sent: list[tuple[str, str]] = field(default_factory=list)
    fail: bool = False

    async def send_dm(self, user: ChatUserRef, message: OutboundMessage) -> str:
        if self.fail:
            raise ProviderUnavailable("slack request failed: account_inactive xoxb-secret")
        self.sent.append((user.external_id, message.text))
        return "ts-1"

    async def open_thread(self, user: ChatUserRef) -> str:
        return "thread"

    async def fetch_reply(self, thread_id: str) -> InboundMessage | None:
        return None


def _sender(**overrides: object) -> ConnectionReportSender:
    async def chat_id(tenant_id: str, member_id: str) -> str | None:
        return {"dev-asha": "U-ASHA"}.get(member_id)

    values: dict[str, object] = {
        "connections": _Connections(),
        "chat_provider": _Chat,
        "channel_poster": None,
        "member_chat_id": chat_id,
    }
    values.update(overrides)
    return ConnectionReportSender(**values)  # type: ignore[arg-type]


async def test_a_person_gets_the_report_as_a_direct_message() -> None:
    chat = _Chat()
    sender = _sender(chat_provider=lambda: chat)

    sent = await sender.deliver(
        TENANT, ReportDestination(kind=DestinationKind.PERSON, target="dev-asha"), _report()
    )
    unknown = await sender.deliver(
        TENANT, ReportDestination(kind=DestinationKind.PERSON, target="dev-gone"), _report()
    )

    assert sent.ok and sent.detail == "Sent as a direct message."
    assert chat.sent[0][0] == "U-ASHA"
    assert "█████" in chat.sent[0][1]
    assert not unknown.ok


async def test_a_failed_send_records_a_fixed_sentence_never_the_error() -> None:
    sender = _sender(chat_provider=lambda: _Chat(fail=True))

    outcome = await sender.deliver(
        TENANT, ReportDestination(kind=DestinationKind.PERSON, target="dev-asha"), _report()
    )

    assert outcome.ok is False
    assert outcome.detail == "Not sent: the chat provider did not take the message."
    assert "xoxb" not in outcome.detail


async def test_a_chat_channel_needs_slack_and_the_app_in_the_channel() -> None:
    posted: list[tuple[str, str]] = []

    async def post(channel: str, text: str) -> str:
        if channel == "C-PRIVATE":
            raise ProviderUnavailable("slack request failed: not_in_channel")
        posted.append((channel, text))
        return "ts"

    no_slack = await _sender().deliver(
        TENANT, ReportDestination(kind=DestinationKind.CHAT_CHANNEL, target="C1"), _report()
    )
    ok = await _sender(channel_poster=post).deliver(
        TENANT, ReportDestination(kind=DestinationKind.CHAT_CHANNEL, target="C1"), _report()
    )
    private = await _sender(channel_poster=post).deliver(
        TENANT, ReportDestination(kind=DestinationKind.CHAT_CHANNEL, target="C-PRIVATE"), _report()
    )

    assert no_slack.detail == "Not sent: chat channels need the Slack chat provider."
    assert ok.ok and posted[0][0] == "C1"
    assert "&lt;script&gt;" in posted[0][1] and "<script>" not in posted[0][1]
    assert (
        private.detail == "Not sent: the OpenProgram app is not in that channel. Invite it first."
    )


async def test_email_goes_through_the_email_connection_with_text_and_html() -> None:
    sent: list[EmailMessage] = []

    def smtp(values: Mapping[str, str], message: EmailMessage) -> None:
        assert values["host"] == "smtp.example.com"
        sent.append(message)

    connections = _Connections(
        email={"host": "smtp.example.com", "port": "587", "from_address": "op@example.com"}
    )
    outcome = await _sender(connections=connections, smtp_send=smtp).deliver(
        TENANT, ReportDestination(kind=DestinationKind.EMAIL, target="team@example.com"), _report()
    )
    missing = await _sender().deliver(
        TENANT, ReportDestination(kind=DestinationKind.EMAIL, target="team@example.com"), _report()
    )

    assert outcome.ok and outcome.detail == "Emailed."
    message = sent[0]
    assert message["To"] == "team@example.com"
    assert message["Subject"] == "Checkout Revamp: day report, Mon 5 Oct 2026"
    assert "OpenProgram <op@example.com>" in message["From"]
    html = message.get_body(("html",))
    assert html is not None and "&lt;script&gt;" in html.get_content()
    assert missing.detail == "Not sent: email is not set up. Add it under Admin, Integrations."


@respx.mock
async def test_teams_posts_a_card_to_the_connections_webhook() -> None:
    hook = respx.post("https://acme.webhook.office.com/workflows/1").mock(
        return_value=httpx.Response(202)
    )
    connections = _Connections(teams={"webhook_url": "https://acme.webhook.office.com/workflows/1"})

    outcome = await _sender(connections=connections).deliver(
        TENANT, ReportDestination(kind=DestinationKind.TEAMS), _report()
    )

    assert outcome.ok
    card = json.loads(hook.calls.last.request.content)["attachments"][0]["content"]
    assert card["body"][0]["text"] == "Checkout Revamp: day report, Mon 5 Oct 2026"
    assert card["actions"][0]["url"] == "https://console.example.com/delivery/project/checkout"


def test_every_format_carries_the_same_report() -> None:
    report = _report()

    teams = json.dumps(teams_payload(report))
    for rendered in (render_text(report), slack_text(report), email_html(report), teams):
        assert "50% complete" in rendered
        assert "No open risk signals." in rendered
        assert "Omar" in rendered
        assert "Fix: PLT-9 rate limits (5 days)." in rendered
        assert "Is it in scope?" in rendered
    assert teams.count("No open risk signals.") == 1
    assert "<script>" not in slack_text(report) and "<script>" not in email_html(report)
    assert "<td" in email_html(report) and "<th" in email_html(report)
    assert "• CHK-1 · What we asked: Is it in scope? · Asked to: Priya" in render_text(report)


# --- Postgres -------------------------------------------------------------------------------


@dataclass
class _RecordingExecutor:
    rows: list[dict[str, object]] = field(default_factory=list)
    calls: list[tuple[str, Sequence[object]]] = field(default_factory=list)

    async def execute(self, query: str, params: Sequence[object] = ()) -> object:
        self.calls.append((query, params))
        return object()

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]:
        self.calls.append((query, params))
        return self.rows


async def test_postgres_claims_a_scheduled_run_with_the_partial_unique_index() -> None:
    executor = _RecordingExecutor()
    repository = PostgresDayReportRepository(executor)
    run = ReportRun(
        tenant_id=TENANT,
        run_id="run-1",
        report_id="rep-1",
        report_date=TODAY,
        trigger=RunTrigger.SCHEDULE,
        started_at=NOW,
    )

    claimed = await repository.claim_scheduled_run(run)

    query, params = executor.calls[0]
    assert "ON CONFLICT (tenant_id, report_id, report_date)" in query
    assert "WHERE trigger = 'schedule' DO NOTHING" in query
    assert params == (TENANT, "run-1", "rep-1", TODAY, NOW)
    assert claimed is False  # no row came back


async def test_postgres_reads_a_definition_back() -> None:
    executor = _RecordingExecutor(
        rows=[
            {
                "tenant_id": TENANT,
                "report_id": "rep-1",
                "name": "Checkout daily",
                "project_id": "checkout",
                "enabled": True,
                "local_time": time(18, 0),
                "timezone": BERLIN,
                "weekdays": [0, 1, 2, 3, 4],
                "destinations": [{"kind": "email", "target": "team@example.com"}],
                "updated_at": NOW,
                "updated_by": "admin",
            }
        ]
    )

    definition = await PostgresDayReportRepository(executor).get_definition(TENANT, "rep-1")

    assert definition is not None
    assert definition.schedule == _schedule()
    assert definition.destinations == (
        ReportDestination(kind=DestinationKind.EMAIL, target="team@example.com"),
    )
    assert definition.release_id is None


async def test_postgres_keeps_a_reports_release_and_its_days_note() -> None:
    executor = _RecordingExecutor(
        rows=[
            {
                "tenant_id": TENANT,
                "report_id": "rep-1",
                "report_date": TODAY,
                "text": "Vendor answers on Thursday.",
                "author": "dev-priya",
                "updated_at": NOW,
            }
        ]
    )
    repository = PostgresDayReportRepository(executor)

    await repository.save_definition(_definition(release_id="rel-1"))
    note = await repository.get_note(TENANT, "rep-1", TODAY)
    assert note is not None
    await repository.save_note(replace(note, text=""))

    insert_query, insert_params = executor.calls[0]
    assert "release_id" in insert_query and insert_params[-1] == "rel-1"
    assert note.text == "Vendor answers on Thursday."
    assert executor.calls[-1][0].strip().startswith("DELETE FROM day_report_notes")


# --- API ------------------------------------------------------------------------------------


def _report_body(project_id: str, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": "Checkout daily",
        "project_id": project_id,
        "enabled": True,
        "schedule": {"local_time": "18:00", "timezone": BERLIN, "weekdays": [0, 1, 2, 3, 4]},
        "destinations": [{"kind": "person", "target": "U1001"}],
    }
    body.update(overrides)
    return body


def test_api_creates_previews_sends_and_lists_a_report(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"chat_provider": "fake"}))
    with TestClient(app) as client:
        asyncio.run(
            app.state.registry.graph_repository().upsert_node(
                Developer(tenant_id=TENANT, id="U1001", name="Dana")
            )
        )
        assert (
            client.post("/config/projects", json={"id": "checkout", "name": "Checkout"}).status_code
            == 201
        )
        created = client.post("/config/reports", json=_report_body("checkout"))
        report_id = created.json()["report_id"]
        preview = client.post(f"/config/reports/{report_id}/preview")
        sent = client.post(f"/config/reports/{report_id}/send")
        listed = client.get("/config/reports")
        runs = client.get(f"/config/reports/{report_id}/runs")
        options = client.get("/config/reports/destinations")
        updated = client.put(
            f"/config/reports/{report_id}", json=_report_body("checkout", enabled=False)
        )
        removed = client.delete(f"/config/reports/{report_id}")
        gone = client.get(f"/config/reports/{report_id}")

    assert created.status_code == 201
    assert preview.status_code == 200
    assert preview.json()["title"].startswith("Checkout: day report")
    assert "WHERE WE STAND" in preview.json()["text"]
    assert sent.status_code == 200
    assert sent.json()["trigger"] == "manual"
    assert sent.json()["status"] == "sent"
    assert listed.json()[0]["last_run"]["run_id"] == sent.json()["run_id"]
    assert [run["run_id"] for run in runs.json()] == [sent.json()["run_id"]]
    available = {option["kind"]: option["available"] for option in options.json()}
    assert available == {"chat_channel": False, "person": True, "email": False, "teams": False}
    assert updated.json()["enabled"] is False
    assert removed.status_code == 204
    assert gone.status_code == 404


def test_api_names_the_member_who_sent_a_report(settings: Settings) -> None:
    app = create_app(settings=settings.model_copy(update={"chat_provider": "fake"}))
    with TestClient(app) as client:
        client.post("/config/projects", json={"id": "checkout", "name": "Checkout"})
        report_id = client.post("/config/reports", json=_report_body("checkout")).json()[
            "report_id"
        ]
        unnamed = client.post(f"/config/reports/{report_id}/send")
        client.post("/config/members", json={"id": "dev-user", "name": "Dana Admin"})
        named = client.post(f"/config/reports/{report_id}/send")
        runs = client.get(f"/config/reports/{report_id}/runs")
        listed = client.get("/config/reports")
        read = client.get(f"/config/reports/{report_id}")

    # An id that is no member's keeps no name: the console shows the id itself.
    assert (unnamed.json()["actor"], unnamed.json()["actor_name"]) == ("dev-user", None)
    assert (named.json()["actor"], named.json()["actor_name"]) == ("dev-user", "Dana Admin")
    assert [run["actor_name"] for run in runs.json()] == ["Dana Admin", "Dana Admin"]
    assert listed.json()[0]["last_run"]["actor_name"] == "Dana Admin"
    assert read.json()["last_run"]["actor_name"] == "Dana Admin"
    # A scheduled run has no sender to name.
    scheduled = ReportRun(
        tenant_id=TENANT,
        run_id="r",
        report_id=report_id,
        report_date=TODAY,
        trigger=RunTrigger.SCHEDULE,
        started_at=NOW,
    )
    assert ReportRunResponse.from_domain(scheduled, {"dev-user": "Dana Admin"}).actor_name is None


def test_api_refuses_a_report_it_cannot_send(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        no_project = client.post("/config/reports", json=_report_body("nope"))
        client.post("/config/projects", json={"id": "checkout", "name": "Checkout"})
        nowhere = client.post("/config/reports", json=_report_body("checkout", destinations=[]))

    assert no_project.status_code == 422
    assert nowhere.status_code == 422
    assert "needs somewhere to go" in nowhere.json()["detail"]


@pytest.mark.parametrize("role", ["dev", "po", "sm", "mgr", "exec"])
def test_only_an_admin_manages_reports(settings: Settings, role: str) -> None:
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": role}))
    with TestClient(app, raise_server_exceptions=False) as client:
        responses = [
            client.get("/config/reports"),
            client.post("/config/reports", json=_report_body("checkout")),
            client.post("/config/reports/x/send"),
            client.get("/config/reports/destinations"),
        ]

    assert [response.status_code for response in responses] == [403] * 4


@pytest.mark.parametrize(("role", "allowed"), [("po", True), ("mgr", True), ("dev", False)])
def test_a_product_owner_writes_the_note_without_managing_reports(
    settings: Settings, role: str, allowed: bool
) -> None:
    app = create_app(settings=settings.model_copy(update={"demo_mode": True}))
    persona = {"x-openprogram-dev-user": "dev-priya", "x-openprogram-dev-roles": role}
    with TestClient(app) as client:
        client.post("/config/projects", json={"id": "checkout", "name": "Checkout"})
        report_id = client.post("/config/reports", json=_report_body("checkout")).json()[
            "report_id"
        ]
        listed = client.get("/projects/checkout/day-reports", headers=persona)
        written = client.put(
            f"/day-reports/{report_id}/note", json={"text": "Vendor on Thu."}, headers=persona
        )
        missing = client.put("/day-reports/nope/note", json={"text": "x"}, headers=persona)

    if not allowed:
        assert (listed.status_code, written.status_code) == (403, 403)
        return
    assert listed.status_code == 200
    assert [item["report_id"] for item in listed.json()] == [report_id]
    assert written.status_code == 200
    assert written.json()["note"]["text"] == "Vendor on Thu."
    assert missing.status_code == 404


# --- Workflow -------------------------------------------------------------------------------


async def test_the_dispatch_activity_sends_due_reports_and_respects_the_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _store = await _seeded_registry()
    service = registry.day_report_service()
    await service.save(_definition(report_id=""), actor="admin")
    monkeypatch.setattr(delivery_reports, "_service_registry", lambda: registry)

    sent = await delivery_reports.run_day_report_dispatch_activity(
        delivery_reports.DayReportDispatchInput(tenant_id=TENANT, observed_at=NOW.isoformat())
    )
    off = registry.settings.model_copy(update={"day_report_enabled": False})
    monkeypatch.setattr(
        delivery_reports, "_service_registry", lambda: ServiceRegistry(off, graph_store=_store)
    )
    disabled = await delivery_reports.run_day_report_dispatch_activity(
        delivery_reports.DayReportDispatchInput(tenant_id=TENANT, observed_at=NOW.isoformat())
    )

    assert (sent.status, sent.reports_sent) == ("ok", 1)
    assert (disabled.status, disabled.reports_sent) == ("disabled", 0)


async def test_the_snapshot_activity_records_every_project_for_the_local_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _store = await _seeded_registry()
    monkeypatch.setattr(delivery_reports, "_service_registry", lambda: registry)

    # 23:30 UTC on the 5th is already the 6th in Berlin.
    result = await delivery_reports.run_delivery_snapshot_activity(
        delivery_reports.DeliverySnapshotInput(
            tenant_id=TENANT, observed_at=datetime(2026, 10, 5, 23, 30, tzinfo=UTC).isoformat()
        )
    )

    assert result.day == "2026-10-06"
    assert result.projects_recorded == 1
    stored = await registry.requirements_snapshot_repository().get(
        TENANT, "checkout", date(2026, 10, 6)
    )
    assert stored is not None and stored.total == 3
