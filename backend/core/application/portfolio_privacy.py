"""What the portfolio's reads say to a reader who sees counts, never a person (D87).

The heat map and Exec Today's attention read are one read of the day for a
manager and an executive alike, and their reasons name people: "3 of 4 haven't
answered today: Ada, Ben and Cy", "Blocker on SHOP-8 (Ada)", a blocker in its
owner's own words, "Incoming dependency: Ada waits on PAY-3". An executive
reads no pod's check-ins and no blocker (``AuthorizationPolicy``: those are
per person, for the scrum master and the manager), so they get the same day
worded with nobody in it:

- every person's cell but the reader's own is left out (a person in no pod
  still sees their own check-in, N5);
- a blocker is "an open blocker" on its work item, never its owner's words;
- a dependency is another team's, never a named person's;
- no reason prints a person's name: each says how many, and on which work.

This is decided on the server, by role, never by hiding in the console. Pure:
the persona read calls in with what it loaded.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from core.application.attention import AttentionDay
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.people_words import small_pods
from core.domain.auth import Principal
from core.domain.graph import NodeKind
from core.domain.rollup import FactorKind, NodeStatus, RollupFactor

if TYPE_CHECKING:
    from core.application.persona_views import ProjectProgressView

#: A blocker as a counts-only reader reads it: never in its owner's words.
OPEN_BLOCKER = "Open blocker."
_DEPENDENCY = re.compile(r"^Incoming dependency: .+? waits on ")
_ANOTHER_TEAM = "Incoming dependency: another team waits on "


def reads_people(principal: Principal) -> bool:
    """Whether the portfolio's reasons may name people to this reader.

    The readers of the pods' check-ins do: the manager is the last step of the
    non-response escalation, a scrum master runs the pods, an admin keeps it
    all. An executive holds the heat map but no pod check-in, and reads counts.
    """
    return AuthorizationPolicy().can(principal, Capability.READ_POD_CHECKINS)


def counted_statuses(statuses: Iterable[NodeStatus]) -> list[NodeStatus]:
    """The statuses with each factor worded so it names nobody.

    A blocker keeps its identity (``blocker_id``, else one made from its
    words), so it is still counted once wherever it reaches, but loses the
    words. A dependency names the team, not the person waiting.
    """
    return [replace(status, factors=tuple(map(_counted, status.factors))) for status in statuses]


def counted_day(day: AttentionDay) -> AttentionDay:
    """The day with no person's name to print: reasons count people, never list them.

    A pod of fewer than ``SMALL_GROUP`` people gets no people counted on it at
    all (D99): its people are counted in the project's line.
    """
    graph = day.graph
    labels = {
        node_id: label
        for node_id, label in graph.labels.items()
        if graph.kinds.get(node_id) is not NodeKind.DEVELOPER
    }
    sizes = {
        node_id: len(graph.under(node_id, NodeKind.DEVELOPER))
        for node_id, kind in graph.kinds.items()
        if kind is NodeKind.POD
    }
    return replace(day, graph=replace(graph, labels=labels), small_pods=small_pods(sizes))


def without_small_pod_people(
    statuses: Iterable[NodeStatus], small: frozenset[str]
) -> list[NodeStatus]:
    """Each small pod's status without its people's own updates among its factors (D99).

    So no line made from its factors ("1 missing update") counts its people;
    its colour stays, and the project above still counts them.
    """
    return [
        replace(status, factors=tuple(f for f in status.factors if not _own_update(f)))
        if status.entity_ref.kind is NodeKind.POD and status.entity_ref.id in small
        else status
        for status in statuses
    ]


def counted_progress(view: ProjectProgressView) -> ProjectProgressView:
    """A project's progress with nobody in its factors, for an executive (D87).

    No person's own status is among them (an executive reads no one's
    check-in: their counts are in the project's), a blocker is "Open blocker."
    on its work item and a dependency another team's, as on the heat map. The
    names of what the factors still cite are kept; nobody's else (a person a
    blocker still cites is said as their role or team by the route).
    """
    factors = tuple(_counted(factor) for factor in view.factors if not _own_update(factor))
    cited = {factor.source_ref.id for factor in factors} | {
        factor.work_item_ref.id for factor in factors if factor.work_item_ref is not None
    }
    names = {node_id: name for node_id, name in view.source_names.items() if node_id in cited}
    return replace(view, factors=factors, source_names=names)


def own_cells_only(statuses: Sequence[NodeStatus], subject: str | None) -> list[NodeStatus]:
    """The statuses but every person's own, except the reader's."""
    return [
        status
        for status in statuses
        if status.entity_ref.kind is not NodeKind.DEVELOPER or status.entity_ref.id == subject
    ]


def counted_factor(factor: RollupFactor) -> RollupFactor:
    """One factor worded so it names nobody: a blocker without its words, a dependency a team's."""
    return _counted(factor)


def _counted(factor: RollupFactor) -> RollupFactor:
    if factor.kind is FactorKind.BLOCKER:
        return replace(
            factor,
            description=OPEN_BLOCKER,
            blocker_id=factor.blocker_id or _anonymous_id(factor.description),
        )
    if factor.kind is FactorKind.DEPENDENCY:
        return replace(
            factor,
            description=_DEPENDENCY.sub(_ANOTHER_TEAM, factor.description, count=1),
        )
    return factor


def _own_update(factor: RollupFactor) -> bool:
    """A person's own status among a team's factors."""
    return factor.kind is FactorKind.STATUS and factor.source_ref.kind is NodeKind.DEVELOPER


def _anonymous_id(description: str) -> str:
    """A blocker without an id is told apart by its words, which are not shown."""
    return "words:" + hashlib.sha256(description.encode()).hexdigest()[:16]


__all__ = [
    "OPEN_BLOCKER",
    "counted_day",
    "counted_factor",
    "counted_progress",
    "counted_statuses",
    "own_cells_only",
    "reads_people",
    "without_small_pod_people",
]
