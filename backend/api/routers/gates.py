"""Gates, their items, and the questions requirements wait on.

Gate templates are runtime config (manage_config). The board is read by
whoever reads the project's progress or works its gates. Confirming,
dismissing and adding items or questions needs edit_gates; signing an item off
is further limited to the roles its gate names for the item's kind. The
tenant always comes from the principal.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from api.dependencies import get_current_principal, get_forecast_service, get_gate_service
from api.dtos import (
    GateBoardResponse,
    GateItemCreateRequest,
    GateItemResponse,
    GateItemSignOffRequest,
    GateScanResponse,
    GateTemplateDto,
    GateTemplatesResponse,
    QuestionCreateRequest,
    QuestionUpdateRequest,
    TrackedQuestionResponse,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.forecast_service import ForecastService
from core.application.gate_service import GateService
from core.domain.auth import Principal
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.forecast import Release
from core.domain.gates import GateError, GateTemplate, ItemKind

router = APIRouter(tags=["gates"])


@router.get("/config/gates", response_model=GateTemplatesResponse)
async def list_templates(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[GateService, Depends(get_gate_service)],
) -> GateTemplatesResponse:
    _ensure(principal, Capability.MANAGE_CONFIG)
    templates, is_default = await service.templates(principal.tenant_id)
    return GateTemplatesResponse(
        templates=[GateTemplateDto.from_domain(item) for item in templates],
        is_default=is_default,
    )


@router.put("/config/gates", response_model=GateTemplateDto)
async def save_template(
    request: GateTemplateDto,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[GateService, Depends(get_gate_service)],
) -> GateTemplateDto:
    """Create a gate (empty template_id) or replace the one with that id."""
    _ensure(principal, Capability.MANAGE_CONFIG)
    try:
        saved = await service.save_template(
            GateTemplate(
                tenant_id=principal.tenant_id,
                template_id=request.template_id,
                name=request.name,
                guards_stage=request.guards_stage,
                kinds=tuple(
                    ItemKind(
                        key=kind.key,
                        label=kind.label,
                        sign_off_roles=tuple(kind.sign_off_roles),
                        evidence_required=kind.evidence_required,
                        headings=tuple(kind.headings),
                        gherkin=kind.gherkin,
                    )
                    for kind in request.kinds
                ),
                issue_types=tuple(request.issue_types),
                enabled=request.enabled,
            ),
            actor=principal.subject,
        )
    except GateError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return GateTemplateDto.from_domain(saved)


@router.delete("/config/gates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[GateService, Depends(get_gate_service)],
) -> Response:
    _ensure(principal, Capability.MANAGE_CONFIG)
    await service.delete_template(principal.tenant_id, template_id, actor=principal.subject)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/projects/{project_id}/gates", response_model=GateBoardResponse)
async def gate_board(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[GateService, Depends(get_gate_service)],
    forecasts: Annotated[ForecastService, Depends(get_forecast_service)],
    as_of: Annotated[date, Query(default_factory=date.today)],
    release_id: Annotated[str | None, Query()] = None,
) -> GateBoardResponse:
    _ensure_reader(principal)
    release = await _release(forecasts, principal, project_id, release_id)
    try:
        view = await service.board(principal.tenant_id, project_id, as_of, release)
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return GateBoardResponse.from_view(view)


@router.post("/projects/{project_id}/gates/scan", response_model=GateScanResponse)
async def scan_now(
    project_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[GateService, Depends(get_gate_service)],
    forecasts: Annotated[ForecastService, Depends(get_forecast_service)],
    release_id: Annotated[str | None, Query()] = None,
    force: Annotated[bool, Query(description="Read every issue, changed or not.")] = False,
) -> GateScanResponse:
    _ensure(principal, Capability.EDIT_GATES)
    release = await _release(forecasts, principal, project_id, release_id)
    summary = await service.scan_scope(
        principal.tenant_id, project_id, date.today(), release, force=force
    )
    return GateScanResponse(
        read=summary.read,
        unchanged=summary.unchanged,
        failed=summary.failed,
        suggested_items=summary.suggested_items,
        questions=summary.questions,
    )


@router.post("/issues/{issue_key}/gate-items", response_model=GateItemResponse, status_code=201)
async def add_item(
    issue_key: str,
    request: GateItemCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[GateService, Depends(get_gate_service)],
) -> GateItemResponse:
    _ensure(principal, Capability.EDIT_GATES)
    try:
        item = await service.add_item(
            principal.tenant_id,
            issue_key,
            template_id=request.template_id,
            kind=request.kind,
            text=request.text,
            actor=principal.subject,
        )
    except (GateError, GraphNotFound) as exc:
        raise _error(exc) from exc
    return GateItemResponse.from_domain(item)


@router.post("/gate-items/{item_id}/confirm", response_model=GateItemResponse)
async def confirm_item(
    item_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[GateService, Depends(get_gate_service)],
) -> GateItemResponse:
    _ensure(principal, Capability.EDIT_GATES)
    try:
        item = await service.confirm_item(principal.tenant_id, item_id, actor=principal.subject)
    except GraphNotFound as exc:
        raise _error(exc) from exc
    return GateItemResponse.from_domain(item)


@router.post("/gate-items/{item_id}/dismiss", response_model=GateItemResponse)
async def dismiss_item(
    item_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[GateService, Depends(get_gate_service)],
) -> GateItemResponse:
    _ensure(principal, Capability.EDIT_GATES)
    try:
        item = await service.dismiss_item(principal.tenant_id, item_id, actor=principal.subject)
    except GraphNotFound as exc:
        raise _error(exc) from exc
    return GateItemResponse.from_domain(item)


@router.put("/gate-items/{item_id}/sign-off", response_model=GateItemResponse)
async def sign_off(
    item_id: str,
    request: GateItemSignOffRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[GateService, Depends(get_gate_service)],
) -> GateItemResponse:
    _ensure(principal, Capability.EDIT_GATES)
    try:
        item = await service.sign_off(
            principal.tenant_id,
            item_id,
            request.status,
            actor=principal.subject,
            roles=principal.roles,
            evidence_url=request.evidence_url,
            note=request.note,
        )
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except (GateError, GraphNotFound) as exc:
        raise _error(exc) from exc
    return GateItemResponse.from_domain(item)


@router.put("/questions/{question_id}", response_model=TrackedQuestionResponse)
async def update_question(
    question_id: str,
    request: QuestionUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[GateService, Depends(get_gate_service)],
) -> TrackedQuestionResponse:
    _ensure(principal, Capability.EDIT_GATES)
    try:
        question = await service.update_question(
            principal.tenant_id,
            question_id,
            actor=principal.subject,
            confirmed=request.confirmed,
            dismissed=request.dismissed,
            status=request.status,
        )
    except GraphNotFound as exc:
        raise _error(exc) from exc
    return TrackedQuestionResponse.from_domain(question)


@router.post(
    "/issues/{issue_key}/questions", response_model=TrackedQuestionResponse, status_code=201
)
async def add_question(
    issue_key: str,
    request: QuestionCreateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[GateService, Depends(get_gate_service)],
) -> TrackedQuestionResponse:
    _ensure(principal, Capability.EDIT_GATES)
    try:
        question = await service.add_question(
            principal.tenant_id,
            issue_key,
            asked_to=request.asked_to,
            summary=request.summary,
            actor=principal.subject,
        )
    except GateError as exc:
        raise _error(exc) from exc
    return TrackedQuestionResponse.from_domain(question)


async def _release(
    forecasts: ForecastService, principal: Principal, project_id: str, release_id: str | None
) -> Release | None:
    if not release_id:
        return None
    try:
        release = await forecasts.release(principal.tenant_id, release_id)
    except GraphNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if release.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such release.")
    return release


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, GraphNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


def _ensure_reader(principal: Principal) -> None:
    policy = AuthorizationPolicy()
    if not (
        policy.can(principal, Capability.READ_PROJECT_PROGRESS)
        or policy.can(principal, Capability.EDIT_GATES)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed.")


def _ensure(principal: Principal, capability: Capability) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
