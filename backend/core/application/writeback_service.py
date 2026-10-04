from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from enum import Enum
from typing import Literal
from uuid import uuid4

import structlog

from core.application.authorization import AuthorizationPolicy, Capability
from core.application.merge_request_links import (
    ISSUE_KEY,
    MERGE_REQUEST_FACT_SOURCE,
    MergeRequestIndex,
    is_merged_merge_request,
    is_open_merge_request,
    merge_request_label,
    merge_request_reference,
    merge_requests_by_issue_key,
)
from core.application.sync_services import record_issue_as_synced
from core.domain.auth import Principal, Role
from core.domain.cross_person import merge_request_refs_in
from core.domain.errors import ProviderUnavailable
from core.domain.graph import FactEvent
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
# ``source`` of the ``declined`` row a person's "no" (for all, or for one issue)
# records, and of the ``expired`` row that closes a proposal nobody answered
# before the check-in closed. Neither makes a tracker call.
CONSENT_REPLY_SOURCE = "consent_reply"
CHECKIN_CLOSED_SOURCE = "checkin_closed"
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

    async def asks_before_writing(self, tenant_id: str, developer_id: str) -> bool:
        """Whether this developer's claims are proposed and wait for their yes/no.

        Read-only: the capability and system gates, and consent ``always_ask``
        (the default when no preference is stored).
        """
        principal = Principal(
            tenant_id=tenant_id, subject=developer_id, roles=frozenset({Role.DEV})
        )
        if not self._policy.can(principal, Capability.WRITE_ISSUE_TRACKER):
            return False
        if not await self.system_gate_open(tenant_id):
            return False
        return await self._consent(tenant_id, developer_id) is WriteBackConsent.ALWAYS_ASK

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
        for claim, _ in await self._with_mentioned_merge_requests(tenant_id, claims):
            target_state = await self._target_for(tenant_id, claim)
            if target_state is None:
                continue
            issue = await self._read_issue(tenant_id, claim.issue_key)
            if issue is None or _already_in_target_state(issue, target_state):
                continue
            if _only_moves_a_todo_issue(claim) and issue.state is not IssueState.TODO:
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
        reported_on: date | None = None,
    ) -> list[WriteBackAudit]:
        """Write, propose or refuse each claim behind the four gates.

        ``reported_on`` is the check-in's date, named in the comment an applied
        write posts (the clock's date when not given).
        """
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
        for claim, mention_only in await self._with_mentioned_merge_requests(tenant_id, claims):
            target_state = await self._target_for(tenant_id, claim)
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
                reported_on=reported_on,
                mention_only=mention_only,
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
        reported_on: date | None = None,
        mention_only: bool = False,
    ) -> WriteBackAudit | None:
        """Apply or propose one claim whose consent gates already hold, or record why not.

        ``mention_only``: the check-in named only a merge request for the issue
        (N28). Such a claim is never refused on the record: an issue that is
        not the person's, or that cannot be read, is left alone with no row.
        """
        row_source = "standing_consent" if consent is WriteBackConsent.AUTO_APPLY else source
        # "Started" or "on track" with no state of its own, or a mention of an
        # issue an open merge request is for, moves only an issue that is still
        # To Do (N21, N28); on any other issue it is no change at all.
        todo_only = _only_moves_a_todo_issue(claim)
        issue = await self._read_issue(tenant_id, claim.issue_key)
        if issue is None and mention_only:
            return None
        if issue is None:
            synced = await self._synced_state(tenant_id, claim.issue_key)
            if synced is not None and (
                synced is _issue_state_for(target_state)
                or (todo_only and synced is not IssueState.TODO)
            ):
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
        if todo_only and issue.state is not IssueState.TODO:
            return None
        refusal = await self._ownership_refusal(tenant_id, developer_id, issue)
        if refusal is not None and mention_only:
            return None
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
                reported_on=reported_on,
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
        claims: Sequence[IssueClaim] = (),
        reported_on: date | None = None,
    ) -> list[WriteBackAudit]:
        """Resolve pending write-back proposals from a developer's yes/no answer.

        ``claims`` (the check-in's) word the comment of each applied write as
        the claim path does; ``reported_on`` is the check-in's date.

        The answer may differ per issue ("yes for CHK-3, leave CHK-4"): see
        ``interpret_consent_answer``. Returns the audit rows produced --
        ``applied`` for a yes, ``declined`` for a no, and nothing for an issue
        the answer leaves unclear (it stays pending), when there is no pending
        proposal, or when the gates have since closed (idempotent: once a
        proposal is resolved it is no longer pending). All writes still flow
        through the single audited ``_apply`` path, so the owner check, the
        open merge request hold and the canonical target apply to every yes.
        """
        pending = await self.list_pending_proposals(tenant_id, correlation_id)
        if not pending:
            return []
        decisions = interpret_consent_answer(
            reply_text, [proposal.issue_key for proposal in pending]
        )
        if not decisions:
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
            intent = decisions.get(proposal.issue_key)
            if intent is None:
                continue  # this issue's answer is still open
            if intent == "affirm":
                results.append(
                    await self._apply_confirmed(
                        tenant_id,
                        developer_id,
                        correlation_id,
                        proposal,
                        claim=_claim_for(claims, proposal),
                        reported_on=reported_on,
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
                        source=CONSENT_REPLY_SOURCE,
                    )
                )
        return results

    async def expire_pending_proposals(
        self, *, tenant_id: str, developer_id: str, correlation_id: str
    ) -> list[WriteBackAudit]:
        """Close every proposal still waiting for a yes/no when the check-in closes.

        Each gets an ``expired`` row with source ``checkin_closed``; nothing is
        written to the tracker, and a later yes no longer finds it pending.
        """
        return [
            await self._record(
                tenant_id=tenant_id,
                developer_id=developer_id,
                correlation_id=correlation_id,
                issue_key=proposal.issue_key,
                target_state=proposal.target_state,
                status=WriteBackStatus.EXPIRED,
                before_state=proposal.before_state,
                after_state=proposal.before_state,
                comment=None,
                source=CHECKIN_CLOSED_SOURCE,
            )
            for proposal in await self.list_pending_proposals(tenant_id, correlation_id)
        ]

    async def reply_outcomes(self, tenant_id: str, correlation_id: str) -> list[WriteBackAudit]:
        """What the check-in's write-back did, for the reply: one row per issue.

        The latest ``applied`` row, a ``declined`` row for an open merge request
        or a person's no, or the ``expired`` row of a proposal the check-in
        closed on. Refusals the person need not hear about (not their issue,
        unassigned) and failed reads are left out.
        """
        rows = await self._audit.list_writeback_by_correlation(tenant_id, correlation_id)
        latest: dict[str, WriteBackAudit] = {}
        for row in sorted(rows, key=lambda item: item.created_at):
            if (
                row.status is WriteBackStatus.APPLIED
                or (
                    row.status is WriteBackStatus.DECLINED
                    and row.source in (OPEN_MR_SOURCE, CONSENT_REPLY_SOURCE)
                )
                or (row.status is WriteBackStatus.EXPIRED and row.source == CHECKIN_CLOSED_SOURCE)
            ):
                latest[row.issue_key] = row
        return list(latest.values())

    async def _apply_confirmed(
        self,
        tenant_id: str,
        developer_id: str,
        correlation_id: str,
        proposal: WriteBackAudit,
        *,
        claim: IssueClaim | None = None,
        reported_on: date | None = None,
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
        confirmed = claim or IssueClaim(
            issue_key=proposal.issue_key, claimed_state=proposal.target_state
        )
        return await self._apply(
            tenant_id,
            developer_id,
            correlation_id,
            confirmed,
            proposal.target_state,
            "consent_reply",
            before_state=issue.state.value,
            tracker_state=tracker_state,
            reported_on=reported_on,
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

    async def _synced_state(self, tenant_id: str, issue_key: str) -> IssueState | None:
        """The state OpenProgram's synced copy of the issue shows, or ``None``.

        Read only when the tracker cannot be read: the issue sync's task node
        ``state``. ``None`` when no graph is wired or the issue was never synced;
        the claim then still records the failed read.
        """
        if self._graph is None:
            return None
        try:
            node = await self._graph.get_node(tenant_id, issue_key)
        except ProviderUnavailable:
            return None
        state = node.metadata.get("state") if node is not None else None
        try:
            return IssueState(state) if isinstance(state, str) else None
        except ValueError:
            return None

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
        issue = await self._read_issue(tenant_id, issue_key)
        if self._graph is None:
            return issue
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
        if target_state != WriteBackTarget.DONE.value:
            return ()
        return await self._open_merge_requests_for(tenant_id, issue_key)

    async def _open_merge_requests_for(self, tenant_id: str, issue_key: str) -> tuple[str, ...]:
        """Open or draft merge requests naming the issue, as ``repo !id``; () without facts."""
        if self._facts is None:
            return ()
        linked = merge_requests_by_issue_key(
            await self._merge_request_facts(tenant_id), {issue_key}
        ).get(issue_key, [])
        return tuple(
            sorted(merge_request_label(fact) for fact in linked if is_open_merge_request(fact))
        )

    async def _merge_request_facts(self, tenant_id: str) -> list[FactEvent]:
        if self._facts is None:
            return []
        return await self._facts.list_recent_facts(
            tenant_id,
            sources=(MERGE_REQUEST_FACT_SOURCE,),
            limit=_MERGE_REQUEST_FACT_SCAN_LIMIT,
        )

    async def _target_for(self, tenant_id: str, claim: IssueClaim) -> str | None:
        """The claim's canonical target, with an open merge request as start evidence (N28).

        A claim that names no state and does not say work started still targets
        in progress when a merge request naming the issue is open (or a draft)
        and its wording leaves a start open: in R4 Zoe named CHK-12 and opened
        storefront-web !5 for it. Like any started claim it moves only a To Do
        issue, behind every gate. Wording that says the start lies ahead or has
        not happened, or that the issue is blocked, waiting or done, rules it out.
        """
        target = _claim_target(claim)
        if target is not None or _rules_out_a_start(claim):
            return target
        if await self._open_merge_requests_for(tenant_id, claim.issue_key):
            return WriteBackTarget.IN_PROGRESS.value
        return None

    async def _with_mentioned_merge_requests(
        self, tenant_id: str, claims: Sequence[IssueClaim]
    ) -> list[tuple[IssueClaim, bool]]:
        """The claims, then a bare mention of each issue an open merge request they name is for.

        Each comes with whether it is such a mention. "Opened storefront-web
        !5" names the merge request, not its issue; the request's source or
        title does ("CHK-12 Promo code validation"). An issue no claim names
        gets a claim with no wording of its own, which only the open merge
        request can give a target (``_target_for``). Only a merge request named
        with its repository counts.
        """
        given = [(claim, False) for claim in claims]
        refs = {
            (ref.repo, ref.number)
            for claim in claims
            for ref in merge_request_refs_in(f"{claim.claimed_state or ''} {claim.note}")
            if ref.repo is not None
        }
        if not refs or self._facts is None:
            return given
        index = MergeRequestIndex.from_facts(await self._merge_request_facts(tenant_id))
        named = {claim.issue_key.strip().upper() for claim in claims}
        mentioned: list[tuple[IssueClaim, bool]] = []
        for ref in sorted(refs):
            fact = index.by_ref.get(ref)
            if fact is None or not is_open_merge_request(fact):
                continue
            for key in _issue_keys_named_by(fact):
                if key not in named:
                    named.add(key)
                    mentioned.append((IssueClaim(issue_key=key), True))
        return [*given, *mentioned]

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
        reported_on: date | None = None,
    ) -> WriteBackAudit:
        # ``target_state`` keys the audit row; ``tracker_state`` (default: the
        # same) is what the tracker is asked to move to.
        to_state = tracker_state or target_state
        try:
            await self._issue_tracker.transition(tenant_id, claim.issue_key, to_state)
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
                comment=None,
                source=source,
            )
        written = await self._record_written_issue(tenant_id, claim.issue_key, to_state)
        comment: str | None = await self._write_back_comment(
            tenant_id,
            developer_id,
            claim,
            to_state,
            written=written,
            reported_on=reported_on,
        )
        try:
            await self._issue_tracker.add_comment(tenant_id, claim.issue_key, comment or "")
        except ProviderUnavailable:
            # The issue did move; only the note is missing. The row says so by
            # carrying no comment, and the write stays applied (and revertible).
            _logger.warning(
                "writeback_comment_failed", tenant_id=tenant_id, issue_key=claim.issue_key
            )
            comment = None
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

    async def _write_back_comment(
        self,
        tenant_id: str,
        developer_id: str,
        claim: IssueClaim,
        to_state: str,
        *,
        written: Issue | None,
        reported_on: date | None,
    ) -> str:
        """The note an applied write posts: what moved, who reported it, when (N20).

        Neutral and factual, built only from OpenProgram's own data: the state
        the issue now has, the person's name, the canonical state they reported,
        the merge requests that back it, and the check-in date. It never copies
        the reply or the parser's note, which the write itself can make false
        ("Jira not updated yet").
        """
        return write_back_comment(
            destination=_destination_label(written, to_state),
            person=await self._person_name(tenant_id, developer_id),
            reported=_reported_phrase(claim, to_state),
            merge_requests=await self._backing_merge_requests(tenant_id, claim.issue_key, to_state),
            reported_on=reported_on or self._clock().date(),
        )

    async def _person_name(self, tenant_id: str, developer_id: str) -> str:
        # The member record's name; never a raw chat or tracker id.
        if self._graph is not None:
            node = await self._graph.get_node(tenant_id, developer_id)
            if node is not None and node.name and node.name != developer_id:
                return node.name
        return "the assignee"

    async def _backing_merge_requests(
        self, tenant_id: str, issue_key: str, to_state: str
    ) -> tuple[str, ...]:
        """The merge requests naming the issue that back the new state, as repo !id.

        Merged ones for ``done``; open (or draft) ones for review and in progress;
        none for any other state or when no fact store is wired.
        """
        if self._facts is None:
            return ()
        try:
            target = WriteBackTarget(to_state)
        except ValueError:
            return ()
        if target is WriteBackTarget.DONE:
            backs = is_merged_merge_request
        elif target in (WriteBackTarget.IN_REVIEW, WriteBackTarget.IN_PROGRESS):
            backs = is_open_merge_request
        else:
            return ()
        facts = await self._facts.list_recent_facts(
            tenant_id,
            sources=(MERGE_REQUEST_FACT_SOURCE,),
            limit=_MERGE_REQUEST_FACT_SCAN_LIMIT,
        )
        linked = merge_requests_by_issue_key(facts, {issue_key}).get(issue_key, [])
        return tuple(sorted(merge_request_reference(fact) for fact in linked if backs(fact)))

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
            comment=None,
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


# A per-issue answer: "yes for CHK-3. CHK-4 I haven't started, so leave that one
# as is" (Liam, R1). Clauses split on sentence ends, commas and "but".
_CLAUSE_SPLIT = re.compile(r"[.;!?\n]+|,|\bbut\b|\bthough\b")
_ANSWER_TOKEN = re.compile(
    r"\b(?:"
    r"(?P<unsure>not sure|unsure|maybe|perhaps|let me check|no idea)"
    # Status wording, not an answer: "no blockers", "not started yet".
    r"|(?P<status>no (?:blockers?|changes?|updates?|issues?|problems?|eta|news|movement|"
    r"reviewers?|progress)|not (?:yet )?(?:started|blocked|done|merged|finished)|"
    r"(?:haven t|hasn t|have not|has not) (?:\w+ )?(?:started|begun))"
    r"|(?P<decline>don t|dont|do not|not now|not yet|hold off|as is|nope|nah|no|n|stop|"
    r"cancel|decline|skip|leave|keep|hold|wait|untouched|not)"
    r"|(?P<affirm>go ahead|do it|please do|sounds good|update it|move it|go for it|yes|y|"
    r"yeah|yep|yup|sure|ok|okay|apply|confirm|confirmed|correct)"
    r"|(?P<only>only|just)"
    r")\b"
)
_ISSUE_KEY_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9_]*-\d+(?![0-9])")
# A longer message is more status, not an answer to a yes/no question.
_MAX_ANSWER_WORDS = 40

_Answer = Literal["affirm", "decline", "unclear"]


def interpret_consent_answer(
    text: str, issue_keys: Sequence[str]
) -> dict[str, Literal["affirm", "decline"]]:
    """Read a reply to a write-back consent question, issue by issue.

    Returns a decision for each of ``issue_keys`` the reply answers; an empty
    dict when it answers none (unclear, or not an answer). Deterministic:

    * An answer word decides the issues it is said about: "yes for CHK-3 and no
      for CHK-4", "CHK-3 yes".
    * A clause naming no issue decides the issues the clause before it named
      ("CHK-4 I haven't started, so leave that one as is"), or, before any issue
      is named, every issue ("yes", "no").
    * "Only CHK-3" says yes to CHK-3 and no to the others.
    * Mixed or unsure wording decides nothing, so the issue stays pending: an
      unclear answer never writes.
    * A reply naming an issue that is not pending, or a long one, is more status,
      not an answer, so nothing is decided.
    """
    keys = {key.upper(): key for key in issue_keys}
    if not _reads_as_an_answer(text, keys):
        return {}
    decided: dict[str, _Answer] = {}
    everyone: set[_Answer] = set()
    subject: list[str] = []
    for clause in _CLAUSE_SPLIT.split(text):
        clause_keys, keyless = _read_answer_clause(clause, keys, decided)
        if clause_keys:
            subject = clause_keys
        elif keyless is not None and subject:
            decided.update(dict.fromkeys(subject, keyless))
        elif keyless is not None:
            everyone.add(keyless)
    if len(everyone) == 1:
        (answer,) = everyone
        for key in keys.values():
            decided.setdefault(key, answer)
    return {key: answer for key, answer in decided.items() if answer != "unclear"}


def _reads_as_an_answer(text: str, keys: Mapping[str, str]) -> bool:
    """Short, and naming no issue but the ones asked about."""
    named = {match.upper() for match in _ISSUE_KEY_TOKEN.findall(text)}
    return bool(keys) and named <= set(keys) and len(text.split()) <= _MAX_ANSWER_WORDS


def _read_answer_clause(
    clause: str, keys: Mapping[str, str], decided: dict[str, _Answer]
) -> tuple[list[str], _Answer | None]:
    """Decide the issues one clause names; return them, or the clause's answer if it names none.

    "Only CHK-3" says yes to CHK-3 and no to every other issue asked about.
    """
    clause_keys: list[str] = []
    waiting: list[str] = []
    current: _Answer | None = None
    seen: set[_Answer] = set()
    only = False
    for _, kind, value in _answer_tokens(clause, keys):
        if kind == "key":
            clause_keys.append(value)
            if current is None:
                waiting.append(value)
            else:
                decided[value] = current
        elif kind == "only":
            only = True
        else:
            current = _KIND_ANSWERS[kind]
            seen.add(current)
            decided.update(dict.fromkeys(waiting, current))
            waiting = []
    if clause_keys and only and seen <= {"affirm"}:
        decided.update(
            {key: "affirm" if key in clause_keys else "decline" for key in keys.values()}
        )
    if clause_keys or not seen:
        return clause_keys, None
    return [], (seen.pop() if len(seen) == 1 else "unclear")


_KIND_ANSWERS: dict[str, _Answer] = {
    "unsure": "unclear",
    "affirm": "affirm",
    "decline": "decline",
}


def _answer_tokens(clause: str, keys: Mapping[str, str]) -> list[tuple[int, str, str]]:
    """The clause's issue keys and answer words, in the order they are written."""
    # Same length as the clause, so token positions line up with the key matches.
    blanked = _ISSUE_KEY_TOKEN.sub(lambda match: " " * len(match.group()), clause)
    lowered = blanked.lower().replace("'", " ").replace("\u2019", " ")
    tokens = [
        (match.start(), "key", keys[match.group().upper()])
        for match in _ISSUE_KEY_TOKEN.finditer(clause)
    ]
    tokens.extend(
        (match.start(), match.lastgroup or "", match.group())
        for match in _ANSWER_TOKEN.finditer(lowered)
        if match.lastgroup != "status"
    )
    return sorted(tokens)


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


# "Started", "working on", "on track": work on the issue is under way, though the
# wording names no state of its own (N21: Asha "CHK-16 on track", Hana "outline
# started", both To Do in Jira; N28: Zoe "picking up CHK-12", Asha "agenda and
# numbers drafted"). Conservative: anything negated, planned, modal or about a
# future start ("planning to start Monday", "will pick it up after CHK-14",
# "starting next") is not a start.
_WORK_STARTED = re.compile(
    r"\b(?:started|started on|on track|working on|work in progress|in progress|wip|underway|"
    r"under way|ongoing|began|begun|picked up|picking up|picking (?:it|this|that) up|drafted)\b"
)
_NOT_A_START = re.compile(
    r"\b(?:will|shall|plan|plans|planned|planning|going to|about to|intend|intends|"
    r"hope|hoping|expect|expects|expected|should|would|could|might|may|to start|"
    r"to begin|to pick|start|starts|starting|tomorrow|next week|next sprint|later|"
    r"blocked|stuck|on hold|waiting|awaiting|pending|done|finished|complete|completed|merged|"
    r"not yet|yet to)\b"
)


def _claims_work_started(claim: IssueClaim) -> bool:
    """Whether the claim clearly says work on the issue has started.

    Reads the claimed state, or the parser's note when no state was claimed;
    neither is ever written anywhere.
    """
    text = (
        claim.claimed_state if claim.claimed_state and claim.claimed_state.strip() else claim.note
    )
    phrase = " ".join(_NON_WORD.split((text or "").lower())).strip()
    if not phrase or not _WORK_STARTED.search(phrase):
        return False
    return not (
        _NOT_A_START.search(phrase)
        or _NOT_STARTED_WORDING.search(phrase)
        or _NEGATED_STATE.search(phrase)
    )


def _only_moves_a_todo_issue(claim: IssueClaim) -> bool:
    """Whether a claim's in-progress target comes from the started rule (N21, N28).

    Such a claim names no state of its own: it says work started, or it names
    an issue an open merge request is for. Either way it moves only an issue
    that is still To Do. Asked only of a claim that has a target.
    """
    return _target_state(claim) is None


def _claim_target(claim: IssueClaim, *, open_merge_request: bool = False) -> str | None:
    """The canonical target of a claim: its stated state, else in progress if work started.

    The second case moves only an issue still To Do (``_only_moves_a_todo_issue``).
    ``open_merge_request``: a merge request naming the issue is open, which is
    evidence of a start unless the wording rules one out (N28).
    """
    target = _target_state(claim)
    if target is None and (
        _claims_work_started(claim) or (open_merge_request and not _rules_out_a_start(claim))
    ):
        return WriteBackTarget.IN_PROGRESS.value
    return target


# N28: what rules out reading an open merge request as a start. Narrower than
# _NOT_A_START, which also refuses wording about something else in the future
# ("MR expected shortly"): with the merge request open, only wording about the
# start itself, or about another state, counts against it.
_START_VERB = re.compile(
    r"\b(?:start|starts|starting|started|begin|begins|beginning|began|begun|pick|picks|"
    r"picking|picked|kick off|kicking off|work on|working on)\b"
)
_FUTURE_OR_MODAL = re.compile(
    r"\b(?:will|shall|plan|plans|planned|planning|going to|about to|intend|intends|hope|"
    r"hoping|expect|expects|expected|should|would|could|might|may|aim|aiming|tomorrow|"
    r"tonight|next|later|soon|shortly|after|once|when|monday|tuesday|wednesday|thursday|"
    r"friday|saturday|sunday)\b"
)
_OTHER_STATE = re.compile(
    r"\b(?:blocked|stuck|on hold|waiting|awaiting|pending|done|finished|complete|completed|"
    r"merged)\b"
)
_NO_CHANGE = re.compile(
    r"\b(?:no change|no changes|unchanged|same as|as before|no update|nothing new|not yet)\b"
)
_CLAUSE_BREAK = re.compile(r"[.;:!?,]+|\b(?:and|but|then)\b")


def _rules_out_a_start(claim: IssueClaim) -> bool:
    """Whether the claim's wording says work has not started, or is not what is happening.

    Reads the claimed state, or the parser's note when no state was claimed.
    A start ahead is a start verb with a future or modal word in the same
    clause ("will start Monday", "planning to pick it up"); "MR expected
    shortly" beside "being picked up today" is about the merge request.
    """
    text = (
        claim.claimed_state if claim.claimed_state and claim.claimed_state.strip() else claim.note
    )
    lowered = (text or "").lower()
    phrase = " ".join(_NON_WORD.split(lowered)).strip()
    if (
        _NOT_STARTED_WORDING.search(phrase)
        or _NEGATED_STATE.search(phrase)
        or _OTHER_STATE.search(phrase)
        or _NO_CHANGE.search(phrase)
    ):
        return True
    for part in _CLAUSE_BREAK.split(lowered):
        clause = " ".join(_NON_WORD.split(part)).strip()
        if _START_VERB.search(clause) and _FUTURE_OR_MODAL.search(clause):
            return True
    return False


def _issue_keys_named_by(fact: FactEvent) -> tuple[str, ...]:
    """The issue keys a merge request's source branch or title names, upper case."""
    text = " ".join(
        value
        for value in (fact.payload.get("source_branch"), fact.payload.get("title"))
        if isinstance(value, str)
    )
    return tuple(sorted({match.upper() for match in ISSUE_KEY.findall(text)}))


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


def _issue_state_for(target_state: str) -> IssueState | None:
    """The issue state a canonical target reads as, ``None`` for older free text."""
    try:
        return _TARGET_ISSUE_STATES[WriteBackTarget(target_state)]
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


_REPORTED_PHRASES: dict[WriteBackTarget, str] = {
    WriteBackTarget.TODO: "it not started",
    WriteBackTarget.IN_PROGRESS: "it in progress",
    WriteBackTarget.IN_REVIEW: "it in review",
    WriteBackTarget.BLOCKED: "it blocked",
    WriteBackTarget.DONE: "it done",
}
_MERGED_WORDING = re.compile(r"\bmerged?\b")


def _reported_phrase(claim: IssueClaim, to_state: str) -> str:
    """What the person reported, as a canonical phrase ("it merged"), never their words."""
    try:
        target = WriteBackTarget(_stored_target_for_tracker(to_state))
    except ValueError:
        return "a change"
    wording = " ".join(_NON_WORD.split((claim.claimed_state or "").lower()))
    if target is WriteBackTarget.DONE and _MERGED_WORDING.search(wording):
        return "it merged"
    if target is WriteBackTarget.IN_PROGRESS and _only_moves_a_todo_issue(claim):
        # Said started; or only named, with its open merge request (N28).
        return "work on it started" if _claims_work_started(claim) else "work on it"
    return _REPORTED_PHRASES[target]


def _destination_label(written: Issue | None, to_state: str) -> str:
    """The state the issue now has: the tracker's own status name when read back."""
    state = _written_issue_state(to_state)
    if written is not None and written.state is state:
        # Only a read-back that already shows the write names the status; a
        # stale read would name the state the issue just left.
        status = written.metadata.get("status")
        if isinstance(status, str) and status.strip():
            return status.strip()
    if state is None:
        return to_state
    return {
        IssueState.TODO: "To Do",
        IssueState.IN_PROGRESS: "In Progress",
        IssueState.BLOCKED: "Blocked",
        IssueState.DONE: "Done",
    }[state]


def write_back_comment(
    *,
    destination: str,
    person: str,
    reported: str,
    merge_requests: Sequence[str],
    reported_on: date,
) -> str:
    """The tracker note for an applied write-back.

    "Moved to Done by OpenProgram: Raj Iyer reported it merged
    (acme/insights-pipeline !2) in the 2026-10-04 check-in."
    """
    backing = f" ({', '.join(merge_requests)})" if merge_requests else ""
    return (
        f"Moved to {destination} by OpenProgram: {person} reported {reported}{backing} "
        f"in the {reported_on.isoformat()} check-in."
    )


def _claim_for(claims: Sequence[IssueClaim], proposal: WriteBackAudit) -> IssueClaim | None:
    """The check-in claim a proposal came from, when it still names the same target."""
    # A proposal is made only where the claim had a target, so a claim that
    # needed its open merge request for one (N28) is that proposal's claim too.
    for claim in reversed(claims):
        if (
            claim.issue_key == proposal.issue_key
            and _claim_target(claim, open_merge_request=True) == proposal.target_state
        ):
            return claim
    return None
