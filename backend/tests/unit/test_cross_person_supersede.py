"""Older open copies of an ask recorded before N11 are superseded by the newest (N11b).

R4 monitor B: two pairs from before the N11 fix stayed open with nothing to
close them. Mina asked Asha about the CHK-10 acceptance criteria in R1 and
again in R2; Noah's R2 ask of Liam about CHK-6 stayed open beside his R3 copy,
which Liam had acknowledged. Neither waits on a merge request, so no merge
pass closed them.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from core.application.cross_person_service import CrossPersonRequestService
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestStatus,
)
from core.domain.directory import DirectoryUser
from core.domain.graph import EntityRef, NodeKind
from core.domain.messaging import ChatUserRef, InboundMessage
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider

TENANT = "demo"
R1 = datetime(2026, 10, 3, 12, 16, 42, tzinfo=UTC)
R2 = datetime(2026, 10, 3, 18, 4, 4, tzinfo=UTC)
R3 = datetime(2026, 10, 4, 0, 4, 15, tzinfo=UTC)
SWEEP_AT = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
MINA, ASHA, NOAH, LIAM, RAJ = "U-mina", "U-asha", "U-noah", "U-liam", "U-raj"
_NAMES = {
    MINA: "Mina Patel",
    ASHA: "Asha Rao",
    NOAH: "Noah Weber",
    LIAM: "Liam Chen",
    RAJ: "Raj Iyer",
}


async def test_the_two_pre_fix_pairs_collapse_into_their_newest_copy_and_nobody_is_dmed() -> None:
    service, store, chat = await _service()
    rows = await _qa2_requests(store)

    resolved = await service.settle_merged_work(TENANT)

    assert resolved == ()  # nothing merged, so the merge pass itself resolves nothing
    assert await _statuses(store) == {
        "mina-r1": CrossPersonRequestStatus.DISMISSED,
        "mina-r2": CrossPersonRequestStatus.OPEN,
        "noah-r2": CrossPersonRequestStatus.DISMISSED,
        "noah-r3": CrossPersonRequestStatus.ACKNOWLEDGED,
        # Other work, a vague ask and an unnamed reviewer are no copies.
        "noah-idp3": CrossPersonRequestStatus.OPEN,
        "noah-vague": CrossPersonRequestStatus.OPEN,
        "raj-reviewer": CrossPersonRequestStatus.NEEDS_RESOLUTION,
    }
    assert chat.sent == []
    facts = await _superseded_facts(store)
    assert {(f["request_id"], f["superseded_by"], f["dependency_status"]) for f in facts} == {
        ("mina-r1", "mina-r2", "dismissed"),
        ("noah-r2", "noah-r3", "dismissed"),
    }
    # OpenProgram closed the copies itself: no member made the change.
    assert [f["changed_by"] for f in facts] == [None, None]
    # The kept copy is the ask as it stands; the dismissed one keeps its words.
    kept = await store.get(TENANT, "noah-r3")
    assert kept == rows["noah-r3"]


async def test_the_sweep_is_idempotent_and_two_passes_at_once_close_each_copy_once() -> None:
    service, store, chat = await _service()
    await _qa2_requests(store)

    first, second = await asyncio.gather(
        service.supersede_repeats(TENANT), service.supersede_repeats(TENANT)
    )
    third = await service.supersede_repeats(TENANT)

    assert sorted(r.id for r in (*first, *second)) == ["mina-r1", "noah-r2"]
    assert third == ()
    assert len(await _superseded_facts(store)) == 2
    assert chat.sent == []


async def test_the_kept_copy_still_tells_its_requester_once_when_it_resolves() -> None:
    service, store, chat = await _service()
    rows = await _qa2_requests(store)
    await service.supersede_repeats(TENANT)
    mina_r2 = rows["mina-r2"]

    await service.handle_counterpart_reply(_reply(ASHA, mina_r2, "approved"), mina_r2)

    # A superseded copy is dismissed, not resolved, so it does not pass for a
    # copy Mina was already told about.
    told = [m for m in chat.sent if m.metadata["purpose"] == "cross_person_request_resolved"]
    assert [m.metadata["request_id"] for m in told] == ["mina-r2"]
    dismissed = await store.get(TENANT, "mina-r1")
    assert dismissed is not None and dismissed.status is CrossPersonRequestStatus.DISMISSED


async def test_a_superseded_copy_whose_dm_failed_is_never_retried() -> None:
    service, store, chat = await _service()
    await _qa2_requests(store)
    # Mina's R1 DM had failed and a retry was due.
    failed = await store.get(TENANT, "mina-r1")
    assert failed is not None
    store._cross_person_requests[(TENANT, failed.id)] = replace(
        failed,
        notify_message_id=None,
        notify_correlation_id=None,
        notify_attempts=1,
        notify_last_attempt_at=R1,
        notify_next_attempt_at=R1 + timedelta(minutes=5),
    )

    await service.settle_merged_work(TENANT)
    retried = await service.retry_failed_notifications(TENANT, now=SWEEP_AT)

    assert retried.due == 0
    assert chat.sent == []


# --- helpers --------------------------------------------------------------------


async def _service() -> tuple[CrossPersonRequestService, InMemoryGraphStore, FakeChatProvider]:
    store = InMemoryGraphStore()
    directory = InMemoryDirectoryUserRepository(store)
    await directory.upsert_users(
        [
            DirectoryUser(tenant_id=TENANT, external_id=key, display_name=name)
            for key, name in _NAMES.items()
        ]
    )
    chat = FakeChatProvider()
    service = CrossPersonRequestService(
        repository=store,
        chat_provider=chat,
        directory_repository=directory,
        time_series_repository=store,
        clock=lambda: SWEEP_AT,
    )
    return service, store, chat


async def _qa2_requests(store: InMemoryGraphStore) -> dict[str, CrossPersonRequest]:
    """The qa2 open requests after R4, as cross_person_requests held them."""
    rows = (
        _stored("mina-r1", MINA, ASHA, "Review of drafted acceptance criteria scheduled", R1),
        _stored("mina-r2", MINA, ASHA, "reviewing acceptance criteria for CHK-10", R2),
        _stored("noah-r2", NOAH, LIAM, "review CHK-6 refund edge cases PR", R2),
        replace(
            _stored("noah-r3", NOAH, LIAM, "review CHK-6 on checkout-api !3", R3),
            status=CrossPersonRequestStatus.ACKNOWLEDGED,
            updated_at=datetime(2026, 10, 4, 6, 4, 57, tzinfo=UTC),
            task_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-6"),
        ),
        _stored("noah-idp3", NOAH, LIAM, "review IDP-3 passkey enrolment", R1),
        _stored("noah-vague", NOAH, LIAM, "can you take a look?", R1),
        replace(
            _stored("raj-reviewer", RAJ, ASHA, "Needs a reviewer assigned to merge !1", R1),
            counterpart_id=None,
            counterpart_display_name=None,
            status=CrossPersonRequestStatus.NEEDS_RESOLUTION,
            notify_message_id=None,
            notify_correlation_id=None,
        ),
    )
    for row in rows:
        await store.create(row)
    return {row.id: row for row in rows}


def _stored(
    request_id: str, requester: str, counterpart: str, note: str, at: datetime
) -> CrossPersonRequest:
    return CrossPersonRequest(
        tenant_id=TENANT,
        id=request_id,
        requester_id=requester,
        requester_chat_ref=requester,
        counterpart_id=counterpart,
        counterpart_display_name=_NAMES[counterpart],
        kind=CrossPersonRequestKind.REVIEW,
        note=note,
        source_correlation_id=f"checkin-{request_id}",
        status=CrossPersonRequestStatus.OPEN,
        created_at=at,
        updated_at=at,
        notify_message_id=f"msg-{request_id}",
        notify_correlation_id=f"xreq-{request_id}",
        notify_attempts=1,
    )


async def _statuses(store: InMemoryGraphStore) -> dict[str, CrossPersonRequestStatus]:
    return {
        request.id: request.status
        for request in store._cross_person_requests.values()
        if request.tenant_id == TENANT
    }


async def _superseded_facts(store: InMemoryGraphStore) -> list[dict[str, object]]:
    facts = await store.list_recent_facts(TENANT, sources=("cross_person_request",), limit=100)
    return [dict(fact.payload) for fact in facts if fact.payload.get("transition") == "superseded"]


def _reply(counterpart: str, request: CrossPersonRequest, text: str) -> InboundMessage:
    return InboundMessage(
        tenant_id=TENANT,
        user=ChatUserRef(tenant_id=TENANT, external_id=counterpart),
        text=text,
        thread_id=request.notify_message_id or "",
        message_id=f"m-{request.id}-{text}",
        correlation_id=request.notify_correlation_id or "",
        received_at=SWEEP_AT,
    )
