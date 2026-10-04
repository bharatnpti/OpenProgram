"""A blocker cleared outside a check-in is marked cleared in the person's summary (N34).

R4: storefront-web !1 had merged and platform-libs !1 (CHK-17) merged at 06:06;
the 06:15:04 merge pass resolved Zoe's request to Omar and her CHK-11 wait and
took it out of her blockers. Her summary still read "CHK-11 is still blocked on
CHK-17" twice, so Ask, the digest and her person view contradicted the green.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

from core.application.blocker_settlement import (
    BlockerSettlement,
    cleared_by_merge,
    summary_with_cleared,
)
from core.application.cross_person_service import CrossPersonRequestService
from core.domain.blockers import BlockerResolutionReason, DeveloperBlocker
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestStatus,
)
from core.domain.graph import EntityRef, FactEvent, NodeKind
from core.domain.messaging import ChatUserRef, InboundMessage
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

R4_STATED = datetime(2026, 10, 4, 6, 4, 43, tzinfo=UTC)
PLATFORM_LIBS_MERGED = datetime(2026, 10, 4, 6, 6, 26, tzinfo=UTC)
PASS_AT = datetime(2026, 10, 4, 6, 15, 4, tzinfo=UTC)
# Zoe's stored R4 summary, as developer_statuses held it after the 06:15 pass.
ZOE_R4_SUMMARY = (
    "CHK-8 is merged and done from their side. CHK-11 is still blocked on CHK-17. "
    "CHK-12 is being picked up today with an MR expected shortly. CHK-12 is being worked "
    "on with an ETA for review by end of day tomorrow. CHK-11 is still blocked on CHK-17 "
    "and has no ETA."
)
ZOE_R4_SUMMARY_CLEARED = (
    "CHK-8 is merged and done from their side. CHK-11 is still blocked on CHK-17 "
    "(cleared: CHK-17 merged). CHK-12 is being picked up today with an MR expected "
    "shortly. CHK-12 is being worked on with an ETA for review by end of day tomorrow. "
    "CHK-11 is still blocked on CHK-17 and has no ETA (cleared: CHK-17 merged)."
)


async def test_zoes_r4_summary_says_the_chk17_wait_cleared_once_platform_libs_1_merges() -> None:
    store = await _zoe_after_r4()
    service, chat = _service(store)
    await store.create(_to_omar())
    await _merge_request(store, "acme/platform-libs", "1", "CHK-17 Upgrade shared HTTP client")

    await service.settle_merged_work(TENANT)

    status = await store.latest_developer_status(TENANT, ZOE, DAY)
    assert status is not None
    assert status.blockers == ()
    assert status.summary == ZOE_R4_SUMMARY_CLEARED
    assert await store.open_blockers(TENANT, ZOE, DAY) == []
    # Zoe is told about her request once, as before; the summary adds no message.
    assert [m.metadata["purpose"] for m in chat.sent] == ["cross_person_request_resolved"]

    # The next sync's pass changes nothing, and marks nothing twice.
    await service.settle_merged_work(TENANT)
    again = await store.latest_developer_status(TENANT, ZOE, DAY)
    assert again is not None and again.summary == ZOE_R4_SUMMARY_CLEARED


async def test_the_merge_pass_marks_it_the_same_with_no_request_raised() -> None:
    store = await _zoe_after_r4()
    service, _ = _service(store)
    await _merge_request(store, "acme/platform-libs", "1", "CHK-17 Upgrade shared HTTP client")

    await service.settle_merged_work(TENANT)

    status = await store.latest_developer_status(TENANT, ZOE, DAY)
    assert status is not None
    assert (status.blockers, status.summary) == ((), ZOE_R4_SUMMARY_CLEARED)


async def test_a_reply_marks_only_the_wait_it_ended_and_keeps_the_one_still_open() -> None:
    store = await checkout_slice()
    status = await store.latest_developer_status(TENANT, ZOE, DAY)
    assert status is not None
    await store.record_developer_status(
        replace(
            status,
            summary="CHK-8 is waiting on Noah's review of storefront-web !1. "
            "CHK-11 is still blocked on CHK-17.",
        )
    )
    service, _ = _service(store)
    to_noah = await store.create(
        _request("xreq-6286", NOAH, "Noah Weber", "review storefront-web !1 for CHK-8")
    )

    await service.handle_counterpart_reply(_reply(to_noah, NOAH, "approved"), to_noah)

    status = await store.latest_developer_status(TENANT, ZOE, DAY)
    assert status is not None
    assert status.blockers == (CHK11_BLOCKER,)
    assert status.summary == (
        "CHK-8 is waiting on Noah's review of storefront-web !1 "
        "(cleared: review request to Noah Weber resolved). CHK-11 is still blocked on CHK-17."
    )


async def test_a_summary_written_on_an_earlier_day_keeps_its_words() -> None:
    store = await checkout_slice()
    oct3 = date(2026, 10, 3)
    del store._developer_statuses[(TENANT, ZOE, DAY)]
    said = "CHK-11 is waiting on Omar's HTTP client upgrade (CHK-17)."
    status = await _status(store, NOAH)
    await store.record_developer_status(
        replace(status, developer_id=ZOE, as_of=oct3, blockers=(CHK11_BLOCKER,), summary=said)
    )
    for row in list(store._developer_blockers.values()):
        if row.developer_id == ZOE:
            store._developer_blockers[(TENANT, row.blocker_id)] = replace(row, last_seen_on=oct3)
    service, _ = _service(store)
    await _merge_request(store, "acme/platform-libs", "1", "CHK-17 Upgrade shared HTTP client")

    await service.settle_merged_work(TENANT)

    kept = await store.latest_developer_status(TENANT, ZOE, DAY)
    assert kept is not None and kept.as_of == oct3
    # The flat-status fallback must not revive it, so the blocker leaves the
    # list as before; the day's own words stay as they were said.
    assert kept.blockers == ()
    assert kept.summary == said


async def test_a_summary_that_never_states_the_blocker_says_so_at_the_end() -> None:
    store = await _zoe_after_r4(summary="Update recorded.")
    service, _ = _service(store)
    await _merge_request(store, "acme/platform-libs", "1", "CHK-17 Upgrade shared HTTP client")

    await service.settle_merged_work(TENANT)

    status = await store.latest_developer_status(TENANT, ZOE, DAY)
    assert status is not None
    assert status.summary == (
        "Update recorded. CHK-11 is waiting on Omar's HTTP client upgrade (CHK-17) "
        "(cleared: CHK-17 merged)."
    )


def test_a_sentence_is_marked_only_for_what_sets_the_cleared_blocker_apart() -> None:
    cleared = _blocker("a", "CHK-11 is waiting on CHK-17.", "CHK-17")
    still_open = "CHK-11 is waiting on the design sign-off."
    summary = (
        "CHK-11 is blocked on CHK-17. CHK-11 is also blocked on the design sign-off. "
        "CHK-17 is Omar's work."
    )

    marked = summary_with_cleared(
        summary, [(cleared, "CHK-17 merged")], still_open=(still_open,), listed={"a"}
    )

    assert marked == (
        "CHK-11 is blocked on CHK-17 (cleared: CHK-17 merged). CHK-11 is also blocked on "
        "the design sign-off. CHK-17 is Omar's work."
    )
    # Marking again adds nothing.
    assert (
        summary_with_cleared(
            marked, [(cleared, "CHK-17 merged")], still_open=(still_open,), listed={"a"}
        )
        == marked
    )


def test_a_quoted_blocker_is_marked_where_the_carried_note_quotes_it() -> None:
    cleared = _blocker("a", CHK8_BLOCKER, "CHK-8")
    summary = (
        "ETA for CHK-11 is one day after CHK-17 merges. Prior blockers carried forward "
        f"until explicitly resolved: {CHK8_BLOCKER}"
    )

    marked = summary_with_cleared(summary, [(cleared, "CHK-8 merged")], listed={"a"})

    assert marked == f"{summary.removesuffix('.')} (cleared: CHK-8 merged)."


def test_how_a_merge_ended_the_wait_names_the_issue_else_the_merge_request() -> None:
    platform_libs = _fact("acme/platform-libs", "1", "CHK-17 Upgrade shared HTTP client")
    storefront = _fact("acme/storefront-web", "1", "Payment form validation")

    assert cleared_by_merge({"CHK-11", "CHK-17"}, [platform_libs]) == "CHK-17 merged"
    assert cleared_by_merge({"CHK-8"}, [storefront]) == "storefront-web !1 merged"
    assert (
        cleared_by_merge(set(), [platform_libs, storefront])
        == "platform-libs !1 and storefront-web !1 merged"
    )


# --- helpers --------------------------------------------------------------------


async def _zoe_after_r4(*, summary: str = ZOE_R4_SUMMARY) -> InMemoryGraphStore:
    """Zoe after her R4 reply: CHK-8 resolved at 03:30, the CHK-11 wait stated at 06:04."""
    store = await checkout_slice(stated_at=R4_STATED)
    chk8 = zoe_blocker("blk-chk8", CHK8_BLOCKER, work_item_id="CHK-8")
    store._developer_blockers[(TENANT, "blk-chk8")] = replace(
        chk8,
        resolved_on=DAY,
        resolved_reason=BlockerResolutionReason.REPORTED_RESOLVED,
        updated_at=datetime(2026, 10, 4, 3, 30, 2, tzinfo=UTC),
    )
    status = await _status(store, ZOE)
    await store.record_developer_status(replace(status, blockers=(CHK11_BLOCKER,), summary=summary))
    return store


async def _status(store: InMemoryGraphStore, developer_id: str):  # noqa: ANN202
    status = await store.latest_developer_status(TENANT, developer_id, DAY)
    assert status is not None
    return status


def _service(store: InMemoryGraphStore) -> tuple[CrossPersonRequestService, FakeChatProvider]:
    chat = FakeChatProvider()
    service = CrossPersonRequestService(
        repository=store,
        chat_provider=chat,
        directory_repository=InMemoryDirectoryUserRepository(store),
        time_series_repository=store,
        blocker_settlement=BlockerSettlement(store),
        clock=lambda: PASS_AT,
    )
    return service, chat


def _to_omar() -> CrossPersonRequest:
    return _request(
        "xreq-d78d", OMAR, "Omar Haddad", "Complete HTTP client upgrade for CHK-17", "dependency"
    )


def _request(
    request_id: str,
    counterpart: str,
    name: str,
    note: str,
    kind: str = "review",
) -> CrossPersonRequest:
    at = datetime(2026, 10, 3, 18, 3, 44, tzinfo=UTC)
    return CrossPersonRequest(
        tenant_id=TENANT,
        id=request_id,
        requester_id=ZOE,
        requester_chat_ref=ZOE,
        counterpart_id=counterpart,
        counterpart_display_name=name,
        kind=CrossPersonRequestKind(kind),
        note=note,
        source_correlation_id="checkin-zoe-r2",
        status=CrossPersonRequestStatus.OPEN,
        created_at=at,
        updated_at=at,
        notify_message_id=f"msg-{request_id}",
        notify_correlation_id=f"xreq-{request_id}",
    )


def _reply(request: CrossPersonRequest, counterpart: str, text: str) -> InboundMessage:
    return InboundMessage(
        tenant_id=TENANT,
        user=ChatUserRef(tenant_id=TENANT, external_id=counterpart),
        text=text,
        thread_id=request.notify_message_id or "",
        message_id=f"m-{request.id}-{text}",
        correlation_id=request.notify_correlation_id or "",
        received_at=PASS_AT,
    )


def _blocker(blocker_id: str, description: str, work_item_id: str) -> DeveloperBlocker:
    return replace(
        zoe_blocker(blocker_id, description, work_item_id=work_item_id),
        resolved_on=DAY,
        resolved_reason=BlockerResolutionReason.REPORTED_RESOLVED,
    )


def _fact(repo: str, number: str, title: str) -> FactEvent:
    return FactEvent(
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
        observed_at=PLATFORM_LIBS_MERGED,
        correlation_id=f"vcs:pull_request:{TENANT}:{repo}:{number}",
    )


async def _merge_request(store: InMemoryGraphStore, repo: str, number: str, title: str) -> None:
    await store.append_fact(_fact(repo, number, title))
