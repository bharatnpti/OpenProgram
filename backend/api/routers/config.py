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
    DirectoryItemResponse,
    DirectorySearchResponse,
    DirectorySyncResponse,
    DirectoryUserResponse,
    IdentityAutoMatchResponse,
    IdentityLinkResponse,
    IdentityLinkUpdateRequest,
    MemberFromDirectoryRequest,
    MemberTaskAssignmentRequest,
    PodEscalationContactsResponse,
    PodEscalationContactsUpdateRequest,
    PodMemberLinkRequest,
    ProgramProjectLinkRequest,
    TenantWritebackResponse,
    TenantWritebackUpdateRequest,
    UnmappedMemberResponse,
    WorkItemCreateRequest,
    WorkItemFromBranchRequest,
    WorkItemFromPrRequest,
    WorkItemTransitionRequest,
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
from core.domain.errors import (
    AuthorizationDenied,
    GraphNotFound,
    ProviderConfigurationError,
    ProviderUnavailable,
)
from core.domain.graph import GraphNode, JsonScalar, NodeKind
from core.domain.identity import IdentityLink
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


@router.get("/config/workstreams", response_model=list[ConfigNodeResponse])
async def list_config_workstreams(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> list[ConfigNodeResponse]:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return [
        ConfigNodeResponse.from_domain(node)
        for node in await service.list_nodes(principal.tenant_id, NodeKind.WORKSTREAM)
    ]


@router.post(
    "/config/workstreams",
    response_model=ConfigNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_config_workstream(
    request: ConfigNodeCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return ConfigNodeResponse.from_domain(
        await _create_node(service, principal.tenant_id, NodeKind.WORKSTREAM, request)
    )


@router.put("/config/workstreams/{workstream_id}", response_model=ConfigNodeResponse)
async def update_config_workstream(
    workstream_id: str,
    request: ConfigNodeUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return ConfigNodeResponse.from_domain(
        await _update_node(
            service,
            principal.tenant_id,
            workstream_id,
            NodeKind.WORKSTREAM,
            request,
        )
    )


@router.delete("/config/workstreams/{workstream_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config_workstream(
    workstream_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    await _delete_node(service, principal.tenant_id, workstream_id, NodeKind.WORKSTREAM)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/config/workstreams/{workstream_id}", response_model=ConfigNodeResponse)
async def get_config_workstream(
    workstream_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        node = await service.get_node(principal.tenant_id, workstream_id, NodeKind.WORKSTREAM)
    except GraphNotFound as exc:
        raise _http_error(exc) from exc
    return ConfigNodeResponse.from_domain(node)


@router.get("/config/work-items", response_model=list[ConfigNodeResponse])
async def list_config_work_items(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> list[ConfigNodeResponse]:
    _ensure(principal, Capability.MANAGE_CONFIG)
    return [
        ConfigNodeResponse.from_domain(node)
        for node in await service.list_nodes(principal.tenant_id, NodeKind.WORK_ITEM)
    ]


@router.post(
    "/config/work-items",
    response_model=ConfigNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_config_work_item(
    request: WorkItemCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    node = await service.create_work_item(
        principal.tenant_id,
        request.id,
        request.name,
        _work_item_metadata(
            request.metadata,
            state=request.state,
            item_type=request.item_type,
            repo=request.repo,
            branch=request.branch,
            pr_id=request.pr_id,
        ),
    )
    if request.workstream_id is not None:
        await service.link_work_item_to_workstream(
            principal.tenant_id, request.workstream_id, request.id
        )
    return ConfigNodeResponse.from_domain(node)


@router.post(
    "/config/work-items/from-branch",
    response_model=ConfigNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_config_work_item_from_branch(
    request: WorkItemFromBranchRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    node = await service.create_work_item_from_branch(
        principal.tenant_id,
        request.repo,
        request.branch,
        request.name,
        _work_item_metadata(
            request.metadata,
            item_type=request.item_type,
            repo=request.repo,
            branch=request.branch,
        ),
    )
    if request.workstream_id is not None:
        await service.link_work_item_to_workstream(
            principal.tenant_id, request.workstream_id, node.id
        )
    return ConfigNodeResponse.from_domain(node)


@router.post(
    "/config/work-items/from-pr",
    response_model=ConfigNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_config_work_item_from_pr(
    request: WorkItemFromPrRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    node = await service.create_work_item_from_pr(
        principal.tenant_id,
        request.repo,
        request.pr_id,
        request.title,
        _work_item_metadata(
            request.metadata,
            item_type=request.item_type,
            repo=request.repo,
            pr_id=request.pr_id,
        ),
    )
    if request.workstream_id is not None:
        await service.link_work_item_to_workstream(
            principal.tenant_id, request.workstream_id, node.id
        )
    return ConfigNodeResponse.from_domain(node)


@router.post("/config/work-items/{work_item_id}/transition", response_model=ConfigNodeResponse)
async def transition_config_work_item(
    work_item_id: str,
    request: WorkItemTransitionRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigNodeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        node = await service.transition_work_item(
            principal.tenant_id, work_item_id, request.new_state
        )
    except (ConfigConflict, ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return ConfigNodeResponse.from_domain(node)


@router.post(
    "/config/workstreams/{workstream_id}/work-items/{work_item_id}",
    response_model=ConfigEdgeResponse,
)
async def link_config_workstream_work_item(
    workstream_id: str,
    work_item_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigEdgeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        edge = await service.link_work_item_to_workstream(
            principal.tenant_id,
            workstream_id,
            work_item_id,
        )
    except (ConfigConflict, ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return ConfigEdgeResponse.from_domain(edge)


@router.delete(
    "/config/workstreams/{workstream_id}/work-items/{work_item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unlink_config_workstream_work_item(
    workstream_id: str,
    work_item_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        await service.unlink_work_item_from_workstream(
            principal.tenant_id,
            workstream_id,
            work_item_id,
        )
    except (ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@router.get("/config/members/unmapped", response_model=list[UnmappedMemberResponse])
async def list_config_unmapped_members(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> list[UnmappedMemberResponse]:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        members = await service.list_unmapped_members(principal.tenant_id)
    except ConfigValidationError as exc:
        raise _http_error(exc) from exc
    return [UnmappedMemberResponse.from_domain(member) for member in members]


@router.post(
    "/config/members/identity-links/auto-match",
    response_model=IdentityAutoMatchResponse,
)
async def auto_match_config_identity_links(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> IdentityAutoMatchResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        result = await service.auto_match_identity_links(principal.tenant_id)
    except ConfigValidationError as exc:
        raise _http_error(exc) from exc
    return IdentityAutoMatchResponse.from_domain(result)


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
    try:
        result = await service.sync(principal.tenant_id)
    except ProviderUnavailable as exc:
        raise _http_error(exc) from exc
    return DirectorySyncResponse(
        tenant_id=result.tenant_id,
        synced_count=result.synced_count,
        deactivated_count=result.deactivated_count,
    )


@router.post(
    "/config/members/from-directory",
    response_model=list[ConfigNodeResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_config_members_from_directory(
    request: MemberFromDirectoryRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> list[ConfigNodeResponse]:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        members = await service.add_members_from_directory(
            principal.tenant_id,
            request.external_ids,
        )
    except (ConfigConflict, ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return [ConfigNodeResponse.from_domain(member) for member in members]


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


@router.post(
    "/config/projects/{project_id}/workstreams/{workstream_id}",
    response_model=ConfigEdgeResponse,
)
async def link_config_project_workstream(
    project_id: str,
    workstream_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigEdgeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        edge = await service.link_project_workstream(
            principal.tenant_id,
            project_id,
            workstream_id,
        )
    except (ConfigConflict, ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return ConfigEdgeResponse.from_domain(edge)


@router.delete(
    "/config/projects/{project_id}/workstreams/{workstream_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unlink_config_project_workstream(
    project_id: str,
    workstream_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        await service.unlink_project_workstream(principal.tenant_id, project_id, workstream_id)
    except (ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/config/pods/{pod_id}/workstreams/{workstream_id}",
    response_model=ConfigEdgeResponse,
)
async def link_config_pod_workstream(
    pod_id: str,
    workstream_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigEdgeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        edge = await service.assign_pod_workstream(principal.tenant_id, pod_id, workstream_id)
    except (ConfigConflict, ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return ConfigEdgeResponse.from_domain(edge)


@router.delete(
    "/config/pods/{pod_id}/workstreams/{workstream_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unlink_config_pod_workstream(
    pod_id: str,
    workstream_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        await service.unassign_pod_workstream(principal.tenant_id, pod_id, workstream_id)
    except (ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/config/workstreams/{workstream_id}/tasks/{task_id}",
    response_model=ConfigEdgeResponse,
)
async def link_config_workstream_task(
    workstream_id: str,
    task_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> ConfigEdgeResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        edge = await service.link_workstream_task(principal.tenant_id, workstream_id, task_id)
    except (ConfigConflict, ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return ConfigEdgeResponse.from_domain(edge)


@router.delete(
    "/config/workstreams/{workstream_id}/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unlink_config_workstream_task(
    workstream_id: str,
    task_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        await service.unlink_workstream_task(principal.tenant_id, workstream_id, task_id)
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


@router.get(
    "/config/members/{member_id}/identity-link",
    response_model=IdentityLinkResponse,
)
async def get_config_member_identity_link(
    member_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> IdentityLinkResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        link = await service.get_identity_link(principal.tenant_id, member_id)
    except (ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return IdentityLinkResponse.from_domain(
        link or IdentityLink(tenant_id=principal.tenant_id, developer_id=member_id)
    )


@router.put(
    "/config/members/{member_id}/identity-link",
    response_model=IdentityLinkResponse,
)
async def update_config_member_identity_link(
    member_id: str,
    request: IdentityLinkUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> IdentityLinkResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        existing = await service.get_identity_link(principal.tenant_id, member_id)
        link = _merge_identity_link(
            request,
            existing or IdentityLink(tenant_id=principal.tenant_id, developer_id=member_id),
        )
        updated = await service.set_identity_link(link)
    except (ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return IdentityLinkResponse.from_domain(updated)


@router.get(
    "/config/pods/{pod_id}/escalation-contacts",
    response_model=PodEscalationContactsResponse,
)
async def get_config_pod_escalation_contacts(
    pod_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> PodEscalationContactsResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        contacts = await service.get_pod_escalation_contacts(principal.tenant_id, pod_id)
    except (ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return PodEscalationContactsResponse.from_domain(pod_id, contacts)


@router.put(
    "/config/pods/{pod_id}/escalation-contacts",
    response_model=PodEscalationContactsResponse,
)
async def update_config_pod_escalation_contacts(
    pod_id: str,
    request: PodEscalationContactsUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> PodEscalationContactsResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        updated = await service.set_pod_escalation_contacts(
            principal.tenant_id, pod_id, request.to_domain()
        )
    except (ConfigValidationError, GraphNotFound) as exc:
        raise _http_error(exc) from exc
    return PodEscalationContactsResponse.from_domain(pod_id, updated)


@router.get("/config/tenant/writeback", response_model=TenantWritebackResponse)
async def get_config_tenant_writeback(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
    settings: Annotated[Settings, Depends(get_settings_from_request)],
) -> TenantWritebackResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        enabled = await service.get_tenant_writeback_enabled(
            principal.tenant_id, settings.jira_writeback_enabled
        )
    except ConfigValidationError as exc:
        raise _http_error(exc) from exc
    return TenantWritebackResponse(enabled=enabled)


@router.put("/config/tenant/writeback", response_model=TenantWritebackResponse)
async def update_config_tenant_writeback(
    request: TenantWritebackUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[ConfigService, Depends(get_config_service)],
) -> TenantWritebackResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        await service.set_tenant_writeback_enabled(principal.tenant_id, request.enabled)
    except ConfigValidationError as exc:
        raise _http_error(exc) from exc
    return TenantWritebackResponse(enabled=request.enabled)


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


@router.get("/workstreams", response_model=list[DirectoryItemResponse])
async def list_workstreams(
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DirectoryService, Depends(get_directory_service)],
) -> list[DirectoryItemResponse]:
    _ensure(principal, Capability.READ_DIRECTORY)
    return [
        DirectoryItemResponse.from_view(item)
        for item in await service.list_workstreams(principal.tenant_id, as_of)
    ]


@router.get("/workstreams/{workstream_id}", response_model=DirectoryItemResponse)
async def get_workstream(
    workstream_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DirectoryService, Depends(get_directory_service)],
) -> DirectoryItemResponse:
    _ensure(principal, Capability.READ_DIRECTORY)
    try:
        item = await service.get_workstream(principal.tenant_id, workstream_id, as_of)
    except GraphNotFound as exc:
        raise _http_error(exc) from exc
    return DirectoryItemResponse.from_view(item)


@router.get("/projects/{project_id}/workstreams", response_model=list[DirectoryItemResponse])
async def list_project_workstreams(
    project_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[DirectoryService, Depends(get_directory_service)],
) -> list[DirectoryItemResponse]:
    _ensure(principal, Capability.READ_DIRECTORY)
    try:
        items = await service.list_project_workstreams(principal.tenant_id, project_id, as_of)
    except GraphNotFound as exc:
        raise _http_error(exc) from exc
    return [DirectoryItemResponse.from_view(item) for item in items]


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
            _node_metadata(
                request.metadata,
                description=request.description,
                code=request.code,
                jira_project_key=(
                    request.jira_project_key
                    if "jira_project_key" in request.model_fields_set
                    else _UNCHANGED
                ),
                jira_base_jql=(
                    request.jira_base_jql
                    if "jira_base_jql" in request.model_fields_set
                    else _UNCHANGED
                ),
                jira_board_id=(
                    request.jira_board_id
                    if "jira_board_id" in request.model_fields_set
                    else _UNCHANGED
                ),
                jira_filter_jql=(
                    request.jira_filter_jql
                    if "jira_filter_jql" in request.model_fields_set
                    else _UNCHANGED
                ),
                github_repos=(
                    request.github_repos
                    if "github_repos" in request.model_fields_set
                    else _UNCHANGED
                ),
                description_set="description" in request.model_fields_set,
                code_set="code" in request.model_fields_set,
            ),
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
        or "jira_project_key" in request.model_fields_set
        or "jira_base_jql" in request.model_fields_set
        or "jira_board_id" in request.model_fields_set
        or "jira_filter_jql" in request.model_fields_set
        or "github_repos" in request.model_fields_set
    ):
        metadata = _node_metadata(
            request.metadata or {},
            description=request.description,
            code=request.code,
            jira_project_key=(
                request.jira_project_key
                if "jira_project_key" in request.model_fields_set
                else _UNCHANGED
            ),
            jira_base_jql=(
                request.jira_base_jql if "jira_base_jql" in request.model_fields_set else _UNCHANGED
            ),
            jira_board_id=(
                request.jira_board_id if "jira_board_id" in request.model_fields_set else _UNCHANGED
            ),
            jira_filter_jql=(
                request.jira_filter_jql
                if "jira_filter_jql" in request.model_fields_set
                else _UNCHANGED
            ),
            github_repos=(
                request.github_repos if "github_repos" in request.model_fields_set else _UNCHANGED
            ),
            description_set="description" in request.model_fields_set,
            code_set="code" in request.model_fields_set,
        )
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


_UNCHANGED = object()


def _node_metadata(
    metadata: dict[str, JsonScalar],
    *,
    description: str | None,
    code: str | None,
    jira_project_key: str | None | object = _UNCHANGED,
    jira_base_jql: str | None | object = _UNCHANGED,
    jira_board_id: str | None | object = _UNCHANGED,
    jira_filter_jql: str | None | object = _UNCHANGED,
    github_repos: list[str] | None | object = _UNCHANGED,
    description_set: bool = True,
    code_set: bool = True,
) -> dict[str, JsonScalar]:
    merged = dict(metadata)
    if description_set:
        merged["description"] = description if description != "" else None
    if code_set:
        merged["code"] = code if code != "" else None
    _set_optional_string(merged, "jira_project_key", jira_project_key)
    _set_optional_string(merged, "jira_base_jql", jira_base_jql)
    _set_optional_string(merged, "jira_board_id", jira_board_id)
    _set_optional_string(merged, "jira_filter_jql", jira_filter_jql)
    if github_repos is not _UNCHANGED:
        repos = github_repos if isinstance(github_repos, list) else []
        merged["github_repos"] = ",".join(repos) if repos else None
    return merged


def _set_optional_string(
    metadata: dict[str, JsonScalar],
    key: str,
    value: str | None | object,
) -> None:
    if value is _UNCHANGED:
        return
    metadata[key] = value if isinstance(value, str) and value else None


def _work_item_metadata(
    metadata: dict[str, JsonScalar],
    *,
    state: str | object = _UNCHANGED,
    item_type: str | object = _UNCHANGED,
    repo: str | None | object = _UNCHANGED,
    branch: str | None | object = _UNCHANGED,
    pr_id: str | None | object = _UNCHANGED,
) -> dict[str, JsonScalar]:
    merged = dict(metadata)
    _set_optional_string(merged, "state", state)
    _set_optional_string(merged, "item_type", item_type)
    _set_optional_string(merged, "repo", repo)
    _set_optional_string(merged, "branch", branch)
    _set_optional_string(merged, "pr_id", pr_id)
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
        write_back_consent=existing.write_back_consent,
    )


def _merge_identity_link(
    request: IdentityLinkUpdateRequest,
    existing: IdentityLink,
) -> IdentityLink:
    fields = request.model_fields_set
    return IdentityLink(
        tenant_id=existing.tenant_id,
        developer_id=existing.developer_id,
        chat_user_id=(request.chat_user_id if "chat_user_id" in fields else existing.chat_user_id),
        jira_account_id=(
            request.jira_account_id if "jira_account_id" in fields else existing.jira_account_id
        ),
        jira_email=request.jira_email if "jira_email" in fields else existing.jira_email,
        vcs_username=(request.vcs_username if "vcs_username" in fields else existing.vcs_username),
    )


def _ensure(principal: Principal, capability: Capability) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProviderConfigurationError):
        return HTTPException(status_code=status.HTTP_424_FAILED_DEPENDENCY, detail=str(exc))
    if isinstance(exc, ProviderUnavailable):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, GraphNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ConfigConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))
