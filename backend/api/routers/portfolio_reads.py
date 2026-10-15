"""The portfolio's bulk reads: one request each for every project a person reads.

- ``GET /portfolio/delivery``: Will we make the date? From the kept forecast rows,
  labelled with when they were computed, never a live forecast per project. Read
  by whoever reads a project's dates: the product owner, manager, executive and
  admin every project, a scrum master their own.
- ``GET /portfolio/scope``: Scope control. Each project against its planning
  baseline (the baselines' readers), and the open Jira hygiene findings as each
  reader's project board shows them (an executive the rules' counts only).
- ``GET /portfolio/warnings``: Early warnings by kind, for the aggregate readers;
  blockers listed to the pods' blocker readers, counted for everyone else.

Scope control and Early warnings also read one pod's part (``pod_id``): the
work below the pod, its findings and its people's blockers. An executive reads
no pod of fewer than five people on its own (403), and a pod that does not work
on the project asked for is not found (404).

A developer reads none of them (403), but for their own pod's early warnings.
The tenant always comes from the principal, and none of them writes anything.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import (
    as_of_or_tenant_today,
    get_current_principal,
    get_registry,
    get_risk_service,
)
from api.portfolio_dtos import (
    PortfolioDeliveryResponse,
    PortfolioScopeResponse,
    PortfolioWarningsResponse,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.blocker_resolution import BlockerResolutionService
from core.application.delivery_scope import DeliveryScopeService, reads_project
from core.application.portfolio_delivery_service import (
    MAX_PAGE,
    PortfolioDeliveryService,
    PortfolioQuery,
)
from core.application.portfolio_index import PortfolioIndexReader
from core.application.portfolio_scope_service import PortfolioScopeService
from core.application.portfolio_warnings_service import PortfolioWarningsService
from core.application.risk_service import RiskService
from core.domain.auth import Principal, Role
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.portfolio import PortfolioSort, VerdictFilter
from infra.registry import ServiceRegistry

router = APIRouter(tags=["portfolio"])

Caller = Annotated[Principal, Depends(get_current_principal)]
Registry = Annotated[ServiceRegistry, Depends(get_registry)]

_NOT_A_DATES_READER = "Will we make the date? is read by the people who read projects' dates."
_NOT_A_SCOPE_READER = "Scope control is read by the people who read projects' scope."
_NOT_AN_AGGREGATE_READER = "Early warnings are read by the people who read the portfolio."
_POD_PART = "One pod's part: the work below the pod, its findings and its people's blockers."


def _scope_service(registry: ServiceRegistry) -> DeliveryScopeService:
    # Membership is read on the tenant's today, the day the config routes link on,
    # so a scrum master who joined a pod today reads its projects.
    return DeliveryScopeService(
        graph_repository=registry.graph_repository(),
        rollup_repository=registry.rollup_repository(),
        today=registry.tenant_today,
    )


def _index(registry: ServiceRegistry) -> PortfolioIndexReader:
    return PortfolioIndexReader(registry.graph_repository(), _scope_service(registry))


def get_portfolio_delivery_service(registry: Registry) -> PortfolioDeliveryService:
    return PortfolioDeliveryService(
        index=_index(registry),
        forecast_days=registry.forecast_day_repository(),
        commitments=registry.commitment_repository(),
        releases=registry.release_repository(),
        snapshots=registry.requirements_snapshot_repository(),
        baselines=registry.baseline_service(),
        rollups=registry.rollup_repository(),
        statuses=registry.status_repository(),
        zone=registry.tenant_zone(),
    )


def get_portfolio_scope_service(registry: Registry) -> PortfolioScopeService:
    return PortfolioScopeService(
        index=_index(registry),
        baselines=registry.baseline_service(),
        snapshots=registry.requirements_snapshot_repository(),
        hygiene=registry.hygiene_repository(),
        forecasts=registry.forecast_service(),
        readiness=registry.release_readiness_service(),
        zone=registry.tenant_zone(),
    )


def get_portfolio_warnings_service(
    registry: Registry, risks: Annotated[RiskService, Depends(get_risk_service)]
) -> PortfolioWarningsService:
    names = registry.provider_names()
    return PortfolioWarningsService(
        index=_index(registry),
        risks=risks,
        blockers=BlockerResolutionService(
            registry.graph_repository(), registry.status_repository()
        ),
        graph=registry.graph_repository(),
        scope=_scope_service(registry),
        statuses=registry.status_repository(),
        tracker=names.tracker,
        vcs=names.vcs,
        zone=registry.tenant_zone(),
    )


@router.get("/portfolio/delivery", response_model=PortfolioDeliveryResponse)
async def portfolio_delivery(
    principal: Caller,
    service: Annotated[PortfolioDeliveryService, Depends(get_portfolio_delivery_service)],
    as_of: Annotated[date, Depends(as_of_or_tenant_today)],
    q: Annotated[str, Query(max_length=120, description="Name, code, Jira key or programme.")] = "",
    verdict: Annotated[
        list[VerdictFilter] | None,
        Query(description="Keep these verdicts: late, at_risk, no_date, on_track, ..."),
    ] = None,
    program_id: Annotated[str | None, Query()] = None,
    sort: Annotated[PortfolioSort, Query()] = PortfolioSort.WORST,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
) -> PortfolioDeliveryResponse:
    """Will we make the date? for every project the reader reads, worst first.

    Each project's three dates (committed, likely, the team's own), its verdict
    and one reason line, today's health in words, done of total per pod, and the
    changes on its bar, from the kept rows "as of" ``computed_at``.
    """
    if not reads_project(AuthorizationPolicy(), principal, own=True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_A_DATES_READER)
    view = await service.read(
        principal,
        as_of,
        PortfolioQuery(
            search=q,
            verdicts=frozenset(verdict or ()),
            program_id=program_id or None,
            sort=sort,
            offset=offset,
            limit=limit,
        ),
    )
    return PortfolioDeliveryResponse.from_view(view)


@router.get("/portfolio/scope", response_model=PortfolioScopeResponse)
async def portfolio_scope(
    principal: Caller,
    service: Annotated[PortfolioScopeService, Depends(get_portfolio_scope_service)],
    as_of: Annotated[date, Depends(as_of_or_tenant_today)],
    q: Annotated[str, Query(max_length=120)] = "",
    program_id: Annotated[str | None, Query()] = None,
    project_id: Annotated[str | None, Query()] = None,
    pod_id: Annotated[str | None, Query(description=_POD_PART)] = None,
) -> PortfolioScopeResponse:
    """What joined each project after planning, and its open Jira hygiene findings.

    With ``pod_id``, the pod's part of each project it works on: the plan counted
    over the requirements below the pod, and the findings on its issues or its board.
    """
    if not reads_project(AuthorizationPolicy(), principal, own=True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_A_SCOPE_READER)
    try:
        view = await service.read(
            principal,
            as_of,
            search=q,
            program_id=program_id or None,
            project_id=project_id or None,
            pod_id=pod_id or None,
        )
    except (AuthorizationDenied, GraphNotFound) as exc:
        raise _refused(exc) from exc
    return PortfolioScopeResponse.from_view(view)


@router.get("/portfolio/warnings", response_model=PortfolioWarningsResponse)
async def portfolio_warnings(
    principal: Caller,
    service: Annotated[PortfolioWarningsService, Depends(get_portfolio_warnings_service)],
    as_of: Annotated[date, Depends(as_of_or_tenant_today)],
    q: Annotated[str, Query(max_length=120)] = "",
    project_id: Annotated[str | None, Query()] = None,
    pod_id: Annotated[str | None, Query(description=_POD_PART)] = None,
) -> PortfolioWarningsResponse:
    """Early warnings across the readable projects, grouped by kind with a count each.

    Each item names the pods it sits in. With ``pod_id``, only that pod's part; a
    developer reads their own pod's, in counts and teams.
    """
    policy = AuthorizationPolicy()
    reads = policy.can(principal, Capability.READ_TEAM_AGGREGATE) or policy.can(
        principal, Capability.READ_EXEC_AGGREGATE
    )
    if not reads and not (pod_id and principal.has_role(Role.DEV)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_AN_AGGREGATE_READER)
    try:
        view = await service.read(
            principal, as_of, project_id=project_id or None, pod_id=pod_id or None, search=q
        )
    except (AuthorizationDenied, GraphNotFound) as exc:
        raise _refused(exc) from exc
    return PortfolioWarningsResponse.from_view(view)


def _refused(exc: AuthorizationDenied | GraphNotFound) -> HTTPException:
    if isinstance(exc, GraphNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
