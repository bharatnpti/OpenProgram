"""Two merge passes at once resolve, and announce, a request or a blocker once (N24).

Deploy 5, 03:30 UTC: the six per-repository git syncs ran in parallel and each
ran the merge pass. Two of them both resolved Liam's "review and approve
CHK-3" request and both sent him "checkout-api !1 is merged, so I marked your
review request to Noah Weber resolved", at 03:30:03 and 03:30:04. The resolve
was an UPDATE with no status guard, and the chat adapter's send-once guard was
get, post, put: both passes read nothing and both posted.

Now a resolve is one conditional update and only the pass that changed the
row tells anybody; a blocker row resolves the same way, with the person's
status revised by that pass alone; and the send-once guard claims its key, the
request and its new state, before it posts.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pytest

from core.application.blocker_settlement import BlockerSettlement
from core.application.cross_person_service import (
    CrossPersonRequestService,
    requester_notice_key,
)
from core.domain.blockers import BlockerResolutionReason, DeveloperBlocker
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestStatus,
)
from core.domain.directory import DirectoryUser
from core.domain.errors import ProviderUnavailable
from core.domain.graph import EntityRef, FactEvent, NodeKind
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from core.domain.status import DeveloperStatus, StatusSource
from infra.adapters.chat.rate_limit import InMemoryRateLimiter
from infra.adapters.chat.send_once import InMemorySendOnceStore, RedisSendOnceStore
from infra.adapters.chat.slack import SlackChatAdapter
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from infra.persistence.postgres_cross_person import PostgresCrossPersonRequestRepository
from infra.persistence.postgres_status import PostgresStatusRepository
from tests.contract.fakes import FakeChatProvider
from tests.unit.qa2_checkout_slice import (
    CHK8_BLOCKER,
    CHK11_BLOCKER,
    DAY,
    NOAH,
    R3_STATED,
    TENANT,
    ZOE,
    checkout_slice,
)

LIAM = "U-liam"
R2 = datetime(2026, 10, 3, 18, 4, tzinfo=UTC)
MERGED_AT = datetime(2026, 10, 4, 0, 5, 35, tzinfo=UTC)
PASS_AT = datetime(2026, 10, 4, 3, 30, 1, tzinfo=UTC)


# --- requests -------------------------------------------------------------------


async def test_two_merge_passes_at_once_resolve_liams_request_once_and_dm_him_once() -> None:
    store = _HoldBeforeResolve()
    service, chat = await _service(store)
    liam = await store.create(
        _request("xreq-69bd", LIAM, NOAH, "Noah Weber", "review and approve CHK-3 for merge")
    )
    await _merge_request(store, "acme/checkout-api", "1", "CHK-3 Payment intent API")

    # Both passes read the request open before either resolves it, as the
    # checkout-api and storefront-web syncs did at 03:30:01.
    first, second = await asyncio.gather(
        service.settle_merged_work(TENANT), service.settle_merged_work(TENANT)
    )

    assert store.arrived == 2
    assert sorted(len(result) for result in (first, second)) == [0, 1]
    stored = await store.get(TENANT, liam.id)
    assert stored is not None and stored.status is CrossPersonRequestStatus.RESOLVED
    told = _resolved_notices(chat)
    assert [message.text for message in told] == [
        "checkout-api !1 is merged, so I marked your review request to Noah Weber resolved: "
        "review and approve CHK-3 for merge"
    ]
    assert [message.metadata["idempotency_key"] for message in told] == [f"xreq-resolved:{liam.id}"]
    facts = await store.list_recent_facts(TENANT, sources=("cross_person_request",))
    assert [fact.correlation_id for fact in facts] == [f"cross-person:{liam.id}:resolved"]

    # A third pass, a retry of either, finds nothing to resolve and tells nobody.
    assert await service.settle_merged_work(TENANT) == ()
    assert len(_resolved_notices(chat)) == 1


async def test_a_reply_racing_the_merge_pass_resolves_once_and_tells_the_requester_once() -> None:
    store = _HoldBeforeResolve()
    service, chat = await _service(store)
    liam = await store.create(
        _request("xreq-69bd", LIAM, NOAH, "Noah Weber", "review and approve CHK-3 for merge")
    )
    await _merge_request(store, "acme/checkout-api", "1", "CHK-3 Payment intent API")

    merged, replied = await asyncio.gather(
        service.settle_merged_work(TENANT),
        service.handle_counterpart_reply(_reply(liam, NOAH, "approved", PASS_AT), liam),
    )

    assert store.arrived == 2
    assert replied.status is CrossPersonRequestStatus.RESOLVED
    assert len(merged) + (replied.updated_at == PASS_AT) == 1
    assert len(_resolved_notices(chat)) == 1


async def test_two_console_resolves_at_once_tell_the_requester_once() -> None:
    store = _HoldBeforeResolve()
    service, chat = await _service(store)
    liam = await store.create(
        _request("xreq-69bd", LIAM, NOAH, "Noah Weber", "review and approve CHK-3 for merge")
    )

    first, second = await asyncio.gather(
        service.update_status(TENANT, liam.id, CrossPersonRequestStatus.RESOLVED, actor=NOAH),
        service.update_status(TENANT, liam.id, CrossPersonRequestStatus.RESOLVED, actor=NOAH),
    )

    # The one that lost gets the request as it is now, not "not found".
    assert first is not None and second is not None
    assert {first.status, second.status} == {CrossPersonRequestStatus.RESOLVED}
    assert len(_resolved_notices(chat)) == 1


async def test_a_resolve_changes_only_a_request_in_a_status_it_may_leave() -> None:
    store = InMemoryGraphStore()
    request = await store.create(
        _request("xreq-1", LIAM, NOAH, "Noah Weber", "review and approve CHK-3 for merge")
    )
    open_or_acked = (CrossPersonRequestStatus.OPEN, CrossPersonRequestStatus.ACKNOWLEDGED)

    resolved = await store.update_status(
        TENANT, request.id, CrossPersonRequestStatus.RESOLVED, PASS_AT, from_statuses=open_or_acked
    )
    again = await store.update_status(
        TENANT, request.id, CrossPersonRequestStatus.RESOLVED, PASS_AT, from_statuses=open_or_acked
    )
    dismissed = await store.create(
        _request(
            "xreq-2",
            LIAM,
            NOAH,
            "Noah Weber",
            "review CHK-4",
            status=CrossPersonRequestStatus.DISMISSED,
        )
    )
    merge_on_dismissed = await store.update_status(
        TENANT,
        dismissed.id,
        CrossPersonRequestStatus.RESOLVED,
        PASS_AT,
        from_statuses=open_or_acked,
    )

    assert resolved is not None and resolved.status is CrossPersonRequestStatus.RESOLVED
    assert again is None
    assert merge_on_dismissed is None
    kept = await store.get(TENANT, dismissed.id)
    assert kept is not None and kept.status is CrossPersonRequestStatus.DISMISSED


async def test_postgres_resolve_is_one_conditional_update() -> None:
    executor = _RecordingExecutor(rows=[_request_row(status="resolved")])
    repository = PostgresCrossPersonRequestRepository(executor)

    resolved = await repository.update_status(
        TENANT,
        "xreq-1",
        CrossPersonRequestStatus.RESOLVED,
        PASS_AT,
        from_statuses=(CrossPersonRequestStatus.OPEN, CrossPersonRequestStatus.ACKNOWLEDGED),
    )

    assert resolved is not None and resolved.status is CrossPersonRequestStatus.RESOLVED
    [(query, params)] = executor.calls
    compact = " ".join(query.split())
    assert compact == (
        "UPDATE cross_person_requests SET status = %s, updated_at = %s "
        "WHERE tenant_id = %s AND id = %s AND status <> %s AND status IN (%s, %s) "
        "RETURNING *"
    )
    assert params == (
        "resolved",
        PASS_AT,
        TENANT,
        "xreq-1",
        "resolved",
        "open",
        "acknowledged",
    )


async def test_postgres_resolve_that_lost_returns_none() -> None:
    repository = PostgresCrossPersonRequestRepository(_RecordingExecutor(rows=[]))

    assert (
        await repository.update_status(TENANT, "xreq-1", CrossPersonRequestStatus.RESOLVED, PASS_AT)
        is None
    )


# --- blockers -------------------------------------------------------------------


async def test_two_merge_passes_at_once_resolve_zoes_chk8_blocker_once() -> None:
    store = _HoldBeforeBlockerResolve.of(await checkout_slice())
    service, _ = await _service(store, blocker_settlement=BlockerSettlement(store))
    await _merge_request(store, "acme/storefront-web", "1", "CHK-8 Payment form validation UI")

    await asyncio.gather(service.settle_merged_work(TENANT), service.settle_merged_work(TENANT))

    assert store.arrived == 2
    assert [[row.blocker_id for row in result] for result in store.results] in (
        [["blk-chk8"], []],
        [[], ["blk-chk8"]],
    )
    assert store.revisions == 1
    assert [b.blocker_id for b in await store.open_blockers(TENANT, ZOE, DAY)] == ["blk-chk11"]
    row = store._developer_blockers[(TENANT, "blk-chk8")]
    assert row.resolved_on == DAY
    assert row.resolved_reason is BlockerResolutionReason.REPORTED_RESOLVED
    status = await store.latest_developer_status(TENANT, ZOE, DAY)
    assert status is not None and status.blockers == (CHK11_BLOCKER,)


async def test_two_merge_passes_over_zoes_request_resolve_it_and_her_blocker_once() -> None:
    store = _HoldBeforeBlockerResolve.of(await checkout_slice())
    service, chat = await _service(store, blocker_settlement=BlockerSettlement(store))
    to_noah = await store.create(
        _request("xreq-1c57", ZOE, NOAH, "Noah Weber", "Review storefront-web !1 for CHK-8")
    )
    await _merge_request(store, "acme/storefront-web", "1", "CHK-8 Payment form validation UI")

    await asyncio.gather(service.settle_merged_work(TENANT), service.settle_merged_work(TENANT))

    stored = await store.get(TENANT, to_noah.id)
    assert stored is not None and stored.status is CrossPersonRequestStatus.RESOLVED
    assert len(_resolved_notices(chat)) == 1
    changed = [row.blocker_id for result in store.results for row in result]
    assert changed == ["blk-chk8"]
    assert store.revisions == 1
    status = await store.latest_developer_status(TENANT, ZOE, DAY)
    assert status is not None and status.blockers == (CHK11_BLOCKER,)


async def test_a_blocker_restated_after_it_was_read_is_not_resolved() -> None:
    store = await checkout_slice()
    [chk8] = [b for b in await store.open_blockers(TENANT, ZOE, DAY) if b.blocker_id == "blk-chk8"]
    # Zoe restates it (a check-in) between the pass reading it and resolving it.
    store.blocker_clock = lambda: R3_STATED + timedelta(minutes=5)
    await store.record_developer_blockers(TENANT, (chk8,))

    changed = await store.resolve_developer_blockers(
        TENANT,
        ZOE,
        (_resolved(chk8),),
        status_as_of=DAY,
        revise_status=_never_revised,
    )

    assert changed == ()
    assert "blk-chk8" in {b.blocker_id for b in await store.open_blockers(TENANT, ZOE, DAY)}


async def test_postgres_blocker_resolve_is_conditional_and_revises_the_status_once_locked() -> None:
    chk8 = _blocker_row("blk-chk8", resolved=True)
    executor = _TransactionRecorder(
        results=[[chk8], [], [_status_row(ZOE, (CHK11_BLOCKER, CHK8_BLOCKER))]]
    )
    repository = PostgresStatusRepository(executor)  # type: ignore[arg-type]
    seen: list[tuple[str, ...]] = []

    def revise(
        status: DeveloperStatus, resolved: Sequence[DeveloperBlocker]
    ) -> DeveloperStatus | None:
        seen.append(tuple(row.blocker_id for row in resolved))
        return DeveloperStatus(
            tenant_id=status.tenant_id,
            developer_id=status.developer_id,
            as_of=status.as_of,
            source=status.source,
            blockers=(CHK11_BLOCKER,),
            summary=status.summary,
        )

    changed = await repository.resolve_developer_blockers(
        TENANT,
        ZOE,
        (_resolved(_domain_blocker("blk-chk8")), _resolved(_domain_blocker("blk-gone"))),
        status_as_of=DAY,
        revise_status=revise,
    )

    assert [row.blocker_id for row in changed] == ["blk-chk8"]
    assert seen == [("blk-chk8",)]
    assert executor.transactions == 1
    resolves = [call for call in executor.calls if call[0].startswith("UPDATE developer_blockers")]
    assert len(resolves) == 2
    for query, params in resolves:
        for condition in (
            "WHERE tenant_id = %s AND blocker_id = %s AND developer_id = %s",
            "AND resolved_on IS NULL",
            "AND updated_at IS NOT DISTINCT FROM %s::timestamptz",
            "RETURNING",
        ):
            assert condition in query
        assert params[0] == DAY and params[1] == "reported_resolved"
        assert params[-1] == R3_STATED
    [(lock, lock_params)] = [call for call in executor.calls if "FOR UPDATE" in call[0]]
    assert lock.startswith("SELECT") and lock_params == (TENANT, ZOE, DAY)
    [(upsert, upsert_params)] = [
        call for call in executor.calls if call[0].startswith("INSERT INTO developer_statuses")
    ]
    assert upsert_params[4] == {"items": [CHK11_BLOCKER]}


async def test_postgres_blocker_resolve_that_lost_reads_no_status() -> None:
    executor = _TransactionRecorder(results=[[]])
    repository = PostgresStatusRepository(executor)  # type: ignore[arg-type]

    changed = await repository.resolve_developer_blockers(
        TENANT,
        ZOE,
        (_resolved(_domain_blocker("blk-chk8")),),
        status_as_of=DAY,
        revise_status=_never_revised,
    )

    assert changed == ()
    assert [call[0].split()[0] for call in executor.calls] == ["UPDATE"]


# --- the send-once guard ----------------------------------------------------------


def test_a_requester_notice_is_keyed_on_the_request_and_its_new_state() -> None:
    resolved = requester_notice_key("xreq-69bd", CrossPersonRequestStatus.RESOLVED)
    acknowledged = requester_notice_key("xreq-69bd", CrossPersonRequestStatus.ACKNOWLEDGED)

    # The keys deploy 5 wrote, so a notice it sent is not sent again after this one.
    assert resolved == "xreq-resolved:xreq-69bd"
    assert acknowledged == "xreq-acknowledged:xreq-69bd"
    assert requester_notice_key("xreq-other", CrossPersonRequestStatus.RESOLVED) != resolved


async def test_two_sends_at_once_with_one_key_post_once() -> None:
    http = _SlowSlackHttpClient()
    adapter = _adapter(http)
    user = ChatUserRef(tenant_id=TENANT, external_id=LIAM)
    notice = _notice("xreq-resolved:xreq-69bd")

    first, second = await asyncio.gather(
        adapter.send_dm(user, notice), adapter.send_dm(user, notice)
    )

    assert first == second == "1759548603.000001"
    assert http.posted == [("D-U-liam", notice.text)]
    # A retry later returns the first message too.
    assert await adapter.send_dm(user, notice) == first
    assert len(http.posted) == 1


async def test_a_failed_send_frees_its_key_for_the_retry() -> None:
    http = _SlowSlackHttpClient(failures=1)
    adapter = _adapter(http)
    user = ChatUserRef(tenant_id=TENANT, external_id=LIAM)
    notice = _notice("xreq-resolved:xreq-69bd")

    with pytest.raises(ProviderUnavailable):
        await adapter.send_dm(user, notice)
    retried = await adapter.send_dm(user, notice)

    assert retried == "1759548603.000001"
    assert http.posted == [("D-U-liam", notice.text)]


async def test_a_key_held_by_a_send_that_never_finishes_fails_without_posting() -> None:
    store = InMemorySendOnceStore()
    assert await store.claim(TENANT, "xreq-resolved:xreq-69bd")
    http = _SlowSlackHttpClient()
    adapter = _adapter(http, store=store, wait_seconds=0.05)

    with pytest.raises(ProviderUnavailable):
        await adapter.send_dm(
            ChatUserRef(tenant_id=TENANT, external_id=LIAM), _notice("xreq-resolved:xreq-69bd")
        )

    assert http.posted == []


async def test_redis_send_once_claims_atomically_and_releases_only_a_claim() -> None:
    redis = _FakeRedis()
    store = RedisSendOnceStore(client=redis, ttl_seconds=86_400, claim_seconds=30)  # type: ignore[arg-type]
    key = "openprogram:chat:send-once:demo:xreq-resolved:xreq-69bd"

    assert await store.claim("demo", "xreq-resolved:xreq-69bd")
    assert not await store.claim("demo", "xreq-resolved:xreq-69bd")
    # A claim is not a message id.
    assert await store.get("demo", "xreq-resolved:xreq-69bd") is None
    assert redis.expiry[key] == 30

    await store.release("demo", "xreq-resolved:xreq-69bd")
    assert await store.claim("demo", "xreq-resolved:xreq-69bd")
    await store.put("demo", "xreq-resolved:xreq-69bd", "1759548603.000001")
    assert redis.expiry[key] == 86_400
    assert await store.get("demo", "xreq-resolved:xreq-69bd") == "1759548603.000001"

    # A recorded message id is never released, and its key cannot be claimed.
    await store.release("demo", "xreq-resolved:xreq-69bd")
    assert await store.get("demo", "xreq-resolved:xreq-69bd") == "1759548603.000001"
    assert not await store.claim("demo", "xreq-resolved:xreq-69bd")


# --- helpers --------------------------------------------------------------------


class _HoldBeforeResolve(InMemoryGraphStore):
    """Holds each request transition until two have arrived: both read it open."""

    def __init__(self, **fields: object) -> None:
        super().__init__(**fields)  # type: ignore[arg-type]
        self.arrived = 0
        self._both = asyncio.Event()

    async def update_status(
        self,
        tenant_id: str,
        request_id: str,
        status: CrossPersonRequestStatus,
        updated_at: datetime,
        *,
        from_statuses: Sequence[CrossPersonRequestStatus] | None = None,
    ) -> CrossPersonRequest | None:
        self.arrived += 1
        if self.arrived >= 2:
            self._both.set()
        await _wait(self._both)
        return await super().update_status(
            tenant_id, request_id, status, updated_at, from_statuses=from_statuses
        )


class _HoldBeforeBlockerResolve(InMemoryGraphStore):
    """Holds each blocker resolution until two have arrived, and records them."""

    def __init__(self, **fields: object) -> None:
        super().__init__(**fields)  # type: ignore[arg-type]
        self.arrived = 0
        self.revisions = 0
        self.results: list[tuple[DeveloperBlocker, ...]] = []
        self._both = asyncio.Event()

    @classmethod
    def of(cls, store: InMemoryGraphStore) -> _HoldBeforeBlockerResolve:
        """The same store, sharing its rows, with the hold in front of it."""
        return cls(**{name: getattr(store, name) for name in store.__dataclass_fields__})

    async def resolve_developer_blockers(
        self,
        tenant_id: str,
        developer_id: str,
        blockers: Sequence[DeveloperBlocker],
        *,
        status_as_of: date,
        revise_status: Callable[
            [DeveloperStatus, Sequence[DeveloperBlocker]], DeveloperStatus | None
        ],
    ) -> tuple[DeveloperBlocker, ...]:
        self.arrived += 1
        if self.arrived >= 2:
            self._both.set()
        await _wait(self._both)

        def counted(
            status: DeveloperStatus, resolved: Sequence[DeveloperBlocker]
        ) -> DeveloperStatus | None:
            self.revisions += 1
            return revise_status(status, resolved)

        result = await super().resolve_developer_blockers(
            tenant_id,
            developer_id,
            blockers,
            status_as_of=status_as_of,
            revise_status=counted,
        )
        self.results.append(result)
        return result


async def _wait(event: asyncio.Event) -> None:
    # A pass that never meets a second one goes on alone rather than hang.
    try:
        await asyncio.wait_for(event.wait(), timeout=1)
    except TimeoutError:
        return


async def _service(
    store: InMemoryGraphStore, *, blocker_settlement: BlockerSettlement | None = None
) -> tuple[CrossPersonRequestService, FakeChatProvider]:
    directory = InMemoryDirectoryUserRepository(store)
    await directory.upsert_users(
        [
            DirectoryUser(tenant_id=TENANT, external_id=LIAM, display_name="Liam Chen"),
            DirectoryUser(tenant_id=TENANT, external_id=NOAH, display_name="Noah Weber"),
            DirectoryUser(tenant_id=TENANT, external_id=ZOE, display_name="Zoe Almeida"),
        ]
    )
    chat = FakeChatProvider()
    service = CrossPersonRequestService(
        repository=store,
        chat_provider=chat,
        directory_repository=directory,
        time_series_repository=store,
        blocker_settlement=blocker_settlement,
        clock=lambda: PASS_AT,
    )
    return service, chat


def _resolved_notices(chat: FakeChatProvider) -> list[OutboundMessage]:
    return [m for m in chat.sent if m.metadata["purpose"] == "cross_person_request_resolved"]


def _request(
    request_id: str,
    requester: str,
    counterpart: str,
    name: str,
    note: str,
    *,
    status: CrossPersonRequestStatus = CrossPersonRequestStatus.OPEN,
) -> CrossPersonRequest:
    return CrossPersonRequest(
        tenant_id=TENANT,
        id=request_id,
        requester_id=requester,
        requester_chat_ref=requester,
        counterpart_id=counterpart,
        counterpart_display_name=name,
        kind=CrossPersonRequestKind.REVIEW,
        note=note,
        source_correlation_id=f"checkin-{request_id}",
        status=status,
        created_at=R2,
        updated_at=R2,
        notify_message_id=f"msg-{request_id}",
        notify_correlation_id=f"xreq-{request_id}",
    )


def _reply(
    request: CrossPersonRequest, counterpart: str, text: str, at: datetime
) -> InboundMessage:
    return InboundMessage(
        tenant_id=TENANT,
        user=ChatUserRef(tenant_id=TENANT, external_id=counterpart),
        text=text,
        thread_id=request.notify_message_id or "",
        message_id=f"m-{request.id}-{text}",
        correlation_id=request.notify_correlation_id or "",
        received_at=at,
    )


async def _merge_request(store: InMemoryGraphStore, repo: str, number: str, title: str) -> None:
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
                "merged": True,
                "state": "merged",
                "web_url": f"http://gitlab.local/{repo}/-/merge_requests/{number}",
            },
            observed_at=MERGED_AT,
            correlation_id=f"vcs:pull_request:{TENANT}:{repo}:{number}",
        )
    )


def _domain_blocker(blocker_id: str) -> DeveloperBlocker:
    from core.domain.blockers import BlockerSource, normalize_blocker_key

    return DeveloperBlocker(
        tenant_id=TENANT,
        blocker_id=blocker_id,
        developer_id=ZOE,
        description=CHK8_BLOCKER,
        normalized_key=normalize_blocker_key(CHK8_BLOCKER),
        work_item_id="CHK-8",
        source=BlockerSource.CHECKIN,
        first_seen_on=date(2026, 10, 3),
        last_seen_on=DAY,
        updated_at=R3_STATED,
    )


def _resolved(blocker: DeveloperBlocker) -> DeveloperBlocker:
    from dataclasses import replace

    return replace(
        blocker,
        resolved_on=DAY,
        resolved_reason=BlockerResolutionReason.REPORTED_RESOLVED,
        last_seen_on=DAY,
    )


def _never_revised(
    status: DeveloperStatus, resolved: Sequence[DeveloperBlocker]
) -> DeveloperStatus | None:
    raise AssertionError("no status is revised when no blocker changed")


def _request_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "tenant_id": TENANT,
        "id": "xreq-1",
        "requester_id": LIAM,
        "requester_chat_ref": LIAM,
        "counterpart_id": NOAH,
        "kind": "review",
        "note": "review and approve CHK-3 for merge",
        "task_kind": None,
        "task_id": None,
        "source_correlation_id": "checkin-1",
        "status": "open",
        "created_at": R2,
        "updated_at": PASS_AT,
        "counterpart_display_name": "Noah Weber",
        "notify_attempts": 1,
    }
    row.update(overrides)
    return row


def _blocker_row(blocker_id: str, *, resolved: bool) -> dict[str, object]:
    return {
        "tenant_id": TENANT,
        "blocker_id": blocker_id,
        "developer_id": ZOE,
        "description": CHK8_BLOCKER,
        "normalized_key": CHK8_BLOCKER.lower(),
        "work_item_id": "CHK-8",
        "pod_id": None,
        "source": "checkin",
        "source_correlation_id": None,
        "attribution_asked_at": None,
        "first_seen_on": date(2026, 10, 3),
        "last_seen_on": DAY,
        "resolved_on": DAY if resolved else None,
        "resolved_reason": "reported_resolved" if resolved else None,
        "updated_at": PASS_AT,
    }


def _status_row(developer_id: str, blockers: tuple[str, ...]) -> dict[str, object]:
    return {
        "tenant_id": TENANT,
        "developer_id": developer_id,
        "as_of": DAY,
        "source": StatusSource.CONFIRMED.value,
        "blockers": {"items": list(blockers)},
        "summary": "Update recorded.",
        "eta_change_days": None,
        "developer_confirmed": True,
        "confirmed_at": None,
    }


@dataclass
class _RecordingExecutor:
    rows: list[dict[str, object]]
    calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)

    async def execute(self, query: str, params: Sequence[object] = ()) -> object:
        self.calls.append((query, tuple(params)))
        return None

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]:
        self.calls.append((query, tuple(params)))
        return self.rows


@dataclass
class _TransactionRecorder:
    """Answers each fetch in a transaction with the next canned result."""

    results: list[list[dict[str, object]]]
    calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)
    transactions: int = 0

    async def execute(self, query: str, params: Sequence[object] = ()) -> object:
        self.calls.append((" ".join(query.split()), tuple(params)))
        return None

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]:
        self.calls.append((" ".join(query.split()), tuple(params)))
        return self.results.pop(0) if self.results else []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_TransactionRecorder]:
        self.transactions += 1
        yield self


@dataclass
class _SlowSlackHttpClient:
    """Posts after a pause, so two sends with one key overlap; may fail first."""

    failures: int = 0
    posted: list[tuple[str, str]] = field(default_factory=list)

    async def open_conversation(self, user_id: str) -> str:
        return f"D-{user_id}"

    async def post_message(self, channel_id: str, text: str) -> str:
        await asyncio.sleep(0.02)
        if self.failures > 0:
            self.failures -= 1
            raise ProviderUnavailable("slack is down")
        self.posted.append((channel_id, text))
        return f"1759548603.{len(self.posted):06d}"

    async def latest_reply(self, thread_id: str) -> Mapping[str, object] | None:
        return None

    async def list_users(self, cursor: str | None = None) -> Mapping[str, object]:
        return {"members": []}


def _adapter(
    http: _SlowSlackHttpClient,
    *,
    store: InMemorySendOnceStore | None = None,
    wait_seconds: float = 2.0,
) -> SlackChatAdapter:
    return SlackChatAdapter(
        tenant_id=TENANT,
        http_client=http,
        rate_limiter=InMemoryRateLimiter(),
        send_once_store=store or InMemorySendOnceStore(),
        send_once_wait_seconds=wait_seconds,
        send_once_poll_seconds=0.005,
    )


def _notice(key: str) -> OutboundMessage:
    return OutboundMessage(
        tenant_id=TENANT,
        text=(
            "checkout-api !1 is merged, so I marked your review request to Noah Weber "
            "resolved: review and approve CHK-3 for merge"
        ),
        correlation_id="xreq-resolved-xreq-69bd",
        metadata={"purpose": "cross_person_request_resolved", "idempotency_key": key},
    )


@dataclass
class _FakeRedis:
    values: dict[str, str] = field(default_factory=dict)
    expiry: dict[str, int] = field(default_factory=dict)

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(
        self, key: str, value: str, *, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        if nx and key in self.values:
            return None
        self.values[key] = value
        if ex is not None:
            self.expiry[key] = ex
        return True

    async def delete(self, key: str) -> int:
        self.expiry.pop(key, None)
        return 1 if self.values.pop(key, None) is not None else 0
