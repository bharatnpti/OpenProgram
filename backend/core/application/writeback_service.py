from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from typing import Literal
from uuid import uuid4

import structlog

from core.application.authorization import AuthorizationPolicy, Capability
from core.application.merge_request_links import (
    MERGE_REQUEST_FACT_SOURCE,
    is_open_merge_request,
    merge_request_label,
    merge_requests_by_issue_key,
)
from core.application.sync_services import record_issue_as_synced
from core.domain.auth import Principal, Role
from core.domain.errors import ProviderUnavailable
from core.domain.integrations import Issue, IssueState
from core.domain.status import IssueClaim, WriteBackConsent
from core.domain.writeback import (
    WriteBackAdoption,
    WriteBackAudit,
    WriteBackStatus,
    WriteBackTarget,
)
from core.ports.issue_tracker import IssueTracker
from core.ports.repositories import (
    GraphRepository,
    IdentityLinkRepository,
    StatusRepository,
    TimeSeriesRepository,
    WriteBackAuditRepository,
    WriteBackConfigRepository,
)

# ``source`` of the ``declined`` row recorded when the ownership gate refuses a
# write: the issue is assigned to someone else, or to nobody. No Jira call is
# made for it; the row only shows why the claim did not reach the tracker.
NOT_OWNER_SOURCE = "not_owner"
UNASSIGNED_SOURCE = "unassigned"
# ``source`` of the ``expired`` row that resolves a confirmed proposal whose
# issue the tracker already shows in the target state: nothing is written.
NO_CHANGE_SOURCE = "no_change"
# ``source`` of the ``declined`` row recorded when a ``done`` write is held back
# because a merge request naming the issue is still open (or a draft): the work
# is not merged, so the tracker stays where it is. No Jira call is made for it.
OPEN_MR_SOURCE = "open_mr"
# How many synced merge request facts the open merge request check reads.
_MERGE_REQUEST_FACT_SCAN_LIMIT = 5000

_logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class WriteBackDryRun:
    """What ``apply_from_checkin`` would do right away for a person's claims.

    ``written``: issue keys it would write. ``held_for_open_mr``: issue keys whose
    ``done`` it would hold back, each with the open merge requests naming it
    (``insights-pipeline !1``).
    """

    written: frozenset[str] = frozenset()
    held_for_open_mr: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class OpenMergeRequestHold:
    """A ``done`` claim held back by open merge requests, for the reply to name."""

    issue_key: str
    current_state: str
    merge_requests: tuple[str, ...]


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

    A ``done`` target is also held back while a merge request naming the issue
    in OpenProgram's synced facts (the key whole in its source branch or title,
    as the ``merged_issue_open`` drift reads it) is still open or a draft:
    nothing is transitioned, proposed or commented on, and a ``declined`` row
    with source ``open_mr`` records why. Merged or closed requests, or none at
    all, leave ``done`` as it was; other targets are not affected.

    ``always_ask`` records a ``proposed`` audit row and writes nothing until the
    developer answers yes/no -- ``resolve_consent_reply`` then applies (yes) or
    records ``declined`` (no); ``never`` and a closed system gate do nothing.
    Every applied write captures the prior state for reversibility and is
    idempotent on (issue_key, target_state, correlation_id).

    The target is always a canonical ``WriteBackTarget`` (see
    ``canonical_target_state``), never the parser's free text, and a claim the
    tracker already shows is a no-op: no transition, no comment, no proposal and
    no row.

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
        time_series_repository: TimeSeriesRepository | None = None,
        graph_repository: GraphRepository | None = None,
        authorization_policy: AuthorizationPolicy | None = None,
        writeback_enabled_default: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._issue_tracker = issue_tracker
        self._audit = audit_repository
        self._config = config_repository
        self._status = status_repository
        self._identity_links = identity_link_repository
        self._facts = time_series_repository
        self._graph = graph_repository
        self._policy = authorization_policy or AuthorizationPolicy()
        self._default_enabled = writeback_enabled_default
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    async def system_gate_open(self, tenant_id: str) -> bool:
        override = await self._config.get_writeback_enabled(tenant_id)
        return self._default_enabled if override is None else override

    async def standing_consent_open(self, tenant_id: str, developer_id: str) -> bool:
        """Whether this developer's claims on their own issues are written without asking.

        Read-only: the capability, system and consent gates only. Used to tell the
        check-in conversation that OpenProgram updates the tracker itself.
        """
        principal = Principal(
            tenant_id=tenant_id, subject=developer_id, roles=frozenset({Role.DEV})
        )
        if not self._policy.can(principal, Capability.WRITE_ISSUE_TRACKER):
            return False
        if not await self.system_gate_open(tenant_id):
            return False
        return await self._consent(tenant_id, developer_id) is WriteBackConsent.AUTO_APPLY

    async def auto_apply_issue_keys(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        claims: Sequence[IssueClaim],
    ) -> frozenset[str]:
        """Issue keys ``apply_from_checkin`` would write right away for these claims.

        A dry run with no write and no audit row: every gate, the ownership
        check, a canonical target, a real change of state and, for ``done``, no
        open merge request must all hold. The check-in conversation uses it so
        it never asks a person to update the tracker for an update OpenProgram
        is about to make itself.
        """
        return (
            await self.dry_run(tenant_id=tenant_id, developer_id=developer_id, claims=claims)
        ).written

    async def dry_run(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        claims: Sequence[IssueClaim],
    ) -> WriteBackDryRun:
        """The keys ``apply_from_checkin`` would write, and the ones an open MR holds back.

        No write and no audit row, and only for a person whose own claims are
        written without asking (``standing_consent_open``).
        """
        if not claims or not await self.standing_consent_open(tenant_id, developer_id):
            return WriteBackDryRun()
        keys: set[str] = set()
        held: dict[str, tuple[str, ...]] = {}
        for claim in claims:
            target_state = _target_state(claim)
            if target_state is None:
                continue
            issue = await self._read_issue(tenant_id, claim.issue_key)
            if issue is None or _already_in_target_state(issue, target_state):
                continue
            if await self._ownership_refusal(tenant_id, developer_id, issue) is not None:
                continue
            open_requests = await self._open_merge_requests(tenant_id, issue.key, target_state)
            if open_requests:
                held[claim.issue_key] = open_requests
            else:
                keys.add(claim.issue_key)
        return WriteBackDryRun(written=frozenset(keys), held_for_open_mr=held)

    async def open_merge_request_holds(
        self, tenant_id: str, rows: Sequence[WriteBackAudit]
    ) -> list[OpenMergeRequestHold]:
        """The open merge requests behind each ``declined``/``open_mr`` row, for the reply."""
        holds: list[OpenMergeRequestHold] = []
        for row in rows:
            if row.status is not WriteBackStatus.DECLINED or row.source != OPEN_MR_SOURCE:
                continue
            holds.append(
                OpenMergeRequestHold(
                    issue_key=row.issue_key,
                    current_state=row.before_state or "",
                    merge_requests=await self._open_merge_requests(
                        tenant_id, row.issue_key, WriteBackTarget.DONE.value
                    ),
                )
            )
        return holds

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
            row = await self._write_claim(
                tenant_id=tenant_id,
                developer_id=developer_id,
                correlation_id=correlation_id,
                claim=claim,
                target_state=target_state,
                consent=consent,
                source=source,
            )
            if row is not None:
                results.append(row)
        return results

    async def _write_claim(
        self,
        *,
        tenant_id: str,
        developer_id: str,
        correlation_id: str,
        claim: IssueClaim,
        target_state: str,
        consent: WriteBackConsent,
        source: str,
    ) -> WriteBackAudit | None:
        """Apply or propose one claim whose consent gates already hold, or record why not."""
        row_source = "standing_consent" if consent is WriteBackConsent.AUTO_APPLY else source
        issue = await self._read_issue(tenant_id, claim.issue_key)
        if issue is None:
            if await self._synced_copy_shows(tenant_id, claim.issue_key, target_state):
                # The read failed (R2: a network stall), but OpenProgram's own
                # copy already shows the claim, so there was nothing to write:
                # a no-op, not a failure (N14). No row, as for a live no-op.
                return None
            # Without the issue there is no owner to check, so nothing is
            # written or proposed; the row says the tracker read failed.
            return await self._record(
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
        refusal = await self._ownership_refusal(tenant_id, developer_id, issue)
        if refusal is not None:
            return await self._refuse(
                tenant_id, developer_id, correlation_id, issue, target_state, refusal
            )
        if _already_in_target_state(issue, target_state):
            # The tracker already says so: no transition, no comment, no
            # proposal and no row. Only a real change reaches the tracker.
            return None
        if await self._open_merge_requests(tenant_id, issue.key, target_state):
            # Done while a merge request for it is still open would make the
            # tracker wrong: nothing is written or proposed; the row says why.
            return await self._refuse(
                tenant_id, developer_id, correlation_id, issue, target_state, OPEN_MR_SOURCE
            )
        if consent is WriteBackConsent.AUTO_APPLY:
            # Standing consent -- apply immediately, tagging the provenance so
            # the audit distinguishes it from an interactively-confirmed write.
            return await self._apply(
                tenant_id,
                developer_id,
                correlation_id,
                claim,
                target_state,
                row_source,
                before_state=issue.state.value,
            )
        return await self._propose(  # WriteBackConsent.ALWAYS_ASK
            tenant_id,
            developer_id,
            correlation_id,
            claim,
            target_state,
            row_source,
            before_state=issue.state.value,
        )

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
                results.append(
                    await self._apply_confirmed(tenant_id, developer_id, correlation_id, proposal)
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

    async def _apply_confirmed(
        self,
        tenant_id: str,
        developer_id: str,
        correlation_id: str,
        proposal: WriteBackAudit,
    ) -> WriteBackAudit:
        """Write a proposal the developer said yes to, re-checking owner and state.

        Every row keeps the proposal's own target so it resolves the proposal;
        the tracker is asked for the canonical state (older proposals stored
        free text, which is normalised here when it can be).
        """
        # The issue may have been reassigned since it was proposed; a yes never
        # writes to an issue the developer no longer owns. A failed read records
        # ``failed`` and leaves the proposal pending.
        issue = await self._read_issue(tenant_id, proposal.issue_key)
        if issue is None:
            return await self._record(
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
        refusal = await self._ownership_refusal(tenant_id, developer_id, issue)
        if refusal is not None:
            return await self._refuse(
                tenant_id, developer_id, correlation_id, issue, proposal.target_state, refusal
            )
        tracker_state = _stored_target_for_tracker(proposal.target_state)
        if _already_in_target_state(issue, tracker_state):
            # Already there: resolve the proposal without touching the tracker.
            return await self._record(
                tenant_id=tenant_id,
                developer_id=developer_id,
                correlation_id=correlation_id,
                issue_key=proposal.issue_key,
                target_state=proposal.target_state,
                status=WriteBackStatus.EXPIRED,
                before_state=issue.state.value,
                after_state=issue.state.value,
                comment=None,
                source=NO_CHANGE_SOURCE,
            )
        if await self._open_merge_requests(tenant_id, issue.key, tracker_state):
            # A yes never moves the issue to done while a merge request for it
            # is open; the declined row resolves the proposal.
            return await self._refuse(
                tenant_id,
                developer_id,
                correlation_id,
                issue,
                proposal.target_state,
                OPEN_MR_SOURCE,
            )
        claim = IssueClaim(
            issue_key=proposal.issue_key,
            claimed_state=proposal.target_state,
            note=proposal.comment or "",
        )
        return await self._apply(
            tenant_id,
            developer_id,
            correlation_id,
            claim,
            proposal.target_state,
            "consent_reply",
            before_state=issue.state.value,
            tracker_state=tracker_state,
        )

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
        await self._record_written_issue(audit.tenant_id, audit.issue_key, audit.before_state)
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

    async def _synced_copy_shows(self, tenant_id: str, issue_key: str, target_state: str) -> bool:
        """Whether OpenProgram's synced copy of the issue already shows ``target_state``.

        Read only when the tracker cannot be read: the issue sync's task node
        (its ``state``, read as ``_already_in_target_state`` reads the tracker).
        False when no graph is wired, the issue was never synced, or the copy
        shows another state -- the claim then still records the failed read.
        """
        if self._graph is None:
            return False
        try:
            target = WriteBackTarget(target_state)
            node = await self._graph.get_node(tenant_id, issue_key)
        except (ValueError, ProviderUnavailable):
            return False
        if node is None:
            return False
        state = node.metadata.get("state")
        return isinstance(state, str) and state == _TARGET_ISSUE_STATES[target].value

    async def _record_written_issue(
        self, tenant_id: str, issue_key: str, written_state: str
    ) -> Issue | None:
        """Bring OpenProgram's own copy of an issue up to date right after a write (N9).

        The issue is read back from the tracker and stored exactly as the next
        issue sync stores it (task node, assignment edge, issue fact on the sync's
        key), so views and drift no longer wait up to an hour for that sync. If
        the read-back fails, only the synced node's ``state`` moves to the state
        written. Best-effort: the tracker write already happened and is audited,
        so a failure here is logged and the next sync repairs the copy.
        """
        if self._graph is None:
            return None
        issue = await self._read_issue(tenant_id, issue_key)
        try:
            if issue is not None:
                await record_issue_as_synced(
                    issue,
                    graph_repository=self._graph,
                    time_series_repository=self._facts,
                    identity_link_repository=self._identity_links,
                    observed_at=self._clock(),
                )
            else:
                await self._record_written_state(tenant_id, issue_key, written_state)
        except Exception:  # pragma: no cover - defensive; the next sync repairs it
            _logger.warning(
                "writeback_local_issue_update_failed", tenant_id=tenant_id, issue_key=issue_key
            )
        return issue

    async def _record_written_state(
        self, tenant_id: str, issue_key: str, written_state: str
    ) -> None:
        assert self._graph is not None
        node = await self._graph.get_node(tenant_id, issue_key)
        state = _written_issue_state(written_state)
        if node is None or state is None:
            return
        await self._graph.upsert_node(
            replace(node, metadata={**node.metadata, "state": state.value})
        )

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

    async def _open_merge_requests(
        self, tenant_id: str, issue_key: str, target_state: str
    ) -> tuple[str, ...]:
        """Open or draft merge requests naming the issue, when the target is ``done``.

        Read from OpenProgram's synced merge request facts with the matcher the
        ``merged_issue_open`` drift uses: the key whole in the source branch or
        title, the latest fact of each request. Empty for every other target,
        and when no fact store is wired, so those writes behave as before.
        """
        if target_state != WriteBackTarget.DONE.value or self._facts is None:
            return ()
        facts = await self._facts.list_recent_facts(
            tenant_id,
            sources=(MERGE_REQUEST_FACT_SOURCE,),
            limit=_MERGE_REQUEST_FACT_SCAN_LIMIT,
        )
        linked = merge_requests_by_issue_key(facts, {issue_key}).get(issue_key, [])
        return tuple(
            sorted(merge_request_label(fact) for fact in linked if is_open_merge_request(fact))
        )

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
        tracker_state: str | None = None,
    ) -> WriteBackAudit:
        # ``target_state`` keys the audit row; ``tracker_state`` (default: the
        # same) is what the tracker is asked to move to.
        to_state = tracker_state or target_state
        comment = claim.note.strip() or None
        try:
            await self._issue_tracker.transition(tenant_id, claim.issue_key, to_state)
            await self._record_written_issue(tenant_id, claim.issue_key, to_state)
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
            after_state=to_state,
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


# --- Canonical target states -------------------------------------------------
#
# A claim's state is free text from the reply parser ("on track", "merged and
# ready to close", "starting", "acceptance criteria drafted, pending review").
# Only wording that clearly names one state becomes a target; anything that
# says "no change", hedges about the future, negates, or names two states at
# once becomes no target at all, so nothing is written for it.


class _NoTarget(Enum):
    NO_TRANSITION = "no_transition"  # wording that rules a transition out
    UNRECOGNISED = "unrecognised"  # wording that names no state


_TODO = WriteBackTarget.TODO
_IN_PROGRESS = WriteBackTarget.IN_PROGRESS
_IN_REVIEW = WriteBackTarget.IN_REVIEW
_BLOCKED = WriteBackTarget.BLOCKED
_DONE = WriteBackTarget.DONE

_EXACT_STATES: dict[str, WriteBackTarget] = {
    "todo": _TODO,
    "to do": _TODO,
    "open": _TODO,
    "backlog": _TODO,
    "new": _TODO,
    "not started": _TODO,
    "not yet started": _TODO,
    "yet to start": _TODO,
    "in progress": _IN_PROGRESS,
    "inprogress": _IN_PROGRESS,
    "wip": _IN_PROGRESS,
    "work in progress": _IN_PROGRESS,
    "started": _IN_PROGRESS,
    "ongoing": _IN_PROGRESS,
    "doing": _IN_PROGRESS,
    "active": _IN_PROGRESS,
    "underway": _IN_PROGRESS,
    "in development": _IN_PROGRESS,
    "draft": _IN_PROGRESS,
    "in review": _IN_REVIEW,
    "review": _IN_REVIEW,
    "under review": _IN_REVIEW,
    "code review": _IN_REVIEW,
    "in code review": _IN_REVIEW,
    "pending review": _IN_REVIEW,
    "awaiting review": _IN_REVIEW,
    "ready for review": _IN_REVIEW,
    "up for review": _IN_REVIEW,
    "blocked": _BLOCKED,
    "on hold": _BLOCKED,
    "stuck": _BLOCKED,
    "done": _DONE,
    "closed": _DONE,
    "resolved": _DONE,
    "complete": _DONE,
    "completed": _DONE,
    "finished": _DONE,
    "merged": _DONE,
    "shipped": _DONE,
    "released": _DONE,
    "ready to close": _DONE,
    "ready to be closed": _DONE,
}
_STATE_SIGNALS: tuple[tuple[WriteBackTarget, re.Pattern[str]], ...] = (
    (
        _DONE,
        re.compile(
            r"\b(?:done|closed|close it|ready to close|ready to be closed|can be closed|"
            r"resolved|complete|completed|finished|merged|shipped|released)\b"
        ),
    ),
    (_IN_REVIEW, re.compile(r"\b(?:review|reviewing)\b")),
    (_BLOCKED, re.compile(r"\b(?:blocked|on hold|impeded|stuck)\b")),
    (
        _IN_PROGRESS,
        re.compile(
            r"\b(?:in progress|wip|work in progress|started|ongoing|underway|"
            r"in development|working on|implementing|draft)\b"
        ),
    ),
    (_TODO, re.compile(r"\b(?:to do|todo|backlog)\b")),
)
# "On track", "no change" and friends: a status, not a request to move anything.
_NO_TRANSITION_PHRASES = frozenset({"fine", "ok", "okay", "good", "all good", "same", "as is"})
_NO_TRANSITION_WORDING = re.compile(
    r"\b(?:on track|no change|no changes|unchanged|same as|as before|as planned|"
    r"no update|nothing new|steady|going well)\b"
)
# A state in the future (or only partly reached) is not the state now:
# "starting" (as in "starting CHK-4 next"), "MR to be opened", "almost done".
_HEDGED_WORDING = re.compile(
    r"\b(?:almost|nearly|soon|tomorrow|next|will|going to|about to|plan to|planning to|"
    r"expected|expect|expecting|hopefully|should|aim|aiming|later|partially|partly|"
    r"mostly|might|maybe|probably|eventually|shortly|eta|by eod|by end of|starting|"
    r"by (?:mon|tues|wednes|thurs|fri|satur|sun)day)\b"
    r"|(?<!ready )\bto be (?:opened|raised|created|merged|reviewed|closed|done|started)\b"
)
# Something still outstanding ("merged, waiting for QA"), unless it is the review.
_WAITING_WORDING = re.compile(r"\b(?:waiting|awaiting|pending)\b")
# A negation close before a state word ("not done", "isn't merged yet").
_NEGATED_STATE = re.compile(
    r"\b(?:not|no|never|isn t|hasn t|haven t|aren t|wasn t|didn t|don t|doesn t|"
    r"cannot|can t|won t|yet to)\s+(?:\w+\s+){0,2}"
    r"(?:done|closed|close|merged|resolved|complete|completed|finished|shipped|released|"
    r"started|begun|review|reviewed|progress|blocked|stuck)\b"
)
_NOT_STARTED_WORDING = re.compile(
    r"\b(?:not started|not yet started|yet to start|hasn t started|haven t started|not begun)\b"
)
_NON_WORD = re.compile(r"[^a-z0-9]+")


def canonical_target_state(text: str | None) -> WriteBackTarget | None:
    """The canonical state a claimed state names, or ``None`` when it names none.

    Pure and deterministic. ``None`` covers both wording that rules a
    transition out ("on track", "no change", "almost done", "not done") and
    wording that names no state ("branch pushed").
    """
    verdict = _classify_claimed_state(text or "")
    return verdict if isinstance(verdict, WriteBackTarget) else None


def _state_signals(phrase: str) -> set[WriteBackTarget]:
    return {target for target, pattern in _STATE_SIGNALS if pattern.search(phrase)}


def _classify_claimed_state(text: str) -> WriteBackTarget | _NoTarget:
    phrase = " ".join(_NON_WORD.split(text.lower())).strip()
    if not phrase:
        return _NoTarget.UNRECOGNISED
    exact = _EXACT_STATES.get(phrase)
    if exact is not None:
        return exact
    if (
        phrase in _NO_TRANSITION_PHRASES
        or _NO_TRANSITION_WORDING.search(phrase)
        or _HEDGED_WORDING.search(phrase)
    ):
        return _NoTarget.NO_TRANSITION
    if _NOT_STARTED_WORDING.search(phrase):
        # "Not started (yet)" is the one negation that names a state.
        rest = _NOT_STARTED_WORDING.sub(" ", phrase)
        if _state_signals(rest) <= {_TODO} and not _NEGATED_STATE.search(rest):
            return _TODO
        return _NoTarget.NO_TRANSITION
    if _NEGATED_STATE.search(phrase):
        return _NoTarget.NO_TRANSITION
    signals = _state_signals(phrase)
    if _WAITING_WORDING.search(phrase) and _IN_REVIEW not in signals:
        return _NoTarget.NO_TRANSITION
    if signals == {_IN_PROGRESS, _IN_REVIEW}:
        return _IN_REVIEW
    if len(signals) == 1:
        return next(iter(signals))
    # Two states at once ("code done, in review") is no target; none is unknown.
    return _NoTarget.UNRECOGNISED if not signals else _NoTarget.NO_TRANSITION


def _target_state(claim: IssueClaim) -> str | None:
    """The canonical target for a claim, or ``None`` when nothing should move.

    The stated state decides. Wording that rules a transition out wins over the
    parser's ``claimed_done`` flag; only wording that names no state at all
    falls back to it.
    """
    if claim.claimed_state and claim.claimed_state.strip():
        verdict = _classify_claimed_state(claim.claimed_state)
        if isinstance(verdict, WriteBackTarget):
            return verdict.value
        if verdict is _NoTarget.NO_TRANSITION:
            return None
    if claim.claimed_done:
        return WriteBackTarget.DONE.value
    return None


# Review statuses read as in progress (the tracker adapters map them so), so an
# "in review" claim on an in-progress issue is no change OpenProgram can see.
_TARGET_ISSUE_STATES: dict[WriteBackTarget, IssueState] = {
    WriteBackTarget.TODO: IssueState.TODO,
    WriteBackTarget.IN_PROGRESS: IssueState.IN_PROGRESS,
    WriteBackTarget.IN_REVIEW: IssueState.IN_PROGRESS,
    WriteBackTarget.BLOCKED: IssueState.BLOCKED,
    WriteBackTarget.DONE: IssueState.DONE,
}


def _written_issue_state(written_state: str) -> IssueState | None:
    """The issue state a written target (canonical, or a reverted prior state) reads as."""
    try:
        return _TARGET_ISSUE_STATES[WriteBackTarget(written_state)]
    except ValueError:
        pass
    try:
        return IssueState(written_state)
    except ValueError:
        return None


def _already_in_target_state(issue: Issue, target_state: str) -> bool:
    try:
        target = WriteBackTarget(target_state)
    except ValueError:
        return False  # an older free-text proposal: nothing to compare with
    return issue.state is _TARGET_ISSUE_STATES[target]


def _stored_target_for_tracker(target_state: str) -> str:
    """The canonical form of a stored target; older free text kept when it has none."""
    try:
        return WriteBackTarget(target_state).value
    except ValueError:
        canonical = canonical_target_state(target_state)
        return canonical.value if canonical is not None else target_state


_TARGET_LABELS: dict[WriteBackTarget, str] = {
    WriteBackTarget.TODO: "To Do",
    WriteBackTarget.IN_PROGRESS: "In Progress",
    WriteBackTarget.IN_REVIEW: "In Review",
    WriteBackTarget.BLOCKED: "Blocked",
    WriteBackTarget.DONE: "Done",
}


def target_state_label(target_state: str) -> str:
    """A person-facing name for a stored target ("in_review" -> "In Review")."""
    try:
        return _TARGET_LABELS[WriteBackTarget(target_state)]
    except ValueError:
        return target_state
