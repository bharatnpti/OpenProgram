"""The tenant's console branding: the logo in the header.

Everyone signed in to the tenant may read it, because the header shows it on
every screen. Only an admin replaces or removes it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from api.dependencies import get_branding_service, get_current_principal
from api.dtos import BrandingResponse, TenantLogoUploadRequest
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.branding_service import (
    BrandingService,
    LogoRejected,
    LogoTooLarge,
    UnsupportedLogoType,
)
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied

router = APIRouter(tags=["branding"])


@router.get("/config/branding", response_model=BrandingResponse)
async def get_branding(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[BrandingService, Depends(get_branding_service)],
) -> BrandingResponse:
    # No capability check: the header shows the logo to every signed-in person,
    # whatever their role. The tenant comes from the principal, never the request.
    return BrandingResponse.from_domain(await service.logo(principal.tenant_id))


@router.put("/config/branding/logo", response_model=BrandingResponse)
async def replace_branding_logo(
    request: TenantLogoUploadRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[BrandingService, Depends(get_branding_service)],
) -> BrandingResponse:
    _ensure_admin(principal)
    try:
        logo = await service.replace_logo(
            principal.tenant_id,
            content_type=request.content_type,
            data_base64=request.data_base64,
            updated_by=principal.subject,
        )
    except LogoRejected as exc:
        raise _rejection(exc) from exc
    return BrandingResponse.from_domain(logo)


@router.delete("/config/branding/logo", status_code=status.HTTP_204_NO_CONTENT)
async def delete_branding_logo(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[BrandingService, Depends(get_branding_service)],
) -> Response:
    _ensure_admin(principal)
    # Idempotent: removing a logo that is already gone still leaves the default.
    await service.remove_logo(principal.tenant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _ensure_admin(principal: Principal) -> None:
    try:
        AuthorizationPolicy().ensure(principal, Capability.MANAGE_CONFIG)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def _rejection(exc: LogoRejected) -> HTTPException:
    if isinstance(exc, LogoTooLarge):
        status_code = status.HTTP_413_CONTENT_TOO_LARGE
    elif isinstance(exc, UnsupportedLogoType):
        status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    else:
        status_code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=status_code, detail=str(exc))
