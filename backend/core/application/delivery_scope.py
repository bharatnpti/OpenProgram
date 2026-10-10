"""Whose part of the delivery tree a person works on, and what of it they may open.

Managers, executives and admins read the whole portfolio, so their Delivery
lists the directory. Everyone else works on a part of it: the pods they belong
to or run, the projects those pods work on or that name them as owner, and
every pod of those projects. Inside that part some reads go beyond what the
role reads everywhere (``AuthorizationPolicy``):

- a scrum master reads the progress and dates of a project one of their pods
  works on, as a product owner reads any project's;
- a developer reads their own pod's panel (its colour and reasons, check-ins,
  blockers and tasks) and its dates.

Every other read keeps its role capability, unchanged. Membership is read for
today: a read for a past day is asked by who the person is now, and the tree a
past day shows is that same part, as the graph stood on the day.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from core.application.authorization import AuthorizationPolicy, Capability
from core.domain.auth import Principal, Role
from core.domain.errors import AuthorizationDenied
from core.domain.graph import EdgeKind, GraphEdge, GraphNode, NodeKind
from core.domain.rollup import Rag
from core.ports.repositories import GraphRepository, RollupRepository

#: The pod metadata key naming its scrum master contact (ForecastService.runs_pod).
POD_SM_KEY = "escalation_sm_member_id"
#: The project metadata key naming the project's owner, a member id or chat id.
PROJECT_OWNER_KEY = "owner_id"

# Plain words for a read outside the caller's own part of the tree, where the
# role has a way in to it (a scrum master's projects, a developer's pod). A
# read the role has no way in to anywhere keeps the policy's own refusal.
PROJECT_OUTSIDE_SCOPE = "You read the projects your own pods work on, and this is not one of them."
POD_OUTSIDE_SCOPE = "You read your own pod's details, and this is not your pod."
POD_DATES_OUTSIDE_SCOPE = "You read your own pod's dates, and this is not your pod."


@dataclass(frozen=True, kw_only=True)
class MemberScope:
    """A person's own part of the tree: their pods, and their pods' and own projects."""

    pods: frozenset[str] = frozenset()
    projects: frozenset[str] = frozenset()


class NodeAccess(StrEnum):
    """What a node's Delivery panel shows the caller."""

    #: The whole panel: a project's progress and dates; a pod's colour and
    #: reasons, check-ins, blockers, tasks and dates; a program's rollup.
    PANEL = "panel"
    #: A pod's dates and colour, as a project's reader sees the pods under it.
    DATES = "dates"
    #: Its name in the tree, and nothing to open.
    NAME = "name"


@dataclass(frozen=True, kw_only=True)
class DeliveryTreeNodeView:
    id: str
    kind: NodeKind
    name: str
    #: The node's colour, only where the caller reads it (``access`` is not NAME).
    rag: Rag | None
    access: NodeAccess
    #: A pod the caller belongs to or runs; every listed project and program is theirs.
    own: bool
    #: A project's programs; a pod's listed projects.
    parent_ids: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class DeliveryTreeView:
    as_of: date
    programs: tuple[DeliveryTreeNodeView, ...]
    projects: tuple[DeliveryTreeNodeView, ...]
    pods: tuple[DeliveryTreeNodeView, ...]


def member_scope(
    nodes: Iterable[GraphNode], contains: Iterable[GraphEdge], subject: str
) -> MemberScope:
    """The pods ``subject`` belongs to or runs, and the projects of those pods or that they own.

    A pod is theirs when it contains them, or names them as its scrum master
    contact -- the rule ForecastService.runs_pod uses for who sets a pod's date
    and sends its reports. A project is theirs when it contains one of those
    pods, or names them as its owner (by member id or by chat id, as admins
    type person fields). ``contains`` must be the edges active on the day read.
    """
    by_id = {node.id: node for node in nodes}
    member = by_id.get(subject)
    person_ids = {subject}
    if member is not None and member.kind is NodeKind.DEVELOPER:
        chat_id = member.metadata.get("chat_external_id")
        if isinstance(chat_id, str) and chat_id:
            person_ids.add(chat_id)
    edges = [edge for edge in contains if edge.kind is EdgeKind.CONTAINS]

    def kind_of(node_id: str) -> NodeKind | None:
        node = by_id.get(node_id)
        return node.kind if node is not None else None

    pods = {
        edge.from_node_id
        for edge in edges
        if edge.to_node_id == subject and kind_of(edge.from_node_id) is NodeKind.POD
    } | {
        node.id
        for node in by_id.values()
        if node.kind is NodeKind.POD and node.metadata.get(POD_SM_KEY) == subject
    }
    projects = {
        edge.from_node_id
        for edge in edges
        if edge.to_node_id in pods and kind_of(edge.from_node_id) is NodeKind.PROJECT
    } | {
        node.id
        for node in by_id.values()
        if node.kind is NodeKind.PROJECT and node.metadata.get(PROJECT_OWNER_KEY) in person_ids
    }
    return MemberScope(pods=frozenset(pods), projects=frozenset(projects))


def reads_project(policy: AuthorizationPolicy, principal: Principal, *, own: bool) -> bool:
    """A project's progress and dates: its readers everywhere, and a scrum master's own."""
    if policy.can(principal, Capability.READ_PROJECT_PROGRESS):
        return True
    return own and principal.has_role(Role.SM)


def reads_pod_detail(policy: AuthorizationPolicy, principal: Principal, *, own: bool) -> bool:
    """A pod's colour and reasons, check-ins, blockers and tasks.

    The pod readers everywhere (scrum master, manager, admin), and a developer
    for their own pod. A product owner and an executive still read none.
    """
    if policy.can(principal, Capability.READ_POD_CHECKINS) and policy.can(
        principal, Capability.READ_POD_BLOCKERS
    ):
        return True
    return own and principal.has_role(Role.DEV)


def reads_pod_dates(policy: AuthorizationPolicy, principal: Principal, *, own: bool) -> bool:
    """A pod's dates: who sets pod dates or reads project progress, and a developer's own pod."""
    if policy.can(principal, Capability.SET_POD_DATES) or policy.can(
        principal, Capability.READ_PROJECT_PROGRESS
    ):
        return True
    return own and principal.has_role(Role.DEV)


def project_access(policy: AuthorizationPolicy, principal: Principal, *, own: bool) -> NodeAccess:
    return NodeAccess.PANEL if reads_project(policy, principal, own=own) else NodeAccess.NAME


def pod_access(policy: AuthorizationPolicy, principal: Principal, *, own: bool) -> NodeAccess:
    """What a listed pod opens on.

    Its whole panel when it is the caller's own pod and they read its detail,
    or when they read the portfolio (a manager or admin opens every pod).
    Another pod of their projects opens as a project's reader sees it: its
    dates and colour, for those who read pod dates; else only its name. A
    scrum master still reads every pod's detail through the pod routes, as
    before; their Delivery opens only their own pods that far.
    """
    detail = reads_pod_detail(policy, principal, own=own)
    if detail and (own or policy.can(principal, Capability.READ_PROGRAM_ROLLUP)):
        return NodeAccess.PANEL
    if reads_pod_dates(policy, principal, own=own):
        return NodeAccess.DATES
    return NodeAccess.NAME


class DeliveryScopeService:
    """Resolves a person's part of the tree, guards the reads scoped to it, and lists it."""

    def __init__(
        self,
        graph_repository: GraphRepository,
        rollup_repository: RollupRepository,
        *,
        policy: AuthorizationPolicy | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._graph = graph_repository
        self._rollups = rollup_repository
        self._policy = policy or AuthorizationPolicy()
        self._today = today

    async def scope_of(self, tenant_id: str, subject: str) -> MemberScope:
        """The person's own part of the tree, as it stands today."""
        today = self._today()
        nodes = await self._graph.list_nodes(tenant_id, as_of=today)
        return member_scope(nodes, await self._contains(tenant_id, today), subject)

    async def ensure_project_read(self, principal: Principal, project_id: str) -> None:
        """A project's progress or dates; raises AuthorizationDenied in plain words."""
        if reads_project(self._policy, principal, own=False):
            return
        if principal.has_role(Role.SM):
            scope = await self.scope_of(principal.tenant_id, principal.subject)
            if reads_project(self._policy, principal, own=project_id in scope.projects):
                return
            raise AuthorizationDenied(PROJECT_OUTSIDE_SCOPE)
        self._policy.ensure(principal, Capability.READ_PROJECT_PROGRESS)

    async def readable_projects(
        self, principal: Principal, project_ids: Iterable[str]
    ) -> tuple[str, ...]:
        """Those of ``project_ids`` that :meth:`ensure_project_read` lets the person read.

        One scope read covers them all, and only for a scrum master: a role that
        reads every project keeps them all, any other role none.
        """
        wanted = tuple(dict.fromkeys(project_ids))
        if reads_project(self._policy, principal, own=False):
            return wanted
        if not principal.has_role(Role.SM):
            return ()
        scope = await self.scope_of(principal.tenant_id, principal.subject)
        return tuple(
            project_id
            for project_id in wanted
            if reads_project(self._policy, principal, own=project_id in scope.projects)
        )

    async def ensure_pod_detail(self, principal: Principal, pod_id: str) -> None:
        """A pod's rollup, check-ins, blockers or tasks; raises AuthorizationDenied."""
        if reads_pod_detail(self._policy, principal, own=False):
            return
        if principal.has_role(Role.DEV):
            scope = await self.scope_of(principal.tenant_id, principal.subject)
            if reads_pod_detail(self._policy, principal, own=pod_id in scope.pods):
                return
            raise AuthorizationDenied(POD_OUTSIDE_SCOPE)
        self._policy.ensure(principal, Capability.READ_POD_CHECKINS)
        self._policy.ensure(principal, Capability.READ_POD_BLOCKERS)

    async def ensure_pod_dates(self, principal: Principal, pod_id: str) -> None:
        """A pod's dates (``GET /pods/{id}/delivery``); raises AuthorizationDenied."""
        if reads_pod_dates(self._policy, principal, own=False):
            return
        if principal.has_role(Role.DEV):
            scope = await self.scope_of(principal.tenant_id, principal.subject)
            if reads_pod_dates(self._policy, principal, own=pod_id in scope.pods):
                return
            raise AuthorizationDenied(POD_DATES_OUTSIDE_SCOPE)
        # The refusal the route always gave a principal with no date capability.
        raise AuthorizationDenied("Not allowed.")

    async def tree(self, principal: Principal, as_of: date) -> DeliveryTreeView:
        """The caller's part of the tree on ``as_of``: their programs, projects and those pods.

        Every pod of a listed project is listed, by name at least, so a
        developer sees the pods beside their own. A node's colour comes with
        it only where the caller reads it: a project they open, a pod they
        open, a program whose rollup they read.
        """
        tenant_id = principal.tenant_id
        scope = await self.scope_of(tenant_id, principal.subject)
        nodes = await self._graph.list_nodes(tenant_id, as_of=as_of)
        by_id = {node.id: node for node in nodes}
        contains = await self._contains(tenant_id, as_of)
        rag_of = await self._rags(tenant_id, as_of)

        def kind_of(node_id: str) -> NodeKind | None:
            node = by_id.get(node_id)
            return node.kind if node is not None else None

        project_ids = sorted(
            node_id for node_id in scope.projects if kind_of(node_id) is NodeKind.PROJECT
        )
        listed = set(project_ids)
        programs_of: dict[str, list[str]] = {}
        pods_of: dict[str, list[str]] = {}
        for edge in contains:
            if edge.to_node_id in listed and kind_of(edge.from_node_id) is NodeKind.PROGRAM:
                programs_of.setdefault(edge.to_node_id, []).append(edge.from_node_id)
            if edge.from_node_id in listed and kind_of(edge.to_node_id) is NodeKind.POD:
                pods_of.setdefault(edge.to_node_id, []).append(edge.from_node_id)

        reads_rollup = self._policy.can(principal, Capability.READ_PROGRAM_ROLLUP)
        programs = [
            _node(
                by_id[program_id],
                NodeAccess.PANEL if reads_rollup else NodeAccess.NAME,
                rag_of,
                own=True,
                parent_ids=(),
            )
            for program_id in sorted({pid for ids in programs_of.values() for pid in ids})
        ]
        projects = [
            _node(
                by_id[project_id],
                project_access(self._policy, principal, own=True),
                rag_of,
                own=True,
                parent_ids=tuple(sorted(set(programs_of.get(project_id, ())))),
            )
            for project_id in project_ids
        ]
        pods = [
            _node(
                by_id[pod_id],
                pod_access(self._policy, principal, own=pod_id in scope.pods),
                rag_of,
                own=pod_id in scope.pods,
                parent_ids=tuple(sorted(set(parents))),
            )
            for pod_id, parents in sorted(pods_of.items())
        ]
        return DeliveryTreeView(
            as_of=as_of,
            programs=_by_name(programs),
            projects=_by_name(projects),
            pods=_by_name(pods),
        )

    async def _contains(self, tenant_id: str, on: date) -> list[GraphEdge]:
        return [
            edge
            for edge in await self._graph.list_edges(tenant_id, kind=EdgeKind.CONTAINS)
            if edge.is_active_on(on)
        ]

    async def _rags(self, tenant_id: str, as_of: date) -> Mapping[tuple[NodeKind, str], Rag]:
        """Each node's colour on the day, the one the directory lists."""
        return {
            (status.entity_ref.kind, status.entity_ref.id): status.rag
            for status in await self._rollups.list_node_statuses(tenant_id, as_of)
        }


def _node(
    node: GraphNode,
    access: NodeAccess,
    rag_of: Mapping[tuple[NodeKind, str], Rag],
    *,
    own: bool,
    parent_ids: tuple[str, ...],
) -> DeliveryTreeNodeView:
    return DeliveryTreeNodeView(
        id=node.id,
        kind=node.kind,
        name=node.name,
        rag=None if access is NodeAccess.NAME else rag_of.get((node.kind, node.id), Rag.UNKNOWN),
        access=access,
        own=own,
        parent_ids=parent_ids,
    )


def _by_name(items: list[DeliveryTreeNodeView]) -> tuple[DeliveryTreeNodeView, ...]:
    return tuple(sorted(items, key=lambda item: (item.name.casefold(), item.id)))
