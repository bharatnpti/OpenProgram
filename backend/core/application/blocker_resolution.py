"""Read-time resolution of developer blockers to pods.

This is the single place the pod-attribution rule lives; rollups, persona
views, and risk detection all consume it so the rule can never fork:

1. An explicit ``pod_id`` on the blocker wins outright.
2. Else a ``work_item_id`` resolves via the graph — the union of direct
   ``pod --CONTAINS--> task`` edges and ``pod --ASSIGNED_TO--> workstream
   --CONTAINS--> task`` paths.
3. Else (or when step 2 finds no pods) the blocker is *unattributed*: it
   applies to every pod containing the developer, flagged so views can mark
   it as possibly not theirs.

Legacy fallback: for a developer with no lifecycle rows at all (including
resolved ones), the flat ``DeveloperStatus.blockers`` strings are synthesized
as unattributed pseudo-blockers so the read path works before the write path
has recorded anything. Sources are never mixed for one developer-date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from core.domain.blockers import DeveloperBlocker
from core.domain.graph import EdgeKind, EntityRef, NodeKind
from core.domain.status import DeveloperStatus
from core.ports.repositories import GraphRepository, StatusRepository

_SYNTHETIC_BLOCKER = "no confirmed reply"


class BlockerProvenance(StrEnum):
    LIFECYCLE = "lifecycle"
    LEGACY_STATUS = "legacy_status"


@dataclass(frozen=True, kw_only=True)
class ResolvedBlocker:
    blocker_id: str
    description: str
    developer_id: str
    first_seen_on: date
    work_item_ref: EntityRef | None
    explicit_pod_ref: EntityRef | None
    pod_ids: tuple[str, ...]
    unattributed: bool
    critical: bool
    provenance: BlockerProvenance


class BlockerResolutionService:
    def __init__(
        self,
        graph_repository: GraphRepository,
        status_repository: StatusRepository,
    ) -> None:
        self._graph_repository = graph_repository
        self._status_repository = status_repository

    async def open_blockers_for_developer(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> tuple[ResolvedBlocker, ...]:
        if await self._status_repository.has_blocker_rows(tenant_id, developer_id):
            blockers = await self._status_repository.open_blockers(tenant_id, developer_id, as_of)
            return tuple([await self._resolve(tenant_id, blocker, as_of) for blocker in blockers])
        status = await self._status_repository.latest_developer_status(
            tenant_id, developer_id, as_of
        )
        return await self._legacy_fallback(tenant_id, developer_id, status, as_of)

    async def _resolve(
        self, tenant_id: str, blocker: DeveloperBlocker, as_of: date
    ) -> ResolvedBlocker:
        work_item_ref: EntityRef | None = None
        explicit_pod_ref: EntityRef | None = None
        critical = False
        if blocker.pod_id is not None:
            pod_ids: tuple[str, ...] = (blocker.pod_id,)
            unattributed = False
            explicit_pod_ref = EntityRef(tenant_id=tenant_id, kind=NodeKind.POD, id=blocker.pod_id)
            if blocker.work_item_id is not None:
                work_item_ref, critical = await self._work_item_details(
                    tenant_id, blocker.developer_id, blocker.work_item_id
                )
        elif blocker.work_item_id is not None:
            work_item_ref, critical = await self._work_item_details(
                tenant_id, blocker.developer_id, blocker.work_item_id
            )
            pods = await self._graph_repository.pods_for_task(
                tenant_id, blocker.work_item_id, as_of
            )
            if pods:
                pod_ids = tuple(pod.id for pod in pods)
                unattributed = False
            else:
                pod_ids = await self._developer_pod_ids(tenant_id, blocker.developer_id, as_of)
                unattributed = True
        else:
            pod_ids = await self._developer_pod_ids(tenant_id, blocker.developer_id, as_of)
            unattributed = True
        return ResolvedBlocker(
            blocker_id=blocker.blocker_id,
            description=blocker.description,
            developer_id=blocker.developer_id,
            first_seen_on=blocker.first_seen_on,
            work_item_ref=work_item_ref,
            explicit_pod_ref=explicit_pod_ref,
            pod_ids=pod_ids,
            unattributed=unattributed,
            critical=critical,
            provenance=BlockerProvenance.LIFECYCLE,
        )

    async def _work_item_details(
        self, tenant_id: str, developer_id: str, work_item_id: str
    ) -> tuple[EntityRef | None, bool]:
        """Fail-soft node lookup: an unknown issue key keeps a TASK-kind ref.

        ``critical`` honors ``critical_path`` on the work-item node or on the
        developer's assignment edge to it (matching the legacy rollup rule).
        """
        node = await self._graph_repository.get_node(tenant_id, work_item_id)
        if node is None:
            return (
                EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id=work_item_id),
                False,
            )
        critical = _truthy(node.metadata.get("critical_path"))
        if not critical:
            edges = await self._graph_repository.list_edges(
                tenant_id,
                from_node_id=developer_id,
                to_node_id=work_item_id,
                kind=EdgeKind.ASSIGNED_TO,
            )
            critical = any(_truthy(edge.metadata.get("critical_path")) for edge in edges)
        return node.ref, critical

    async def _developer_pod_ids(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> tuple[str, ...]:
        pods = await self._graph_repository.pods_containing_developer(
            tenant_id, developer_id, as_of
        )
        return tuple(pod.id for pod in pods)

    async def _legacy_fallback(
        self,
        tenant_id: str,
        developer_id: str,
        status: DeveloperStatus | None,
        as_of: date,
    ) -> tuple[ResolvedBlocker, ...]:
        if status is None:
            return ()
        descriptions = [
            blocker for blocker in status.blockers if blocker.strip().lower() != _SYNTHETIC_BLOCKER
        ]
        if not descriptions:
            return ()
        pod_ids = await self._developer_pod_ids(tenant_id, developer_id, as_of)
        return tuple(
            ResolvedBlocker(
                blocker_id=f"legacy:{developer_id}:{index}",
                description=description,
                developer_id=developer_id,
                first_seen_on=status.as_of,
                work_item_ref=None,
                explicit_pod_ref=None,
                pod_ids=pod_ids,
                unattributed=True,
                critical=False,
                provenance=BlockerProvenance.LEGACY_STATUS,
            )
            for index, description in enumerate(descriptions, start=1)
        )


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if isinstance(value, int | float):
        return bool(value)
    return False
