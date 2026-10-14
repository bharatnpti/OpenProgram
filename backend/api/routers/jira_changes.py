"""Today › "Jira changes waiting for your yes": the person's own list, and their answer (D89).

Under ``/me`` only, keyed on the caller: nobody lists or answers anyone
else's changes, an admin included. A proposal's answer goes through
``WriteBackService.resolve_proposal``, the path the chat's answer takes, under
the correlation's lock, then finishes a check-in held for it and sends one line
to the person's DM. 404: no such change for the caller. 422: a change the
console cannot make (a due date), or an answer that is not yes or no.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import (
    get_current_principal,
    get_delivery_scope_service,
    get_persona_view_service,
    get_registry,
    get_risk_service,
)
from api.jira_change_dtos import (
    JiraChangeAnswerRequest,
    JiraChangeAnswerResponse,
    JiraChangesResponse,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.delivery_scope import DeliveryScopeService
from core.application.jira_changes_service import (
    JiraChangeNotAnswerable,
    JiraChangeNotFound,
    JiraChangesService,
)
from core.application.persona_views import PersonaViewService
from core.application.risk_service import RiskService
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied
from infra.registry import ServiceRegistry

router = APIRouter(tags=["checkins"])

Caller = Annotated[Principal, Depends(get_current_principal)]


def get_jira_changes_service(
    registry: Annotated[ServiceRegistry, Depends(get_registry)],
    risks: Annotated[RiskService, Depends(get_risk_service)],
    scope: Annotated[DeliveryScopeService, Depends(get_delivery_scope_service)],
    persona: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> JiraChangesService:
    return JiraChangesService(
        write_back=registry.write_back_service(),
        status_repository=registry.status_repository(),
        graph_repository=registry.graph_repository(),
        identity_link_repository=registry.identity_link_repository(),
        project_drift=risks.project_drift,
        scope_of=scope.scope_of,
        focus_of=persona.focus,
        # Built only for an answer: the collector brings the chat and the model with it.
        finisher=registry.status_collector,
        tracker_name=registry.provider_names().tracker,
        today=registry.tenant_today,
    )


Service = Annotated[JiraChangesService, Depends(get_jira_changes_service)]


@router.get("/me/jira-changes", response_model=JiraChangesResponse)
async def my_jira_changes(principal: Caller, service: Service) -> JiraChangesResponse:
    """The caller's own changes waiting for their yes, each with the gate a yes would meet."""
    _ensure_own_work(principal)
    return JiraChangesResponse.from_view(
        await service.changes(principal.tenant_id, principal.subject)
    )


@router.post(
    "/me/jira-changes/{item_id}/answer",
    response_model=JiraChangeAnswerResponse,
    responses={
        404: {"description": "No such change is waiting for the caller."},
        422: {"description": "A change the console cannot make, such as a due date."},
    },
)
async def answer_my_jira_change(
    item_id: str,
    request: JiraChangeAnswerRequest,
    principal: Caller,
    service: Service,
) -> JiraChangeAnswerResponse:
    """Say yes or no to one change. Every gate of the write applies again to a yes."""
    _ensure_own_work(principal)
    try:
        view = await service.answer(principal.tenant_id, principal.subject, item_id, request.answer)
    except JiraChangeNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except JiraChangeNotAnswerable as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return JiraChangeAnswerResponse.from_view(view)


def _ensure_own_work(principal: Principal) -> None:
    try:
        AuthorizationPolicy().ensure(principal, Capability.READ_OWN_WORK)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
