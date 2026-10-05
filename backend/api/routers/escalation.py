"""Escalation matrices: after how long each kind of ask goes up, and to whom.

The tenant's matrix is what every project uses until it has its own; a
project's own replaces it, and removing that falls back to the tenant's.
Setting them up is runtime config, so every route needs manage_config. The
tenant always comes from the principal.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from api.dependencies import get_current_principal, get_escalation_matrix_service
from api.dtos import (
    EscalationMatrixRequest,
    EscalationMatrixResponse,
    EscalationOverviewResponse,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.escalation_matrix_service import EscalationMatrixService, MatrixSource
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.escalation_matrix import TENANT_SCOPE, EscalationMatrix, EscalationMatrixError

router = APIRouter(prefix="/config/escalation", tags=["escalation"])


@router.get("", response_model=EscalationOverviewResponse)
async def overview(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EscalationMatrixService, Depends(get_escalation_matrix_service)],
) -> EscalationOverviewResponse:
    _ensure(principal)
    tenant = await service.matrix_for(principal.tenant_id, TENANT_SCOPE)
    return EscalationOverviewResponse(
        tenant=EscalationMatrixResponse.from_view(tenant),
        projects=[
            EscalationMatrixResponse.from_matrix(matrix, MatrixSource.PROJECT)
            for matrix in await service.stored(principal.tenant_id)
            if matrix.project_id != TENANT_SCOPE
        ],
    )


@router.get("/projects/{project_id}", response_model=EscalationMatrixResponse)
async def project_matrix(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EscalationMatrixService, Depends(get_escalation_matrix_service)],
) -> EscalationMatrixResponse:
    """The matrix the project's asks escalate by, and where it comes from."""
    _ensure(principal)
    return EscalationMatrixResponse.from_view(
        await service.matrix_for(principal.tenant_id, project_id)
    )


@router.put("/tenant", response_model=EscalationMatrixResponse)
async def save_tenant_matrix(
    request: EscalationMatrixRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EscalationMatrixService, Depends(get_escalation_matrix_service)],
) -> EscalationMatrixResponse:
    _ensure(principal)
    return await _save(service, principal, TENANT_SCOPE, request)


@router.put("/projects/{project_id}", response_model=EscalationMatrixResponse)
async def save_project_matrix(
    project_id: str,
    request: EscalationMatrixRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EscalationMatrixService, Depends(get_escalation_matrix_service)],
) -> EscalationMatrixResponse:
    _ensure(principal)
    return await _save(service, principal, project_id, request)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_project_matrix(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[EscalationMatrixService, Depends(get_escalation_matrix_service)],
) -> Response:
    """Go back to the tenant's matrix."""
    _ensure(principal)
    await service.remove(principal.tenant_id, project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _save(
    service: EscalationMatrixService,
    principal: Principal,
    project_id: str,
    request: EscalationMatrixRequest,
) -> EscalationMatrixResponse:
    try:
        saved = await service.save(
            EscalationMatrix(
                tenant_id=principal.tenant_id,
                project_id=project_id,
                decision_owner_id=request.decision_owner_id,
                levels=tuple(level.to_domain() for level in request.levels),
            ),
            actor=principal.subject,
        )
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EscalationMatrixError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    source = MatrixSource.TENANT if project_id == TENANT_SCOPE else MatrixSource.PROJECT
    return EscalationMatrixResponse.from_matrix(saved, source)


def _ensure(principal: Principal) -> None:
    try:
        AuthorizationPolicy().ensure(principal, Capability.MANAGE_CONFIG)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
