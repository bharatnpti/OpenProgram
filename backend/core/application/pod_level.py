"""One pod's part of a project, as the console's pod level reads it.

The pod level narrows a project's reads to one pod: Early warnings to the work
below the pod and its people's blockers, Scope control to its requirements and
findings, requirements by stage and the gates to the requirements below it.
Whoever reads the project's read reads the pod's part of it, with two rules
of the pod level's own, decided here on the server:

- **the small-group rule:** an executive reads no pod of fewer than
  ``SMALL_GROUP`` people on its own; its work is read with its project's,
  where nobody in it can be told apart;
- **a developer's own pod:** a developer reads their own pod's early warnings,
  as they read its panel (``reads_pod_detail``), in counts and teams.

A pod's part is the requirements below it on the day: the rule the forecast
keeps a pod's part by (``ForecastService.pod_keys``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from core.application.delivery_service import DeliveryService, PodPart
from core.application.forecast_service import ForecastService
from core.application.people_words import SMALL_GROUP, hides_names, is_small
from core.application.portfolio_index import IndexedProject, PortfolioIndex
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.forecast import Release
from core.domain.graph import EdgeKind, GraphNode, NodeKind
from core.ports.repositories import GraphRepository

#: Why an executive is refused a small pod's own part.
SMALL_POD = f"A pod of fewer than {SMALL_GROUP} people is read as part of its project."


def not_on_project(pod: str, project: str) -> str:
    """Why a pod's part of a project it does not work on is not found."""
    return f"{pod} doesn't work on {project}."


def refuses_small_pod(principal: Principal, people: int) -> bool:
    """Whether the small-group rule keeps this reader out of a pod of ``people``."""
    return hides_names(principal) and is_small(people)


def pod_projects(
    principal: Principal,
    index: PortfolioIndex,
    projects: Sequence[IndexedProject],
    pod_id: str,
    project_id: str | None = None,
) -> list[IndexedProject]:
    """Those of ``projects`` the pod works on, for a bulk read of its part.

    Raises GraphNotFound for no such pod, or when ``project_id`` names a project
    the pod does not work on, and AuthorizationDenied for a small pod read by a
    reader of counts.
    """
    pod = index.pod(pod_id)
    if pod is None:
        raise GraphNotFound(f"No pod {pod_id!r}.")
    if refuses_small_pod(principal, index.people_in(pod_id)):
        raise AuthorizationDenied(SMALL_POD)
    if project_id:
        project = index.nodes.get(project_id)
        if (
            project is not None
            and project.kind is NodeKind.PROJECT
            and project_id not in index.parents.get(pod_id, ())
        ):
            raise GraphNotFound(not_on_project(pod.name, project.name))
    return [project for project in projects if pod_id in {pod.id for pod in project.pods}]


async def pod_people(graph: GraphRepository, tenant_id: str, pod_id: str, as_of: date) -> int:
    """How many people the pod holds on ``as_of``."""
    people = {
        node.id for node in await graph.list_nodes(tenant_id, NodeKind.DEVELOPER, as_of=as_of)
    }
    return sum(
        1
        for edge in await graph.list_edges(tenant_id, from_node_id=pod_id, kind=EdgeKind.CONTAINS)
        if edge.is_active_on(as_of) and edge.to_node_id in people
    )


async def ensure_pod_level(
    graph: GraphRepository, principal: Principal, pod_id: str, as_of: date
) -> GraphNode:
    """The pod, when this reader may read its own part; raises in plain words.

    GraphNotFound when the id is no pod on the day; AuthorizationDenied when the
    small-group rule keeps the reader to the project's whole.
    """
    tenant_id = principal.tenant_id
    pod = await graph.get_node(tenant_id, pod_id, as_of=as_of)
    if pod is None or pod.kind is not NodeKind.POD:
        raise GraphNotFound(f"No pod {pod_id!r}.")
    if hides_names(principal) and is_small(await pod_people(graph, tenant_id, pod_id, as_of)):
        raise AuthorizationDenied(SMALL_POD)
    return pod


async def pod_part(
    *,
    graph: GraphRepository,
    delivery: DeliveryService,
    forecasts: ForecastService,
    tenant_id: str,
    project_id: str,
    pod: GraphNode,
    as_of: date,
    release: Release | None = None,
) -> PodPart:
    """The pod's part of the project (or release): its requirements' keys on ``as_of``.

    Raises GraphNotFound, in words, when the pod does not work on the project.
    """
    tasks = await delivery.scope_tasks(tenant_id, project_id, as_of, release)
    keys = await forecasts.pod_keys(
        tenant_id, project_id, {_task_key(task): task for task in tasks}, as_of
    )
    if pod.id not in keys:
        project = await graph.get_node(tenant_id, project_id, as_of=as_of)
        raise GraphNotFound(not_on_project(pod.name, project.name if project else project_id))
    return PodPart(id=pod.id, name=pod.name, keys=keys[pod.id])


def _task_key(task: GraphNode) -> str:
    key = task.metadata.get("key")
    return key if isinstance(key, str) and key else task.id


__all__ = [
    "SMALL_POD",
    "ensure_pod_level",
    "not_on_project",
    "pod_part",
    "pod_people",
    "pod_projects",
    "refuses_small_pod",
]
