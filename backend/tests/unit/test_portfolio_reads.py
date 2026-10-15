"""The portfolio's bulk reads: Will we make the date?, Scope control and Early warnings.

One request each, from what is kept, for every project the reader reads; each
role reads what its project board would show it, and an executive no person.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from api.main import create_app
from config.settings import Settings
from core.domain.baseline import BaselineItem, BaselineKind, BaselineScopeKind, PlanningBaseline
from core.domain.delivery import DeliveryStage, RequirementsSnapshot
from core.domain.forecast import (
    CommitmentScope,
    CommitmentScopeKind,
    DateChange,
    ForecastDayRecord,
    Verdict,
)
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
)
from core.domain.hygiene import (
    HygieneFinding,
    HygieneSettings,
    HygieneState,
    RuleSeverity,
    default_examples,
)
from core.domain.impact import DayAttribution, Driver, Item, Shift, Step, attribution_payload
from core.domain.release_readiness import ScopeKind, ScopeRef
from core.domain.rollup import FactorKind, NodeStatus, Rag, RollupFactor
from core.domain.status import CheckIn, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.registry import ServiceRegistry

T = "demo"
AS_OF = date(2026, 6, 15)
NOW = datetime(2026, 6, 15, 8, 30, tzinfo=UTC)
#: Comet's crew: a pod of five, the smallest an executive reads on its own.
CREW = ("Cleo Dunn", "Ravi Hale", "Nia Ford", "Omar Vance", "Lena Park")
PEOPLE = ("Ada Quill", "Ben Marsh", "Sam Ortiz", *CREW)


def _ref(kind: NodeKind, node_id: str) -> EntityRef:
    return EntityRef(tenant_id=T, kind=kind, id=node_id)


def _contains(parent: str, child: str) -> GraphEdge:
    return GraphEdge(tenant_id=T, from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS)


def _scope(kind: CommitmentScopeKind, scope_id: str, project_id: str) -> CommitmentScope:
    return CommitmentScope(kind=kind, id=scope_id, project_id=project_id)


def _row(
    scope: CommitmentScope,
    verdict: Verdict,
    *,
    day: date = AS_OF,
    target: int | None = 20,
    p50: int | None = 25,
    p85: int | None = 40,
    team: int | None = 30,
    attribution: dict[str, object] | None = None,
    remaining: float = 5,
) -> ForecastDayRecord:
    def offset(days: int | None) -> date | None:
        return AS_OF + timedelta(days=days) if days is not None else None

    return ForecastDayRecord(
        tenant_id=T,
        scope=scope,
        day=day,
        p50=offset(p50),
        p85=offset(p85),
        verdict=verdict,
        target_date=offset(target),
        team_latest=offset(team),
        team_latest_key="ATL-2" if team is not None else None,
        remaining=remaining,
        unit="requirements",
        sample_days=12,
        completed_in_sample=6,
        min_sample_days=10,
        computed_at=NOW,
        attribution=attribution,
    )


def _added_on(day: date, keys: tuple[str, ...], share: float) -> dict[str, object]:
    return attribution_payload(
        DayAttribution(
            from_day=day - timedelta(days=1),
            to_day=day,
            unit="requirements",
            from_p50=AS_OF,
            from_p85=AS_OF,
            to_p50=AS_OF,
            to_p85=AS_OF,
            steps=(Step(driver=Driver.SCOPE_ADDED, shift=Shift(p50=share, p85=share * len(keys))),),
            added=tuple(Item(key=key, share=Shift(p50=share, p85=share)) for key in keys),
        )
    )


async def _seed(store: InMemoryGraphStore, registry: ServiceRegistry) -> None:
    nodes = [
        Program(tenant_id=T, id="prog-north", name="North Programme"),
        Program(tenant_id=T, id="prog-south", name="South Programme"),
        Project(tenant_id=T, id="p-atlas", name="Atlas", metadata={"jira_project_key": "ATL"}),
        Project(tenant_id=T, id="p-beacon", name="Beacon", metadata={"jira_project_key": "BEA"}),
        Project(tenant_id=T, id="p-comet", name="Comet"),
        Project(tenant_id=T, id="p-delta", name="Delta"),
        Pod(
            tenant_id=T,
            id="pod-a1",
            name="Atlas Core",
            metadata={"escalation_sm_member_id": "dev-sam"},
        ),
        Pod(tenant_id=T, id="pod-a2", name="Atlas Edge"),
        Pod(tenant_id=T, id="pod-b", name="Beacon Pod"),
        Pod(tenant_id=T, id="pod-c", name="Comet Crew"),
        *(
            Developer(tenant_id=T, id=f"dev-c{number}", name=name)
            for number, name in enumerate(CREW, start=1)
        ),
        Developer(tenant_id=T, id="dev-ada", name="Ada Quill"),
        Developer(tenant_id=T, id="dev-ben", name="Ben Marsh"),
        Developer(tenant_id=T, id="dev-sam", name="Sam Ortiz"),
        Developer(tenant_id=T, id="dev-elle", name="Elle Exec"),
        *(
            Task(
                tenant_id=T,
                id=key,
                name=f"Story {key}",
                metadata={"key": key, "labels": labels},
            )
            for key, labels in (
                ("ATL-1", ""),
                ("ATL-2", ""),
                ("ATL-3", "AGREED"),
                ("ATL-4", "ui"),
                ("ATL-5", ""),
                ("BEA-1", ""),
            )
        ),
    ]
    for node in nodes:
        await store.upsert_node(node)
    for parent, child in (
        ("prog-north", "p-atlas"),
        ("prog-north", "p-beacon"),
        ("prog-south", "p-comet"),
        ("prog-south", "p-delta"),
        ("p-atlas", "pod-a1"),
        ("p-atlas", "pod-a2"),
        ("p-beacon", "pod-b"),
        ("p-comet", "pod-c"),
        *(("pod-c", f"dev-c{number}") for number in range(1, len(CREW) + 1)),
        ("pod-a1", "dev-ada"),
        ("pod-a1", "dev-ben"),
        ("pod-a1", "dev-sam"),
        ("pod-b", "dev-sam"),
        ("pod-a1", "ATL-1"),
        ("pod-a1", "ATL-2"),
        ("pod-a1", "ATL-3"),
        ("pod-a2", "ATL-4"),
        ("pod-a2", "ATL-5"),
        ("pod-b", "BEA-1"),
    ):
        await store.add_edge(_contains(parent, child))

    atlas = _scope(CommitmentScopeKind.PROJECT, "p-atlas", "p-atlas")
    await registry.forecast_day_repository().save(
        [
            _row(
                atlas,
                Verdict.OFF_TRACK,
                day=AS_OF - timedelta(days=2),
                attribution=_added_on(AS_OF - timedelta(days=2), ("ATL-4", "ATL-5"), 3.0),
            ),
            _row(
                atlas,
                Verdict.OFF_TRACK,
                day=AS_OF - timedelta(days=1),
                attribution=_added_on(AS_OF - timedelta(days=1), ("ATL-3",), 2.5),
            ),
            _row(atlas, Verdict.OFF_TRACK, p50=30, p85=45),
            _row(_scope(CommitmentScopeKind.POD, "pod-a1", "p-atlas"), Verdict.AT_RISK),
            _row(_scope(CommitmentScopeKind.POD, "pod-a2", "p-atlas"), Verdict.OFF_TRACK),
            _row(
                _scope(CommitmentScopeKind.PROJECT, "p-beacon", "p-beacon"),
                Verdict.ON_TRACK,
                target=60,
                p85=30,
            ),
            _row(
                _scope(CommitmentScopeKind.PROJECT, "p-comet", "p-comet"),
                Verdict.AT_RISK,
                target=30,
                p50=25,
                p85=40,
            ),
            _row(
                _scope(CommitmentScopeKind.PROJECT, "p-delta", "p-delta"),
                Verdict.NO_DATE,
                target=None,
            ),
        ]
    )
    for days_ago, target in ((30, 10), (8, 20)):
        await registry.commitment_repository().append(
            DateChange(
                tenant_id=T,
                scope=atlas,
                target_date=AS_OF + timedelta(days=target),
                changed_at=NOW - timedelta(days=days_ago),
                changed_by="dev-ada",
            )
        )
    stages = {
        "ATL-1": DeliveryStage.PRODUCTION,
        "ATL-2": DeliveryStage.IN_DEVELOPMENT,
        "ATL-3": DeliveryStage.IN_DEVELOPMENT,
        "ATL-4": DeliveryStage.PRODUCTION,
        "ATL-5": DeliveryStage.RAISED,
    }
    await registry.requirements_snapshot_repository().save(
        RequirementsSnapshot(
            tenant_id=T,
            project_id="p-atlas",
            day=AS_OF,
            stage_counts={stage: list(stages.values()).count(stage) for stage in DeliveryStage},
            stage_points={},
            has_points=False,
            excluded=0,
            unmapped_statuses=(),
            items=stages,
            computed_at=NOW,
        )
    )
    await registry.planning_baseline_repository().add(
        PlanningBaseline(
            tenant_id=T,
            baseline_id="bl-1",
            scope_kind=BaselineScopeKind.PROJECT,
            scope_id="p-atlas",
            project_id="p-atlas",
            kind=BaselineKind.DATE,
            planned_on=AS_OF - timedelta(days=20),
            items={"ATL-1": BaselineItem(stage=None), "ATL-2": BaselineItem(stage=None)},
            reconstructed=False,
            snapshot_day=None,
            # The tenant's own change label, never a default spelled in code.
            change_label="AGREED",
            grace_working_days=2,
            set_by="dev-ada",
            set_at=NOW - timedelta(days=20),
        )
    )
    blocker = RollupFactor(
        description="Blocker: waiting on Ben Marsh for the schema",
        contributes=Rag.RED,
        source_ref=_ref(NodeKind.TASK, "ATL-2"),
        kind=FactorKind.BLOCKER,
        blocker_id="b-1",
        work_item_ref=_ref(NodeKind.TASK, "ATL-2"),
    )
    await store.record_node_status(
        NodeStatus(
            entity_ref=_ref(NodeKind.PROJECT, "p-atlas"),
            rag=Rag.RED,
            source=StatusSource.CONFIRMED,
            factors=(blocker,),
            as_of=AS_OF,
        )
    )
    for person, replied in (("dev-ada", True), ("dev-ben", False)):
        await store.record_checkin(
            CheckIn(
                tenant_id=T,
                developer_id=person,
                correlation_id=f"checkin-{person}",
                asked_at=NOW,
                replied_at=NOW + timedelta(minutes=5) if replied else None,
                raw_reply=None,
                signals=None,
            )
        )
    hygiene = registry.hygiene_repository()
    await hygiene.save_settings(HygieneSettings(tenant_id=T, enabled=True))
    rule = replace(default_examples(T)[0], severity=RuleSeverity.ASK)
    await hygiene.save_rule(rule)
    await hygiene.save_finding(
        HygieneFinding(
            tenant_id=T,
            finding_id="f-1",
            rule_id=rule.rule_id,
            rule_version=rule.version,
            issue_key="ATL-4",
            scope=ScopeRef(kind=ScopeKind.PROJECT, id="p-atlas"),
            project_id="p-atlas",
            state=HygieneState.OPEN,
            owner_member_id="dev-ben",
            detail={},
            fingerprint="fp-1",
            held=False,
            first_seen_at=NOW - timedelta(days=3),
            last_seen_at=NOW,
        )
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    demo = settings.model_copy(update={"demo_mode": True, "dev_principal_subject": "dev-elle"})
    store = InMemoryGraphStore()
    registry = ServiceRegistry(demo, graph_store=store)
    asyncio.run(_seed(store, registry))
    with TestClient(create_app(settings=demo, registry=registry)) as test_client:
        yield test_client


def _as(client: TestClient, path: str, roles: str, subject: str = "dev-elle") -> Response:
    return client.get(
        path,
        headers={"x-openprogram-dev-user": subject, "x-openprogram-dev-roles": roles},
    )


def _named(body: object) -> list[str]:
    text = json.dumps(body)
    return [name for name in PEOPLE if re.search(rf"\b{re.escape(name)}\b", text)]


# ---- Will we make the date? ------------------------------------------------------------


def test_delivery_lists_every_project_worst_first_from_the_kept_rows(client: TestClient) -> None:
    response = _as(client, f"/portfolio/delivery?as_of={AS_OF}", "mgr")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [project["name"] for project in body["projects"]] == [
        "Atlas",
        "Comet",
        "Delta",
        "Beacon",
    ]
    assert body["counts"]["off_track"] == 1 and body["counts"]["on_track"] == 1
    assert body["total"] == 4
    assert body["computed_at"].startswith("2026-06-15T08:30")
    assert [program["name"] for program in body["programs"]] == [
        "North Programme",
        "South Programme",
    ]

    atlas = body["projects"][0]
    assert atlas["verdict"] == "off_track"
    assert atlas["target_source"] == "committed"
    assert (atlas["target"], atlas["p50"], atlas["p85"]) == (
        "2026-07-05",
        "2026-07-15",
        "2026-07-30",
    )
    assert atlas["days_late"] == 19
    # Three added after planning, two without the tenant's label; the kept days say
    # they moved the 85% date 3 + 3 + 2.5 working days.
    assert atlas["plan"]["added"] == 3 and atlas["plan"]["without_label"] == 2
    assert atlas["plan"]["change_label"] == "AGREED"
    assert atlas["reason"] == (
        "Off track because 3 requirements were added after planning (26 May), "
        "2 without the AGREED label, pushing it back 8 working days."
    )
    # Counted, never named: of the three in its pods, two were asked and one replied.
    assert atlas["health"] == "1 open blocker, 1 of 2 not replied"
    assert (atlas["done"], atlas["total"]) == (2, 5)
    pods = {pod["name"]: (pod["done"], pod["total"], pod["verdict"]) for pod in atlas["pods"]}
    assert pods == {"Atlas Core": (1, 3, "at_risk"), "Atlas Edge": (1, 2, "off_track")}
    kinds = [(mark["kind"], mark["pushed"]) for mark in atlas["marks"]]
    assert ("scope_added", True) in kinds and ("committed_date_changed", False) in kinds
    assert all(mark["working_days"] for mark in atlas["marks"] if mark["kind"] == "scope_added")


def test_delivery_search_filters_sort_and_pages(client: TestClient) -> None:
    late = _as(client, f"/portfolio/delivery?as_of={AS_OF}&verdict=late&verdict=no_date", "mgr")
    searched = _as(client, f"/portfolio/delivery?as_of={AS_OF}&q=bea", "mgr")
    by_key = _as(client, f"/portfolio/delivery?as_of={AS_OF}&q=ATL", "mgr")
    south = _as(client, f"/portfolio/delivery?as_of={AS_OF}&program_id=prog-south", "mgr")
    paged = _as(client, f"/portfolio/delivery?as_of={AS_OF}&offset=1&limit=2", "mgr")
    named = _as(client, f"/portfolio/delivery?as_of={AS_OF}&sort=name", "mgr")

    assert [p["name"] for p in late.json()["projects"]] == ["Atlas", "Delta"]
    # A tile counts what the search matches, whatever the verdict filter keeps.
    assert late.json()["counts"]["on_track"] == 1 and late.json()["total"] == 2
    assert [p["name"] for p in searched.json()["projects"]] == ["Beacon"]
    assert [p["name"] for p in by_key.json()["projects"]] == ["Atlas"]
    assert [p["name"] for p in south.json()["projects"]] == ["Comet", "Delta"]
    assert [p["name"] for p in paged.json()["projects"]] == ["Comet", "Delta"]
    assert paged.json()["total"] == 4 and paged.json()["offset"] == 1
    assert [p["name"] for p in named.json()["projects"]] == ["Atlas", "Beacon", "Comet", "Delta"]


def test_delivery_follows_each_roles_project_reads(client: TestClient) -> None:
    executive = _as(client, f"/portfolio/delivery?as_of={AS_OF}", "exec")
    owner = _as(client, f"/portfolio/delivery?as_of={AS_OF}", "po")
    scrum_master = _as(client, f"/portfolio/delivery?as_of={AS_OF}", "sm", subject="dev-sam")
    developer = _as(client, f"/portfolio/delivery?as_of={AS_OF}", "dev", subject="dev-ada")

    assert executive.status_code == 200 and executive.json()["total"] == 4
    assert _named(executive.json()) == []
    assert owner.json()["total"] == 4
    # A scrum master reads the projects their pods work on, and the counts are over those.
    assert [p["name"] for p in scrum_master.json()["projects"]] == ["Atlas", "Beacon"]
    assert sum(scrum_master.json()["counts"].values()) == 2
    assert developer.status_code == 403


def test_delivery_reads_sixty_projects_in_one_request_without_a_forecast(
    settings: Settings,
) -> None:
    demo = settings.model_copy(update={"demo_mode": True})
    store = InMemoryGraphStore()
    registry = ServiceRegistry(demo, graph_store=store)
    verdicts = (Verdict.OFF_TRACK, Verdict.AT_RISK, Verdict.ON_TRACK, Verdict.NO_DATE)

    async def seed() -> None:
        await store.upsert_node(Program(tenant_id=T, id="prog", name="Programme"))
        rows: list[ForecastDayRecord] = []
        for number in range(60):
            project_id = f"p-{number:02d}"
            await store.upsert_node(
                Project(tenant_id=T, id=project_id, name=f"Project {number:02d}")
            )
            await store.add_edge(_contains("prog", project_id))
            for pod in range(3):
                pod_id = f"{project_id}-pod-{pod}"
                await store.upsert_node(Pod(tenant_id=T, id=pod_id, name=f"Pod {pod}"))
                await store.add_edge(_contains(project_id, pod_id))
                rows.append(
                    _row(_scope(CommitmentScopeKind.POD, pod_id, project_id), Verdict.AT_RISK)
                )
            scope = _scope(CommitmentScopeKind.PROJECT, project_id, project_id)
            for back in range(30):
                day = AS_OF - timedelta(days=back)
                rows.append(
                    _row(
                        scope,
                        verdicts[number % 4],
                        day=day,
                        p85=40 + number % 7,
                        attribution=_added_on(day, (f"K-{number}-{back}",), 0.5),
                    )
                )
        await registry.forecast_day_repository().save(rows)

    asyncio.run(seed())
    service_calls: list[str] = []
    forecasts = registry.forecast_service()
    original = forecasts.project_delivery

    async def counted(*args: object, **kwargs: object) -> object:
        service_calls.append("project_delivery")
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    forecasts.project_delivery = counted  # type: ignore[method-assign]
    with TestClient(create_app(settings=demo, registry=registry)) as client:
        started = time.perf_counter()
        first = _as(client, f"/portfolio/delivery?as_of={AS_OF}&limit=50", "mgr")
        took = time.perf_counter() - started
        rest = _as(client, f"/portfolio/delivery?as_of={AS_OF}&limit=50&offset=50", "mgr")

    assert first.status_code == 200, first.text
    body = first.json()
    assert body["total"] == 60 and len(body["projects"]) == 50
    assert len(rest.json()["projects"]) == 10
    names = [p["name"] for p in body["projects"]] + [p["name"] for p in rest.json()["projects"]]
    assert len(set(names)) == 60
    assert sum(body["counts"].values()) == 60
    # Worst first across the pages: every off track project before any on track one.
    verdict_order = [p["verdict"] for p in body["projects"]]
    assert verdict_order.index("on_track") > max(
        i for i, value in enumerate(verdict_order) if value == "off_track"
    )
    assert all(len(project["pods"]) == 3 for project in body["projects"])
    assert service_calls == []
    assert took < 3.0, f"60 projects took {took:.2f}s"


# ---- Scope control ---------------------------------------------------------------------


def test_scope_lists_plan_and_hygiene_with_owner_for_a_manager(client: TestClient) -> None:
    response = _as(client, f"/portfolio/scope?as_of={AS_OF}", "mgr")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reads_baselines"] and body["hygiene_enabled"] and not body["counts_only"]
    atlas = next(project for project in body["projects"] if project["id"] == "p-atlas")
    assert body["projects"][0]["id"] == "p-atlas"
    assert atlas["plan"]["planned"] == 2 and atlas["plan"]["added"] == 3
    assert atlas["plan"]["without_label"] == 2 and atlas["plan"]["without_label_percent"] == 67
    assert atlas["plan"]["change_label"] == "AGREED"
    assert atlas["added_keys"] == ["ATL-3", "ATL-4", "ATL-5"]
    assert atlas["without_label_keys"] == ["ATL-4", "ATL-5"]
    assert atlas["hygiene_open"] == 1
    (finding,) = body["findings"]
    assert finding["issue_key"] == "ATL-4" and finding["owner_name"] == "Ben Marsh"
    assert finding["owner_team"] == "Atlas Edge"
    assert finding["found_on"] == "2026-06-12" and finding["open_days"] == 3
    # The rule asks its owner in the day report: there is nothing to send from here.
    assert finding["asks"] is True
    assert body["rules"][0]["open"] == 1


def test_scope_gives_an_executive_keys_and_counts_never_who(client: TestClient) -> None:
    executive = _as(client, f"/portfolio/scope?as_of={AS_OF}", "exec").json()
    scrum_master = _as(client, f"/portfolio/scope?as_of={AS_OF}", "sm", subject="dev-sam")
    developer = _as(client, f"/portfolio/scope?as_of={AS_OF}", "dev", subject="dev-ada")

    assert executive["counts_only"] and executive["findings"] == []
    assert executive["findings_total"] == 1 and executive["rules"][0]["open"] == 1
    atlas = next(project for project in executive["projects"] if project["id"] == "p-atlas")
    assert atlas["added_keys"] == ["ATL-3", "ATL-4", "ATL-5"]
    assert _named(executive) == []
    # A scrum master reads no baselines, and the hygiene of their own projects.
    sm = scrum_master.json()
    assert not sm["reads_baselines"]
    assert {project["id"] for project in sm["projects"]} == {"p-atlas", "p-beacon"}
    assert all(project["plan"] is None for project in sm["projects"])
    assert [finding["issue_key"] for finding in sm["findings"]] == ["ATL-4"]
    assert developer.status_code == 403


# ---- Early warnings --------------------------------------------------------------------


def test_warnings_group_by_kind_and_list_blockers_to_their_readers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.application.blocker_resolution import (
        BlockerProvenance,
        BlockerResolutionService,
        ResolvedBlocker,
    )

    blocker = ResolvedBlocker(
        blocker_id="b-1",
        description="Waiting on the payment sandbox",
        developer_id="dev-ada",
        first_seen_on=AS_OF - timedelta(days=6),
        work_item_ref=_ref(NodeKind.TASK, "ATL-2"),
        work_item_name="Story ATL-2",
        explicit_pod_ref=None,
        pod_ids=("pod-a1",),
        unattributed=False,
        critical=True,
        provenance=BlockerProvenance.LIFECYCLE,
    )

    async def blockers_for(
        self: BlockerResolutionService, tenant_id: str, developer_id: str, as_of: date
    ) -> tuple[ResolvedBlocker, ...]:
        return (blocker,) if developer_id == "dev-ada" else ()

    monkeypatch.setattr(BlockerResolutionService, "open_blockers_for_developer", blockers_for)

    manager = _as(client, f"/portfolio/warnings?as_of={AS_OF}", "mgr")
    executive = _as(client, f"/portfolio/warnings?as_of={AS_OF}", "exec")
    developer = _as(client, f"/portfolio/warnings?as_of={AS_OF}", "dev", subject="dev-ada")

    assert manager.status_code == 200, manager.text
    groups = {group["kind"]: group for group in manager.json()["groups"]}
    assert list(groups) == [
        "blockers",
        "tools_disagree",
        "done_in_code",
        "review_stuck",
        "no_movement",
        "no_code_yet",
    ]
    (item,) = groups["blockers"]["items"]
    assert groups["blockers"]["count"] == 1
    assert (item["key"], item["age_days"], item["severity"]) == ("ATL-2", 6, "red")
    # Within the team, its scrum master resolves it.
    assert item["owner"] == "Sam Ortiz" and item["owner_team"] == "Atlas Core"

    exec_groups = {group["kind"]: group for group in executive.json()["groups"]}
    assert exec_groups["blockers"]["count"] == 1
    assert exec_groups["blockers"]["counts_only"] and exec_groups["blockers"]["items"] == []
    assert _named(executive.json()) == []
    assert developer.status_code == 403


# ---- One pod's part ---------------------------------------------------------------------


def _findings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blocker in Atlas Core, a stale issue in Atlas Edge and a merged issue in Atlas Core."""
    from core.application.blocker_resolution import (
        BlockerProvenance,
        BlockerResolutionService,
        ResolvedBlocker,
    )
    from core.application.risk_service import RiskService
    from core.domain.risk import (
        DriftFinding,
        DriftFindingKind,
        RiskEvidence,
        RiskFinding,
        RiskFindingStatus,
        RiskRuleId,
    )

    blocker = ResolvedBlocker(
        blocker_id="b-1",
        description="Waiting on the payment sandbox",
        developer_id="dev-ada",
        first_seen_on=AS_OF - timedelta(days=6),
        work_item_ref=_ref(NodeKind.TASK, "ATL-2"),
        work_item_name="Story ATL-2",
        explicit_pod_ref=None,
        pod_ids=("pod-a1",),
        unattributed=False,
        critical=True,
        provenance=BlockerProvenance.LIFECYCLE,
    )
    stale = RiskFinding(
        tenant_id=T,
        rule_id=RiskRuleId.STALE_WORK_ITEM,
        severity=Rag.AMBER,
        entity_ref=_ref(NodeKind.TASK, "ATL-4"),
        workstream_id=None,
        reason="ATL-4 has not moved for 9 days.",
        evidence=RiskEvidence(identifier="ATL-4"),
        age_days=9,
        threshold_days=5,
        detected_at=NOW - timedelta(days=2),
        status=RiskFindingStatus.OPEN,
        owner_id="dev-ben",
        # A summary stored before it named people still holds a member id.
        owner_status_summary="On track, pairing with dev-ada on the last screen.",
        owner_status_source=StatusSource.CONFIRMED,
        owner_status_as_of=AS_OF,
    )
    merged = DriftFinding(
        tenant_id=T,
        kind=DriftFindingKind.MERGED_ISSUE_OPEN,
        severity=Rag.AMBER,
        entity_ref=_ref(NodeKind.TASK, "ATL-1"),
        workstream_id=None,
        reason="merged, still open",
        detected_at=NOW - timedelta(days=1),
        owner_id="dev-ada",
    )

    async def blockers_for(
        self: BlockerResolutionService, tenant_id: str, developer_id: str, as_of: date
    ) -> tuple[ResolvedBlocker, ...]:
        return (blocker,) if developer_id == "dev-ada" else ()

    async def risks(
        self: RiskService, tenant_id: str, as_of: date
    ) -> dict[str | None, list[RiskFinding]]:
        return {"p-atlas": [stale]}

    async def drift(
        self: RiskService, tenant_id: str, project_ids: list[str], as_of: date
    ) -> dict[str, list[DriftFinding]]:
        return {
            project_id: [merged] if project_id == "p-atlas" else [] for project_id in project_ids
        }

    monkeypatch.setattr(BlockerResolutionService, "open_blockers_for_developer", blockers_for)
    monkeypatch.setattr(RiskService, "risks_by_project", risks)
    monkeypatch.setattr(RiskService, "drift_by_project", drift)


def _items(body: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    groups = body["groups"]
    assert isinstance(groups, list)
    return {group["kind"]: group["items"] for group in groups}


def _counts(body: dict[str, object]) -> dict[str, int]:
    groups = body["groups"]
    assert isinstance(groups, list)
    return {group["kind"]: group["count"] for group in groups}


def test_warnings_name_each_items_pods_and_what_its_owner_says(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _findings(monkeypatch)

    manager = _as(client, f"/portfolio/warnings?as_of={AS_OF}", "mgr")
    owner = _as(client, f"/portfolio/warnings?as_of={AS_OF}", "po")
    executive = _as(client, f"/portfolio/warnings?as_of={AS_OF}", "exec")

    assert manager.status_code == 200, manager.text
    items = _items(manager.json())
    (stale,) = items["no_movement"]
    (merged,) = items["done_in_code"]
    (blocker,) = items["blockers"]
    # The issue's pod, else its owner's; a blocker its own.
    assert stale["pods"] == [{"id": "pod-a2", "name": "Atlas Edge"}]
    assert merged["pods"] == [{"id": "pod-a1", "name": "Atlas Core"}]
    assert blocker["pods"] == [{"id": "pod-a1", "name": "Atlas Core"}]
    # The owner's own words, with the member id said as the name, and their source.
    assert stale["owner"] == "Ben Marsh"
    assert stale["owner_says"] == {
        "text": "On track, pairing with Ada Quill on the last screen.",
        "source": "confirmed",
        "on": "2026-06-15",
    }
    # A reader who sees the team instead reads none of the owner's words.
    for body in (owner.json(), executive.json()):
        for found in _items(body).values():
            assert all(item["owner"] is None and item["owner_says"] is None for item in found)
    assert _named(executive.json()) == []


def test_warnings_keep_one_pods_part(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _findings(monkeypatch)

    core = _as(client, f"/portfolio/warnings?as_of={AS_OF}&pod_id=pod-a1", "mgr")
    edge = _as(client, f"/portfolio/warnings?as_of={AS_OF}&project_id=p-atlas&pod_id=pod-a2", "mgr")
    elsewhere = _as(
        client, f"/portfolio/warnings?as_of={AS_OF}&project_id=p-beacon&pod_id=pod-a1", "mgr"
    )
    unknown = _as(client, f"/portfolio/warnings?as_of={AS_OF}&pod_id=pod-x", "mgr")

    assert core.status_code == 200, core.text
    assert core.json()["projects"] == 1
    assert {kind: count for kind, count in _counts(core.json()).items() if count} == {
        "blockers": 1,
        "done_in_code": 1,
    }
    assert {kind: count for kind, count in _counts(edge.json()).items() if count} == {
        "no_movement": 1
    }
    assert edge.json()["total"] == 1
    assert elsewhere.status_code == 404
    assert elsewhere.json()["detail"] == "Atlas Core doesn't work on Beacon."
    assert unknown.status_code == 404


def test_a_pods_part_keeps_the_small_group_rule_and_a_developers_own_pod(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _findings(monkeypatch)

    small = _as(client, f"/portfolio/warnings?as_of={AS_OF}&pod_id=pod-a1", "exec")
    crew = _as(client, f"/portfolio/warnings?as_of={AS_OF}&pod_id=pod-c", "exec")
    small_scope = _as(client, f"/portfolio/scope?as_of={AS_OF}&pod_id=pod-a1", "exec")
    own = _as(client, f"/portfolio/warnings?as_of={AS_OF}&pod_id=pod-a1", "dev", subject="dev-ada")
    other = _as(
        client, f"/portfolio/warnings?as_of={AS_OF}&pod_id=pod-a2", "dev", subject="dev-ada"
    )
    whole = _as(client, f"/portfolio/warnings?as_of={AS_OF}", "dev", subject="dev-ada")
    elsewhere = _as(
        client,
        f"/portfolio/warnings?as_of={AS_OF}&project_id=p-beacon&pod_id=pod-a1",
        "dev",
        subject="dev-ada",
    )
    dev_scope = _as(client, f"/portfolio/scope?as_of={AS_OF}&pod_id=pod-a1", "dev", "dev-ada")

    # Fewer than five people: an executive reads it as part of its project.
    assert small.status_code == 403
    assert small.json()["detail"] == "A pod of fewer than 5 people is read as part of its project."
    assert small_scope.status_code == 403
    assert crew.status_code == 200 and crew.json()["projects"] == 1
    assert _named(crew.json()) == []
    # A developer reads their own pod's part, in counts and teams, and no other.
    assert own.status_code == 200, own.text
    body = own.json()
    assert body["projects"] == 1
    blockers = next(group for group in body["groups"] if group["kind"] == "blockers")
    assert blockers["count"] == 1 and blockers["counts_only"] and blockers["items"] == []
    (merged,) = _items(body)["done_in_code"]
    assert merged["owner"] is None and merged["owner_is_you"] and merged["owner_says"] is None
    assert merged["owner_team"] == "Atlas Core"
    assert _named(body) == []
    assert other.status_code == 403 and whole.status_code == 403
    assert elsewhere.status_code == 404
    assert dev_scope.status_code == 403


def test_delivery_gives_an_executive_no_row_for_a_pod_under_five(client: TestClient) -> None:
    executive = _as(client, f"/portfolio/delivery?as_of={AS_OF}", "exec").json()
    manager = _as(client, f"/portfolio/delivery?as_of={AS_OF}", "mgr").json()

    def pods(body: dict[str, object], project_id: str) -> list[str]:
        projects = body["projects"]
        assert isinstance(projects, list)
        found = next(project for project in projects if project["id"] == project_id)
        return [pod["name"] for pod in found["pods"]]

    assert pods(manager, "p-atlas") == ["Atlas Core", "Atlas Edge"]
    assert pods(executive, "p-atlas") == []
    assert pods(executive, "p-comet") == ["Comet Crew"]
    atlas = next(project for project in executive["projects"] if project["id"] == "p-atlas")
    # Three people: no reply is counted for an executive, the work items still are.
    assert atlas["health"] == "1 open blocker"
    mgr_atlas = next(project for project in manager["projects"] if project["id"] == "p-atlas")
    assert mgr_atlas["health"] == "1 open blocker, 1 of 2 not replied"


def test_scope_reads_one_pods_part(client: TestClient) -> None:
    edge = _as(client, f"/portfolio/scope?as_of={AS_OF}&pod_id=pod-a2", "mgr")
    core = _as(client, f"/portfolio/scope?as_of={AS_OF}&project_id=p-atlas&pod_id=pod-a1", "mgr")
    elsewhere = _as(
        client, f"/portfolio/scope?as_of={AS_OF}&project_id=p-beacon&pod_id=pod-a1", "mgr"
    )

    assert edge.status_code == 200, edge.text
    (atlas,) = edge.json()["projects"]
    # Nothing of the plan was the edge's; both its requirements joined after, unlabelled.
    assert atlas["plan"]["planned"] == 0 and atlas["plan"]["added"] == 2
    assert atlas["plan"]["without_label"] == 2
    assert atlas["added_keys"] == ["ATL-4", "ATL-5"]
    assert atlas["hygiene_open"] == 1
    assert [finding["issue_key"] for finding in edge.json()["findings"]] == ["ATL-4"]
    (atlas,) = core.json()["projects"]
    assert atlas["plan"]["planned"] == 2 and atlas["plan"]["added"] == 1
    assert atlas["plan"]["without_label"] == 0 and atlas["added_keys"] == ["ATL-3"]
    assert atlas["hygiene_open"] == 0 and core.json()["findings"] == []
    assert core.json()["rules"][0]["open"] == 0
    assert elsewhere.status_code == 404


def test_requirements_and_gates_read_one_pods_part(client: TestClient) -> None:
    whole = _as(client, f"/projects/p-atlas/requirements?as_of={AS_OF}", "mgr")
    core = _as(client, f"/projects/p-atlas/requirements?as_of={AS_OF}&pod_id=pod-a1", "mgr")
    edge = _as(client, f"/projects/p-atlas/requirements?as_of={AS_OF}&pod_id=pod-a2", "mgr")
    elsewhere = _as(client, f"/projects/p-atlas/requirements?as_of={AS_OF}&pod_id=pod-b", "mgr")
    small = _as(client, f"/projects/p-atlas/requirements?as_of={AS_OF}&pod_id=pod-a1", "exec")
    crew = _as(client, f"/projects/p-comet/requirements?as_of={AS_OF}&pod_id=pod-c", "exec")
    board = _as(client, f"/projects/p-atlas/gates?as_of={AS_OF}&pod_id=pod-a2", "mgr")
    small_board = _as(client, f"/projects/p-atlas/gates?as_of={AS_OF}&pod_id=pod-a1", "exec")
    developer = _as(
        client, f"/projects/p-atlas/requirements?as_of={AS_OF}&pod_id=pod-a1", "dev", "dev-ada"
    )

    assert (whole.json()["total"], whole.json()["done"], whole.json()["pod_id"]) == (5, 2, None)
    assert core.status_code == 200, core.text
    body = core.json()
    assert (body["pod_id"], body["pod_name"]) == ("pod-a1", "Atlas Core")
    assert (body["total"], body["done"]) == (3, 1)
    assert sorted(item["key"] for item in body["requirements"]) == ["ATL-1", "ATL-2", "ATL-3"]
    counts = {stage["stage"]: stage["count"] for stage in body["stages"]}
    assert counts["in_development"] == 2 and counts["production"] == 1
    assert (edge.json()["total"], edge.json()["done"]) == (2, 1)
    assert elsewhere.status_code == 404
    assert elsewhere.json()["detail"] == "Beacon Pod doesn't work on Atlas."
    assert small.status_code == 403
    assert crew.status_code == 200 and crew.json()["total"] == 0
    assert board.status_code == 200, board.text
    assert board.json()["pod_id"] == "pod-a2"
    assert [issue["key"] for issue in board.json()["issues"]] == ["ATL-4", "ATL-5"]
    assert small_board.status_code == 403
    # The readers stay as they are: a developer reads no project's requirements.
    assert developer.status_code == 403
