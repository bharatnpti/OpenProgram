"""The tasks a pod's Delivery panel lists, and who may read them."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.persona_views import PersonaViewService
from core.domain.blockers import BlockerSource, DeveloperBlocker, normalize_blocker_key
from core.domain.errors import GraphNotFound
from core.domain.graph import (
    Developer,
    EdgeKind,
    FactEvent,
    GraphEdge,
    GraphNode,
    Pod,
    Project,
    Task,
    WorkItem,
    Workstream,
)
from core.domain.rollup import Rag
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.fixtures.demo_graph import populate_demo_graph

AS_OF = date(2026, 1, 10)


async def test_pod_tasks_list_members_work_blocked_first() -> None:
    store = InMemoryGraphStore()
    pod = Pod(tenant_id="demo", id="pod-a", name="Payments Pod")
    asha = Developer(tenant_id="demo", id="dev-asha", name="Asha")
    ben = Developer(tenant_id="demo", id="dev-ben", name="Ben")
    outsider = Developer(tenant_id="demo", id="dev-out", name="Outsider")
    workstream = Workstream(tenant_id="demo", id="ws-a", name="Payments API")
    await _add(store, pod, asha, ben, outsider, workstream)
    await _edge(store, pod.id, asha.id)
    await _edge(store, pod.id, ben.id)
    await _edge(store, pod.id, workstream.id, kind=EdgeKind.ASSIGNED_TO)
    tasks = {
        "task-green": ("Capture runbook", ben, "done"),
        "task-unknown": ("Refund report", ben, None),
        "task-amber": ("Retry policy", asha, "at-risk"),
        "task-amber-blocked": ("Step-up flow", asha, "at-risk"),
        "task-red": ("Refund edge cases", ben, "blocked"),
        "task-theirs": ("Someone else's", outsider, "blocked"),
    }
    for task_id, (name, owner, status) in tasks.items():
        work_item = WorkItem(tenant_id="demo", id=f"WI-{task_id}", name=name)
        metadata = {"status": status} if status else {}
        task = Task(tenant_id="demo", id=task_id, name=name, metadata=metadata)
        await _add(store, work_item, task)
        await _edge(store, workstream.id, work_item.id)
        await _edge(store, work_item.id, task_id)
        await _edge(store, owner.id, task_id, kind=EdgeKind.ASSIGNED_TO)
    # Both owners take the amber task; the latest fact sets its status and source.
    await _edge(store, ben.id, "task-amber", kind=EdgeKind.ASSIGNED_TO)
    await store.append_fact(_fact("task-amber", {"status": "amber", "confidence": 0.6}))
    # Raised against the work item the task implements, not the task itself.
    await store.record_developer_status_with_blockers(
        _status(asha.id),
        (
            _blocker(
                asha.id,
                "sandbox credentials missing",
                work_item_id="WI-task-amber-blocked",
                pod_id=pod.id,
                first_seen_on=AS_OF - timedelta(days=4),
            ),
        ),
    )

    view = await _service(store).pod_tasks("demo", pod.id, AS_OF)

    assert view.pod_name == "Payments Pod"
    # Blocked first (red, then a blocker), then worst first; silence before green.
    assert [task.id for task in view.tasks] == [
        "task-red",
        "task-amber-blocked",
        "task-amber",
        "task-unknown",
        "task-green",
    ]
    by_id = {task.id: task for task in view.tasks}
    assert by_id["task-red"].blocked is True
    assert by_id["task-red"].open_blockers == ()
    blocked = by_id["task-amber-blocked"]
    assert blocked.blocked is True
    assert [(item.description, item.age_days) for item in blocked.open_blockers] == [
        ("sandbox credentials missing", 4)
    ]
    amber = by_id["task-amber"]
    assert amber.blocked is False
    assert [owner.name for owner in amber.owners] == ["Asha", "Ben"]
    assert (amber.rag, amber.source, amber.confidence) == (Rag.AMBER, StatusSource.INFERRED, 0.6)
    assert by_id["task-unknown"].rag is Rag.UNKNOWN


async def test_pod_tasks_never_absorb_another_pods_work_through_a_shared_member() -> None:
    """A member in two pods brings both pods' tasks into each pod's tree.

    From `pod-a`, `dev-shared --assigned_to--> task-b` is reachable while
    `ws-b > WI-b > task-b` is not, so the tree alone reads `task-b` as
    unclaimed. Its owner is `ws-b`, which only `pod-b` serves.
    """
    store = InMemoryGraphStore()
    shared = Developer(tenant_id="demo", id="dev-shared", name="Sam")
    await _add(store, shared)
    for suffix in ("a", "b"):
        pod = Pod(tenant_id="demo", id=f"pod-{suffix}", name=f"Pod {suffix.upper()}")
        workstream = Workstream(tenant_id="demo", id=f"ws-{suffix}", name=f"Stream {suffix}")
        work_item = WorkItem(tenant_id="demo", id=f"WI-{suffix}", name=f"Item {suffix}")
        task = Task(tenant_id="demo", id=f"task-{suffix}", name=f"Task {suffix}")
        await _add(store, pod, workstream, work_item, task)
        await _edge(store, pod.id, shared.id)
        await _edge(store, pod.id, workstream.id, kind=EdgeKind.ASSIGNED_TO)
        await _edge(store, workstream.id, work_item.id)
        await _edge(store, work_item.id, task.id)
        await _edge(store, shared.id, task.id, kind=EdgeKind.ASSIGNED_TO)
    # Held by pod-a outright, so it is pod-a's and no one else's.
    own = Task(tenant_id="demo", id="task-own", name="Pod A chore")
    # Nothing but the assignment: unclaimed, so it shows on both of Sam's pods.
    loose = Task(tenant_id="demo", id="task-loose", name="Loose end")
    await _add(store, own, loose)
    await _edge(store, "pod-a", own.id)
    await _edge(store, shared.id, own.id, kind=EdgeKind.ASSIGNED_TO)
    await _edge(store, shared.id, loose.id, kind=EdgeKind.ASSIGNED_TO)
    await store.record_developer_status_with_blockers(
        _status(shared.id),
        (_blocker(shared.id, "waiting on review", work_item_id="WI-b", pod_id="pod-b"),),
    )
    service = _service(store)

    pod_a = await service.pod_tasks("demo", "pod-a", AS_OF)
    pod_b = await service.pod_tasks("demo", "pod-b", AS_OF)

    assert [task.id for task in pod_a.tasks] == ["task-loose", "task-own", "task-a"]
    assert all(task.open_blockers == () for task in pod_a.tasks)
    assert [task.id for task in pod_b.tasks] == ["task-b", "task-loose"]
    assert [item.description for item in pod_b.tasks[0].open_blockers] == ["waiting on review"]


async def test_pod_tasks_include_another_projects_workstream_the_pod_serves() -> None:
    """Shared pods are legitimate: serving a workstream makes its members' tasks the pod's."""
    store = InMemoryGraphStore()
    ours = Project(tenant_id="demo", id="project-ours", name="Ours")
    theirs = Project(tenant_id="demo", id="project-theirs", name="Theirs")
    pod = Pod(tenant_id="demo", id="pod-data", name="Data Pod")
    member = Developer(tenant_id="demo", id="dev-1", name="Asha")
    served = Workstream(tenant_id="demo", id="ws-cart", name="Cart")
    unserved = Workstream(tenant_id="demo", id="ws-other", name="Other")
    await _add(store, ours, theirs, pod, member, served, unserved)
    await _edge(store, ours.id, pod.id)
    await _edge(store, theirs.id, served.id)
    await _edge(store, theirs.id, unserved.id)
    await _edge(store, pod.id, member.id)
    await _edge(store, pod.id, served.id, kind=EdgeKind.ASSIGNED_TO)
    for task_id, parent_id in (
        ("task-cart", served.id),
        ("task-other", unserved.id),
        ("task-ours", ours.id),
        ("task-theirs", theirs.id),
    ):
        await _add(store, Task(tenant_id="demo", id=task_id, name=task_id))
        await _edge(store, parent_id, task_id)
        await _edge(store, member.id, task_id, kind=EdgeKind.ASSIGNED_TO)

    view = await _service(store).pod_tasks("demo", pod.id, AS_OF)

    assert [task.id for task in view.tasks] == ["task-cart", "task-ours"]


async def test_pod_tasks_read_membership_and_status_as_of_the_panel_date() -> None:
    store = InMemoryGraphStore()
    pod = Pod(tenant_id="demo", id="pod-a", name="Pod A")
    early = Developer(tenant_id="demo", id="dev-early", name="Early")
    late = Developer(tenant_id="demo", id="dev-late", name="Late")
    early_task = Task(tenant_id="demo", id="task-early", name="Early task")
    late_task = Task(tenant_id="demo", id="task-late", name="Late task")
    await _add(store, pod, early, late, early_task, late_task)
    await _edge(store, pod.id, early.id)
    await _edge(store, pod.id, late.id, valid_from=AS_OF + timedelta(days=1))
    await _edge(store, early.id, early_task.id, kind=EdgeKind.ASSIGNED_TO)
    await _edge(store, late.id, late_task.id, kind=EdgeKind.ASSIGNED_TO)
    # A blocker first seen after the panel's date is not open on it.
    await store.record_developer_status_with_blockers(
        _status(early.id),
        (
            _blocker(
                early.id,
                "later trouble",
                work_item_id=early_task.id,
                first_seen_on=AS_OF + timedelta(days=2),
            ),
        ),
    )
    service = _service(store)

    today = await service.pod_tasks("demo", pod.id, AS_OF)
    later = await service.pod_tasks("demo", pod.id, AS_OF + timedelta(days=3))

    assert [task.id for task in today.tasks] == ["task-early"]
    assert today.tasks[0].open_blockers == ()
    assert today.as_of == AS_OF
    assert [task.id for task in later.tasks] == ["task-early", "task-late"]
    assert [item.description for item in later.tasks[0].open_blockers] == ["later trouble"]


async def test_pod_tasks_are_empty_for_a_pod_whose_members_hold_no_tasks() -> None:
    store = InMemoryGraphStore()
    pod = Pod(tenant_id="demo", id="pod-a", name="Pod A")
    member = Developer(tenant_id="demo", id="dev-1", name="Asha")
    workstream = Workstream(tenant_id="demo", id="ws-a", name="Stream")
    unassigned = Task(tenant_id="demo", id="task-free", name="Nobody's yet")
    await _add(store, pod, member, workstream, unassigned)
    await _edge(store, pod.id, member.id)
    await _edge(store, pod.id, workstream.id, kind=EdgeKind.ASSIGNED_TO)
    await _edge(store, workstream.id, unassigned.id)

    view = await _service(store).pod_tasks("demo", pod.id, AS_OF)

    assert view.tasks == ()


async def test_pod_tasks_refuse_a_node_that_is_not_a_pod() -> None:
    store = InMemoryGraphStore()
    await _add(store, Workstream(tenant_id="demo", id="ws-a", name="Stream"))

    with pytest.raises(GraphNotFound):
        await _service(store).pod_tasks("demo", "ws-a", AS_OF)


def test_pod_tasks_route_is_readable_only_with_pod_detail_access(settings: Settings) -> None:
    for roles in ("sm", "admin"):
        app = _app(settings, roles)
        with TestClient(app) as client:
            response = client.get("/pods/pod-runtime/tasks?as_of=2026-06-15")
            missing = client.get("/pods/pod-nowhere/tasks?as_of=2026-06-15")

        assert response.status_code == 200, (roles, response.text)
        body = response.json()
        assert body["pod_name"] == "Runtime Pod"
        # The fixture's tasks hang off their owners only, so both are unclaimed
        # and Liam's amber one sorts ahead of Asha's green one.
        assert [
            (task["id"], [owner["name"] for owner in task["owners"]], task["rag"])
            for task in body["tasks"]
        ] == [("task-graph", ["Liam"], "amber"), ("task-api", ["Asha"], "green")]
        assert body["tasks"][0]["source"] == "inferred"
        assert body["tasks"][0]["confidence"] == 0.6
        # Liam's fixture blocker is a flat status string: unattributed, so it
        # marks no task even though it counts as one of the pod's blockers.
        assert body["tasks"][0]["open_blockers"] == []
        assert missing.status_code == 404

    for roles in ("dev", "po", "mgr", "exec"):
        app = _app(settings, roles)
        with TestClient(app) as client:
            denied = client.get("/pods/pod-runtime/tasks?as_of=2026-06-15")

        assert denied.status_code == 403, roles


def _app(settings: Settings, roles: str) -> FastAPI:
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": roles}))
    registry = app.state.registry
    asyncio.run(
        populate_demo_graph(
            registry.graph_repository(),
            registry.time_series_repository(),
            settings.tenant_id,
        )
    )
    return app


def _service(store: InMemoryGraphStore) -> PersonaViewService:
    return PersonaViewService(
        graph_repository=store,
        status_repository=store,
        rollup_repository=store,
        time_series_repository=store,
    )


async def _add(store: InMemoryGraphStore, *nodes: GraphNode) -> None:
    for node in nodes:
        await store.upsert_node(node)


async def _edge(
    store: InMemoryGraphStore,
    from_id: str,
    to_id: str,
    *,
    kind: EdgeKind = EdgeKind.CONTAINS,
    valid_from: date | None = None,
) -> None:
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id=from_id,
            to_node_id=to_id,
            kind=kind,
            valid_from=valid_from,
        )
    )


def _fact(task_id: str, payload: dict[str, str | float]) -> FactEvent:
    return FactEvent(
        tenant_id="demo",
        source="fixture",
        entity_ref=Task(tenant_id="demo", id=task_id, name=task_id).ref,
        payload=payload,
        observed_at=datetime.combine(AS_OF, datetime.min.time(), tzinfo=UTC),
        correlation_id=f"fixture-{task_id}",
    )


def _status(developer_id: str) -> DeveloperStatus:
    return DeveloperStatus(
        tenant_id="demo",
        developer_id=developer_id,
        as_of=AS_OF,
        source=StatusSource.CONFIRMED,
        blockers=(),
        summary="Status summary.",
    )


def _blocker(
    developer_id: str,
    description: str,
    *,
    work_item_id: str | None = None,
    pod_id: str | None = None,
    first_seen_on: date = AS_OF,
) -> DeveloperBlocker:
    key = normalize_blocker_key(description)
    return DeveloperBlocker(
        tenant_id="demo",
        blocker_id=f"blk-{key.replace(' ', '-')}",
        developer_id=developer_id,
        description=description,
        normalized_key=key,
        work_item_id=work_item_id,
        pod_id=pod_id,
        source=BlockerSource.CHECKIN,
        first_seen_on=first_seen_on,
        last_seen_on=max(first_seen_on, AS_OF),
    )
