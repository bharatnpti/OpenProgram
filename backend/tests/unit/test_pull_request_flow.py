"""Flow through review, read from synced pull and merge requests (GET /portfolio/pr-flow).

One small tenant throughout: Commerce Program > Checkout project (repo
acme/checkout-api) and Web project (acme/storefront-web); Web Pod works on
Web, with Zoe and Noah in it. No workstreams anywhere: the flow needs none.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.pull_request_flow_service import (
    FlowScopeInvalid,
    PullRequestFlowService,
    PullRequestFlowView,
)
from core.application.sync_services import VcsReadSyncService
from core.domain.errors import GraphNotFound
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    JsonScalar,
    NodeKind,
    Pod,
    Program,
    Project,
    Task,
)
from core.domain.identity import IdentityLink
from core.domain.integrations import (
    PullRequest,
    PullRequestEvent,
    PullRequestEventKind,
    Repo,
    SyncCursor,
    UserRef,
)
from core.domain.review_flow import ReviewStage
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeVcsProvider

DAY = date(2026, 10, 7)
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def hours_ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _edge(parent: str, child: str) -> GraphEdge:
    return GraphEdge(
        tenant_id="demo", from_node_id=parent, to_node_id=child, kind=EdgeKind.CONTAINS
    )


async def _tenant() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    for node in (
        Program(tenant_id="demo", id="prog", name="Commerce Program"),
        Project(
            tenant_id="demo",
            id="checkout",
            name="Checkout",
            metadata={"github_repos": "acme/checkout-api"},
        ),
        Project(
            tenant_id="demo", id="web", name="Web", metadata={"github_repos": "acme/storefront-web"}
        ),
        Project(tenant_id="demo", id="empty", name="Empty"),
        Pod(tenant_id="demo", id="pod-web", name="Web Pod"),
        Developer(tenant_id="demo", id="U-zoe", name="Zoe Almeida"),
        Developer(tenant_id="demo", id="U-noah", name="Noah Weber"),
        Developer(tenant_id="demo", id="U-liam", name="Liam Chen"),
        Task(tenant_id="demo", id="CHK-3", name="Payment intent", metadata={"issue_type": "Bug"}),
        Task(tenant_id="demo", id="CHK-8", name="Payment form", metadata={"issue_type": "Story"}),
    ):
        await store.upsert_node(node)
    for parent, child in (
        ("prog", "checkout"),
        ("prog", "web"),
        ("web", "pod-web"),
        ("pod-web", "U-zoe"),
        ("pod-web", "U-noah"),
    ):
        await store.add_edge(_edge(parent, child))
    return store


async def _fact(
    store: InMemoryGraphStore,
    *,
    repo: str,
    number: str,
    title: str,
    member: str | None,
    observed_at: datetime,
    state: str = "merged",
    timeline: Mapping[str, datetime | None] | None = None,
    **extra: JsonScalar,
) -> None:
    payload: dict[str, JsonScalar] = {
        "repo": repo,
        "id": number,
        "title": title,
        "merged": state == "merged",
        "state": state,
        "draft": False,
        **extra,
    }
    if timeline is not None:
        payload["timeline_read"] = True
        payload.update({key: iso(value) for key, value in timeline.items()})
    entity = (
        EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id=member)
        if member is not None
        else EntityRef(tenant_id="demo", kind=NodeKind.REPO, id=repo)
    )
    await store.append_fact(
        FactEvent(
            tenant_id="demo",
            source="vcs_pull_request",
            entity_ref=entity,
            payload=payload,
            observed_at=observed_at,
            correlation_id=f"pr:{repo}:{number}:{observed_at.isoformat()}:{timeline is not None}",
        )
    )


def _merged_timeline(merged_hours_ago: float, waits: tuple[float, float, float, float]) -> dict:
    """Stage boundaries for a request merged ``merged_hours_ago``, stage hours ``waits``."""
    coding, awaiting, review, merge = waits
    merged = hours_ago(merged_hours_ago)
    approved = merged - timedelta(hours=merge)
    first_review = approved - timedelta(hours=review)
    ready = first_review - timedelta(hours=awaiting)
    return {
        "first_commit_at": ready - timedelta(hours=coding),
        "ready_at": ready,
        "first_review_at": first_review,
        "approved_at": approved,
        "last_review_at": approved,
        "merged_at": merged,
    }


async def _seeded() -> InMemoryGraphStore:
    store = await _tenant()
    # Checkout: two merged requests in the window, one long ago, one closed.
    await _fact(
        store,
        repo="acme/checkout-api",
        number="1",
        title="CHK-3 Payment intent API",
        member="U-liam",
        observed_at=hours_ago(20),
        timeline=_merged_timeline(20, (2, 16, 2, 1)),
        merged_at=iso(hours_ago(20)),
    )
    await _fact(
        store,
        repo="acme/checkout-api",
        number="2",
        title="docs: sandbox setup",
        member="U-liam",
        observed_at=hours_ago(40),
        timeline=_merged_timeline(40, (4, 30, 1, 0.5)),
        merged_at=iso(hours_ago(40)),
    )
    await _fact(
        store,
        repo="acme/checkout-api",
        number="3",
        title="feat: old work",
        member="U-liam",
        observed_at=hours_ago(24 * 45),
        merged_at=iso(hours_ago(24 * 45)),
    )
    await _fact(
        store,
        repo="acme/checkout-api",
        number="4",
        title="feat: abandoned",
        member="U-liam",
        observed_at=hours_ago(5),
        state="closed",
    )
    # Web: Zoe's open request awaiting review; a bot's dependency update merged
    # with no review; an old fact without a history for request 6.
    await _fact(
        store,
        repo="acme/storefront-web",
        number="5",
        title="CHK-8 Payment form validation UI",
        member="U-zoe",
        observed_at=hours_ago(30),
        state="open",
        timeline={"first_commit_at": hours_ago(50), "ready_at": hours_ago(30)},
        opened_at=iso(hours_ago(30)),
    )
    await _fact(
        store,
        repo="acme/storefront-web",
        number="6",
        title="Update dependency react to v19",
        member=None,
        observed_at=hours_ago(10),
        timeline={"first_commit_at": hours_ago(11), "ready_at": hours_ago(11)},
        merged_at=iso(hours_ago(10)),
        author_name="Renovate Bot",
        author="renovate-bot",
    )
    await _fact(
        store,
        repo="acme/storefront-web",
        number="7",
        title="Cart totals",
        member="U-noah",
        observed_at=hours_ago(8),
        merged_at=iso(hours_ago(8)),
    )
    return store


async def _flow(
    store: InMemoryGraphStore,
    *,
    as_of: date = DAY,
    days: int = 30,
    program_id: str | None = None,
    project_id: str | None = None,
    pod_id: str | None = None,
) -> PullRequestFlowView:
    return await PullRequestFlowService(store, store).flow(
        "demo",
        as_of,
        days=days,
        program_id=program_id,
        project_id=project_id,
        pod_id=pod_id,
        now=NOW,
    )


async def test_the_tenant_flow_times_each_stage_and_names_the_worst_jam() -> None:
    view = await _flow(await _seeded())

    assert (view.merged_count, view.open_count) == (4, 1)
    stages = {stage.stage: stage for stage in view.stages}
    assert stages[ReviewStage.CODING].measured_count == 3
    assert stages[ReviewStage.CODING].p50_hours == pytest.approx(2.0)
    assert stages[ReviewStage.AWAITING_REVIEW].p50_hours == pytest.approx(23.0)
    assert stages[ReviewStage.AWAITING_REVIEW].p75_hours == pytest.approx(26.5)
    # The bot's request skipped review: it is not a wait of zero.
    assert stages[ReviewStage.AWAITING_REVIEW].measured_count == 2
    assert stages[ReviewStage.AWAITING_REVIEW].open_count == 1
    assert (view.worst_jam_p50, view.worst_jam_p75) == (
        ReviewStage.AWAITING_REVIEW,
        ReviewStage.AWAITING_REVIEW,
    )
    assert (view.timed_merged_count, view.unreviewed_merged_count, view.untimed_count) == (3, 1, 1)
    assert view.notes == (
        "1 request was synced before review histories were read, so its stages are not "
        "known: counted, not timed. The next Git sync reads them.",
        "1 merged request had no review by anyone but the author: counted for coding only, "
        "not as waiting for review.",
    )


async def test_each_request_is_typed_and_listed_newest_first_with_open_ones_after() -> None:
    view = await _flow(await _seeded())

    rows = [
        (item.repo, item.number, item.state, item.request_type.value, item.type_source.value)
        for item in view.items
    ]
    assert rows == [
        ("acme/storefront-web", "7", "merged", "unclassified", "none"),
        ("acme/storefront-web", "6", "merged", "dependency_update", "author"),
        ("acme/checkout-api", "1", "merged", "bug_fix", "issue"),
        ("acme/checkout-api", "2", "merged", "documentation", "title"),
        ("acme/storefront-web", "5", "open", "feature", "issue"),
    ]
    open_item = view.items[-1]
    assert open_item.author_name == "Zoe Almeida"
    assert open_item.stage is ReviewStage.AWAITING_REVIEW
    assert open_item.stage_age_hours == pytest.approx(30.0)
    assert open_item.stage_hours[ReviewStage.CODING] == pytest.approx(20.0)
    bot = view.items[1]
    assert bot.author_name == "Renovate Bot"
    counts = {
        row.request_type.value: (row.merged_count, row.open_count) for row in view.type_counts
    }
    assert counts["bug_fix"] == (1, 0)
    assert counts["feature"] == (0, 1)
    assert counts["unclassified"] == (1, 0)
    assert sum(merged for merged, _ in counts.values()) == 4


async def test_a_project_reads_its_repositories_and_a_program_its_projects() -> None:
    store = await _seeded()

    checkout = await _flow(store, project_id="checkout")
    program = await _flow(store, program_id="prog")
    empty = await _flow(store, project_id="empty")

    assert checkout.scope.repos == ("acme/checkout-api",)
    assert {item.number for item in checkout.items} == {"1", "2"}
    assert program.scope.repos == ("acme/checkout-api", "acme/storefront-web")
    assert program.merged_count == 4
    assert empty.merged_count == 0
    assert empty.notes[0].startswith("This project lists no repositories")


async def test_a_pod_is_its_members_requests_in_its_projects_repositories() -> None:
    view = await _flow(await _seeded(), pod_id="pod-web")

    # Zoe's and Noah's; the bot's request in the same repository is nobody's in the pod.
    assert {item.number for item in view.items} == {"5", "7"}
    assert view.scope.member_count == 2
    assert view.scope.repos == ("acme/storefront-web",)


async def test_with_only_coding_timed_there_is_no_worst_jam() -> None:
    store = await _tenant()
    await _fact(
        store,
        repo="acme/checkout-api",
        number="8",
        title="chore: bump version",
        member="U-liam",
        observed_at=hours_ago(2),
        timeline={"first_commit_at": hours_ago(3), "ready_at": hours_ago(2.5)},
        merged_at=iso(hours_ago(2)),
    )

    view = await _flow(store)

    assert view.stages[0].p50_hours == pytest.approx(0.5)
    assert (view.worst_jam_p50, view.worst_jam_p75) == (None, None)


async def test_one_scope_at_a_time_and_an_unknown_one_is_not_found() -> None:
    store = await _seeded()

    with pytest.raises(FlowScopeInvalid):
        await _flow(store, project_id="checkout", pod_id="pod-web")
    with pytest.raises(GraphNotFound):
        await _flow(store, project_id="pod-web")


async def test_a_past_day_reads_the_facts_known_by_its_end_and_a_longer_window_more() -> None:
    store = await _seeded()

    past = await _flow(store, as_of=DAY - timedelta(days=1))
    longer = await _flow(store, days=90)

    # By the end of 6 October, 1 (16:00) and 2 had merged; 6 and 7 came on the 7th.
    assert {item.number for item in past.items} == {"1", "2", "5"}
    assert past.window_end == datetime(2026, 10, 7, tzinfo=UTC)
    assert longer.merged_count == 5


async def test_the_fact_with_the_history_wins_over_an_older_fact_for_the_same_update() -> None:
    store = await _tenant()
    when = hours_ago(3)
    await _fact(
        store,
        repo="acme/checkout-api",
        number="9",
        title="feat: x",
        member="U-liam",
        observed_at=when,
        timeline=_merged_timeline(3, (1, 1, 1, 0)),
        merged_at=iso(when),
    )
    await _fact(
        store,
        repo="acme/checkout-api",
        number="9",
        title="feat: x",
        member="U-liam",
        observed_at=when,
        merged_at=iso(when),
    )

    view = await _flow(store)

    assert [item.timed for item in view.items] == [True]


# ---- the sync keeps what the stages are read from -------------------------------------------


_HISTORY = (
    PullRequestEvent(kind=PullRequestEventKind.COMMIT, at=hours_ago(40)),
    PullRequestEvent(kind=PullRequestEventKind.COMMENT, at=hours_ago(12), actor="noah"),
    PullRequestEvent(kind=PullRequestEventKind.APPROVAL, at=hours_ago(6), actor="noah"),
)


def _request(events: tuple[PullRequestEvent, ...] | None = _HISTORY) -> PullRequest:
    return PullRequest(
        tenant_id="demo",
        id="7",
        title="CHK-8 Payment form",
        author=UserRef(tenant_id="demo", external_id="zalmeida", display_name="Zoe"),
        merged=True,
        metadata={"repo": "acme/storefront-web", "state": "merged", "draft": False},
        updated_at=hours_ago(1),
        opened_at=hours_ago(30),
        merged_at=hours_ago(2),
        labels=("type::feature",),
        events=events,
    )


async def _sync(store: InMemoryGraphStore, *requests: PullRequest) -> None:
    await store.upsert_identity_link(
        IdentityLink(tenant_id="demo", developer_id="U-zoe", vcs_username="zalmeida")
    )
    provider = FakeVcsProvider(
        repos=[Repo(tenant_id="demo", id="r1", name="acme/storefront-web")],
        pull_requests=list(requests),
    )
    service = VcsReadSyncService(
        vcs_provider=provider,
        graph_repository=store,
        time_series_repository=store,
        cursor_repository=store,
        identity_link_repository=store,
    )
    await service.sync_repo(tenant_id="demo", repo_name="acme/storefront-web", observed_at=NOW)


async def test_the_sync_keeps_merge_time_labels_and_the_stage_times_on_the_fact() -> None:
    store = await _tenant()

    await _sync(store, _request())

    facts = await store.list_facts(
        "demo", EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="U-zoe")
    )
    payload = facts[0].payload
    assert facts[0].correlation_id.endswith(":history")
    assert payload["merged_at"] == iso(hours_ago(2))
    assert payload["labels"] == '["type::feature"]'
    assert (payload["timeline_read"], payload["reviewer_count"]) == (True, 1)
    assert payload["first_commit_at"] == iso(hours_ago(40))
    assert payload["ready_at"] == iso(hours_ago(30))
    assert payload["first_review_at"] == iso(hours_ago(12))
    assert payload["approved_at"] == iso(hours_ago(6))
    view = await _flow(store, pod_id="pod-web")
    assert view.items[0].stage_hours == {
        ReviewStage.CODING: pytest.approx(10.0),
        ReviewStage.AWAITING_REVIEW: pytest.approx(18.0),
        ReviewStage.IN_REVIEW: pytest.approx(6.0),
        ReviewStage.AWAITING_MERGE: pytest.approx(4.0),
    }


async def test_a_request_without_a_history_keeps_its_old_fact_identity() -> None:
    store = await _tenant()

    await _sync(store, _request(events=None))

    facts = await store.list_facts(
        "demo", EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="U-zoe")
    )
    assert not facts[0].correlation_id.endswith(":history")
    assert "timeline_read" not in facts[0].payload


async def test_a_cursor_from_before_histories_re_reads_the_requests_once() -> None:
    store = await _tenant()
    asked: list[SyncCursor | None] = []

    class RecordingProvider(FakeVcsProvider):
        async def list_pull_requests(
            self, tenant_id: str, repo: str, cursor: SyncCursor | None = None
        ) -> list[PullRequest]:
            asked.append(cursor)
            return await super().list_pull_requests(tenant_id, repo, cursor)

    provider = RecordingProvider(
        repos=[Repo(tenant_id="demo", id="r1", name="acme/storefront-web")],
        pull_requests=[_request()],
    )
    service = VcsReadSyncService(
        vcs_provider=provider,
        graph_repository=store,
        time_series_repository=store,
        cursor_repository=store,
    )
    old = SyncCursor(updated_at=hours_ago(1), value=iso(hours_ago(1)))
    await store.record_cursor("demo", "vcs", "repo:acme/storefront-web", old)

    await service.sync_repo(tenant_id="demo", repo_name="acme/storefront-web", observed_at=NOW)
    await service.sync_repo(tenant_id="demo", repo_name="acme/storefront-web", observed_at=NOW)

    assert asked[0] is None
    assert asked[1] is not None and asked[1].updated_at == hours_ago(1)
    cursor = await store.get_cursor("demo", "vcs", "repo:acme/storefront-web")
    assert cursor.metadata["pull_request_history"] is True


# ---- the API ---------------------------------------------------------------------------------


def _seed_app(app: object) -> None:
    store = app.state.registry.graph_repository()

    async def seed() -> None:
        for node in (
            Project(
                tenant_id="demo",
                id="checkout",
                name="Checkout",
                metadata={"github_repos": "acme/checkout-api"},
            ),
            Developer(tenant_id="demo", id="U-liam", name="Liam Chen"),
        ):
            await store.upsert_node(node)
        merged = datetime.now(tz=UTC) - timedelta(hours=2)
        await store.append_fact(
            FactEvent(
                tenant_id="demo",
                source="vcs_pull_request",
                entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.DEVELOPER, id="U-liam"),
                payload={
                    "repo": "acme/checkout-api",
                    "id": "1",
                    "title": "fix: refunds",
                    "merged": True,
                    "state": "merged",
                    "merged_at": merged.isoformat(),
                    "timeline_read": True,
                    "first_commit_at": (merged - timedelta(hours=5)).isoformat(),
                    "ready_at": (merged - timedelta(hours=4)).isoformat(),
                    "first_review_at": (merged - timedelta(hours=2)).isoformat(),
                    "approved_at": (merged - timedelta(hours=1)).isoformat(),
                    "last_review_at": (merged - timedelta(hours=1)).isoformat(),
                    "reviewer_count": 1,
                },
                observed_at=merged,
                correlation_id="pr:api:1",
            )
        )

    asyncio.run(seed())


def test_the_endpoint_answers_for_a_scope_and_keeps_the_flow_read_rules(
    settings: Settings,
) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        _seed_app(app)
        tenant = client.get("/portfolio/pr-flow", params={"days": 30})
        project = client.get("/portfolio/pr-flow", params={"project_id": "checkout"})
        unknown = client.get("/portfolio/pr-flow", params={"pod_id": "nope"})
        both = client.get("/portfolio/pr-flow", params={"project_id": "checkout", "pod_id": "x"})
        too_long = client.get("/portfolio/pr-flow", params={"days": 400})
    by_role: dict[str, tuple[int, int]] = {}
    for role in ("dev", "sm", "po", "mgr", "exec"):
        as_role = create_app(settings=settings.model_copy(update={"dev_principal_roles": role}))
        with TestClient(as_role) as client:
            by_role[role] = (
                client.get("/portfolio/pr-flow").status_code,
                client.get("/portfolio/flow").status_code,
            )

    assert tenant.status_code == 200
    body = tenant.json()
    assert (body["merged_count"], body["window_days"], body["scope"]["kind"]) == (1, 30, "tenant")
    assert body["worst_jam"] == {"p50": "awaiting_review", "p75": "awaiting_review"}
    assert body["items"][0]["request_type"] == "bug_fix"
    assert body["items"][0]["author_name"] == "Liam Chen"
    assert body["items"][0]["stage_hours"] == {
        "coding": 1.0,
        "awaiting_review": 2.0,
        "in_review": 1.0,
        "awaiting_merge": 1.0,
    }
    assert [row["label"] for row in body["type_counts"]][:3] == [
        "Feature",
        "Dependency update",
        "Bug fix",
    ]
    assert project.json()["scope"] == {
        "kind": "project",
        "id": "checkout",
        "name": "Checkout",
        "repos": ["acme/checkout-api"],
        "member_count": None,
    }
    assert unknown.status_code == 404
    assert both.status_code == 422
    assert too_long.status_code == 422
    # Who may read it is who may read the work-item flow: everyone but a developer.
    assert by_role == {
        "dev": (403, 403),
        "sm": (200, 200),
        "po": (200, 200),
        "mgr": (200, 200),
        "exec": (200, 200),
    }
