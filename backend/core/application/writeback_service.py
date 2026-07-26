from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import uuid4

from core.application.authorization import AuthorizationPolicy, Capability
from core.domain.auth import Principal, Role
from core.domain.errors import ProviderUnavailable
from core.domain.integrations import IssueState
from core.domain.status import IssueClaim, WriteBackConsent
from core.domain.writeback import WriteBackAudit, WriteBackStatus
from core.ports.issue_tracker import IssueTracker
from core.ports.repositories import (
    StatusRepository,
    WriteBackAuditRepository,
    WriteBackConfigRepository,
)


class WriteBackService:
    """Apply audited, gated, idempotent, reversible writes to the issue tracker.

    A write happens only if all three default-deny gates hold:

    1. System gate -- the tenant override (or the injected settings default) is on.
    2. Capability gate -- the acting principal holds ``WRITE_ISSUE_TRACKER``.
    3. Consent gate -- the developer's standing consent is ``auto_apply``.

    ``always_ask`` records a ``proposed`` audit row and writes nothing (the
    interactive DM confirmation loop is a documented follow-up); ``never`` and a
    closed system gate do nothing. Every applied write captures the prior state
    for reversibility and is idempotent on (issue_key, target_state,
    correlation_id).

    This is the single sanctioned application-layer caller of the issue tracker
    write methods; the architecture-boundary guard is scoped to allow it.
    """

    def __init__(
        self,
        *,
        issue_tracker: IssueTracker,
        audit_repository: WriteBackAuditRepository,
        config_repository: WriteBackConfigRepository,
        status_repository: StatusRepository,
        authorization_policy: AuthorizationPolicy | None = None,
        writeback_enabled_default: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._issue_tracker = issue_tracker
        self._audit = audit_repository
        self._config = config_repository
        self._status = status_repository
        self._policy = authorization_policy or AuthorizationPolicy()
        self._default_enabled = writeback_enabled_default
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    async def system_gate_open(self, tenant_id: str) -> bool:
        override = await self._config.get_writeback_enabled(tenant_id)
        return self._default_enabled if override is None else override

    async def apply_from_checkin(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        correlation_id: str,
        claims: Sequence[IssueClaim],
        source: str = "checkin",
    ) -> list[WriteBackAudit]:
        principal = Principal(
            tenant_id=tenant_id,
            subject=developer_id,
            roles=frozenset({Role.DEV}),
        )
        if not self._policy.can(principal, Capability.WRITE_ISSUE_TRACKER):
            return []
        if not await self.system_gate_open(tenant_id):
            return []
        consent = await self._consent(tenant_id, developer_id)
        if consent is WriteBackConsent.NEVER:
            return []

        results: list[WriteBackAudit] = []
        for claim in claims:
            target_state = _target_state(claim)
            if target_state is None:
                continue
            existing = await self._audit.find_existing(
                tenant_id, claim.issue_key, target_state, correlation_id
            )
            if existing is not None:
                continue
            if consent is WriteBackConsent.AUTO_APPLY:
                results.append(
                    await self._apply(
                        tenant_id, developer_id, correlation_id, claim, target_state, source
                    )
                )
            else:  # WriteBackConsent.ALWAYS_ASK
                results.append(
                    await self._propose(
                        tenant_id, developer_id, correlation_id, claim, target_state, source
                    )
                )
        return results

    async def revert(self, audit: WriteBackAudit) -> WriteBackAudit | None:
        """Reverse a previously applied write back to its captured prior state."""
        if audit.status is not WriteBackStatus.APPLIED or audit.before_state is None:
            return None
        try:
            await self._issue_tracker.transition(
                audit.tenant_id, audit.issue_key, audit.before_state
            )
        except ProviderUnavailable:
            return await self._record(
                tenant_id=audit.tenant_id,
                developer_id=audit.developer_id,
                correlation_id=audit.correlation_id,
                issue_key=audit.issue_key,
                target_state=audit.before_state,
                status=WriteBackStatus.FAILED,
                before_state=audit.after_state,
                after_state=audit.after_state,
                comment=None,
                source="revert",
            )
        return await self._record(
            tenant_id=audit.tenant_id,
            developer_id=audit.developer_id,
            correlation_id=audit.correlation_id,
            issue_key=audit.issue_key,
            target_state=audit.before_state,
            status=WriteBackStatus.REVERTED,
            before_state=audit.after_state,
            after_state=audit.before_state,
            comment=None,
            source="revert",
        )

    async def _consent(self, tenant_id: str, developer_id: str) -> WriteBackConsent:
        preference = await self._status.checkin_preference_for(tenant_id, developer_id)
        if preference is None:
            return WriteBackConsent.ALWAYS_ASK
        return preference.write_back_consent

    async def _current_state(self, tenant_id: str, issue_key: str) -> str | None:
        try:
            issue = await self._issue_tracker.get_issue(tenant_id, issue_key)
        except (ProviderUnavailable, KeyError):
            return None
        return issue.state.value

    async def _apply(
        self,
        tenant_id: str,
        developer_id: str,
        correlation_id: str,
        claim: IssueClaim,
        target_state: str,
        source: str,
    ) -> WriteBackAudit:
        before_state = await self._current_state(tenant_id, claim.issue_key)
        comment = claim.note.strip() or None
        try:
            await self._issue_tracker.transition(tenant_id, claim.issue_key, target_state)
            if comment is not None:
                await self._issue_tracker.add_comment(tenant_id, claim.issue_key, comment)
        except ProviderUnavailable:
            return await self._record(
                tenant_id=tenant_id,
                developer_id=developer_id,
                correlation_id=correlation_id,
                issue_key=claim.issue_key,
                target_state=target_state,
                status=WriteBackStatus.FAILED,
                before_state=before_state,
                after_state=before_state,
                comment=comment,
                source=source,
            )
        return await self._record(
            tenant_id=tenant_id,
            developer_id=developer_id,
            correlation_id=correlation_id,
            issue_key=claim.issue_key,
            target_state=target_state,
            status=WriteBackStatus.APPLIED,
            before_state=before_state,
            after_state=target_state,
            comment=comment,
            source=source,
        )

    async def _propose(
        self,
        tenant_id: str,
        developer_id: str,
        correlation_id: str,
        claim: IssueClaim,
        target_state: str,
        source: str,
    ) -> WriteBackAudit:
        before_state = await self._current_state(tenant_id, claim.issue_key)
        return await self._record(
            tenant_id=tenant_id,
            developer_id=developer_id,
            correlation_id=correlation_id,
            issue_key=claim.issue_key,
            target_state=target_state,
            status=WriteBackStatus.PROPOSED,
            before_state=before_state,
            after_state=target_state,
            comment=claim.note.strip() or None,
            source=source,
        )

    async def _record(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        correlation_id: str,
        issue_key: str,
        target_state: str,
        status: WriteBackStatus,
        before_state: str | None,
        after_state: str | None,
        comment: str | None,
        source: str,
    ) -> WriteBackAudit:
        audit = WriteBackAudit(
            id=uuid4().hex,
            tenant_id=tenant_id,
            developer_id=developer_id,
            issue_key=issue_key,
            correlation_id=correlation_id,
            status=status,
            target_state=target_state,
            before_state=before_state,
            after_state=after_state,
            comment=comment,
            source=source,
            created_at=self._clock(),
        )
        await self._audit.record(audit)
        return audit


def _target_state(claim: IssueClaim) -> str | None:
    if claim.claimed_state:
        state = claim.claimed_state.strip()
        if state:
            return state
    if claim.claimed_done:
        return IssueState.DONE.value
    return None
