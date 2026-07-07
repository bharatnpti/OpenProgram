from __future__ import annotations

from datetime import date

import pytest

from core.application.sync_targets import RuntimeSyncTargetResolver, SyncTargetValidationError
from core.domain.graph import EdgeKind, GraphEdge, GraphNode, NodeKind
from infra.persistence.in_memory_graph import InMemoryGraphStore


async def test_runtime_sync_target_resolver_combines_jira_and_dedupes_github_repos() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(
        GraphNode(
            tenant_id="demo",
            id="project-alpha",
            kind=NodeKind.PROJECT,
            name="Alpha",
            metadata={
                "jira_project_key": "PO",
                "jira_board_id": "board-1",
                "github_repos": "oneai/openprogram, oneai/api",
            },
        )
    )
    await store.upsert_node(
        GraphNode(
            tenant_id="demo",
            id="project-beta",
            kind=NodeKind.PROJECT,
            name="Beta",
            metadata={
                "jira_base_jql": 'labels = "platform"',
                "github_repos": "oneai/openprogram",
            },
        )
    )
    await store.upsert_node(
        GraphNode(
            tenant_id="demo",
            id="pod-runtime",
            kind=NodeKind.POD,
            name="Runtime",
            metadata={"jira_filter_jql": "component = API", "github_repos": "oneai/api"},
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="project-alpha",
            to_node_id="pod-runtime",
            kind=EdgeKind.CONTAINS,
        )
    )

    targets = await RuntimeSyncTargetResolver(store).resolve("demo", as_of=date(2026, 1, 10))

    issue_payloads = {
        item.payload["target_node_id"]: item.payload for item in targets.issue_dispatches
    }
    assert issue_payloads["project-alpha"] == {
        "jql": 'project = "PO"',
        "target_node_id": "project-alpha",
        "target_node_kind": "project",
        "cursor_scope": issue_payloads["project-alpha"]["cursor_scope"],
        "project_key": "PO",
        "board_id": "board-1",
    }
    assert issue_payloads["pod-runtime"]["jql"] == '(project = "PO") AND (component = API)'
    assert issue_payloads["pod-runtime"]["target_node_kind"] == "pod"
    assert issue_payloads["project-beta"]["jql"] == 'labels = "platform"'
    assert issue_payloads["project-alpha"]["cursor_scope"].startswith(
        "query:project:project-alpha:"
    )
    assert issue_payloads["pod-runtime"]["cursor_scope"].startswith("query:pod:pod-runtime:")

    vcs_payloads = {item.payload["repo_name"]: item.payload for item in targets.vcs_dispatches}
    assert vcs_payloads == {
        "oneai/api": {"repo_name": "oneai/api", "container_ids": "pod-runtime,project-alpha"},
        "oneai/openprogram": {
            "repo_name": "oneai/openprogram",
            "container_ids": "project-alpha,project-beta",
        },
    }


async def test_runtime_sync_target_resolver_empty_integration_fields_produce_no_targets() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(
        GraphNode(tenant_id="demo", id="project-empty", kind=NodeKind.PROJECT, name="Empty")
    )
    await store.upsert_node(
        GraphNode(
            tenant_id="demo",
            id="pod-filter-only",
            kind=NodeKind.POD,
            name="Filter Only",
            metadata={"jira_filter_jql": "component = API"},
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="project-empty",
            to_node_id="pod-filter-only",
            kind=EdgeKind.CONTAINS,
        )
    )

    targets = await RuntimeSyncTargetResolver(store).resolve("demo", as_of=date(2026, 1, 10))

    assert targets.issue_dispatches == ()
    assert targets.vcs_dispatches == ()


async def test_runtime_sync_target_resolver_query_hash_changes_when_jql_changes() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(
        GraphNode(
            tenant_id="demo",
            id="project-alpha",
            kind=NodeKind.PROJECT,
            name="Alpha",
            metadata={"jira_project_key": "PO"},
        )
    )
    first = await RuntimeSyncTargetResolver(store).resolve("demo", as_of=date(2026, 1, 10))

    await store.upsert_node(
        GraphNode(
            tenant_id="demo",
            id="project-alpha",
            kind=NodeKind.PROJECT,
            name="Alpha",
            metadata={"jira_project_key": "ENG"},
        )
    )
    second = await RuntimeSyncTargetResolver(store).resolve("demo", as_of=date(2026, 1, 10))

    assert first.issue_dispatches[0].scope != second.issue_dispatches[0].scope


async def test_runtime_sync_target_resolver_rejects_pod_repo_outside_project_allowlist() -> None:
    store = InMemoryGraphStore()
    await store.upsert_node(
        GraphNode(
            tenant_id="demo",
            id="project-alpha",
            kind=NodeKind.PROJECT,
            name="Alpha",
            metadata={"github_repos": "oneai/openprogram"},
        )
    )
    await store.upsert_node(
        GraphNode(
            tenant_id="demo",
            id="pod-runtime",
            kind=NodeKind.POD,
            name="Runtime",
            metadata={"github_repos": "oneai/api"},
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id="demo",
            from_node_id="project-alpha",
            to_node_id="pod-runtime",
            kind=EdgeKind.CONTAINS,
        )
    )

    with pytest.raises(SyncTargetValidationError, match="outside linked project scope"):
        await RuntimeSyncTargetResolver(store).resolve("demo", as_of=date(2026, 1, 10))
