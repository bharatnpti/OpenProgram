from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from core.domain.errors import GraphNotFound, PulseOpsError
from core.domain.directory import DirectoryUser
from core.domain.graph import EdgeKind, GraphEdge, GraphNode, JsonScalar, NodeKind
from core.domain.rollup import Rag
from core.domain.status import CheckInPreference, StatusSource
from core.ports.directory import DirectoryUserRepository
from core.ports.repositories import GraphRepository, RollupRepository, StatusRepository


class ConfigValidationError(PulseOpsError):
    """Raised when a requested runtime config mutation is invalid."""


class ConfigConflict(PulseOpsError):
    """Raised when a requested runtime config mutation would duplicate state."""


@dataclass(frozen=True, kw_only=True)
class DirectoryItemView:
    id: str
    kind: NodeKind
    name: str
    description: str | None
    code: str | None
    metadata: Mapping[str, JsonScalar]
    rag: Rag | None = None
    source: StatusSource | None = None
    program_ids: tuple[str, ...] = ()
    project_ids: tuple[str, ...] = ()
    pod_ids: tuple[str, ...] = ()
    member_ids: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()


class ConfigService:
    def __init__(
        self,
        graph_repository: GraphRepository,
        status_repository: StatusRepository,
        directory_repository: DirectoryUserRepository | None = None,
    ) -> None:
        self._graph_repository = graph_repository
        self._status_repository = status_repository
        self._directory_repository = directory_repository

    async def list_nodes(self, tenant_id: str, kind: NodeKind) -> list[GraphNode]:
        return await self._graph_repository.list_nodes(tenant_id, kind)

    async def get_node(self, tenant_id: str, id: str, kind: NodeKind) -> GraphNode:
        return await self._ensure_node(tenant_id, id, kind)

    async def create_node(
        self,
        tenant_id: str,
        kind: NodeKind,
        id: str,
        name: str,
        metadata: Mapping[str, JsonScalar] | None = None,
    ) -> GraphNode:
        normalized_id = _clean_required(id, "id")
        normalized_name = _clean_required(name, "name")
        existing = await self._graph_repository.get_node(tenant_id, normalized_id)
        if existing is not None:
            raise ConfigConflict(f"node {normalized_id} already exists")
        node = GraphNode(
            tenant_id=tenant_id,
            id=normalized_id,
            kind=kind,
            name=normalized_name,
            metadata=_metadata(metadata),
        )
        await self._graph_repository.upsert_node(node)
        return node

    async def update_node(
        self,
        tenant_id: str,
        id: str,
        kind: NodeKind,
        name: str | None = None,
        metadata: Mapping[str, JsonScalar] | None = None,
    ) -> GraphNode:
        existing = await self._ensure_node(tenant_id, id, kind)
        updated_metadata = dict(existing.metadata)
        if metadata is not None:
            updated_metadata.update(_metadata(metadata))
        updated = GraphNode(
            tenant_id=existing.tenant_id,
            id=existing.id,
            kind=existing.kind,
            name=_clean_required(name, "name") if name is not None else existing.name,
            metadata=updated_metadata,
        )
        await self._graph_repository.upsert_node(updated)
        return updated

    async def delete_node(self, tenant_id: str, id: str, kind: NodeKind) -> None:
        existing = await self._ensure_node(tenant_id, id, kind)
        await self._graph_repository.delete_node(existing.tenant_id, existing.id)
        if kind is NodeKind.DEVELOPER:
            await self._status_repository.delete_checkin_preference(tenant_id, id)

    async def list_edges(self, tenant_id: str) -> list[GraphEdge]:
        return await self._graph_repository.list_edges(tenant_id)

    async def link_program_project(
        self,
        tenant_id: str,
        program_id: str,
        project_id: str,
    ) -> GraphEdge:
        await self._ensure_node(tenant_id, program_id, NodeKind.PROGRAM)
        await self._ensure_node(tenant_id, project_id, NodeKind.PROJECT)
        return await self._add_unique_edge(
            tenant_id,
            program_id,
            project_id,
            EdgeKind.CONTAINS,
        )

    async def unlink_program_project(
        self,
        tenant_id: str,
        program_id: str | None,
        project_id: str,
    ) -> None:
        await self._ensure_node(tenant_id, project_id, NodeKind.PROJECT)
        edges = await self._graph_repository.list_edges(
            tenant_id,
            from_node_id=program_id,
            to_node_id=project_id,
            kind=EdgeKind.CONTAINS,
        )
        await self._remove_edges_from_kind(
            tenant_id,
            edges,
            from_kind=NodeKind.PROGRAM,
            not_found=f"program link for project {project_id} was not found",
        )

    async def link_project_pod(
        self,
        tenant_id: str,
        project_id: str,
        pod_id: str,
    ) -> GraphEdge:
        await self._ensure_node(tenant_id, project_id, NodeKind.PROJECT)
        await self._ensure_node(tenant_id, pod_id, NodeKind.POD)
        return await self._add_unique_edge(
            tenant_id,
            project_id,
            pod_id,
            EdgeKind.CONTAINS,
        )

    async def unlink_project_pod(
        self,
        tenant_id: str,
        project_id: str,
        pod_id: str,
    ) -> None:
        await self._ensure_node(tenant_id, project_id, NodeKind.PROJECT)
        await self._ensure_node(tenant_id, pod_id, NodeKind.POD)
        await self._remove_exact_edge(tenant_id, project_id, pod_id, EdgeKind.CONTAINS)

    async def link_pod_member(
        self,
        tenant_id: str,
        pod_id: str,
        member_id: str,
        role: str,
        valid_from: date,
    ) -> GraphEdge:
        await self._ensure_node(tenant_id, pod_id, NodeKind.POD)
        await self._ensure_node(tenant_id, member_id, NodeKind.DEVELOPER)
        return await self._add_unique_edge(
            tenant_id,
            pod_id,
            member_id,
            EdgeKind.CONTAINS,
            valid_from=valid_from,
            metadata={"role": _clean_required(role, "role")},
        )

    async def unlink_pod_member(self, tenant_id: str, pod_id: str, member_id: str) -> None:
        await self._ensure_node(tenant_id, pod_id, NodeKind.POD)
        await self._ensure_node(tenant_id, member_id, NodeKind.DEVELOPER)
        await self._remove_exact_edge(tenant_id, pod_id, member_id, EdgeKind.CONTAINS)

    async def assign_member_task(
        self,
        tenant_id: str,
        member_id: str,
        task_id: str,
    ) -> GraphEdge:
        await self._ensure_node(tenant_id, member_id, NodeKind.DEVELOPER)
        await self._ensure_node(tenant_id, task_id, NodeKind.TASK)
        return await self._add_unique_edge(
            tenant_id,
            member_id,
            task_id,
            EdgeKind.ASSIGNED_TO,
        )

    async def unassign_member_task(
        self,
        tenant_id: str,
        member_id: str,
        task_id: str,
    ) -> None:
        await self._ensure_node(tenant_id, member_id, NodeKind.DEVELOPER)
        await self._ensure_node(tenant_id, task_id, NodeKind.TASK)
        await self._remove_exact_edge(tenant_id, member_id, task_id, EdgeKind.ASSIGNED_TO)

    async def get_checkin_preference(
        self,
        tenant_id: str,
        member_id: str,
    ) -> CheckInPreference | None:
        await self._ensure_node(tenant_id, member_id, NodeKind.DEVELOPER)
        return await self._status_repository.checkin_preference_for(tenant_id, member_id)

    async def record_checkin_preference(self, preference: CheckInPreference) -> CheckInPreference:
        await self._ensure_node(preference.tenant_id, preference.developer_id, NodeKind.DEVELOPER)
        await self._status_repository.record_checkin_preference(preference)
        return preference

    async def list_checkin_preferences(self, tenant_id: str) -> list[CheckInPreference]:
        return await self._status_repository.list_checkin_preferences(tenant_id)

    async def search_directory(
        self,
        tenant_id: str,
        query: str = "",
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[DirectoryUser], int]:
        repository = self._directory_repository_or_raise()
        users = await repository.search(tenant_id, query=query, limit=limit, offset=offset)
        count = await repository.count(tenant_id, query=query)
        return users, count

    async def add_member_from_directory(self, tenant_id: str, external_id: str) -> GraphNode:
        repository = self._directory_repository_or_raise()
        directory_user = await repository.get(tenant_id, external_id)
        if directory_user is None:
            raise GraphNotFound(f"directory user {external_id} not found for tenant {tenant_id}")
        existing = await self._graph_repository.get_node(tenant_id, external_id)
        if existing is not None:
            if existing.kind is not NodeKind.DEVELOPER:
                raise GraphNotFound(f"{external_id} exists as a {existing.kind.value}, not a developer")
            return existing
        node = GraphNode(
            tenant_id=tenant_id,
            id=external_id,
            kind=NodeKind.DEVELOPER,
            name=directory_user.display_name,
            metadata=_directory_metadata(directory_user),
        )
        await self._graph_repository.upsert_node(node)
        return node

    async def _ensure_node(
        self,
        tenant_id: str,
        id: str,
        kind: NodeKind,
    ) -> GraphNode:
        node = await self._graph_repository.get_node(tenant_id, id)
        if node is None:
            raise GraphNotFound(f"{kind.value} {id} not found for tenant {tenant_id}")
        if node.kind is not kind:
            raise GraphNotFound(f"{id} exists as a {node.kind.value}, not a {kind.value}")
        return node

    async def _add_unique_edge(
        self,
        tenant_id: str,
        from_node_id: str,
        to_node_id: str,
        kind: EdgeKind,
        *,
        valid_from: date | None = None,
        metadata: Mapping[str, JsonScalar] | None = None,
    ) -> GraphEdge:
        if from_node_id == to_node_id:
            raise ConfigValidationError("self links are not allowed")
        existing = await self._graph_repository.list_edges(
            tenant_id,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            kind=kind,
        )
        if existing:
            raise ConfigConflict(f"{kind.value} link already exists")
        edge = GraphEdge(
            tenant_id=tenant_id,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            kind=kind,
            valid_from=valid_from,
            metadata=_metadata(metadata),
        )
        await self._graph_repository.add_edge(edge)
        return edge

    async def _remove_exact_edge(
        self,
        tenant_id: str,
        from_node_id: str,
        to_node_id: str,
        kind: EdgeKind,
    ) -> None:
        edges = await self._graph_repository.list_edges(
            tenant_id,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            kind=kind,
        )
        if not edges:
            raise GraphNotFound(f"{kind.value} link was not found")
        for edge in edges:
            await self._graph_repository.remove_edge(edge)

    async def _remove_edges_from_kind(
        self,
        tenant_id: str,
        edges: list[GraphEdge],
        *,
        from_kind: NodeKind,
        not_found: str,
    ) -> None:
        matching: list[GraphEdge] = []
        for edge in edges:
            from_node = await self._graph_repository.get_node(tenant_id, edge.from_node_id)
            if from_node is not None and from_node.kind is from_kind:
                matching.append(edge)
        if not matching:
            raise GraphNotFound(not_found)
        for edge in matching:
            await self._graph_repository.remove_edge(edge)

    def _directory_repository_or_raise(self) -> DirectoryUserRepository:
        if self._directory_repository is None:
            raise ConfigValidationError("directory repository is not configured")
        return self._directory_repository


class DirectoryService:
    def __init__(
        self,
        graph_repository: GraphRepository,
        rollup_repository: RollupRepository,
    ) -> None:
        self._graph_repository = graph_repository
        self._rollup_repository = rollup_repository

    async def list_programs(self, tenant_id: str, as_of: date) -> list[DirectoryItemView]:
        return await self._list_items(tenant_id, NodeKind.PROGRAM, as_of)

    async def list_projects(self, tenant_id: str, as_of: date) -> list[DirectoryItemView]:
        return await self._list_items(tenant_id, NodeKind.PROJECT, as_of)

    async def list_pods(self, tenant_id: str, as_of: date) -> list[DirectoryItemView]:
        return await self._list_items(tenant_id, NodeKind.POD, as_of)

    async def _list_items(
        self,
        tenant_id: str,
        kind: NodeKind,
        as_of: date,
    ) -> list[DirectoryItemView]:
        nodes = await self._graph_repository.list_nodes(tenant_id)
        node_by_id = {node.id: node for node in nodes}
        selected = [node for node in nodes if node.kind is kind]
        edges = await self._graph_repository.list_edges(tenant_id)
        status_by_ref = {
            (status.entity_ref.kind, status.entity_ref.id): status
            for status in await self._rollup_repository.list_node_statuses(tenant_id, as_of)
        }
        views: list[DirectoryItemView] = []
        for node in selected:
            status = status_by_ref.get((node.kind, node.id))
            outgoing = [
                edge
                for edge in edges
                if edge.from_node_id == node.id and edge.kind is EdgeKind.CONTAINS
            ]
            incoming = [
                edge
                for edge in edges
                if edge.to_node_id == node.id and edge.kind is EdgeKind.CONTAINS
            ]
            assignments = [
                edge
                for edge in edges
                if edge.from_node_id == node.id and edge.kind is EdgeKind.ASSIGNED_TO
            ]
            views.append(
                DirectoryItemView(
                    id=node.id,
                    kind=node.kind,
                    name=node.name,
                    description=_string_metadata(node, "description"),
                    code=_string_metadata(node, "code"),
                    metadata=node.metadata,
                    rag=status.rag if status else None,
                    source=status.source if status else None,
                    program_ids=_source_ids(incoming, node_by_id, NodeKind.PROGRAM),
                    project_ids=tuple(
                        dict.fromkeys(
                            (
                                *_source_ids(incoming, node_by_id, NodeKind.PROJECT),
                                *_target_ids(outgoing, node_by_id, NodeKind.PROJECT),
                            )
                        )
                    ),
                    pod_ids=tuple(
                        dict.fromkeys(
                            (
                                *_source_ids(incoming, node_by_id, NodeKind.POD),
                                *_target_ids(outgoing, node_by_id, NodeKind.POD),
                            )
                        )
                    ),
                    member_ids=_target_ids(outgoing, node_by_id, NodeKind.DEVELOPER),
                    task_ids=tuple(
                        dict.fromkeys(
                            (
                                *_target_ids(outgoing, node_by_id, NodeKind.TASK),
                                *_target_ids(assignments, node_by_id, NodeKind.TASK),
                            )
                        )
                    ),
                )
            )
        return sorted(views, key=lambda item: (item.name, item.id))


def _source_ids(
    edges: list[GraphEdge],
    node_by_id: Mapping[str, GraphNode],
    kind: NodeKind,
) -> tuple[str, ...]:
    ids: list[str] = []
    for edge in edges:
        node = node_by_id.get(edge.from_node_id)
        if node is not None and node.kind is kind and node.id not in ids:
            ids.append(node.id)
    return tuple(sorted(ids))


def _target_ids(
    edges: list[GraphEdge],
    node_by_id: Mapping[str, GraphNode],
    kind: NodeKind,
) -> tuple[str, ...]:
    ids: list[str] = []
    for edge in edges:
        node = node_by_id.get(edge.to_node_id)
        if node is not None and node.kind is kind and node.id not in ids:
            ids.append(node.id)
    return tuple(sorted(ids))


def _metadata(metadata: Mapping[str, JsonScalar] | None) -> dict[str, JsonScalar]:
    if metadata is None:
        return {}
    return {
        key: value
        for key, value in metadata.items()
        if isinstance(key, str) and (value is None or isinstance(value, str | int | float | bool))
    }


def _clean_required(value: str, field: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ConfigValidationError(f"{field} must not be empty")
    return cleaned


def _string_metadata(node: GraphNode, key: str) -> str | None:
    value = node.metadata.get(key)
    return value if isinstance(value, str) and value else None


def _directory_metadata(user: object) -> dict[str, JsonScalar]:
    metadata: dict[str, JsonScalar] = {}
    for key in ("email", "handle", "avatar_url", "source"):
        value = getattr(user, key, None)
        if isinstance(value, str) and value:
            metadata[key if key != "source" else "source"] = value
    external_id = getattr(user, "external_id", None)
    if isinstance(external_id, str) and external_id:
        metadata["chat_external_id"] = external_id
    return metadata
