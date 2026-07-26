from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.application.narrative_brief_service import NarrativeBriefService
from core.application.persona_views import PersonaViewService
from core.application.portfolio_feed_service import PortfolioFeedService
from core.domain.brief import BriefKind
from core.domain.graph import GraphNode, NodeKind

if TYPE_CHECKING:
    from infra.registry import ServiceRegistry


@dataclass(frozen=True, kw_only=True)
class BriefGenerationInput:
    tenant_id: str
    kind: str
    observed_at: str | None = None


@dataclass(frozen=True, kw_only=True)
class BriefGenerationResult:
    tenant_id: str
    kind: str
    briefs_generated: int


async def run_brief_generation_activity(
    payload: BriefGenerationInput,
) -> BriefGenerationResult:
    """Generate + persist scheduled narrative briefs for one kind.

    Runs on a cron schedule (see infra/workflows/schedule.py). Resolves the
    target scopes for the requested kind (pods for daily_pod, projects for
    weekly_project, a single portfolio brief for exec) from the graph, then
    composes a descriptive brief per scope. Reads only graph/fact/rollup state --
    never raw check-in or DM/reply content -- and stores each brief.
    """
    registry = _service_registry()
    try:
        kind = BriefKind(payload.kind)
        observed_at = _timestamp(payload.observed_at)
        service = _brief_service(registry)
        scopes = await _target_scopes(registry, payload.tenant_id, kind)
        generated = 0
        for scope_id in scopes:
            await service.generate(payload.tenant_id, kind, scope_id, as_of=observed_at)
            generated += 1
        return BriefGenerationResult(
            tenant_id=payload.tenant_id,
            kind=kind.value,
            briefs_generated=generated,
        )
    finally:
        await registry.close()


async def _target_scopes(
    registry: ServiceRegistry,
    tenant_id: str,
    kind: BriefKind,
) -> list[str]:
    if kind is BriefKind.EXEC:
        return [""]
    node_kind = NodeKind.POD if kind is BriefKind.DAILY_POD else NodeKind.PROJECT
    nodes = await registry.graph_repository().list_nodes(tenant_id, node_kind)
    return [node.id for node in _sorted_nodes(nodes)]


def _sorted_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
    return sorted(nodes, key=lambda node: node.id)


def _brief_service(registry: ServiceRegistry) -> NarrativeBriefService:
    time_series_repository = registry.time_series_repository()
    return NarrativeBriefService(
        llm_provider=registry.llm_provider(),
        feed_service=PortfolioFeedService(time_series_repository),
        persona_view_service=PersonaViewService(
            graph_repository=registry.graph_repository(),
            status_repository=registry.status_repository(),
            rollup_repository=registry.rollup_repository(),
            time_series_repository=time_series_repository,
        ),
        graph_repository=registry.graph_repository(),
        brief_repository=registry.narrative_brief_repository(),
        model=registry.settings.default_llm_model,
    )


def _timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _service_registry() -> ServiceRegistry:
    from config.settings import get_settings
    from infra.registry import ServiceRegistry

    return ServiceRegistry(get_settings())
