from __future__ import annotations

from typing import Annotated, cast

from fastapi import Header, Request

from config.settings import Settings
from core.application.ask_service import AskService
from core.application.config_service import ConfigService, DirectoryService
from core.application.directory_sync_service import DirectorySyncService
from core.application.flow_metrics_service import FlowMetricsService
from core.application.graph_queries import GraphQueryService
from core.application.persona_views import PersonaViewService
from core.application.portfolio_feed_service import PortfolioFeedService
from core.domain.auth import Principal
from infra.registry import ServiceRegistry


def get_settings_from_request(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_registry(request: Request) -> ServiceRegistry:
    return cast(ServiceRegistry, request.app.state.registry)


async def get_current_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    registry = get_registry(request)
    return await registry.current_principal(authorization).get()


def get_graph_query_service(request: Request) -> GraphQueryService:
    registry = get_registry(request)
    return GraphQueryService(
        graph_repository=registry.graph_repository(),
        time_series_repository=registry.time_series_repository(),
    )


def get_config_service(request: Request) -> ConfigService:
    registry = get_registry(request)
    return ConfigService(
        graph_repository=registry.graph_repository(),
        status_repository=registry.status_repository(),
        directory_repository=registry.directory_user_repository(),
        time_series_repository=registry.time_series_repository(),
    )


def get_flow_metrics_service(request: Request) -> FlowMetricsService:
    registry = get_registry(request)
    return FlowMetricsService(
        graph_repository=registry.graph_repository(),
        time_series_repository=registry.time_series_repository(),
    )


def get_portfolio_feed_service(request: Request) -> PortfolioFeedService:
    registry = get_registry(request)
    return PortfolioFeedService(registry.time_series_repository())


def get_ask_service(request: Request) -> AskService:
    registry = get_registry(request)
    settings = get_settings_from_request(request)
    return AskService(
        llm_provider=registry.llm_provider(),
        graph_repository=registry.graph_repository(),
        time_series_repository=registry.time_series_repository(),
        flow_metrics_service=get_flow_metrics_service(request),
        persona_view_service=get_persona_view_service(request),
        model=settings.litellm_model,
    )


def get_directory_service(request: Request) -> DirectoryService:
    registry = get_registry(request)
    return DirectoryService(
        graph_repository=registry.graph_repository(),
        rollup_repository=registry.rollup_repository(),
    )


def get_directory_sync_service(request: Request) -> DirectorySyncService:
    registry = get_registry(request)
    return registry.directory_sync_service()


def get_persona_view_service(request: Request) -> PersonaViewService:
    registry = get_registry(request)
    return PersonaViewService(
        graph_repository=registry.graph_repository(),
        status_repository=registry.status_repository(),
        rollup_repository=registry.rollup_repository(),
        time_series_repository=registry.time_series_repository(),
    )
