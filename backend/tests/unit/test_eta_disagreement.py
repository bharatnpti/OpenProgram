"""Different ETAs for one issue on one day are flagged where drift shows (N23).

R3 live: Ira's scrum-master summary said CHK-4 would be ready for review on
Friday, while its owner Liam said Tuesday. Both were stored and nothing
flagged the mismatch.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from core.application.blocker_resolution import BlockerResolutionService
from core.application.checkin_drift import IssueEta, issue_eta
from core.application.risk_service import RiskService
from core.application.status_collector import StatusCollector
from core.domain.graph import Developer, EdgeKind, EntityRef, GraphEdge, NodeKind, Project, Task
from core.domain.messaging import ChatUserRef, InboundMessage
from core.domain.risk import DriftFinding, DriftFindingKind, RiskProviderConfig
from core.domain.rollup import Rag
from core.domain.status import CheckIn, IssueClaim
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import FakeChatProvider, FakeIssueTracker
from tests.unit.test_status_collector import SequenceLlmProvider

_TENANT = "demo"
_DAY = date(2026, 10, 4)  # R3, a Sunday
_LIAM = "U-liam"
_IRA = "U-ira"


def _evaluation(claims: list[dict[str, object]], *, eta_change_days: int | None) -> str:
    return json.dumps(
        {
            "is_status_update": True,
            "sufficient": True,
            "question": None,
            "signals": {
                "progress_note": "Status.",
                "blockers": [],
                "eta_change_days": eta_change_days,
                "blockers_answered": True,
                "eta_answered": True,
                "issue_updates": claims,
            },
        }
    )


# What R3 stored for each of them (checkins.signals, 2026-10-04).
_LIAM_R3 = _evaluation(
    [
        {
            "issue_key": "CHK-4",
            "claimed_done": False,
            "claimed_state": "in progress",
            "note": "Should be ready for review by Tuesday",
        }
    ],
    eta_change_days=None,
)
_IRA_R3 = _evaluation(
    [
        {
            "issue_key": "CHK-4",
            "claimed_done": False,
            "claimed_state": "ready for review by end of week",
            "note": "Targeting Friday afternoon for review readiness.",
        }
    ],
    eta_change_days=3,
)


async def _seed(store: InMemoryGraphStore) -> None:
    await store.upsert_node(Project(tenant_id=_TENANT, id="proj-chk", name="Checkout"))
    await store.upsert_node(
        Task(
            tenant_id=_TENANT,
            id="CHK-4",
            name="Step-up flow",
            metadata={"key": "CHK-4", "state": "in_progress", "status": "In Progress"},
        )
    )
    await store.upsert_node(Developer(tenant_id=_TENANT, id=_LIAM, name="Liam Chen"))
    await store.upsert_node(Developer(tenant_id=_TENANT, id=_IRA, name="Ira Novak"))
    await store.add_edge(
        GraphEdge(
            tenant_id=_TENANT, from_node_id="proj-chk", to_node_id="CHK-4", kind=EdgeKind.CONTAINS
        )
    )
    await store.add_edge(
        GraphEdge(
            tenant_id=_TENANT, from_node_id=_LIAM, to_node_id="CHK-4", kind=EdgeKind.ASSIGNED_TO
        )
    )
    for developer in (_LIAM, _IRA):
        await store.record_checkin(
            CheckIn(
                tenant_id=_TENANT,
                developer_id=developer,
                correlation_id=f"corr-{developer}",
                asked_at=datetime(2026, 10, 4, 0, 0, tzinfo=UTC),
                replied_at=None,
                raw_reply=None,
                signals=None,
            )
        )


async def _check_in(
    store: InMemoryGraphStore, developer: str, evaluation: str, minute: int
) -> None:
    collector = StatusCollector(
        issue_tracker=FakeIssueTracker(),
        chat_provider=FakeChatProvider(),
        llm_provider=SequenceLlmProvider(texts=[evaluation]),
        status_repository=store,
        time_series_repository=store,
        conversation_repository=store,
        graph_repository=store,
        model="test-model",
        checkin_ack_enabled=False,
    )
    outcome = await collector.handle_reply(
        InboundMessage(
            tenant_id=_TENANT,
            user=ChatUserRef(tenant_id=_TENANT, external_id=developer),
            text="status",
            thread_id=f"thread-{developer}",
            message_id=f"msg-{developer}",
            correlation_id=f"corr-{developer}",
            received_at=datetime(2026, 10, 4, 0, minute, tzinfo=UTC),
        )
    )
    assert outcome.kind == "processed"


async def _drift(store: InMemoryGraphStore) -> list[DriftFinding]:
    service = RiskService(
        graph_repository=store,
        time_series_repository=store,
        status_repository=store,
        blocker_resolution=BlockerResolutionService(store, store),
        rollup_repository=store,
        provider_config=RiskProviderConfig(),
    )
    return await service.project_drift(_TENANT, "proj-chk", _DAY)


async def test_owner_and_scrum_master_etas_for_chk_4_disagree() -> None:
    store = InMemoryGraphStore()
    await _seed(store)

    await _check_in(store, _LIAM, _LIAM_R3, 6)
    await _check_in(store, _IRA, _IRA_R3, 8)
    findings = await _drift(store)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind is DriftFindingKind.ETA_DISAGREEMENT
    assert finding.severity is Rag.AMBER
    assert finding.entity_ref == EntityRef(tenant_id=_TENANT, kind=NodeKind.TASK, id="CHK-4")
    assert finding.owner_id == _LIAM
    assert finding.reason == (
        "ETAs disagree for CHK-4: Liam Chen (owner) said Tuesday, Oct 6; "
        "Ira Novak said Friday, Oct 9. The owner's ETA is the one used."
    )
    # Each person's own status keeps their own ETA: Ira's never reaches Liam's.
    liam = await store.latest_developer_status(_TENANT, _LIAM, _DAY)
    ira = await store.latest_developer_status(_TENANT, _IRA, _DAY)
    assert liam is not None and liam.eta_change_days is None
    assert ira is not None and ira.eta_change_days == 3


async def test_owner_is_named_first_whoever_answered_first() -> None:
    store = InMemoryGraphStore()
    await _seed(store)

    await _check_in(store, _IRA, _IRA_R3, 5)
    await _check_in(store, _LIAM, _LIAM_R3, 9)
    findings = await _drift(store)

    assert [finding.reason for finding in findings] == [
        "ETAs disagree for CHK-4: Liam Chen (owner) said Tuesday, Oct 6; "
        "Ira Novak said Friday, Oct 9. The owner's ETA is the one used."
    ]


async def test_etas_that_mean_the_same_day_do_not_disagree() -> None:
    store = InMemoryGraphStore()
    await _seed(store)
    liam_friday = _evaluation(
        [
            {
                "issue_key": "CHK-4",
                "claimed_done": False,
                "claimed_state": "in progress",
                "note": "Ready for review Friday",
            }
        ],
        eta_change_days=None,
    )
    ira_end_of_week = _evaluation(
        [
            {
                "issue_key": "CHK-4",
                "claimed_done": False,
                "claimed_state": "in progress",
                "note": "Liam expects it by end of week",
            }
        ],
        eta_change_days=None,
    )

    await _check_in(store, _LIAM, liam_friday, 6)
    await _check_in(store, _IRA, ira_end_of_week, 8)

    assert await _drift(store) == []


async def test_one_person_alone_never_disagrees() -> None:
    store = InMemoryGraphStore()
    await _seed(store)

    await _check_in(store, _LIAM, _LIAM_R3, 6)

    assert await _drift(store) == []


@pytest.mark.parametrize(
    ("note", "state", "expected"),
    [
        (
            "Should be ready for review by Tuesday",
            "in progress",
            IssueEta(label="Tuesday", day=date(2026, 10, 6)),
        ),
        (
            "Targeting Friday afternoon for review readiness.",
            "ready for review by end of week",
            IssueEta(label="Friday", day=date(2026, 10, 9)),
        ),
        ("Should wrap up today", "in progress", IssueEta(label="today", day=_DAY)),
        ("Done tomorrow", None, IssueEta(label="tomorrow", day=date(2026, 10, 5))),
        ("", "ready by end of the week", IssueEta(label="end of week", day=date(2026, 10, 9))),
        ("Next Monday", None, IssueEta(label="next Monday", day=date(2026, 10, 12))),
        ("ETA Oct 14", None, IssueEta(label="Oct 14", day=date(2026, 10, 14))),
        ("due 2026-10-20", None, IssueEta(label="Oct 20", day=date(2026, 10, 20))),
        ("Noah approved checkout-api !1, waiting on Asha to merge", "approved", None),
        ("We decided 5 things", None, None),
        # A day that does not look ahead is no ETA.
        ("Started today", "in progress", None),
        ("Reviewed on Monday", "in review", None),
        # Nor is a day on work said to be done.
        ("Merged Monday, will close it tomorrow", "merged", None),
    ],
)
def test_issue_eta_reads_the_day_a_claim_names(
    note: str, state: str | None, expected: IssueEta | None
) -> None:
    claim = IssueClaim(issue_key="CHK-4", claimed_state=state, note=note)

    assert issue_eta(claim, _DAY) == expected
