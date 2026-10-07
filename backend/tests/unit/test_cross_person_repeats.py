"""The same ask, made again round after round, is one request (N11).

In the QA rounds every check-in restated what was still pending, and each
restatement opened a new request: Mina asked Asha about CHK-10 in R1 and again
in R2, so Asha was DMed twice and both copies stayed open. Zoe's R2 ask of Noah
stayed acknowledged after the R3 copy was resolved, and Liam's R2 "approve
CHK-3" stayed open although checkout-api !1 was merged. None was linked to its
issue.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from core.application.cross_person_service import CrossPersonRequestService
from core.application.sync_services import SyncRunResult
from core.domain.cross_person import (
    CrossPersonRequest,
    CrossPersonRequestKind,
    CrossPersonRequestResolution,
    CrossPersonRequestStatus,
    MergeRequestRef,
    is_repeat_of,
    merge_request_refs_in,
    request_subject,
    same_subject,
    subject_of_text,
)
from core.domain.directory import DirectoryUser
from core.domain.graph import EntityRef, FactEvent, JsonScalar, NodeKind
from core.domain.integrations import SyncCursor
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.status import CrossPersonMention
from infra.persistence.in_memory_graph import InMemoryDirectoryUserRepository, InMemoryGraphStore
from infra.workflows import git_sync
from tests.contract.fakes import FakeChatProvider

R1 = datetime(2026, 10, 3, 12, 16, tzinfo=UTC)
R2 = datetime(2026, 10, 3, 18, 4, tzinfo=UTC)
R3 = datetime(2026, 10, 4, 0, 4, tzinfo=UTC)

MINA, ASHA, ZOE, NOAH, LIAM, OMAR = (
    "U-mina",
    "U-asha",
    "U-zoe",
    "U-noah",
    "U-liam",
    "U-omar",
)
_NAMES = {
    MINA: "Mina Park",
    ASHA: "Asha Rao",
    ZOE: "Zoe Almeida",
    NOAH: "Noah Weber",
    LIAM: "Liam Chen",
    OMAR: "Omar Haddad",
}


# --- what an ask is about ------------------------------------------------------


def test_an_ask_names_its_issue_and_its_merge_requests() -> None:
    subject = subject_of_text("approval needed for checkout-api !1 and acme/storefront-web !1")

    assert subject.merge_requests == {
        MergeRequestRef(repo="checkout-api", number="1"),
        MergeRequestRef(repo="storefront-web", number="1"),
    }
    assert subject_of_text("Review storefront-web !1 for CHK-8").issue_keys == {"CHK-8"}
    # A bare number, or one after an everyday word, names no repository.
    assert merge_request_refs_in("review !3") == {MergeRequestRef(repo=None, number="3")}
    assert merge_request_refs_in("merge !1") == {MergeRequestRef(repo=None, number="1")}
    assert merge_request_refs_in("checkout-api MR !3") == {
        MergeRequestRef(repo="checkout-api", number="3")
    }
    # Prose that looks like a key is not one.
    assert subject_of_text("a follow-up on covid-19 numbers").issue_keys == frozenset()


def test_rounds_of_the_same_ask_are_the_same_subject_and_different_work_is_not() -> None:
    def same(first: str, second: str, *, vague: bool = False) -> bool:
        return same_subject(subject_of_text(first), subject_of_text(second), vague_matches=vague)

    # Mina -> Asha, R1 and R2: R1 named no issue, both are about the criteria.
    assert same(
        "Review of drafted acceptance criteria scheduled",
        "reviewing acceptance criteria for CHK-10",
    )
    # Noah -> Liam, R2 and R3; Zoe -> Noah, R2 and R3.
    assert same("review CHK-6 refund edge cases PR", "review CHK-6 on checkout-api !3")
    assert same("Review storefront-web !1 for CHK-8", "review storefront-web !1 for CHK-8")
    # Two issues, or two merge requests, are two asks.
    assert not same("review CHK-6", "review IDP-3")
    assert not same("review checkout-api !3", "review storefront-web !2")
    assert not same("review the API spec", "review the API client")
    # A vague ask is the same one only where that is asked for.
    assert not same("can you take a look?", "review CHK-6")
    assert same("can you take a look?", "review CHK-6", vague=True)


# --- a repeated ask refreshes the open request ----------------------------------


async def test_mina_asking_asha_again_refreshes_the_r1_request_and_dms_nobody() -> None:
    service, store, chat = await _service()

    first = await _ask(
        service, MINA, ASHA, "Review of drafted acceptance criteria scheduled", R1, "r1"
    )
    again = await _ask(service, MINA, ASHA, "reviewing acceptance criteria for CHK-10", R2, "r2")

    assert again.id == first.id
    assert [message.metadata["purpose"] for message in chat.sent] == ["cross_person_request"]
    stored = await store.list_for_requester("demo", MINA)
    assert [request.id for request in stored] == [first.id]
    assert stored[0].status is CrossPersonRequestStatus.OPEN
    assert stored[0].updated_at == R2
    # The R2 ask names the issue, so the request is linked to it now.
    assert stored[0].task_ref == EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="CHK-10")
    # Its note is still what Asha was told.
    assert stored[0].note == "Review of drafted acceptance criteria scheduled"


async def test_an_acknowledged_request_asked_again_stays_acknowledged() -> None:
    service, store, chat = await _service()
    first = await _ask(service, ZOE, NOAH, "Review storefront-web !1 for CHK-8", R2, "r2")
    await service.handle_counterpart_reply(_reply(NOAH, first, "on it", R2), first)

    again = await _ask(service, ZOE, NOAH, "review storefront-web !1 for CHK-8", R3, "r3")

    assert again.id == first.id
    assert again.status is CrossPersonRequestStatus.ACKNOWLEDGED
    assert len(await store.list_for_requester("demo", ZOE)) == 1
    assert len(chat.sent) == 1


async def test_a_new_request_is_linked_to_the_issue_it_names() -> None:
    service, _, _ = await _service()

    request = await _ask(service, ZOE, NOAH, "Review storefront-web !1 for CHK-8", R2, "r2")

    assert request.task_ref == EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="CHK-8")


async def test_asking_the_same_person_about_other_work_opens_another_request() -> None:
    service, store, chat = await _service()

    await _ask(service, NOAH, LIAM, "review CHK-6 on checkout-api !3", R3, "r3")
    await _ask(service, NOAH, LIAM, "review IDP-3 passkey enrolment", R3, "r3b")
    await _ask(service, NOAH, ASHA, "review CHK-6 on checkout-api !3", R3, "r3c")

    assert len(await store.list_for_requester("demo", NOAH)) == 3
    assert len(chat.sent) == 3


async def test_a_resolved_request_is_not_refreshed_by_a_new_ask() -> None:
    service, store, _ = await _service()
    first = await _ask(service, ZOE, NOAH, "review storefront-web !1 for CHK-8", R2, "r2")
    await service.handle_counterpart_reply(_reply(NOAH, first, "approved", R2), first)

    again = await _ask(service, ZOE, NOAH, "review storefront-web !1 for CHK-8", R3, "r3")

    assert again.id != first.id
    assert again.status is CrossPersonRequestStatus.OPEN
    assert len(await store.list_for_requester("demo", ZOE)) == 2


# --- a resolved request closes its copies ---------------------------------------


async def test_noah_approving_the_r3_copy_resolves_zoes_r2_copy_and_tells_her_once() -> None:
    service, store, chat = await _service()
    # The R2 and R3 copies, as they were recorded before asks were refreshed.
    r2 = await store.create(_stored("xreq-r2", ZOE, NOAH, "Review storefront-web !1 for CHK-8", R2))
    await store.update_status("demo", r2.id, CrossPersonRequestStatus.ACKNOWLEDGED, R2)
    r3 = await store.create(_stored("xreq-r3", ZOE, NOAH, "review storefront-web !1 for CHK-8", R3))
    other = await store.create(_stored("xreq-other", ZOE, NOAH, "review CHK-12 copy", R3))

    resolved = await service.handle_counterpart_reply(_reply(NOAH, r3, "approved", R3), r3)

    assert resolved.status is CrossPersonRequestStatus.RESOLVED
    copy = await store.get("demo", r2.id)
    assert copy is not None and copy.status is CrossPersonRequestStatus.RESOLVED
    untouched = await store.get("demo", other.id)
    assert untouched is not None and untouched.status is CrossPersonRequestStatus.OPEN
    told = [m for m in chat.sent if m.metadata["purpose"] == "cross_person_request_resolved"]
    assert len(told) == 1
    assert told[0].metadata["request_id"] == r3.id


async def test_resolving_from_the_console_also_closes_the_copies() -> None:
    service, store, _ = await _service()
    r1 = await store.create(
        _stored("xreq-r1", MINA, ASHA, "Review of drafted acceptance criteria scheduled", R1)
    )
    r2 = await store.create(
        _stored("xreq-r2", MINA, ASHA, "reviewing acceptance criteria for CHK-10", R2)
    )

    await service.update_status("demo", r2.id, CrossPersonRequestStatus.RESOLVED, actor=ASHA)

    copy = await store.get("demo", r1.id)
    assert copy is not None and copy.status is CrossPersonRequestStatus.RESOLVED
    # The copy closed with the resolution, so its fact names who made that one.
    assert await _resolved_by(store) == {r1.id: ASHA, r2.id: ASHA}


# --- a merged merge request resolves the ask about it ---------------------------


async def test_liams_approve_chk3_resolves_once_checkout_api_1_is_merged() -> None:
    service, store, chat = await _service()
    liam = await store.create(
        _stored("xreq-liam", LIAM, NOAH, "review and approve CHK-3 for merge", R2, chat_ref=LIAM)
    )
    noah = await store.create(
        _stored("xreq-noah", NOAH, LIAM, "review CHK-6 on checkout-api !3", R3, chat_ref=NOAH)
    )
    zoe = await store.create(
        _stored("xreq-zoe", ZOE, OMAR, "Complete HTTP client upgrade for CHK-17", R2, chat_ref=ZOE)
    )
    await _merge_request(store, "acme/checkout-api", "1", "CHK-3 Payment intent API", merged=True)
    await _merge_request(store, "acme/checkout-api", "3", "CHK-6 Refund edge cases", merged=False)
    await _merge_request(store, "acme/platform-libs", "1", "CHK-17 Upgrade shared HTTP client")

    resolved = await service.resolve_merged_work("demo")

    assert [request.id for request in resolved] == [liam.id]
    still = {r.id: r.status for r in await store.list_open("demo")}
    assert still == {
        noah.id: CrossPersonRequestStatus.OPEN,
        zoe.id: CrossPersonRequestStatus.OPEN,
    }
    told = [m for m in chat.sent if m.metadata["purpose"] == "cross_person_request_resolved"]
    assert [m.text for m in told] == [
        "checkout-api !1 is merged, so I marked your review request to Noah Weber resolved: "
        "review and approve CHK-3 for merge"
    ]
    # A second pass finds nothing left to resolve and tells nobody again.
    assert await service.resolve_merged_work("demo") == ()
    assert len(chat.sent) == 1
    # The merge resolved it, not any member in OpenProgram.
    assert await _resolved_by(store) == {liam.id: None}


async def test_an_ask_about_a_named_merge_request_resolves_when_it_merges() -> None:
    service, store, chat = await _service()
    r2 = await store.create(
        _stored("xreq-r2", ZOE, NOAH, "Review storefront-web !1 for CHK-8", R2, chat_ref=ZOE)
    )
    await store.update_status("demo", r2.id, CrossPersonRequestStatus.ACKNOWLEDGED, R2)
    both = await store.create(
        _stored(
            "xreq-asha",
            ASHA,
            NOAH,
            "approval needed for checkout-api !1 and storefront-web !1",
            R3,
            chat_ref=ASHA,
        )
    )
    await _merge_request(
        store, "acme/storefront-web", "1", "CHK-8 Payment form validation UI", merged=True
    )
    await _merge_request(store, "acme/checkout-api", "1", "CHK-3 Payment intent API", merged=False)

    resolved = await service.resolve_merged_work("demo")

    assert [request.id for request in resolved] == [r2.id]
    still = await store.get("demo", both.id)
    assert still is not None and still.status is CrossPersonRequestStatus.OPEN
    assert "storefront-web !1 is merged" in chat.sent[0].text


async def test_a_copy_resolved_after_its_ask_was_announced_tells_nobody_again() -> None:
    service, store, chat = await _service()
    # Before the fix: Noah's reply resolved the R3 copy and Zoe was told; the
    # R2 copy stayed acknowledged. The merge then resolves the R2 copy.
    r2 = await store.create(
        _stored("xreq-r2", ZOE, NOAH, "Review storefront-web !1 for CHK-8", R2, chat_ref=ZOE)
    )
    await store.update_status("demo", r2.id, CrossPersonRequestStatus.ACKNOWLEDGED, R2)
    r3 = await store.create(
        _stored("xreq-r3", ZOE, NOAH, "review storefront-web !1 for CHK-8", R3, chat_ref=ZOE)
    )
    await store.update_status("demo", r3.id, CrossPersonRequestStatus.RESOLVED, R3)
    await _merge_request(
        store, "acme/storefront-web", "1", "CHK-8 Payment form validation UI", merged=True
    )

    resolved = await service.resolve_merged_work("demo")
    late = await service.handle_counterpart_reply(_reply(NOAH, r2, "approved", R3), r2)

    assert [request.id for request in resolved] == [r2.id]
    assert late.status is CrossPersonRequestStatus.RESOLVED
    assert chat.sent == []


async def test_an_ask_with_no_merge_request_is_left_to_its_people() -> None:
    service, store, _ = await _service()
    await store.create(
        _stored("xreq-mina", MINA, ASHA, "reviewing acceptance criteria for CHK-10", R2)
    )
    await _merge_request(store, "acme/checkout-api", "1", "CHK-3 Payment intent API", merged=True)

    assert await service.resolve_merged_work("demo") == ()


def test_a_request_is_a_repeat_only_of_the_same_requester_and_person() -> None:
    first = _stored("a", ZOE, NOAH, "review storefront-web !1", R2)
    assert is_repeat_of(replace(first, id="b"), first, vague_matches=False)
    assert not is_repeat_of(replace(first, id="b", requester_id=LIAM), first, vague_matches=False)
    assert not is_repeat_of(replace(first, id="b", counterpart_id=OMAR), first, vague_matches=False)
    assert not is_repeat_of(first, first, vague_matches=True)
    linked = replace(
        first,
        note="review it",
        task_ref=EntityRef(tenant_id="demo", kind=NodeKind.TASK, id="CHK-8"),
    )
    assert request_subject(linked).issue_keys == {"CHK-8"}


# --- helpers --------------------------------------------------------------------


async def _service() -> tuple[CrossPersonRequestService, InMemoryGraphStore, FakeChatProvider]:
    store = InMemoryGraphStore()
    directory = InMemoryDirectoryUserRepository(store)
    await directory.upsert_users(
        [
            DirectoryUser(tenant_id="demo", external_id=key, display_name=name)
            for key, name in _NAMES.items()
        ]
    )
    chat = FakeChatProvider()
    service = CrossPersonRequestService(
        repository=store,
        chat_provider=chat,
        directory_repository=directory,
        time_series_repository=store,
        clock=lambda: R1,
    )
    return service, store, chat


async def _ask(
    service: CrossPersonRequestService,
    requester: str,
    counterpart: str,
    note: str,
    at: datetime,
    round_id: str,
) -> CrossPersonRequest:
    created = await service.record_from_checkin(
        tenant_id="demo",
        requester_id=requester,
        requester_chat_ref=requester,
        source_correlation_id=f"checkin-{requester}-{round_id}",
        resolutions=(
            CrossPersonRequestResolution(
                mention=CrossPersonMention(raw_name=_NAMES[counterpart], kind="review", note=note),
                status=CrossPersonRequestStatus.OPEN,
                counterpart_id=counterpart,
                counterpart_display_name=_NAMES[counterpart],
            ),
        ),
        observed_at=at,
    )
    return created[0]


def _stored(
    request_id: str,
    requester: str,
    counterpart: str,
    note: str,
    at: datetime,
    *,
    chat_ref: str | None = None,
) -> CrossPersonRequest:
    return CrossPersonRequest(
        tenant_id="demo",
        id=request_id,
        requester_id=requester,
        requester_chat_ref=chat_ref or requester,
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
    )


def _reply(
    counterpart: str, request: CrossPersonRequest, text: str, at: datetime
) -> InboundMessage:
    return InboundMessage(
        tenant_id="demo",
        user=ChatUserRef(tenant_id="demo", external_id=counterpart),
        text=text,
        thread_id=request.notify_message_id or "",
        message_id=f"m-{request.id}-{text}",
        correlation_id=request.notify_correlation_id or "",
        received_at=at,
    )


async def _resolved_by(store: InMemoryGraphStore) -> dict[str, JsonScalar]:
    """Who each recorded resolution names as having made it, by request id."""
    facts = await store.list_recent_facts("demo", sources=("cross_person_request",))
    return {
        str(fact.payload["request_id"]): fact.payload["changed_by"]
        for fact in facts
        if fact.payload["transition"] == CrossPersonRequestStatus.RESOLVED.value
    }


async def _merge_request(
    store: InMemoryGraphStore,
    repo: str,
    number: str,
    title: str,
    *,
    merged: bool = False,
) -> None:
    at = datetime(2026, 10, 4, 0, 5, 39, tzinfo=UTC)
    await store.append_fact(
        FactEvent(
            tenant_id="demo",
            source="vcs_pull_request",
            entity_ref=EntityRef(tenant_id="demo", kind=NodeKind.REPO, id=repo),
            payload={
                "repo": repo,
                "id": number,
                "title": title,
                "source_branch": title.replace(" ", "-"),
                "merged": merged,
                "state": "merged" if merged else "open",
                "web_url": f"http://gitlab.local/{repo}/-/merge_requests/{number}",
            },
            observed_at=at,
            correlation_id=f"vcs:pull_request:demo:{repo}:{number}",
        )
    )


# --- the git sync settles merged work ---------------------------------------------


class _StubSync:
    async def sync_repo(self, **_: object) -> SyncRunResult:
        return SyncRunResult(
            connector="vcs", scope="repo:checkout-api", items_synced=1, cursor=SyncCursor()
        )


class _StubRequests:
    def __init__(self, *, fail: bool) -> None:
        self.fail = fail
        self.tenants: list[str] = []

    async def settle_merged_work(self, tenant_id: str) -> tuple[CrossPersonRequest, ...]:
        self.tenants.append(tenant_id)
        if self.fail:
            raise RuntimeError("chat down")
        return ()


class _StubRegistry:
    def __init__(self, requests: _StubRequests) -> None:
        self.requests = requests
        self.closed = False

    def vcs_read_sync_service(self) -> _StubSync:
        return _StubSync()

    def cross_person_request_service(self) -> _StubRequests:
        return self.requests

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("fail", [False, True])
async def test_each_repo_sync_settles_merged_work_and_never_fails_for_it(
    monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    requests = _StubRequests(fail=fail)
    registry = _StubRegistry(requests)
    monkeypatch.setattr(git_sync, "_service_registry", lambda: registry)

    result = await git_sync.sync_git_repo_activity(
        git_sync.GitSyncInput(tenant_id="demo", repo_name="checkout-api")
    )

    assert result.items_synced == 1
    assert requests.tenants == ["demo"]
    assert registry.closed
