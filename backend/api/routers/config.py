from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from api.dependencies import (
    get_config_service,
    get_current_principal,
    get_directory_service,
    get_directory_sync_service,
    get_settings_from_request,
)
from api.dtos import (
    CheckinPreferenceResponse,
    CheckinPreferenceUpdateRequest,
    ConfigEdgeResponse,
    ConfigNodeCreateRequest,
    ConfigNodeResponse,
    ConfigNodeUpdateRequest,
    DirectorySearchResponse,
    DirectoryItemResponse,
    DirectorySyncResponse,
    DirectoryUserResponse,
    MemberFromDirectoryRequest,
    MemberTaskAssignmentRequest,
    PodMemberLinkRequest,
    ProgramProjectLinkRequest,
)
from config.settings import Settings
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.config_service import (
    ConfigConflict,
    ConfigService,
    ConfigValidationError,
    DirectoryService,
)
from core.application.directory_sync_service import DirectorySyncService
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.graph import GraphNode, JsonScalar, NodeKind
from core.domain.status import CheckInPreference

router = APIRouter(tags=["config"])


@router.get("/config/programs", response_model=list[ConfigNodeResponse])
async def list_config_programs(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> list[ConfigNodeResponse]:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return [
        ConfigNodeResponse.from_domain(node)
        for node in await service.list_nodes(principal.tenant_id, NodeKind.PROGRAM)
    ]


@router.post(
    "/config/programs",
    response_model=ConfigNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_config_program(
    request: ConfigNodeCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return ConfigNodeResponse.from_domain(
        await _create_node(service, principal.tenant_id, NodeKind.PROGRAM, request)
    )


@router.put("/config/programs/{program_id}", response_model=ConfigNodeResponse)
async def update_config_program(
    program_id: str,
    request: ConfigNodeUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return ConfigNodeResponse.from_domain(
        await _update_node(service, principal.tenant_id, program_id, NodeKind.PROGRAM, request)
    )


@router.delete("/config/programs/{program_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config_program(
    program_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    await _delete_node(service, principal.tenant_id, program_id, NodeKind.PROGRAM)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/config/programs/{program_id}", response_model=ConfigNodeResponse)
async def get_config_program(
    program_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        node = await service.get_node(principal.tenant_id, program_id, NodeKind.PROGRAM)
    except GraphNotFound as exc:
        raise _http_error(exc) from exc
    return ConfigNodeResponse.from_domain(node)


@router.get("/config/projects", response_model=list[ConfigNodeResponse])
async def list_config_projects(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> list[ConfigNodeResponse]:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return [
        ConfigNodeResponse.from_domain(node)
        for node in await service.list_nodes(principal.tenant_id, NodeKind.PROJECT)
    ]


@router.post(
    "/config/projects",
    response_model=ConfigNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_config_project(
    request: ConfigNodeCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return ConfigNodeResponse.from_domain(
        await _create_node(service, principal.tenant_id, NodeKind.PROJECT, request)
    )


@router.put("/config/projects/{project_id}", response_model=ConfigNodeResponse)
async def update_config_project(
    project_id: str,
    request: ConfigNodeUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return ConfigNodeResponse.from_domain(
        await _update_node(service, principal.tenant_id, project_id, NodeKind.PROJECT, request)
    )


@router.delete("/config/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config_project(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    await _delete_node(service, principal.tenant_id, project_id, NodeKind.PROJECT)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/config/projects/{project_id}", response_model=ConfigNodeResponse)
async def get_config_project(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        node = await service.get_node(principal.tenant_id, project_id, NodeKind.PROJECT)
    except GraphNotFound as exc:
        raise _http_error(exc) from exc
    return ConfigNodeResponse.from_domain(node)


@router.get("/config/pods", response_model=list[ConfigNodeResponse])
async def list_config_pods(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> list[ConfigNodeResponse]:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return [
        ConfigNodeResponse.from_domain(node)
        for node in await service.list_nodes(principal.tenant_id, NodeKind.POD)
    ]


@router.post(
    "/config/pods",
    response_model=ConfigNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_config_pod(
    request: ConfigNodeCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return ConfigNodeResponse.from_domain(
        await _create_node(service, principal.tenant_id, NodeKind.POD, request)
    )


@router.put("/config/pods/{pod_id}", response_model=ConfigNodeResponse)
async def update_config_pod(
    pod_id: str,
    request: ConfigNodeUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return ConfigNodeResponse.from_domain(
        await _update_node(service, principal.tenant_id, pod_id, NodeKind.POD, request)
    )


@router.delete("/config/pods/{pod_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config_pod(
    pod_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    await _delete_node(service, principal.tenant_id, pod_id, NodeKind.POD)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/config/pods/{pod_id}", response_model=ConfigNodeResponse)
async def get_config_pod(
    pod_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        node = await service.get_node(principal.tenant_id, pod_id, NodeKind.POD)
    except GraphNotFound as exc:
        raise _http_error(exc) from exc
    return ConfigNodeResponse.from_domain(node)


@router.get("/config/members", response_model=list[ConfigNodeResponse])
async def list_config_members(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> list[ConfigNodeResponse]:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return [
        ConfigNodeResponse.from_domain(node)
        for node in await service.list_nodes(principal.tenant_id, NodeKind.DEVELOPER)
    ]


@router.get("/config/directory/users", response_model=DirectorySearchResponse)
async def search_config_directory_users(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
    query: str = Query(default=""),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> DirectorySearchResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    users, total = await service.search_directory(principal.tenant_id, query, limit, offset)
    return DirectorySearchResponse(
        items=[DirectoryUserResponse.from_domain(user) for user in users],
        total=total,
    )


@router.post("/config/directory/sync", response_model=DirectorySyncResponse)
async def sync_config_directory(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DirectorySyncService, Depends(get_directory_sync_service)],
) -> DirectorySyncResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    result = await service.sync(principal.tenant_id)
    return DirectorySyncResponse(
        tenant_id=result.tenant_id,
        synced_count=result.synced_count,
        deactivated_count=result.deactivated_count,
    )


@router.post("/config/members/from-directory", response_model=list[ConfigNodeResponse], status_code=status.HTTP_201_CREATED)
async def create_config_members_from_directory(
    request: MemberFromDirectoryRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> list[ConfigNodeResponse]:
    _ensure(principal, Capability.MANAGE_CONFIG)
    members: list[ConfigNodeResponse] = []
    for external_id in request.external_ids:
        members.append(
            ConfigNodeResponse.from_domain(
                await service.add_member_from_directory(principal.tenant_id, external_id)
            )
        )
    return members


@router.post(
    "/config/members",
    response_model=ConfigNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_config_member(
    request: ConfigNodeCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return ConfigNodeResponse.from_domain(
        await _create_node(service, principal.tenant_id, NodeKind.DEVELOPER, request)
    )


@router.put("/config/members/{member_id}", response_model=ConfigNodeResponse)
async def update_config_member(
    member_id: str,
    request: ConfigNodeUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return ConfigNodeResponse.from_domain(
        await _update_node(service, principal.tenant_id, member_id, NodeKind.DEVELOPER, request)
    )


@router.delete("/config/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config_member(
    member_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    await _delete_node(service, principal.tenant_id, member_id, NodeKind.DEVELOPER)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/config/members/{member_id}", response_model=ConfigNodeResponse)
async def get_config_member(
    member_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        node = await service.get_node(principal.tenant_id, member_id, NodeKind.DEVELOPER)
    except GraphNotFound as exc:
        raise _http_error(exc) from exc
    return ConfigNodeResponse.from_domain(node)


@router.post("/config/projects/{project_id}/program", response_model=ConfigEdgeResponse)
async def link_config_project_program(
    project_id: str,
    request: ProgramProjectLinkRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigEdgeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        edge = await service.link_program_project(
            principal.tenant_id, request.program_id, project_id
        )
    except (ConfigConflict, ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return ConfigEdgeResponse.from_domain(edge)


@router.delete("/config/projects/{project_id}/program", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_config_project_program(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
    program_id: Annotated[str | None, Query()] = None,
) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        await service.unlink_program_project(principal.tenant_id, program_id, project_id)
    except (ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/config/pods/{pod_id}/projects/{project_id}", response_model=ConfigEdgeResponse)
async def link_config_pod_project(
    pod_id: str,
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigEdgeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        edge = await service.link_project_pod(principal.tenant_id, project_id, pod_id)
    except (ConfigConflict, ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return ConfigEdgeResponse.from_domain(edge)


@router.delete(
    "/config/pods/{pod_id}/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unlink_config_pod_project(
    pod_id: str,
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        await service.unlink_project_pod(principal.tenant_id, project_id, pod_id)
    except (ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/config/pods/{pod_id}/members/{member_id}", response_model=ConfigEdgeResponse)
async def link_config_pod_member(
    pod_id: str,
    member_id: str,
    request: PodMemberLinkRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigEdgeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        edge = await service.link_pod_member(
            principal.tenant_id,
            pod_id,
            member_id,
            request.role,
            date.today(),
        )
    except (ConfigConflict, ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return ConfigEdgeResponse.from_domain(edge)


@router.delete(
    "/config/pods/{pod_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unlink_config_pod_member(
    pod_id: str,
    member_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        await service.unlink_pod_member(principal.tenant_id, pod_id, member_id)
    except (ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/config/members/{member_id}/tasks", response_model=ConfigEdgeResponse)
async def assign_config_member_task(
    member_id: str,
    request: MemberTaskAssignmentRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigEdgeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        edge = await service.assign_member_task(principal.tenant_id, member_id, request.task_id)
    except (ConfigConflict, ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return ConfigEdgeResponse.from_domain(edge)


@router.delete("/config/members/{member_id}/tasks", status_code=status.HTTP_204_NO_CONTENT)
async def unassign_config_member_task(
    member_id: str,
    task_id: Annotated[str, Query(min_length=1)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        await service.unassign_member_task(principal.tenant_id, member_id, task_id)
    except (ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/config/members/{member_id}/checkin-preference",
    response_model=CheckinPreferenceResponse,
)
async def get_config_member_checkin_preference(
    member_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
    settings: Annotated[Settings, Depends(get_settings_from_request)],
) -> CheckinPreferenceResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        preference = await service.get_checkin_preference(principal.tenant_id, member_id)
    except GraphNotFound as exc:
        raise _http_error(exc) from exc
    return CheckinPreferenceResponse.from_domain(
        preference or _default_preference(principal.tenant_id, member_id, settings)
    )


@router.put(
    "/config/members/{member_id}/checkin-preference",
    response_model=CheckinPreferenceResponse,
)
async def update_config_member_checkin_preference(
    member_id: str,
    request: CheckinPreferenceUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
    settings: Annotated[Settings, Depends(get_settings_from_request)],
) -> CheckinPreferenceResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        existing = await service.get_checkin_preference(principal.tenant_id, member_id)
        preference = _merge_preference(
            request,
            existing or _default_preference(principal.tenant_id, member_id, settings),
        )
        updated = await service.record_checkin_preference(preference)
    except (ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return CheckinPreferenceResponse.from_domain(updated)


@router.get("/config/checkin-preferences", response_model=list[CheckinPreferenceResponse])
async def list_config_checkin_preferences(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
    settings: Annotated[Settings, Depends(get_settings_from_request)],
) -> list[CheckinPreferenceResponse]:
    _ensure(principal, Capability.MANAGE_CONFIG)
    members = await service.list_nodes(principal.tenant_id, NodeKind.DEVELOPER)
    preferences = {
        preference.developer_id: preference
        for preference in await service.list_checkin_preferences(principal.tenant_id)
    }
    return [
        CheckinPreferenceResponse.from_domain(
            preferences.get(member.id)
            or _default_preference(principal.tenant_id, member.id, settings)
        )
        for member in members
    ]


@router.get("/programs", response_model=list[DirectoryItemResponse])
async def list_programs(
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DirectoryService, Depends(get_directory_service)],
) -> list[DirectoryItemResponse]:
    _ensure(principal, Capability.READ_DIRECTORY)
    return [
        DirectoryItemResponse.from_view(item)
        for item in await service.list_programs(principal.tenant_id, as_of)
    ]


@router.get("/projects", response_model=list[DirectoryItemResponse])
async def list_projects(
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DirectoryService, Depends(get_directory_service)],
) -> list[DirectoryItemResponse]:
    _ensure(principal, Capability.READ_DIRECTORY)
    return [
        DirectoryItemResponse.from_view(item)
        for item in await service.list_projects(principal.tenant_id, as_of)
    ]


@router.get("/pods", response_model=list[DirectoryItemResponse])
async def list_pods(
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DirectoryService, Depends(get_directory_service)],
) -> list[DirectoryItemResponse]:
    _ensure(principal, Capability.READ_DIRECTORY)
    return [
        DirectoryItemResponse.from_view(item)
        for item in await service.list_pods(principal.tenant_id, as_of)
    ]


async def _create_node(
    service: ConfigService,
    tenant_id: str,
    kind: NodeKind,
    request: ConfigNodeCreateRequest,
) -> GraphNode:
    try:
        return await service.create_node(
            tenant_id,
            kind,
            request.id,
            request.name,
            _node_metadata(request.metadata, request.description, request.code),
        )
    except (ConfigConflict, ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc


async def _update_node(
    service: ConfigService,
    tenant_id: str,
    id: str,
    kind: NodeKind,
    request: ConfigNodeUpdateRequest,
) -> GraphNode:
    metadata: dict[str, JsonScalar] | None = None
    if (
        request.metadata is not None
        or "description" in request.model_fields_set
        or "code" in request.model_fields_set
    ):
        metadata = _node_metadata(request.metadata or {}, request.description, request.code)
    try:
        return await service.update_node(tenant_id, id, kind, request.name, metadata)
    except (ConfigConflict, ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc


async def _delete_node(
    service: ConfigService,
    tenant_id: str,
    id: str,
    kind: NodeKind,
) -> None:
    try:
        await service.delete_node(tenant_id, id, kind)
    except (ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc


def _node_metadata(
    metadata: dict[str, JsonScalar],
    description: str | None,
    code: str | None,
) -> dict[str, JsonScalar]:
    merged = dict(metadata)
    if description is not None:
        merged["description"] = description if description != "" else None
    if code is not None:
        merged["code"] = code if code != "" else None
    return merged


def _default_preference(
    tenant_id: str,
    member_id: str,
    settings: Settings,
) -> CheckInPreference:
    return CheckInPreference(
        tenant_id=tenant_id,
        developer_id=member_id,
        timezone=settings.tenant_default_timezone,
        reply_wait_seconds=settings.checkin_reply_wait_seconds,
        final_reply_wait_seconds=settings.checkin_final_reply_wait_seconds,
    )


def _merge_preference(
    request: CheckinPreferenceUpdateRequest,
    existing: CheckInPreference,
) -> CheckInPreference:
    fields = request.model_fields_set
    return CheckInPreference(
        tenant_id=existing.tenant_id,
        developer_id=existing.developer_id,
        local_time=(
            request.local_time
            if "local_time" in fields and request.local_time is not None
            else existing.local_time
        ),
        timezone=request.timezone if "timezone" in fields else existing.timezone,
        weekdays=(
            tuple(request.weekdays)
            if "weekdays" in fields and request.weekdays is not None
            else existing.weekdays
        ),
        reply_wait_seconds=(
            request.reply_wait_seconds
            if "reply_wait_seconds" in fields and request.reply_wait_seconds is not None
            else existing.reply_wait_seconds
        ),
        final_reply_wait_seconds=(
            request.final_reply_wait_seconds
            if "final_reply_wait_seconds" in fields and request.final_reply_wait_seconds is not None
            else existing.final_reply_wait_seconds
        ),
    )


def _ensure(principal: Principal, capability: Capability) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, GraphNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ConfigConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))
