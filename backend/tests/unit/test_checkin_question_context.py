"""The check-in question is composed from today's facts, not yesterday's status (N32).

R4 live: Zoe was asked about "CHK-8 (waiting on Noah's review)" at 06:00,
although storefront-web !1 had merged at 00:05 and the blocker had resolved
at 03:30. The context listed the last status's blocker strings, its "carried
forward" note, and an older fact of !1 that still said it was open.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.application.status_collector import StatusCollector
from core.domain.blockers import (
    BlockerResolutionReason,
    BlockerSource,
    DeveloperBlocker,
    normalize_blocker_key,
)
from core.domain.graph import EntityRef, FactEvent, NodeKind
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.status import DeveloperStatus, StatusSource
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_status_collector import SequenceLlmProvider

_TENANT = "demo"
_ZOE = "U-zoe"
_CHK_8_BLOCKER = (
    "CHK-8 payment form validation is done on storefront-web !1, but it's blocked until "
    "Noah reviews it, nobody has looked at it yet. (CHK-8)"
)
_CHK_11_BLOCKER = "CHK-11 is waiting on Omar's HTTP client upgrade (CHK-17)."


def _blocker(description: str, work_item: str, *, resolved: bool) -> DeveloperBlocker:
    today = datetime.now(tz=UTC).date()
    return DeveloperBlocker(
        tenant_id=_TENANT,
        blocker_id=f"blocker-{work_item}",
        developer_id=_ZOE,
        description=description,
        normalized_key=normalize_blocker_key(description),
        work_item_id=work_item,
        source=BlockerSource.CHECKIN,
        first_seen_on=today - timedelta(days=1),
        last_seen_on=today,
        resolved_on=today if resolved else None,
        resolved_reason=BlockerResolutionReason.REPORTED_RESOLVED if resolved else None,
    )


def _merge_request(pr_id: str, key: str, *, state: str, hours_ago: int) -> FactEvent:
    return FactEvent(
        tenant_id=_TENANT,
        source="vcs_pull_request",
        entity_ref=EntityRef(tenant_id=_TENANT, kind=NodeKind.DEVELOPER, id=_ZOE),
        payload={
            "repo": "acme/storefront-web",
            "id": pr_id,
            "source_branch": f"{key}-work",
            "title": f"{key} work",
            "state": state,
            "merged": state == "merged",
            "draft": False,
            "web_url": f"http://git.test/acme/storefront-web/-/merge_requests/{pr_id}",
        },
        observed_at=datetime.now(tz=UTC) - timedelta(hours=hours_ago),
        correlation_id=f"mr-{pr_id}-{state}",
    )


async def _zoe_at_six() -> str:
    today = datetime.now(tz=UTC).date()
    zoe = UserRef(tenant_id=_TENANT, external_id=_ZOE)
    tracker = FakeIssueTracker(
        issues={
            key: Issue(tenant_id=_TENANT, key=key, title=title, state=state, assignee=zoe)
            for key, title, state in (
                ("CHK-8", "Payment form validation UI", IssueState.IN_PROGRESS),
                ("CHK-11", "Cart price breakdown", IssueState.IN_PROGRESS),
                ("CHK-12", "Promo code validation", IssueState.TODO),
            )
        }
    )
    store = InMemoryGraphStore()
    # R3's confirmed status, with both blockers and the carried-forward note.
    await store.record_developer_status(
        DeveloperStatus(
            tenant_id=_TENANT,
            developer_id=_ZOE,
            as_of=today - timedelta(days=1),
            source=StatusSource.CONFIRMED,
            blockers=(_CHK_11_BLOCKER, _CHK_8_BLOCKER),
            summary=(
                "ETA for CHK-11 is one day after CHK-17 merges. Prior blockers carried "
                f"forward until explicitly resolved: {_CHK_8_BLOCKER}."
            ),
        )
    )
    # The merge pass resolved the CHK-8 blocker; CHK-11's wait is still open.
    await store.record_developer_blockers(
        _TENANT,
        [
            _blocker(_CHK_8_BLOCKER, "CHK-8", resolved=True),
            _blocker(_CHK_11_BLOCKER, "CHK-17", resolved=False),
        ],
    )
    for fact in (
        _merge_request("1", "CHK-8", state="open", hours_ago=12),
        _merge_request("1", "CHK-8", state="merged", hours_ago=6),
        _merge_request("2", "CHK-11", state="open", hours_ago=40),
    ):
        await store.append_fact(fact)
    collector = StatusCollector(
        issue_tracker=tracker,
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(texts=[]),
        status_repository=store,
        conversation_repository=store,
        time_series_repository=store,
        graph_repository=store,
        model="test-model",
    )
    return await collector.build_context(
        tenant_id=_TENANT, developer_id=_ZOE, developer_name="Zoe Almeida"
    )


async def test_resolved_blocker_and_carried_forward_note_are_not_in_the_context() -> None:
    context = await _zoe_at_six()

    assert "Noah" not in context
    assert "carried forward" not in context
    assert _CHK_11_BLOCKER in context
    assert "ETA for CHK-11 is one day after CHK-17 merges." in context


async def test_a_merged_merge_request_reads_merged_never_open() -> None:
    context = await _zoe_at_six()
    lines = context.splitlines()

    chk_8 = next(line for line in lines if line.startswith("Active issue CHK-8:"))
    assert "merge request acme/storefront-web !1 merged" in chk_8
    chk_11 = next(line for line in lines if line.startswith("Active issue CHK-11:"))
    assert "merge request acme/storefront-web !2 open" in chk_11
    # The older fact that said !1 was open is not shown, nor the merged one.
    request_lines = [line for line in lines if "source=vcs_pull_request" in line]
    assert len(request_lines) == 1
    assert "id=2" in request_lines[0]
