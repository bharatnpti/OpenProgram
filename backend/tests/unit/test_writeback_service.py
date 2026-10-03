from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.application.authorization import AuthorizationPolicy, Capability
from core.application.writeback_service import (
    NO_CHANGE_SOURCE,
    NOT_OWNER_SOURCE,
    UNASSIGNED_SOURCE,
    WriteBackService,
    canonical_target_state,
    interpret_consent_reply,
    target_state_label,
)
from core.domain.auth import Principal
from core.domain.errors import ProviderUnavailable
from core.domain.identity import IdentityLink
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.status import CheckInPreference, IssueClaim, WriteBackConsent
from core.domain.writeback import WriteBackAudit, WriteBackStatus, WriteBackTarget
from tests.contract.fakes import (
    FakeIdentityLinkRepository,
    FakeIssueTracker,
    FakeStatusRepository,
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
        authorization_policy=policy,
        writeback_enabled_default=default_enabled,
        clock=lambda: datetime(2026, 7, 25, 9, 0, tzinfo=UTC),
    )
    return service, issue_tracker, audit


def _claims() -> list[IssueClaim]:
    return [IssueClaim(issue_key="PO-1", claimed_done=True, note="shipped it")]


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
    assert tracker.comments == [(_TENANT, "PO-1", "shipped it")]
    assert len(results) == 1
    recorded = results[0]
    assert recorded.status is WriteBackStatus.APPLIED
    assert recorded.before_state == IssueState.IN_PROGRESS.value
    assert recorded.after_state == IssueState.DONE.value
    assert recorded.comment == "shipped it"
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
    assert tracker.comments == [(_TENANT, "PO-1", "shipped it")]
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
    assert tracker.comments == [(_TENANT, "IDP-5", "Fix merged, all acceptance criteria met.")]
    [row] = results
    assert row.status is WriteBackStatus.APPLIED
    assert (row.target_state, row.before_state, row.after_state) == (
        "done",
        "in_progress",
        "done",
    )


async def test_on_track_is_a_noop_with_no_comment() -> None:
    tracker = FakeIssueTracker(issues={"CHK-16": _issue("CHK-16", IssueState.TODO)})
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


def test_target_state_label_names_canonical_states_and_keeps_free_text() -> None:
    assert target_state_label("in_review") == "In Review"
    assert target_state_label("done") == "Done"
    assert target_state_label("merged and ready to close") == "merged and ready to close"
