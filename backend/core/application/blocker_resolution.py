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

A blocker counts where the blocked work is, not where the work it waits on
is. Its issues are the ``work_item_id`` and the issue keys its text names; an
issue assigned to someone else, and not to the developer, is the one waited
on. Zoe's "CHK-11 is waiting on Omar's HTTP client upgrade (CHK-17)" was
recorded on CHK-17, so it applied only to Platform, which Zoe is not in, and
vanished from every view above her (N15). It now applies to Storefront,
through CHK-11; and when the blocked issue has no pod, to the developer's
pods. The pods of the issue waited on that the developer is not in are
``depends_on_pod_ids``: those teams see an incoming dependency, not a blocker.

Legacy fallback: for a developer with no lifecycle rows at all (including
resolved ones), the flat ``DeveloperStatus.blockers`` strings are synthesized
as unattributed pseudo-blockers so the read path works before the write path
has recorded anything. Sources are never mixed for one developer-date.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from core.domain.blockers import DeveloperBlocker
from core.domain.cross_person import issue_keys_in
from core.domain.graph import EdgeKind, EntityRef, GraphNode, NodeKind
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
    #: The work item's name when the graph knows it, so views can label a
    #: blocker's attribution without a second lookup.
    work_item_name: str | None
    explicit_pod_ref: EntityRef | None
    pod_ids: tuple[str, ...]
    unattributed: bool
    critical: bool
    provenance: BlockerProvenance
    #: Other teams' pods whose work this blocker waits on: the pods of an
    #: issue assigned to someone else, less the developer's own pods.
    depends_on_pod_ids: tuple[str, ...] = ()
    depends_on_pod_names: tuple[str, ...] = ()
    #: The issue waited on, when it sits with one of those pods.
    depends_on_ref: EntityRef | None = None

    @property
    def cross_team(self) -> bool:
        return bool(self.depends_on_pod_ids)


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

    async def issue_placement(
        self, tenant_id: str, developer_id: str, issue_id: str, as_of: date
    ) -> tuple[tuple[str, ...], bool]:
        """The pods a signal on the developer's own issue counts in, and whether that is a guess.

        The rule a blocker on that issue follows (``_placement``): the issue's
        pods, with the developer's pods added when they are in none of them;
        with no pod for the issue, the developer's pods, as a guess. Drift on
        an issue (N3) is placed this way, so it shows where its blocker would.
        """
        developer_pods = await self._graph_repository.pods_containing_developer(
            tenant_id, developer_id, as_of
        )
        pods = tuple(await self._graph_repository.pods_for_task(tenant_id, issue_id, as_of))
        _, pod_ids, unattributed = _placement([(issue_id, pods)], [], developer_pods)
        return pod_ids, unattributed

    async def blockers_on_tasks(
        self, tenant_id: str, task_ids: Iterable[str], as_of: date
    ) -> tuple[tuple[GraphNode, ResolvedBlocker], ...]:
        """Open blockers recorded on these tasks, each with its developer's node.

        How a pod's own view finds the people of other teams who wait on its
        work: they are not in the pod's tree, but their blockers name its tasks.
        """
        found: dict[str, DeveloperBlocker] = {}
        for task_id in task_ids:
            for blocker in await self._status_repository.blockers_for_work_item(
                tenant_id, task_id, as_of
            ):
                found.setdefault(blocker.blocker_id, blocker)
        resolved: list[tuple[GraphNode, ResolvedBlocker]] = []
        for blocker in found.values():
            developer = await self._graph_repository.get_node(tenant_id, blocker.developer_id)
            if developer is not None:
                resolved.append((developer, await self._resolve(tenant_id, blocker, as_of)))
        return tuple(resolved)

    async def _resolve(
        self, tenant_id: str, blocker: DeveloperBlocker, as_of: date
    ) -> ResolvedBlocker:
        developer_pods = await self._graph_repository.pods_containing_developer(
            tenant_id, blocker.developer_id, as_of
        )
        own, foreign = await self._blocker_issues(tenant_id, blocker, as_of)
        work_item_ref: EntityRef | None = None
        work_item_name: str | None = None
        explicit_pod_ref: EntityRef | None = None
        critical = False
        if blocker.pod_id is not None:
            pod_ids: tuple[str, ...] = (blocker.pod_id,)
            unattributed = False
            explicit_pod_ref = EntityRef(tenant_id=tenant_id, kind=NodeKind.POD, id=blocker.pod_id)
            blocked = own[0][0] if own else blocker.work_item_id
        else:
            blocked, pod_ids, unattributed = _placement(own, foreign, developer_pods)
        if blocked is not None:
            work_item_ref, work_item_name, critical = await self._work_item_details(
                tenant_id, blocker.developer_id, blocked
            )
        depends_on = _depends_on(foreign, {pod.id for pod in developer_pods} | set(pod_ids))
        return ResolvedBlocker(
            blocker_id=blocker.blocker_id,
            description=blocker.description,
            developer_id=blocker.developer_id,
            first_seen_on=blocker.first_seen_on,
            work_item_ref=work_item_ref,
            work_item_name=work_item_name,
            explicit_pod_ref=explicit_pod_ref,
            pod_ids=pod_ids,
            unattributed=unattributed,
            critical=critical,
            provenance=BlockerProvenance.LIFECYCLE,
            depends_on_pod_ids=tuple(pod.id for pod in depends_on[1]),
            depends_on_pod_names=tuple(pod.name for pod in depends_on[1]),
            depends_on_ref=(
                EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id=depends_on[0])
                if depends_on[0] is not None
                else None
            ),
        )

    async def _blocker_issues(
        self, tenant_id: str, blocker: DeveloperBlocker, as_of: date
    ) -> tuple[list[_Issue], list[_Issue]]:
        """The blocker's issues with their pods: the developer's own, and others'.

        An issue is someone else's when it is assigned to another person and
        not to the developer. One nobody is assigned, or the graph does not
        know, counts as the developer's own, as the work item always did.
        """
        keys = [blocker.work_item_id] if blocker.work_item_id else []
        keys += [key for key in sorted(issue_keys_in(blocker.description)) if key not in keys]
        own: list[_Issue] = []
        foreign: list[_Issue] = []
        for key in keys:
            pods = tuple(await self._graph_repository.pods_for_task(tenant_id, key, as_of))
            assignees = {
                edge.from_node_id
                for edge in await self._graph_repository.list_edges(
                    tenant_id, to_node_id=key, kind=EdgeKind.ASSIGNED_TO
                )
                if edge.is_active_on(as_of)
            }
            if assignees and blocker.developer_id not in assignees:
                foreign.append((key, pods))
            else:
                own.append((key, pods))
        return own, foreign

    async def _work_item_details(
        self, tenant_id: str, developer_id: str, work_item_id: str
    ) -> tuple[EntityRef | None, str | None, bool]:
        """Fail-soft node lookup: an unknown issue key keeps a TASK-kind ref.

        ``critical`` honors ``critical_path`` on the work-item node or on the
        developer's assignment edge to it (matching the legacy rollup rule).
        The name comes back too: the node is already in hand here, and every
        caller that wants to label the attribution would otherwise re-fetch it.
        """
        node = await self._graph_repository.get_node(tenant_id, work_item_id)
        if node is None:
            return (
                EntityRef(tenant_id=tenant_id, kind=NodeKind.TASK, id=work_item_id),
                None,
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
        return node.ref, node.name, critical

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
                work_item_name=None,
                explicit_pod_ref=None,
                pod_ids=pod_ids,
                unattributed=True,
                critical=False,
                provenance=BlockerProvenance.LEGACY_STATUS,
            )
            for index, description in enumerate(descriptions, start=1)
        )


_Issue = tuple[str, tuple[GraphNode, ...]]


def _placement(
    own: list[_Issue],
    foreign: list[_Issue],
    developer_pods: list[GraphNode],
) -> tuple[str | None, tuple[str, ...], bool]:
    """The blocked issue, the pods the blocker counts in, and whether that is a guess.

    The developer's own issue decides, through its pods. With no pod for it,
    the developer's own pods do (their membership). A blocker that names only
    someone else's issue counts in the pods both share; with none shared, in
    the developer's pods, as a guess.
    """
    member_of = tuple(pod.id for pod in developer_pods)
    placed = [(key, pods) for key, pods in own if pods]
    if placed:
        pod_ids = _ordered_unique(pod.id for _, pods in placed for pod in pods)
        if member_of and not set(pod_ids) & set(member_of):
            # Their issue sits with a pod they are not in, which never rolls
            # them up: count it in their own pods too, or no team shows it.
            pod_ids = _ordered_unique((*pod_ids, *member_of))
        return placed[0][0], pod_ids, False
    if own:
        return own[0][0], member_of, True
    if foreign:
        shared = _ordered_unique(
            pod.id for _, pods in foreign for pod in pods if pod.id in member_of
        )
        return foreign[0][0], shared or member_of, not shared
    return None, member_of, True


def _depends_on(
    foreign: list[_Issue], own_pod_ids: set[str]
) -> tuple[str | None, tuple[GraphNode, ...]]:
    """The other teams' issue waited on and their pods; nothing within one's own pods."""
    for key, pods in foreign:
        others = tuple(pod for pod in pods if pod.id not in own_pod_ids)
        if others:
            return key, others
    return None, ()


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if isinstance(value, int | float):
        return bool(value)
    return False
