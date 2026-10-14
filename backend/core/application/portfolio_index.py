"""The projects a person reads across the portfolio, and the tree around them, read once.

The bulk reads behind Will we make the date?, Scope control and Early warnings
 start here: one read of the day's nodes and ``contains`` edges gives
every readable project with its programmes and pods, each pod's requirement
keys, each person's pods and every issue by its key. Nothing per project is
read here: what a read needs per project it reads itself, for the projects it
returns.

Which projects a person reads is ``DeliveryScopeService.readable_projects``,
the rule every project read keeps: every project for its readers (product
owner, manager, executive, admin), a scrum master's own, a developer's none.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date

from core.application.delivery_scope import POD_SM_KEY, DeliveryScopeService
from core.domain.auth import Principal
from core.domain.graph import EdgeKind, GraphNode, NodeKind
from core.ports.repositories import GraphRepository

#: The project metadata a search matches beside the name.
_SEARCHED_KEYS = ("code", "jira_project_key")


@dataclass(frozen=True, kw_only=True)
class IndexedProject:
    node: GraphNode
    #: Its programmes, by name.
    programs: tuple[GraphNode, ...]
    #: The pods working on it, by name.
    pods: tuple[GraphNode, ...]

    @property
    def id(self) -> str:
        return self.node.id

    @property
    def name(self) -> str:
        return self.node.name

    @property
    def program_id(self) -> str | None:
        """The programme it is grouped under: its first by name."""
        return self.programs[0].id if self.programs else None

    def matches(self, query: str) -> bool:
        """A search by name, code, Jira project key or programme name."""
        wanted = query.strip().casefold()
        if not wanted:
            return True
        values = [self.node.name, *(program.name for program in self.programs)]
        values += [
            value for key in _SEARCHED_KEYS if isinstance(value := self.node.metadata.get(key), str)
        ]
        return any(wanted in value.casefold() for value in values)


@dataclass(frozen=True, kw_only=True)
class PortfolioIndex:
    as_of: date
    #: The readable projects, by name.
    projects: tuple[IndexedProject, ...]
    nodes: Mapping[str, GraphNode]
    #: Each node's ``contains`` parents on the day, and its children.
    parents: Mapping[str, tuple[str, ...]]
    children: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Each issue by its tracker key (a node's id may differ from it).
    tasks: Mapping[str, GraphNode] = field(default_factory=dict)
    _above: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)

    def project(self, project_id: str) -> IndexedProject | None:
        return next((project for project in self.projects if project.id == project_id), None)

    def name(self, node_id: str | None) -> str | None:
        node = self.nodes.get(node_id) if node_id else None
        return node.name if node is not None else None

    def people_of(self, project: IndexedProject) -> tuple[str, ...]:
        """The people in the project's pods on the day, by id."""
        return tuple(
            sorted(
                {
                    child
                    for pod in project.pods
                    for child in self.children.get(pod.id, ())
                    if (node := self.nodes.get(child)) is not None
                    and node.kind is NodeKind.DEVELOPER
                }
            )
        )

    def pods_of_person(self, person_id: str) -> tuple[GraphNode, ...]:
        """The pods a person is in on the day, by name."""
        return _by_name(
            node
            for parent in self.parents.get(person_id, ())
            if (node := self.nodes.get(parent)) is not None and node.kind is NodeKind.POD
        )

    def scrum_master_of(self, pod_id: str) -> str | None:
        """The pod's scrum master contact, by member id."""
        pod = self.nodes.get(pod_id)
        value = pod.metadata.get(POD_SM_KEY) if pod is not None else None
        return value if isinstance(value, str) and value else None

    def pods_of_issue(self, key: str, project: IndexedProject) -> tuple[GraphNode, ...]:
        """The project's pods an issue sits under, by name."""
        task = self.tasks.get(key)
        if task is None:
            return ()
        above = self._above.get(task.id)
        if above is None:
            above = self._above[task.id] = frozenset(_ancestors(task.id, self.parents))
        return tuple(pod for pod in project.pods if pod.id in above)

    def pod_keys(self, project: IndexedProject, keys: Iterable[str]) -> dict[str, frozenset[str]]:
        """Each of the project's pods' keys among ``keys``: the issues below the pod.

        The rule the forecast keeps a pod's part by (ForecastService._pod_keys).
        """
        found: dict[str, set[str]] = {pod.id: set() for pod in project.pods}
        for key in keys:
            for pod in self.pods_of_issue(key, project):
                found[pod.id].add(key)
        return {pod_id: frozenset(values) for pod_id, values in found.items()}


class PortfolioIndexReader:
    def __init__(self, graph: GraphRepository, scope: DeliveryScopeService) -> None:
        self._graph = graph
        self._scope = scope

    async def read(self, principal: Principal, as_of: date) -> PortfolioIndex:
        tenant_id = principal.tenant_id
        nodes = {node.id: node for node in await self._graph.list_nodes(tenant_id, as_of=as_of)}
        parents: dict[str, list[str]] = {}
        children: dict[str, list[str]] = {}
        for edge in await self._graph.list_edges(tenant_id, kind=EdgeKind.CONTAINS):
            if edge.is_active_on(as_of):
                parents.setdefault(edge.to_node_id, []).append(edge.from_node_id)
                children.setdefault(edge.from_node_id, []).append(edge.to_node_id)
        project_ids = [node.id for node in nodes.values() if node.kind is NodeKind.PROJECT]
        readable = set(await self._scope.readable_projects(principal, project_ids))

        def of_kind(ids: Iterable[str], kind: NodeKind) -> tuple[GraphNode, ...]:
            return _by_name(
                node for node_id in ids if (node := nodes.get(node_id)) and node.kind is kind
            )

        projects = tuple(
            IndexedProject(
                node=nodes[project_id],
                programs=of_kind(parents.get(project_id, ()), NodeKind.PROGRAM),
                pods=of_kind(children.get(project_id, ()), NodeKind.POD),
            )
            for project_id in project_ids
            if project_id in readable
        )
        return PortfolioIndex(
            as_of=as_of,
            projects=tuple(
                sorted(projects, key=lambda project: (project.name.casefold(), project.id))
            ),
            nodes=nodes,
            parents={key: tuple(values) for key, values in parents.items()},
            children={key: tuple(values) for key, values in children.items()},
            tasks={_task_key(node): node for node in nodes.values() if node.kind is NodeKind.TASK},
        )


def _ancestors(node_id: str, parents: Mapping[str, tuple[str, ...]]) -> set[str]:
    found: set[str] = set()
    queue = list(parents.get(node_id, ()))
    while queue:
        current = queue.pop()
        if current in found:
            continue
        found.add(current)
        queue.extend(parents.get(current, ()))
    return found


def _task_key(task: GraphNode) -> str:
    key = task.metadata.get("key")
    return key if isinstance(key, str) and key else task.id


def _by_name(nodes: Iterable[GraphNode]) -> tuple[GraphNode, ...]:
    unique = {node.id: node for node in nodes}
    return tuple(sorted(unique.values(), key=lambda node: (node.name.casefold(), node.id)))


__all__ = ["IndexedProject", "PortfolioIndex", "PortfolioIndexReader"]
