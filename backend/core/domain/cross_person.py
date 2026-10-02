from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from core.domain.graph import EntityRef
from core.domain.status import CrossPersonMention


class CrossPersonRequestKind(StrEnum):
    DEPENDENCY = "dependency"
    REVIEW = "review"
    INPUT = "input"


class CrossPersonRequestStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    NEEDS_RESOLUTION = "needs_resolution"


class CrossPersonDelivery(StrEnum):
    """Whether the counterpart has been sent the DM about a request."""

    SENT = "sent"
    RETRYING = "retrying"
    NOT_DELIVERED = "not_delivered"


@dataclass(frozen=True, kw_only=True)
class CrossPersonRequest:
    tenant_id: str
    id: str
    requester_id: str
    requester_chat_ref: str | None
    counterpart_id: str | None
    kind: CrossPersonRequestKind
    note: str
    source_correlation_id: str
    status: CrossPersonRequestStatus
    created_at: datetime
    updated_at: datetime
    task_ref: EntityRef | None = None
    raw_name: str | None = None
    email: str | None = None
    counterpart_display_name: str | None = None
    counterpart_email: str | None = None
    notify_message_id: str | None = None
    notify_correlation_id: str | None = None
    # Counterpart DM attempts. The next attempt time is set when an attempt is
    # claimed and cleared once the DM is recorded or the attempts run out.
    notify_attempts: int = 0
    notify_last_attempt_at: datetime | None = None
    notify_next_attempt_at: datetime | None = None

    @property
    def notified(self) -> bool:
        return self.notify_message_id is not None or self.notify_correlation_id is not None

    @property
    def delivery(self) -> CrossPersonDelivery | None:
        """Where the counterpart DM has got to; None when none was attempted.

        No attempt means there was nobody to tell (unmatched, oneself) or
        notification was off when the request was recorded.
        """
        if self.notified:
            return CrossPersonDelivery.SENT
        if self.notify_attempts == 0:
            return None
        if self.status is CrossPersonRequestStatus.OPEN and self.notify_next_attempt_at is not None:
            return CrossPersonDelivery.RETRYING
        return CrossPersonDelivery.NOT_DELIVERED


@dataclass(frozen=True, kw_only=True)
class CrossPersonNotifyRetrySummary:
    """One retry pass over counterpart DMs that have not been sent."""

    due: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    given_up: int = 0


@dataclass(frozen=True, kw_only=True)
class CrossPersonRequestResolution:
    mention: CrossPersonMention
    status: CrossPersonRequestStatus
    counterpart_id: str | None = None
    counterpart_display_name: str | None = None
    counterpart_email: str | None = None


def new_cross_person_request(
    *,
    tenant_id: str,
    id: str,
    requester_id: str,
    requester_chat_ref: str | None,
    source_correlation_id: str,
    resolution: CrossPersonRequestResolution,
    created_at: datetime | None = None,
) -> CrossPersonRequest:
    observed_at = created_at or datetime.now(tz=UTC)
    return CrossPersonRequest(
        tenant_id=tenant_id,
        id=id,
        requester_id=requester_id,
        requester_chat_ref=requester_chat_ref,
        counterpart_id=resolution.counterpart_id,
        kind=CrossPersonRequestKind(resolution.mention.kind),
        note=resolution.mention.note,
        source_correlation_id=source_correlation_id,
        status=resolution.status,
        created_at=observed_at,
        updated_at=observed_at,
        raw_name=resolution.mention.raw_name,
        email=resolution.mention.email,
        counterpart_display_name=resolution.counterpart_display_name,
        counterpart_email=resolution.counterpart_email,
    )
