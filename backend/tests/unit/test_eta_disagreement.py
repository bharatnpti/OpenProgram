"""Different ETAs for one issue on one day are flagged where drift shows (N23).

R3 live: Ira's scrum-master summary said CHK-4 would be ready for review on
Friday, while its owner Liam said Tuesday. Both were stored and nothing
flagged the mismatch.

Only people who own or actively work the issue are compared (N43): its assignee
and anyone whose own branch, commit or merge request names it. Until the N43
tests at the end, a commit of Ira's names CHK-4, so she works it and her ETA
counts; without such git activity her ETA, whatever her roles, is context and
never a disagreement.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from core.application.blocker_resolution import BlockerResolutionService
from core.application.checkin_drift import IssueEta, issue_eta
from core.application.risk_service import RiskService
from core.application.status_collector import StatusCollector
from core.domain.graph import (
    Developer,
    EdgeKind,
    EntityRef,
    FactEvent,
    GraphEdge,
    NodeKind,
    Project,
    Task,
)
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
_NOAH = "U-noah"
_MINA = "U-mina"


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


async def _works_on(
    store: InMemoryGraphStore, developer: str, issue_key: str, *, via: str = "commit"
) -> None:
    """Git activity the sync linked to ``developer`` that names ``issue_key`` (N43).

    ``via``: a commit message, or a merge request's source ``branch`` or ``title``.
    """
    author = EntityRef(tenant_id=_TENANT, kind=NodeKind.DEVELOPER, id=developer)
    observed_at = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
    ref = f"{developer}-{issue_key}"
    if via == "commit":
        await store.append_fact(
            FactEvent(
                tenant_id=_TENANT,
                source="vcs_commit",
                entity_ref=author,
                payload={"repo": "acme/app", "sha": f"sha-{ref}", "message": f"{issue_key}: wip"},
                observed_at=observed_at,
                correlation_id=f"vcs:commit:{_TENANT}:acme/app:sha-{ref}",
            )
        )
        return
    await store.append_fact(
        FactEvent(
            tenant_id=_TENANT,
            source="vcs_pull_request",
            entity_ref=author,
            payload={
                "repo": "acme/app",
                "id": ref,
                "title": f"{issue_key}: first cut" if via == "title" else "First cut",
                "merged": False,
                "state": "opened",
                "draft": False,
                "source_branch": f"feature/{issue_key}" if via == "branch" else "feature/wip",
            },
            observed_at=observed_at,
            correlation_id=f"vcs:pull_request:{_TENANT}:acme/app:{ref}",
        )
    )


async def _seed(store: InMemoryGraphStore, *, ira_works_on_chk_4: bool = True) -> None:
    if ira_works_on_chk_4:
        await _works_on(store, _IRA, "CHK-4")
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


def _risks(store: InMemoryGraphStore) -> RiskService:
    return RiskService(
        graph_repository=store,
        time_series_repository=store,
        status_repository=store,
        blocker_resolution=BlockerResolutionService(store, store),
        rollup_repository=store,
        provider_config=RiskProviderConfig(),
    )


async def _drift(store: InMemoryGraphStore, project_id: str = "proj-chk") -> list[DriftFinding]:
    return await _risks(store).project_drift(_TENANT, project_id, _DAY)


async def test_owner_and_teammate_etas_for_chk_4_disagree() -> None:
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


def _eta_claim(key: str, state: str | None, note: str) -> str:
    return _evaluation(
        [{"issue_key": key, "claimed_done": False, "claimed_state": state, "note": note}],
        eta_change_days=None,
    )


async def test_owner_early_next_week_and_teammate_end_of_week_disagree() -> None:
    # N31, R4 live: Ira said IDP-3 by end of week, its owner Noah "early next
    # week"; Noah's vague ETA was not stored, so nothing was compared. (A commit
    # of Ira's names IDP-3 here; without it her ETA is context only, N43.)
    store = InMemoryGraphStore()
    await _seed(store)
    await _works_on(store, _IRA, "IDP-3")
    await store.upsert_node(Project(tenant_id=_TENANT, id="proj-idp", name="Identity"))
    await store.upsert_node(
        Task(
            tenant_id=_TENANT,
            id="IDP-3",
            name="Passkey enrolment",
            metadata={"key": "IDP-3", "state": "in_progress", "status": "In Progress"},
        )
    )
    await store.upsert_node(Developer(tenant_id=_TENANT, id=_NOAH, name="Noah Weber"))
    for edge in (
        GraphEdge(
            tenant_id=_TENANT, from_node_id="proj-idp", to_node_id="IDP-3", kind=EdgeKind.CONTAINS
        ),
        GraphEdge(
            tenant_id=_TENANT, from_node_id=_NOAH, to_node_id="IDP-3", kind=EdgeKind.ASSIGNED_TO
        ),
    ):
        await store.add_edge(edge)
    await store.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=_NOAH,
            correlation_id=f"corr-{_NOAH}",
            asked_at=datetime(2026, 10, 4, 0, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )
    noah = _eta_claim(
        "IDP-3",
        "pending review",
        "Will close after identity-service !1 is reviewed, likely early next week",
    )
    ira = _eta_claim(
        "IDP-3",
        "should be approved and merged by end of week",
        "IDP-3 expected to be approved and merged by end of week.",
    )

    await _check_in(store, _NOAH, noah, 6)
    await _check_in(store, _IRA, ira, 8)
    findings = await _drift(store, "proj-idp")

    assert [finding.reason for finding in findings] == [
        "ETAs disagree for IDP-3: Noah Weber (owner) said early next week, Oct 12 to Oct 14; "
        "Ira Novak said end of week, Oct 9. The owner's ETA is the one used."
    ]
    assert findings[0].owner_id == _NOAH
    facts = await store.list_recent_facts(_TENANT, sources=("checkin_drift",))
    assert {
        (fact.payload["developer_id"], fact.payload["eta_start"], fact.payload["eta_date"])
        for fact in facts
    } == {(_NOAH, "2026-10-12", "2026-10-14"), (_IRA, "2026-10-09", "2026-10-09")}


async def test_overlapping_windows_do_not_disagree() -> None:
    store = InMemoryGraphStore()
    await _seed(store)

    await _check_in(store, _LIAM, _eta_claim("CHK-4", None, "Should land early next week"), 6)
    await _check_in(store, _IRA, _eta_claim("CHK-4", None, "Ready by next Tuesday"), 8)

    assert await _drift(store) == []


async def test_a_fact_stored_before_windows_is_one_day() -> None:
    store = InMemoryGraphStore()
    await _seed(store)
    await _check_in(store, _LIAM, _eta_claim("CHK-4", None, "Should land early next week"), 6)
    # What R4 stored for Ira (no eta_start).
    await store.append_fact(
        FactEvent(
            tenant_id=_TENANT,
            source="checkin_drift",
            entity_ref=EntityRef(tenant_id=_TENANT, kind=NodeKind.TASK, id="CHK-4"),
            payload={
                "kind": "eta_stated",
                "as_of": _DAY.isoformat(),
                "eta_date": "2026-10-09",
                "eta_label": "Friday",
                "issue_key": "CHK-4",
                "developer_id": _IRA,
                "developer_name": "Ira Novak",
            },
            observed_at=datetime(2026, 10, 4, 0, 8, tzinfo=UTC),
            correlation_id="corr-ira-r4",
        )
    )

    assert [finding.kind for finding in await _drift(store)] == [DriftFindingKind.ETA_DISAGREEMENT]


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
        # N31, R4 live: vague ETAs are windows from the check-in date (a Sunday,
        # so "this week" is the week ahead and "next week" the one after).
        (
            "Will close after identity-service !1 is reviewed, likely early next week",
            "pending review",
            IssueEta(label="early next week", start=date(2026, 10, 12), day=date(2026, 10, 14)),
        ),
        (
            "Should land next week",
            None,
            IssueEta(label="next week", start=date(2026, 10, 12), day=date(2026, 10, 16)),
        ),
        (
            "Ready by end of next week",
            None,
            IssueEta(label="end of next week", start=date(2026, 10, 15), day=date(2026, 10, 16)),
        ),
        (
            "MR !4 is idle. ETA 2-3 days, reviewing with team",
            "in progress",
            IssueEta(label="2-3 days", start=date(2026, 10, 6), day=date(2026, 10, 7)),
        ),
        ("Should be merged in 3 days", None, IssueEta(label="in 3 days", day=date(2026, 10, 7))),
        (
            "Within a few days",
            None,
            IssueEta(label="within a few days", start=_DAY, day=date(2026, 10, 8)),
        ),
        # N45, R5 live: a duration that looks ahead is a window from the check-in
        # date, with or without another ETA word, in digits or in words.
        (
            "2-3 days to have the test plan reviewed and finalized",
            "in review",
            IssueEta(label="2-3 days", start=date(2026, 10, 6), day=date(2026, 10, 7)),
        ),
        (
            "2-3 days to get it reviewed",
            None,
            IssueEta(label="2-3 days", start=date(2026, 10, 6), day=date(2026, 10, 7)),
        ),
        (
            "Two to three days.",
            None,
            IssueEta(label="2-3 days", start=date(2026, 10, 6), day=date(2026, 10, 7)),
        ),
        ("In two days", None, IssueEta(label="in two days", day=date(2026, 10, 6))),
        ("Needs a couple of days", None, IssueEta(label="a couple of days", day=date(2026, 10, 6))),
        (
            "In the next few days",
            None,
            IssueEta(label="in the next few days", start=date(2026, 10, 6), day=date(2026, 10, 8)),
        ),
        # A number of days that looks back is no ETA.
        ("Took 2 days to fix the flaky test", "in review", None),
        ("3 days in review, still waiting", "in review", None),
        # The answer to an ETA question can be the day alone.
        ("Monday", None, IssueEta(label="Monday", day=date(2026, 10, 5))),
        ("Monday EOD", "in review", IssueEta(label="Monday", day=date(2026, 10, 5))),
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


# N43, R5 live (checkins.signals, 2026-10-04 12:08): Ira, a scrum master, on
# Noah's IDP-3, and Noah, its owner. The drift on her remark alone made Noah,
# Identity pod, Identity Platform and the programme amber.
_IRA_R5 = _eta_claim(
    "IDP-3",
    "in review",
    "In review on identity-service!1 with Noah's review, should wrap this week.",
)
_NOAH_R5 = _eta_claim("IDP-3", "pending review", "Will close after review, likely early next week.")


async def _seed_identity(
    store: InMemoryGraphStore, *, ira_roles: str | None, owner: str = _NOAH
) -> None:
    """R5's Identity: IDP-3 is ``owner``'s (Noah, a developer); Ira has ``ira_roles``.

    No branch, commit or merge request names IDP-3 unless a test adds one.
    """
    await _seed(store)
    await store.upsert_node(Project(tenant_id=_TENANT, id="proj-idp", name="Identity"))
    await store.upsert_node(
        Task(
            tenant_id=_TENANT,
            id="IDP-3",
            name="Passkey enrolment",
            metadata={"key": "IDP-3", "state": "in_progress", "status": "In Progress"},
        )
    )
    await store.upsert_node(
        Developer(tenant_id=_TENANT, id=_NOAH, name="Noah Weber", metadata={"app_roles": "dev"})
    )
    await store.upsert_node(
        Developer(
            tenant_id=_TENANT,
            id=_IRA,
            name="Ira Novak",
            metadata={"app_roles": ira_roles} if ira_roles is not None else {},
        )
    )
    for edge in (
        GraphEdge(
            tenant_id=_TENANT, from_node_id="proj-idp", to_node_id="IDP-3", kind=EdgeKind.CONTAINS
        ),
        GraphEdge(
            tenant_id=_TENANT, from_node_id=owner, to_node_id="IDP-3", kind=EdgeKind.ASSIGNED_TO
        ),
    ):
        await store.add_edge(edge)
    await store.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=_NOAH,
            correlation_id=f"corr-{_NOAH}",
            asked_at=datetime(2026, 10, 4, 0, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )


# Whatever her roles, a coordinator's, a manager's or even a developer's: with
# no branch, commit or merge request naming IDP-3, Ira neither owns nor works it.
@pytest.mark.parametrize("roles", ["sm", "po", "exec", "mgr,admin", "dev", None])
async def test_an_eta_from_someone_who_neither_owns_nor_works_the_issue_is_context(
    roles: str | None,
) -> None:
    store = InMemoryGraphStore()
    await _seed_identity(store, ira_roles=roles)

    await _check_in(store, _IRA, _IRA_R5, 8)
    await _check_in(store, _NOAH, _NOAH_R5, 9)

    assert await _drift(store, "proj-idp") == []
    # Noah's cell takes no drift for it, so he stays green.
    assert _NOAH not in await _risks(store).owner_drift(_TENANT, _DAY)
    # Her ETA stays recorded as context; only the comparison leaves it out.
    facts = await store.list_recent_facts(_TENANT, sources=("checkin_drift",))
    assert {
        (fact.payload["developer_id"], fact.payload["eta_start"], fact.payload["eta_date"])
        for fact in facts
        if fact.payload["kind"] == "eta_stated"
    } == {(_IRA, "2026-10-09", "2026-10-09"), (_NOAH, "2026-10-12", "2026-10-14")}


# A commit message, or a merge request's branch or title, naming IDP-3 makes
# Ira someone who works it, whatever her roles.
@pytest.mark.parametrize(
    ("roles", "via"), [(None, "commit"), ("sm", "branch"), ("mgr,admin", "title")]
)
async def test_etas_of_two_people_who_work_the_issue_still_disagree(
    roles: str | None, via: str
) -> None:
    store = InMemoryGraphStore()
    await _seed_identity(store, ira_roles=roles)
    await _works_on(store, _IRA, "IDP-3", via=via)

    await _check_in(store, _IRA, _IRA_R5, 8)
    await _check_in(store, _NOAH, _NOAH_R5, 9)

    assert [finding.reason for finding in await _drift(store, "proj-idp")] == [
        "ETAs disagree for IDP-3: Noah Weber (owner) said early next week, Oct 12 to Oct 14; "
        "Ira Novak said end of week, Oct 9. The owner's ETA is the one used."
    ]
    owner_drift = await _risks(store).owner_drift(_TENANT, _DAY)
    assert [signal.kind for signal in owner_drift[_NOAH]] == ["eta_disagreement"]


async def test_a_coordinator_who_owns_the_issue_still_counts() -> None:
    # Mina, a product owner, owns CHK-10: her ETA is the owner's.
    store = InMemoryGraphStore()
    await _seed(store)
    await store.upsert_node(
        Developer(tenant_id=_TENANT, id=_MINA, name="Mina Patel", metadata={"app_roles": "po"})
    )
    await store.upsert_node(
        Task(
            tenant_id=_TENANT,
            id="CHK-10",
            name="Acceptance criteria",
            metadata={"key": "CHK-10", "state": "in_progress", "status": "In Progress"},
        )
    )
    for edge in (
        GraphEdge(
            tenant_id=_TENANT, from_node_id="proj-chk", to_node_id="CHK-10", kind=EdgeKind.CONTAINS
        ),
        GraphEdge(
            tenant_id=_TENANT, from_node_id=_MINA, to_node_id="CHK-10", kind=EdgeKind.ASSIGNED_TO
        ),
    ):
        await store.add_edge(edge)
    await store.record_checkin(
        CheckIn(
            tenant_id=_TENANT,
            developer_id=_MINA,
            correlation_id=f"corr-{_MINA}",
            asked_at=datetime(2026, 10, 4, 0, 0, tzinfo=UTC),
            replied_at=None,
            raw_reply=None,
            signals=None,
        )
    )

    await _works_on(store, _LIAM, "CHK-10", via="branch")
    await _check_in(store, _MINA, _eta_claim("CHK-10", "in review", "Wrapping up Monday EOD"), 6)
    await _check_in(store, _LIAM, _eta_claim("CHK-10", None, "Should be ready by Friday"), 8)
    findings = await _drift(store)

    assert [finding.reason for finding in findings] == [
        "ETAs disagree for CHK-10: Mina Patel (owner) said Monday, Oct 5; "
        "Liam Chen said Friday, Oct 9. The owner's ETA is the one used."
    ]
    assert findings[0].owner_id == _MINA


async def test_a_managers_eta_on_a_developers_issue_is_context_but_the_assignees_counts() -> None:
    # N43 as decided: only the assignee and people whose branch, commit or merge
    # request names the issue are compared, and a manager is no exception.
    store = InMemoryGraphStore()
    await _seed_identity(store, ira_roles="mgr,admin")

    await _check_in(store, _IRA, _IRA_R5, 8)
    await _check_in(store, _NOAH, _NOAH_R5, 9)

    assert await _drift(store, "proj-idp") == []
    assert _NOAH not in await _risks(store).owner_drift(_TENANT, _DAY)

    # The same manager as the assignee: her ETA is the owner's, and it counts
    # against a developer whose merge request names the issue.
    owned = InMemoryGraphStore()
    await _seed_identity(owned, ira_roles="mgr,admin", owner=_IRA)
    await _works_on(owned, _NOAH, "IDP-3", via="branch")

    await _check_in(owned, _IRA, _IRA_R5, 8)
    await _check_in(owned, _NOAH, _NOAH_R5, 9)
    findings = await _drift(owned, "proj-idp")

    assert [finding.reason for finding in findings] == [
        "ETAs disagree for IDP-3: Ira Novak (owner) said end of week, Oct 9; "
        "Noah Weber said early next week, Oct 12 to Oct 14. The owner's ETA is the one used."
    ]
    assert findings[0].owner_id == _IRA
