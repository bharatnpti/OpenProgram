from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class WriteBackTarget(StrEnum):
    """The canonical states a check-in claim can move a tracker issue to.

    Free-text claims ("on track", "merged and ready to close") are normalised to
    one of these before anything is written; each tracker adapter maps them to a
    transition its workflow really offers. Stored as ``target_state`` on audit
    rows, so it is never exposed as an API enum.
    """

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    BLOCKED = "blocked"
    DONE = "done"


class WriteBackStatus(StrEnum):
    """Lifecycle of a single audited issue-tracker write."""

    PROPOSED = "proposed"
    APPLIED = "applied"
    FAILED = "failed"
    REVERTED = "reverted"
    DECLINED = "declined"
    EXPIRED = "expired"


@dataclass(frozen=True, kw_only=True)
class WriteBackAudit:
    """Append-only record of a proposed, applied, failed, or reverted write.

    Every gated write to the issue tracker produces exactly one audit row so the
    change is logged, idempotent (keyed on issue_key + target_state +
    correlation_id), and reversible (``before_state`` captures the prior value).
    The type is provider-neutral: it never names a concrete tracker.
    """

    id: str
    tenant_id: str
    developer_id: str
    issue_key: str
    correlation_id: str
    status: WriteBackStatus
    target_state: str
    before_state: str | None = None
    after_state: str | None = None
    comment: str | None = None
    source: str = "checkin"
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class WriteBackAdoption:
    """Aggregate view of how many issue-tracker writes were applied via check-in.

    ``applied_count`` is the tenant total of ``applied`` audit rows (optionally
    within a window). ``recent`` carries identifier-only summaries of the latest
    applied writes -- never the developer's raw note or any DM/reply content.
    """

    applied_count: int
    recent: tuple[WriteBackAudit, ...] = field(default_factory=tuple)


class WriteBackGateSource(StrEnum):
    """Where the tenant-wide write-back switch takes its value from."""

    TENANT = "tenant"
    DEFAULT = "default"


@dataclass(frozen=True, kw_only=True)
class WriteBackGate:
    """The effective tenant-wide write-back switch.

    ``source`` is ``tenant`` when the tenant has a persisted override and
    ``default`` when the deployment-wide default applies. Per-developer consent
    only matters while ``enabled`` is true.
    """

    enabled: bool
    source: WriteBackGateSource
