"""The tasks a pod's Delivery panel lists, the blockers on them, and who may read them."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.ask_service import PodBlockersTool
from core.application.blocker_resolution import BlockerResolutionService
from core.application.persona_views import (
    BlockerPodView,
    BlockerView,
    PersonaViewService,
    confirmed_with_listed_blockers,
)
from core.application.rollup_service import CONFIRMED_NO_BLOCKERS_REASON, RollupService
from core.domain.blockers import BlockerSource, DeveloperBlocker, normalize_blocker_key
from core.domain.errors import GraphNotFound
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    GraphNode,
    NodeKind,
    Pod,
    Project,
    Task,
    WorkItem,
    Workstream,
)
from core.domain.rollup import FactorKind, Rag
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


async def test_a_task_two_pods_list_reads_blocked_in_both_for_a_member_of_both() -> None:
    """IDP-6 on the QA tenant: one answer per task, whichever pod lists it.

    The Identity project contains both pods, and the Jira sync puts IDP-6 in
    the project and in the Identity Pod. Omar is in both pods, so both list
    it: Platform through its project. His blocker on IDP-6 counts where the
    blocked work sits, the Identity Pod, so Platform's task row read it clear.
    """
    service = _service(await _idp6_store())

    reads = {
        pod_id: (await service.pod_tasks("demo", pod_id, AS_OF)).tasks
        for pod_id in ("pod-identity", "pod-platform")
    }

    for pod_id, tasks in reads.items():
        (row,) = tasks
        assert (row.id, row.blocked) == ("IDP-6", True), pod_id
        assert [(item.description, item.age_days) for item in row.open_blockers] == [
            ("Waiting on review for sso-gateway!1", 4)
        ], pod_id


async def _idp6_store() -> InMemoryGraphStore:
    """The QA tenant's IDP-6: both pods list it, its blocker counts in the Identity Pod."""
    store = InMemoryGraphStore()
    project = Project(tenant_id="demo", id="project-identity", name="Identity Platform")
    identity = Pod(tenant_id="demo", id="pod-identity", name="Identity Pod")
    platform = Pod(tenant_id="demo", id="pod-platform", name="Platform Pod")
    omar = Developer(tenant_id="demo", id="dev-omar", name="Omar")
    task = Task(tenant_id="demo", id="IDP-6", name="SSO session refresh")
    await _add(store, project, identity, platform, omar, task)
    for parent, child in (
        (project.id, identity.id),
        (project.id, platform.id),
        (identity.id, omar.id),
        (platform.id, omar.id),
        (project.id, task.id),
        (identity.id, task.id),
    ):
        await _edge(store, parent, child)
    await _edge(store, omar.id, task.id, kind=EdgeKind.ASSIGNED_TO)
    await store.record_developer_status_with_blockers(
        _status(omar.id),
        (
            _blocker(
                omar.id,
                "Waiting on review for sso-gateway!1",
                work_item_id=task.id,
                first_seen_on=AS_OF - timedelta(days=4),
            ),
        ),
    )
    return store


async def test_a_pods_blockers_list_one_on_a_task_it_lists_and_say_where_it_counts() -> None:
    """The Platform Pod's page listed IDP-6 blocked and, above it, "No open blockers".

    Its task list marks IDP-6 by Omar's blocker, which counts where the blocked
    work sits: the Identity Pod. The blocker list now lists it too, marked as
    counted in the Identity Pod, so the page says one thing.
    """
    service = _service(await _idp6_store())

    identity = await service.pod_blockers("demo", "pod-identity", AS_OF)
    platform = await service.pod_blockers("demo", "pod-platform", AS_OF)

    (counted,) = identity.blockers
    assert (counted.description, counted.age_days, counted.counted_here, counted.counted_in) == (
        "Waiting on review for sso-gateway!1",
        4,
        True,
        (),
    )
    (listed,) = platform.blockers
    assert (listed.blocker_id, listed.description, listed.age_days, listed.owner_name) == (
        counted.blocker_id,
        counted.description,
        4,
        "Omar",
    )
    assert listed.work_item_ref is not None and listed.work_item_ref.id == "IDP-6"
    assert listed.counted_here is False
    assert [(pod.id, pod.name) for pod in listed.counted_in] == [("pod-identity", "Identity Pod")]
    # The page's two lists agree: the task the blocker list names reads blocked.
    (row,) = (await service.pod_tasks("demo", "pod-platform", AS_OF)).tasks
    assert (row.id, row.blocked) == ("IDP-6", True)


async def test_a_blocker_listed_where_it_does_not_count_changes_no_rollup_or_colour() -> None:
    """Listing is not counting: only the Identity Pod's rollup has the blocker."""
    service = _service(await _idp6_store())

    rollups = {
        pod_id: await service.pod_rollup("demo", pod_id, AS_OF)
        for pod_id in ("pod-identity", "pod-platform")
    }

    counted = {
        pod_id: [factor for factor in view.factors if factor.kind is FactorKind.BLOCKER]
        for pod_id, view in rollups.items()
    }
    assert len(counted["pod-identity"]) == 1
    assert counted["pod-platform"] == []
    assert rollups["pod-identity"].rag is Rag.AMBER
    assert rollups["pod-platform"].rag not in {Rag.AMBER, Rag.RED}


async def test_a_green_pods_reason_names_the_blocker_its_list_holds_and_where_it_counts() -> None:
    """The Platform Pod read "All child statuses are confirmed with no blockers." over
    "1 open blocker": Omar's IDP-6 blocker, listed there and counted in the Identity Pod.

    The reason now says what the list holds and where it counts. The colour, the
    line's own colour and the stored rollup stay as they were, and the blocker still
    counts only in the Identity Pod.
    """
    store = await _idp6_store()
    service = _service(store)
    computed = await service.pod_rollup("demo", "pod-platform", AS_OF)
    platform_ref = EntityRef(tenant_id="demo", kind=NodeKind.POD, id="pod-platform")
    tree = await store.get_program_tree("demo", "pod-platform", AS_OF)
    stored = next(
        status
        for status in await RollupService(
            store, store, BlockerResolutionService(store, store)
        ).compute(tree, AS_OF)
        if status.entity_ref == platform_ref
    )
    await store.record_node_status(stored)

    view = await service.pod_rollup("demo", "pod-platform", AS_OF)
    blockers = await service.pod_blockers("demo", "pod-platform", AS_OF)

    assert len(blockers.blockers) == 1
    (reason,) = view.factors
    assert reason.description == (
        "All child statuses are confirmed; 1 open blocker (IDP-6, Omar, 4 days), "
        "counted in Identity Pod."
    )
    assert (reason.kind, reason.contributes) == (FactorKind.AGGREGATE, Rag.GREEN)
    assert view.rag is Rag.GREEN
    assert computed.factors == view.factors
    # Only the read's words: the stored row keeps its line.
    recorded = await store.latest_node_status("demo", platform_ref, AS_OF)
    assert recorded is not None
    assert [factor.description for factor in recorded.factors] == [CONFIRMED_NO_BLOCKERS_REASON]
    identity = await service.pod_rollup("demo", "pod-identity", AS_OF)
    assert [factor.kind for factor in identity.factors] == [FactorKind.BLOCKER]
    assert identity.rag is Rag.AMBER


async def test_a_green_pod_with_nothing_on_its_blocker_list_keeps_its_no_blockers_line() -> None:
    store = InMemoryGraphStore()
    pod = Pod(tenant_id="demo", id="pod-quiet", name="Quiet Pod")
    lea = Developer(tenant_id="demo", id="dev-lea", name="Lea")
    await _add(store, pod, lea)
    await _edge(store, pod.id, lea.id)
    await store.record_developer_status_with_blockers(_status(lea.id), ())

    view = await _service(store).pod_rollup("demo", "pod-quiet", AS_OF)

    assert [factor.description for factor in view.factors] == [CONFIRMED_NO_BLOCKERS_REASON]
    assert view.rag is Rag.GREEN


def _listed(work: str | None, owner: str, age: int, *, counted_in: str | None) -> BlockerView:
    ref = EntityRef(tenant_id="demo", kind=NodeKind.TASK, id=work) if work else None
    return BlockerView(
        id=f"blk-{owner}-{age}",
        blocker_id=f"blk-{owner}-{age}",
        description="waiting",
        age_days=age,
        owner_id=f"dev-{owner}",
        owner_name=owner,
        source=StatusSource.CONFIRMED,
        status_as_of=AS_OF,
        source_ref=ref or EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id=owner),
        work_item_ref=ref,
        pod_ref=None,
        unattributed=False,
        first_seen_on=AS_OF - timedelta(days=age),
        counted_here=counted_in is None,
        counted_in=(BlockerPodView(id="pod-x", name=counted_in),) if counted_in else (),
    )


def test_the_reason_names_a_few_blockers_oldest_first_and_says_where_they_count() -> None:
    two = confirmed_with_listed_blockers(
        [
            _listed("IDP-6", "Omar Haddad", 5, counted_in="Identity Pod"),
            _listed(None, "Lea Novak", 1, counted_in=None),
        ]
    )
    five = confirmed_with_listed_blockers(
        [_listed(f"IDP-{n}", "Omar Haddad", n, counted_in="Identity Pod") for n in (9, 8, 7, 6)]
        + [_listed("IDP-1", "Omar Haddad", 0, counted_in="Payments Pod")]
    )

    assert two == (
        "All child statuses are confirmed; 2 open blockers (IDP-6, Omar Haddad, 5 days; "
        "Lea Novak, 1 day), 1 counted in Identity Pod."
    )
    assert five == (
        "All child statuses are confirmed; 5 open blockers (IDP-9, Omar Haddad, 9 days; "
        "IDP-8, Omar Haddad, 8 days; IDP-7, Omar Haddad, 7 days; and 2 more), "
        "counted in Identity Pod and Payments Pod."
    )


async def test_a_members_blocker_on_work_the_pod_does_not_list_stays_off_its_list() -> None:
    """Sam's blocker counts in pod-b on pod-b's work; pod-a lists no task it marks."""
    store = InMemoryGraphStore()
    shared = Developer(tenant_id="demo", id="dev-shared", name="Sam")
    await _add(store, shared)
    for suffix in ("a", "b"):
        pod = Pod(tenant_id="demo", id=f"pod-{suffix}", name=f"Pod {suffix.upper()}")
        workstream = Workstream(tenant_id="demo", id=f"ws-{suffix}", name=f"Stream {suffix}")
        task = Task(tenant_id="demo", id=f"task-{suffix}", name=f"Task {suffix}")
        await _add(store, pod, workstream, task)
        await _edge(store, pod.id, shared.id)
        await _edge(store, pod.id, workstream.id, kind=EdgeKind.ASSIGNED_TO)
        await _edge(store, workstream.id, task.id)
        await _edge(store, shared.id, task.id, kind=EdgeKind.ASSIGNED_TO)
    await store.record_developer_status_with_blockers(
        _status(shared.id),
        (_blocker(shared.id, "waiting on review", work_item_id="task-b"),),
    )
    service = _service(store)

    pod_a = await service.pod_blockers("demo", "pod-a", AS_OF)
    pod_b = await service.pod_blockers("demo", "pod-b", AS_OF)

    assert pod_a.blockers == ()
    assert [(item.description, item.counted_here) for item in pod_b.blockers] == [
        ("waiting on review", True)
    ]


async def test_ask_lists_a_blocker_once_over_every_pod_and_says_where_it_counts() -> None:
    """Over every pod Ask counts Omar's IDP-6 blocker once, where it counts."""
    store = await _idp6_store()
    tool = PodBlockersTool(tenant_id="demo", service=_service(store), repository=store, as_of=AS_OF)

    every = json.loads(await tool.run({}))
    platform = json.loads(await tool.run({"pod_id": "pod-platform"}))

    assert every["blocker_count"] == 1
    (counted,) = every["blockers"]
    assert (counted["pod_name"], counted["work_item_id"], counted["counted_in_this_pod"]) == (
        "Identity Pod",
        "IDP-6",
        True,
    )
    assert "counts_in" not in counted
    assert platform["blocker_count"] == 1
    (listed,) = platform["blockers"]
    assert (listed["pod_name"], listed["counted_in_this_pod"], listed["counts_in"]) == (
        "Platform Pod",
        False,
        ["Identity Pod"],
    )


def test_the_pod_blockers_route_says_where_a_listed_blocker_counts(settings: Settings) -> None:
    app = _app(settings, "sm")
    with TestClient(app) as client:
        store = app.state.registry.graph_repository()

        async def seed() -> None:
            source = await _idp6_store()
            for node in await source.list_nodes("demo"):
                await store.upsert_node(node)
            for edge in await source.list_edges("demo"):
                await store.add_edge(edge)
            blockers = await source.open_blockers("demo", "dev-omar", AS_OF)
            await app.state.registry.status_repository().record_developer_status_with_blockers(
                _status("dev-omar"), blockers
            )

        asyncio.run(seed())
        response = client.get(f"/pods/pod-platform/blockers?as_of={AS_OF.isoformat()}")
        rollup = client.get(f"/pods/pod-platform/rollup?as_of={AS_OF.isoformat()}")

    assert response.status_code == 200, response.text
    (blocker,) = response.json()["blockers"]
    assert blocker["work_item_ref"]["id"] == "IDP-6"
    assert blocker["counted_here"] is False
    assert blocker["counted_in"] == [{"id": "pod-identity", "name": "Identity Pod"}]
    # The pod's reason, read by the same panel, agrees with that list.
    assert rollup.status_code == 200, rollup.text
    assert [factor["description"] for factor in rollup.json()["factors"]] == [
        "All child statuses are confirmed; 1 open blocker (IDP-6, Omar, 4 days), "
        "counted in Identity Pod."
    ]


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


async def test_pod_tasks_show_the_issue_trackers_own_status() -> None:
    """N17: a manager moving a ticket to In Progress changed nothing a task row showed."""
    store = InMemoryGraphStore()
    pod = Pod(tenant_id="demo", id="pod-a", name="Payments Pod")
    liam = Developer(tenant_id="demo", id="dev-liam", name="Liam")
    await _add(store, pod, liam)
    await _edge(store, pod.id, liam.id)
    tasks: dict[str, dict[str, str]] = {
        # As the issue sync writes it: the tracker's name beside its state.
        "CHK-4": {"key": "CHK-4", "status": "In Progress", "state": "in_progress"},
        # A tracker that gave no status name: its normalized state, named.
        "GH-7": {"key": "GH-7", "state": "todo"},
        # Seeded with a RAG word and no tracker state: the chip's colour, not a status.
        "task-seeded": {"status": "at-risk"},
    }
    for task_id, metadata in tasks.items():
        await _add(store, Task(tenant_id="demo", id=task_id, name=task_id, metadata=metadata))
        await _edge(store, pod.id, task_id)
        await _edge(store, liam.id, task_id, kind=EdgeKind.ASSIGNED_TO)

    view = await _service(store).pod_tasks("demo", pod.id, AS_OF)

    shown = {task.id: (task.tracker_status, task.rag) for task in view.tasks}
    assert shown == {
        # In progress has no colour: the tracker status is what moved.
        "CHK-4": ("In Progress", Rag.UNKNOWN),
        "GH-7": ("To Do", Rag.UNKNOWN),
        "task-seeded": (None, Rag.AMBER),
    }


async def test_pod_tasks_refuse_a_node_that_is_not_a_pod() -> None:
    store = InMemoryGraphStore()
    await _add(store, Workstream(tenant_id="demo", id="ws-a", name="Stream"))

    with pytest.raises(GraphNotFound):
        await _service(store).pod_tasks("demo", "ws-a", AS_OF)


def test_pod_tasks_route_is_readable_only_with_pod_detail_access(settings: Settings) -> None:
    # A manager is the last escalation step for a pod, so it opens pods too.
    for roles in ("sm", "mgr", "admin"):
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
        # The fixture's tasks are not from a tracker: no tracker status.
        assert [task["tracker_status"] for task in body["tasks"]] == [None, None]
        assert missing.status_code == 404

    for roles in ("dev", "po", "exec"):
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
