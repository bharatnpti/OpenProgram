"""Planning complete: a project's or a release's baseline.

Reading the baselines needs read_project_progress, as the forecast's history
does. Marking planning complete needs set_project_dates: the product owner and
the manager (and an admin), the people who commit the project's and the
releases' dates. The tenant always comes from the principal.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.baseline_dtos import (
    PlanningBaselineResponse,
    PlanningBaselinesResponse,
    PlanningCompleteRequest,
)
from api.dependencies import as_of_or_tenant_today, get_baseline_service, get_current_principal
from api.people_projection import PeopleWordsDep, for_reader
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.baseline_service import BaselineService
from core.domain.auth import Principal
from core.domain.baseline import BaselineError
from core.domain.errors import AuthorizationDenied, GraphNotFound

router = APIRouter(tags=["delivery"])


@router.get("/projects/{project_id}/baselines", response_model=PlanningBaselinesResponse)
async def project_baselines(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[BaselineService, Depends(get_baseline_service)],
    words: PeopleWordsDep,
    as_of: Annotated[date, Depends(as_of_or_tenant_today)],
    release_id: Annotated[str | None, Query(description="One release of the project.")] = None,
) -> PlanningBaselinesResponse:
    """The project's (or one release's) planning baselines as they stood on ``as_of``,
    newest first, each with the scope of that day against it."""
    _ensure(principal, Capability.READ_PROJECT_PROGRESS)
    try:
        view = await service.baselines(
            principal.tenant_id, project_id, release_id=release_id, as_of=as_of
        )
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    # An executive reads who marked planning complete as their role (D87, D98).
    return for_reader(PlanningBaselinesResponse.from_view(view), words)


@router.post(
    "/projects/{project_id}/baselines",
    response_model=PlanningBaselineResponse,
    status_code=status.HTTP_201_CREATED,
)
async def mark_planning_complete(
    project_id: str,
    request: PlanningCompleteRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[BaselineService, Depends(get_baseline_service)],
) -> PlanningBaselineResponse:
    """Freeze the requirements in scope as the new baseline; an earlier one is kept, replaced."""
    _ensure(principal, Capability.SET_PROJECT_DATES)
    try:
        view = await service.mark_planning_complete(
            principal.tenant_id,
            project_id,
            release_id=request.release_id,
            planned_on=request.planned_on,
            change_label=request.change_label,
            grace_working_days=request.grace_working_days,
            note=request.note,
            actor=principal.subject,
        )
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except BaselineError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return PlanningBaselineResponse.from_view(view)


def _ensure(principal: Principal, capability: Capability) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
