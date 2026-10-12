"""The timeline per project and pod: kept forecast rows, dates and impact markers per row.

Stored rows only: nothing is simulated on a read. Who sees which row, and how
much of it, is TimelineService's rule (core/application/timeline_service.py):
a project's timeline for its readers and for whoever sets a pod's date (a scrum
master sees the pods only: their own whole, the others' dates); a pod's timeline
under the pod's dates' gate (``GET /pods/{id}/delivery``), a developer's own pod
included. The tenant always comes from the principal.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import get_current_principal, get_timeline_service
from api.timeline_dtos import TimelineResponse
from core.application.timeline_service import TimelineService
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied, GraphNotFound

router = APIRouter(tags=["delivery"])


@router.get("/projects/{project_id}/timeline", response_model=TimelineResponse)
async def project_timeline(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[TimelineService, Depends(get_timeline_service)],
    as_of: Annotated[date, Query(default_factory=date.today)],
    release_id: Annotated[str | None, Query(description="One release of the project.")] = None,
) -> TimelineResponse:
    """The project's (or one release's) row, its releases, then its pods worst verdict
    first, each from planning to its committed date with progress, the forecast band,
    the team's date, the committed date's history and the impact markers."""
    try:
        view = await service.project_timeline(
            principal, project_id, release_id=release_id, as_of=as_of
        )
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TimelineResponse.from_view(view)


@router.get("/pods/{pod_id}/timeline", response_model=TimelineResponse)
async def pod_timeline(
    pod_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[TimelineService, Depends(get_timeline_service)],
    as_of: Annotated[date, Query(default_factory=date.today)],
) -> TimelineResponse:
    """The pod's row for each project it works on, with the project's committed date as a
    reference tick."""
    try:
        view = await service.pod_timeline(principal, pod_id, as_of=as_of)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TimelineResponse.from_view(view)
