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
