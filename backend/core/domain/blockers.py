"""First-class developer blocker lifecycle.

A blocker is owned by a person and spans days: it is minted the first time a
developer reports it, re-asserted on later mentions, and resolved explicitly.
``DeveloperStatus.blockers`` remains a derived compatibility projection of the
open set; rows in this module are the source of truth.

Attribution is optional and layered: an explicit ``pod_id`` (usually from a
clarification answer) wins over a ``work_item_id`` (task/issue node id) which
read paths resolve to pods via the graph; a blocker with neither is
"unattributed" and surfaces in every pod the developer belongs to.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from uuid import uuid4


class BlockerSource(StrEnum):
    CHECKIN = "checkin"
    CORRECTION = "correction"
    CARRY_FORWARD = "carry_forward"
    BACKFILL = "backfill"
    SYSTEM = "system"


class BlockerResolutionReason(StrEnum):
    REPORTED_RESOLVED = "reported_resolved"
    OMITTED_IN_CORRECTION = "omitted_in_correction"
    CONFIRMED_NO_BLOCKERS = "confirmed_no_blockers"


class ReconcileMode(StrEnum):
    """How a batch of blocker reports relates to the open set.

    ``CHECKIN``: a chat reply is a partial statement — unmentioned open
    blockers carry forward untouched (``last_seen_on`` is not bumped, which is
    what keeps blocker age honest).
    ``CONFIRM_ALL``: the developer confirmed the status as-is — every open
    blocker is re-asserted today.
    ``RESOLVE_ALL``: the reply explicitly resolved everything outstanding.
    ``AUTHORITATIVE_SET``: a UI correction is a total statement — open
    blockers omitted from the reports are resolved.
    """

    CHECKIN = "checkin"
    CONFIRM_ALL = "confirm_all"
    RESOLVE_ALL = "resolve_all"
    AUTHORITATIVE_SET = "authoritative_set"


@dataclass(frozen=True, kw_only=True)
class BlockerReport:
    """A single blocker as stated in one check-in reply or correction."""

    description: str
    issue_key: str | None = None
    pod_id: str | None = None
    resolved: bool = False


@dataclass(frozen=True, kw_only=True)
class DeveloperBlocker:
    tenant_id: str
    blocker_id: str
    developer_id: str
    description: str
    normalized_key: str
    work_item_id: str | None = None
    pod_id: str | None = None
    source: BlockerSource
    source_correlation_id: str | None = None
    attribution_asked_at: datetime | None = None
    first_seen_on: date
    last_seen_on: date
    resolved_on: date | None = None
    resolved_reason: BlockerResolutionReason | None = None

    @property
    def is_attributed(self) -> bool:
        return self.pod_id is not None or self.work_item_id is not None

    def is_open_on(self, as_of: date) -> bool:
        started = self.first_seen_on <= as_of
        unresolved = self.resolved_on is None or self.resolved_on > as_of
        return started and unresolved


def normalize_blocker_key(description: str) -> str:
    """Deterministic identity key for matching restatements of one blocker.

    Whitespace-collapsed, lowercased, trailing sentence punctuation stripped.
    Kept in exact parity with the SQL used by the backfill migration:
    ``rtrim(lower(btrim(regexp_replace(x, '\\s+', ' ', 'g'))), '.!')``.
    """

    return " ".join(description.split()).lower().rstrip(".!")


def blocker_descriptions(blockers: Sequence[DeveloperBlocker]) -> tuple[str, ...]:
    return tuple(blocker.description for blocker in blockers)


def report_descriptions(reports: Sequence[BlockerReport]) -> tuple[str, ...]:
    return tuple(report.description for report in reports)


def unattributed_blockers(
    blockers: Sequence[DeveloperBlocker],
) -> tuple[DeveloperBlocker, ...]:
    return tuple(blocker for blocker in blockers if not blocker.is_attributed)


def _default_mint_id() -> str:
    return uuid4().hex


@dataclass(frozen=True, kw_only=True)
class BlockerReconciliation:
    """Outcome of reconciling one batch of reports against the open set."""

    open_after: tuple[DeveloperBlocker, ...]
    upserts: tuple[DeveloperBlocker, ...]
    minted: tuple[DeveloperBlocker, ...]
    resolved: tuple[DeveloperBlocker, ...]
    carried: tuple[DeveloperBlocker, ...]


def reconcile_blockers(
    open_blockers: Sequence[DeveloperBlocker],
    reports: Sequence[BlockerReport],
    *,
    tenant_id: str,
    developer_id: str,
    as_of: date,
    mode: ReconcileMode,
    source: BlockerSource,
    resolved_blocker_ids: Sequence[str] = (),
    source_correlation_id: str | None = None,
    overwrite_attribution: bool = False,
    mint_id: Callable[[], str] = _default_mint_id,
) -> BlockerReconciliation:
    """Match reports to open blockers, minting/bumping/resolving as needed.

    Matching precedence per report: ``issue_key == blocker.work_item_id``
    first, then equality of ``normalize_blocker_key(description)``. A match
    bumps ``last_seen_on``, adopts the latest wording, and fills attribution
    only where the row has none — unless ``overwrite_attribution`` is set
    (explicit human corrections may re-attribute). Unmatched reports mint new
    rows.
    """

    remaining = {blocker.blocker_id: blocker for blocker in open_blockers}
    touched: dict[str, DeveloperBlocker] = {}
    minted: list[DeveloperBlocker] = []
    resolved: list[DeveloperBlocker] = []

    for report in _dedupe_reports(reports):
        match = _match_report(report, list(remaining.values()))
        if match is not None:
            del remaining[match.blocker_id]
            updated = _apply_report(
                match, report, as_of=as_of, overwrite_attribution=overwrite_attribution
            )
            if report.resolved:
                updated = _resolve(updated, as_of, BlockerResolutionReason.REPORTED_RESOLVED)
                resolved.append(updated)
            else:
                touched[updated.blocker_id] = updated
            continue
        if report.resolved:
            # A resolution for something we do not track is a no-op.
            continue
        minted.append(
            _mint(
                report,
                tenant_id=tenant_id,
                developer_id=developer_id,
                as_of=as_of,
                source=source,
                source_correlation_id=source_correlation_id,
                mint_id=mint_id,
            )
        )

    for blocker_id in resolved_blocker_ids:
        match_by_id = remaining.pop(blocker_id, None) or touched.pop(blocker_id, None)
        if match_by_id is not None:
            resolved.append(_resolve(match_by_id, as_of, BlockerResolutionReason.REPORTED_RESOLVED))

    carried, extra_resolved, extra_touched = _apply_mode(
        tuple(remaining.values()), mode=mode, as_of=as_of, had_reports=bool(reports)
    )
    resolved.extend(extra_resolved)
    for blocker in extra_touched:
        touched[blocker.blocker_id] = blocker

    open_after = tuple(touched.values()) + tuple(minted) + carried
    upserts = tuple(touched.values()) + tuple(minted) + tuple(resolved)
    return BlockerReconciliation(
        open_after=open_after,
        upserts=upserts,
        minted=tuple(minted),
        resolved=tuple(resolved),
        carried=carried,
    )


def _dedupe_reports(reports: Sequence[BlockerReport]) -> tuple[BlockerReport, ...]:
    seen: set[str] = set()
    unique: list[BlockerReport] = []
    for report in reports:
        key = normalize_blocker_key(report.description)
        if key in seen:
            continue
        seen.add(key)
        unique.append(report)
    return tuple(unique)


def _match_report(
    report: BlockerReport, blockers: Sequence[DeveloperBlocker]
) -> DeveloperBlocker | None:
    if report.issue_key:
        for blocker in blockers:
            if blocker.work_item_id == report.issue_key:
                return blocker
    key = normalize_blocker_key(report.description)
    for blocker in blockers:
        if blocker.normalized_key == key:
            return blocker
    return None


def _apply_report(
    blocker: DeveloperBlocker,
    report: BlockerReport,
    *,
    as_of: date,
    overwrite_attribution: bool = False,
) -> DeveloperBlocker:
    if overwrite_attribution and (report.issue_key or report.pod_id):
        work_item_id = report.issue_key
        pod_id = report.pod_id
    else:
        work_item_id = blocker.work_item_id or report.issue_key
        pod_id = blocker.pod_id or report.pod_id
    return replace(
        blocker,
        description=report.description,
        normalized_key=normalize_blocker_key(report.description),
        work_item_id=work_item_id,
        pod_id=pod_id,
        last_seen_on=max(blocker.last_seen_on, as_of),
    )


def _mint(
    report: BlockerReport,
    *,
    tenant_id: str,
    developer_id: str,
    as_of: date,
    source: BlockerSource,
    source_correlation_id: str | None,
    mint_id: Callable[[], str],
) -> DeveloperBlocker:
    return DeveloperBlocker(
        tenant_id=tenant_id,
        blocker_id=mint_id(),
        developer_id=developer_id,
        description=report.description,
        normalized_key=normalize_blocker_key(report.description),
        work_item_id=report.issue_key,
        pod_id=report.pod_id,
        source=source,
        source_correlation_id=source_correlation_id,
        first_seen_on=as_of,
        last_seen_on=as_of,
    )


def _resolve(
    blocker: DeveloperBlocker, as_of: date, reason: BlockerResolutionReason
) -> DeveloperBlocker:
    return replace(
        blocker,
        resolved_on=as_of,
        resolved_reason=reason,
        last_seen_on=max(blocker.last_seen_on, as_of),
    )


def _apply_mode(
    unmatched: tuple[DeveloperBlocker, ...],
    *,
    mode: ReconcileMode,
    as_of: date,
    had_reports: bool,
) -> tuple[
    tuple[DeveloperBlocker, ...],
    tuple[DeveloperBlocker, ...],
    tuple[DeveloperBlocker, ...],
]:
    """Return (carried, resolved, touched) for open blockers no report matched."""

    if mode is ReconcileMode.RESOLVE_ALL:
        resolved = tuple(
            _resolve(blocker, as_of, BlockerResolutionReason.REPORTED_RESOLVED)
            for blocker in unmatched
        )
        return (), resolved, ()
    if mode is ReconcileMode.AUTHORITATIVE_SET:
        reason = (
            BlockerResolutionReason.OMITTED_IN_CORRECTION
            if had_reports
            else BlockerResolutionReason.CONFIRMED_NO_BLOCKERS
        )
        resolved = tuple(_resolve(blocker, as_of, reason) for blocker in unmatched)
        return (), resolved, ()
    if mode is ReconcileMode.CONFIRM_ALL:
        touched = tuple(
            replace(blocker, last_seen_on=max(blocker.last_seen_on, as_of)) for blocker in unmatched
        )
        return (), (), touched
    return unmatched, (), ()
