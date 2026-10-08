from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Annotated, cast

from fastapi import Header, HTTPException, Request, status

from config.settings import Settings
from core.application.ask_service import AskService
from core.application.blocker_resolution import BlockerResolutionService
from core.application.branding_service import BrandingService
from core.application.config_service import ConfigService, DirectoryService
from core.application.connection_service import ConnectionService
from core.application.cross_person_service import CrossPersonRequestService
from core.application.day_report_service import DayReportService
from core.application.dead_letter_service import DeadLetterService
from core.application.delivery_service import DeliveryService
from core.application.directory_sync_service import DirectorySyncService
from core.application.escalation_matrix_service import EscalationMatrixService
from core.application.flow_metrics_service import FlowMetricsService
from core.application.forecast_service import ForecastService
from core.application.gate_service import GateService
from core.application.graph_queries import GraphQueryService
from core.application.person_names import PersonNames
from core.application.persona_views import PersonaViewService, ProviderNames
from core.application.portfolio_feed_service import PortfolioFeedService
from core.application.pull_request_flow_service import PullRequestFlowService
from core.application.risk_service import RiskService
from core.application.self_status_service import SelfStatusService
from core.application.sync_status_service import SyncStatusService
from core.application.task_update_service import TaskUpdateService
from core.application.writeback_service import WriteBackService
from core.domain.auth import Principal, Role
from core.domain.errors import (
    AuthenticationRequired,
    ProviderConfigurationError,
    ProviderUnavailable,
)
from core.domain.risk import RiskProviderConfig
from core.ports.auth import AuthCredentials
from core.ports.repositories import NarrativeBriefRepository
from infra.registry import ServiceRegistry


def get_settings_from_request(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_registry(request: Request) -> ServiceRegistry:
    return cast(ServiceRegistry, request.app.state.registry)


DEV_USER_HEADER = "x-openprogram-dev-user"
DEV_ROLES_HEADER = "x-openprogram-dev-roles"


def credentials_from_request(request: Request, authorization: str | None) -> AuthCredentials:
    settings = get_settings_from_request(request)
    return AuthCredentials(
        authorization=authorization,
        session_id=request.cookies.get(settings.auth_cookie_name),
        impersonate_subject=_impersonated_subject(request, settings),
        impersonate_roles=_impersonated_roles(request, settings),
    )


def _dev_switching_allowed(settings: Settings) -> bool:
    """Persona switching is an opt-in local-demo affordance, not an auth bypass.

    Requires ``demo_mode``, which settings refuse outside a local dev-auth
    tenant. Off by default, so the headers are inert unless someone turned the
    demo on deliberately.
    """
    return settings.demo_mode_available


def _impersonated_subject(request: Request, settings: Settings) -> str | None:
    if not _dev_switching_allowed(settings):
        return None
    value = (request.headers.get(DEV_USER_HEADER) or "").strip()
    return value or None


def _impersonated_roles(request: Request, settings: Settings) -> frozenset[Role] | None:
    if not _dev_switching_allowed(settings):
        return None
    raw = (request.headers.get(DEV_ROLES_HEADER) or "").strip()
    if not raw:
        return None
    roles: set[Role] = set()
    for item in raw.split(","):
        candidate = item.strip().lower()
        if not candidate:
            continue
        try:
            roles.add(Role(candidate))
        except ValueError:
            # An unknown role name falls back to the configured dev roles rather
            # than 400-ing a demo click.
            continue
    return frozenset(roles) or None


async def get_current_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    registry = get_registry(request)
    settings = get_settings_from_request(request)
    credentials = credentials_from_request(request, authorization)
    try:
        return await registry.current_principal(credentials).get()
    except AuthenticationRequired as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "message": str(exc),
                "login_url": auth_login_url_for_request(request, settings),
            },
        ) from exc


def auth_login_url_for_request(request: Request, settings: Settings) -> str:
    return_url = request.headers.get("referer") or "/"
    return settings.auth_login_url(return_url)


def get_graph_query_service(request: Request) -> GraphQueryService:
    registry = get_registry(request)
    return GraphQueryService(
        graph_repository=registry.graph_repository(),
        time_series_repository=registry.time_series_repository(),
    )


def get_config_service(request: Request) -> ConfigService:
    return config_service_for(get_registry(request))


def config_service_for(
    registry: ServiceRegistry, *, today: Callable[[], date] = date.today
) -> ConfigService:
    """The config service over the registry's stores; ``today`` dates the links it changes."""
    return ConfigService(
        graph_repository=registry.graph_repository(),
        status_repository=registry.status_repository(),
        directory_repository=registry.directory_user_repository(),
        time_series_repository=registry.time_series_repository(),
        identity_link_repository=registry.identity_link_repository(),
        writeback_config_repository=registry.writeback_config_repository(),
        issue_tracker=registry.issue_tracker(),
        require_issue_tracker_link=registry.settings.issue_tracker_provider != "fake",
        today=today,
    )


def get_flow_metrics_service(request: Request) -> FlowMetricsService:
    registry = get_registry(request)
    return FlowMetricsService(
        graph_repository=registry.graph_repository(),
        time_series_repository=registry.time_series_repository(),
    )


def get_pull_request_flow_service(request: Request) -> PullRequestFlowService:
    registry = get_registry(request)
    return PullRequestFlowService(
        graph_repository=registry.graph_repository(),
        time_series_repository=registry.time_series_repository(),
    )


def get_person_names(request: Request) -> PersonNames:
    registry = get_registry(request)
    return PersonNames(
        graph_repository=registry.graph_repository(),
        directory_repository=registry.directory_user_repository(),
    )


def get_portfolio_feed_service(request: Request) -> PortfolioFeedService:
    registry = get_registry(request)
    return PortfolioFeedService(
        registry.time_series_repository(), person_names=get_person_names(request)
    )


def get_narrative_brief_repository(request: Request) -> NarrativeBriefRepository:
    registry = get_registry(request)
    return registry.narrative_brief_repository()


def get_dead_letter_service(request: Request) -> DeadLetterService:
    registry = get_registry(request)
    return registry.dead_letter_service()


def get_sync_status_service(request: Request) -> SyncStatusService:
    registry = get_registry(request)
    return registry.sync_status_service()


def get_write_back_service(request: Request) -> WriteBackService:
    registry = get_registry(request)
    return registry.write_back_service()


def get_cross_person_request_service(request: Request) -> CrossPersonRequestService:
    registry = get_registry(request)
    return registry.cross_person_request_service()


def get_risk_service(request: Request) -> RiskService:
    registry = get_registry(request)
    settings = get_settings_from_request(request)
    return RiskService(
        graph_repository=registry.graph_repository(),
        time_series_repository=registry.time_series_repository(),
        status_repository=registry.status_repository(),
        blocker_resolution=BlockerResolutionService(
            registry.graph_repository(), registry.status_repository()
        ),
        rollup_repository=registry.rollup_repository(),
        provider_config=RiskProviderConfig(
            jira_base_url=settings.jira_base_url,
            github_base_url=settings.github_base_url,
            default_no_pr_days=settings.risk_default_no_pr_days,
            default_pr_age_days=settings.risk_default_pr_age_days,
            default_stale_days=settings.risk_default_stale_days,
            default_no_activity_days=settings.drift_no_activity_days,
        ),
    )


def get_ask_service(request: Request) -> AskService:
    registry = get_registry(request)
    settings = get_settings_from_request(request)
    return AskService(
        llm_provider=registry.llm_provider(),
        graph_repository=registry.graph_repository(),
        time_series_repository=registry.time_series_repository(),
        flow_metrics_service=get_flow_metrics_service(request),
        persona_view_service=get_persona_view_service(request),
        risk_service=get_risk_service(request),
        model=settings.default_llm_model,
    )


def get_directory_service(request: Request) -> DirectoryService:
    registry = get_registry(request)
    return DirectoryService(
        graph_repository=registry.graph_repository(),
        rollup_repository=registry.rollup_repository(),
        identity_link_repository=registry.identity_link_repository(),
    )


def get_directory_sync_service(request: Request) -> DirectorySyncService:
    registry = get_registry(request)
    try:
        return registry.directory_sync_service()
    except ProviderUnavailable as exc:
        if isinstance(exc, ProviderConfigurationError):
            status_code = status.HTTP_424_FAILED_DEPENDENCY
        else:
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


def get_persona_view_service(request: Request) -> PersonaViewService:
    registry = get_registry(request)
    return PersonaViewService(
        graph_repository=registry.graph_repository(),
        status_repository=registry.status_repository(),
        rollup_repository=registry.rollup_repository(),
        time_series_repository=registry.time_series_repository(),
        # A day with no stored rollup is computed with the drift the stored
        # ones carry (N3), read by the Signals list's own reader.
        drift_signals=get_risk_service(request),
        identity_link_repository=registry.identity_link_repository(),
    )


def get_provider_names(request: Request) -> ProviderNames:
    """How a reason names the configured tracker and code host: "Jira", "GitLab"."""
    return get_registry(request).provider_names()


def get_self_status_service(request: Request) -> SelfStatusService:
    registry = get_registry(request)
    return registry.self_status_service()


def get_task_update_service(request: Request) -> TaskUpdateService:
    return get_registry(request).task_update_service()


def get_branding_service(request: Request) -> BrandingService:
    registry = get_registry(request)
    return BrandingService(repository=registry.tenant_logo_repository())


def get_connection_service(request: Request) -> ConnectionService:
    return get_registry(request).connection_service()


def get_delivery_service(request: Request) -> DeliveryService:
    return get_registry(request).delivery_service()


def get_day_report_service(request: Request) -> DayReportService:
    return get_registry(request).day_report_service()


def get_forecast_service(request: Request) -> ForecastService:
    return get_registry(request).forecast_service()


def get_gate_service(request: Request) -> GateService:
    return get_registry(request).gate_service()


def get_escalation_matrix_service(request: Request) -> EscalationMatrixService:
    return get_registry(request).escalation_matrix_service()
