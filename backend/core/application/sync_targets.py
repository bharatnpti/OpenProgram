from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from hashlib import sha256

from core.domain.errors import OpenProgramError
from core.domain.graph import EdgeKind, GraphEdge, GraphNode, NodeKind
from core.domain.workflows import SyncDispatchInput
from core.ports.repositories import GraphRepository


class SyncTargetValidationError(OpenProgramError):
    """Raised when runtime integration scope configuration is invalid."""


@dataclass(frozen=True, kw_only=True)
class RuntimeSyncTargets:
    issue_dispatches: tuple[SyncDispatchInput, ...]
    vcs_dispatches: tuple[SyncDispatchInput, ...]

    def by_connector(self, connector: str | None = None) -> tuple[SyncDispatchInput, ...]:
        if connector == "issue":
            return self.issue_dispatches
        if connector == "vcs":
            return self.vcs_dispatches
        return self.issue_dispatches + self.vcs_dispatches


class RuntimeSyncTargetResolver:
    def __init__(self, graph_repository: GraphRepository) -> None:
        self._graph_repository = graph_repository

    async def resolve(
        self,
        tenant_id: str,
        *,
        as_of: date | None = None,
    ) -> RuntimeSyncTargets:
        effective_date = as_of or date.today()
        projects = await self._graph_repository.list_nodes(tenant_id, NodeKind.PROJECT)
        pods = await self._graph_repository.list_nodes(tenant_id, NodeKind.POD)
        edges = [
            edge
            for edge in await self._graph_repository.list_edges(tenant_id, kind=EdgeKind.CONTAINS)
            if edge.is_active_on(effective_date)
        ]

        projects_by_id = {project.id: project for project in projects}
        pods_by_id = {pod.id: pod for pod in pods}
        project_pod_edges = tuple(
            edge
            for edge in edges
            if edge.from_node_id in projects_by_id and edge.to_node_id in pods_by_id
        )

        issue_dispatches = _project_issue_dispatches(tenant_id, projects)
        issue_dispatches += _pod_issue_dispatches(
            tenant_id,
            project_pod_edges,
            projects_by_id,
            pods_by_id,
        )
        vcs_dispatches = _vcs_dispatches(projects, pods, project_pod_edges)
        return RuntimeSyncTargets(
            issue_dispatches=tuple(issue_dispatches),
            vcs_dispatches=tuple(vcs_dispatches),
        )


def _project_issue_dispatches(
    tenant_id: str,
    projects: list[GraphNode],
) -> list[SyncDispatchInput]:
    dispatches: list[SyncDispatchInput] = []
    for project in projects:
        query = _project_jql(project)
        if query is None:
            continue
        scope = _query_scope(project, query)
        payload: dict[str, str | int | float | bool | None] = {
            "jql": query,
            "target_node_id": project.id,
            "target_node_kind": project.kind.value,
            "cursor_scope": scope,
        }
        project_key = _metadata_string(project, "jira_project_key")
        board_id = _metadata_string(project, "jira_board_id")
        if project_key is not None:
            payload["project_key"] = project_key
        if board_id is not None:
            payload["board_id"] = board_id
        dispatches.append(
            SyncDispatchInput(
                tenant_id=tenant_id,
                connector="issue",
                scope=scope,
                payload=payload,
            )
        )
    return dispatches


def _pod_issue_dispatches(
    tenant_id: str,
    project_pod_edges: tuple[GraphEdge, ...],
    projects_by_id: dict[str, GraphNode],
    pods_by_id: dict[str, GraphNode],
) -> list[SyncDispatchInput]:
    dispatches: list[SyncDispatchInput] = []
    for edge in project_pod_edges:
        project = projects_by_id[edge.from_node_id]
        pod = pods_by_id[edge.to_node_id]
        base_query = _project_jql(project)
        pod_filter = _metadata_string(pod, "jira_filter_jql")
        if base_query is None or pod_filter is None:
            continue
        query = f"({base_query}) AND ({pod_filter})"
        scope = _query_scope(pod, query)
        payload: dict[str, str | int | float | bool | None] = {
            "jql": query,
            "target_node_id": pod.id,
            "target_node_kind": pod.kind.value,
            "cursor_scope": scope,
        }
        project_key = _metadata_string(project, "jira_project_key")
        if project_key is not None:
            payload["project_key"] = project_key
        dispatches.append(
            SyncDispatchInput(
                tenant_id=tenant_id,
                connector="issue",
                scope=scope,
                payload=payload,
            )
        )
    return dispatches


def _vcs_dispatches(
    projects: list[GraphNode],
    pods: list[GraphNode],
    project_pod_edges: tuple[GraphEdge, ...],
) -> list[SyncDispatchInput]:
    if not projects:
        return []
    tenant_id = projects[0].tenant_id
    project_repos = {
        project.id: tuple(_metadata_list(project, "github_repos")) for project in projects
    }
    pod_project_ids: dict[str, set[str]] = defaultdict(set)
    for edge in project_pod_edges:
        pod_project_ids[edge.to_node_id].add(edge.from_node_id)

    repo_containers: dict[str, set[str]] = defaultdict(set)
    for project in projects:
        for repo in project_repos[project.id]:
            repo_containers[repo].add(project.id)

    for pod in pods:
        pod_repos = tuple(_metadata_list(pod, "github_repos"))
        if not pod_repos:
            continue
        allowed_repos = {
            repo
            for project_id in pod_project_ids.get(pod.id, set())
            for repo in project_repos.get(project_id, ())
        }
        missing = tuple(repo for repo in pod_repos if repo not in allowed_repos)
        if missing:
            raise SyncTargetValidationError(
                f"pod {pod.id} references GitHub repos outside linked project scope: "
                f"{', '.join(missing)}"
            )
        for repo in pod_repos:
            repo_containers[repo].add(pod.id)

    return [
        SyncDispatchInput(
            tenant_id=tenant_id,
            connector="vcs",
            scope=f"repo:{repo}",
            payload={
                "repo_name": repo,
                "container_ids": ",".join(sorted(containers)),
            },
        )
        for repo, containers in sorted(repo_containers.items())
    ]


def _project_jql(project: GraphNode) -> str | None:
    base_jql = _metadata_string(project, "jira_base_jql")
    if base_jql is not None:
        return base_jql
    project_key = _metadata_string(project, "jira_project_key")
    if project_key is None:
        return None
    return f'project = "{_escape_jql_string(project_key)}"'


def _query_scope(node: GraphNode, query: str) -> str:
    return f"query:{node.kind.value}:{node.id}:{_query_hash(query)}"


def _query_hash(query: str) -> str:
    normalized = " ".join(query.split())
    return sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _metadata_string(node: GraphNode, key: str) -> str | None:
    value = node.metadata.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _metadata_list(node: GraphNode, key: str) -> list[str]:
    value = node.metadata.get(key)
    if not isinstance(value, str):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for raw_item in value.replace("\n", ",").split(","):
        item = raw_item.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        items.append(item)
    return items


def _escape_jql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
