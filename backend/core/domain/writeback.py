from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class WriteBackStatus(StrEnum):
    """Lifecycle of a single audited issue-tracker write."""

    PROPOSED = "proposed"
    APPLIED = "applied"
    FAILED = "failed"
    REVERTED = "reverted"


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
