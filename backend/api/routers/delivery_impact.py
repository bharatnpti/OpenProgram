"""Why the date moved: a project's, release's or pod's forecast move over a period, taken apart.

The gate is the delivery forecast's own, decided by DeliveryImpactService.access
for this route and for Ora's delivery_impact alike: a project or release for
its readers (a scrum master for the projects their pods work on); a pod whole
for the project's readers and the scrum master who runs it, dates only for a
scrum master's other pods; nothing for a developer. The tenant always comes
from the principal.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import (
    as_of_or_tenant_today,
    get_current_principal,
    get_delivery_impact_service,
)
from api.impact_dtos import DeliveryImpactResponse
from api.people_projection import PeopleWordsDep, for_reader
from core.application.impact_service import DeliveryImpactService, ImpactPeriod, PeriodKind
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.forecast import CommitmentScope, CommitmentScopeKind
from core.domain.impact import Measure

router = APIRouter(tags=["delivery"])


@router.get("/projects/{project_id}/delivery/impact", response_model=DeliveryImpactResponse)
async def delivery_impact(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DeliveryImpactService, Depends(get_delivery_impact_service)],
    words: PeopleWordsDep,
    as_of: Annotated[date, Depends(as_of_or_tenant_today)],
    period: Annotated[PeriodKind, Query()] = PeriodKind.SINCE_BASELINE,
    from_day: Annotated[
        date | None,
        Query(alias="from", description="The period's first day, with period=from."),
    ] = None,
    measure: Annotated[Measure, Query()] = Measure.P85,
    release_id: Annotated[str | None, Query(description="One release of the project.")] = None,
    pod_id: Annotated[str | None, Query(description="One pod's part of the project.")] = None,
) -> DeliveryImpactResponse:
    """Why the project's (one release's, one pod's) 85% or 50% date moved over the period:
    the forecast then and now, each driver in working days with its work items, the
    committed date's moves, the team's date, late and undated requirements, the
    timeline's markers, and what is not known."""
    if release_id and pod_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Ask for one release or one pod, not both.",
        )
    if period is PeriodKind.FROM_DAY and from_day is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A period from a named day needs the day (from).",
        )
    scope = (
        CommitmentScope(kind=CommitmentScopeKind.RELEASE, id=release_id, project_id=project_id)
        if release_id
        else CommitmentScope(kind=CommitmentScopeKind.POD, id=pod_id, project_id=project_id)
        if pod_id
        else CommitmentScope(kind=CommitmentScopeKind.PROJECT, id=project_id, project_id=project_id)
    )
    try:
        access = await service.access(principal, scope)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    try:
        view = await service.impact(
            principal.tenant_id,
            scope,
            period=ImpactPeriod(kind=period, from_day=from_day),
            measure=measure,
            as_of=as_of,
            access=access,
        )
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    # An executive reads who moved the committed date as their role (D87, D98).
    return for_reader(DeliveryImpactResponse.from_view(view), words)
