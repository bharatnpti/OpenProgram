"""Exec Today's reasons, headline and top signals, from one reading of the day (attention.py).

A small portfolio, neutral names: Commerce Program > Shop project > Web Pod
(Ada, Ben, Cleo) and Payments Pod (Dina, Eli); Fay is in no team. Cart is a
workstream Web Pod serves with no ticket linked; Payments API holds one
ticket that reports no health. Statuses are rolled up by the real rollup.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from core.application.attention import (
    MAX_SIGNALS,
    AttentionDay,
    TeamGraph,
    attention_view,
    cell_reasons,
)
from core.application.persona_views import PersonaViewService, ProviderNames
from core.domain.blockers import BlockerSource, DeveloperBlocker, normalize_blocker_key
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    GraphEdge,
    NodeKind,
    Pod,
    Program,
    Project,
    Task,
    Workstream,
)
from core.domain.risk import (
    DriftFinding,
    DriftFindingKind,
    RiskEvidence,
    RiskFinding,
    RiskFindingStatus,
    RiskRuleId,
)
from core.domain.rollup import FactorKind, NodeStatus, Rag, RollupFactor
from core.domain.status import CheckIn, DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore

DAY = date(2026, 3, 9)
ASKED = datetime(2026, 3, 9, 7, 46, tzinfo=UTC)
KOLKATA = ZoneInfo("Asia/Kolkata")
NAMES = ProviderNames(tracker="Jira", vcs="GitLab")
ADA, BEN, CLEO, DINA, ELI, FAY = (f"U0PERSON{n}" for n in range(1, 7))
PEOPLE = {
    ADA: "Ada Lind",
    BEN: "Ben Okafor",
    CLEO: "Cleo Morales",
    DINA: "Dina Shah",
    ELI: "Eli Park",
    FAY: "Fay Moreau",
}
TEAM = (ADA, BEN, CLEO, DINA, ELI)


def _contains(parent: str, child: str) -> GraphEdge:
    return GraphEdge(
        tenant_id="demo", from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
    )


async def _portfolio() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    nodes = [
        Program(tenant_id="demo", id="prog", name="Commerce Program"),
        Project(tenant_id="demo", id="proj-shop", name="Shop"),
        Pod(tenant_id="demo", id="pod-web", name="Web Pod"),
        Pod(tenant_id="demo", id="pod-pay", name="Payments Pod"),
        Workstream(tenant_id="demo", id="ws-cart", name="Cart"),
        Workstream(tenant_id="demo", id="ws-pay", name="Payments API"),
        Task(
            tenant_id="demo",
            id="SHOP-8",
            name="Payment form",
            metadata={"key": "SHOP-8", "state": "in_progress", "status": "In Progress"},
        ),
        Task(
            tenant_id="demo",
            id="SHOP-5",
            name="Refund API",
            metadata={"key": "SHOP-5", "state": "todo", "status": "To Do"},
        ),
        *(Developer(tenant_id="demo", id=person, name=name) for person, name in PEOPLE.items()),
    ]
    for node in nodes:
        await store.upsert_node(node)
    edges = [
        _contains("prog", "proj-shop"),
        _contains("proj-shop", "pod-web"),
        _contains("proj-shop", "pod-pay"),
        _contains("proj-shop", "ws-cart"),
        _contains("proj-shop", "ws-pay"),
        _contains("ws-pay", "SHOP-5"),
        _contains("pod-web", "SHOP-8"),
        *(_contains("pod-web", person) for person in (ADA, BEN, CLEO)),
        *(_contains("pod-pay", person) for person in (DINA, ELI)),
        GraphEdge(
            tenant_id="demo",
            from_node_id="pod-web",
            to_node_id="ws-cart",
            kind=EdgeKind.ASSIGNED_TO,
        ),
    ]
    for edge in edges:
        await store.add_edge(edge)
    return store


async def _status(store: InMemoryGraphStore, person: str, source: StatusSource) -> None:
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id="demo",
            developer_id=person,
            as_of=DAY,
            source=source,
            blockers=(),
            summary="Checked in." if source is StatusSource.CONFIRMED else "No reply.",
        )
    )


async def _asked(store: InMemoryGraphStore, person: str, *, replied: bool) -> None:
    await store.record_checkin(
        CheckIn(
            tenant_id="demo",
            developer_id=person,
            correlation_id=f"checkin-{person}",
            asked_at=ASKED,
            replied_at=ASKED + timedelta(minutes=10) if replied else None,
            raw_reply=None,
            signals=None,
            checkin_date=DAY,
        )
    )


def _service(store: InMemoryGraphStore) -> PersonaViewService:
    return PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )


async def _cells(store: InMemoryGraphStore, today: date = DAY) -> dict[str, tuple[str, tuple]]:
    view = await _service(store).portfolio_heatmap("demo", DAY, "prog", today=today, names=NAMES)
    return {cell.entity_ref.id: (cell.reason or "", cell.reasons) for cell in view.cells}


async def _unanswered_day() -> InMemoryGraphStore:
    store = await _portfolio()
    for person in PEOPLE:
        await _status(store, person, StatusSource.INFERRED)
        await _asked(store, person, replied=False)
    return store


# ---- heat-cell reasons --------------------------------------------------------------------


async def test_every_cell_says_why_when_no_one_has_answered_today() -> None:
    cells = await _cells(await _unanswered_day())

    assert cells["pod-web"][0] == "3 of 3 unanswered today"
    assert cells["pod-web"][1] == (
        "3 of 3 haven't answered today's check-in: Ada Lind, Ben Okafor and Cleo Morales. "
        "Their statuses are inferred from Jira and GitLab until they reply.",
    )
    assert cells["pod-pay"][0] == "2 of 2 unanswered today"
    assert cells["proj-shop"][0] == "5 of 5 unanswered today"
    # A person in no team: their own check-in, said apart from the teams.
    assert cells[FAY] == (
        "Check-in unanswered today",
        (
            "Hasn't answered today's check-in, so the status is inferred from Jira and "
            "GitLab until there is a reply.",
            "In no pod, so no team colour counts this check-in.",
        ),
    )
    # A person's own cell never names them.
    assert "Ada Lind" not in " ".join(cells[ADA][1])


async def test_an_unknown_workstream_says_why_it_has_no_status() -> None:
    cells = await _cells(await _unanswered_day())

    assert cells["ws-cart"] == (
        "No tasks linked",
        (
            "No tasks are linked to this workstream, so nothing reports a status for it.",
            "Assigned to Web Pod, but none of its tickets is linked to it.",
        ),
    )
    assert cells["ws-pay"][0] == "No task reports health"


async def test_a_past_day_is_never_called_today() -> None:
    cells = await _cells(await _unanswered_day(), today=DAY + timedelta(days=1))

    assert cells["pod-web"][0] == "3 of 3 unanswered"
    assert cells["pod-web"][1][0].startswith("3 of 3 didn't answer the 9 Mar check-in")


async def test_a_blocker_and_a_partial_update_are_named_by_person_and_ticket() -> None:
    store = await _portfolio()
    for person in TEAM:
        await _asked(store, person, replied=True)
        await _status(
            store, person, StatusSource.PARTIAL if person == BEN else StatusSource.CONFIRMED
        )
    await store.record_developer_blockers(
        "demo",
        [
            DeveloperBlocker(
                tenant_id="demo",
                blocker_id="blk-form",
                developer_id=ADA,
                description="Waiting on the payment provider",
                normalized_key=normalize_blocker_key("Waiting on the payment provider"),
                work_item_id="SHOP-8",
                pod_id="pod-web",
                source=BlockerSource.CHECKIN,
                first_seen_on=DAY - timedelta(days=2),
                last_seen_on=DAY,
            )
        ],
    )

    cells = await _cells(store)
    view = await _service(store).portfolio_attention(
        "demo", DAY, "prog", None, today=DAY, zone=KOLKATA, names=NAMES
    )

    assert cells["pod-web"][0] == "Blocker on SHOP-8 (Ada)"
    assert cells["pod-web"][1] == (
        "Open blocker on SHOP-8 (Ada Lind).",
        "1 of 3 updates is partial (Ben Okafor): blockers or the ETA not confirmed.",
    )
    assert cells[ADA][0] == "Blocker on SHOP-8"
    assert cells["pod-pay"][0] == "All 2 confirmed"
    assert view.headline == "Amber: Ada Lind is blocked on SHOP-8."
    assert view.detail == "Also: 1 of 5 updates is partial, with blockers or ETAs open."
    assert [
        (signal.kind, signal.title, signal.link.kind, signal.link.id) for signal in view.signals
    ] == [
        ("blocker", "Ada Lind is blocked on SHOP-8 for 2 days", "pod", "pod-web"),
        (
            "partial",
            "Ben Okafor's update is partial: blockers or the ETA not confirmed",
            "pod",
            "pod-web",
        ),
    ]
    assert view.signals[0].age_days == 2


# ---- the headline --------------------------------------------------------------------------


async def test_the_headline_says_no_one_has_answered_with_counts_and_the_readers_clock() -> None:
    service = _service(await _unanswered_day())

    today = await service.portfolio_attention(
        "demo", DAY, "prog", None, today=DAY, zone=KOLKATA, names=NAMES
    )
    later = await service.portfolio_attention(
        "demo", DAY, "prog", None, today=DAY + timedelta(days=1), zone=KOLKATA, names=NAMES
    )
    untimed = await service.portfolio_attention(
        "demo", DAY, "prog", None, today=DAY, zone=KOLKATA, names=NAMES, clock=False
    )

    # The team members only: Fay is in no team and counts in no headline.
    assert today.headline == (
        "Amber: no one has answered today's check-in yet (0 of 5, asked 13:16 IST), "
        "so today's statuses are inferred from Jira and GitLab."
    )
    assert len(today.headline) <= 150
    assert today.checkins.people == 5 and today.checkins.answered == 0
    assert later.headline == (
        "Amber: no one answered the 9 Mar check-in (0 of 5, asked 13:16 IST), "
        "so that day's statuses were inferred from Jira and GitLab."
    )
    assert untimed.headline == (
        "Amber: no one has answered today's check-in yet (0 of 5), "
        "so today's statuses are inferred from Jira and GitLab."
    )
    assert [(signal.kind, signal.link.kind) for signal in today.signals] == [
        ("unanswered", "program")
    ]
    assert today.signals[0].title == (
        "No one has answered today's check-in yet (0 of 5, asked 13:16 IST)"
    )


async def test_some_unanswered_are_named_when_three_or_fewer() -> None:
    store = await _portfolio()
    for person in TEAM:
        silent = person in (BEN, DINA)
        await _asked(store, person, replied=not silent)
        await _status(store, person, StatusSource.INFERRED if silent else StatusSource.CONFIRMED)

    view = await _service(store).portfolio_attention(
        "demo", DAY, "prog", None, today=DAY, zone=UTC, names=NAMES
    )

    assert view.headline == (
        "Amber: 2 of 5 people haven't answered today's check-in (asked 07:46 UTC): "
        "Ben Okafor and Dina Shah; 2 statuses are inferred from Jira and GitLab."
    )


async def test_green_says_everyone_confirmed_and_nothing_needs_attention() -> None:
    store = await _portfolio()
    for person in TEAM:
        await _asked(store, person, replied=True)
        await _status(store, person, StatusSource.CONFIRMED)

    view = await _service(store).portfolio_attention(
        "demo", DAY, "prog", None, today=DAY, zone=UTC, names=NAMES
    )
    cells = await _cells(store)

    assert view.rag is Rag.GREEN
    assert view.headline == "Green: all 5 people confirmed with no open blockers."
    assert view.signals == ()
    assert cells["pod-web"][0] == "All 3 confirmed"


# ---- pure: red, findings and the signals' order -------------------------------------------


def _day(statuses: list[NodeStatus]) -> AttentionDay:
    graph = TeamGraph.from_graph(
        [
            Program(tenant_id="demo", id="prog", name="Commerce Program"),
            Pod(tenant_id="demo", id="pod-web", name="Web Pod"),
            Developer(tenant_id="demo", id=ADA, name="Ada Lind"),
            Developer(tenant_id="demo", id=BEN, name="Ben Okafor"),
            Task(tenant_id="demo", id="SHOP-8", name="Payment form", metadata={"key": "SHOP-8"}),
            Task(tenant_id="demo", id="SHOP-9", name="Refunds", metadata={"key": "SHOP-9"}),
        ],
        [
            _contains("prog", "pod-web"),
            _contains("pod-web", ADA),
            _contains("pod-web", BEN),
        ],
        DAY,
    )
    return AttentionDay.build(
        as_of=DAY, today=DAY, statuses=statuses, graph=graph, tracker="Jira", vcs="GitLab"
    )


def _ref(kind: NodeKind, node_id: str) -> EntityRef:
    return EntityRef(tenant_id="demo", kind=kind, id=node_id)


def _blocker(person: str, key: str) -> RollupFactor:
    return RollupFactor(
        description=f"Blocker: waiting on {key}.",
        contributes=Rag.AMBER,
        source_ref=_ref(NodeKind.TASK, key),
        kind=FactorKind.BLOCKER,
        blocker_id=f"blk-{person}-{key}",
        work_item_ref=_ref(NodeKind.TASK, key),
        applies_to_pod_ids=("pod-web",),
    )


def _status_of(kind: NodeKind, node_id: str, rag: Rag, *factors: RollupFactor) -> NodeStatus:
    return NodeStatus(
        entity_ref=_ref(kind, node_id),
        rag=rag,
        source=StatusSource.CONFIRMED,
        factors=factors,
        as_of=DAY,
    )


def test_two_open_blockers_make_a_red_headline_naming_both() -> None:
    ada, ben = _blocker(ADA, "SHOP-8"), _blocker(BEN, "SHOP-9")
    day = _day(
        [
            _status_of(NodeKind.DEVELOPER, ADA, Rag.AMBER, ada),
            _status_of(NodeKind.DEVELOPER, BEN, Rag.AMBER, ben),
            _status_of(NodeKind.POD, "pod-web", Rag.RED, ada, ben),
            _status_of(NodeKind.PROGRAM, "prog", Rag.RED, ada, ben),
        ]
    )

    view = attention_view(day, "prog")
    pod = cell_reasons(day, day.statuses[(NodeKind.POD, "pod-web")])

    assert view.headline == "Red: 2 open blockers: Ada Lind on SHOP-8 and Ben Okafor on SHOP-9."
    assert pod.reason == "2 blockers (Ada, Ben)"


def test_signals_rank_by_severity_then_age_and_stop_at_five() -> None:
    day = _day(
        [
            _status_of(NodeKind.DEVELOPER, ADA, Rag.GREEN),
            _status_of(NodeKind.POD, "pod-web", Rag.GREEN),
        ]
    )
    risks = [
        RiskFinding(
            tenant_id="demo",
            rule_id=RiskRuleId.PR_AGE,
            severity=Rag.RED if age > 6 else Rag.AMBER,
            entity_ref=_ref(NodeKind.TASK, "SHOP-8"),
            workstream_id=None,
            reason=f"Pull request open for {age} days.",
            evidence=RiskEvidence(identifier="web!4"),
            age_days=age,
            threshold_days=3,
            detected_at=datetime(2026, 3, 9, tzinfo=UTC),
            status=RiskFindingStatus.OPEN,
        )
        for age in (3, 9, 4, 5, 8)
    ]
    drift = [
        DriftFinding(
            tenant_id="demo",
            kind=DriftFindingKind.ETA_DISAGREEMENT,
            severity=Rag.AMBER,
            entity_ref=_ref(NodeKind.TASK, "SHOP-9"),
            workstream_id=None,
            reason="ETAs disagree for SHOP-9.",
            detected_at=datetime(2026, 3, 3, tzinfo=UTC),
        )
    ]

    view = attention_view(day, "prog", risks=risks, drift=drift)

    assert len(view.signals) == MAX_SIGNALS
    assert [(signal.severity, signal.age_days) for signal in view.signals] == [
        (Rag.RED, 9),
        (Rag.RED, 8),
        (Rag.AMBER, 6),
        (Rag.AMBER, 5),
        (Rag.AMBER, 4),
    ]
    assert view.signals[0].title == "A merge request for SHOP-8 has been open 9 days"
    assert view.signals[2].title == "The ETAs given for SHOP-9 do not overlap"
    assert all(signal.link.kind == "signals" for signal in view.signals)
    # Green tiles, but findings to act on: the second line names them.
    assert view.headline == "Green: all 2 people confirmed with no open blockers."
    assert view.detail is not None and view.detail.startswith(
        "Also: the ETAs given for SHOP-9 do not overlap"
    )


def test_nothing_reporting_is_never_green() -> None:
    day = _day([_status_of(NodeKind.POD, "pod-web", Rag.UNKNOWN)])

    view = attention_view(day, "prog")
    pod = cell_reasons(day, day.statuses[(NodeKind.POD, "pod-web")])

    assert view.rag is Rag.UNKNOWN
    assert view.headline == "No status yet: nothing in the program has reported for today."
    assert pod.reason == "No status reported yet"
