"""A blocker resolves when what it waits on is done (N19).

R3: Zoe's "CHK-8 ... blocked until Noah reviews it" stayed open after Noah
approved (00:05:48) and storefront-web !1 merged (00:05:39). The 00:15 and
01:15 rollups kept Zoe red, and Payments, Checkout and the program amber.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from core.application.blocker_resolution import BlockerResolutionService
from core.application.blocker_settlement import BlockerSettlement, request_match_score
from core.application.cross_person_service import CrossPersonRequestService
from core.application.rollup_service import RollupService
from core.domain.blockers import BlockerResolutionReason
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestStatus,
)
from core.domain.graph import EntityRef, FactEvent, NodeKind
from core.domain.messaging import ChatUserRef, InboundMessage, OutboundMessage
from core.domain.rollup import Rag
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider
from tests.unit.qa2_checkout_slice import (
    CHK8_BLOCKER,
    CHK11_BLOCKER,
    DAY,
    NOAH,
    OMAR,
    TENANT,
    ZOE,
    checkout_slice,
    zoe_blocker,
)

APPROVED_AT = datetime(2026, 10, 4, 0, 5, 48, tzinfo=UTC)
MERGED_AT = datetime(2026, 10, 4, 0, 5, 39, tzinfo=UTC)


async def test_noahs_approval_resolves_zoes_chk8_blocker_and_her_status_follows() -> None:
    store = await checkout_slice()
    service = _service(store)
    to_noah = await store.create(
        _request("xreq-6286", NOAH, "Noah Weber", "review storefront-web !1 for CHK-8")
    )
    await store.create(
        _request(
            "xreq-d78d",
            OMAR,
            "Omar Haddad",
            "Complete HTTP client upgrade for CHK-17",
            "dependency",
        )
    )

    await service.handle_counterpart_reply(_reply(to_noah, NOAH, "approved", APPROVED_AT), to_noah)

    still_open = await store.open_blockers(TENANT, ZOE, DAY)
    assert [blocker.blocker_id for blocker in still_open] == ["blk-chk11"]
    resolved = await _row(store, "blk-chk8")
    assert resolved.resolved_on == DAY
    assert resolved.resolved_reason is BlockerResolutionReason.REPORTED_RESOLVED
    status = await store.latest_developer_status(TENANT, ZOE, DAY)
    assert status is not None and status.blockers == (CHK11_BLOCKER,)

    by_id = await _rollup(store)
    # One blocker left: Zoe is amber, not red, and Payments has nothing of hers.
    assert by_id[ZOE] is Rag.AMBER
    assert by_id["pod-payments"] is Rag.GREEN


async def test_the_dependency_on_omar_is_still_open_after_noahs_approval() -> None:
    store = await checkout_slice()
    service = _service(store)
    to_omar = await store.create(
        _request(
            "xreq-d78d",
            OMAR,
            "Omar Haddad",
            "Complete HTTP client upgrade for CHK-17",
            "dependency",
        )
    )

    await service.handle_counterpart_reply(
        _reply(to_omar, OMAR, "CHK-17 should merge tomorrow", APPROVED_AT), to_omar
    )
    assert {b.blocker_id for b in await store.open_blockers(TENANT, ZOE, DAY)} == {
        "blk-chk8",
        "blk-chk11",
    }

    await service.handle_counterpart_reply(_reply(to_omar, OMAR, "merged", APPROVED_AT), to_omar)
    assert [b.blocker_id for b in await store.open_blockers(TENANT, ZOE, DAY)] == ["blk-chk8"]


async def test_a_blocker_restated_after_the_approval_is_kept() -> None:
    later = datetime(2026, 10, 4, 0, 10, tzinfo=UTC)
    store = await checkout_slice(stated_at=later)
    service = _service(store)
    to_noah = await store.create(
        _request("xreq-6286", NOAH, "Noah Weber", "review storefront-web !1 for CHK-8")
    )

    await service.handle_counterpart_reply(_reply(to_noah, NOAH, "approved", APPROVED_AT), to_noah)

    assert {b.blocker_id for b in await store.open_blockers(TENANT, ZOE, DAY)} == {
        "blk-chk8",
        "blk-chk11",
    }


async def test_a_blocker_waiting_on_two_people_stays_open_until_both_are_done() -> None:
    store = await checkout_slice()
    both = "CHK-8 is blocked until Noah and Liam both review storefront-web !1"
    await store.record_developer_blockers(
        TENANT, (zoe_blocker("blk-both", both, work_item_id="CHK-8"),)
    )
    service = _service(store)
    to_noah = await store.create(
        _request("xreq-n", NOAH, "Noah Weber", "review storefront-web !1 for CHK-8")
    )
    await store.create(
        _request("xreq-l", "U-liam", "Liam Chen", "review storefront-web !1 for CHK-8")
    )

    await service.handle_counterpart_reply(_reply(to_noah, NOAH, "approved", APPROVED_AT), to_noah)

    assert "blk-both" in {b.blocker_id for b in await store.open_blockers(TENANT, ZOE, DAY)}


async def test_the_merge_of_storefront_web_1_resolves_the_chk8_blocker_without_any_request() -> (
    None
):
    store = await checkout_slice()
    service = _service(store)
    await _merged(store, "acme/storefront-web", "1", "CHK-8 Payment form validation UI")
    await _open(store, "acme/platform-libs", "1", "CHK-17 Upgrade shared HTTP client library")

    await service.settle_merged_work(TENANT)

    assert [b.blocker_id for b in await store.open_blockers(TENANT, ZOE, DAY)] == ["blk-chk11"]
    status = await store.latest_developer_status(TENANT, ZOE, DAY)
    assert status is not None and status.blockers == (CHK11_BLOCKER,)


async def test_a_blocker_stated_after_the_merge_is_what_the_person_still_reports() -> None:
    store = await checkout_slice(stated_at=datetime(2026, 10, 4, 0, 30, tzinfo=UTC))
    service = _service(store)
    await _merged(store, "acme/storefront-web", "1", "CHK-8 Payment form validation UI")

    await service.settle_merged_work(TENANT)

    assert {b.blocker_id for b in await store.open_blockers(TENANT, ZOE, DAY)} == {
        "blk-chk8",
        "blk-chk11",
    }


async def test_the_last_blocker_resolved_leaves_no_flat_string_for_the_rollup_to_revive() -> None:
    store = await checkout_slice()
    service = _service(store)
    await _merged(store, "acme/storefront-web", "1", "CHK-8 Payment form validation UI")
    await _merged(store, "acme/platform-libs", "1", "CHK-17 Upgrade shared HTTP client library")

    await service.settle_merged_work(TENANT)

    assert await store.open_blockers(TENANT, ZOE, DAY) == []
    by_id = await _rollup(store)
    assert by_id[ZOE] is Rag.GREEN
    assert by_id["pod-payments"] is Rag.GREEN
    assert by_id["project-checkout"] is Rag.GREEN
    assert by_id["program-platform"] is Rag.GREEN


async def test_a_status_string_with_its_work_item_suffix_is_taken_out_too() -> None:
    store = await checkout_slice()
    status = await store.latest_developer_status(TENANT, ZOE, DAY)
    assert status is not None
    await store.record_developer_status(
        replace(status, blockers=(CHK11_BLOCKER, f"{CHK8_BLOCKER} (CHK-8)"))
    )

    await BlockerSettlement(store).settle_for_request(
        _request("xreq-6286", NOAH, "Noah Weber", "review storefront-web !1 for CHK-8"),
        resolved_at=APPROVED_AT,
    )

    status = await store.latest_developer_status(TENANT, ZOE, DAY)
    assert status is not None and status.blockers == (CHK11_BLOCKER,)


async def test_a_failed_resolution_dm_leaves_no_blocker_or_copy_open_behind_it() -> None:
    store = await checkout_slice()
    service = replace(_service(store), chat_provider=_ResolvedNoticeFails())
    r2 = await store.create(
        _request("xreq-1c57", NOAH, "Noah Weber", "Review storefront-web !1 for CHK-8")
    )
    r3 = await store.create(
        _request("xreq-6286", NOAH, "Noah Weber", "review storefront-web !1 for CHK-8")
    )

    with pytest.raises(RuntimeError):
        await service.handle_counterpart_reply(_reply(r3, NOAH, "approved", APPROVED_AT), r3)

    copy = await store.get(TENANT, r2.id)
    assert copy is not None and copy.status is CrossPersonRequestStatus.RESOLVED
    assert [b.blocker_id for b in await store.open_blockers(TENANT, ZOE, DAY)] == ["blk-chk11"]


async def test_one_failed_merge_notice_does_not_stop_the_pass() -> None:
    store = await checkout_slice()
    service = replace(_service(store), chat_provider=_ResolvedNoticeFails())
    first = await store.create(
        _request("xreq-a", NOAH, "Noah Weber", "review storefront-web !1 for CHK-8")
    )
    second = await store.create(
        replace(
            _request("xreq-b", OMAR, "Omar Haddad", "approve checkout-api !1"),
            requester_id="U-liam",
            requester_chat_ref="U-liam",
        )
    )
    await _merged(store, "acme/storefront-web", "1", "CHK-8 Payment form validation UI")
    await _merged(store, "acme/checkout-api", "1", "CHK-3 Payment intent API")

    resolved = await service.settle_merged_work(TENANT)

    assert {request.id for request in resolved} == {first.id, second.id}
    assert [b.blocker_id for b in await store.open_blockers(TENANT, ZOE, DAY)] == ["blk-chk11"]


def test_which_request_a_blocker_waits_on() -> None:
    chk8 = zoe_blocker("a", CHK8_BLOCKER, work_item_id="CHK-8")
    chk11 = zoe_blocker("b", CHK11_BLOCKER, work_item_id="CHK-17")
    to_noah = _request("n", NOAH, "Noah Weber", "review storefront-web !1 for CHK-8")
    to_omar = _request("o", OMAR, "Omar Haddad", "Complete HTTP client upgrade for CHK-17")
    vague = _request("v", NOAH, "Noah Weber", "take a look please")

    assert request_match_score(chk8, to_noah) == 3
    assert request_match_score(chk11, to_omar) == 3
    assert request_match_score(chk8, to_omar) == 0
    assert request_match_score(chk11, to_noah) == 0
    # Naming the person is enough only where neither names its issue.
    assert request_match_score(chk8, vague) == 1
    # Someone else's blocker never matches.
    assert request_match_score(replace(chk8, developer_id=OMAR), to_noah) == 0


# --- helpers --------------------------------------------------------------------


class _ResolvedNoticeFails(FakeChatProvider):
    async def send_dm(self, user: ChatUserRef, message: OutboundMessage) -> str:
        if message.metadata.get("purpose") == "cross_person_request_resolved":
            raise RuntimeError("chat down")
        return await super().send_dm(user, message)


def _service(store: InMemoryGraphStore) -> CrossPersonRequestService:
    return CrossPersonRequestService(
        repository=store,
        chat_provider=FakeChatProvider(),
        directory_repository=InMemoryDirectoryUserRepository(store),
        time_series_repository=store,
        blocker_settlement=BlockerSettlement(store),
        clock=lambda: APPROVED_AT,
    )


async def _rollup(store: InMemoryGraphStore) -> dict[str, Rag]:
    tree = await store.get_program_tree(TENANT, "program-platform", DAY)
    statuses = await RollupService(
        store, blocker_resolution=BlockerResolutionService(store, store)
    ).compute(tree, DAY)
    return {status.entity_ref.id: status.rag for status in statuses}


async def _row(store: InMemoryGraphStore, blocker_id: str):  # noqa: ANN202
    rows = [b for b in store._developer_blockers.values() if b.blocker_id == blocker_id]
    assert len(rows) == 1
    return rows[0]


def _request(
    request_id: str,
    counterpart: str,
    name: str,
    note: str,
    kind: str = "review",
) -> CrossPersonRequest:
    return CrossPersonRequest(
        tenant_id=TENANT,
        id=request_id,
        requester_id=ZOE,
        requester_chat_ref=ZOE,
        counterpart_id=counterpart,
        counterpart_display_name=name,
        kind=CrossPersonRequestKind(kind),
        note=note,
        source_correlation_id="checkin-zoe-r3",
        status=CrossPersonRequestStatus.OPEN,
        created_at=datetime(2026, 10, 4, 0, 4, 10, tzinfo=UTC),
        updated_at=datetime(2026, 10, 4, 0, 4, 10, tzinfo=UTC),
        notify_message_id=f"msg-{request_id}",
        notify_correlation_id=f"xreq-{request_id}",
        task_ref=EntityRef(tenant_id=TENANT, kind=NodeKind.TASK, id="CHK-8")
        if "CHK-8" in note
        else None,
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


async def _merged(store: InMemoryGraphStore, repo: str, number: str, title: str) -> None:
    await _merge_request(store, repo, number, title, merged=True)


async def _open(store: InMemoryGraphStore, repo: str, number: str, title: str) -> None:
    await _merge_request(store, repo, number, title, merged=False)


async def _merge_request(
    store: InMemoryGraphStore, repo: str, number: str, title: str, *, merged: bool
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
                "merged": merged,
                "state": "merged" if merged else "opened",
                "web_url": f"http://gitlab.local/{repo}/-/merge_requests/{number}",
            },
            observed_at=MERGED_AT,
            correlation_id=f"vcs:pull_request:{TENANT}:{repo}:{number}",
        )
    )
