from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from core.application.authorization import AuthorizationPolicy, Capability
from core.application.sync_services import IssueReadSyncService
from core.application.writeback_service import (
    NO_CHANGE_SOURCE,
    NOT_OWNER_SOURCE,
    OPEN_MR_SOURCE,
    UNASSIGNED_SOURCE,
    OpenMergeRequestHold,
    WriteBackService,
    canonical_target_state,
    interpret_consent_answer,
    interpret_consent_reply,
    target_state_label,
    write_back_comment,
)
from core.domain.auth import Principal
from core.domain.errors import ProviderUnavailable
from core.domain.graph import Developer, EntityRef, FactEvent, NodeKind, Task
from core.domain.identity import IdentityLink
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.status import CheckInPreference, IssueClaim, WriteBackConsent
from core.domain.writeback import WriteBackAudit, WriteBackStatus, WriteBackTarget
from infra.persistence.in_memory_graph import InMemoryGraphStore
from tests.contract.fakes import (
    FakeIdentityLinkRepository,
    FakeIssueTracker,
    FakeStatusRepository,
    FakeTimeSeriesRepository,
    FakeWriteBackAuditRepository,
    FakeWriteBackConfigRepository,
)

_TENANT = "demo"
_DEV = "dev-asha"
_CORRELATION = "checkin-1"


class _DenyingPolicy(AuthorizationPolicy):
    def can(self, principal: Principal, capability: Capability) -> bool:
        return False


class _FailingIssueTracker(FakeIssueTracker):
    async def transition(self, tenant_id: str, key: str, to_state: str) -> None:
        raise ProviderUnavailable("transition rejected")


class _UnreadableIssueTracker(FakeIssueTracker):
    async def get_issue(self, tenant_id: str, key: str) -> Issue:
        raise ProviderUnavailable("tracker down")


def _issue(
    key: str,
    state: IssueState,
    assignee: str | None = _DEV,
) -> Issue:
    return Issue(
        tenant_id=_TENANT,
        key=key,
        title=f"Work on {key}",
        state=state,
        assignee=UserRef(tenant_id=_TENANT, external_id=assignee) if assignee else None,
    )


def _build(
    *,
    tracker: FakeIssueTracker | None = None,
    consent: WriteBackConsent = WriteBackConsent.AUTO_APPLY,
    tenant_override: bool | None = None,
    default_enabled: bool = False,
    policy: AuthorizationPolicy | None = None,
    developer_id: str = _DEV,
    identity_links: FakeIdentityLinkRepository | None = None,
    facts: FakeTimeSeriesRepository | InMemoryGraphStore | None = None,
    graph: InMemoryGraphStore | None = None,
) -> tuple[WriteBackService, FakeIssueTracker, FakeWriteBackAuditRepository]:
    issue_tracker = tracker or FakeIssueTracker(
        issues={
            "PO-1": Issue(
                tenant_id=_TENANT,
                key="PO-1",
                title="Wire write-back",
                state=IssueState.IN_PROGRESS,
                assignee=UserRef(tenant_id=_TENANT, external_id=_DEV),
            )
        }
    )
    audit = FakeWriteBackAuditRepository()
    config = FakeWriteBackConfigRepository()
    if tenant_override is not None:
        config.enabled[_TENANT] = tenant_override
    status = FakeStatusRepository()
    status.checkin_preferences[(_TENANT, developer_id)] = CheckInPreference(
        tenant_id=_TENANT,
        developer_id=developer_id,
        write_back_consent=consent,
    )
    service = WriteBackService(
        issue_tracker=issue_tracker,
        audit_repository=audit,
        config_repository=config,
        status_repository=status,
        identity_link_repository=identity_links,
        time_series_repository=facts,
        graph_repository=graph,
        authorization_policy=policy,
        writeback_enabled_default=default_enabled,
        clock=lambda: datetime(2026, 7, 25, 9, 0, tzinfo=UTC),
    )
    return service, issue_tracker, audit


def _claims() -> list[IssueClaim]:
    return [IssueClaim(issue_key="PO-1", claimed_done=True, note="shipped it")]


# What an applied PO-1 "done" posts: neutral, never the note ("shipped it").
_PO1_DONE_COMMENT = (
    "Moved to Done by OpenProgram: the assignee reported it done in the 2026-07-25 check-in."
)


async def test_system_gate_closed_is_a_noop() -> None:
    service, tracker, audit = _build(default_enabled=False, consent=WriteBackConsent.AUTO_APPLY)
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=_claims(),
    )
    assert results == []
    assert tracker.transitions == []
    assert tracker.comments == []
    assert audit.audits == {}


async def test_capability_denied_is_a_noop() -> None:
    service, tracker, audit = _build(default_enabled=True, policy=_DenyingPolicy())
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=_claims(),
    )
    assert results == []
    assert tracker.transitions == []
    assert audit.audits == {}


async def test_consent_never_is_a_noop() -> None:
    service, tracker, audit = _build(default_enabled=True, consent=WriteBackConsent.NEVER)
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=_claims(),
    )
    assert results == []
    assert tracker.transitions == []
    assert audit.audits == {}


async def test_auto_apply_transitions_comments_and_audits() -> None:
    service, tracker, audit = _build(default_enabled=True, consent=WriteBackConsent.AUTO_APPLY)
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=_claims(),
    )
    assert tracker.transitions == [(_TENANT, "PO-1", IssueState.DONE.value)]
    assert tracker.comments == [(_TENANT, "PO-1", _PO1_DONE_COMMENT)]
    assert len(results) == 1
    recorded = results[0]
    assert recorded.status is WriteBackStatus.APPLIED
    assert recorded.before_state == IssueState.IN_PROGRESS.value
    assert recorded.after_state == IssueState.DONE.value
    assert recorded.comment == _PO1_DONE_COMMENT
    assert await audit.list_for_issue(_TENANT, "PO-1") == [recorded]


async def test_always_ask_records_proposed_without_writing() -> None:
    service, tracker, audit = _build(default_enabled=True, consent=WriteBackConsent.ALWAYS_ASK)
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=_claims(),
    )
    assert tracker.transitions == []
    assert tracker.comments == []
    assert len(results) == 1
    assert results[0].status is WriteBackStatus.PROPOSED
    assert results[0].before_state == IssueState.IN_PROGRESS.value
    assert results[0].after_state == IssueState.DONE.value


async def test_tenant_override_opens_gate_over_default() -> None:
    service, tracker, _ = _build(
        default_enabled=False,
        tenant_override=True,
        consent=WriteBackConsent.AUTO_APPLY,
    )
    assert await service.system_gate_open(_TENANT) is True
    await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=_claims(),
    )
    assert tracker.transitions == [(_TENANT, "PO-1", IssueState.DONE.value)]


async def test_apply_is_idempotent_on_correlation() -> None:
    service, tracker, audit = _build(default_enabled=True, consent=WriteBackConsent.AUTO_APPLY)
    first = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=_claims(),
    )
    second = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=_claims(),
    )
    assert len(first) == 1
    assert second == []
    assert tracker.transitions == [(_TENANT, "PO-1", IssueState.DONE.value)]
    assert len(await audit.list_for_issue(_TENANT, "PO-1")) == 1


async def test_provider_failure_records_failed_audit() -> None:
    tracker = _FailingIssueTracker(issues={"PO-1": _issue("PO-1", IssueState.IN_PROGRESS)})
    service, _, audit = _build(
        tracker=tracker, default_enabled=True, consent=WriteBackConsent.AUTO_APPLY
    )
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=_claims(),
    )
    assert len(results) == 1
    assert results[0].status is WriteBackStatus.FAILED
    assert await audit.list_for_issue(_TENANT, "PO-1") == results


async def test_revert_transitions_back_and_audits() -> None:
    service, tracker, _ = _build(default_enabled=True, consent=WriteBackConsent.AUTO_APPLY)
    applied = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=_claims(),
    )
    reverted = await service.revert(applied[0])
    assert reverted is not None
    assert reverted.status is WriteBackStatus.REVERTED
    assert reverted.after_state == IssueState.IN_PROGRESS.value
    assert tracker.transitions[-1] == (_TENANT, "PO-1", IssueState.IN_PROGRESS.value)


async def test_claim_without_actionable_change_is_skipped() -> None:
    service, tracker, audit = _build(default_enabled=True, consent=WriteBackConsent.AUTO_APPLY)
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=[IssueClaim(issue_key="PO-1", note="just an update")],
    )
    assert results == []
    assert tracker.transitions == []
    assert audit.audits == {}


async def test_auto_apply_records_standing_consent_provenance() -> None:
    service, _, _ = _build(default_enabled=True, consent=WriteBackConsent.AUTO_APPLY)
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=_claims(),
    )
    assert results[0].status is WriteBackStatus.APPLIED
    assert results[0].source == "standing_consent"


def test_interpret_consent_reply_affirm() -> None:
    for text in ("yes", "Yes please", "yep", "apply it", "do it", "go ahead", "OK"):
        assert interpret_consent_reply(text) == "affirm", text


def test_interpret_consent_reply_decline() -> None:
    for text in ("no", "No thanks", "nope", "please don't", "do not", "stop", "cancel"):
        assert interpret_consent_reply(text) == "decline", text


def test_interpret_consent_reply_unclear() -> None:
    for text in ("", "maybe later", "what do you mean?", "yes but no", "hmm"):
        assert interpret_consent_reply(text) == "unclear", text


async def _propose_pending(
    service: WriteBackService,
) -> None:
    proposed = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=_claims(),
    )
    assert len(proposed) == 1
    assert proposed[0].status is WriteBackStatus.PROPOSED


async def test_resolve_affirmative_applies_pending_proposal() -> None:
    service, tracker, audit = _build(default_enabled=True, consent=WriteBackConsent.ALWAYS_ASK)
    await _propose_pending(service)
    assert tracker.transitions == []

    results = await service.resolve_consent_reply(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        reply_text="yes, do it",
    )
    assert len(results) == 1
    assert results[0].status is WriteBackStatus.APPLIED
    assert results[0].source == "consent_reply"
    assert tracker.transitions == [(_TENANT, "PO-1", IssueState.DONE.value)]
    assert tracker.comments == [(_TENANT, "PO-1", _PO1_DONE_COMMENT)]
    assert await service.list_pending_proposals(_TENANT, _CORRELATION) == []


async def test_resolve_negative_records_declined_without_writing() -> None:
    service, tracker, _ = _build(default_enabled=True, consent=WriteBackConsent.ALWAYS_ASK)
    await _propose_pending(service)

    results = await service.resolve_consent_reply(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        reply_text="no thanks",
    )
    assert len(results) == 1
    assert results[0].status is WriteBackStatus.DECLINED
    assert tracker.transitions == []
    assert tracker.comments == []
    assert await service.list_pending_proposals(_TENANT, _CORRELATION) == []


async def test_resolve_unclear_leaves_proposal_pending() -> None:
    service, tracker, _ = _build(default_enabled=True, consent=WriteBackConsent.ALWAYS_ASK)
    await _propose_pending(service)

    results = await service.resolve_consent_reply(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        reply_text="maybe later",
    )
    assert results == []
    assert tracker.transitions == []
    pending = await service.list_pending_proposals(_TENANT, _CORRELATION)
    assert len(pending) == 1


async def test_resolve_affirmative_is_idempotent() -> None:
    service, tracker, _ = _build(default_enabled=True, consent=WriteBackConsent.ALWAYS_ASK)
    await _propose_pending(service)

    first = await service.resolve_consent_reply(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        reply_text="yes",
    )
    second = await service.resolve_consent_reply(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        reply_text="yes",
    )
    assert len(first) == 1
    assert second == []
    assert tracker.transitions == [(_TENANT, "PO-1", IssueState.DONE.value)]


async def test_resolve_without_pending_proposal_is_a_noop() -> None:
    service, tracker, _ = _build(default_enabled=True, consent=WriteBackConsent.ALWAYS_ASK)
    results = await service.resolve_consent_reply(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        reply_text="yes",
    )
    assert results == []
    assert tracker.transitions == []


async def test_resolve_respects_closed_system_gate() -> None:
    service, tracker, _ = _build(default_enabled=True, consent=WriteBackConsent.ALWAYS_ASK)
    await _propose_pending(service)
    # System gate flipped off after the proposal was recorded.
    await service._config.set_writeback_enabled(_TENANT, False)  # type: ignore[attr-defined]

    results = await service.resolve_consent_reply(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        reply_text="yes",
    )
    assert results == []
    assert tracker.transitions == []


# --- Ownership gate: only the issue's assignee may write to it (R1-1, G7) ---

_SM = "U-IRA"  # scrum master: no tickets of her own
_SOFIA = "U-SOFIA"
_NOAH = "U-NOAH"


def _team_tracker() -> FakeIssueTracker:
    # Jira knows people by account id; members are linked to theirs.
    return FakeIssueTracker(
        issues={
            "CHK-15": _issue("CHK-15", IssueState.TODO, assignee="acct-sofia"),
            "CHK-6": _issue("CHK-6", IssueState.IN_PROGRESS, assignee="acct-noah"),
            "IDP-5": _issue("IDP-5", IssueState.IN_PROGRESS, assignee="acct-noah"),
        }
    )


def _team_links() -> FakeIdentityLinkRepository:
    links = FakeIdentityLinkRepository()
    for developer_id, account in (
        (_SM, "acct-ira"),
        (_SOFIA, "acct-sofia"),
        (_NOAH, "acct-noah"),
    ):
        links.identity_links[(_TENANT, developer_id)] = IdentityLink(
            tenant_id=_TENANT, developer_id=developer_id, jira_account_id=account
        )
    return links


async def test_third_party_summary_writes_nothing_to_other_peoples_issues() -> None:
    # Ira (SM) summarises her team; her consent is auto_apply. None of these
    # issues is hers, so Jira sees no transition and no comment.
    service, tracker, audit = _build(
        tracker=_team_tracker(),
        default_enabled=True,
        consent=WriteBackConsent.AUTO_APPLY,
        developer_id=_SM,
        identity_links=_team_links(),
    )
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_SM,
        correlation_id=_CORRELATION,
        claims=[
            IssueClaim(
                issue_key="CHK-15",
                claimed_state="in progress",
                note="Sofia is working on CHK-15",
            ),
            IssueClaim(issue_key="CHK-6", claimed_state="in progress", note="Noah on CHK-6"),
            IssueClaim(issue_key="IDP-5", claimed_done=True, note="Noah has IDP-5 merged"),
        ],
    )
    assert tracker.transitions == []
    assert tracker.comments == []
    assert [(row.issue_key, row.status, row.source) for row in results] == [
        ("CHK-15", WriteBackStatus.DECLINED, NOT_OWNER_SOURCE),
        ("CHK-6", WriteBackStatus.DECLINED, NOT_OWNER_SOURCE),
        ("IDP-5", WriteBackStatus.DECLINED, NOT_OWNER_SOURCE),
    ]
    # The refusal records the unchanged state and never the reporter's note.
    chk15 = results[0]
    assert chk15.before_state == chk15.after_state == IssueState.TODO.value
    assert chk15.comment is None
    # Re-processing the same check-in does not pile up rows.
    again = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_SM,
        correlation_id=_CORRELATION,
        claims=[IssueClaim(issue_key="CHK-15", claimed_state="in progress")],
    )
    assert again == []
    assert len(audit.audits) == 3


async def test_always_ask_never_proposes_for_someone_elses_issue() -> None:
    service, tracker, _ = _build(
        tracker=_team_tracker(),
        default_enabled=True,
        consent=WriteBackConsent.ALWAYS_ASK,
        developer_id=_SM,
        identity_links=_team_links(),
    )
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_SM,
        correlation_id=_CORRELATION,
        claims=[IssueClaim(issue_key="CHK-15", claimed_state="in progress")],
    )
    assert [row.status for row in results] == [WriteBackStatus.DECLINED]
    assert await service.list_pending_proposals(_TENANT, _CORRELATION) == []
    assert tracker.transitions == []


async def test_unassigned_issue_gets_no_write_back() -> None:
    tracker = FakeIssueTracker(issues={"CHK-9": _issue("CHK-9", IssueState.TODO, assignee=None)})
    service, _, _ = _build(
        tracker=tracker,
        default_enabled=True,
        consent=WriteBackConsent.AUTO_APPLY,
        developer_id=_SOFIA,
        identity_links=_team_links(),
    )
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_SOFIA,
        correlation_id=_CORRELATION,
        claims=[IssueClaim(issue_key="CHK-9", claimed_done=True, note="done")],
    )
    assert tracker.transitions == []
    assert tracker.comments == []
    assert [(row.status, row.source) for row in results] == [
        (WriteBackStatus.DECLINED, UNASSIGNED_SOURCE)
    ]


async def test_owner_matched_through_identity_link_is_written() -> None:
    # The developer id is the chat id; Jira assigns by account id.
    service, tracker, _ = _build(
        tracker=_team_tracker(),
        default_enabled=True,
        consent=WriteBackConsent.AUTO_APPLY,
        developer_id=_NOAH,
        identity_links=_team_links(),
    )
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_NOAH,
        correlation_id=_CORRELATION,
        claims=[IssueClaim(issue_key="IDP-5", claimed_done=True, note="merged")],
    )
    assert [row.status for row in results] == [WriteBackStatus.APPLIED]
    assert tracker.transitions == [(_TENANT, "IDP-5", IssueState.DONE.value)]


async def test_owner_matched_through_linked_email_when_account_unknown() -> None:
    tracker = _team_tracker()
    tracker.user_emails["noah@example.com"] = "acct-noah"
    links = FakeIdentityLinkRepository()
    links.identity_links[(_TENANT, _NOAH)] = IdentityLink(
        tenant_id=_TENANT, developer_id=_NOAH, jira_email="noah@example.com"
    )
    service, _, _ = _build(
        tracker=tracker,
        default_enabled=True,
        consent=WriteBackConsent.AUTO_APPLY,
        developer_id=_NOAH,
        identity_links=links,
    )
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_NOAH,
        correlation_id=_CORRELATION,
        claims=[IssueClaim(issue_key="IDP-5", claimed_done=True)],
    )
    assert [row.status for row in results] == [WriteBackStatus.APPLIED]


async def test_unreadable_issue_writes_nothing() -> None:
    tracker = _UnreadableIssueTracker(issues={"PO-1": _issue("PO-1", IssueState.IN_PROGRESS)})
    service, _, _ = _build(tracker=tracker, default_enabled=True)
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=_claims(),
    )
    assert [row.status for row in results] == [WriteBackStatus.FAILED]
    assert tracker.transitions == []
    assert tracker.comments == []


async def test_consent_yes_after_reassignment_writes_nothing() -> None:
    service, tracker, _ = _build(default_enabled=True, consent=WriteBackConsent.ALWAYS_ASK)
    await _propose_pending(service)
    # Reassigned to someone else before the developer answered.
    tracker.issues["PO-1"] = _issue("PO-1", IssueState.IN_PROGRESS, assignee="dev-other")

    results = await service.resolve_consent_reply(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        reply_text="yes",
    )
    assert [(row.status, row.source) for row in results] == [
        (WriteBackStatus.DECLINED, NOT_OWNER_SOURCE)
    ]
    assert tracker.transitions == []
    assert tracker.comments == []
    assert await service.list_pending_proposals(_TENANT, _CORRELATION) == []


# --- Canonical target states: free text never reaches the tracker (R1-5, R1-9) ---

# Every target_state string qa2 recorded in writeback_audit on 2026-10-03 (R1 and
# its 13:05 close-out), with the issue's state at the time. Each one must become
# a transition the QA workflow (To Do / In Progress / Done) offers, or nothing.
_R1_TARGETS: list[tuple[str, bool, IssueState, str | None]] = [
    # (claimed_state, claimed_done, Jira state then, canonical target)
    ("acceptance criteria drafted, pending review", False, IssueState.IN_PROGRESS, "in_review"),
    ("branch pushed, MR to be opened", False, IssueState.IN_PROGRESS, None),
    ("draft", False, IssueState.IN_PROGRESS, "in_progress"),
    ("in progress", False, IssueState.IN_PROGRESS, "in_progress"),
    ("in progress", False, IssueState.TODO, "in_progress"),
    ("in review", False, IssueState.IN_PROGRESS, "in_review"),
    ("merged and ready to close", True, IssueState.IN_PROGRESS, "done"),
    ("not started", False, IssueState.TODO, "todo"),
    ("on track", False, IssueState.TODO, None),
    ("ready to close", True, IssueState.IN_PROGRESS, "done"),
    ("starting", False, IssueState.TODO, None),
    ("to do", False, IssueState.TODO, "todo"),
    ("To Do", False, IssueState.TODO, "todo"),
]
# "On track" names no state, but on an issue that is still To Do it says work has
# started, so the write moves it to In Progress (N21; Asha's R1 CHK-16).
_R1_WRITE_TARGETS = {("on track", IssueState.TODO): "in_progress"}
# What a canonical target means against the issue's state: the same state is no
# change. A review status reads as in progress, so in_review on an in-progress
# issue is no change either.
_SAME_STATE = {
    "todo": IssueState.TODO,
    "in_progress": IssueState.IN_PROGRESS,
    "in_review": IssueState.IN_PROGRESS,
    "done": IssueState.DONE,
}


@pytest.mark.parametrize(("claimed_state", "claimed_done", "jira_state", "target"), _R1_TARGETS)
async def test_every_r1_target_is_a_valid_transition_or_an_explicit_noop(
    claimed_state: str,
    claimed_done: bool,
    jira_state: IssueState,
    target: str | None,
) -> None:
    tracker = FakeIssueTracker(issues={"PO-1": _issue("PO-1", jira_state)})
    service, _, audit = _build(tracker=tracker, default_enabled=True)
    claim = IssueClaim(
        issue_key="PO-1",
        claimed_state=claimed_state,
        claimed_done=claimed_done,
        note="a note that must not be posted on a no-op",
    )

    results = await service.apply_from_checkin(
        tenant_id=_TENANT, developer_id=_DEV, correlation_id=_CORRELATION, claims=[claim]
    )

    target = _R1_WRITE_TARGETS.get((claimed_state, jira_state), target)
    if target is None or _SAME_STATE[target] is jira_state:
        # Explicit no-op: no transition, no comment, no audit row.
        assert tracker.transitions == []
        assert tracker.comments == []
        assert results == []
        assert audit.audits == {}
    else:
        assert target in {state.value for state in WriteBackTarget}
        assert tracker.transitions == [(_TENANT, "PO-1", target)]
        assert [(row.status, row.target_state) for row in results] == [
            (WriteBackStatus.APPLIED, target)
        ]


@pytest.mark.parametrize(
    ("wording", "expected"),
    [
        *[(state, target) for state, _, _, target in _R1_TARGETS],
        ("Done", "done"),
        ("closed", "done"),
        ("merged", "done"),
        ("IN_PROGRESS", "in_progress"),
        ("waiting on review for platform-libs !1", "in_review"),
        ("blocked", "blocked"),
        ("merged and ready to be closed", "done"),
        # Wording that rules a transition out.
        ("no change", None),
        ("unchanged", None),
        ("in review, on track", None),
        ("not done", None),
        ("not yet done", None),
        ("almost done", None),
        ("should be done today", None),
        ("done by Friday", None),
        ("will start Monday", None),
        ("starting CHK-4 next", None),
        ("code is done and in review", None),
        ("merged, waiting for QA", None),
        ("no longer blocked", None),
        # R2 (Raj): his answer to the INS-4 follow-up, and INS-2 "done" pending a merge.
        ("starting Monday", None),
        ("done code-wise, waiting on merge of insights-pipeline !1", None),
        # Wording that names no state at all.
        ("branch pushed", None),
        ("", None),
    ],
)
def test_claimed_state_wording_maps_to_one_canonical_state_or_none(
    wording: str, expected: str | None
) -> None:
    assert canonical_target_state(wording) == (
        WriteBackTarget(expected) if expected is not None else None
    )


async def test_owners_done_claim_transitions_to_done() -> None:
    # Noah's R1 claim on his own IDP-5, consent auto_apply.
    service, tracker, _ = _build(
        tracker=_team_tracker(),
        default_enabled=True,
        consent=WriteBackConsent.AUTO_APPLY,
        developer_id=_NOAH,
        identity_links=_team_links(),
    )
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_NOAH,
        correlation_id=_CORRELATION,
        claims=[
            IssueClaim(
                issue_key="IDP-5",
                claimed_done=True,
                claimed_state="merged and ready to close",
                note="Fix merged, all acceptance criteria met.",
            )
        ],
    )
    assert tracker.transitions == [(_TENANT, "IDP-5", WriteBackTarget.DONE.value)]
    assert tracker.comments == [
        (
            _TENANT,
            "IDP-5",
            "Moved to Done by OpenProgram: the assignee reported it merged "
            "in the 2026-07-25 check-in.",
        )
    ]
    [row] = results
    assert row.status is WriteBackStatus.APPLIED
    assert (row.target_state, row.before_state, row.after_state) == (
        "done",
        "in_progress",
        "done",
    )


@pytest.mark.parametrize("jira_state", [IssueState.IN_PROGRESS, IssueState.DONE])
async def test_on_track_is_a_noop_with_no_comment_unless_the_issue_is_to_do(
    jira_state: IssueState,
) -> None:
    tracker = FakeIssueTracker(issues={"CHK-16": _issue("CHK-16", jira_state)})
    service, _, audit = _build(tracker=tracker, default_enabled=True)
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=[
            IssueClaim(
                issue_key="CHK-16",
                claimed_state="on track",
                note="Roadmap review prep is on track, ETA unchanged",
            )
        ],
    )
    assert results == []
    assert tracker.transitions == []
    assert tracker.comments == []
    assert audit.audits == {}


async def test_claim_matching_the_current_state_posts_no_comment() -> None:
    # R1-9: "in progress" on an In Progress issue used to post the note anyway.
    tracker = FakeIssueTracker(issues={"CHK-14": _issue("CHK-14", IssueState.IN_PROGRESS)})
    service, _, audit = _build(tracker=tracker, default_enabled=True)
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=[IssueClaim(issue_key="CHK-14", claimed_state="in progress", note="ETA next week")],
    )
    assert results == []
    assert tracker.transitions == []
    assert tracker.comments == []
    assert audit.audits == {}


async def test_always_ask_asks_nothing_for_a_claim_jira_already_shows() -> None:
    tracker = FakeIssueTracker(issues={"CHK-3": _issue("CHK-3", IssueState.IN_PROGRESS)})
    service, _, _ = _build(
        tracker=tracker, default_enabled=True, consent=WriteBackConsent.ALWAYS_ASK
    )
    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=[IssueClaim(issue_key="CHK-3", claimed_state="in review")],
    )
    assert results == []
    assert await service.list_pending_proposals(_TENANT, _CORRELATION) == []


async def _seed_failed_row(
    audit: FakeWriteBackAuditRepository, *, correlation_id: str, target_state: str
) -> None:
    await audit.record(
        WriteBackAudit(
            id=f"failed-{correlation_id}",
            tenant_id=_TENANT,
            developer_id=_NOAH,
            issue_key="IDP-5",
            correlation_id=correlation_id,
            status=WriteBackStatus.FAILED,
            target_state=target_state,
            before_state="in_progress",
            after_state="in_progress",
            source="standing_consent",
            created_at=datetime(2026, 10, 3, 13, 5, tzinfo=UTC),
        )
    )


@pytest.mark.parametrize(
    ("old_correlation", "old_target"),
    [
        # R1's failed row, then a fresh claim in the 18:00 round's own check-in.
        ("checkin-R1", "ready to close"),
        ("checkin-R1", "done"),
        # A re-run of the same check-in: the old free-text target is a different key.
        ("checkin-R2", "ready to close"),
    ],
)
async def test_an_earlier_failed_write_back_does_not_block_a_fresh_claim(
    old_correlation: str, old_target: str
) -> None:
    service, tracker, audit = _build(
        tracker=_team_tracker(),
        default_enabled=True,
        developer_id=_NOAH,
        identity_links=_team_links(),
    )
    await _seed_failed_row(audit, correlation_id=old_correlation, target_state=old_target)

    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_NOAH,
        correlation_id="checkin-R2",
        claims=[IssueClaim(issue_key="IDP-5", claimed_done=True, claimed_state="ready to close")],
    )
    assert [row.status for row in results] == [WriteBackStatus.APPLIED]
    assert tracker.transitions == [(_TENANT, "IDP-5", "done")]


async def test_confirmed_proposal_already_in_state_expires_without_writing() -> None:
    service, tracker, _ = _build(default_enabled=True, consent=WriteBackConsent.ALWAYS_ASK)
    await _propose_pending(service)
    # Someone closed it before the developer said yes.
    tracker.issues["PO-1"] = _issue("PO-1", IssueState.DONE)

    results = await service.resolve_consent_reply(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        reply_text="yes",
    )
    assert [(row.status, row.source) for row in results] == [
        (WriteBackStatus.EXPIRED, NO_CHANGE_SOURCE)
    ]
    assert tracker.transitions == []
    assert tracker.comments == []
    assert await service.list_pending_proposals(_TENANT, _CORRELATION) == []


async def test_older_free_text_proposal_is_normalised_when_confirmed() -> None:
    # Proposals recorded before canonical targets stored the parser's wording.
    service, tracker, audit = _build(default_enabled=True, consent=WriteBackConsent.ALWAYS_ASK)
    await audit.record(
        WriteBackAudit(
            id="legacy-proposal",
            tenant_id=_TENANT,
            developer_id=_DEV,
            issue_key="PO-1",
            correlation_id=_CORRELATION,
            status=WriteBackStatus.PROPOSED,
            target_state="merged and ready to close",
            before_state="in_progress",
            after_state="merged and ready to close",
            created_at=datetime(2026, 10, 3, 12, 15, tzinfo=UTC),
        )
    )

    results = await service.resolve_consent_reply(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        reply_text="yes",
    )
    assert tracker.transitions == [(_TENANT, "PO-1", "done")]
    [row] = results
    assert (row.status, row.target_state, row.after_state) == (
        WriteBackStatus.APPLIED,
        "merged and ready to close",
        "done",
    )
    assert await service.list_pending_proposals(_TENANT, _CORRELATION) == []


async def test_auto_apply_issue_keys_is_a_dry_run_of_the_same_gates() -> None:
    service, tracker, audit = _build(
        tracker=_team_tracker(),
        default_enabled=True,
        developer_id=_NOAH,
        identity_links=_team_links(),
    )
    keys = await service.auto_apply_issue_keys(
        tenant_id=_TENANT,
        developer_id=_NOAH,
        claims=[
            IssueClaim(issue_key="IDP-5", claimed_done=True),  # his, would move
            IssueClaim(issue_key="CHK-6", claimed_state="in progress"),  # his, no change
            IssueClaim(issue_key="CHK-15", claimed_state="in progress"),  # Sofia's
        ],
    )
    assert keys == frozenset({"IDP-5"})
    assert tracker.transitions == []
    assert audit.audits == {}


async def test_auto_apply_issue_keys_is_empty_without_standing_consent() -> None:
    service, _, _ = _build(
        tracker=_team_tracker(),
        default_enabled=True,
        consent=WriteBackConsent.ALWAYS_ASK,
        developer_id=_NOAH,
        identity_links=_team_links(),
    )
    keys = await service.auto_apply_issue_keys(
        tenant_id=_TENANT,
        developer_id=_NOAH,
        claims=[IssueClaim(issue_key="IDP-5", claimed_done=True)],
    )
    assert keys == frozenset()


async def test_dry_run_reports_what_an_always_ask_person_would_be_asked_about() -> None:
    # N41 (R5): under always_ask the dry run used to report nothing, so the
    # check-in kept asking Omar "is CHK-17 complete?" before the consent question.
    service, tracker, audit = _build(
        tracker=_team_tracker(),
        default_enabled=True,
        consent=WriteBackConsent.ALWAYS_ASK,
        developer_id=_NOAH,
        identity_links=_team_links(),
    )
    claims = [
        IssueClaim(issue_key="IDP-5", claimed_done=True),  # his, would be proposed
        IssueClaim(issue_key="CHK-6", claimed_state="in progress"),  # his, no change
        IssueClaim(issue_key="CHK-15", claimed_state="in progress"),  # Sofia's
    ]

    dry_run = await service.dry_run(tenant_id=_TENANT, developer_id=_NOAH, claims=claims)
    keys = await service.auto_apply_issue_keys(tenant_id=_TENANT, developer_id=_NOAH, claims=claims)

    assert dry_run.proposed == frozenset({"IDP-5"})
    assert dry_run.written == frozenset()
    assert keys == frozenset()  # nothing is written without the yes
    assert tracker.transitions == []
    assert audit.audits == {}  # a dry run proposes nothing either


async def test_dry_run_reports_nothing_for_consent_never() -> None:
    service, _, _ = _build(
        tracker=_team_tracker(),
        default_enabled=True,
        consent=WriteBackConsent.NEVER,
        developer_id=_NOAH,
        identity_links=_team_links(),
    )

    dry_run = await service.dry_run(
        tenant_id=_TENANT,
        developer_id=_NOAH,
        claims=[IssueClaim(issue_key="IDP-5", claimed_done=True)],
    )

    assert (dry_run.written, dry_run.proposed, dict(dry_run.held_for_open_mr)) == (
        frozenset(),
        frozenset(),
        {},
    )


def test_target_state_label_names_canonical_states_and_keeps_free_text() -> None:
    assert target_state_label("in_review") == "In Review"
    assert target_state_label("done") == "Done"
    assert target_state_label("merged and ready to close") == "merged and ready to close"


# --- G6: no done while the issue's merge request is still open ---------------


def _merge_request_fact(
    pr_id: str,
    *,
    title: str,
    source_branch: str,
    state: str = "open",
    draft: bool = False,
    repo: str = "acme/insights-pipeline",
    observed_at: datetime = datetime(2026, 10, 3, 15, 0, tzinfo=UTC),
) -> FactEvent:
    """A synced merge request fact as the VCS sync records it."""
    return FactEvent(
        tenant_id=_TENANT,
        source="vcs_pull_request",
        entity_ref=EntityRef(tenant_id=_TENANT, kind=NodeKind.REPO, id=repo),
        payload={
            "repo": repo,
            "id": pr_id,
            "title": title,
            "merged": state == "merged",
            "state": state,
            "draft": draft,
            "source_branch": source_branch,
            "web_url": f"https://gitlab.example/{repo}/-/merge_requests/{pr_id}",
        },
        observed_at=observed_at,
        correlation_id=f"vcs:pull_request:{_TENANT}:{repo}:{pr_id}:{observed_at.isoformat()}",
    )


def _ins2_mr(
    *,
    state: str = "open",
    draft: bool = False,
    observed_at: datetime = datetime(2026, 10, 3, 15, 0, tzinfo=UTC),
) -> FactEvent:
    return _merge_request_fact(
        "1",
        title="INS-2: backfill the insights tables",
        source_branch="feature/INS-2-backfill",
        state=state,
        draft=draft,
        observed_at=observed_at,
    )


def _ins2_tracker(state: IssueState = IssueState.IN_PROGRESS) -> FakeIssueTracker:
    return FakeIssueTracker(issues={"INS-2": _issue("INS-2", state)})


def _ins2_done() -> list[IssueClaim]:
    # Raj's "INS-2 backfill is done" as the reply parser records it.
    return [
        IssueClaim(
            issue_key="INS-2", claimed_done=True, claimed_state="done", note="Backfill is done."
        )
    ]


async def _apply_ins2(
    service: WriteBackService, claims: list[IssueClaim] | None = None
) -> list[WriteBackAudit]:
    return await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=claims or _ins2_done(),
    )


@pytest.mark.parametrize(
    "merge_request",
    [
        pytest.param(_ins2_mr(), id="open"),
        pytest.param(_ins2_mr(draft=True), id="draft"),
    ],
)
async def test_done_with_an_open_or_draft_merge_request_is_declined_open_mr(
    merge_request: FactEvent,
) -> None:
    facts = FakeTimeSeriesRepository(facts=[merge_request])
    service, tracker, audit = _build(tracker=_ins2_tracker(), default_enabled=True, facts=facts)

    results = await _apply_ins2(service)

    [row] = results
    assert (row.status, row.source, row.target_state) == (
        WriteBackStatus.DECLINED,
        OPEN_MR_SOURCE,
        "done",
    )
    assert (row.before_state, row.after_state, row.comment) == ("in_progress", "in_progress", None)
    # No Jira call at all: no transition and no comment.
    assert tracker.transitions == []
    assert tracker.comments == []
    assert list(audit.audits.values()) == [row]
    # The reply can name the request that held it back.
    assert await service.open_merge_request_holds(_TENANT, results) == [
        OpenMergeRequestHold(
            issue_key="INS-2",
            current_state="in_progress",
            merge_requests=("insights-pipeline !1",),
        )
    ]
    # Re-processing the same check-in records nothing new.
    assert await _apply_ins2(service) == []
    assert len(audit.audits) == 1


@pytest.mark.parametrize("state", ["merged", "closed"])
async def test_done_with_only_a_merged_or_closed_merge_request_transitions(state: str) -> None:
    # The request was open earlier; its latest fact is what counts.
    facts = FakeTimeSeriesRepository(
        facts=[
            _ins2_mr(observed_at=datetime(2026, 10, 3, 9, 0, tzinfo=UTC)),
            _ins2_mr(state=state, observed_at=datetime(2026, 10, 3, 15, 0, tzinfo=UTC)),
        ]
    )
    service, tracker, _ = _build(tracker=_ins2_tracker(), default_enabled=True, facts=facts)

    [row] = await _apply_ins2(service)

    assert (row.status, row.source, row.after_state) == (
        WriteBackStatus.APPLIED,
        "standing_consent",
        "done",
    )
    assert tracker.transitions == [(_TENANT, "INS-2", "done")]
    assert len(tracker.comments) == 1


async def test_done_with_no_merge_request_for_the_issue_transitions() -> None:
    # Open requests that name other keys, matched whole: INS-21 and XINS-2 are not INS-2.
    facts = FakeTimeSeriesRepository(
        facts=[
            _merge_request_fact("2", title="INS-21: rename columns", source_branch="INS-21"),
            _merge_request_fact("3", title="XINS-2 spike", source_branch="spike/XINS-2"),
        ]
    )
    service, tracker, _ = _build(tracker=_ins2_tracker(), default_enabled=True, facts=facts)

    [row] = await _apply_ins2(service)

    assert row.status is WriteBackStatus.APPLIED
    assert tracker.transitions == [(_TENANT, "INS-2", "done")]


async def test_an_open_merge_request_does_not_hold_back_a_non_done_target() -> None:
    facts = FakeTimeSeriesRepository(facts=[_ins2_mr()])
    service, tracker, _ = _build(
        tracker=_ins2_tracker(IssueState.TODO), default_enabled=True, facts=facts
    )

    [row] = await _apply_ins2(service, [IssueClaim(issue_key="INS-2", claimed_state="started")])

    assert (row.status, row.target_state) == (WriteBackStatus.APPLIED, "in_progress")
    assert tracker.transitions == [(_TENANT, "INS-2", "in_progress")]


async def test_always_ask_is_not_asked_to_confirm_done_while_the_merge_request_is_open() -> None:
    facts = FakeTimeSeriesRepository(facts=[_ins2_mr()])
    service, tracker, _ = _build(
        tracker=_ins2_tracker(),
        default_enabled=True,
        consent=WriteBackConsent.ALWAYS_ASK,
        facts=facts,
    )

    [row] = await _apply_ins2(service)

    assert (row.status, row.source) == (WriteBackStatus.DECLINED, OPEN_MR_SOURCE)
    assert await service.list_pending_proposals(_TENANT, _CORRELATION) == []
    assert tracker.transitions == []


async def test_a_yes_never_moves_to_done_once_a_merge_request_is_open() -> None:
    facts = FakeTimeSeriesRepository()
    service, tracker, _ = _build(
        tracker=_ins2_tracker(),
        default_enabled=True,
        consent=WriteBackConsent.ALWAYS_ASK,
        facts=facts,
    )
    [proposal] = await _apply_ins2(service)
    assert proposal.status is WriteBackStatus.PROPOSED
    facts.facts.append(_ins2_mr())  # opened between the question and the answer

    [row] = await service.resolve_consent_reply(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        reply_text="yes",
    )

    assert (row.status, row.source, row.target_state) == (
        WriteBackStatus.DECLINED,
        OPEN_MR_SOURCE,
        "done",
    )
    assert tracker.transitions == []
    assert tracker.comments == []
    assert await service.list_pending_proposals(_TENANT, _CORRELATION) == []


async def test_dry_run_holds_back_done_for_an_open_merge_request() -> None:
    facts = FakeTimeSeriesRepository(facts=[_ins2_mr(draft=True)])
    service, tracker, audit = _build(tracker=_ins2_tracker(), default_enabled=True, facts=facts)

    dry_run = await service.dry_run(tenant_id=_TENANT, developer_id=_DEV, claims=_ins2_done())
    keys = await service.auto_apply_issue_keys(
        tenant_id=_TENANT, developer_id=_DEV, claims=_ins2_done()
    )

    assert dry_run.written == frozenset()
    assert dict(dry_run.held_for_open_mr) == {"INS-2": ("insights-pipeline !1",)}
    assert keys == frozenset()
    assert tracker.transitions == []
    assert audit.audits == {}


# --- N14: an "in review" claim the tracker already shows is a no-op, never a failure ---

# R2 18:05:19, Noah IDP-3 and Omar IDP-6: "in review" on an In Progress issue in the
# QA workflow (To Do / In Progress / Done, no review state).
_IDP3_IN_REVIEW = IssueClaim(
    issue_key="IDP-3",
    claimed_state="in review",
    note="Passkey enrolment in review on identity-service !1.",
)


@pytest.mark.parametrize("consent", [WriteBackConsent.AUTO_APPLY, WriteBackConsent.ALWAYS_ASK])
async def test_in_review_on_an_in_progress_issue_is_a_noop_without_a_review_state(
    consent: WriteBackConsent,
) -> None:
    tracker = FakeIssueTracker(issues={"IDP-3": _issue("IDP-3", IssueState.IN_PROGRESS)})
    service, _, audit = _build(tracker=tracker, default_enabled=True, consent=consent)

    results = await service.apply_from_checkin(
        tenant_id=_TENANT, developer_id=_DEV, correlation_id=_CORRELATION, claims=[_IDP3_IN_REVIEW]
    )

    assert results == []
    assert tracker.transitions == []
    assert tracker.comments == []
    assert audit.audits == {}
    dry_run = await service.dry_run(tenant_id=_TENANT, developer_id=_DEV, claims=[_IDP3_IN_REVIEW])
    assert dry_run.written == frozenset()


async def _synced_graph(state: IssueState) -> InMemoryGraphStore:
    graph = InMemoryGraphStore()
    await graph.upsert_node(
        Task(
            tenant_id=_TENANT,
            id="IDP-3",
            name="Passkey enrolment",
            metadata={"key": "IDP-3", "state": state.value, "project_key": "IDP"},
        )
    )
    return graph


async def test_in_review_claim_is_a_noop_when_the_read_fails_and_the_synced_copy_agrees() -> None:
    # The R2 rows had no before_state: the Jira read itself failed in a network
    # stall, so "failed" was recorded for a claim that needed no write at all.
    tracker = _UnreadableIssueTracker()
    service, _, audit = _build(
        tracker=tracker,
        default_enabled=True,
        graph=await _synced_graph(IssueState.IN_PROGRESS),
    )

    results = await service.apply_from_checkin(
        tenant_id=_TENANT, developer_id=_DEV, correlation_id=_CORRELATION, claims=[_IDP3_IN_REVIEW]
    )

    assert results == []
    assert tracker.transitions == []
    assert tracker.comments == []
    assert audit.audits == {}


@pytest.mark.parametrize("synced", [IssueState.TODO, None])
async def test_a_failed_read_is_still_recorded_when_the_claim_would_change_the_issue(
    synced: IssueState | None,
) -> None:
    graph = await _synced_graph(synced) if synced is not None else InMemoryGraphStore()
    service, tracker, _ = _build(
        tracker=_UnreadableIssueTracker(), default_enabled=True, graph=graph
    )

    results = await service.apply_from_checkin(
        tenant_id=_TENANT, developer_id=_DEV, correlation_id=_CORRELATION, claims=[_IDP3_IN_REVIEW]
    )

    assert [(row.status, row.before_state) for row in results] == [(WriteBackStatus.FAILED, None)]
    assert tracker.transitions == []
    assert tracker.comments == []


# --- N9: OpenProgram's own copy of the issue follows a write right away ---

_MOVED_AT = datetime(2026, 10, 4, 0, 4, 53, tzinfo=UTC)
_STATUS_NAMES = {
    IssueState.TODO: "To Do",
    IssueState.IN_PROGRESS: "In Progress",
    IssueState.DONE: "Done",
    IssueState.BLOCKED: "Blocked",
}


class _MovingIssueTracker(FakeIssueTracker):
    """A tracker whose transition really moves the issue, as Jira does (no review state)."""

    async def transition(self, tenant_id: str, key: str, to_state: str) -> None:
        await super().transition(tenant_id, key, to_state)
        state = {
            "todo": IssueState.TODO,
            "in_progress": IssueState.IN_PROGRESS,
            "in_review": IssueState.IN_PROGRESS,
            "blocked": IssueState.BLOCKED,
            "done": IssueState.DONE,
        }[to_state]
        issue = self.issues[key]
        self.issues[key] = replace(
            issue,
            state=state,
            updated_at=_MOVED_AT,
            metadata={**issue.metadata, "status": _STATUS_NAMES[state]},
        )


def _ins3(state: IssueState) -> Issue:
    return Issue(
        tenant_id=_TENANT,
        key="INS-3",
        title="Duplicate events in hourly rollup",
        state=state,
        assignee=UserRef(tenant_id=_TENANT, external_id=_DEV),
        updated_at=datetime(2026, 10, 2, 16, 41, tzinfo=UTC),
        metadata={"project_key": "INS", "status": _STATUS_NAMES[state], "issue_type": "Task"},
    )


async def _synced_store(tracker: FakeIssueTracker) -> InMemoryGraphStore:
    """A store the hourly issue sync has filled from ``tracker``."""
    store = InMemoryGraphStore()
    await store.upsert_node(Developer(tenant_id=_TENANT, id=_DEV, name="Raj Iyer"))
    await IssueReadSyncService(
        issue_tracker=tracker,
        graph_repository=store,
        time_series_repository=store,
        cursor_repository=store,
    ).sync_project(tenant_id=_TENANT, project_key="INS")
    return store


async def _issue_facts(store: InMemoryGraphStore) -> list[FactEvent]:
    return await store.list_facts(
        _TENANT, EntityRef(tenant_id=_TENANT, kind=NodeKind.TASK, id="INS-3")
    )


_INS3_MERGED = IssueClaim(issue_key="INS-3", claimed_done=True, claimed_state="merged")


async def test_applied_write_updates_the_local_issue_as_the_next_sync_would() -> None:
    # R3: INS-3 moved to Done at 00:04, but OpenProgram read In Progress (and kept the
    # merged_issue_open drift) until the 01:00 sync.
    tracker = _MovingIssueTracker(issues={"INS-3": _ins3(IssueState.IN_PROGRESS)})
    store = await _synced_store(tracker)
    service, _, _ = _build(tracker=tracker, default_enabled=True, graph=store, facts=store)

    [row] = await service.apply_from_checkin(
        tenant_id=_TENANT, developer_id=_DEV, correlation_id=_CORRELATION, claims=[_INS3_MERGED]
    )

    assert row.status is WriteBackStatus.APPLIED
    node = await store.get_node(_TENANT, "INS-3")
    assert node is not None and node.metadata["state"] == "done"
    # Exactly what the next sync writes for the moved issue: same node, same fact key.
    after_sync = await _synced_store(tracker)
    assert node == await after_sync.get_node(_TENANT, "INS-3")
    latest = (await _issue_facts(store))[-1]
    [synced_fact] = await _issue_facts(after_sync)
    assert (latest.correlation_id, latest.payload, latest.observed_at) == (
        synced_fact.correlation_id,
        synced_fact.payload,
        synced_fact.observed_at,
    )
    assert latest.payload["state"] == "done"


class _ReadOnceTracker(_MovingIssueTracker):
    """Readable for the write itself, then down (the read-back fails)."""

    reads = 0

    async def get_issue(self, tenant_id: str, key: str) -> Issue:
        self.reads += 1
        if self.reads > 1:
            raise ProviderUnavailable("tracker down after the write")
        return await super().get_issue(tenant_id, key)


async def test_a_failed_read_back_still_moves_the_local_state() -> None:
    tracker = _ReadOnceTracker(issues={"INS-3": _ins3(IssueState.IN_PROGRESS)})
    store = await _synced_store(
        _MovingIssueTracker(issues={"INS-3": _ins3(IssueState.IN_PROGRESS)})
    )
    service, _, _ = _build(tracker=tracker, default_enabled=True, graph=store, facts=store)

    await service.apply_from_checkin(
        tenant_id=_TENANT, developer_id=_DEV, correlation_id=_CORRELATION, claims=[_INS3_MERGED]
    )

    node = await store.get_node(_TENANT, "INS-3")
    assert node is not None
    assert (node.metadata["state"], node.metadata["status"]) == ("done", "In Progress")


async def test_revert_moves_the_local_issue_back() -> None:
    tracker = _MovingIssueTracker(issues={"INS-3": _ins3(IssueState.IN_PROGRESS)})
    store = await _synced_store(tracker)
    service, _, _ = _build(tracker=tracker, default_enabled=True, graph=store, facts=store)
    [applied] = await service.apply_from_checkin(
        tenant_id=_TENANT, developer_id=_DEV, correlation_id=_CORRELATION, claims=[_INS3_MERGED]
    )

    reverted = await service.revert(applied)

    assert reverted is not None and reverted.status is WriteBackStatus.REVERTED
    node = await store.get_node(_TENANT, "INS-3")
    assert node is not None and node.metadata["state"] == "in_progress"


async def test_a_failed_write_leaves_the_local_issue_alone() -> None:
    tracker = _FailingIssueTracker(issues={"INS-3": _ins3(IssueState.IN_PROGRESS)})
    store = await _synced_store(tracker)
    service, _, _ = _build(tracker=tracker, default_enabled=True, graph=store, facts=store)
    facts_before = await _issue_facts(store)

    [row] = await service.apply_from_checkin(
        tenant_id=_TENANT, developer_id=_DEV, correlation_id=_CORRELATION, claims=[_INS3_MERGED]
    )

    assert row.status is WriteBackStatus.FAILED
    node = await store.get_node(_TENANT, "INS-3")
    assert node is not None and node.metadata["state"] == "in_progress"
    assert await _issue_facts(store) == facts_before


# --- N20: the write-back comment is neutral and factual, never the person's words ---


async def test_comment_names_person_merge_request_and_date_never_the_note() -> None:
    # R3 Raj INS-3: the comment was "Merged on !2, Jira not updated yet.", which the
    # write itself makes false, and it named no repository.
    tracker = _MovingIssueTracker(issues={"INS-3": _ins3(IssueState.IN_PROGRESS)})
    store = await _synced_store(tracker)
    await store.append_fact(
        _merge_request_fact("2", title="INS-3 dedupe", source_branch="INS-3", state="merged")
    )
    service, _, _ = _build(tracker=tracker, default_enabled=True, graph=store, facts=store)

    [row] = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=[
            IssueClaim(
                issue_key="INS-3",
                claimed_done=True,
                claimed_state="merged",
                note="Merged on !2, Jira not updated yet.",
            )
        ],
        reported_on=date(2026, 10, 4),
    )

    expected = (
        "Moved to Done by OpenProgram: Raj Iyer reported it merged "
        "(acme/insights-pipeline !2) in the 2026-10-04 check-in."
    )
    assert tracker.comments == [(_TENANT, "INS-3", expected)]
    assert row.comment == expected


async def test_review_claim_comment_names_the_state_jira_really_moved_to() -> None:
    # No review state in the QA workflow: "in review" lands on In Progress.
    tracker = _MovingIssueTracker(issues={"INS-3": _ins3(IssueState.TODO)})
    store = await _synced_store(tracker)
    await store.append_fact(
        _merge_request_fact("4", title="INS-3 dedupe", source_branch="INS-3", state="open")
    )
    service, _, _ = _build(tracker=tracker, default_enabled=True, graph=store, facts=store)

    await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=[IssueClaim(issue_key="INS-3", claimed_state="in review", note="PR is up")],
        reported_on=date(2026, 10, 4),
    )

    assert tracker.comments == [
        (
            _TENANT,
            "INS-3",
            "Moved to In Progress by OpenProgram: Raj Iyer reported it in review "
            "(acme/insights-pipeline !4) in the 2026-10-04 check-in.",
        )
    ]


async def test_a_failed_comment_keeps_the_applied_write() -> None:
    class _NoCommentTracker(_MovingIssueTracker):
        async def add_comment(self, tenant_id: str, key: str, body: str) -> None:
            raise ProviderUnavailable("comment rejected")

    tracker = _NoCommentTracker(issues={"INS-3": _ins3(IssueState.IN_PROGRESS)})
    service, _, _ = _build(tracker=tracker, default_enabled=True)

    [row] = await service.apply_from_checkin(
        tenant_id=_TENANT, developer_id=_DEV, correlation_id=_CORRELATION, claims=[_INS3_MERGED]
    )

    assert (row.status, row.after_state, row.comment) == (WriteBackStatus.APPLIED, "done", None)
    assert tracker.transitions == [(_TENANT, "INS-3", "done")]


def test_write_back_comment_wording() -> None:
    assert write_back_comment(
        destination="Done",
        person="Raj Iyer",
        reported="it merged",
        merge_requests=("acme/insights-pipeline !2",),
        reported_on=date(2026, 10, 4),
    ) == (
        "Moved to Done by OpenProgram: Raj Iyer reported it merged "
        "(acme/insights-pipeline !2) in the 2026-10-04 check-in."
    )


# --- N21: "started" / "on track" on the owner's To Do issue moves it to In Progress ---

# R3 (2026-10-04), as the parser read them: both issues were To Do in Jira.
_ASHA_CHK16 = IssueClaim(
    issue_key="CHK-16",
    claimed_state="on track",
    note=(
        "Prep is on track, merging related MRs after review. Agenda and Q4 checkout "
        "numbers are drafted, final pass remaining."
    ),
)
_HANA_IDP9 = IssueClaim(
    issue_key="IDP-9", note="Outline started, on track for end of week completion."
)


async def _people_store() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    await store.upsert_node(Developer(tenant_id=_TENANT, id=_DEV, name="Asha Rao"))
    return store


@pytest.mark.parametrize("claim", [_ASHA_CHK16, _HANA_IDP9], ids=["asha-chk16", "hana-idp9"])
async def test_owner_saying_work_started_moves_a_to_do_issue_to_in_progress(
    claim: IssueClaim,
) -> None:
    tracker = _MovingIssueTracker(
        issues={claim.issue_key: _issue(claim.issue_key, IssueState.TODO)}
    )
    service, _, _ = _build(tracker=tracker, default_enabled=True, graph=await _people_store())

    [row] = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=[claim],
        reported_on=date(2026, 10, 4),
    )

    assert (row.status, row.target_state, row.before_state) == (
        WriteBackStatus.APPLIED,
        "in_progress",
        "todo",
    )
    assert tracker.transitions == [(_TENANT, claim.issue_key, "in_progress")]
    assert tracker.comments == [
        (
            _TENANT,
            claim.issue_key,
            "Moved to In Progress by OpenProgram: Asha Rao reported work on it started "
            "in the 2026-10-04 check-in.",
        )
    ]
    dry_run = await service.dry_run(tenant_id=_TENANT, developer_id=_DEV, claims=[claim])
    assert dry_run.written == frozenset()  # already applied: In Progress now


async def test_always_ask_owner_is_asked_before_a_started_issue_moves() -> None:
    tracker = FakeIssueTracker(issues={"CHK-16": _issue("CHK-16", IssueState.TODO)})
    service, _, _ = _build(
        tracker=tracker, default_enabled=True, consent=WriteBackConsent.ALWAYS_ASK
    )

    [row] = await service.apply_from_checkin(
        tenant_id=_TENANT, developer_id=_DEV, correlation_id=_CORRELATION, claims=[_ASHA_CHK16]
    )

    assert (row.status, row.target_state, row.comment) == (
        WriteBackStatus.PROPOSED,
        "in_progress",
        None,
    )
    assert tracker.transitions == []


async def test_someone_elses_to_do_issue_is_not_moved_by_their_on_track() -> None:
    tracker = FakeIssueTracker(
        issues={"CHK-16": _issue("CHK-16", IssueState.TODO, assignee="dev-other")}
    )
    service, _, _ = _build(tracker=tracker, default_enabled=True)

    [row] = await service.apply_from_checkin(
        tenant_id=_TENANT, developer_id=_DEV, correlation_id=_CORRELATION, claims=[_ASHA_CHK16]
    )

    assert (row.status, row.source) == (WriteBackStatus.DECLINED, NOT_OWNER_SOURCE)
    assert tracker.transitions == []
    assert tracker.comments == []


@pytest.mark.parametrize(
    ("claimed_state", "note"),
    [
        # Raj R3 INS-4, Zoe R3 CHK-12, Sofia R3 CHK-15, Liam R1 CHK-4.
        ("not started", "Planned to start Monday after !1 merges."),
        (None, "Not started, planned for next week"),
        (None, "Will start after CHK-14 is merged"),
        ("starting", "Work on CHK-4 to begin next."),
        (None, "planning to start Monday"),
        (None, "Should start tomorrow, on track otherwise"),
        (None, "Haven't started yet, still on track for Friday"),
        (None, "Still waiting on review"),
        (None, "On track, blocked on the API contract"),
        (None, ""),
        # N28: a start that lies ahead stays not started.
        (None, "Will start Monday"),
        (None, "Picking up CHK-12 tomorrow"),
        (None, "Planning to pick it up after CHK-11"),
        (None, "Drafted? Not yet."),
        # Zoe R4 CHK-12 before storefront-web !5 was opened: the parser's note
        # also says the merge request is expected, and no merge request is open.
        (
            None,
            "Being picked up today, MR expected shortly. Aiming for review by end of day tomorrow.",
        ),
    ],
)
async def test_wording_that_does_not_clearly_say_work_started_moves_nothing(
    claimed_state: str | None, note: str
) -> None:
    tracker = FakeIssueTracker(issues={"CHK-12": _issue("CHK-12", IssueState.TODO)})
    service, _, audit = _build(tracker=tracker, default_enabled=True)

    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=[IssueClaim(issue_key="CHK-12", claimed_state=claimed_state, note=note)],
    )

    assert [row for row in results if row.target_state == "in_progress"] == []
    assert [t for t in tracker.transitions if t[2] == "in_progress"] == []


async def test_a_started_claim_whose_read_fails_is_a_noop_unless_the_copy_is_to_do() -> None:
    graph = await _synced_graph(IssueState.IN_PROGRESS)
    service, tracker, audit = _build(
        tracker=_UnreadableIssueTracker(), default_enabled=True, graph=graph
    )

    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=[IssueClaim(issue_key="IDP-3", claimed_state="on track")],
    )

    assert results == []
    assert audit.audits == {}


# --- N28: more start wording, and an open merge request as start evidence ---

# R4 (2026-10-04), as the parser read them; both issues were To Do in Jira.
_ASHA_R4_CHK16 = IssueClaim(
    issue_key="CHK-16",
    claimed_state="agenda and numbers drafted",
    note=(
        "Drafting work nearly complete, final pass pending. ETA unchanged, final pass this "
        "week. Agenda and Q4 checkout numbers drafted, final pass remaining."
    ),
)
_ZOE_R4_CHK12 = IssueClaim(
    issue_key="CHK-12",
    note="Being picked up today, MR expected shortly. Aiming for review by end of day tomorrow.",
)


def _storefront_mr5(*, state: str = "open") -> FactEvent:
    # Zoe opened it at 06:05:54 on branch CHK-12-promo-code-validation.
    return _merge_request_fact(
        "5",
        title="CHK-12 Promo code validation",
        source_branch="CHK-12-promo-code-validation",
        state=state,
        repo="acme/storefront-web",
        observed_at=datetime(2026, 10, 4, 6, 5, 54, tzinfo=UTC),
    )


async def _named_store(name: str) -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    await store.upsert_node(Developer(tenant_id=_TENANT, id=_DEV, name=name))
    return store


@pytest.mark.parametrize(
    ("claim", "name"),
    [
        pytest.param(_ASHA_R4_CHK16, "Asha Rao", id="asha-r4-agenda-drafted"),
        pytest.param(
            IssueClaim(issue_key="CHK-12", note="Picking up CHK-12 promo code validation today."),
            "Zoe Almeida",
            id="picking-up",
        ),
        pytest.param(
            IssueClaim(issue_key="CHK-12", note="Working on the promo code rules."),
            "Zoe Almeida",
            id="working-on",
        ),
        pytest.param(
            IssueClaim(issue_key="CHK-12", note="Started on the validation rules."),
            "Zoe Almeida",
            id="started-on",
        ),
        pytest.param(
            IssueClaim(issue_key="CHK-12", note="Promo code validation in progress."),
            "Zoe Almeida",
            id="in-progress",
        ),
    ],
)
async def test_more_wording_that_says_work_began_moves_a_to_do_issue(
    claim: IssueClaim, name: str
) -> None:
    tracker = _MovingIssueTracker(
        issues={claim.issue_key: _issue(claim.issue_key, IssueState.TODO)}
    )
    service, _, _ = _build(tracker=tracker, default_enabled=True, graph=await _named_store(name))

    [row] = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=[claim],
        reported_on=date(2026, 10, 4),
    )

    assert (row.status, row.target_state, row.before_state) == (
        WriteBackStatus.APPLIED,
        "in_progress",
        "todo",
    )
    assert tracker.comments == [
        (
            _TENANT,
            claim.issue_key,
            f"Moved to In Progress by OpenProgram: {name} reported work on it started "
            "in the 2026-10-04 check-in.",
        )
    ]


async def test_zoes_picking_up_with_her_open_merge_request_moves_chk12() -> None:
    tracker = _MovingIssueTracker(issues={"CHK-12": _issue("CHK-12", IssueState.TODO)})
    facts = FakeTimeSeriesRepository(facts=[_storefront_mr5()])
    service, _, _ = _build(
        tracker=tracker,
        default_enabled=True,
        facts=facts,
        graph=await _named_store("Zoe Almeida"),
    )
    dry_run = await service.dry_run(tenant_id=_TENANT, developer_id=_DEV, claims=[_ZOE_R4_CHK12])

    [row] = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=[_ZOE_R4_CHK12],
        reported_on=date(2026, 10, 4),
    )

    assert dry_run.written == frozenset({"CHK-12"})
    assert (row.status, row.target_state, row.before_state, row.source) == (
        WriteBackStatus.APPLIED,
        "in_progress",
        "todo",
        "standing_consent",
    )
    assert tracker.transitions == [(_TENANT, "CHK-12", "in_progress")]
    assert tracker.comments == [
        (
            _TENANT,
            "CHK-12",
            "Moved to In Progress by OpenProgram: Zoe Almeida reported work on it "
            "(acme/storefront-web !5) in the 2026-10-04 check-in.",
        )
    ]


@pytest.mark.parametrize(
    ("claimed_state", "note"),
    [
        (None, "Will start Monday"),
        (None, "will start Monday, MR is a placeholder"),
        (None, "Planning to pick it up after CHK-11"),
        ("not started", ""),
        ("to do", ""),
        (None, "Not started yet"),
        (None, "Blocked on CHK-17"),
        (None, "Waiting on the API contract"),
        (None, "No change"),
        (None, "Almost done"),
    ],
)
async def test_an_open_merge_request_does_not_move_an_issue_the_owner_says_is_not_started(
    claimed_state: str | None, note: str
) -> None:
    tracker = FakeIssueTracker(issues={"CHK-12": _issue("CHK-12", IssueState.TODO)})
    facts = FakeTimeSeriesRepository(facts=[_storefront_mr5()])
    service, _, audit = _build(tracker=tracker, default_enabled=True, facts=facts)
    claim = IssueClaim(issue_key="CHK-12", claimed_state=claimed_state, note=note)

    results = await service.apply_from_checkin(
        tenant_id=_TENANT, developer_id=_DEV, correlation_id=_CORRELATION, claims=[claim]
    )

    assert [row for row in results if row.target_state == "in_progress"] == []
    assert tracker.transitions == []
    assert [a for a in audit.audits.values() if a.target_state == "in_progress"] == []


async def test_a_bare_mention_with_an_open_merge_request_waits_for_an_always_ask_yes() -> None:
    tracker = _MovingIssueTracker(issues={"CHK-12": _issue("CHK-12", IssueState.TODO)})
    facts = FakeTimeSeriesRepository(facts=[_storefront_mr5()])
    service, _, _ = _build(
        tracker=tracker,
        default_enabled=True,
        consent=WriteBackConsent.ALWAYS_ASK,
        facts=facts,
        graph=await _named_store("Zoe Almeida"),
    )
    claim = IssueClaim(issue_key="CHK-12", note="")

    [proposed] = await service.apply_from_checkin(
        tenant_id=_TENANT, developer_id=_DEV, correlation_id=_CORRELATION, claims=[claim]
    )
    assert tracker.transitions == []
    [applied] = await service.resolve_consent_reply(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        reply_text="yes",
        claims=[claim],
        reported_on=date(2026, 10, 4),
    )

    assert (proposed.status, proposed.target_state) == (WriteBackStatus.PROPOSED, "in_progress")
    assert (applied.status, applied.target_state) == (WriteBackStatus.APPLIED, "in_progress")
    assert tracker.transitions == [(_TENANT, "CHK-12", "in_progress")]
    assert tracker.comments[-1][2] == (
        "Moved to In Progress by OpenProgram: Zoe Almeida reported work on it "
        "(acme/storefront-web !5) in the 2026-10-04 check-in."
    )


@pytest.mark.parametrize(
    ("state", "merge_request"),
    [
        pytest.param(IssueState.IN_PROGRESS, _storefront_mr5(), id="already-in-progress"),
        pytest.param(IssueState.DONE, _storefront_mr5(), id="done"),
        pytest.param(IssueState.TODO, _storefront_mr5(state="merged"), id="merged-not-open"),
        pytest.param(IssueState.TODO, _storefront_mr5(state="closed"), id="closed"),
    ],
)
async def test_an_open_merge_request_moves_only_a_to_do_issue_and_only_while_open(
    state: IssueState, merge_request: FactEvent
) -> None:
    tracker = FakeIssueTracker(issues={"CHK-12": _issue("CHK-12", state)})
    facts = FakeTimeSeriesRepository(facts=[merge_request])
    service, _, audit = _build(tracker=tracker, default_enabled=True, facts=facts)

    results = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=[IssueClaim(issue_key="CHK-12", note="")],
    )

    assert results == []
    assert tracker.transitions == []
    assert audit.audits == {}


async def test_someone_elses_to_do_issue_with_an_open_merge_request_is_not_moved() -> None:
    tracker = FakeIssueTracker(
        issues={"CHK-12": _issue("CHK-12", IssueState.TODO, assignee="dev-zoe")}
    )
    facts = FakeTimeSeriesRepository(facts=[_storefront_mr5()])
    service, _, _ = _build(tracker=tracker, default_enabled=True, facts=facts)

    [row] = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=[IssueClaim(issue_key="CHK-12", note="Reviewing it later today.")],
    )

    assert (row.status, row.source) == (WriteBackStatus.DECLINED, NOT_OWNER_SOURCE)
    assert tracker.transitions == []


async def test_naming_the_open_merge_request_moves_the_issue_it_is_for() -> None:
    tracker = _MovingIssueTracker(
        issues={
            "CHK-11": _issue("CHK-11", IssueState.IN_PROGRESS),
            "CHK-12": _issue("CHK-12", IssueState.TODO),
        }
    )
    facts = FakeTimeSeriesRepository(facts=[_storefront_mr5()])
    service, _, _ = _build(
        tracker=tracker,
        default_enabled=True,
        facts=facts,
        graph=await _named_store("Zoe Almeida"),
    )
    claim = IssueClaim(issue_key="CHK-11", note="Still on it; opened storefront-web !5 meanwhile.")

    rows = await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=[claim],
        reported_on=date(2026, 10, 4),
    )

    assert [(row.issue_key, row.status, row.target_state) for row in rows] == [
        ("CHK-12", WriteBackStatus.APPLIED, "in_progress")
    ]
    assert tracker.transitions == [(_TENANT, "CHK-12", "in_progress")]


async def test_naming_someone_elses_open_merge_request_leaves_no_row() -> None:
    tracker = FakeIssueTracker(
        issues={
            "CHK-11": _issue("CHK-11", IssueState.IN_PROGRESS),
            "CHK-12": _issue("CHK-12", IssueState.TODO, assignee="dev-zoe"),
        }
    )
    facts = FakeTimeSeriesRepository(facts=[_storefront_mr5()])
    service, _, audit = _build(tracker=tracker, default_enabled=True, facts=facts)
    claim = IssueClaim(issue_key="CHK-11", note="Reviewed storefront-web !5 for Zoe.")

    results = await service.apply_from_checkin(
        tenant_id=_TENANT, developer_id=_DEV, correlation_id=_CORRELATION, claims=[claim]
    )

    assert results == []
    assert audit.audits == {}
    assert tracker.transitions == []


# --- G1: a consent answer, issue by issue ---


@pytest.mark.parametrize(
    ("text", "keys", "expected"),
    [
        # Liam, R1 12:17:01.
        (
            "yes for CHK-3. CHK-4 I haven't actually started yet, so leave that one as is for now.",
            ["CHK-3", "CHK-4"],
            {"CHK-3": "affirm", "CHK-4": "decline"},
        ),
        ("yes", ["CHK-3", "CHK-4"], {"CHK-3": "affirm", "CHK-4": "affirm"}),
        ("no", ["CHK-3", "CHK-4"], {"CHK-3": "decline", "CHK-4": "decline"}),
        ("yes, do it", ["PO-1"], {"PO-1": "affirm"}),
        (
            "yes for CHK-3 and no for CHK-4",
            ["CHK-3", "CHK-4"],
            {"CHK-3": "affirm", "CHK-4": "decline"},
        ),
        ("yes for CHK-3 and CHK-4", ["CHK-3", "CHK-4"], {"CHK-3": "affirm", "CHK-4": "affirm"}),
        ("CHK-3 yes, CHK-4 no", ["CHK-3", "CHK-4"], {"CHK-3": "affirm", "CHK-4": "decline"}),
        ("only CHK-3", ["CHK-3", "CHK-4"], {"CHK-3": "affirm", "CHK-4": "decline"}),
        ("yes but wait on CHK-4", ["CHK-3", "CHK-4"], {"CHK-3": "affirm", "CHK-4": "decline"}),
        (
            "leave CHK-4, go ahead with CHK-3",
            ["CHK-3", "CHK-4"],
            {"CHK-3": "affirm", "CHK-4": "decline"},
        ),
        # Unsure about one issue: that one stays pending.
        ("Not sure about CHK-4, yes for CHK-3", ["CHK-3", "CHK-4"], {"CHK-3": "affirm"}),
        ("yes for CHK-3", ["CHK-3", "CHK-4"], {"CHK-3": "affirm"}),
        # Not an answer: nothing is decided.
        ("yes but no", ["CHK-3"], {}),
        ("hmm", ["CHK-3"], {}),
        ("maybe later", ["CHK-3"], {}),
        ("No blockers, CHK-3 still in review", ["CHK-3"], {}),
        ("Yes, and CHK-5 is done now", ["CHK-3"], {}),
    ],
)
def test_consent_answer_is_read_issue_by_issue(
    text: str, keys: list[str], expected: dict[str, str]
) -> None:
    assert interpret_consent_answer(text, keys) == expected


async def test_per_issue_answer_resolves_only_what_it_answers() -> None:
    tracker = FakeIssueTracker(
        issues={key: _issue(key, IssueState.TODO) for key in ("CHK-3", "CHK-4")}
    )
    service, _, _ = _build(
        tracker=tracker, default_enabled=True, consent=WriteBackConsent.ALWAYS_ASK
    )
    await service.apply_from_checkin(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        claims=[
            IssueClaim(issue_key="CHK-3", claimed_state="in review"),
            IssueClaim(issue_key="CHK-4", claimed_state="in progress"),
        ],
    )

    results = await service.resolve_consent_reply(
        tenant_id=_TENANT,
        developer_id=_DEV,
        correlation_id=_CORRELATION,
        reply_text="yes for CHK-3",
    )

    assert [(row.issue_key, row.status) for row in results] == [("CHK-3", WriteBackStatus.APPLIED)]
    assert [
        row.issue_key for row in await service.list_pending_proposals(_TENANT, _CORRELATION)
    ] == ["CHK-4"]
    expired = await service.expire_pending_proposals(
        tenant_id=_TENANT, developer_id=_DEV, correlation_id=_CORRELATION
    )
    assert [(row.issue_key, row.status, row.source) for row in expired] == [
        ("CHK-4", WriteBackStatus.EXPIRED, "checkin_closed")
    ]
    assert await service.list_pending_proposals(_TENANT, _CORRELATION) == []
    assert tracker.transitions == [(_TENANT, "CHK-3", "in_review")]
