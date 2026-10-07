"""Config changes are dated: a link holds from the day it is made, and unlinking or
deleting ends it that day, so a read as of an earlier day still sees it."""

from __future__ import annotations

from datetime import date

import pytest

from core.application.config_service import ConfigConflict, ConfigService, DirectoryService
from core.domain.errors import GraphNotFound
from core.domain.graph import NodeKind
from infra.persistence.in_memory_graph import InMemoryGraphStore

TENANT = "demo"
MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY = (date(2026, 3, day) for day in (2, 3, 4, 5, 6))


class _Calendar:
    """The day the config service acts on, moved forward by each test."""

    def __init__(self, day: date) -> None:
        self.day = day

    def today(self) -> date:
        return self.day


async def _tenant() -> tuple[ConfigService, DirectoryService, InMemoryGraphStore, _Calendar]:
    """A project with one pod and one member, all linked on Monday."""
    store = InMemoryGraphStore()
    calendar = _Calendar(MONDAY)
    config = ConfigService(store, store, today=calendar.today)
    for kind, node_id, name in (
        (NodeKind.PROGRAM, "program", "Retail"),
        (NodeKind.PROJECT, "checkout", "Checkout"),
        (NodeKind.POD, "pod-pay", "Payments"),
        (NodeKind.DEVELOPER, "dev-asha", "Asha"),
    ):
        await config.create_node(TENANT, kind, node_id, name)
    await config.link_program_project(TENANT, "program", "checkout")
    await config.link_project_pod(TENANT, "checkout", "pod-pay")
    await config.link_pod_member(TENANT, "pod-pay", "dev-asha", "engineer", calendar.today())
    return config, DirectoryService(store, store), store, calendar


async def _pods_of_checkout(directory: DirectoryService, day: date) -> tuple[str, ...]:
    projects = await directory.list_projects(TENANT, day)
    return next(project.pod_ids for project in projects if project.id == "checkout")


async def test_unlinking_ends_a_link_today_and_an_earlier_day_still_reads_it() -> None:
    config, directory, _store, calendar = await _tenant()

    calendar.day = WEDNESDAY
    await config.unlink_project_pod(TENANT, "checkout", "pod-pay")

    assert await _pods_of_checkout(directory, TUESDAY) == ("pod-pay",)
    assert await _pods_of_checkout(directory, WEDNESDAY) == ()
    (pod,) = await directory.list_pods(TENANT, TUESDAY)
    assert pod.project_ids == ("checkout",)
    # Unlinked once, there is nothing left to unlink.
    with pytest.raises(GraphNotFound):
        await config.unlink_project_pod(TENANT, "checkout", "pod-pay")


async def test_every_config_unlink_keeps_the_earlier_days() -> None:
    config, directory, _store, calendar = await _tenant()

    calendar.day = WEDNESDAY
    await config.unlink_program_project(TENANT, None, "checkout")
    await config.unlink_pod_member(TENANT, "pod-pay", "dev-asha")

    (tuesday_project,) = await directory.list_projects(TENANT, TUESDAY)
    (wednesday_project,) = await directory.list_projects(TENANT, WEDNESDAY)
    assert tuesday_project.program_ids == ("program",)
    assert wednesday_project.program_ids == ()
    assert (await directory.list_pods(TENANT, TUESDAY))[0].member_ids == ("dev-asha",)
    assert (await directory.list_pods(TENANT, WEDNESDAY))[0].member_ids == ()


async def test_linking_again_holds_from_that_day_and_the_days_between_stay_unlinked() -> None:
    config, directory, _store, calendar = await _tenant()
    calendar.day = WEDNESDAY
    await config.unlink_project_pod(TENANT, "checkout", "pod-pay")

    calendar.day = FRIDAY
    relinked = await config.link_project_pod(TENANT, "checkout", "pod-pay")

    assert relinked.valid_from == FRIDAY
    assert await _pods_of_checkout(directory, TUESDAY) == ("pod-pay",)
    assert await _pods_of_checkout(directory, THURSDAY) == ()
    assert await _pods_of_checkout(directory, FRIDAY) == ("pod-pay",)
    with pytest.raises(ConfigConflict):
        await config.link_project_pod(TENANT, "checkout", "pod-pay")


async def test_unlinking_and_linking_again_the_same_day_leaves_no_gap() -> None:
    config, directory, _store, calendar = await _tenant()

    calendar.day = WEDNESDAY
    await config.unlink_project_pod(TENANT, "checkout", "pod-pay")
    await config.link_project_pod(TENANT, "checkout", "pod-pay")

    for day in (TUESDAY, WEDNESDAY, THURSDAY):
        assert await _pods_of_checkout(directory, day) == ("pod-pay",)


async def test_a_link_made_and_ended_the_same_day_leaves_nothing_behind() -> None:
    config, _directory, store, calendar = await _tenant()
    calendar.day = WEDNESDAY
    await config.create_node(TENANT, NodeKind.WORKSTREAM, "ws-pay", "Payments")

    await config.link_project_workstream(TENANT, "checkout", "ws-pay")
    await config.unlink_project_workstream(TENANT, "checkout", "ws-pay")

    assert await store.list_edges(TENANT, to_node_id="ws-pay") == []


async def test_a_link_made_today_is_not_on_an_earlier_day() -> None:
    config, directory, _store, calendar = await _tenant()
    calendar.day = WEDNESDAY
    await config.create_node(TENANT, NodeKind.POD, "pod-search", "Search")

    await config.link_project_pod(TENANT, "checkout", "pod-search")

    assert await _pods_of_checkout(directory, TUESDAY) == ("pod-pay",)
    assert await _pods_of_checkout(directory, WEDNESDAY) == ("pod-pay", "pod-search")


async def test_deleting_a_pod_keeps_it_and_its_links_on_an_earlier_day() -> None:
    config, directory, _store, calendar = await _tenant()

    calendar.day = WEDNESDAY
    await config.delete_node(TENANT, "pod-pay", NodeKind.POD)

    (tuesday_pod,) = await directory.list_pods(TENANT, TUESDAY)
    assert (tuesday_pod.id, tuesday_pod.name) == ("pod-pay", "Payments")
    assert tuesday_pod.project_ids == ("checkout",)
    assert tuesday_pod.member_ids == ("dev-asha",)
    assert await _pods_of_checkout(directory, TUESDAY) == ("pod-pay",)
    assert await directory.list_pods(TENANT, WEDNESDAY) == []
    assert await _pods_of_checkout(directory, WEDNESDAY) == ()
    # Admin works on now: the pod is gone there.
    assert await config.list_nodes(TENANT, NodeKind.POD) == []
    with pytest.raises(GraphNotFound):
        await config.get_node(TENANT, "pod-pay", NodeKind.POD)


async def test_a_pod_made_again_under_a_deleted_id_starts_without_the_old_links() -> None:
    config, directory, _store, calendar = await _tenant()
    calendar.day = WEDNESDAY
    await config.delete_node(TENANT, "pod-pay", NodeKind.POD)

    calendar.day = FRIDAY
    await config.create_node(TENANT, NodeKind.POD, "pod-pay", "Payments again")

    (friday_pod,) = await directory.list_pods(TENANT, FRIDAY)
    assert (friday_pod.name, friday_pod.project_ids, friday_pod.member_ids) == (
        "Payments again",
        (),
        (),
    )
    assert await _pods_of_checkout(directory, TUESDAY) == ("pod-pay",)
    assert await _pods_of_checkout(directory, THURSDAY) == ()


async def test_deleting_a_member_keeps_their_membership_on_an_earlier_day() -> None:
    config, directory, store, calendar = await _tenant()

    calendar.day = WEDNESDAY
    await config.delete_node(TENANT, "dev-asha", NodeKind.DEVELOPER)

    assert (await directory.list_pods(TENANT, TUESDAY))[0].member_ids == ("dev-asha",)
    assert (await directory.list_pods(TENANT, WEDNESDAY))[0].member_ids == ()
    # Who was a member that day is read the same way by the check-in reads.
    assert "dev-asha" in await store.developers_without_checkin(TENANT, TUESDAY)
    assert "dev-asha" not in await store.developers_without_checkin(TENANT, WEDNESDAY)
