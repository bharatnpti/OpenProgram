from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from core.application.authorization import AuthorizationPolicy, Capability
from core.domain.auth import Principal, Role
from core.domain.errors import ProviderUnavailable
from core.domain.integrations import Issue, IssueState
from core.domain.status import IssueClaim, WriteBackConsent
from core.domain.writeback import WriteBackAdoption, WriteBackAudit, WriteBackStatus
from core.ports.issue_tracker import IssueTracker
from core.ports.repositories import (
    IdentityLinkRepository,
    StatusRepository,
    WriteBackAuditRepository,
    WriteBackConfigRepository,
)

# ``source`` of the ``declined`` row recorded when the ownership gate refuses a
# write: the issue is assigned to someone else, or to nobody. No Jira call is
# made for it; the row only shows why the claim did not reach the tracker.
NOT_OWNER_SOURCE = "not_owner"
UNASSIGNED_SOURCE = "unassigned"


class WriteBackService:
    """Apply audited, gated, idempotent, reversible writes to the issue tracker.

    A write happens only if all four default-deny gates hold:

    1. System gate -- the tenant override (or the injected settings default) is on.
    2. Capability gate -- the acting principal holds ``WRITE_ISSUE_TRACKER``.
    3. Consent gate -- the developer's standing consent is ``auto_apply``.
    4. Ownership gate -- the developer is the issue's current assignee in the
       tracker, through their identity link. An issue assigned to someone else
       or to nobody is never transitioned or commented on, whoever reported it;
       a ``declined`` row with source ``not_owner``/``unassigned`` records why.

    ``always_ask`` records a ``proposed`` audit row and writes nothing until the
    developer answers yes/no -- ``resolve_consent_reply`` then applies (yes) or
    records ``declined`` (no); ``never`` and a closed system gate do nothing.
    Every applied write captures the prior state for reversibility and is
    idempotent on (issue_key, target_state, correlation_id).

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
        identity_link_repository: IdentityLinkRepository | None = None,
        authorization_policy: AuthorizationPolicy | None = None,
        writeback_enabled_default: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._issue_tracker = issue_tracker
        self._audit = audit_repository
        self._config = config_repository
        self._status = status_repository
        self._identity_links = identity_link_repository
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
            row_source = "standing_consent" if consent is WriteBackConsent.AUTO_APPLY else source
            issue = await self._read_issue(tenant_id, claim.issue_key)
            if issue is None:
                # Without the issue there is no owner to check, so nothing is
                # written or proposed; the row says the tracker read failed.
                results.append(
                    await self._record(
                        tenant_id=tenant_id,
                        developer_id=developer_id,
                        correlation_id=correlation_id,
                        issue_key=claim.issue_key,
                        target_state=target_state,
                        status=WriteBackStatus.FAILED,
                        before_state=None,
                        after_state=None,
                        comment=None,
                        source=row_source,
                    )
                )
                continue
            refusal = await self._ownership_refusal(tenant_id, developer_id, issue)
            if refusal is not None:
                results.append(
                    await self._refuse(
                        tenant_id, developer_id, correlation_id, issue, target_state, refusal
                    )
                )
                continue
            if consent is WriteBackConsent.AUTO_APPLY:
                # Standing consent -- apply immediately, tagging the provenance so
                # the audit distinguishes it from an interactively-confirmed write.
                results.append(
                    await self._apply(
                        tenant_id,
                        developer_id,
                        correlation_id,
                        claim,
                        target_state,
                        row_source,
                        before_state=issue.state.value,
                    )
                )
            else:  # WriteBackConsent.ALWAYS_ASK
                results.append(
                    await self._propose(
                        tenant_id,
                        developer_id,
                        correlation_id,
                        claim,
                        target_state,
                        row_source,
                        before_state=issue.state.value,
                    )
                )
        return results

    async def list_pending_proposals(
        self, tenant_id: str, correlation_id: str
    ) -> list[WriteBackAudit]:
        """Proposals for this check-in still awaiting a developer yes/no.

        A proposal is pending when a ``proposed`` row exists for an
        ``(issue_key, target_state)`` pair and no later ``applied``/``declined``/
        ``reverted``/``expired`` row has resolved that same pair. Resolving a
        proposal appends a terminal row, so this naturally becomes empty once
        answered -- the basis for idempotent resolution.
        """
        rows = await self._audit.list_writeback_by_correlation(tenant_id, correlation_id)
        resolved = {
            (row.issue_key, row.target_state)
            for row in rows
            if row.status
            in (
                WriteBackStatus.APPLIED,
                WriteBackStatus.DECLINED,
                WriteBackStatus.REVERTED,
                WriteBackStatus.EXPIRED,
            )
        }
        pending: list[WriteBackAudit] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            if row.status is not WriteBackStatus.PROPOSED:
                continue
            key = (row.issue_key, row.target_state)
            if key in resolved or key in seen:
                continue
            seen.add(key)
            pending.append(row)
        return pending

    async def resolve_consent_reply(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        correlation_id: str,
        reply_text: str,
    ) -> list[WriteBackAudit]:
        """Resolve pending write-back proposals from a developer's yes/no answer.

        Returns the audit rows produced -- ``applied`` on an affirmative answer,
        ``declined`` on a negative one, and nothing when the answer is unclear,
        when there is no pending proposal, or when the gates have since closed
        (idempotent: once a proposal is resolved it is no longer pending). All
        writes still flow through the single audited ``_apply`` path.
        """
        pending = await self.list_pending_proposals(tenant_id, correlation_id)
        if not pending:
            return []
        intent = interpret_consent_reply(reply_text)
        if intent == "unclear":
            return []
        principal = Principal(
            tenant_id=tenant_id,
            subject=developer_id,
            roles=frozenset({Role.DEV}),
        )
        if not self._policy.can(principal, Capability.WRITE_ISSUE_TRACKER):
            return []
        if not await self.system_gate_open(tenant_id):
            return []
        if await self._consent(tenant_id, developer_id) is WriteBackConsent.NEVER:
            return []

        results: list[WriteBackAudit] = []
        for proposal in pending:
            if intent == "affirm":
                claim = IssueClaim(
                    issue_key=proposal.issue_key,
                    claimed_state=proposal.target_state,
                    note=proposal.comment or "",
                )
                # The issue may have been reassigned since it was proposed; a yes
                # never writes to an issue the developer no longer owns. A failed
                # read records ``failed`` and leaves the proposal pending.
                issue = await self._read_issue(tenant_id, proposal.issue_key)
                if issue is None:
                    results.append(
                        await self._record(
                            tenant_id=tenant_id,
                            developer_id=developer_id,
                            correlation_id=correlation_id,
                            issue_key=proposal.issue_key,
                            target_state=proposal.target_state,
                            status=WriteBackStatus.FAILED,
                            before_state=proposal.before_state,
                            after_state=proposal.before_state,
                            comment=None,
                            source="consent_reply",
                        )
                    )
                    continue
                refusal = await self._ownership_refusal(tenant_id, developer_id, issue)
                if refusal is not None:
                    results.append(
                        await self._refuse(
                            tenant_id,
                            developer_id,
                            correlation_id,
                            issue,
                            proposal.target_state,
                            refusal,
                        )
                    )
                    continue
                results.append(
                    await self._apply(
                        tenant_id,
                        developer_id,
                        correlation_id,
                        claim,
                        proposal.target_state,
                        "consent_reply",
                        before_state=issue.state.value,
                    )
                )
            else:  # intent == "decline"
                results.append(
                    await self._record(
                        tenant_id=tenant_id,
                        developer_id=developer_id,
                        correlation_id=correlation_id,
                        issue_key=proposal.issue_key,
                        target_state=proposal.target_state,
                        status=WriteBackStatus.DECLINED,
                        before_state=proposal.before_state,
                        after_state=proposal.before_state,
                        comment=None,
                        source="consent_reply",
                    )
                )
        return results

    async def revert(self, audit: WriteBackAudit) -> WriteBackAudit | None:
        """Reverse a previously applied write back to its captured prior state.

        Returns ``None`` when the audit is not an applied write with a captured
        prior state, or when this exact applied write has already been reverted
        (idempotent -- reverting twice never double-applies the reverse write).
        """
        if audit.status is not WriteBackStatus.APPLIED or audit.before_state is None:
            return None
        if await self._already_reverted(audit):
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

    async def get_audit(self, tenant_id: str, audit_id: str) -> WriteBackAudit | None:
        """Look up a single audit row scoped to the tenant, or ``None``."""
        return await self._audit.get_writeback_audit(tenant_id, audit_id)

    async def adoption(
        self,
        tenant_id: str,
        *,
        limit: int = 5,
        since: datetime | None = None,
    ) -> WriteBackAdoption:
        """Count applied write-backs (Jira updates landed via check-in).

        ``recent`` carries identifier-only summaries; the caller must never
        surface the developer note or any raw DM/reply content.
        """
        count = await self._audit.count_applied_writebacks(tenant_id, since)
        recent = await self._audit.list_applied_writebacks(tenant_id, limit, since)
        return WriteBackAdoption(applied_count=count, recent=tuple(recent))

    async def _already_reverted(self, audit: WriteBackAudit) -> bool:
        # The applied row stays APPLIED forever (append-only log); a revert adds a
        # new REVERTED row keyed on the same correlation with target = prior state.
        prior = await self._audit.list_for_issue(audit.tenant_id, audit.issue_key)
        return any(
            row.status is WriteBackStatus.REVERTED
            and row.correlation_id == audit.correlation_id
            and row.target_state == audit.before_state
            for row in prior
        )

    async def _consent(self, tenant_id: str, developer_id: str) -> WriteBackConsent:
        preference = await self._status.checkin_preference_for(tenant_id, developer_id)
        if preference is None:
            return WriteBackConsent.ALWAYS_ASK
        return preference.write_back_consent

    async def _read_issue(self, tenant_id: str, issue_key: str) -> Issue | None:
        try:
            return await self._issue_tracker.get_issue(tenant_id, issue_key)
        except (ProviderUnavailable, KeyError):
            return None

    async def _ownership_refusal(
        self, tenant_id: str, developer_id: str, issue: Issue
    ) -> str | None:
        """Why this developer may not write to ``issue``, or ``None`` if they own it.

        The owner is the issue's assignee as the tracker reports it now -- the
        same field the issue sync stores. Assignment edges in the graph are not
        used: the sync only ever adds them, so a reassigned issue keeps the old
        assignee's edge. The tracker knows its own account ids, so the developer
        is matched through their identity link, as the sync maps assignees.
        """
        if issue.assignee is None:
            return UNASSIGNED_SOURCE
        if issue.assignee.external_id in await self._tracker_account_ids(tenant_id, developer_id):
            return None
        return NOT_OWNER_SOURCE

    async def _tracker_account_ids(self, tenant_id: str, developer_id: str) -> set[str]:
        # A member whose id is the tracker account id itself, or the account the
        # identity link names (resolved read-only from the linked email if the
        # account id is not stored yet). Mirrors the issue sync's assignee map.
        accounts = {developer_id}
        if self._identity_links is None:
            return accounts
        link = await self._identity_links.get_identity_link(tenant_id, developer_id)
        if link is None:
            return accounts
        if link.jira_account_id:
            accounts.add(link.jira_account_id)
        elif link.jira_email:
            try:
                found = await self._issue_tracker.find_user_by_email(tenant_id, link.jira_email)
            except ProviderUnavailable:
                found = None
            if found is not None:
                accounts.add(found.external_id)
        return accounts

    async def _refuse(
        self,
        tenant_id: str,
        developer_id: str,
        correlation_id: str,
        issue: Issue,
        target_state: str,
        reason: str,
    ) -> WriteBackAudit:
        # No tracker call at all: no transition, no comment. The claim itself
        # stays in the check-in signals as status context.
        return await self._record(
            tenant_id=tenant_id,
            developer_id=developer_id,
            correlation_id=correlation_id,
            issue_key=issue.key,
            target_state=target_state,
            status=WriteBackStatus.DECLINED,
            before_state=issue.state.value,
            after_state=issue.state.value,
            comment=None,
            source=reason,
        )

    async def _apply(
        self,
        tenant_id: str,
        developer_id: str,
        correlation_id: str,
        claim: IssueClaim,
        target_state: str,
        source: str,
        *,
        before_state: str | None,
    ) -> WriteBackAudit:
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
        *,
        before_state: str | None,
    ) -> WriteBackAudit:
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


_AFFIRM_TOKENS = frozenset(
    {"yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay", "apply", "confirm", "confirmed"}
)
_AFFIRM_PHRASES = ("do it", "go ahead", "please do", "sounds good")
_DECLINE_TOKENS = frozenset(
    {"no", "n", "nope", "nah", "don't", "dont", "stop", "cancel", "decline", "skip"}
)
_DECLINE_PHRASES = ("do not", "leave it", "not now")


def interpret_consent_reply(text: str) -> Literal["affirm", "decline", "unclear"]:
    """Classify a developer's free-text reply as a yes/no to a write-back proposal.

    A deliberately small keyword/phrase allow-list: ``affirm`` for yes-like
    replies, ``decline`` for no-like replies, and ``unclear`` when neither or
    both are present (an ambiguous reply leaves the proposal pending -- never
    written). This is pure and provider-neutral so the reply pipeline can call it
    without any I/O.
    """
    words = re.findall(r"[a-z']+", text.lower())
    if not words:
        return "unclear"
    tokens = set(words)
    bigrams = {f"{a} {b}" for a, b in zip(words, words[1:], strict=False)}
    affirm = bool(tokens & _AFFIRM_TOKENS) or bool(bigrams & set(_AFFIRM_PHRASES))
    decline = bool(tokens & _DECLINE_TOKENS) or bool(bigrams & set(_DECLINE_PHRASES))
    if affirm == decline:
        return "unclear"
    return "affirm" if affirm else "decline"


def _target_state(claim: IssueClaim) -> str | None:
    if claim.claimed_state:
        state = claim.claimed_state.strip()
        if state:
            return state
    if claim.claimed_done:
        return IssueState.DONE.value
    return None
