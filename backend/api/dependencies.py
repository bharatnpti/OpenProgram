from __future__ import annotations

from typing import Annotated, cast

from fastapi import Header, Request

from config.settings import Settings
from core.application.graph_queries import GraphQueryService
from core.application.persona_views import PersonaViewService
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


def get_persona_view_service(request: Request) -> PersonaViewService:
    registry = get_registry(request)
    return PersonaViewService(
        graph_repository=registry.graph_repository(),
        status_repository=registry.status_repository(),
        rollup_repository=registry.rollup_repository(),
        time_series_repository=registry.time_series_repository(),
    )
