from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import (
    get_cross_person_request_service,
    get_current_principal,
    get_flow_metrics_service,
    get_narrative_brief_repository,
    get_persona_view_service,
    get_portfolio_feed_service,
    get_risk_service,
    get_write_back_service,
)
from api.dtos import (
    CrossPersonRequestResponse,
    CrossPersonRequestsResponse,
    CrossPersonRequestStatusUpdateRequest,
    DriftFindingResponse,
    FocusResponse,
    NarrativeBriefResponse,
    NarrativeBriefsResponse,
    NodeTrendResponse,
    PodBlockersResponse,
    PodCheckinsResponse,
    PortfolioFeedResponse,
    PortfolioFlowResponse,
    PortfolioHeatmapResponse,
    PortfolioRisksResponse,
    ProgramTreeResponse,
    ProjectProgressResponse,
    ProjectRisksResponse,
    RiskFindingResponse,
    WorkstreamFlowResponse,
    WorkstreamProgressResponse,
    WriteBackAdoptionResponse,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.cross_person_service import CrossPersonRequestService
from core.application.flow_metrics_service import FlowMetricsService
from core.application.persona_views import PersonaViewService
from core.application.portfolio_feed_service import PortfolioFeedService
from core.application.risk_service import RiskService
from core.application.writeback_service import WriteBackService
from core.domain.auth import Principal
from core.domain.brief import BriefKind
from core.domain.cross_person import CrossPersonRequest, CrossPersonRequestStatus
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.graph import NodeKind
from core.ports.repositories import NarrativeBriefRepository

router = APIRouter(tags=["personas"])

# Entity kinds that carry a rolled-up RAG status in node_statuses.
_TREND_KINDS = frozenset(
    {NodeKind.PROGRAM, NodeKind.PROJECT, NodeKind.POD, NodeKind.DEVELOPER, NodeKind.TASK}
)


@router.get("/me/focus", response_model=FocusResponse)
async def my_focus(
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> FocusResponse:
    _ensure(principal, Capability.READ_OWN_WORK)
    view = await persona_service.focus(principal.tenant_id, principal.subject, as_of)
    return FocusResponse.from_view(view)


@router.get("/pods/{pod_id}/blockers", response_model=PodBlockersResponse)
async def pod_blockers(
    pod_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> PodBlockersResponse:
    _ensure(principal, Capability.READ_POD_BLOCKERS)
    view = await persona_service.pod_blockers(principal.tenant_id, pod_id, as_of)
    return PodBlockersResponse.from_view(view)


@router.get("/pods/{pod_id}/checkins", response_model=PodCheckinsResponse)
async def pod_checkins(
    pod_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> PodCheckinsResponse:
    _ensure(principal, Capability.READ_POD_CHECKINS)
    view = await persona_service.pod_checkins(principal.tenant_id, pod_id, as_of)
    return PodCheckinsResponse.from_view(view)


@router.get("/projects/{project_id}/progress", response_model=ProjectProgressResponse)
async def project_progress(
    project_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> ProjectProgressResponse:
    _ensure(principal, Capability.READ_PROJECT_PROGRESS)
    view = await persona_service.project_progress(principal.tenant_id, project_id, as_of)
    return ProjectProgressResponse.from_view(view)


@router.get("/workstreams/{workstream_id}/progress", response_model=WorkstreamProgressResponse)
async def workstream_progress(
    workstream_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> WorkstreamProgressResponse:
    _ensure(principal, Capability.READ_PROJECT_PROGRESS)
    view = await persona_service.workstream_progress(principal.tenant_id, workstream_id, as_of)
    return WorkstreamProgressResponse.from_view(view)


@router.get("/programs/{program_id}/tree", response_model=ProgramTreeResponse)
async def program_tree(
    program_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> ProgramTreeResponse:
    _ensure(principal, Capability.READ_PROGRAM_ROLLUP)
    view = await persona_service.program_tree(principal.tenant_id, program_id, as_of)
    return ProgramTreeResponse.from_view(view)


@router.get("/portfolio/heatmap", response_model=PortfolioHeatmapResponse)
async def portfolio_heatmap(
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
    program_root_id: Annotated[str | None, Query()] = None,
) -> PortfolioHeatmapResponse:
    _ensure(principal, Capability.READ_PORTFOLIO_HEATMAP)
    view = await persona_service.portfolio_heatmap(principal.tenant_id, as_of, program_root_id)
    return PortfolioHeatmapResponse.from_view(view)


@router.get("/persona/{level}/{entity_id}/trend", response_model=NodeTrendResponse)
async def node_trend(
    level: NodeKind,
    entity_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
    as_of: Annotated[date, Query(default_factory=date.today)],
    window_days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> NodeTrendResponse:
    _ensure_aggregate(principal)
    if level not in _TREND_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"trend is not available for entity kind '{level.value}'",
        )
    view = await persona_service.node_trend(
        principal.tenant_id, level, entity_id, as_of, window_days
    )
    return NodeTrendResponse.from_view(view)


@router.get("/workstreams/{workstream_id}/flow", response_model=WorkstreamFlowResponse)
async def workstream_flow(
    workstream_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[FlowMetricsService, Depends(get_flow_metrics_service)],
) -> WorkstreamFlowResponse:
    _ensure_aggregate(principal)
    view = await service.workstream_flow(principal.tenant_id, workstream_id, as_of)
    return WorkstreamFlowResponse.from_view(view)


@router.get("/portfolio/flow", response_model=PortfolioFlowResponse)
async def portfolio_flow(
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[FlowMetricsService, Depends(get_flow_metrics_service)],
) -> PortfolioFlowResponse:
    _ensure_aggregate(principal)
    view = await service.portfolio_flow(principal.tenant_id, as_of)
    return PortfolioFlowResponse.from_view(view)


@router.get("/portfolio/feed", response_model=PortfolioFeedResponse)
async def portfolio_feed(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PortfolioFeedService, Depends(get_portfolio_feed_service)],
    since: Annotated[datetime | None, Query()] = None,
) -> PortfolioFeedResponse:
    _ensure_aggregate(principal)
    view = await service.feed(principal.tenant_id, since)
    return PortfolioFeedResponse.from_view(view)


@router.get("/persona/briefs", response_model=NarrativeBriefsResponse)
async def narrative_briefs(
    principal: Annotated[Principal, Depends(get_current_principal)],
    repository: Annotated[NarrativeBriefRepository, Depends(get_narrative_brief_repository)],
    kind: Annotated[BriefKind | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> NarrativeBriefsResponse:
    # apiClient note: frontend client regen picks this up automatically.
    _ensure_aggregate(principal)
    briefs = await repository.latest_briefs(principal.tenant_id, kind, limit)
    return NarrativeBriefsResponse(
        briefs=[NarrativeBriefResponse.from_domain(brief) for brief in briefs],
    )


@router.get("/persona/writeback-adoption", response_model=WriteBackAdoptionResponse)
async def writeback_adoption(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WriteBackService, Depends(get_write_back_service)],
    window_days: Annotated[int | None, Query(ge=1, le=365)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> WriteBackAdoptionResponse:
    # Surfaces "Jira updates applied via check-in" so the time-saved is visible.
    _ensure_aggregate(principal)
    since = datetime.now(tz=UTC) - timedelta(days=window_days) if window_days is not None else None
    adoption = await service.adoption(principal.tenant_id, limit=limit, since=since)
    return WriteBackAdoptionResponse.from_domain(adoption)


@router.get("/portfolio/cross-person-requests", response_model=CrossPersonRequestsResponse)
async def portfolio_cross_person_requests(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[CrossPersonRequestService, Depends(get_cross_person_request_service)],
    status: Annotated[CrossPersonRequestStatus | None, Query()] = CrossPersonRequestStatus.OPEN,
) -> CrossPersonRequestsResponse:
    _ensure_aggregate(principal)
    requests = await service.list_portfolio(principal.tenant_id, status)
    return CrossPersonRequestsResponse(
        requests=[CrossPersonRequestResponse.from_domain(request) for request in requests],
    )


@router.get("/me/cross-person-requests", response_model=CrossPersonRequestsResponse)
async def my_cross_person_requests(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[CrossPersonRequestService, Depends(get_cross_person_request_service)],
) -> CrossPersonRequestsResponse:
    _ensure(principal, Capability.READ_OWN_WORK)
    requests = await service.list_inbox(
        principal.tenant_id,
        principal.subject,
        statuses=(CrossPersonRequestStatus.OPEN, CrossPersonRequestStatus.ACKNOWLEDGED),
    )
    return CrossPersonRequestsResponse(
        requests=[CrossPersonRequestResponse.from_domain(request) for request in requests],
    )


@router.post(
    "/cross-person-requests/{request_id}/status",
    response_model=CrossPersonRequestResponse,
)
async def update_cross_person_request_status(
    request_id: str,
    request: CrossPersonRequestStatusUpdateRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[CrossPersonRequestService, Depends(get_cross_person_request_service)],
) -> CrossPersonRequestResponse:
    if not _can_update_cross_person_request(principal):
        raise HTTPException(
            status_code=403,
            detail=f"{principal.subject} is not authorized to update cross-person requests",
        )
    existing = await service.get(principal.tenant_id, request_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"cross-person request {request_id} not found")
    if not _can_update_cross_person_request(principal, existing):
        raise HTTPException(
            status_code=403,
            detail=(
                f"{principal.subject} is not authorized to update cross-person request {request_id}"
            ),
        )
    updated = await service.update_status(principal.tenant_id, request_id, request.status)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"cross-person request {request_id} not found")
    return CrossPersonRequestResponse.from_domain(updated)


@router.get("/projects/{project_id}/risks", response_model=ProjectRisksResponse)
async def project_risks(
    project_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[RiskService, Depends(get_risk_service)],
) -> ProjectRisksResponse:
    _ensure_aggregate(principal)
    try:
        findings = await service.project_risks(principal.tenant_id, project_id, as_of)
        drift = await service.project_drift(principal.tenant_id, project_id, as_of)
    except GraphNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProjectRisksResponse(
        project_id=project_id,
        as_of=as_of,
        risks=[RiskFindingResponse.from_domain(finding) for finding in findings],
        drift=[DriftFindingResponse.from_domain(finding) for finding in drift],
    )


@router.get("/portfolio/risks", response_model=PortfolioRisksResponse)
async def portfolio_risks(
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[RiskService, Depends(get_risk_service)],
) -> PortfolioRisksResponse:
    _ensure_aggregate(principal)
    findings = await service.portfolio_risks(principal.tenant_id, as_of)
    drift = await service.portfolio_drift(principal.tenant_id, as_of)
    return PortfolioRisksResponse(
        as_of=as_of,
        risks=[RiskFindingResponse.from_domain(finding) for finding in findings],
        drift=[DriftFindingResponse.from_domain(finding) for finding in drift],
    )


def _ensure(principal: Principal, capability: Capability) -> None:
    try:
        AuthorizationPolicy().ensure(principal, capability)
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _ensure_aggregate(principal: Principal) -> None:
    policy = AuthorizationPolicy()
    if policy.can(principal, Capability.READ_TEAM_AGGREGATE):
        return
    if policy.can(principal, Capability.READ_EXEC_AGGREGATE):
        return
    raise HTTPException(
        status_code=403, detail=f"{principal.subject} is not authorized for aggregate read"
    )


def _can_update_cross_person_request(
    principal: Principal,
    request: CrossPersonRequest | None = None,
) -> bool:
    policy = AuthorizationPolicy()
    if policy.can(principal, Capability.READ_TEAM_AGGREGATE):
        return True
    if policy.can(principal, Capability.READ_EXEC_AGGREGATE):
        return True
    if not policy.can(principal, Capability.READ_OWN_WORK):
        return False
    if request is None:
        return True
    return principal.subject in {request.requester_id, request.counterpart_id}
