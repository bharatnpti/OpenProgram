from __future__ import annotations

from datetime import UTC, datetime

from core.application.authorization import AuthorizationPolicy, Capability
from core.application.writeback_service import WriteBackService
from core.domain.auth import Principal
from core.domain.errors import ProviderUnavailable
from core.domain.integrations import Issue, IssueState
from core.domain.status import CheckInPreference, IssueClaim, WriteBackConsent
from core.domain.writeback import WriteBackStatus
from tests.contract.fakes import (
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


def _build(
    *,
    tracker: FakeIssueTracker | None = None,
    consent: WriteBackConsent = WriteBackConsent.AUTO_APPLY,
    tenant_override: bool | None = None,
    default_enabled: bool = False,
    policy: AuthorizationPolicy | None = None,
) -> tuple[WriteBackService, FakeIssueTracker, FakeWriteBackAuditRepository]:
    issue_tracker = tracker or FakeIssueTracker(
        issues={
            "PO-1": Issue(
                tenant_id=_TENANT,
                key="PO-1",
                title="Wire write-back",
                state=IssueState.IN_PROGRESS,
            )
        }
    )
    audit = FakeWriteBackAuditRepository()
    config = FakeWriteBackConfigRepository()
    if tenant_override is not None:
        config.enabled[_TENANT] = tenant_override
    status = FakeStatusRepository()
    status.checkin_preferences[(_TENANT, _DEV)] = CheckInPreference(
        tenant_id=_TENANT,
        developer_id=_DEV,
        write_back_consent=consent,
    )
    service = WriteBackService(
        issue_tracker=issue_tracker,
        audit_repository=audit,
        config_repository=config,
        status_repository=status,
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
    tracker = _FailingIssueTracker(
        issues={
            "PO-1": Issue(
                tenant_id=_TENANT,
                key="PO-1",
                title="Wire write-back",
                state=IssueState.IN_PROGRESS,
            )
        }
    )
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
