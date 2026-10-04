from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.application.narrative_brief_service import NarrativeBriefService
from core.application.person_names import PersonNames
from core.application.persona_views import PersonaViewService
from core.application.portfolio_feed_service import PortfolioFeedService
from core.domain.brief import BriefKind
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    NodeKind,
    Pod,
    Program,
    Project,
    RepoNode,
    SprintNode,
    Task,
    WorkItem,
    Workstream,
)
from core.domain.llm import LlmResponse, TokenUsage
from core.domain.rollup import NodeStatus, Rag
from core.domain.status import DeveloperStatus, StatusSource
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
    # The pod holds the work item: a pod's brief reads its own facts only (N39).
    await store.upsert_node(WorkItem(tenant_id="demo", id="WI-1", name="Graph adapter"))
    await store.add_edge(
        GraphEdge(tenant_id="demo", from_node_id="pod-1", to_node_id="WI-1", kind=EdgeKind.CONTAINS)
    )
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


# --- N38 / N39: what a brief is written from, and what it may say ---------------

# 09:15 UTC on a Monday, as the scheduled briefs run: a blocker reported at
# 06:05 that cleared at 06:06, when the merge request it waited on merged.
BRIEF_AT = datetime(2026, 1, 12, 9, 15, tzinfo=UTC)
ADA, BEN, CY = "U0123ABCD", "U0123EFGH", "U0123IJKL"


def _reply(text: str) -> LlmResponse:
    return LlmResponse(
        tenant_id="demo",
        text=text,
        model="test-model",
        usage=TokenUsage(
            prompt_tokens=1, completion_tokens=1, total_tokens=2, cost_usd=0.0, latency_ms=1.0
        ),
        trace_id="trace-brief",
    )


def _contains(parent: str, child: str) -> GraphEdge:
    return GraphEdge(
        tenant_id="demo", from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
    )


def _assigned(person: str, task: str) -> GraphEdge:
    return GraphEdge(
        tenant_id="demo", from_node_id=person, to_node_id=task, kind=EdgeKind.ASSIGNED_TO
    )


def _issue(key: str, title: str, state: str, status: str) -> Task:
    return Task(
        tenant_id="demo",
        id=key,
        name=title,
        metadata={"key": key, "state": state, "status": status, "project_key": "SHOP"},
    )


def _fact(
    source: str, kind: NodeKind, entity_id: str, at: datetime, **payload: str | int | bool
) -> FactEvent:
    return FactEvent(
        tenant_id="demo",
        source=source,
        entity_ref=EntityRef(tenant_id="demo", kind=kind, id=entity_id),
        payload=payload,
        observed_at=at,
        correlation_id=f"{source}-{entity_id}-{at.isoformat()}",
    )


async def _seed_portfolio(store: InMemoryGraphStore) -> None:
    """Two pods of one project; Ben is in both, and works on a ticket of each.

    Web Pod holds SHOP-1 (done) and SHOP-2 (merged, ticket still In Progress);
    Payments Pod holds SHOP-3 (Ben's) and SHOP-4 (done). Repositories, the
    sprint and the workstream carry no status of their own.
    """
    nodes = [
        Program(tenant_id="demo", id="prog", name="Commerce Program"),
        Project(
            tenant_id="demo", id="proj-shop", name="Shop", metadata={"jira_project_key": "SHOP"}
        ),
        Pod(tenant_id="demo", id="pod-web", name="Web Pod"),
        Pod(tenant_id="demo", id="pod-pay", name="Payments Pod"),
        Developer(tenant_id="demo", id=ADA, name="Ada Lind"),
        Developer(tenant_id="demo", id=BEN, name="Ben Okafor"),
        Developer(tenant_id="demo", id=CY, name="Cy Morales"),
        RepoNode(tenant_id="demo", id="acme/web", name="acme/web"),
        RepoNode(tenant_id="demo", id="acme/pay", name="acme/pay"),
        SprintNode(tenant_id="demo", id="sprint-1", name="Shop Sprint 1"),
        Workstream(tenant_id="demo", id="ws-cart", name="Cart"),
        _issue("SHOP-1", "Checkout form", "done", "Done"),
        _issue("SHOP-2", "Upgrade HTTP client", "in_progress", "In Progress"),
        _issue("SHOP-3", "Refund flow", "in_progress", "In Progress"),
        _issue("SHOP-4", "Payment retries", "done", "Done"),
    ]
    for node in nodes:
        await store.upsert_node(node)
    edges = [
        _contains("prog", "proj-shop"),
        _contains("proj-shop", "pod-web"),
        _contains("proj-shop", "pod-pay"),
        _contains("proj-shop", "acme/web"),
        _contains("proj-shop", "acme/pay"),
        _contains("proj-shop", "sprint-1"),
        _contains("proj-shop", "ws-cart"),
        _contains("pod-web", ADA),
        _contains("pod-web", BEN),
        _contains("pod-pay", BEN),
        _contains("pod-pay", CY),
        _contains("pod-web", "acme/web"),
        _contains("pod-pay", "acme/pay"),
        _contains("pod-web", "SHOP-1"),
        _contains("pod-web", "SHOP-2"),
        _contains("pod-pay", "SHOP-3"),
        _contains("pod-pay", "SHOP-4"),
        _assigned(ADA, "SHOP-1"),
        _assigned(BEN, "SHOP-2"),
        _assigned(BEN, "SHOP-3"),
        _assigned(CY, "SHOP-4"),
    ]
    edges += [_contains("proj-shop", key) for key in ("SHOP-1", "SHOP-2", "SHOP-3", "SHOP-4")]
    for edge in edges:
        await store.add_edge(edge)

    at = BRIEF_AT.replace(hour=6, minute=5)
    facts = [
        _fact(
            "issue", NodeKind.TASK, "SHOP-1", at - timedelta(hours=3), key="SHOP-1", state="done"
        ),
        _fact(
            "issue", NodeKind.TASK, "SHOP-4", at - timedelta(hours=2), key="SHOP-4", state="done"
        ),
        _fact(
            "issue",
            NodeKind.TASK,
            "SHOP-3",
            at - timedelta(hours=1),
            key="SHOP-3",
            state="in_progress",
        ),
        _fact(
            "checkin",
            NodeKind.DEVELOPER,
            ADA,
            at,
            developer_name="Ada Lind",
            status_source="confirmed",
            blocker_count=1,
        ),
        _fact(
            "checkin",
            NodeKind.DEVELOPER,
            BEN,
            at + timedelta(seconds=20),
            developer_name="Ben Okafor",
            status_source="confirmed",
            blocker_count=0,
            eta_change_days=1,
        ),
        _fact(
            "checkin",
            NodeKind.DEVELOPER,
            CY,
            at + timedelta(seconds=40),
            developer_name="Cy Morales",
            status_source="confirmed",
            blocker_count=0,
        ),
        # Ben's merge ends Ada's wait at 06:06; his ticket stays In Progress.
        _fact(
            "vcs_pull_request",
            NodeKind.DEVELOPER,
            BEN,
            at + timedelta(minutes=1),
            repo="acme/web",
            id="7",
            title="SHOP-2 Upgrade HTTP client",
            merged=True,
            state="merged",
        ),
        _fact(
            "cross_person_request",
            NodeKind.DEVELOPER,
            BEN,
            at + timedelta(minutes=1),
            request_id="xreq-wait",
            transition="resolved",
            dependency_kind="waiting_on",
            reporter_id=ADA,
            reporter_name="Ada Lind",
            referenced_person_id=BEN,
            referenced_person_name="Ben Okafor",
            summary="Finish the HTTP client upgrade for SHOP-2",
        ),
        _fact(
            "vcs_pull_request",
            NodeKind.DEVELOPER,
            BEN,
            at + timedelta(minutes=2),
            repo="acme/pay",
            id="3",
            title="SHOP-3 Refund flow",
            merged=False,
            state="opened",
        ),
        # An older copy of Cy's ask, closed for its newest one: nothing moved.
        _fact(
            "cross_person_request",
            NodeKind.DEVELOPER,
            BEN,
            at - timedelta(hours=4),
            request_id="xreq-old",
            transition="opened",
            dependency_kind="needs_review",
            reporter_id=CY,
            reporter_name="Cy Morales",
            referenced_person_id=BEN,
            referenced_person_name="Ben Okafor",
            summary="Review of the refund flow scheduled",
        ),
        _fact(
            "cross_person_request",
            NodeKind.DEVELOPER,
            BEN,
            at + timedelta(hours=2),
            request_id="xreq-old",
            transition="superseded",
            dependency_kind="needs_review",
            reporter_id=CY,
            reporter_name="Cy Morales",
            referenced_person_id=BEN,
            referenced_person_name="Ben Okafor",
            summary="Review of the refund flow scheduled",
            superseded_by="xreq-new",
        ),
    ]
    for fact in facts:
        await store.append_fact(fact)
    for person in (ADA, BEN, CY):
        await store.record_developer_status(
            DeveloperStatus(
                tenant_id="demo",
                developer_id=person,
                as_of=BRIEF_AT.date(),
                source=StatusSource.CONFIRMED,
                blockers=(),
                summary="Checked in.",
                developer_confirmed=True,
            )
        )
    # Today's stored rollup: every status-bearing cell green; the repository,
    # sprint and workstream cells unknown, as they always are.
    cells = {
        NodeKind.PROGRAM: ["prog"],
        NodeKind.PROJECT: ["proj-shop"],
        NodeKind.POD: ["pod-web", "pod-pay"],
        NodeKind.DEVELOPER: [ADA, BEN, CY],
        NodeKind.REPO: ["acme/web", "acme/pay"],
        NodeKind.SPRINT: ["sprint-1"],
        NodeKind.WORKSTREAM: ["ws-cart"],
    }
    for kind, ids in cells.items():
        bearing = kind in {NodeKind.PROGRAM, NodeKind.PROJECT, NodeKind.POD, NodeKind.DEVELOPER}
        for node_id in ids:
            await store.record_node_status(
                NodeStatus(
                    entity_ref=EntityRef(tenant_id="demo", kind=kind, id=node_id),
                    rag=Rag.GREEN if bearing else Rag.UNKNOWN,
                    source=StatusSource.CONFIRMED if bearing else StatusSource.UNKNOWN,
                    factors=(),
                    as_of=BRIEF_AT.date(),
                )
            )


def _scoped_service(store: InMemoryGraphStore, llm: FakeLlmProvider) -> NarrativeBriefService:
    return NarrativeBriefService(
        llm_provider=llm,
        feed_service=PortfolioFeedService(store, person_names=PersonNames(graph_repository=store)),
        persona_view_service=PersonaViewService(
            graph_repository=store,
            status_repository=store,
            rollup_repository=store,
            time_series_repository=store,
        ),
        graph_repository=store,
        brief_repository=store,
        model="test-model",
        status_repository=store,
    )


async def test_exec_brief_context_counts_status_cells_and_states_tickets_and_blockers() -> None:
    """N38: the exec brief's facts, and the 09:15:25 brief's claims checked against them."""
    store = InMemoryGraphStore()
    await _seed_portfolio(store)
    llm = FakeLlmProvider(
        responses=[
            _reply(
                "The portfolio currently shows 7 green and 4 unknown status cells. "
                "Recent milestones include completion of the checkout form (SHOP-1) and "
                "successful upgrade of the HTTP client (SHOP-2). All recent team check-ins "
                "report no blockers, and a key dependency on the upgrade has been resolved. "
                "One team member has an ETA adjustment of +1 day."
            )
        ]
    )

    brief = await _scoped_service(store, llm).generate("demo", BriefKind.EXEC, "", as_of=BRIEF_AT)

    context = llm.requests[-1].messages[0].content
    # Only people, pods, projects and the program carry a status of their own.
    assert "Status of 3 people, 2 pods, 1 project and the program: 7 green." in context
    assert "unknown" not in context
    # A merged merge request on an open ticket is not a finished issue.
    assert "Issues done in the tracker: SHOP-1 Checkout form; SHOP-4 Payment retries." in context
    assert (
        "Merged, ticket still open in the tracker (not done): SHOP-2 (Upgrade HTTP client) is "
        "merged in merge request 7 in web, ticket still open (In Progress in the tracker)."
    ) in context
    # Ada's blocker is reported, cleared one minute later by Ben's merge.
    assert (
        "Blockers: Ada Lind's check-in at 06:05 UTC reported 1 blocker, cleared at 06:06 UTC. "
        "No blocker is open now."
    ) in context
    # The superseded copy of Cy's ask is bookkeeping, not an event.
    assert "superseded" not in context and "Review of the refund flow" not in context

    sentences = brief.body.split(". ")
    assert "Status of 3 people, 2 pods, 1 project and the program: 7 green" in sentences
    assert "4 unknown" not in brief.body
    assert not any("SHOP-2" in sentence and "complet" in sentence for sentence in sentences)
    assert "SHOP-2 (Upgrade HTTP client) is merged in merge request 7 in web" in brief.body
    assert "ticket still open (In Progress in the tracker)" in brief.body
    assert "SHOP-1 (Checkout form) is done in the tracker." in brief.body
    assert "report no blockers" not in brief.body
    assert "reported 1 blocker, cleared at 06:06 UTC" in brief.body
    assert "One team member has an ETA adjustment of +1 day." in brief.body


async def test_pod_brief_reads_only_its_own_members_and_issues() -> None:
    """N39: Web Pod's brief holds no Payments Pod issue, person or request."""
    store = InMemoryGraphStore()
    await _seed_portfolio(store)
    llm = FakeLlmProvider(
        responses=[
            _reply(
                "Both members confirmed check-ins. SHOP-1 is done in the tracker. "
                "SHOP-4 payment retries were completed. Cy Morales confirmed his check-in. "
                "Two cross-person reviews were superseded and rescheduled."
            )
        ]
    )

    brief = await _scoped_service(store, llm).generate(
        "demo", BriefKind.DAILY_POD, "pod-web", as_of=BRIEF_AT
    )

    context = llm.requests[-1].messages[0].content
    assert "Members: Ada Lind, Ben Okafor." in context
    assert "SHOP-1" in context and "SHOP-2" in context
    # Ben's Payments ticket and its merge request, Cy, his ask: all Payments Pod's.
    for foreign in ("SHOP-3", "SHOP-4", "acme/pay", "pay !3", "Cy Morales", "refund"):
        assert foreign not in context
    assert "Ada Lind's dependency request to Ben Okafor" in context
    assert "reported 1 blocker, cleared at 06:06 UTC" in context
    assert not {"task:SHOP-3", "task:SHOP-4"} & set(brief.sources)
    assert brief.sources[0] == "pod:pod-web"
    assert "task:SHOP-1" in brief.sources

    assert brief.body.startswith("Both members confirmed check-ins. SHOP-1 is done in the tracker.")
    for invented in ("SHOP-4", "Cy Morales", "rescheduled"):
        assert invented not in brief.body
    # The blocker the pod's check-ins reported is stated even though the model left it out.
    assert brief.body.endswith(
        "Ada Lind's check-in at 06:05 UTC reported 1 blocker, cleared at 06:06 UTC. "
        "No blocker is open now."
    )

    payments = await _scoped_service(store, FakeLlmProvider()).facts(
        "demo", BriefKind.DAILY_POD, "pod-pay", as_of=BRIEF_AT
    )
    assert {"SHOP-3", "SHOP-4"} <= payments.issue_keys
    assert not {"SHOP-1", "SHOP-2"} & payments.issue_keys
    assert "Ada Lind" not in payments.context
