from __future__ import annotations

from datetime import date, time

import pytest

from core.application.config_service import ConfigConflict, ConfigService, DirectoryService
from core.domain.directory import DirectoryUser
from core.domain.errors import GraphNotFound
from core.domain.graph import EdgeKind, EntityRef, NodeKind
from core.domain.rollup import NodeStatus, Rag
from core.domain.status import CheckInPreference, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeDirectoryUserRepository


async def test_config_service_crud_links_assignments_and_preferences() -> None:
    store = InMemoryGraphStore()
    service = ConfigService(store, store)

    program = await service.create_node(
        "demo",
        NodeKind.PROGRAM,
        "program-1",
        "Program",
        {"description": "Runtime program"},
    )
    project = await service.create_node("demo", NodeKind.PROJECT, "project-1", "Project")
    pod = await service.create_node("demo", NodeKind.POD, "pod-1", "Pod")
    member = await service.create_node("demo", NodeKind.DEVELOPER, "dev-1", "Asha")
    task = await service.create_node("demo", NodeKind.TASK, "task-1", "Task")

    assert program.metadata["description"] == "Runtime program"
    assert [node.id for node in await service.list_nodes("demo", NodeKind.PROJECT)] == ["project-1"]

    program_edge = await service.link_program_project("demo", program.id, project.id)
    project_edge = await service.link_project_pod("demo", project.id, pod.id)
    member_edge = await service.link_pod_member(
        "demo",
        pod.id,
        member.id,
        "tech lead",
        date(2026, 1, 10),
    )
    assignment = await service.assign_member_task("demo", member.id, task.id)

    assert program_edge.kind is EdgeKind.CONTAINS
    assert project_edge.kind is EdgeKind.CONTAINS
    assert member_edge.metadata["role"] == "tech lead"
    assert member_edge.valid_from == date(2026, 1, 10)
    assert assignment.kind is EdgeKind.ASSIGNED_TO

    with pytest.raises(ConfigConflict):
        await service.link_program_project("demo", program.id, project.id)

    preference = await service.record_checkin_preference(
        CheckInPreference(
            tenant_id="demo",
            developer_id=member.id,
            local_time=time(10, 15),
            timezone="Asia/Kolkata",
        )
    )
    assert await service.get_checkin_preference("demo", member.id) == preference
    assert await service.list_checkin_preferences("demo") == [preference]

    await service.unlink_project_pod("demo", project.id, pod.id)
    assert project_edge not in await service.list_edges("demo")

    await service.delete_node("demo", member.id, NodeKind.DEVELOPER)
    assert await service.list_checkin_preferences("demo") == []
    with pytest.raises(GraphNotFound):
        await service.assign_member_task("demo", member.id, task.id)


async def test_directory_service_lists_configured_relationships_and_rollup() -> None:
    store = InMemoryGraphStore()
    service = ConfigService(store, store)
    directory = DirectoryService(store, store)
    as_of = date(2026, 1, 10)

    program = await service.create_node("demo", NodeKind.PROGRAM, "program-1", "Program")
    project = await service.create_node(
        "demo",
        NodeKind.PROJECT,
        "project-1",
        "Project",
        {"description": "Foundations", "code": "FOUND"},
    )
    pod = await service.create_node("demo", NodeKind.POD, "pod-1", "Pod")
    member = await service.create_node("demo", NodeKind.DEVELOPER, "dev-1", "Asha")
    await service.link_program_project("demo", program.id, project.id)
    await service.link_project_pod("demo", project.id, pod.id)
    await service.link_pod_member("demo", pod.id, member.id, "engineer", as_of)
    await store.record_node_status(
        NodeStatus(
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.PROJECT, id=project.id),
            rag=Rag.AMBER,
            source=StatusSource.INFERRED,
            factors=(),
            as_of=as_of,
        )
    )

    projects = await directory.list_projects("demo", as_of)
    pods = await directory.list_pods("demo", as_of)

    assert projects[0].id == project.id
    assert projects[0].description == "Foundations"
    assert projects[0].code == "FOUND"
    assert projects[0].rag is Rag.AMBER
    assert projects[0].program_ids == (program.id,)
    assert projects[0].pod_ids == (pod.id,)
    assert pods[0].project_ids == (project.id,)
    assert pods[0].member_ids == (member.id,)


async def test_config_service_reports_wrong_kind_node() -> None:
    store = InMemoryGraphStore()
    service = ConfigService(store, store)

    project = await service.create_node("demo", NodeKind.PROJECT, "project-1", "Project")

    with pytest.raises(GraphNotFound, match="exists as a project, not a pod"):
        await service.get_node("demo", project.id, NodeKind.POD)


async def test_directory_search_and_add_member_from_directory() -> None:
    store = InMemoryGraphStore()
    directory = FakeDirectoryUserRepository()
    service = ConfigService(store, store, directory)
    await directory.upsert_users(
        [
            DirectoryUser(
                tenant_id="demo",
                external_id="U1001",
                display_name="Asha Rao",
                email="asha@example.com",
                handle="asha",
                source="slack",
            ),
            DirectoryUser(
                tenant_id="demo",
                external_id="U1002",
                display_name="Mina Patel",
                email="mina@example.com",
                handle="mina",
                source="slack",
            ),
        ]
    )

    users, total = await service.search_directory("demo", query="ash")
    assert total == 1
    assert [user.external_id for user in users] == ["U1001"]

    member = await service.add_member_from_directory("demo", "U1001")
    assert member.id == "U1001"
    assert member.metadata["chat_external_id"] == "U1001"
    assert member.metadata["email"] == "asha@example.com"

    assert await service.add_member_from_directory("demo", "U1001") == member


async def test_add_member_from_directory_rejects_inactive_user() -> None:
    store = InMemoryGraphStore()
    directory = FakeDirectoryUserRepository()
    service = ConfigService(store, store, directory)
    await directory.upsert_users(
        [
            DirectoryUser(
                tenant_id="demo",
                external_id="U1001",
                display_name="Inactive User",
                email="inactive@example.com",
                handle="inactive",
                is_active=False,
                source="slack",
            )
        ]
    )

    with pytest.raises(GraphNotFound, match="active directory user U1001 not found"):
        await service.add_member_from_directory("demo", "U1001")

    assert await store.get_node("demo", "U1001") is None


async def test_add_members_from_directory_validates_batch_before_writing() -> None:
    store = InMemoryGraphStore()
    directory = FakeDirectoryUserRepository()
    service = ConfigService(store, store, directory)
    await directory.upsert_users(
        [
            DirectoryUser(
                tenant_id="demo",
                external_id="U1001",
                display_name="Asha Rao",
                email="asha@example.com",
                handle="asha",
                source="slack",
            )
        ]
    )

    with pytest.raises(GraphNotFound, match="active directory user missing-user not found"):
        await service.add_members_from_directory("demo", ["U1001", "missing-user"])

    assert await store.get_node("demo", "U1001") is None


async def test_add_member_from_directory_rejects_wrong_kind_conflict() -> None:
    store = InMemoryGraphStore()
    directory = FakeDirectoryUserRepository()
    service = ConfigService(store, store, directory)
    await directory.upsert_users(
        [
            DirectoryUser(
                tenant_id="demo",
                external_id="U1001",
                display_name="Asha Rao",
                source="slack",
            )
        ]
    )
    await service.create_node("demo", NodeKind.PROJECT, "U1001", "Project")

    with pytest.raises(ConfigConflict, match="exists as a project, not a developer"):
        await service.add_member_from_directory("demo", "U1001")
