"""The tenant's connections to external systems, set up by an admin.

Every route needs manage_config, and saving a secret also needs
write_connector_secret. Secrets go in and never come back out: a read names the
secret fields that hold a value and nothing more. The tenant always comes from
the principal, never from the request.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status

from api.dependencies import get_connection_service, get_current_principal
from api.dtos import (
    ConnectionResponse,
    ConnectionTestRequest,
    ConnectionTestResponse,
    ConnectionUpdateRequest,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.connection_service import (
    ConnectionConflict,
    ConnectionDraft,
    ConnectionService,
    UnknownConnector,
)
from core.domain.auth import Principal
from core.domain.connections import ConnectionValidationError
from core.domain.errors import AuthorizationDenied, OpenProgramError

router = APIRouter(prefix="/config/integrations", tags=["integrations"])


@router.get("", response_model=list[ConnectionResponse])
async def list_connections(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> list[ConnectionResponse]:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return [
        ConnectionResponse.from_view(view)
        for view in await service.list_connections(principal.tenant_id)
    ]


@router.get("/{connector}", response_model=ConnectionResponse)
async def get_connection(
    connector: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> ConnectionResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        view = await service.connection(principal.tenant_id, connector)
    except OpenProgramError as exc:
        raise _http_error(exc) from exc
    return ConnectionResponse.from_view(view)


@router.put("/{connector}", response_model=ConnectionResponse)
async def save_connection(
    connector: str,
    request: ConnectionUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> ConnectionResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    if request.secrets:
        _ensure(principal, Capability.WRITE_CONNECTOR_SECRET)
    try:
        view = await service.save(
            principal.tenant_id,
            connector,
            enabled=request.enabled,
            settings=request.settings,
            secrets=request.secrets,
            actor=principal.subject,
        )
    except OpenProgramError as exc:
        raise _http_error(exc) from exc
    return ConnectionResponse.from_view(view)


@router.delete("/{connector}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_connection(
    connector: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConnectionService, Depends(get_connection_service)],
) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    _ensure(principal, Capability.WRITE_CONNECTOR_SECRET)
    try:
        # Idempotent: removing a connection that is already gone changes nothing.
        await service.remove(principal.tenant_id, connector)
    except OpenProgramError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{connector}/test", response_model=ConnectionTestResponse)
async def test_connection(
    connector: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConnectionService, Depends(get_connection_service)],
    request: Annotated[ConnectionTestRequest | None, Body()] = None,
) -> ConnectionTestResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    draft = (
        ConnectionDraft(settings=request.settings, secrets=request.secrets)
        if request is not None and (request.settings or request.secrets)
        else None
    )
    try:
        view = await service.test(principal.tenant_id, connector, draft=draft)
    except OpenProgramError as exc:
        raise _http_error(exc) from exc
    return ConnectionTestResponse.from_view(view)


def _ensure(principal: Principal, capability: Capability) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def _http_error(exc: OpenProgramError) -> HTTPException:
    if isinstance(exc, UnknownConnector):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ConnectionConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, ConnectionValidationError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
