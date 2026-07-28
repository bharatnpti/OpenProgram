from __future__ import annotations

from datetime import datetime, timedelta

from core.application.persona_views import HeatmapCellView, PersonaViewService
from core.application.portfolio_feed_service import PortfolioFeedItemView, PortfolioFeedService
from core.domain.brief import BriefKind, NarrativeBrief
from core.domain.errors import GraphNotFound
from core.domain.llm import LlmMessage, LlmRequest
from core.ports.llm import LlmProvider
from core.ports.repositories import GraphRepository, NarrativeBriefRepository

BRIEF_SYSTEM_PROMPT = (
    "You write concise, descriptive program-management briefs for leaders. "
    "Summarize ONLY the delivery facts, feed items, and status rollups provided. "
    "Do not invent details, do not add recommendations, and never include raw "
    "chat, DM, or reply content. Write two to four short sentences of plain prose."
)

# Per-kind lookback windows for the descriptive feed context.
_LOOKBACK_DAYS: dict[BriefKind, int] = {
    BriefKind.DAILY_POD: 1,
    BriefKind.WEEKLY_PROJECT: 7,
    BriefKind.EXEC: 7,
}

_MAX_FEED_ITEMS = 12
_MAX_SOURCES = 20


class NarrativeBriefService:
    def __init__(
        self,
        llm_provider: LlmProvider,
        feed_service: PortfolioFeedService,
        persona_view_service: PersonaViewService,
        graph_repository: GraphRepository,
        brief_repository: NarrativeBriefRepository,
        model: str,
    ) -> None:
        self._llm_provider = llm_provider
        self._feed_service = feed_service
        self._persona_view_service = persona_view_service
        self._graph_repository = graph_repository
        self._brief_repository = brief_repository
        self._model = model

    async def generate(
        self,
        tenant_id: str,
        kind: BriefKind,
        scope_id: str,
        *,
        as_of: datetime,
    ) -> NarrativeBrief:
        lookback = _LOOKBACK_DAYS.get(kind, 7)
        since = as_of - timedelta(days=lookback)
        feed = await self._feed_service.feed(tenant_id, since=since)
        items = feed.items[:_MAX_FEED_ITEMS]
        rollup_line = await self._rollup_context(tenant_id, kind, scope_id, as_of)
        scope_name = await self._scope_name(tenant_id, kind, scope_id)
        title = _title_for(kind, scope_name)
        context = _context_text(kind, scope_name, rollup_line, items)
        body = await self._compose_body(tenant_id, kind, scope_id, as_of, title, context)
        sources = _sources_for(kind, scope_id, items)
        brief = NarrativeBrief(
            tenant_id=tenant_id,
            kind=kind,
            scope_id=scope_id,
            title=title,
            body=body,
            generated_at=as_of,
            sources=sources,
        )
        await self._brief_repository.record_brief(brief)
        return brief

    async def _compose_body(
        self,
        tenant_id: str,
        kind: BriefKind,
        scope_id: str,
        as_of: datetime,
        title: str,
        context: str,
    ) -> str:
        fallback = _fallback_body(context)
        request = LlmRequest(
            tenant_id=tenant_id,
            prompt=f"{title}\n\n{context}",
            model=self._model,
            correlation_id=f"brief-{kind.value}-{scope_id or 'portfolio'}-{as_of.isoformat()}",
            system=BRIEF_SYSTEM_PROMPT,
            messages=(LlmMessage(role="user", content=context),),
            metadata={
                "agent": "narrative_brief_service",
                "purpose": "narrative_brief",
                "kind": kind.value,
            },
        )
        try:
            response = await self._llm_provider.complete(request)
        except Exception:
            return fallback
        return _safe_body(response.text, fallback)

    async def _rollup_context(
        self,
        tenant_id: str,
        kind: BriefKind,
        scope_id: str,
        as_of: datetime,
    ) -> str:
        as_of_date = as_of.date()
        try:
            if kind is BriefKind.DAILY_POD:
                view = await self._persona_view_service.pod_checkins(
                    tenant_id, scope_id, as_of_date
                )
                return (
                    f"Check-ins: {view.confirmed} confirmed, {view.partial} partial, "
                    f"{view.stale} stale, {view.missing} missing "
                    f"across {len(view.developers)} member(s)."
                )
            if kind is BriefKind.WEEKLY_PROJECT:
                progress = await self._persona_view_service.project_progress(
                    tenant_id, scope_id, as_of_date
                )
                return (
                    f"Status {progress.rag.value} ({progress.source.value}); "
                    f"{progress.percent_complete:.0f}% complete across "
                    f"{progress.total_tasks} task(s) "
                    f"({progress.green_tasks} green, {progress.amber_tasks} amber, "
                    f"{progress.red_tasks} red, {progress.unknown_tasks} unknown)."
                )
            heatmap = await self._persona_view_service.portfolio_heatmap(tenant_id, as_of_date)
            return _heatmap_context(heatmap.cells)
        except GraphNotFound:
            return "No rollup status is available yet."

    async def _scope_name(
        self,
        tenant_id: str,
        kind: BriefKind,
        scope_id: str,
    ) -> str:
        if kind is BriefKind.EXEC or not scope_id:
            return "portfolio"
        node = await self._graph_repository.get_node(tenant_id, scope_id)
        return node.name if node is not None else scope_id


def _heatmap_context(cells: tuple[HeatmapCellView, ...]) -> str:
    counts: dict[str, int] = {}
    for cell in cells:
        rag = cell.rag.value
        counts[rag] = counts.get(rag, 0) + 1
    if not counts:
        return "No portfolio status cells are available yet."
    summary = ", ".join(f"{count} {rag}" for rag, count in sorted(counts.items()))
    return f"Portfolio status across {len(cells)} cell(s): {summary}."


def _context_text(
    kind: BriefKind,
    scope_name: str,
    rollup_line: str,
    items: tuple[PortfolioFeedItemView, ...],
) -> str:
    label = {
        BriefKind.DAILY_POD: "Daily pod summary",
        BriefKind.WEEKLY_PROJECT: "Weekly project update",
        BriefKind.EXEC: "Executive brief",
    }[kind]
    lines = [f"{label} for {scope_name}.", rollup_line]
    if items:
        lines.append("Recent activity:")
        lines.extend(f"- {item.summary}" for item in items)
    else:
        lines.append("No recent activity in the window.")
    return "\n".join(lines)


def _title_for(kind: BriefKind, scope_name: str) -> str:
    if kind is BriefKind.DAILY_POD:
        return f"Daily pod summary: {scope_name}"
    if kind is BriefKind.WEEKLY_PROJECT:
        return f"Weekly project update: {scope_name}"
    return "Executive portfolio brief"


def _sources_for(
    kind: BriefKind,
    scope_id: str,
    items: tuple[PortfolioFeedItemView, ...],
) -> tuple[str, ...]:
    sources: list[str] = []
    seen: set[str] = set()

    def _add(ref: str) -> None:
        if ref and ref not in seen:
            seen.add(ref)
            sources.append(ref)

    if scope_id:
        scope_kind = "pod" if kind is BriefKind.DAILY_POD else "project"
        _add(f"{scope_kind}:{scope_id}")
    for item in items:
        _add(f"{item.entity_ref.kind.value}:{item.entity_ref.id}")
        if len(sources) >= _MAX_SOURCES:
            break
    return tuple(sources[:_MAX_SOURCES])


def _fallback_body(context: str) -> str:
    # Deterministic, privacy-safe summary used when the model output is empty or
    # unusable. Collapses the descriptive context into a single line.
    collapsed = " ".join(line.strip("- ").strip() for line in context.splitlines() if line.strip())
    return collapsed or "No activity to report for this period."


def _safe_body(text: str, fallback: str) -> str:
    stripped = text.strip()
    if not stripped:
        return fallback
    return stripped
