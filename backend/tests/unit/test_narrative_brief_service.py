from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.application.narrative_brief_service import NarrativeBriefService
from core.application.persona_views import PersonaViewService
from core.application.portfolio_feed_service import PortfolioFeedService
from core.domain.brief import BriefKind
from core.domain.graph import EntityRef, FactEvent, NodeKind, Pod
from core.domain.llm import LlmResponse, TokenUsage
from core.domain.workflows import SyncScheduleConfig
from infra.persistence.in_memory_graph import InMemoryGraphStore
from infra.workflows.brief_generation import BriefGenerationInput, BriefGenerationResult
from infra.workflows.dispatch import sync_dispatch_for_schedule, sync_workflow_input
from tests.contract.fakes import FakeLlmProvider

AS_OF = datetime(2026, 1, 10, 17, 0, tzinfo=UTC)


def _store_with_activity() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    return store


async def _seed(store: InMemoryGraphStore) -> None:
    await store.upsert_node(Pod(tenant_id="demo", id="pod-1", name="Runtime Pod"))
    await store.append_fact(
        FactEvent(
            tenant_id="demo",
            source="work_item",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.WORK_ITEM, id="WI-1"),
            payload={"name": "Graph adapter", "from_state": "in_progress", "to_state": "done"},
            observed_at=AS_OF - timedelta(hours=2),
            correlation_id="corr-1",
        )
    )


def _service(store: InMemoryGraphStore, llm: FakeLlmProvider) -> NarrativeBriefService:
    return NarrativeBriefService(
        llm_provider=llm,
        feed_service=PortfolioFeedService(store),
        persona_view_service=PersonaViewService(
            graph_repository=store,
            status_repository=store,
            rollup_repository=store,
            time_series_repository=store,
        ),
        graph_repository=store,
        brief_repository=store,
        model="test-model",
    )


async def test_generate_produces_titled_sourced_brief_and_persists() -> None:
    store = _store_with_activity()
    await _seed(store)
    llm = FakeLlmProvider()
    service = _service(store, llm)

    brief = await service.generate("demo", BriefKind.DAILY_POD, "pod-1", as_of=AS_OF)

    assert brief.kind is BriefKind.DAILY_POD
    assert brief.title == "Daily pod summary: Runtime Pod"
    assert brief.body.strip()
    assert brief.generated_at == AS_OF
    assert "pod:pod-1" in brief.sources
    assert "work_item:WI-1" in brief.sources
    # The composing call carries the privacy-guarding system prompt.
    assert "never include raw" in (llm.requests[-1].system or "")

    stored = await store.latest_briefs("demo", BriefKind.DAILY_POD)
    assert stored == [brief]


async def test_generate_falls_back_to_deterministic_body_when_llm_empty() -> None:
    store = _store_with_activity()
    await _seed(store)
    llm = FakeLlmProvider(
        responses=[
            LlmResponse(
                tenant_id="demo",
                text="   ",
                model="test-model",
                usage=TokenUsage(
                    prompt_tokens=1,
                    completion_tokens=0,
                    total_tokens=1,
                    cost_usd=0.0,
                    latency_ms=1.0,
                ),
                trace_id="trace-empty",
            )
        ]
    )
    service = _service(store, llm)

    brief = await service.generate("demo", BriefKind.WEEKLY_PROJECT, "proj-x", as_of=AS_OF)

    assert brief.body.strip()
    assert "Weekly project update" in brief.body


async def test_exec_brief_uses_portfolio_scope() -> None:
    store = _store_with_activity()
    await _seed(store)
    service = _service(store, FakeLlmProvider())

    brief = await service.generate("demo", BriefKind.EXEC, "", as_of=AS_OF)

    assert brief.title == "Executive portfolio brief"
    assert brief.scope_id == ""


def test_brief_generation_dataclasses_are_json_native() -> None:
    payload = BriefGenerationInput(
        tenant_id="demo", kind="daily_pod", observed_at=AS_OF.isoformat()
    )
    result = BriefGenerationResult(tenant_id="demo", kind="daily_pod", briefs_generated=3)
    assert payload.kind == "daily_pod"
    assert isinstance(payload.observed_at, str)
    assert result.briefs_generated == 3


def test_dispatch_maps_brief_connector_to_brief_input() -> None:
    config = SyncScheduleConfig(
        schedule_id="openprogram-narrative-brief-daily",
        tenant_id="demo",
        connector="brief",
        scope="daily_pod",
        payload={"kind": "daily_pod"},
        cron="0 17 * * 1-5",
    )
    dispatch = sync_dispatch_for_schedule(config, AS_OF)
    workflow_input = sync_workflow_input(dispatch)
    assert isinstance(workflow_input, BriefGenerationInput)
    assert workflow_input.kind == "daily_pod"
    assert workflow_input.observed_at == AS_OF.isoformat()
