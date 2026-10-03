from __future__ import annotations

from datetime import UTC, datetime

from core.application.authorization import AuthorizationPolicy, Capability
from core.application.writeback_service import (
    NOT_OWNER_SOURCE,
    UNASSIGNED_SOURCE,
    WriteBackService,
    interpret_consent_reply,
)
from core.domain.auth import Principal
from core.domain.errors import ProviderUnavailable
from core.domain.identity import IdentityLink
from core.domain.integrations import Issue, IssueState, UserRef
from core.domain.status import CheckInPreference, IssueClaim, WriteBackConsent
from core.domain.writeback import WriteBackStatus
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
