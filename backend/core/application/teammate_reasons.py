"""Why a pod has its colour, as a developer in it reads it.

A developer opens their own pod's reasons ("Why this colour") but reads no
teammate's check-in: the reasons speak in counts and work items. A teammate's
own status becomes a count of the pod's people in that state ("2 not
replied"), a blocker becomes how many are open on which work item ("1 blocker
on IDP-6"), a disagreement names the work item, never whose claim it was, and
a dependency names another team. The reader's own reasons are theirs, and keep
their own words.

Decided on the server for anyone who does not read the pod's check-ins
(``portfolio_privacy.reads_people``): a scrum master, a manager and an admin
keep the reasons as built. Pure: the pod read calls in with what it built.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import TYPE_CHECKING

from core.application.portfolio_privacy import counted_factor
from core.domain.graph import EntityRef, NodeKind
from core.domain.rollup import FactorKind, Rag, RollupFactor

if TYPE_CHECKING:
    from core.application.persona_views import PodRollupView

_SEVERITY = {Rag.GREEN: 0, Rag.UNKNOWN: 1, Rag.AMBER: 2, Rag.RED: 3}
#: A teammate's own status, as a count says it, in the order the reasons list them.
_STATES = ("not replied", "carried over", "replied in part", "inferred, not confirmed", "confirmed")


def teammates_counted(view: PodRollupView, reader_id: str) -> PodRollupView:
    """The pod's reasons with no teammate in them: counts and work items only."""
    pod = EntityRef(tenant_id=_tenant(view.factors), kind=NodeKind.POD, id=view.pod_id)
    own: list[RollupFactor] = []
    states: dict[str, list[RollupFactor]] = {}
    blockers: dict[str, list[RollupFactor]] = {}
    drift: dict[str, list[RollupFactor]] = {}
    rest: list[RollupFactor] = []
    for factor in view.factors:
        source = factor.source_ref
        if source.kind is NodeKind.DEVELOPER and source.id == reader_id:
            own.append(factor)
        elif factor.kind is FactorKind.BLOCKER:
            blockers.setdefault(_item(factor), []).append(factor)
        elif factor.kind is FactorKind.DRIFT and source.kind is NodeKind.DEVELOPER:
            drift.setdefault(_item(factor), []).append(factor)
        elif factor.kind is FactorKind.STATUS and source.kind is NodeKind.DEVELOPER:
            states.setdefault(_state(factor), []).append(factor)
        else:
            rest.append(counted_factor(factor))
    factors = (
        *own,
        *(_blocker_count(item, same) for item, same in blockers.items()),
        *(_drift_count(item, same) for item, same in drift.items()),
        *(_state_count(state, states[state], pod) for state in _STATES if state in states),
        *rest,
    )
    cited = {factor.source_ref.id for factor in factors} | {
        factor.work_item_ref.id for factor in factors if factor.work_item_ref is not None
    }
    names = {
        node_id: name
        for node_id, name in view.source_names.items()
        if node_id in cited and (node_id == reader_id or not _person(node_id, view.factors))
    }
    names[view.pod_id] = view.pod_name
    return replace(view, factors=factors, source_names=names)


def _state(factor: RollupFactor) -> str:
    """A person's own status factor, as the state a count says (rollup_service's wording)."""
    words = factor.description.casefold()
    if factor.contributes is Rag.UNKNOWN or "unknown" in words or "no developer status" in words:
        return "not replied"
    if "stale" in words:
        return "carried over"
    if "partial" in words:
        return "replied in part"
    if factor.contributes is Rag.GREEN:
        return "confirmed"
    return "inferred, not confirmed"


def _state_count(state: str, same: list[RollupFactor], pod: EntityRef) -> RollupFactor:
    return RollupFactor(
        description=f"{len(same)} {state}.",
        contributes=_worst(same),
        source_ref=pod,
        kind=FactorKind.STATUS,
    )


def _blocker_count(item: str, same: list[RollupFactor]) -> RollupFactor:
    """ "1 blocker on IDP-6": how many, on which work item; one identity for the group."""
    first = same[0]
    n = len(same)
    where = f"on {item}" if item else "not tied to a work item"
    ids = sorted({factor.blocker_id or factor.description for factor in same})
    return replace(
        first,
        description=f"{n} blocker{'s' if n != 1 else ''} {where}.",
        contributes=_worst(same),
        source_ref=first.work_item_ref or first.source_ref,
        blocker_id="|".join(ids) if len(ids) > 1 else (first.blocker_id or None),
    )


def _drift_count(item: str, same: list[RollupFactor]) -> RollupFactor:
    first = same[0]
    on = f"on {item}" if item else "on a work item"
    return replace(
        first,
        description=f"Status and Jira disagree {on}.",
        source_ref=first.work_item_ref or first.source_ref,
    )


def _item(factor: RollupFactor) -> str:
    if factor.work_item_ref is not None:
        return factor.work_item_ref.id
    if factor.source_ref.kind is not NodeKind.DEVELOPER:
        return factor.source_ref.id
    return ""


def _person(node_id: str, factors: Iterable[RollupFactor]) -> bool:
    return any(
        factor.source_ref.kind is NodeKind.DEVELOPER and factor.source_ref.id == node_id
        for factor in factors
    )


def _worst(factors: Iterable[RollupFactor]) -> Rag:
    return max((factor.contributes for factor in factors), key=_SEVERITY.__getitem__)


def _tenant(factors: Iterable[RollupFactor]) -> str:
    return next((factor.source_ref.tenant_id for factor in factors), "")


__all__ = ["teammates_counted"]
