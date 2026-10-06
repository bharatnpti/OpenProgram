from __future__ import annotations

from dataclasses import replace
from datetime import date, time

import pytest

from core.application.config_service import (
    ConfigConflict,
    ConfigService,
    ConfigValidationError,
    DirectoryPersonView,
    DirectoryService,
    EscalationContactChoice,
    PodEscalationContactChoices,
)
from core.domain.directory import DirectoryUser
from core.domain.errors import GraphNotFound
from core.domain.graph import EdgeKind, EntityRef, GraphEdge, NodeKind
from core.domain.identity import IdentityLink
from core.domain.rollup import NodeStatus, Rag
from core.domain.status import CheckInPreference, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeDirectoryUserRepository, FakeIssueTracker


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


async def test_directory_service_resolves_workstream_people_to_member_names() -> None:
    store = InMemoryGraphStore()
    service = ConfigService(store, store, identity_link_repository=store)
    directory = DirectoryService(store, store, store)
    as_of = date(2026, 1, 10)

    # One member per way a person field can name someone: by node id, by the
    # chat id on an identity link, and by the chat id copied from the directory.
    await service.create_node("demo", NodeKind.DEVELOPER, "dev-asha", "Asha Rao")
    await service.create_node("demo", NodeKind.DEVELOPER, "dev-ira", "Ira Novak")
    await service.set_identity_link(
        IdentityLink(tenant_id="demo", developer_id="dev-ira", chat_user_id="U2006")
    )
    await service.create_node(
        "demo", NodeKind.DEVELOPER, "dev-ben", "Ben Sorensen", {"chat_external_id": "U2014"}
    )
    pod = await service.create_node("demo", NodeKind.POD, "pod-1", "Pod")
    await service.create_node(
        "demo",
        NodeKind.WORKSTREAM,
        "ws-resolved",
        "Payments",
        {"owner_id": "dev-asha", "tpm_id": "U2006", "sm_id": "U2014", "phase": "build"},
    )
    await service.create_node(
        "demo",
        NodeKind.WORKSTREAM,
        "ws-unresolved",
        "Search",
        # An id no member carries, and the id of a node that is not a person.
        {"owner_id": "U1002", "tpm_id": pod.id, "sm_id": ""},
    )

    # Neither holds work yet, so the lists leave both out; a direct read opens each.
    workstreams = {
        workstream_id: await directory.get_workstream("demo", workstream_id, as_of)
        for workstream_id in ("ws-resolved", "ws-unresolved")
    }

    assert workstreams["ws-resolved"].people == (
        DirectoryPersonView(key="owner_id", id="dev-asha", member_id="dev-asha", name="Asha Rao"),
        DirectoryPersonView(key="tpm_id", id="U2006", member_id="dev-ira", name="Ira Novak"),
        DirectoryPersonView(key="sm_id", id="U2014", member_id="dev-ben", name="Ben Sorensen"),
    )
    # An unmatched id is kept as is with no name, never guessed; a blank field
    # names nobody.
    assert workstreams["ws-unresolved"].people == (
        DirectoryPersonView(key="owner_id", id="U1002"),
        DirectoryPersonView(key="tpm_id", id=pod.id),
    )
    assert (await directory.get_workstream("demo", "ws-resolved", as_of)).people[0].name == (
        "Asha Rao"
    )
    assert (await directory.list_pods("demo", as_of))[0].people == ()


async def test_directory_service_prefers_a_member_id_over_another_members_chat_id() -> None:
    store = InMemoryGraphStore()
    service = ConfigService(store, store, identity_link_repository=store)
    directory = DirectoryService(store, store)

    await service.create_node("demo", NodeKind.DEVELOPER, "U1002", "Liam Chen")
    await service.create_node(
        "demo", NodeKind.DEVELOPER, "dev-other", "Someone Else", {"chat_external_id": "U1002"}
    )
    await service.create_node(
        "demo", NodeKind.WORKSTREAM, "ws-1", "Payments", {"owner_id": "U1002"}
    )

    workstream = await directory.get_workstream("demo", "ws-1", date(2026, 1, 10))

    assert workstream.people == (
        DirectoryPersonView(key="owner_id", id="U1002", member_id="U1002", name="Liam Chen"),
    )


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


async def _escalation_service() -> tuple[InMemoryGraphStore, ConfigService]:
    """A pod with a scrum master in it, a manager outside it, and one unlinked member."""
    store = InMemoryGraphStore()
    service = ConfigService(store, store, identity_link_repository=store)
    await service.create_node("demo", NodeKind.POD, "pod-1", "Pod")
    for member_id, name in (("sam", "Sam SM"), ("mia", "Mia Manager"), ("nolink", "No Link")):
        await service.create_node("demo", NodeKind.DEVELOPER, member_id, name)
    await service.link_pod_member("demo", "pod-1", "sam", "scrum_master", date(2026, 1, 1))
    await service.set_identity_link(
        IdentityLink(tenant_id="demo", developer_id="sam", chat_user_id="U-SM")
    )
    await service.set_identity_link(
        IdentityLink(tenant_id="demo", developer_id="mia", chat_user_id="U-MGR")
    )
    return store, service


def _member(member_id: str) -> EscalationContactChoice:
    return EscalationContactChoice(member_id=member_id)


async def test_pod_escalation_contacts_picked_from_members_round_trip_and_clear() -> None:
    store, service = await _escalation_service()

    empty = await service.get_pod_escalation_contacts("demo", "pod-1")
    assert empty.scrum_master is None
    assert empty.manager is None

    saved = await service.set_pod_escalation_contacts(
        "demo",
        "pod-1",
        PodEscalationContactChoices(scrum_master=_member("sam"), manager=_member("mia")),
    )
    assert saved.scrum_master is not None
    assert saved.scrum_master.member_id == "sam"

    pod = await store.get_node("demo", "pod-1")
    assert pod is not None
    # The chat id and name come from the member and its identity link, never typed.
    assert pod.metadata["escalation_sm_member_id"] == "sam"
    assert pod.metadata["escalation_sm_chat_external_id"] == "U-SM"
    assert pod.metadata["escalation_sm_display_name"] == "Sam SM"
    assert pod.metadata["escalation_manager_member_id"] == "mia"

    reloaded = await service.get_pod_escalation_contacts("demo", "pod-1")
    assert reloaded.scrum_master is not None
    assert reloaded.scrum_master.chat_external_id == "U-SM"
    assert reloaded.scrum_master.display_name == "Sam SM"
    assert reloaded.manager is not None
    assert reloaded.manager.chat_external_id == "U-MGR"
    assert reloaded.manager.member_id == "mia"

    cleared = await service.set_pod_escalation_contacts(
        "demo", "pod-1", PodEscalationContactChoices()
    )
    assert cleared.scrum_master is None
    reloaded_after_clear = await service.get_pod_escalation_contacts("demo", "pod-1")
    assert reloaded_after_clear.scrum_master is None
    assert reloaded_after_clear.manager is None
    pod = await store.get_node("demo", "pod-1")
    assert pod is not None
    assert pod.metadata["escalation_sm_member_id"] is None


async def test_pod_escalation_contacts_follow_the_members_identity_link() -> None:
    _, service = await _escalation_service()
    await service.set_pod_escalation_contacts(
        "demo", "pod-1", PodEscalationContactChoices(scrum_master=_member("sam"))
    )

    await service.set_identity_link(
        IdentityLink(tenant_id="demo", developer_id="sam", chat_user_id="U-SM-NEW")
    )
    await service.update_node("demo", "sam", NodeKind.DEVELOPER, name="Sam Renamed")

    reloaded = await service.get_pod_escalation_contacts("demo", "pod-1")
    assert reloaded.scrum_master is not None
    assert reloaded.scrum_master.member_id == "sam"
    assert reloaded.scrum_master.chat_external_id == "U-SM-NEW"
    assert reloaded.scrum_master.display_name == "Sam Renamed"


async def test_pod_escalation_contacts_reject_unlinked_or_unknown_members() -> None:
    _, service = await _escalation_service()

    with pytest.raises(ConfigValidationError, match="No Link has no chat ID linked"):
        await service.set_pod_escalation_contacts(
            "demo", "pod-1", PodEscalationContactChoices(scrum_master=_member("nolink"))
        )
    with pytest.raises(GraphNotFound, match="member ghost was not found"):
        await service.set_pod_escalation_contacts(
            "demo", "pod-1", PodEscalationContactChoices(manager=_member("ghost"))
        )
    with pytest.raises(ConfigValidationError, match="U-NOBODY is not linked to any member"):
        await service.set_pod_escalation_contacts(
            "demo",
            "pod-1",
            PodEscalationContactChoices(
                manager=EscalationContactChoice(chat_external_id="U-NOBODY")
            ),
        )


async def test_pod_escalation_contacts_keep_a_legacy_chat_id_contact() -> None:
    store, service = await _escalation_service()
    pod = await store.get_node("demo", "pod-1")
    assert pod is not None
    # Saved by hand before contacts were picked from members.
    await store.upsert_node(
        replace(
            pod,
            metadata={
                **pod.metadata,
                "escalation_sm_chat_external_id": "U-OLD",
                "escalation_sm_display_name": "Old SM",
                "escalation_manager_chat_external_id": "U-MGR",
                "escalation_manager_display_name": "Typed Name",
            },
        )
    )

    opened = await service.get_pod_escalation_contacts("demo", "pod-1")
    assert opened.scrum_master is not None
    assert opened.scrum_master.chat_external_id == "U-OLD"
    assert opened.scrum_master.display_name == "Old SM"
    assert opened.scrum_master.member_id is None
    # A typed chat id that is a member's linked one resolves to that member.
    assert opened.manager is not None
    assert opened.manager.member_id == "mia"
    assert opened.manager.display_name == "Mia Manager"
    pod = await store.get_node("demo", "pod-1")
    assert pod is not None
    assert pod.metadata.get("escalation_manager_member_id") is None  # reading never rewrites

    saved = await service.set_pod_escalation_contacts(
        "demo",
        "pod-1",
        PodEscalationContactChoices(
            scrum_master=EscalationContactChoice(chat_external_id="U-OLD"),
            manager=EscalationContactChoice(chat_external_id="U-MGR"),
        ),
    )
    assert saved.scrum_master is not None
    assert saved.scrum_master.chat_external_id == "U-OLD"
    assert saved.scrum_master.display_name == "Old SM"
    assert saved.scrum_master.member_id is None
    pod = await store.get_node("demo", "pod-1")
    assert pod is not None
    assert pod.metadata["escalation_sm_chat_external_id"] == "U-OLD"
    assert pod.metadata["escalation_sm_member_id"] is None
    assert pod.metadata["escalation_manager_member_id"] == "mia"


async def test_escalation_candidates_list_pod_members_first() -> None:
    store, service = await _escalation_service()
    await service.create_node("demo", NodeKind.DEVELOPER, "aaron", "Aaron Former")
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="pod-1",
            to_node_id="aaron",
            kind=EdgeKind.CONTAINS,
            valid_from=date(2025, 1, 1),
            valid_to=date(2026, 1, 15),
            metadata={"role": "scrum_master"},
        )
    )
    await service.link_pod_member("demo", "pod-1", "nolink", "developer", date(2026, 1, 1))

    candidates = await service.list_escalation_candidates("demo", "pod-1", date(2026, 2, 1))

    assert [(c.member_id, c.in_pod, c.pod_role, c.chat_user_id) for c in candidates] == [
        ("nolink", True, "developer", None),
        ("sam", True, "scrum_master", "U-SM"),
        ("aaron", False, None, None),
        ("mia", False, None, "U-MGR"),
    ]
    with pytest.raises(GraphNotFound):
        await service.list_escalation_candidates("demo", "missing-pod", date(2026, 2, 1))


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


async def test_auto_match_resolves_jira_account_ids_from_email() -> None:
    # Jira finds issues by accountId only. Auto-match fills the email from the
    # directory and must also resolve it, or the member's work is never found.
    # An admin-entered Jira email (Raj's differs from his Slack one) resolves too.
    store = InMemoryGraphStore()
    directory = FakeDirectoryUserRepository()
    tracker = FakeIssueTracker(
        user_emails={"asha@example.com": "acct-asha", "r.iyer@corp.example": "acct-raj"}
    )
    service = ConfigService(
        store, store, directory, identity_link_repository=store, issue_tracker=tracker
    )
    await directory.upsert_users(
        [
            DirectoryUser(tenant_id="demo", external_id=external_id, display_name=name, email=email)
            for external_id, name, email in (
                ("U1001", "Asha Rao", "asha@example.com"),
                ("U1002", "Raj Iyer", "raj@example.com"),
                ("U1003", "Elena Fischer", "elena@example.com"),
            )
        ]
    )
    for member_id in ("U1001", "U1002", "U1003"):
        await service.add_member_from_directory("demo", member_id)
    await service.set_identity_link(
        IdentityLink(tenant_id="demo", developer_id="U1002", jira_email="r.iyer@corp.example")
    )

    result = await service.auto_match_identity_links("demo")

    filled = {member.id: set(member.filled) for member in result.members}
    assert filled["U1001"] == {"chat_user_id", "jira_email", "jira_account_id"}
    assert filled["U1002"] == {"chat_user_id", "jira_account_id"}
    # Elena has no tracker account: email filled, nothing invented.
    assert filled["U1003"] == {"chat_user_id", "jira_email"}
    asha = await service.get_identity_link("demo", "U1001")
    raj = await service.get_identity_link("demo", "U1002")
    elena = await service.get_identity_link("demo", "U1003")
    assert asha is not None and asha.jira_account_id == "acct-asha"
    assert raj is not None and raj.jira_account_id == "acct-raj"
    assert raj.jira_email == "r.iyer@corp.example"
    assert elena is not None and elena.jira_account_id is None


async def test_list_unmapped_members_flags_missing_tracker_link_when_required() -> None:
    store = InMemoryGraphStore()
    directory = FakeDirectoryUserRepository()
    service = ConfigService(
        store, store, directory, identity_link_repository=store, require_issue_tracker_link=True
    )
    await directory.upsert_users(
        [
            DirectoryUser(tenant_id="demo", external_id="U1001", display_name="Asha Rao"),
            DirectoryUser(tenant_id="demo", external_id="U1002", display_name="Omar Haddad"),
        ]
    )
    await service.add_member_from_directory("demo", "U1001")
    await service.add_member_from_directory("demo", "U1002")
    await service.set_identity_link(
        IdentityLink(
            tenant_id="demo", developer_id="U1001", chat_user_id="U1001", jira_account_id="a-1"
        )
    )
    # Reachable on chat, but the tracker cannot attribute anything to him.
    await service.set_identity_link(
        IdentityLink(tenant_id="demo", developer_id="U1002", chat_user_id="U1002")
    )

    unmapped = await service.list_unmapped_members("demo")

    assert [member.id for member in unmapped] == ["U1002"]
    assert "jira_account_id" in unmapped[0].missing
    assert "chat_user_id" not in unmapped[0].missing
