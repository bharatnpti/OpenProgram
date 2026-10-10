"""Admin › Jira writes: the tenant's master switch, one switch per kind of write, the projects.

Runtime config (``manage_config``: an admin); anyone else gets 403 on both
routes. GET answers what is in force and where each value comes from (the
default, the deployment's environment, or an admin), with the latest changes.
PUT changes only what it gives; an unknown field or a project key that cannot
be saved is a 422. Every change is audited by ``JiraWritesService``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_current_principal, get_jira_writes_service, get_person_names
from api.jira_writes_dtos import JiraWritesResponse, JiraWritesUpdateRequest
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.jira_writes_service import JiraWritesService
from core.application.person_names import PersonNames
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied
from core.domain.jira_writes import JiraWritesError

router = APIRouter(tags=["config"])

#: How many changes the panel lists.
CHANGES_SHOWN = 20


Service = Annotated[JiraWritesService, Depends(get_jira_writes_service)]
Names = Annotated[PersonNames, Depends(get_person_names)]
Caller = Annotated[Principal, Depends(get_current_principal)]


@router.get("/config/tenant/jira-writes", response_model=JiraWritesResponse)
async def get_jira_writes(principal: Caller, service: Service, names: Names) -> JiraWritesResponse:
    _ensure(principal)
    return await _response(principal.tenant_id, service, names)


@router.put("/config/tenant/jira-writes", response_model=JiraWritesResponse)
async def update_jira_writes(
    request: JiraWritesUpdateRequest, principal: Caller, service: Service, names: Names
) -> JiraWritesResponse:
    _ensure(principal)
    try:
        update = request.to_domain()
    except JiraWritesError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    await service.update(principal.tenant_id, update, actor=principal.subject)
    return await _response(principal.tenant_id, service, names)


async def _response(
    tenant_id: str, service: JiraWritesService, names: PersonNames
) -> JiraWritesResponse:
    writes = await service.effective(tenant_id)
    changes = await service.changes(tenant_id, limit=CHANGES_SHOWN)
    resolved = await names.resolve(tenant_id, {change.actor for change in changes})
    return JiraWritesResponse.from_domain(writes, changes, resolved)


def _ensure(principal: Principal) -> None:
    try:
        AuthorizationPolicy().ensure(principal, Capability.MANAGE_CONFIG)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
