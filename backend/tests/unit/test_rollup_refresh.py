"""N27: today's rollup is recorded again after a change made outside a check-in.

R4, 06:15: the hourly rollup stored its rows at 06:15:00.98 and the git sync's
merge pass resolved Zoe's CHK-11 wait at 06:15:04, so Storefront, Checkout and
the program read amber on a resolved blocker until the 07:15 rollup, and the
heat map, Ask and the digest all read it so.

The pass that changes a blocker now asks for today's rollup again; the requests
of one burst run once, and a refresh and the hourly rollup never interleave.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from core.application.blocker_resolution import BlockerResolutionService
from core.application.blocker_settlement import BlockerSettlement
from core.application.cross_person_service import CrossPersonRequestService
from core.application.rollup_service import RollupService
from core.domain.blockers import DeveloperBlocker
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestStatus,
)
from core.domain.directory import DirectoryUser
from core.domain.graph import EntityRef, FactEvent, NodeKind
from core.domain.rollup import Rag
from core.domain.status import DeveloperStatus
from infra.adapters.workflows import dbos as dbos_adapter
from infra.adapters.workflows import temporal as temporal_adapter
from infra.adapters.workflows.fake import FakeRollupRefresher
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from infra.persistence.postgres_status import PostgresRollupRepository
from infra.workflows import rollup as rollup_workflow
from infra.workflows.rollup import RollupInput, rollup_refresh_input, rollup_refresh_key
from tests.contract.fakes import FakeChatProvider
from tests.unit.qa2_checkout_slice import (
    DAY,
    OMAR,
    TENANT,
    ZOE,
    checkout_slice,
)

# R4: Asha merged platform-libs !1 (CHK-17) at 06:06:26; the 06:15 rollup ran at
# 06:15:00.98 and the merge pass at 06:15:04.
CHK8_MERGED_AT = datetime(2026, 10, 4, 0, 5, 39, tzinfo=UTC)
CHK17_MERGED_AT = datetime(2026, 10, 4, 6, 6, 26, tzinfo=UTC)
PASS_AT = datetime(2026, 10, 4, 6, 15, 4, tzinfo=UTC)
ASKED_AT = datetime(2026, 10, 3, 18, 4, tzinfo=UTC)
_PODS_AND_UP = ("pod-storefront", "pod-payments", "project-checkout", "program-platform")


async def test_two_merge_passes_at_once_ask_for_one_rollup_that_shows_the_resolution() -> None:
    store = _HoldBeforeRequestResolve.of(await checkout_slice())
    refresher = _CoalescingRefresher()
    service = await _service(store, refresher)
    await store.create(_to_omar())
    # 03:30: storefront-web !1 had merged and the pass cleared Zoe's CHK-8 wait.
    await _merge_request(store, "acme/storefront-web", "1", "CHK-8 Payment form", CHK8_MERGED_AT)
    await service.settle_merged_work(TENANT)
    refresher.reset()
    # storefront-web !2 for CHK-11 itself is still open, as in R4.
    await _merge_request(store, "acme/storefront-web", "2", "CHK-11 Cart price breakdown", None)
    # 06:15:01: the hourly rollup still counts the CHK-11 wait.
    await _record_rollup(store)
    before = await _stored_rags(store)
    assert {
        before[node] for node in ("pod-storefront", "project-checkout", "program-platform")
    } == {Rag.AMBER}
    await _merge_request(
        store, "acme/platform-libs", "1", "CHK-17 Upgrade shared HTTP client", CHK17_MERGED_AT
    )

    # 06:15:04: two of the six per-repository syncs run the merge pass at once.
    await asyncio.gather(service.settle_merged_work(TENANT), service.settle_merged_work(TENANT))

    assert store.arrived == 2  # both passes read the request open
    assert refresher.requests == [(TENANT, DAY)]
    assert refresher.runs_due() == [rollup_refresh_input(TENANT, DAY)]
    await refresher.run_due(lambda payload: _record_rollup(store, as_of=payload.as_of))
    after = await _stored_rags(store)
    assert after[ZOE] is Rag.GREEN
    assert {after[node] for node in _PODS_AND_UP} == {Rag.GREEN}
    assert await store.open_blockers(TENANT, ZOE, DAY) == []


async def test_a_pass_that_changes_nothing_asks_for_no_rollup() -> None:
    store = await checkout_slice()
    refresher = FakeRollupRefresher()
    service = await _service(store, refresher)
    await _merge_request(store, "acme/storefront-web", "1", "CHK-8 Payment form", CHK8_MERGED_AT)
    await service.settle_merged_work(TENANT)
    assert refresher.requests == [(TENANT, DAY)]

    await service.settle_merged_work(TENANT)
    await service.settle_merged_work(TENANT)

    assert refresher.requests == [(TENANT, DAY)]


async def test_a_console_resolve_that_clears_a_blocker_asks_for_the_rollup() -> None:
    store = await checkout_slice()
    refresher = FakeRollupRefresher()
    service = await _service(store, refresher)
    to_omar = await store.create(_to_omar())

    await service.update_status(TENANT, to_omar.id, CrossPersonRequestStatus.RESOLVED)

    assert [b.blocker_id for b in await store.open_blockers(TENANT, ZOE, DAY)] == ["blk-chk8"]
    assert refresher.requests == [(TENANT, DAY)]


async def test_a_failed_refresh_request_leaves_the_blocker_resolved() -> None:
    store = await checkout_slice()
    service = await _service(store, _FailingRefresher())
    await _merge_request(store, "acme/storefront-web", "1", "CHK-8 Payment form", CHK8_MERGED_AT)

    await service.settle_merged_work(TENANT)

    open_ids = [b.blocker_id for b in await store.open_blockers(TENANT, ZOE, DAY)]
    assert open_ids == ["blk-chk11"]


async def test_a_refresh_running_with_the_hourly_rollup_leaves_the_later_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hourly rollup reads the open blocker and is slow to write; the refresh waits."""
    store = _HoldAfterFirstStatusRead.of(await checkout_slice())
    registry = _Registry(store)
    monkeypatch.setattr(rollup_workflow, "_service_registry", lambda: registry)
    hourly = asyncio.create_task(rollup_workflow.run_rollup_activity(_hourly()))
    await asyncio.wait_for(store.read_once.wait(), timeout=1)
    # While the hourly rollup holds what it read, the merge pass clears both waits.
    for blocker in await store.open_blockers(TENANT, ZOE, DAY):
        await store.resolve_developer_blockers(
            TENANT,
            ZOE,
            (_resolved(blocker),),
            status_as_of=DAY,
            revise_status=_without_all,
        )
    refresh = asyncio.create_task(
        rollup_workflow.run_rollup_activity(rollup_refresh_input(TENANT, DAY))
    )
    await asyncio.sleep(0.05)
    assert not refresh.done()  # waiting for the hourly rollup's day

    store.release.set()
    await asyncio.gather(hourly, refresh)

    stored = await _stored_rags(store)
    assert {stored[node] for node in _PODS_AND_UP} == {Rag.GREEN}


async def test_postgres_holds_one_advisory_lock_per_tenant_and_day() -> None:
    executor = _TransactionRecorder()
    repository = PostgresRollupRepository(executor)  # type: ignore[arg-type]

    async with repository.exclusive_day("qa2", DAY):
        executor.calls.append(("-- body", ()))

    assert executor.transactions == 1
    assert executor.calls == [
        ("SET LOCAL lock_timeout = '120s'", ()),
        (
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ("openprogram:node_statuses:qa2:2026-10-04",),
        ),
        ("-- body", ()),
    ]


async def test_dbos_debounces_the_rollup_workflow_outside_the_callers_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dbos._context import DBOSContext, _dbos_context_var

    calls: list[tuple[str, float, object, object]] = []

    class _Debouncer:
        def __init__(self, workflow: object, timeout: float | None) -> None:
            self.workflow = workflow
            self.timeout = timeout

        @staticmethod
        def create_async(workflow: object, *, debounce_timeout_sec: float | None) -> _Debouncer:
            return _Debouncer(workflow, debounce_timeout_sec)

        def debounce(self, key: str, period: float, payload: RollupInput) -> None:
            from dbos._context import get_local_dbos_context

            calls.append((key, period, payload, get_local_dbos_context()))
            assert self.workflow is dbos_adapter.dbos_rollup_workflow
            assert self.timeout == rollup_workflow.ROLLUP_REFRESH_MAX_WAIT_SECONDS

    monkeypatch.setattr(dbos_adapter, "Debouncer", _Debouncer)
    monkeypatch.setattr(dbos_adapter, "_ensure_dbos_runtime", lambda config: False)
    refresher = dbos_adapter.DbosRollupRefresher(app_name="openprogram", system_database_url="x")
    # The caller is inside a DBOS step, as the merge pass of a sync is.
    step = DBOSContext()
    step.workflow_id = "sched-openprogram-runtime-vcs-sync-0-git-repo-acme-platform-libs"
    step.curr_step_function_id = 1
    token = _dbos_context_var.set(step)
    try:
        await refresher.refresh_rollup(TENANT, DAY)
        await refresher.refresh_rollup(TENANT, DAY)
    finally:
        _dbos_context_var.reset(token)

    key = rollup_refresh_key(TENANT, DAY)
    payload = RollupInput(tenant_id=TENANT, as_of="2026-10-04", backfill_days=0)
    assert calls == [(key, 20, payload, None), (key, 20, payload, None)]


async def test_temporal_starts_one_delayed_rollup_per_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from temporalio.exceptions import WorkflowAlreadyStartedError

    started: list[dict[str, Any]] = []

    class _Client:
        async def start_workflow(
            self, run: object, payload: RollupInput, **options: object
        ) -> None:
            if any(item["id"] == options["id"] for item in started):
                raise WorkflowAlreadyStartedError(options["id"], "RollupWorkflow")
            started.append({"run": run, "payload": payload, **options})

    async def connect(target: str) -> _Client:
        return _Client()

    monkeypatch.setattr(temporal_adapter, "_connect_temporal", connect)
    refresher = temporal_adapter.TemporalRollupRefresher(target="t", task_queue="q")

    await refresher.refresh_rollup(TENANT, DAY)
    await refresher.refresh_rollup(TENANT, DAY)

    assert len(started) <= 2  # a window may close between the two requests
    first = started[0]
    assert first["run"] == temporal_adapter.RollupWorkflow.run
    assert first["payload"] == rollup_refresh_input(TENANT, DAY)
    assert first["start_delay"] == timedelta(seconds=20)
    assert first["id"].startswith("rollup-refresh-demo-2026-10-04-")
    assert first["task_queue"] == "q"


def test_the_registry_gives_the_merge_pass_a_rollup_refresher() -> None:
    from config.settings import Settings
    from infra.registry import ServiceRegistry

    settings = Settings.model_validate(
        {
            "secret_key": "q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=",
            "runtime_mode": "memory",
            "chat_provider": "fake",
            "directory_provider": "fake",
            "issue_tracker_provider": "fake",
            "vcs_provider": "fake",
            "calendar_provider": "fake",
            "llm_provider": "fake",
            "workflow_provider": "fake",
        }
    )
    registry = ServiceRegistry(settings, graph_store=InMemoryGraphStore())
    settlement = registry.cross_person_request_service().blocker_settlement

    assert settlement is not None
    assert isinstance(settlement._rollup_refresher, FakeRollupRefresher)


# --- helpers --------------------------------------------------------------------


@dataclass
class _CoalescingRefresher:
    """Coalesces requests per tenant and day until run, as the debounce does."""

    requests: list[tuple[str, date]] = field(default_factory=list)
    _due: dict[str, RollupInput] = field(default_factory=dict)

    async def refresh_rollup(self, tenant_id: str, as_of: date) -> None:
        self.requests.append((tenant_id, as_of))
        self._due[rollup_refresh_key(tenant_id, as_of)] = rollup_refresh_input(tenant_id, as_of)

    def runs_due(self) -> list[RollupInput]:
        return list(self._due.values())

    async def run_due(self, run: Callable[[RollupInput], Awaitable[None]]) -> None:
        due, self._due = self._due, {}
        for payload in due.values():
            await run(payload)

    def reset(self) -> None:
        self.requests.clear()
        self._due.clear()


class _FailingRefresher:
    async def refresh_rollup(self, tenant_id: str, as_of: date) -> None:
        raise ConnectionError("system database unreachable")


class _HoldBeforeRequestResolve(InMemoryGraphStore):
    """Holds each request transition until two have arrived: both passes read it open."""

    def __init__(self, **fields: object) -> None:
        super().__init__(**fields)  # type: ignore[arg-type]
        self.arrived = 0
        self._both = asyncio.Event()

    @classmethod
    def of(cls, store: InMemoryGraphStore) -> _HoldBeforeRequestResolve:
        return cls(**{name: getattr(store, name) for name in store.__dataclass_fields__})

    async def update_status(
        self,
        tenant_id: str,
        request_id: str,
        status: CrossPersonRequestStatus,
        updated_at: datetime,
        *,
        from_statuses: Sequence[CrossPersonRequestStatus] | None = None,
    ) -> CrossPersonRequest | None:
        if from_statuses is not None:
            self.arrived += 1
            if self.arrived >= 2:
                self._both.set()
            try:
                await asyncio.wait_for(self._both.wait(), timeout=1)
            except TimeoutError:
                pass
        return await super().update_status(
            tenant_id, request_id, status, updated_at, from_statuses=from_statuses
        )


class _HoldAfterFirstStatusRead(InMemoryGraphStore):
    """The first rollup to read Zoe's status pauses there until released."""

    def __init__(self, **fields: object) -> None:
        super().__init__(**fields)  # type: ignore[arg-type]
        self.read_once = asyncio.Event()
        self.release = asyncio.Event()

    @classmethod
    def of(cls, store: InMemoryGraphStore) -> _HoldAfterFirstStatusRead:
        return cls(**{name: getattr(store, name) for name in store.__dataclass_fields__})

    async def latest_developer_status(
        self, tenant_id: str, developer_id: str, as_of: date
    ) -> DeveloperStatus | None:
        status = await super().latest_developer_status(tenant_id, developer_id, as_of)
        if developer_id == ZOE and not self.read_once.is_set():
            self.read_once.set()
            await asyncio.wait_for(self.release.wait(), timeout=2)
        return status


@dataclass
class _Settings:
    rollup_backfill_days: int = 0
    # What the rollup's drift reader (N3) is configured with.
    jira_base_url: str | None = None
    github_base_url: str = "https://api.github.com"
    risk_default_no_pr_days: int = 3
    risk_default_pr_age_days: int = 3
    risk_default_stale_days: int = 7
    drift_no_activity_days: int = 3


@dataclass
class _Registry:
    store: InMemoryGraphStore
    settings: _Settings = field(default_factory=_Settings)

    def graph_repository(self) -> InMemoryGraphStore:
        return self.store

    def rollup_repository(self) -> InMemoryGraphStore:
        return self.store

    def status_repository(self) -> InMemoryGraphStore:
        return self.store

    def time_series_repository(self) -> InMemoryGraphStore:
        return self.store

    async def close(self) -> None:
        return None


@dataclass
class _TransactionRecorder:
    calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)
    transactions: int = 0

    async def execute(self, query: str, params: Sequence[object] = ()) -> object:
        self.calls.append((" ".join(query.split()), tuple(params)))
        return None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_TransactionRecorder]:
        self.transactions += 1
        yield self


async def _service(store: InMemoryGraphStore, refresher: object) -> CrossPersonRequestService:
    directory = InMemoryDirectoryUserRepository(store)
    await directory.upsert_users(
        [
            DirectoryUser(tenant_id=TENANT, external_id=ZOE, display_name="Zoe Almeida"),
            DirectoryUser(tenant_id=TENANT, external_id=OMAR, display_name="Omar Haddad"),
        ]
    )
    return CrossPersonRequestService(
        repository=store,
        chat_provider=FakeChatProvider(),
        directory_repository=directory,
        time_series_repository=store,
        blocker_settlement=BlockerSettlement(
            store,
            rollup_refresher=refresher,  # type: ignore[arg-type]
            clock=lambda: PASS_AT,
        ),
        clock=lambda: PASS_AT,
    )


def _to_omar() -> CrossPersonRequest:
    return CrossPersonRequest(
        tenant_id=TENANT,
        id="xreq-d78dbc0b",
        requester_id=ZOE,
        requester_chat_ref=ZOE,
        counterpart_id=OMAR,
        counterpart_display_name="Omar Haddad",
        kind=CrossPersonRequestKind.DEPENDENCY,
        note="Complete HTTP client upgrade for CHK-17",
        source_correlation_id="checkin-zoe-r2",
        status=CrossPersonRequestStatus.OPEN,
        created_at=ASKED_AT,
        updated_at=ASKED_AT,
        notify_message_id="msg-xreq-d78dbc0b",
        notify_correlation_id="xreq-xreq-d78dbc0b",
    )


async def _merge_request(
    store: InMemoryGraphStore, repo: str, number: str, title: str, merged_at: datetime | None
) -> None:
    await store.append_fact(
        FactEvent(
            tenant_id=TENANT,
            source="vcs_pull_request",
            entity_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.REPO, id=repo),
            payload={
                "repo": repo,
                "id": number,
                "title": title,
                "source_branch": title.replace(" ", "-"),
                "merged": merged_at is not None,
                "state": "merged" if merged_at is not None else "opened",
                "web_url": f"http://gitlab.local/{repo}/-/merge_requests/{number}",
            },
            observed_at=merged_at or ASKED_AT,
            correlation_id=f"vcs:pull_request:{TENANT}:{repo}:{number}",
        )
    )


async def _record_rollup(store: InMemoryGraphStore, *, as_of: str | None = None) -> None:
    day = date.fromisoformat(as_of) if as_of else DAY
    async with store.exclusive_day(TENANT, day):
        tree = await store.get_program_tree(TENANT, "program-platform", day)
        await RollupService(
            store, store, BlockerResolutionService(store, store)
        ).compute_and_record(tree, day)


async def _stored_rags(store: InMemoryGraphStore) -> dict[str, Rag]:
    return {
        status.entity_ref.id: status.rag for status in await store.list_node_statuses(TENANT, DAY)
    }


def _hourly() -> RollupInput:
    return RollupInput(tenant_id=TENANT, as_of=DAY.isoformat(), backfill_days=0)


def _resolved(blocker: DeveloperBlocker) -> DeveloperBlocker:
    from dataclasses import replace

    from core.domain.blockers import BlockerResolutionReason

    return replace(
        blocker,
        resolved_on=DAY,
        resolved_reason=BlockerResolutionReason.REPORTED_RESOLVED,
        last_seen_on=DAY,
    )


def _without_all(
    status: DeveloperStatus, resolved: Sequence[DeveloperBlocker]
) -> DeveloperStatus | None:
    from dataclasses import replace

    return replace(status, blockers=())
