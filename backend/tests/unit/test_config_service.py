from __future__ import annotations

from datetime import date, time

import pytest

from core.application.config_service import ConfigConflict, ConfigService, DirectoryService
from core.domain.directory import DirectoryUser
from core.domain.errors import GraphNotFound
from core.domain.escalation import EscalationContact, EscalationTarget, PodEscalationContacts
from core.domain.graph import EdgeKind, EntityRef, NodeKind
from core.domain.identity import IdentityLink
from core.domain.rollup import NodeStatus, Rag
from core.domain.status import CheckInPreference, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeDirectoryUserRepository


async def test_config_service_crud_links_assignments_and_preferences() -> None:
    store = InMemoryGraphStore()
    service = ConfigService(store, store, time_series_repository=store)

    program = await service.create_node(
        "demo",
        NodeKind.PROGRAM,
        "program-1",
        "Program",
        {"description": "Runtime program"},
    )
    project = await service.create_node("demo", NodeKind.PROJECT, "project-1", "Project")
    workstream = await service.create_node(
        "demo",
        NodeKind.WORKSTREAM,
        "workstream-1",
        "Workstream",
        {
            "type": "feature",
            "phase": "build",
            "owner_id": "dev-1",
            "target_date": "2026-01-31",
        },
    )
    pod = await service.create_node("demo", NodeKind.POD, "pod-1", "Pod")
    member = await service.create_node("demo", NodeKind.DEVELOPER, "dev-1", "Asha")
    task = await service.create_node("demo", NodeKind.TASK, "task-1", "Task")
    work_item = await service.create_work_item(
        "demo",
        "work-item-1",
        "Feature slice",
        {"state": "proposed", "item_type": "feature", "repo": "repo-1"},
    )

    assert program.metadata["description"] == "Runtime program"
    assert [node.id for node in await service.list_nodes("demo", NodeKind.PROJECT)] == ["project-1"]
    assert [node.id for node in await service.list_nodes("demo", NodeKind.WORKSTREAM)] == [
        "workstream-1"
    ]

    program_edge = await service.link_program_project("demo", program.id, project.id)
    project_edge = await service.link_project_pod("demo", project.id, pod.id)
    workstream_edge = await service.link_project_workstream("demo", project.id, workstream.id)
    pod_workstream_edge = await service.assign_pod_workstream("demo", pod.id, workstream.id)
    workstream_task_edge = await service.link_workstream_task("demo", workstream.id, task.id)
    work_item_edge = await service.link_work_item_to_workstream("demo", workstream.id, work_item.id)
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
    assert workstream_edge.kind is EdgeKind.CONTAINS
    assert pod_workstream_edge.kind is EdgeKind.ASSIGNED_TO
    assert workstream_task_edge.kind is EdgeKind.CONTAINS
    assert work_item_edge.kind is EdgeKind.CONTAINS
    assert member_edge.metadata["role"] == "tech lead"
    assert member_edge.valid_from == date(2026, 1, 10)
    assert assignment.kind is EdgeKind.ASSIGNED_TO

    transitioned = await service.transition_work_item("demo", work_item.id, "in_progress")
    assert transitioned.metadata["state"] == "in_progress"
    assert transitioned.metadata["last_transition_at"]
    work_item_facts = await store.list_recent_facts("demo", sources=("work_item",), limit=10)
    assert work_item_facts[0].payload["to_state"] == "in_progress"
    assert work_item_facts[0].payload["repo"] == "repo-1"

    with pytest.raises(ConfigConflict):
        await service.link_program_project("demo", program.id, project.id)
    with pytest.raises(ConfigConflict):
        await service.link_project_workstream("demo", project.id, workstream.id)

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
    await service.unlink_project_workstream("demo", project.id, workstream.id)
    await service.unassign_pod_workstream("demo", pod.id, workstream.id)
    await service.unlink_workstream_task("demo", workstream.id, task.id)
    await service.unlink_work_item_from_workstream("demo", workstream.id, work_item.id)
    assert workstream_edge not in await service.list_edges("demo")
    assert pod_workstream_edge not in await service.list_edges("demo")
    assert workstream_task_edge not in await service.list_edges("demo")
    assert work_item_edge not in await service.list_edges("demo")

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
    workstream = await service.create_node(
        "demo",
        NodeKind.WORKSTREAM,
        "workstream-1",
        "Runtime Admin",
        {"type": "feature", "phase": "build"},
    )
    member = await service.create_node("demo", NodeKind.DEVELOPER, "dev-1", "Asha")
    task = await service.create_node("demo", NodeKind.TASK, "task-1", "Task")
    await service.link_program_project("demo", program.id, project.id)
    await service.link_project_pod("demo", project.id, pod.id)
    await service.link_project_workstream("demo", project.id, workstream.id)
    await service.assign_pod_workstream("demo", pod.id, workstream.id)
    await service.link_workstream_task("demo", workstream.id, task.id)
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
    workstreams = await directory.list_workstreams("demo", as_of)
    pods = await directory.list_pods("demo", as_of)

    assert projects[0].id == project.id
    assert projects[0].description == "Foundations"
    assert projects[0].code == "FOUND"
    assert projects[0].rag is Rag.AMBER
    assert projects[0].program_ids == (program.id,)
    assert projects[0].workstream_ids == (workstream.id,)
    assert projects[0].pod_ids == (pod.id,)
    assert workstreams[0].id == workstream.id
    assert workstreams[0].project_ids == (project.id,)
    assert workstreams[0].pod_ids == (pod.id,)
    assert workstreams[0].task_ids == (task.id,)
    assert (await directory.get_workstream("demo", workstream.id, as_of)).id == workstream.id
    project_workstreams = await directory.list_project_workstreams("demo", project.id, as_of)
    assert [item.id for item in project_workstreams] == [workstream.id]
    assert pods[0].project_ids == (project.id,)
    assert pods[0].workstream_ids == (workstream.id,)
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


async def test_pod_escalation_contacts_round_trip_and_clear() -> None:
    store = InMemoryGraphStore()
    service = ConfigService(store, store)
    await service.create_node("demo", NodeKind.POD, "pod-1", "Pod")

    empty = await service.get_pod_escalation_contacts("demo", "pod-1")
    assert empty.scrum_master is None
    assert empty.manager is None

    saved = await service.set_pod_escalation_contacts(
        "demo",
        "pod-1",
        PodEscalationContacts(
            scrum_master=EscalationContact(
                target=EscalationTarget.SCRUM_MASTER,
                chat_external_id="U-SM",
                display_name="Sam SM",
            ),
            manager=EscalationContact(
                target=EscalationTarget.MANAGER,
                chat_external_id="U-MGR",
            ),
        ),
    )
    assert saved.scrum_master is not None

    reloaded = await service.get_pod_escalation_contacts("demo", "pod-1")
    assert reloaded.scrum_master is not None
    assert reloaded.scrum_master.chat_external_id == "U-SM"
    assert reloaded.scrum_master.display_name == "Sam SM"
    assert reloaded.manager is not None
    assert reloaded.manager.chat_external_id == "U-MGR"
    assert reloaded.manager.display_name is None

    cleared = await service.set_pod_escalation_contacts("demo", "pod-1", PodEscalationContacts())
    assert cleared.scrum_master is None
    reloaded_after_clear = await service.get_pod_escalation_contacts("demo", "pod-1")
    assert reloaded_after_clear.scrum_master is None
    assert reloaded_after_clear.manager is None


async def test_pod_escalation_contacts_requires_pod_node() -> None:
    store = InMemoryGraphStore()
    service = ConfigService(store, store)

    with pytest.raises(GraphNotFound):
        await service.get_pod_escalation_contacts("demo", "missing-pod")


async def test_auto_match_identity_links_fills_missing_from_directory() -> None:
    store = InMemoryGraphStore()
    directory = FakeDirectoryUserRepository()
    service = ConfigService(store, store, directory, identity_link_repository=store)
    await directory.upsert_users(
        [
            DirectoryUser(
                tenant_id="demo",
                external_id="U1001",
                display_name="Asha Rao",
                email="asha@example.com",
                handle="asha",
            ),
            DirectoryUser(
                tenant_id="demo",
                external_id="U1002",
                display_name="Mina Patel",
                email="mina@example.com",
                handle="mina",
            ),
        ]
    )
    await service.add_member_from_directory("demo", "U1001")
    await service.add_member_from_directory("demo", "U1002")
    # U1002 already has an admin-set chat id and jira email that must survive.
    await service.set_identity_link(
        IdentityLink(
            tenant_id="demo",
            developer_id="U1002",
            chat_user_id="ADMIN-CHAT",
            jira_email="admin@corp.example",
        )
    )

    result = await service.auto_match_identity_links("demo")

    assert result.updated_count == 1
    assert [member.id for member in result.members] == ["U1001"]
    assert set(result.members[0].filled) == {"chat_user_id", "jira_email"}

    asha = await service.get_identity_link("demo", "U1001")
    assert asha is not None
    assert asha.chat_user_id == "U1001"
    assert asha.jira_email == "asha@example.com"

    # Admin-set values are never overwritten by auto-match.
    mina = await service.get_identity_link("demo", "U1002")
    assert mina is not None
    assert mina.chat_user_id == "ADMIN-CHAT"
    assert mina.jira_email == "admin@corp.example"


async def test_list_unmapped_members_flags_members_without_chat_id() -> None:
    store = InMemoryGraphStore()
    directory = FakeDirectoryUserRepository()
    service = ConfigService(store, store, directory, identity_link_repository=store)
    await directory.upsert_users(
        [
            DirectoryUser(
                tenant_id="demo",
                external_id="U1001",
                display_name="Asha Rao",
                email="asha@example.com",
            ),
            DirectoryUser(
                tenant_id="demo",
                external_id="U1002",
                display_name="Mina Patel",
                email="mina@example.com",
            ),
        ]
    )
    await service.add_member_from_directory("demo", "U1001")
    await service.add_member_from_directory("demo", "U1002")
    # U1001 is fully mapped for delivery; U1002 keeps a partial link with no chat id.
    await service.set_identity_link(
        IdentityLink(tenant_id="demo", developer_id="U1001", chat_user_id="U1001")
    )
    await service.set_identity_link(
        IdentityLink(tenant_id="demo", developer_id="U1002", jira_email="mina@example.com")
    )

    unmapped = await service.list_unmapped_members("demo")

    assert [member.id for member in unmapped] == ["U1002"]
    assert "chat_user_id" in unmapped[0].missing
    assert "jira_email" not in unmapped[0].missing
