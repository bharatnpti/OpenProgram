"""The issue sync reads each changed issue's history as issue_change facts on the task."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.application.persona_views import _task_status_from_facts
from core.application.sync_services import (
    ISSUE_CHANGE_FACT_SOURCE,
    ISSUE_CHANGES_SINCE_KEY,
    ISSUE_CHANGES_THROUGH_KEY,
    ISSUE_CHANGES_WAITING_KEY,
    IssueReadSyncService,
)
from core.domain.graph import EntityRef, FactEvent, GraphNode, NodeKind
from core.domain.identity import IdentityLink
from core.domain.integrations import (
    Issue,
    IssueChange,
    IssueChangeField,
    IssueState,
    Project,
    SyncCursor,
    UserRef,
)
from core.domain.rollup import Rag
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeIssueTracker

TENANT = "demo"
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def _issue(key: str, updated_at: datetime, **metadata: object) -> Issue:
    return Issue(
        tenant_id=TENANT,
        key=key,
        title=f"Work on {key}",
        state=IssueState.IN_PROGRESS,
        metadata={"project_key": "CHK", **metadata},  # type: ignore[dict-item]
        updated_at=updated_at,
    )


def _change(
    history_id: str,
    at: datetime,
    field: IssueChangeField = IssueChangeField.STATUS,
    *,
    author: str | None = "acc-ana",
    from_value: str | None = "To Do",
    to_value: str | None = "In Progress",
) -> IssueChange:
    return IssueChange(
        history_id=history_id,
        author=UserRef(tenant_id=TENANT, external_id=author) if author else None,
        at=at,
        field=field,
        from_value=from_value,
        to_value=to_value,
    )


async def _store() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    await store.upsert_node(
        GraphNode(tenant_id=TENANT, id="U1001", kind=NodeKind.DEVELOPER, name="Ana")
    )
    await store.upsert_identity_link(
        IdentityLink(
            tenant_id=TENANT, developer_id="U1001", chat_user_id="U1001", jira_account_id="acc-ana"
        )
    )
    return store


def _service(
    store: InMemoryGraphStore, tracker: FakeIssueTracker, **kwargs: int
) -> IssueReadSyncService:
    return IssueReadSyncService(
        issue_tracker=tracker,
        graph_repository=store,
        time_series_repository=store,
        cursor_repository=store,
        identity_link_repository=store,
        **kwargs,  # type: ignore[arg-type]
    )


def _tracker(issues: list[Issue], changes: dict[str, list[IssueChange]]) -> FakeIssueTracker:
    return FakeIssueTracker(
        projects=[Project(tenant_id=TENANT, id="10000", key="CHK", name="Checkout")],
        issues={issue.key: issue for issue in issues},
        changes=changes,
    )


async def _changes_on(store: InMemoryGraphStore, key: str) -> list[FactEvent]:
    facts = await store.list_facts(TENANT, EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id=key))
    return [fact for fact in facts if fact.source == ISSUE_CHANGE_FACT_SOURCE]


async def _sync(service: IssueReadSyncService, at: datetime = NOW) -> None:
    await service.sync_project(tenant_id=TENANT, project_key="CHK", observed_at=at)


async def test_each_change_is_a_fact_on_the_task_with_its_author_mapped_to_a_member() -> None:
    store = await _store()
    at = NOW - timedelta(hours=2)
    tracker = _tracker(
        [_issue("CHK-4", NOW - timedelta(hours=1))],
        {
            "CHK-4": [
                _change("100", at),
                _change(
                    "101",
                    at + timedelta(minutes=5),
                    IssueChangeField.LABELS,
                    author="acc-outsider",
                    from_value=None,
                    to_value="CR",
                ),
                _change(
                    "102",
                    at + timedelta(minutes=10),
                    IssueChangeField.ASSIGNEE,
                    from_value="acc-outsider",
                    to_value="acc-ana",
                ),
                _change(
                    "103",
                    at + timedelta(minutes=15),
                    IssueChangeField.SUMMARY,
                    author=None,
                    from_value=None,
                    to_value=None,
                ),
            ]
        },
    )

    await _sync(_service(store, tracker))

    facts = {fact.payload["history_id"]: fact for fact in await _changes_on(store, "CHK-4")}
    status = facts["100"]
    assert status.payload == {
        "key": "CHK-4",
        "field": "status",
        "from": "To Do",
        "to": "In Progress",
        "project_key": "CHK",
        "history_id": "100",
        "author_member_id": "U1001",
    }
    assert status.observed_at == at
    assert status.correlation_id == "issue_change:demo:CHK-4:100:status"
    # An author who is no member keeps the tracker's id and becomes no person.
    assert facts["101"].payload["unlinked_author_id"] == "acc-outsider"
    assert "author_member_id" not in facts["101"].payload
    assert {node.id for node in await store.list_nodes(TENANT, NodeKind.DEVELOPER)} == {"U1001"}
    # An assignee change names the member it went to; the account it left is kept as is.
    assert facts["102"].payload["to_member_id"] == "U1001"
    assert "from_member_id" not in facts["102"].payload
    assert facts["103"].payload["from"] is None and facts["103"].payload["to"] is None
    assert "unlinked_author_id" not in facts["103"].payload
    # The facts are the work item's: nothing lands on the person.
    on_member = await store.list_facts(
        TENANT, EntityRef(tenant_id=TENANT, kind=NodeKind.DEVELOPER, id="U1001")
    )
    assert on_member == []


async def test_a_reread_history_is_one_fact_per_change() -> None:
    store = await _store()
    tracker = _tracker(
        [_issue("CHK-4", NOW - timedelta(hours=1))],
        {"CHK-4": [_change("100", NOW - timedelta(hours=2))]},
    )
    service = _service(store, tracker)

    await _sync(service)
    await store.record_cursor(TENANT, "issue", "project:CHK", SyncCursor())
    await _sync(service, NOW + timedelta(hours=1))

    assert len(await _changes_on(store, "CHK-4")) == 1


async def test_the_first_read_goes_back_the_backfill_window_only() -> None:
    store = await _store()
    tracker = _tracker(
        [
            _issue("CHK-1", NOW - timedelta(days=200)),
            _issue("CHK-2", NOW - timedelta(days=10)),
        ],
        {
            "CHK-1": [_change("1", NOW - timedelta(days=200))],
            "CHK-2": [
                _change("2", NOW - timedelta(days=150)),
                _change("3", NOW - timedelta(days=10)),
            ],
        },
    )

    await _sync(_service(store, tracker))

    assert [key for key, _since in tracker.change_reads] == ["CHK-2"]
    assert await _changes_on(store, "CHK-1") == []
    assert [fact.payload["history_id"] for fact in await _changes_on(store, "CHK-2")] == ["3"]
    cursor = await store.get_cursor(TENANT, "issue", "project:CHK")
    assert cursor.metadata[ISSUE_CHANGES_WAITING_KEY] == 0
    assert cursor.metadata[ISSUE_CHANGES_THROUGH_KEY] == cursor.metadata[ISSUE_CHANGES_SINCE_KEY]


async def test_a_target_synced_before_histories_were_read_backfills_by_a_search() -> None:
    store = await _store()
    recent = NOW - timedelta(days=3)
    tracker = _tracker(
        [_issue("CHK-1", recent)],
        {"CHK-1": [_change("1", recent)]},
    )
    # The sync ran before: its cursor is past CHK-1, with no history read yet.
    await store.record_cursor(
        TENANT, "issue", "project:CHK", SyncCursor(updated_at=NOW - timedelta(days=1))
    )

    await _sync(_service(store, tracker))

    assert tracker.searches[-1].updated_at == NOW - timedelta(days=120)
    assert [fact.payload["history_id"] for fact in await _changes_on(store, "CHK-1")] == ["1"]


async def test_at_most_the_budget_is_read_a_tick_and_no_change_is_lost_waiting() -> None:
    store = await _store()
    base = NOW - timedelta(days=5)
    issues = [_issue(f"CHK-{n}", base + timedelta(hours=n)) for n in range(1, 6)]
    tracker = _tracker(
        issues,
        {
            # CHK-5 changed before CHK-1 to CHK-2 were read, and again later.
            "CHK-5": [
                _change("50", base + timedelta(minutes=30)),
                _change("51", base + timedelta(hours=5)),
            ],
            **{f"CHK-{n}": [_change(str(n), base + timedelta(hours=n))] for n in range(1, 5)},
        },
    )
    service = _service(store, tracker, changes_per_tick=2)

    await _sync(service)
    first = await store.get_cursor(TENANT, "issue", "project:CHK")
    await _sync(service, NOW + timedelta(hours=1))
    second = await store.get_cursor(TENANT, "issue", "project:CHK")
    await _sync(service, NOW + timedelta(hours=2))
    third = await store.get_cursor(TENANT, "issue", "project:CHK")
    reads_before = len(tracker.change_reads)
    searches_before = len(tracker.searches)
    await _sync(service, NOW + timedelta(hours=3))

    assert [key for key, _since in tracker.change_reads] == [
        "CHK-1",
        "CHK-2",
        "CHK-3",
        "CHK-4",
        "CHK-5",
    ]
    assert first.metadata[ISSUE_CHANGES_WAITING_KEY] == 3
    assert second.metadata[ISSUE_CHANGES_WAITING_KEY] == 1
    assert third.metadata[ISSUE_CHANGES_WAITING_KEY] == 0
    assert first.metadata[ISSUE_CHANGES_THROUGH_KEY] == (base + timedelta(hours=2)).isoformat()
    # Until the backlog is read, every history is read from where it began.
    backfill_start = NOW - timedelta(days=120)
    assert first.metadata[ISSUE_CHANGES_SINCE_KEY] == backfill_start.isoformat()
    assert second.metadata[ISSUE_CHANGES_SINCE_KEY] == backfill_start.isoformat()
    assert third.metadata[ISSUE_CHANGES_SINCE_KEY] == (base + timedelta(hours=5)).isoformat()
    assert {fact.payload["history_id"] for fact in await _changes_on(store, "CHK-5")} == {
        "50",
        "51",
    }
    # Caught up and nothing new: no search, no history read.
    assert len(tracker.change_reads) == reads_before
    assert len(tracker.searches) == searches_before + 1


async def test_issues_updated_at_the_same_moment_are_read_in_the_same_tick() -> None:
    store = await _store()
    same = NOW - timedelta(days=1)
    tracker = _tracker([_issue("CHK-1", same), _issue("CHK-2", same)], {})

    await _sync(_service(store, tracker, changes_per_tick=1))

    assert sorted(key for key, _since in tracker.change_reads) == ["CHK-1", "CHK-2"]
    cursor = await store.get_cursor(TENANT, "issue", "project:CHK")
    assert cursor.metadata[ISSUE_CHANGES_WAITING_KEY] == 0


async def test_a_history_that_cannot_be_read_never_fails_the_sync() -> None:
    store = await _store()
    base = NOW - timedelta(days=2)
    tracker = _tracker(
        [_issue("CHK-1", base), _issue("CHK-2", base + timedelta(hours=1))],
        {
            "CHK-1": [_change("1", base)],
            "CHK-2": [_change("2", base + timedelta(hours=1))],
        },
    )
    tracker.failing_changes.add("CHK-2")
    service = _service(store, tracker)

    result = await service.sync_project(tenant_id=TENANT, project_key="CHK", observed_at=NOW)

    cursor = await store.get_cursor(TENANT, "issue", "project:CHK")
    assert result.items_synced == 2
    assert cursor.updated_at == base + timedelta(hours=1)
    assert "last_failed_at" not in cursor.metadata
    assert cursor.metadata[ISSUE_CHANGES_THROUGH_KEY] == base.isoformat()
    assert cursor.metadata[ISSUE_CHANGES_WAITING_KEY] == 1

    tracker.failing_changes.clear()
    await _sync(service, NOW + timedelta(hours=1))

    assert [fact.payload["history_id"] for fact in await _changes_on(store, "CHK-2")] == ["2"]


async def test_a_budget_of_nothing_reads_no_history() -> None:
    store = await _store()
    tracker = _tracker(
        [_issue("CHK-1", NOW - timedelta(hours=1))],
        {"CHK-1": [_change("1", NOW - timedelta(hours=1))]},
    )

    await _sync(_service(store, tracker, changes_per_tick=0))

    assert tracker.change_reads == []
    cursor = await store.get_cursor(TENANT, "issue", "project:CHK")
    assert ISSUE_CHANGES_THROUGH_KEY not in cursor.metadata


async def test_a_query_target_reads_the_history_of_what_its_query_returns() -> None:
    store = await _store()
    tracker = _tracker(
        [_issue("CHK-1", NOW - timedelta(hours=1))],
        {"CHK-1": [_change("1", NOW - timedelta(hours=1))]},
    )

    await _service(store, tracker).sync_query(
        tenant_id=TENANT,
        jql='project = "CHK"',
        target_node_id="pod-checkout",
        target_node_kind=NodeKind.POD,
        cursor_scope="query:pod:pod-checkout:hash",
        observed_at=NOW,
    )

    (fact,) = await _changes_on(store, "CHK-1")
    assert fact.payload["project_key"] == "CHK"
    cursor = await store.get_cursor(TENANT, "issue", "query:pod:pod-checkout:hash")
    assert cursor.metadata[ISSUE_CHANGES_WAITING_KEY] == 0


async def test_the_task_names_its_reporter_and_creator_and_the_issue_fact_keeps_dates() -> None:
    store = await _store()
    issue = _issue(
        "CHK-4",
        NOW - timedelta(hours=1),
        reporter_id="acc-ana",
        creator_id="acc-outsider",
        due_date="2026-10-20",
        fix_versions="R1, R2",
        labels="CR, checkout",
        components="Payments",
        severity="S2",
    )

    await _sync(_service(store, _tracker([issue], {})))

    node = await store.get_node(TENANT, "CHK-4")
    assert node is not None
    assert node.metadata["reporter_member_id"] == "U1001"
    assert "creator_member_id" not in node.metadata
    assert node.metadata["creator_id"] == "acc-outsider"
    assert node.metadata["components"] == "Payments"
    assert node.metadata["severity"] == "S2"
    facts = await store.list_facts(
        TENANT, EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-4")
    )
    (issue_fact,) = [fact for fact in facts if fact.source == "issue"]
    assert issue_fact.payload["due_date"] == "2026-10-20"
    assert issue_fact.payload["fix_versions"] == "R1, R2"
    assert issue_fact.payload["labels"] == "CR, checkout"


def test_a_field_change_never_colours_the_task() -> None:
    """The issue fact of an update carries the state; its field changes are no colour."""
    node = GraphNode(
        tenant_id=TENANT,
        id="CHK-4",
        kind=NodeKind.TASK,
        name="Guest checkout",
        metadata={"key": "CHK-4", "state": "in_progress", "status": "In Progress"},
    )
    ref = EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-4")
    done = FactEvent(
        tenant_id=TENANT,
        source="issue",
        entity_ref=ref,
        payload={"key": "CHK-4", "state": "done", "status": "Done"},
        observed_at=NOW - timedelta(hours=1),
        correlation_id="issue:demo:CHK-4:1",
    )
    change = FactEvent(
        tenant_id=TENANT,
        source=ISSUE_CHANGE_FACT_SOURCE,
        entity_ref=ref,
        payload={"key": "CHK-4", "field": "labels", "from": None, "to": "CR"},
        observed_at=NOW,
        correlation_id="issue_change:demo:CHK-4:2:labels",
    )

    status = _task_status_from_facts(node, [done, change], NOW.date())

    assert status.rag is Rag.GREEN
