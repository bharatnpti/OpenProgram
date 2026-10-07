from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import (
    get_cross_person_request_service,
    get_current_principal,
    get_flow_metrics_service,
    get_narrative_brief_repository,
    get_person_names,
    get_persona_view_service,
    get_portfolio_feed_service,
    get_provider_names,
    get_pull_request_flow_service,
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
    PodRollupResponse,
    PodTasksResponse,
    PortfolioAttentionResponse,
    PortfolioFeedResponse,
    PortfolioFlowResponse,
    PortfolioHeatmapResponse,
    PortfolioRisksResponse,
    ProgramTreeResponse,
    ProjectProgressResponse,
    ProjectRisksResponse,
    PullRequestFlowResponse,
    RiskFindingResponse,
    WorkstreamFlowResponse,
    WorkstreamProgressResponse,
    WriteBackAdoptionResponse,
)
from core.application.authorization import AuthorizationPolicy, Capability
from core.application.cross_person_service import (
    CrossPersonRequestService,
    RequestStatusNotSettable,
)
from core.application.flow_metrics_service import FlowMetricsService
from core.application.person_names import PersonNames, person_name
from core.application.persona_views import PersonaViewService, ProviderNames
from core.application.portfolio_feed_service import PortfolioFeedService
from core.application.pull_request_flow_service import FlowScopeInvalid, PullRequestFlowService
from core.application.risk_service import RiskService
from core.application.writeback_service import WriteBackService
from core.domain.auth import Principal
from core.domain.brief import BriefKind
from core.domain.cross_person import CrossPersonRequest, CrossPersonRequestStatus
from core.domain.errors import AuthorizationDenied, GraphNotFound
from core.domain.graph import NodeKind
from core.domain.risk import RiskFinding
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
    try:
        view = await persona_service.pod_blockers(principal.tenant_id, pod_id, as_of)
    except GraphNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PodBlockersResponse.from_view(view)


@router.get("/pods/{pod_id}/checkins", response_model=PodCheckinsResponse)
async def pod_checkins(
    pod_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> PodCheckinsResponse:
    _ensure(principal, Capability.READ_POD_CHECKINS)
    try:
        view = await persona_service.pod_checkins(principal.tenant_id, pod_id, as_of)
    except GraphNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PodCheckinsResponse.from_view(view)


@router.get("/pods/{pod_id}/rollup", response_model=PodRollupResponse)
async def pod_rollup(
    pod_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> PodRollupResponse:
    # A pod's reasons name its open blockers and who has not checked in, so
    # they need both pod capabilities: the pair the pod panel already needs.
    _ensure(principal, Capability.READ_POD_BLOCKERS)
    _ensure(principal, Capability.READ_POD_CHECKINS)
    try:
        view = await persona_service.pod_rollup(principal.tenant_id, pod_id, as_of)
    except GraphNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PodRollupResponse.from_view(view)


@router.get("/pods/{pod_id}/tasks", response_model=PodTasksResponse)
async def pod_tasks(
    pod_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> PodTasksResponse:
    # Each task carries its members' status and the pod's blockers attributed
    # to it, so the list needs both pod capabilities: it never shows a role
    # what the pod's check-ins or blockers would refuse it.
    _ensure(principal, Capability.READ_POD_CHECKINS)
    _ensure(principal, Capability.READ_POD_BLOCKERS)
    try:
        view = await persona_service.pod_tasks(principal.tenant_id, pod_id, as_of)
    except GraphNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PodTasksResponse.from_view(view)


@router.get("/projects/{project_id}/progress", response_model=ProjectProgressResponse)
async def project_progress(
    project_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> ProjectProgressResponse:
    _ensure(principal, Capability.READ_PROJECT_PROGRESS)
    try:
        view = await persona_service.project_progress(principal.tenant_id, project_id, as_of)
    except GraphNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProjectProgressResponse.from_view(view)


@router.get("/workstreams/{workstream_id}/progress", response_model=WorkstreamProgressResponse)
async def workstream_progress(
    workstream_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
) -> WorkstreamProgressResponse:
    _ensure(principal, Capability.READ_PROJECT_PROGRESS)
    try:
        view = await persona_service.workstream_progress(principal.tenant_id, workstream_id, as_of)
    except GraphNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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
    names: Annotated[ProviderNames, Depends(get_provider_names)],
    program_root_id: Annotated[str | None, Query()] = None,
) -> PortfolioHeatmapResponse:
    _ensure(principal, Capability.READ_PORTFOLIO_HEATMAP)
    view = await persona_service.portfolio_heatmap(
        principal.tenant_id, as_of, program_root_id, names=names
    )
    return PortfolioHeatmapResponse.from_view(view)


@router.get("/portfolio/attention", response_model=PortfolioAttentionResponse)
async def portfolio_attention(
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    persona_service: Annotated[PersonaViewService, Depends(get_persona_view_service)],
    risk_service: Annotated[RiskService, Depends(get_risk_service)],
    names: Annotated[ProviderNames, Depends(get_provider_names)],
    program_root_id: Annotated[str | None, Query()] = None,
    tz: Annotated[
        str | None,
        Query(description="The reader's IANA time zone, for the times a sentence names."),
    ] = None,
) -> PortfolioAttentionResponse:
    """Exec Today's headline, its next drivers and the top signals for one day.

    The heat map's read (the same colours and reasons) with the portfolio's
    risk and drift findings, so it needs both: manager, exec and admin hold them.
    """
    _ensure(principal, Capability.READ_PORTFOLIO_HEATMAP)
    _ensure_aggregate(principal)
    zone = _zone(tz)
    view = await persona_service.portfolio_attention(
        principal.tenant_id,
        as_of,
        program_root_id,
        risk_service,
        today=datetime.now(tz=zone).date(),
        zone=zone,
        names=names,
    )
    return PortfolioAttentionResponse.from_view(view)


def _zone(name: str | None) -> ZoneInfo:
    """The reader's zone, else UTC: a bad name never fails the read."""
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ZoneInfo("UTC")


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


@router.get("/portfolio/pr-flow", response_model=PullRequestFlowResponse)
async def portfolio_pull_request_flow(
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[PullRequestFlowService, Depends(get_pull_request_flow_service)],
    days: Annotated[int, Query(ge=1, le=180)] = 30,
    program_id: Annotated[str | None, Query()] = None,
    project_id: Annotated[str | None, Query()] = None,
    pod_id: Annotated[str | None, Query()] = None,
) -> PullRequestFlowResponse:
    """How long pull and merge requests spend coding, awaiting review, in review and
    awaiting merge, and what kind of work they are: merged ones in the ``days``
    before ``as_of`` give the stage times, open ones are counted where they stand.

    The whole tenant, or one of a program, a project or a pod. The same read
    permission as ``/portfolio/flow``.
    """
    _ensure_aggregate(principal)
    try:
        view = await service.flow(
            principal.tenant_id,
            as_of,
            days=days,
            program_id=program_id,
            project_id=project_id,
            pod_id=pod_id,
        )
    except FlowScopeInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GraphNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PullRequestFlowResponse.from_view(view)


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


class MyRequestRelation(StrEnum):
    """Which side of one's own cross-person requests to return."""

    WAITING = "waiting"
    RAISED = "raised"
    BOTH = "both"


@router.get("/portfolio/cross-person-requests", response_model=CrossPersonRequestsResponse)
async def portfolio_cross_person_requests(
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[CrossPersonRequestService, Depends(get_cross_person_request_service)],
    # No filter means every active request, matching `list_portfolio` itself.
    # Defaulting to OPEN quietly narrowed two callers that ask for all of them:
    # the three-column board, whose Acknowledged and Needs-resolution columns
    # could therefore never fill, and the older console's "All active" filter.
    status: Annotated[CrossPersonRequestStatus | None, Query()] = None,
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
    relation: Annotated[MyRequestRelation, Query()] = MyRequestRelation.WAITING,
) -> CrossPersonRequestsResponse:
    """One's own cross-person requests, as counterpart and/or as requester.

    ``waiting`` is the inbox and stays the default. ``raised`` answers "did my
    ask land?", which had no endpoint at all, and ``both`` serves a screen that
    shows the two together.
    """
    _ensure(principal, Capability.READ_OWN_WORK)
    active = (CrossPersonRequestStatus.OPEN, CrossPersonRequestStatus.ACKNOWLEDGED)
    # An ask nobody was matched to (needs_resolution) has no counterpart, so it
    # is in no inbox: it waits on its requester, who has to say who was meant.
    # Listing only the active statuses told a requester they had no asks of
    # others for exactly the one that waits on them.
    raised = (*active, CrossPersonRequestStatus.NEEDS_RESOLUTION)
    requests: list[CrossPersonRequest] = []
    if relation in {MyRequestRelation.WAITING, MyRequestRelation.BOTH}:
        requests.extend(
            await service.list_inbox(principal.tenant_id, principal.subject, statuses=active)
        )
    if relation in {MyRequestRelation.RAISED, MyRequestRelation.BOTH}:
        requests.extend(
            await service.list_raised(principal.tenant_id, principal.subject, statuses=raised)
        )
    # A request one raised on oneself would otherwise appear twice under `both`.
    unique = list({request.id: request for request in requests}.values())
    return CrossPersonRequestsResponse(
        requests=[CrossPersonRequestResponse.from_domain(request) for request in unique],
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
    """Acknowledge or resolve a cross-person request, as the signed-in member.

    Only the person the request asks acknowledges it, and only they or its
    requester resolve it, whatever else the caller's role reads (403). No other
    status is set by hand (422). The change is recorded with who made it.
    """
    _ensure(principal, Capability.READ_OWN_WORK)
    try:
        updated = await service.update_status(
            principal.tenant_id, request_id, request.status, actor=principal.subject
        )
    except AuthorizationDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RequestStatusNotSettable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail=f"cross-person request {request_id} not found")
    return CrossPersonRequestResponse.from_domain(updated)


@router.get("/projects/{project_id}/risks", response_model=ProjectRisksResponse)
async def project_risks(
    project_id: str,
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[RiskService, Depends(get_risk_service)],
    person_names: Annotated[PersonNames, Depends(get_person_names)],
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
        risks=await _risk_responses(principal.tenant_id, findings, person_names),
        drift=[DriftFindingResponse.from_domain(finding) for finding in drift],
    )


@router.get("/portfolio/risks", response_model=PortfolioRisksResponse)
async def portfolio_risks(
    as_of: Annotated[date, Query(default_factory=date.today)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[RiskService, Depends(get_risk_service)],
    person_names: Annotated[PersonNames, Depends(get_person_names)],
) -> PortfolioRisksResponse:
    _ensure_aggregate(principal)
    findings = await service.portfolio_risks(principal.tenant_id, as_of)
    drift = await service.portfolio_drift(principal.tenant_id, as_of)
    return PortfolioRisksResponse(
        as_of=as_of,
        risks=await _risk_responses(principal.tenant_id, findings, person_names),
        drift=[DriftFindingResponse.from_domain(finding) for finding in drift],
    )


async def _risk_responses(
    tenant_id: str, findings: list[RiskFinding], person_names: PersonNames
) -> list[RiskFindingResponse]:
    """Risk findings, each person they are about named rather than shown by chat id."""
    people = [finding.entity_ref.id for finding in findings if _is_person(finding)]
    names = await person_names.resolve(tenant_id, people)
    return [
        RiskFindingResponse.from_domain(
            finding,
            person_name=(
                person_name(names, finding.entity_ref.id, None) if _is_person(finding) else None
            ),
        )
        for finding in findings
    ]


def _is_person(finding: RiskFinding) -> bool:
    return finding.entity_ref.kind is NodeKind.DEVELOPER


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
